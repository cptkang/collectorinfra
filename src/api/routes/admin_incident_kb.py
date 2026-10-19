"""관리자 「조사 지식」 API — KB 초안 목록·YAML 내보내기·집계 (plans/187 W9 · §5.7.5 · D-341 ⑩⑳).

`NOISE_INVESTIGATION_KB_ENABLED`가 켜졌을 때만 `server.create_app`이 이 라우터를 등록한다
(기동 시 1회 · 꺼지면 라우트 부재 = 404). 관리자 의존성(`require_admin_user`) 뒤에서
role 클레임을 한 번 더 명시 검증한다. KB 기록·조사 저장소 헬퍼는 조사 라우트(`alarm.py`)의 것을
그대로 쓴다.

- 초안은 본체 KB 기록에만 있다 — YAML은 내려받아 저장소 리뷰로 편입한다(런타임 쓰기 0 · G-11).
- 집계(런북 커버리지 · 검토 대상 · 엔진 통계·전환 권고 · KE 재발)는 조사 저장소 끝
  `investigation_kb_scan_max_lines`줄과 브리핑에 실린 값만 쓴다
  (sre_agent 파일을 읽지 않는다 · D-118).
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from src.api.dependencies import is_admin, require_admin_user
from src.api.routes.alarm import _audit_kb, _kb_scan_records, _kb_store
from src.domain.kb_draft import (
    draft_to_yaml,
    engine_stats,
    ke_activity,
    ke_feedback_review,
    parse_ts,
    recurrence_of,
    review_targets,
    runbook_coverage,
)

logger = logging.getLogger(__name__)
router = APIRouter()

_DRAFT_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
#: 전환 권고 고정 표기(⑳ — 자동 전환 없음).
SWITCH_NOTICE = "권고 표시만 — 전환은 관리자가 플레이북 YAML을 바꿔 리뷰로 반영한다(자동 전환 없음)"


def _assert_admin(request: Request, admin: dict[str, Any]) -> None:
    """role 클레임 명시 검증 — 개발 모드(인증 꺼짐)만 예외."""
    if request.app.state.config.auth.enabled and not is_admin(admin):
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")


def _draft_summary(draft: dict[str, Any]) -> dict[str, Any]:
    raw = draft.get("ke")
    ke: dict[str, Any] = raw if isinstance(raw, dict) else {}
    return {
        "draft_id": draft.get("draft_id"),
        "investigation_id": draft.get("investigation_id"),
        "playbook_id": draft.get("playbook_id"),
        "title": ke.get("title"),
        "cause": ke.get("cause"),
        "match_candidates": len((ke.get("match") or {}).get("all") or []),
        "engine_choice": draft.get("engine_choice"),
        "cause_source": draft.get("cause_source"),
        "created_by": draft.get("created_by"),
        "created_at": draft.get("created_at"),
    }


@router.get(
    "/admin/incident-kb/drafts",
    summary="KB 초안 목록(관리자)",
    description="본체 KB 기록의 초안 목록(최신 먼저 · 조회 창 안)을 반환합니다.",
    tags=["admin"],
)
async def list_kb_drafts(
    request: Request,
    admin: dict[str, Any] = Depends(require_admin_user),
) -> dict[str, Any]:
    """`{items[{draft_id, investigation_id, playbook_id, title, cause, …}]}`."""
    _assert_admin(request, admin)
    store = _kb_store(request.app.state.config)
    return {"items": [_draft_summary(d) for d in store.list_drafts()]}


@router.get(
    "/admin/incident-kb/drafts/{draft_id}/yaml",
    summary="KB 초안 YAML 조각 내려받기(관리자)",
    description=(
        "알려진 오류 레코드 YAML 조각(state: wip · 검토 주석)을 첨부 파일로 반환합니다. "
        "저장소 리뷰로 편입하는 재료이며 서버는 sre_agent 파일에 쓰지 않습니다."
    ),
    tags=["admin"],
)
async def download_kb_draft_yaml(
    draft_id: str,
    request: Request,
    admin: dict[str, Any] = Depends(require_admin_user),
) -> Response:
    """초안 → `application/yaml` 첨부(`<draft_id>.yaml`)."""
    from src.security.audit_logger import INVESTIGATION_OK

    _assert_admin(request, admin)
    if not _DRAFT_ID.fullmatch(draft_id):
        raise HTTPException(status_code=400, detail={"code": "invalid_draft_id",
                                                     "reason": "초안 id 형식이 아닙니다."})
    draft = _kb_store(request.app.state.config).get_draft(draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail={"code": "draft_not_found",
                                                     "reason": "초안을 찾지 못했습니다."})
    await _audit_kb(admin, INVESTIGATION_OK, "kb_draft_export", draft_id=draft_id,
                    investigation_id=draft.get("investigation_id"))
    return Response(
        content=draft_to_yaml(draft),
        media_type="application/yaml; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{draft_id}.yaml"'},
    )


@router.get(
    "/admin/incident-kb/summary",
    summary="조사 지식 집계(관리자)",
    description=(
        "런북 커버리지(브리핑에서 본 플레이북 id 분포) · "
        "검토 대상(런북·KE 기한 경과 · 「아님」 연속 · "
        "일치 0건 180일) · 유형별 엔진 선택 통계와 전환 권고 · KE 재발 신호(30일)를 반환합니다. "
        "집계 창은 조사 저장소 끝 `NOISE_INVESTIGATION_KB_SCAN_MAX_LINES`줄입니다(`window`)."
    ),
    tags=["admin"],
)
async def incident_kb_summary(
    request: Request,
    admin: dict[str, Any] = Depends(require_admin_user),
) -> dict[str, Any]:
    """관리자 「조사 지식」 탭 1회 조회분."""
    _assert_admin(request, admin)
    config = request.app.state.config
    ng = config.noise_gate
    store = _kb_store(config)
    now = datetime.now(UTC)
    records = _kb_scan_records(config)
    stamps = [t for t in (parse_ts(r.get("ts")) for r in records) if t is not None]
    oldest = min(stamps) if stamps else None
    coverage = runbook_coverage(records)
    activity = ke_activity(records, now=now)
    feedback = ke_feedback_review(
        store.ke_feedback(), streak=int(getattr(ng, "investigation_kb_incorrect_streak", 3))
    )
    min_samples = int(getattr(ng, "investigation_kb_engine_min_samples", 30))
    switch_ratio = float(getattr(ng, "investigation_kb_engine_switch_ratio", 0.8))
    return {
        "generated_at": now.isoformat(),
        "window": {
            "rows": len(records),
            "oldest": oldest.isoformat() if oldest else None,
            "newest": max(stamps).isoformat() if stamps else None,
            "scan_max_lines": int(getattr(ng, "investigation_kb_scan_max_lines", 10000)),
        },
        "runbook_coverage": coverage,
        "review_targets": review_targets(
            coverage=coverage, activity=activity, feedback_review=feedback,
            now=now, window_oldest=oldest,
        ),
        "engine_stats": {
            "items": engine_stats(
                store.engine_choices(), min_samples=min_samples, switch_ratio=switch_ratio
            ),
            "min_samples": min_samples,
            "switch_ratio": switch_ratio,
            "notice": SWITCH_NOTICE,
        },
        "ke_feedback": feedback,
        "ke_recurrence": recurrence_of(activity),
        "drafts": len(store.list_drafts()),
    }
