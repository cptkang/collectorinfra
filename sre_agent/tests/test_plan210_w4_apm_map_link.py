"""plans/210 W4 sre_agent 쪽 — APM↔SMS 연계 힌트(`meta.hints.polestar_link` 가산 키 · §3.7) 소비.

근거 문장(`basis: apm_map` · `map_origin`) · 관리자 「연계 없음」(absent) · 연계 미확정
(`no_config_fallback` — 설정 매핑 대체 금지 · `node_address_status` 사유) · 연계 대상 여럿
(`targets` — 지표 단계·사전 수집을 대상마다) · `kind != "sms"` 무시 · 받은 등록명 그대로 사용을
본다.
녹화 픽스처·가짜 콜러블만 쓴다(실 MCP·LLM 호출 0).
"""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest
from sre_agent.application.evidence_prefetch import (
    TOOL_ALARMS,
    TOOL_METRIC,
    prefetch_and_correlate,
    scope_from_job,
)
from sre_agent.application.investigation_guidance import correlation_note
from sre_agent.application.playbook_runner import (
    _POLESTAR_REASON_MARKS,
    POLESTAR_APM_MAP_REASONS,
    POLESTAR_LINK_ABSENT_APM_MAP,
    POLESTAR_LINK_TARGETS_MAX,
    POLESTAR_LINK_UNRESOLVED,
    initial_scope,
    mapped_polestar_source,
    polestar_link,
    polestar_link_targets,
    resolve_polestar_source,
    run_playbook,
)
from test_plan187_w2_runner import PLAYBOOKS, FakeCaller, _load

MAP = {"common": "polestar_b0"}  # 설정 매핑 — 연계가 이겨야 하는 다른 존 소스
MANUAL = "APM↔SMS 연계 — 관리자 등록"
AUTO = "APM↔SMS 연계 — 자동(노드 주소 · VM 일치 · ITAM 업무 추정)"


def _map(**kw) -> dict:
    """연계 매핑 hit linked(대표 = 픽스처의 폴스타 서버 wasdemo01)."""
    return {
        "status": "linked",
        "db_id": "polestar",
        "server_name": "wasdemo01",
        "hostname": "wasdemo01",
        "basis": "apm_map",
        "map_origin": "manual",
        "map_scope": "instance",
        "map_id": 17,
        "map_basis": "manual",
        "resource_id": "R-100",
        "evidence_text": "관리자 지정",
        "at": "2026-11-03T09:00",
        **kw,
    }


def _alarm(link: dict | None) -> dict:
    alarm = copy.deepcopy(_load("latency_sql")["alarm"])
    if link is not None:
        alarm["meta"]["hints"]["polestar_link"] = link
    return alarm


def _was_latency():
    return next(p for p in PLAYBOOKS if p.id == "was.latency")


def _run(alarm: dict, caller):
    mapped, mapped_basis = mapped_polestar_source(alarm, MAP)
    return run_playbook(alarm, _was_latency(), matched_by="t", polestar=caller, apm=caller,
                        polestar_source=mapped, polestar_source_basis=mapped_basis,
                        max_workers=1, for_each_max=1)


# ── 근거 문장 ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("origin", "text"), [
    ("manual", MANUAL),
    ("auto", AUTO),
    ("weird", "APM↔SMS 연계 — 미상"),
    (None, "APM↔SMS 연계 — 미상"),
])
def test_apm_map_basis_by_origin(origin, text) -> None:
    alarm = _alarm(_map(map_origin=origin))
    assert resolve_polestar_source(alarm, "polestar_b0") == ("polestar", None)  # 설정 매핑을 이긴다
    scope = initial_scope(alarm, "polestar", None)
    assert scope.polestar_link == "linked"
    assert scope.polestar_basis == text
    assert scope.polestar_targets == []  # 단일 대상
    assert "polestar_targets" not in scope.to_dict()


def test_apm_map_basis_prefix_is_a_reason_mark() -> None:
    """S0 사유 갱신(`_refresh_polestar_reason`)이 연계 근거 토막을 찾는다(접두 목록 대칭)."""
    assert "APM↔SMS 연계 — " in _POLESTAR_REASON_MARKS


