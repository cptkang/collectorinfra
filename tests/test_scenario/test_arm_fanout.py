"""사다리 단 arm 전개 — `plans/110` §3.1 `110·N-1`.

2단(`intent_orchestration`)과 3단(`semantic_router`)을 **같은 run 안의 arm** 으로 나란히
재기 위한 수단이다. 플래그를 꺼서 한쪽만 돌리면 실행 시각이 단 효과와 교란되므로
(93 스위프 실측: 전반 31 arm 61.1초 대 후반 31 arm 56.6초) 두 단은 한 run 에 있어야 한다.

이 파일이 고정하는 계약은 넷이다.
  1. **치환이 아니라 병합** — 시나리오 자기 프로파일(`optin_alarm` 등)의 플래그가 살아남는다.
  2. 키 충돌 시 **arm 이 이긴다**(arm 이 측정 축이다).
  3. 같은 시나리오가 arm 마다 한 번씩, **같은 run 안에서** 돈다.
  4. `run.json` 이 조합 이름·arm·시나리오 프로파일·사다리 단을 함께 싣는다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.scenario import __main__ as cli
from scripts.scenario import runner as runner_mod
from scripts.scenario.catalog import Catalog, Group, Scenario, Turn, load_catalog
from scripts.scenario.report import profile_breakdown
from scripts.scenario.runner import (
    ArmError,
    RunConfig,
    arm_profile_name,
    estimate,
    execute,
    iter_executions,
    merge_arm_profiles,
    split_arm_profile,
)


def _catalog() -> Catalog:
    def scenario(sid: str, profile: str = "baseline") -> Scenario:
        return Scenario(id=sid, group="T", plans=[110], title=sid, profile=profile,
                        env="both", turns=[Turn({"query": "q"}, {})])

    return Catalog(
        groups={"T": Group(id="T", name="t", latency_target_ms=10000)},
        scenarios=[scenario("T-01"), scenario("T-02"), scenario("D-01", profile="optin_alarm")],
        profiles={
            "baseline": {},
            "optin_alarm": {"TEXT2SQL_ALARM_DETERMINISTIC": "true"},
            "tier2_intent": {"ENABLE_DEEPAGENTS_PACKAGE": "false",
                             "ENABLE_INTENT_ORCHESTRATION": "true",
                             "ENABLE_SEMANTIC_ROUTING": "true"},
            "tier3_router": {"ENABLE_DEEPAGENTS_PACKAGE": "false",
                             "ENABLE_INTENT_ORCHESTRATION": "false",
                             "ENABLE_SEMANTIC_ROUTING": "true"},
        },
    )


# --- 병합 대 치환 (`110·N-1` 의 핵심 계약) --------------------------------

def test_arm은_시나리오_프로파일을_치환하지_않고_병합한다() -> None:
    """치환하면 `optin_alarm` 시나리오가 자기 플래그를 잃는다 - 잘못 재는 게 아니라 다른 걸 잰다."""
    catalog = _catalog()
    _expanded, bindings = merge_arm_profiles(catalog, catalog.scenarios, ["tier3_router"])

    combo = "optin_alarm+tier3_router"
    assert combo in bindings
    assert catalog.profiles[combo] == {
        "TEXT2SQL_ALARM_DETERMINISTIC": "true",      # 시나리오 자기 프로파일에서 살아남았다
        "ENABLE_DEEPAGENTS_PACKAGE": "false",
        "ENABLE_INTENT_ORCHESTRATION": "false",
        "ENABLE_SEMANTIC_ROUTING": "true",
    }


def test_키가_충돌하면_arm이_이긴다() -> None:
    """arm 이 측정 축이다. 시나리오 프로파일이 축을 덮으면 그 arm 은 arm 이 아니게 된다."""
    catalog = _catalog()
    catalog.profiles["optin_alarm"]["ENABLE_INTENT_ORCHESTRATION"] = "true"
    merge_arm_profiles(catalog, catalog.scenarios, ["tier3_router"])

    merged = catalog.profiles["optin_alarm+tier3_router"]
    assert merged["ENABLE_INTENT_ORCHESTRATION"] == "false"
    assert merged["TEXT2SQL_ALARM_DETERMINISTIC"] == "true"


def test_실_카탈로그의_D군은_arm을_얹어도_알람_플래그를_유지한다() -> None:
    """회귀 방어: 실제 카탈로그·실제 프로파일 정본으로 확인한다.

    벤치 스위프(`scripts/bench/sweep.py:301·482`)는 `profile=arm.arm_id` 로 **치환**해
    이 8건이 매 arm 에서 `TEXT2SQL_ALARM_DETERMINISTIC` 을 잃는다. 시나리오 하네스는
    그 결함을 복제하지 않는다.
    """
    catalog = load_catalog()
    alarm = [s for s in catalog.scenarios if s.profile == "optin_alarm"]
    assert alarm, "카탈로그에 optin_alarm 시나리오가 없다 - 이 회귀 테스트가 무의미해진다"

    expanded, _bindings = merge_arm_profiles(
        catalog, catalog.scenarios, ["tier2_intent", "tier3_router"]
    )
    alarm_ids = {s.id for s in alarm}
    combos = {s.profile for s in expanded if s.id in alarm_ids}
    assert combos == {"optin_alarm+tier2_intent", "optin_alarm+tier3_router"}
    for combo in combos:
        assert catalog.profiles[combo]["TEXT2SQL_ALARM_DETERMINISTIC"] == "true"


# --- 전개·순서 -----------------------------------------------------------

def test_arm_수만큼_전개하고_프로파일마다_서버를_한_번_띄운다() -> None:
    plan = dict(
        (profile, [s.id for s in scenarios])
        for profile, scenarios in iter_executions(
            _catalog(), RunConfig(env="sandbox", arms=["tier2_intent", "tier3_router"])
        )
    )
    assert sum(len(v) for v in plan.values()) == 3 * 2
    assert plan == {
        "baseline+tier2_intent": ["T-01", "T-02"],
        "baseline+tier3_router": ["T-01", "T-02"],
        "optin_alarm+tier2_intent": ["D-01"],
        "optin_alarm+tier3_router": ["D-01"],
    }


def test_실행_순서는_프로파일_major_arm_minor다() -> None:
    """중간에 끊겨도 먼저 끝난 프로파일에서는 **두 arm 이 모두** 남는다."""
    order = [
        profile for profile, _ in iter_executions(
            _catalog(), RunConfig(env="sandbox", arms=["tier2_intent", "tier3_router"])
        )
    ]
    assert order == [
        "baseline+tier2_intent", "baseline+tier3_router",
        "optin_alarm+tier2_intent", "optin_alarm+tier3_router",
    ]


def test_profile_필터는_arm보다_먼저_걸린다() -> None:
    """`--profile` 은 시나리오가 선언한 자기 프로파일에 거는 필터다(덧씌운 이름이 아니다)."""
    plan = dict(
        (profile, [s.id for s in scenarios])
        for profile, scenarios in iter_executions(
            _catalog(),
            RunConfig(env="sandbox", profiles=["optin_alarm"],
                      arms=["tier3_router", "tier2_intent"]),
        )
    )
    assert plan == {
        "optin_alarm+tier3_router": ["D-01"],
        "optin_alarm+tier2_intent": ["D-01"],
    }


def test_arm_없는_실행은_종전과_같다() -> None:
    plan = dict(
        (profile, [s.id for s in scenarios])
        for profile, scenarios in iter_executions(_catalog(), RunConfig(env="sandbox"))
    )
    assert plan == {"baseline": ["T-01", "T-02"], "optin_alarm": ["D-01"]}


def test_같은_arm을_두_번_줘도_한_번만_돈다() -> None:
    plan = [p for p, _ in iter_executions(
        _catalog(), RunConfig(env="sandbox", arms=["tier3_router", "tier3_router"])
    )]
    assert plan == ["baseline+tier3_router", "optin_alarm+tier3_router"]


def test_arm을_자기_프로파일로_쓰면_이름을_겹쳐_적지_않는다() -> None:
    assert arm_profile_name("tier3_router", "tier3_router") == "tier3_router"
    assert arm_profile_name("baseline", "tier3_router") == "baseline+tier3_router"


def test_조합_이름은_되돌릴_수_있다() -> None:
    arms = ["tier2_intent", "tier3_router"]
    assert split_arm_profile("optin_alarm+tier3_router", arms) == ("optin_alarm", "tier3_router")
    assert split_arm_profile("baseline", arms) == ("baseline", None)
    assert split_arm_profile("baseline", []) == ("baseline", None)


def test_정의되지_않은_arm은_전개_전에_거부된다() -> None:
    with pytest.raises(ArmError) as exc:
        merge_arm_profiles(_catalog(), _catalog().scenarios, ["tier9_없음"])
    assert "tier9_없음" in str(exc.value)


# --- 예상치 · 승인 근거 ---------------------------------------------------

def test_예상치는_arm_전개를_반영한다() -> None:
    """이 출력이 D-127 승인 근거다. arm 을 빠뜨리면 실제의 절반으로 승인받게 된다."""
    catalog = _catalog()
    plain = estimate(catalog, RunConfig(env="sandbox"))
    armed = estimate(catalog, RunConfig(env="sandbox", arms=["tier2_intent", "tier3_router"]))

    assert armed["turns"] == plain["turns"] * 2
    assert armed["scenarios"] == plain["scenarios"] * 2
    assert armed["profiles"] == [
        "baseline+tier2_intent", "baseline+tier3_router",
        "optin_alarm+tier2_intent", "optin_alarm+tier3_router",
    ]


# --- run.json · 리포트 ----------------------------------------------------

def test_run_json에_조합_이름과_arm과_단이_남는다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """arm 마다 단을 판독하지 못하면 '모든 arm 이 같은 단'인 사고를 run 이 끝난 뒤에도 모른다."""
    monkeypatch.setattr(runner_mod, "RESULTS_ROOT", tmp_path)
    summary = execute(
        _catalog(),
        RunConfig(mode="mock", env="sandbox", only=["D-01"],
                  arms=["tier2_intent", "tier3_router"]),
    )
    assert summary["meta"]["arms"] == ["tier2_intent", "tier3_router"]
    seen = {
        p["name"]: (p["arm"], p["base_profile"], p["tier"]) for p in summary["profiles"]
    }
    assert seen == {
        "optin_alarm+tier2_intent": ("tier2_intent", "optin_alarm", "mock"),
        "optin_alarm+tier3_router": ("tier3_router", "optin_alarm", "mock"),
    }
    # 산출물 경로도 조합 이름으로 갈린다 - 섞이면 arm 별 사후 분석이 불가능하다.
    logs = {p.name for p in (Path(summary["out_dir"]) / "logs").glob("server-*.log")}
    assert logs == {"server-optin_alarm+tier2_intent.log",
                    "server-optin_alarm+tier3_router.log"}


def test_리포트는_arm을_섞지_않고_갈라_집계한다() -> None:
    """§2 군별 표는 시나리오 id 로 접어 arm 을 가로지른다 - 그래서 프로파일별 집계가 따로 있다."""
    rows = [
        {"profile": "baseline+tier2_intent", "scenario_id": "T-01", "turn": 1, "repeat": 0,
         "group": "T", "func_verdict": "fail", "processing_time_ms": 900.0},
        {"profile": "baseline+tier3_router", "scenario_id": "T-01", "turn": 1, "repeat": 0,
         "group": "T", "func_verdict": "pass", "processing_time_ms": 100.0},
    ]
    run = {"profiles": [
        {"name": "baseline+tier2_intent", "arm": "tier2_intent",
         "base_profile": "baseline", "tier": "intent_orchestration"},
        {"name": "baseline+tier3_router", "arm": "tier3_router",
         "base_profile": "baseline", "tier": "semantic_router"},
    ]}
    breakdown = {row["name"]: row for row in profile_breakdown(rows, run)}

    assert breakdown["baseline+tier2_intent"]["fail"] == 1
    assert breakdown["baseline+tier2_intent"]["pass"] == 0
    assert breakdown["baseline+tier3_router"]["pass"] == 1
    assert breakdown["baseline+tier3_router"]["fail"] == 0
    assert breakdown["baseline+tier3_router"]["tier"] == "semantic_router"


# --- CLI ------------------------------------------------------------------

def test_dry_run이_arm_전개를_출력한다(capsys: pytest.CaptureFixture[str]) -> None:
    """돌리기 전에 '몇 건이 몇 번 도는가'가 보여야 6시간을 잘못 쓰지 않는다."""
    assert cli.main(["--dry-run", "--arm", "tier2_intent", "--arm", "tier3_router"]) == 0
    out = capsys.readouterr().out
    assert "arm 전개: tier2_intent, tier3_router" in out
    assert "optin_alarm+tier3_router" in out
    assert "TEXT2SQL_ALARM_DETERMINISTIC=true" in out


def test_오타난_arm은_실행_전에_거부된다(capsys: pytest.CaptureFixture[str]) -> None:
    """오타는 카탈로그 검증을 통과한다 - 주입만 조용히 빠진 채 전 arm 이 같은 단으로 돈다."""
    assert cli.main(["--dry-run", "--arm", "tier3_rooter"]) == 1
    assert "tier3_rooter" in capsys.readouterr().err


def test_arm_전개는_JSON으로_되읽을_수_있다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`run.json` 만으로 사후에 arm 을 가른다 - 러너 상태를 들고 있지 않아도 된다."""
    monkeypatch.setattr(runner_mod, "RESULTS_ROOT", tmp_path)
    summary = execute(
        _catalog(),
        RunConfig(mode="mock", env="sandbox", only=["T-01"], arms=["tier3_router"]),
    )
    saved = json.loads((Path(summary["out_dir"]) / "run.json").read_text(encoding="utf-8"))
    assert [p["arm"] for p in saved["profiles"]] == ["tier3_router"]
    assert [p["name"] for p in saved["profiles"]] == ["baseline+tier3_router"]


