"""폴스타 실시간 프로세스 조회 API 클라이언트 (Plan 47-1).

CPU/메모리 알람 시점에 해당 서버에서 자원을 점유 중인 프로세스를 폴스타 실시간
프로세스 API로 조회한다 (읽기 전용 HTTP GET).

엔드포인트:
    GET {base_url}/rest/server/process/listByhostname?hostname={hostname}

핵심 규칙 (Plan 47-1 §2, §5.3, §9):
    - **조회 키는 hostname** — server_name(=${platformName})이 아니라 AlarmEvent.hostname.
      (Plan 47의 DB 이력 조회가 r.name(=serverName)을 쓰는 것과 정반대 키. 혼동 금지.)
    - **인증 불필요** — 내부 시스템이며 비로그인 조회됨. 인증 헤더/토큰을 다루지 않는다.
    - **scheme는 http://** — 내부망이라 TLS 미사용. verify_ssl 등 설정 없음.
    - hostname은 URL 인코딩(urllib.parse.quote)하여 쿼리 인젝션 방지.
    - base_url은 설정 고정값(사용자 입력 아님) → SSRF/경로 조작 불가.
    - 비200/파싱 실패/타임아웃/네트워크 오류 → None 반환 (호출부에서 graceful degradation).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, NamedTuple, Optional
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

_PATH = "/rest/server/process/listByhostname"
_DATE_FMT = "%Y-%m-%d %H:%M:%S"


class ProcessApiResult(NamedTuple):
    """프로세스 API 응답 (스냅샷 시각 + 프로세스 원시 목록).

    list_by_hostname 은 응답 본문의 최상위 ``date``(스냅샷 시각)와 ``data.list``를
    함께 반환한다 — ProcessSnapshot.captured_at 표시용으로 date가 필요하기 때문이다
    (Plan 47-1 §5.3 의 list 반환을 시각까지 포함하도록 확장).
    """

    captured_at: Optional[datetime]
    processes: list[dict[str, Any]]


def _parse_date(value: Any) -> Optional[datetime]:
    """응답 최상위 date("YYYY-mm-DD HH:MM:SS")를 datetime으로 변환한다 (실패 시 None)."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        return datetime.strptime(text, _DATE_FMT)
    except ValueError:
        try:
            return datetime.fromisoformat(text.replace(" ", "T", 1))
        except ValueError:
            return None


class PolestarProcessApiClient:
    """폴스타 실시간 프로세스 조회 API 클라이언트 (httpx, 읽기 전용 GET)."""

    def __init__(self, alarm_cfg) -> None:  # noqa: ANN001 — AlarmConfig
        """클라이언트를 초기화한다.

        Args:
            alarm_cfg: AlarmConfig — get_process_api_base_url(db_id)/
                process_api_timeout_seconds 를 사용한다.
        """
        self._alarm_cfg = alarm_cfg

    def get_base_url(self, db_id: str) -> Optional[str]:
        """db_id에 매핑된 base_url을 반환한다 (없으면 None)."""
        return self._alarm_cfg.get_process_api_base_url(db_id)

    async def list_by_hostname(
        self, db_id: str, hostname: str
    ) -> Optional[ProcessApiResult]:
        """{base_url}/rest/server/process/listByhostname?hostname=... 를 호출한다.

        - base_url = alarm_cfg.get_process_api_base_url(db_id); None이면 즉시 None 반환
        - hostname은 urllib.parse.quote로 URL 인코딩 (쿼리 인젝션 방지)
        - 타임아웃 process_api_timeout_seconds
        - 비200/파싱 실패/타임아웃/네트워크 오류 → None 반환 (예외를 호출부로 던지지 않음)

        Returns:
            ProcessApiResult(captured_at=응답 date, processes=data.list 배열).
            list가 없으면 processes=[]. 조회 불가 시 None.
        """
        base_url = self.get_base_url(db_id)
        if not base_url:
            logger.debug("프로세스 API base_url 미매핑 — 조회 건너뜀: db_id=%s", db_id)
            return None
        if not hostname:
            logger.debug("프로세스 API hostname 비어있음 — 조회 건너뜀: db_id=%s", db_id)
            return None

        url = f"{base_url.rstrip('/')}{_PATH}?hostname={quote(hostname, safe='')}"
        timeout = self._alarm_cfg.process_api_timeout_seconds
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(url)
            if resp.status_code != 200:
                logger.warning(
                    "프로세스 API 비200 응답 — 조회 생략: db_id=%s hostname=%s status=%s",
                    db_id,
                    hostname,
                    resp.status_code,
                )
                return None
            payload = resp.json()
        except Exception as exc:
            logger.warning(
                "프로세스 API 조회 실패 — 이력 없이 진행: db_id=%s hostname=%s err=%s",
                db_id,
                hostname,
                exc,
            )
            return None

        captured_at = _parse_date(payload.get("date")) if isinstance(payload, dict) else None
        data = payload.get("data") if isinstance(payload, dict) else None
        proc_list = data.get("list") if isinstance(data, dict) else None
        if proc_list is None:
            # 200을 받았으나 기대 경로(data.list)가 없음 — 응답 envelope이 인스턴스마다 다를 수
            # 있다(예: 은행 레거시 b0). 실제 형태를 진단할 수 있도록 최상위 구조를 로깅한다.
            top_keys = list(payload.keys()) if isinstance(payload, dict) else type(payload).__name__
            data_keys = list(data.keys()) if isinstance(data, dict) else type(data).__name__
            logger.warning(
                "프로세스 API 응답에 data.list 경로 없음 — 0건 처리: db_id=%s hostname=%s "
                "top_keys=%s data_keys=%s body=%s",
                db_id, hostname, top_keys, data_keys, repr(payload)[:800],
            )
            return ProcessApiResult(captured_at=captured_at, processes=[])
        if not proc_list:
            logger.info(
                "프로세스 API data.list 비어있음(0건): db_id=%s hostname=%s captured_at=%s",
                db_id, hostname, captured_at,
            )
        return ProcessApiResult(
            captured_at=captured_at,
            processes=[it for it in proc_list if isinstance(it, dict)],
        )
