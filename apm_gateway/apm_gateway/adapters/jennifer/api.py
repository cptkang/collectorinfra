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
from apm_gateway.domain.errors import API_ERROR, ApmError

SOURCE = jf.SOURCE
SOURCE_LABEL = jf.SOURCE_LABEL
# X-View 기본 데이터 조회 창(1분) — 서버가 구간을 1분 단위로 나눠 부른다(§5.2(c)).
XVIEW_WINDOW_MS = 60_000
# 소스 변경 이력 1회 조회 창 상한(25시간 이하 — v2 매뉴얼 `deploy.md`) — 호출자가 조각으로 나눈다.
CHANGES_WINDOW_MS = 25 * 3_600_000
# 통계 종류(중립) → 경로(plans/134 W2 N-5)
_STATUS_PATHS: dict[str, str] = {
    "application": "/api/status/application",
    "sql": "/api/status/sql",
    "external_call": "/api/status/external_call",
}
STATUS_KINDS: tuple[str, ...] = tuple(_STATUS_PATHS)


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

    async def businesses(self, domain_id: int) -> list[dict[str, Any]]:
        """도메인의 업무 정의 목록(id·이름·설명 — plans/130 N-2 B2). 정의에 인스턴스 목록은 없다 —
        처리 인스턴스는 거래·액티브 서비스의 업무 id로 역추적한다. 모양 위반은 `apm_api_error`다
        (0건으로 강등하지 않는다 — GUID 조회 선례)."""
        body = await self.client.get_json("/api/business", {"domain_id": domain_id})
        if not (isinstance(body, dict) and isinstance(body.get("result"), list)):
            raise ApmError(
                API_ERROR, "업무 목록 응답 모양이 예상과 다르다({result: [...]} 봉투가 아님)"
            )
        return [jf.parse_business(r) for r in jf.result_list(body)]

    async def realtime(
        self, domain_id: int, instance_ids: list[int] | None = None
    ) -> list[dict[str, Any]]:
        """실시간 인스턴스 — 대상이 정해졌으면 그 인스턴스만 묻는다(COV-RT-INSTANCE
        `instance_id`)."""
        body = await self.client.get_json(
            "/api/realtime/instance", {"domain_id": domain_id, **_ids(instance_ids)}
        )
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
        self,
        domain_id: int,
        instance_ids: list[int] | None,
        start_ms: int,
        end_ms: int,
        level: str | None = None,
    ) -> list[dict[str, Any]]:
        """이벤트 — `level`(중립 소문자)을 주면 API `level`로 넘긴다(대문자 — 의미는 W10 확인 ·
        호출자가 응답을 다시 거른다)."""
        body = await self.client.get_json(
            "/api/dbsearch/event",
            {
                "domain_id": domain_id,
                "start_time": start_ms,
                "end_time": end_ms,
                **_ids(instance_ids),
                **({"level": level.upper()} if level else {}),
            },
        )
        return [jf.parse_event(r) for r in jf.result_list(body)]

    async def errors(
        self,
        domain_id: int,
        instance_ids: list[int] | None,
        start_ms: int,
        end_ms: int,
        error_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """오류 기록 — `error_type`(대문자)을 주면 그 유형만(스펙 *should be capitalized*)."""
        body = await self.client.get_json(
            "/api/dbsearch/error",
            {
                "domain_id": domain_id,
                "start_time": start_ms,
                "end_time": end_ms,
                **_ids(instance_ids),
                **({"error_type": error_type.upper()} if error_type else {}),
            },
        )
        return [jf.parse_error(r) for r in jf.result_list(body)]

    @staticmethod
    def metric_id(neutral_metric: str) -> str | None:
        """중립 지표 이름 → 구간 시계열 지표 식별자(매핑 없으면 None)."""
        return jf.METRIC_FIELDS.get(neutral_metric, (None, None))[1]

    @staticmethod
    def neutral_metrics() -> dict[str, str]:
        """중립 지표 이름 → 구간 시계열 식별자(매핑이 있는 것만)."""
        return {n: m for n, (_field, m) in jf.METRIC_FIELDS.items() if m}

    @staticmethod
    def neutral_metric(metric_id: str) -> str | None:
        """구간 시계열 지표 식별자 → 중립 지표 이름(없으면 None)."""
        for neutral, (_field, metric) in jf.METRIC_FIELDS.items():
            if metric == metric_id:
                return neutral
        return None

    async def metric_catalog(self) -> tuple[dict[str, list[str]], list[str]]:
        """지표 카탈로그 (`{scope: [지표 식별자…]}`, 모양 위반 scope 목록)(scope = domain·instance·
        business·application·sql·external_call). 응답 모양이 다르면(정상 군 0개 포함)
        `apm_api_error`(빈 카탈로그로 강등하지 않는다)."""
        catalog = jf.parse_metric_catalog(await self.client.get_json("/api/metrics"))
        if catalog is None:
            raise ApmError(
                API_ERROR, "지표 카탈로그 응답 모양이 예상과 다르다(result가 지표 군 객체가 아님)"
            )
        return catalog

    async def instance_metric_series(
        self,
        domain_id: int,
        instance_id: int,
        metric_id: str,
        interval_minute: int,
        start_ms: int,
        end_ms: int,
    ) -> list[dict[str, Any]]:
        """인스턴스 지표 시계열(지표 식별자 그대로 — 호출자가 카탈로그로 검증 · 1지표/호출)."""
        body = await self.client.get_json(
            "/api/dbmetrics/instance",
            {
                "domain_id": domain_id,
                "instance_id": instance_id,
                "interval_minute": interval_minute,
                "metrics": metric_id,
                "start_time": start_ms,
                "end_time": end_ms,
            },
        )
        return [jf.parse_metric_point(r) for r in jf.result_list(body)]

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
        metric = self.metric_id(neutral_metric)
        if metric is None:
            return None
        return await self.instance_metric_series(
            domain_id, instance_id, metric, interval_minute, start_ms, end_ms
        )

    async def status_stats(
        self,
        kind: str,
        domain_id: int,
        instance_ids: list[int] | None,
        start_ms: int,
        end_ms: int,
        *,
        sort_by: str | None = None,
        max_row: int | None = None,
        application_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """시 단위 통계(`kind` = application·sql·external_call) — 호출자가 시 경계로 맞춘 구간을
        넘긴다(§0.9 판단 ⑤). 선택 인자는 준 것만 싣는다(`max_row`가 없으면 서버 기본 행 수 —
        미공개 · W10). application 행은 25필드, sql·external_call 행은 7필드."""
        body = await self.client.get_json(
            _STATUS_PATHS[kind],
            {
                "domain_id": domain_id,
                "start_time": start_ms,
                "end_time": end_ms,
                **({"sort_by_metrics": sort_by} if sort_by else {}),
                **({"max_row": max_row} if max_row is not None else {}),
                **({"application_name": application_name} if application_name else {}),
                **_ids(instance_ids),
            },
        )
        parse = jf.parse_application_status if kind == "application" else jf.parse_call_status
        return [parse(r) for r in jf.result_list(body)]

    async def application_status(
        self,
        domain_id: int,
        instance_ids: list[int] | None,
        start_ms: int,
        end_ms: int,
        max_row: int | None = None,
    ) -> list[dict[str, Any]]:
        """시 단위 애플리케이션 통계(`status_stats(kind=application)`)."""
        return await self.status_stats(
            "application", domain_id, instance_ids, start_ms, end_ms, max_row=max_row
        )

    @staticmethod
    def status_sort_field(kind: str, sort_by: str | None) -> str | None:
        """정렬 기준 이름 → 행 칸(여러 묶음을 합쳐 다시 정렬할 때 · 모르면 None)."""
        return jf.status_sort_field(kind, sort_by)

    async def source_changes(
        self, domain_id: int, start_ms: int, end_ms: int
    ) -> list[dict[str, Any]]:
        """소스코드(리소스) 변경 감지 이력 — 구간은 `CHANGES_WINDOW_MS` 이하(호출자가 나눈다).
        v2 응답은 맨 배열이다(COV E-18) — 배열이 아니면 `apm_api_error`(빈 목록으로 강등하지
        않는다)."""
        body = await self.client.get_json(
            "/api-v2/deploy/{domainId}",
            {"startTime": start_ms, "endTime": end_ms},
            {"domainId": domain_id},
        )
        rows = jf.bare_list(body)
        if rows is None:
            raise ApmError(
                API_ERROR,
                "소스 변경 이력 응답 모양이 예상과 다르다(배열이 아니거나 항목이 객체가 아님)",
            )
        return [jf.parse_source_change(r) for r in rows]

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
        self,
        domain_id: int,
        txid: int,
        time_ms: int,
        limit: int | None = None,
        *,
        profile_no: int | None = None,
        include_param_key: bool | None = None,
    ) -> list[tuple[bool, str]]:
        """트랜잭션 SQL 칸 문자열 (SQL 문 칸인가, 값) — 선택 인자는 준 것만 싣는다(plans/134 W5 ·
        COV-TX-SQL). 문인지는 칸 이름으로 가른다(`fields.is_sql_statement_key` — 그 밖 칸은 바인드
        값일 수 있다). `key`는 허용목록에 있지만 의미·값 출처가 미공개라(W10) 보내지 않는다."""
        body: Any = await self.client.get_json(
            "/api/transaction/sql",
            {
                "domain_id": domain_id,
                "txid": txid,
                "time": time_ms,
                **({"profile_no": profile_no} if profile_no is not None else {}),
                **(
                    {"include_param_key": "true" if include_param_key else "false"}
                    if include_param_key is not None
                    else {}
                ),
            },
        )
        return [(jf.is_sql_statement_key(k), v) for k, v in jf.extract_sql_texts(body, limit)]

    async def transactions_by_guid(
        self, domain_id: int, guid: str, start_ms: int, end_ms: int
    ) -> list[dict[str, Any]]:
        """GUID가 같은 거래(도메인 1개 · plans/134 W5 N-13 · COV-TX-GUID) — `TransactionData`
        모양이라 X-View와 같은 파서를 쓴다(실응답 모양은 W10)."""
        body = await self.client.get_json(
            "/api/transaction/guid",
            {"domain_id": domain_id, "guid": guid, "start_time": start_ms, "end_time": end_ms},
        )
        if not (isinstance(body, dict) and isinstance(body.get("result"), list)):
            # 모양 위반을 0건(「찾지 못했다」)으로 강등하지 않는다(W2 `bare_list` 선례 · VG-2)
            raise ApmError(
                API_ERROR, "GUID 거래 응답 모양이 예상과 다르다({result: [...]} 봉투가 아님)"
            )
        return [jf.parse_transaction(r) for r in jf.result_list(body)]

    def event_signal(self, event_type: str) -> tuple[str, str, str] | None:
        return jf.event_signal(event_type)

    @staticmethod
    def same_error_type(actual: str, wanted: str) -> bool:
        return jf.same_error_type(actual, wanted)

    @staticmethod
    def error_type_variants(error_type: str) -> list[str]:
        """오류 유형 API 표기 후보(물어볼 순서 · 최대 3개)."""
        return jf.error_type_variants(error_type)
