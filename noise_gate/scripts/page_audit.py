"""분류 정책 분석 로그 (plans/162 W0 §10 · D-330 ⑥).

「지금 정책이 알람마다 어떻게 판정했고, PAGE는 어느 경로·어떤 반복에서 나왔으며, §4 규칙을 적용하면
몇 건이 되는가」를 한 번의 실행으로 낸다. 제품 코드(워커·게이트·도메인)는 바꾸지 않는 별도
스크립트이고, 내부망 서버에서 사람이 한 번씩 돌린다.

- **읽기 전용** — 판정 감사 파일은 읽기만 한다. Redis는 `XRANGE`·`XINFO GROUPS`·`XPENDING`만
  쓴다(`ReadOnlyStreams`가 그 셋만 노출한다 — 소비자 그룹·소비 위치를 건드리지 않는다).
  Redis 계정은 읽기 전용 ACL(`+xrange +xinfo +xpending`)을 권장한다.
  DB·LLM·게이트웨이는 부르지 않는다.
- **비식별 산출물만** — 알람 단위 행은 `page_audit_sanitize.Sanitizer`(허용 목록)를 거친다. 산출물은
  임시 폴더에 쓰고 누출 검사를 통과해야 확정 폴더로 옮긴다. 걸리면 `leak_check.json`(파일·종류·
  건수만)만 남기고 종료 코드 2.
- **정책 스냅샷**은 코드 기본값이 아니라 `AppConfig()` 실제 해석값이다(D-127).

흐름: 읽기(감사 · 스트림 · dead) → alarm_id로 맞대기 → 처분 분류 → 파생 특징 → what-if 시뮬레이션
→ 비식별 행 → 집계 S0~S10 → 폴스타↔APM 상관 진단(행 `xs` · S11~S14 — `page_audit_xsource` · §11)
→ 누출 검사 → 파일 쓰기(`<out>/<YYYYMMDD-HHMMSS>/`).

what-if 재판정 R1~R3은 도메인 `noise_gate.domain.page_review`(`PageRegistry` + `review_page`,
enforce 가정)를 그대로 부른다 — 규칙 정의의 출처가 하나다. 생성 경로 규칙(R4·R5)은
`generation_tier`, 만성 규칙(R6)은 이 스크립트의 `_r6`이다.

`is_message_alarm`(application `alarm_analyzer`) import로 `src.llm`이 함께 올라오지만 이 스크립트는
LLM을 부르지 않는다(메시지형 판별 규칙을 워커와 같게 쓰려고 그 함수만 쓴다).

사용:
    python -m noise_gate.scripts.page_audit --dry-run
    python -m noise_gate.scripts.page_audit --hours 24 --days 7
    python -m noise_gate.scripts.page_audit --hours 24 --no-stream \\
        [--decisions logs/alarm_decisions.jsonl] [--out results/page_audit] [--summary-only]
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections import Counter, defaultdict, deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import noise_gate.domain.notification_policy as _policy_module
from noise_gate.application.nodes.alarm_analyzer import is_message_alarm
from noise_gate.application.server_identity import is_apm_source, zone_labels_for
from noise_gate.domain.alarm import AlarmEvent
from noise_gate.domain.notification_policy import (
    STAGE_ANNOTATION,
    STAGE_COLLECTION_FAILED,
    STAGE_CORRELATION,
    STAGE_CROSS_SOURCE,
    STAGE_DEPENDENCY,
    STAGE_FLAPPING,
    STAGE_INHIBITION,
    STAGE_MAINTENANCE,
    STAGE_MATRIX,
    STAGE_NON_ALARM,
    STAGE_ORDER,
    STAGE_PAGE_REVIEW,
    STAGE_RESOLVED,
    STAGE_SELF_HEAL,
    STAGE_SEVERITY3,
    STAGE_SILENCE,
    STAGE_STORM,
    STAGE_UNKNOWN,
    TIER_DASHBOARD,
    TIER_PAGE,
    TIER_SUPPRESS,
    TIER_TICKET,
    NotificationDecision,
    _matrix_tier,
)
from noise_gate.domain.page_review import (
    EVIDENCE_KEY,
    MODE_ENFORCE,
    RULE_FANOUT,
    RULE_OPEN_REPEAT,
    RULE_SAME_HOST,
    PageRegistry,
    PageReviewSignals,
    fanout_key_of,
    host_key_of,
    review_page,
)
from noise_gate.domain.severity import coerce_severity
from noise_gate.domain.severity_signatures import scan_signature_severity
from noise_gate.scripts import page_audit_xsource as xsource
from noise_gate.scripts.page_audit_sanitize import (
    IDENT_ALARM_ID,
    IDENT_HOST,
    IDENT_IP,
    IDENT_RESOURCE,
    KST,
    AlarmContext,
    IdentifierSet,
    Pseudonymizer,
    Sanitizer,
    leak_check,
    new_run_key,
    policy_snapshot,
    record_type_label,
    vocabulary_words,
    xs_vocabulary,
)

REPO = Path(__file__).resolve().parents[2]

TIERS: tuple[str, ...] = (TIER_PAGE, TIER_TICKET, TIER_DASHBOARD, TIER_SUPPRESS)
DISPOSITIONS: tuple[str, ...] = (
    "decided",
    "dedup_fingerprint",
    "below_min_severity",
    "dead_letter",
    "pending",
    "unaccounted",
    "decision_without_raw",
)
SOURCE_POLESTAR = "polestar"
SOURCE_JENNIFER = "jennifer"

# 처분 중 스트림이 있어야 가릴 수 있는 것 — `--no-stream`이면 0이 아니라 null(모름)이다.
STREAM_DISPOSITIONS = frozenset({
    "below_min_severity", "dead_letter", "pending", "unaccounted", "decision_without_raw",
})

# what-if 규칙 매개변수(§10.7 — W1 기본 권고값으로 시뮬레이션한다 · 항상 PAGE 목록은 빈 값).
R1_REMINDER_S = 24 * 3600
R1_REOPEN_QUIET_S = 30 * 60
R2_HOST_WINDOW_S = 30 * 60
R3_FANOUT_WINDOW_S = 10 * 60
R3_FANOUT_PAGE_MAX = 1
R6_CHRONIC_DAYS = 5
R6_LOOKBACK_DAYS = 7
GENERATION_RULES: tuple[str, ...] = ("R4", "R5")
DOMAIN_RULES: tuple[str, ...] = (RULE_OPEN_REPEAT, RULE_SAME_HOST, RULE_FANOUT)
REVIEW_RULES: tuple[str, ...] = (*DOMAIN_RULES, "R6")
# 누적 적용 순서 — 생성 경로(R4·R5) 먼저, 재판정(R1→R2→R3 도메인 · R6) 뒤.
CUMULATIVE_RULES: tuple[str, ...] = (*GENERATION_RULES, *REVIEW_RULES)
# 도메인 `review_page`에 주는 설정(page_review_* 7필드 · enforce 가정).
_SIM_CONFIG = SimpleNamespace(
    page_review_mode=MODE_ENFORCE,
    page_review_reminder_hours=R1_REMINDER_S // 3600,
    page_review_reopen_quiet_minutes=R1_REOPEN_QUIET_S // 60,
    page_review_host_window_minutes=R2_HOST_WINDOW_S // 60,
    page_review_fanout_window_minutes=R3_FANOUT_WINDOW_S // 60,
    page_review_fanout_page_max=R3_FANOUT_PAGE_MAX,
    page_review_always_page_globs=[],
)
# 끈 규칙이 첫 적중이면 그 규칙의 재료만 비우고 다시 본다(「그 규칙만 켠」 단독 수치).
_RULE_SIGNAL_KEY: dict[str, str] = {
    RULE_OPEN_REPEAT: "fingerprint_page",
    RULE_SAME_HOST: "host_pages",
    RULE_FANOUT: "fanout_members",
}
# 강등이 가리키는 대표 PAGE(근거 블록 키)와 묶음 창(초) — 고아 사후 검증용.
_REP_LINK: dict[str, tuple[str, float]] = {
    RULE_OPEN_REPEAT: ("last_page_alarm_id", float(R1_REMINDER_S)),
    RULE_SAME_HOST: ("host_page_alarm_id", float(R2_HOST_WINDOW_S)),
    RULE_FANOUT: ("representative_alarm_id", float(R3_FANOUT_WINDOW_S)),
}
# 스트림 읽기 상한(항목 수 · 스트림별) — 7일 운영량을 넉넉히 덮는 값. 넘으면 멈추고 안내한다.
DEFAULT_MAX_STREAM_ENTRIES = 500_000

# 읽기 창 — R6(7일 중 PAGE 일수)·R1(24h 리마인더)을 창 첫머리에서도 판정하려고 7일 이력을 더 읽는다.
HISTORY_MARGIN = timedelta(days=R6_LOOKBACK_DAYS)
# 창 경계에서 수신과 판정이 갈리는 경우를 맞대려고 스트림을 10분 더 읽는다(행은 창 안 수신만).
RAW_SLACK = timedelta(minutes=10)
# 같은 alarm_id의 판정 레코드를 수신에 맞댈 때 허용하는 시차(수신 60초 전 ~ 1시간 뒤).
MATCH_BEFORE_S = 60.0
MATCH_AFTER_S = 3600.0
STREAM_PAGE = 1000
DEDUP_WINDOW_DEFAULT_S = 14400
BURST_PER_MINUTE = 5
BURST_WINDOW_S = 60.0
TOP_FINGERPRINTS = 20
HOST_CLUSTER_WINDOW_S = 30 * 60

# 단계 → 활성 플래그(정책 스냅샷의 NoiseGateConfig 필드). 상시 단계는 따로 둔다.
_STAGE_FLAGS: dict[str, str] = {
    STAGE_NON_ALARM: "non_alarm_filter_enabled",
    STAGE_SILENCE: "silence_enabled",
    STAGE_DEPENDENCY: "dependency_suppression",
    STAGE_INHIBITION: "inhibition_enabled",
    STAGE_FLAPPING: "flapping_enabled",
    STAGE_STORM: "storm_grouping_enabled",
    STAGE_CORRELATION: "cross_host_correlation_enabled",
    STAGE_ANNOTATION: "annotation_planned_suppress",
}
_ALWAYS_STAGES = frozenset({
    STAGE_SEVERITY3,
    STAGE_SELF_HEAL,
    STAGE_RESOLVED,
    STAGE_COLLECTION_FAILED,
    STAGE_MAINTENANCE,
    STAGE_MATRIX,
})
_CLEAR_STAGES = frozenset({STAGE_RESOLVED, STAGE_SELF_HEAL})
_VALID_IMPORTANCE = frozenset({"높음", "보통", "낮음"})


# ── Redis 읽기 전용 래퍼 ──────────────────────────────────────────────────────
class ReadOnlyStreams:
    """스트림 읽기 명령 3종(`XRANGE`·`XINFO GROUPS`·`XPENDING`)만 노출하는 래퍼.

    쓰기·소비 명령(`XADD`·`XACK`·`XREADGROUP`·`XGROUP CREATE` 등)은 메서드 자체가 없다 — 운영 워커의
    소비 위치를 코드 수준에서 건드릴 수 없게 한다. 원 클라이언트는 속성으로 두지 않고 읽기 함수
    3개의 클로저에만 담는다(`reader._client.xadd(...)` 같은 우회 경로가 없다).
    """

    __slots__ = ("_read",)

    def __init__(self, client: Any) -> None:
        """동기 Redis 클라이언트(또는 같은 모양의 가짜)를 받는다."""

        def _xrange(name: str, min: str, max: str, count: int | None) -> Any:
            return client.xrange(name, min=min, max=max, count=count)

        def _xinfo_groups(name: str) -> Any:
            return client.xinfo_groups(name)

        def _xpending(name: str, groupname: str) -> Any:
            return client.xpending(name, groupname)

        self._read = (_xrange, _xinfo_groups, _xpending)

    def xrange(
        self, name: str, min: str = "-", max: str = "+", count: int | None = None
    ) -> list[tuple[Any, dict[Any, Any]]]:
        """`XRANGE` — (id, 필드) 목록."""
        return list(self._read[0](name, min, max, count))

    def xinfo_groups(self, name: str) -> list[dict[Any, Any]]:
        """`XINFO GROUPS` — 소비자 그룹 목록."""
        return list(self._read[1](name))

    def xpending(self, name: str, groupname: str) -> dict[Any, Any]:
        """`XPENDING` 요약."""
        return dict(self._read[2](name, groupname))


def open_streams(redis_cfg: Any) -> ReadOnlyStreams:
    """`AppConfig().redis` 설정으로 동기 클라이언트를 열어 읽기 전용 래퍼로 감싼다."""
    import redis

    client = redis.Redis(
        host=redis_cfg.host,
        port=int(redis_cfg.port),
        db=int(redis_cfg.db),
        password=(redis_cfg.password or None),
        ssl=bool(redis_cfg.ssl),
        socket_timeout=float(redis_cfg.socket_timeout),
        decode_responses=False,
    )
    return ReadOnlyStreams(client)


def _s(value: Any) -> str:
    """bytes/str → str."""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace")
    return str(value)


def _field(fields: Mapping[Any, Any], name: str) -> Any:
    """스트림 필드 — bytes 키와 str 키를 함께 받는다."""
    if name.encode() in fields:
        return fields[name.encode()]
    return fields.get(name)


def _parse_stream_id(stream_id: str) -> tuple[int, int]:
    """`<ms>-<seq>` → (ms, seq). 형식이 아니면 (0, 0)."""
    ms, _, seq = stream_id.partition("-")
    try:
        return int(ms), int(seq or 0)
    except ValueError:
        return 0, 0


class StreamLimitError(RuntimeError):
    """스트림 항목이 `--max-stream-entries`를 넘었다(메모리 보호 — 창을 줄이거나 상한을 올린다)."""


def read_stream(
    streams: ReadOnlyStreams, key: str, start: datetime, end: datetime,
    max_entries: int | None = None,
) -> list[tuple[str, Mapping[Any, Any]]]:
    """`XRANGE`를 1,000건 페이지로 돌며 [start, end] 창의 항목을 읽는다.

    `max_entries`를 넘으면 `StreamLimitError`로 멈춘다(읽은 것을 버린다).
    """
    out: list[tuple[str, Mapping[Any, Any]]] = []
    cursor = f"{int(start.timestamp() * 1000)}-0"
    upper = str(int(end.timestamp() * 1000))
    while True:
        batch = streams.xrange(key, min=cursor, max=upper, count=STREAM_PAGE)
        if not batch:
            break
        for msg_id, fields in batch:
            out.append((_s(msg_id), fields))
        if max_entries is not None and len(out) > max_entries:
            raise StreamLimitError(f"{len(out)}>{max_entries}")
        if len(batch) < STREAM_PAGE:
            break
        ms, seq = _parse_stream_id(out[-1][0])
        cursor = f"{ms}-{seq + 1}"
    return out


def _group_info(streams: ReadOnlyStreams, key: str, group: str) -> dict[str, Any]:
    """소비자 그룹 요약 — 마지막 전달 id·대기·지연·소비자 수(소비자 이름은 싣지 않는다)."""
    info: dict[str, Any] = {"group_found": False}
    for g in streams.xinfo_groups(key):
        norm = {_s(k): v for k, v in g.items()}
        if _s(norm.get("name", "")) != group:
            continue
        lag = norm.get("lag")
        info = {
            "group_found": True,
            "last_delivered_id": _s(norm.get("last-delivered-id", "0-0")),
            "pending": int(norm.get("pending") or 0),
            "consumers": int(norm.get("consumers") or 0),
            "lag": int(lag) if isinstance(lag, (int, float)) else None,
        }
        pend = {_s(k): v for k, v in streams.xpending(key, group).items()}
        info["xpending_count"] = int(pend.get("pending") or 0)
        info["xpending_consumers"] = len(pend.get("consumers") or [])
        break
    return info


# ── 입력 모델 ─────────────────────────────────────────────────────────────────
def _parse_ts(value: object) -> datetime | None:
    """ISO 시각 → aware datetime(시간대 없으면 UTC로 본다)."""
    if not isinstance(value, str) or not value:
        return None
    try:
        ts = datetime.fromisoformat(value)
    except ValueError:
        return None
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)


@dataclass
class Decision:
    """판정 감사 레코드 1건(`type` 없음)."""

    idx: int
    ts: datetime
    rec: dict[str, Any]

    @property
    def alarm_id(self) -> str:
        """원문 alarm_id."""
        return str(self.rec.get("alarm_id") or "")

    @property
    def tier(self) -> str:
        """티어."""
        return str(self.rec.get("tier") or "")

    @property
    def stage(self) -> str | None:
        """단계 라벨(옛 레코드는 None)."""
        stage = self.rec.get("stage")
        return str(stage) if stage else None

    @property
    def fingerprint(self) -> str:
        """원 지문."""
        return str(self.rec.get("fingerprint") or "")

    @property
    def signals(self) -> dict[str, Any]:
        """판정 신호."""
        sig = self.rec.get("signals")
        return sig if isinstance(sig, dict) else {}

    @property
    def evidence(self) -> dict[str, Any]:
        """결정 단계 근거."""
        ev = self.rec.get("stage_evidence")
        return ev if isinstance(ev, dict) else {}

    @property
    def is_clear(self) -> bool:
        """해소 판정 — 원 심각도 0 또는 해소·자가복구 단계."""
        return self.signals.get("severity") == 0 or self.stage in _CLEAR_STAGES

    @property
    def is_page(self) -> bool:
        """해소가 아닌 PAGE."""
        return self.tier == TIER_PAGE and not self.is_clear


@dataclass
class AuditInputs:
    """판정 감사 파일에서 읽은 것."""

    decisions: list[Decision] = field(default_factory=list)
    recurrences: list[tuple[datetime, dict[str, Any]]] = field(default_factory=list)
    resolutions: list[tuple[datetime, dict[str, Any]]] = field(default_factory=list)
    late_promotions: int = 0
    other_types: Counter[str] = field(default_factory=Counter)
    bad_lines: int = 0
    lines: int = 0
    first_ts: datetime | None = None


def read_audit(path: Path, start: datetime, end: datetime) -> AuditInputs:
    """판정 감사 JSONL을 순차로 읽어 [start, end] 창의 레코드를 종류별로 나눈다."""
    out = AuditInputs()
    if not path.exists():
        return out
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            out.lines += 1
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                out.bad_lines += 1
                continue
            if not isinstance(rec, dict):
                out.bad_lines += 1
                continue
            ts = _parse_ts(rec.get("ts"))
            if ts is None:
                out.bad_lines += 1
                continue
            if out.first_ts is None or ts < out.first_ts:
                out.first_ts = ts
            if not start <= ts <= end:
                continue
            kind = rec.get("type")
            if kind is None:
                out.decisions.append(Decision(len(out.decisions), ts, rec))
            elif kind == "recurrence":
                out.recurrences.append((ts, rec))
            elif kind == "resolution":
                out.resolutions.append((ts, rec))
            elif kind == "late_promotion":
                out.late_promotions += 1
            else:
                out.other_types[str(kind)] += 1
    out.decisions.sort(key=lambda d: d.ts)
    for i, d in enumerate(out.decisions):
        d.idx = i
    return out


@dataclass
class RawAlarm:
    """수신 스트림 항목 1건."""

    stream_id: str
    recv_ts: datetime
    payload: dict[str, Any] | None
    event: AlarmEvent | None
    severity: int | None

    @property
    def alarm_id(self) -> str:
        """원문 alarmId."""
        return str((self.payload or {}).get("alarmId") or "")


def _event_from_payload(payload: dict[str, Any], received: datetime) -> AlarmEvent:
    """워커 `_process`와 같은 규칙으로 페이로드 → AlarmEvent(식별 역조회는 하지 않는다)."""
    try:
        alarm_time = datetime.strptime(str(payload.get("alarmTime", "")), "%Y%m%d%H%M%S")
    except ValueError:
        alarm_time = received
    severity, _ = coerce_severity(payload.get("severity"))
    return AlarmEvent(
        db_id=str(payload.get("dbId", "") or ""),
        server_name=str(payload.get("serverName", "") or ""),
        hostname=str(payload.get("hostname", "") or ""),
        ip_address=str(payload.get("ipAddress", "") or ""),
        resource_ancestry=str(payload.get("resourceAncestry", "") or ""),
        alarm_id=str(payload.get("alarmId", "") or ""),
        severity=severity,
        alarm_status=str(payload.get("alarmStatus", "") or ""),
        resource_type=str(payload.get("resourceType", "") or ""),
        resource_name=str(payload.get("resourceName", "") or ""),
        alarm_name=str(payload.get("alarmName", "") or ""),
        alarm_time=alarm_time,
        conditions=str(payload.get("conditions", "") or ""),
        condition_log=str(payload.get("conditionLog", "") or ""),
        is_clear=(severity == 0),
        raw_payload=payload,
        received_at=received,
    )


def parse_raw(stream_id: str, fields: Mapping[Any, Any]) -> RawAlarm:
    """스트림 항목 → RawAlarm(파싱 실패면 payload None)."""
    ms, _ = _parse_stream_id(stream_id)
    recv = datetime.fromtimestamp(ms / 1000, tz=UTC)
    data = _field(fields, "data")
    payload: dict[str, Any] | None = None
    try:
        loaded = json.loads(_s(data)) if data is not None else None
        payload = loaded if isinstance(loaded, dict) else None
    except json.JSONDecodeError:
        payload = None
    if payload is None:
        return RawAlarm(stream_id, recv, None, None, None)
    event = _event_from_payload(payload, recv)
    return RawAlarm(stream_id, recv, payload, event, event.severity)


def _dead_keys(entries: Iterable[tuple[str, Mapping[Any, Any]]]) -> tuple[set[str], set[str]]:
    """dead-letter 항목 → (원 스트림 id 집합, alarmId 집합)."""
    msg_ids: set[str] = set()
    alarm_ids: set[str] = set()
    for _, fields in entries:
        src = _field(fields, "source_msg_id")
        if src is not None:
            msg_ids.add(_s(src))
        data = _field(fields, "data")
        try:
            payload = json.loads(_s(data)) if data is not None else None
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict) and payload.get("alarmId") is not None:
            alarm_ids.add(str(payload["alarmId"]))
    return msg_ids, alarm_ids


# ── 맞대기·처분 ───────────────────────────────────────────────────────────────
@dataclass
class Matched:
    """수신 1건(또는 수신 없는 판정 1건)의 맞대기 결과."""

    raw: RawAlarm | None
    disposition: str
    decision: Decision | None = None
    recurrence: dict[str, Any] | None = None
    recurrence_ts: datetime | None = None


def _take(
    pool: dict[str, list[tuple[datetime, int]]], used: set[int], alarm_id: str, recv: datetime
) -> int | None:
    """같은 alarm_id 후보 중 수신 시차 안에서 가장 이른 미사용 항목의 번호를 꺼낸다."""
    for ts, num in pool.get(alarm_id, []):
        if num in used:
            continue
        delta = (ts - recv).total_seconds()
        if -MATCH_BEFORE_S <= delta <= MATCH_AFTER_S:
            used.add(num)
            return num
    return None


def assign_dispositions(
    raws: list[RawAlarm],
    decisions: list[Decision],
    recurrences: list[tuple[datetime, dict[str, Any]]],
    *,
    dead_msg_ids: set[str],
    dead_alarm_ids: set[str],
    min_severity: int,
    last_delivered: tuple[int, int] | None,
) -> tuple[list[Matched], list[Decision], int]:
    """수신 항목마다 처분 7종 중 하나를 붙인다(§10.3).

    Returns:
        (수신 맞대기 결과, 수신에 맞대지 못한 판정, 수신에 맞대지 못한 재발 억제 수)
    """
    dec_pool: dict[str, list[tuple[datetime, int]]] = defaultdict(list)
    for i, d in enumerate(decisions):
        dec_pool[d.alarm_id].append((d.ts, i))
    rec_pool: dict[str, list[tuple[datetime, int]]] = defaultdict(list)
    for i, (ts, rec) in enumerate(recurrences):
        rec_pool[str(rec.get("alarm_id") or "")].append((ts, i))
    used_dec: set[int] = set()
    used_rec: set[int] = set()
    out: list[Matched] = []
    for raw in sorted(raws, key=lambda r: _parse_stream_id(r.stream_id)):
        aid = raw.alarm_id
        hit = _take(dec_pool, used_dec, aid, raw.recv_ts) if aid else None
        if hit is not None:
            out.append(Matched(raw, "decided", decision=decisions[hit]))
            continue
        rec_hit = _take(rec_pool, used_rec, aid, raw.recv_ts) if aid else None
        if rec_hit is not None:
            ts, rec = recurrences[rec_hit]
            out.append(Matched(raw, "dedup_fingerprint", recurrence=rec, recurrence_ts=ts))
            continue
        if raw.stream_id in dead_msg_ids or (aid and aid in dead_alarm_ids):
            out.append(Matched(raw, "dead_letter"))
        elif raw.severity is not None and 1 <= raw.severity < min_severity:
            out.append(Matched(raw, "below_min_severity"))
        elif last_delivered is not None and _parse_stream_id(raw.stream_id) > last_delivered:
            out.append(Matched(raw, "pending"))
        else:
            out.append(Matched(raw, "unaccounted"))
    leftover = [d for i, d in enumerate(decisions) if i not in used_dec]
    return out, leftover, len(recurrences) - len(used_rec)


# ── 파생 특징 ─────────────────────────────────────────────────────────────────
@dataclass
class PageEvent:
    """원 PAGE 1건의 시뮬레이션 입력(원문 키 — 출력되지 않는다)."""

    idx: int
    ts: float
    fp: str
    host: tuple[str, str]
    defn: tuple[str, str]
    eff_sev: int
    severity: int
    importance: str
    stage: str | None
    app_fatal: int
    boost_origin: str | None
    page_days_7d: int
    r6_decidable: bool
    alarm_id: str = ""
    decision: NotificationDecision | None = None


def _host_key(d: Decision) -> tuple[str, str]:
    """호스트 키 = (db_id, 정규화 서버명) — 도메인 `host_key_of`와 같다."""
    return host_key_of(str(d.rec.get("db_id") or ""), str(d.rec.get("server_name") or ""))


def _def_key(d: Decision) -> tuple[str, str]:
    """동시 발생 키 = (db_id, 알람명) — 도메인 `fanout_key_of`와 같다."""
    return fanout_key_of(str(d.rec.get("db_id") or ""), str(d.rec.get("alarm_name") or ""))


def _int(value: object, default: int = 0) -> int:
    """정수 변환(bool·실패는 기본값)."""
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _eff_of(d: Decision) -> int:
    """실효 심각도 — 없으면 원 심각도, 그것도 없으면 3(보수)."""
    return _int(d.signals.get("effective_severity"), _int(d.signals.get("severity"), 3))


def _as_notification(d: Decision) -> NotificationDecision:
    """감사 레코드 → 도메인 판정 객체(재판정 입력 · 근거는 `stage_evidence`)."""
    return NotificationDecision(
        tier=d.tier,
        reason=str(d.rec.get("reason") or ""),
        priority=_int(d.rec.get("priority")),
        signals=dict(d.signals),
        fingerprint=d.fingerprint,
        stage=d.stage or "",
        evidence=dict(d.evidence),
    )


def _kst_date(ts: float) -> str:
    """epoch → KST 날짜 문자열."""
    return datetime.fromtimestamp(ts, tz=KST).date().isoformat()


def boost_origin(
    decision: Decision, log_text: str | None
) -> str | None:
    """sev3 단락의 상향 출처 — 원 sev3면 None · 3급 시그니처면 `signature:<라벨>` ·
    그 밖이면 `llm_or_anomaly` · 조건 로그가 없으면 `unknown`."""
    if decision.stage != STAGE_SEVERITY3 or not decision.is_page:
        return None
    if _int(decision.signals.get("severity"), 3) >= 3:
        return None
    if not log_text:
        return "unknown"
    sig = scan_signature_severity(log_text)
    if sig is not None and sig[0] >= 3:
        return f"signature:{sig[1]}"
    return "llm_or_anomaly"


@dataclass
class Derived:
    """판정 1건의 파생 특징."""

    fp_page_seq: int | None = None
    since_prev_fp_page_s: float | None = None
    since_prev_host_page_s: float | None = None
    fanout_hosts_10m: int | None = None
    clear_after_s: float | None = None
    fp_page_days_7d: int | None = None
    within_dedup_window_repage: bool | None = None

    def as_dict(self) -> dict[str, Any]:
        """출력용 dict."""
        return dict(self.__dict__)


def build_resolutions(
    decisions: list[Decision], resolutions: list[tuple[datetime, dict[str, Any]]]
) -> dict[str, list[float]]:
    """지문별 해소 시각(정렬) — 해소 판정 레코드 + 자가복구 `type=resolution`."""
    out: dict[str, list[float]] = defaultdict(list)
    for d in decisions:
        if d.is_clear and d.fingerprint:
            out[d.fingerprint].append(d.ts.timestamp())
    for ts, rec in resolutions:
        fp = str(rec.get("fingerprint") or "")
        if fp:
            out[fp].append(ts.timestamp())
    for values in out.values():
        values.sort()
    return out


def derive_features(
    decisions: list[Decision],
    clears: dict[str, list[float]],
    *,
    window_start: datetime,
    coverage_start: datetime,
    repeat_interval_s: float,
    sev3_repeat_interval_s: float,
) -> tuple[dict[int, Derived], list[PageEvent]]:
    """원 PAGE의 반복·호스트·동시 발생·해소·만성 특징을 계산한다(창 이전 7일 이력 포함).

    dedup 창 안 재PAGE는 워커 `_is_duplicate_fingerprint`와 같은 창을 쓴다 — 원 심각도 3이면
    `sev3_repeat_interval_seconds`, 그 밖은 `repeat_interval_seconds`.
    """
    derived: dict[int, Derived] = {}
    events: list[PageEvent] = []
    last_fp: dict[str, Decision] = {}
    last_host: dict[tuple[str, str], float] = {}
    seq_by_fp: Counter[str] = Counter()
    fanout: dict[tuple[str, str], deque[tuple[float, tuple[str, str]]]] = defaultdict(deque)
    days_by_fp: dict[str, deque[float]] = defaultdict(deque)
    win_start = window_start.timestamp()
    for d in decisions:
        if not d.is_page:
            continue
        ts = d.ts.timestamp()
        fp, host, defn = d.fingerprint, _host_key(d), _def_key(d)
        eff = _eff_of(d)
        severity = _int(d.signals.get("severity"), eff)
        feat = Derived()
        if ts >= win_start:
            seq_by_fp[fp] += 1
            feat.fp_page_seq = seq_by_fp[fp]
        prev = last_fp.get(fp)
        if prev is not None:
            prev_ts = prev.ts.timestamp()
            gap = ts - prev_ts
            feat.since_prev_fp_page_s = round(gap, 3)
            window = sev3_repeat_interval_s if severity == 3 else repeat_interval_s
            cleared_between = any(prev_ts < c <= ts for c in clears.get(fp, ()))
            feat.within_dedup_window_repage = bool(
                gap < window and eff <= _eff_of(prev) and not cleared_between
            )
        else:
            feat.within_dedup_window_repage = False
        if host in last_host:
            feat.since_prev_host_page_s = round(ts - last_host[host], 3)
        q = fanout[defn]
        q.append((ts, host))
        while q and ts - q[0][0] > R3_FANOUT_WINDOW_S:
            q.popleft()
        feat.fanout_hosts_10m = len({h for _, h in q})
        nxt = next((c for c in clears.get(fp, ()) if c > ts), None)
        feat.clear_after_s = round(nxt - ts, 3) if nxt is not None else None
        days = days_by_fp[fp]
        days.append(ts)
        today = datetime.fromtimestamp(ts, tz=KST).date()
        first_day = today - timedelta(days=R6_LOOKBACK_DAYS - 1)
        day_start = datetime(first_day.year, first_day.month, first_day.day, tzinfo=KST)
        while days and days[0] < day_start.timestamp():
            days.popleft()
        feat.fp_page_days_7d = len({_kst_date(t) for t in days})
        derived[d.idx] = feat
        last_fp[fp] = d
        last_host[host] = ts
        importance = str(d.signals.get("importance") or "보통")
        events.append(PageEvent(
            idx=d.idx,
            ts=ts,
            fp=fp,
            host=host,
            defn=defn,
            eff_sev=eff,
            severity=severity,
            importance=importance if importance in _VALID_IMPORTANCE else "보통",
            stage=d.stage,
            app_fatal=_int(d.evidence.get("app_impact_fatal_events")),
            boost_origin=None,
            page_days_7d=feat.fp_page_days_7d,
            r6_decidable=day_start >= coverage_start,
            alarm_id=d.alarm_id,
            decision=_as_notification(d),
        ))
    return derived, events


# ── what-if 시뮬레이션 ────────────────────────────────────────────────────────
def generation_tier(ev: PageEvent, rule: str) -> str | None:
    """생성 경로 규칙(R4·R5)을 적용한 티어. 규칙 대상이 아니면 None.

    R4: 수집 실패 보수화 PAGE · 실효 심각도 <3 → 매트릭스(심각도, 보통).
    R5: sev3 단락 · 원 심각도 <3 · 상향 출처가 LLM·이상탐지 → 원 심각도 매트릭스.
    """
    if rule == "R4" and ev.stage == STAGE_COLLECTION_FAILED and ev.eff_sev < 3:
        return _matrix_tier(ev.eff_sev, "보통")
    if (
        rule == "R5"
        and ev.stage == STAGE_SEVERITY3
        and ev.severity < 3
        and ev.boost_origin == "llm_or_anomaly"
    ):
        return _matrix_tier(ev.severity, ev.importance)
    return None


def _r6(ev: PageEvent) -> str:
    """R6 만성 — 최근 7일 중 PAGE 일수 ≥5(7일 이력이 없으면 판정 불가).

    보존 1(새 사건 첫 PAGE)·2(심각도 상승)·4(리마인더)는 적용하지 않는다(대표 상태를 보지 않는
    규칙이다) — 보존 3(앱 영향)만 호출자가 건다.
    """
    if not ev.r6_decidable:
        return "undecidable"
    return "demote" if ev.page_days_7d >= R6_CHRONIC_DAYS else "keep"


def _review_domain(
    registry: PageRegistry, ev: PageEvent, enabled: frozenset[str]
) -> NotificationDecision:
    """도메인 `review_page`(enforce)로 R1~R3을 본다 — 켠 규칙만.

    도메인은 R1→R2→R3 첫 적중에서 멈춘다. 첫 적중이 끈 규칙이면 그 규칙의 재료만 비우고 다시
    부른다(다른 규칙 적중은 keep 취급 — 「그 규칙만 켠」 효과). R1 적중이면 리마인더 보존 재료는
    애초에 없으므로 재료를 비워도 다른 보존 판정은 그대로다.
    """
    assert ev.decision is not None
    signals: PageReviewSignals = registry.signals_for(ev.fp, ev.host, ev.defn, ev.ts)
    while True:
        out = review_page(ev.decision, signals, _SIM_CONFIG)
        block = out.evidence.get(EVIDENCE_KEY) or {}
        rule = block.get("rule")
        if rule is None or rule in enabled:
            return out
        masked: dict[str, Any] = dict(signals)
        masked[_RULE_SIGNAL_KEY[str(rule)]] = None if rule == RULE_OPEN_REPEAT else []
        signals = cast(PageReviewSignals, masked)


@dataclass
class RunResult:
    """규칙 묶음 1회 시뮬레이션 결과."""

    labels: dict[int, str] = field(default_factory=dict)
    demoted_by: dict[int, str] = field(default_factory=dict)
    # 도메인 규칙 강등 → (규칙, 가리키는 대표 alarm_id) — 고아 사후 검증 재료.
    links: dict[int, tuple[str, str]] = field(default_factory=dict)
    orphans: int = 0
    evictions: int = 0


def _clear_decision(fp: str) -> NotificationDecision:
    """레지스트리에 해소를 알리는 최소 판정(지문만 쓴다)."""
    return NotificationDecision(
        tier=TIER_SUPPRESS, reason="", priority=0, signals={"severity": 0}, fingerprint=fp,
        stage=STAGE_RESOLVED,
    )


def simulate(
    events: list[PageEvent], clears: dict[str, list[float]], rules: tuple[str, ...]
) -> RunResult:
    """원 PAGE를 시각 순으로 훑으며 규칙 묶음을 적용한다.

    순서: 생성 경로(R4·R5) → 도메인 재판정(R1~R3 · `PageRegistry`) → R6. 강등된 건은 대표가 되지
    않는다(enforce 가정 — 도메인 강등은 레지스트리에 반복으로만 남고, 생성 경로·R6 강등은
    레지스트리에 알리지 않는다). 끝나면 `verify_orphans`로 고아를 행 단위로 다시 센다.
    """
    result = RunResult()

    def _evicted(_what: str, count: int) -> None:
        result.evictions += count

    registry = PageRegistry(
        host_window_minutes=_SIM_CONFIG.page_review_host_window_minutes,
        fanout_window_minutes=_SIM_CONFIG.page_review_fanout_window_minutes,
        reminder_hours=_SIM_CONFIG.page_review_reminder_hours,
        reopen_quiet_minutes=_SIM_CONFIG.page_review_reopen_quiet_minutes,
        on_evict=_evicted,
    )
    timeline: list[tuple[float, int, Any]] = [(ev.ts, 1, ev) for ev in events]
    timeline += [(ts, 0, fp) for fp, values in clears.items() for ts in values]
    timeline.sort(key=lambda t: (t[0], t[1]))
    enabled = frozenset(r for r in rules if r in DOMAIN_RULES)
    for ts, kind, item in timeline:
        if kind == 0:
            registry.observe(_clear_decision(item), fingerprint=item, host_key=("", ""),
                             fanout_key=("", ""), alarm_id="", now=ts)
            continue
        ev: PageEvent = item
        demoted_by: str | None = None
        label = "keep"
        for rule in (r for r in rules if r in GENERATION_RULES):
            tier = generation_tier(ev, rule)
            label = "n/a" if tier is None else ("keep" if tier == TIER_PAGE else "demote")
            if label == "demote" and ev.app_fatal > 0:
                label = "keep"
            if label == "demote":
                demoted_by = rule
                break
        reviewed: NotificationDecision | None = None
        if demoted_by is None and enabled:
            reviewed = _review_domain(registry, ev, enabled)
            block = reviewed.evidence.get(EVIDENCE_KEY) or {}
            if reviewed.tier != TIER_PAGE:
                rule = str(block.get("rule"))
                demoted_by, label = rule, "demote"
                result.links[ev.idx] = (rule, str(block.get(_REP_LINK[rule][0]) or ""))
            else:
                label = "keep"
        if demoted_by is None and "R6" in rules:
            label = _r6(ev)
            if label == "demote" and ev.app_fatal > 0:
                label = "keep"
            if label == "demote":
                demoted_by = "R6"
        result.labels[ev.idx] = label
        if demoted_by is not None:
            result.demoted_by[ev.idx] = demoted_by
        if reviewed is not None and demoted_by in (None, *DOMAIN_RULES):
            registry.observe(reviewed, fingerprint=ev.fp, host_key=ev.host, fanout_key=ev.defn,
                             alarm_id=ev.alarm_id, now=ev.ts)
    result.orphans = verify_orphans(events, result)
    return result


def verify_orphans(events: list[PageEvent], result: RunResult) -> int:
    """고아 사후 검증 — 도메인 규칙 강등 건마다 가리키는 대표 alarm이 남은 PAGE인지 다시 본다.

    시뮬레이터 상태와 독립으로 판정 결과만 쓴다: 대표 alarm_id가 이 실행에서 강등되지 않은
    원 PAGE이고, 강등 건보다 앞서 규칙 창 안에 있으며, R1은 같은 지문 · R2는 같은 호스트의 다른
    지문 · R3은 같은 동시발생 키의 다른 호스트여야 한다. 하나라도 어긋나면 고아 1건이다.
    """
    kept: dict[str, list[PageEvent]] = defaultdict(list)
    for ev in events:
        if ev.idx not in result.demoted_by:
            kept[ev.alarm_id].append(ev)
    by_idx = {ev.idx: ev for ev in events}
    return sum(
        1 for idx, (rule, rep_id) in result.links.items()
        if not any(_bound_to(rep, by_idx[idx], rule) for rep in kept.get(rep_id, ()))
    )


def _bound_to(rep: PageEvent, ev: PageEvent, rule: str) -> bool:
    """`ev`(강등 건)가 `rep`(남은 PAGE)에 규칙대로 묶일 수 있는가."""
    gap = ev.ts - rep.ts
    window = _REP_LINK[rule][1]
    if rule == RULE_OPEN_REPEAT:
        return 0 <= gap < window and rep.fp == ev.fp
    if rule == RULE_SAME_HOST:
        return 0 <= gap <= window and rep.host == ev.host and rep.fp != ev.fp
    return 0 <= gap <= window and rep.defn == ev.defn and rep.host != ev.host


def run_whatif(
    events: list[PageEvent], clears: dict[str, list[float]]
) -> tuple[dict[str, RunResult], dict[int, dict[str, str]]]:
    """단독 6종 + 누적 1종을 돌리고, 행별 what-if 라벨을 만든다."""
    runs: dict[str, RunResult] = {
        rule: simulate(events, clears, (rule,)) for rule in CUMULATIVE_RULES
    }
    runs["cumulative"] = simulate(events, clears, CUMULATIVE_RULES)
    per_row: dict[int, dict[str, str]] = {}
    for ev in events:
        labels = {rule: runs[rule].labels.get(ev.idx, "n/a") for rule in CUMULATIVE_RULES}
        by = runs["cumulative"].demoted_by.get(ev.idx)
        labels["cumulative"] = f"demote:{by}" if by else "keep"
        per_row[ev.idx] = labels
    return runs, per_row


# ── 집계 ──────────────────────────────────────────────────────────────────────
S8_NOTES: tuple[str, ...] = (
    "R1~R3은 도메인 page_review(PageRegistry + review_page · enforce 가정)를 그대로 부른다. "
    "단독 수치는 끈 규칙이 첫 적중이면 그 규칙의 재료만 비우고 다시 판정한 값이다"
    "(다른 규칙 적중은 keep 취급).",
    "누적 순서: R4·R5 → R1~R3(도메인 · 첫 적중) → R6. 강등된 건은 대표가 되지 않는다.",
    "R6은 보존 1(새 사건 첫 PAGE)·2(심각도 상승)·4(리마인더)를 적용하지 않는다"
    "(보존 3 앱 영향만).",
    "고아 = 강등 건이 가리키는 대표 alarm이 그 실행에서 남은 PAGE로 규칙 창 안에 없는 건"
    "(판정 뒤 행 단위 사후 검증).",
)
S10_NOTES: tuple[str, ...] = (
    "일반 단어형 자원명(예: Memory)은 다른 알람의 이름에서도 <RES>로 지워질 수 있다.",
    "가명 원천: 판정 행은 등록 서버명, 판정 없는 행은 원문 serverName이라 같은 서버가 두 가명으로 "
    "보일 수 있다.",
    "같은 alarmId로 발생·해소가 오면 수신·판정 맞대기가 엇갈릴 수 있다"
    "(수신 60초 전~1시간 뒤 · 가장 이른 미사용 판정).",
)


def _bucket(value: float, edges: tuple[tuple[float, str], ...], last: str) -> str:
    """경계 목록으로 구간 라벨."""
    for upper, label in edges:
        if value < upper:
            return label
    return last


_INTERVAL_EDGES: tuple[tuple[float, str], ...] = (
    (300, "<5분"), (1800, "5~30분"), (3600, "30분~1시간"), (14400, "1~4시간"), (86400, "4~24시간"),
)
_CLEAR_EDGES: tuple[tuple[float, str], ...] = ((300, "<5분"), (900, "<15분"), (3600, "<1시간"))
_COUNT_EDGES: tuple[tuple[float, str], ...] = (
    (2, "1"), (3, "2"), (4, "3"), (6, "4-5"), (11, "6-10"),
)
_CLUSTER_EDGES: tuple[tuple[float, str], ...] = ((3, "2"), (6, "3-5"), (11, "6-10"))


def _percentile(values: list[int], pct: float) -> int | None:
    """최근접 순위 백분위."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, -(-len(ordered) * pct // 100))
    return ordered[int(rank) - 1]


