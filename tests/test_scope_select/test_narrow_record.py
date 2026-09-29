"""미조회 범위 기록 + 재확장 (Plan 82 W65-T8 · SPEC-scope-select §5.3 불변식 6).

★ 범위 축소는 **정보 손실이 복구되지 않는 절단**이다. 그래서 두 가지가 함께 있어야 한다:
  ① 무엇을 보지 않았는지 응답에 남는다(침묵 절단 금지)
  ② 되돌릴 1클릭 경로가 있다 — 기록만 있고 되돌릴 길이 없으면 좁히기 비용이 이득을 넘는다

DB·LLM 0(D-127).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.api.routes.query import _scope_narrowed_or_none, build_scope_reexpand
from src.config import MultiDBConfig
from src.nodes.output_generator import _append_scope_note

ALL_DBS = ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd"]


def _config(active=None):
    return SimpleNamespace(
        multi_db=MultiDBConfig(
            active_db_ids_csv=",".join(ALL_DBS if active is None else active)
        )
    )


def _body(selected=None):
    return SimpleNamespace(query="모든 서버의 OS 버전", selected_db_ids=selected)


class TestNarrowedRecord:
    def test_partial_selection_is_recorded(self):
        record = _scope_narrowed_or_none(_body(["polestar_b0"]), _config(), None)

        assert record["selected"] == ["은행존"]
        assert record["skipped"] == ["공동존"]
        assert record["skipped_db_ids"] == ["polestar_cm_gp", "polestar_cm_yd"]
        assert record["all_db_ids"] == ALL_DBS

    def test_full_selection_is_not_a_narrowing(self):
        assert _scope_narrowed_or_none(_body(ALL_DBS), _config(), None) is None

    def test_group_partial_selection_names_unqueried_zone(self):
        """공동존에서 김포만 골랐으면 여의도도 미조회다(plans/123 W-2 ③ — 존 이름으로)."""
        record = _scope_narrowed_or_none(
            _body(["polestar_b0", "polestar_cm_gp"]), _config(), None
        )

        assert record["skipped"] == ["공동존 여의도"]
        assert record["skipped_db_ids"] == ["polestar_cm_yd"]

    def test_no_selection_is_not_a_narrowing(self):
        assert _scope_narrowed_or_none(_body(None), _config(), None) is None

    def test_authorization_shapes_the_universe(self):
        """인가 밖 존은 애초에 '조회 대상'이 아니므로 미조회로 세지 않는다."""
        record = _scope_narrowed_or_none(
            _body(["polestar_cm_gp", "polestar_cm_yd"]), _config(),
            {"allowed_db_ids": ["polestar_cm_gp", "polestar_cm_yd"]},
        )

        assert record is None, "허용 범위(공동존 둘)를 모두 골랐으면 좁힌 것이 없다"

        narrowed = _scope_narrowed_or_none(
            _body(["polestar_cm_gp"]), _config(),
            {"allowed_db_ids": ["polestar_cm_gp", "polestar_cm_yd"]},
        )
        # 권한 밖 은행존은 미조회 목록에 나오지 않는다(D-264 ②) — 권한 안의 여의도만 적는다
        assert narrowed["skipped_db_ids"] == ["polestar_cm_yd"]
        assert "은행존" not in narrowed["skipped"]


class TestReexpandPanel:
    def test_panel_offers_every_authorized_db_id(self):
        record = _scope_narrowed_or_none(_body(["polestar_b0"]), _config(), None)

        panel = build_scope_reexpand(record, "모든 서버의 OS 버전")

        assert panel["kind"] == "scope_reexpand"
        assert panel["options"][0]["db_ids"] == ALL_DBS
        assert panel["options"][0]["default"] is True
        assert panel["original_query"] == "모든 서버의 OS 버전"

    def test_panel_names_what_was_skipped(self):
        record = _scope_narrowed_or_none(_body(["polestar_b0"]), _config(), None)

        assert "공동존" in build_scope_reexpand(record, "q")["question"]

    def test_no_panel_without_narrowing(self):
        assert build_scope_reexpand(None, "q") is None
        assert build_scope_reexpand({"skipped": []}, "q") is None


class TestResponseNote:
    def test_note_is_appended_deterministically(self):
        """★ LLM 각주 지시는 누락된다 — 코드가 붙인다."""
        record = _scope_narrowed_or_none(_body(["polestar_b0"]), _config(), None)

        text = _append_scope_note("조회 결과입니다.", {"scope_narrowed": record})

        assert "조회 결과입니다." in text
        assert "공동존은(는) 조회하지 않았습니다" in text

    def test_no_note_without_narrowing(self):
        assert _append_scope_note("결과", {"scope_narrowed": None}) == "결과"
        assert _append_scope_note("결과", {}) == "결과"
