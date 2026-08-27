"""호스트 인가 게이트 (Plan 78 W3-5 / Plan 80 WU-15 · SPEC M5 · **안전 필수**).

**조회 권한 ≠ 조사 권한.** `allowed_db_ids`만으로 실호스트 조사를 허용하면 "DB를 읽을 수 있는
사람 = 서버에 명령을 보낼 수 있는 사람"이 된다(ETCLOVG G 계층 갭 ① — 78 §4.4 최우선).

R-9 확정(2026-08-27): `HOST_AUTHZ_MODE=admin_only` · **미설정·미상 값도 차단**(fail-closed).
"""

from __future__ import annotations

import ast
import pathlib
from types import SimpleNamespace

import pytest

from src.domain.host_authz import (
    DENY_DB_NOT_ALLOWED,
    DENY_NO_PRINCIPAL,
    DENY_ROLE_NOT_ALLOWED,
    DENY_UNKNOWN_MODE,
    Principal,
    authorize_host_investigation,
)


def _authorize(**kw):
    kw.setdefault("mode", "admin_only")
    return authorize_host_investigation(**kw)


# ──────────────────────────────────────────────
# fail-closed (R-9)
# ──────────────────────────────────────────────

@pytest.mark.parametrize("mode", [None, "", "   ", "allow_all", "ADMIN", "admin-only", "off"])
def test_unknown_mode_is_denied(mode):
    """★ 미설정·오타·모르는 값은 **전부 차단**. 인가에서 fail-open은 통제가 없는 것과 같다."""
    d = authorize_host_investigation(mode=mode, principal=Principal(role="admin", user_id="a"))
    assert d.allowed is False
    assert d.reason == DENY_UNKNOWN_MODE


@pytest.mark.parametrize("principal", [None, Principal(), Principal(role=""),
                                       Principal(role="   ")])
def test_missing_principal_is_denied(principal):
    """★ 주체 미상도 차단 — `user_role` 전파 누락(C-4)이 곧 fail-open이 되지 않는다."""
    d = _authorize(principal=principal)
    assert d.allowed is False
    assert d.reason == DENY_NO_PRINCIPAL


@pytest.mark.parametrize("role", ["user", "viewer", "operator", "Admins", "root"])
def test_non_admin_roles_are_denied(role):
    d = _authorize(principal=Principal(role=role, user_id="u"))
    assert d.allowed is False
    assert d.reason == DENY_ROLE_NOT_ALLOWED


def test_admin_is_allowed():
    d = _authorize(principal=Principal(role="admin", user_id="a1"))
    assert d.allowed is True
    assert d.principal == "a1"


def test_role_matching_is_case_insensitive_but_exact():
    """대소문자만 무시한다 — 부분매칭은 `admin_readonly` 같은 값을 통과시킨다."""
    assert _authorize(principal=Principal(role="ADMIN", user_id="a")).allowed is True
    assert _authorize(principal=Principal(role="admin_readonly", user_id="a")).allowed is False


def test_system_principal_is_allowed_for_event_path():
    """이벤트 경로(알람 자동 조사)에는 사용자가 없다 — `system` 주체를 명시 허용한다.

    막으면 CW-A가 무력화된다. 대신 **판정 결과가 감사에 남는다**(SPEC Q4 — 사용자 확인 사항).
    """
    d = _authorize(principal=Principal.system())
    assert d.allowed is True
    assert d.principal.startswith("system:")


def test_db_scope_still_applies_on_top_of_role():
    """★ 조사 권한이 있어도 **조회 인가 밖의 DB**는 열지 않는다 — 둘은 곱해진다."""
    p = Principal(role="admin", user_id="a", allowed_db_ids=["polestar_gimpo"])
    assert _authorize(principal=p, db_id="polestar_gimpo").allowed is True
    d = _authorize(principal=p, db_id="polestar_yeouido")
    assert d.allowed is False and d.reason == DENY_DB_NOT_ALLOWED


def test_none_allowed_db_ids_means_unrestricted():
    """`allowed_db_ids=None`은 기존 규약대로 제한 없음이다(회귀 0)."""
    p = Principal(role="admin", user_id="a", allowed_db_ids=None)
    assert _authorize(principal=p, db_id="any").allowed is True


def test_every_denial_carries_a_reason():
    """거부는 **조용히 건너뛰지 않는다** — 사유 없는 거부가 없다."""
    for d in (
        authorize_host_investigation(mode=None, principal=Principal(role="admin")),
        _authorize(principal=None),
        _authorize(principal=Principal(role="user", user_id="u")),
    ):
        assert d.allowed is False and d.reason


def test_decision_maps_to_audit_slot():
    """W6-5 — 판정이 감사 레코드의 `authz` 슬롯 형태로 나온다."""
    d = _authorize(principal=Principal(role="user", user_id="u"), hostname="svweb001")
    rec = d.as_audit()
    assert set(rec) == {"allowed", "mode", "principal", "reason", "target"}
    assert rec["target"] == "svweb001"


# ──────────────────────────────────────────────
# 전파 배선 (SPEC C-4)
# ──────────────────────────────────────────────

