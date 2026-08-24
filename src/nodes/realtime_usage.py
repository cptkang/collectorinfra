"""실시간 CPU/메모리 사용률 조회 노드 (Plan 71 — 2단계 하이브리드 데이터 평면).

흐름 (Plan 71 §2, 전부 결정적 — LLM 미사용, D-035):
    ① 존별 서버 목록 SQL (cmm_resource 결정적 조립 — few-shot 캡 모방 위험 원천 제거)
    ② measurement API 호출 (200대/콜 청크·존 병렬, PolestarMeasurementClient)
    ③ 병합: 요청 ID 대조 → "미수집" 표기, time(수집 시각) 임계 초과 → "수집 지연" 플래그

폴백 규약 (침묵 금지):
    - 전 존 실패 → None 반환: 호출부(run_data_query_pipeline)가 기존 SQL 파이프라인으로
      폴백한다(사유는 로그).
    - 일부 존 실패 → 성공 존 데이터 + summary에 실패 존·사유 명시.

감사로깅: measurement API 호출을 audit 로그(query_execution 이벤트, sql 필드에
[REALTIME-API] 표기)로 기록한다 — "모든 조회는 감사로깅" 스펙 정합.
"""

from __future__ import annotations

import logging
import re
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from src.clients.polestar_measurement import PolestarMeasurementClient
from src.config import AppConfig
from src.routing.db_registry import DBRegistry
from src.routing.db_schema import get_schema_prefix
from src.routing.domain_config import get_domain_by_id
from src.security.audit_logger import log_query_execution

logger = logging.getLogger(__name__)

_KST = timezone(timedelta(hours=9))

_METRIC_COLUMNS = {"cpu": "CPU 사용률(%)", "memory": "메모리 사용률(%)"}

# 존 표기 (선택지 라벨과 동일 입도 — Plan 75 §4.4). 라벨 정본은 존 역질문 선택지
# (ZONE_CLARIFY_OPTIONS)이므로 파생한다(사본 금지 — 위치 표면어 정본 1곳 단언 테스트 준수).
from src.utils.query_gen_common import ZONE_CLARIFY_OPTIONS

_ZONE_LABELS = {o["db_id"]: o["label"] for o in ZONE_CLARIFY_OPTIONS}

# 서버명 지목 질의의 결정적 필터용 토큰: 영숫자+하이픈, 숫자 포함, 5자 이상
# (예: cob0-bnoapd05). "cpu"/"메모리" 등 지표어는 숫자가 없어 자연 배제된다.
_HOST_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-_]{3,}")


def detect_metrics(user_query: str | None) -> list[str]:
    """질의에서 조회 지표를 결정적으로 판정한다 (기본 CPU)."""
    q = (user_query or "").lower()
    metrics: list[str] = []
    if "cpu" in q or "씨피유" in q:
        metrics.append("cpu")
    if "메모리" in q or "mem" in q:
        metrics.append("memory")
    return metrics or ["cpu"]


def _host_tokens(user_query: str) -> list[str]:
    """질의에서 서버명 후보 토큰(숫자 포함 영숫자열)을 추출한다."""
    return [
        t.lower() for t in _HOST_TOKEN_RE.findall(user_query or "")
        if any(ch.isdigit() for ch in t)
    ]


def _server_list_sql(db_id: str) -> str:
    """존의 활성 서버 목록 SQL을 결정적으로 조립한다 (엔진 공통 — LIMIT 불필요,
    실행 클라이언트 max_rows가 안전망).

    avail_status 포함(2026-07-24 실측 확정): '미수집' 서버는 대부분 Power off 또는
    에이전트 통신 이슈 — 가용성을 함께 표기해 미수집 원인이 결과에서 바로 읽히게 한다.
    """
    prefix = get_schema_prefix(db_id) or ""
    return (
        f"SELECT id, name, hostname, avail_status FROM {prefix}cmm_resource "
        "WHERE resource_type = 'server.Server' AND dtime IS NULL"
    )


def _row_get(row: dict, key: str) -> Any:
    """DB2가 결과 칼럼 라틴 문자를 소문자/대문자로 반환하는 편차를 흡수한다."""
    if key in row:
        return row[key]
    return row.get(key.upper(), row.get(key.lower()))


def _zone_label(db_id: str) -> str:
    if db_id in _ZONE_LABELS:
        return _ZONE_LABELS[db_id]
    domain = get_domain_by_id(db_id)
    return domain.display_name if domain else db_id


