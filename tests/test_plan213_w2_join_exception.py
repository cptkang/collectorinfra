"""plans/213 W2(D-366 ②) — 심층 섹션이 실패·타임아웃·생략인 `done` 조사에는 합류하지 않는다.

다시 분석(`_manual_join_target`)과 추가 조사 재접수 양쪽이 같은 판정(`_deep_dive_retryable`)을 쓴다.
`incomplete`·`done`·심층 키 없음·저장 상한 절단으로 판정 불가 → 종전대로 합류.

저장 레코드 픽스처는 sre_agent `build_playbook_briefing` 산출 모양(키 순서 포함)을 그대로 옮긴
리터럴이다 — 짝 테스트 `sre_agent/tests/test_plan213_w2_briefing_shape.py`가 키 경로·값을 고정한다.
실 LLM·실 서비스 0(가짜 클라이언트).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from noise_gate.infrastructure.investigation_store import MAX_BRIEFING_BYTES, clip_briefing
from src.api.routes import alarm as alarm_routes
from tests.test_api.test_plan187_w10_followup import _post as _post_followup
from tests.test_api.test_plan187_w10_followup import _seed_parent
from tests.test_api.test_plan187_w11_manual import (  # noqa: F401 — autouse·fixture 등록
    ADMIN,
    _client,
    _config,
    _FakeClient,
    _hostname,
    _inject,
    _post,
    _reset_poll_state,
    _seed_alarm,
    _store,
    audits,
)

_LABEL = "심층 조사(HolmesGPT · 가설)"  # sre_agent DEEP_DIVE_LABEL


def _deep(status: str, **extra: Any) -> dict[str, Any]:
    """sre_agent `_deep_section` 산출 모양."""
    reasons = {
        "failed": "심층 조사 실패 — BadRequestError: unexpected tokens remaining in message header",
        "timeout": "심층 조사 타임아웃 — 전체 예산 investigation_timeout_seconds(600s) 소진",
        "skipped": "도구 1건은 처리됐으나 마지막 답이 서술이 아니라 도구 호출 꼴(마크업 리터럴)로 끝남",
    }
    out: dict[str, Any] = {"status": status, "mode": "parallel",
                           "reason": reasons.get(status), "label": _LABEL}
    if status == "done":
        out.update({"summary": "심층 요약", "cause": "SQL 지연", "narrative": "심층 서술",
                    "tool_calls": ["apm_resource_pool 풀 조회"], "incomplete": False})
    out.update(extra)
    return out


def _briefing(deep: dict[str, Any] | None, **over: Any) -> dict[str, Any]:
    """`build_playbook_briefing` 산출 모양(키 순서 동일 · `deep_dive`는 `playbook` 뒤)."""
    b: dict[str, Any] = {
        "severity": {"level": "high", "confidence": "medium", "escalate": False},
        "summary": "WAS 응답 지연 — 1순위 가설 SQL 지연",
        "timeline": ["09:30 알람", "09:31 S3 SQL 지배"],
        "bottleneck": "sql",
        "cause": "1) SQL 지연(지지)",
        "root_cause_hypotheses": ["SQL 지연"],
        "recommendation": {"items": ["롤백 검토"], "note": "사람 승인 후 실행"},
        "limitations": [],
        "citations_verified": True,
        "hypotheses": [],
        "playbook": {
            "id": "was.latency", "version": 1, "matched_by": "rule",
            "readings": {"dominant": "sql"},
            "sections": [{"step": "S0", "status": "ok", "verdict": "degraded",
                          "tools": ["apm_instance_map"], "table": {"rows": [["A1", "degraded"]]}}],
            "synthesis": {"hypotheses": ["SQL 지연"], "actions": [{"text": "롤백 검토"}]},
        },
    }
    if deep is not None:
        b["deep_dive"] = deep
    b["engine_agreement"] = {"result": "not_comparable"}
    b.update(over)
    return b


def _seed_manual(tmp_path, briefing: dict | None, *, status: str = "done",  # noqa: ANN001
                 inv: str = "man-old", alarm_id: str = "a1") -> None:
    _store(tmp_path).record(
        investigation_id=inv, alarm_ids=[alarm_id], kind="manual", status=status,
        source="manual", origin="manual", parent_alarm_id=alarm_id, briefing=briefing,
        ts=datetime.now(UTC) - timedelta(minutes=3),
    )


# ── 판정 함수 ────────────────────────────────────────────────────────────
@pytest.mark.parametrize(("deep", "expected"), [
    (_deep("failed"), True),
    (_deep("timeout"), True),
    (_deep("skipped"), True),
    (_deep("done"), False),
    (_deep("done", incomplete=True), False),
    (None, False),
    ("failed", False),
    ({"mode": "parallel"}, False),
])
def test_deep_dive_retryable(deep, expected):  # noqa: ANN001
    assert alarm_routes._deep_dive_retryable({"briefing": _briefing(deep)}) is expected


def test_no_briefing_or_non_dict_is_not_retryable():
    assert alarm_routes._deep_dive_retryable({}) is False
    assert alarm_routes._deep_dive_retryable({"briefing": '{"deep_dive": {}}'}) is False
    assert alarm_routes._deep_dive_retryable({"briefing": {"summary": "주 경로"}}) is False


def test_undeterminable_playbook_briefing_logs_debug(caplog):
    with caplog.at_level(logging.DEBUG, logger=alarm_routes.logger.name):
        assert alarm_routes._deep_dive_retryable(
            {"investigation_id": "inv-x", "briefing": _briefing(None)}) is False
    assert any("판정 불가" in r.getMessage() and "inv-x" in r.getMessage()
               for r in caplog.records)


# ── 저장 상한 절단(clip_briefing) ─────────────────────────────────────────
def test_clip_keeps_deep_status_when_playbook_is_large():
    big_pb = _briefing(_deep("failed"))["playbook"]
    big_pb["sections"] = [
        {"step": f"S{i}", "status": "ok", "verdict": "degraded", "tools": ["t"],
         "opinion": "소견 " * 300, "table": {"rows": [["x" * 50] * 4] * 40}}
        for i in range(8)
    ]
    original = _briefing(_deep("failed"), playbook=big_pb)
    clipped = clip_briefing(original)
    assert clipped is not original  # 상한을 넘어 실제로 줄였다
    assert clipped["deep_dive"]["status"] == "failed"
    assert alarm_routes._deep_dive_retryable({"briefing": clipped}) is True


def test_clip_keeps_status_when_narrative_is_large():
    deep = _deep("done", narrative="서술 " * 20000)
    clipped = clip_briefing(_briefing(deep))
    assert clipped["deep_dive"]["status"] == "done"
    assert len(clipped["deep_dive"].get("narrative") or "") < len(deep["narrative"])
    assert alarm_routes._deep_dive_retryable({"briefing": clipped}) is False


def test_clip_dropping_deep_dive_is_undeterminable():
    clipped = clip_briefing(_briefing(_deep("failed"), summary="요약 " * MAX_BRIEFING_BYTES))
    assert "deep_dive" not in clipped  # 앞선 큰 값이 상한을 다 썼다
    assert alarm_routes._deep_dive_retryable({"briefing": clipped}) is False


# ── 다시 분석 ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("status", ["failed", "timeout", "skipped"])
def test_manual_done_with_failed_deep_submits_new(tmp_path, monkeypatch, audits, status):  # noqa: ANN001, F811
    _seed_alarm(tmp_path)
    _seed_manual(tmp_path, _briefing(_deep(status)))
    fake = _FakeClient()
    _inject(monkeypatch, fake)
    body = _post(_client(_config(tmp_path), ADMIN)).json()
    assert body["joined"] is False and body["investigation_id"] == "man-1"
    assert len(fake.submit_calls) == 1


@pytest.mark.parametrize("briefing", [
    _briefing(_deep("done")),
    _briefing(_deep("done", incomplete=True)),
    _briefing(None),
    {"summary": "주 경로 브리핑"},
    clip_briefing(_briefing(_deep("failed"), summary="요약 " * MAX_BRIEFING_BYTES)),
], ids=["done", "incomplete", "no_deep_key", "main_path", "clipped_out"])
def test_manual_done_joins_when_deep_ok_or_unknown(tmp_path, monkeypatch, audits, briefing):  # noqa: ANN001, F811
    _seed_alarm(tmp_path)
    _seed_manual(tmp_path, briefing)
    fake = _FakeClient()
    _inject(monkeypatch, fake)
    body = _post(_client(_config(tmp_path), ADMIN)).json()
    assert body == {"investigation_id": "man-old", "status": "done", "joined": True}
    assert fake.submit_calls == []


def test_manual_inflight_join_unchanged(tmp_path, monkeypatch, audits):  # noqa: ANN001, F811
    _seed_alarm(tmp_path)
    _seed_manual(tmp_path, None, status="submitted")
    fake = _FakeClient(poll={"status": "running"})
    _inject(monkeypatch, fake)
    body = _post(_client(_config(tmp_path), ADMIN)).json()
    assert body["joined"] is True and body["investigation_id"] == "man-old"
    assert fake.submit_calls == []


# ── 추가 조사 재접수 ─────────────────────────────────────────────────────
def _seed_diag(tmp_path, briefing: dict | None, status: str = "done") -> None:  # noqa: ANN001
    _store(tmp_path).record(
        investigation_id="diag-old", alarm_ids=[], kind="diagnosis", status=status,
        source="investigation_chat", owner_sub="admin1", parent_investigation_id="inv-1",
        focus="db_wait", briefing=briefing,
    )


def _followup_config(tmp_path):  # noqa: ANN001, ANN202
    cfg = _config(tmp_path)
    cfg.noise_gate.investigation_additional_enabled = True
    return cfg


@pytest.mark.parametrize("status", ["failed", "timeout", "skipped"])
def test_followup_done_with_failed_deep_submits_new(tmp_path, monkeypatch, audits, status):  # noqa: ANN001, F811
    _seed_parent(tmp_path)
    _seed_diag(tmp_path, _briefing(_deep(status)))
    fake = _FakeClient()
    _inject(monkeypatch, fake)
    resp = _post_followup(_client(_followup_config(tmp_path), ADMIN), focus="db_wait").json()
    assert resp["reused"] is False and resp["investigation_id"] == "diag-1"
    assert len(fake.diagnose_calls) == 1


@pytest.mark.parametrize("briefing", [
    _briefing(_deep("done")),
    _briefing(_deep("done", incomplete=True)),
    _briefing(None),
    None,
], ids=["done", "incomplete", "no_deep_key", "no_briefing"])
def test_followup_done_reuses_when_deep_ok_or_unknown(tmp_path, monkeypatch, audits, briefing):  # noqa: ANN001, F811
    _seed_parent(tmp_path)
    _seed_diag(tmp_path, briefing)
    fake = _FakeClient()
    _inject(monkeypatch, fake)
    resp = _post_followup(_client(_followup_config(tmp_path), ADMIN), focus="db_wait").json()
    assert resp["reused"] is True and resp["investigation_id"] == "diag-old"
    assert fake.diagnose_calls == []
