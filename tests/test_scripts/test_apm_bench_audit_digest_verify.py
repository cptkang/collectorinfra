"""plans/164 W0a 독립 검증 — `--audit-digest` 누출·기대 순서·정렬 반례(verifier).

구현자 테스트(`test_apm_bench_audit_digest.py`)와 별도로 ① 감사 줄의 모든 칸에 적대적 값을 넣어
파일·콘솔 어디에도 나오지 않는지 ② 기대 시나리오열이 `cmd_run` → `driver.run` 순서와 같은지
③ 정렬 표지 `exact`가 틀릴 수 있는 반례(xfail strict — 결함 보고용)를 고정한다. LLM·네트워크 0.
"""

from __future__ import annotations

import json
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from scripts.apm_bench import __main__ as cli
from scripts.apm_bench import audit_digest as ad
from scripts.apm_bench import guard as gd
from scripts.apm_bench.hygiene import TERM_POLICY_PATH
from scripts.apm_bench.scenarios import Expect, Scenario, Turn


@pytest.fixture(autouse=True)
def _quarantine_in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """관문 실패 격리본이 실제 홈(`~/apm_bench_quarantine`)에 쌓이지 않게 한다(plans/164 W2a)."""
    from scripts.apm_bench import rundir

    monkeypatch.setattr(rundir, "default_quarantine_base", lambda: tmp_path / "quarantine")


SECRET_HOST = "zzhostqx"
SECRET_USER = "benchuserqx"
SECRET_TID = "tidqx-0001"
SECRET_NUMS = ("98765431", "31415926", "27182818")
WINDOW = ["--since", "2026-10-13T09:00+09:00", "--until", "2026-10-13T10:00+09:00"]
T0 = datetime.fromisoformat("2026-10-13T09:00:00+09:00")


def _ts(minute: int) -> str:
    return f"2026-10-13T00:{minute:02d}:00.5+00:00"


def _hostile_rows() -> list[Any]:
    h = SECRET_HOST
    inv = {"timestamp": _ts(1), "event": "host_investigation", "entry_point": f"chat-{h}",
           "user_id": SECRET_USER, "thread_id": SECRET_TID, "backend": "apm_gateway",
           "profile": f"apm.instances,apm.{h},{h},apm.instances {h}",
           "outcome": "partial", "target_count": int(SECRET_NUMS[0]),
           "duration_ms": float(SECRET_NUMS[1]) / 1000,
           "degraded": [
               {"reason": f"instance_unresolved: hostname '{h}'에 대응하는 인스턴스 없음"},
               {"reason": f"apm_api_error:{h} 502"},
               {"reason": f"instance_unresolved{h}"},           # 접두 뒤에 이름이 붙은 꼴
               {"reason": f"{h}_down"},
               {"reason": f"대상 '{h}' 해석 0건"},
               {"reason": {"nested": h}},
               {"reason": [h]},
               {"reason": int(SECRET_NUMS[2])},
               {"reason": "apm_partial_sources", "detail": f"apm.instances:{h}"},
               h, 12345,
           ],
           "targets": [{"hostname": h}], "commands": [f"apm_instance_map hostname={h}"],
           "stdout": h, "authz": {"principal": h}, "extra": {"deep": {"deeper": [h]}},
           h: h}
    return [
        inv,
        {**inv, "timestamp": _ts(2), "outcome": f"ok {h}", "profile": None, "degraded": None},
        {**inv, "timestamp": _ts(2), "outcome": {"o": h}, "profile": [h, "apm.instances"]},
        {"timestamp": _ts(3), "event": "clarification_issued", "kind": f"zone_select {h}",
         "axis": h, "option_count": int(SECRET_NUMS[0]), "user_id": SECRET_USER,
         "thread_id": SECRET_TID},
        {"timestamp": _ts(3), "event": "clarification_issued", "kind": [h], "axis": {"a": h},
         "user_id": SECRET_USER, "thread_id": SECRET_TID},
        {"timestamp": _ts(4), "event": "query_execution", "sql": f"SELECT '{h}'",
         "user_id": SECRET_USER, "thread_id": SECRET_TID},
        {**inv, "timestamp": _ts(4), "backend": f"polestar-{h}"},   # 다른 백엔드 조사 줄
        {**inv, "timestamp": _ts(5), "event": h},                   # 모르는 이벤트
        {**inv, "timestamp": _ts(5), "thread_id": {"t": h}, "profile": "apm.runtime",
         "degraded": None},                                        # 비문자열 thread_id
        {**inv, "timestamp": f"{h}T00:01"},                         # 깨진 시각
        {**inv, "user_id": f"op-{h}"},                              # 다른 사용자
        f'{{"timestamp": "{h}", broken',                            # 깨진 JSON
    ]


