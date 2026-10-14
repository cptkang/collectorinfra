"""plans/164 W2 (d) 실행 전 키트 검사 · G-4 (a) · W4 실행 위생.

자리표 실재(있음·없음·여럿) · 「없음」 일반 턴 판정 불가 분리와 분모 · 값 미출력(카나리아) ·
소스 대응 범위 경고 · `apm_gateway/var` 전후 비교 · E2 root 거부.

LLM·네트워크·제니퍼 0 — 드라이버는 `test_apm_bench_cli._fake_driver` 대역에 키트 검사 훅
호출만 더한다. `var/` 위치·홈·결과 루트는 tmp 로 돌린다.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.apm_bench import __main__ as cli
from scripts.apm_bench import driver as dv
from scripts.apm_bench import kit
from scripts.apm_bench import rundir as rd
from scripts.apm_bench import safe_summary as ss
from scripts.apm_bench import scenarios as sc
from tests.test_scripts.test_apm_bench_cli import _fake_driver

HOST_TURN = "APM-INST-host"  # 대상 host:1
INST_TURN = "APM-HEALTH-inst"  # 대상 inst:1
#: 카나리아 자리표 값 — 콘솔·실행 로그·안전 요약·run 메타 어디에도 나오면 안 된다.
CANARY_HOST = "zqkit-host-canary77"
CANARY_INST = "zqkit-inst-canary88"


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """홈 · 격리 기준 · 결과 루트 · var 위치를 tmp 로 · 실행 계정은 비root 로 고정."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(rd, "default_quarantine_base", lambda: tmp_path / "quarantine")
    monkeypatch.setattr(cli, "RESULTS_ROOT", tmp_path / "results")
    monkeypatch.setattr(kit, "VAR_DIR", tmp_path / "var")
    monkeypatch.setattr(kit.os, "geteuid", lambda: 501, raising=False)
    return tmp_path


def _placeholders(tmp_path: Path, **values: str) -> Path:
    """E1 자리표 파일 사본(값 덮어쓰기)."""
    data = yaml.safe_load(cli.PLACEHOLDERS_E1.read_text(encoding="utf-8"))
    data["values"].update(values)
    path = tmp_path / "ph.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def _kit_driver(inventory: Callable[[dict[str, str]], list[str]],
                sources: Callable[[dict[str, str]], dict[str, set[str]]] | None = None,
                hook: Callable[[], None] | None = None,
                errors: list[str] | None = None) -> Callable[..., Any]:
    """`_fake_driver` + 수집 직후 키트 검사 훅 호출(드라이버와 같은 자리 · 수집 실패 사유 포함) ·
    선택 부수 효과."""
    base = _fake_driver({})

    def run(picked: Any, **kw: Any) -> Any:
        values = kw["placeholders"].values
        kw["kit_check"](inventory(values), (sources or (lambda v: {}))(values), errors or [])
        if hook is not None:
            hook()
        records, meta = base(picked, **kw)
        if errors:
            meta["inventory"] = {"calls": 2, "names": 1, "errors": list(errors)}
        return records, meta

    return run


def _run(tmp_path: Path, driver_run: Callable[..., Any], *extra: str,
         only: tuple[str, ...] = (HOST_TURN,), env: str = "e1") -> tuple[int, Path]:
    out = tmp_path / "out"
    args = cli.build_parser().parse_args(
        ["--run", "--env", env, "--only", *only, "--out", str(out), *extra])
    return cli.cmd_run(args, driver_run=driver_run), out


def _meta(out: Path) -> dict[str, Any]:
    return json.loads((out / ss.RUN_META_FILE).read_text(encoding="utf-8"))


def _scenarios() -> list[sc.Scenario]:
    data = yaml.safe_load(cli.SCENARIOS_PATH.read_text(encoding="utf-8"))
    return sc.parse_scenarios(data)[0]


# --- 판정 단위 ----------------------------------------------------------------------------