def _ts(row: Mapping[str, Any]) -> float | None:
    """행의 판정 시각(없으면 수신 시각) epoch."""
    text = row.get("decision_ts") or row.get("recv_ts")
    parsed = _parse_ts(text)
    return parsed.timestamp() if parsed is not None else None


def _is_page(row: Mapping[str, Any]) -> bool:
    """원 PAGE 행(해소 제외)."""
    return row.get("tier") == TIER_PAGE and not row.get("is_clear") and row.get(
        "stage"
    ) not in _CLEAR_STAGES


def _sorted_counter(counter: Mapping[str, int]) -> dict[str, int]:
    """건수 내림차순 dict."""
    return dict(sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])))


def summarize(
    rows: list[dict[str, Any]],
    *,
    meta: dict[str, Any],
    runs: dict[str, RunResult],
    page_idx_of_row: dict[int, int],
    stats: dict[str, Any],
) -> dict[str, Any]:
    """집계 S0~S10(§10.6). 비식별 행만 입력으로 쓴다(what-if 묶음 검증 수치는 시뮬레이션에서)."""
    short = [r for r in rows if r.get("in_hours_window")]
    decided = [r for r in short if r.get("tier")]
    pages = [r for r in decided if _is_page(r)]
    long_pages = [r for r in rows if r.get("tier") and _is_page(r)]
    s: dict[str, Any] = {"S0": meta}

    # S1 수신·처분 — 스트림을 안 읽었으면 스트림이 있어야 가릴 처분은 0이 아니라 null(모름)이다.
    stream_read = bool(meta.get("stream_read"))
    received = [r for r in short if r.get("disposition") != "decision_without_raw"]
    lags = [int(r["lag_ms"]) for r in short if isinstance(r.get("lag_ms"), int)]
    disp = Counter(str(r.get("disposition")) for r in short)

    def _known(name: str, value: Any) -> Any:
        return value if stream_read or name not in STREAM_DISPOSITIONS else None

    s["S1"] = {
        "received": len(received) if stream_read else None,
        "decisions": len(decided),
        "by_severity": _sorted_counter(Counter(str(r.get("severity")) for r in received)),
        "by_db_id": _sorted_counter(Counter(str(r.get("db_id")) for r in received)),
        "by_source": _sorted_counter(Counter(str(r.get("source")) for r in received)),
        "dispositions": {k: _known(k, disp.get(k, 0)) for k in DISPOSITIONS},
        "lag_ms_p50": _percentile(lags, 50),
        "lag_ms_p95": _percentile(lags, 95),
        "decision_without_raw": _known("decision_without_raw", disp.get("decision_without_raw", 0)),
        "recurrence_without_raw": (
            stats.get("recurrence_without_raw", 0) if stream_read else None
        ),
    }

    # S2 티어×단계
    stage_names = [*STAGE_ORDER, STAGE_UNKNOWN]
    table = {st: {t: 0 for t in TIERS} for st in stage_names}
    for r in decided:
        st = r.get("stage") or STAGE_UNKNOWN
        table.setdefault(st, {t: 0 for t in TIERS})
        if r.get("tier") in TIERS:
            table[st][r["tier"]] += 1
    sev3 = [r for r in pages if r.get("stage") == STAGE_SEVERITY3]
    failed = [r for r in pages if r.get("stage") == STAGE_COLLECTION_FAILED]
    promote: Counter[str] = Counter()
    for r in pages:
        if r.get("stage") != STAGE_MATRIX:
            continue
        ev = r.get("evidence") or {}
        reasons = [str(p) for p in (ev.get("promote") or [])]
        if ev.get("app_impact_fatal_events"):
            reasons.append("앱 영향")
        for reason in reasons or ["(승격 없음)"]:
            promote[reason] += 1
    s["S2"] = {
        "tier_by_stage": table,
        "page_by_stage": {st: v[TIER_PAGE] for st, v in table.items() if v[TIER_PAGE]},
        "sev3_origin": _sorted_counter(
            Counter(str(r.get("boost_origin") or "original_sev3") for r in sev3)
        ),
        "sev3_boost_basis": stats.get("boost_basis", {}),
        "collection_failed": {
            "by_noise_ctx_source": _sorted_counter(
                Counter(str(r.get("noise_ctx_source")) for r in failed)
            ),
            "by_db_id": _sorted_counter(Counter(str(r.get("db_id")) for r in failed)),
            "by_source": _sorted_counter(Counter(str(r.get("source")) for r in failed)),
        },
        "matrix_page_by_promote": _sorted_counter(promote),
        "late_promotions": stats.get("late_promotions", 0),
    }

    # S3 반복
    per_fp: Counter[str] = Counter(str(r.get("fp")) for r in pages)
    first_row: dict[str, dict[str, Any]] = {}
    for r in pages:
        first_row.setdefault(str(r.get("fp")), r)
    intervals = [
        float(r["since_prev_fp_page_s"]) for r in pages
        if isinstance(r.get("since_prev_fp_page_s"), (int, float))
    ]
    s["S3"] = {
        "fp_page_count_dist": dict(
            Counter(_bucket(n, _COUNT_EDGES, "11+") for n in per_fp.values())
        ),
        "repage_interval_dist": dict(
            Counter(_bucket(v, _INTERVAL_EDGES, "24시간+") for v in intervals)
        ),
        "within_dedup_window_repage": sum(1 for r in pages if r.get("within_dedup_window_repage")),
        "top_fingerprints": [
            {
                "fp": fp,
                "alarm_name_norm": first_row[fp].get("alarm_name_norm"),
                "db_id": first_row[fp].get("db_id"),
                "pages": n,
            }
            for fp, n in per_fp.most_common(TOP_FINGERPRINTS)
        ],
    }

    # S4 군집
    by_host: dict[str, list[float]] = defaultdict(list)
    by_def: dict[tuple[str, str], list[tuple[float, str]]] = defaultdict(list)
    for r in pages:
        t = _ts(r)
        if t is None:
            continue
        by_host[str(r.get("host"))].append(t)
        by_def[(str(r.get("db_id")), str(r.get("alarm_def")))].append((t, str(r.get("host"))))
    # 30분 군집 안 PAGE — 같은 호스트의 다른 PAGE가 앞뒤 30분 안에 있는 건만 센다.
    clustered: dict[str, int] = {}
    for h, ts_list in by_host.items():
        ordered = sorted(ts_list)
        n = sum(
            1 for i, t in enumerate(ordered)
            if (i > 0 and t - ordered[i - 1] <= HOST_CLUSTER_WINDOW_S)
            or (i + 1 < len(ordered) and ordered[i + 1] - t <= HOST_CLUSTER_WINDOW_S)
        )
        if n:
            clustered[h] = n
    clusters: list[int] = []
    cluster_pages = 0
    for items in by_def.values():
        items.sort()
        i = 0
        while i < len(items):
            start = items[i][0]
            j = i
            while j < len(items) and items[j][0] - start <= R3_FANOUT_WINDOW_S:
                j += 1
            hosts = {h for _, h in items[i:j]}
            if len(hosts) >= 2:
                clusters.append(len(hosts))
                cluster_pages += j - i
            i = j
    s["S4"] = {
        "hosts_multi_page_30m": len(clustered),
        "pages_on_those_hosts": sum(clustered.values()),
        "fanout_clusters_10m": len(clusters),
        "fanout_cluster_size_dist": dict(
            Counter(_bucket(n, _CLUSTER_EDGES, "11+") for n in clusters)
        ),
        "pages_in_fanout_clusters": cluster_pages,
    }

    # S5 시간
    hourly = Counter(
        datetime.fromtimestamp(t, tz=KST).strftime("%H") for t in (_ts(r) for r in pages) if t
    )
    # 폭주 — 60초 슬라이딩 창(시작 PAGE부터 60초 미만)에 PAGE ≥5.
    # 겹치지 않게 창 끝 뒤에서 다시 찾는다.
    page_ts = sorted(t for t in (_ts(r) for r in pages) if t is not None)
    all_ts = sorted(t for t in (_ts(r) for r in rows if r.get("decision_ts")) if t is not None)
    bursts = []
    i = 0
    while i < len(page_ts):
        start = page_ts[i]
        j = bisect.bisect_left(page_ts, start + BURST_WINDOW_S)
        if j - i < BURST_PER_MINUTE:
            i += 1
            continue
        k = bisect.bisect_left(all_ts, start)
        bursts.append({
            "minute": datetime.fromtimestamp(start, tz=KST).strftime("%Y-%m-%dT%H:%M"),
            "pages": j - i,
            "gap_before_s": round(start - all_ts[k - 1], 3) if k > 0 else None,
        })
        i = j
    s["S5"] = {"hourly_pages": {f"{h:02d}": hourly.get(f"{h:02d}", 0) for h in range(24)},
               "bursts": bursts[:50]}

    # S6 해소
    s["S6"] = {"clear_after_dist": dict(Counter(
        "미해소" if not isinstance(r.get("clear_after_s"), (int, float))
        else _bucket(float(r["clear_after_s"]), _CLEAR_EDGES, "1시간+")
        for r in pages
    ))}

    # S7 만성(긴 창)
    days_by_fp: dict[str, set[str]] = defaultdict(set)
    pages_by_fp: Counter[str] = Counter()
    for r in long_pages:
        t = _ts(r)
        if t is None:
            continue
        days_by_fp[str(r.get("fp"))].add(_kst_date(t))
        pages_by_fp[str(r.get("fp"))] += 1
    chronic = [fp for fp, d in days_by_fp.items() if len(d) >= R6_CHRONIC_DAYS]
    s["S7"] = {
        "window_days": meta.get("window", {}).get("long_days"),
        "page_days_dist": dict(Counter(str(min(len(d), 7)) + ("+" if len(d) >= 7 else "")
                                       for d in days_by_fp.values())),
        "chronic_fps": len(chronic),
        "chronic_pages": sum(pages_by_fp[fp] for fp in chronic),
    }

    # S8 what-if
    long_days = meta.get("window", {}).get("long_days")
    row_of_idx = {idx: rows[r_i] for r_i, idx in page_idx_of_row.items()}
    base_short = sum(1 for idx, r in row_of_idx.items() if r.get("in_hours_window"))
    base_long = len(row_of_idx)
    what: dict[str, Any] = {}
    for name, run in runs.items():
        kept_short = sum(1 for idx, r in row_of_idx.items()
                         if r.get("in_hours_window") and idx not in run.demoted_by)
        kept_long = sum(1 for idx in row_of_idx if idx not in run.demoted_by)
        stages = Counter(
            str(row_of_idx[idx].get("stage") or STAGE_UNKNOWN)
            for idx in run.demoted_by
            if idx in row_of_idx and row_of_idx[idx].get("in_hours_window")
        )
        what[name] = {
            "pages_after": kept_short,
            "demoted": base_short - kept_short,
            "daily_avg_long": round(kept_long / long_days, 2) if long_days else None,
            "demoted_origin_stage": _sorted_counter(stages),
            "undecidable": sum(1 for idx, lab in run.labels.items()
                               if lab == "undecidable" and idx in row_of_idx
                               and row_of_idx[idx].get("in_hours_window")),
            "orphans": run.orphans,
        }
    s["S8"] = {
        "pages_before": base_short,
        "daily_avg_long_before": round(base_long / long_days, 2) if long_days else None,
        "rules": what,
        "orphans_total": sum(v.orphans for v in runs.values()),
        "registry_evictions": sum(v.evictions for v in runs.values()),
        "params": {
            "r1_reminder_s": R1_REMINDER_S, "r1_reopen_quiet_s": R1_REOPEN_QUIET_S,
            "r2_host_window_s": R2_HOST_WINDOW_S, "r3_fanout_window_s": R3_FANOUT_WINDOW_S,
            "r6_chronic_days": R6_CHRONIC_DAYS, "r6_lookback_days": R6_LOOKBACK_DAYS,
            "always_page": [],
        },
        "r6_feedback_exclusion": "범위 밖(피드백 파일 미독)",
        "notes": list(S8_NOTES),
    }

    # S9 지문 안정성
    fps_by_group: dict[tuple[str, str], set[str]] = defaultdict(set)
    defs_by_norm: dict[str, set[str]] = defaultdict(set)
    for r in decided:
        if r.get("fp"):
            fps_by_group[(str(r.get("host")), str(r.get("alarm_name_norm")))].add(str(r["fp"]))
        if r.get("alarm_def"):
            defs_by_norm[str(r.get("alarm_name_norm"))].add(str(r["alarm_def"]))
    unstable = [v for v in fps_by_group.values() if len(v) >= 2]
    merged = [v for v in defs_by_norm.values() if len(v) >= 2]
    s["S9"] = {
        "host_name_groups_multi_fp": len(unstable),
        "fps_in_those_groups": sum(len(v) for v in unstable),
        "norm_names_merging_defs": len(merged),
        "alarm_defs_merged": sum(len(v) for v in merged),
    }

    # S10 데이터 품질
    s["S10"] = {
        "decisions_without_stage": sum(1 for r in decided if not r.get("stage")),
        "analysis_failed": stats.get("analysis_failed", 0),
        "dropped_evidence_keys": stats.get("dropped_evidence_keys", {}),
        "policy_excluded_fields": stats.get("policy_excluded", []),
        "policy_omitted_fields": stats.get("policy_omitted", []),
        "bad_audit_lines": stats.get("bad_lines", 0),
        "other_record_types": stats.get("other_types", {}),
        "noise_ctx_source_limit": (
            "polestar_db·cache는 감사 레코드로 가릴 수 없음(apm_policy·unavailable만)"
        ),
        "notes": list(S10_NOTES),
    }
    return s


