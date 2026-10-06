"""`apm_*` 도구 코어 14종 + `gateway_health` (plans/87 §5.2(c) · SPEC-apm-gateway §3 · plans/134
W1·W2 — W2에서 통계·지표·변경 감지 3종 · W5·W6에서 GUID 추적·변경 전후·기간 비교 3종).

반환은 dict(정상 `{rows, row_count, …}` / 오류 `{error, reason}`)이고, MCP 등록·감사·JSON 직렬화는
인터페이스 계층이 맡는다. 원칙:

- 대상 인스턴스는 **결정적 정합**으로만 정한다(LLM이 인스턴스명을 추측하지 않는다) — 정합된 전부.
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
"""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import json
import logging
import math
import re
import time
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from apm_gateway.adapters.jennifer.allowlist import ALLOWED
from apm_gateway.adapters.jennifer.api import (
    CHANGES_WINDOW_MS,
    SOURCE,
    STATUS_KINDS,
    XVIEW_WINDOW_MS,
    JenniferApi,
)
from apm_gateway.application.jobs import (
    FILE_ONLY_KEY,
    MASKED_KEY,
    TEXT_PARTS_KEY,
    UNRESOLVED_KEY,
)
from apm_gateway.application.masking import (
    mask_identifier,
    mask_ip,
    mask_sql,
    mask_text,
    mask_url,
)
from apm_gateway.application.resolver import SHORT_CACHE_SECONDS, Resolution
from apm_gateway.application.sources import (
    STATUS_OK,
    STATUS_UNAVAILABLE,
    JenniferSource,
    SourceSet,
    partial_of,
)
from apm_gateway.config import GatewayConfig
from apm_gateway.domain import analysis as an
from apm_gateway.domain import jobs as js
from apm_gateway.domain import signals as sig
from apm_gateway.domain.call_context import expect_calls
from apm_gateway.domain.errors import (
    API_ERROR,
    CONTRACT_VIOLATION,
    INVALID_ARGUMENT,
    PROFILE_REF_MISMATCH,
    RATE_LIMITED,
    SOURCE_UNAVAILABLE,
    ApmError,
)
from apm_gateway.domain.events import level_rank

logger = logging.getLogger(__name__)

SOURCE_KIND = "apm_api"
EVENTS_DEFAULT_MINUTES = 30
SLOW_TX_DEFAULT_MINUTES = 10
HEALTH_DEFAULT_LOOKBACK = 30
# 창이 이보다 길면 시 단위 애플리케이션 통계를 함께 싣는다(맥락 — 조회 상한이 아니다).
HOURLY_AFTER_MINUTES = 10
TREND_INTERVAL_MINUTE = 5
DEFAULT_N = 10
# 화면용 발췌 — 전문은 결과 파일(`artifact.text_parts` profile)
PROFILE_EXCERPT_LINES = 60
PROFILE_EXCERPT_CHARS = 4000
HOURLY_TOP_APPLICATIONS = 5
LEVELS = ("fatal", "warning", "normal")
LEVEL_MODES = ("min", "exact")
RECORDS = ("event", "error")
_ERROR_TYPE = re.compile(r"[A-Za-z0-9_]{1,64}")
FILE_ONLY_COLUMNS = ("instance_oid",)
# 프로파일 행은 트랜잭션이 중첩 칸이다(W1 검증 L-3)
PROFILE_FILE_ONLY = ("transaction.instance_oid",)
# `mask_identifier`로 가리는 칸(SPEC-coverage §4.3) — 원값이 있었으면 봉투 고지 대상(L-5)
IDENTIFIER_FIELDS = ("client_id", "user_id")
_PROFILE_BUDGET_TTL = 3600.0
_HEALTH_CACHE_SECONDS = SHORT_CACHE_SECONDS
# plans/134 W2(N-5~N-7)
DEFAULT_TREND_METRICS = ("heap_used_mb", "heap_committed_mb", "gc_time_usage_pct")
STATUS_DEFAULT_MINUTES = 60
SERIES_DEFAULT_MINUTES = 60
CHANGES_DEFAULT_MINUTES = 24 * 60
CHANGE_DETECTION_NOTE = (
    "[한계] 변경 감지(데이터 서버가 소스코드·리소스 변경을 인지한 시각) — 배포 확정 아님"
)
METRIC_MODES = ("catalog", "series")
METRIC_SCOPES = ("domain", "instance", "business", "application", "sql", "external_call")
# 정렬 기준·지표 이름은 허용값이 미공개라(COV E-05·E-06) 식별자 형식만 본다
# (값 검증은 카탈로그·서버).
_IDENT = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,127}")
_METRIC_SUGGESTIONS = 3
# plans/134 W5 — 프로파일 예산을 쓰지 않는 주체(채팅 전용 토큰 · `APM_GATEWAY_BEARER_TOKENS`의 키)
CHAT_PRINCIPAL = "chat"
_UNSPECIFIED = "_unspecified"
KEY_ARG_NOTE = "[한계] key 인자는 의미·값 출처 미확인(W10) — 보내지 않았다"
# plans/134 W5 N-13 — GUID 추적 창 기본값
TRACE_DEFAULT_MINUTES = 60
TRACE_AROUND_MINUTES = 5
GUID_MAX = 256
# plans/134 W6 A-1 — 명시 시각 상한(9999-01-01 UTC · 시 경계 올림·표시가 넘치지 않게)
_ABSOLUTE_MAX_MS = 253370764800000
# plans/134 W6 A-2 — 변경 전후 비교 폭 기본값(분) · 구간 지표(X-View 칸) · 증감을 내는 지표
CHANGE_WIDTH_MINUTES = 60
_XVIEW_SIDE_METRICS = (
    "calls",
    "tx_errors",
    "error_rate",
    "avg_response_ms",
    "p95_response_ms",
    "max_response_ms",
)
_IMPACT_METRICS = (
    "calls",
    "tx_errors",
    "error_rate",
    "avg_response_ms",
    "p95_response_ms",
    "error_records",
)
# plans/134 W6 A-1 — 기간 비교 지표 · 구간 이름
_PERIOD_METRICS = ("calls", "failures", "failure_rate", "avg_response_ms", "max_response_ms")
_PERIOD_LABELS = {"current": "현재", "baseline": "기준"}


@dataclass
class _Catalog:
    """소스 1개의 지표 카탈로그 캐시(TTL) — 다시 읽었을 때 지문이 바뀌었으면 `change_note`."""

    fetched_at: float
    scopes: dict[str, list[str]]
    fingerprint: str
    change_note: str | None = None
    # 모양 위반 군(목록 아님·문자열 아닌 항목) — 빈 군이 아니라 「검증 불가」(W2V-G4)
    invalid: tuple[str, ...] = ()


def _now_iso(clock: Callable[[], float]) -> str:
    return datetime.fromtimestamp(clock()).astimezone().isoformat(timespec="seconds")


class Window:
    def __init__(self, start_ms: int, end_ms: int, end_is_now: bool, tz: str) -> None:
        self.start_ms = start_ms
        self.end_ms = end_ms
        self.end_is_now = end_is_now
        self.tz = tz

    @property
    def minutes(self) -> int:
        return max(1, round((self.end_ms - self.start_ms) / 60_000))

    def as_dict(self) -> dict[str, Any]:
        zone = ZoneInfo(self.tz)
        return {
            "start": datetime.fromtimestamp(self.start_ms / 1000, zone).isoformat(
                timespec="seconds"
            ),
            "end": datetime.fromtimestamp(self.end_ms / 1000, zone).isoformat(timespec="seconds"),
            "minutes": self.minutes,
        }


class _Limits(list[str]):
    """`[한계]` 목록 — 일부 단위 조회 실패를 `fail`로 적으면 봉투 `partial`이 선다(문구 그대로).
    선택 조건을 빼거나 바꿔 조회했으면 `unresolve`로 사용자용 한 줄을 남긴다(봉투 고지
    `apm_unresolved_condition` — 값은 지표·정렬 이름만)."""

    partial: bool = False

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self.unresolved: list[str] = []

    def fail(self, text: str) -> None:
        self.partial = True
        self.append(text)

    def unresolve(self, text: str) -> None:
        if text not in self.unresolved:
            self.unresolved.append(text)


def _positive_int(value: Any, name: str, default: int) -> int:
    """양의 정수 인자(비면 기본값) — 1 미만·정수 아님은 `invalid_argument`."""
    if value is None:
        return default
    if isinstance(value, int) and not isinstance(value, bool):
        number = value
    elif isinstance(value, str) and value.strip().isascii() and value.strip().isdigit():
        number = int(value.strip())  # ASCII 숫자만(위첨자·전각 숫자는 int()가 못 읽는다 — W2V-G6)
    else:
        number = 0
    if number < 1:
        raise ApmError(INVALID_ARGUMENT, f"{name}는 양의 정수여야 한다: {value!r}")
    return number


def _nonneg_int(value: Any, name: str) -> int:
    """0 이상의 정수 인자(프로파일 번호 등) — 그 밖은 `invalid_argument`."""
    if isinstance(value, int) and not isinstance(value, bool):
        number = value
    elif isinstance(value, str) and value.strip().isascii() and value.strip().isdigit():
        number = int(value.strip())
    else:
        number = -1
    if number < 0:
        raise ApmError(INVALID_ARGUMENT, f"{name}는 0 이상의 정수여야 한다: {value!r}")
    return number


def _identifier(value: Any, name: str) -> str | None:
    """식별자 형식 인자(정렬 기준 등 — 허용값 미공개라 형식만 본다). 비면 None."""
    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    if not _IDENT.fullmatch(text):
        raise ApmError(
            INVALID_ARGUMENT, f"{name}는 영문으로 시작하는 영문·숫자·밑줄이어야 한다: {value!r}"
        )
    return text


