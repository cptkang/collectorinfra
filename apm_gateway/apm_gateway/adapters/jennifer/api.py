"""제니퍼 Open API 조회 함수 — 허용목록 경로를 부르고 벤더 중립 레코드를 돌려준다 (plans/87
§5.2(c)·(e)).

애플리케이션 계층은 이 클래스의 메서드만 부른다 — Open API 경로·파라미터 이름·필드명이 이 패키지
밖으로 나가지 않는다(벤더 리터럴 격리 · overfit 게이트 제외 대상은 이 하위 패키지뿐). 시각은 epoch
ms로만 주고받고 `time_pattern`은 쓰지 않는다(§5.5 · U-14).
"""

from __future__ import annotations

from typing import Any

from apm_gateway.adapters.jennifer import fields as jf
from apm_gateway.adapters.jennifer.client import JenniferClient

SOURCE = jf.SOURCE
SOURCE_LABEL = jf.SOURCE_LABEL
# X-View 기본 데이터 조회 창(1분) — 서버가 구간을 1분 단위로 나눠 부른다(§5.2(c)).
XVIEW_WINDOW_MS = 60_000


def _ids(instance_ids: list[int] | None) -> dict[str, str]:
    return {"instance_id": ",".join(str(i) for i in instance_ids)} if instance_ids else {}


class JenniferApi:
    def __init__(self, client: JenniferClient) -> None:
        self.client = client

    @property
    def configured(self) -> bool:
        return self.client.configured

    @property
    def calls_total(self) -> int:
        return self.client.calls_total

    async def domains(self) -> list[dict[str, Any]]:
        body = await self.client.get_json("/api/domain")
        return [jf.parse_domain(d) for d in jf.result_list(body)]

    async def instances(self, domain_id: int, domain_name: str = "") -> list[dict[str, Any]]:
        body = await self.client.get_json("/api/instance", {"domain_id": domain_id})
        return [jf.parse_instance(r, domain_id, domain_name) for r in jf.result_list(body)]

    async def realtime(self, domain_id: int) -> list[dict[str, Any]]:
        body = await self.client.get_json("/api/realtime/instance", {"domain_id": domain_id})
        return [jf.parse_realtime(r) for r in jf.result_list(body)]

    async def active_services(
        self, domain_id: int, instance_ids: list[int] | None
    ) -> list[dict[str, Any]]:
        body = await self.client.get_json(
            "/api/activeService/list", {"domain_id": domain_id, **_ids(instance_ids)}
        )
        return [jf.parse_active_service(r) for r in jf.result_list(body)]

    async def transactions(
        self, domain_id: int, instance_ids: list[int] | None, start_ms: int, end_ms: int
    ) -> list[dict[str, Any]]:
        body = await self.client.get_json(
            "/api/transaction/time",
            {
                "domain_id": domain_id,
                "start_time": start_ms,
                "end_time": end_ms,
                **_ids(instance_ids),
            },
        )
        return [jf.parse_transaction(r) for r in jf.result_list(body)]

    async def events(
        self, domain_id: int, instance_ids: list[int] | None, start_ms: int, end_ms: int
    ) -> list[dict[str, Any]]:
        body = await self.client.get_json(
            "/api/dbsearch/event",
            {
                "domain_id": domain_id,
                "start_time": start_ms,
                "end_time": end_ms,
                **_ids(instance_ids),
            },
        )
        return [jf.parse_event(r) for r in jf.result_list(body)]

    async def errors(
        self, domain_id: int, instance_ids: list[int] | None, start_ms: int, end_ms: int
    ) -> list[dict[str, Any]]:
        body = await self.client.get_json(
            "/api/dbsearch/error",
            {
                "domain_id": domain_id,
                "start_time": start_ms,
                "end_time": end_ms,
                **_ids(instance_ids),
            },
        )
        return [jf.parse_error(r) for r in jf.result_list(body)]

    async def metric_series(
        self,
        domain_id: int,
        instance_id: int,
        neutral_metric: str,
        interval_minute: int,
        start_ms: int,
        end_ms: int,
    ) -> list[dict[str, Any]] | None:
        """중립 지표 이름의 구간 시계열. 식별자 매핑이 없는 지표는 None(조회하지 않는다)."""
        metric = jf.METRIC_FIELDS.get(neutral_metric, (None, None))[1]
        if metric is None:
            return None
        body = await self.client.get_json(
            "/api/dbmetrics/instance",
            {
                "domain_id": domain_id,
                "instance_id": instance_id,
                "interval_minute": interval_minute,
                "metrics": metric,
                "start_time": start_ms,
                "end_time": end_ms,
            },
        )
        return [jf.parse_metric_point(r) for r in jf.result_list(body)]

    async def application_status(
        self,
        domain_id: int,
        instance_ids: list[int] | None,
        start_ms: int,
        end_ms: int,
        max_row: int,
    ) -> list[dict[str, Any]]:
        """시 단위 애플리케이션 통계 — 호출자가 시 경계로 맞춘 구간을 넘긴다(§0.9 판단 ⑤)."""
        body = await self.client.get_json(
            "/api/status/application",
            {
                "domain_id": domain_id,
                "start_time": start_ms,
                "end_time": end_ms,
                "max_row": max_row,
                **_ids(instance_ids),
            },
        )
        return [jf.parse_application_status(r) for r in jf.result_list(body)]

    async def transaction_detail(
        self, domain_id: int, txid: int, time_ms: int
    ) -> dict[str, Any] | None:
        body = await self.client.get_json(
            "/api/transaction/txid", {"domain_id": domain_id, "txid": txid, "time": time_ms}
        )
        rows = jf.result_list(body)
        if rows:
            return jf.parse_transaction(rows[0])
        if isinstance(body, dict) and isinstance(body.get("result"), dict):
            return jf.parse_transaction(body["result"])
        return None

    async def profile_text(self, domain_id: int, txid: int, time_ms: int) -> str:
        return await self.client.get_text(
            "/api/transaction/profile.txt", {"domain_id": domain_id, "txid": txid, "time": time_ms}
        )

    async def transaction_sqls(
        self, domain_id: int, txid: int, time_ms: int, limit: int
    ) -> list[str]:
        body: Any = await self.client.get_json(
            "/api/transaction/sql", {"domain_id": domain_id, "txid": txid, "time": time_ms}
        )
        return jf.extract_sql_texts(body, limit)

    def event_signal(self, event_type: str) -> tuple[str, str, str] | None:
        return jf.event_signal(event_type)
