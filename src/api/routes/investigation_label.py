"""조사 「실제 원인」 라벨 · 관리자 조사 정확도 API (plans/198 W0 I-1 · D-350 ②).

`NOISE_INVESTIGATION_LABEL_ENABLED`가 켜졌을 때만 `server.create_app`이 이 라우터를 등록한다
(기동 시 1회 · 꺼지면 라우트 부재 = 404). 라우트마다 같은 플래그를 인증보다 먼저 한 번 더 본다.

- 라벨(`/alarm/investigations/{id}/cause-label`): 조회 인가(`_assert_investigation_access`
  — 존·소유자 규칙 그대로) 뒤에 **쓰기 인가**를 더 건다 — 조사 소유자(`owner_sub`) 또는
  관리자만. 자동 조사(alarm·episode)는 소유자가 없어 관리자만이다(fail-closed). 완료 조사만
  받는다. 저장은 기존 피드백 저장소(`feedback_store_path`)에 `investigation_id`로 붙인다(새 저장소
  없음). 자유 서술은 추가 조사 보충 한 줄과 같은 정리·길이 상한·마스킹(`sanitize_note`)을 쓴다.
- 관리자(`/admin/investigation/accuracy`·`…/candidates`): 관리자 의존성 뒤에서 role 클레임을 명시
  검증한다. 집계는 `noise_gate.domain.investigation_accuracy`(결정적 · LLM 0)이고, 조사 저장소 끝
  `investigation_kb_scan_max_lines`줄의 조사별 최신 행을 쓴다. 후보 내보내기는 메타만(D-308).
- (plans/198 W7 I-8 · D-350 ⑤) 비슷한 과거 조사(`/alarm/investigations/{id}/similar`)는 별도
  라우터(`memory_router`)로 `NOISE_INVESTIGATION_MEMORY_ENABLED`가 켜졌을 때만 등록한다. 대상
  조사 조회 인가 뒤, 같은 스캔 범위에서 지문(`noise_gate.domain.incident_fingerprint` — 결정적 ·
  LLM 0)이 비슷한 완료 조사 중 **조회 인가를 통과한 것만** 3건 보인다. 결론은 라벨이 붙은
  조사만(사용자 뷰는 마스킹). 같은 플래그 + KB(`NOISE_INVESTIGATION_KB_ENABLED`)가 켜져 있으면
  **운영자(관리자) 라벨** 저장 직후 KEDB 부트스트랩을 본다 — 같은 지문 + 같은 확정 원인의
  운영자 라벨이 N회면 기존 KB 초안 흐름(`build_draft` · `wip` 고정)으로 1건만 만든다(기존
  `POST …/kb-draft`가 운영자 전용인 것과 정렬 · 승격은 사람 · 실패해도 라벨 응답은 그대로).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from noise_gate.domain.incident_fingerprint import (
    SIMILAR_LIMIT,
    kedb_candidate,
    similar_investigations,
)
from noise_gate.domain.investigation_accuracy import (
    CAUSE_VERDICTS,
    OUTCOME_DONE,
    export_candidates,
    hypothesis_ranks,
    outcome_of,
    validate_label,
    weekly_report,
)
from src.api.dependencies import is_admin, require_admin_user, require_user
from src.api.routes.alarm import (
    _assert_investigation_access,
    _audit_kb,
    _followup_error,
    _investigation_mask_fn,
    _investigation_store,
    _is_alarm_operator,
    _kb_alarm,
    _kb_scan_records,
    _kb_store,
)
from src.domain.kb_draft import CAUSE_FROM_LABEL

logger = logging.getLogger(__name__)
router = APIRouter()
#: (plans/198 W7 I-8) 비슷한 과거 조사 — `NOISE_INVESTIGATION_MEMORY_ENABLED`일 때만 등록.
memory_router = APIRouter()

#: 감사 진입점 값(`log_investigation(entry_point=…)`).
_LABEL_ENTRY_POINT = "investigation_label"
#: KEDB 부트스트랩 초안의 작성자 표기(사람이 만든 초안과 구분).
KEDB_CREATED_BY = "kedb_bootstrap"
#: 비슷한 과거 조사 고정 표기 — 결론은 라벨 붙은 조사만(D-350 ⑤).
SIMILAR_NOTICE = "실제 원인이 입력된 조사만 결론을 보입니다 — 입력 전 조사는 「확정 전」입니다."
#: 내보내기 고정 표기(D-308 — 값·서술 반출 없음).
EXPORT_NOTICE = (
    "메타만 — 조사 id·kind·엔진·라벨·가설 순위/상태. "
    "알람 원문·브리핑 서술·자유 서술 본문은 싣지 않습니다(D-308)."
)


def _label_enabled(config: Any) -> bool:
    """라벨·정확도 플래그(기동 시 설정값)."""
    return bool(getattr(config.noise_gate, "investigation_label_enabled", False))


async def _require_label_enabled(request: Request) -> None:
    """플래그 off면 미등록 라우트와 같은 404 — 인증·저장소 접근보다 먼저 판정한다."""
    if not _label_enabled(request.app.state.config):
        raise HTTPException(status_code=404, detail="Not Found")


def _memory_enabled(config: Any) -> bool:
    """사건 기억 플래그(기동 시 설정값)."""
    return bool(getattr(config.noise_gate, "investigation_memory_enabled", False))


async def _require_memory_enabled(request: Request) -> None:
    """플래그 off면 미등록 라우트와 같은 404 — 인증·저장소 접근보다 먼저 판정한다."""
    if not _memory_enabled(request.app.state.config):
        raise HTTPException(status_code=404, detail="Not Found")


def _assert_admin(request: Request, admin: dict[str, Any]) -> None:
    """role 클레임 명시 검증 — 개발 모드(인증 꺼짐)만 예외."""
    if request.app.state.config.auth.enabled and not is_admin(admin):
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")


def _feedback_store(config: Any) -> Any:
    """기존 피드백 저장소 — 꺼져 있으면 503(쓰기·집계 불가 사유 노출)."""
    from noise_gate.infrastructure.feedback_store import FeedbackStore

    ng = config.noise_gate
    if not bool(getattr(ng, "feedback_store_enabled", True)):
        raise _followup_error(
            503, "feedback_store_disabled",
            "피드백 저장소가 꺼져 있습니다(NOISE_FEEDBACK_STORE_ENABLED).",
        )
    return FeedbackStore(
        ng.feedback_store_path, True, int(getattr(ng, "feedback_store_max_lines", 20000))
    )


async def _audit(current_user: dict[str, Any], outcome: str, **extra: Any) -> None:
    """라벨 감사 1건(쓰기·거부 · 실패해도 응답을 막지 않는다)."""
    from src.security import audit_logger

    try:
        await audit_logger.log_investigation(
            entry_point=_LABEL_ENTRY_POINT,
            outcome=outcome,
            user_id=current_user.get("sub"),
            backend="feedback_store",
            action="cause_label",
            **extra,
        )
    except Exception:  # noqa: BLE001 — 감사 기록 실패가 응답을 막지 않는다(사유는 로그)
        logger.warning("조사 라벨 감사 기록 실패", exc_info=True)


def _viewable_record(
    request: Request, current_user: dict[str, Any], investigation_id: str
) -> dict[str, Any]:
    """조회 인가(`_assert_investigation_access`)까지 끝낸 조사 레코드.

    Raises:
        HTTPException: 저장소 미설정 503 · 없음 404 · 조회 권한 없음 403.
    """
    store = _investigation_store(request.app.state.config)
    if store is None:
        raise _followup_error(503, "store_unconfigured", "조사 저장소 경로가 설정되지 않았습니다.")
    rec = store.get_by_investigation(investigation_id)
    if rec is None:
        raise _followup_error(404, "investigation_not_found", "조사를 찾지 못했습니다.")
    _assert_investigation_access(request, current_user, rec)
    return rec


def _can_label(config: Any, current_user: dict[str, Any], rec: dict[str, Any]) -> bool:
    """쓰기 인가 — 조사 소유자 또는 관리자(자동 조사는 소유자가 없어 관리자만 · fail-closed)."""
    sub = current_user.get("sub")
    return _is_alarm_operator(config, current_user) or bool(sub and sub == rec.get("owner_sub"))


async def _label_target(
    request: Request, current_user: dict[str, Any], investigation_id: str
) -> dict[str, Any]:
    """라벨 대상 조사 — 조회 인가 + 쓰기 인가(소유자 또는 관리자)까지 끝낸 레코드(쓰기 전용).

    Raises:
        HTTPException: 저장소 미설정 503 · 없음 404 · 권한 없음 403.
    """
    from src.security.audit_logger import INVESTIGATION_DENIED

    rec = _viewable_record(request, current_user, investigation_id)
    if not _can_label(request.app.state.config, current_user, rec):
        await _audit(current_user, INVESTIGATION_DENIED,
                     investigation_id=investigation_id, reason="owner_only")
        raise _followup_error(
            403, "owner_only", "실제 원인은 조사를 요청한 사용자 또는 관리자만 입력합니다."
        )
    return rec


def _note_max_chars(config: Any) -> int:
    """자유 서술 상한 — 추가 조사 보충 한 줄과 같은 설정값(화면 입력란 maxlength도 이 값)."""
    return int(getattr(config.noise_gate, "investigation_additional_note_max_chars", 200))


class CauseLabelRequest(BaseModel):
    """「실제 원인」 라벨 본문."""

    verdict: str = Field(
        ..., max_length=32,
        description="top1(1순위 맞음) | ranked(k순위 맞음 — rank 필수) | "
        "not_listed(목록에 없음) | undeterminable(판단 불가)",
    )
    rank: int | None = Field(None, ge=1, le=99, description="ranked일 때 맞은 가설 순위(2 이상)")
    note: str | None = Field(
        None, max_length=2000,
        description="자유 서술(선택 · 개행 제거 · 설정 상한 이내 · 마스킹)",
    )


def _label_view(label: dict[str, Any] | None) -> dict[str, Any] | None:
    if label is None:
        return None
    return {k: label.get(k) for k in ("verdict", "rank", "note", "ts")}


@router.get(
    "/alarm/investigations/{investigation_id}/cause-label",
    summary="조사 「실제 원인」 라벨 조회(조사 소유자·관리자)",
    description=(
        "이 조사의 최신 라벨과 입력 가능 여부(완료 조사만) · 가설 순위 목록 · 자유 서술 상한을 "
        "반환합니다. 조회 권한은 있으나 입력 권한(조사를 요청한 사용자 또는 관리자)이 없으면 "
        "`labelable=false`·라벨 null입니다(거부 감사 없음).<br/>"
        "조사 조회 권한 · `NOISE_INVESTIGATION_LABEL_ENABLED`가 꺼져 있으면 404입니다."
    ),
    tags=["alarm"],
    dependencies=[Depends(_require_label_enabled)],
)
async def get_cause_label(
    investigation_id: str,
    request: Request,
    current_user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    """조사 → `{investigation_id, labelable, hypothesis_ranks, verdicts, label, note_max_chars}`.

    입력 권한이 없으면 거부 감사 없이 `labelable=false`(화면은 그리지 않는다 · 교정 m2 — 열람마다
    거부 감사가 쌓이지 않게). 쓰기 거부 감사는 POST에만 남는다.
    """
    config = request.app.state.config
    rec = _viewable_record(request, current_user, investigation_id)
    out: dict[str, Any] = {
        "investigation_id": investigation_id,
        "labelable": False,
        "hypothesis_ranks": [],
        "verdicts": list(CAUSE_VERDICTS),
        "label": None,
        "note_max_chars": _note_max_chars(config),
    }
    if not _can_label(config, current_user, rec):
        return out
    store = _feedback_store(config)
    out.update(
        labelable=outcome_of(rec) == OUTCOME_DONE,
        hypothesis_ranks=hypothesis_ranks(rec),
        label=_label_view(store.investigation_labels().get(investigation_id)),
    )
    return out


@router.post(
    "/alarm/investigations/{investigation_id}/cause-label",
    summary="조사 「실제 원인」 라벨 입력(조사 소유자·관리자)",
    description=(
        "완료 조사의 실제 원인을 기록합니다 — 1순위 맞음 / k순위 맞음 / 목록에 없음(+자유 서술) / "
        "판단 불가. 다시 입력하면 최신 1건이 유효합니다. "
        "판정·가설 순위·통보는 바뀌지 않습니다.<br/>"
        "조사를 요청한 사용자 또는 관리자만 · 완료 전 409 · "
        "`NOISE_INVESTIGATION_LABEL_ENABLED`가 꺼져 있으면 404입니다."
    ),
    tags=["alarm"],
    dependencies=[Depends(_require_label_enabled)],
)
async def post_cause_label(
    investigation_id: str,
    body: CauseLabelRequest,
    request: Request,
    current_user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    """라벨 1건 → `{recorded, investigation_id, verdict, rank, note, ts}`."""
    from src.domain.investigation_followup import sanitize_note
    from src.security.audit_logger import INVESTIGATION_DENIED, INVESTIGATION_OK

    config = request.app.state.config
    rec = await _label_target(request, current_user, investigation_id)
    if outcome_of(rec) != OUTCOME_DONE:
        raise _followup_error(
            409, "not_done", "완료된 조사만 실제 원인을 입력할 수 있습니다."
        )
    rank, reason = validate_label(body.verdict, body.rank, hypothesis_ranks(rec))
    if reason:
        await _audit(current_user, INVESTIGATION_DENIED,
                     investigation_id=investigation_id, reason="invalid_label")
        raise _followup_error(400, "invalid_label", reason)
    note, note_reason = sanitize_note(
        body.note, _note_max_chars(config), _investigation_mask_fn(config)
    )
    if note_reason:
        await _audit(current_user, INVESTIGATION_DENIED,
                     investigation_id=investigation_id, reason="invalid_note")
        raise _followup_error(400, "invalid_note", note_reason)
    store = _feedback_store(config)
    now = datetime.now(UTC)
    operator = _is_alarm_operator(config, current_user)
    if not store.record_investigation_label(
        investigation_id=investigation_id, verdict=body.verdict, rank=rank, note=note or "",
        kind=str(rec.get("kind") or "alarm"), labeled_by=str(current_user.get("sub") or ""),
        operator=operator, ts=now,
    ):
        # 디스크 실패도 여기로 온다(교정 M2) — 성공 응답·성공 감사를 남기지 않는다
        await _audit(current_user, INVESTIGATION_DENIED,
                     investigation_id=investigation_id, reason="store_write_failed")
        raise _followup_error(503, "store_write_failed", "라벨 기록에 실패했습니다.")
    await _audit(current_user, INVESTIGATION_OK, investigation_id=investigation_id,
                 verdict=body.verdict, rank=rank)
    out: dict[str, Any] = {
        "recorded": True, "investigation_id": investigation_id, "verdict": body.verdict,
        "rank": rank, "note": note or "", "ts": now.isoformat(),
    }
    # (교정 M3) KEDB 부트스트랩은 운영자 라벨일 때만 — 기존 KB 초안 라우트가 운영자 전용이다
    if operator and _memory_enabled(config):
        try:
            draft = await _bootstrap_kedb(config, current_user, rec, store)
        except Exception:  # noqa: BLE001 — 초안 실패가 라벨 저장을 무르지 않는다(사유는 로그)
            logger.warning(
                "KEDB 부트스트랩 실패: investigation=%s", investigation_id, exc_info=True
            )
            draft = None
        if draft is not None:
            out["kedb_draft"] = draft
    return out


async def _bootstrap_kedb(
    config: Any, current_user: dict[str, Any], rec: dict[str, Any], feedback: Any
) -> dict[str, Any] | None:
    """KEDB 부트스트랩(plans/198 W7 I-8 ③) — 만든 초안 `{draft_id, state, count}` 또는 None.

    호출부가 운영자 라벨일 때만 부른다. 문턱 계수도 운영자 라벨(`operator` 표지)만 센다 —
    소유자 라벨이 KB 초안 인가(운영자 전용)를 우회하지 않게(교정 M3).
    KB 플래그가 꺼졌거나 · 플레이북 조사가 아니거나(기존 KB 초안 흐름과 같은 전제) · 문턱
    미달이거나 · 같은 (지문, 확정 원인) 초안이 이미 있으면 만들지 않는다. 초안은
    `build_draft`(`wip` 고정)에 원인만 라벨이 확정한 원인으로 바꾸고 근거 메타(`bootstrap`)를
    붙인다. 승격은 사람이 한다.
    """
    import uuid
    from datetime import UTC as _UTC

    from src.domain.kb_draft import DRAFT_STATE, build_draft, playbook_of
    from src.security.audit_logger import INVESTIGATION_OK

    ng = config.noise_gate
    if not bool(getattr(ng, "investigation_kb_enabled", False)):
        return None
    briefing = rec.get("briefing")
    if not isinstance(briefing, dict) or playbook_of(briefing) is None:
        return None
    operator_labels = {
        iid: label for iid, label in feedback.investigation_labels().items()
        if label.get("operator") is True
    }
    candidate = kedb_candidate(
        rec, _kb_scan_records(config), operator_labels,
        min_count=int(getattr(ng, "investigation_kedb_min_count", 3)),
    )
    if candidate is None:
        return None
    try:
        kb = _kb_store(config)
    except HTTPException:
        logger.warning("KEDB 부트스트랩 생략 — KB 기록 경로 미설정·거부")
        return None
    if any(
        isinstance(d.get("bootstrap"), dict) and d["bootstrap"].get("key") == candidate["key"]
        for d in kb.list_drafts()
    ):
        return None
    investigation_id = str(rec.get("investigation_id") or "")
    now = datetime.now(_UTC)
    draft = build_draft(
        draft_id=uuid.uuid4().hex[:12],
        investigation_id=investigation_id,
        briefing=briefing,
        alarm=_kb_alarm(config, rec),
        engine_choice=None,
        created_by=KEDB_CREATED_BY,
        created_at=now.isoformat(),
    )
    draft["ke"]["cause"] = (
        f"{candidate['cause']} (운영 라벨 {candidate['count']}회 확정 — 사람 검토 전)"
    )
    draft["cause_source"] = CAUSE_FROM_LABEL
    draft["bootstrap"] = {
        **candidate, "triggered_by": str(current_user.get("sub") or ""),
    }
    if not kb.record_draft(draft, ts=now):
        logger.warning("KEDB 부트스트랩 초안 기록 실패: investigation=%s", investigation_id)
        return None
    await _audit_kb(current_user, INVESTIGATION_OK, "kb_draft_bootstrap",
                    investigation_id=investigation_id, draft_id=draft["draft_id"],
                    playbook_id=draft["playbook_id"])
    return {"draft_id": draft["draft_id"], "state": DRAFT_STATE, "count": candidate["count"]}


def _kb_scan_resolved(config: Any) -> list[dict[str, Any]]:
    """`_kb_scan_records`와 같은 스캔 범위의 병합본(`resolved_records` — 연결 행 알람 합집합)."""
    from noise_gate.infrastructure.investigation_store import InvestigationStore

    ng = config.noise_gate
    path = ng.investigation_store_path
    if not path:
        return []
    return InvestigationStore(
        path, int(getattr(ng, "investigation_kb_scan_max_lines", 10000))
    ).resolved_records()


def _labels_or_empty(config: Any) -> dict[str, dict[str, Any]]:
    """라벨 맵 — 피드백 저장소가 꺼져 있으면 빈 맵(모든 유사 조사가 「확정 전」)."""
    ng = config.noise_gate
    if not bool(getattr(ng, "feedback_store_enabled", True)):
        return {}
    labels: dict[str, dict[str, Any]] = _feedback_store(config).investigation_labels()
    return labels


@memory_router.get(
    "/alarm/investigations/{investigation_id}/similar",
    summary="비슷한 과거 조사(결정적 지문 · 결론은 라벨 붙은 것만)",
    description=(
        "이 조사와 지문(조사 종류·플레이북·판독 값·지지 가설·호스트군/서비스)이 비슷한 완료 조사 "
        "최대 3건을 반환합니다(Jaccard · 임베딩·LLM 없음). 「실제 원인」 라벨이 붙은 조사만 결론을 "
        "싣고, 없으면 `labeled=false`·결론 null(「확정 전」)입니다. 조회 권한이 있는 조사만 "
        "나옵니다.<br/>"
        "이 조사의 조회 권한 · 사용자 뷰는 결론 마스킹 · "
        "`NOISE_INVESTIGATION_MEMORY_ENABLED`가 꺼져 있으면 404입니다."
    ),
    tags=["alarm"],
    dependencies=[Depends(_require_memory_enabled)],
)
async def similar_investigations_route(
    investigation_id: str,
    request: Request,
    current_user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    """조사 → `{investigation_id, items[], limit, notice}`."""
    config = request.app.state.config
    store = _investigation_store(config)
    if store is None:
        raise _followup_error(503, "store_unconfigured", "조사 저장소 경로가 설정되지 않았습니다.")
    rec = store.get_by_investigation(investigation_id)
    if rec is None:
        raise _followup_error(404, "investigation_not_found", "조사를 찾지 못했습니다.")
    _assert_investigation_access(request, current_user, rec)
    # (교정 M1) 후보 인가는 직접 열람과 같은 병합본(연결 행의 알람까지 · fail-closed)으로 판정한다.
    # 지문은 알람 목록을 쓰지 않으므로 같은 병합본으로 점수도 매긴다(파일 1회 스캔).
    records = _kb_scan_resolved(config)
    by_id = {str(r.get("investigation_id") or ""): r for r in records}
    mask = None if _is_alarm_operator(config, current_user) else _investigation_mask_fn(config)
    db_cache: dict[str, str | None] = {}  # (교정 m6) 후보 루프의 판정 기록 tail 메모
    items: list[dict[str, Any]] = []
    for item in similar_investigations(rec, records, _labels_or_empty(config), limit=None):
        try:
            _assert_investigation_access(
                request, current_user, by_id[item["investigation_id"]], db_cache=db_cache
            )
        except HTTPException:
            continue
        if mask is not None and item["conclusion"]:
            item["conclusion"] = mask(item["conclusion"])
        items.append(item)
        if len(items) >= SIMILAR_LIMIT:
            break
    return {"investigation_id": investigation_id, "items": items, "limit": SIMILAR_LIMIT,
            "notice": SIMILAR_NOTICE}


@router.get(
    "/admin/investigation/accuracy",
    summary="조사 정확도 주간 표(관리자 · 결정적)",
    description=(
        "조사 기록 × 「실제 원인」 라벨로 주별 후보 재현율 · RC@1 · RC@3 · 라벨률 · 미완주율 · "
        "조사 불가율과 창 전체의 엔진별·kind별 지표를 반환합니다. 라벨 없는 조사와 「판단 불가」는 "
        "정확도 분모에서 빠지며, 분모가 0이면 비율은 null입니다.<br/>"
        "관리자 전용 · 읽기 전용 · `NOISE_INVESTIGATION_LABEL_ENABLED`가 꺼져 있으면 404입니다."
    ),
    tags=["admin"],
    dependencies=[Depends(_require_label_enabled)],
)
async def accuracy_report(
    request: Request,
    weeks: int = Query(8, ge=1, le=26, description="최근 몇 주(이번 주 포함)"),
    admin: dict[str, Any] = Depends(require_admin_user),
) -> dict[str, Any]:
    """`{window, weeks[], total, by_engine, by_kind, generated_at, scan_max_lines}`."""
    _assert_admin(request, admin)
    config = request.app.state.config
    labels = _feedback_store(config).investigation_labels()
    now = datetime.now(UTC)
    report = weekly_report(
        _kb_scan_records(config), labels, now=now, weeks=weeks,
        tz=datetime.now().astimezone().tzinfo or UTC,
    )
    return {
        **report,
        "generated_at": now.isoformat(),
        "scan_max_lines": int(getattr(config.noise_gate, "investigation_kb_scan_max_lines", 10000)),
    }


@router.get(
    "/admin/investigation/accuracy/candidates",
    summary="라벨 붙은 조사 — 154 세계 후보 목록(관리자 · 메타만)",
    description=(
        "판정 라벨(1순위·k순위·목록에 없음)이 붙은 완료 조사의 메타(조사 id·kind·엔진·플레이북 id·"
        "라벨·가설 순위/상태)를 반환합니다. 값·서술은 싣지 않습니다(D-308).<br/>"
        "관리자 전용 · `NOISE_INVESTIGATION_LABEL_ENABLED`가 꺼져 있으면 404입니다."
    ),
    tags=["admin"],
    dependencies=[Depends(_require_label_enabled)],
)
async def accuracy_candidates(
    request: Request,
    admin: dict[str, Any] = Depends(require_admin_user),
) -> dict[str, Any]:
    """`{items[], count, notice, generated_at}`."""
    from src.security.audit_logger import INVESTIGATION_OK

    _assert_admin(request, admin)
    config = request.app.state.config
    labels = _feedback_store(config).investigation_labels()
    items = export_candidates(_kb_scan_records(config), labels)
    # (교정 m3) 관리자 데이터 반출 — 기존 KB 내보내기와 같은 감사 1건
    await _audit_kb(admin, INVESTIGATION_OK, "accuracy_candidates_export", count=len(items))
    return {"items": items, "count": len(items), "notice": EXPORT_NOTICE,
            "generated_at": datetime.now(UTC).isoformat()}


__all__ = ["memory_router", "router"]
