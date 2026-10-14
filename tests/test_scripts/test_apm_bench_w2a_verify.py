"""plans/164 W2a · W1 3라운드 독립 검증(verifier) — 누출 경로 · 격리 경로 · 재관문 · purge 범위.

결함은 `xfail(strict=True, reason="V-n: …")`로 남긴다(고쳐지면 XPASS 로 깨져 표지를 걷게 한다).
LLM·네트워크 0 — 드라이버는 `test_apm_bench_cli._fake_driver` 대역 · 격리 기준은 tmp.
"""

from __future__ import annotations

import json
import os
import re
import stat
import time
from pathlib import Path
from typing import Any

import pytest

from scripts.apm_bench import REPO_ROOT
from scripts.apm_bench import __main__ as cli
from scripts.apm_bench import hygiene as hy
from scripts.apm_bench import rundir as rd
from scripts.apm_bench import safe_summary as ss
from scripts.apm_bench import scenarios as sc
from tests.test_scripts.test_apm_bench_cli import SCENARIO, _fake_driver

RAW_NAME = "zzordwas77"  # 인벤토리 원이름(합성)
RUN_USER = "zz-run-user-9"
CANARY = "canary@example.invalid"


@pytest.fixture
def qbase(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    base = tmp_path / "quarantine"
    monkeypatch.setattr(rd, "default_quarantine_base", lambda: base)
    return base


@pytest.fixture(scope="module")
def vocab() -> ss.SafeVocab:
    registry = sc.load_registry_info()
    return ss.build_vocab(sc.load_scenarios(), hy.build_code_vocab(registry))


def _args(out: Path, *extra: str) -> Any:
    return cli.build_parser().parse_args(
        ["--run", "--env", "e1", "--only", SCENARIO, "--out", str(out), *extra])


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _manifest_names(out: Path) -> list[str]:
    rows = (out / rd.MANIFEST_FILE).read_text(encoding="utf-8").splitlines()
    return [r.split("  ", 1)[1] for r in rows]


def _leak_name_driver() -> Any:
    """인벤토리 원이름을 reason 문장·건너뜀 사유·진행 줄에 싣는 드라이버."""
    base = _fake_driver({})

    def run(*a: Any, **k: Any) -> Any:
        records, meta = base(*a, **k)
        meta["inventory_names"] = [*meta["inventory_names"], RAW_NAME]
        meta["aborted"] = f"budget:{RAW_NAME}"
        observed = records[0]["observed"]
        for call in observed.get("calls") or []:
            call["error"] = f"{RAW_NAME} 접속 실패 Traceback"
        records[0]["verdict_hint"] = RAW_NAME
        return records, meta

    return run


def _reinject_after_sanitize(monkeypatch: pytest.MonkeyPatch) -> None:
    """치환기가 놓친 것처럼 원이름을 치환 뒤 레코드에 다시 넣는다(관문 실패 유도)."""
    original = hy.RunSanitizer.sanitize

    def leaky(self: Any, *a: Any, **k: Any) -> Any:
        clean = original(self, *a, **k)
        clean.records[0]["observed"]["degraded_reason"] = f"대상 {RAW_NAME}"
        return clean

    monkeypatch.setattr(hy.RunSanitizer, "sanitize", leaky)


# --- ① 관문 실패 경로의 반출 파일에 원문이 없다 -------------------------------------------------


def test_gate_failure_exports_carry_no_raw_name(tmp_path: Path, qbase: Path,
                                                vocab: ss.SafeVocab,
                                                monkeypatch: pytest.MonkeyPatch,
                                                capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli, "run_user_values", lambda *_a: {"login_id": RUN_USER})
    _reinject_after_sanitize(monkeypatch)
    out = tmp_path / "out"
    assert cli.cmd_run(_args(out), driver_run=_leak_name_driver()) == 1
    meta = _read_json(out / ss.RUN_META_FILE)
    assert meta["gate"]["passed"] is False and meta["quarantine"] == "default_local"
    exported = _manifest_names(out)
    assert set(exported) == set(rd.EXPORT_FAILED)
    printed = capsys.readouterr().out
    for text in [printed, *((out / n).read_text(encoding="utf-8") for n in exported)]:
        for raw in (RAW_NAME, RUN_USER, CANARY, "접속 실패", "Traceback", str(tmp_path),
                    str(Path.home())):
            assert raw not in text, raw
    safe = _read_json(out / ss.SAFE_SUMMARY_FILE)
    assert safe["run"]["aborted"] == "budget:other"
    assert ss.violations(safe, vocab) == 0
    # 격리본은 원문을 담고 저장소 밖 · 700/600
    qdir = qbase / meta["run_id"]
    assert not rd.inside(qdir.resolve(), REPO_ROOT.resolve())
    assert stat.S_IMODE(qdir.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in qdir.iterdir())
    assert RAW_NAME in (qdir / "meta.json").read_text(encoding="utf-8")


def test_regate_reproduces_same_safe_summary(tmp_path: Path, qbase: Path,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    """위생 무변경 재관문 → 같은 판정·같은 관문 결과(턴 행·집계·규칙 동일)."""
    monkeypatch.setattr(cli, "run_user_values", lambda *_a: {"login_id": RUN_USER})
    _reinject_after_sanitize(monkeypatch)
    out = tmp_path / "out"
    assert cli.cmd_run(_args(out), driver_run=_leak_name_driver()) == 1
    first = _read_json(out / ss.SAFE_SUMMARY_FILE)
    run_id = _read_json(out / ss.RUN_META_FILE)["run_id"]
    assert cli.main(["--regate", run_id, "--out", str(out)]) == 1
    again = _read_json(out / ss.SAFE_SUMMARY_FILE)
    for key in ("turns", "summary", "gate", "calls", "inventory", "dropped"):
        assert again[key] == first[key], key
    assert RAW_NAME not in (out / rd.LOG_FILE).read_text(encoding="utf-8")


# --- ② 격리 경로 거부 ---------------------------------------------------------------------


def test_quarantine_rejects_symlink_and_dotdot_into_run_dir_or_repo(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    link = tmp_path / "qlink"
    link.symlink_to(out, target_is_directory=True)
    rel_repo = tmp_path / ".." / os.path.relpath(REPO_ROOT / "results", tmp_path.parent)
    for bad in (link, link / "sub", out / ".." / "out" / "q", rel_repo):
        with pytest.raises(rd.QuarantineError):
            rd.check_quarantine_base(bad, out)


def test_regate_out_inside_quarantine_rejected(tmp_path: Path, qbase: Path,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "run_user_values", lambda *_a: {"login_id": RUN_USER})
    _reinject_after_sanitize(monkeypatch)
    out = tmp_path / "out"
    assert cli.cmd_run(_args(out), driver_run=_leak_name_driver()) == 1
    run_id = _read_json(out / ss.RUN_META_FILE)["run_id"]
    assert cli.main(["--regate", run_id, "--out", str(qbase / "inside")]) == 2
    assert not (qbase / "inside").exists()


_ALT = Path(str(REPO_ROOT).swapcase())


@pytest.mark.skipif(not _ALT.exists(), reason="대소문자 구분 파일 시스템(Linux)")
def test_quarantine_case_variant_repo_path_rejected(tmp_path: Path) -> None:
    with pytest.raises(rd.QuarantineError):
        rd.check_quarantine_base(_ALT / "results" / "q", tmp_path / "out")


# --- ③ --purge 범위 -----------------------------------------------------------------------


def test_purge_without_id_keeps_non_run_dirs(tmp_path: Path,
                                             capsys: pytest.CaptureFixture[str]) -> None:
    base = tmp_path / "shared"
    victim = base / "my_project"
    victim.mkdir(parents=True)
    (victim / "keep.txt").write_text("x", encoding="utf-8")
    stale = time.time() - 30 * 86400
    os.utime(victim, (stale, stale))
    cli.main(["--purge", "--quarantine-dir", str(base)])
    assert victim.is_dir()


def test_purge_rejects_base_inside_repo(tmp_path: Path) -> None:
    missing = REPO_ROOT / "results" / "zz_verify_no_such_quarantine"  # 없는 경로 — 지울 것 없음
    assert not missing.exists()
    assert cli.main(["--purge", "--quarantine-dir", str(missing)]) == 2


# --- ④ 예외 원문 ---------------------------------------------------------------------------


def test_run_abort_error_sites_use_fixed_phrases() -> None:
    text = (REPO_ROOT / "scripts" / "apm_bench" / "driver.py").read_text(encoding="utf-8")
    sites = re.findall(r"raise RunAbortError\(\s*([^,)\s]+)", text)
    assert sites and all(s.startswith("ABORT_") for s in sites), sites


def test_unexpected_exception_status_failed_and_log_clean(
        tmp_path: Path, qbase: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "out"

    def driver_run(*a: Any, **k: Any) -> Any:
        raise KeyError(RAW_NAME)

    with pytest.raises(KeyError):
        cli.cmd_run(_args(out), driver_run=driver_run)
    meta = _read_json(out / ss.RUN_META_FILE)
    assert meta["status"] == "failed" and meta["exit_code"] is None
    assert RAW_NAME not in (out / rd.LOG_FILE).read_text(encoding="utf-8")
    assert not (out / rd.PID_FILE).exists()


# --- 관문 실패에도 안전 요약·격리본이 남는가 ---------------------------------------------------


def test_substitution_collision_does_not_block_safe_summary(vocab: ss.SafeVocab) -> None:
    sanitizer = hy.RunSanitizer(term_policy=hy.load_term_policy(), canaries=[CANARY],
                                user_values={}, vocab=hy.build_code_vocab(
                                    sc.load_registry_info()))
    gate = hy.make_gate(inventory_names=[RAW_NAME], placeholders={}, user_values={},
                        canaries=[CANARY], sanitizer=sanitizer)
    real = gate._fakes  # type: ignore[attr-defined]

    class _Colliding:  # 충돌 1건 흉내(나머지는 실물 위임 — FakeValues 는 슬롯 클래스)
        def collisions(self) -> int:
            return 1

        def __getattr__(self, name: str) -> Any:
            return getattr(real, name)

    gate._fakes = _Colliding()  # type: ignore[attr-defined]
    text, rules = ss.check_safe_summary({"schema": 1, "turns": []}, vocab, gate)
    assert text is not None, rules


def test_malformed_observed_still_quarantined(tmp_path: Path, qbase: Path,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "run_user_values", lambda *_a: {"login_id": RUN_USER})
    _reinject_after_sanitize(monkeypatch)
    base = _leak_name_driver()

    def driver_run(*a: Any, **k: Any) -> Any:
        records, meta = base(*a, **k)
        records[0]["observed"]["clarify_kind"] = {"kind": "x"}
        return records, meta

    out = tmp_path / "out"
    try:
        cli.cmd_run(_args(out), driver_run=driver_run)
    except TypeError:
        pass
    meta = _read_json(out / ss.RUN_META_FILE)
    assert meta["quarantine"] == "default_local"
    assert (out / ss.SAFE_SUMMARY_FILE).is_file()


# --- ⑥ 수용 기준 — locus 교집합 · run 메타 ----------------------------------------------------


@pytest.mark.parametrize("locus, kept", [
    ("args.{{host_1}}", False),
    ("F1:evil-host:args.x", False),
    ("F1:apm.instances:args.zz_not_an_arg", False),
    ("F1:apm.instances", True),
    ("sources:zz-not-a-source", False),
])
def test_locus_intersection(vocab: ss.SafeVocab, locus: str, kept: bool) -> None:
    assert vocab.code.allows("locus", locus) is kept
    assert vocab.ok(locus) is kept


def test_field_path_masks_raw_segments(vocab: ss.SafeVocab) -> None:
    assert ss.field_path(f"observed.{RAW_NAME}.error", vocab) == "observed.*.error"
    assert ss.field_path(f"{RAW_NAME}[3]", vocab) == "*[3]"


def test_run_meta_has_no_raw_paths_or_user(tmp_path: Path, qbase: Path,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "run_user_values", lambda *_a: {"login_id": RUN_USER})
    out = tmp_path / "out"
    assert cli.cmd_run(_args(out, "--quarantine-dir", str(tmp_path / "myq")),
                       driver_run=_fake_driver({})) == 0
    text = (out / ss.RUN_META_FILE).read_text(encoding="utf-8")
    for raw in (str(tmp_path), RUN_USER, "myq"):
        assert raw not in text
    assert not list(out.glob(".*.tmp"))
