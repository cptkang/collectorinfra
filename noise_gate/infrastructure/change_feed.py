"""변경/구성 이벤트 피드 어댑터 (Plan 60 E5 · D-081 초안 — 읽기전용 인프라).

폴스타 변경이력 테이블 `cmm_resource_lifecycle_history`에서 최근 창(window) 내 변경
이벤트를 조회해 domain `overlay_changes`가 소비할 `ChangeEvent` 리스트로 반환한다.

실측 스키마(폴스타 제품 스키마 — 개발 :5434에서 확인):
    cmm_resource_lifecycle_history(
        id, resource_id(bigint), resource_type(varchar), lifecycle_type(varchar),
        description(varchar), event_time(bigint))
    → 변경 이벤트 = "resource_id가 event_time에 lifecycle_type 변경". E5 ChangeEvent에 정확 매핑.

주의(실측):
    - **개발 sandbox 데이터는 합성 placeholder**(event_time=1..5·description_N·cmm_resource
      조인 0건)라 통합 테스트에서 실 오버레이는 나오지 않는다. 스키마는 실 폴스타 제품
      스키마이나 dev 데이터는 더미다 → 어댑터는 반드시 **graceful**(데이터 부재·조회 실패·
      빈 결과 → 빈 리스트). 상관 로직 검증은 합성 ChangeEvent 단위 테스트로 한다.
    - **event_time 단위 미확정**(dev 더미로 초/ms 판별 불가). 실운영은 다른 폴스타 timestamp
      칼럼 관례를 따른다고 가정하고, window 비교는 reference_epoch와 **동일 epoch 단위**로
      일관 처리한다(cutoff = reference_epoch − window_seconds). 단위가 다르면(예: ms) 실운영
      편입 시 `event_time` 스케일 보정이 필요하다 — 편입 시 실측 후 조정한다.

안전 제약(D-003 정합):
    - 읽기 전용 SELECT 단일문만. DML/DDL 절대 금지. 외부 입력은 `_sql_literal`로 이스케이프.
    - PostgreSQL 방언(`polestar.` 스키마 한정)은 기존 polestar_noise_context 패턴 재사용.
      비PostgreSQL(b0/DB2)·조회 실패·빈 결과 → 빈 리스트(호출부는 change_nearby 미산출).
    - DBHub(MCP) 실행 경로는 호출부가 열어 준 client(execute_sql)를 재사용한다(topology_loader
      패턴 — 별도 커넥션 열지 않음). 폐쇄망 로컬.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

# 스키마 한정·리터럴 이스케이프·행 조회는 노이즈 컨텍스트와 단일 출처 공유(동일 infra 계층).
from noise_gate.infrastructure.polestar_noise_context import (
    _row_value,
    _sql_literal,
    _table,
)

logger = logging.getLogger(__name__)

# 조회 상한(메모리·비용 가드) — 창 내 변경이 폭주해도 유계로 조회한다.
_MAX_CHANGE_ROWS = 200


@dataclass(frozen=True)
class ChangeEvent:
    """변경이력 1건(cmm_resource_lifecycle_history 행 매핑, §7.2 확정 설계).

    Attributes:
        resource_id: 변경된 리소스 ID(문자열 정규화 — E4 리소스 ID·overlay 매칭과 동형).
        resource_type: 리소스 유형(varchar).
        change_type: 변경 유형(= lifecycle_type varchar).
        description: 변경 설명(varchar).
        event_time: 변경 발생 시각(epoch bigint — 단위 미확정, 모듈 주석 참조).
    """

    resource_id: str
    resource_type: str
    change_type: str
    description: str
    event_time: int


def build_recent_changes_sql(
    db_id: str,
    cutoff_epoch: int,
    reference_epoch: int,
    resource_ids: Optional[list[str]] = None,
) -> str:
    """최근 창 내 변경이력 조회 SQL을 조립한다(읽기전용 SELECT 단일문).

    `cutoff_epoch <= event_time <= reference_epoch`(알람 직전 창)로 유계 조회하고,
    resource_ids가 주어지면 그 리소스로 스코프한다(효율 — 영향 리소스만). 정렬은
    event_time 내림차순(최신 우선), LIMIT로 상한. cutoff/reference는 숫자 epoch이라
    정수 리터럴로 보간하고, resource_ids는 `_sql_literal`로 이스케이프한다.
    """
    t_hist = _table(db_id, "cmm_resource_lifecycle_history")
    where = [
        f"event_time >= {int(cutoff_epoch)}",
        f"event_time <= {int(reference_epoch)}",
    ]
    if resource_ids:
        in_list = ", ".join(_sql_literal(str(r)) for r in resource_ids)
        where.append(f"resource_id IN ({in_list})")
    where_clause = "\n  AND ".join(where)
    return (
        "SELECT\n"
        "    id,\n"
        "    resource_id,\n"
        "    resource_type,\n"
        "    lifecycle_type,\n"
        "    description,\n"
        "    event_time\n"
        f"FROM {t_hist}\n"
        f"WHERE {where_clause}\n"
        "ORDER BY event_time DESC\n"
        f"LIMIT {_MAX_CHANGE_ROWS}"
    )


def _parse_event_time(value: Any) -> Optional[int]:
    """event_time을 int로 파싱한다(변환 불가·None → None, 해당 행은 제외)."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class ChangeFeed:
    """폴스타 변경이력 조회기(무상태 — noise_ctx Redis 캐시가 결과를 재사용한다, §7.2).

    repo가 인스턴스 하나를 보유·재사용하지만 자체 캐시는 두지 않는다(변경 후보는
    promote 전용 신호라 노후 수용 — 노이즈 컨텍스트 캐시 300s 왕복으로 충분).
    """

    async def fetch_recent_changes(
        self,
        client,  # noqa: ANN001 — DBClient (execute_sql)
        db_id: str,
        window_seconds: int,
        *,
        reference_epoch: int,
        resource_ids: Optional[list[str]] = None,
    ) -> list[ChangeEvent]:
        """알람 직전 window 내 변경이력을 조회한다. 실패·빈 결과·피드 부재 → 빈 리스트(graceful).

        계획 인터페이스(`fetch_recent_changes(window_seconds) -> list[ChangeEvent]`)를 계승하되,
        실행에 필요한 client·db_id·기준 시각을 명시로 받고(topology_loader 패턴), 효율을 위해
        resource_ids 스코프 인자를 선택적으로 받는다(§7.2 실측 보완).

        Args:
            client: 호출부가 열어 준 DBClient(execute_sql). 별도 커넥션을 열지 않는다.
            db_id: 대상 폴스타 DB.
            window_seconds: 알람 직전 이 창(초) 내 변경만 조회한다.
            reference_epoch: 알람 발생 시각(epoch). cutoff = reference_epoch − window_seconds.
            resource_ids: 영향 리소스 ID 스코프(선택). None이면 리소스 필터 없이 창 전체 조회.

        Returns:
            `list[ChangeEvent]`(event_time 내림차순). 어떤 실패에도 예외를 전파하지 않고
            빈 리스트로 강등한다(D-003 정합 — 피드 부재/조회 실패/빈 결과 graceful).
        """
        cutoff = int(reference_epoch) - max(int(window_seconds), 0)
        sql = build_recent_changes_sql(db_id, cutoff, int(reference_epoch), resource_ids)
        try:
            result = await client.execute_sql(sql)
            rows = list(getattr(result, "rows", []) or [])
        except Exception as exc:  # noqa: BLE001 — 조회 실패 → 빈 리스트(graceful)
            logger.warning(
                "변경 피드 조회 실패 — 빈 리스트 강등: db_id=%s: %s", db_id, exc
            )
            return []

        changes: list[ChangeEvent] = []
        for row in rows:
            event_time = _parse_event_time(_row_value(row, "event_time"))
            if event_time is None:
                continue  # 시각 미상 변경은 오버레이 불가 → 제외
            rid = _row_value(row, "resource_id")
            changes.append(
                ChangeEvent(
                    resource_id=str(rid) if rid is not None else "",
                    resource_type=str(_row_value(row, "resource_type") or ""),
                    change_type=str(_row_value(row, "lifecycle_type") or ""),
                    description=str(_row_value(row, "description") or ""),
                    event_time=event_time,
                )
            )
        return changes