@pytest.mark.parametrize("value, inventory, expected", [
    ("was01_a", ["was01_a", "api02_main"], (kit.PRESENT, 1)),
    ("WAS01_A", ["was01_a"], (kit.PRESENT, 1)),  # 대소문자 무시
    ("nope", ["was01_a", "api02_main"], (kit.ABSENT, 0)),
    ("was01", ["was01_a", "was01_b", "api"], (kit.PARTIAL, 2)),  # 값을 품은 이름
    ("was-host01", ["was-host01", "was-host01.example.local"], (kit.PARTIAL, 2)),
    ("was-host01.example.local", ["was-host01"], (kit.PARTIAL, 1)),  # FQDN 값 ↔ 짧은 이름
    ("xab-1", ["ab"], (kit.ABSENT, 0)),  # 값에 든 짧은 이름(3자 미만)은 부분 일치가 아니다
    ("", ["a"], (kit.ABSENT, 0)),
])
def test_presence(value: str, inventory: list[str], expected: tuple[str, int]) -> None:
    assert kit.presence(value, inventory) == expected


def test_check_kit_kinds_and_source_scope() -> None:
    """이름 종류만 대조 · 단건 식별자는 검사 제외 · 대응 없음 · 소스 불일치."""
    picked = [s for s in _scenarios() if s.id in (HOST_TURN, INST_TURN, "APM-PROC-pid-host")]
    ph = dv.Placeholders(values={"host_1": "h1", "inst_1": "i1", "pid_1": "4242"},
                         source_default="cm_prd", source_by_placeholder={"inst_1": "cm_prd"})
    report = kit.check_kit(picked, ph, ["h1", "i1"], {"h1": {"b0"}, "i1": {"b0"}})
    assert report.status == {"host_1": (kit.PRESENT, 1), "inst_1": (kit.PRESENT, 1)}
    assert report.excluded >= 1  # pid_1
    assert report.unmapped == ["host_1"]  # inst_1 은 by_placeholder 에 있다
    assert report.mismatch == ["host_1"]  # 인벤토리 소스 b0 ≠ default cm_prd
    same = kit.check_kit(picked, ph, ["h1", "i1"], {"h1": {"cm_prd", "b0"}})
    assert same.unmapped == ["host_1"] and same.mismatch == []
    unknown = kit.check_kit(picked, ph, ["h1", "i1"], {})  # 소스 표지 없음 → 불일치 판단 안 함
    assert unknown.mismatch == []


def test_failed_inventory_tool_makes_its_kinds_unchecked() -> None:
    """V-1 — 실패한 도구가 내는 종류에서 못 찾은 값은 「검사 불가」(찾은 값은 판정 그대로)."""
    assert kit.unchecked_kinds(["apm_instance_map:timeout"]) == {"inst", "host", "svc"}
    assert kit.unchecked_kinds(["apm_business:apm_api_error"]) == {"biz", "svc"}
    assert kit.unchecked_kinds([]) == set()
    picked = [s for s in _scenarios() if s.id in (HOST_TURN, INST_TURN, "APM-BIZ-name")]
    ph = dv.Placeholders(values={"host_1": "h1", "inst_1": "i1", "biz_1": "b1"})
    report = kit.check_kit(picked, ph, ["i1"], errors=["apm_instance_map:timeout"])
    assert report.status == {"host_1": (kit.UNCHECKED, 0), "inst_1": (kit.PRESENT, 1),
                             "biz_1": (kit.ABSENT, 0)}  # business 도구는 성공 → 「없음」 유지
    assert report.counts()["unchecked"] == 1
    assert any("[경고] 자리표 검사 불가 host_1" in line for line in kit.kit_lines(report))


def test_inventory_failure_keeps_turns_in_denominator(tmp_path: Path,
                                                      capsys: pytest.CaptureFixture[str]) -> None:
    ph = _placeholders(tmp_path)
    driver_run = _kit_driver(lambda v: [v["biz_1"]], errors=["apm_instance_map:timeout"])
    code, out = _run(tmp_path, driver_run, "--placeholders", str(ph),
                     only=(HOST_TURN, INST_TURN))
    assert code == 0
    safe = json.loads((out / ss.SAFE_SUMMARY_FILE).read_text(encoding="utf-8"))
    assert safe["summary"]["counts"]["unjudged"] == 0 and safe["summary"]["turn"]["total"] == 2
    meta = _meta(out)["kit"]
    assert meta["placeholders"]["unchecked"] == 2 and meta["absent"] == []
    assert "검사 불가 2" in capsys.readouterr().out