# ── summary.md ────────────────────────────────────────────────────────────────
def _md_table(headers: list[str], rows: Iterable[Iterable[Any]]) -> list[str]:
    """마크다운 표 줄."""
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for row in rows:
        out.append("| " + " | ".join("-" if v is None else str(v) for v in row) + " |")
    return out


def _md_counter(title: str, counter: Mapping[str, Any]) -> list[str]:
    """건수 dict → 2열 표."""
    if not counter:
        return [f"- {title}: 없음", ""]
    return [f"**{title}**", "", *_md_table(["항목", "건수"], counter.items()), ""]


def render_md(summary: dict[str, Any]) -> str:
    """summary → 한국어 마크다운."""
    s0, s1, s2, s3 = summary["S0"], summary["S1"], summary["S2"], summary["S3"]
    s4, s5, s6, s7 = summary["S4"], summary["S5"], summary["S6"], summary["S7"]
    s8, s9, s10 = summary["S8"], summary["S9"], summary["S10"]
    w = s0["window"]
    lines = ["# 분류 정책 분석 로그 (page_audit)", ""]
    lines += ["## S0 실행 정보", ""]
    lines += _md_table(["항목", "값"], [
        ("창 끝(KST)", w["end"]),
        ("짧은 창", f"{w['hours']}시간 (시작 {w['hours_start']})"),
        ("긴 창", f"{w['long_days']}일 (시작 {w['long_start']})" if w.get("long_days") else "없음"),
        ("이력 읽기 시작", w["read_start"]),
        ("스트림 읽음", s0["stream_read"]),
        ("코드 SHA", s0.get("code_sha")),
        ("notification_policy.py 해시", s0.get("policy_module_sha256")),
    ])
    lines += ["", *_md_counter("입력 건수", s0["inputs"])]
    lines += _md_table(["단계", "상태"], s0["stages"].items()) + [""]
    p50 = "-" if s1["lag_ms_p50"] is None else f"{s1['lag_ms_p50']}ms"
    p95 = "-" if s1["lag_ms_p95"] is None else f"{s1['lag_ms_p95']}ms"
    if s1["received"] is None:
        lines += ["## S1 수신·처분", "", f"- 판정 건수 {s1['decisions']}건 · 스트림 미독 — 수신·"
                  "처분(최소 심각도 미만·dead·대기·미설명·수신 없는 판정)은 모름(-)", ""]
    else:
        lines += ["## S1 수신·처분", "",
                  f"- 수신 {s1['received']}건 · 판정 건수 {s1['decisions']}건 · "
                  f"처리 지연 p50 {p50} · p95 {p95} · 수신 없는 판정(H9) "
                  f"{s1['decision_without_raw']}건", ""]
    lines += _md_counter("처분", s1["dispositions"])
    lines += _md_counter("심각도별", s1["by_severity"])
    lines += _md_counter("db_id별", s1["by_db_id"])
    lines += _md_counter("출처별", s1["by_source"])
    lines += ["## S2 티어×단계", ""]
    lines += _md_table(["단계", *TIERS], [
        (st, *(v[t] for t in TIERS)) for st, v in s2["tier_by_stage"].items() if any(v.values())
    ]) + [""]
    lines += _md_counter("PAGE 단계별", s2["page_by_stage"])
    lines += _md_counter("sev3 단락 상향 출처(H6)", s2["sev3_origin"])
    lines += _md_counter("sev3 상향 판별 근거(raw=스트림 원문 · audit=200자 절단 · none=모름)",
                         s2["sev3_boost_basis"])
    lines += _md_counter("수집 실패 PAGE — noise_ctx 출처(H5)",
                         s2["collection_failed"]["by_noise_ctx_source"])
    lines += _md_counter("수집 실패 PAGE — db_id", s2["collection_failed"]["by_db_id"])
    lines += _md_counter("수집 실패 PAGE — 출처", s2["collection_failed"]["by_source"])
    lines += _md_counter("매트릭스 PAGE 승격 사유", s2["matrix_page_by_promote"])
    lines += [f"- 사후 승격(late_promotion) {s2['late_promotions']}건", ""]
    lines += ["## S3 반복", ""]
    lines += _md_counter("지문당 PAGE 횟수", s3["fp_page_count_dist"])
    lines += _md_counter("재PAGE 간격", s3["repage_interval_dist"])
    lines += [f"- dedup 창 안 재PAGE(재기동·지문 흔들림 흔적) "
              f"{s3['within_dedup_window_repage']}건", ""]
    lines += _md_table(["지문", "정규화 알람명", "db_id", "PAGE"], [
        (t["fp"], t["alarm_name_norm"], t["db_id"], t["pages"]) for t in s3["top_fingerprints"]
    ]) + [""]
    lines += ["## S4 군집", ""]
    lines += [f"- 30분 안 PAGE ≥2 호스트 {s4['hosts_multi_page_30m']}대 · 그 PAGE "
              f"{s4['pages_on_those_hosts']}건",
              f"- 10분 안 같은 알람 다수 호스트 군집 {s4['fanout_clusters_10m']}개 · 군집 PAGE "
              f"{s4['pages_in_fanout_clusters']}건", ""]
    lines += _md_counter("군집 크기(호스트 수)", s4["fanout_cluster_size_dist"])
    lines += ["## S5 시간", ""]
    lines += _md_table(["시", "PAGE"], s5["hourly_pages"].items()) + [""]
    lines += _md_table(["분", "PAGE", "직전 판정 공백(초)"], [
        (b["minute"], b["pages"], b["gap_before_s"]) for b in s5["bursts"]
    ]) + [""]
    lines += ["## S6 해소", "", *_md_counter("PAGE 후 해소까지", s6["clear_after_dist"])]
    lines += ["## S7 만성", "", f"- 긴 창 {s7['window_days']}일 · PAGE ≥{R6_CHRONIC_DAYS}일 지문 "
              f"{s7['chronic_fps']}개 · 그 PAGE {s7['chronic_pages']}건", ""]
    lines += _md_counter("지문별 PAGE 일수", s7["page_days_dist"])
    lines += ["## S8 what-if", "", f"- 원 PAGE {s8['pages_before']}건 · 긴 창 일평균 "
              f"{s8['daily_avg_long_before']} · 고아 합계 {s8['orphans_total']} · 레지스트리 "
              f"키 상한 초과로 버린 키 {s8['registry_evictions']}건", ""]
    headers = ["규칙", "남는 PAGE", "강등", "긴 창 일평균", "판정 불가", "고아", "강등 원 단계"]
    lines += _md_table(headers, [
        (name, v["pages_after"], v["demoted"], v["daily_avg_long"], v["undecidable"], v["orphans"],
         ", ".join(f"{k}:{n}" for k, n in v["demoted_origin_stage"].items()))
        for name, v in s8["rules"].items()
    ]) + [""]
    lines += [f"- 주: {note}" for note in s8["notes"]] + [""]
    lines += ["## S9 지문 안정성", "", *_md_table(["항목", "값"], s9.items()), ""]
    lines += ["## S10 데이터 품질", ""]
    lines += _md_table(["항목", "값"], [
        ("단계 라벨 없는 판정", s10["decisions_without_stage"]),
        ("AI 분석 실패", s10["analysis_failed"]),
        ("깨진 감사 줄", s10["bad_audit_lines"]),
        ("noise_ctx 출처 한계", s10["noise_ctx_source_limit"]),
    ]) + [""]
    lines += _md_counter("버린 근거 키(이름만)", s10["dropped_evidence_keys"])
    lines += _md_counter("기타 레코드 유형", s10["other_record_types"])
    excluded = ", ".join(s10["policy_excluded_fields"]) or "없음"
    lines += [f"- 정책 스냅샷 제외(민감 이름): {excluded}", ""]
    lines += [f"- 주: {note}" for note in s10["notes"]] + [""]
    if s10.get("xs_v0_actual_mismatch"):
        lines += [f"- 경고: 상관 시뮬레이션 V0 ≠ 실제 기록 {s10['xs_v0_actual_mismatch']}건(S13)",
                  ""]
    if "S11" in summary:
        lines += xsource.render_md(summary)
    return "\n".join(lines)


