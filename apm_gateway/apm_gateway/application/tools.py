"""`apm_*` 도구 코어 14종 + `gateway_health` (plans/87 §5.2(c) · SPEC-apm-gateway §3 · plans/134
W1·W2 — W2에서 통계·지표·변경 감지 3종 · W5·W6에서 GUID 추적·변경 전후·기간 비교 3종).

반환은 dict(정상 `{rows, row_count, …}` / 오류 `{error, reason}`)이고, MCP 등록·감사·JSON 직렬화는
인터페이스 계층이 맡는다. 원칙:

- 대상 인스턴스는 **결정적 정합**으로만 정한다(LLM이 인스턴스명을 추측하지 않는다) — 정합된 전부.
  hostname 대신 인스턴스 이름 **정확 일치**(`instance_name`)로도 정한다 — 부분 이름은
  `apm_instance_map(query=…)` 검색 몫이고 유사 후보는 자동 채택하지 않는다(plans/130 N-1·N-3).
- 마스킹은 서버측에서 한다(원문은 반환·감사 어디에도 남기지 않는다). 사람·계정 식별자(`user_id`·
  `client_id`)는 `mask_identifier`, IP는 `mask_ip`, URL은 `mask_url`, 자유 텍스트는 `mask_text`.
  행 텍스트 칸(메시지·실행 텍스트·상태 메시지·설명)은 **마스킹한 전문**을 싣는다(plans/134 W2 —
  화면·LLM 입력 절단은 본체 책임). 오류 사유(`reason`)만 길이를 자른다.
- **자체 상한 없음**(D-296 ④ · plans/134 W1 N-4) — 기간·대상·건수·추세 인스턴스·SQL 수를 코드가
  줄이지 않는다. 상위 N은 사용자가 정한 `n`(1 이상 · 기본은 도구별 종전 값)이고 `full=true`면
  전체 행이다. 남는 한계는 제니퍼 쪽 사실(X-View 1분 창 · `/api/status/*` 시 단위 · 단위 미확인
  필드)뿐이고 `[한계]`로 드러낸다. 큰 결과는 W0-B 작업·스풀이 `artifact`로 넘긴다.
- **전체 파일 전용** 칸(`instance_oid` — 내부 OID)은 봉투의 `_file_only` 목록으로 표시한다. 작업
  관리자가 결과 파일에는 남기고 화면용 `rows`(LLM 입력)에서는 뺀다.
- 침묵 폴백 금지 — 일부 호출 실패·과거 시점 등은 `limits`에 `[한계]`로 적고, 전부 실패면 오류를
  돌려준다. 일부 단위(소스·도메인·창 조각·호출) 조회 실패가 있으면 봉투 `partial: true`를 세운다.
- 호출 계획(`expect_calls`)을 신고해 작업의 진행·비용 예측에 쓴다(작업 밖이면 아무 일도 없다).
- WAS 판정은 `domain.signals` 한 곳에서만 한다(`was_signals`). 비교 계산(가중 평균·비율·증감·
  원시 p95)은 `domain.analysis` 한 곳에서만 한다(plans/134 W6).
- 프로파일 예산(조사당 `APM_PROFILE_CALLS_PER_INVESTIGATION`)은 호출 주체로 가른다 — 채팅 주체
  `chat`(전송 토큰으로 정해진다 · 인자로 얻을 수 없다)은 쓰지 않고, 그 밖 주체는 (주체,
  `investigation_id` → `owner` → `_unspecified`) 칸마다 센다(plans/134 W5 · D-296 ④).
- 제니퍼 소스가 여럿이면(plans/87 J8 · D-287) 인스턴스는 (`source_id`, `domain_id`, `instance_id`)로
  식별하고 호출은 그 소스 서버로만 보낸다. 봉투 `source_kind`·`source`는 그대로다(소비자 인식 키).

구조(plans/178 W2·W3 · D-337): 공유 상태·봉투·해석·소스 호출 공용은 코어 `ApmCore`(`apm_core`)이고,
도구군 5개(`InstanceTools`·`HealthTools`·`TransactionTools`·`StatsTools`·`CompareTools`)는 코어를
받아 합성한다. `ApmTools`는 코어를 이어받은 Facade로 도구군 메서드를 형식 그대로 위임하고
(`_Forward`), 도구군으로 옮긴 모듈 수준 이름 중 테스트가 이 경로로 쓰는 것만 옛 이름으로 다시
노출한다. `apm_app_health`는 방문·호출 수 노트의 정의가 이 파일 하나에만 있어야 하므로(소스 가드
seam) Facade에 남는다.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Concatenate, Generic, ParamSpec, TypeVar, overload

from apm_gateway.application.apm_core import SOURCE_KIND as SOURCE_KIND
from apm_gateway.application.apm_core import ApmCore
from apm_gateway.application.business_map import cache_fresh
from apm_gateway.application.compare_tools import CompareTools
from apm_gateway.application.health_tools import HealthTools
from apm_gateway.application.instance_tools import InstanceTools
from apm_gateway.application.masking import mask_text
from apm_gateway.application.name_search import QUERY_MAX as QUERY_MAX
from apm_gateway.application.name_search import domain_order
from apm_gateway.application.name_search import search_query as search_query
from apm_gateway.application.period_stats import period_stats, stats_delta, sum_stats
from apm_gateway.application.sources import SourceSet
from apm_gateway.application.stats_tools import StatsTools
from apm_gateway.application.tool_common import (
    ABSOLUTE_MAX_MS,
    FILE_ONLY_COLUMNS,
    HOURLY_AFTER_MINUTES,
    InstKey,
    Limits,
    check_n,
    hour_ceil,
    hour_floor,
    inst_key,
    inst_meta,
    is_given,
    nonneg_int,
    positive_int,
    tag_records,
    target_text,
)
from apm_gateway.application.tool_common import Window as Window
from apm_gateway.application.tool_common import limit_notice as limit_notice
from apm_gateway.application.transaction_tools import CHAT_PRINCIPAL as CHAT_PRINCIPAL
from apm_gateway.application.transaction_tools import KEY_ARG_NOTE as KEY_ARG_NOTE
from apm_gateway.application.transaction_tools import TransactionTools
from apm_gateway.config import GatewayConfig
from apm_gateway.domain import signals as sig
from apm_gateway.domain.errors import SOURCE_UNAVAILABLE, ApmError

# 옛 이름 별칭 — D-337 ③ 1사이클(plans/178) · 테스트가 이 경로로 쓰는 것만 남긴다(plans/166 단계 8)
_ABSOLUTE_MAX_MS = ABSOLUTE_MAX_MS
_Limits = Limits
_positive_int = positive_int
_nonneg_int = nonneg_int
_check_n = check_n
_hour_floor = hour_floor
_hour_ceil = hour_ceil
_given = is_given
_target_text = target_text
_domain_order = domain_order
_fresh = cache_fresh

P = ParamSpec("P")
R = TypeVar("R")


class _Forward(Generic[P, R]):
    """도구군 메서드 위임 서술자(plans/178 W2) — 인스턴스에서 읽으면 도구군의 바운드 메서드를
    그대로 돌려준다(시그니처·코루틴 여부 보존 · 기본값을 복제하지 않는다). 클래스에서 읽으면 도구군
    함수다."""

    def __init__(self, group: str, method: Callable[Concatenate[Any, P], R]) -> None:
        self.group = group
        self.name = method.__name__
        self.method = method

    @overload
    def __get__(self, obj: None, owner: type) -> Callable[Concatenate[Any, P], R]: ...
    @overload
    def __get__(self, obj: object, owner: type) -> Callable[P, R]: ...
    def __get__(self, obj: object | None, owner: type) -> Any:
        if obj is None:
            return self.method
        return getattr(getattr(obj, self.group), self.name)


# 방문·호출 수 칸을 싣는 보기 공용(인스턴스 건강·서비스 현재값·전 대상 순위 — 사본 금지)
VISIT_HIT_NOTE = (
    "[한계] 방문·호출 수(visit_day·visit_hour·hit_day·hit_hour)는 단위·「하루」 경계(자정 기준인지"
    " 24시간 이동인지)가 미확인이다(W10)"
)


class ApmTools(ApmCore):
    """도구 코어. 인스턴스 하나를 프로세스에서 공유한다."""

    def __init__(
        self,
        sources: SourceSet,
        cfg: GatewayConfig,
        *,
        clock: Callable[[], float] = time.time,
        poller_status: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(sources, cfg, clock=clock, poller_status=poller_status)
        # 도구군(plans/178 W2·W3) — 코어(self)를 받아 합성한다
        self._instance_tools = InstanceTools(self)
        self._health_tools = HealthTools(self)
        self._transaction_tools = TransactionTools(self)
        self._stats_tools = StatsTools(self)
        self._compare_tools = CompareTools(self)

    # ── 도구군 위임(plans/178 W2·W3) ──────────────────────────
    apm_instance_map = _Forward("_instance_tools", InstanceTools.apm_instance_map)
    apm_runtime_health = _Forward("_health_tools", HealthTools.apm_runtime_health)
    apm_resource_pool = _Forward("_health_tools", HealthTools.apm_resource_pool)
    gateway_health = _Forward("_health_tools", HealthTools.gateway_health)
    apm_slow_transactions = _Forward("_transaction_tools", TransactionTools.apm_slow_transactions)
    apm_active_services = _Forward("_transaction_tools", TransactionTools.apm_active_services)
    apm_events = _Forward("_transaction_tools", TransactionTools.apm_events)
    apm_transaction_profile = _Forward(
        "_transaction_tools", TransactionTools.apm_transaction_profile
    )
    apm_status_stats = _Forward("_stats_tools", StatsTools.apm_status_stats)
    apm_metrics = _Forward("_stats_tools", StatsTools.apm_metrics)
    apm_source_changes = _Forward("_compare_tools", CompareTools.apm_source_changes)
    apm_transaction_trace = _Forward("_compare_tools", CompareTools.apm_transaction_trace)
    apm_change_impact = _Forward("_compare_tools", CompareTools.apm_change_impact)
    apm_period_compare = _Forward("_compare_tools", CompareTools.apm_period_compare)

    # 옛 이름으로 다시 노출한다(D-337 ③ — 테스트가 클래스에서 부른다). 구간 합계·증감은
    # `period_stats` 모듈, GUID 검증은 `CompareTools.guid`
    _guid = staticmethod(CompareTools.guid)
    _period_stats = staticmethod(period_stats)
    _sum_stats = staticmethod(sum_stats)
    _stats_delta = staticmethod(stats_delta)

    # ── 도구 8종(W1까지) ────────────────────────────────────

    async def apm_app_health(
        self,
        hostname: str | None = None,
        instance_id: int | None = None,
        reference_time: str | None = None,
        lookback_minutes: int | None = None,
        source_ids: list[str] | None = None,
        instance_name: str | None = None,
    ) -> dict[str, Any]:
        tool = "apm_app_health"
        res = await self.resolve(hostname, instance_id, source_ids, instance_name)
        window = self.window(reference_time, lookback_minutes, default_minutes=None)
        limits = Limits()
        current: dict[InstKey, dict[str, Any]] = {}
        if window is None or window.end_is_now:
            current = await self.realtime(res, limits)
            if current:
                limits.append(VISIT_HIT_NOTE)
        else:
            limits.append(
                "[한계] 과거 기준시각 — 실시간 스냅샷 생략(현재값은 사건 시점 증거가 아니다)"
            )
        per_inst_tx: dict[InstKey, list[dict[str, Any]]] = {}
        xview_ok = False
        hourly = None
        if window is not None:
            txs, xview_ok = await self.xview(res, window, limits)
            for tx in txs:
                per_inst_tx.setdefault(inst_key(tx), []).append(tx)
            if window.minutes > HOURLY_AFTER_MINUTES:
                hourly = await self.hourly(res, window, limits)
        if not current and not xview_ok and hourly is None:
            raise ApmError(SOURCE_UNAVAILABLE, "; ".join(limits) or "APM 데이터 조회 실패")
        rows: list[dict[str, Any]] = []
        signals: list[dict[str, Any]] = []
        for inst in res.instances:
            key = inst_key(inst)
            cur = current.get(key)
            row = inst_meta(inst)
            if cur:
                for field_name in (
                    "response_time_avg_ms",
                    "tps",
                    "active_services",
                    "bad_response_active_services",
                    "reject_rate",
                    "concurrent_users",
                    "arrival_rate",
                    "visit_day",
                    "visit_hour",
                    "hit_day",
                    "hit_hour",
                    "active_range_count_0",
                    "active_range_count_1",
                    "active_range_count_2",
                    "active_range_count_3",
                    "service_rate_by_range",
                    "instance_oid",
                ):
                    row[field_name] = cur.get(field_name)
                row["instance_description"] = mask_text(
                    cur.get("instance_description", ""), limit=None
                )
            stats = self.window_stats(per_inst_tx.get(key, [])) if window is not None else None
            if stats is not None:
                row["window"] = stats
            rows.append(row)
            signals += tag_records(sig.judge_app(cur, stats, self.th, instance_id=key[1]), key[0])
        extra: dict[str, Any] = {"hourly": hourly} if hourly is not None else {}
        return self.ok(
            tool,
            rows,
            resolution=res,
            window=window,
            was_signals=signals,
            limits=limits,
            partial=limits.partial,
            file_only=FILE_ONLY_COLUMNS,
            **extra,
        )