def test_hook_errors_reconciled_with_driver_meta(tmp_path: Path) -> None:
    """훅이 실패 사유를 못 받았어도 드라이버 메타(정본)의 실패 사유로 다시 판정한다."""
    ph = _placeholders(tmp_path)
    base = _kit_driver(lambda v: [v["biz_1"]])

    def driver_run(picked: Any, **kw: Any) -> Any:
        records, meta = base(picked, **kw)
        meta["inventory"] = {"calls": 2, "names": 1, "errors": ["apm_instance_map:timeout"]}
        return records, meta

    code, out = _run(tmp_path, driver_run, "--placeholders", str(ph), only=(HOST_TURN,))
    assert code == 0
    safe = json.loads((out / ss.SAFE_SUMMARY_FILE).read_text(encoding="utf-8"))
    assert safe["summary"]["counts"]["unjudged"] == 0
    assert _meta(out)["kit"]["placeholders"]["unchecked"] == 1


# --- G-4 (a) — 「없음」 판정 불가 · 「여럿」 분모 유지 ---------------------------------------


def test_absent_turn_unjudged_partial_stays_in_denominator(tmp_path: Path) -> None:
    ph = _placeholders(tmp_path)

    def inventory(values: dict[str, str]) -> list[str]:  # host_1 없음 · inst_1 여럿
        return [values["inst_1"], values["inst_1"] + "_x", "other"]

    code, out = _run(tmp_path, _kit_driver(inventory), "--placeholders", str(ph),
                     only=(HOST_TURN, INST_TURN))
    assert code == 0
    trace = {r["scenario_id"]: r for r in (json.loads(line) for line in (
        out / "trace.jsonl").read_text(encoding="utf-8").splitlines())}
    host_v, inst_v = trace[HOST_TURN]["verdict"], trace[INST_TURN]["verdict"]
    # 반출본(trace)은 치환기가 이름 토큰을 가짜 값으로 바꿀 수 있어 접두만 본다 — 정확한 자리표
    # 이름은 안전 요약(치환 전 레코드 · 허용 자리표 정규식)에서 본다
    assert str(host_v["axis_detail"]["unjudged"]).startswith("placeholder_missing:")
    assert host_v["turn_pass"] is False
    assert not inst_v["axis_detail"].get("unjudged")  # 「여럿」은 분리하지 않는다
    safe = json.loads((out / ss.SAFE_SUMMARY_FILE).read_text(encoding="utf-8"))
    summ = safe["summary"]
    assert summ["unjudged"] == {"placeholder_missing:host_1": 1}
    assert summ["counts"]["unjudged"] == 1 and summ["counts"]["normal"] == 1
    assert summ["turn"]["total"] == 1  # 분모 = 일반 턴(「없음」 1건만 빠짐)
    rows = {t["id"]: t for t in safe["turns"]}
    assert rows[f"{HOST_TURN}#0"]["unjudged"] == "placeholder_missing:host_1"
    assert rows[f"{INST_TURN}#0"]["unjudged"] is None
    kit_meta = _meta(out)["kit"]
    assert kit_meta["checked"] is True and kit_meta["absent"] == ["host_1"]
    assert kit_meta["partial"] == ["inst_1"]
    assert kit_meta["placeholders"] == {"present": 0, "absent": 1, "partial": 1, "unchecked": 0,
                                        "excluded": 0}


def test_absent_marks_only_normal_judgment_path() -> None:
    """되묻기·거절 정답 턴은 「없음」 표시가 있어도 그 판정 경로 그대로다(일반 턴만 분리)."""
    records: list[dict[str, Any]] = [
        {"placeholders": {"host_1": "x"}, "verdict": {}},
        {"placeholders": {"inst_1": "y"}, "verdict": {}},
        {"placeholders": {"host_1": "x"}, "verdict": {"skipped": "heavy_off"}},
    ]
    assert kit.mark_absent(records, ["host_1"]) == 1
    assert records[0]["extra"] == {kit.KIT_ABSENT_KEY: ["host_1"]}
    assert "extra" not in records[1] and "extra" not in records[2]
    assert kit.mark_absent(records, []) == 0


