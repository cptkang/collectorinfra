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