def test_apm_map_linked_run_uses_given_server_without_rescoping() -> None:
    """받은 등록명·resource_id를 그대로 쓴다 — S0가 제니퍼 호스트명으로 덮지 않는다(D-360 ④)."""
    fx = _load("latency_sql")
    caller = FakeCaller(fx, strict=False)
    alarm = _alarm(_map(server_name="SMS-REG-01", map_origin="auto"))
    run = _run(alarm, caller)
    assert run.scope["polestar_source"] == "polestar"
    assert run.scope["polestar_server"] == "SMS-REG-01"
    assert run.scope["polestar_basis"] == AUTO and AUTO in run.notes
    assert not any("설정 매핑으로 대체" in n for n in run.notes)
    texts = [*run.notes, run.scope["polestar_basis"]]
    assert not any("제니퍼 호스트명으로 조회" in n for n in texts)
    pol = [a for t, a in caller.calls if t.startswith("polestar_")]
    assert pol and all((a.get("server_name") or a.get("hostname")) == "SMS-REG-01" for a in pol)
    s0 = run.section("S0")
    assert s0 is not None and s0.data["scope"]["polestar_server"] == "SMS-REG-01"


# ── 관리자 「연계 없음」(absent) ────────────────────────────────────────────


def test_apm_map_absent_skips_polestar_and_ignores_config_map() -> None:
    fx = _load("latency_sql")
    caller = FakeCaller(fx, strict=False)
    alarm = _alarm({"status": "absent", "basis": "apm_map"})
    assert resolve_polestar_source(alarm, "polestar_b0") == (None, POLESTAR_LINK_ABSENT_APM_MAP)
    run = _run(alarm, caller)
    assert run.scope["polestar_source"] is None and run.scope["polestar_link"] == "absent"
    assert not [t for t, _ in caller.calls if t.startswith("polestar_")]
    s4 = run.section("S4")
    assert s4 is not None and s4.status == "skipped" and not s4.missing
    assert POLESTAR_LINK_ABSENT_APM_MAP in (s4.reason or "")


# ── 연계 미확정(no_config_fallback) ─────────────────────────────────────────


def _unresolved(reason: str, **kw) -> dict:
    return {"status": "unresolved", "reason": reason, "no_config_fallback": True, **kw}


@pytest.mark.parametrize("reason", sorted(POLESTAR_APM_MAP_REASONS))
def test_no_config_fallback_blocks_config_map(reason: str) -> None:
    alarm = _alarm(_unresolved(reason))
    source, why = resolve_polestar_source(alarm, "polestar_b0")
    assert source is None
    assert why == f"폴스타 서버 미확정 — APM↔SMS 연계 미확정({POLESTAR_APM_MAP_REASONS[reason]})"
    assert initial_scope(alarm, None, why).polestar_link == "unresolved"  # 생략 자리


def test_no_config_fallback_reasons_distinct_and_unknown_explained() -> None:
    texts = {
        resolve_polestar_source(_alarm(_unresolved(r)), "x")[1] for r in POLESTAR_APM_MAP_REASONS
    }
    assert len(texts) == len(POLESTAR_APM_MAP_REASONS)
    assert resolve_polestar_source(_alarm(_unresolved("apm_map_weird")), "x")[1] == (
        "폴스타 서버 미확정 — APM↔SMS 연계 미확정(사유 미상(apm_map_weird))"
    )
    assert resolve_polestar_source(_alarm(_unresolved("")), "x")[1] == (
        "폴스타 서버 미확정 — APM↔SMS 연계 미확정(사유 미상)"
    )


