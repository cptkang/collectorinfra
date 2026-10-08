"""러너 단위 테스트 — 권장 규칙 · 지표 · 옵션 · 반출물 자기 검사 (plans/148 W3b).

프로세스를 띄우지 않는다(합성 결과 줄로 계산만 확인).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import poc_run as pr  # noqa: E402

from tests.conftest import make_settings  # noqa: E402


def _opts(**kw: Any) -> pr.RunOptions:
    base: dict[str, Any] = {
        "contents_mode": "turns",
        "protocol_lang": "en",
        "fewshot": "static",
        "fewshot_placement": "system",
        "repair_max": 1,
        "passthrough": False,
    }
    base.update(kw)
    return pr.RunOptions(**base)


def _call(opts: pr.RunOptions, scenario: str, **kw: Any) -> dict[str, Any]:
    rec: dict[str, Any] = {
        "record": "call",
        "run_id": "r1",
        "label": opts.label(),
        "options": opts.as_record(),
        "scenario": scenario,
        "case": "c",
        "rep": 1,
        "turn": None,
        "http_status": 200,
        "error_code": None,
        "measured": True,
        "emulation": "parsed",
        "example_tool_called": False,
        "format_ok": True,
        "args_ok": None,
        "selection_ok": None,
        "false_positive": None,
        "usage_present": None,
        "content_filtered": None,
        "latency_ms": 10,
        "payload_chars": 0,
        "payload_est_tokens": 0,
        "failure_type": None,
    }
    rec.update(kw)
    return rec


def _case(opts: pr.RunOptions, scenario: str, **kw: Any) -> dict[str, Any]:
    rec: dict[str, Any] = {
        "record": "case",
        "run_id": "r1",
        "label": opts.label(),
        "options": opts.as_record(),
        "scenario": scenario,
        "case": "c",
        "rep": 1,
        "completed": True,
        "reflected": True,
        "repeated_call": False,
        "steps": 3,
        "latency_ms": 100,
        "skipped": None,
        "failure_type": None,
    }
    rec.update(kw)
    return rec


def _format_rows(opts: pr.RunOptions, ok: int, total: int, scenario: str = "S1") -> list[dict]:
    """형식 유효 ok건 + 502 tool_call_invalid (total-ok)건."""
    rows = [_call(opts, scenario) for _ in range(ok)]
    rows += [
        _call(opts, scenario, http_status=502, error_code="tool_call_invalid", format_ok=False,
              emulation="invalid")
        for _ in range(total - ok)
    ]
    return rows


# ─────────────────────────── ⑤a fewshot ───────────────────────────


def test_fewshot_excludes_example_miscall_mode() -> None:
    static, dynamic, none = _opts(fewshot="static"), _opts(fewshot="dynamic"), _opts(fewshot="none")
    records = _format_rows(static, 100, 100) + _format_rows(dynamic, 90, 100)
    records += _format_rows(none, 80, 100)
    records.append(_call(static, "S4", example_tool_called=True))
    rec = pr.compute_recommendation(records, None, None)
    assert rec.values["FEWSHOT"] == "dynamic"
    assert rec.values["FEWSHOT_PLACEMENT"] == "system"
    assert "RECOMMEND FEWSHOT=dynamic" in rec.lines


def test_fewshot_within_two_points_prefers_static() -> None:
    records = _format_rows(_opts(fewshot="none"), 97, 100)
    records += _format_rows(_opts(fewshot="static"), 96, 100)
    records += _format_rows(_opts(fewshot="dynamic"), 95, 100)
    assert pr.compute_recommendation(records, None, None).values["FEWSHOT"] == "static"


def test_fewshot_beyond_two_points_picks_best() -> None:
    records = _format_rows(_opts(fewshot="none"), 90, 100)
    records += _format_rows(_opts(fewshot="static"), 90, 100)
    records += _format_rows(_opts(fewshot="dynamic"), 100, 100)
    assert pr.compute_recommendation(records, None, None).values["FEWSHOT"] == "dynamic"


def test_fewshot_all_low_advises_contents_rerun_then_uses_it() -> None:
    records = _format_rows(_opts(fewshot="none"), 80, 100)
    records += _format_rows(_opts(fewshot="static"), 85, 100)
    records += _format_rows(_opts(fewshot="dynamic"), 70, 100)
    rec = pr.compute_recommendation(records, None, None)
    assert any(line.startswith("ADVISE FEWSHOT_RERUN=") for line in rec.lines)
    assert rec.values["FEWSHOT"] == "static"

    records += _format_rows(_opts(fewshot="static", fewshot_placement="contents"), 95, 100)
    rec = pr.compute_recommendation(records, None, None)
    assert not any(line.startswith("ADVISE FEWSHOT_RERUN=") for line in rec.lines)
    assert (rec.values["FEWSHOT"], rec.values["FEWSHOT_PLACEMENT"]) == ("static", "contents")


def test_fewshot_needs_two_modes() -> None:
    rec = pr.compute_recommendation(_format_rows(_opts(), 10, 10), None, None)
    assert "FEWSHOT" not in rec.values


# ─────────────────────────── ⑤b 언어 ───────────────────────────


def test_lang_higher_format_wins() -> None:
    records = _format_rows(_opts(protocol_lang="en"), 90, 100)
    records += _format_rows(_opts(protocol_lang="ko"), 95, 100)
    assert pr.compute_recommendation(records, None, None).values["PROTOCOL_LANG"] == "ko"


def test_lang_tie_uses_s5_completion_then_en() -> None:
    en, ko = _opts(protocol_lang="en"), _opts(protocol_lang="ko")
    records = _format_rows(en, 99, 100) + _format_rows(ko, 100, 100)
    records += [_case(en, "S5", completed=False), _case(ko, "S5", completed=True)]
    assert pr.compute_recommendation(records, None, None).values["PROTOCOL_LANG"] == "ko"

    tied = _format_rows(en, 100, 100) + _format_rows(ko, 100, 100)
    tied += [_case(en, "S5"), _case(ko, "S5")]
    assert pr.compute_recommendation(tied, None, None).values["PROTOCOL_LANG"] == "en"


# ─────────────────────────── 탐침 · recommend.env ───────────────────────────


def test_probe_results_feed_recommendation() -> None:
    contents = {"recommend_contents_mode": "transcript",
                "tool_history": {"turns": {"n": 3, "ok": 1}, "transcript": {"n": 3, "ok": 3}}}
    native = {"candidates": [{"source": "FABRIX_NATIVE_URL", "tool_calls": True}]}
    rec = pr.compute_recommendation([], native, contents)
    assert rec.values == {"CONTENTS_MODE": "transcript"}
    assert rec.lines[0] == "RECOMMEND CONTENTS_MODE=transcript"
    assert any(line.startswith("ADVISE PASSTHROUGH=") for line in rec.lines)


def test_recommend_env_updates_and_keeps_other_keys(tmp_path: Path) -> None:
    path = tmp_path / "recommend.env"
    path.write_text("# c\nFEWSHOT=none\nCUSTOM=1\n", encoding="utf-8")
    pr.update_recommend_env(path, {"FEWSHOT": "static"}, pr.ExportGuard())
    lines = path.read_text(encoding="utf-8").splitlines()
    assert "FEWSHOT=static" in lines and "CUSTOM=1" in lines
    assert not any(line.startswith(("PROTOCOL_LANG", "CONTENTS_MODE")) for line in lines)

    empty = tmp_path / "none.env"
    pr.update_recommend_env(empty, {}, pr.ExportGuard())
    assert not empty.exists()


# ─────────────────────────── 지표 ───────────────────────────


def test_percentile_nearest_rank() -> None:
    values = list(range(1, 101))
    assert pr.percentile(values, 50) == 50
    assert pr.percentile(values, 95) == 95
    assert pr.percentile([7], 95) == 7
    assert pr.percentile([], 50) is None


def test_label_metrics() -> None:
    o = _opts()
    records = [
        _call(o, "S1", emulation="repaired", selection_ok=True, args_ok=True),
        _call(o, "S1", selection_ok=False, args_ok=True, failure_type="tool_selection"),
        _call(o, "S1", http_status=502, error_code="upstream_error", measured=False,
              format_ok=None, emulation=None, failure_type="limit_block", latency_ms=50),
        _call(o, "S4", false_positive=True, failure_type="tool_selection", latency_ms=30),
        _call(o, "S4", false_positive=False, latency_ms=20),
        _call(o, "S5", turn=1, payload_chars=100, payload_est_tokens=25),
        _call(o, "S5", turn=2, payload_chars=300, payload_est_tokens=75, example_tool_called=True),
        _case(o, "S5", completed=True, reflected=False, failure_type="history"),
        _case(o, "S5", completed=True, reflected=True),
        _case(o, "S5", completed=False, reflected=None, failure_type="history"),
        _call(o, "F5", usage_present=True),
        _call(o, "F5", http_status=400, error_code="content_filter", measured=False,
              format_ok=None, content_filtered=True, failure_type="limit_block"),
    ]
    m = pr.label_metrics(records)
    assert m["format_s123"] == (2, 2)  # 인프라 실패는 제외
    assert m["select_s1"] == (1, 2)
    assert m["fp_s4"] == (1, 2)
    assert m["complete_s5"] == (2, 3)
    assert m["reflect_s5"] == (1, 2)  # 완주 건 중 반영
    assert m["repair_dep"] == (1, 7)  # 200 응답 7건 중 교정 1건
    assert m["example_miscall"] == 1
    assert m["infra"] == 2
    assert m["usage_f5"] == (1, 1) and m["pii_f5"] == (1, 1)
    assert m["turns_s5"]["growth"] == {"chars": 200, "tokens": 50}
    assert m["failures"]["history"] == 2 and m["failures"]["limit_block"] == 2
    judged = {row[0]: row[3] for row in pr.judgments(m)}
    assert judged["오탐률(S4)"] == "미달"
    assert judged["예시 도구 오호출(교정 전 원응답)"] == "미달"
    assert judged["완주율(S6)"] == "자료 없음"


def test_judge_calls_modes() -> None:
    expect = [{"name": "convert_currency", "args": {"amount": [120], "from_currency": ["usd"]}}]
    good = [("convert_currency", {"amount": "120", "from_currency": "USD"})]
    assert pr.judge_calls(good, expect, "set") == (True, True)
    assert pr.judge_calls([("convert_currency", {"amount": 5})], expect, "set") == (True, False)
    assert pr.judge_calls([("find_book", {"title": "x"})], expect, "set") == (False, True)
    assert pr.judge_calls([], expect, "required") == (False, False)
    named = pr.judge_calls([("find_book", {})], [], "named", force="find_book")
    assert named == (True, True)


def test_reflects_folds_unicode_variants() -> None:
    # 실 FabriX(GptOss) 최종 답 실측 형태 · U+2011 하이픈 · U+202F 공백 · U+2019 따옴표
    final = "The ticket\u2019s verification code is **VR\u20115531**; office: Maple\u202fCourt."
    assert pr.reflects(final, ["VR-5531", "Maple Court"])
    assert pr.reflects("code zx\u20144417 at harbor point", ["ZX-4417", "Harbor Point"])
    assert not pr.reflects(final, ["VR-5531", "Harbor Point"])  # 빠진 값은 계속 실패
    assert not pr.reflects("VR5531 Maple Court", ["VR-5531"])  # 하이픈 자체가 없으면 실패
    assert pr.reflects("anything", [])


# ─────────────────────────── 옵션 · --repeat ───────────────────────────


def test_parse_repeat_two_forms() -> None:
    assert set(pr.parse_repeat("3").values()) == {3}
    custom = pr.parse_repeat("S1=10,s4=2")
    assert custom["S1"] == 10 and custom["S4"] == 2 and custom["S5"] == pr.DEFAULT_REPEAT["S5"]
    assert pr.parse_repeat(None) == pr.DEFAULT_REPEAT
    for bad in ("0", "S9=1", "S1=x", "S1"):
        with pytest.raises(pr.UsageError):
            pr.parse_repeat(bad)


def _args(**kw: Any) -> argparse.Namespace:
    values: dict[str, Any] = {
        "contents_mode": None, "protocol_lang": None, "protocol_file": None, "fewshot": None,
        "fewshot_placement": None, "fewshot_file": None, "repair_max": None, "passthrough": False,
    }
    values.update(kw)
    return argparse.Namespace(**values)


def test_file_options_are_inlined(tmp_path: Path) -> None:
    protocol = tmp_path / "my_rules.txt"
    protocol.write_text("RULES\n{{TOOLS}}\n", encoding="utf-8")
    fewshot = tmp_path / "my_fewshot.json"
    fewshot.write_text(json.dumps({"tools": [], "examples": []}), encoding="utf-8")
    opts = pr.resolve_run_options(
        _args(protocol_file=str(protocol), fewshot_file=str(fewshot), fewshot="none",
              repair_max=0),
        make_settings(),
    )
    options = opts.proxy_options()
    assert options["protocol_text"] == "RULES\n{{TOOLS}}\n"
    assert options["fewshot_examples"] == {"tools": [], "examples": []}
    assert options["fewshot"] == "none" and options["repair_max"] == 0
    assert opts.label().endswith(",protocol_file=my_rules.txt,fewshot_file=my_fewshot.json")
    assert "RULES" not in json.dumps(opts.as_record())


def test_file_options_validated_before_run(tmp_path: Path) -> None:
    bad_protocol = tmp_path / "bad.txt"
    bad_protocol.write_text("no placeholder", encoding="utf-8")
    with pytest.raises(pr.UsageError):
        pr.resolve_run_options(_args(protocol_file=str(bad_protocol)), make_settings())
    bad_fewshot = tmp_path / "bad.json"
    bad_fewshot.write_text(json.dumps({"tools": []}), encoding="utf-8")
    with pytest.raises(pr.UsageError):
        pr.resolve_run_options(_args(fewshot_file=str(bad_fewshot)), make_settings())
    with pytest.raises(pr.UsageError):
        pr.resolve_run_options(_args(protocol_file=str(tmp_path / "missing.txt")),
                               make_settings())


def test_label_defaults_follow_settings() -> None:
    opts = pr.resolve_run_options(_args(), make_settings(fabrix_proxy_fewshot="dynamic"))
    assert opts.label() == (
        "contents=turns,fewshot=dynamic,placement=system,lang=en,repair=1,passthrough=false"
    )


# ─────────────────────────── 반출물 자기 검사 ───────────────────────────


def test_guard_rules_and_no_values_in_violation(tmp_path: Path) -> None:
    settings = make_settings(fabrix_native_url="https://native.example.invalid/v1")
    guard = pr.ExportGuard.build([settings], ["SECRET-BODY-17"])
    guard.note_response("The model answered with a long sentence here.")
    cases = {
        "url": "see https://x",
        "auth_header": "Bearer abc",
        "secret:FABRIX_API_KEY": "k=" + settings.fabrix_api_key[:8],
        "secret:FABRIX_PROXY_TOKEN": settings.fabrix_proxy_token,
        "host:FABRIX_BASE_URL": "upstream.invalid",
        "host:FABRIX_NATIVE_URL": "native.example.invalid",
        "leak_marker": "x SECRET-BODY-17 y",
        "response_text": "prefix The model answered with a long sentence",
    }
    for rule, text in cases.items():
        assert rule in guard.violations(text), rule
    assert guard.violations("plain judged values 0.95 S1 find_book") == []

    target = tmp_path / "summary.md"
    target.write_text("old", encoding="utf-8")
    with pytest.raises(pr.ExportCheckError) as info:
        guard.write(target, "leak SECRET-BODY-17")
    assert not target.exists()
    assert info.value.rules == ["leak_marker"]
    assert "SECRET-BODY-17" not in str(info.value)
    with pytest.raises(pr.ExportCheckError):  # 한 번 걸리면 이후 쓰기도 거부
        guard.write(tmp_path / "other.md", "clean")


def test_guard_append_deletes_file(tmp_path: Path) -> None:
    guard = pr.ExportGuard(markers=["ZX-4417"])
    path = tmp_path / "results.jsonl"
    guard.append(path, '{"ok": 1}\n')
    with pytest.raises(pr.ExportCheckError):
        guard.append(path, '{"x": "ZX-4417"}\n')
    assert not path.exists()


def test_short_secret_values_are_skipped() -> None:
    guard = pr.ExportGuard.build([make_settings(fabrix_client_key="abc")], [])
    assert all("FABRIX_CLIENT_KEY" not in k for k in guard.secret_needles)


def test_safe_names_mask_model_text() -> None:
    assert pr.safe_name("find_book") == "find_book"
    assert pr.safe_name("example_lookup_item") == "example_lookup_item"
    assert pr.safe_name("write_todos") == "write_todos"
    assert pr.safe_name("some hallucinated name") == "<unknown>"
    assert pr.safe_reason("schema_error:title") == "schema_error:title"
    assert pr.safe_reason("schema_error:invented_key") == "schema_error:<other>"
    assert pr.safe_code("tool_call_invalid") == "tool_call_invalid"
    assert pr.safe_code("Some Message") == "other"


def test_outcome_from_body() -> None:
    ok = pr.outcome_from_body(200, 5, {
        "choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c", "type": "function",
             "function": {"name": "find_book", "arguments": "{\"title\": \"x\"}"}}]}}],
        "usage": {"total_tokens": 3},
        "fabrix_proxy_diag": {"emulation": "parsed", "raw_heads": ["RAW"]},
    })
    assert ok.measured and ok.usage and ok.calls() == [("find_book", {"title": "x"})]
    assert ok.raw_heads() == ["RAW"]
    bad = pr.outcome_from_body(502, 5, {"error": {"code": "tool_call_invalid", "message": "m"}})
    assert bad.measured and bad.error_code == "tool_call_invalid"
    infra = pr.outcome_from_body(502, 5, {"error": {"code": "upstream_error"}})
    assert not infra.measured


def test_compare_fields() -> None:
    sig = {"top_level_keys": ["messages"], "stream": False}
    reqs = [{"case": "a", "signature": sig}]
    base = {"consumers": {
        "openai_sdk_nonstream": {"status": "recorded", "requests": reqs},
        "body_tools_deep_agent": {"status": "recorded"},
    }}
    same = {"consumers": {
        "openai_sdk_nonstream": {"status": "recorded", "requests": reqs},
    }}
    assert pr.compare_fields(base, same) == []
    changed = {"consumers": {"openai_sdk_nonstream": {"status": "recorded", "requests": [
        {"case": "a", "signature": {"top_level_keys": ["messages"], "stream": True}}]}}}
    assert pr.compare_fields(base, changed) == ["openai_sdk_nonstream 요청#1: stream"]
    missing = {"consumers": {"openai_sdk_nonstream": {"status": "skipped"}}}
    assert pr.compare_fields(base, missing) == ["openai_sdk_nonstream: 상태 recorded → skipped"]
