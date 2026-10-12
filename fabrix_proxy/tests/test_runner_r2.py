"""러너 R2 교정 테스트 — 반출 확대(export) · S5 판정(도구 미사용·추측 병렬) · 권장값 반영률 ·
F1 후보 0개.

plans/148 1단계 2차 내부망 측정(2026-10-12) 결과 반영. 실 LLM 0건 — 합성 결과 줄 · 가짜 클라이언트 ·
가짜 KBGenAI(하위 프로세스 1건)만 쓴다.
"""

from __future__ import annotations

import asyncio
import json
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

FAKE_KEY = "fakekey-9Q2X7W1Z"
FAKE_HOST = "fabrix-gw.bank.internal"
ENCENV_EXTRA = "extra-secret-77AB"
MARKER = "QK-2096"  # 시나리오 표지(합성 값)


def _settings() -> Any:
    return make_settings(
        fabrix_api_key=FAKE_KEY, fabrix_base_url=f"https://{FAKE_HOST}/kbgenai/v1/chat",
    )


def _guard() -> pr.ExportGuard:
    return pr.ExportGuard.build([_settings()], [MARKER])


# ─────────────────────────── 반출 자기 검사 · 마스킹 ───────────────────────────


def test_diagnostic_relaxes_only_marker_and_response_text() -> None:
    guard = _guard()
    guard.note_response("The model answered with a long sentence here.")
    relaxed = f"{MARKER} · The model answered with a long sentence here."
    assert guard.violations(relaxed) == ["leak_marker", "response_text"]
    assert guard.violations(relaxed, diagnostic=True) == []
    for text, rule in (
        ("see https://x.example/y", "url"),
        ("Authorization: Bearer xyz", "auth_header"),
        (f"k={FAKE_KEY[:8]}", "secret:FABRIX_API_KEY"),
        (f"host {FAKE_HOST}", "host:FABRIX_BASE_URL"),
    ):
        assert rule in guard.violations(text, diagnostic=True), rule


def test_mask_hides_credentials_hosts_urls_and_bearer() -> None:
    guard = _guard()
    text = (
        f"key={FAKE_KEY} pre={FAKE_KEY[:8]}xx host={FAKE_HOST} "
        f"url=https://{FAKE_HOST}/v1?k=1 Authorization: Bearer xyz\n{MARKER}"
    )
    masked, hits = guard.mask(text)
    assert guard.violations(masked, diagnostic=True) == []
    for value in (FAKE_KEY[:8], FAKE_HOST, "https://", "Bearer", "xyz"):
        assert value not in masked, value
    assert MARKER in masked  # 합성 표지는 남긴다
    assert set(hits) == {"secret:FABRIX_API_KEY", "url", "host:FABRIX_BASE_URL", "auth_header"}
    assert "<redacted:secret:FABRIX_API_KEY>" in masked and "<url>" in masked


def test_add_encenv_values_become_secrets(tmp_path: Path) -> None:
    enc = tmp_path / ".encenv"
    enc.write_text(f"# c\nEXTRA_TOKEN='{ENCENV_EXTRA}'\nSHORT=abc\n", encoding="utf-8")
    guard = pr.ExportGuard()
    guard.add_encenv(enc)
    assert guard.violations(f"x {ENCENV_EXTRA[:8]}") == ["secret:ENCENV:EXTRA_TOKEN"]
    assert guard.violations("abc") == []
    guard.add_encenv(tmp_path / "missing")  # 없으면 조용히 넘어간다


def _run_dir(root: Path) -> Path:
    out = root / "run"
    (out / "failures" / "lab").mkdir(parents=True)
    (out / "summary.md").write_text("# summary\nRECOMMEND FEWSHOT=static\n", encoding="utf-8")
    (out / "results.jsonl").write_text('{"record": "call"}\n', encoding="utf-8")
    (out / "env_check.md").write_text("# env\n", encoding="utf-8")
    (out / "recommend.env").write_text("FEWSHOT=static\n", encoding="utf-8")
    (out / "probe_native.json").write_text(
        json.dumps({"candidates": [{"source": "--path#1", "path": "/v1/chat/completions"}]}),
        encoding="utf-8",
    )
    (out / "failures" / "lab" / "s5_ticket_ko_r1.txt").write_text(
        f"final: {MARKER} at https://{FAKE_HOST}/x key {FAKE_KEY} Bearer xyz {ENCENV_EXTRA}\n",
        encoding="utf-8",
    )
    (out / "failures" / "lab" / "note.json").write_text("{}", encoding="utf-8")  # 미분류
    (out / "proxy.log").write_text(f"log {FAKE_HOST}\n", encoding="utf-8")
    return out