def test_user_role_is_propagated_end_to_end():
    """★ `role`은 종전에 `AgentState`까지 오지 않았다(C-4) — 전 구간 배선을 실측한다.

    하나라도 빠지면 role이 None이 되어 **전 조사가 차단**된다(fail-closed라 안전 방향으로
    깨지지만, 기능이 죽는다).
    """
    for rel, token in (
        ("src/state.py", "user_role"),
        ("src/api/routes/query.py", 'user_role=current_user.get("role")'),
        ("src/orchestration/subagents.py", '"user_role": state.get("user_role")'),
        ("src/orchestration/deep_agent.py", '"user_role"'),
    ):
        assert token in pathlib.Path(rel).read_text(), f"{rel}에 role 전파가 없다"


def test_query_routes_propagate_role_at_every_entry():
    """라우트 진입점이 여럿이다 — 한 곳만 고치면 그 경로만 조사가 된다(비대칭)."""
    src = pathlib.Path("src/api/routes/query.py").read_text()
    assert src.count('user_role=current_user.get("role")') == 3


def test_initial_state_defaults_role_to_none():
    """명시 전달이 없으면 None — 즉 **차단**이다(기본값이 안전 방향)."""
    from src.state import create_initial_state

    assert create_initial_state(user_query="q")["user_role"] is None


# ──────────────────────────────────────────────
# 실행 경계 · 채팅·이벤트 대칭 (G5)
# ──────────────────────────────────────────────

def _calls_authz(rel: str) -> bool:
    """해당 모듈이 인가 함수를 **실제로 호출**하는지 AST로 본다(주석 오탐 방지)."""
    tree = ast.parse(pathlib.Path(rel).read_text())
    return any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "authorize_host_investigation"
        for n in ast.walk(tree)
    )


def test_both_entry_points_call_the_same_authz_module():
    """★ G5 — 채팅·이벤트가 **같은 인가 모듈**을 호출한다. 한쪽만 적용되는 비대칭 금지."""
    assert _calls_authz("src/nodes/fault_diagnosis.py"), "채팅 경로에 인가가 없다"
    assert _calls_authz(
        "noise_gate/application/nodes/investigation_trigger.py"
    ), "이벤트 경로에 인가가 없다"


def test_authz_is_evaluated_before_delegation_in_chat_path():
    """★ 판정은 **위임 직전**(실행 경계)이다 — planner/LLM 경로에서 막으면 우회가 생긴다.

    소스 순서로 확인한다: 인가 호출이 조사 클라이언트 생성보다 **앞**에 있어야 한다.
    """
    src = pathlib.Path("src/nodes/fault_diagnosis.py").read_text()
    assert src.index("authorize_host_investigation(") < src.index("_build_client(gate_cfg)")


@pytest.mark.asyncio
async def test_denied_chat_request_never_starts_investigation(monkeypatch):
    """★ 인가되지 않으면 조사가 **시작되지 않는다**(호출 0회)."""
    from src.nodes import fault_diagnosis as fd

    built = {"n": 0}

    def _spy(gate_cfg):
        built["n"] += 1
        return object()

    monkeypatch.setattr(fd, "_build_client", _spy)
    out = await fd.fault_diagnosis(
        {
            "user_query": "web-01 원인 분석",
            "user_role": "user",           # 관리자가 아니다
            "user_id": "u1",
            "parsed_requirements": {
                "filter_conditions": [{"field": "hostname", "value": "web-01"}]
            },
        },
        app_config=SimpleNamespace(
            noise_gate=SimpleNamespace(fault_diagnosis_enabled=True),
            host_authz=SimpleNamespace(mode="admin_only"),
            composite=SimpleNamespace(max_targets=10),
        ),
    )
    assert built["n"] == 0, "인가 거부인데 조사 클라이언트가 생성됐다"
    assert "관리자 권한" in out["final_response"]


@pytest.mark.asyncio
async def test_denied_chat_request_is_audited_and_counted(monkeypatch):
    """거부가 **감사와 지표에** 남는다 — 거버넌스 증거(W6-5)이자 경로 판정 재료(W6-4)다."""
    from src.nodes import fault_diagnosis as fd
    from src.observability import investigation_metrics as metrics

    metrics.reset()
    records: list[dict] = []

    async def _capture(**kwargs):
        records.append(kwargs)

    monkeypatch.setattr(fd, "log_investigation", _capture)
    monkeypatch.setattr(fd, "_build_client", lambda g: object())
    await fd.fault_diagnosis(
        {"user_query": "q", "user_role": "user", "user_id": "u1",
         "parsed_requirements": {"filter_conditions": [
             {"field": "hostname", "value": "web-01"}]}},
        app_config=SimpleNamespace(
            noise_gate=SimpleNamespace(fault_diagnosis_enabled=True),
            host_authz=SimpleNamespace(mode="admin_only"),
            composite=SimpleNamespace(max_targets=10),
        ),
    )
    assert records[0]["outcome"] == "denied"
    assert records[0]["authz"]["reason"] == DENY_ROLE_NOT_ALLOWED
    assert metrics.snapshot()["routing"]["denied_by_reason"][DENY_ROLE_NOT_ALLOWED] == 1
    metrics.reset()


def test_denial_messages_distinguish_config_error_from_policy():
    """"권한 없음" 하나로 뭉치면 **설정 오류**(미상 모드)와 정상 거부가 구분되지 않는다."""
    from src.nodes.fault_diagnosis import _DENY_MESSAGES

    assert _DENY_MESSAGES[DENY_UNKNOWN_MODE] != _DENY_MESSAGES[DENY_ROLE_NOT_ALLOWED]
    assert "HOST_AUTHZ_MODE" in _DENY_MESSAGES[DENY_UNKNOWN_MODE]
