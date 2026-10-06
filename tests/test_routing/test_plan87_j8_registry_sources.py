"""plans/87 J8 · D-287 ④ — 레지스트리 `solutions[].sources`(제니퍼 소스 ↔ 존 정본) 로더·조회.

고정하는 계약:
  1. 정본 데이터 — `solutions[apm].sources` = bank(은행존) · common(공동존) ·
     legacy(은행존 · G-14 ①), 선언 순서 그대로.
  2. 검증 실패는 `RegistryError`다 — 알람 존 판정(RBAC)의 정본이라 틀린 항목을 조용히 버리지 않는다.
  3. `alarm_source` — 알람 dbId `{family}_{소스 id}` → (시스템, 소스). 단일 설정
     (`db_id == family`)은 None(경고 없음) · 표에 없는 소스 id는 None + id별 경고 1회(M-4).
     판정은 레지스트리 family로 한다.
"""

from __future__ import annotations

import logging

import pytest

from src.routing import registry as registry_mod
from src.routing.registry import RegistryError, SourceSpec, get_registry, parse_registry


def _data(sources, *, family: str = "acme", extra_dbs=None) -> dict:
    return {
        "zones": [{"code": "zone_a", "label": "가존(A리전) — 설명"}, {"code": "zone_b"}],
        "solutions": [
            {"code": "db_sys", "backend": "sql", "family": "dbfam"},
            {"code": "apm_x", "backend": "mcp", "family": family, "sources": sources},
        ],
        "databases": [{"db_id": "d1", "family": "dbfam", "zone": "zone_a"}, *(extra_dbs or [])],
    }


# ── 1. 정본 데이터 ─────────────────────────────────────────────────────────────


def test_registry_declares_three_jennifer_sources_in_order() -> None:
    sources = get_registry().sources_of("apm")
    assert sources == (
        SourceSpec(id="bank", label="은행존 제니퍼", zone="bankjon"),
        SourceSpec(id="common", label="공동존 제니퍼", zone="gongjon"),
        SourceSpec(id="legacy", label="레거시 제니퍼", zone="bankjon"),
    )


def test_sources_of_unknown_or_sourceless_system_is_empty() -> None:
    reg = get_registry()
    assert reg.sources_of("polestar") == ()
    assert reg.sources_of("doc") == ()
    assert reg.sources_of("no_such_system") == ()


def test_views_are_untouched_by_sources() -> None:
    """`views`(보기 표)와 `sources`는 다른 층이다 — 소스를 더해도 보기 표는 그대로다."""
    # plans/134 W2 — 보기 5종 추가(SPEC-apm-question-coverage §6.2 · 의도된 갱신)
    assert [v.id for v in get_registry().views_of("apm")] == [
        "apm.instances", "apm.app_health", "apm.runtime", "apm.pool",
        "apm.active", "apm.slow_tx", "apm.events",
        "apm.app_stats", "apm.sql_stats", "apm.external_stats", "apm.metrics", "apm.changes",
        # plans/134 W5·W6·W7 — 보기 10종 추가(계약 §4.1 순서 · 의도된 갱신)
        "apm.profile", "apm.trace", "apm.change_impact", "apm.event_rules", "apm.process",
        "apm.jennifer_server", "apm.loaded_classes", "apm.environment", "apm.users",
        "apm.active_detail",
    ]


# ── 2. 검증 실패 = RegistryError ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "sources,needle",
    [
        (["bank"], "id가 없습니다"),                                 # 항목이 매핑 아님
        ([{"label": "이름만"}], "id가 없습니다"),                     # id 없음
        ([{"id": "Bank"}], "형식 위반"),                             # 대문자
        ([{"id": "1bank"}], "형식 위반"),                            # 숫자 시작
        ([{"id": "bank-a"}], "형식 위반"),                           # 하이픈
        ([{"id": "a" * 17}], "형식 위반"),                           # 17자(상한 16)
        ([{"id": "default"}], "예약어"),
        ([{"id": "api"}], "예약어"),
        ([{"id": "bank"}, {"id": "bank", "zone": "zone_b"}], "중복"),
        ([{"id": "bank", "zone": "zone_z"}], "미선언 존"),
        ({"id": "bank"}, "목록이어야"),                               # 목록 아님
    ],
)
def test_invalid_sources_raise_registry_error(sources, needle: str) -> None:
    with pytest.raises(RegistryError, match=needle):
        parse_registry(_data(sources))


def test_empty_zone_and_sixteen_char_id_are_allowed() -> None:
    reg = parse_registry(_data([{"id": "a" * 16}, {"id": "nozone", "label": "존 없음"}]))
    assert reg.sources_of("apm_x") == (
        SourceSpec(id="a" * 16), SourceSpec(id="nozone", label="존 없음", zone=""),
    )


def test_absent_sources_key_is_empty_tuple() -> None:
    data = _data(None)
    del data["solutions"][1]["sources"]
    assert parse_registry(data).sources_of("apm_x") == ()


# ── 3. alarm_source ───────────────────────────────────────────────────────────


def test_alarm_source_resolves_registry_sources() -> None:
    reg = get_registry()
    assert reg.alarm_source("jennifer_bank") == ("apm", reg.sources_of("apm")[0])
    assert reg.alarm_source("jennifer_common")[1].zone == "gongjon"
    assert reg.alarm_source("jennifer_legacy")[1].zone == "bankjon"


def test_single_setting_default_is_none_without_warning(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger=registry_mod.__name__):
        assert get_registry().alarm_source("jennifer") is None
    assert caplog.records == []


def test_unknown_source_id_warns_once_and_is_none(caplog) -> None:
    registry_mod._WARNED_UNKNOWN_SOURCES.discard("jennifer_zzz")
    with caplog.at_level(logging.WARNING, logger=registry_mod.__name__):
        assert get_registry().alarm_source("jennifer_zzz") is None
        assert get_registry().alarm_source("jennifer_zzz") is None
    warned = [r for r in caplog.records if "jennifer_zzz" in r.getMessage()]
    assert len(warned) == 1 and "존 없음" in warned[0].getMessage()


@pytest.mark.parametrize("db_id", ["", "polestar_b0", "cloud_portal", "unknown_db", "jenniferbank"])
def test_non_source_db_ids_are_none(db_id: str) -> None:
    assert get_registry().alarm_source(db_id) is None


def test_family_is_read_from_registry_not_vendor_literal() -> None:
    """벤더 문자열이 아니라 레지스트리 family로 판정한다 — 다른 family도 같은 규칙으로 풀린다."""
    reg = parse_registry(_data([{"id": "east", "zone": "zone_b"}], family="acme"))
    assert reg.alarm_source("acme_east") == ("apm_x", SourceSpec(id="east", zone="zone_b"))
    assert reg.alarm_source("jennifer_east") is None


def test_registered_db_id_is_never_read_as_source() -> None:
    """family 접두가 겹쳐도 등록 DB 항목이면 소스로 보지 않는다(DB 항목이 우선)."""
    reg = parse_registry(_data([{"id": "east"}], family="acme",
                               extra_dbs=[{"db_id": "acme_east", "zone": "zone_a"}]))
    assert reg.alarm_source("acme_east") is None