def test_export_copies_verdicts_and_masked_diagnostics(tmp_path: Path) -> None:
    out = _run_dir(tmp_path)
    original = (out / "failures" / "lab" / "s5_ticket_ko_r1.txt").read_text(encoding="utf-8")
    guard = _guard()
    guard.add_encenv(_write_encenv(tmp_path))
    stats = pr.export_run(out, out / "export", guard)

    dest = out / "export"
    files = sorted(str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file())
    assert files == [
        "env_check.md", "failures/lab/s5_ticket_ko_r1.txt", "probe_native.json",
        "recommend.env", "results.jsonl", "summary.md",
    ]  # proxy.log · 미분류 note.json은 복사하지 않는다
    dump = (dest / "failures" / "lab" / "s5_ticket_ko_r1.txt").read_text(encoding="utf-8")
    assert MARKER in dump
    for leaked in (FAKE_HOST, FAKE_KEY[:8], "https://", "Bearer", "xyz", ENCENV_EXTRA[:8]):
        assert leaked not in dump, leaked
    # 원본(내부망 보관)은 그대로
    assert (out / "failures" / "lab" / "s5_ticket_ko_r1.txt").read_text(
        encoding="utf-8") == original
    assert stats["files"] == 6 and stats["failures"] == 1 and stats["masked_files"] == 1
    # 반출 폴더 전체를 진단 기준으로 다시 검사해도 0건
    for path in dest.rglob("*"):
        if path.is_file():
            assert guard.violations(path.read_text(encoding="utf-8"), diagnostic=True) == []


def _write_encenv(root: Path) -> Path:
    enc = root / ".encenv"
    enc.write_text(f"FABRIX_API_KEY={FAKE_KEY}\nOTHER={ENCENV_EXTRA}\n", encoding="utf-8")
    return enc


def test_export_refuses_marker_in_verdict_file_and_writes_nothing(tmp_path: Path) -> None:
    out = _run_dir(tmp_path)
    (out / "results.jsonl").write_text(f'{{"note": "{MARKER}"}}\n', encoding="utf-8")
    with pytest.raises(pr.ExportCheckError) as info:
        pr.export_run(out, out / "export", _guard())
    assert info.value.rules == ["leak_marker"] and MARKER not in str(info.value)
    assert not (out / "export").exists()


def test_export_refuses_when_mask_leaves_residue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _run_dir(tmp_path)
    monkeypatch.setattr(pr.ExportGuard, "mask", lambda self, text: (text, []))
    with pytest.raises(pr.ExportCheckError) as info:
        pr.export_run(out, out / "export", _guard())
    assert "url" in info.value.rules and "auth_header" in info.value.rules
    assert not (out / "export").exists()


