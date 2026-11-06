"""「업무 서비스 정의」(P1) 조회 API — 목록 · CSV 내보내기 · 상세 (plans/215 §5.1 ①② · §5.3 · §6 ·
§9 · D-368).

`NOISE_BIZ_SERVICE_ENABLED`(`biz_service_active`)가 켜진 채 기동했을 때만 등록한다(꺼져 있으면
라우트가 없어 404). 저장소(`app.state.biz_service_repo`)가 없으면 503 `biz_service_unavailable`.
조립은 `noise_gate.application.biz_service_view`가 하고, 여기는 인가·존 범위·마스킹만 맡는다
(D-139).

- 인가: 로그인 사용자(`require_user` — JWT `type` 클레임 검증 · 미로그인 401). 편집은 관리 API
  (`/api/v1/admin/biz-services` · `require_admin_user`)만 한다 — 여기에는 변경 동작이 없다.
- 존 필터: 알람 존(`alarm_zones_for_user`) 밖 구성원은 이름 없이 건수만 · 보이는 구성원이 없는
  서비스는 목록·CSV에서 빼고 `hidden_service_count`로만 센다(상세는 404 — 존재도 드러내지 않는다).
- 사용자 경로의 자유 문장(설명·담당 부서)은 관제 사용자 경로와 같은 값 패턴 마스킹(D-245 부기).
- CSV는 수식 주입 방지(`=`·`+`·`-`·`@`·탭·CR 시작 칸 앞에 `'`) · UTF-8 BOM · 목록과 같은 필터.
- 라우트 순서: `/export.csv`를 `/{service_id}`보다 먼저 두고 id는 정수 경로 변환(`:int`)이라
  `/monitor`(업무 현황 라우터)를 먹지 않는다.
- SQL은 저장소의 바인드 고정 SQL뿐 · 관측 DB·게이트웨이 호출 0 · LLM 0.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from src.api.dependencies import alarm_zones_for_user, is_admin, require_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/biz-services")

_Status = Literal["confirmed", "inferred", "review", "disabled"]
_Sort = Literal["tier", "name", "sms", "apm", "coverage", "inferred", "updated"]
#: 사용자 경로에서 값 패턴 마스킹을 거는 서비스 자유 문장 칸.
_FREE_TEXT_FIELDS = ("description", "owner_org")
#: 연계 지표를 찾을 때 거슬러 보는 적용 실행 수.
_RUN_LOOKBACK = 20


def _error(status_code: int, code: str, reason: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "reason": reason})


def _repo(request: Request) -> Any:
    repo = getattr(request.app.state, "biz_service_repo", None)
    if repo is None:
        raise _error(503, "biz_service_unavailable",
                     "업무 서비스 저장소(PG)를 쓸 수 없습니다 — 내부 PG(auth 풀) 없이 기동했거나 "
                     "기동 단계가 실패했습니다.")
    return repo


def _store_failed(exc: BaseException) -> HTTPException:
    logger.warning("업무 서비스 조회 실패", exc_info=exc)
    return _error(503, "biz_service_store_failed",
                  f"업무 서비스 저장소(PG) 조회에 실패했습니다({type(exc).__name__}). "
                  "잠시 뒤 다시 시도하세요.")


def _zone_scope(request: Request, user: dict[str, Any]) -> frozenset[str] | None:
    """요청자 알람 존 범위 — 전 존이면 None. 빈 집합은 거부가 아니라 「전부 건수만」이다."""
    from src.routing.zones import all_zones

    zones = alarm_zones_for_user(user, request.app.state.config.auth.enabled)
    if zones >= set(all_zones()):
        return None
    return frozenset(zones)


def _is_operator(request: Request, user: dict[str, Any]) -> bool:
    return bool(not request.app.state.config.auth.enabled) or is_admin(user)


def _mask_fn(request: Request) -> Callable[[str], str]:
    """관제 사용자 경로와 같은 값 패턴 마스킹(값 전체가 비밀값 꼴이면 가린다)."""
    from src.security.data_masker import DataMasker

    mask = request.app.state.config.security.mask_pattern

    def _mask(value: str) -> str:
        for pattern in DataMasker.SENSITIVE_VALUE_PATTERNS:
            if pattern.match(value):
                return str(mask)
        return value

    return _mask


def _mask_fields(row: dict[str, Any], mask: Callable[[str], str]) -> None:
    for name in _FREE_TEXT_FIELDS:
        if isinstance(row.get(name), str):
            row[name] = mask(row[name])


async def _latest_metrics(repo: Any) -> tuple[str | None, Any, dict[str, Mapping[str, Any]]]:
    """마지막 완료 적용 실행 → `(run_id, finished_at, 서비스 키별 지표)` — 없으면 빈 지표."""
    from noise_gate.application.biz_service_view import analysis_services

    for run in await repo.list_runs(mode="apply", limit=_RUN_LOOKBACK):
        if run.get("status") == "done" and isinstance(run.get("summary"), Mapping):
            return run.get("run_id"), run.get("finished_at"), analysis_services(run)
    return None, None, {}


async def _scoped_rows(
    request: Request,
    user: dict[str, Any],
    *,
    q: str | None,
    zone: str | None,
    tier: int | None,
    status: str | None,
    monitored: bool | None,
    coverage_below: int | None,
    sort: str,
    order: str,
) -> tuple[list[dict[str, Any]], int, str | None, Any]:
    """목록·CSV 공용 — 조건 검색 → 존 범위·필터·정렬 → `(행, 숨긴 서비스 수, run_id, 시각)`."""
    from noise_gate.application.biz_service_view import list_rows

    repo = _repo(request)
    scope = _zone_scope(request, user)
    try:
        services = await repo.search_services(
            tier=tier, state="disabled" if status == "disabled" else None,
            monitored=monitored, query=q, member_zones=scope,
        )
        stats = await repo.member_stats()
        run_id, at, metrics = await _latest_metrics(repo)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — 사유를 실어 503
        raise _store_failed(exc) from None
    rows, hidden = list_rows(
        services, stats, scope=scope, metrics=metrics, zone=zone or None,
        status=status, coverage_below=coverage_below, sort=sort, descending=order == "desc",
    )
    return rows, hidden, run_id, at


@router.get(
    "",
    summary="업무 서비스 목록(업무 서비스 정의)",
    description=(
        "검색(표시명·서비스 키·구성원 서버명·hostname)·존·등급·상태·감시 대상·APM 커버리지 필터, "
        "정렬과 페이지(≤100).<br/>존 권한 밖 구성원은 이름 없이 건수만 셉니다."
    ),
    tags=["biz-service"],
)
async def list_biz_services(
    request: Request,
    q: str | None = Query(None, max_length=200),
    zone: str | None = Query(None, max_length=64),
    tier: int | None = Query(None, ge=1, le=3),
    status: _Status | None = None,
    monitored: bool | None = None,
    coverage_below: int | None = Query(None, ge=1, le=100),
    sort: _Sort = "tier",
    order: Literal["asc", "desc"] = "asc",
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    """`{rows[], total, limit, offset, hidden_service_count, analysis_run_id, analysis_at,
    is_admin}`."""
    rows, hidden, run_id, at = await _scoped_rows(
        request, current_user, q=q, zone=zone, tier=tier, status=status, monitored=monitored,
        coverage_below=coverage_below, sort=sort, order=order,
    )
    operator = _is_operator(request, current_user)
    page = rows[offset:offset + limit]
    if not operator:
        mask = _mask_fn(request)
        for row in page:
            _mask_fields(row, mask)
    return {"rows": page, "total": len(rows), "limit": limit, "offset": offset,
            "hidden_service_count": hidden, "analysis_run_id": run_id, "analysis_at": at,
            "is_admin": operator}


@router.get(
    "/export.csv",
    summary="업무 서비스 CSV 내보내기",
    description=(
        "목록과 같은 필터·존 범위로 가져오기 템플릿 형식"
        "(`service_key,display_name,tier,owner_org,db_id,server_name,hostname,layer`) CSV를 "
        "내려줍니다.<br/>UTF-8 BOM · 수식으로 읽힐 칸은 앞에 `'`를 붙입니다."
    ),
    tags=["biz-service"],
)
async def export_biz_services(
    request: Request,
    q: str | None = Query(None, max_length=200),
    zone: str | None = Query(None, max_length=64),
    tier: int | None = Query(None, ge=1, le=3),
    status: _Status | None = None,
    monitored: bool | None = None,
    coverage_below: int | None = Query(None, ge=1, le=100),
    sort: _Sort = "tier",
    order: Literal["asc", "desc"] = "asc",
    current_user: dict[str, Any] = Depends(require_user),
) -> Response:
    """CSV 본문(`text/csv` · 첨부)."""
    from noise_gate.application.biz_service_view import export_csv

    rows, _hidden, _run, _at = await _scoped_rows(
        request, current_user, q=q, zone=zone, tier=tier, status=status, monitored=monitored,
        coverage_below=coverage_below, sort=sort, order=order,
    )
    try:
        full = {int(s["id"]): s for s in await _repo(request).all_services_with_members()}
    except Exception as exc:  # noqa: BLE001 — 사유를 실어 503
        raise _store_failed(exc) from None
    ordered = [full[int(r["id"])] for r in rows if int(r["id"]) in full]
    mask = None if _is_operator(request, current_user) else _mask_fn(request)
    body = export_csv(ordered, _zone_scope(request, current_user), mask=mask)
    return Response(
        content=body.encode("utf-8"), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="biz-services.csv"'},
    )


@router.get(
    "/{service_id:int}",
    summary="업무 서비스 상세(업무 서비스 정의)",
    description=(
        "서비스 + 구성원(존 권한 밖은 종류별 건수만) + 마지막 적용 연계 지표 + 연계 그래프"
        "(서비스 → APM 도메인 → 인스턴스 → SMS 서버).<br/>없는 서비스·존 권한 밖 서비스는 "
        "404입니다."
    ),
    tags=["biz-service"],
)
async def get_biz_service(
    service_id: int, request: Request, current_user: dict[str, Any] = Depends(require_user)
) -> dict[str, Any]:
    """`{service, members[], hidden_member_count, hidden_by_kind, metrics, analysis_run_id,
    link_graph, is_admin}`."""
    from noise_gate.application.biz_service_view import scoped_detail
    from noise_gate.domain.biz_service_keys import KIND_SMS_SERVER

    repo = _repo(request)
    scope = _zone_scope(request, current_user)
    operator = _is_operator(request, current_user)
    try:
        row = await repo.get_service(service_id)
        run_id, _at, metrics = await _latest_metrics(repo)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — 사유를 실어 503
        raise _store_failed(exc) from None
    body = scoped_detail(row, scope, operator=operator) if row else None
    if body is None:
        raise _error(404, "not_found", "업무 서비스를 찾을 수 없습니다.")
    members = body["members"]
    refs = [(str(m.get("db_id") or ""), str(m.get("resource_id") or ""))
            for m in members if m.get("kind") == KIND_SMS_SERVER]
    try:
        branches = await repo.owner_branches(refs)
    except Exception:  # noqa: BLE001 — 담당 부점은 보조 표시(없으면 칸을 안 단다)
        logger.info("업무 서비스 담당 부점 읽기 실패: service=%s", service_id, exc_info=True)
        branches = {}
    for m in members:
        hit = branches.get((str(m.get("db_id") or ""), str(m.get("resource_id") or "")))
        if hit:
            m["owner_branch"] = hit
    body["link_graph"] = await _link_graph(request, members, scope)
    if not operator:
        _mask_fields(body["service"], _mask_fn(request))
    body.update(metrics=metrics.get(str(body["service"].get("service_key") or "")),
                analysis_run_id=run_id, is_admin=operator)
    return body


async def _link_graph(
    request: Request, members: list[dict[str, Any]], scope: frozenset[str] | None
) -> dict[str, Any]:
    """연계 그래프 — 인스턴스 구성원이 있을 때만 210 매핑을 1회 읽는다(실패는 사유)."""
    from noise_gate.application.biz_service import registry_zone
    from noise_gate.application.biz_service_view import link_graph
    from noise_gate.domain.biz_service_keys import KIND_APM_INSTANCE

    map_repo = getattr(request.app.state, "apm_host_map_repo", None)
    if map_repo is None:
        return link_graph(members, None, scope, registry_zone)
    if not any(m.get("kind") == KIND_APM_INSTANCE for m in members):
        return link_graph(members, [], scope, registry_zone)
    try:
        maps = await map_repo.all_rows()
    except Exception as exc:  # noqa: BLE001 — 그래프만 사유로 비운다
        logger.warning("업무 서비스 연계 그래프 — 210 매핑 읽기 실패: %s", type(exc).__name__)
        return {"available": False,
                "reason": f"연계 매핑을 읽지 못했습니다({type(exc).__name__}).",
                "domains": [], "unlinked_servers": [], "hidden_server_count": 0}
    return link_graph(members, maps, scope, registry_zone)