async def realtime_usage_lookup(
    db_ids: list[str],
    user_query: str,
    app_config: AppConfig,
    *,
    user_id: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> Optional[dict]:
    """실시간 사용률을 조회해 data_query 호환 결과 dict를 반환한다 (실패 시 None).

    Args:
        db_ids: 대상 폴스타 DB 목록 (라우팅 확정 결과)
        user_query: 원문 질의 (지표 판정·서버명 필터용)
        app_config: 앱 설정 (polestar_rest.*)
        user_id / thread_id: 감사로깅용

    Returns:
        {organized_data, query_results, generated_sql:"", source:"realtime_api"}
        전 존 실패·대상 없음이면 None (호출부 SQL 파이프라인 폴백).
    """
    cfg = app_config.polestar_rest
    client = PolestarMeasurementClient(cfg)
    registry = DBRegistry(app_config)
    metrics = detect_metrics(user_query)
    tokens = _host_tokens(user_query)
    stale_ms = cfg.stale_after_minutes * 60 * 1000
    now_ms = time.time() * 1000

    rows_out: list[dict] = []
    notes: list[str] = []
    any_success = False

    for db_id in db_ids:
        label = _zone_label(db_id)
        if not cfg.get_base_url(db_id):
            notes.append(f"{label}: 실시간 API 미설정 — 제외")
            continue

        # ① 서버 목록 (결정적 SQL)
        started = time.time()
        try:
            async with registry.get_client(db_id) as db:
                result = await db.execute_sql(_server_list_sql(db_id))
                servers = list(result.rows or [])
        except Exception as e:  # noqa: BLE001 — 존 단위 graceful (부분 반환 보장)
            logger.warning("realtime_usage 서버 목록 실패: db_id=%s %s", db_id, e)
            notes.append(f"{label}: 서버 목록 조회 실패")
            continue

        # 서버명 지목 질의면 결정적 필터 (매칭 0이면 존 전체 유지)
        if tokens:
            filtered = [
                s for s in servers
                if any(
                    t in str(_row_get(s, "name") or "").lower()
                    or t in str(_row_get(s, "hostname") or "").lower()
                    for t in tokens
                )
            ]
            if filtered:
                servers = filtered

        ids: list[int] = []
        for s in servers:
            rid = _row_get(s, "id")
            try:
                ids.append(int(rid))
            except (TypeError, ValueError):
                continue
        if not ids:
            notes.append(f"{label}: 대상 서버 없음")
            continue

        # 동일 서버명 중복 등록 탐지(2026-07-24 실측 가설): 재등록된 구행(dtime IS NULL 잔존)의
        # id는 measurement에 데이터가 없어 '미수집'으로 보인다 — 단건 수동 호출(살아있는 id)은
        # 정상인데 파이프라인만 미수집인 증상의 유력 원인. 중복이 있으면 명시 경고.
        name_counts = Counter(
            str(_row_get(s, "name") or "").strip() for s in servers
        )
        dup_names = [n for n, c in name_counts.items() if n and c > 1]
        if dup_names:
            notes.append(
                f"{label}: 동일 서버명 중복 등록 {len(dup_names)}건 감지"
                f"(예: {', '.join(dup_names[:5])}) — 구등록 행은 '미수집'으로 표시될 수 있음, "
                "'리소스 ID' 컬럼으로 대조 필요"
            )

        # ② measurement API (지표별 1콜 세트)
        per_metric: dict[str, Any] = {}
        failed_metric: Optional[str] = None
        for metric in metrics:
            mres = await client.fetch_zone(db_id, ids, metric)
            if mres is None:
                failed_metric = metric
                break
            per_metric[metric] = mres

        elapsed_ms = (time.time() - started) * 1000
        try:
            await log_query_execution(
                sql=f"[REALTIME-API] measurement db_id={db_id} servers={len(ids)} metrics={metrics}",
                row_count=0 if failed_metric else len(ids),
                execution_time_ms=elapsed_ms,
                success=failed_metric is None,
                error=f"metric={failed_metric} 실패" if failed_metric else None,
                user_id=user_id,
                thread_id=thread_id,
                source_name=db_id,
            )
        except Exception:  # noqa: BLE001 — 감사 실패가 조회를 막지 않도록
            logger.debug("realtime_usage 감사로깅 실패 (무시)", exc_info=True)

        if failed_metric:
            notes.append(f"{label}: 실시간 조회 실패({failed_metric}) — DB 경로로 재질의 필요")
            continue
        any_success = True

        # ③ 병합 — 미수집·수집 지연·조회 실패 표기 (Plan 75 §1.3-2 파서 주의 ①③)
        # "조회 실패"(API 청크 실패 — 재시도하면 나올 수 있음)와 "미수집"(응답에 없음 =
        # measurement에 최근 수집값 부재: 수집 중단·모니터링 미등록 등)을 구분한다.
        failed_ids: set[int] = set()
        for m in per_metric.values():
            failed_ids |= set(m.failed_ids)
        partial = sum(m.failed_chunks for m in per_metric.values())
        if partial:
            notes.append(
                f"{label}: 일부 구간({partial}청크, 서버 {len(failed_ids)}대) API 실패 — '조회 실패' 표기"
            )
        for s in servers:
            try:
                rid = int(_row_get(s, "id"))
            except (TypeError, ValueError):
                continue
            # 가용성(avail_status: 0=정상, 그 외=비정상 — Known Mistakes 값 규약).
            # 미수집 원인(Power off/에이전트 통신 이슈, 2026-07-24 실측)을 행에서 바로 판독.
            _avail = _row_get(s, "avail_status")
            try:
                avail_label = "정상" if int(_avail) == 0 else "비정상(중지/통신이상)"
            except (TypeError, ValueError):
                avail_label = "-"
            row: dict[str, Any] = {
                "존": label,
                "서버명": _row_get(s, "name"),
                "hostname": _row_get(s, "hostname"),
                # 진단 대조용(2026-07-24): 미수집 행의 ID를 수동 API 호출·콘솔 ID와 바로
                # 대조할 수 있게 노출 — ID 불일치(중복 등록 구행) 판별의 핵심 단서.
                "리소스 ID": rid,
                "가용성": avail_label,
            }
            collected_ms: Optional[int] = None
            missing = False
            for metric in metrics:
                mrow = per_metric[metric].rows.get(rid)
                if mrow is None or mrow.avg is None:
                    row[_METRIC_COLUMNS[metric]] = None
                    missing = True
                else:
                    row[_METRIC_COLUMNS[metric]] = round(mrow.avg, 2)
                    if mrow.collected_at_ms:
                        collected_ms = max(collected_ms or 0, mrow.collected_at_ms)
            if collected_ms:
                row["수집 시각"] = datetime.fromtimestamp(
                    collected_ms / 1000, tz=_KST
                ).strftime("%Y-%m-%d %H:%M:%S")
                row["상태"] = (
                    "수집 지연" if (now_ms - collected_ms) > stale_ms else "정상"
                )
            else:
                row["수집 시각"] = None
                if rid in failed_ids:
                    row["상태"] = "조회 실패"
                else:
                    row["상태"] = "미수집" if missing else "정상"
            if missing and row["상태"] == "정상":
                row["상태"] = "일부 미수집"
            rows_out.append(row)

    if not any_success or not rows_out:
        logger.warning(
            "realtime_usage 전 존 실패/무데이터 — SQL 경로 폴백: db_ids=%s notes=%s",
            db_ids, notes,
        )
        return None

    metric_label = "·".join(_METRIC_COLUMNS[m] for m in metrics)
    status_counts = Counter(r.get("상태") for r in rows_out)
    status_text = ", ".join(f"{k} {v}대" for k, v in status_counts.most_common())
    summary = (
        f"실시간 사용률 조회(폴스타 measurement API, {metric_label}): "
        f"서버 {len(rows_out)}대 ({status_text}). "
        "값은 마지막 수집 시점 기준이며 '수집 시각' 컬럼 참조."
    )
    if status_counts.get("미수집"):
        summary += (
            " '미수집'은 폴스타 measurement에 최근 수집값이 없는 서버입니다"
            "(실측 확인: 대부분 Power off 또는 에이전트 통신 이슈 — '가용성' 컬럼으로 판독 가능). "
            "단, 가용성이 '정상'인 미수집은 수집 주기와 조회 시점의 경합으로 생기는 "
            "일시 상태일 수 있습니다(2026-07-24 실측: 재조회 시 값 반환됨 — 재질의로 해소)."
        )
    if status_counts.get("조회 실패"):
        summary += " '조회 실패'는 API 구간 오류입니다 — 재질의 시 해소될 수 있습니다."
    if notes:
        summary += " [주의] " + " / ".join(notes)

    return {
        "organized_data": {
            "summary": summary,
            "rows": rows_out,
            "column_mapping": None,
            "resolved_mapping": None,
            "is_sufficient": True,
            "sheet_mappings": None,
        },
        "query_results": rows_out,
        "generated_sql": "",
        "source": "realtime_api",
    }
