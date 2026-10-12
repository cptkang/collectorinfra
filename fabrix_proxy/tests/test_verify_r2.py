"""plans/148 R2 교정 독립 검증 — 반출 가림 구멍 · 권장값 동률 순서 · 2차 반출물 재계산 ·
런북 12절 명령 ↔ argparse 대조.

실 LLM 0건. 제품 결함을 드러내는 테스트는 xfail이 아니라 실패로 둔다(verifier 규칙).
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import poc_run as pr  # noqa: E402
import probe_fabrix as pf  # noqa: E402

from tests.conftest import make_settings  # noqa: E402

KEY = "vkey-5Tz8Lm2Qa9"
HOST = "fabrix-gw.bank.internal"
MARKER = "QK-2096"
RUNBOOK = Path(__file__).resolve().parents[1] / "POC_RUNBOOK.md"
R2_EXPORTS = (
    Path("/Users/cptkang/AIOps/collectorinfra/results/fabrix_proxy/export/results.jsonl"),
    Path("/Users/cptkang/AIOps/collectorinfra/results/fabrix_proxy/export_s6fewshot/results.jsonl"),
)


def _guard(**overrides: Any) -> pr.ExportGuard:
    values: dict[str, Any] = {
        "fabrix_api_key": KEY,
        "fabrix_base_url": f"https://{HOST}:8443/kbgenai/v1/chat",
    }
    values.update(overrides)
    return pr.ExportGuard.build([make_settings(**values)], [MARKER])


def _export_one(tmp_path: Path, guard: pr.ExportGuard, text: str) -> str | None:
    """진단 파일 1개를 가진 RUN_DIR을 반출한다 — 반출 사본 내용(거부면 None)."""
    out = tmp_path / "run"
    (out / "failures" / "lab").mkdir(parents=True)
    (out / "summary.md").write_text("# s\n", encoding="utf-8")
    (out / "results.jsonl").write_text("{}\n", encoding="utf-8")
    (out / "failures" / "lab" / "c_r1.txt").write_text(text, encoding="utf-8")
    try:
        pr.export_run(out, out / "export", guard)
    except pr.ExportCheckError:
        return None
    return (out / "export" / "failures" / "lab" / "c_r1.txt").read_text(encoding="utf-8")


# ─────────────────────────── 가림 — 통과해야 하는 형태 ───────────────────────────


@pytest.mark.parametrize("text", [
    f"see https://{HOST}:8443/v1?api_key={KEY}&x=1 done",  # 키 값이 URL 안에
    f"see https://{KEY}@{HOST}/v1",  # 키가 userinfo 자리에
    f"gateway {HOST}:8443 refused",  # URL 밖 호스트 + 포트
    f"HTTPS://{HOST.upper()}:8443/V1 upper scheme",  # 대문자 URL(스킴 매치로 통째 가림)
    f"Authorization: Bearer {KEY}",
    f'{{"headers": {{"Authorization": "Bearer {KEY}"}}}}',
    f"prefix only {KEY[:8]}",
])
def test_mask_known_forms_leave_nothing(tmp_path: Path, text: str) -> None:
    copied = _export_one(tmp_path, _guard(), text)
    if copied is None:
        return  # 거부도 안전하다
    for needle in (KEY[:8], HOST, HOST.upper(), "https://", "HTTPS://"):
        assert needle not in copied, needle
    assert "bearer" not in copied.lower()


def test_short_key_six_chars_is_masked_whole(tmp_path: Path) -> None:
    guard = _guard(fabrix_client_key="Zq7Wx2")
    copied = _export_one(tmp_path, guard, "client key Zq7Wx2 in header")
    assert copied is not None and "Zq7Wx2" not in copied


def test_encenv_parsing_forms(tmp_path: Path) -> None:
    enc = tmp_path / ".encenv"
    enc.write_text(
        "# FABRIX_API_KEY=commented-out-value\n"
        "export EXPORTED_KEY=exported-val-123\n"
        "SPACED_KEY = 'spaced-val-456'  \n"
        'QUOTED_KEY="quoted-val-789"\n'
        "\n"
        "EMPTY=\n",
        encoding="utf-8",
    )
    guard = pr.ExportGuard()
    guard.add_encenv(enc)
    for value in ("exported-val-123", "spaced-val-456", "quoted-val-789"):
        assert any(r.startswith("secret:ENCENV:") for r in guard.violations(f"x {value} y")), value
        masked, _ = guard.mask(f"x {value} y")
        assert value[:8] not in masked
    assert guard.violations("commented-out-value") == []


def test_cmd_export_wires_encenv_from_package_root() -> None:
    assert pr.ENCENV_FILE == Path(pr.__file__).resolve().parents[1] / ".encenv"
    src = Path(pr.__file__).read_text(encoding="utf-8")
    body = src[src.index("def cmd_export"):src.index("# ─", src.index("def cmd_export"))]
    assert "guard.add_encenv(ENCENV_FILE)" in body and "export_run(" in body
    assert body.index("add_encenv") < body.index("export_run(")


def test_verdict_file_with_settings_host_or_key_refused_at_export(tmp_path: Path) -> None:
    for i, leak in enumerate((f"host {HOST}", f"key {KEY[:8]}", "https://x.invalid/")):
        out = tmp_path / f"run{i}"
        out.mkdir()
        (out / "summary.md").write_text(f"# s\n{leak}\n", encoding="utf-8")
        (out / "results.jsonl").write_text("{}\n", encoding="utf-8")
        with pytest.raises(pr.ExportCheckError):
            pr.export_run(out, out / "export", _guard())
        assert not (out / "export").exists()


# ─────────────────────────── 가림 — 구멍(결함이면 실패) ───────────────────────────


def test_bearer_value_after_quote_is_not_left_behind(tmp_path: Path) -> None:
    """`Bearer "tok"`·`bearer: 'tok'` — 가림이 키워드만 지우고 값은 남기면 재검사가 못 잡는다."""
    for text in ('Authorization: Bearer "eyJhbGciOiJIUzI1NiJ9.unknown"',
                 "auth bearer: 'eyJhbGciOiJIUzI1NiJ9.unknown'"):
        copied = _export_one(tmp_path / str(abs(hash(text))), _guard(), text)
        assert copied is None or "eyJhbGciOiJIUzI1NiJ9" not in copied, copied


def test_authorization_basic_value_is_not_exported(tmp_path: Path) -> None:
    """수용 기준 A — Authorization 값은 어떤 반출 파일에도 나가면 안 된다(Bearer만이 아니라)."""
    copied = _export_one(tmp_path, _guard(), "Authorization: Basic dXNlcjpwYXNzd29yZA==")
    assert copied is None or "dXNlcjpwYXNzd29yZA" not in copied, copied


def test_uppercase_host_outside_url_is_masked_or_refused(tmp_path: Path) -> None:
    """호스트 이름은 대소문자를 가리지 않는다 — URL 밖 대문자 호스트도 가려야 한다."""
    copied = _export_one(tmp_path, _guard(), f"connect to {HOST.upper()} failed")
    assert copied is None or HOST.upper() not in copied, copied


def test_json_escaped_url_is_detected(tmp_path: Path) -> None:
    """`https:\\/\\/…`(JSON 슬래시 이스케이프) — URL 규칙이 못 잡으면 업스트림 경로가 남는다."""
    text = 'raw {"next": "https:\\/\\/' + HOST + '\\/kbgenai\\/v1\\/chat"}'
    assert "url" in _guard().violations(text, diagnostic=True)


# ─────────────────────────── 권장값 ───────────────────────────


def _opts(**kw: Any) -> pr.RunOptions:
    base: dict[str, Any] = {"contents_mode": "turns", "protocol_lang": "en", "fewshot": "static",
                            "fewshot_placement": "system", "repair_max": 1, "passthrough": False}
    base.update(kw)
    return pr.RunOptions(**base)


def _row(opts: pr.RunOptions, record: str, scenario: str, **kw: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "record": record, "run_id": "r1", "label": opts.label(), "options": opts.as_record(),
        "scenario": scenario, "case": "c", "rep": 1, "turn": None, "http_status": 200,
        "error_code": None, "measured": True, "emulation": "parsed",
        "example_tool_called": False, "format_ok": True, "failure_type": None,
        "latency_ms": 10, "payload_chars": 0, "payload_est_tokens": 0,
        "completed": True, "reflected": True, "skipped": None,
    }
    row.update(kw)
    return row


def test_tie_order_none_dynamic_prefers_dynamic_when_static_far_behind() -> None:
    """1차 C.1 형태 — none 100% = dynamic 100% · static 94.7%(>2%p 뒤). 옛 순번은 none."""
    records: list[dict[str, Any]] = []
    for mode, bad in (("none", 0), ("static", 2), ("dynamic", 0)):
        o = _opts(fewshot=mode)
        records += [_row(o, "call", "S1") for _ in range(38 - bad)]
        records += [_row(o, "call", "S1", format_ok=False, failure_type="format")
                    for _ in range(bad)]
    rec = pr.compute_recommendation(records, None, None)
    assert rec.values["FEWSHOT"] == "dynamic"


@pytest.mark.skipif(not all(p.is_file() for p in R2_EXPORTS), reason="2차 반출물 없음")
def test_second_export_recomputes_static_system_ko(tmp_path: Path) -> None:
    records: list[dict[str, Any]] = []
    for i, src in enumerate(R2_EXPORTS):
        copy = tmp_path / f"{i}.jsonl"
        shutil.copyfile(src, copy)
        records += [json.loads(line) for line in copy.read_text(encoding="utf-8").splitlines()]
    rec = pr.compute_recommendation(records, None, None)
    assert (rec.values.get("FEWSHOT"), rec.values.get("FEWSHOT_PLACEMENT"),
            rec.values.get("PROTOCOL_LANG")) == ("static", "system", "ko")
    assert "⑤b 선택 기준: 반영률(S6) → lang=ko" in rec.basis


def test_judgment_threshold_rows_unchanged() -> None:
    m = pr.label_metrics([_row(_opts(), "case", "S5")])
    rows = {name: line for name, _, line, _ in pr.judgments(m)}
    assert rows["완주율(S5)"] == "≥90%" and rows["도구 결과 반영률(S5)"] == "≥90%"
    assert rows["형식 유효율(S1~S3)"] == "≥95%" and rows["오탐률(S4)"] == "≤5%"


# ─────────────────────────── 런북 12절 명령 ↔ argparse ───────────────────────────


def _section12_commands() -> list[list[str]]:
    text = RUNBOOK.read_text(encoding="utf-8")
    sec = text[text.index("## 12. 재측정 R2"):]
    cmds = []
    for line in re.findall(r'^"\$PY" scripts/(?:poc_run|probe_fabrix)\.py .*$', sec, re.M):
        line = line.replace('"$RUN_DIR"', "/tmp/run").replace('"$PY" ', "")
        cmds.append(shlex.split(line))
    return cmds


def test_runbook_section12_commands_parse() -> None:
    cmds = _section12_commands()
    assert len(cmds) >= 5
    for argv in cmds:
        script, args = argv[0], argv[1:]
        if script.endswith("poc_run.py"):
            ns = pr.build_parser().parse_args(args)
            assert callable(ns.func)
        else:
            class _StopError(Exception):
                pass

            def _stop(_: Any) -> Path:
                raise _StopError

            mp = pytest.MonkeyPatch()
            mp.setattr(pf.common, "resolve_out", _stop)
            try:
                with pytest.raises(_StopError):
                    pf.main(args)
            finally:
                mp.undo()


# ─────────────── 가림·독립 검사 보강분(검증 도중 반영된 교정) — 우회 형태 ───────────────


@pytest.mark.parametrize("text", [
    '{"Authorization": "Basic dXNlcjpwYXNzd29yZA=="}',  # JSON 헤더 · Basic
    "x-openapi-token: someUnknownToken123",  # KBGenAI 헤더 이름 · 모르는 값
    "Bearertoken-without-space",  # 경계 없는 bearer — 가리지 못하면 거부여야 한다
    f"key {KEY[:4]}%{ord(KEY[4]):02X}{KEY[5:]}",  # 퍼센트 인코딩된 키
    f"host {HOST[:6]}​{HOST[6:]}",  # 제로폭 공백이 낀 호스트
    "see https：／／example.invalid/x",  # 전각 콜론·슬래시 URL
    "upstream 10.20.30.40:8443 reset",  # IPv4(진단 파일)
])
def test_hardened_forms_are_masked_or_refused(tmp_path: Path, text: str) -> None:
    copied = _export_one(tmp_path, _guard(), text)
    if copied is None:
        return
    for needle in ("dXNlcjpwYXNzd29yZA", "someUnknownToken123", "token-without-space",
                   KEY[5:], HOST[6:], "example.invalid", "10.20.30.40"):
        assert needle not in copied, (needle, copied)


def test_verdict_file_refused_when_failure_dump_answer_leaks_into_it(tmp_path: Path) -> None:
    """판정값 검사가 `failures/` 원출력 조각을 불러 쓰기 시점과 같은 엄격도인지."""
    answer = "The owner office is a long free-form model sentence here."
    out = tmp_path / "run"
    (out / "failures" / "lab").mkdir(parents=True)
    (out / "failures" / "lab" / "s5_c_r1.txt").write_text(
        f"# S5 c r1 — failure=history\n\n--- final answer (앞 2000자)\n{answer}\n",
        encoding="utf-8")
    (out / "summary.md").write_text(f"# s\n{answer}\n", encoding="utf-8")
    (out / "results.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(pr.ExportCheckError) as info:
        pr.export_run(out, out / "export", _guard())
    assert "response_text" in info.value.rules
    assert not (out / "export").exists()


def test_export_without_encenv_needs_explicit_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "run"
    out.mkdir()
    (out / "summary.md").write_text("# s\n", encoding="utf-8")
    (out / "results.jsonl").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(pr, "load_settings", lambda: make_settings(fabrix_api_key=KEY))
    monkeypatch.setattr(pr, "ENCENV_FILE", tmp_path / "missing.encenv")
    monkeypatch.setattr(pr, "ENV_FILE", tmp_path / "missing.env")
    assert pr.main(["export", "--out", str(out)]) == 2
    assert not (out / "export").exists()
    assert pr.main(["export", "--out", str(out), "--allow-no-encenv"]) == 0
    assert (out / "export" / "summary.md").is_file()


def test_export_ignores_symlinked_failure_outside_run_dir(tmp_path: Path) -> None:
    secret_file = tmp_path / "outside.txt"
    secret_file.write_text("outside content must not be copied\n", encoding="utf-8")
    out = tmp_path / "run"
    (out / "failures" / "lab").mkdir(parents=True)
    (out / "summary.md").write_text("# s\n", encoding="utf-8")
    (out / "results.jsonl").write_text("{}\n", encoding="utf-8")
    (out / "failures" / "lab" / "link.txt").symlink_to(secret_file)
    stats = pr.export_run(out, out / "export", _guard())
    assert stats["skipped"] == 1
    assert not (out / "export" / "failures" / "lab" / "link.txt").exists()