def test_values_never_printed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """카나리아 값 — 콘솔 · run.log · 안전 요약 · run 메타에 값이 없고 자리표 이름만 있다."""
    ph = _placeholders(tmp_path, host_1=CANARY_HOST, inst_1=CANARY_INST)

    def inventory(values: dict[str, str]) -> list[str]:
        return [values["inst_1"] + ".fqdn", "zz-other"]  # inst_1 여럿 · host_1 없음

    code, out = _run(tmp_path, _kit_driver(
        inventory, lambda v: {v["inst_1"].casefold(): {"b0"}}), "--placeholders", str(ph),
        only=(HOST_TURN, INST_TURN))
    assert code == 0
    console = capsys.readouterr().out
    texts = {"console": console,
             "run.log": (out / rd.LOG_FILE).read_text(encoding="utf-8"),
             "summary": (out / ss.SAFE_SUMMARY_FILE).read_text(encoding="utf-8"),
             "run_meta": (out / ss.RUN_META_FILE).read_text(encoding="utf-8")}
    for where, text in texts.items():
        for value in (CANARY_HOST, CANARY_INST, CANARY_INST.casefold()):
            assert value not in text, where
    assert "키트 검사: 자리표 있음 0 · 없음 1 · 여럿 1" in console
    assert "자리표 실재: host_1 없음, inst_1 여럿(부분 일치 1)" in console
    assert "[경고] 자리표 없음 host_1" in texts["run.log"]


# --- 소스 대응 범위 -------------------------------------------------------------------------


def test_unmapped_and_mismatch_warnings(tmp_path: Path,
                                        capsys: pytest.CaptureFixture[str]) -> None:
    ph = _placeholders(tmp_path)

    def inventory(values: dict[str, str]) -> list[str]:
        return [values["host_1"]]

    code, out = _run(tmp_path, _kit_driver(
        inventory, lambda v: {v["host_1"].casefold(): {"b0"}}), "--placeholders", str(ph))
    assert code == 0
    console = capsys.readouterr().out
    assert "[경고] 소스 대응 없음 host_1" in console  # 견본은 inst_b·inst_c 만 매핑
    assert "[경고] 소스 불일치 host_1" in console  # 인벤토리 소스 b0 · default cm_prd
    kit_lines = [ln for ln in console.splitlines() if "키트 검사" in ln or "소스 " in ln]
    assert kit_lines and not any("cm_prd" in ln or "b0" in ln for ln in kit_lines)  # 소스 id 미출력
    meta = _meta(out)["kit"]
    assert meta["source_unmapped"] == 1 and meta["source_mismatch"] == 1


def test_no_kit_hook_records_unchecked(tmp_path: Path) -> None:
    """드라이버가 훅을 부르지 않으면(중단 등) `checked` false · 판정 불가 분리 없음."""
    code, out = _run(tmp_path, _fake_driver({}))
    assert code == 0
    meta = _meta(out)["kit"]
    assert meta["checked"] is False and meta["placeholders"] is None and meta["absent"] == []


# --- apm_gateway/var -------------------------------------------------------------------


def _present(values: dict[str, str]) -> list[str]:
    return list(values.values())


