"""plans/140 W1 — 라틴 이름·선언 기본키 DB의 P1 자산이 기준 커밋과 비트 동일한지.

기준값은 기준 커밋 `a3da2b7`(W1 직전)의 격리 worktree에서 같은 픽스처(`test_plan140_w1_fixtures`)로
뽑은 해시다 — 자산(`assets`) 정렬 JSON과 실행 SQL 목록(순서 포함)의 SHA-256. `evidence`는 키가
늘었으므로 비교하지 않는다.

- 폴스타형 PostgreSQL(`testdata/pg/init/01_create_tables.sql` · 기본키 있음)
- 같은 DDL의 DB2 변형(대문자 스키마 `POLESTAR`)
- 로컬 샌드박스(`testdata/itam/init/01_schema.sql` · MariaDB · 라틴 camelCase · 기본키 있음)
- 폴스타 전체 DDL(`04_create_all_tables.sql` · 394테이블 · **기본키 0**) — 이름 일치 관계와 예산
  확대가 의도대로 자산을 바꾸므로, 두 기능을 0으로 묶으면(이름 일치 조회 상한 0 · 예산 상한 400)
  기준과 같음을 확인한다(기존 경로 불변).
"""

from __future__ import annotations

import pytest

from src.schema_cache import asset_generation_service as ags
from tests.test_schema_cache.test_plan140_w1_fixtures import (
    ROOT,
    digest,
    profile,
    snapshot_from_ddl,
)

PG_DDL = ROOT / "testdata/pg/init/01_create_tables.sql"
PG_FULL_DDL = ROOT / "testdata/pg/init/04_create_all_tables.sql"
SANDBOX_DDL = ROOT / "testdata/itam/init/01_schema.sql"

# (DDL, DDL 엔진, DDL 기본 스키마, 실행 엔진, 레지스트리 db_schema) → (자산 해시, SQL 해시, SQL 수)
CASES = {
    "pg_postgresql": (
        (PG_DDL, "postgresql", None, "postgresql", ""),
        ("510fd16bf78571ec719aa54de0ef679375fc5b3828e1418f30ab7ba0edc57750",
         "2dc47a51cfdc783842fd50d4b78872d998705f8e8cbc2fe641eab533c9f24c1d", 23),
    ),
    "pg_db2": (
        (PG_DDL, "db2", "POLESTAR", "db2", "POLESTAR"),
        ("9eb4ac4bc855331f1b2888e59c437ecd7c498d67e5a28aba885009d7b4b467a0",
         "26ee4addd81364ddfbf4f7911e96a4cd44686b8998f18970000bac0f1b9a385d", 21),
    ),
    "sandbox_mariadb": (
        (SANDBOX_DDL, "mariadb", None, "mariadb", ""),
        ("540fdbb840b9e79a00fcc647298e3f8b12ac33e85080a2038c4429de93e46342",
         "20c12837bd9ea21b51a803f147e48abe861ec9b209dd26f75e8117f26a06318b", 44),
    ),
}
PG_FULL_BASE = (
    "64016d2b0fe6f7d7f0801e7239930d5173163aa9156c3e11d9e2af4d7618e919",
    "940246ba61967bc95c82dfe67ee89644420aee3985d486fa6c944417816a9eb6", 400,
)


@pytest.mark.parametrize("name", sorted(CASES))
async def test_assets_and_sql_match_base_commit(name):
    (path, ddl_engine, default_schema, engine, db_schema), expected = CASES[name]
    snapshot = snapshot_from_ddl(path, ddl_engine, default_schema)
    out = await profile(snapshot, engine, db_schema)
    assert (digest(out["draft"]["assets"]), digest(out["sql"]), len(out["sql"])) == expected
    budget = out["draft"]["evidence"]["budget"]
    # 필요량이 기본 예산 이하이면 예산은 종전 그대로 400
    assert budget["limit"] == ags.DEFAULT_PROBE_BUDGET == 400
    assert budget["requested"] <= 400 and budget["cap"] == ags.PROBE_BUDGET_CAP
    assert not [e for e in out["draft"]["evidence"]["relationships"]
                if e["origin"] == "name_match"]


async def test_no_pk_polestar_existing_paths_unchanged(monkeypatch):
    """기본키 없는 394테이블 — 이름 일치·예산 확대를 0으로 묶으면 기준과 같다."""
    monkeypatch.setattr(ags, "NAME_MATCH_MAX_QUERIES", 0)
    monkeypatch.setattr(ags, "PROBE_BUDGET_CAP", 400)
    snapshot = snapshot_from_ddl(PG_FULL_DDL, "postgresql")
    out = await profile(snapshot, "postgresql")
    assert (digest(out["draft"]["assets"]), digest(out["sql"]), len(out["sql"])) == PG_FULL_BASE
    # 이름 일치 후보는 침묵 생략 없이 「후보 상한 초과」로 남는다
    name_match = [e for e in out["draft"]["evidence"]["relationships"]
                  if e["origin"] == "name_match"]
    assert name_match and {e["error"] for e in name_match} == {"후보 상한 초과"}


async def test_no_pk_polestar_gains_only_additive_assets():
    """기본 상수에서는 이름 일치 관계가 더해지고 예산이 늘어 표본 컬럼이 많아진다.

    기존 경로의 관계는 0건 그대로다(기본키·선언 FK 없음).
    """
    snapshot = snapshot_from_ddl(PG_FULL_DDL, "postgresql")
    out = await profile(snapshot, "postgresql")
    assets, evidence = out["draft"]["assets"], out["draft"]["evidence"]
    assert all(r["origin"] == "name_match" for r in assets["relationships"])
    assert assets["relationships"]
    budget = evidence["budget"]
    assert 400 < budget["limit"] == min(budget["requested"], budget["cap"])
    assert budget["skipped"] == 0
