"""plans/143 W7 — 테이블 정의 `kind` 기준·코드 → 공통코드 테이블 후보.

기본키 없음 · K5 · D-294 부기.

결정적 가짜 클라이언트(`test_plan140_w1_fixtures`)로 P1(`run_asset_profile`)을 돌린다. 프로필
(승인 테이블 정의)만 이 파일의 가짜 저장소가 돌려준다. 모의 DB(3308)는 쓰지 않는다 — 조회 답은
테스트가 표로 정한다. 네트워크·DB·Redis·LLM 0.

고정하는 계약:
  ① 기본키 없는 기준·코드 테이블의 코드/이름 컬럼 쌍 → 값 덮음 ≥ 0.9면 라벨 채택
  ② 덮음 0.5는 기각 · 같은 코드에 이름이 둘이면 그 코드는 버림
  ③ 정의 없음 · 그 `kind` 없음 → 종전(쌍 조회 0) · 기존 후보(기본키·이름 단서)는 그대로
  ④ 쌍 조회는 예산 산정에 들어간다 · SELECT만 · 한글 식별자는 엔진별 인용
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from src.domain import schema_inference as inf
from src.domain.table_definitions import KINDS
from src.schema_cache import asset_generation_service as ags
from src.schema_cache.asset_generation_service import AssetGenerationService
from tests.test_schema_cache.test_plan140_w1_fixtures import (
    Ctx,
    FakeClient,
    FakeStore,
    snapshot_from_columns,
)

DATA_COL = "장비구분코드"
CODE_COL, NAME_COL = "구분코드", "구분명"


class _ProfiledStore(FakeStore):
    """승인 프로필(테이블 정의)을 돌려주는 가짜 저장소."""

    def __init__(self, snapshot: dict, profile: dict | None) -> None:
        super().__init__(snapshot)
        self.profile = profile

    def read_current_profile(self, source: str) -> dict | None:  # type: ignore[override]
        return {"profile": self.profile} if self.profile is not None else None


def _snapshot(*, code_pk: bool = False) -> dict:
    v = "varchar"
    return snapshot_from_columns({
        "t_srv": [("장비명", v, False), (DATA_COL, "char", False)],
        "t_kind": [(CODE_COL, "char", code_pk), (NAME_COL, v, False), ("비고내용", v, False)],
    })


def _definitions(kind: str = inf.CODE_TABLE_KIND) -> dict:
    return {
        "t_srv": {"manages": "장비 목록", "kind": "현행", "origin": "import"},
        "t_kind": {"manages": "장비 구분 코드", "kind": kind, "origin": "import"},
    }


def _responder(pairs: list[tuple[str, str]], values: list[str]) -> Any:
    def respond(sql: str) -> list[dict[str, Any]] | None:
        if "column_comment" in sql:
            return []  # 주석 열거가 라벨을 먼저 채우지 않게
        if "AS code_value" in sql:
            return [{"code_value": c, "code_label": n} for c, n in pairs]
        if sql.startswith("SELECT DISTINCT") and (DATA_COL in sql or CODE_COL in sql):
            return [{"v": x} for x in values]
        return None

    return respond


async def _run(profile: dict | None, pairs: list[tuple[str, str]], values: list[str],
               *, code_pk: bool = False) -> dict[str, Any]:
    snapshot = _snapshot(code_pk=code_pk)
    store = _ProfiledStore(snapshot, profile)
    client = FakeClient(snapshot, _responder(pairs, values))

    @asynccontextmanager
    async def factory(source: str | None) -> AsyncIterator[FakeClient]:
        yield client

    registry = SimpleNamespace(get=lambda s: SimpleNamespace(engine="mariadb", db_schema=""))
    config = SimpleNamespace(dbhub=SimpleNamespace(server_url="http://mcp.test:9099/sse"))
    service = AssetGenerationService(
        config, MagicMock(), client_factory=factory, registry_getter=lambda: registry,
        store=store, asset_store=MagicMock(),
    )
    await service.run_asset_profile("src_x", tables=None, by="t", ctx=Ctx())
    draft = store.drafts[-1]
    return {
        "assets": draft["assets"], "evidence": draft["evidence"], "sql": client.sql,
        "pairs_sql": [s for s in client.sql if "AS code_value" in s],
    }


GOOD = [("A", "서버"), ("B", "네트워크")]


class TestDomain:
    def test_kind_value_is_a_definition_kind(self):
        assert inf.CODE_TABLE_KIND in KINDS

    def test_defined_code_tables(self):
        defs = {"S.T_Code": {"kind": inf.CODE_TABLE_KIND}, "t_x": {"kind": "현행"}, "t_y": "x"}
        assert inf.defined_code_tables(defs) == {"t_code"}
        assert inf.defined_code_tables(None) == set()
        assert inf.defined_code_tables(["t_code"]) == set()

    def test_code_name_pairs_korean(self):
        cols = [("그룹회사코드", "char"), ("부점코드", "char"), ("부점명", "varchar"),
                ("본부부점명", "varchar"), ("인스턴스코드ID", "varchar"),
                ("인스턴스코드ID명", "varchar"), ("관리자그룹식별자", "char"),
                ("관리자그룹명", "varchar")]
        assert inf.code_name_pairs(cols) == [
            ("부점코드", "부점명"), ("인스턴스코드ID", "인스턴스코드ID명"),
            ("관리자그룹식별자", "관리자그룹명"),
        ]

    def test_code_name_pairs_latin_and_types(self):
        cols = [("dept_cd", "varchar"), ("dept_nm", "varchar"), ("grpCd", "int"),
                ("grpNm", "varchar"), ("typ_cd", "datetime"), ("typ_nm", "varchar"),
                ("kind_cd", "char"), ("kind_nm", "int")]
        # 날짜형 코드 · 수형 이름은 짝이 아니다
        assert inf.code_name_pairs(cols) == [("dept_cd", "dept_nm"), ("grpCd", "grpNm")]


class TestProfile:
    async def test_no_pk_code_table_labels_adopted(self):
        out = await _run({"table_definitions": _definitions()}, GOOD, ["A", "B"])

        assert out["assets"]["code_labels"][f"t_srv.{DATA_COL}"] == {"A": "서버", "B": "네트워크"}
        row = next(e for e in out["evidence"]["code_columns"] if e["key"] == f"t_srv.{DATA_COL}")
        assert row["labels_from"] == "code_table:t_kind"
        (sql,) = out["pairs_sql"]
        assert sql.startswith(f"SELECT DISTINCT `{CODE_COL}` AS code_value, `{NAME_COL}`")
        (table,) = out["evidence"]["code_tables"]
        assert (table["table"], table["code_column"], table["name_column"]) == (
            "t_kind", CODE_COL, NAME_COL,
        )

    async def test_half_overlap_rejected(self):
        out = await _run({"table_definitions": _definitions()}, [("A", "서버"), ("Z", "기타")],
                         ["A", "B"])

        assert f"t_srv.{DATA_COL}" not in out["assets"]["code_labels"]
        assert len(out["pairs_sql"]) == 1  # 조회는 했고 덮음 0.5로 기각

    async def test_ambiguous_code_label_dropped(self):
        out = await _run({"table_definitions": _definitions()},
                         [("A", "서버"), ("A", "서버(구)"), ("B", "네트워크")], ["A", "B"])

        assert f"t_srv.{DATA_COL}" not in out["assets"]["code_labels"]  # 덮음 0.5
        assert out["evidence"]["code_tables"][0]["ambiguous"] == ["A"]

    async def test_without_definitions_or_kind_unchanged(self):
        base = await _run(None, GOOD, ["A", "B"])
        other_kind = await _run({"table_definitions": _definitions("현행")}, GOOD, ["A", "B"])

        for out in (base, other_kind):
            assert out["pairs_sql"] == [] and out["evidence"]["code_tables"] == []
            assert f"t_srv.{DATA_COL}" not in out["assets"]["code_labels"]
        assert base["sql"] == other_kind["sql"] and base["assets"] == other_kind["assets"]

    async def test_pairs_counted_in_budget(self):
        base = await _run(None, GOOD, ["A", "B"])
        kind = await _run({"table_definitions": _definitions()}, GOOD, ["A", "B"])

        assert kind["evidence"]["budget"]["requested"] == base["evidence"]["budget"][
            "requested"] + 1
        assert all(s.upper().startswith("SELECT") for s in kind["sql"])


class TestCandidates:
    def test_legacy_candidates_bit_identical(self):
        """기본키·이름 단서 경로(폴스타 형)는 정의 유무와 무관하게 같다 · 중복 후보 없음."""
        snapshot = snapshot_from_columns({
            "t_cmcode": [("codeCd", "varchar", True), ("codeNm", "varchar", False)],
            "t_srv": [("hostNm", "varchar", True)],
        })
        legacy = ags._code_table_candidates(snapshot, {})
        assert legacy == [("t_cmcode", "codeCd", "codeNm")]
        assert ags._code_table_candidates(snapshot, {}, None) == legacy
        assert ags._code_table_candidates(snapshot, {}, {"t_srv": {"kind": "현행"}}) == legacy
        assert ags._code_table_candidates(
            snapshot, {}, {"t_cmcode": {"kind": inf.CODE_TABLE_KIND}}
        ) == legacy

    def test_defined_pairs_capped(self, monkeypatch):
        monkeypatch.setattr(ags, "MAX_DEFINED_CODE_PAIRS", 1)
        snapshot = snapshot_from_columns({
            "t_a": [("가코드", "char", False), ("가명", "varchar", False)],
            "t_b": [("나코드", "char", False), ("나명", "varchar", False)],
        })
        defs = {t: {"kind": inf.CODE_TABLE_KIND} for t in ("t_a", "t_b")}
        assert ags._code_table_candidates(snapshot, {}, defs) == [("t_a", "가코드", "가명")]

    async def test_pk_table_without_name_hint_picked_by_kind(self):
        """기본키가 있어도 테이블 이름·주석에 코드 단서가 없으면 종전 경로는 못 고른다 — 정의
        경로가 고른다."""
        out = await _run({"table_definitions": _definitions()}, GOOD, ["A", "B"], code_pk=True)
        assert out["assets"]["code_labels"][f"t_srv.{DATA_COL}"] == {"A": "서버", "B": "네트워크"}