# ── 실행 ──────────────────────────────────────────────────────────────────────
@dataclass
class AuditResult:
    """실행 결과 — 종료 코드(0 정상 · 2 누출 검사 실패)와 출력 폴더."""

    exit_code: int
    out_dir: Path | None
    leak: dict[str, Any] | None = None


def _code_sha() -> str | None:
    """저장소 HEAD SHA(없으면 None)."""
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return res.stdout.strip()[:40] or None


def _policy_module_hash() -> str | None:
    """notification_policy.py 내용 해시(앞 12자)."""
    path = getattr(_policy_module, "__file__", None)
    if not path:
        return None
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]


def _stage_states(groups: Mapping[str, Any]) -> dict[str, str]:
    """단계별 활성 상태 — 스냅샷 플래그로 판독(매핑 없는 새 단계는 「미상」)."""
    ng = groups.get("noise_gate", {})
    out: dict[str, str] = {"(enable_noise_gate)": "on" if ng.get("enable_noise_gate") else "off"}
    for stage in STAGE_ORDER:
        if stage in _ALWAYS_STAGES:
            out[stage] = "상시"
        elif stage == STAGE_CROSS_SOURCE:
            out[stage] = str(ng.get("cross_source_mode", "미상"))
        elif stage == STAGE_PAGE_REVIEW:
            out[stage] = str(ng.get("page_review_mode", "미상"))
        elif stage in _STAGE_FLAGS:
            out[stage] = "on" if ng.get(_STAGE_FLAGS[stage]) else "off"
        else:
            out[stage] = "미상"
    return out


