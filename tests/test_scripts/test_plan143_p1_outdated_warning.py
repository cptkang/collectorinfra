"""plans/143 3회차 반출 준비 ⑥ — 구버전 P1 초안 경고(`P1_OUTDATED_DRAFT`).

b1fabf4(plans/140) 이전 빌드로 만든 P1 초안은 evidence 에 `columns`·`budget.requested`·`budget.cap`
이 없다(2회차 run 의 P1 근거 0 — F7). 카탈로그 `p1_warnings`가 이를 고정 문구로 알리는지,
해시 불일치 경고와 독립인지, 누출 관문을 지나는지 고정한다. 실 Redis·DB·LLM 0 · 픽스처는 합성.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.itam_bench import catalog as cat
from scripts.itam_bench import redact as rd
from scripts.itam_bench import report as rp
from tests.test_scripts.test_plan140_w2_export import _draft, _store


@pytest.fixture()
def policy() -> cat.ColumnPolicy:
    return cat.ColumnPolicy(
        db_id="itam",
        scope="closed",
        tables={"자산마스터": {"담당자명": "pii", "금액구분": "amount", "상태코드": "general"}},
        canary_literals=("가상카나리아",),
    )


def _old_draft(
    *, drop_columns: bool = True, drop_budget: tuple[str, ...] = ("requested", "cap"), **over: Any
) -> dict[str, Any]:
    """구버전 모양 초안 — 현행 픽스처에서 b1fabf4 가 더한 키를 뺀다."""
    draft = copy.deepcopy(_draft(**over))
    if drop_columns:
        draft["evidence"].pop("columns")
    for key in drop_budget:
        draft["evidence"]["budget"].pop(key)
    return draft


def _catalog(policy: cat.ColumnPolicy, drafts: list[dict[str, Any]]) -> dict[str, Any]:
    schema = cat.load_schema_source("structure_store", store=_store(drafts=drafts))
    return cat.build_schema_catalog(schema, policy, assets={})


class TestOutdatedDraft:
    def test_old_shape_warns(self, policy: cat.ColumnPolicy) -> None:
        catalog = _catalog(policy, [_old_draft()])
        assert catalog["p1_warnings"] == [cat.P1_OUTDATED_DRAFT]
        assert catalog["p1_fallback"] is None and catalog["p1"]["draft_id"] == "d0000000aaaa"
        # 예산 칸은 그대로 옮기되 없는 키는 None
        assert catalog["p1"]["budget"]["requested"] is None

    @pytest.mark.parametrize(
        ("drop_columns", "drop_budget"),
        [(True, ()), (False, ("requested",)), (False, ("cap",))],
    )
    def test_any_marker_missing_warns(
        self, drop_columns: bool, drop_budget: tuple[str, ...]
    ) -> None:
        assert cat.p1_outdated(_old_draft(drop_columns=drop_columns, drop_budget=drop_budget))

    def test_empty_evidence_is_outdated(self) -> None:
        assert cat.p1_outdated({"draft_id": "x", "kind": "profile", "evidence": {}})

    def test_current_shape_no_warning(self, policy: cat.ColumnPolicy) -> None:
        assert not cat.p1_outdated(_draft())
        assert _catalog(policy, [_draft()])["p1_warnings"] == []

    def test_independent_of_hash_mismatch(self, policy: cat.ColumnPolicy) -> None:
        catalog = _catalog(policy, [_old_draft(snapshot_hash="older")])
        assert catalog["p1_warnings"] == [cat.P1_HASH_MISMATCH, cat.P1_OUTDATED_DRAFT]
        catalog = _catalog(policy, [_draft(snapshot_hash="older")])
        assert catalog["p1_warnings"] == [cat.P1_HASH_MISMATCH]

    def test_no_draft_no_warning(self, policy: cat.ColumnPolicy) -> None:
        catalog = _catalog(policy, [])
        assert catalog["p1_warnings"] == []
        assert catalog["p1"] is None and catalog["p1_fallback"] == "P1 초안 없음"

    def test_fallback_path_no_warning(self, tmp_path: Path) -> None:
        (tmp_path / "itam_schema.json").write_text(
            json.dumps(
                {
                    "_cache_version": 1,
                    "_fingerprint": "x",
                    "_db_id": "itam",
                    "_cached_at": 0.0,
                    "_cached_at_iso": "",
                    "schema": {
                        "tables": {
                            "자산마스터": {
                                "columns": [
                                    {"name": "자산번호", "type": "varchar", "primary_key": True}
                                ]
                            }
                        },
                        "relationships": [],
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        schema = cat.load_schema_source(
            "structure_store",
            store=_store(snapshot=None, drafts=[_old_draft()]),
            cache_dir=tmp_path,
        )
        assert schema["source"] == "schema_cache"
        assert schema["p1_fallback"] == "스냅샷 없음" and schema["p1_warnings"] == []

    def test_report_head(self, policy: cat.ColumnPolicy) -> None:
        catalog = _catalog(policy, [_old_draft()])
        head = rp.render_report({"run_id": "r"}, catalog, []).split("## 0.")[0]
        assert f"> 주의 — {cat.P1_OUTDATED_DRAFT}" in head


class TestLeakGate:
    def _gate(self, policy: cat.ColumnPolicy) -> rd.LeakGate:
        return rd.LeakGate(
            policy=policy,
            vault=rd.PiiVault.from_policy(policy),
            user_values={"login_id": "user9999"},
            code_originals=None,
        )

    def test_fixed_text_passes(self, policy: cat.ColumnPolicy) -> None:
        gate = self._gate(policy)
        assert gate.rules(cat.P1_OUTDATED_DRAFT, schema_section=True) == []
        assert gate.rules(f"> 주의 — {cat.P1_OUTDATED_DRAFT}", schema_section=True) == []

    def test_warning_in_outputs_passes(self, policy: cat.ColumnPolicy) -> None:
        catalog = _catalog(policy, [_old_draft()])
        head = rp.render_report({"run_id": "r"}, catalog, []).split("## 0.")[0]
        files = {
            "schema_catalog.yaml": yaml.safe_dump(
                {"p1_warnings": catalog["p1_warnings"]}, allow_unicode=True
            ),
            "report.md": head,
        }
        assert self._gate(policy).check(files) == []


# ──────────────────────────────────────────────
# 형식 손상 초안(`P1_MALFORMED_DRAFT`) — 경고하고 초안 없음과 같게 진행(사용자 결정 2026-10-07)
# ──────────────────────────────────────────────

from scripts.itam_bench import __main__ as cli  # noqa: E402


def _set(path: tuple[Any, ...], value: Any) -> dict[str, Any]:
    """현행 초안에서 경로 한 칸을 바꾼다(정수는 목록 첨자)."""
    draft = copy.deepcopy(_draft())
    node: Any = draft
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = value
    return draft


_MALFORMED: dict[str, tuple[tuple[Any, ...], Any]] = {
    "evidence_list": (("evidence",), ["columns"]),
    "evidence_empty_list": (("evidence",), []),
    "evidence_str": (("evidence",), "columns budget"),
    "evidence_int": (("evidence",), 7),
    "budget_list": (("evidence", "budget"), ["requested", "cap"]),
    "budget_str": (("evidence", "budget"), "requested cap"),
    "columns_int": (("evidence", "columns"), 7),
    "columns_str": (("evidence", "columns"), "자산마스터.상태코드"),
    "relationships_dict": (("evidence", "relationships"), {"child": "자산이력"}),
    "allowed_tables_int": (("evidence", "allowed_tables"), 7),
    "code_columns_str": (("evidence", "code_columns"), "x"),
    "column_flag_int": (("evidence", "columns", 0, "flag"), 7),
    "relationship_columns_int": (("evidence", "relationships", 0, "child_columns"), 7),
    "assets_list": (("assets",), ["code_values"]),
    "code_values_list": (("assets", "code_values"), ["A01"]),
    "code_values_item_int": (("assets", "code_values", "자산마스터.상태코드"), 7),
    "code_labels_str": (("assets", "code_labels"), "A01"),
}


def _assert_runs_without_p1(policy: cat.ColumnPolicy, drafts: list[Any], tmp_path: Path) -> None:
    """카탈로그·run.json 지문·치환 코드값·리포트·누출 관문까지 예외 없이 지나고 P1 근거는 없다."""
    schema = cat.load_schema_source("structure_store", store=_store(drafts=drafts))
    assert schema["source"] == "structure_store"  # 스냅샷은 지킨다(파일 폴백 아님)
    assert schema["_p1_draft"] is None
    assets = {"profile": None, "p1": cat.p1_asset(schema["_p1_draft"])}
    catalog = cat.build_schema_catalog(schema, policy, assets=assets, repo_root=tmp_path)
    assert catalog["p1_warnings"] == [cat.P1_MALFORMED_DRAFT]
    assert catalog["p1"] is None and catalog["p1_fallback"] == "P1 초안 형식 손상"
    assert catalog["assets"]["p1"] is None
    assert catalog["summary"]["p1_profiled_columns"] == 0
    assert "p1" not in catalog["summary"]["relations"]
    # 손상 초안의 설명 초안도 읽지 않는다(DDL 주석만)
    history = {c["name"]: c for c in catalog["tables"]["자산이력"]["columns"]}
    assert history["이력코드"]["meaning"] != "이력 종류"
    staged, gate = cli.stage_gated(
        run_meta={"run_id": "r", "env": "closed", "assets": catalog["assets"]},
        catalog_doc=catalog,
        records=[],
        policy=policy,
        vault=rd.PiiVault.from_policy(policy),
        user_values={"login_id": "user9999"},
        p1_draft=schema["_p1_draft"],
    )
    assert "code_samples.yaml" not in staged
    ok, violations = rd.write_gated(tmp_path / "run", staged, gate)
    assert ok and violations == []
    report = (tmp_path / "run" / "report.md").read_text(encoding="utf-8")
    assert f"> 주의 — {cat.P1_MALFORMED_DRAFT}" in report
    assert "사유: P1 초안 형식 손상" in report


class TestMalformedDraft:
    @pytest.mark.parametrize("case", sorted(_MALFORMED))
    def test_detected_and_public_helpers_do_not_raise(self, case: str) -> None:
        draft = _set(*_MALFORMED[case])
        assert cat.p1_malformed(draft)
        # 공개 함수는 로더 밖에서 불려도 멈추지 않는다
        cat.p1_outdated(draft)
        cat.p1_summary(draft)
        cat.p1_asset(draft)

    @pytest.mark.parametrize("case", sorted(_MALFORMED))
    def test_catalog_continues_without_p1(
        self, case: str, policy: cat.ColumnPolicy, tmp_path: Path
    ) -> None:
        _assert_runs_without_p1(policy, [_set(*_MALFORMED[case])], tmp_path)

    @pytest.mark.parametrize("item", ["broken", ["x"], 7, None])
    def test_non_mapping_draft_item(
        self, item: Any, policy: cat.ColumnPolicy, tmp_path: Path
    ) -> None:
        _assert_runs_without_p1(policy, [item], tmp_path)
        for value in (item, ["x"]):
            assert cat.p1_malformed(value)
            assert cat.p1_summary(value) is None and cat.p1_asset(value) is None

    def test_non_mapping_item_beside_valid_draft(self, policy: cat.ColumnPolicy) -> None:
        catalog = _catalog(policy, ["broken", _draft()])
        assert catalog["p1_warnings"] == [cat.P1_MALFORMED_DRAFT]
        assert catalog["p1"]["draft_id"] == "d0000000aaaa" and catalog["p1_fallback"] is None
        assert catalog["summary"]["p1_profiled_columns"] > 0

    def test_distinct_from_outdated(self) -> None:
        assert cat.P1_MALFORMED_DRAFT != cat.P1_OUTDATED_DRAFT

    @pytest.mark.parametrize(
        "draft",
        [
            _draft(),
            _old_draft(),
            {"draft_id": "x", "kind": "profile", "evidence": {}},
            {"draft_id": "x", "kind": "profile"},
            {"draft_id": "x", "kind": "profile", "evidence": None, "assets": None},
            {"draft_id": "x", "kind": "profile", "evidence": {"budget": None, "columns": None}},
        ],
    )
    def test_absent_fields_are_not_malformed(self, draft: dict[str, Any]) -> None:
        # 없는 칸은 구버전 판정 몫 — 손상으로 보지 않는다(기존 동작 그대로)
        assert not cat.p1_malformed(draft)

    @pytest.mark.parametrize("path", [("evidence",), ("evidence", "budget")])
    def test_none_section_stays_outdated(
        self, path: tuple[str, ...], policy: cat.ColumnPolicy
    ) -> None:
        catalog = _catalog(policy, [_set(path, None)])
        assert catalog["p1_warnings"] == [cat.P1_OUTDATED_DRAFT]
        assert catalog["p1"]["draft_id"] == "d0000000aaaa"

    def test_fixed_text_passes_gate(self, policy: cat.ColumnPolicy) -> None:
        gate = TestLeakGate()._gate(policy)
        assert gate.rules(cat.P1_MALFORMED_DRAFT, schema_section=True) == []
        assert gate.rules(f"> 주의 — {cat.P1_MALFORMED_DRAFT}", schema_section=True) == []

    @pytest.mark.parametrize(
        "draft_id", [{"a": 1}, ["d0"], 7, "", "bad id", "x" * 65, "가상초안"]
    )
    def test_draft_id_outside_gate_form(
        self, draft_id: Any, policy: cat.ColumnPolicy, tmp_path: Path
    ) -> None:
        # 관문(`p1_draft_id` 식별자 형식)이 거르는 모양은 손상 — run 이 [4/4] 에서 멈추지 않는다
        draft = _set(("draft_id",), draft_id)
        assert cat.p1_malformed(draft)
        _assert_runs_without_p1(policy, [draft], tmp_path)

    def test_draft_id_form_matches_gate(self) -> None:
        assert cat.P1_DRAFT_ID_FORM.pattern == rd._CODE_SAMPLES_ID.pattern

    @pytest.mark.parametrize("draft", [_draft(), {"kind": "profile", "evidence": {}}])
    def test_draft_id_valid_or_absent_not_malformed(self, draft: dict[str, Any]) -> None:
        assert not cat.p1_malformed(draft)
        assert not cat.p1_malformed({**draft, "draft_id": None})


# ──────────────────────────────────────────────
# 설명 초안 손상(`P1_DESCRIPTION_DRAFT_MALFORMED`) — 설명 초안만 버리고 스냅샷·P1 은 지킨다
# ──────────────────────────────────────────────


class TestMalformedDescriptionDraft:
    @pytest.mark.parametrize(
        ("descriptions", "description_draft_id"),
        [
            ({"desc00000001": ["자산이력"]}, "desc00000001"),
            ({"desc00000001": "이력 종류"}, "desc00000001"),
            ({"desc00000001": {"descriptions": ["자산이력"]}}, "desc00000001"),
            ({"desc00000001": {"descriptions": "이력 종류"}}, "desc00000001"),
            ({}, ["desc00000001"]),
        ],
    )
    def test_keeps_snapshot_and_p1(
        self,
        descriptions: dict[str, Any],
        description_draft_id: Any,
        policy: cat.ColumnPolicy,
        tmp_path: Path,
    ) -> None:
        draft = _set(("description_draft_id",), description_draft_id)
        schema = cat.load_schema_source(
            "structure_store", store=_store(drafts=[draft], descriptions=descriptions)
        )
        assert schema["source"] == "structure_store" and schema["_p1_draft"] is not None
        catalog = cat.build_schema_catalog(schema, policy, assets={})
        assert catalog["p1_warnings"] == [cat.P1_DESCRIPTION_DRAFT_MALFORMED]
        assert catalog["p1"]["draft_id"] == "d0000000aaaa" and catalog["p1_fallback"] is None
        assert catalog["summary"]["p1_profiled_columns"] > 0
        history = {c["name"]: c for c in catalog["tables"]["자산이력"]["columns"]}
        assert history["이력코드"]["meaning"] is None
        head = rp.render_report({"run_id": "r"}, catalog, []).split("## 0.")[0]
        assert f"> 주의 — {cat.P1_DESCRIPTION_DRAFT_MALFORMED}" in head

    def test_non_text_value_skipped(self, policy: cat.ColumnPolicy) -> None:
        # 문자열 아닌 설명 값은 그 항목만 건너뛴다(경고 없음 — 매핑 아닌 테이블 묶음과 같은 처리)
        descriptions = {
            "desc00000001": {
                "descriptions": {"자산이력": {"자산이력.이력코드": {"text": "이력 종류"}}}
            }
        }
        catalog = cat.build_schema_catalog(
            cat.load_schema_source("structure_store", store=_store(descriptions=descriptions)),
            policy,
            assets={},
        )
        assert catalog["p1_warnings"] == []
        history = {c["name"]: c for c in catalog["tables"]["자산이력"]["columns"]}
        assert history["이력코드"]["meaning"] is None

    def test_normal_description_unchanged(self, policy: cat.ColumnPolicy) -> None:
        catalog = _catalog(policy, [_draft()])
        history = {c["name"]: c for c in catalog["tables"]["자산이력"]["columns"]}
        assert history["이력코드"]["meaning"] == "이력 종류" and catalog["p1_warnings"] == []

    def test_fixed_text_passes_gate(self, policy: cat.ColumnPolicy) -> None:
        gate = TestLeakGate()._gate(policy)
        text = cat.P1_DESCRIPTION_DRAFT_MALFORMED
        assert gate.rules(text, schema_section=True) == []
        assert gate.rules(f"> 주의 — {text}", schema_section=True) == []


# ──────────────────────────────────────────────
# 생산자 계약 — 현행 P1(`run_asset_profile`)이 어느 경로에서도 표지 키를 쓴다(verifier 추가)
# ──────────────────────────────────────────────

import asyncio  # noqa: E402

from src.schema_cache import asset_generation_service as ags  # noqa: E402
from tests.test_schema_cache.test_d294_asset_generation_service import (  # noqa: E402
    SRC,
    _service,
    _snapshot,
    env,  # noqa: F401 — 픽스처 재사용
)
from tests.test_schema_cache.test_plan104_service_fixtures import (  # noqa: E402
    failing_client_factory,
)


async def _real_draft(e: Any, tables: list[str] | None = None, **kw: Any) -> dict[str, Any]:
    await _snapshot(e)
    result = await _service(e, **kw).run_asset_profile(SRC, tables=tables, by="v", ctx=e.ctx)
    draft = await e.store.get_asset_draft(SRC, result["draft_id"])
    assert draft is not None and draft["kind"] == "profile"
    return draft


class TestProducerContract:
    """오경고 방지 — 현행 빌드가 만든 초안은 경로와 무관하게 `p1_outdated` 거짓이다."""

    async def test_online(self, env: Any) -> None:  # noqa: F811
        draft = await _real_draft(env)
        assert draft["evidence"]["columns"] and not cat.p1_outdated(draft)

    async def test_offline(self, env: Any) -> None:  # noqa: F811
        draft = await _real_draft(
            env, client_factory=failing_client_factory(ConnectionError("down"))
        )
        assert draft["evidence"]["offline"] and not cat.p1_outdated(draft)

    async def test_zero_budget(self, env: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
        monkeypatch.setattr(ags, "PROBE_BUDGET_CAP", 0)
        draft = await _real_draft(env)
        budget = draft["evidence"]["budget"]
        assert budget["limit"] == 0 and budget["cap"] == 0 and budget["skipped"] > 0
        assert not cat.p1_outdated(draft)

    async def test_no_profiled_columns(self, env: Any) -> None:  # noqa: F811
        # 행 0 테이블만 범위 — 컬럼 근거가 비어도 키는 있다(빈 목록 ≠ 구버전)
        draft = await _real_draft(env, tables=["t_empty"])
        assert "columns" in draft["evidence"] and not cat.p1_outdated(draft)

    def test_through_catalog_loader(self, env: Any) -> None:  # noqa: F811
        # 로더가 내부에서 asyncio.run 을 쓰므로 동기 테스트로 둔다
        asyncio.run(_real_draft(env))
        schema = cat.load_schema_source("structure_store", db_id=SRC, store=env.store)
        assert schema["p1"] is not None and cat.P1_OUTDATED_DRAFT not in schema["p1_warnings"]