@pytest.mark.parametrize(("status", "text"), [
    ("disabled", "노드 주소 조회 실패(제니퍼 웹 조회 계정 미설정)"),
    ("login_failed", "노드 주소 조회 실패(제니퍼 웹 로그인 실패)"),
    ("login_blocked", "노드 주소 조회 실패(제니퍼 웹 로그인 연속 실패로 일시 정지)"),
    ("error", "노드 주소 조회 실패(응답 형태 오류 또는 시간 초과)"),
    ("missing", "제니퍼 노드 주소 조회 비활성(게이트웨이 노드 주소 조회 꺼짐)"),
    ("weird", "노드 주소 조회 실패(weird)"),
])
def test_node_address_status_sentence_appended(status: str, text: str) -> None:
    alarm = _alarm(_unresolved("apm_map_pending", node_address_status=status))
    why = resolve_polestar_source(alarm, "polestar_b0")[1]
    assert why == (
        f"폴스타 서버 미확정 — APM↔SMS 연계 미확정({POLESTAR_APM_MAP_REASONS['apm_map_pending']})"
        f" · {text}"
    )


@pytest.mark.parametrize("flag", [True, "True", "true", "1"])
def test_no_config_fallback_bool_and_stringified(flag) -> None:
    alarm = _alarm(_unresolved("apm_map_failed", no_config_fallback=flag))
    assert resolve_polestar_source(alarm, "polestar_b0")[0] is None


@pytest.mark.parametrize("flag", [False, "False", None])
def test_unresolved_without_flag_keeps_config_map(flag) -> None:
    link = _unresolved("apm_map_failed", no_config_fallback=flag)
    if flag is None:
        link.pop("no_config_fallback")
    alarm = _alarm(link)
    assert resolve_polestar_source(alarm, "polestar_b0") == ("polestar_b0", None)
    assert resolve_polestar_source(alarm) == (None, POLESTAR_LINK_UNRESOLVED)


def test_pod_with_apm_map_reason_uses_pod_link_sentence() -> None:
    """파드의 연계 미확정은 「K8s 파드」 표지 — 화면이 선택 UI를 열지 않는다(D-355 ④)."""
    alarm = _alarm(_unresolved("apm_map_ambiguous", pod=True, host_address="10.1.1.1",
                               node_address_status="login_blocked"))
    why = resolve_polestar_source(alarm, "polestar_b0")[1] or ""
    assert why.startswith("폴스타 서버 미확정 — K8s 파드 · APM↔SMS 연계 미확정(후보가 너무 넓어")
    assert why.endswith("노드 주소 조회 실패(제니퍼 웹 로그인 연속 실패로 일시 정지)")


def test_pod_reason_gets_node_status_unless_already_lookup() -> None:
    pod = {"status": "unresolved", "pod": True, "host_address": "10.1.1.1"}
    why = resolve_polestar_source(
        _alarm({**pod, "reason": "node_not_registered", "node_address_status": "missing"}), "x"
    )[1]
    assert why == (
        "폴스타 서버 미확정 — K8s 파드(노드 10.1.1.1 — 노드 IP 폴스타 미등록)"
        " · 제니퍼 노드 주소 조회 비활성(게이트웨이 노드 주소 조회 꺼짐)"
    )
    why = resolve_polestar_source(
        _alarm({**pod, "reason": "node_lookup_error", "node_address_status": "error"}), "x"
    )[1]
    assert why == (
        "폴스타 서버 미확정 — K8s 파드(노드 10.1.1.1 — "
        "노드 주소 조회 실패(응답 형태 오류 또는 시간 초과))"
    )


def test_no_config_fallback_run_skips_polestar_without_config_map() -> None:
    fx = _load("latency_sql")
    caller = FakeCaller(fx, strict=False)
    alarm = _alarm(_unresolved("apm_map_no_inventory", node_address_status="disabled"))
    run = _run(alarm, caller)
    sentence = resolve_polestar_source(alarm)[1]
    # 자동 매핑 off면 스캔이 거부된다 — 「지금 스캔」만 안내하지 않는다
    assert "관리자 수동 매핑 또는 자동 매핑을 켠 뒤 「지금 스캔」" in sentence
    assert run.scope["polestar_source"] is None and run.scope["polestar_link"] == "unresolved"
    assert sentence in run.notes
    assert not any("설정 매핑으로 대체" in n for n in run.notes)
    assert not [t for t, _ in caller.calls if t.startswith("polestar_")]
    for sid in ("S4", "S6"):
        s = run.section(sid)
        assert s is not None and s.status == "skipped" and s.reason == sentence and not s.missing