def _new_out_dir(out_root: Path, now: datetime) -> Path:
    """`<out>/<YYYYMMDD-HHMMSS>` 경로 — 이미 있으면 접미 번호를 붙인다(만들지는 않는다)."""
    base = out_root / now.astimezone(KST).strftime("%Y%m%d-%H%M%S")
    candidate, n = base, 1
    while candidate.exists():
        candidate = base.with_name(f"{base.name}-{n}")
        n += 1
    return candidate


def _write_private(path: Path, text: str) -> None:
    """새 파일을 0600으로 만들어 쓴다(있으면 실패)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)


def write_outputs(
    out_root: Path, now: datetime, files: Mapping[str, str], leak: dict[str, Any]
) -> Path:
    """산출물을 임시 폴더(`.tmp-<pid>` · 0700)에 다 쓴 뒤 확정 폴더로 옮긴다(`os.replace`).

    파일은 0600 · `leak_check.json`을 마지막에 쓴다. 누출 검사가 실패했으면 `leak_check.json`만
    쓴다. 중간에 실패하면 임시 폴더를 지우고 예외를 다시 올린다 — 반쯤 쓴 폴더가 남지 않는다.
    """
    out_root.mkdir(parents=True, exist_ok=True)
    tmp = out_root / f".tmp-{os.getpid()}"
    if tmp.exists():
        shutil.rmtree(tmp)
    os.mkdir(tmp, 0o700)
    try:
        os.chmod(tmp, 0o700)
        if leak["passed"]:
            for name, text in files.items():
                _write_private(tmp / name, text)
        leak_text = json.dumps(leak, ensure_ascii=False, indent=2) + "\n"
        _write_private(tmp / "leak_check.json", leak_text)
        final = _new_out_dir(out_root, now)
        os.replace(tmp, final)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return final


def _collect_identifiers(
    idents: IdentifierSet, raws: Iterable[RawAlarm], decisions: Iterable[Decision],
    recurrences: Iterable[tuple[datetime, dict[str, Any]]],
) -> None:
    """이번 실행에서 본 원문 식별자를 모은다(정규화 치환 · 누출 검사 대상)."""
    for raw in raws:
        p = raw.payload or {}
        idents.add(IDENT_HOST, p.get("serverName"))
        idents.add(IDENT_HOST, p.get("hostname"))
        idents.add(IDENT_IP, p.get("ipAddress"))
        idents.add(IDENT_RESOURCE, p.get("resourceName"))
        idents.add(IDENT_ALARM_ID, p.get("alarmId"))
    for d in decisions:
        idents.add(IDENT_HOST, d.rec.get("server_name"))
        idents.add(IDENT_RESOURCE, d.rec.get("resource_name"))
        idents.add(IDENT_ALARM_ID, d.rec.get("alarm_id"))
        root = d.signals.get("root_resource")
        if isinstance(root, str):
            idents.add(IDENT_RESOURCE, root)
    for _, rec in recurrences:
        idents.add(IDENT_ALARM_ID, rec.get("alarm_id"))


def _noise_ctx_source(d: Decision) -> str | None:
    """감사 레코드로 가릴 수 있는 noise_ctx 출처 — apm_policy · unavailable(수집 실패)."""
    if "apm_policy" in d.evidence:
        return "apm_policy"
    if d.stage == STAGE_COLLECTION_FAILED:
        return "unavailable"
    return None


def _context(
    seq: int, m: Matched, *, zone_of: Any, hours_start: datetime
) -> tuple[AlarmContext, str | None, str | None]:
    """맞대기 결과 → 원문 맥락. (맥락, 조건 로그 텍스트, 그 근거 raw|audit|None)."""
    raw, d = m.raw, m.decision
    p = (raw.payload if raw is not None else None) or {}
    rec = d.rec if d is not None else {}
    event = raw.event if raw is not None else None
    if event is None:
        event = AlarmEvent(
            db_id=str(rec.get("db_id") or ""), server_name=str(rec.get("server_name") or ""),
            hostname="", ip_address="", resource_ancestry="",
            alarm_id=str(rec.get("alarm_id") or ""),
            severity=_int((d.signals if d else {}).get("severity"), 0), alarm_status="",
            resource_type="", resource_name=str(rec.get("resource_name") or ""),
            alarm_name=str(rec.get("alarm_name") or ""),
            alarm_time=(d.ts if d else datetime.now(UTC)), conditions="",
            condition_log=str(rec.get("condition_log") or ""),
        )
    raw_log = str(p.get("conditionLog") or "")
    audit_log = str(rec.get("condition_log") or "")
    log_text, basis = (raw_log, "raw") if raw_log else ((audit_log, "audit") if audit_log
                                                        else (None, None))
    sig = scan_signature_severity(log_text) if log_text else None
    db_id = str(rec.get("db_id") or p.get("dbId") or "")
    severity = raw.severity if raw is not None and raw.severity is not None else (
        _int(d.signals.get("severity"), 0) if d is not None and "severity" in d.signals else None
    )
    when = d.ts if d is not None else (m.recurrence_ts or (raw.recv_ts if raw else None))
    ctx = AlarmContext(
        seq=seq,
        recv_ts=raw.recv_ts if raw is not None else None,
        decision_ts=d.ts if d is not None else m.recurrence_ts,
        db_id=db_id,
        zone=zone_of(db_id),
        source=SOURCE_JENNIFER if is_apm_source(event) else SOURCE_POLESTAR,
        server_name=str(rec.get("server_name") or p.get("serverName") or ""),
        hostname=str(p.get("hostname") or ""),
        ip_address=str(p.get("ipAddress") or ""),
        resource_name=str(rec.get("resource_name") or p.get("resourceName") or ""),
        resource_type=str(p.get("resourceType") or ""),
        resource_ancestry=str(p.get("resourceAncestry") or ""),
        alarm_name=str(rec.get("alarm_name") or p.get("alarmName") or ""),
        conditions=str(p.get("conditions") or ""),
        severity=severity,
        is_clear=(severity == 0) if severity is not None else None,
        sig_label=sig[1] if sig else None,
        # 원문이 없으면 자원 유형을 몰라 메시지형인지 가릴 수 없다(null).
        is_message=(
            None if raw is None else (is_message_alarm(event) if (raw_log or audit_log) else False)
        ),
        condition_log_len=len(log_text) if log_text is not None else (0 if raw else None),
        disposition=m.disposition,
        tier=d.tier if d is not None else None,
        stage=d.stage if d is not None else None,
        signals=d.signals if d is not None else None,
        evidence=d.evidence if d is not None else None,
        noise_ctx_source=_noise_ctx_source(d) if d is not None else None,
        fingerprint=(d.fingerprint if d is not None
                     else str((m.recurrence or {}).get("fingerprint") or "")),
        recurrence=rec.get("recurrence") if isinstance(rec.get("recurrence"), dict) else None,
        correlation_meta=(rec.get("correlation_meta")
                          if isinstance(rec.get("correlation_meta"), dict) else None),
        in_hours_window=bool(when is not None and when >= hours_start),
    )
    if d is not None:
        ctx.boost_origin = boost_origin(d, log_text)
    return ctx, log_text, basis


def run_audit(
    *,
    config: Any,
    decisions_path: Path,
    out_root: Path,
    hours: float = 24.0,
    days: int | None = None,
    streams: ReadOnlyStreams | None = None,
    summary_only: bool = False,
    now: datetime | None = None,
    key: bytes | None = None,
    sanitizer_cls: type[Sanitizer] = Sanitizer,
    max_stream_entries: int | None = DEFAULT_MAX_STREAM_ENTRIES,
) -> AuditResult:
    """분석 로그 1회 실행 — 모든 산출물을 메모리에서 만든 뒤 누출 검사를 통과해야만 쓴다.

    Args:
        config: `AppConfig()`(또는 `noise_gate`·`alarm` 그룹을 가진 객체) — 정책 스냅샷·창·키.
        decisions_path: 판정 감사 JSONL.
        out_root: 출력 루트(아래에 `<YYYYMMDD-HHMMSS>/`를 만든다).
        hours: 짧은 창(시간). days: 긴 창(일) — 없으면 짧은 창만.
        streams: 읽기 전용 스트림 래퍼(None이면 `--no-stream` — 감사 파일만).
        summary_only: `alarms.jsonl` 생략.
        now: 창 끝(시험용 주입 · 기본 현재).
        key: 가명 키(시험용 주입 · 기본 실행 단위 난수 — 어디에도 쓰지 않는다).
        sanitizer_cls: 허용 목록 변환기(누출 검사가 닫히는지 시험할 때만 바꾼다).
        max_stream_entries: 스트림별 읽기 상한(넘으면 `StreamLimitError`).
    """
    end = (now or datetime.now(UTC)).astimezone(UTC)
    long_hours = max(float(hours), float(days or 0) * 24.0)
    hours_start = end - timedelta(hours=float(hours))
    long_start = end - timedelta(hours=long_hours)
    read_start = long_start - HISTORY_MARGIN
    alarm_cfg = config.alarm
    ng_cfg = config.noise_gate

    audit = read_audit(decisions_path, read_start, end)
    coverage_start = max(read_start, audit.first_ts or end)

    raws: list[RawAlarm] = []
    dead_entries: list[tuple[str, Mapping[Any, Any]]] = []
    group_info: dict[str, Any] | None = None
    if streams is not None:
        stream_key = str(alarm_cfg.redis_stream_key)
        raw_start = long_start - RAW_SLACK
        raws = [parse_raw(i, f) for i, f in
                read_stream(streams, stream_key, raw_start, end, max_stream_entries)]
        dead_entries = read_stream(
            streams, str(alarm_cfg.dead_letter_stream_key), raw_start, end, max_stream_entries
        )
        group_info = _group_info(streams, stream_key, str(alarm_cfg.redis_consumer_group))

    # 맞대기·처분
    pool = [d for d in audit.decisions if d.ts >= long_start - RAW_SLACK]
    recs = [(ts, r) for ts, r in audit.recurrences if ts >= long_start - RAW_SLACK]
    leftover_rec = 0
    if streams is not None:
        dead_ids, dead_aids = _dead_keys(dead_entries)
        last = (_parse_stream_id(group_info["last_delivered_id"])
                if group_info and group_info.get("group_found") else None)
        matched, leftover, leftover_rec = assign_dispositions(
            raws, pool, recs, dead_msg_ids=dead_ids, dead_alarm_ids=dead_aids,
            min_severity=int(alarm_cfg.min_severity), last_delivered=last,
        )
        matched = [m for m in matched if m.raw is not None and m.raw.recv_ts >= long_start]
        matched += [Matched(None, "decision_without_raw", decision=d) for d in leftover
                    if d.ts >= long_start]
    else:
        matched = [Matched(None, "decided", decision=d) for d in pool if d.ts >= long_start]
        matched += [Matched(None, "dedup_fingerprint", recurrence=r, recurrence_ts=ts)
                    for ts, r in recs if ts >= long_start]

    def _when(m: Matched) -> datetime:
        if m.raw is not None:
            return m.raw.recv_ts
        if m.decision is not None:
            return m.decision.ts
        return m.recurrence_ts or end

    matched.sort(key=_when)

    # 파생 특징 · what-if (원문 키 — 출력 전)
    clears = build_resolutions(audit.decisions, audit.resolutions)
    derived, events = derive_features(
        audit.decisions, clears, window_start=long_start, coverage_start=coverage_start,
        repeat_interval_s=float(getattr(ng_cfg, "repeat_interval_seconds", DEDUP_WINDOW_DEFAULT_S)),
        sev3_repeat_interval_s=float(
            getattr(ng_cfg, "sev3_repeat_interval_seconds", DEDUP_WINDOW_DEFAULT_S)
        ),
    )

    # 비식별 준비
    idents = IdentifierSet()
    _collect_identifiers(idents, raws, audit.decisions, audit.recurrences)
    # (§11.4 W0-X) 인스턴스명·제니퍼 도메인명·URL·정규화 전후 호스트 키·미등록 APM 소스 id — 행을
    # 만들기 전에 더한다. 행 db_id는 레지스트리 등록 값만 원문으로 싣는다(`db_id_label`).
    reg_db_ids, reg_apm_sources = xsource.registry_ids()
    xsource.collect_identifiers(idents, (r.payload for r in raws), (d.rec for d in audit.decisions),
                                apm_sources=reg_apm_sources)
    pseudo = Pseudonymizer(key if key is not None else new_run_key())
    sanitizer = sanitizer_cls(pseudo, idents, db_ids=reg_db_ids, apm_sources=reg_apm_sources)
    zones: dict[str, str] = {}

    def zone_of(db_id: str) -> str:
        if db_id not in zones:
            zones[db_id] = zone_labels_for(db_id)[0] if db_id else ""
        return zones[db_id]

    contexts: list[AlarmContext] = []
    boost_basis: Counter[str] = Counter()
    event_by_idx = {ev.idx: ev for ev in events}
    for seq, m in enumerate(matched, start=1):
        ctx, _, basis = _context(seq, m, zone_of=zone_of, hours_start=hours_start)
        d = m.decision
        if d is not None and d.idx in derived:
            ctx.derived = derived[d.idx].as_dict()
            if d.idx in event_by_idx:
                event_by_idx[d.idx].boost_origin = ctx.boost_origin
        if ctx.boost_origin is not None and ctx.in_hours_window:
            boost_basis["raw" if basis == "raw" else ("audit" if basis == "audit" else "none")] += 1
        contexts.append(ctx)
    # 창 이전 이력의 PAGE는 행이 없다 — 감사 절단 로그로 상향 출처만 정한다(R5 판정용).
    for ev in events:
        if ev.boost_origin is None:
            d = audit.decisions[ev.idx]
            ev.boost_origin = boost_origin(d, str(d.rec.get("condition_log") or "") or None)

    runs, whatif = run_whatif(events, clears)
    page_idx_of_row: dict[int, int] = {}
    for i, (ctx, m) in enumerate(zip(contexts, matched)):
        if m.decision is not None and m.decision.idx in whatif:
            ctx.whatif = whatif[m.decision.idx]
            page_idx_of_row[i] = m.decision.idx

    rows = [sanitizer.row(ctx) for ctx in contexts]

    snapshot = policy_snapshot(config)
    if group_info is not None:
        snapshot["stream"] = group_info
    inputs = {
        "audit_lines": audit.lines,
        "decisions": len(audit.decisions),
        "recurrences": len(audit.recurrences),
        "resolutions": len(audit.resolutions),
        "late_promotions": audit.late_promotions,
        "raw_entries": len(raws),
        "dead_entries": len(dead_entries),
        "rows": len(rows),
    }
    meta = {
        "window": {
            "end": end.astimezone(KST).replace(microsecond=0).isoformat(),
            "hours": hours,
            "hours_start": hours_start.astimezone(KST).replace(microsecond=0).isoformat(),
            "long_days": days,
            "long_start": long_start.astimezone(KST).replace(microsecond=0).isoformat(),
            "read_start": read_start.astimezone(KST).replace(microsecond=0).isoformat(),
            "coverage_start": coverage_start.astimezone(KST).replace(microsecond=0).isoformat(),
        },
        "stream_read": streams is not None,
        "inputs": inputs,
        "code_sha": _code_sha(),
        "policy_module_sha256": _policy_module_hash(),
        "stages": _stage_states(snapshot["groups"]),
    }
    analysis_failed = sum(
        1 for d in audit.decisions
        if d.ts >= hours_start and isinstance(d.rec.get("analysis"), dict)
        and d.rec["analysis"].get("failed")
    )
    stats = {
        "recurrence_without_raw": leftover_rec,
        "boost_basis": dict(boost_basis),
        "late_promotions": audit.late_promotions,
        "analysis_failed": analysis_failed,
        "dropped_evidence_keys": _sorted_counter(sanitizer.dropped_evidence_keys),
        "policy_excluded": snapshot["excluded"],
        "policy_omitted": snapshot["omitted"],
        "bad_lines": audit.bad_lines,
        "other_types": _sorted_counter(_relabel(audit.other_types)),
    }
    summary = summarize(rows, meta=meta, runs=runs, page_idx_of_row=page_idx_of_row, stats=stats)

    # (§11 W0-X) 폴스타↔APM 상관 진단 — 행 `xs` · S11~S14 · 정책 스냅샷 `cross_source`.
    xs_args: dict[str, Any] = {
        "config": ng_cfg, "pseudo": pseudo, "stream_read": streams is not None,
        "late_promotions": audit.late_promotions,
        "window_seconds": (end - long_start).total_seconds(),
        "dedup_window_seconds": float(
            getattr(ng_cfg, "repeat_interval_seconds", DEDUP_WINDOW_DEFAULT_S)
        ),
    }
    xs = xsource.analyze(
        [xsource.XsItem(
            event=m.raw.event if m.raw is not None else None,
            rec=m.decision.rec if m.decision is not None else {},
            ts=m.decision.ts if m.decision is not None else _when(m),
            decided=m.decision is not None,
        ) for m in matched],
        recurrences=recs, **xs_args,
    )
    for row, xs_row in zip(rows, xs.rows, strict=True):
        row["xs"] = xs_row
    snapshot["cross_source"] = xs.snapshot
    summary.update(xs.sections)
    if xs.v0_actual_mismatch:
        summary["S10"]["xs_v0_actual_mismatch"] = xs.v0_actual_mismatch

    files: dict[str, str] = {
        "summary.json": json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        "summary.md": render_md(summary) + "\n",
        "policy_snapshot.json": json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
    }
    if not summary_only:
        files["alarms.jsonl"] = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)

    # 코드가 내는 고정 어휘(빈 입력 산출물 · 라벨)와 같은 식별자는 출력과 구별할 수 없어 뺀다.
    # 규칙 표 해시는 제외 재료가 아니다(64 hex 안에 짧은 식별자가 우연히 들어 있을 수 있다).
    empty = summarize([], meta=meta, runs={}, page_idx_of_row={}, stats=stats)
    empty.update(xsource.without_rules_hash(xsource.analyze([], **xs_args).sections))
    snapshot_vocab = {**snapshot, "cross_source": xsource.without_rules_hash(xs.snapshot)}
    idents.exclude_vocabulary("\n".join([
        json.dumps(empty, ensure_ascii=False), render_md(empty),
        json.dumps(snapshot_vocab, ensure_ascii=False),
        json.dumps(sanitizer.row(AlarmContext(seq=0)), ensure_ascii=False), *vocabulary_words(),
        *xs_vocabulary(),
    ]))
    leak = leak_check(files, idents, pseudo.emitted)
    out_dir = write_outputs(out_root, end, files, leak)
    return AuditResult(0 if leak["passed"] else 2, out_dir, leak)


def _relabel(counter: Counter[str]) -> Counter[str]:
    """기타 레코드 유형 — 알려진 유형만 이름으로, 나머지는 `<unknown_key>`로 합산."""
    out: Counter[str] = Counter()
    for kind, n in counter.items():
        out[record_type_label(kind)] += n
    return out


def dry_run(
    *, config: Any, decisions_path: Path, hours: float, days: int | None,
    streams: ReadOnlyStreams | None, now: datetime | None = None,
    max_stream_entries: int | None = DEFAULT_MAX_STREAM_ENTRIES,
) -> dict[str, Any]:
    """입력 위치·건수·창만 센다(쓰기 없음)."""
    end = (now or datetime.now(UTC)).astimezone(UTC)
    long_start = end - timedelta(hours=max(float(hours), float(days or 0) * 24.0))
    audit = read_audit(decisions_path, long_start - HISTORY_MARGIN, end)
    info: dict[str, Any] = {
        "window_end": end.astimezone(KST).replace(microsecond=0).isoformat(),
        "long_start": long_start.astimezone(KST).replace(microsecond=0).isoformat(),
        "decisions_path": str(decisions_path),
        "decisions_exists": decisions_path.exists(),
        "decisions": len(audit.decisions),
        "recurrences": len(audit.recurrences),
        "resolutions": len(audit.resolutions),
        "bad_lines": audit.bad_lines,
        "stream": None,
    }
    if streams is not None:
        alarm_cfg = config.alarm
        key = str(alarm_cfg.redis_stream_key)
        info["stream"] = {
            "key": key,
            "raw_entries": len(read_stream(streams, key, long_start, end, max_stream_entries)),
            "dead_key": str(alarm_cfg.dead_letter_stream_key),
            "dead_entries": len(read_stream(streams, str(alarm_cfg.dead_letter_stream_key),
                                            long_start, end, max_stream_entries)),
            "group": _group_info(streams, key, str(alarm_cfg.redis_consumer_group)),
        }
    return info


def main(argv: list[str] | None = None) -> int:
    """CLI 진입점. 오류는 예외 종류 이름만 보인다(`-v`면 상세) — 메시지에 원문이 실릴 수 있다."""
    from src.config import AppConfig

    parser = argparse.ArgumentParser(description="분류 정책 분석 로그(비식별) — plans/162 W0")
    parser.add_argument(
        "--dry-run", action="store_true", help="입력 위치·건수·창만 출력(쓰기 없음)"
    )
    parser.add_argument("--hours", type=float, default=24.0, help="짧은 창(시간, 기본 24)")
    parser.add_argument("--days", type=int, default=None, help="긴 창(일) — S7·S8 일평균")
    parser.add_argument("--no-stream", action="store_true", help="Redis 없이 감사 파일만")
    parser.add_argument("--decisions", type=Path, default=None,
                        help="판정 감사 JSONL(기본 NOISE_DECISION_STORE_PATH)")
    parser.add_argument("--out", type=Path, default=Path("results/page_audit"), help="출력 루트")
    parser.add_argument("--summary-only", action="store_true", help="alarms.jsonl 생략")
    parser.add_argument("--max-stream-entries", type=int, default=DEFAULT_MAX_STREAM_ENTRIES,
                        help=f"스트림별 읽기 상한(기본 {DEFAULT_MAX_STREAM_ENTRIES:,}) — "
                             "넘으면 중단")
    parser.add_argument("-v", "--verbose", action="store_true", help="오류 상세 출력")
    args = parser.parse_args(argv)

    config = AppConfig()
    decisions_path = args.decisions or Path(str(config.noise_gate.decision_store_path))
    try:
        streams = None if args.no_stream else open_streams(config.redis)
        if args.dry_run:
            print(json.dumps(dry_run(config=config, decisions_path=decisions_path, hours=args.hours,
                                     days=args.days, streams=streams,
                                     max_stream_entries=args.max_stream_entries),
                             ensure_ascii=False, indent=2))
            return 0
        result = run_audit(
            config=config, decisions_path=decisions_path, out_root=args.out, hours=args.hours,
            days=args.days, streams=streams, summary_only=args.summary_only,
            max_stream_entries=args.max_stream_entries,
        )
    except StreamLimitError:
        print(f"스트림 항목이 --max-stream-entries({args.max_stream_entries:,})를 넘어 멈췄다 — "
              "--hours·--days를 줄이거나 상한을 올린다", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — 원문이 실릴 수 있는 메시지 대신 종류만 보이고 끝낸다
        hint = " — 스트림 없이 돌리려면 --no-stream" if (
            isinstance(exc, OSError) or type(exc).__module__.startswith("redis")
        ) else ""
        print(f"실행 실패: {type(exc).__name__}{hint}", file=sys.stderr)
        if args.verbose:
            raise
        return 1
    if result.exit_code == 2:
        print(f"누출 검사 실패 — 분석 파일을 쓰지 않았다: {result.out_dir}/leak_check.json",
              file=sys.stderr)
    else:
        print(f"출력: {result.out_dir}")
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
