"""plans/130 W3 M-3 — 간선 E6 업무명 → hostname 연결(`entity_link.link_business_names` · LLM 0).

고정하는 계약:
  1. 등록명 일치(field=name) · 비고 일치(field=description) · 0건 `unlinked` · 다건 `linked:many`.
  2. 조회 DB = 간선 소유자 활성 DB ∩ 사용자 DB 권한(D-232) — 권한 밖 DB 는 SQL 이 실행되지 않는다.
  3. DB 하나 실패해도 다른 DB 결과를 쓴다 · 전부 실패면 `not_queried`(사유).
  4. 행 상한 도달 → 단계 `truncated` + 장부 사유 · DB2 대문자 키 행 · hostname 빈 행 제외.
  5. 2자 미만 검색어 · 간선 경로 부재 → 조회 없이 `not_queried`.
업무 표기 행은 **테스트 전용 픽스처**다(로컬 샌드박스에 업무 표기 데이터 없음 — W0 실측).
DB 는 전부 대역(LLM·네트워크 0).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from src.db_adapters.polestar import entity_probe
from src.dbhub.models import QueryResult
from src.orchestration import entity_link as el

DOMAINS = {
    "polestar_cm_gp": SimpleNamespace(display_name="김포", db_engine="postgresql",
                                      db_schema="polestar"),
    "polestar_b0": SimpleNamespace(display_name="은행존", db_engine="db2", db_schema="POLESTAR"),
}
GP = "polestar_cm_gp"
B0 = "polestar_b0"


def _cfg(active_dbs: tuple[str, ...] = (GP, B0)) -> SimpleNamespace:
    return SimpleNamespace(
        multi_db=SimpleNamespace(get_active_db_ids=lambda: list(active_dbs)),
        get_polestar_db_ids=lambda: {GP, B0},
    )


def _row(name: str, host: str, desc: str = "", *, upper: bool = False) -> dict[str, Any]:
    row = {"server_name": name, "hostname": host, "description": desc}
    return {k.upper(): v for k, v in row.items()} if upper else row


class _Polestar:
    """폴스타 DB 대역 — DB 별 행을 그대로 돌려준다(SQL 의 OR 일치는 상위 집합 · 판정은 호출부)."""

    def __init__(self, table: dict[str, list[dict[str, Any]]], fail: set[str] | None = None,
                 truncated: set[str] | None = None) -> None:
        self.table = table
        self.fail = fail or set()
        self.truncated = truncated or set()
        self.sqls: list[tuple[str, str]] = []

    def client(self, db_id: str) -> Any:
        outer = self

        class _Client:
            async def execute_sql(self, sql: str) -> QueryResult:
                outer.sqls.append((db_id, sql))
                if db_id in outer.fail:
                    raise RuntimeError("down")
                rows = outer.table.get(db_id, [])
                return QueryResult(columns=list(rows[0]) if rows else [], rows=rows,
                                   row_count=len(rows), truncated=db_id in outer.truncated)

        return _Client()


@pytest.fixture
def polestar(monkeypatch: pytest.MonkeyPatch) -> Any:
    def install(table: dict[str, list[dict[str, Any]]], **kw: Any) -> _Polestar:
        db = _Polestar(table, **kw)

        @asynccontextmanager
        async def ctx(_config: Any, *, db_id: str | None = None) -> Any:
            yield db.client(str(db_id))

        monkeypatch.setattr(el, "get_db_client", ctx)
        monkeypatch.setattr(el, "get_domain_by_id", DOMAINS.get)
        return db

    return install


FIXTURE_GP = [
    _row("sicwso01 (이미지 업무 WAS#1)", "sicwso01"),
    _row("svr-img-db", "imgdb01", "이미지 업무 DB"),
    _row("svr-etc", "etc01", "무관 서버"),
]


# ── 1. 등록명 · 비고 · 0건 ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_name_and_description_matches(polestar: Any) -> None:
    db = polestar({GP: FIXTURE_GP})
    found, ledger, steps = await el.link_business_names(
        ["이미지 업무", "결제"], app_config=_cfg((GP,)), authorized_db_ids=None)
    assert found["이미지 업무"] == [
        {"hostname": "sicwso01", "server_name": "sicwso01 (이미지 업무 WAS#1)", "db_id": GP,
         "field": "name"},
        {"hostname": "imgdb01", "server_name": "svr-img-db", "db_id": GP, "field": "description"},
    ], "무관 서버는 파이썬 재판정에서 빠진다"
    assert found["결제"] == []
    by_key = {e.key: e for e in ledger}
    img = by_key["이미지 업무"]
    assert (img.status, img.grade, img.edge, img.facet) == ("linked", "many", "E6", "business_name")
    assert img.value == "sicwso01, imgdb01"
    assert by_key["결제"].status == "unlinked" and by_key["결제"].grade == "none"
    assert "찾지 못했다" in by_key["결제"].reason
    assert steps == [{"edge": "E6", "from": "business_name", "to": "hostname",
                      "owner": "polestar", "keys": 2, "db_ids": [GP], "linked": 1}]
    assert len(db.sqls) == 1, "DB 하나에 조회 1회(검색어 전부를 한 SQL 로)"
    assert el.ledger_line(ledger) == "업무명 연결(업무명 → hostname) 1/2 · 미연결 1"


@pytest.mark.asyncio
async def test_single_match_is_grade_one_and_case_insensitive(polestar: Any) -> None:
    polestar({GP: [_row("SICWSO01 (Image WAS)", "sicwso01"), _row("svr-x", "x01", "무관")]})
    found, ledger, _ = await el.link_business_names(
        ["image was"], app_config=_cfg((GP,)), authorized_db_ids=None)
    assert [h["hostname"] for h in found["image was"]] == ["sicwso01"]
    assert ledger[0].status == "linked" and ledger[0].grade == "one"
    assert ledger[0].value == "sicwso01"


# ── 2. 권한 ∩ 활성 ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unauthorized_active_db_is_never_queried(polestar: Any) -> None:
    db = polestar({GP: FIXTURE_GP, B0: [_row("b0-img", "b0img01", "이미지 업무")]})
    found, _ledger, steps = await el.link_business_names(
        ["이미지 업무"], app_config=_cfg((GP, B0)), authorized_db_ids=[GP])
    assert [d for d, _ in db.sqls] == [GP], "권한 밖 활성 DB(B0)의 SQL 은 실행되지 않는다"
    assert steps[0]["db_ids"] == [GP]
    assert {h["db_id"] for h in found["이미지 업무"]} == {GP}


@pytest.mark.asyncio
async def test_none_means_all_active_and_registry_order(polestar: Any) -> None:
    db = polestar({GP: FIXTURE_GP, B0: [_row("b0-img", "b0img01", "이미지 업무")]})
    found, _ledger, steps = await el.link_business_names(
        ["이미지 업무"], app_config=_cfg((GP, B0)), authorized_db_ids=None)
    assert steps[0]["db_ids"] == [B0, GP], "레지스트리 선언 순서(은행존 → 공동존)"
    assert [d for d, _ in db.sqls] == [B0, GP]
    b0_sql = dict(db.sqls)[B0]
    assert "POLESTAR.cmm_resource" in b0_sql and "FETCH FIRST 1000 ROWS ONLY" in b0_sql
    assert "LIMIT 1000" in dict(db.sqls)[GP]
    assert len(found["이미지 업무"]) == 3


@pytest.mark.asyncio
async def test_empty_authorization_or_no_active_db(polestar: Any) -> None:
    db = polestar({GP: FIXTURE_GP})
    found, ledger, steps = await el.link_business_names(
        ["이미지 업무"], app_config=_cfg((GP,)), authorized_db_ids=[])
    assert db.sqls == [] and found == {"이미지 업무": []}
    assert ledger[0].status == "not_queried"
    assert ledger[0].reason == "권한 안의 활성 polestar DB 가 없다" == steps[0]["error"]
    _found, ledger, steps = await el.link_business_names(
        ["이미지 업무"], app_config=_cfg(()), authorized_db_ids=None)
    assert db.sqls == [] and steps[0]["error"] == "polestar 활성 DB 가 없다"
    assert el.ledger_line(ledger) == "업무명 연결(업무명 → hostname) 0/1 · 조회 안 함 1"


# ── 3. 부분 실패 ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_one_db_failure_keeps_other_results(polestar: Any) -> None:
    db = polestar({GP: FIXTURE_GP}, fail={B0})
    found, ledger, steps = await el.link_business_names(
        ["이미지 업무", "결제"], app_config=_cfg((GP, B0)), authorized_db_ids=None)
    assert len(db.sqls) == 2, "실패한 DB 가 있어도 다음 DB 를 조회한다"
    assert steps[0]["errors"] == [f"{B0}: 조회 실패(RuntimeError)"]
    assert [h["hostname"] for h in found["이미지 업무"]] == ["sicwso01", "imgdb01"]
    by_key = {e.key: e for e in ledger}
    assert by_key["이미지 업무"].status == "linked"
    assert "일부 DB 조회 실패" in by_key["이미지 업무"].reason
    assert by_key["결제"].status == "unlinked" and "일부 DB 조회 실패" in by_key["결제"].reason


@pytest.mark.asyncio
async def test_all_db_failure_is_not_queried(polestar: Any) -> None:
    polestar({}, fail={GP})
    _found, ledger, steps = await el.link_business_names(
        ["이미지 업무"], app_config=_cfg((GP,)), authorized_db_ids=None)
    assert ledger[0].status == "not_queried" and "조회 실패" in ledger[0].reason
    assert steps[0]["errors"] and steps[0]["linked"] == 0


@pytest.mark.asyncio
async def test_missing_adapter_hook_is_reason(polestar: Any,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    db = polestar({GP: FIXTURE_GP})
    monkeypatch.setattr(el, "get_adapter", lambda *_a, **_k: None)
    _found, ledger, steps = await el.link_business_names(
        ["이미지 업무"], app_config=_cfg((GP,)), authorized_db_ids=None)
    assert db.sqls == [] and steps[0]["errors"] == [f"{GP}: 업무명 조회 수단(어댑터)이 없다"]
    assert ledger[0].status == "not_queried"


# ── 4. 상한 · 대문자 키 · 빈 hostname · 중복 ────────────────────────────────────

@pytest.mark.asyncio
async def test_row_cap_reached_marks_truncated(polestar: Any) -> None:
    rows = [_row(f"svr-{i} (이미지 업무)", f"h{i:04d}") for i in range(el._BUSINESS_ROW_CAP)]
    polestar({GP: rows})
    found, ledger, steps = await el.link_business_names(
        ["이미지 업무"], app_config=_cfg((GP,)), authorized_db_ids=None)
    assert steps[0]["truncated"] is True and len(found["이미지 업무"]) == el._BUSINESS_ROW_CAP
    assert "조회 상한 1000행에 닿아 일부만 확인" in ledger[0].reason


@pytest.mark.asyncio
async def test_driver_truncated_flag_marks_truncated(polestar: Any) -> None:
    polestar({GP: FIXTURE_GP}, truncated={GP})
    _found, ledger, steps = await el.link_business_names(
        ["결제"], app_config=_cfg((GP,)), authorized_db_ids=None)
    assert steps[0]["truncated"] is True
    assert ledger[0].status == "unlinked" and "조회 상한" in ledger[0].reason


@pytest.mark.asyncio
async def test_uppercase_keys_blank_hostname_and_duplicates(polestar: Any) -> None:
    polestar({B0: [
        _row("sicwso01 (이미지 업무 WAS#1)", "sicwso01", upper=True),
        _row("sicwso01 (이미지 업무 WAS#1)", "sicwso01", "이미지 업무", upper=True),
        _row("svr-nohost", "", "이미지 업무", upper=True),
    ]})
    found, ledger, _ = await el.link_business_names(
        ["이미지 업무"], app_config=_cfg((B0,)), authorized_db_ids=[B0])
    assert found["이미지 업무"] == [{"hostname": "sicwso01",
                                  "server_name": "sicwso01 (이미지 업무 WAS#1)",
                                  "db_id": B0, "field": "name"}], "DB2 대문자 키 · 같은 db+host 1회"
    assert ledger[0].grade == "one" and "hostname 빈 행 1건 제외" in ledger[0].reason


# ── 5. 조회 전 거절 ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_short_and_blank_terms_are_not_queried(polestar: Any) -> None:
    db = polestar({GP: FIXTURE_GP})
    found, ledger, steps = await el.link_business_names(
        ["", "  ", "a"], app_config=_cfg((GP,)), authorized_db_ids=None)
    assert db.sqls == [] and steps == [] and found == {"a": []}
    assert [(e.key, e.status) for e in ledger] == [("a", "not_queried")]
    assert "2자 미만" in ledger[0].reason


@pytest.mark.asyncio
async def test_missing_edge_path_is_not_queried(polestar: Any,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    db = polestar({GP: FIXTURE_GP})
    monkeypatch.setattr(el, "facet_path", lambda *_a, **_k: None)
    _found, ledger, steps = await el.link_business_names(
        ["이미지 업무"], app_config=_cfg((GP,)), authorized_db_ids=None)
    assert db.sqls == [] and steps == []
    assert ledger[0].status == "not_queried" and "E6" in ledger[0].reason


def test_row_cap_and_min_len_match_adapter() -> None:
    assert el._BUSINESS_ROW_CAP == entity_probe.BUSINESS_LOOKUP_ROW_LIMIT
    assert el._BUSINESS_TERM_MIN_LEN == entity_probe.BUSINESS_TERM_MIN_LEN
