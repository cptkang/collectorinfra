"""폴스타 measurement API 클라이언트 (Plan 71 — 실시간 CPU/메모리 사용률).

엔드포인트 (Plan 75 §1 확정 — 2안):
    GET {base}/rest/v1/dashboard/measurement
        ?resourceIds={ids}&definitions={def}&type={type}&timeSelector=recent&count=1

핵심 규칙:
    - **읽기 전용 HTTP GET** — D-003 정합. 내부망 http, 비인증 (Plan 47-1과 동일 규약).
    - resourceIds는 **서버 리소스 ID**(cmm_resource.id). 콤마 구분, 200개/콜 청크
      (Plan 75 §1.3-4 실측: 200대 yd 814ms·gp 2,460ms/37KB — 안전 범위).
    - 청크는 병렬 호출 + **전체 타임아웃 가드**(per-call만으론 무력화 — Known Mistakes).
    - 응답 shape는 Plan 75 §1.3-2 확정본: 최상위 date(호출 시각) + data.measurement[]
      (resourceId·resourceName·min/avg/max·time(수집 시각 Unix ms)·targetId).
    - 요청한 서버가 응답에 누락될 수 있음(수집 중단 등) — 호출부가 요청 ID 집합과
      대조해 "미수집" 처리한다.
    - 비200/파싱 실패/타임아웃/네트워크 오류 → 해당 청크 실패로 기록하고 부분 반환.
      전 청크 실패 시 None (호출부 graceful degradation — SQL 경로 폴백).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, NamedTuple, Optional

import httpx

logger = logging.getLogger(__name__)

_PATH = "/rest/v1/dashboard/measurement"

# 지표 → (definitions, type) 파라미터 (Plan 75 §1.2 — 지표별 1콜)
METRIC_SPECS: dict[str, tuple[str, str]] = {
    "cpu": ("Utilization", "server.Cpus"),
    "memory": ("UsedPercent", "server.Memory"),
}


class MeasurementRow(NamedTuple):
    """서버 1대의 실시간 측정값 (measurement[] 항목 1개)."""

    resource_id: int
    resource_name: str
    avg: Optional[float]
    min_value: Optional[float]
    max_value: Optional[float]
    collected_at_ms: Optional[int]   # time — 마지막 수집 시각(Unix ms). 호출 시각 아님!
    target_id: Optional[int]         # CPU/MEM 리소스 ID — 1안 lastdata 드릴다운용 (후속)


class MeasurementResult(NamedTuple):
    """존 1개·지표 1개 조회 결과."""

    rows: dict[int, MeasurementRow]  # resourceId → row
    failed_chunks: int               # 실패한 청크 수 (0이면 완전 수집)
    total_chunks: int
    # 실패 청크에 속한 서버 ID — 호출부가 "조회 실패"(API 구간 실패)와
    # "미수집"(응답에 없음 = measurement에 최근 수집값 부재)을 구분 표기하기 위함
    # (2026-07-24 폐쇄망 실측: 두 원인이 모두 '미수집'으로 표기돼 파악 불가).
    failed_ids: frozenset[int] = frozenset()


def _to_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_measurement_payload(payload: Any) -> dict[int, MeasurementRow]:
    """확정 응답 shape(data.measurement[])를 resourceId 키 dict로 파싱한다.

    shape 이탈 항목(비dict, resourceId 없음)은 건너뛴다 — 실 런타임 shape 방어.
    """
    rows: dict[int, MeasurementRow] = {}
    if not isinstance(payload, dict):
        return rows
    data = payload.get("data")
    if not isinstance(data, dict):
        return rows
    items = data.get("measurement")
    if not isinstance(items, list):
        return rows
    for item in items:
        if not isinstance(item, dict):
            continue
        rid = _to_int(item.get("resourceId"))
        if rid is None:
            continue
        rows[rid] = MeasurementRow(
            resource_id=rid,
            resource_name=str(item.get("resourceName") or ""),
            avg=_to_float(item.get("avg")),
            min_value=_to_float(item.get("min")),
            max_value=_to_float(item.get("max")),
            collected_at_ms=_to_int(item.get("time")),
            target_id=_to_int(item.get("targetId")),
        )
    return rows


class PolestarMeasurementClient:
    """폴스타 measurement API 클라이언트 (httpx, 읽기 전용 GET)."""

    def __init__(self, rest_cfg) -> None:  # noqa: ANN001 — PolestarRestConfig
        """클라이언트를 초기화한다.

        Args:
            rest_cfg: PolestarRestConfig — get_base_url(db_id)/measurement_* 설정 사용.
        """
        self._cfg = rest_cfg

    def _build_url(self, base_url: str, ids: list[int], metric: str) -> str:
        definition, rtype = METRIC_SPECS[metric]
        id_csv = ",".join(str(i) for i in ids)
        return (
            f"{base_url.rstrip('/')}{_PATH}"
            f"?resourceIds={id_csv}&definitions={definition}&type={rtype}"
            f"&timeSelector=recent&count=1"
        )

    async def _fetch_chunk(
        self, client: httpx.AsyncClient, base_url: str, ids: list[int], metric: str
    ) -> Optional[dict[int, MeasurementRow]]:
        """청크 1개를 호출한다. 실패(비200/파싱/네트워크) 시 None."""
        url = self._build_url(base_url, ids, metric)
        try:
            resp = await client.get(url)
            if resp.status_code != 200:
                logger.warning(
                    "measurement API 비200: status=%s ids=%d개", resp.status_code, len(ids)
                )
                return None
            rows = parse_measurement_payload(resp.json())
            # 진단(2026-07-24 폐쇄망: 단건 호출은 되는데 배치에선 미수집): 청크별
            # 요청/수신 대수와 미수신 ID를 로그로 확정 — ID 불일치(중복 등록 구행) vs
            # API 배치 상한을 구분할 수 있게 한다.
            missing = [i for i in ids if i not in rows]
            if missing:
                logger.info(
                    "measurement 청크: 요청 %d대 → 수신 %d대, 미수신 %d대 (미수신 ID 샘플: %s)",
                    len(ids), len(rows), len(missing), missing[:20],
                )
            else:
                logger.info("measurement 청크: 요청 %d대 → 수신 %d대 (전량)", len(ids), len(rows))
            return rows
        except Exception as e:  # noqa: BLE001 — graceful degradation (Plan 47-1 규약)
            logger.warning("measurement API 청크 실패: %s (ids=%d개)", e, len(ids))
            return None

    async def fetch_zone(
        self, db_id: str, resource_ids: list[int], metric: str
    ) -> Optional[MeasurementResult]:
        """존(db_id) 1개의 서버 목록에 대해 지표 1개를 조회한다.

        Args:
            db_id: 폴스타 DB 식별자 (base_url 매핑 키)
            resource_ids: 서버 리소스 ID 목록 (cmm_resource.id)
            metric: "cpu" | "memory"

        Returns:
            MeasurementResult. base_url 미매핑·지표 미지원·전 청크 실패 시 None
            (호출부에서 SQL 경로 폴백 — 침묵 금지, 사유는 호출부가 명시).
        """
        base_url = self._cfg.get_base_url(db_id)
        if not base_url:
            logger.info("measurement base_url 미매핑 — 건너뜀: db_id=%s", db_id)
            return None
        if metric not in METRIC_SPECS:
            logger.warning("measurement 미지원 지표: %s", metric)
            return None
        if not resource_ids:
            return MeasurementResult(rows={}, failed_chunks=0, total_chunks=0)

        size = max(1, int(self._cfg.measurement_chunk_size))
        chunks = [resource_ids[i:i + size] for i in range(0, len(resource_ids), size)]
        timeout = self._cfg.measurement_timeout_seconds

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                # 청크 병렬 + 전체 가드: 병렬이라 이론상 per-call ≈ 전체지만,
                # 커넥션 지연 등 per-call 밖 병목까지 상한을 강제한다(Known Mistakes).
                results = await asyncio.wait_for(
                    asyncio.gather(
                        *(self._fetch_chunk(client, base_url, c, metric) for c in chunks),
                        return_exceptions=False,
                    ),
                    timeout=timeout * 2 + 5,
                )
        except (asyncio.TimeoutError, Exception) as e:  # noqa: BLE001
            logger.warning("measurement 전체 타임아웃/실패: db_id=%s %s", db_id, e)
            return None

        merged: dict[int, MeasurementRow] = {}
        failed = 0
        failed_ids: set[int] = set()
        for chunk, r in zip(chunks, results):
            if r is None:
                failed += 1
                failed_ids.update(chunk)
            else:
                merged.update(r)
        if failed == len(chunks):
            return None  # 전 청크 실패 — 폴백 대상
        logger.info(
            "measurement 존 합계: db_id=%s metric=%s 요청 %d대 → 수신 %d대 (실패청크 %d/%d)",
            db_id, metric, len(resource_ids), len(merged), failed, len(chunks),
        )
        return MeasurementResult(
            rows=merged, failed_chunks=failed, total_chunks=len(chunks),
            failed_ids=frozenset(failed_ids),
        )
