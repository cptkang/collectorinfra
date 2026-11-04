"""plans/209 W2 (D-360 ③④) — 질문 식별자 라벨 · 폴스타 식별자 지침 · S0 뒤 폴스타 스코프 재작성."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sre_agent.application.investigation_guidance import (
    POLESTAR_IDENTIFIER_NOTE,
    build_guidance,
    filter_tool_mentions,
)
from sre_agent.application.playbook_runner import compute_windows
from sre_agent.application.playbook_steps import (
    POLESTAR_JENNIFER_HOST_BASIS,
    RunScope,
    StepContext,
    _rescoped_polestar_server,
)
from sre_agent.domain.playbook import WindowSpec
from sre_agent.interface.mcp_service import _job_to_question
from sre_agent.settings import AgentSettings

_HEAD = POLESTAR_IDENTIFIER_NOTE.split("\n", 1)[0]


def _job(payload: Any = None, **kw: Any) -> SimpleNamespace:
    base = {"kind": "alarm", "question": None, "payload": payload, "reference_time": None,
            "lookback_minutes": None}
    return SimpleNamespace(**{**base, **kw})


def _polestar(server: str | None = None, host: str | None = None, db: str | None = "polestar_cm_gp") -> dict:
    event = {"resourceType": "server.Cpu", "alarmName": "CPU Utilization"}
    for k, v in (("serverName", server), ("hostname", host), ("dbId", db)):
        if v is not None:
            event[k] = v
    return {"event": event}


def _apm(server: str, host: str) -> dict:
    return {"event": {"resourceType": "apm.Instance", "alarmName": "TX_BAD_RESPONSE",
                      "serverName": server, "hostname": host, "dbId": "cm_prd"},
            "meta": {"hints": {"solution": "apm"}}}


def _settings() -> AgentSettings:
    return AgentSettings(_env_file=None, model="test/model", api_key=None, gemini_api_key=None)


# ── 질문 라벨 ───────────────────────────────────────────────────────────────


def test_question_labels_registered_name_and_hostname() -> None:
    q = _job_to_question(_job(_polestar("cocm-ngcpkd05", "worker1")))
    assert q.startswith("cocm-ngcpkd05 장애 원인을")
    assert q.endswith(" 대상 서버: 폴스타 등록명 cocm-ngcpkd05 · OS 호스트명 worker1 · 폴스타 source polestar_cm_gp.")


def test_question_omits_hostname_equal_to_registered_name() -> None:
    q = _job_to_question(_job(_polestar("SAE0011", "SAE0011")))
    assert q.endswith(" 대상 서버: 폴스타 등록명 SAE0011 · 폴스타 source polestar_cm_gp.")
    assert "OS 호스트명" not in q


def test_question_omits_missing_values() -> None:
    q = _job_to_question(_job(_polestar(None, "worker1", db=None)))
    assert q.startswith("worker1 장애 원인을") and q.endswith(" 대상 서버: OS 호스트명 worker1.")
    assert "폴스타 등록명" not in q and "폴스타 source" not in q
    bare = _job_to_question(_job({"event": {"alarmName": "x"}}))
    assert bare.startswith("대상 서버 장애 원인을") and "대상 서버:" not in bare


@pytest.mark.parametrize(("server", "host", "label"), [
    ("wasdemo01", "wasdemo01", " 대상 서버: OS 호스트명 wasdemo01."),  # 제니퍼 serverName = hostname
    ("PS-REG-01", "wasdemo01", " 대상 서버: 폴스타 등록명 PS-REG-01 · OS 호스트명 wasdemo01."),
])
def test_question_apm_trigger_labels(server: str, host: str, label: str) -> None:
    q = _job_to_question(_job(_apm(server, host)))
    assert q.endswith(label)
    assert "cm_prd" not in q  # 제니퍼 dbId는 폴스타 source가 아니다


def test_pull_question_unchanged() -> None:
    job = _job(None, kind="diagnosis", question="김포 서버 CPU 왜 높아?")
    assert _job_to_question(job) == "김포 서버 CPU 왜 높아?"


# ── 조사 지침 블록 ──────────────────────────────────────────────────────────


def test_guidance_has_identifier_note_exactly_once() -> None:
    g = build_guidance(_settings(), _job(_polestar("cocm-ngcpkd05", "worker1"))) or ""
    assert g.count(POLESTAR_IDENTIFIER_NOTE) == 1


def test_guidance_without_identifier_has_no_note() -> None:
    for payload in (None, {"event": {"alarmName": "x"}}, _polestar(None, "  ")):
        g = build_guidance(_settings(), _job(payload)) or ""
        assert _HEAD not in g


def test_guidance_note_follows_toolset_scope() -> None:
    job = _job(_polestar("cocm-ngcpkd05", "worker1"))
    inside = build_guidance(_settings(), job, toolset_scope=("host", ("polestar", "prometheus"))) or ""
    outside = build_guidance(_settings(), job, toolset_scope=("was", ("apm",))) or ""
    assert inside.count(_HEAD) == 1 and _HEAD not in outside


def test_note_tool_filter_drops_whole_lines_only() -> None:
    """도구 이름 필터가 없는 도구의 줄만 지운다 — 남은 줄의 문장이 잘려 이어지지 않는다."""
    kept = filter_tool_mentions(POLESTAR_IDENTIFIER_NOTE, {"polestar_metric_trend"})
    assert "polestar_os_config" not in kept and "polestar_process_snapshot" not in kept
    assert "- polestar_* 도구의 server_name 인자 = 폴스타 등록명 · source 인자" in kept
    assert len(kept.split("\n")) == len(POLESTAR_IDENTIFIER_NOTE.split("\n")) - 2


# ── S0 뒤 폴스타 스코프 ─────────────────────────────────────────────────────


def _ctx(event: dict, **scope: Any) -> StepContext:
    return StepContext(
        payload={"event": event}, windows=compute_windows("2026-10-27T09:23:00", WindowSpec()),
        scope=RunScope(**scope), args={}, targets=[], polestar=None, apm=None, now=datetime.now,
        prior={},
    )


_REG = {"serverName": "cocm-ngcpkd05", "hostname": "worker1"}
_SAME = {"serverName": "wasdemo01", "hostname": "wasdemo01"}


@pytest.mark.parametrize("current", [None, "worker1"])
def test_registered_name_kept_without_link(current: str | None) -> None:
    ctx = _ctx(_REG, polestar_source="polestar", polestar_server=current)
    assert _rescoped_polestar_server(ctx, "worker1") == ("cocm-ngcpkd05", None)


def test_source_ranked_server_not_overwritten() -> None:
    """D-348 소스 순위가 정한 범위 값(호스트명 아님)은 이벤트 등록명으로도 덮지 않는다."""
    ctx = _ctx(_REG, polestar_source="polestar", polestar_server="PS-MAPPED", polestar_basis="설정 매핑")
    assert _rescoped_polestar_server(ctx, "worker1") == ("PS-MAPPED", "설정 매핑")


@pytest.mark.parametrize(("current", "basis", "expected_basis"), [
    (None, None, POLESTAR_JENNIFER_HOST_BASIS.format(host="wasdemo01")),
    ("wasdemo01", "설정 매핑", "설정 매핑 · " + POLESTAR_JENNIFER_HOST_BASIS.format(host="wasdemo01")),
])
def test_empty_or_hostname_scope_uses_host_and_records_basis(
    current: str | None, basis: str | None, expected_basis: str,
) -> None:
    ctx = _ctx(_SAME, polestar_source="polestar", polestar_server=current, polestar_basis=basis)
    assert _rescoped_polestar_server(ctx, "wasdemo01") == ("wasdemo01", expected_basis)


def test_no_polestar_source_records_no_basis() -> None:
    ctx = _ctx(_SAME, polestar_server=None)
    assert _rescoped_polestar_server(ctx, "wasdemo01") == ("wasdemo01", None)


def test_linked_scope_unchanged() -> None:
    ctx = _ctx(_REG, polestar_source="polestar", polestar_server="PS-REG-01", polestar_link="linked",
               polestar_basis="b")
    assert _rescoped_polestar_server(ctx, "worker1") == ("PS-REG-01", "b")