def _write(path: Path, rows: list[Any]) -> Path:
    path.write_text("".join((r if isinstance(r, str) else json.dumps(r, ensure_ascii=False))
                            + "\n" for r in rows), encoding="utf-8")
    return path


def test_hostile_values_in_every_field_never_reach_file_or_console(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    log = _write(tmp_path / "audit-2026-10-13.jsonl", _hostile_rows())
    out = tmp_path / "o"
    code = cli.main(["--audit-digest", str(log), *WINDOW, "--env", "e2",
                     "--bench-user", SECRET_USER, "--only", "APM-INST-list", "--out", str(out)])
    printed = capsys.readouterr()
    assert code == 0
    text = (out / "audit_digest.json").read_text(encoding="utf-8")
    for blob in (text, printed.out, printed.err):
        for secret in (SECRET_HOST, SECRET_USER, SECRET_TID, "qx", *SECRET_NUMS, "2026-10-13",
                       "SELECT", "polestar", "502"):
            assert secret not in blob, secret
    digest = json.loads(text)
    assert digest["mapping"]["exact"] == 1  # 대응 자체는 유지된다(보기 apm.instances)
    assert digest["aggregates"]["reason_codes"] == {
        "apm_api_error": 2, "apm_partial_sources": 2, "instance_unresolved": 2,
        "target_text_unresolved": 2}  # 고정 문형 「대상 '…' 해석 0건」 → 코드만(교정 2차 ④)
    assert digest["input"]["other_user"] == 1 and digest["input"]["broken"] == 2


def test_crash_paths_do_not_echo_line_values(tmp_path: Path) -> None:
    """적대적 모양 줄이 예외를 내더라도 그 메시지·스택에 값이 실리지 않는다."""
    base = {"timestamp": _ts(1), "event": "host_investigation", "user_id": "anonymous",
            "thread_id": SECRET_TID, "backend": "apm_gateway", "profile": "apm.instances"}
    for i, line in enumerate([json.dumps({**base, "degraded": int(SECRET_NUMS[0])}),
                              "[" * 100_000 + SECRET_HOST]):
        log = tmp_path / f"a{i}.jsonl"
        log.write_text(line + "\n", encoding="utf-8")
        try:
            cli.main(["--audit-digest", str(log), *WINDOW, "--only", "APM-INST-list",
                      "--out", str(tmp_path / f"o{i}")])
        except Exception:  # noqa: BLE001 — 예외 자체는 별도 지적(Low) · 여기선 값 미노출만
            tb = traceback.format_exc()
            for secret in (SECRET_HOST, SECRET_TID, SECRET_NUMS[0]):
                assert secret not in tb, secret


@pytest.mark.parametrize("extra", [
    ["--env", "e2"],
    ["--env", "e2", "--skip-heavy"],
    ["--env", "e1", "--limit", "40"],
    ["--env", "e2", "--only", "APM-HEALTH-inst", "APM-INST-list"],
])
def test_expected_order_matches_cmd_run_driver_order(tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch,
                                                     extra: list[str]) -> None:
    """기대 시나리오열 = `cmd_run`이 넘기는 시나리오를 `driver.run`이 도는 순서(light → heavy)."""
    seen: dict[str, list[str]] = {}
    real_build = ad.build_digest

    def spy_build(threads: Any, counts: Any, dropped: Any, expected: Any, head: Any) -> Any:
        seen["digest"] = [s.id for s in expected]
        return real_build(threads, counts, dropped, expected, head)

    def fake_driver_run(scenarios: Any, *, env: str, **_: Any) -> Any:
        plan = gd.schedule(scenarios, env=env)  # driver.run 과 같은 일정 함수(`driver.py:892`)
        seen["driver"] = [s.id for s in (*plan.light, *plan.heavy)]
        raise RuntimeError("stop")

    monkeypatch.setattr(ad, "build_digest", spy_build)
    log = _write(tmp_path / "a.jsonl", [])
    assert cli.main(["--audit-digest", str(log), *WINDOW, *extra,
                     "--out", str(tmp_path / "o")]) == 0
    # E2 `--run`은 `--skip-heavy`가 기본 켬이다(plans/164 W2b) — 로그 경로 모드 진단표와 같은
    # 플래그로 맞춘다(run id 모드는 run 메타에서 채운다)
    run_args = cli.build_parser().parse_args(
        ["--run", *extra, *([] if "--skip-heavy" in extra else ["--no-skip-heavy"]),
         "--placeholders", str(cli.PLACEHOLDERS_E1), "--term-policy", str(TERM_POLICY_PATH),
         "--view-server-version", "99.0.0", "--out", str(tmp_path / "run")])
    assert cli.cmd_run(run_args, driver_run=fake_driver_run) == 1
    assert seen["digest"] == seen["driver"] and seen["digest"]


# --- 정렬 반례(`exact`는 틀리지 않는다 — §4.0 ④ · §6 W0a) -----------------------------------


def _sc(sid: str, views: tuple[str, ...], turns: int = 1) -> Scenario:
    expect = Expect(agents=(), views=views, target="inst:1", window="default")
    return Scenario(id=sid, covers=(), split="dev",
                    turns=tuple(Turn(prompt="p", expect=expect) for _ in range(turns)))


def _thread(order: int, views: set[str]) -> ad.Thread:
    return ad.Thread(tid=f"t{order}", first=(T0, order), views=views)


@pytest.mark.xfail(strict=True, reason="알려진 한계 — plans/164 §4.0: 무이벤트 시나리오에서 두 칸 "
                                       "이상 밀린 틀린 보기 연쇄는 이웃 강등(1칸)에 안 걸린다")
def test_shifted_wrong_views_two_deep_are_not_exact() -> None:
    # 진실: E1 무이벤트 · E2 턴이 E1 보기(instances)를, E3 턴이 E2 보기(app_health)를 골랐다
    expected = [_sc("E1", ("apm.instances",)), _sc("E2", ("apm.app_health",)),
                _sc("E3", ("apm.runtime",))]
    threads = [_thread(1, {"apm.instances"}), _thread(2, {"apm.app_health"})]
    labels, _, _ = ad.label_threads(threads, expected)
    assert labels[0][0] != "exact"  # 현재: ("exact", E1) — 실제는 E2


def test_split_thread_of_multiturn_scenario_is_not_exact_on_silent_next() -> None:
    """멀티턴 첫 턴이 done 없이 끝나면(타임아웃·스트림 끊김) 다음 턴이 새 thread 라 시나리오 하나가
    스레드 둘이 된다 — 둘째가 무이벤트 이웃 시나리오에 exact 로 붙지 않는다(갈린 스레드 강등)."""
    # 진실: E1(2턴)의 1턴 → t1{instances}, 2턴(새 thread) → t2{app_health} · E2 무이벤트
    expected = [_sc("E1", ("apm.instances", "apm.app_health"), turns=2),
                _sc("E2", ("apm.app_health",)), _sc("E3", ("apm.runtime",))]
    threads = [_thread(1, {"apm.instances"}), _thread(2, {"apm.app_health"}),
               _thread(3, {"apm.runtime"})]
    labels, _, _ = ad.label_threads(threads, expected)
    assert labels[1][0] != "exact"  # 실제는 E1 의 둘째 thread
    assert [c.id for c in labels[1][2]] == ["E1", "E2"]
