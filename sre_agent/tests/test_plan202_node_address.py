"""plans/202 sre_agent 쪽 — 제니퍼 K8s 파드 알람의 노드 주소 연결(D-355 ②③④⑦).

연결 힌트 `meta.hints.polestar_link`의 `basis: node_address` linked · 파드 미확정
(`unresolved` + `pod`)을 소스 결정·근거 문장·사전 수집이 어떻게 쓰는지 본다.
녹화 픽스처·가짜 콜러블만 쓴다(실 MCP·LLM 호출 0).
"""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest
from sre_agent.application.evidence_prefetch import (
    TOOL_ALARMS,
    prefetch_and_correlate,
    scope_from_job,
)
from sre_agent.application.playbook_runner import (
    POLESTAR_LINK_UNRESOLVED,
    initial_scope,
    mapped_polestar_source,
    polestar_link,
    polestar_previous_link,
    resolve_polestar_source,
    run_playbook,
)
from sre_agent.domain.topology_neighbors import ORIGIN_LINK, Neighbor
from test_plan187_w2_runner import PLAYBOOKS, FakeCaller, _load

REF = "2026-10-12T10:15:00"
NODE_IP = "10.136.5.21"
MAP = {"b0": "polestar_b0"}


def _node(**kw) -> dict:
    return {
        "status": "linked",
        "db_id": "polestar_cm_gp",
        "server_name": "CMGP-NODE-07",
        "hostname": "was-pod-7f9c",
        "basis": "node_address",
        "host_address": NODE_IP,
        "at": "2026-10-29T09:00",
        **kw,
    }


def _pod(reason: str, **kw) -> dict:
    return {"status": "unresolved", "reason": reason, "pod": True, "host_address": NODE_IP, **kw}


def _latency_alarm(link: dict | None = None) -> dict:
    alarm = copy.deepcopy(_load("latency_sql")["alarm"])
    alarm["meta"]["hints"]["source_id"] = "b0"  # B0 제니퍼 알람
    if link is not None:
        alarm["meta"]["hints"]["polestar_link"] = link
    return alarm


def _was_latency():
    return next(p for p in PLAYBOOKS if p.id == "was.latency")


# ── 노드 주소 연결 — 소스·근거 ──────────────────────────────────────────────


def test_node_linked_overrides_configured_map_with_cm_db_and_auto_basis() -> None:
    alarm = _latency_alarm(_node())
    mapped, _ = mapped_polestar_source(alarm, MAP)
    assert mapped == "polestar_b0"  # 설정 매핑은 b0 — 노드는 공동존
    assert resolve_polestar_source(alarm, mapped) == ("polestar_cm_gp", None)
    scope = initial_scope(alarm, "polestar_cm_gp", None)
    assert scope.polestar_link == "linked" and scope.polestar_server == "CMGP-NODE-07"
    assert scope.polestar_basis == f"제니퍼 노드 주소 일치 — {NODE_IP} (알람 시점 · 자동)"


def test_node_linked_run_queries_node_server_and_notes_basis() -> None:
    fx = _load("latency_sql")
    caller = FakeCaller(fx, strict=False)
    alarm = _latency_alarm(_node())
    mapped, mapped_basis = mapped_polestar_source(alarm, MAP)
    run = run_playbook(alarm, _was_latency(), matched_by="t", polestar=caller, apm=caller,
                       polestar_source=mapped, polestar_source_basis=mapped_basis,
                       max_workers=1, for_each_max=1)
    basis = f"제니퍼 노드 주소 일치 — {NODE_IP} (알람 시점 · 자동)"
    assert run.scope["polestar_source"] == "polestar_cm_gp"
    assert run.scope["polestar_server"] == "CMGP-NODE-07"
    assert run.scope["polestar_basis"] == basis and basis in run.notes
    assert not any("설정 매핑으로 대체" in n for n in run.notes)
    pol = [a for t, a in caller.calls if t.startswith("polestar_")]
    assert pol and all(
        (a.get("server_name") or a.get("hostname")) == "CMGP-NODE-07" for a in pol
    )
    assert basis in (run.section("S4").reason or "")  # type: ignore[union-attr]


def test_node_linked_differs_from_saved_link_basis_shows_both() -> None:
    prev = {"db_id": "polestar_b0", "server_name": "B0-OLD-01"}
    alarm = _latency_alarm(_node(previous_link=prev))
    link = polestar_link(alarm)
    assert link is not None and "previous_link" not in link  # 중첩 값은 문자열로 바꾸지 않는다
    assert polestar_previous_link(alarm) == prev
    basis = initial_scope(alarm, "polestar_cm_gp", None).polestar_basis or ""
    assert basis.startswith(f"제니퍼 노드 주소 일치 — {NODE_IP}")
    assert "B0-OLD-01(polestar_b0)" in basis and "노드 주소 우선" in basis


