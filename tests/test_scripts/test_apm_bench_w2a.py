"""plans/164 W2a — 진단 가능한 반출 · run 디렉터리 · 격리 보관 · `--regate`/`--purge`.

LLM·네트워크 0 — 드라이버는 `test_apm_bench_cli._fake_driver` 대역이다. 격리 기준 경로는 tmp 로
돌린다(실제 홈 `~/apm_bench_quarantine`에 쓰지 않는다).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import stat
import time
from pathlib import Path
from typing import Any

import pytest

from scripts.apm_bench import REPO_ROOT, SCENARIOS_PATH
from scripts.apm_bench import __main__ as cli
from scripts.apm_bench import audit_digest as ad
from scripts.apm_bench import driver as dv
from scripts.apm_bench import hygiene as hy
from scripts.apm_bench import judge as jd
from scripts.apm_bench import record as rc
from scripts.apm_bench import rundir as rd
from scripts.apm_bench import safe_summary as ss
from scripts.apm_bench import scenarios as sc
from tests.test_scripts.test_apm_bench_cli import SCENARIO, _fake_driver

RUN_USER = "zz-run-user-9"
CANARY = "canary@example.invalid"  # placeholders.e1.yaml canaries
FAILED_FILES = {"leak_check.json", "summary_safe.json", "run.log", "run_meta.json",
                "manifest.sha256", "scenarios.yaml"}


@pytest.fixture
def qbase(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """격리 기본 경로를 tmp 로."""
    base = tmp_path / "quarantine"
    monkeypatch.setattr(rd, "default_quarantine_base", lambda: base)
    return base


@pytest.fixture(scope="module")
def vocab_registry() -> tuple[ss.SafeVocab, sc.RegistryInfo]:
    registry = sc.load_registry_info()
    return ss.build_vocab(sc.load_scenarios(), hy.build_code_vocab(registry)), registry


def _args(out: Path, *extra: str) -> Any:
    return cli.build_parser().parse_args(
        ["--run", "--env", "e1", "--only", SCENARIO, "--out", str(out), *extra])


def _leaky_driver(value: str) -> Any:
    """관문이 막는 값(사용자 정보)을 치환 대상이 아닌 칸에 심는다(`test_apm_bench_cli` 관문 실패
    모양)."""
    base = _fake_driver({})

    def run(*a: Any, **k: Any) -> Any:
        records, meta = base(*a, **k)
        records[0]["observed"]["degraded_reason"] = f"조회자 {value}"
        return records, meta

    return run


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _manifest(out: Path) -> dict[str, str]:
    rows = (out / rd.MANIFEST_FILE).read_text(encoding="utf-8").splitlines()
    return {name: digest for digest, name in (r.split("  ", 1) for r in rows)}


# --- 오류 코드 사본 통합(§4.3) -----------------------------------------------------------------


def test_error_code_copies_unified_in_record() -> None:
    assert ad.GATEWAY_ERROR_CODES is rc.GATEWAY_ERROR_CODES
    assert jd.INSTANCE_UNRESOLVED is rc.INSTANCE_UNRESOLVED
    assert rc.INSTANCE_UNRESOLVED in rc.GATEWAY_ERROR_CODES
    pattern = re.compile(r"^(GATEWAY_ERROR_CODES|INSTANCE_UNRESOLVED)\b.*=", re.M)
    owners = [p.name for p in (REPO_ROOT / "scripts" / "apm_bench").glob("*.py")
              if pattern.search(p.read_text(encoding="utf-8"))]
    assert owners == ["record.py"]


# --- 안전 요약 어휘 -------------------------------------------------------------------------


def _judged_record(registry: sc.RegistryInfo) -> dict[str, Any]:
    records, _ = _fake_driver({})(
        [s for s in sc.load_scenarios() if s.id == SCENARIO], env="e1",
        placeholders=dv.load_placeholders(cli.PLACEHOLDERS_E1))
    jd.judge_records([s for s in sc.load_scenarios() if s.id == SCENARIO], records, registry)
    return records[0]


@pytest.mark.parametrize("text, code", [
    ("heavy_off", "heavy_off"),
    ("e2_skip", "e2_skip"),
    (dv.HEAVY_GATE_REASON, dv.HEAVY_GATE_REASON),
    ("heavy_cap:3", "heavy_cap:3"),
    ("aborted:quota_429", "aborted:quota_429"),
    ("aborted:budget:nope-src", None),
    ("turn_error:ord-was01 접속 실패", "turn_error"),
    ("placeholder_missing:inst_1", "placeholder_missing:inst_1"),
    ("placeholder_missing:ord-was01", None),
    ("was_once:no-such-cov", None),
    ("free sentence ord-was01", None),
])
def test_skip_code_whitelist(vocab_registry: Any, text: str, code: str | None) -> None:
    vocab, _ = vocab_registry
    assert ss.skip_code(text, vocab) == code


def test_out_of_vocab_strings_dropped(vocab_registry: Any) -> None:
    """어휘 밖 문자열(분류·locus·도구·오류 코드·되묻기·건너뜀 사유)은 버리고 건수만."""
    vocab, registry = vocab_registry
    good = _judged_record(registry)
    bad = json.loads(json.dumps(good))
    bad["verdict"]["categories"] = ["evil-category"]
    bad["verdict"]["turn_pass"] = False
    bad["verdict"]["locus"] = ["args.{{host_1}}", "F1:evil-host:args.x", "F1:apm.instances"]
    bad["observed"]["calls"][0].update(tool="evil_tool", error="SECRET_CODE ord-was01")
    bad["observed"]["clarify_kind"] = "evil-kind"
    skipped = json.loads(json.dumps(good))
    skipped["verdict"]["skipped"] = "turn_error:ord-secret01 접속 실패"
    other = json.loads(json.dumps(good))
    other["verdict"]["skipped"] = "자유 사유 ord-secret01"
    records = [bad, skipped, other]
    summary = jd.summarize([good])
    summary["categories"]["evil-category"] = 1
    doc = ss.build_safe_summary(
        records, {"inventory_names": ["ord-secret01"], "gateway_pid": 4242}, run_id="x",
        env="e1", summary=summary, vocab=vocab, registry=registry,
        directional=True, gate_passed=False, violations=[])
    text = json.dumps(doc, ensure_ascii=False)
    for raw in ("evil", "SECRET_CODE", "ord-secret01", "{{host_1}}", "4242", "접속 실패"):
        assert raw not in text, raw
    assert ss.violations(json.loads(text), vocab) == 0
    row = doc["turns"][0]
    assert row["categories"] == [] and row["locus"] == ["F1:apm.instances"]
    assert row["calls"][0]["tool"] is None and row["calls"][0]["code"] is None
    assert row["clarify_kind"] is None
    assert doc["turns"][1]["skipped"] == "turn_error" and doc["turns"][2]["skipped"] is None
    assert doc["run_id"] is None  # run id 형식 밖
    dropped = doc["dropped"]
    for key in ("categories_dropped", "locus_dropped", "tools_dropped", "codes_dropped",
                "clarify_kind_dropped", "skip_other"):
        assert dropped.get(key), key
    assert doc["summary"]["locus_top"] == {"F1:apm.instances": 1}


def test_double_gate_blocks_inventory_name(vocab_registry: Any) -> None:
    """① 어휘 대조를 통과해도(어휘에 원이름이 섞인 경우) ② 관문 원이름 규칙이 막는다."""
    vocab, _ = vocab_registry
    gate = hy.make_gate(inventory_names=["ord-was01"], placeholders={}, user_values={},
                        canaries=[CANARY])
    doc = {"schema": 1, "turns": [{"id": "ord-was01"}]}
    text, rules = ss.check_safe_summary(doc, vocab, gate)
    assert text is None and "closed_vocab" in rules and set(rules) - {"closed_vocab"}
    leaky = dataclasses.replace(vocab, literals=vocab.literals | {"ord-was01"})
    text, rules = ss.check_safe_summary(doc, leaky, gate)
    assert text is None and "closed_vocab" not in rules and rules
    text, rules = ss.check_safe_summary({"schema": 1, "turns": []}, vocab, gate)
    assert text is not None and rules == {}


def test_run_meta_doc_drops_out_of_vocab_leaves() -> None:
    doc = ss.run_meta_doc({
        "run_id": "20261014-120000-e1-abcdef0", "env": "e9", "status": "weird",
        "started_at": "2026-10-14T12:00:00", "ended_at": "2026-10-14T12:00:01+09:00",
        "sha": "ord-was01", "flags": {"only": [SCENARIO, "ord-was01"], "user": "op01",
                                      "view_server_version": "ord-was01"},
        "gate": {"passed": True, "violations": 2, "rules": {"canary": 1, "ord-was01": 1}},
        "quarantine": "/home/x",
    }, scenario_ids=[SCENARIO])
    assert doc["run_id"] == "20261014-120000-e1-abcdef0"
    assert doc["env"] is None and doc["status"] is None and doc["sha"] is None
    assert doc["started_at"] is None and doc["ended_at"] == "2026-10-14T12:00:01+09:00"
    assert doc["flags"]["only"] == [SCENARIO] and doc["flags"]["user"] is False
    assert doc["flags"]["view_server_version"] is None
    assert doc["gate"]["rules"] == {"canary": 1} and doc["quarantine"] == "none"


def test_driver_abort_helpers_keep_vocab_only() -> None:
    tool = dv.INVENTORY_CALLS[0][0]
    summary = dv.inventory_error_summary([f"{tool}:instance_unresolved", f"{tool}:TimeoutError",
                                          f"{tool}:ord-was01 접속 실패", "evil:x"])
    assert summary == {"codes": {f"{tool}:instance_unresolved": 1},
                       "exceptions": {tool: 1}, "other": 2}
    assert dv.safe_abort_reason("budget:ord-was01", {"common"}) == "budget:other"
    assert dv.safe_abort_reason("quota_429", {"common"}) == "quota_429"
    exc = dv.RunAbortError(dv.ABORT_INVENTORY_EMPTY, ["instance_unresolved", "ord-was01"])
    assert exc.codes == ("instance_unresolved",) and "ord-was01" not in str(exc)


# --- run 디렉터리 ---------------------------------------------------------------------------


def test_passed_run_layout_meta_and_manifest(tmp_path: Path, qbase: Path) -> None:
    out = tmp_path / "out"
    seen: dict[str, Any] = {}
    base = _fake_driver({})

    def driver_run(*a: Any, **k: Any) -> Any:  # 실행 중 상태 관찰
        seen["pid"] = (out / rd.PID_FILE).read_text(encoding="utf-8").strip()
        seen["meta"] = _read_json(out / ss.RUN_META_FILE)
        return base(*a, **k)

    assert cli.cmd_run(_args(out), driver_run=driver_run) == 0
    assert seen["pid"].split()[0] == str(os.getpid())  # `<PID> <run_id> <시작 epoch>`(W2b 교정)
    running = seen["meta"]
    assert running["status"] == "running" and running["ended_at"] is None
    assert ss.ISO_RE.fullmatch(running["started_at"])  # 오프셋 포함
    meta = _read_json(out / ss.RUN_META_FILE)
    assert meta["status"] == "done" and meta["exit_code"] == 0
    assert ss.ISO_RE.fullmatch(meta["ended_at"]) and isinstance(meta["elapsed_sec"], float)
    assert meta["run_id"] == running["run_id"] and ss.RUN_ID_RE.fullmatch(meta["run_id"])
    assert meta["flags"]["only"] == [SCENARIO] and meta["flags"]["placeholders"] == "e1_default"
    assert meta["scenarios"] == {"source": "default", "copied": True, "sha256": hashlib.sha256(
        SCENARIOS_PATH.read_bytes()).hexdigest(), "copy": rd.SCENARIO_COPY}
    assert meta["gate"] == {"passed": True, "violations": 0, "rules": {}}
    assert meta["safe_summary_written"] is True and meta["quarantine"] == "none"
    assert not (out / rd.PID_FILE).exists() and not qbase.exists()
    manifest = _manifest(out)
    assert list(manifest) == list(rd.EXPORT_PASSED)
    for name, digest in manifest.items():
        assert rd.sha256_file(out / name) == digest
    safe = _read_json(out / ss.SAFE_SUMMARY_FILE)
    assert safe["gate"]["passed"] is True and safe["turns"][0]["id"] == f"{SCENARIO}#0"
    log = (out / rd.LOG_FILE).read_text(encoding="utf-8")
    assert log.startswith("# apm_bench run.log — run_id ") and "결과       : 완료" in log
    assert "대표 분류  : 실패 턴" in log and "(통과 턴 포함)" in log


def test_gate_failure_e1_end_to_end_quarantine_and_regate(
        tmp_path: Path, qbase: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    """강제 관문 실패(E1) → 반출 허용 파일만 · 격리본 700/600 · `--regate`가 고친 위생으로 통과."""
    monkeypatch.setattr(cli, "run_user_values", lambda *_a: {"login_id": RUN_USER})
    out = tmp_path / "out"
    assert cli.cmd_run(_args(out), driver_run=_leaky_driver(RUN_USER)) == 1
    assert {p.name for p in out.iterdir()} == FAILED_FILES
    meta = _read_json(out / ss.RUN_META_FILE)
    run_id = meta["run_id"]
    assert meta["status"] == "failed" and meta["exit_code"] == 1
    assert meta["gate"]["passed"] is False and meta["gate"]["rules"] == {
        "user_info": meta["gate"]["violations"]}
    assert meta["quarantine"] == "default_local" and meta["safe_summary_written"] is True
    assert list(_manifest(out)) == list(rd.EXPORT_FAILED)
    safe = _read_json(out / ss.SAFE_SUMMARY_FILE)
    assert safe["gate"]["passed"] is False and safe["gate"]["rules"] == {
        "user_info": safe["gate"]["violations"]}
    assert {i["file"] for i in safe["gate"]["items"]} == {"trace.jsonl"}
    printed = capsys.readouterr().out
    for text in (printed, (out / rd.LOG_FILE).read_text(encoding="utf-8"),
                 (out / ss.SAFE_SUMMARY_FILE).read_text(encoding="utf-8")):
        assert RUN_USER not in text and str(qbase) not in text and str(Path.home()) not in text
    assert "격리 보관 : 있음(기본 위치)" in printed
    # 격리본 — 저장소 밖 · 700/600 · 원문 재료
    qdir = qbase / run_id
    assert stat.S_IMODE(qdir.stat().st_mode) == 0o700
    assert {p.name for p in qdir.iterdir()} == set(rd.QUARANTINE_FILES)
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in qdir.iterdir())
    assert RUN_USER in (qdir / "records.json").read_text(encoding="utf-8")
    # 재관문 — 같은 재료가 그대로면 다시 실패
    assert cli.main(["--regate", run_id, "--out", str(out)]) == 1
    assert _read_json(out / ss.RUN_META_FILE)["gate"]["passed"] is False
    # 위생 수정(치환기가 그 칸을 비우게 됨)을 흉내 → 재관문 통과 · 산출물 6종 기록
    original = hy.RunSanitizer.sanitize

    def fixed(self: Any, *a: Any, **k: Any) -> Any:
        clean = original(self, *a, **k)
        for record in clean.records:
            record["observed"]["degraded_reason"] = None
        return clean

    monkeypatch.setattr(hy.RunSanitizer, "sanitize", fixed)
    capsys.readouterr()
    assert cli.main(["--regate", run_id, "--out", str(out)]) == 0
    assert set(hy.OUTPUT_FILES) <= {p.name for p in out.iterdir()}
    regated = _read_json(out / ss.RUN_META_FILE)
    assert regated["gate"]["passed"] is True and ss.ISO_RE.fullmatch(regated["regated_at"])
    assert ss.SHA_RE.fullmatch(regated["regate_sha"]) or regated["regate_sha"] is None
    assert regated["started_at"] == meta["started_at"] and regated["status"] == "failed"
    assert list(_manifest(out)) == list(rd.EXPORT_PASSED)
    assert _read_json(out / ss.SAFE_SUMMARY_FILE)["gate"]["passed"] is True
    log = (out / rd.LOG_FILE).read_text(encoding="utf-8")
    assert log.count("# regate — ") == 2 and RUN_USER not in log
    assert f"--purge {run_id}" in capsys.readouterr().out
    # 격리 디렉터리 경로로도 해석된다(run 디렉터리는 --out)
    assert cli.main(["--regate", str(qdir), "--out", str(out)]) == 0


def test_regate_rejects_untrusted_quarantine(tmp_path: Path, qbase: Path,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "run_user_values", lambda *_a: {"login_id": RUN_USER})
    out = tmp_path / "out"
    assert cli.cmd_run(_args(out), driver_run=_leaky_driver(RUN_USER)) == 1
    run_id = _read_json(out / ss.RUN_META_FILE)["run_id"]
    qdir = qbase / run_id
    os.chmod(qdir, 0o755)
    assert cli.main(["--regate", run_id, "--out", str(out)]) == 2
    os.chmod(qdir, 0o700)
    os.chmod(qdir / "records.json", 0o644)
    assert cli.main(["--regate", run_id, "--out", str(out)]) == 2
    os.chmod(qdir / "records.json", 0o600)
    real_uid = os.getuid()
    monkeypatch.setattr(rd.os, "getuid", lambda: real_uid + 1)  # 다른 계정 소유처럼
    assert cli.main(["--regate", run_id, "--out", str(out)]) == 2
    monkeypatch.setattr(rd.os, "getuid", lambda: real_uid)
    assert cli.main(["--regate", "20991231-000000-e1-0000000", "--out", str(out)]) == 2
    assert cli.main(["--regate", str(tmp_path / "nothing")]) == 2


def test_write_quarantine_refuses_existing(tmp_path: Path) -> None:
    base, out = tmp_path / "q", tmp_path / "out"
    payload = {"context.json": {"run_id": "r"}}
    final = rd.write_quarantine(base, out, "r1", payload, "scenarios: []\n")
    assert stat.S_IMODE(final.stat().st_mode) == 0o700
    assert stat.S_IMODE((final / "context.json").stat().st_mode) == 0o600
    with pytest.raises(rd.QuarantineError):
        rd.write_quarantine(base, out, "r1", payload, "")


def test_quarantine_path_inside_repo_or_run_dir_rejected(tmp_path: Path) -> None:
    out = tmp_path / "out"
    for bad, run_dir in ((REPO_ROOT / "results" / "q", out), (out / "q", out),
                         (tmp_path / "q", tmp_path / "q" / "out")):
        with pytest.raises(rd.QuarantineError):
            rd.check_quarantine_base(bad, run_dir)
    link = tmp_path / "link"
    link.symlink_to(REPO_ROOT / "results", target_is_directory=True)
    with pytest.raises(rd.QuarantineError):  # 심볼릭 링크 해석 뒤 저장소 하위
        rd.check_quarantine_base(link / "q", out)
    assert rd.check_quarantine_base(tmp_path / "q", out) == (tmp_path / "q").resolve()


def test_run_rejects_quarantine_dir_inside_repo_before_run_dir(tmp_path: Path) -> None:
    out = tmp_path / "out"
    called: list[int] = []
    code = cli.cmd_run(_args(out, "--quarantine-dir", str(REPO_ROOT / "results" / "q")),
                       driver_run=lambda *a, **k: called.append(1))
    assert code == 2 and called == [] and not out.exists()


def test_explicit_quarantine_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "run_user_values", lambda *_a: {"login_id": RUN_USER})
    out, q = tmp_path / "out", tmp_path / "myq"
    assert cli.cmd_run(_args(out, "--quarantine-dir", str(q)),
                       driver_run=_leaky_driver(RUN_USER)) == 1
    meta = _read_json(out / ss.RUN_META_FILE)
    assert meta["quarantine"] == "explicit" and (q / meta["run_id"]).is_dir()


def test_purge(tmp_path: Path, qbase: Path, capsys: pytest.CaptureFixture[str]) -> None:
    old, new = "20260901-000000-e1-0000000", "20261014-000000-e1-0000000"
    for run_id in (old, new):
        rd.write_quarantine(qbase, tmp_path / "out", run_id, {"context.json": {}}, "")
    stale = time.time() - 20 * 86400
    os.utime(qbase / old, (stale, stale))
    assert cli.main(["--purge"]) == 0
    printed = capsys.readouterr().out
    assert not (qbase / old).exists() and (qbase / new).is_dir()
    assert old in printed and f"{new} · 0일 경과" in printed and str(qbase) not in printed
    assert cli.main(["--purge", new]) == 0 and not (qbase / new).exists()
    assert cli.main(["--purge", new]) == 1  # 이미 없음
    assert cli.main(["--purge", "../etc"]) == 2  # run id 형식만


# --- 실행 로그 위생 ---------------------------------------------------------------------------


@pytest.mark.parametrize("exc, code", [
    (RuntimeError("ord-secret01 접속 실패"), 1),
    (ValueError("ord-secret01 형식"), 1),
    (dv.RunAbortError(dv.ABORT_VERIFY_FAILED, ["instance_unresolved"]), 1),
    (dv.LLMBlockedError("ord-secret01 과금 평면"), 2),
])
def test_abort_lines_carry_no_exception_text(tmp_path: Path, capsys: pytest.CaptureFixture[str],
                                             qbase: Path, exc: Exception, code: int) -> None:
    out = tmp_path / "out"

    def driver_run(*a: Any, **k: Any) -> Any:
        raise exc

    assert cli.cmd_run(_args(out), driver_run=driver_run) == code
    printed = capsys.readouterr().out
    log = (out / rd.LOG_FILE).read_text(encoding="utf-8")
    for text in (printed, log):
        assert "ord-secret01" not in text and "[중단]" in text
    meta = _read_json(out / ss.RUN_META_FILE)
    assert meta["status"] == "aborted" and meta["exit_code"] == code
    assert not (out / rd.PID_FILE).exists() and not qbase.exists()
    assert list(_manifest(out)) == ["run.log", "run_meta.json"]


def test_unexpected_exception_still_closes_run_dir(tmp_path: Path, qbase: Path) -> None:
    out = tmp_path / "out"

    def driver_run(*a: Any, **k: Any) -> Any:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        cli.cmd_run(_args(out), driver_run=driver_run)
    meta = _read_json(out / ss.RUN_META_FILE)
    assert meta["status"] == "aborted" and meta["exit_code"] is None
    assert ss.ISO_RE.fullmatch(meta["ended_at"]) and not (out / rd.PID_FILE).exists()


def test_run_log_second_gate_suppresses(tmp_path: Path, qbase: Path,
                                        capsys: pytest.CaptureFixture[str]) -> None:
    """진행 줄로 새어 나간 원문(카나리아)은 run 끝 관문 재검사가 로그를 억제로 갈아쓴다."""
    out = tmp_path / "out"
    base = _fake_driver({})

    def driver_run(*a: Any, **k: Any) -> Any:
        k["say"](f"      진행 {CANARY}")
        return base(*a, **k)

    assert cli.cmd_run(_args(out), driver_run=driver_run) == 0
    log = (out / rd.LOG_FILE).read_text(encoding="utf-8")
    assert CANARY not in log and "[위생] 로그 출력 억제 — 위반" in log
    assert log.startswith("# apm_bench run.log — run_id ") and len(log.splitlines()) == 2
    assert "[위생] 실행 로그 억제" in capsys.readouterr().out


def test_safe_summary_blocked_suppresses_console(tmp_path: Path, qbase: Path,
                                                 monkeypatch: pytest.MonkeyPatch,
                                                 capsys: pytest.CaptureFixture[str]) -> None:
    """안전 요약이 이중 관문에 걸리면 파일을 쓰지 않고 콘솔 요약 줄도 억제한다."""
    monkeypatch.setattr(ss, "check_safe_summary",
                        lambda *a, **k: (None, {"closed_vocab": 1}))
    out = tmp_path / "out"
    assert cli.cmd_run(_args(out), driver_run=_fake_driver({})) == 0
    assert not (out / ss.SAFE_SUMMARY_FILE).exists()
    printed = capsys.readouterr().out
    assert "[위생] 로그 출력 억제 — 안전 요약이 관문에 걸렸다" in printed
    assert "턴 정확도" not in printed
    assert _read_json(out / ss.RUN_META_FILE)["safe_summary_written"] is False


# --- 교정 1라운드(security-auditor M-1·M-2·L-1·L-2) -------------------------------------------


def test_quarantine_base_must_be_private_dir(tmp_path: Path) -> None:
    """이미 있는 기준 경로가 심볼릭 링크 · 그룹·기타 권한이면 거부 · 새로 만들면 상위까지 700."""
    out = tmp_path / "out"
    wide = tmp_path / "wide"
    wide.mkdir()
    os.chmod(wide, 0o777)
    with pytest.raises(rd.QuarantineError):
        rd.write_quarantine(wide, out, "r1", {"context.json": {}}, "")
    target = tmp_path / "real"
    target.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(rd.QuarantineError):
        rd.check_quarantine_base(link, out)
    fresh = tmp_path / "a" / "b"
    rd.write_quarantine(fresh, out, "r1", {"context.json": {}}, "")
    assert stat.S_IMODE((tmp_path / "a").stat().st_mode) == 0o700
    assert stat.S_IMODE(fresh.stat().st_mode) == 0o700


def test_purge_only_touches_quarantines(tmp_path: Path, qbase: Path) -> None:
    """run id 형식 · 실 디렉터리 · context.json · 신뢰 검사 통과분만 지운다(심볼릭 링크 · 형식 밖 ·
    context 없음은 남는다) — 형식 밖 이름은 출력하지 않는다."""
    old = "20260901-000000-e1-0000000"
    rd.write_quarantine(qbase, tmp_path / "out", old, {"context.json": {}}, "")
    no_ctx = qbase / "20260902-000000-e1-0000000"
    no_ctx.mkdir(mode=0o700)
    other = qbase / "my_notes"
    other.mkdir(mode=0o700)
    victim = tmp_path / "victim"
    victim.mkdir()
    (qbase / "20260903-000000-e1-0000000").symlink_to(victim, target_is_directory=True)
    stale = time.time() - 30 * 86400
    for path in (qbase / old, no_ctx, other):
        os.utime(path, (stale, stale))
    removed, kept, skipped = rd.purge(qbase, None)
    assert removed == [old] and kept == [] and skipped == 3
    assert no_ctx.is_dir() and other.is_dir() and victim.is_dir()


def test_regate_dir_form_checks_parent_and_log_symlink(tmp_path: Path, qbase: Path,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "run_user_values", lambda *_a: {"login_id": RUN_USER})
    out = tmp_path / "out"
    assert cli.cmd_run(_args(out), driver_run=_leaky_driver(RUN_USER)) == 1
    run_id = _read_json(out / ss.RUN_META_FILE)["run_id"]
    qdir = qbase / run_id
    os.chmod(qbase, 0o755)  # 디렉터리 경로 형태도 부모(기준 경로) 검사를 받는다
    assert cli.main(["--regate", str(qdir), "--out", str(out)]) == 2
    os.chmod(qbase, 0o700)
    (out / rd.LOG_FILE).unlink()
    elsewhere = tmp_path / "elsewhere.log"
    elsewhere.write_text("", encoding="utf-8")
    (out / rd.LOG_FILE).symlink_to(elsewhere)
    assert cli.main(["--regate", str(qdir), "--out", str(out)]) == 2
    assert elsewhere.read_text(encoding="utf-8") == ""