def test_export_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    out = _run_dir(tmp_path)
    monkeypatch.setattr(pr, "load_settings", _settings)
    monkeypatch.setattr(pr, "ENCENV_FILE", _write_encenv(tmp_path))
    monkeypatch.setattr(pr, "ENV_FILE", tmp_path / "no.env")
    assert pr.main(["export", "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "[export] 파일 6개(failures 1개)" in printed and FAKE_HOST not in printed
    dump = (out / "export" / "failures" / "lab" / "s5_ticket_ko_r1.txt").read_text()
    assert "<redacted:secret:ENCENV:OTHER>" in dump

    assert pr.main(["export", "--out", str(out)]) == 2  # 반출 폴더가 비어 있지 않다
    dest = tmp_path / "elsewhere"
    (out / "summary.md").write_text(f"see https://{FAKE_HOST}/\n", encoding="utf-8")
    assert pr.main(["export", "--out", str(out), "--dest", str(dest)]) == 3
    err = capsys.readouterr().err
    assert "summary.md" in err and FAKE_HOST not in err
    assert not dest.exists() and not (tmp_path / ".elsewhere.tmp").exists()
    assert pr.main(["export", "--out", str(tmp_path / "nope")]) == 2


# ──────────────── 반출 보안 교정 (감사 M1~M4 · L1~L4 · 독립 최종 검사) ────────────────


@pytest.mark.parametrize("text", [
    'Bearer "tok123456"',
    "BEARER tok999",
    "Authorization: Basic dXNlcjpwYXNz",
    "api-key: Zm9vYmFyYmF6",
    "x-api-key: Zm9vYmFyYmF6",
    "proxy-authorization: tokABC",
    '{"x-openapi-token": "Bearer abcdefgh", "x-generative-ai-client": "clientval"}',
    "x-generative-ai-client=clientval",
])
def test_auth_header_values_detected_and_masked_to_line_end(text: str) -> None:
    guard = pr.ExportGuard()
    assert "auth_header" in guard.violations(text, diagnostic=True)
    masked, hits = guard.mask(text + "\nnext line")
    assert hits == ["auth_header"] and masked.endswith("\nnext line")
    for value in ("tok123456", "tok999", "dXNlcjpwYXNz", "Zm9vYmFyYmF6", "tokABC",
                  "abcdefgh", "clientval"):
        assert value not in masked, value
    assert guard.violations(masked, diagnostic=True) == []
    assert pr.final_scan(guard, masked, diagnostic=True) == []


def test_auth_header_names_without_value_are_not_flagged() -> None:
    """헤더 이름 안내문(값 없음)·설정 필드 이름은 걸지 않는다(probe_native.json 안내 문구)."""
    guard = pr.ExportGuard()
    for text in ("KBGenAI와 같은 헤더(x-openapi-token · x-generative-ai-client) — 추정",
                 "fabrix_api_key: 설정됨"):
        assert guard.violations(text) == [] and pr.final_scan(guard, text, diagnostic=True) == []


@pytest.mark.parametrize("text", [
    r"https:\/\/kb.example\/x",
    "https%3A%2F%2Fkb.example%2Fx",
    "ftp://u:p@h.example/f",
    "wss://h.example/socket",
])
def test_scheme_agnostic_urls_detected_and_masked(text: str) -> None:
    guard = pr.ExportGuard()
    assert "url" in guard.violations(text)
    masked, hits = guard.mask(f"see {text} end")
    assert masked == "see <url> end" and hits == ["url"]
    assert pr.final_scan(guard, masked, diagnostic=True) == []


def test_host_match_ignores_case() -> None:
    guard = _guard()
    upper = FAKE_HOST.upper()
    assert "host:FABRIX_BASE_URL" in guard.violations(f"at {upper}/x")
    masked, _ = guard.mask(f"at {upper} and {FAKE_HOST.title()}")
    assert FAKE_HOST not in masked.lower()
    assert "final:host:FABRIX_BASE_URL" in pr.final_scan(guard, f"x {upper}", diagnostic=False)


@pytest.mark.parametrize("variant", [
    FAKE_HOST.replace("gw", "g\u200bw"),  # 제로폭 공백
    "ｆａｂｒｉｘ" + FAKE_HOST[6:],  # 전각
    FAKE_HOST.replace(".", "%2E"),  # 퍼센트 인코딩
])
def test_final_scan_catches_host_variants_mask_misses(tmp_path: Path, variant: str) -> None:
    """가림은 원문에만 한다 — 변종이 남으면 정규화 사본의 독립 검사가 거부한다."""
    guard = _guard()
    masked, _ = guard.mask(f"note {variant}")
    assert "final:host:FABRIX_BASE_URL" in pr.final_scan(guard, masked, diagnostic=True)
    out = _run_dir(tmp_path)
    (out / "failures" / "lab" / "s5_ticket_ko_r1.txt").write_text(f"x {variant}\n")
    with pytest.raises(pr.ExportCheckError) as info:
        pr.export_run(out, out / "export", guard)
    assert info.value.rules == ["final:host:FABRIX_BASE_URL"]
    assert not (out / "export").exists() and not (out / ".export.tmp").exists()


def test_ipv4_masked_in_diagnostic_files_only() -> None:
    guard = pr.ExportGuard()
    text = "up 10.20.30.40:8443 and 192.168.0.1 · python 3.13.1"
    assert "ipv4" in guard.violations(text, diagnostic=True)
    assert "ipv4" not in guard.violations(text)
    masked, hits = guard.mask(text)
    assert masked == "up <ip> and <ip> · python 3.13.1" and hits == ["ipv4"]
    assert pr.final_scan(guard, masked, diagnostic=True) == []


def test_secret_tail_fragment_and_short_values() -> None:
    guard = pr.ExportGuard()
    guard.add_secret("K", "sk-ABCDEFGH-1234567890")
    guard.add_secret("S", "abc1234")  # 6~7자는 전체 비교
    assert guard.violations("tail 34567890 only") == ["secret:K"]
    assert guard.violations("head sk-ABCDE") == ["secret:K"]  # 앞 8자
    assert guard.violations("sk-ABCD · 4567890") == []  # 8자 미만 조각은 검사 밖
    assert guard.violations("x abc1234 y") == ["secret:S"] and guard.violations("abc123") == []
    masked, _ = guard.mask("v=1234567890")
    assert "34567890" not in masked
    assert pr.final_scan(guard, "ｓｋ-ABCDEFGH", diagnostic=True) == ["final:secret:K"]


def test_encenv_parsed_with_dotenv_rules(tmp_path: Path) -> None:
    enc = tmp_path / ".encenv"
    enc.write_text(
        "export A=enc-value-AAAA1111\n"
        "B=val-secret-123 # inline comment\n"
        "C='quoted # not comment'\n"
        "EMPTY=\n",
        encoding="utf-8",
    )
    guard = pr.ExportGuard()
    assert guard.add_encenv(enc) == 3
    values = set(guard.secret_values.values())
    assert values == {"enc-value-AAAA1111", "val-secret-123", "quoted # not comment"}
    assert guard.violations("comment") == []  # 인라인 주석은 값이 아니다
    assert pr.ExportGuard().add_encenv(tmp_path / "missing") == 0


def test_export_cli_refuses_without_encenv_unless_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    out = _run_dir(tmp_path)
    monkeypatch.setattr(pr, "load_settings", _settings)
    monkeypatch.setattr(pr, "ENV_FILE", tmp_path / "no.env")
    monkeypatch.setattr(pr, "ENCENV_FILE", tmp_path / "missing.encenv")
    assert pr.main(["export", "--out", str(out)]) == 2
    assert ".encenv" in capsys.readouterr().err
    assert not (out / "export").exists()
    empty = tmp_path / "empty.encenv"
    empty.write_text("# only comment\nX=\n", encoding="utf-8")
    monkeypatch.setattr(pr, "ENCENV_FILE", empty)
    assert pr.main(["export", "--out", str(out)]) == 2
    assert pr.main(["export", "--out", str(out), "--allow-no-encenv"]) == 0
    assert (out / "export" / "summary.md").is_file()


def test_export_collects_url_hosts_from_env_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _run_dir(tmp_path)
    env = tmp_path / ".env"
    env.write_text("OTHER_URL=https://Other-GW.corp.example:9443/api\n", encoding="utf-8")
    monkeypatch.setattr(pr, "load_settings", _settings)
    monkeypatch.setattr(pr, "ENV_FILE", env)
    monkeypatch.setattr(pr, "ENCENV_FILE", _write_encenv(tmp_path))
    (out / "failures" / "lab" / "s5_ticket_ko_r1.txt").write_text("host other-gw.corp.example\n")
    assert pr.main(["export", "--out", str(out)]) == 0
    dump = (out / "export" / "failures" / "lab" / "s5_ticket_ko_r1.txt").read_text()
    assert "other-gw" not in dump.lower() and "<redacted:host:ENV:OTHER_URL>" in dump
    (out / "summary.md").write_text("see OTHER-GW.CORP.EXAMPLE\n", encoding="utf-8")
    assert pr.main(["export", "--out", str(out), "--dest", str(tmp_path / "d2")]) == 3


def test_export_skips_symlinks_hidden_and_outside_files(
    tmp_path: Path, capsys: Any
) -> None:
    out = _run_dir(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("PRIVATE outside\n", encoding="utf-8")
    outside = tmp_path / "outside_dir"
    outside.mkdir()
    (outside / "x.txt").write_text("OUTSIDE\n", encoding="utf-8")
    lab = out / "failures" / "lab"
    (lab / "link.txt").symlink_to(secret)
    (out / "failures" / "linkdir").symlink_to(outside, target_is_directory=True)
    (lab / ".hidden.txt").write_text("hidden\n", encoding="utf-8")
    (out / "probe_contents.json").symlink_to(secret)
    stats = pr.export_run(out, out / "export", _guard())
    dest = out / "export"
    files = sorted(str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file())
    assert "failures/lab/link.txt" not in files and "probe_contents.json" not in files
    assert not any("hidden" in f or "linkdir" in f for f in files)
    assert all(not p.is_symlink() for p in dest.rglob("*"))
    assert stats["skipped"] == 3
    err = capsys.readouterr().err
    assert "failures/lab/link.txt" in err and "PRIVATE" not in err


def test_export_refuses_symlinked_verdict_file(tmp_path: Path) -> None:
    out = _run_dir(tmp_path)
    real = tmp_path / "real_env_check.md"
    real.write_text("# env\n", encoding="utf-8")
    (out / "env_check.md").unlink()
    (out / "env_check.md").symlink_to(real)
    with pytest.raises(pr.ExportCheckError) as info:
        pr.export_run(out, out / "export", _guard())
    assert info.value.rules == ["symlink"] and not (out / "export").exists()


def test_export_dest_rules_and_atomic_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _run_dir(tmp_path)
    with pytest.raises(pr.UsageError):
        pr.export_run(out, out / "failures" / "exp", _guard())
    afile = tmp_path / "afile"
    afile.write_text("x", encoding="utf-8")
    with pytest.raises(pr.UsageError):
        pr.export_run(out, afile, _guard())

    def broken_rename(self: Path, target: Any) -> Path:
        raise OSError("rename failed")

    monkeypatch.setattr(Path, "rename", broken_rename)
    with pytest.raises(OSError):
        pr.export_run(out, out / "export", _guard())
    assert not (out / "export").exists() and not (out / ".export.tmp").exists()


def test_export_loads_failure_raw_output_into_response_check(tmp_path: Path) -> None:
    """쓰기 시점과 같은 엄격도 — `failures/` 원출력 조각이 판정값 파일에 있으면 거부한다."""
    out = _run_dir(tmp_path)
    raw = "The owner of ticket T-3003 is Rei Tanaka from Osaka office."
    (out / "failures" / "lab" / "s1_book_en_r1_t1.txt").write_text(
        f"--- attempt 1\n{raw}\n", encoding="utf-8")
    (out / "summary.md").write_text(f"# summary\nnote: {raw[:40]}\n", encoding="utf-8")
    with pytest.raises(pr.ExportCheckError) as info:
        pr.export_run(out, out / "export", _guard())
    assert info.value.rules == ["response_text"]


def test_probe_safe_path_rejects_odd_paths() -> None:
    assert pf.safe_path("https://h/v1/chat/completions") == "/v1/chat/completions"
    assert pf.safe_path("https://h/x?token=1") == "/x"  # 질의 문자열은 애초에 싣지 않는다
    for url in ("https://h/a b", "https://h/" + "a" * 90, "https://h/%2Fsecret", "https://h",
                "https://h/a%20b"):
        assert pf.safe_path(url) == "<path>", url


# ─────────────────────────── S5 판정 · 케이스 덤프 ───────────────────────────


def _msg(*calls: tuple[str, dict[str, Any]], content: str | None = None) -> dict[str, Any]:
    tool_calls = [
        {"id": f"c{i}", "type": "function",
         "function": {"name": name, "arguments": json.dumps(args)}}
        for i, (name, args) in enumerate(calls)
    ]
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return message


class _Scripted:
    """턴마다 정해진 assistant 메시지를 돌려주는 가짜 프록시 클라이언트."""

    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self.messages = list(messages)
        self.url, self.token = "http://fake", "tok"

    async def chat(self, body: dict[str, Any]) -> pr.CallOutcome:
        return pr.CallOutcome(200, 1, None, {"emulation": "parsed", "attempts": []},
                              self.messages.pop(0))


def _run_s5(tmp_path: Path, messages: list[dict[str, Any]]) -> tuple[dict[str, Any], Path]:
    opts = pr.RunOptions("turns", "ko", "static", "system", 1, False)
    ctx = pr.RunContext(
        client=_Scripted(messages), alias="fabrix-tools", opts=opts,  # type: ignore[arg-type]
        run_id="r", out=tmp_path, guard=pr.ExportGuard(), sem=asyncio.Semaphore(1), sleep=0.0,
    )
    scen = pr.load_scenarios()["S5"]
    case = next(c for c in scen["cases"] if c["id"] == "s5_ticket_ko")
    asyncio.run(pr.run_react_case(ctx, scen, case, 1))
    records = [json.loads(line) for line in (tmp_path / "results.jsonl").read_text().splitlines()]
    dump = tmp_path / "failures" / opts.label().replace(",", "_") / "s5_ticket_ko_r1.txt"
    return next(r for r in records if r["record"] == "case"), dump


def test_s5_speculative_parallel_failure_and_dump(tmp_path: Path) -> None:
    # 2차 내부망 실측 형태 — 첫 턴에 get_ticket + 짐작한 ID의 get_person·get_office를 동시에
    case, dump = _run_s5(tmp_path, [
        _msg(("get_ticket", {"ticket_id": "T-3002"}), ("get_person", {"person_id": "u-1"}),
             ("get_office", {"office_id": "of-1"})),
        _msg(content="The verification code is QK-2096."),
    ])
    assert case["speculative_parallel"] is True
    assert case["completed"] is True and case["reflected"] is False
    assert case["domain_tools_used"] is True
    assert case["failure_type"] == "speculative_parallel"
    text = dump.read_text(encoding="utf-8")
    assert "failure=speculative_parallel" in text
    assert 'call get_person {"person_id": "u-1"}' in text
    assert 'result get_person {"person_id": "u-1"} → not_found' in text
    assert 'result get_ticket {"ticket_id": "T-3002"} → found' in text
    assert "--- final answer" in text and "QK-2096" in text


def test_s5_no_tool_use_is_separated(tmp_path: Path) -> None:
    case, dump = _run_s5(tmp_path, [_msg(content="담당자 정보는 포털에서 확인하세요.")])
    assert (case["completed"], case["domain_tools_used"], case["failure_type"]) == (
        True, False, "no_tool_use",
    )
    assert case["speculative_parallel"] is False
    assert "포털에서 확인" in dump.read_text(encoding="utf-8")


def test_s5_speculative_but_reflected_keeps_flag_only(tmp_path: Path) -> None:
    case, dump = _run_s5(tmp_path, [
        _msg(("get_ticket", {"ticket_id": "T-3002"}), ("get_person", {"person_id": "u-1"})),
        _msg(("get_person", {"person_id": "u-71"})),
        _msg(("get_office", {"office_id": "of-3"})),
        _msg(content="Code QK-2096, office Cedar Hall."),
    ])
    assert case["speculative_parallel"] is True and case["reflected"] is True
    assert case["failure_type"] is None
    assert not dump.exists()  # 성공 케이스는 덤프하지 않는다


def test_s5_stop_after_first_tool_stays_history(tmp_path: Path) -> None:
    case, _ = _run_s5(tmp_path, [
        _msg(("get_ticket", {"ticket_id": "T-3002"})),
        _msg(content="The ticket exists."),
    ])
    assert case["failure_type"] == "history" and case["speculative_parallel"] is False


# ─────────────────────────── 지표 · summary ───────────────────────────


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


def _s1(opts: pr.RunOptions, ok: int = 10, total: int = 10) -> list[dict[str, Any]]:
    rows = [_row(opts, "call", "S1") for _ in range(ok)]
    rows += [_row(opts, "call", "S1", http_status=502, error_code="tool_call_invalid",
                  format_ok=False) for _ in range(total - ok)]
    return rows


def _cases(opts: pr.RunOptions, scenario: str, good: int, total: int) -> list[dict[str, Any]]:
    return [
        _row(opts, "case", scenario, reflected=i < good,
             failure_type=None if i < good else "history")
        for i in range(total)
    ]


def test_summary_has_s5_columns_failure_line_and_completion_note() -> None:
    o = _opts()
    records = [
        _row(o, "case", "S5", completed=True, reflected=False, failure_type="no_tool_use",
             speculative_parallel=False),
        _row(o, "case", "S5", completed=True, reflected=False,
             failure_type="speculative_parallel", speculative_parallel=True),
        _row(o, "case", "S5", completed=True, reflected=True, speculative_parallel=True),
        _row(o, "case", "S5", completed=True, reflected=True),  # 옛 결과 줄(플래그 없음)
    ]
    m = pr.label_metrics(records)
    assert m["no_tool_s5"] == (1, 4) and m["spec_s5"] == (1, 4) and m["spec_flag_s5"] == 2
    assert m["failures"]["speculative_parallel"] == 1 and m["failures"]["no_tool_use"] == 1
    summary = pr.render_summary(records, None, None, pr.Recommendation())
    assert "| 도구 미사용(S5) | 추측 병렬(S5) | 완주(S6) |" in summary
    assert "| 25.0% (1/4) | 25.0% (1/4) |" in summary
    assert "추측 병렬 1" in summary  # 실패 유형 줄
    assert ("- S5 도구 미사용: 1건 · 추측 병렬(실패 유형): 1건 · "
            "추측 병렬 플래그(반영 성공 포함): 2건") in summary
    assert "완주율(S5)은 도구 0개 평문도 완주로 센다(정의 결함) — 판정은 반영률로 본다." in summary
    assert "| 완주율(S5) | 100.0% (4/4) | ≥90% | 충족 |" in summary  # 합격선 행은 그대로
    header = next(line for line in summary.splitlines() if line.startswith("| 라벨 |"))
    sep = next(line for line in summary.splitlines() if line.startswith("|---|---:|"))
    assert header.count("|") == sep.count("|")
    assert "pr.export" not in summary and "`poc_run.py export`" in summary


# ─────────────────────────── 권장값 — 반영률 근거 ───────────────────────────


def test_lang_uses_s6_reflect_over_s5_completion() -> None:
    en, ko = _opts(protocol_lang="en"), _opts(protocol_lang="ko")
    # 2차 실측 형태 — 형식 동률 · S5 완주율 동률(100%) · S6 반영 en 2/10 · ko 8/10
    records = _s1(en) + _s1(ko) + _cases(en, "S5", 14, 20) + _cases(ko, "S5", 4, 5)
    records += _cases(en, "S6", 2, 10) + _cases(ko, "S6", 8, 10)
    rec = pr.compute_recommendation(records, None, None)
    assert rec.values["PROTOCOL_LANG"] == "ko"
    assert "⑤b 선택 기준: 반영률(S6) → lang=ko" in rec.basis
    assert any(b.startswith("⑤b lang=ko") and "S6 80.0% (8/10)" in b for b in rec.basis)


def test_lang_falls_back_to_s5_reflect_then_format_gate() -> None:
    en, ko = _opts(protocol_lang="en"), _opts(protocol_lang="ko")
    records = _s1(en) + _s1(ko) + _cases(en, "S5", 3, 5) + _cases(ko, "S5", 5, 5)
    records += _cases(en, "S6", 9, 10)  # ko에 S6이 없으면 S6으로 비교하지 않는다
    rec = pr.compute_recommendation(records, None, None)
    assert rec.values["PROTOCOL_LANG"] == "ko"
    assert "⑤b 선택 기준: 반영률(S5) → lang=ko" in rec.basis

    gated = _s1(en, 10, 10) + _s1(ko, 8, 10) + _cases(en, "S5", 1, 5) + _cases(ko, "S5", 5, 5)
    rec = pr.compute_recommendation(gated, None, None)
    assert rec.values["PROTOCOL_LANG"] == "en"  # 형식 유효율 2%p 초과 차이가 먼저다


def test_fewshot_picks_highest_s6_reflect_with_s6_only_labels() -> None:
    # R1·2차 S6 few-shot 비교 형태 — none·dynamic은 S6만, static은 S1·S5·S6
    none, static, dynamic = (_opts(protocol_lang="ko", fewshot=m)
                             for m in ("none", "static", "dynamic"))
    records = _s1(static) + _cases(static, "S5", 4, 5)
    records += _cases(none, "S6", 3, 10) + _cases(static, "S6", 8, 10)
    records += _cases(dynamic, "S6", 3, 10)
    # en 쪽 ⑤a 그룹(S6 없음 · S5 반영은 none이 최고)이 더 최근이어도 S6 그룹을 고른다
    for mode, good in (("none", 5), ("static", 3), ("dynamic", 2)):
        o = _opts(fewshot=mode)
        records += _s1(o) + _cases(o, "S5", good, 5)
    rec = pr.compute_recommendation(records, None, None)
    assert (rec.values["FEWSHOT"], rec.values["FEWSHOT_PLACEMENT"]) == ("static", "system")
    assert any("반영률(S6) 최고" in b and "fewshot=static" in b for b in rec.basis)
    assert any(b.startswith("⑤a fewshot=none") and "S6 30.0% (3/10)" in b for b in rec.basis)


def test_fewshot_format_threshold_excludes_low_mode_even_if_reflect_higher() -> None:
    low = _opts(fewshot="dynamic")
    records = _s1(_opts(fewshot="static"), 10, 10) + _cases(_opts(fewshot="static"), "S5", 2, 5)
    records += _s1(low, 8, 10) + _cases(low, "S5", 5, 5)  # 형식 80% <90%
    rec = pr.compute_recommendation(records, None, None)
    assert rec.values["FEWSHOT"] == "static"


def test_fewshot_reflect_tie_within_two_points_prefers_static_and_partial_uses_format() -> None:
    records = []
    for mode, good in (("none", 10), ("static", 10), ("dynamic", 9)):
        o = _opts(fewshot=mode)
        records += _s1(o) + _cases(o, "S5", good, 10)
    assert pr.compute_recommendation(records, None, None).values["FEWSHOT"] == "static"

    partial = _s1(_opts(fewshot="none"), 10, 10) + _cases(_opts(fewshot="none"), "S5", 5, 5)
    partial += _s1(_opts(fewshot="dynamic"), 10, 10)  # dynamic에 S5가 없다 → 형식으로 고른다
    rec = pr.compute_recommendation(partial, None, None)
    assert rec.values["FEWSHOT"] == "dynamic"
    assert any("형식 유효율 최고" in b for b in rec.basis)


# ─────────────────────────── F1 탐침 ───────────────────────────


def test_path_candidates_use_base_origin_only() -> None:
    base = f"https://{FAKE_HOST}:8443/kbgenai/v1/chat"
    assert pf.path_candidates(base, ["/v1/chat/completions", "native/x"]) == [
        ("--path#1", f"https://{FAKE_HOST}:8443/v1/chat/completions"),
        ("--path#2", f"https://{FAKE_HOST}:8443/native/x"),
    ]
    assert pf.path_candidates("", []) == []
    with pytest.raises(pr.UsageError):
        pf.path_candidates("", ["/v1/chat/completions"])


def test_probe_native_zero_candidates_warns_and_summary_says_so(
    tmp_path: Path, capsys: Any
) -> None:
    settings = make_settings(fabrix_native_url="")
    result = asyncio.run(pf.probe_native(settings, tmp_path, pr.ExportGuard(), []))
    assert result["candidates"] == []
    assert "후보 0개" in capsys.readouterr().err
    summary = pr.render_summary([], result, None, pr.Recommendation())
    assert "F1: 후보 0개(FABRIX_NATIVE_URL·--url·--path 미지정) — 네이티브 미확인" in summary
    assert "| 후보 | 경로 |" not in summary


def test_probe_native_records_path_not_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_once(settings: Any, url: str, guard: Any) -> dict[str, Any]:
        return {"http_status": 200, "error": None, "endpoint_exists": True, "tool_calls": True}

    monkeypatch.setattr(pf, "_native_once", fake_once)
    settings = _settings()
    guard = pr.ExportGuard.build([settings], [])
    extra = pf.path_candidates(settings.fabrix_base_url, ["/v1/chat/completions"])
    asyncio.run(pf.probe_native(settings, tmp_path, guard, extra))
    text = (tmp_path / pr.PROBE_NATIVE).read_text(encoding="utf-8")
    data = json.loads(text)
    assert data["candidates"][0]["source"] == "--path#1"
    assert data["candidates"][0]["path"] == "/v1/chat/completions"
    assert FAKE_HOST not in text and "https" not in text
    summary = pr.render_summary([], data, None, pr.Recommendation())
    assert "| --path#1 | /v1/chat/completions | 200 | — | 있음 | 있음 |" in summary


def test_probe_cli_dry_run_with_path(tmp_path: Path) -> None:
    out = tmp_path / "out"
    assert pf.main(["native", "--dry-run", "--path", "/native/v1/chat/completions",
                    "--out", str(out)]) == 0
    data = json.loads((out / pr.PROBE_NATIVE).read_text(encoding="utf-8"))
    row = next(c for c in data["candidates"] if c["source"] == "--path#1")
    assert row["path"] == "/native/v1/chat/completions" and row["tool_calls"] is True
    assert "127.0.0.1" not in (out / pr.PROBE_NATIVE).read_text(encoding="utf-8")