def test_node_linked_with_unchecked_zone_names_it_in_basis() -> None:
    # D-355 부기 2 — 일부 존 실패 중 1대 일치 연결은 확인 못 한 소스를 근거에 적는다
    alarm = _latency_alarm(_node(unchecked_db_ids="polestar_b0"))
    basis = initial_scope(alarm, "polestar_cm_gp", None).polestar_basis or ""
    assert basis == (
        f"제니퍼 노드 주소 일치 — {NODE_IP} (알람 시점 · 자동) · "
        "일부 존 확인 못 함(polestar_b0) — 나머지 존에서 1대 일치"
    )


def test_node_linked_requires_db_id_and_server_name() -> None:
    for broken in (_node(db_id=""), _node(server_name=None)):
        alarm = _latency_alarm(broken)
        assert polestar_link(alarm) is None
        assert resolve_polestar_source(alarm, "polestar_b0") == ("polestar_b0", None)


def test_infra_db_ids_still_win_over_node_link() -> None:
    alarm = _latency_alarm(_node())
    alarm["meta"]["episode"] = {"episode_id": "e", "infra_db_ids": ["polestar_cm_yd"],
                                "members": []}
    assert resolve_polestar_source(alarm, "polestar_b0") == ("polestar_cm_yd", None)
    scope = initial_scope(alarm, "polestar_cm_yd", None)
    assert scope.polestar_link is None and scope.polestar_basis is None


# ── 파드 미확정 — 설정 매핑 대체 금지 · 사유 문장 ──────────────────────────────

POD_REASONS = [
    "node_not_registered", "node_ambiguous", "node_unchecked",
    "node_lookup_stale", "node_lookup_not_found", "node_lookup_disabled",
    "node_lookup_login_failed", "node_lookup_login_blocked", "node_lookup_error",
]


@pytest.mark.parametrize("reason", POD_REASONS)
def test_pod_unresolved_not_replaced_by_configured_map(reason: str) -> None:
    alarm = _latency_alarm(_pod(reason))
    source, why = resolve_polestar_source(alarm, "polestar_b0")
    assert source is None and why
    assert why.startswith(f"폴스타 서버 미확정 — K8s 파드(노드 {NODE_IP} — ")
    scope = initial_scope(alarm, None, why)
    assert scope.polestar_link == "unresolved"  # 기존 생략 자리 — 사유만 다르다


def test_pod_reasons_are_all_distinct() -> None:
    texts = [
        resolve_polestar_source(_latency_alarm(_pod(r)), "polestar_b0")[1] for r in POD_REASONS
    ]
    assert len(set(texts)) == len(POD_REASONS)
    joined = " ".join(t or "" for t in texts)
    for word in ("폴스타 미등록", "여러 서버에 일치", "일부 존 확인 못 함", "로그인 실패",
                 "형태 오류 또는 시간 초과"):
        assert word in joined


def test_pod_reason_unknown_and_missing_host_still_explained() -> None:
    why = resolve_polestar_source(
        _latency_alarm({"status": "unresolved", "pod": True, "reason": "node_lookup_weird"}),
        "polestar_b0",
    )[1]
    assert why == "폴스타 서버 미확정 — K8s 파드(노드 — 노드 주소 조회 실패(weird))"
    why = resolve_polestar_source(
        _latency_alarm({"status": "unresolved", "pod": True}), "polestar_b0"
    )[1]
    assert why == "폴스타 서버 미확정 — K8s 파드(노드 — 노드 미확정)"


@pytest.mark.parametrize("pod", [True, "True", "true"])
def test_pod_flag_bool_and_stringified(pod) -> None:
    alarm = _latency_alarm(_pod("node_ambiguous", pod=pod))
    link = polestar_link(alarm)
    assert link is not None and link["pod"] == "true"
    assert resolve_polestar_source(alarm, "polestar_b0")[0] is None


@pytest.mark.parametrize("pod", [False, "False", ""])
def test_unresolved_without_pod_keeps_configured_map(pod) -> None:
    link = {"status": "unresolved"} if pod == "" else {"status": "unresolved", "pod": pod}
    alarm = _latency_alarm(link)
    assert "pod" not in (polestar_link(alarm) or {})
    assert resolve_polestar_source(alarm, "polestar_b0") == ("polestar_b0", None)
    assert resolve_polestar_source(alarm) == (None, POLESTAR_LINK_UNRESOLVED)


def test_pod_unresolved_run_skips_polestar_with_pod_sentence() -> None:
    """파드 미확정은 기존 「선택 대기」와 같은 생략 자리 — 사유만 파드 문장(머리 중복 없음)."""
    fx = _load("latency_sql")
    caller = FakeCaller(fx)
    alarm = _latency_alarm(_pod("node_not_registered"))
    mapped, mapped_basis = mapped_polestar_source(alarm, MAP)
    run = run_playbook(alarm, _was_latency(), matched_by="t", polestar=caller, apm=caller,
                       polestar_source=mapped, polestar_source_basis=mapped_basis,
                       max_workers=1, for_each_max=1)
    sentence = f"폴스타 서버 미확정 — K8s 파드(노드 {NODE_IP} — 노드 IP 폴스타 미등록)"
    assert run.scope["polestar_source"] is None
    assert run.scope["polestar_link"] == "unresolved"
    assert "polestar_basis" not in run.scope
    assert sentence in run.notes
    assert not [t for t, _ in caller.calls if t.startswith("polestar_")]
    for sid in ("S4", "S6"):
        s = run.section(sid)
        assert s is not None and s.status == "skipped" and s.reason == sentence and not s.missing
    s5, s7 = run.section("S5"), run.section("S7")
    assert s5 is not None and f"폴스타: {sentence}" in (s5.reason or "")
    assert s7 is not None and s7.data["axes"]["polestar"]["status"] == "skipped"
    texts = [*run.notes, *(s.reason or "" for s in run.sections)]
    assert not any("폴스타 서버 미확정 — 폴스타 서버 미확정" in t for t in texts)
    assert not any("폴스타 서버 선택 대기" in t for t in texts)


