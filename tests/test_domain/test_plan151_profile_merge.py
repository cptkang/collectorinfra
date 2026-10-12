"""plans/151 W1·W7 — 정의 출처 순위 병합 · 식별 키 후보에서 수집 이력 제외.

확인하는 계약:
- W1: `table_definitions` 출처 순위 `manual` > `import` > `llm` > `comment`. 초안 항목은 base보다
  순위가 같거나 높을 때만 교체한다. 출처 없는(미지 값) base는 `import`, 출처 없는(미지 값) 초안은
  최저로 본다. 정의 키가 없는 프로필은 병합 결과가 종전과 같다.
- W7: `entity_keys_asset(definitions=…)`는 `kind`가 수집이력·수집적재인 테이블을 후보에서 뺀다.
  후보가 전부 빠지면 종전 규칙 · definitions=None이면 종전과 같다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from src.domain import profile_merge
from src.domain import schema_inference as inf
from src.domain.profile_merge import (
    DEFINITION_ORIGIN_RANK,
    base_definition_origin,
    merge_profile,
)
from src.domain.table_definitions import ORIGINS, PROFILE_KEY

ROOT = Path(__file__).resolve().parents[2]
RANK_ORDER = ("comment", "llm", "import", "manual")  # 낮음 → 높음


def _def(manages: str, origin: str | None) -> dict[str, Any]:
    item: dict[str, Any] = {"manages": manages}
    if origin is not None:
        item["origin"] = origin
    return item


def _merge_one(base_item: Any, draft_item: Any) -> Any:
    merged = merge_profile({PROFILE_KEY: {"t_a": base_item}}, {PROFILE_KEY: {"t_a": draft_item}},
                           local_sandbox=False)
    return merged[PROFILE_KEY]["t_a"]


# ─── W1 출처 순위 ───────────────────────────────────────────────


def test_rank_covers_every_origin_in_order():
    assert set(DEFINITION_ORIGIN_RANK) == set(ORIGINS)
    assert sorted(DEFINITION_ORIGIN_RANK, key=DEFINITION_ORIGIN_RANK.__getitem__) == list(
        RANK_ORDER
    )


@pytest.mark.parametrize("base_origin", RANK_ORDER)
@pytest.mark.parametrize("draft_origin", RANK_ORDER)
def test_all_origin_pairs(base_origin, draft_origin):
    base, draft = _def("기존", base_origin), _def("초안", draft_origin)
    out = _merge_one(base, draft)
    replaced = RANK_ORDER.index(draft_origin) >= RANK_ORDER.index(base_origin)
    assert out == (draft if replaced else base)


@pytest.mark.parametrize("base_origin", [*RANK_ORDER, None, "unknown"])
def test_manual_draft_always_wins(base_origin):
    out = _merge_one(_def("기존", base_origin), _def("사람이 고침", "manual"))
    assert out == _def("사람이 고침", "manual")


@pytest.mark.parametrize("base_origin", [None, "unknown"])
@pytest.mark.parametrize("draft_origin", RANK_ORDER)
def test_base_without_origin_counts_as_import(base_origin, draft_origin):
    base = _def("가져오기 이전 사본", base_origin)
    out = _merge_one(base, _def("초안", draft_origin))
    expected_replace = draft_origin in ("import", "manual")
    assert out == (_def("초안", draft_origin) if expected_replace else base)
    assert base_definition_origin(base) == "import"


@pytest.mark.parametrize("draft_origin", [None, "unknown"])
@pytest.mark.parametrize("base_origin", RANK_ORDER)
def test_draft_without_origin_never_replaces(draft_origin, base_origin):
    base = _def("기존", base_origin)
    assert _merge_one(base, _def("초안", draft_origin)) == base


def test_draft_without_origin_still_added_for_new_table():
    merged = merge_profile({PROFILE_KEY: {"t_a": _def("a", "import")}},
                           {PROFILE_KEY: {"t_new": _def("새 테이블", None)}}, local_sandbox=False)
    assert merged[PROFILE_KEY] == {"t_a": _def("a", "import"), "t_new": _def("새 테이블", None)}


def test_comment_draft_does_not_overwrite_curated_import_case_insensitive():
    """6회차 꼴 — P1 주석 초안 98개가 큐레이션 가져오기 정의를 덮지 않는다(맨 이름 비교)."""
    curated = {"manages": "서버 현행 원장", "kind": "현행", "key_columns": ["hostNm"],
               "notes": "주의", "origin": "import"}
    base = {"source": "manual", PROFILE_KEY: {"app.T_SRV": curated, "t_cmt": _def("x", "comment")}}
    draft = {PROFILE_KEY: {"t_srv": _def("서버수집기본", "comment"),
                           "t_cmt": _def("주석 갱신", "comment")}}
    merged = merge_profile(base, draft, local_sandbox=False)
    assert merged[PROFILE_KEY] == {"app.T_SRV": curated, "t_cmt": _def("주석 갱신", "comment")}


UNCHANGED_PROFILES = [
    "polestar.yaml", "polestar_b0.yaml", "polestar_cm_gp.yaml", "polestar_cm_yd.yaml",
    "test_db.yaml",
]


@pytest.mark.parametrize("name", UNCHANGED_PROFILES)
def test_profiles_without_definitions_merge_identically(name, monkeypatch):
    """정의 키가 없는 프로필은 순위 규칙이 있든 없든(규칙 비활성) 병합 결과가 같다."""
    base = yaml.safe_load((ROOT / "config" / "db_profiles" / name).read_text(encoding="utf-8"))
    assert PROFILE_KEY not in base
    drafts = [{}, {"code_labels": {"x.y": {"1": "하나"}}}]
    with_rule = [merge_profile(base, d, local_sandbox=False) for d in drafts]
    monkeypatch.setattr(profile_merge, "TABLE_UNIT_KEYS", ())
    without_rule = [merge_profile(base, d, local_sandbox=False) for d in drafts]
    assert with_rule == without_rule
    assert all(PROFILE_KEY not in m for m in with_rule)


# ─── W7 식별 키 후보 ────────────────────────────────────────────

_HOST = {"column": "hostNm", "ratio": 1.0, "multi_value": False}
_IP = {"column": "ipAddr", "ratio": 1.0, "multi_value": False}
ENTITY = {
    "app.t_hist": {"hostname": _HOST, "ip": _IP},  # 수집 이력 — 행 수가 가장 많다
    "app.t_srv": {"hostname": _HOST, "ip": _IP},  # 현행 원장
    "app.t_aux": {"hostname": _HOST},
}
ROWS = {"app.t_hist": 900_000, "app.t_srv": 5_000, "app.t_aux": 10_000}


def test_history_table_excluded_by_definition_kind():
    assert inf.entity_keys_asset(ENTITY, ROWS)["table"] == "app.t_hist"  # 종전
    definitions = {"T_HIST": {"manages": "수집 이력", "kind": "수집이력", "origin": "import"},
                   "t_srv": {"manages": "원장", "kind": "현행", "origin": "import"}}
    out = inf.entity_keys_asset(ENTITY, ROWS, definitions)
    assert out == inf.entity_keys_asset(
        {k: v for k, v in ENTITY.items() if k != "app.t_hist"}, ROWS
    )
    assert out is not None and out["table"] == "app.t_srv"


def test_collect_load_kind_also_excluded():
    definitions = {"t_hist": {"manages": "적재", "kind": "수집적재", "origin": "comment"}}
    assert inf.entity_keys_asset(ENTITY, ROWS, definitions)["table"] == "app.t_srv"


def test_all_candidates_history_falls_back_to_old_rule():
    definitions = {t.rpartition(".")[2]: {"manages": "이력", "kind": "수집이력"} for t in ENTITY}
    assert inf.entity_keys_asset(ENTITY, ROWS, definitions) == inf.entity_keys_asset(ENTITY, ROWS)


@pytest.mark.parametrize("definitions", [None, {}, {"t_srv": {"manages": "원장", "kind": "현행"}},
                                         "사람이 적은 메모"])
def test_without_excluding_definitions_identical(definitions):
    assert inf.entity_keys_asset(ENTITY, ROWS, definitions) == inf.entity_keys_asset(ENTITY, ROWS)
    assert inf.entity_keys_asset({}, {}, definitions) is None
