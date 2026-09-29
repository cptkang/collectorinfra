"""교차 소스 시나리오 군 M 의 카탈로그 계약 (plans/121 TP-0.7 · D-270 ① · D-272 ③).

전부 무과금이다 - 로더·선택·예상치·판정기 순수 함수만 부른다(LLM·DB·서버 0).

고정하는 것:
  1. 군 M 9건이 로더를 통과하고 전부 `env: sandbox` · 2단 확정 프로파일이다.
  2. `--env closed` 명시 run 은 M 을 고르지 않는다(선택 0건 · 예상치 0건).
  3. `--env` 없는 자동 판정 run(D-216 ③)에서 closed 로 판정돼도 M 은 **불합격을 만들지 않는다** -
     환경 불일치 보류 뒤에 남는 키가 `manual_review` 와 샌드박스 합성 표식 부정 단언뿐이다.
     폐쇄망 run 에서 사유와 함께 빼는 수단은 요구 소스 선언이다(plans/122 C-4b · D-276 ②) — M군
     전 시나리오가 선언을 가진다(군 헤더 `[polestar, itam]` · Prometheus 3건 `[polestar]`).
  4. 오라클 파일(`fixtures/m_cross_system_oracle.yaml`)이 카탈로그와 짝이고, 오라클의 호스트·
     응답 단언의 호스트가 샌드박스 시드에 실재한다(없는 서버명 금지).
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from scripts.scenario import REPO_ROOT
from scripts.scenario.__main__ import main
from scripts.scenario.assertions import Observation, evaluate_turn
from scripts.scenario.catalog import Catalog, Scenario, load_catalog
from scripts.scenario.runner import ENV_NEUTRAL_KEYS, RunConfig, _hold_for_env, estimate

GROUP = "M"
PROFILE = "cross_system_tier2"
EXPECTED_IDS = [f"M-{n:02d}" for n in range(1, 10)]

ORACLE_PATH = REPO_ROOT / "testdata" / "scenarios" / "fixtures" / "m_cross_system_oracle.yaml"
POLESTAR_SEEDS = [
    *sorted((REPO_ROOT / "testdata" / "pg" / "init").glob("*.sql")),
    REPO_ROOT / "testdata" / "pg" / "05_insert_excel_data.sql",
]
ITAM_SEED = REPO_ROOT / "testdata" / "itam" / "init" / "02_seed.sql"
PROMETHEUS_CONFIG = REPO_ROOT / "testdata" / "prometheus" / "prometheus.yml"

#: 폐쇄망 응답에 나올 수 없는 샌드박스 합성 표식(ITAM 시드 `generate_init.py` — 계약명·FQDN·시리얼).
SANDBOX_MARKERS = ("합성", "synth", "SN-SYNTH")
#: 호스트명 모양의 단언 문구(숫자를 품은 라틴 토큰). 순수 숫자·소스 이름·한글 문구는 뺀다.
_HOSTLIKE = re.compile(r"^[A-Za-z][A-Za-z0-9.-]*\d[A-Za-z0-9.-]*$")


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return load_catalog()


@pytest.fixture(scope="module")
def group_m(catalog: Catalog) -> list[Scenario]:
    return [s for s in catalog.scenarios if s.group == GROUP]


@pytest.fixture(scope="module")
def oracle() -> dict:
    return yaml.safe_load(ORACLE_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def polestar_text() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in POLESTAR_SEEDS)


@pytest.fixture(scope="module")
def itam_hosts() -> set[str]:
    """ITAM 서버 자산(TCDMSIF80)의 호스트명 - 생성기가 행마다 `-- {호스트} · {설계}` 주석을 단다."""
    text = ITAM_SEED.read_text(encoding="utf-8")
    main_table = text.split("INSERT INTO `TCDMSIF80`", 1)[1].split("INSERT INTO", 1)[0]
    return set(re.findall(r"^-- (\S+) · ", main_table, flags=re.MULTILINE))


def _short(host: str) -> str:
    return host.split(".", 1)[0].lower()


def _present(name: str, text: str) -> bool:
    """토큰 경계로 찾는다 - 부분 문자열이면 없는 `svr-web-1` 이 `svr-web-10` 에 걸려 통과한다."""
    return re.search(rf"(?<![A-Za-z0-9-]){re.escape(name)}(?![A-Za-z0-9-])", text) is not None


# --- 1. 로더 · 군 구성 -----------------------------------------------------

def test_group_m_loads_nine_sandbox_only_scenarios(group_m: list[Scenario]) -> None:
    """M군 9건이 로더를 통과하고 전부 샌드박스 전용 · 2단 프로파일 · normal 이다."""
    assert [s.id for s in group_m] == EXPECTED_IDS
    for scenario in group_m:
        assert scenario.env == "sandbox", f"{scenario.id}: env 는 sandbox 고정이다(TP-0.7 조건)"
        assert scenario.profile == PROFILE, scenario.id
        # compound 는 R군 집계(반복 3 · 대응 등급 정책)에 들어간다 - E군처럼 normal 로 둔다.
        assert scenario.kind == "normal", scenario.id
        assert 121 in scenario.plans, scenario.id
        assert len(scenario.turns) == 1 and scenario.turns[0].send.get("query"), scenario.id


#: Prometheus **미연결**에서 「요청 소스 불가」(N-11)를 재는 3건 — `[prometheus]` 를 요구하면
#: Prometheus 가 붙을 때까지 어느 run 에서도 선택되지 않는다. 판정 전제 데이터는 샌드박스
#: 폴스타뿐이다.
PROMETHEUS_UNAVAILABLE_IDS = {"M-03", "M-04", "M-07"}


def test_group_m_declares_required_sources(catalog: Catalog, group_m: list[Scenario]) -> None:
    """M군 전 시나리오가 요구 소스를 가진다(plans/122 C-4b · D-276 ① 주의) — 선언이 빠지면 폐쇄망
    자동 판정 run 이 이 군을 다시 돌려 서버를 한 번 더 띄우고 보류 행을 쌓는다."""
    assert catalog.groups[GROUP].requires_sources == ("polestar", "itam")
    missing = [s.id for s in group_m if not s.requires_sources]
    assert not missing, f"요구 소스 선언이 없는 M 시나리오: {missing}"
    for scenario in group_m:
        expected = (["polestar"] if scenario.id in PROMETHEUS_UNAVAILABLE_IDS
                    else ["polestar", "itam"])
        assert scenario.requires_sources == expected, scenario.id
        # 폐쇄망 활성 DB(존이 붙은 폴스타 3종)만으로는 어느 것도 충족하지 않는다.
        assert set(scenario.requires_sources) - {
            "polestar_b0", "polestar_cm_gp", "polestar_cm_yd",
        }, scenario.id


def test_group_letter_does_not_prefix_collide(catalog: Catalog, group_m) -> None:
    """`--group M` 은 접두 일치다(`Catalog.select`) - 다른 군이 M 으로 시작하면 섞여 돈다."""
    assert catalog.groups[GROUP].policy_confirmed is False
    assert catalog.select(groups=[GROUP]) == group_m


def test_profile_resolves_tier2_without_db_injection(catalog: Catalog) -> None:
    """M군 프로파일은 2단을 확정하고 활성 DB 를 주입하지 않는다.

    로컬 `.env` 는 1단 확정이다(D-272 ④). 주입이 빠지면 1단을 잰다.

    `ACTIVE_DB_IDS` 를 주입하면 폐쇄망 자동 판정 run 이 같은 프로파일로 띄우는 서버가 샌드박스
    DB 에 붙는다 - 활성 DB 는 로컬 `.env` 전제로 둔다.
    """
    from src.observability.ladder import LadderTier, resolve_ladder_tier
    from src.orchestration.deep_agent import select_orchestration_backend

    injected = catalog.profiles[PROFILE]
    assert set(injected) == {
        "ENABLE_DEEPAGENTS_PACKAGE", "ENABLE_INTENT_ORCHESTRATION", "ENABLE_SEMANTIC_ROUTING",
    }
    cfg = SimpleNamespace(**{key.lower(): value == "true" for key, value in injected.items()})
    backend = select_orchestration_backend(cfg)   # 1단 플래그 off 면 가용성 조회 없이 결정된다
    tier, reason = resolve_ladder_tier(cfg, backend=backend, buildable=False)
    assert tier is LadderTier.INTENT_ORCHESTRATION, (tier, reason)


# --- 2. 폐쇄망 명시 run --------------------------------------------------------

def test_explicit_closed_run_selects_none(catalog: Catalog, group_m, capsys) -> None:
    """`--env closed` 명시 run 은 M군을 고르지 않고, sandbox 는 9건 전부 고른다."""
    closed_ids = {s.id for s in catalog.select(env="closed")}
    sandbox_ids = {s.id for s in catalog.select(env="sandbox")}
    assert not closed_ids & set(EXPECTED_IDS)
    assert set(EXPECTED_IDS) <= sandbox_ids

    assert main(["--dry-run", "--group", GROUP, "--env", "closed"]) == 0
    assert "선택: 0건" in capsys.readouterr().out
    assert main(["--dry-run", "--group", GROUP, "--env", "sandbox"]) == 0
    assert f"선택: {len(group_m)}건" in capsys.readouterr().out


def test_estimate_sandbox_single_boot(catalog: Catalog) -> None:
    """예상치: 샌드박스 단독 실행은 9턴 · 서버 기동 1회, closed 에서는 0건이다."""
    sandbox = estimate(catalog, RunConfig(mode="run", env="sandbox", groups=[GROUP]))
    assert sandbox["scenarios"] == len(EXPECTED_IDS)
    assert sandbox["turns"] == len(EXPECTED_IDS)
    assert sandbox["r_group_turns"] == 0
    assert sandbox["profiles"] == [PROFILE]

    closed = estimate(catalog, RunConfig(mode="run", env="closed", groups=[GROUP]))
    assert closed["scenarios"] == 0 and closed["profiles"] == []


# --- 3. 자동 판정 closed run 의 보류 ------------------------------------------

def test_env_neutral_keys_are_sandbox_marker_negatives_only(group_m) -> None:
    """환경 불일치에도 판정되는 키는 샌드박스 합성 표식 부정 단언뿐이다.

    부정 단언은 환경 불일치 보류를 통과한다(`ENV_NEUTRAL_KEYS`) - 폐쇄망 데이터로 발화할 수 있는
    문구(`.37`·합산값·`cmm_metric_stat`)를 쓰면 폐쇄망 기준선에 불합격·silent_wrong 을 흘린다."""
    for scenario in group_m:
        for turn in scenario.turns:
            neutral = set(turn.expect) & ENV_NEUTRAL_KEYS
            assert neutral <= {"manual_review", "response_must_not_contain"}, (scenario.id, neutral)
            for needle in turn.expect.get("response_must_not_contain") or []:
                assert any(marker in str(needle) for marker in SANDBOX_MARKERS), (
                    f"{scenario.id}: 부정 단언 {needle!r} 가 샌드박스 합성 표식이 아니다"
                )
                assert str(needle) in ITAM_SEED.read_text(encoding="utf-8"), (
                    f"{scenario.id}: {needle!r} 는 샌드박스 시드에 없는 값이다"
                )


def test_auto_closed_run_holds_turns_as_manual(catalog: Catalog, group_m) -> None:
    """자동 판정 closed run 에서 M군 턴은 불합격이 아니라 보류(`manual`)다.

    폐쇄망이 낼 법한 응답(폴스타 SQL 대체 · 사용률 소수부 · 큰 수)을 줘도 `manual` 이다."""
    closed_like = Observation(
        http_status=200,
        status="completed",
        response=(
            "서버 1,684대 중 CPU 사용률 83.37% · 합계 84 · "
            "프로메테우스 대신 폴스타 통계로 답합니다"
        ),
        executed_sqls=["SELECT hostname, avg_val FROM polestar.cmm_metric_stat_h LIMIT 10"],
        row_count=10,
        db_ids=["polestar_cm_gp"],
        sse_events=["node_start", "done"],
    )
    group = catalog.groups[GROUP]
    for scenario in group_m:
        held = _hold_for_env(scenario.turns[0], scenario, "closed")
        assert "환경 불일치" in held.expect["manual_review"], scenario.id
        verdict = evaluate_turn(scenario, 1, held, closed_like, group)
        assert verdict.func == "manual", (scenario.id, verdict.func, verdict.failures)
        assert verdict.forbidden_mode is None, scenario.id


# --- 4. 오라클 ----------------------------------------------------------------

def test_oracle_pairs_with_catalog(oracle: dict, group_m) -> None:
    """오라클 파일의 시나리오 집합이 카탈로그 M군과 같다."""
    assert set(oracle["scenarios"]) == {s.id for s in group_m}
    for scenario_id, spec in oracle["scenarios"].items():
        assert spec.get("shape"), scenario_id
        for negative in spec.get("negative") or []:
            assert isinstance(negative.get("closed_safe"), bool), scenario_id


def test_oracle_and_assertion_hosts_exist_in_sandbox_seeds(
    oracle: dict, group_m, polestar_text: str, itam_hosts: set[str]
) -> None:
    """오라클과 응답 단언의 호스트가 샌드박스 시드에 실재한다.

    존재하지 않는 서버명으로 짠 질의·오라클은 기준선을 거짓으로 만든다(시드 실측)."""
    assert len(itam_hosts) == oracle["sources"]["itam"]["server_rows"] == 30

    for scenario_id, spec in oracle["scenarios"].items():
        producer, bridge = spec.get("producer") or {}, spec.get("bridge") or {}
        consumer = spec.get("consumer") or {}
        names = [
            *producer.get("expected", []),
            *bridge.get("link", []), *bridge.get("none", []), *bridge.get("possible", []),
            *(bridge.get("multi") or {}).get("rows", []),
            *consumer.get("present", []), *consumer.get("absent", []),
        ]
        for name in names:
            assert _present(name, polestar_text), f"{scenario_id}: {name} 이 폴스타 시드에 없다"

    for scenario in group_m:
        expect = scenario.turns[0].expect
        needles = list(expect.get("response_must_contain") or [])
        for option in expect.get("response_must_contain_any") or []:
            needles.extend(option if isinstance(option, list) else [option])
        for needle in (str(n) for n in needles):
            if not _HOSTLIKE.match(needle):
                continue
            assert _present(needle, polestar_text) or needle in itam_hosts, (
                f"{scenario.id}: 단언 호스트 {needle!r} 가 샌드박스 시드에 없다"
            )


def test_oracle_bridge_grades_match_seeds(oracle: dict, itam_hosts: set[str]) -> None:
    """오라클 브리지 등급이 시드와 맞는다.

    1:1 = 대소문자 무시 일치 · 가능 = FQDN 단축명만 일치 · 없음 = 어느 쪽도 불일치."""
    exact = {host.lower() for host in itam_hosts}
    short = {_short(host) for host in itam_hosts}
    nodenames = set(oracle["sources"]["prometheus"]["nodenames"])
    assert all(f"nodename: {name}" in PROMETHEUS_CONFIG.read_text(encoding="utf-8")
               for name in nodenames)

    for scenario_id, spec in oracle["scenarios"].items():
        bridge = spec.get("bridge") or {}
        target = bridge.get("to")
        if target == "itam":
            for name in bridge.get("link", []):
                assert name.lower() in exact, f"{scenario_id}: {name} 는 1:1 이 아니다"
            for name in bridge.get("possible", []):
                assert name.lower() not in exact and _short(name) in short, (
                    f"{scenario_id}: {name} 는 가능 일치(FQDN)가 아니다"
                )
            for name in bridge.get("none", []):
                assert name.lower() not in exact and _short(name) not in short, (
                    f"{scenario_id}: {name} 는 자산관리에 있다"
                )
        elif target == "prometheus":
            assert set(bridge.get("link", [])) <= nodenames, scenario_id
            assert not set(bridge.get("none", [])) & nodenames, scenario_id


def test_multi_match_trap_comes_from_ip_substring_only(oracle: dict) -> None:
    """다건 브리지 함정은 IP 부분 일치로만 생긴다.

    ITAM 시드는 호스트당 1행이라(`per_ip` 다행 없음) 다건은 IP 부분 일치로 만든다(M-08)."""
    multi = oracle["scenarios"]["M-08"]["bridge"]["multi"]
    text = ITAM_SEED.read_text(encoding="utf-8")
    main_table = text.split("INSERT INTO `TCDMSIF80`", 1)[1].split("INSERT INTO", 1)[0]
    rows = re.findall(r"^-- (\S+) · .*\n\(([^\n]*)\)", main_table, flags=re.MULTILINE)
    hits = sorted(host for host, values in rows if "10.0.1.1" in values)
    assert hits == sorted(multi["rows"]), hits


def test_oracle_path_is_outside_loader_glob() -> None:
    """오라클 경로는 로더 glob 밖이다.

    `fixtures/` 는 하위 폴더라 `load_catalog` 가 군 파일로 읽지 않는다.

    읽으면 `group` 이 없어 카탈로그 전체가 거부된다.
    """
    from scripts.scenario.catalog import SCENARIO_DIR

    assert ORACLE_PATH.parent != SCENARIO_DIR
    assert ORACLE_PATH not in set(Path(SCENARIO_DIR).glob("*.yaml"))