# ── 기존 근거 문장 회귀 ─────────────────────────────────────────────────────


@pytest.mark.parametrize(("basis", "text"), [
    ("previous_confirmed", "이전 확정 연결 — kim·2026-10-27T10:00"),
    ("user_selected", "사용자 선택으로 연결 — kim·2026-10-27T10:00"),
    ("other", "확정 연결 — kim·2026-10-27T10:00"),
])
def test_existing_basis_sentences_unchanged(basis: str, text: str) -> None:
    link = {"status": "linked", "db_id": "polestar", "server_name": "PS-REG-01",
            "basis": basis, "by": "kim", "at": "2026-10-27T10:00"}
    assert initial_scope(_latency_alarm(link), "polestar", None).polestar_basis == text


# ── 사전 수집(evidence_prefetch) ────────────────────────────────────────────


def _apm_job(link: dict | None, *, episode_db: str | None = None) -> SimpleNamespace:
    meta: dict = {"hints": {"solution": "apm", "source_id": "b0"}}
    if link is not None:
        meta["hints"]["polestar_link"] = link
    if episode_db:
        meta["episode"] = {"infra_db_ids": [episode_db]}
    payload = {
        "event": {"dbId": "jennifer", "serverName": "was-pod-7f9c",
                  "resourceType": "apm.Instance"},
        "meta": meta,
    }
    return SimpleNamespace(reference_time=REF, lookback_minutes=60, payload=payload)


def test_prefetch_opens_polestar_axis_with_node_link() -> None:
    scope = scope_from_job(_apm_job(_node()))
    assert scope is not None and not scope.skip_infra
    assert (scope.source, scope.server_name) == ("polestar_cm_gp", "CMGP-NODE-07")
    calls: list = []

    def _mcp(batch):
        calls.extend(batch)
        return ['{"rows": []}'] * len(batch)

    prefetch_and_correlate(scope, _mcp)
    assert calls and calls[0] == (TOOL_ALARMS, {
        "source": "polestar_cm_gp", "server_name": "CMGP-NODE-07",
        "reference_time": REF, "lookback_minutes": 60,
    })


def test_prefetch_infra_db_ids_first_then_node_link() -> None:
    scope = scope_from_job(_apm_job(_node(), episode_db="polestar_cm_yd"), cross_source=True)
    assert scope is not None and (scope.source, scope.server_name) == (
        "polestar_cm_yd", "was-pod-7f9c"
    )
    # 사건 소스 판독이 꺼져 있으면(cross_source off) 노드 연결을 쓴다
    scope = scope_from_job(_apm_job(_node(), episode_db="polestar_cm_yd"))
    assert scope is not None and scope.source == "polestar_cm_gp"


@pytest.mark.parametrize("link", [
    _pod("node_not_registered"),
    _pod("node_lookup_error"),
    {"status": "linked", "db_id": "polestar_cm_gp", "server_name": "H",
     "basis": "previous_confirmed"},  # 이전 확정 연결은 대상이 아니라 이웃 — 현행
    None,
])
def test_prefetch_does_not_open_axis_without_node_link(link) -> None:
    scope = scope_from_job(_apm_job(link))
    assert scope is not None and scope.skip_infra and scope.source == ""


def test_prefetch_previous_link_becomes_neighbor_candidate() -> None:
    prev = {"db_id": "polestar_b0", "server_name": "B0-OLD-01"}
    scope = scope_from_job(_apm_job(_node(previous_link=prev)), neighbors_max=5)
    assert scope is not None and scope.source == "polestar_cm_gp"
    assert scope.link_neighbor == Neighbor("B0-OLD-01", "polestar_b0", ORIGIN_LINK)
    # previous_link 없으면 노드 연결 자체는 이웃이 아니다
    assert scope_from_job(_apm_job(_node()), neighbors_max=5).link_neighbor is None
    # 이웃 끄면(0) 종전대로 없음
    assert scope_from_job(_apm_job(_node(previous_link=prev))).link_neighbor is None


def test_previous_link_stringified_json_is_parsed() -> None:
    alarm = _latency_alarm(
        _node(previous_link='{"db_id": "polestar_b0", "server_name": "B0-OLD-01"}')
    )
    assert polestar_previous_link(alarm) == {"db_id": "polestar_b0", "server_name": "B0-OLD-01"}
    assert polestar_previous_link(_latency_alarm(_node(previous_link="{broken"))) is None