def _fingerprint(scopes: dict[str, list[str]]) -> str:
    raw = json.dumps(scopes, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _check_n(n: Any, default: int | None = DEFAULT_N) -> int | None:
    """상위 N — 1 이상이면 상한 없음(SPEC §2.1). 없으면 도구별 기본(None = 전부)."""
    if n is None:
        return default
    value = int(n)
    if value < 1:
        raise ApmError(INVALID_ARGUMENT, f"n은 1 이상이어야 한다: {n}")
    return value


def _select(rows: list[Any], n: int | None, full: bool) -> list[Any]:
    """정렬된 행에서 화면 행을 고른다 — `full`이면 전부, 아니면 앞 `n`(None = 전부)."""
    return list(rows) if full or n is None else rows[:n]


InstKey = tuple[str, int]


def _key(rec: dict[str, Any]) -> InstKey:
    """인스턴스 식별 키 — 도메인·인스턴스 id는 소스마다 따로 매겨 겹칠 수 있다(S-4)."""
    return rec["source_id"], rec["instance_id"]


def _group(resolution: Resolution) -> dict[tuple[str, int], list[int]]:
    """호출 묶음 = (소스, 도메인) — 호출은 그 소스 서버로만 나간다."""
    groups: dict[tuple[str, int], list[int]] = {}
    for inst in resolution.instances:
        groups.setdefault((inst["source_id"], inst["domain_id"]), []).append(inst["instance_id"])
    return groups


def _tag(records: list[dict[str, Any]], source_id: str, domain_id: int | None = None) -> list[
    dict[str, Any]
]:
    """소스 id를 붙인다 — 응답에 도메인 id가 없으면 호출 묶음의 도메인으로 채운다."""
    out = []
    for r in records:
        rec = {**r, "source_id": source_id}
        if domain_id is not None and rec.get("domain_id") is None:
            rec["domain_id"] = domain_id
        out.append(rec)
    return out


def _inst_meta(inst: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_id": inst["source_id"],
        "instance_id": inst["instance_id"],
        "instance_name": inst["instance_name"],
        "domain_id": inst["domain_id"],
    }


def _hour_floor(ms: int) -> int:
    return ms - ms % 3_600_000


def _hour_ceil(ms: int) -> int:
    floor = _hour_floor(ms)
    return floor if floor == ms else floor + 3_600_000


def _full_text(text: str) -> str:
    """원문 텍스트 전문(마스킹본) — 줄마다 자르지 않고 가린다."""
    return "\n".join(mask_text(line, limit=max(len(line), 1)) for line in text.splitlines())


def _tx_fields(tx: dict[str, Any]) -> dict[str, Any]:
    """트랜잭션 레코드(X-View·상세) → 반환 칸(마스킹 · 식별자 가림)."""
    return {
        "domain_id": tx.get("domain_id"),
        "domain_name": tx.get("domain_name", ""),
        "instance_id": tx.get("instance_id"),
        "instance_name": tx.get("instance_name", ""),
        "instance_oid": tx.get("instance_oid"),
        "application": mask_url(tx.get("application", "")),
        "txid": tx.get("txid", ""),
        "guid": tx.get("guid", ""),
        "response_time_ms": tx.get("response_time_ms"),
        "cpu_ms": tx.get("cpu_ms"),
        "sql_ms": tx.get("sql_ms"),
        "fetch_ms": tx.get("fetch_ms"),
        "external_ms": tx.get("external_ms"),
        "network_ms": tx.get("network_ms"),
        "frontend_ms": tx.get("frontend_ms"),
        "sql_count": tx.get("sql_count"),
        "fetch_count": tx.get("fetch_count"),
        "external_call_count": tx.get("external_call_count"),
        "error_type": tx.get("error_type", ""),
        "start_time_ms": tx.get("start_time_ms"),
        "end_time_ms": tx.get("end_time_ms"),
        "collect_time_ms": tx.get("collect_time_ms"),
        "client_ip": mask_ip(tx.get("client_ip", "")),
        "client_id": mask_identifier(tx.get("client_id", "")),
        "user_id": mask_identifier(tx.get("user_id", "")),
        "is_async": tx.get("is_async"),
        "link_root": tx.get("link_root"),
        "has_stacktrace": tx.get("has_stacktrace"),
        "business_ids": list(tx.get("business_ids") or []),
        "business_names": list(tx.get("business_names") or []),
    }


def _identifier_fields(records: Iterable[dict[str, Any] | None]) -> set[str]:
    """식별자 가림이 실제로 적용된 칸 이름(원값이 비어 있지 않은 칸)."""
    return {f for rec in records if rec for f in IDENTIFIER_FIELDS if rec.get(f)}


def _profile_ref(rec: dict[str, Any], time_key: str) -> dict[str, Any] | None:
    if not rec.get("txid"):
        return None
    return {
        "source_id": rec["source_id"],
        "domain_id": rec.get("domain_id"),
        "txid": rec["txid"],
        "time_ms": rec.get(time_key),
    }


class ApmTools:
    """도구 코어. 인스턴스 하나를 프로세스에서 공유한다."""

    def __init__(
        self,
        sources: SourceSet,
        cfg: GatewayConfig,
        *,
        clock: Callable[[], float] = time.time,
        poller_status: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self.sources = sources
        self.cfg = cfg
        self.clock = clock
        self.poller_status = poller_status
        self._th = cfg.policies.thresholds
        self._tz = cfg.runtime.timezone
        self._profile_budget: dict[tuple[str, str], tuple[int, float]] = {}
        self._health_cache: tuple[float, dict[str, Any]] | None = None
        self._catalogs: dict[str, _Catalog] = {}

    # ── 공통 ──────────────────────────────────────────────

    def ok(
        self,
        tool: str,
        rows: list[dict[str, Any]],
        *,
        resolution: Resolution | None = None,
        window: Window | None = None,
        was_signals: list[dict[str, Any]] | None = None,
        limits: list[str] | None = None,
        sources: list[dict[str, Any]] | None = None,
        partial: bool = False,
        file_only: tuple[str, ...] = (),
        text_parts: dict[str, str] | None = None,
        masked: set[str] | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "rows": rows,
            "row_count": len(rows),
            "queried_at": _now_iso(self.clock),
            "source_kind": SOURCE_KIND,
            "source": SOURCE,
            "tool": tool,
        }
        if resolution is not None:
            payload["instance_resolution"] = resolution.as_dict()
        if window is not None:
            payload["window"] = window.as_dict()
        if was_signals is not None:
            payload["was_signals"] = sig.dedupe(was_signals)
        payload["limits"] = list(
            dict.fromkeys((resolution.limits if resolution else []) + (limits or []))
        )
        if sources is None and resolution is not None:
            sources = resolution.sources
        if sources is not None:
            payload["sources"] = sources
        if partial or (resolution is not None and resolution.partial):
            payload["partial"] = True
        if file_only:
            payload[FILE_ONLY_KEY] = list(file_only)
        if text_parts:
            payload[TEXT_PARTS_KEY] = dict(text_parts)
        if masked:
            payload[MASKED_KEY] = sorted(masked)
        if isinstance(limits, _Limits) and limits.unresolved:
            payload[UNRESOLVED_KEY] = list(limits.unresolved)
        payload.update(extra)
        return payload

    @staticmethod
    def err(tool: str, code: str, reason: str) -> dict[str, Any]:
        return {
            "error": code,
            "reason": mask_text(reason, limit=400),
            "source_kind": SOURCE_KIND,
            "source": SOURCE,
            "tool": tool,
        }

    def window(
        self,
        reference_time: str | None,
        lookback_minutes: int | None,
        *,
        default_minutes: int | None,
    ) -> Window | None:
        """구간 인자 → 창. 둘 다 없고 기본값도 없으면 None(현재 시점 조회)."""
        if reference_time is None and lookback_minutes is None and default_minutes is None:
            return None
        if reference_time is not None:
            try:
                ref = datetime.fromisoformat(str(reference_time).strip())
            except ValueError as e:
                raise ApmError(
                    INVALID_ARGUMENT, f"reference_time은 ISO 8601이어야 한다: {reference_time!r}"
                ) from e
            if ref.tzinfo is None:
                ref = ref.replace(tzinfo=ZoneInfo(self._tz))
            end_ms = int(ref.timestamp() * 1000)
            end_is_now = False
        else:
            end_ms = int(self.clock() * 1000)
            end_is_now = True
        minutes = (
            lookback_minutes
            if lookback_minutes is not None
            else (default_minutes or HEALTH_DEFAULT_LOOKBACK)
        )
        minutes = int(minutes)
        if minutes < 1:
            raise ApmError(
                INVALID_ARGUMENT, f"lookback_minutes는 1 이상이어야 한다: {lookback_minutes}"
            )
        return Window(end_ms - minutes * 60_000, end_ms, end_is_now, self._tz)

    async def _resolve(
        self,
        hostname: str | None,
        instance_id: int | None = None,
        source_ids: list[str] | None = None,
    ) -> Resolution:
        self.sources.require_configured()
        if not hostname or not str(hostname).strip():
            raise ApmError(INVALID_ARGUMENT, "hostname이 비어 있음")
        return await self.sources.resolve(str(hostname).strip(), instance_id, source_ids)

    def _api(self, source_id: str) -> JenniferApi:
        return self.sources.get(source_id).api

    def _inst_label(self, key: InstKey) -> str:
        """인스턴스 문구 — 소스가 하나면 v4 그대로(id만)."""
        return str(key[1]) if len(self.sources) == 1 else f"{key[0]}:{key[1]}"

    def _ids_text(self, keys: set[InstKey]) -> str:
        if len(self.sources) == 1:
            return str(sorted(iid for _, iid in keys))
        return "[" + ", ".join(sorted(self._inst_label(k) for k in keys)) + "]"

    async def _realtime(
        self, resolution: Resolution, limits: _Limits
    ) -> dict[InstKey, dict[str, Any]]:
        """해소 인스턴스의 실시간 스냅샷((소스, 도메인)당 1호출 · 대상 인스턴스만 묻는다)."""
        wanted = {_key(i) for i in resolution.instances}
        out: dict[InstKey, dict[str, Any]] = {}
        groups = _group(resolution)
        expect_calls(len(groups))
        for (sid, domain_id), ids in groups.items():
            try:
                for rec in _tag(await self._api(sid).realtime(domain_id, ids), sid):
                    if _key(rec) in wanted:
                        out[_key(rec)] = rec
            except ApmError as e:
                if e.code == CONTRACT_VIOLATION:
                    raise
                limits.fail(
                    f"[한계] 실시간 조회 실패({self.sources.where(sid, domain_id)}): {e.code}"
                )
        missing = wanted - set(out)
        if missing:
            limits.append(f"[한계] 실시간 데이터 없는 인스턴스: {self._ids_text(missing)}")
        return out

    async def _xview(
        self, resolution: Resolution, window: Window, limits: _Limits
    ) -> tuple[list[dict[str, Any]], bool]:
        """X-View 기본 데이터를 1분 창으로 나눠 **창 전체**를 모은다(제니퍼 1분 창 제약 —
        상한 없음). (거래, 성공 여부)."""
        wanted = {_key(i) for i in resolution.instances}
        seen: set[tuple[str, str]] = set()
        txs: list[dict[str, Any]] = []
        any_ok = False
        groups = _group(resolution)
        expect_calls(math.ceil((window.end_ms - window.start_ms) / XVIEW_WINDOW_MS) * len(groups))
        for (sid, domain_id), ids in groups.items():
            t = window.start_ms
            while t < window.end_ms:
                chunk_end = min(t + XVIEW_WINDOW_MS, window.end_ms)
                query_end = chunk_end if chunk_end == window.end_ms else chunk_end - 1
                try:
                    batch = await self._api(sid).transactions(domain_id, ids, t, query_end)
                    any_ok = True
                except ApmError as e:
                    if e.code == CONTRACT_VIOLATION:
                        raise
                    limits.fail(
                        f"[한계] 트랜잭션 조회 실패({self.sources.where(sid, domain_id)}): {e.code}"
                    )
                    break
                for tx in _tag(batch, sid, domain_id):
                    key = (
                        sid,
                        tx.get("txid")
                        or f"{tx.get('instance_id')}:{tx.get('end_time_ms')}:{len(txs)}",
                    )
                    if _key(tx) in wanted and key not in seen:
                        seen.add(key)
                        txs.append(tx)
                t = chunk_end
        return txs, any_ok

    @staticmethod
    def _window_stats(txs: list[dict[str, Any]]) -> dict[str, Any]:
        times = [t["response_time_ms"] for t in txs if t.get("response_time_ms") is not None]
        errors = sum(1 for t in txs if t.get("error_type"))
        calls = len(txs)
        return {
            "calls": calls,
            "errors": errors,
            "error_rate": (errors / calls) if calls else None,
            "response_time_p50_ms": sig.percentile(times, 50),
            "response_time_p95_ms": sig.percentile(times, 95),
            "response_time_max_ms": max(times) if times else None,
        }

    async def _hourly(
        self, resolution: Resolution, window: Window, limits: _Limits
    ) -> dict[str, Any] | None:
        """시 단위 애플리케이션 통계(창이 길 때 맥락) — 시 경계로 내림·올림. 합계는 받은 **전**
        애플리케이션 행으로 낸다(종전 `max_row=20` 합계 누락 교정 · plans/134 W1 N-2)."""
        start, end = _hour_floor(window.start_ms), _hour_ceil(window.end_ms)
        rows: list[dict[str, Any]] = []
        groups = _group(resolution)
        expect_calls(len(groups))
        for (sid, domain_id), ids in groups.items():
            try:
                rows.extend(
                    _tag(await self._api(sid).application_status(domain_id, ids, start, end), sid)
                )
            except ApmError as e:
                if e.code == CONTRACT_VIOLATION:
                    raise
                limits.fail(
                    f"[한계] 시 단위 통계 조회 실패({self.sources.where(sid, domain_id)}): {e.code}"
                )
        if not rows:
            return None
        calls = sum(r["calls"] for r in rows)
        failures = sum(r["failures"] for r in rows)
        if all(r.get("total_response_ms") is not None for r in rows):
            weighted = float(sum(r["total_response_ms"] for r in rows))
        else:
            weighted = sum((r["response_time_avg_ms"] or 0) * r["calls"] for r in rows)
        top = sorted(rows, key=lambda r: r["response_time_avg_ms"] or 0, reverse=True)[
            :HOURLY_TOP_APPLICATIONS
        ]
        limits.append("[한계] 시 단위 통계 — 사건창보다 넓은 시간 경계로 집계된 값")
        limits.append(
            "[한계] 시 단위 통계 행 수는 서버 기본값이다(미공개 — W10) · 합계는 받은 전"
            " 애플리케이션 행으로 냈다 · top_applications는 평균 응답시간 상위 "
            f"{HOURLY_TOP_APPLICATIONS}개 요약(전 애플리케이션 목록이 아니다)"
        )
        return {
            "calls": calls,
            "failures": failures,
            "failure_rate": (failures / calls) if calls else None,
            "response_time_avg_ms": (weighted / calls) if calls else None,
            "max_response_time_ms": max((r["max_response_time_ms"] or 0) for r in rows),
            "application_count": len(rows),
            "top_applications": [{**r, "application": mask_url(r["application"])} for r in top],
            "hour_start": datetime.fromtimestamp(start / 1000, ZoneInfo(self._tz)).isoformat(
                timespec="seconds"
            ),
            "hour_end": datetime.fromtimestamp(end / 1000, ZoneInfo(self._tz)).isoformat(
                timespec="seconds"
            ),
        }

    async def _domain_descriptions(self, source_ids: set[str]) -> dict[tuple[str, int], str]:
        """도메인 설명(사용자 입력 자유 텍스트 — 마스킹본) — 캐시된 인벤토리에서 읽는다."""
        out: dict[tuple[str, int], str] = {}
        for sid in source_ids:
            inv = await self.sources.get(sid).resolver.inventory()
            for d in inv.domains:
                out[(sid, d["domain_id"])] = mask_text(
                    d.get("domain_description", ""), limit=None
                )
        return out

    @staticmethod
    def _instance_row(
        inst: dict[str, Any], domain_description: str, **extra: Any
    ) -> dict[str, Any]:
        return {
            "source_id": inst["source_id"],
            "instance_id": inst["instance_id"],
            "instance_name": inst["instance_name"],
            "domain_id": inst["domain_id"],
            "domain_name": inst["domain_name"],
            "domain_description": domain_description,
            "host_name": inst["host_name"],
            "ip_address": inst["ip_address"],
            "platform": inst["platform"],
            "status": inst["status"],
            "agent_version": inst["agent_version"],
            "config_file_path": inst.get("config_file_path", ""),
            "description": mask_text(inst.get("description", ""), limit=None),
            "instance_oid": inst.get("instance_oid"),
            **extra,
        }

    @staticmethod
    def _reason_tail(e: ApmError) -> str:
        """서버가 요청을 거부한 사유(`apm_api_error`)를 그대로 붙인다 — 허용값이 미공개인 인자
        (정렬 기준·간격 — COV E-05·E-06)를 서버가 거부하면 그 사유가 사용자에게 보여야 한다."""
        return f" — {mask_text(e.reason, limit=240)}" if e.code == API_ERROR else ""

    @staticmethod
    def _all_failed(failures: list[tuple[str, ApmError]]) -> ApmError:
        """모든 단위가 실패했을 때의 오류 — 원인 코드가 모두 같으면 그 코드(서버 거부 사유 그대로),
        섞이면 `source_unavailable`."""
        codes = {e.code for _, e in failures}
        return ApmError(
            codes.pop() if len(codes) == 1 else SOURCE_UNAVAILABLE,
            "; ".join(f"{where}: {e.reason}" if where else e.reason for where, e in failures),
        )

    def _where(self, source_id: str) -> str:
        where = self.sources.where(source_id)
        return f"({where})" if where else ""

    async def _catalog(self, source_id: str) -> _Catalog:
        """소스의 지표 카탈로그(TTL 캐시 · plans/134 N-6). 다시 읽어 지문이 바뀌었으면 그 수명 동안
        `[한계]`로 알린다(지표 추가·삭제 수)."""
        now = self.clock()
        cur = self._catalogs.get(source_id)
        if cur is not None and now - cur.fetched_at < self.cfg.runtime.metric_catalog_ttl_seconds:
            return cur
        expect_calls(1)
        scopes, invalid = await self._api(source_id).metric_catalog()
        fingerprint = _fingerprint(scopes)
        note = None
        if cur is not None and cur.fingerprint != fingerprint:
            before = {(sc, m) for sc, names in cur.scopes.items() for m in names}
            after = {(sc, m) for sc, names in scopes.items() for m in names}
            note = (
                f"[한계] 지표 카탈로그 변경 감지{self._where(source_id)} — 지문 {cur.fingerprint}"
                f" → {fingerprint} · 추가 {len(after - before)} · 삭제 {len(before - after)}"
            )
        entry = _Catalog(now, scopes, fingerprint, note, tuple(invalid))
        self._catalogs[source_id] = entry
        return entry

    @staticmethod
    def _metric_names(metrics: list[str] | None) -> list[str]:
        """요청 지표 이름(중복 제거 · 순서 유지) — 식별자 형식만 여기서 본다(값은 카탈로그)."""
        if metrics is None:
            return []
        if isinstance(metrics, str) or not isinstance(metrics, list):
            raise ApmError(INVALID_ARGUMENT, "metrics는 지표 이름 목록이어야 한다")
        names: list[str] = []
        for raw in metrics:
            name = _identifier(raw, "metrics 항목")
            if name is not None and name not in names:
                names.append(name)
        return names

    async def _metric_plan(
        self,
        source_ids: list[str],
        requested: list[str],
        scope: str,
        limits: _Limits,
        *,
        all_unknown_raises: bool,
    ) -> dict[str, list[tuple[str, str]]] | None:
        """요청 지표 → 소스별 `[(지표 식별자, 표시 이름)]`. 카탈로그(`scope` 군)로 검증한다.

        - 중립 이름(`heap_used_mb`)은 식별자로 바꾸고 표시 이름은 중립 이름이다. 식별자로 주면
          표시 이름은 대응 중립 이름(없으면 식별자 그대로)이다.
        - 고른 소스 **전부**의 카탈로그에 없는 지표는 빼고 `[한계]`(difflib 후보 ≤3)에 적는다
          (W2V-B2 — 선택 조건 하나로 보기를 막지 않는다). 요청 지표가 **전부** 그렇다면
          `all_unknown_raises`면 `invalid_argument`(지표가 본질인 시계열), 아니면 None(호출자가
          기본 지표로 조회).
        - 일부 소스 카탈로그에만 없으면 그 소스만 생략하고 `[한계]`.
        - 카탈로그를 읽지 못했거나 그 군이 모양 위반인 소스는 검증 없이 조회하고 `[한계]`
          (partial)로 알린다.
        """
        wanted: list[tuple[str, str]] = []
        for name in requested:
            mapped = JenniferApi.metric_id(name)
            pair = (mapped, name) if mapped else (name, JenniferApi.neutral_metric(name) or name)
            if all(pair[0] != mid for mid, _ in wanted):
                wanted.append(pair)
        known: dict[str, set[str] | None] = {}
        for sid in source_ids:
            try:
                entry = await self._catalog(sid)
            except ApmError as e:
                if e.code == CONTRACT_VIOLATION:
                    raise
                limits.fail(
                    f"[한계] 지표 카탈로그 조회 실패{self._where(sid)}: {e.code} — 지표 이름을"
                    " 검증하지 못하고 그대로 조회했다"
                )
                known[sid] = None
                continue
            if entry.change_note:
                limits.append(entry.change_note)
            if scope in entry.invalid:
                limits.fail(
                    f"[한계] 지표 카탈로그 {scope} 군 모양 위반{self._where(sid)} — 지표 이름을"
                    " 검증하지 못하고 그대로 조회했다"
                )
                known[sid] = None
                continue
            known[sid] = set(entry.scopes.get(scope, []))
        unknown = [
            (mid, label)
            for mid, label in wanted
            if known and all(k is not None and mid not in k for k in known.values())
        ]
        if unknown:
            ids = {m for k in known.values() if k for m in k}
            pool = sorted(ids) + sorted(
                n for n, mid in JenniferApi.neutral_metrics().items() if mid in ids
            )
            parts = []
            for _mid, label in unknown:
                close = difflib.get_close_matches(label, pool, n=_METRIC_SUGGESTIONS)
                parts.append(f"{label}(후보: {', '.join(close) if close else '없음'})")
            if len(unknown) == len(wanted) and all_unknown_raises:
                raise ApmError(
                    INVALID_ARGUMENT,
                    f"{scope} 지표 카탈로그에 없는 지표: {'; '.join(parts)} — 전체 목록은"
                    " apm_metrics(mode catalog)",
                )
            note = f"[한계] {scope} 지표 카탈로그에 없는 지표는 빼고 조회했다: {'; '.join(parts)}"
            if len(unknown) < len(wanted):
                limits.unresolve(
                    f"요청한 지표 {', '.join(label for _, label in unknown)}는 지표 목록에 없어"
                    " 빼고 조회했습니다"
                )
            if all_unknown_raises:
                limits.fail(note)  # 시계열은 지표가 본질이다 — 일부가 빠진 결과
            else:
                limits.append(note)
            if len(unknown) == len(wanted):
                return None
            wanted = [w for w in wanted if w not in unknown]
        plan: dict[str, list[tuple[str, str]]] = {}
        for sid, names in known.items():
            plan[sid] = []
            for mid, label in wanted:
                if names is None or mid in names:
                    plan[sid].append((mid, label))
                else:
                    limits.append(
                        f"[한계] 지표 {label}는 {self.sources.where(sid) or '이 소스'} 카탈로그에"
                        " 없어 그 소스 조회를 생략했다"
                    )
        return plan

    # ── 도구 8종(W1까지) ────────────────────────────────────

    async def apm_instance_map(
        self, hostname: str | None = None, source_ids: list[str] | None = None
    ) -> dict[str, Any]:
        tool = "apm_instance_map"
        self.sources.require_configured()
        if hostname and str(hostname).strip():
            res = await self.sources.resolve(str(hostname).strip(), None, source_ids)
            descriptions = await self._domain_descriptions({i["source_id"] for i in res.instances})
            rows = []
            for inst in res.instances:
                confidence, reason = res.match_of(inst)
                desc = descriptions.get((inst["source_id"], inst["domain_id"]), "")
                rows.append(
                    self._instance_row(
                        inst,
                        desc,
                        port=inst.get("port"),
                        kind=inst.get("kind"),
                        match_confidence=confidence,
                        match_reason=reason,
                    )
                )
            return self.ok(tool, rows, resolution=res, file_only=FILE_ONLY_COLUMNS)
        usable, statuses, limits = await self.sources.available(self.sources.select(source_ids))
        partial = partial_of(usable, statuses)
        rows = []
        for src, inv in usable:
            descriptions = {
                d["domain_id"]: mask_text(d.get("domain_description", ""), limit=None)
                for d in inv.domains
            }
            for inst in inv.instances:
                host, conf, reason, _ = src.resolver.reverse(
                    inv, inst["domain_id"], inst["instance_id"]
                )
                rows.append(
                    self._instance_row(
                        inst,
                        descriptions.get(inst["domain_id"], ""),
                        hostname=host,
                        match_confidence=conf,
                        match_reason=reason,
                    )
                )
        return self.ok(
            tool,
            rows,
            limits=limits,
            sources=statuses,
            partial=partial,
            file_only=FILE_ONLY_COLUMNS,
        )

    async def apm_app_health(
        self,
        hostname: str,
        instance_id: int | None = None,
        reference_time: str | None = None,
        lookback_minutes: int | None = None,
        source_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        tool = "apm_app_health"
        res = await self._resolve(hostname, instance_id, source_ids)
        window = self.window(reference_time, lookback_minutes, default_minutes=None)
        limits = _Limits()
        current: dict[InstKey, dict[str, Any]] = {}
        if window is None or window.end_is_now:
            current = await self._realtime(res, limits)
            if current:
                limits.append(
                    "[한계] 방문·호출 수(visit_day·visit_hour·hit_day·hit_hour)는 단위·"
                    "「하루」 경계(자정 기준인지 24시간 이동인지)가 미확인이다(W10)"
                )
        else:
            limits.append(
                "[한계] 과거 기준시각 — 실시간 스냅샷 생략(현재값은 사건 시점 증거가 아니다)"
            )
        per_inst_tx: dict[InstKey, list[dict[str, Any]]] = {}
        xview_ok = False
        hourly = None
        if window is not None:
            txs, xview_ok = await self._xview(res, window, limits)
            for tx in txs:
                per_inst_tx.setdefault(_key(tx), []).append(tx)
            if window.minutes > HOURLY_AFTER_MINUTES:
                hourly = await self._hourly(res, window, limits)
        if not current and not xview_ok and hourly is None:
            raise ApmError(SOURCE_UNAVAILABLE, "; ".join(limits) or "APM 데이터 조회 실패")
        rows: list[dict[str, Any]] = []
        signals: list[dict[str, Any]] = []
        for inst in res.instances:
            key = _key(inst)
            cur = current.get(key)
            row = _inst_meta(inst)
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
            stats = self._window_stats(per_inst_tx.get(key, [])) if window is not None else None
            if stats is not None:
                row["window"] = stats
            rows.append(row)
            signals += _tag(sig.judge_app(cur, stats, self._th, instance_id=key[1]), key[0])
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

    async def apm_runtime_health(
        self,
        hostname: str,
        instance_id: int | None = None,
        reference_time: str | None = None,
        lookback_minutes: int | None = None,
        source_ids: list[str] | None = None,
        metrics: list[str] | None = None,
        interval_minute: int | None = None,
    ) -> dict[str, Any]:
        tool = "apm_runtime_health"
        interval = _positive_int(interval_minute, "interval_minute", TREND_INTERVAL_MINUTE)
        requested = self._metric_names(metrics)
        res = await self._resolve(hostname, instance_id, source_ids)
        # 지표·간격을 주면 추세가 목적이다 — 구간이 없으면 기본 구간으로 본다(침묵 무시 금지).
        trend_wanted = bool(requested) or interval_minute is not None
        window = self.window(
            reference_time,
            lookback_minutes,
            default_minutes=HEALTH_DEFAULT_LOOKBACK if trend_wanted else None,
        )
        limits = _Limits()
        current: dict[InstKey, dict[str, Any]] = {}
        if window is None or window.end_is_now:
            current = await self._realtime(res, limits)
        else:
            limits.append(
                "[한계] 과거 기준시각 — 실시간 스냅샷 생략(현재값은 사건 시점 증거가 아니다)"
            )
        trends: dict[InstKey, dict[str, Any]] = {}
        if window is not None:
            plan = None
            if requested:
                plan = await self._metric_plan(
                    sorted({i["source_id"] for i in res.instances}),
                    requested,
                    "instance",
                    limits,
                    all_unknown_raises=False,
                )
                if plan is None:  # 요청 지표를 모두 모른다 — 현재값은 버리지 않고 기본 추세로
                    limits.append(
                        "[한계] 요청한 지표를 모두 카탈로그에서 찾지 못해 기본 추세 지표"
                        f"({'·'.join(DEFAULT_TREND_METRICS)})로 조회했다"
                    )
                    limits.unresolve(
                        f"요청한 지표 {', '.join(requested)}를 지표 목록에서 찾지 못해 기본 지표"
                        f"({', '.join(DEFAULT_TREND_METRICS)})로 조회했습니다"
                    )
            if plan is None:  # 기본 3종(중립 이름 — 식별자 매핑이 정해져 있다)
                default = [
                    (mid, m) for m in DEFAULT_TREND_METRICS if (mid := JenniferApi.metric_id(m))
                ]
                plan = {i["source_id"]: default for i in res.instances}
            if interval_minute is not None:
                limits.append(
                    "[한계] interval_minute 허용값은 미공개다(W10) — 서버가 거부하면 그 사유를"
                    " 그대로 싣는다"
                )
            expect_calls(sum(len(plan.get(i["source_id"], [])) for i in res.instances))
            for inst in res.instances:  # 정합된 인스턴스 전부(종전 2개 상한 제거 · W1 N-4)
                trend: dict[str, list[dict[str, Any]]] = {}
                for metric_id, label in plan.get(inst["source_id"], []):
                    try:
                        trend[label] = await self._api(inst["source_id"]).instance_metric_series(
                            inst["domain_id"],
                            inst["instance_id"],
                            metric_id,
                            interval,
                            window.start_ms,
                            window.end_ms,
                        )
                    except ApmError as e:
                        if e.code == CONTRACT_VIOLATION:
                            raise
                        limits.fail(
                            f"[한계] 추세 {label} 조회 실패"
                            f"(인스턴스 {self._inst_label(_key(inst))}): {e.code}"
                            + self._reason_tail(e)
                        )
                if trend:
                    trends[_key(inst)] = trend
        if not current and not trends:
            raise ApmError(SOURCE_UNAVAILABLE, "; ".join(limits) or "APM 런타임 데이터 조회 실패")
        rows: list[dict[str, Any]] = []
        signals: list[dict[str, Any]] = []
        for inst in res.instances:
            key = _key(inst)
            cur = current.get(key)
            row = _inst_meta(inst)
            if cur:
                for field_name in (
                    "heap_used_mb",
                    "heap_committed_mb",
                    "non_heap_used_mb",
                    "gc_time_usage_pct",
                    "process_cpu_pct",
                    "process_memory_mb",
                    "thread_current",
                    "thread_daemon",
                    "thread_started",
                    "socket_count",
                    "file_count",
                    "collection_count",
                ):
                    row[field_name] = cur.get(field_name)
                used, committed = cur.get("heap_used_mb"), cur.get("heap_committed_mb")
                row["heap_usage_ratio"] = (
                    (used / committed) if used is not None and committed else None
                )
            if key in trends:
                row["trend"] = trends[key]
            rows.append(row)
            signals += _tag(
                sig.judge_runtime(cur, trends.get(key), self._th, instance_id=key[1]), key[0]
            )
        return self.ok(
            tool,
            rows,
            resolution=res,
            window=window,
            was_signals=signals,
            limits=limits,
            partial=limits.partial,
        )

    async def apm_resource_pool(
        self,
        hostname: str,
        instance_id: int | None = None,
        source_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        tool = "apm_resource_pool"
        res = await self._resolve(hostname, instance_id, source_ids)
        limits = _Limits(
            [
                "[한계] 현재값 전용 — 과거 사건의 증거로 쓰지 않는다",
                "[한계] WAS 스레드 풀 상한 필드 없음 — thread_current(JVM 전체)·active_services로"
                " 근사",
            ]
        )
        current = await self._realtime(res, limits)
        services: dict[InstKey, list[dict[str, Any]]] = {}
        active_ok = False
        groups = _group(res)
        expect_calls(len(groups))
        for (sid, domain_id), ids in groups.items():
            try:
                for svc in _tag(await self._api(sid).active_services(domain_id, ids), sid):
                    services.setdefault(_key(svc), []).append(svc)
                active_ok = True
            except ApmError as e:
                if e.code == CONTRACT_VIOLATION:
                    raise
                where = self.sources.where(sid, domain_id)
                limits.fail(f"[한계] 액티브 서비스 조회 실패({where}): {e.code}")
        if not current and not active_ok:
            raise ApmError(SOURCE_UNAVAILABLE, "; ".join(limits))
        rows: list[dict[str, Any]] = []
        signals: list[dict[str, Any]] = []
        for inst in res.instances:
            key = _key(inst)
            cur = current.get(key) or {}
            svcs = services.get(key, [])
            active = cur.get("db_pool_active")
            configured = cur.get("db_pool_configured_avg")
            rows.append(
                {
                    **_inst_meta(inst),
                    "db_pool_active": active,
                    "db_pool_idle_avg": cur.get("db_pool_idle_avg"),
                    "db_pool_configured_avg": configured,
                    "db_pool_usage_ratio": (active / configured)
                    if active is not None and configured
                    else None,
                    "thread_current": cur.get("thread_current"),
                    "active_services": cur.get("active_services") if cur else len(svcs),
                    "active_by_running_mode": dict(
                        Counter(s["running_mode"] or "(없음)" for s in svcs)
                    ),
                    "active_by_datasource": dict(
                        Counter(s["datasource"] for s in svcs if s["datasource"])
                    ),
                }
            )
            signals += _tag(sig.judge_pool(cur, self._th, instance_id=key[1]), key[0])
            signals += _tag(
                sig.judge_active_services(svcs, self._th, instance_id=key[1], source_tool=tool),
                key[0],
            )
        return self.ok(
            tool, rows, resolution=res, was_signals=signals, limits=limits, partial=limits.partial
        )

    async def apm_slow_transactions(
        self,
        hostname: str,
        instance_id: int | None = None,
        reference_time: str | None = None,
        lookback_minutes: int | None = None,
        n: int | None = None,
        source_ids: list[str] | None = None,
        full: bool = False,
    ) -> dict[str, Any]:
        tool = "apm_slow_transactions"
        top_n = _check_n(n)
        res = await self._resolve(hostname, instance_id, source_ids)
        window = self.window(
            reference_time, lookback_minutes, default_minutes=SLOW_TX_DEFAULT_MINUTES
        )
        assert window is not None
        limits = _Limits()
        txs, ok = await self._xview(res, window, limits)
        hourly = (
            await self._hourly(res, window, limits)
            if window.minutes > HOURLY_AFTER_MINUTES
            else None
        )
        if not ok and hourly is None:
            raise ApmError(SOURCE_UNAVAILABLE, "; ".join(limits) or "트랜잭션 조회 실패")
        ranked = sorted(txs, key=lambda t: t.get("response_time_ms") or 0, reverse=True)
        selected = _select(ranked, top_n, full)
        rows = [
            {
                "source_id": tx["source_id"],
                **_tx_fields(tx),
                "profile_ref": _profile_ref(tx, "end_time_ms"),
            }
            for tx in selected
        ]
        judged = ranked[:top_n] if top_n is not None else ranked
        signals: list[dict[str, Any]] = []
        per_inst: dict[InstKey, list[dict[str, Any]]] = {}
        for tx in ranked:
            per_inst.setdefault(_key(tx), []).append(tx)
        for (sid, iid), items in per_inst.items():
            signals += _tag(sig.judge_transactions(items[:top_n], self._th, instance_id=iid), sid)
        summary = {**self._window_stats(txs), **sig.transaction_shares(judged)}
        extra: dict[str, Any] = {"summary": summary}
        if hourly is not None:
            extra["hourly"] = hourly
        return self.ok(
            tool,
            rows,
            resolution=res,
            window=window,
            was_signals=signals,
            limits=limits,
            partial=limits.partial,
            file_only=FILE_ONLY_COLUMNS,
            masked=_identifier_fields(selected),
            **extra,
        )

    async def apm_active_services(
        self,
        hostname: str,
        instance_id: int | None = None,
        n: int | None = None,
        source_ids: list[str] | None = None,
        full: bool = False,
    ) -> dict[str, Any]:
        tool = "apm_active_services"
        top_n = _check_n(n)
        res = await self._resolve(hostname, instance_id, source_ids)
        limits = _Limits(
            [
                "[한계] 현재값 전용 — 과거 사건의 증거로 쓰지 않는다",
                "[한계] elapsed_ms는 경과 시간 필드 단위 미기재로 ms로 가정(U-12)",
            ]
        )
        wanted = {_key(i) for i in res.instances}
        services: list[dict[str, Any]] = []
        ok = False
        groups = _group(res)
        expect_calls(len(groups))
        for (sid, domain_id), ids in groups.items():
            try:
                services += [
                    s
                    for s in _tag(
                        await self._api(sid).active_services(domain_id, ids), sid, domain_id
                    )
                    if _key(s) in wanted
                ]
                ok = True
            except ApmError as e:
                if e.code == CONTRACT_VIOLATION:
                    raise
                where = self.sources.where(sid, domain_id)
                limits.fail(f"[한계] 액티브 서비스 조회 실패({where}): {e.code}")
        if not ok:
            raise ApmError(SOURCE_UNAVAILABLE, "; ".join(limits))
        ranked = sorted(services, key=lambda s: s.get("elapsed_ms") or 0, reverse=True)
        rows = [
            {
                "source_id": s["source_id"],
                "domain_id": s.get("domain_id"),
                "domain_name": s.get("domain_name", ""),
                "instance_id": s["instance_id"],
                "instance_name": s.get("instance_name", ""),
                "instance_oid": s.get("instance_oid"),
                "application": mask_url(s["application"]),
                "application_alias": mask_url(s.get("application_alias", "")),
                "status": s["status"],
                "status_name": s["status_name"],
                "status_message": mask_text(s.get("status_message", ""), limit=None),
                "elapsed_ms": s["elapsed_ms"],
                "status_elapsed_ms": s["status_elapsed_ms"],
                "running_ms": s.get("running_ms"),
                "cpu_ms": s.get("cpu_ms"),
                "sql_count": s.get("sql_count"),
                "fetch_count": s.get("fetch_count"),
                "running_mode": s["running_mode"],
                "running_text": mask_text(s["running_text"], limit=None),
                "running_hash": s.get("running_hash"),
                "running_sherpa_oracle_instance": s.get("running_sherpa_oracle_instance", ""),
                "running_sherpa_oracle_seq": s.get("running_sherpa_oracle_seq"),
                "datasource": s["datasource"],
                "client_ip": mask_ip(s["client_ip"]),
                "txid": s["txid"],
                "session_id": s.get("session_id"),
                "thread_hash": s.get("thread_hash"),
                "start_time_ms": s["start_time_ms"],
                "business_ids": list(s.get("business_ids") or []),
                "business_names": list(s.get("business_names") or []),
                # 실행 중 요청 상세(F-15 · W7)의 입력 — 소스·도메인·txid·세션·스레드 해시
                "active_ref": {
                    "source_id": s["source_id"],
                    "domain_id": s.get("domain_id"),
                    "txid": s["txid"],
                    "session_id": s.get("session_id"),
                    "thread_hash": s.get("thread_hash"),
                }
                if s.get("txid")
                else None,
            }
            for s in _select(ranked, top_n, full)
        ]
        signals: list[dict[str, Any]] = []
        for key in sorted(wanted):
            signals += _tag(
                sig.judge_active_services(
                    [s for s in services if _key(s) == key],
                    self._th,
                    instance_id=key[1],
                    source_tool=tool,
                ),
                key[0],
            )
        summary = {
            "total": len(services),
            "by_running_mode": dict(Counter(s["running_mode"] or "(없음)" for s in services)),
            "by_status": dict(
                Counter(s["status_name"] or s["status"] or "(없음)" for s in services)
            ),
        }
        return self.ok(
            tool,
            rows,
            resolution=res,
            was_signals=signals,
            limits=limits,
            partial=limits.partial,
            file_only=FILE_ONLY_COLUMNS,
            summary=summary,
        )

    @staticmethod
    def _event_options(
        level: str | None,
        level_mode: str | None,
        error_type: str | None,
        record: str | None,
    ) -> tuple[str | None, str, str | None, str]:
        lvl = str(level).strip().lower() if level is not None else None
        if lvl is not None and lvl not in LEVELS:
            raise ApmError(
                INVALID_ARGUMENT, f"level은 fatal·warning·normal 중 하나여야 한다: {level}"
            )
        mode = str(level_mode).strip().lower() if level_mode else "min"
        if mode not in LEVEL_MODES:
            raise ApmError(
                INVALID_ARGUMENT, f"level_mode는 min·exact 중 하나여야 한다: {level_mode}"
            )
        if mode == "exact" and lvl is None:
            raise ApmError(INVALID_ARGUMENT, "level_mode=exact에는 level이 필요하다")
        etype = None
        if error_type is not None and str(error_type).strip():
            etype = str(error_type).strip()
            if not _ERROR_TYPE.fullmatch(etype):
                raise ApmError(
                    INVALID_ARGUMENT,
                    f"error_type은 영문·숫자·밑줄 64자 이하여야 한다: {error_type}",
                )
            etype = etype.upper()
            if not JenniferApi.error_type_variants(etype):
                raise ApmError(
                    INVALID_ARGUMENT, f"error_type에 유형 이름이 없다(접두뿐): {error_type}"
                )
        rec = str(record).strip().lower() if record else "event"
        if rec not in RECORDS:
            raise ApmError(INVALID_ARGUMENT, f"record는 event·error 중 하나여야 한다: {record}")
        return lvl, mode, etype, rec

    async def apm_events(
        self,
        hostname: str,
        reference_time: str | None = None,
        lookback_minutes: int | None = None,
        level: str | None = None,
        source_ids: list[str] | None = None,
        level_mode: str | None = None,
        error_type: str | None = None,
        record: str | None = None,
        n: int | None = None,
        full: bool = False,
    ) -> dict[str, Any]:
        tool = "apm_events"
        lvl, mode, etype, rec = self._event_options(level, level_mode, error_type, record)
        top_n = _check_n(n, default=None)
        res = await self._resolve(hostname, None, source_ids)
        limits = _Limits()
        window = self.window(
            reference_time, lookback_minutes, default_minutes=EVENTS_DEFAULT_MINUTES
        )
        assert window is not None
        if mode == "exact":
            limits.append(
                f"[한계] level={lvl} 정확 일치 — API level 필터 의미 미확인(W10) · 응답을 다시"
                " 정확 일치로 걸렀다"
            )
        wanted = {_key(i) for i in res.instances}
        events: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        events_ok = errors_ok = False
        groups = _group(res)
        expect_calls(len(groups) * 2)
        for (sid, domain_id), ids in groups.items():
            api = self._api(sid)
            where = self.sources.where(sid, domain_id)
            try:
                batch = await api.events(
                    domain_id,
                    ids,
                    window.start_ms,
                    window.end_ms,
                    level=lvl if mode == "exact" else None,
                )
                events += [e for e in _tag(batch, sid, domain_id) if _key(e) in wanted]
                events_ok = True
            except ApmError as e:
                if e.code == CONTRACT_VIOLATION:
                    raise
                limits.fail(f"[한계] 이벤트 조회 실패({where}): {e.code}")
            try:
                batch = await self._errors(api, domain_id, ids, window, etype, where, limits)
                errors += [e for e in _tag(batch, sid, domain_id) if _key(e) in wanted]
                errors_ok = True
            except ApmError as e:
                if e.code == CONTRACT_VIOLATION:
                    raise
                limits.fail(f"[한계] 오류 기록 조회 실패({where}): {e.code}")
        if (rec == "event" and not events_ok) or (rec == "error" and not errors_ok):
            raise ApmError(SOURCE_UNAVAILABLE, "; ".join(limits))
        if lvl is not None:
            floor = level_rank(lvl)
            events = [
                e
                for e in events
                if (e["level"] == lvl if mode == "exact" else level_rank(e["level"]) >= floor)
            ]
        if etype is not None:
            events = [e for e in events if self._api(e["source_id"]).same_error_type(
                e["event_type"], etype
            )]
            errors = [e for e in errors if self._api(e["source_id"]).same_error_type(
                e["error_type"], etype
            )]
        events.sort(key=lambda e: e.get("time_ms") or 0, reverse=True)
        errors.sort(key=lambda e: e.get("time_ms") or 0, reverse=True)
        signals: list[dict[str, Any]] = []
        for ev in events:
            s = sig.signal_from_event(
                self._api(ev["source_id"]).event_signal(ev["event_type"]),
                ev["event_type"],
                instance_id=ev["instance_id"],
                source_tool=tool,
            )
            if s is not None:
                signals.append({**s, "source_id": ev["source_id"]})
        if rec == "event":
            rows = [self._event_row(ev) for ev in _select(events, top_n, full)]
        else:
            rows = [self._error_row(er) for er in _select(errors, top_n, full)]
        error_summary = Counter(e["error_type"] or "(없음)" for e in errors).most_common()
        return self.ok(
            tool,
            rows,
            resolution=res,
            window=window,
            was_signals=signals,
            limits=limits,
            partial=limits.partial,
            file_only=FILE_ONLY_COLUMNS,
            record=rec,
            errors_by_type=[{"error_type": k, "count": v} for k, v in error_summary],
        )

    @staticmethod
    async def _errors(
        api: JenniferApi,
        domain_id: int,
        ids: list[int],
        window: Window,
        etype: str | None,
        where: str,
        limits: _Limits,
    ) -> list[dict[str, Any]]:
        """오류 기록 1묶음. `error_type`이 있으면 API 표기 후보(정규화 이름 → `ERROR_` → `WARNING_`
        접두 · 최대 3회)를 차례로 물어 처음 비지 않은 결과를 쓰고, 맞은 표기를 `[한계]`에 적는다
        (운영 명명 U-13 미확정 · W1 검증 M-1 — 이벤트 쪽 접두 무시와 결과를 맞춘다)."""
        if etype is None:
            return await api.errors(domain_id, ids, window.start_ms, window.end_ms)
        variants = api.error_type_variants(etype)
        at = f"({where})" if where else ""
        for i, spelled in enumerate(variants):
            if i:
                expect_calls(1)
            batch = await api.errors(
                domain_id, ids, window.start_ms, window.end_ms, error_type=spelled
            )
            if batch:
                tried = f" — 앞 표기 {'·'.join(variants[:i])} 0건" if i else ""
                limits.append(
                    f"[한계] 오류 유형 API 표기 {spelled}로 조회{at}{tried} · 운영 명명 미확정"
                    "(U-13 · W10)"
                )
                return batch
        limits.append(
            f"[한계] 오류 유형 API 표기 {'·'.join(variants)} 모두 오류 기록 0건{at} · 운영 명명"
            " 미확정(U-13 · W10)"
        )
        return []

    @staticmethod
    def _event_row(ev: dict[str, Any]) -> dict[str, Any]:
        return {
            "time_ms": ev["time_ms"],
            "level": ev["level"],
            "event_type": ev["event_type"],
            "event_kind": ev["event_kind"],
            "value": ev["value"],
            "message": mask_text(ev["message"], limit=None),
            "source_id": ev["source_id"],
            "domain_id": ev.get("domain_id"),
            "domain_name": ev.get("domain_name", ""),
            "instance_id": ev["instance_id"],
            "instance_name": ev["instance_name"],
            "instance_oid": ev.get("instance_oid"),
            "application": mask_url(ev["application"]),
            "profile_ref": _profile_ref(ev, "time_ms"),
        }

    @staticmethod
    def _error_row(er: dict[str, Any]) -> dict[str, Any]:
        ref = _profile_ref(er, "time_ms")
        if ref is not None and er.get("profile_index") is not None:
            # 오류가 난 프로파일 번호 — apm_transaction_profile `profile_no`로 그대로 넘긴다(W5)
            ref["profile_no"] = er["profile_index"]
        return {
            "time_ms": er["time_ms"],
            "error_type": er["error_type"],
            "value": er.get("value"),
            "message": mask_text(er["message"], limit=None),
            "source_id": er["source_id"],
            "domain_id": er.get("domain_id"),
            "domain_name": er.get("domain_name", ""),
            "instance_id": er["instance_id"],
            "instance_name": er.get("instance_name", ""),
            "instance_oid": er.get("instance_oid"),
            "application": mask_url(er["application"]),
            "txid": er["txid"],
            "profile_index": er.get("profile_index"),
            "profile_ref": ref,
        }

    def _consume_profile_budget(
        self, principal: str, investigation_id: str | None, owner: str | None
    ) -> None:
        """프로파일 예산 1회 — 채팅 주체는 쓰지 않는다. 그 밖 주체의 칸은 (주체, `investigation_id`
        → `owner` → `_unspecified`)이고 주체가 다르면 같은 칸을 쓰지 않는다(종전 `_anonymous` 전
        주체 공유 폐지). 주체는 전송 토큰에서만 온다 — 인자(`investigation_id`·`owner`)는 칸을 고를
        뿐 면제를 주지 않는다."""
        if principal == CHAT_PRINCIPAL:
            return
        now = self.clock()
        for key in [
            k for k, (_, t0) in self._profile_budget.items() if now - t0 > _PROFILE_BUDGET_TTL
        ]:
            del self._profile_budget[key]
        inv = str(investigation_id).strip() if investigation_id is not None else ""
        own = str(owner).strip() if owner is not None else ""
        key = (principal, inv or own or _UNSPECIFIED)
        count, t0 = self._profile_budget.get(key, (0, now))
        limit = self.cfg.runtime.profile_calls_per_investigation
        if count >= limit:
            which = (
                f"investigation_id={inv}"
                if inv
                else (f"owner={own}" if own else "investigation_id·owner 없음")
            )
            raise ApmError(
                RATE_LIMITED,
                f"조사당 프로파일 호출 상한 {limit}회 초과(주체 {principal} · {which})",
            )
        self._profile_budget[key] = (count + 1, t0)

    async def apm_transaction_profile(
        self,
        hostname: str,
        domain_id: int | None = None,
        txid: str | int | None = None,
        time_ms: int | None = None,
        top_k: int | None = None,
        investigation_id: str | None = None,
        source_id: str | None = None,
        profile_no: int | None = None,
        include_param_key: bool | None = None,
        *,
        owner: str | None = None,
        principal: str = js.ANONYMOUS_PRINCIPAL,
    ) -> dict[str, Any]:
        """개별 트랜잭션 프로파일. `profile_no`(오류 행 `profile_ref.profile_no`)와
        `include_param_key`는 SQL 조회에만 싣는다. `principal`은 서버가 전송 토큰으로 정한 호출
        주체다(MCP 인자 아님)."""
        tool = "apm_transaction_profile"
        k = _check_n(top_k, default=None)  # 없으면 SQL 전부(W1 — 종전 ≤20·기본 10 상한 제거)
        pno = None if profile_no is None else _nonneg_int(profile_no, "profile_no")
        if include_param_key is not None and not isinstance(include_param_key, bool):
            raise ApmError(INVALID_ARGUMENT, "include_param_key는 true·false여야 한다")
        if domain_id is None or txid is None or time_ms is None:
            raise ApmError(
                INVALID_ARGUMENT,
                "profile_ref(domain_id·txid·time_ms)가 필요하다 — apm_slow_transactions·apm_events"
                " 결과의 profile_ref를 그대로 넘길 것",
            )
        if (
            not str(txid).lstrip("-").isdigit()
            or not str(time_ms).isdigit()
            or not str(domain_id).isdigit()
        ):
            raise ApmError(INVALID_ARGUMENT, "domain_id·txid·time_ms는 정수여야 한다")
        self.sources.require_configured()
        if not source_id:
            if len(self.sources) > 1:
                raise ApmError(
                    INVALID_ARGUMENT,
                    f"소스가 {len(self.sources)}개라 profile_ref의 source_id가 필요하다"
                    f"(설정된 소스: {self.sources.ids}) — profile_ref를 그대로 넘길 것",
                )
            source_id = self.sources.ids[0]
        sid = str(source_id).strip()
        # 정합은 그 소스에서만 한다 — profile_ref가 가리키지 않는 소스는 부르지 않는다.
        res = await self._resolve(hostname, None, [sid])
        if (sid, int(domain_id)) not in res.source_domains:
            raise ApmError(
                PROFILE_REF_MISMATCH,
                f"domain_id {domain_id}는 hostname {hostname!r}의 정합 도메인"
                f" {sorted(d for _, d in res.source_domains)}이 아니다"
                + (f"(소스 {sid})" if len(self.sources) > 1 else ""),
            )
        self._consume_profile_budget(principal, investigation_id, owner)
        api = self._api(sid)
        d, tx_id, t_ms = int(domain_id), int(txid), int(time_ms)
        limits = _Limits(
            [
                "[한계] 프로파일 텍스트 형식 미검증(J0-L-b 녹화 전) — 단계 요약 없이 마스킹 발췌",
                KEY_ARG_NOTE,
            ]
        )
        if include_param_key:
            limits.append(
                "[한계] include_param_key 응답 모양은 미공개다(W10) — SQL 문자열 칸만 읽어 리터럴을"
                " 가렸다(mask_sql)"
            )
        expect_calls(3)
        detail = excerpt = None
        truncated = False
        full_text: str | None = None
        sqls: list[str] = []
        bind_masked = False
        ok = False
        try:
            detail = await api.transaction_detail(d, tx_id, t_ms)
            ok = True
        except ApmError as e:
            if e.code == CONTRACT_VIOLATION:
                raise
            limits.fail(f"[한계] 트랜잭션 상세 조회 실패: {e.code}")
        try:
            text = await api.profile_text(d, tx_id, t_ms)
            lines = text.splitlines()
            shown = [mask_text(line, limit=400) for line in lines[:PROFILE_EXCERPT_LINES]]
            excerpt = "\n".join(shown)[:PROFILE_EXCERPT_CHARS]
            truncated = len(lines) > PROFILE_EXCERPT_LINES or any(
                len(line) > 400 for line in lines[:PROFILE_EXCERPT_LINES]
            ) or len("\n".join(shown)) > PROFILE_EXCERPT_CHARS
            if truncated:
                full_text = _full_text(text)
                limits.append(
                    f"[한계] profile_excerpt는 화면용 발췌(앞 {PROFILE_EXCERPT_LINES}줄) —"
                    " 전문(마스킹본)은 결과 파일(artifact.text_parts profile)"
                )
            ok = True
        except ApmError as e:
            if e.code == CONTRACT_VIOLATION:
                raise
            limits.fail(f"[한계] 프로파일 텍스트 조회 실패: {e.code}")
        try:
            raw_sqls = await api.transaction_sqls(
                d, tx_id, t_ms, k, profile_no=pno, include_param_key=include_param_key
            )
            # 응답 모양이 미공개라(COV E-11) SQL 칸으로 모은 문자열에 바인드 값이 섞일 수 있다 —
            # 문인지는 **출처 칸 이름**으로 가른다(키워드로 가르면 `{call …}`·`BEGIN … END`가
            # 훼손되고 키워드가 섞인 바인드 값이 새어 나간다 · plans/134 W7 AUDIT-8 · VG-1). SQL 문
            # 칸은 리터럴·개인정보를 가리고, 그 밖 칸은 앞 1자만 남긴다(G-11 미결 동안).
            sqls = [mask_sql(s) if is_sql else mask_identifier(s) for is_sql, s in raw_sqls]
            if any(not is_sql for is_sql, _ in raw_sqls):
                bind_masked = True
                limits.append(
                    "[한계] SQL 응답에 SQL 문 칸이 아닌 문자열(바인드 값일 수 있음)이 있어"
                    " 앞 1자만 남기고 가렸다(응답 모양 미공개 — W10)"
                )
            ok = True
        except ApmError as e:
            if e.code == CONTRACT_VIOLATION:
                raise
            limits.fail(f"[한계] SQL 조회 실패: {e.code}")
        if not ok:
            raise ApmError(SOURCE_UNAVAILABLE, "; ".join(limits))
        transaction = _tx_fields(detail) if detail else None
        row = {
            "source_id": sid,
            "domain_id": d,
            "txid": str(tx_id),
            "time_ms": t_ms,
            "profile_no": pno,
            "transaction": transaction,
            "profile_excerpt": excerpt,
            "profile_truncated": truncated,
            "sqls": sqls,
        }
        return self.ok(
            tool,
            [row],
            resolution=res,
            limits=limits,
            partial=limits.partial,
            file_only=PROFILE_FILE_ONLY,
            text_parts={"profile": full_text} if full_text is not None else None,
            masked=_identifier_fields([detail]) | ({"sqls"} if bind_masked else set()),
        )

    # ── W2 도구(plans/134 N-5~N-7) ───────────────────────────

    def _hour_window(self, window: Window, limits: _Limits) -> tuple[int, int]:
        """`/api/status/*` 구간 — 시 경계로 내림·올림하고 넓혔으면 `[한계]`(제니퍼 시 단위 제약)."""
        start, end = _hour_floor(window.start_ms), _hour_ceil(window.end_ms)
        zone = ZoneInfo(self._tz)

        def iso(ms: int) -> str:
            return datetime.fromtimestamp(ms / 1000, zone).isoformat(timespec="minutes")

        limits.append(
            "[한계] 시 단위 통계 — 구간을 시 경계로 맞췄다"
            + (
                f"(요청 {iso(window.start_ms)}~{iso(window.end_ms)} → 조회 {iso(start)}~{iso(end)})"
                if (start, end) != (window.start_ms, window.end_ms)
                else f"({iso(start)}~{iso(end)})"
            )
        )
        return start, end

    @staticmethod
    def _status_summary(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], bool]:
        """합계 — 호출·실패·실패율·평균(= Σ총 응답시간 ÷ Σ호출 · 호출 수 가중). (요약, 총 응답시간
        칸이 빠져 평균×호출 수로 가중했는가)."""
        calls = sum(r["calls"] for r in rows)
        failures = sum(r["failures"] for r in rows)
        fallback = any(r.get("total_response_ms") is None for r in rows)
        if fallback:
            weighted = sum((r["response_time_avg_ms"] or 0) * r["calls"] for r in rows)
        else:
            weighted = float(sum(r["total_response_ms"] for r in rows))
        maxima = [
            r["max_response_time_ms"] for r in rows if r.get("max_response_time_ms") is not None
        ]
        return {
            "row_count": len(rows),
            "calls": calls,
            "failures": failures,
            "failure_rate": (failures / calls) if calls else None,
            "bad_responses": sum(r["bad_responses"] for r in rows),
            "response_time_avg_ms": (weighted / calls) if calls else None,
            "max_response_time_ms": max(maxima) if maxima else None,
        }, fallback

    async def apm_status_stats(
        self,
        kind: str,
        hostname: str,
        instance_id: int | None = None,
        reference_time: str | None = None,
        lookback_minutes: int | None = None,
        sort_by: str | None = None,
        n: int | None = None,
        full: bool = False,
        application_name: str | None = None,
        source_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """시 단위 통계(`kind` = application·sql·external_call · plans/134 N-5)."""
        tool = "apm_status_stats"
        kind = str(kind or "").strip().lower()
        if kind not in STATUS_KINDS:
            raise ApmError(
                INVALID_ARGUMENT, f"kind는 {'·'.join(STATUS_KINDS)} 중 하나여야 한다: {kind!r}"
            )
        sort_name = _identifier(sort_by, "sort_by")
        top_n = _check_n(n)
        app_name = str(application_name).strip() if application_name is not None else ""
        if app_name and kind != "application":
            raise ApmError(INVALID_ARGUMENT, "application_name은 kind application에서만 쓴다")
        res = await self._resolve(hostname, instance_id, source_ids)
        window = self.window(
            reference_time, lookback_minutes, default_minutes=STATUS_DEFAULT_MINUTES
        )
        assert window is not None
        limits = _Limits()
        start, end = self._hour_window(window, limits)
        if full:
            limits.append(
                "[한계] full — 행 수를 지정하지 않아 서버 기본 행 수(미공개 · W10)만큼 받았다"
                " · 서버가 잘랐을 수 있다"
            )
        if sort_name:
            limits.append(
                f"[한계] 정렬 기준 {sort_name} — 허용값 미공개(W10) · 원천이 거부하면 그 조건 없이"
                " 다시 받아 로컬에서 정렬한다(거부 사유는 그대로 싣는다)"
            )
        received: list[list[dict[str, Any]]] = []  # (소스, 도메인) 묶음별 원천 순서
        failures: list[tuple[str, ApmError]] = []
        refused: list[tuple[str, ApmError]] = []  # 정렬 기준을 거부해 조건 없이 다시 받은 묶음
        groups = _group(res)
        expect_calls(len(groups))
        for (sid, domain_id), ids in groups.items():
            where = self.sources.where(sid, domain_id)
            api = self._api(sid)
            try:
                try:
                    batch = await api.status_stats(
                        kind,
                        domain_id,
                        ids,
                        start,
                        end,
                        sort_by=sort_name,
                        max_row=None if full else top_n,
                        application_name=app_name or None,
                    )
                except ApmError as e:
                    if not (sort_name and e.code == API_ERROR):
                        raise
                    # 원천이 정렬 기준을 받지 않았다 — 그 조건 없이·행 수 없이 다시 받아
                    # 로컬에서 정렬한다(W2V-B2 · 선택 조건 하나로 보기를 막지 않는다)
                    expect_calls(1)
                    batch = await api.status_stats(
                        kind, domain_id, ids, start, end, application_name=app_name or None
                    )
                    refused.append((where, e))
            except ApmError as e:
                if e.code == CONTRACT_VIOLATION:
                    raise
                failures.append((where, e))
                limits.fail(
                    f"[한계] {kind} 통계 조회 실패({where}): {e.code}" + self._reason_tail(e)
                )
                continue
            received.append([{"source_id": sid, "domain_id": domain_id, **r} for r in batch])
        if failures and len(failures) == len(groups):
            raise self._all_failed(failures)
        field = JenniferApi.status_sort_field(kind, sort_name)
        rows = [r for group in received for r in group]
        if refused:
            reasons = "; ".join(f"{w}: {mask_text(e.reason, limit=240)}" for w, e in refused)
            how = (
                "전체를 받아 정렬했다"
                if field is not None
                else "전체를 받았다 — 대응하는 행 칸이 없어 원천 기본 순서다"
            )
            limits.append(
                f"[한계] 원천이 정렬 기준 {sort_name}를 받지 않아 {how}(그 조건·행 수 없이 다시"
                f" 받음 — 행 수는 서버 기본값 · 미공개 W10 · 원천 사유 {reasons})"
            )
            limits.unresolve(
                f"원천이 정렬 기준 {sort_name}을(를) 받지 않아 그 조건 없이 받아 "
                + ("직접 정렬했습니다" if field is not None else "원천 기본 순서로 실었습니다")
            )
        if field is not None:
            if len(received) > 1 or refused:  # 묶음을 합치거나 원천이 정렬하지 않았다
                rows.sort(key=lambda r: r.get(field) or 0, reverse=True)
            shown = _select(rows, top_n, full)
        else:
            # 정렬 기준을 행 칸에 대응하지 못한다 — 묶음마다 원천 순서의 상위 n을 모두 남긴다
            # (뒤 묶음이 화면에서 사라지지 않게 · W2V-G3)
            shown = [r for group in received for r in _select(group, top_n, full)]
            if len(received) > 1:
                limits.append(
                    f"[한계] 정렬 기준 {sort_name}를 행 칸에 대응하지 못해 (소스, 도메인) 묶음별"
                    " 원천 순서를 이어 붙였다 — 전역 순위 아님 — 묶음별 상위 "
                    + ("전부" if full or top_n is None else str(top_n))
                )
        summary, fallback = self._status_summary(shown)
        if fallback:
            limits.append(
                "[한계] 일부 행에 총 응답시간 칸이 없어 평균 응답시간 × 호출 수로 가중했다"
            )
        if not full and len(rows) > len(shown):
            limits.append(
                f"[한계] summary는 표시한 상위 {len(shown)}행의 합계다 — 전체 합계는 full"
            )
        out_rows = []
        for r in shown:
            row = dict(r)
            if kind == "application":
                row["application"] = mask_url(r["application"])
            elif kind == "sql":
                row["name"] = mask_sql(r["name"])
            else:
                row["name"] = mask_url(r["name"])
            out_rows.append(row)
        return self.ok(
            tool,
            out_rows,
            resolution=res,
            window=window,
            limits=limits,
            partial=limits.partial,
            kind=kind,
            hour_start=datetime.fromtimestamp(start / 1000, ZoneInfo(self._tz)).isoformat(
                timespec="seconds"
            ),
            hour_end=datetime.fromtimestamp(end / 1000, ZoneInfo(self._tz)).isoformat(
                timespec="seconds"
            ),
            summary=summary,
        )

    async def apm_metrics(
        self,
        mode: str = "catalog",
        scope: str | None = None,
        hostname: str | None = None,
        instance_id: int | None = None,
        metrics: list[str] | None = None,
        interval_minute: int | None = None,
        reference_time: str | None = None,
        lookback_minutes: int | None = None,
        source_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """지표 카탈로그·시계열(plans/134 N-6)."""
        tool = "apm_metrics"
        mode = str(mode or "catalog").strip().lower()
        if mode not in METRIC_MODES:
            raise ApmError(INVALID_ARGUMENT, f"mode는 catalog·series 중 하나여야 한다: {mode!r}")
        scope_name = str(scope or "").strip().lower() or None
        if scope_name is not None and scope_name not in METRIC_SCOPES:
            raise ApmError(
                INVALID_ARGUMENT, f"scope는 {'·'.join(METRIC_SCOPES)} 중 하나여야 한다: {scope!r}"
            )
        if mode == "catalog":
            return await self._metric_catalog(tool, scope_name, source_ids)
        scope_name = scope_name or "instance"
        if scope_name == "domain":
            raise ApmError(INVALID_ARGUMENT, "scope domain 시계열은 W3 예정이다(아직 없음)")
        if scope_name == "business":
            raise ApmError(INVALID_ARGUMENT, "scope business 시계열은 W4 예정이다(아직 없음)")
        if scope_name != "instance":
            raise ApmError(
                INVALID_ARGUMENT,
                f"scope {scope_name} 지표는 시계열이 아니다 — apm_status_stats(kind {scope_name})",
            )
        requested = self._metric_names(metrics)
        if not requested:
            raise ApmError(INVALID_ARGUMENT, "series에는 metrics(지표 이름 목록)가 필요하다")
        interval = _positive_int(interval_minute, "interval_minute", TREND_INTERVAL_MINUTE)
        res = await self._resolve(hostname, instance_id, source_ids)
        window = self.window(
            reference_time, lookback_minutes, default_minutes=SERIES_DEFAULT_MINUTES
        )
        assert window is not None
        limits = _Limits(
            [
                "[한계] interval_minute 허용값·1회 조회 창 상한은 미공개다(W10) — 서버가 거부하면"
                " 그 사유를 그대로 싣는다"
            ]
        )
        plan = await self._metric_plan(
            sorted({i["source_id"] for i in res.instances}),
            requested,
            "instance",
            limits,
            all_unknown_raises=True,
        )
        assert plan is not None  # 전부 모르면 위에서 invalid_argument
        rows: list[dict[str, Any]] = []
        empty: list[str] = []
        failures: list[tuple[str, ApmError]] = []
        attempted = 0
        expect_calls(sum(len(plan.get(i["source_id"], [])) for i in res.instances))
        for inst in res.instances:
            label = self._inst_label(_key(inst))
            for metric_id, _label in plan.get(inst["source_id"], []):
                attempted += 1
                try:
                    points = await self._api(inst["source_id"]).instance_metric_series(
                        inst["domain_id"],
                        inst["instance_id"],
                        metric_id,
                        interval,
                        window.start_ms,
                        window.end_ms,
                    )
                except ApmError as e:
                    if e.code == CONTRACT_VIOLATION:
                        raise
                    failures.append((f"인스턴스 {label} 지표 {metric_id}", e))
                    limits.fail(
                        f"[한계] 지표 {metric_id} 조회 실패(인스턴스 {label}): {e.code}"
                        + self._reason_tail(e)
                    )
                    continue
                if not points:
                    empty.append(f"{label}:{metric_id}")
                rows += [
                    {
                        **_inst_meta(inst),
                        "metric": metric_id,
                        "time_ms": p["time_ms"],
                        "value": p["value"],
                    }
                    for p in points
                ]
        if attempted and len(failures) == attempted:
            raise self._all_failed(failures)
        if empty:
            limits.append(f"[한계] 데이터 점이 없는 (인스턴스:지표): {', '.join(empty)}")
        return self.ok(
            tool,
            rows,
            resolution=res,
            window=window,
            limits=limits,
            partial=limits.partial,
            mode=mode,
            scope=scope_name,
            interval_minute=interval,
        )

    async def _metric_catalog(
        self, tool: str, scope: str | None, source_ids: list[str] | None
    ) -> dict[str, Any]:
        """소스별 지표 카탈로그 → 행 `{source_id, scope, metric}`(TTL 캐시 · 변경 감지)."""
        limits = _Limits()
        rows: list[dict[str, Any]] = []
        statuses: list[dict[str, Any]] = []
        failures: list[tuple[str, ApmError]] = []
        selected = self.sources.select(source_ids)
        for src in selected:
            try:
                entry = await self._catalog(src.source_id)
            except ApmError as e:
                if e.code == CONTRACT_VIOLATION:
                    raise
                failures.append((self.sources.where(src.source_id), e))
                statuses.append(
                    {
                        "source_id": src.source_id,
                        "status": STATUS_UNAVAILABLE,
                        "reason": f"{e.code}: {e.reason}",
                    }
                )
                limits.fail(
                    f"[한계] 지표 카탈로그 조회 실패{self._where(src.source_id)}: {e.code}"
                    + self._reason_tail(e)
                )
                continue
            statuses.append({"source_id": src.source_id, "status": STATUS_OK, "reason": ""})
            if entry.change_note:
                limits.append(entry.change_note)
            broken = [sc for sc in entry.invalid if scope is None or sc == scope]
            if broken:  # 군 단위 모양 위반 — 빈 군이 아니라 검증 불가(W2V-G4)
                limits.fail(
                    f"[한계] 지표 카탈로그 모양 위반{self._where(src.source_id)} — 군"
                    f" {', '.join(broken)}(목록이 아니거나 문자열이 아닌 항목)은 목록을 싣지 못했다"
                    " · 그 군의 지표 이름은 검증 불가"
                )
            for scope_name, names in entry.scopes.items():
                if scope is None or scope_name == scope:
                    rows += [
                        {"source_id": src.source_id, "scope": scope_name, "metric": m}
                        for m in names
                    ]
        if failures and len(failures) == len(selected):
            raise self._all_failed(failures)
        if scope in (None, *STATUS_KINDS):
            limits.append(
                "[한계] application·sql·external_call 지표 이름이 통계 정렬 기준(sort_by) 값인지는"
                " 미확인이다(W10)"
            )
        return self.ok(
            tool, rows, limits=limits, sources=statuses, partial=limits.partial, mode="catalog"
        )

    async def apm_source_changes(
        self,
        hostname: str,
        reference_time: str | None = None,
        lookback_minutes: int | None = None,
        source_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """소스코드(리소스) 변경 감지 이력(plans/134 N-7) — 25시간 이하 조각 · 겹침 제거."""
        tool = "apm_source_changes"
        res = await self._resolve(hostname, None, source_ids)
        window = self.window(
            reference_time, lookback_minutes, default_minutes=CHANGES_DEFAULT_MINUTES
        )
        assert window is not None
        limits = _Limits([CHANGE_DETECTION_NOTE])
        rows = await self._change_rows(res, window, limits)
        return self.ok(
            tool, rows, resolution=res, window=window, limits=limits, partial=limits.partial
        )

    async def _change_rows(
        self, res: Resolution, window: Window, limits: _Limits
    ) -> list[dict[str, Any]]:
        """변경 감지 행(최근 순) — 25시간 이하 조각 · 겹침 제거 · 받은 조각의 행은 남긴다
        (`apm_source_changes`·`apm_change_impact` 공용 · 묶음이 전부 실패하면 오류)."""
        names = {_key(i): i["instance_name"] for i in res.instances}
        seen: set[tuple[str, int, int | None, int | None]] = set()
        rows: list[dict[str, Any]] = []
        failures: list[tuple[str, ApmError]] = []
        groups = _group(res)
        span = window.end_ms - window.start_ms
        chunks = max(1, math.ceil(span / CHANGES_WINDOW_MS))
        expect_calls(chunks * len(groups))
        zone = ZoneInfo(self._tz)
        for sid, domain_id in groups:
            t = window.start_ms
            done = 0  # 이 묶음에서 받은 조각 수
            while True:
                chunk_end = min(t + CHANGES_WINDOW_MS, window.end_ms)
                try:
                    batch = await self._api(sid).source_changes(domain_id, t, chunk_end)
                except ApmError as e:
                    if e.code == CONTRACT_VIOLATION:
                        raise
                    where = self.sources.where(sid, domain_id)
                    if not done:  # 받은 조각이 없으면 묶음 실패(전부 실패면 오류)
                        failures.append((where, e))
                    limits.fail(
                        f"[한계] 변경 이력 조회 실패({where} · 조각 {done + 1}/{chunks}부터 —"
                        f" 남은 조각 생략): {e.code}" + self._reason_tail(e)
                    )
                    # 받은 조각의 행은 남긴다(partial · W2V-G1). 남은 조각은 같은 원인으로
                    # 실패할 가능성이 커 부르지 않는다(X-View 조각과 같은 처리).
                    break
                done += 1
                for change in batch:
                    key = (sid, domain_id, change["instance_id"], change["change_detected_ms"])
                    if (sid, change["instance_id"]) not in names or key in seen:
                        continue
                    seen.add(key)
                    rows.append(
                        {
                            "source_id": sid,
                            "domain_id": domain_id,
                            "instance_id": change["instance_id"],
                            "instance_name": names[(sid, change["instance_id"])],
                            "change_detected_ms": change["change_detected_ms"],
                            # 사람이 읽는 시각(APM_TIMEZONE · W2V-B6) — 원시 epoch ms는 위 칸
                            "change_detected_at": datetime.fromtimestamp(
                                change["change_detected_ms"] / 1000, zone
                            ).isoformat(timespec="seconds")
                            if change["change_detected_ms"] is not None
                            else None,
                        }
                    )
                if chunk_end >= window.end_ms:
                    break
                t = chunk_end
        if failures and len(failures) == len(groups):
            raise self._all_failed(failures)
        rows.sort(key=lambda r: r["change_detected_ms"] or 0, reverse=True)
        return rows

    # ── W5·W6 도구(plans/134 N-13 · A-1~A-3) ───────────────────

    def _at(self, ms: int) -> str:
        """epoch ms → 사람이 읽는 시각(APM_TIMEZONE · 초 단위)."""
        return datetime.fromtimestamp(ms / 1000, ZoneInfo(self._tz)).isoformat(timespec="seconds")

    @staticmethod
    def _guid(value: Any) -> str:
        """GUID 인자 — 앞뒤 공백 제거 뒤 1~256자 · 공백·제어·서식 문자 없음. GUID 형식은 미검증이라
        (W10) 그 밖 형식 제한은 두지 않는다. 길이를 먼저 본다(긴 입력을 끝까지 훑지 않는다)."""
        text = str(value).strip() if value is not None else ""
        if not text or len(text) > GUID_MAX:
            raise ApmError(
                INVALID_ARGUMENT, f"guid는 공백 제거 뒤 1~{GUID_MAX}자여야 한다({len(text)}자)"
            )
        if any(ch.isspace() or unicodedata.category(ch) in ("Cc", "Cf") for ch in text):
            raise ApmError(INVALID_ARGUMENT, "guid에 공백·제어 문자를 둘 수 없다")
        return text

    def _trace_window(
        self,
        reference_time: str | None,
        lookback_minutes: int | None,
        around_ms: int | None,
        around_minutes: int | None,
        limits: _Limits,
    ) -> Window:
        """GUID 추적 창 — 명시 기간 > 앞 결과 시각(`around_ms`) ± `around_minutes`(기본 5) > 최근
        60분. 명시 기간이 아니면 그 사실을 `[한계]`로 적는다(N-13). `around_ms` 없이 받은
        `around_minutes`는 쓰지 않고 그 사실을 적는다(VG-3)."""
        if around_minutes is not None and around_ms is None:
            limits.append("[한계] around_minutes는 around_ms 없이 쓰지 않았다")
        if reference_time is not None or lookback_minutes is not None:
            window = self.window(
                reference_time, lookback_minutes, default_minutes=TRACE_DEFAULT_MINUTES
            )
            assert window is not None
            return window
        if around_ms is not None:
            center = _positive_int(around_ms, "around_ms", 0)
            minutes = _positive_int(around_minutes, "around_minutes", TRACE_AROUND_MINUTES)
            limits.append(f"[한계] 기간 미지정 — ±{minutes}분(앞 결과 시각 기준)")
            return Window(center - minutes * 60_000, center + minutes * 60_000, False, self._tz)
        limits.append(f"[한계] 기간 미지정 — 최근 {TRACE_DEFAULT_MINUTES}분")
        window = self.window(None, TRACE_DEFAULT_MINUTES, default_minutes=TRACE_DEFAULT_MINUTES)
        assert window is not None
        return window

    async def apm_transaction_trace(
        self,
        guid: str,
        hostname: str | None = None,
        reference_time: str | None = None,
        lookback_minutes: int | None = None,
        around_ms: int | None = None,
        around_minutes: int | None = None,
        source_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """GUID가 같은 거래 묶음(plans/134 W5 N-13 · A-3). 범위 = `hostname`의 정합 (소스, 도메인) ·
        없으면 고른 소스의 전 도메인 — 도메인마다 1호출. (소스, 도메인, txid)로 중복을 지우고 원천
        시작 시각 순으로 `trace_order`를 매긴다. 호출 관계는 만들지 않는다."""
        tool = "apm_transaction_trace"
        gid = self._guid(guid)
        limits = _Limits(
            ["[한계] GUID가 같은 거래 묶음이다 — 호출 관계(토폴로지)를 뜻하지 않는다"]
        )
        window = self._trace_window(
            reference_time, lookback_minutes, around_ms, around_minutes, limits
        )
        res: Resolution | None = None
        statuses: list[dict[str, Any]] | None = None
        source_partial = False
        if hostname is not None and str(hostname).strip():
            res = await self._resolve(hostname, None, source_ids)
            groups = list(_group(res))
        else:
            usable, statuses, found = await self.sources.available(
                self.sources.select(source_ids)
            )
            limits.extend(found)
            source_partial = any(row["status"] != STATUS_OK for row in statuses)
            groups = [
                (src.source_id, d["domain_id"])
                for src, inv in usable
                for d in inv.domains
                if d.get("domain_id") is not None
            ]
        expect_calls(len(groups))
        hits: list[dict[str, Any]] = []
        failures: list[tuple[str, ApmError]] = []
        foreign = 0
        for sid, domain_id in groups:
            where = self.sources.where(sid, domain_id)
            try:
                batch = await self._api(sid).transactions_by_guid(
                    domain_id, gid, window.start_ms, window.end_ms
                )
            except ApmError as e:
                if e.code == CONTRACT_VIOLATION:
                    raise
                failures.append((where, e))
                limits.fail(f"[한계] GUID 거래 조회 실패({where}): {e.code}" + self._reason_tail(e))
                continue
            for tx in _tag(batch, sid, domain_id):
                if tx.get("guid") and tx["guid"] != gid:
                    foreign += 1  # 원천이 다른 GUID를 섞어 돌려줬다 — 묶음이 아니다
                    continue
                hits.append(tx)
        if failures and len(failures) == len(groups):
            raise self._all_failed(failures)
        seen: set[tuple[str, Any, str]] = set()
        unique: list[dict[str, Any]] = []
        for tx in hits:
            if tx.get("txid"):
                key = (tx["source_id"], tx.get("domain_id"), tx["txid"])
                if key in seen:
                    continue
                seen.add(key)
            unique.append(tx)
        # 히트가 걸친 (소스, 도메인)은 질의 묶음이 아니라 중복 제거 뒤 행으로 센다 — 원천이
        # domain_id를 무시하고 같은 거래를 두 도메인 질의에 돌려줘도 한 번이다(VG-6)
        with_hits = {(tx["source_id"], tx.get("domain_id")) for tx in unique}
        unique.sort(
            key=lambda t: (
                t.get("start_time_ms") is None,
                t.get("start_time_ms") or 0,
                t["source_id"],
                t.get("domain_id") or 0,
                t.get("txid") or "",
            )
        )
        rows = [
            {
                "source_id": tx["source_id"],
                **_tx_fields(tx),
                "profile_ref": _profile_ref(tx, "end_time_ms"),
                "trace_order": order,
            }
            for order, tx in enumerate(unique, 1)
        ]
        if len(with_hits) > 1:
            limits.append(
                "[한계] 소스·도메인 시계 차이를 보정하지 않았다 — 순서는 각 원천 시각 기준"
            )
        if foreign:
            limits.append(
                f"[한계] 원천이 다른 GUID의 거래 {foreign}건을 함께 돌려줘 뺐다 — guid 인자 의미"
                " 확인 필요(W10)"
            )
        if not rows:
            limits.append(
                "[한계] 구간 안에서 GUID 거래를 찾지 못했다"
                f"(구간 {self._at(window.start_ms)}~{self._at(window.end_ms)})"
            )
        starts = [t["start_time_ms"] for t in unique if t.get("start_time_ms") is not None]
        ends = [t["end_time_ms"] for t in unique if t.get("end_time_ms") is not None]
        first = min(starts) if starts else None
        last = max(ends) if ends else None
        summary = {
            "guid": gid,
            "transactions": len(rows),
            "domains_queried": len(groups),
            "domains_with_hits": len(with_hits),
            "domains_failed": len(failures),
            "sources": [sid for sid in self.sources.ids if any(g[0] == sid for g in with_hits)],
            "first_start_ms": first,
            "last_end_ms": last,
            "span_ms": (last - first) if first is not None and last is not None else None,
            "instances": list(
                dict.fromkeys(t["instance_name"] for t in unique if t.get("instance_name"))
            ),
        }
        return self.ok(
            tool,
            rows,
            resolution=res,
            window=window,
            limits=limits,
            sources=statuses,
            partial=limits.partial or source_partial,
            file_only=FILE_ONLY_COLUMNS,
            masked=_identifier_fields(unique),
            summary=summary,
        )

    async def _impact_side(
        self,
        res: Resolution,
        start_ms: int,
        end_ms: int,
        *,
        inclusive_end: bool,
        what: str,
        limits: _Limits,
    ) -> tuple[dict[str, Any], int, int]:
        """변경 전/후 구간 1개(인스턴스 1개)의 지표 → (지표, 원천 시도 수, 실패 수). X-View(호출·
        오류·응답시간 — 1분 조각)와 오류 기록을 따로 받고, 실패한 원천의 칸만 None으로 둔다
        (0으로 세지 않는다 · 조각 하나라도 실패하면 X-View 칸 전부 None)."""
        inst = res.instances[0]
        sid, domain_id = inst["source_id"], inst["domain_id"]
        where = self.sources.where(sid, domain_id)
        win = Window(start_ms, end_ms if inclusive_end else end_ms - 1, False, self._tz)
        side: dict[str, Any] = {"start_ms": start_ms, "end_ms": end_ms}
        fails = 0
        sub = _Limits()
        txs, ok = await self._xview(res, win, sub)
        limits.extend(sub)
        if sub.partial or not ok:
            fails += 1
            limits.fail(f"[한계] {what} X-View 조회 실패 — 호출·오류·응답시간 N/A")
            side.update(dict.fromkeys(_XVIEW_SIDE_METRICS))
        else:
            times = [t["response_time_ms"] for t in txs if t.get("response_time_ms") is not None]
            errors = sum(1 for t in txs if t.get("error_type"))
            side.update(
                calls=len(txs),
                tx_errors=errors,
                error_rate=an.rate(errors, len(txs)),
                avg_response_ms=an.weighted_mean(float(sum(times)), len(times)),
                p95_response_ms=an.p95(times),
                max_response_ms=max(times) if times else None,
            )
        expect_calls(1)
        try:
            records = await self._errors(
                self._api(sid), domain_id, [inst["instance_id"]], win, None, where, limits
            )
        except ApmError as e:
            if e.code == CONTRACT_VIOLATION:
                raise
            fails += 1
            limits.fail(
                f"[한계] {what} 오류 기록 조회 실패({where}): {e.code}" + self._reason_tail(e)
            )
            side.update(error_records=None, errors_by_type=None)
        else:
            mine = [r for r in _tag(records, sid, domain_id) if _key(r) == _key(inst)]
            side.update(
                error_records=len(mine),
                errors_by_type=[
                    {"error_type": k, "count": v}
                    for k, v in Counter(r["error_type"] or "(없음)" for r in mine).most_common()
                ],
            )
        return side, 2, fails

    async def apm_change_impact(
        self,
        hostname: str,
        reference_time: str | None = None,
        lookback_minutes: int | None = None,
        width_minutes: int | None = None,
        source_ids: list[str] | None = None,
        n: int | None = None,
        full: bool = False,
    ) -> dict[str, Any]:
        """소스 변경 감지 전후 비교(plans/134 W6 A-2 · F-09). 변경 목록은 `apm_source_changes`와
        같은 로직이고, 변경(인스턴스 단위)마다 전 `[t−w, t)` · 후 `[t, min(t+w, 지금))`의 그
        인스턴스 지표를 받아 증감을 낸다. 동반 변화일 뿐 원인 확정이 아니다."""
        tool = "apm_change_impact"
        width = _positive_int(width_minutes, "width_minutes", CHANGE_WIDTH_MINUTES)
        top_n = _check_n(n, default=None)
        res = await self._resolve(hostname, None, source_ids)
        window = self.window(
            reference_time, lookback_minutes, default_minutes=CHANGES_DEFAULT_MINUTES
        )
        assert window is not None
        limits = _Limits(
            [
                "[한계] 변경 감지 시각 전후의 동반 변화다 — 원인 확정이 아니다",
                CHANGE_DETECTION_NOTE,
            ]
        )
        changes = await self._change_rows(res, window, limits)
        selected = _select(changes, top_n, full)
        if not changes:
            limits.append("[한계] 구간 안 변경 감지 0건")
        elif len(selected) < len(changes):
            limits.append(
                f"[한계] 변경 감지 {len(changes)}건 중 최근 {len(selected)}건을 비교했다(n)"
                " — 전부는 full"
            )
        by_key = {_key(i): i for i in res.instances}
        now_ms = int(self.clock() * 1000)
        w_ms = width * 60_000
        rows: list[dict[str, Any]] = []
        attempts = fails = 0
        for change in selected:
            t = change["change_detected_ms"]
            label = f"{change['instance_name']} {change['change_detected_at'] or ''}".strip()
            if t is None:
                limits.fail(f"[한계] 감지 시각이 없는 변경은 비교하지 못했다({label})")
                continue
            one = Resolution(
                res.hostname,
                [by_key[(change["source_id"], change["instance_id"])]],
                res.confidence,
                res.reason,
            )
            before, tried, failed = await self._impact_side(
                one, t - w_ms, t, inclusive_end=False, what=f"{label} 변경 전", limits=limits
            )
            attempts, fails = attempts + tried, fails + failed
            after_end = min(t + w_ms, now_ms)
            if after_end - t < w_ms:
                limits.append(
                    f"[한계] 변경 뒤 구간이 아직 {round(max(0, after_end - t) / 60_000, 1):g}분이다"
                    f"(비교 폭 {width}분) — {label}"
                )
            if after_end > t:
                after, tried, failed = await self._impact_side(
                    one,
                    t,
                    after_end,
                    inclusive_end=after_end == now_ms,
                    what=f"{label} 변경 후",
                    limits=limits,
                )
                attempts, fails = attempts + tried, fails + failed
            else:  # 감지 시각이 지금 이후(시계 차이) — 뒤 구간이 없다
                after = {
                    "start_ms": t,
                    "end_ms": t,
                    **dict.fromkeys(_XVIEW_SIDE_METRICS),
                    "error_records": None,
                    "errors_by_type": None,
                }
            rows.append(
                {
                    "source_id": change["source_id"],
                    "domain_id": change["domain_id"],
                    "instance_id": change["instance_id"],
                    "instance_name": change["instance_name"],
                    "change_detected_ms": t,
                    "change_detected_at": change["change_detected_at"],
                    "width_minutes": width,
                    "before": before,
                    "after": after,
                    "delta": {
                        m: (an.rate_delta if m == "error_rate" else an.delta)(after[m], before[m])
                        for m in _IMPACT_METRICS
                    },
                }
            )
        if attempts and fails == attempts:
            raise ApmError(
                SOURCE_UNAVAILABLE, "변경 전후 지표 조회가 모두 실패했다: " + "; ".join(limits)
            )
        return self.ok(
            tool,
            rows,
            resolution=res,
            window=window,
            limits=limits,
            partial=limits.partial,
            summary={
                "changes": len(changes),
                "compared": len(rows),
                "window": window.as_dict(),
                "width_minutes": width,
            },
        )

    def _absolute_ms(self, value: Any, name: str) -> int:
        """명시 시각(ISO 8601 · 시간대 없으면 APM_TIMEZONE) → epoch ms. 비었거나 형식 밖이면
        `invalid_argument`."""
        text = str(value).strip() if value is not None else ""
        if not text:
            raise ApmError(INVALID_ARGUMENT, f"{name}가 필요하다(ISO 8601)")
        try:
            at = datetime.fromisoformat(text)
        except ValueError as e:
            raise ApmError(
                INVALID_ARGUMENT, f"{name}는 ISO 8601이어야 한다: {text[:64]!r}"
            ) from e
        if at.tzinfo is None:
            at = at.replace(tzinfo=ZoneInfo(self._tz))
        try:
            ms = int(at.timestamp() * 1000)
        except (OverflowError, ValueError, OSError):
            ms = -1
        # 시 경계로 넓힌 뒤에도 시각으로 다시 나타낼 수 있는 범위만(9999-12-31 근처는 올림에서
        # 넘친다 — 내부 오류·스택 대신 인자 오류 · plans/134 W7 I-2)
        if not 0 <= ms < _ABSOLUTE_MAX_MS:
            raise ApmError(
                INVALID_ARGUMENT, f"{name}는 1970~9998년 범위여야 한다: {text[:64]!r}"
            )
        return ms

    @staticmethod
    def _period_stats(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], bool]:
        """시 단위 애플리케이션 행 → 구간 합계(호출·실패·실패율·가중 평균·최대 · 원자료
        `total_response_ms`). (합계, 가중 평균 재료가 빠졌는가) — 호출이 있는 행에 총 응답시간
        칸이 없으면 평균은 None(계산 불가)이다."""
        calls = sum(r["calls"] for r in rows)
        failures = sum(r["failures"] for r in rows)
        missing = any(r.get("total_response_ms") is None for r in rows if r["calls"])
        total = None if missing else float(sum(r.get("total_response_ms") or 0 for r in rows))
        maxima = [
            r["max_response_time_ms"] for r in rows if r.get("max_response_time_ms") is not None
        ]
        return {
            "calls": calls,
            "failures": failures,
            "failure_rate": an.rate(failures, calls),
            "avg_response_ms": an.weighted_mean(total, calls),
            "max_response_ms": max(maxima) if maxima else None,
            "total_response_ms": total,
            "application_count": len(rows),
        }, missing

    @staticmethod
    def _sum_stats(parts: list[dict[str, Any]]) -> dict[str, Any]:
        """인스턴스 합계 → 전체 합계(가중 평균 = Σ총 응답시간 ÷ Σ호출 — 재료가 하나라도 없으면
        None)."""
        calls = sum(p["calls"] for p in parts)
        failures = sum(p["failures"] for p in parts)
        totals = [p["total_response_ms"] for p in parts if p["calls"]]
        total = None if any(t is None for t in totals) else float(sum(totals))
        maxima = [p["max_response_ms"] for p in parts if p["max_response_ms"] is not None]
        return {
            "calls": calls,
            "failures": failures,
            "failure_rate": an.rate(failures, calls),
            "avg_response_ms": an.weighted_mean(total, calls),
            "max_response_ms": max(maxima) if maxima else None,
            "total_response_ms": total,
        }

    @staticmethod
    def _stats_delta(
        current: dict[str, Any] | None, baseline: dict[str, Any] | None
    ) -> dict[str, dict[str, float | None]]:
        """지표별 증감(실패율은 %p) — 한쪽이 없으면 N/A(0으로 채우지 않는다)."""

        def get(d: dict[str, Any] | None, k: str) -> Any:
            return d.get(k) if d else None

        return {
            m: (an.rate_delta if m == "failure_rate" else an.delta)(
                get(current, m), get(baseline, m)
            )
            for m in _PERIOD_METRICS
        }

    async def apm_period_compare(
        self,
        hostname: str,
        current_start: str | None = None,
        current_end: str | None = None,
        baseline_start: str | None = None,
        baseline_end: str | None = None,
        source_ids: list[str] | None = None,
        n: int | None = None,
        full: bool = False,
    ) -> dict[str, Any]:
        """두 명시 구간 비교(plans/134 W6 A-1 — 조사 소비 · 채팅 배선 없음). 시 단위 애플리케이션
        통계를 인스턴스·구간마다 받아(시 경계로 넓힌다) 호출·실패·실패율·가중 평균·최대와 증감을
        낸다. p95는 시 단위 통계에 분포가 없어 싣지 않는다. 한쪽에만 있는 인스턴스는 N/A다."""
        tool = "apm_period_compare"
        top_n = _check_n(n, default=None)
        requested: dict[str, tuple[int, int]] = {}
        for period, start_raw, end_raw in (
            ("current", current_start, current_end),
            ("baseline", baseline_start, baseline_end),
        ):
            start = self._absolute_ms(start_raw, f"{period}_start")
            end = self._absolute_ms(end_raw, f"{period}_end")
            if start >= end:
                raise ApmError(
                    INVALID_ARGUMENT, f"{period}_start는 {period}_end보다 앞이어야 한다"
                )
            requested[period] = (start, end)
        res = await self._resolve(hostname, None, source_ids)
        limits = _Limits()
        hours: dict[str, tuple[int, int]] = {}
        for period, (start, end) in requested.items():
            hours[period] = (_hour_floor(start), _hour_ceil(end))
            span = f"{self._at(hours[period][0])}~{self._at(hours[period][1])}"
            limits.append(
                f"[한계] 시 단위 통계 — {_PERIOD_LABELS[period]} 구간을 시 경계로 맞췄다"
                + (
                    f"(요청 {self._at(start)}~{self._at(end)} → 조회 {span})"
                    if hours[period] != (start, end)
                    else f"({span})"
                )
            )
        lengths = {p: (e - s) / 3_600_000 for p, (s, e) in hours.items()}
        if lengths["current"] != lengths["baseline"]:
            limits.append(
                "[한계] 두 구간 길이가 다르다 — 합계 비교 주의(평균·비율은 비교 가능)"
                f"(현재 {lengths['current']:g}시간 · 기준 {lengths['baseline']:g}시간)"
            )
        limits.append(
            "[한계] p95는 싣지 않았다 — 시 단위 통계에는 응답시간 분포가 없다(구간 p95의 평균은"
            " 계산하지 않는다)"
        )
        limits.append(
            "[한계] 시 단위 통계 행 수는 서버 기본값이다(미공개 — W10) · 합계는 받은 전"
            " 애플리케이션 행으로 냈다"
        )
        stats: dict[tuple[InstKey, str], dict[str, Any] | None] = {}
        failed: dict[str, list[str]] = {"current": [], "baseline": []}
        failures: list[tuple[str, ApmError]] = []
        unweighted: list[str] = []
        expect_calls(len(res.instances) * len(hours))
        for inst in res.instances:
            label = self._inst_label(_key(inst))
            for period, (start, end) in hours.items():
                try:
                    app_rows = await self._api(inst["source_id"]).application_status(
                        inst["domain_id"], [inst["instance_id"]], start, end
                    )
                except ApmError as e:
                    if e.code == CONTRACT_VIOLATION:
                        raise
                    failures.append((f"인스턴스 {label} {_PERIOD_LABELS[period]} 구간", e))
                    failed[period].append(label)
                    limits.fail(
                        f"[한계] {_PERIOD_LABELS[period]} 구간 통계 조회 실패(인스턴스 {label}):"
                        f" {e.code}" + self._reason_tail(e)
                    )
                    stats[(_key(inst), period)] = None
                    continue
                if not app_rows:
                    stats[(_key(inst), period)] = None
                    limits.append(
                        f"[한계] 인스턴스 {inst['instance_name'] or label}은"
                        f" {_PERIOD_LABELS[period]} 구간 통계 행이 없다 — 증감 N/A(0으로 채우지"
                        " 않았다)"
                    )
                    continue
                stat, missing = self._period_stats(app_rows)
                stats[(_key(inst), period)] = stat
                if missing:
                    unweighted.append(f"{label} {_PERIOD_LABELS[period]}")
        if failures and len(failures) == len(res.instances) * len(hours):
            raise self._all_failed(failures)
        if unweighted:
            limits.append(
                "[한계] 평균 응답시간 계산 불가 — 총 응답시간 칸이 없는 통계 행이 있다(가중 평균"
                f" 재료 없음 · {', '.join(unweighted)})"
            )
        summary: dict[str, Any] = {}
        overall: dict[str, dict[str, Any] | None] = {}
        for period, (start, end) in requested.items():
            present = [
                s for i in res.instances if (s := stats.get((_key(i), period))) is not None
            ]
            if failed[period]:
                overall[period] = None
                limits.append(
                    f"[한계] {_PERIOD_LABELS[period]} 구간 전체 합계 계산 불가 — 조회 실패"
                    f" 인스턴스 {', '.join(failed[period])}(0으로 세지 않았다)"
                )
            elif not present:
                overall[period] = None
            else:
                overall[period] = self._sum_stats(present)
            summary[period] = {
                "start": self._at(start),
                "end": self._at(end),
                "hour_start": self._at(hours[period][0]),
                "hour_end": self._at(hours[period][1]),
                "hours": lengths[period],
                **(overall[period] or dict.fromkeys((*_PERIOD_METRICS, "total_response_ms"))),
            }
        summary["delta"] = self._stats_delta(overall["current"], overall["baseline"])
        summary["instances"] = len(res.instances)
        rows = [
            {
                **_inst_meta(inst),
                "current": stats.get((_key(inst), "current")),
                "baseline": stats.get((_key(inst), "baseline")),
                "delta": self._stats_delta(
                    stats.get((_key(inst), "current")), stats.get((_key(inst), "baseline"))
                ),
            }
            for inst in res.instances
        ]
        rows.sort(
            key=lambda r: (
                r["current"] is None,
                -(r["current"]["calls"] if r["current"] else 0),
                r["source_id"],
                r["instance_id"],
            )
        )
        shown = _select(rows, top_n, full)
        if len(shown) < len(rows):
            limits.append(
                f"[한계] 인스턴스 {len(rows)}개 중 현재 구간 호출 수 상위 {len(shown)}개 행만"
                " 실었다 — summary는 전 인스턴스 합계 · 전부는 full"
            )
        return self.ok(
            tool,
            shown,
            resolution=res,
            limits=limits,
            partial=limits.partial,
            summary=summary,
        )

    async def _source_health(self, src: JenniferSource) -> tuple[dict[str, Any], list[str]]:
        body: dict[str, Any] = {
            "source_id": src.source_id,
            "status": "ok",
            "jennifer_configured": src.api.configured,
            "jennifer_reachable": False,
            "domain_count": None,
            "allowlist_size": len(ALLOWED),
        }
        limits: list[str] = []
        where = "" if len(self.sources) == 1 else f"소스 {src.source_id} "
        try:
            domains = await src.api.domains()
            body["jennifer_reachable"] = True
            body["domain_count"] = len(domains)
            if not domains:
                body["status"] = "degraded"
                limits.append(f"[한계] {where}APM 도메인 0건 — 에이전트 미접속·라이선스 확인")
        except ApmError as e:
            body["status"] = "degraded"
            limits.append(f"[한계] {where}APM API 조회 실패: {e.code}")
        body["api_calls_total"] = src.api.calls_total
        return body, limits

    async def gateway_health(self) -> dict[str, Any]:
        """소스별 행 · 전체 상태 `ok`(모두 정상) / `degraded`(하나라도) / `not_configured`(0개)."""
        tool = "gateway_health"
        now = self.clock()
        if self._health_cache and now - self._health_cache[0] < _HEALTH_CACHE_SECONDS:
            cached = dict(self._health_cache[1])
            cached["poller"] = self.poller_status() if self.poller_status else {"enabled": False}
            return cached
        results = await asyncio.gather(*(self._source_health(src) for src in self.sources))
        rows = [body for body, _ in results]
        limits = [line for _, lines in results for line in lines]
        if not rows:
            status = "not_configured"
            limits.append("[한계] APM 소스 미설정 — JENNIFER_API_URL 또는 JENNIFER_SOURCES")
        else:
            status = "ok" if all(r["status"] == "ok" for r in rows) else "degraded"
        result = self.ok(tool, rows, limits=limits, status=status)
        self._health_cache = (now, result)
        result = dict(result)
        result["poller"] = self.poller_status() if self.poller_status else {"enabled": False}
        return result
