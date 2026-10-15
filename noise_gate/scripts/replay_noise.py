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
(plans/153 W4) 같은 모드에서 사건 종합 조사 발주 조건(`episode_investigation_due` — 두 소스 멤버 ·
링크 ≥1 · 통보 멤버 ≥1 · 사건당 1회)이 처음 성립한 알람 행에 `episode_investigation: true`를 달고
요약 `cross_source.episode_investigations`에 발주 수를 센다(조사 서비스는 부르지 않는다).

`--page-review-mode`(plans/162 §8 · 기본 off)를 주면 워커와 **같은 도메인 레지스트리·재판정 함수**
(`PageRegistry` · `review_page`)로 입력 순서대로 PAGE 필요성을 다시 본다(발생 시각을 시계로 쓴다 ·
해소 결정도 반영해 사건을 닫는다 · 레지스트리 설정은 정책 설정의 `page_review_*`, 없으면 도메인
기본). 행에 근거 `page_review` 블록을, 요약에 `page_review`(재판정 수 · 강등/했을 강등 수 · 규칙별 ·
보존 사유별 · 재판정 후 PAGE 수)를 더한다. enforce면 행 티어·단계는 강등 결과다. off면 출력이
종전과 같다.
무소식 닫힘(`page_review_idle_close_minutes` > 0 · 해소가 오지 않는 APM 출처)도 워커처럼
적용한다 — 단 판정 감사 입력은 dedup을 이미 거친 레코드라 버려진 재발의 `touch`가 없어 워커보다
일찍 닫힐 수 있다.

사용:
    python -m noise_gate.scripts.replay_noise noise_gate/testdata/cross_source/mock_events.jsonl
    python -m noise_gate.scripts.replay_noise logs/alarm_decisions.jsonl --out /tmp/replay.json
    python -m noise_gate.scripts.replay_noise noise_gate/testdata/cross_source/mock_events.jsonl \
        --cross-source-mode shadow
    python -m noise_gate.scripts.replay_noise logs/alarm_decisions.jsonl --page-review-mode shadow
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
    episode_investigation_due,
)
from noise_gate.domain.notification_policy import (
    STAGE_COLLECTION_FAILED,
    STAGE_CROSS_SOURCE,
    STAGE_PAGE_REVIEW,
    TIER_PAGE,
    TIER_TICKET,
    NotificationDecision,
    compute_fingerprint,
    decide_notification,
    stage_from_reason,
)
from noise_gate.domain.page_review import EVIDENCE_KEY as PAGE_REVIEW_KEY
from noise_gate.domain.page_review import MODE_OFF as PAGE_REVIEW_OFF
from noise_gate.domain.page_review import (
    PageRegistry,
    fanout_key_of,
    host_key_of,
    review_page,
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

# (plans/162 §8) 재판정 함수가 읽는 설정 필드 — 정책 설정에 있으면 그 값, 없으면 도메인 기본.
_PAGE_REVIEW_FIELDS = (
    "page_review_reminder_hours",
    "page_review_reopen_quiet_minutes",
    "page_review_host_window_minutes",
    "page_review_fanout_window_minutes",
    "page_review_fanout_page_max",
    "page_review_always_page_globs",
    "page_review_idle_close_minutes",
)
# 레지스트리 생성 인자 ← 설정 필드(워커 `_build_page_registry`와 같은 대응).
_PAGE_REGISTRY_ARGS = {
    "host_window_minutes": "page_review_host_window_minutes",
    "fanout_window_minutes": "page_review_fanout_window_minutes",
    "reminder_hours": "page_review_reminder_hours",
    "reopen_quiet_minutes": "page_review_reopen_quiet_minutes",
    "idle_close_minutes": "page_review_idle_close_minutes",
}


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


def _fingerprint(event: AlarmEvent) -> str:
    """워커 지문 — APM이면 정규화 유형을 알람 키로 쓴다(해소 짝맞춤 · plans/144 §4.3)."""
    return compute_fingerprint(
        event, alarm_key=event_type_norm(event) if is_apm_source(event) else None
    )


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
    alarm = episode_alarm(event, fingerprint=_fingerprint(event), is_apm=apm)
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


def page_review_config(config: Any, mode: str) -> SimpleNamespace:
    """재판정 설정 — 정책 설정의 `page_review_*`(없는 필드는 도메인 기본) + CLI 모드."""
    values = {f: getattr(config, f) for f in _PAGE_REVIEW_FIELDS if hasattr(config, f)}
    return SimpleNamespace(**values, page_review_mode=mode)


def build_page_registry(config: Any) -> PageRegistry:
    """리플레이용 PAGE 레지스트리 — 설정에 있는 `page_review_*`만 넘기고 나머지는 도메인 기본."""
    kwargs: dict[str, Any] = {
        arg: int(getattr(config, f)) for arg, f in _PAGE_REGISTRY_ARGS.items() if hasattr(config, f)
    }
    return PageRegistry(**kwargs)


def _review_page(
    registry: PageRegistry, review_cfg: Any, event: AlarmEvent, decision: NotificationDecision
) -> NotificationDecision:
    """워커와 같은 순서로 재판정하고 레지스트리에 반영한다(시계 = 발생 시각).

    비해소면 `signals_for` → `review_page`(PAGE가 아니면 같은 객체), 그 뒤 해소를 포함한 모든
    판정을 `observe`한다(해소는 사건을 닫는다 — 도메인 `is_resolution` · APM은 해소가 오지 않는
    출처로 표시 — 워커 `_observe_page`와 같다).
    """
    fingerprint = _fingerprint(event)
    host_key = host_key_of(event.db_id, event.server_name or event.hostname)
    fanout_key = fanout_key_of(event.db_id, event.alarm_name)
    now = event.alarm_time.timestamp()
    if not event.is_clear:
        signals = dict(registry.signals_for(fingerprint, host_key, fanout_key, now))
        decision = review_page(decision, signals, review_cfg)
    registry.observe(
        decision,
        fingerprint=str(decision.fingerprint or fingerprint),
        host_key=host_key,
        fanout_key=fanout_key,
        alarm_id=event.alarm_id,
        now=now,
        silent_source=is_apm_source(event),
    )
    return decision


def replay(
    lines: Iterable[dict[str, Any]],
    *,
    provider: NoiseCtxProvider = current_noise_ctx,
    config: Any = DEFAULT_POLICY_CONFIG,
    tracker: EpisodeTracker | None = None,
    page_review_mode: str = PAGE_REVIEW_OFF,
) -> tuple[list[dict[str, Any]], int]:
    """레코드를 정책에 다시 통과시킨다 → (이벤트별 행, 건너뛴 줄 수).

    tracker(plans/144 W3)가 있으면 사건 신호를 정책에 넘기고 행에 `cross_source`를 더한다.
    page_review_mode(plans/162 §8)가 off가 아니면 PAGE 재판정을 거치고 행에 `page_review`
    (근거 블록 · PAGE가 아니었으면 None)를 더한다 — enforce면 행 티어·단계가 강등 결과다.
    """
    rows: list[dict[str, Any]] = []
    skipped = 0
    registry: PageRegistry | None = None
    review_cfg: SimpleNamespace | None = None
    if page_review_mode != PAGE_REVIEW_OFF:
        review_cfg = page_review_config(config, page_review_mode)
        registry = build_page_registry(review_cfg)
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
        if registry is not None:
            decision = _review_page(registry, review_cfg, event, decision)
        row = {
            "alarm_id": event.alarm_id,
            "scenario": line.get("scenario", ""),
            "source": SOURCE_JENNIFER if is_apm_source(event) else SOURCE_POLESTAR,
            "severity": event.severity,
            "tier": decision.tier,
            "stage": decision.stage,
        }
        if registry is not None:
            row["page_review"] = (decision.evidence or {}).get(PAGE_REVIEW_KEY)
        if tracker is not None:
            row["cross_source"] = cross_source
            if cross_source is not None:
                tracker.record_tier(cross_source["episode_id"], event.alarm_id, decision.tier)
                # (plans/153 W4) 종합 조사 발주 조건이 처음 성립한 알람 — 워커와 같은 조건 · 1회.
                ep = tracker.get(cross_source["episode_id"])
                if ep is not None and episode_investigation_due(ep):
                    ep.episode_investigation_status = "replayed"
                    row["episode_investigation"] = True
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
        "episode_investigations": sum(1 for r in rows if r.get("episode_investigation")),
        "missing": dict(missing),
        "by_rule": dict(Counter(s["rule_id"] for s in judged)),
    }