def test_var_absent_stays_silent(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = _run(tmp_path, _kit_driver(_present))
    assert code == 0
    assert "apm_gateway/var" not in capsys.readouterr().out
    assert _meta(out)["kit"]["var"] == {"preexisting": False, "created": False, "new": 0,
                                        "changed": 0}


def test_var_preexisting_unchanged_is_info_only(tmp_path: Path,
                                                capsys: pytest.CaptureFixture[str]) -> None:
    var = tmp_path / "var"
    (var / "spool").mkdir(parents=True)
    (var / "spool" / "old.jsonl").write_text("x", encoding="utf-8")
    code, out = _run(tmp_path, _kit_driver(_present))
    assert code == 0
    console = capsys.readouterr().out
    assert "[정보] apm_gateway/var 이전부터 있음" in console
    assert "[경고] apm_gateway/var" not in console
    assert _meta(out)["kit"]["var"]["preexisting"] is True


def test_var_new_file_warns_without_name(tmp_path: Path,
                                         capsys: pytest.CaptureFixture[str]) -> None:
    var = tmp_path / "var"

    def spill() -> None:
        (var / "spool").mkdir(parents=True)
        (var / "spool" / "zq-opname-77.jsonl").write_text("x", encoding="utf-8")

    code, out = _run(tmp_path, _kit_driver(_present, hook=spill))
    assert code == 0
    console = capsys.readouterr().out
    assert "[경고] apm_gateway/var — 이번 run 에서 새 파일 1 · 바뀐 파일 0 · 디렉터리 새로 생김" \
        in console
    for text in (console, (out / rd.LOG_FILE).read_text(encoding="utf-8"),
                 (out / ss.RUN_META_FILE).read_text(encoding="utf-8")):
        assert "zq-opname-77" not in text and "spool" not in text
    assert _meta(out)["kit"]["var"] == {"preexisting": False, "created": True, "new": 1,
                                        "changed": 0}
    run = json.loads((out / ss.SAFE_SUMMARY_FILE).read_text(encoding="utf-8"))["run"]
    assert run["gateway_var"] == {"preexisting": False, "created": True, "new": 1, "changed": 0}


def test_var_changed_file_warns(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    var = tmp_path / "var"
    var.mkdir()
    old = var / "a.log"
    old.write_text("x", encoding="utf-8")

    def grow() -> None:
        old.write_text("xyz", encoding="utf-8")

    code, out = _run(tmp_path, _kit_driver(_present, hook=grow))
    assert code == 0
    assert "새 파일 0 · 바뀐 파일 1" in capsys.readouterr().out
    assert _meta(out)["kit"]["var"]["changed"] == 1


def test_var_checked_even_when_driver_aborts(tmp_path: Path,
                                             capsys: pytest.CaptureFixture[str]) -> None:
    var = tmp_path / "var"

    def abort(*_a: Any, **_k: Any) -> Any:
        var.mkdir()
        (var / "x").write_text("x", encoding="utf-8")
        raise dv.RunAbortError(dv.ABORT_VERIFY_FAILED)

    code, out = _run(tmp_path, abort)
    assert code == 1
    assert "새 파일 1" in capsys.readouterr().out
    assert _meta(out)["kit"]["var"]["new"] == 1


# --- 실행 계정(G-3 (a)) -------------------------------------------------------------------


def test_e2_root_exit_2_before_anything(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                        capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(kit.os, "geteuid", lambda: 0, raising=False)
    called: list[int] = []
    out = tmp_path / "out"
    args = cli.build_parser().parse_args(
        ["--run", "--env", "e2", "--only", HOST_TURN, "--out", str(out)])
    assert cli.cmd_run(args, driver_run=lambda *a, **k: called.append(1)) == 2
    assert called == [] and not out.exists()
    console = capsys.readouterr().out
    assert f"[중단] {kit.ROOT_REFUSAL}" in console
    assert str(Path.home()) not in console


def test_e1_root_passes_and_is_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                        capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(kit.os, "geteuid", lambda: 0, raising=False)
    code, out = _run(tmp_path, _kit_driver(_present))
    assert code == 0
    assert kit.ROOT_REFUSAL not in capsys.readouterr().out
    assert _meta(out)["kit"]["root"] is True


# --- run 메타 스키마 ---------------------------------------------------------------------


def test_run_meta_kit_is_closed() -> None:
    raw = {"kit": {"checked": True,
                   "placeholders": {"present": 2, "absent": "x", "partial": 1, "excluded": 0,
                                    "leak": 9},
                   "absent": ["host_1", "운영서버01", 3], "partial": ["inst_1", "inst_zz"],
                   "source_unmapped": 2, "source_mismatch": True,
                   "var": {"preexisting": True, "created": "yes", "new": 1, "changed": 0,
                           "names": ["a"]},
                   "root": "no"}}
    doc = ss.run_meta_doc(raw, scenario_ids=())["kit"]
    assert doc == {"checked": True,
                   "placeholders": {"present": 2, "absent": None, "partial": 1,
                                    "unchecked": None, "excluded": 0},
                   "absent": ["host_1"], "partial": ["inst_1"], "source_unmapped": 2,
                   "source_mismatch": None,
                   "var": {"preexisting": True, "created": False, "new": 1, "changed": 0},
                   "root": False}
