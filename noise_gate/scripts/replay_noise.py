"""노이즈 정책 리플레이 하네스 (plans/144 W0).

알람 이벤트를 다시 정책 순수 함수(`decide_notification`)에 통과시켜 **소스별(폴스타/제니퍼)·
티어별·단계별 건수**와 PAGE+TICKET 건수를 JSON으로 낸다. Redis·DB·LLM·서버를 쓰지 않는다.

입력(JSONL — 줄마다 형식을 판별한다):
    (a) 목업 레코드 `{"scenario", "payload"[, "noise_ctx"]}` — `mock_polestar_events --dump` 출력
        (`noise_gate/testdata/cross_source/mock_events.jsonl`). 맨 `alarm:raw` 페이로드 줄도 받는다.
    (b) 결정 감사 레코드(`decision_store.record` 한 줄 — `tier`·`signals` 보유). `type`이 붙은
        부속 레코드(재발·해소·조사 등)는 정책을 거치지 않은 것이라 건너뛴다(`skipped`에 센다).

noise_ctx는 **주입 가능한 공급 함수**(`NoiseCtxProvider`)로 받는다. 기본 `current_noise_ctx`는
현행을 재현한다 — 제니퍼는 수집기가 없어 `source="unavailable"`, 폴스타는 기록된 신호(목업
`noise_ctx` · 결정 레코드 `signals`)를 쓰고 없으면 수집 실패로 본다. 이후 Wave는 공급 함수만
갈아끼워 비교한다. 워커 단계(핑거프린트 재발 억제·해소 짝맞춤 등)는 재현하지 않는다 — 결정
레코드에 남은 워커 산출 bool(self_heal·flapping·storm·correlated)만 그대로 넘긴다.

`--cross-source-mode`(plans/144 W3 · 기본 off)를 주면 워커와 **같은 도메인 사건 추적기·규칙 표**로
입력 순서대로 사건을 묶고 크로스소스 신호를 정책에 넘긴다(발생 시각을 시계로 쓴다 · 존은
레지스트리로 푼다 · 해소 짝맞춤 지문은 APM이면 정규화 유형). 요약에 `cross_source`(사건 수 ·
「했을 강등」 수 · link 수 · 실제 상한 수 · 빠진 조건 분포)를 더한다. off면 출력이 종전과 같다.

사용:
    python -m noise_gate.scripts.replay_noise noise_gate/testdata/cross_source/mock_events.jsonl
    python -m noise_gate.scripts.replay_noise logs/alarm_decisions.jsonl --out /tmp/replay.json
    python -m noise_gate.scripts.replay_noise noise_gate/testdata/cross_source/mock_events.jsonl \
        --cross-source-mode shadow
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from noise_gate.application.server_identity import is_apm_source, zone_labels_for
from noise_gate.domain.alarm import AlarmEvent, ServerIdentity
from noise_gate.domain.cross_source import (
    ACTION_DEMOTE,
    ACTION_LINK,
    MODE_OFF,
    EpisodeTracker,
)
from noise_gate.domain.notification_policy import (
    STAGE_COLLECTION_FAILED,
    STAGE_CROSS_SOURCE,
    TIER_PAGE,
    TIER_TICKET,
    compute_fingerprint,
    decide_notification,
    stage_from_reason,
)
from noise_gate.domain.severity import coerce_severity
from noise_gate.infrastructure.apm_noise_context import event_type_norm
from noise_gate.infrastructure.cross_source_rules import episode_alarm, load_rules
from noise_gate.infrastructure.polestar_noise_context import _unavailable

SOURCE_POLESTAR = "polestar"
SOURCE_JENNIFER = "jennifer"

# (event, 기록된 신호 | None) → noise_ctx | None. None·source="unavailable"이면 정책이 수집 실패.
NoiseCtx = dict[str, Any]
NoiseCtxProvider = Callable[[AlarmEvent, NoiseCtx | None], NoiseCtx | None]

# 리플레이 정책 설정 — 플래그는 전부 기본(off)이고 중요도 코드만 픽스처 규약(1·2·3)으로 둔다.
DEFAULT_POLICY_CONFIG = SimpleNamespace(
    importance_value_map={"1": "낮음", "2": "보통", "3": "높음"},
)

# 결정 레코드 signals 중 워커가 산출해 정책 인자로 넘긴 bool.
_WORKER_FLAGS = ("self_heal", "flapping", "storm", "correlated")

# 크로스소스 규칙 표 기본 경로(설정 `cross_source_rules_path` 기본값과 같다).
DEFAULT_CROSS_SOURCE_RULES = "config/cross_source_rules.yaml"
# 사건 idle 종료(설정 `episode_idle_seconds` 기본값과 같다).
DEFAULT_EPISODE_IDLE_SECONDS = 900


def current_noise_ctx(event: AlarmEvent, recorded: NoiseCtx | None) -> NoiseCtx | None:
    """현행 공급자 — 제니퍼는 수집 실패, 폴스타는 기록된 신호(없으면 수집 실패)."""
    if is_apm_source(event) or recorded is None:
        return _unavailable()
    return recorded


def event_from_payload(payload: dict[str, Any]) -> AlarmEvent:
    """`alarm:raw` 페이로드 → AlarmEvent(워커 파싱과 같은 규칙 · 시각 파싱 실패는 epoch)."""
    try:
        alarm_time = datetime.strptime(str(payload.get("alarmTime", "")), "%Y%m%d%H%M%S")
    except ValueError:
        alarm_time = datetime(1970, 1, 1)
    severity, _ = coerce_severity(payload.get("severity"))
    return AlarmEvent(
        db_id=payload.get("dbId", ""),
        server_name=payload.get("serverName", ""),
        hostname=payload.get("hostname", ""),
        ip_address=payload.get("ipAddress", ""),
        resource_ancestry=payload.get("resourceAncestry", ""),
        alarm_id=str(payload.get("alarmId", "")),
        severity=severity,
        alarm_status=payload.get("alarmStatus", ""),
        resource_type=payload.get("resourceType", ""),
        resource_name=payload.get("resourceName", ""),
        alarm_name=payload.get("alarmName", ""),
        alarm_time=alarm_time,
        conditions=payload.get("conditions", ""),
        condition_log=payload.get("conditionLog", ""),
        is_clear=(severity == 0),
        raw_payload=payload,
    )


def _from_decision(
    rec: dict[str, Any], config: Any
) -> tuple[AlarmEvent, NoiseCtx | None, dict[str, Any], Any]:
    """결정 레코드 → (event, 기록된 신호, 워커 인자, 분석 대용).

    기록된 신호는 원 결정이 수집 실패였으면 None이다. 중요도는 라벨로 남아 있어 설정 매핑을
    거꾸로 찾아 코드로 되돌린다(못 찾으면 None → 정책이 '보통'으로 본다).
    """
    raw_signals = rec.get("signals")
    signals: dict[str, Any] = raw_signals if isinstance(raw_signals, dict) else {}
    severity, _ = coerce_severity(signals.get("severity"))
    try:
        when = datetime.fromisoformat(str(rec.get("ts", "")))
    except ValueError:
        when = datetime(1970, 1, 1)
    event = AlarmEvent(
        db_id=str(rec.get("db_id", "") or ""), server_name=str(rec.get("server_name", "") or ""),
        hostname="", ip_address="", resource_ancestry="",
        alarm_id=str(rec.get("alarm_id", "") or ""), severity=severity, alarm_status="",
        resource_type="", resource_name=str(rec.get("resource_name", "") or ""),
        alarm_name=str(rec.get("alarm_name", "") or ""), alarm_time=when, conditions="",
        condition_log=str(rec.get("condition_log", "") or ""), is_clear=(severity == 0),
    )
    stage = rec.get("stage") or stage_from_reason(str(rec.get("reason", "")))
    recorded: NoiseCtx | None = None
    if stage != STAGE_COLLECTION_FAILED:
        code_of = {label: code for code, label in config.importance_value_map.items()}
        recorded = {
            "importance_id": code_of.get(signals.get("importance")),
            "maintenance": signals.get("maintenance"),
            "noti_policy": signals.get("noti_policy"),
            "parent_avail_status": signals.get("parent_avail_status"),
            "cascaded": signals.get("cascaded"),
            "root_resource": signals.get("root_resource"),
            "source": "decision_store",
        }
    flags = {k: bool(signals.get(k)) for k in _WORKER_FLAGS}
    analysis = SimpleNamespace(
        pattern_type=signals.get("pattern") or "",
        is_routine=signals.get("is_routine"),
        ai_message_severity=signals.get("ai_severity"),
        llm_actionability=signals.get("llm_actionability"),
    )
    return event, recorded, flags, analysis


def _cross_source_signal(
    tracker: EpisodeTracker, event: AlarmEvent
) -> dict[str, Any] | None:
    """워커 `_observe_episode`와 같은 규칙으로 사건에 반영한다(시계 = 발생 시각)."""
    apm = is_apm_source(event)
    if event.server_identity is None:
        zone, zone_label, _ = zone_labels_for(event.db_id)
        event.server_identity = ServerIdentity(
            hostname=event.hostname, zone=zone, zone_label=zone_label
        )
    fingerprint = compute_fingerprint(event, alarm_key=event_type_norm(event) if apm else None)
    alarm = episode_alarm(event, fingerprint=fingerprint, is_apm=apm)
    if event.is_clear:
        tracker.resolve(alarm, alarm.occurred_at)
        return None
    return tracker.observe(alarm, alarm.occurred_at)


def build_tracker(mode: str, rules_path: str = DEFAULT_CROSS_SOURCE_RULES) -> EpisodeTracker:
    """리플레이용 사건 추적기 — 규칙 표 적재 실패면 예외(하네스는 조용히 off로 가지 않는다)."""
    rules = load_rules(rules_path)
    if rules is None:
        raise SystemExit(f"크로스소스 규칙 표 적재 실패: {rules_path}")
    return EpisodeTracker(rules, mode=mode, idle_seconds=DEFAULT_EPISODE_IDLE_SECONDS)


def replay(
    lines: Iterable[dict[str, Any]],
    *,
    provider: NoiseCtxProvider = current_noise_ctx,
    config: Any = DEFAULT_POLICY_CONFIG,
    tracker: EpisodeTracker | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """레코드를 정책에 다시 통과시킨다 → (이벤트별 행, 건너뛴 줄 수).

    tracker(plans/144 W3)가 있으면 사건 신호를 정책에 넘기고 행에 `cross_source`를 더한다.
    """
    rows: list[dict[str, Any]] = []
    skipped = 0
    for line in lines:
        flags: dict[str, Any] = {}
        recorded: NoiseCtx | None
        analysis: Any = None
        if isinstance(line.get("payload"), dict):
            event, recorded = event_from_payload(line["payload"]), line.get("noise_ctx")
        elif "alarmId" in line:
            event, recorded = event_from_payload(line), None
        elif "tier" in line and "type" not in line:
            event, recorded, flags, analysis = _from_decision(line, config)
        else:
            skipped += 1
            continue
        cross_source = _cross_source_signal(tracker, event) if tracker is not None else None
        if cross_source is not None:
            flags["cross_source"] = cross_source
        decision = decide_notification(
            event, None, analysis, provider(event, recorded), config, **flags
        )
        row = {
            "alarm_id": event.alarm_id,
            "scenario": line.get("scenario", ""),
            "source": SOURCE_JENNIFER if is_apm_source(event) else SOURCE_POLESTAR,
            "severity": event.severity,
            "tier": decision.tier,
            "stage": decision.stage,
        }
        if tracker is not None:
            row["cross_source"] = cross_source
            if cross_source is not None:
                tracker.record_tier(cross_source["episode_id"], event.alarm_id, decision.tier)
        rows.append(row)
    return rows, skipped


def summarize_cross_source(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """크로스소스 shadow 지표 — 사건 수 · 했을 강등 · link · 실제 상한 · 빠진 조건 분포."""
    signals = [r["cross_source"] for r in rows if r.get("cross_source")]
    judged = [s for s in signals if s.get("rule_id")]
    missing: Counter[str] = Counter()
    for s in judged:
        missing.update(s.get("missing") or [])
    return {
        "episodes": len({s["episode_id"] for s in signals}),
        "members": len(signals),
        "would_demote": sum(1 for s in judged if s.get("action") == ACTION_DEMOTE),
        "link": sum(1 for s in judged if s.get("action") == ACTION_LINK),
        "applied": sum(1 for r in rows if r["stage"] == STAGE_CROSS_SOURCE),
        "with_related_effects": sum(1 for s in signals if s.get("related_effects")),
        "missing": dict(missing),
        "by_rule": dict(Counter(s["rule_id"] for s in judged)),
    }


def summarize(rows: list[dict[str, Any]], skipped: int = 0) -> dict[str, Any]:
    """행 목록 → 소스·티어·단계별 건수와 PAGE+TICKET 건수."""
    by_source_tier: dict[str, Counter[str]] = {}
    by_source_stage: dict[str, Counter[str]] = {}
    for row in rows:
        by_source_tier.setdefault(row["source"], Counter())[row["tier"]] += 1
        by_source_stage.setdefault(row["source"], Counter())[row["stage"]] += 1
    notify = {
        src: tiers[TIER_PAGE] + tiers[TIER_TICKET] for src, tiers in by_source_tier.items()
    }
    summary: dict[str, Any] = {
        "total": len(rows),
        "skipped": skipped,
        "by_source": dict(Counter(r["source"] for r in rows)),
        "by_tier": dict(Counter(r["tier"] for r in rows)),
        "by_stage": dict(Counter(r["stage"] for r in rows)),
        "by_source_tier": {s: dict(c) for s, c in by_source_tier.items()},
        "by_source_stage": {s: dict(c) for s, c in by_source_stage.items()},
        "page_ticket": {"total": sum(notify.values()), "by_source": notify},
    }
    if any("cross_source" in r for r in rows):  # (plans/144 W3) 모드 on일 때만 — off면 종전 모양
        summary["cross_source"] = summarize_cross_source(rows)
    return summary


def load_jsonl(path: str) -> list[dict[str, Any]]:
    """JSONL을 읽는다 — 빈 줄은 무시하고, JSON이 아니거나 객체가 아닌 줄은 오류로 멈춘다."""
    records: list[dict[str, Any]] = []
    for no, text in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not text.strip():
            continue
        rec = json.loads(text)
        if not isinstance(rec, dict):
            raise ValueError(f"{path}:{no} — JSON 객체가 아니다")
        records.append(rec)
    return records


def main(argv: list[str] | None = None) -> int:
    """진입점 — 입력 JSONL들을 현행 공급자로 리플레이해 요약 JSON을 낸다."""
    parser = argparse.ArgumentParser(description="노이즈 정책 리플레이 하네스 (plans/144 W0)")
    parser.add_argument("inputs", nargs="+", help="목업 JSONL 또는 결정 감사 JSONL")
    parser.add_argument("--out", help="요약 JSON 파일 경로(미지정이면 표준 출력)")
    parser.add_argument(
        "--cross-source-mode",
        choices=("off", "shadow", "annotate", "enforce"),
        default=MODE_OFF,
        help="크로스소스 사건 상관 모드(plans/144 W3 · 기본 off = 종전 출력)",
    )
    args = parser.parse_args(argv)
    lines = [rec for path in args.inputs for rec in load_jsonl(path)]
    tracker = build_tracker(args.cross_source_mode) if args.cross_source_mode != MODE_OFF else None
    rows, skipped = replay(lines, tracker=tracker)
    text = json.dumps(summarize(rows, skipped), ensure_ascii=False, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