# ── 연계 대상 여럿 — 해석 ───────────────────────────────────────────────────


def _t(server: str, db: str = "polestar", **kw) -> dict:
    return {"db_id": db, "server_name": server, "hostname": server.lower(),
            "resource_id": f"R-{server}", **kw}


def test_targets_parse_kind_filter_dedupe_and_head_first() -> None:
    link = _map(targets=[
        _t("wasdemo01"),  # 대표와 같다 — 합친다
        _t("SMS-B", kind="sms"),
        _t("DPM-1", kind="dpm"),  # DPM 예약(§3.9) — 무시
        _t("SMS-C", kind="SMS"),
        {"db_id": "polestar", "server_name": ""},  # 무효
        "garbage",
        _t("SMS-B"),  # 중복
    ])
    targets = polestar_link_targets(_alarm(link))
    assert [t["server_name"] for t in targets] == ["wasdemo01", "SMS-B", "SMS-C"]
    assert targets[0]["resource_id"] == "R-100"  # 대표는 최상위 값
    assert targets[1] == {"db_id": "polestar", "server_name": "SMS-B", "hostname": "sms-b",
                          "resource_id": "R-SMS-B"}
    # 평문 칸으로 옮기지 않는다
    assert "targets" not in (polestar_link(_alarm(link)) or {})


def test_targets_cap_json_string_and_single_or_unlinked_empty() -> None:
    many = [_t(f"SMS-{i:02d}") for i in range(15)]
    assert len(polestar_link_targets(_alarm(_map(targets=many)))) == POLESTAR_LINK_TARGETS_MAX
    as_json = _map(targets=json.dumps([_t("wasdemo01"), _t("SMS-B")]))
    assert [t["server_name"] for t in polestar_link_targets(_alarm(as_json))] == [
        "wasdemo01", "SMS-B"
    ]
    assert polestar_link_targets(_alarm(_map(targets="{broken"))) == []
    assert polestar_link_targets(_alarm(_map(targets=[_t("wasdemo01")]))) == []
    assert polestar_link_targets(_alarm(_map(targets=[_t("D", kind="dpm")]))) == []
    assert polestar_link_targets(_alarm(_map())) == []
    assert polestar_link_targets(_alarm(_unresolved("apm_map_pending", targets=[_t("X")]))) == []


# ── 연계 대상 여럿 — 플레이북 지표 단계 ─────────────────────────────────────


class _PerServer(FakeCaller):
    """지표 응답을 서버별로 바꾼다 — `hot`은 cpu 급등, `down`은 전부 오류."""

    def __init__(self, fx: dict, *, hot: str, down: str) -> None:
        super().__init__(fx, strict=False)
        self.hot, self.down = hot, down

    def lookup(self, tool: str, args: dict) -> dict | None:
        server = args.get("server_name")
        if tool == TOOL_METRIC and server in (self.hot, self.down):
            if server == self.down:
                return {"error": "timeout"}
            resp = copy.deepcopy(super().lookup(tool, {**args, "server_name": "wasdemo01"}))
            if args.get("kind") == "cpu":
                inc = resp["window"]["stat_date_incident_from"]
                for row in resp["rows"]:
                    if row["stat_date"] >= inc:
                        row["avg_val"] = 97.0
            return resp
        return super().lookup(tool, args)


