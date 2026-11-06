"""업무 현황 상세 「개요」 — 구성 맵(topology) · 24시간 추이(trend) 조립.

plans/215 §5.2 ④⑦ · D-368.

입력은 현황 상세(`build_detail` 결과 — 존 필터·구성원 상태 판정을 마친 것)다. 여기서는 그 위에
레인 배치 · APM·SMS 연계 선 · 상태 띠 · 스파크라인 점만 만든다(관측 시스템 호출 0 · LLM 0).

- **구성 맵**: 계층 레인 웹 → WAS → DB → 배치 → 기타 → 미지정(계층 값이 없거나 모르는 값).
  레인 안 순서는 고정 정렬 키(상태 나쁜 순 → 종류 → 이름 → 구성원 id)라 같은 입력이면 같은
  배치다. 보이는 구성원이 `FOLD_THRESHOLD`(60)를 넘으면 레인마다 `FOLDED_LANE_CAP`개만 싣고 나머지는
  「외 N」(`folded_count`)으로 접는다.
- **연계 선**: APM 인스턴스 구성원 ↔ SMS 서버 구성원 — 210 연계 사본(`apmhostmap:{source}` 해시 ·
  소스당 HMGET 1회)의 `state=active` 행 대상만 긋는다. 자동·미확인 행은 추정 선(`estimated`),
  대상이 사라진 것(`target_gone`)은 끊김 선(`broken`)이다. 대상이 이 서비스의 보이는 구성원이 아니면
  선 없이 `outside_targets` 건수만 센다(권한 밖 서버 이름을 드러내지 않는다). 사본이 없거나 읽지
  못하면 선 없이 사유(`links_status.reason`)를 낸다.
- **추이**: 24시간 상태 띠(상태 전이 기록 `biz_service_state_log`) + 스파크라인 점(Redis 시계열
  `bizmon:ts:{id}`). 상태 띠·시계열은 서비스 전체(모든 구성원) 기준이라, 존 권한 밖 구성원이 있는
  사용자에게는 보이지 않고 사유만 낸다(머리의 상태는 보이는 구성원만으로 판정한다 —
  섞으면 어긋난다).

계층: application — domain 키·infrastructure 사본 키 함수만 쓴다(Redis·저장소는 덕 타이핑 주입).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from noise_gate.application.biz_monitor_collector import TS_PREFIX
from noise_gate.domain.apm_host_map import (
    KIND_SMS,
    ORIGIN_AUTO,
    STATE_ACTIVE,
    STATE_AMBIGUOUS,
    STATE_DISABLED,
    STATE_FAILED,
    cache_field,
)
from noise_gate.domain.biz_service_keys import (
    KIND_APM_BUSINESS,
    KIND_APM_DOMAIN,
    KIND_APM_INSTANCE,
    KIND_SMS_SERVER,
    parse_member_key,
    sms_member_key,
)
from noise_gate.domain.biz_service_model import LAYERS
from noise_gate.domain.biz_service_state import STATE_LABELS
from noise_gate.infrastructure.apm_host_map_cache import map_key

logger = logging.getLogger(__name__)

#: 미지정 레인(계층 값이 없거나 `LAYERS` 밖).
LANE_NONE = "none"
LANE_LABELS: Mapping[str, str] = {
    "web": "웹",
    "was": "WAS",
    "db": "DB",
    "batch": "배치",
    "etc": "기타",
    LANE_NONE: "미지정",
}
#: 레인 순서(웹 → WAS → DB → 배치 → 기타 → 미지정).
LANE_ORDER: tuple[str, ...] = (*LAYERS, LANE_NONE)
#: 이 수를 넘으면 레인별 접기.
FOLD_THRESHOLD = 60
#: 접을 때 레인마다 싣는 구성원 수.
FOLDED_LANE_CAP = 10

_STATE_ORDER = {"risk": 0, "caution": 1, "unknown": 2, "no_signal": 3}
_KIND_ORDER = {KIND_SMS_SERVER: 0, KIND_APM_INSTANCE: 1, KIND_APM_DOMAIN: 2, KIND_APM_BUSINESS: 3}

LINKS_NO_CACHE = "APM·SMS 연계 사본 없음 — 연계 선을 그리지 않습니다"
LINKS_READ_FAILED = "APM·SMS 연계 사본 조회 실패 — 연계 선을 그리지 않습니다"
NOTE_NO_MAP = "연계 매핑 없음"
NOTE_BAD_KEY = "연계 키를 만들 수 없음"
_STATE_NOTES = {
    STATE_DISABLED: "연계 없음(관리자 지정)",
    STATE_AMBIGUOUS: "연계 후보 여러 개 — 고르지 않음",
    STATE_FAILED: "연계 실패",
}

#: 추이 창(초) · 상태 전이 1회 조회 상한.
TREND_WINDOW_SECONDS = 86400
STATE_LOG_PAGE = 100
NO_RECORD_LABEL = "기록 없음"
TREND_HIDDEN_REASON = "권한 밖 구성원 {n}대가 있어 서비스 전체 추이를 보이지 않습니다"
BAND_NO_REPO = "상태 전이 저장소 없음"
BAND_READ_FAILED = "상태 전이 기록 조회 실패"
SERIES_UNMONITORED = "감시 꺼짐 — 상태 띠만"
SERIES_METRICS_OFF = "지표 수집 꺼짐 — 알람 현황만"
SERIES_NO_REDIS = "업무 현황 Redis 연결 없음"
SERIES_READ_FAILED = "지표 시계열 조회 실패"


def _text(value: Any) -> str:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace").strip()
    return str(value if value is not None else "").strip()


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat()


def _epoch(raw: Any) -> float | None:
    text = _text(raw)
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp()


# ── 구성 맵 ──────────────────────────────────────────────────────────────
def lane_of(layer: Any) -> str:
    """구성원 계층 → 레인 이름(없거나 모르는 값은 미지정)."""
    value = _text(layer).lower()
    return value if value in LAYERS else LANE_NONE


def node_sort_key(member: Mapping[str, Any]) -> tuple[int, int, str, int]:
    """레인 안 고정 정렬 키 — 상태 나쁜 순 → 종류 → 이름 → 구성원 id."""
    mid = member.get("id")
    return (
        _STATE_ORDER.get(_text(member.get("state")), 9),
        _KIND_ORDER.get(_text(member.get("kind")), 9),
        _text(member.get("name")).lower(),
        mid if isinstance(mid, int) and not isinstance(mid, bool) else 0,
    )


def _instance_parts(member: Mapping[str, Any]) -> tuple[str, str, str, str] | None:
    if _text(member.get("kind")) != KIND_APM_INSTANCE:
        return None
    parts = parse_member_key(KIND_APM_INSTANCE, _text(member.get("member_key")))
    if parts is None:
        return None
    source, domain, scope, match_key = parts
    return source, domain, scope, match_key


async def read_link_records(
    map_cache: Any, members: Sequence[Mapping[str, Any]]
) -> tuple[dict[str, dict[str, Any]], str]:
    """APM 인스턴스 구성원의 210 연계 사본 값 — `({member_key: 값}, 사유)`.

    소스마다 HMGET 1회. 사본이 없으면 사유 `LINKS_NO_CACHE`, Redis 오류면 `LINKS_READ_FAILED`
    (로그를 남긴다 — 침묵 금지). 사본에 없는 키는 결과에서 빠진다(호출부가 「연계 매핑 없음」).
    """
    wanted: dict[str, list[tuple[str, str]]] = {}
    for m in members:
        parts = _instance_parts(m)
        if parts is None:
            continue
        source, domain, scope, match_key = parts
        try:
            hkey, fld = map_key(source), cache_field(scope, domain, match_key)
        except ValueError:
            continue
        wanted.setdefault(hkey, []).append((_text(m.get("member_key")), fld))
    if not wanted:
        return {}, ""
    redis = getattr(map_cache, "redis", None) if map_cache is not None else None
    if redis is None:
        return {}, LINKS_NO_CACHE
    out: dict[str, dict[str, Any]] = {}
    for hkey in sorted(wanted):
        pairs = wanted[hkey]
        try:
            values = await redis.hmget(hkey, [f for _, f in pairs])
        except Exception:  # noqa: BLE001 — 사유로 돌려준다(선 없음)
            logger.warning("업무 현황 구성 맵 — 연계 사본 조회 실패: key=%s", hkey, exc_info=True)
            return {}, LINKS_READ_FAILED
        for (key, _), raw in zip(pairs, values or []):
            text = _text(raw)
            if not text:
                continue
            try:
                value = json.loads(text)
            except ValueError:
                logger.warning("업무 현황 구성 맵 — 연계 사본 값 손상: %s", key)
                continue
            if isinstance(value, dict):
                out[key] = value
    return out, ""


def _node(member: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": member.get("id"),
        "kind": member.get("kind"),
        "name": _text(member.get("name")),
        "state": member.get("state"),
        "state_label": member.get("state_label"),
        "layer": lane_of(member.get("layer")),
        "confidence": member.get("confidence"),
        "link_note": "",
        "outside_targets": 0,
    }


def _link(
    node: dict[str, Any],
    record: Mapping[str, Any] | None,
    sms_ids: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """APM 인스턴스 노드 1개의 선 — 노드의 `link_note`·`outside_targets`도 채운다."""
    if record is None:
        node["link_note"] = NOTE_NO_MAP
        return []
    state = _text(record.get("state"))
    if state != STATE_ACTIVE:
        node["link_note"] = _STATE_NOTES.get(state, NOTE_NO_MAP)
        return []
    estimated = _text(record.get("origin")) == ORIGIN_AUTO and record.get("verified") is not True
    label = _text(record.get("evidence_text"))
    edges: dict[Any, dict[str, Any]] = {}
    for target in record.get("targets") or ():
        if not isinstance(target, Mapping) or (_text(target.get("kind")) or KIND_SMS) != KIND_SMS:
            continue
        key = sms_member_key(target.get("db_id"), target.get("resource_id"))
        to = sms_ids.get(key) if key else None
        if to is None:
            node["outside_targets"] += 1
            continue
        broken = target.get("target_gone") is True
        prev = edges.get(to)
        if prev is not None and not (prev["broken"] and not broken):
            continue
        edges[to] = {
            "from": node["id"],
            "to": to,
            "estimated": estimated,
            "broken": broken,
            "label": label,
        }
    return list(edges.values())


def build_topology(
    detail: Mapping[str, Any],
    records: Mapping[str, Mapping[str, Any]],
    *,
    links_reason: str = "",
) -> dict[str, Any]:
    """구성 맵 응답 — 레인 · 노드 · 선(존 필터는 `detail`이 이미 걸었다)."""
    members = [m for m in detail.get("members") or () if isinstance(m, Mapping)]
    service = detail.get("service") or {}
    sms_ids = {
        _text(m.get("member_key")): m.get("id")
        for m in members
        if _text(m.get("kind")) == KIND_SMS_SERVER
    }
    by_lane: dict[str, list[Mapping[str, Any]]] = {lane: [] for lane in LANE_ORDER}
    for m in members:
        by_lane[lane_of(m.get("layer"))].append(m)
    folded = len(members) > FOLD_THRESHOLD
    lanes: list[dict[str, Any]] = []
    shown: dict[Any, dict[str, Any]] = {}
    all_edges: list[dict[str, Any]] = []
    for lane in LANE_ORDER:
        ordered = sorted(by_lane[lane], key=node_sort_key)
        cap = FOLDED_LANE_CAP if folded else len(ordered)
        nodes = []
        for i, m in enumerate(ordered):
            node = _node(m)
            if not links_reason and _instance_parts(m) is not None:
                all_edges.extend(_link(node, records.get(_text(m.get("member_key"))), sms_ids))
            elif _text(m.get("kind")) == KIND_APM_INSTANCE and _instance_parts(m) is None:
                node["link_note"] = NOTE_BAD_KEY
            if i < cap:
                nodes.append(node)
                shown[node["id"]] = node
        lanes.append(
            {
                "layer": lane,
                "label": LANE_LABELS[lane],
                "nodes": nodes,
                "folded_count": len(ordered) - len(nodes),
            }
        )
    edges = sorted(
        (e for e in all_edges if e["from"] in shown and e["to"] in shown),
        key=lambda e: (_text(e["from"]), _text(e["to"])),
    )
    return {
        "generated_at": detail.get("generated_at"),
        "service_id": service.get("id"),
        "member_count": len(members),
        "hidden_member_count": detail.get("hidden_member_count", 0),
        "folded": folded,
        "lanes": lanes,
        "edges": edges,
        "folded_edge_count": len(all_edges) - len(edges),
        "links_status": {"available": not links_reason, "reason": links_reason},
    }


# ── 추이 ────────────────────────────────────────────────────────────────
def _state_view(state: Any) -> tuple[str | None, str]:
    value = _text(state) or None
    if value is None or value not in STATE_LABELS:
        return None, NO_RECORD_LABEL
    return value, STATE_LABELS[value]


def build_state_band(
    rows_desc: Iterable[Mapping[str, Any]],
    prior: Mapping[str, Any] | None,
    *,
    since: float,
    now: float,
) -> list[dict[str, Any]]:
    """상태 전이(최근 순) → 시간 순 구간 `[{start, end, state, state_label}]`.

    창 시작 상태는 창 안 첫 전이의 `from_state`, 창 안 전이가 없으면 창 이전 마지막 전이(`prior`)의
    `to_state`다. 모르면 「기록 없음」(state None).
    """
    rows: list[tuple[float, Mapping[str, Any]]] = []
    for r in rows_desc:
        at = _epoch(r.get("at"))
        if at is not None and at >= since:
            rows.append((min(at, now), r))
    rows.sort(key=lambda pair: pair[0])
    current: Any = rows[0][1].get("from_state") if rows else (prior or {}).get("to_state")
    cuts: list[tuple[float, float, Any]] = []
    cursor = since
    for at, row in rows:
        if at > cursor:
            cuts.append((cursor, at, current))
        cursor, current = max(cursor, at), row.get("to_state")
    if now > cursor:
        cuts.append((cursor, now, current))
    out: list[dict[str, Any]] = []
    for start, end, raw in cuts:
        state, label = _state_view(raw)
        if out and out[-1]["state"] == state:
            out[-1]["end"] = _iso(end)
            continue
        out.append({"start": _iso(start), "end": _iso(end), "state": state, "state_label": label})
    return out


async def read_state_band(
    repo: Any, service_id: int, *, since: float, now: float
) -> tuple[list[dict[str, Any]], bool, str]:
    """저장소에서 띠를 만든다 → `(구간, 잘림 여부, 사유)` — 저장소 없음·실패는 사유로."""
    if repo is None:
        return [], False, BAND_NO_REPO
    since_dt = datetime.fromtimestamp(since, UTC)
    try:
        rows = await repo.list_state_log(service_id, since=since_dt, limit=STATE_LOG_PAGE)
        prior = None
        if not rows:
            latest = await repo.list_state_log(service_id, limit=1)
            prior = latest[0] if latest else None
    except Exception:  # noqa: BLE001 — 사유로 돌려준다
        logger.warning(
            "업무 현황 추이 — 상태 전이 조회 실패: service=%s", service_id, exc_info=True
        )
        return [], False, BAND_READ_FAILED
    truncated = len(rows) >= STATE_LOG_PAGE
    if truncated:
        since = max(since, min((_epoch(r.get("at")) or since) for r in rows))
    return build_state_band(rows, prior, since=since, now=now), truncated, ""


def _metric_map(raw: Any) -> dict[str, float]:
    out: dict[str, float] = {}
    if not isinstance(raw, Mapping):
        return out
    for name, value in raw.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        out[_text(name)] = float(value)
    return out


def parse_ts_point(raw: Any) -> dict[str, Any] | None:
    """시계열 멤버 JSON 1개 → `{at, state, sms_max, apm_max}`(손상이면 None)."""
    try:
        value = json.loads(_text(raw))
    except ValueError:
        return None
    if not isinstance(value, Mapping) or _epoch(value.get("at")) is None:
        return None
    state, _ = _state_view(value.get("state"))
    return {
        "at": _text(value.get("at")),
        "state": state,
        "sms_max": _metric_map(value.get("sms_max")),
        "apm_max": _metric_map(value.get("apm_max")),
    }


async def read_trend_points(
    redis: Any, service_id: int, *, since: float
) -> tuple[list[dict[str, Any]], str]:
    """`bizmon:ts:{id}`에서 창 안 점(시간 순) → `(점, 사유)`."""
    if redis is None:
        return [], SERIES_NO_REDIS
    try:
        raw = await redis.zrangebyscore(f"{TS_PREFIX}{int(service_id)}", since, "+inf")
    except Exception:  # noqa: BLE001 — 사유로 돌려준다
        logger.warning("업무 현황 추이 — 시계열 조회 실패: service=%s", service_id, exc_info=True)
        return [], SERIES_READ_FAILED
    points = [p for p in (parse_ts_point(r) for r in raw or ()) if p is not None]
    points.sort(key=lambda p: _epoch(p["at"]) or 0.0)
    return points, ""


async def build_trend(
    detail: Mapping[str, Any],
    *,
    repo: Any,
    redis: Any,
    metrics_active: bool,
    restricted: bool,
    now: float,
) -> dict[str, Any]:
    """추이 응답 — 24시간 상태 띠 + 스파크라인 점(감시 꺼짐·수집 꺼짐이면 점 없이 사유)."""
    service = detail.get("service") or {}
    sid = int(service.get("id") or 0)
    hidden = int(detail.get("hidden_member_count") or 0)
    monitored = service.get("monitored") is True
    since = now - TREND_WINDOW_SECONDS
    band: list[dict[str, Any]] = []
    points: list[dict[str, Any]] = []
    truncated = False
    if restricted and hidden > 0:
        band_reason = series_reason = TREND_HIDDEN_REASON.format(n=hidden)
    else:
        band, truncated, band_reason = await read_state_band(repo, sid, since=since, now=now)
        if not monitored:
            series_reason = SERIES_UNMONITORED
        elif not metrics_active:
            series_reason = SERIES_METRICS_OFF
        else:
            points, series_reason = await read_trend_points(redis, sid, since=since)
    return {
        "generated_at": _iso(now),
        "service_id": sid,
        "window_seconds": TREND_WINDOW_SECONDS,
        "since": _iso(since),
        "monitored": monitored,
        "hidden_member_count": hidden,
        "band": {"segments": band, "truncated": truncated, "reason": band_reason},
        "series": {"points": points, "reason": series_reason},
    }


__all__ = [
    "FOLDED_LANE_CAP",
    "FOLD_THRESHOLD",
    "LANE_LABELS",
    "LANE_NONE",
    "LANE_ORDER",
    "STATE_LOG_PAGE",
    "TREND_WINDOW_SECONDS",
    "build_state_band",
    "build_topology",
    "build_trend",
    "lane_of",
    "node_sort_key",
    "parse_ts_point",
    "read_link_records",
    "read_state_band",
    "read_trend_points",
]
