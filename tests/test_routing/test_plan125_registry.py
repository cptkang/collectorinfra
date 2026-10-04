"""plans/125 A-1 — 비DB 시스템(APM 게이트웨이) 레지스트리 등재·소유 판정(121 TP-9.2 · D-281 ②).

고정하는 계약:
  1. `solutions[apm]`은 DB 항목이 없는 비DB 시스템이다 — 존 그룹·실행 그룹 0 · 존 시스템 아님.
  2. 비DB 시스템도 답변 영역 소유자가 된다(B-9 해소) — 기존 DB 시스템 영역의 소유는 불변.
  3. 보기 표(§4.2)는 레지스트리 하위 필드다(G-4 (a) — 새 정본 파일 없음 · D-131).
  4. 등재만으로 라우터·분해 소유표와 DB 설명 생성 안내가 바뀌지 않는다(비활성 = 바이트 불변).
LLM·DB 0.
"""

from __future__ import annotations

from src.orchestration.entity_locator import _is_zoned_system
from src.routing import capability_ownership as own
from src.routing.execution_groups import partition_execution_groups
from src.routing.registry import get_registry, parse_registry

WAS_AREAS = ("was_instance", "was_performance", "was_runtime", "was_activity", "apm_event",
             # plans/134 W2 — 통계·지표 목록·소스 변경 감지(의도된 갱신)
             "was_statistics", "apm_metric_catalog", "was_change_detection")


def test_apm_is_a_non_db_system_without_zone_groups() -> None:
    reg = get_registry()
    # plans/127 W1: 문서 소스 `doc`도 비DB 시스템으로 등재된다(G-5 (a) — 의도된 갱신)
    # plans/132 N-1(D-293): Prometheus 명시 인식 전용 항목(처리기·보기·영역 없음) — 의도된 갱신
    assert [s.code for s in reg.non_db_systems()] == ["apm", "doc", "prometheus"]
    assert reg.is_non_db_system("apm") and not reg.is_non_db_system("polestar")
    assert reg.system_db_ids("apm") == ()
    assert _is_zoned_system(reg, "polestar") and not _is_zoned_system(reg, "apm")
    groups = partition_execution_groups(list(reg.db_ids()))
    assert {g["solution"] for g in groups} == {"polestar"}, "실행 그룹은 존 순회뿐"


def test_non_db_system_owns_was_areas_and_db_ownership_is_unchanged() -> None:
    reg = get_registry()
    for code in WAS_AREAS:
        assert reg.capability_owners(code) == ("apm",)
    assert reg.capability_owners("server_usage") == ("polestar",)
    assert reg.capability_owners("asset_owner") == ("itam",)
    assert reg.system_label("apm") == "제니퍼(APM · WAS 모니터링)"


def test_view_table_is_registry_data() -> None:
    views = {v.id: v for v in get_registry().views_of("apm")}
    assert list(views) == ["apm.instances", "apm.app_health", "apm.runtime", "apm.pool",
                           "apm.active", "apm.slow_tx", "apm.events",
                           # plans/134 W2(SPEC §6.2 — 의도된 갱신)
                           "apm.app_stats", "apm.sql_stats", "apm.external_stats",
                           "apm.metrics", "apm.changes"]
    assert views["apm.instances"].first_hop and not views["apm.instances"].required_input
    assert views["apm.app_health"].required_input == "hostname"
    # plans/134 M-2 — 창은 상한이 아니라 의미(current·range·hourly·none) · window_max_minutes 폐지
    assert views["apm.app_health"].window == "range"
    assert [views[v].window for v in ("apm.instances", "apm.runtime", "apm.pool", "apm.active",
                                      "apm.slow_tx", "apm.events")] == [
        "none", "range", "current", "current", "range", "range"]
    assert not hasattr(views["apm.app_health"], "window_max_minutes")
    # plans/134 M-3 — 보기 선택 조건(SPEC §6.2 W1 행)
    assert [a.name for a in views["apm.active"].args] == ["n", "full"]
    assert [a.name for a in views["apm.slow_tx"].args] == ["n", "full"]
    assert [a.name for a in views["apm.events"].args] == [
        "level", "level_mode", "error_type", "record", "n", "full"]
    stale = {"전체 200개 상한", "구간 10분 상한", "현재값 · 상위 20", "상위 20",
             "기본 30분 · 최대 24시간"}
    assert {v.limit for v in views.values()}.isdisjoint(stale), "사라진 자체 상한 표기"
    assert views["apm.events"].capability == "apm_event"
    assert {v.capability for v in views.values()} <= set(WAS_AREAS)
    assert get_registry().views_of("polestar") == ()


def test_registration_does_not_change_ownership_renders() -> None:
    reg = get_registry()
    all_dbs = list(reg.db_ids())
    rendered = own.render_ownership_rows(all_dbs, with_db_ids=True)
    assert not any(code in rendered for code in WAS_AREAS), "활성 DB 없는 시스템 영역은 표에 없다"
    for db_id in all_dbs:
        guidance = own.ownership_guidance_rows(db_id)
        if guidance:
            assert not any(code in guidance[1] + guidance[2] for code in WAS_AREAS)


def test_solution_family_with_databases_is_not_non_db() -> None:
    """DB 항목이 같은 family 를 쓰는 솔루션(폴스타)은 backend 와 무관하게 비DB 시스템이 아니다."""
    reg = parse_registry({
        "databases": [{"db_id": "p1", "family": "p"}],
        "solutions": [
            {"code": "p", "backend": "mcp", "family": "p", "capabilities": ["a"]},
            {"code": "x", "backend": "mcp", "family": "x", "capabilities": ["b"],
             "views": [{"id": "x.v", "capability": "b", "tool": "t", "required_input": "hostname"}]},
        ],
    })
    assert [s.code for s in reg.non_db_systems()] == ["x"]
    assert reg.capability_owners("b") == ("x",)
    assert reg.views_of("x")[0].required_input == "hostname"
