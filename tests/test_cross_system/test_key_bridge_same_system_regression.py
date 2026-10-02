"""키 브리지의 폴스타 → 폴스타(같은 시스템 간선) 회귀 특성 테스트.

plans/121 TP-11.11 · D-272 ⑦ · G-26.

브리지(`CROSS_SYSTEM_KEY_BRIDGE_ENABLED`)를 켜면 같은 시스템 간선에서 세 가지가 재현된다.
기존 `test_key_bridge.py::test_no_value_key_equals_legacy_block`은 호스트명형이 아닌 값
(`SV BATCH 009`)을 써서 이 경로를 우연히 피했다.

1. 선행 행에 `server_name`만 있으면 값이 호스트명형이라 `LOWER(hostname) IN (…)`이 조립된다 —
   폴스타는 name ≠ hostname(D-061)이라 **0행**이 된다.
2. 한글 등록명 열 + 코드 열(`os_type`)이면 코드 열 값('linux','aix')이 **서버 키로 선택**된다 —
   D-100이 막은 식별 열 오수집의 재발이다.
3. 존 간 동명 호스트는 사후 대조에서 `ambiguous`로 분류돼 **행이 조용히 빠진다**(U-16).

G-26 재설계(교차 시스템 간선에서만 브리지 적용 · 같은 시스템 간선은 종전 블록 바이트 그대로 ·
`ambiguous` 제거는 교차에서만)가 들어오면 아래 `xfail(strict=True)`가 통과로 뒤집혀 실패한다 —
그때 표지를 떼고 정상 테스트로 바꾼다. 브리지 off 경로(종전 블록)는 지금 바이트를 고정한다.

LLM·네트워크 0(D-127). 제품 코드 변경 없음.
"""

from __future__ import annotations

import hashlib

import pytest

import src.nodes.key_bridge as kb
from src.utils.prior_dependency import DependencyVerdict
from src.utils.query_gen_common import build_prior_rows_block

#: 같은 시스템(폴스타) 존 — 선행·후속 모두 폴스타
GP = "polestar_cm_gp"
YD = "polestar_cm_yd"

#: ① 선행 결과에 등록명(`server_name`)만 있고 값이 호스트명형이다
SERVER_NAME_ONLY = {
    "t1": [
        {"server_name": "sv-web-001", "_source_db": GP},
        {"server_name": "sv-web-002", "_source_db": GP},
    ]
}
#: ② 한글 등록명 열 + 코드 열 — 등록명은 이름 판정에도 값 판정에도 잡히지 않는다
KOREAN_NAME_WITH_CODE = {
    "t1": [
        {"서버명": "웹서버1", "os_type": "linux", "_source_db": GP},
        {"서버명": "배치서버2", "os_type": "aix", "_source_db": GP},
        {"서버명": "DB서버3", "os_type": "linux", "_source_db": GP},
    ]
}

#: 브리지 off 경로(종전 컬럼명 판정 블록)의 바이트 — G-26 "같은 시스템 간선 바이트 불변"의 기준.
#: 종전 블록 문구를 의도적으로 바꾸면 이 해시도 함께 갱신한다.
LEGACY_BLOCK_SHA256 = {
    "server_name_only": "f24467dc589e3636b0398bbdbe09e5b9b1cc1b94581c2fc7d3882d451a9bd800",
    "korean_name_with_code": hashlib.sha256(b"").hexdigest(),
}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ──────────────────────────────────────────────
# 브리지 off 경로 — 지금 바이트를 고정한다
# ──────────────────────────────────────────────

@pytest.mark.parametrize(
    "prior, key",
    [(SERVER_NAME_ONLY, "server_name_only"), (KOREAN_NAME_WITH_CODE, "korean_name_with_code")],
)
def test_legacy_block_bytes_are_pinned(prior, key):
    """브리지 off(종전 블록)는 이 계획의 어떤 변경으로도 바뀌지 않는다(§12.6 ②)."""
    assert _sha(build_prior_rows_block(prior)) == LEGACY_BLOCK_SHA256[key]


def test_legacy_block_scopes_by_name_column_for_server_name_only():
    """종전 경로는 등록명 열(`name`)로 한정한다 — hostname 조건을 만들지 않는다."""
    block = build_prior_rows_block(SERVER_NAME_ONLY)
    assert "name IN ('sv-web-001', 'sv-web-002')" in block
    assert "LOWER(hostname)" not in block