def test_three_targets_trend_per_target_and_names_hot_node() -> None:
    fx = _load("latency_sql")
    caller = _PerServer(fx, hot="SMS-B", down="SMS-C")
    alarm = _alarm(_map(targets=[_t("wasdemo01"), _t("SMS-B"), _t("SMS-C"), _t("D", kind="dpm")]))
    run = _run(alarm, caller)
    names = [t["server_name"] for t in run.scope["polestar_targets"]]
    assert names == ["wasdemo01", "SMS-B", "SMS-C"]
    assert "연계 대상 3대(wasdemo01·SMS-B·SMS-C)" in run.scope["polestar_basis"]
    metric = [a for t, a in caller.calls if t == TOOL_METRIC]
    by_server = {s: [a["kind"] for a in metric if a["server_name"] == s] for s in
                 ("wasdemo01", "SMS-B", "SMS-C")}
    kinds = by_server["wasdemo01"]
    assert kinds and all(k == kinds for k in by_server.values())  # 대상마다 같은 지표
    assert len(metric) == 3 * len(kinds)  # 대상 3 × 지표 종류
    assert not any(a.get("server_name") == "D" for _, a in caller.calls)
    # 그 밖의 폴스타 단계는 첫 대상만
    others = [a for t, a in caller.calls if t.startswith("polestar_") and t != TOOL_METRIC]
    assert others
    assert all((a.get("server_name") or a.get("hostname")) == "wasdemo01" for a in others)
    s4 = run.section("S4")
    assert s4 is not None and s4.status == "ok" and s4.verdict == "degraded"
    assert s4.data["server_name"] == "SMS-B"  # 판독 재료 = 이상 대상
    assert s4.data["anomalous"] == ["cpu"]
    summary = {t["server_name"]: t for t in s4.data["polestar_targets"]}
    assert summary["SMS-B"]["verdict"] == "degraded" and summary["SMS-B"]["anomalous"] == ["cpu"]
    assert summary["wasdemo01"]["verdict"] == "normal"
    assert summary["SMS-C"]["status"] == "missing"
    reason = s4.reason or ""
    assert reason.startswith("대상별 지표(3대): ")
    assert "SMS-B(polestar) 이상(cpu)" in reason
    assert "wasdemo01(polestar) 이상 없음" in reason
    assert "SMS-C(polestar) 결손(" in reason and "timeout" in reason
    assert s4.table is not None and s4.table["columns"][0] == "target"
    assert {r[0] for r in s4.table["rows"]} == {"wasdemo01", "SMS-B"}


def test_targets_all_normal_but_one_missing_is_undecided() -> None:
    fx = _load("latency_sql")
    caller = _PerServer(fx, hot="nobody", down="SMS-C")
    run = _run(_alarm(_map(targets=[_t("wasdemo01"), _t("SMS-C")])), caller)
    s4 = run.section("S4")
    assert s4 is not None and s4.status == "ok" and s4.verdict is None  # 결손 섞임 = 판단 보류
    assert s4.data["server_name"] == "wasdemo01"


def test_single_target_run_is_unchanged_shape() -> None:
    """`targets` 없는 연계 — S4는 종전 단일 대상 모양(대상 칸·요약 키 없음)."""
    fx = _load("latency_sql")
    run = _run(_alarm(_map()), FakeCaller(fx, strict=False))
    s4 = run.section("S4")
    assert s4 is not None and s4.status == "ok"
    assert "polestar_targets" not in s4.data and s4.table["columns"][0] == "kind"  # type: ignore[index]
    assert "polestar_targets" not in run.scope


# ── 연계 대상 여럿 — 사전 수집 ──────────────────────────────────────────────

REF = "2026-11-03T10:15:00"


def _apm_job(link: dict | None) -> SimpleNamespace:
    meta: dict = {"hints": {"solution": "apm", "source_id": "common"}}
    if link is not None:
        meta["hints"]["polestar_link"] = link
    payload = {"event": {"dbId": "jennifer", "serverName": "was-pod-7f9c",
                         "resourceType": "apm.Instance"}, "meta": meta}
    return SimpleNamespace(reference_time=REF, lookback_minutes=60, payload=payload)


def _metric(server_hot: bool) -> dict:
    base = [{"stat_date": f"202611{d:02d}{h:02d}", "avg_val": 20.0}
            for d in (2, 3) for h in range(24) if (d, h) < (3, 9)]
    inc = [{"stat_date": "2026110309", "avg_val": 95.0 if server_hot else 21.0},
           {"stat_date": "2026110310", "avg_val": 96.0 if server_hot else 20.0}]
    return {"rows": base + inc, "window": {"stat_date_incident_from": "2026110309"}}