def summarize_page_review(rows: list[dict[str, Any]], mode: str) -> dict[str, Any]:
    """PAGE 재판정 지표 — 재판정 수 · 강등/했을 강등 수 · 규칙별 · 보존 사유별 · 재판정 후 PAGE 수.

    `would_demote`는 shadow·enforce 공통(「했을 강등」 — 도메인이 두 모드에서 같게 센다),
    `demoted`는 실제 강등(`stage=page_review` 행 · shadow면 0)이다. `pages_after`는 「했을」
    결과 기준 PAGE 수다(shadow면 했을 강등을 뺀다).
    """
    blocks = [r["page_review"] for r in rows if r.get("page_review")]
    demoting = [b for b in blocks if b.get("would_demote")]
    return {
        "mode": mode,
        "reviewed": len(blocks),
        "would_demote": len(demoting),
        "demoted": sum(1 for r in rows if r["stage"] == STAGE_PAGE_REVIEW),
        "by_rule": dict(Counter(str(b["rule"]) for b in demoting)),
        "by_keep": dict(Counter(str(b["keep"]) for b in blocks if b.get("keep"))),
        "pages_after": sum(
            1 for r in rows
            if r["tier"] == TIER_PAGE and not (r.get("page_review") or {}).get("would_demote")
        ),
    }


def summarize(
    rows: list[dict[str, Any]], skipped: int = 0, *, page_review_mode: str = PAGE_REVIEW_OFF
) -> dict[str, Any]:
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
    if page_review_mode != PAGE_REVIEW_OFF:  # (plans/162 §8) 모드 on일 때만 — off면 종전 모양
        summary["page_review"] = summarize_page_review(rows, page_review_mode)
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
    parser.add_argument(
        "--page-review-mode",
        choices=("off", "shadow", "enforce"),
        default=PAGE_REVIEW_OFF,
        help="PAGE 필요성 재판정 모드(plans/162 §8 · 기본 off = 종전 출력)",
    )
    args = parser.parse_args(argv)
    lines = [rec for path in args.inputs for rec in load_jsonl(path)]
    tracker = build_tracker(args.cross_source_mode) if args.cross_source_mode != MODE_OFF else None
    rows, skipped = replay(lines, tracker=tracker, page_review_mode=args.page_review_mode)
    summary = summarize(rows, skipped, page_review_mode=args.page_review_mode)
    text = json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