# ──────────────────────────────────────────────
# 브리지 on — 현재 결함(재설계 시 통과로 뒤집힌다)
# ──────────────────────────────────────────────

@pytest.mark.xfail(
    strict=True,
    reason=(
        "TP-10.3 재설계 전 결함 — server_name 값을 LOWER(hostname) 조건으로 조립"
        "(폴스타 name≠hostname → 0행)"
    ),
)
def test_same_system_server_name_only_keeps_legacy_block():
    assert kb.bridge_prior_rows_block(SERVER_NAME_ONLY, GP) == build_prior_rows_block(
        SERVER_NAME_ONLY
    )


def test_same_system_server_name_only_current_condition_is_hostname():
    """결함의 현재 모양을 기록한다 — xfail이 다른 이유로 실패하지 않았음을 보장한다."""
    scope = kb.plan_target_scope(SERVER_NAME_ONLY, GP)
    assert scope is not None
    assert scope["source_col"] == "server_name" and scope["target_col"] == "hostname"
    assert scope["condition"].startswith("(LOWER(hostname) IN ('sv-web-001', 'sv-web-002')")


@pytest.mark.xfail(
    strict=True,
    reason=(
        "TP-10.3 재설계 전 결함 — 한글 등록명 + 코드 열이면 코드 열(os_type)이 서버 키로 선택됨"
        "(D-100 재발)"
    ),
)
def test_same_system_code_column_is_not_selected_as_server_key():
    scope = kb.plan_target_scope(KOREAN_NAME_WITH_CODE, GP)
    assert scope is None or scope["source_col"] != "os_type"
    assert kb.bridge_prior_rows_block(KOREAN_NAME_WITH_CODE, GP) == build_prior_rows_block(
        KOREAN_NAME_WITH_CODE
    )


def test_same_system_code_column_current_selection_is_os_type():
    scope = kb.plan_target_scope(KOREAN_NAME_WITH_CODE, GP)
    assert scope is not None and scope["source_col"] == "os_type"
    assert scope["values"] == ["linux", "aix"]


def _cross_zone_same_name() -> tuple[kb.BridgeGate, DependencyVerdict, dict]:
    task = {"task_id": "t2", "agent": "data_query", "input_from": ["t1"], "depends_on": ["t1"]}
    prior = {
        "t1": {
            "query_results": [
                {"hostname": "web01", "_source_db": GP},
                {"hostname": "web01", "_source_db": YD},
                {"hostname": "db02", "_source_db": GP},
            ],
            "target_db_ids": [GP, YD],
        }
    }
    gate = kb.resolve_gate_identity(task, prior)
    assert gate.identity is not None
    col, values = gate.identity
    verdict = DependencyVerdict(ok=True, scope_col=col, scope_values=values, scope_size=len(values))
    result = {
        "query_results": [
            {"hostname": "web01", "cpu": 1, "_source_db": GP},
            {"hostname": "web01", "cpu": 2, "_source_db": YD},
            {"hostname": "db02", "cpu": 3, "_source_db": GP},
        ],
        "target_db_ids": [GP, YD],
    }
    return gate, verdict, result


@pytest.mark.xfail(
    strict=True,
    reason=(
        "TP-10.3 재설계 전 결함 — 같은 시스템 존 간 동명 호스트를 ambiguous로 분류해 "
        "행을 조용히 제거"
    ),
)
def test_same_system_cross_zone_same_name_rows_are_kept():
    gate, verdict, result = _cross_zone_same_name()
    out = kb.apply_bridge_postcheck(gate.context, verdict, result, "t2")
    assert len(out["query_results"]) == 3


def test_same_system_cross_zone_same_name_current_removal():
    gate, verdict, result = _cross_zone_same_name()
    out = kb.apply_bridge_postcheck(gate.context, verdict, result, "t2")
    assert [r["hostname"] for r in out["query_results"]] == ["db02"]
    note = out["dependency_notes"][-1]
    assert note["counts"]["ambiguous"] == 1 and note["removed_rows"] == 2
    assert note["cross_db_entities"] == {"web01": [GP, YD]}