def test_prefetch_apm_map_opens_axis_and_collects_each_target() -> None:
    link = _map(server_name="SMS-A", targets=[_t("SMS-A"), _t("SMS-B", db="polestar_cm_gp"),
                                               _t("SMS-C"), _t("D", kind="dpm")])
    scope = scope_from_job(_apm_job(link))
    assert scope is not None and not scope.skip_infra
    assert (scope.source, scope.server_name) == ("polestar", "SMS-A")
    assert scope.extra_targets == (("polestar_cm_gp", "SMS-B"), ("polestar", "SMS-C"))
    batches: list[list] = []

    def _mcp(batch):
        batches.append(list(batch))
        out = []
        for tool, args in batch:
            if args["server_name"] == "SMS-C":
                out.append(json.dumps({"error": "timeout"}))
            elif tool == TOOL_METRIC:
                hot = args["server_name"] == "SMS-B" and args["kind"] == "cpu"
                out.append(json.dumps(_metric(hot)))
            else:
                out.append(json.dumps({"rows": []}))
        return out

    result = prefetch_and_correlate(scope, _mcp).to_dict()
    assert len(batches) == 2  # 대표 1 + 추가 대상 1(배치 1회)
    assert {a["server_name"] for _, a in batches[0]} == {"SMS-A"}
    assert [(t, a["source"], a["server_name"]) for t, a in batches[1] if t == TOOL_ALARMS] == [
        (TOOL_ALARMS, "polestar_cm_gp", "SMS-B"), (TOOL_ALARMS, "polestar", "SMS-C"),
    ]
    rows = {r["server_name"]: r for r in result["targets"]}
    assert list(rows) == ["SMS-A", "SMS-B", "SMS-C"]
    assert rows["SMS-B"]["anomalies"] == ["cpu"] and rows["SMS-A"]["anomalies"] == []
    assert rows["SMS-C"]["anomalies"] == [] and any("결손" in n for n in rows["SMS-C"]["notes"])
    note = correlation_note(result) or ""
    assert "연계 대상 3대" in note and "이상이 있는 대상 서버를 이름으로" in note
    assert "· SMS-B(polestar_cm_gp): 이상 지표 cpu" in note
    assert "· SMS-A(polestar): 이상 지표 없음" in note


def test_prefetch_extra_targets_budget_exhausted_keeps_primary() -> None:
    link = _map(server_name="SMS-A", targets=[_t("SMS-A"), _t("SMS-B")])
    scope = scope_from_job(_apm_job(link))
    assert scope is not None
    calls: list = []

    def _mcp(batch):
        calls.extend(batch)
        return [json.dumps({"rows": []})] * len(batch)

    result = prefetch_and_correlate(scope, _mcp, neighbor_budget_s=0).to_dict()
    assert {a["server_name"] for _, a in calls} == {"SMS-A"}  # 추가 대상은 부르지 않았다
    rows = {r["server_name"]: r for r in result["targets"]}
    assert any("타임박스 소진" in n for n in rows["SMS-B"]["notes"])


def test_prefetch_without_targets_has_no_targets_key() -> None:
    scope = scope_from_job(_apm_job(_map(server_name="SMS-A")))
    assert scope is not None and scope.extra_targets == ()
    result = prefetch_and_correlate(scope, lambda b: [json.dumps({"rows": []})] * len(b)).to_dict()
    assert "targets" not in result
    assert "연계 대상" not in (correlation_note(result) or "")


@pytest.mark.parametrize("link", [
    {"status": "absent", "basis": "apm_map"},
    _unresolved("apm_map_pending"),
])
def test_prefetch_does_not_open_axis_without_apm_map_link(link) -> None:
    scope = scope_from_job(_apm_job(link))
    assert scope is not None and scope.skip_infra and scope.source == ""