# --- 확정 계약: 소비자는 조합 이름을 파싱하지 않는다 ------------------------

def arm_of(row: dict) -> str:
    """벤치(`scripts/bench/sweep.py`)와 회귀 비교가 arm 을 읽는 방식 — 확정 계약.

    **문자열을 파싱하지 않는다.** 옛 행(`arm` 칸 없음)·새 행(덧씌우기 없음 `arm=None`)·
    새 행(덧씌우기 있음) 세 경우가 이 한 줄로 성립해야 한다.
    """
    return row.get("arm") or row.get("profile")


def _rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **kwargs) -> list[dict]:
    monkeypatch.setattr(runner_mod, "RESULTS_ROOT", tmp_path)
    summary = execute(_catalog(), RunConfig(mode="mock", env="sandbox", **kwargs))
    raw = Path(summary["out_dir"]) / "raw.jsonl"
    return [json.loads(line)
            for line in raw.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_raw_행이_arm과_base_profile을_칸으로_싣는다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = _rows(tmp_path, monkeypatch, only=["D-01", "T-01"], arms=["tier3_router"])
    d_rows = [r for r in rows if r["scenario_id"] == "D-01"]
    t_rows = [r for r in rows if r["scenario_id"] == "T-01"]
    assert d_rows and t_rows

    for row in d_rows:
        assert row["profile"] == "optin_alarm+tier3_router"
        assert row["arm"] == "tier3_router"          # arm id 원본 - 조합 이름이 아니다
        assert row["base_profile"] == "optin_alarm"
    for row in t_rows:
        assert row["profile"] == "baseline+tier3_router"
        assert row["arm"] == "tier3_router"
        assert row["base_profile"] == "baseline"


def test_D군이_기준선_시나리오와_같은_arm으로_묶인다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """치환·파싱 설계에서는 D군이 `optin_alarm+…` 으로 쪼개져 쌍체 비교에서 빠졌다."""
    rows = _rows(tmp_path, monkeypatch, only=["D-01", "T-01"],
                 arms=["tier2_intent", "tier3_router"])
    assert {arm_of(row) for row in rows} == {"tier2_intent", "tier3_router"}

    by_arm: dict[str, set[str]] = {}
    for row in rows:
        by_arm.setdefault(arm_of(row), set()).add(row["scenario_id"])
    # 두 arm 모두 같은 시나리오 집합을 갖는다 - 쌍체 비교가 성립하는 조건이다.
    assert by_arm == {"tier2_intent": {"D-01", "T-01"}, "tier3_router": {"D-01", "T-01"}}


def test_덧씌우기가_없으면_arm은_null이고_base_profile은_profile과_같다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """하위 호환 - `arm_of` 의 폴백이 종전 run 과 같은 값을 낸다."""
    rows = _rows(tmp_path, monkeypatch, only=["D-01", "T-01"])
    for row in rows:
        assert row["arm"] is None
        assert row["base_profile"] == row["profile"]
    assert {arm_of(row) for row in rows} == {"baseline", "optin_alarm"}


def test_옛_행은_profile로_폴백한다() -> None:
    """`arm` 칸이 없던 run 을 지금 규칙으로 읽어도 종전과 같은 값이다."""
    assert arm_of({"profile": "baseline"}) == "baseline"


def test_조합_이름은_ASCII다() -> None:
    """`profile` 값은 파일명이 된다 - 폐쇄망 Windows 에서 비ASCII 파일명을 만들지 않는다."""
    from scripts.scenario.runner import ARM_SEPARATOR

    name = arm_profile_name("optin_alarm", "tier3_router")
    assert ARM_SEPARATOR == "+"
    assert name.isascii(), name


def test_arm마다_체크포인트와_서버_로그가_다른_파일로_갈린다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """같은 파일에 겹쳐 쓰면 arm 격리가 깨진다 - 체크포인트는 상태, 로그는 SQL 감사 원본이다."""
    seen: list[tuple[str, str, str]] = []
    real = runner_mod.ServerHandle

    class Recording(real):  # type: ignore[misc, valid-type]
        def __init__(self, **kwargs):
            seen.append((kwargs["profile"],
                         kwargs["env_overrides"]["CHECKPOINT_DB_URL"],
                         str(kwargs["log_path"])))
            super().__init__(**kwargs)

    monkeypatch.setattr(runner_mod, "RESULTS_ROOT", tmp_path)
    monkeypatch.setattr(runner_mod, "ServerHandle", Recording)
    execute(
        _catalog(),
        RunConfig(mode="mock", env="sandbox", only=["D-01"],
                  arms=["tier2_intent", "tier3_router"]),
    )
    assert len(seen) == 2
    assert len({row[1] for row in seen}) == 2, f"체크포인트가 겹친다: {seen}"
    assert len({row[2] for row in seen}) == 2, f"서버 로그가 겹친다: {seen}"
    for profile, checkpoint, log in seen:
        assert checkpoint.endswith(f"checkpoints-{profile}.db")
        assert log.endswith(f"server-{profile}.log")
