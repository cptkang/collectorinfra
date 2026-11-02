"""조사 정확도 집계 — 운영 「실제 원인」 라벨 × 조사 저장소 (plans/198 W0 I-1 · D-350 ②).

조사 id별 최신 상태 레코드와 운영자 라벨(조사 id → 최신 라벨)을 받아 지표를 낸다. 결정적이고
LLM을 쓰지 않는다. domain 계층이므로 표준 라이브러리만 의존한다(저장소 읽기는 호출부가 한다).

- 라벨 4종: 1순위 맞음(`top1`) · k순위 맞음(`ranked` + k) · 목록에 없음(`not_listed` + 자유 서술) ·
  판단 불가(`undeterminable`). 가설 목록은 브리핑 `root_cause_hypotheses`(두 엔진 공통 계약 키)다.
- **후보 재현율**(정답이 가설 목록에 있었나)과 **순위**(RC@1·RC@3)를 분리한다(L-A6).
- 정확도 분모 = 판정 라벨(1순위·k순위·목록에 없음)이 붙은 완료 조사. 라벨 없는 조사와
  「판단 불가」는 분모에서 뺀다(「판정 불가 ≠ 오답」 — D-238 L5). 분모가 0이면 비율은
  None이다(0%가 아니다).
- 라벨률(입력률) = 라벨(판단 불가 포함)이 붙은 완료 조사 / 완료 조사.
- 미완주율·「조사 불가」율의 분모는 종결 조사 전체(진행 중 제외)다.
- 엔진: 플레이북(`playbook` 블록) · HolmesGPT(플레이북 없는 6요소 브리핑) · 병렬(플레이북 + 심층
  조사 `deep_dive.status == "done"` — 두 엔진 결론이 모두 있음) · 없음(스텁·브리핑 없음).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any

VERDICT_TOP1 = "top1"
VERDICT_RANKED = "ranked"
VERDICT_NOT_LISTED = "not_listed"
VERDICT_UNDETERMINABLE = "undeterminable"
#: 라벨 판정 전체(입력 화면 순서).
CAUSE_VERDICTS: tuple[str, ...] = (
    VERDICT_TOP1, VERDICT_RANKED, VERDICT_NOT_LISTED, VERDICT_UNDETERMINABLE,
)
#: 정확도 분모에 드는 판정 — 「판단 불가」는 빠진다(D-238 L5).
JUDGED_VERDICTS: frozenset[str] = frozenset({VERDICT_TOP1, VERDICT_RANKED, VERDICT_NOT_LISTED})

ENGINE_PLAYBOOK = "playbook"
ENGINE_HOLMESGPT = "holmesgpt"
ENGINE_PARALLEL = "parallel"
ENGINE_NONE = "none"
ENGINES: tuple[str, ...] = (ENGINE_PLAYBOOK, ENGINE_HOLMESGPT, ENGINE_PARALLEL, ENGINE_NONE)

OUTCOME_DONE = "done"
OUTCOME_INCOMPLETE = "incomplete"
OUTCOME_UNAVAILABLE = "unavailable"
OUTCOME_ACTIVE = "active"

#: RC@k의 k(주간 표의 두 번째 순위 지표).
TOP_K = 3

_ACTIVE_STATUSES: frozenset[str] = frozenset({"submitted", "running"})
_REJECTED_STATUS = "rejected"
#: sre_agent 「조사 불가」 브리핑 요약 표지(D-342 — `[미결] 조사 불가 …`).
_UNAVAILABLE_MARK = "조사 불가"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def engine_of(rec: Mapping[str, Any]) -> str:
    """조사 레코드의 엔진 — 브리핑 모양으로 판별한다(모듈 독스트링의 4종)."""
    briefing = _mapping(rec.get("briefing"))
    if not briefing or briefing.get("stub"):
        return ENGINE_NONE
    playbook = _mapping(briefing.get("playbook"))
    if playbook.get("id"):
        if _mapping(briefing.get("deep_dive")).get("status") == "done":
            return ENGINE_PARALLEL
        return ENGINE_PLAYBOOK
    if "root_cause_hypotheses" in briefing or briefing.get("cause"):
        return ENGINE_HOLMESGPT
    return ENGINE_NONE


def outcome_of(rec: Mapping[str, Any]) -> str:
    """조사 결말 — 완료 · 미완주(실패·타임아웃 등) · 조사 불가(거부·스텁 등) · 진행 중."""
    status = str(rec.get("status") or "")
    if status in _ACTIVE_STATUSES:
        return OUTCOME_ACTIVE
    if status == _REJECTED_STATUS:
        return OUTCOME_UNAVAILABLE
    if status != "done":
        return OUTCOME_INCOMPLETE
    briefing = _mapping(rec.get("briefing"))
    if briefing.get("stub") or _UNAVAILABLE_MARK in str(briefing.get("summary") or ""):
        return OUTCOME_UNAVAILABLE
    return OUTCOME_DONE


def hypotheses_of(rec: Mapping[str, Any]) -> list[dict[str, Any]]:
    """가설 목록 `[{rank, status, confidence}]`(순위 오름차순) — 순위가 없으면 목록 위치(1부터).

    서술(`cause`·`reasoning`)은 싣지 않는다 — 라벨 검증·내보내기는 순위·상태 메타만 쓴다(D-308).
    """
    out: list[dict[str, Any]] = []
    raw = _mapping(rec.get("briefing")).get("root_cause_hypotheses")
    for pos, item in enumerate(raw if isinstance(raw, list) else [], start=1):
        hyp = _mapping(item)
        if not hyp:
            continue
        rank = _int(hyp.get("rank"))
        out.append({
            "rank": rank if rank is not None and rank >= 1 else pos,
            "status": hyp.get("status") if isinstance(hyp.get("status"), str) else None,
            "confidence": (
                hyp.get("confidence") if isinstance(hyp.get("confidence"), str) else None
            ),
        })
    out.sort(key=lambda h: h["rank"])
    return out


def hypothesis_ranks(rec: Mapping[str, Any]) -> list[int]:
    """가설 순위 목록(중복 제거 · 오름차순)."""
    return sorted({h["rank"] for h in hypotheses_of(rec)})


def validate_label(
    verdict: str, rank: int | None, ranks: Sequence[int]
) -> tuple[int | None, str | None]:
    """라벨 검증 — (저장할 순위 또는 None, 거부 사유 또는 None).

    1순위 맞음은 순위 1이 목록에 있어야 하고, k순위 맞음은 2 이상이면서 목록에 있는 순위여야 한다.
    목록에 없음·판단 불가는 순위를 저장하지 않는다.
    """
    if verdict not in CAUSE_VERDICTS:
        return None, f"verdict는 {'·'.join(CAUSE_VERDICTS)} 중 하나입니다."
    if verdict == VERDICT_TOP1:
        if 1 not in ranks:
            return None, "이 조사에는 1순위 가설이 없습니다."
        return 1, None
    if verdict == VERDICT_RANKED:
        if rank is None or rank < 2 or rank not in ranks:
            return None, "k순위는 이 조사 가설 목록의 2순위 이상 순위여야 합니다."
        return rank, None
    return None, None


def label_rank(label: Mapping[str, Any]) -> int | None:
    """라벨이 가리킨 정답 순위 — 목록에 없음·판단 불가·형식 오류면 None."""
    verdict = label.get("verdict")
    if verdict == VERDICT_TOP1:
        return 1
    if verdict == VERDICT_RANKED:
        rank = _int(label.get("rank"))
        return rank if rank is not None and rank >= 2 else None
    return None


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def accuracy_metrics(
    records: Iterable[Mapping[str, Any]], labels: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    """레코드 묶음 하나의 지표(건수 + 비율). 진행 중 조사는 세지 않는다."""
    n = done = incomplete = unavailable = 0
    labeled = undeterminable = judged = in_list = top1 = topk = 0
    for rec in records:
        outcome = outcome_of(rec)
        if outcome == OUTCOME_ACTIVE:
            continue
        n += 1
        if outcome == OUTCOME_INCOMPLETE:
            incomplete += 1
            continue
        if outcome == OUTCOME_UNAVAILABLE:
            unavailable += 1
            continue
        done += 1
        label = labels.get(str(rec.get("investigation_id") or ""))
        if label is None or label.get("verdict") not in CAUSE_VERDICTS:
            continue
        labeled += 1
        if label.get("verdict") not in JUDGED_VERDICTS:
            undeterminable += 1
            continue
        judged += 1
        rank = label_rank(label)
        if rank is None:
            continue
        in_list += 1
        top1 += rank == 1
        topk += rank <= TOP_K
    return {
        "investigations": n,
        "done": done,
        "incomplete": incomplete,
        "unavailable": unavailable,
        "labeled": labeled,
        "undeterminable": undeterminable,
        "judged": judged,
        "in_list": in_list,
        "top1": top1,
        "top3": topk,
        "label_rate": _ratio(labeled, done),
        "candidate_recall": _ratio(in_list, judged),
        "rc_at_1": _ratio(top1, judged),
        "rc_at_3": _ratio(topk, judged),
        "incomplete_rate": _ratio(incomplete, n),
        "unavailable_rate": _ratio(unavailable, n),
    }


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _week_start(when: datetime, tz: tzinfo) -> datetime:
    local = when.astimezone(tz)
    return (local - timedelta(days=local.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def _week_label(start: datetime) -> str:
    year, week, _ = start.isocalendar()
    return f"{year}-W{week:02d}"


def weekly_report(
    records: Iterable[Mapping[str, Any]],
    labels: Mapping[str, Mapping[str, Any]],
    *,
    now: datetime,
    weeks: int = 8,
    tz: tzinfo = UTC,
) -> dict[str, Any]:
    """주간 표 — 최근 `weeks`주(이번 주 포함 · 월요일 시작 · `tz` 기준)를 최신 주부터.

    조사는 최신 행 시각(`ts`)의 주에 든다. 창 전체 합계와 엔진별·알람 kind별 지표를 함께 낸다.
    시각이 없거나 창 밖인 레코드는 세지 않는다.
    """
    weeks = max(1, int(weeks))
    current = _week_start(now, tz)
    starts = [current - timedelta(weeks=i) for i in range(weeks)]
    oldest = starts[-1]
    buckets: dict[datetime, list[Mapping[str, Any]]] = {s: [] for s in starts}
    window: list[Mapping[str, Any]] = []
    for rec in records:
        ts = _parse_ts(rec.get("ts"))
        if ts is None:
            continue
        start = _week_start(ts, tz)
        if start < oldest or start > current:
            continue
        buckets[start].append(rec)
        window.append(rec)
    rows = [
        {
            "week": _week_label(s),
            "start": s.date().isoformat(),
            "end": (s + timedelta(days=6)).date().isoformat(),
            **accuracy_metrics(buckets[s], labels),
        }
        for s in starts
    ]
    by_engine = {
        engine: accuracy_metrics([r for r in window if engine_of(r) == engine], labels)
        for engine in ENGINES
    }
    kinds = sorted({str(r.get("kind") or "alarm") for r in window})
    by_kind = {
        kind: accuracy_metrics([r for r in window if str(r.get("kind") or "alarm") == kind], labels)
        for kind in kinds
    }
    return {
        "window": {
            "weeks": weeks,
            "start": oldest.date().isoformat(),
            "end": (current + timedelta(days=6)).date().isoformat(),
        },
        "weeks": rows,
        "total": accuracy_metrics(window, labels),
        "by_engine": by_engine,
        "by_kind": by_kind,
    }


def export_candidates(
    records: Iterable[Mapping[str, Any]], labels: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """154 세계 후보 목록 — 판정 라벨이 붙은 완료 조사의 메타만(D-308: 값·서술 반출 없음).

    항목: 조사 id · kind · 엔진 · 플레이북 id · 라벨(판정·순위 · 자유 서술 유무) · 가설 메타
    (순위·상태·신뢰도). 알람 원문·브리핑 서술·자유 서술 본문은 싣지 않는다. 「판단 불가」는 정답이
    없어 빠진다.
    """
    out: list[dict[str, Any]] = []
    for rec in records:
        if outcome_of(rec) != OUTCOME_DONE:
            continue
        label = labels.get(str(rec.get("investigation_id") or ""))
        if label is None or label.get("verdict") not in JUDGED_VERDICTS:
            continue
        playbook_id = _mapping(_mapping(rec.get("briefing")).get("playbook")).get("id")
        out.append({
            "investigation_id": rec.get("investigation_id"),
            "kind": rec.get("kind") or "alarm",
            "engine": engine_of(rec),
            "playbook_id": playbook_id if isinstance(playbook_id, str) else None,
            "label": {
                "verdict": label.get("verdict"),
                "rank": label_rank(label),
                "has_note": bool(label.get("note")),
            },
            "hypotheses": hypotheses_of(rec),
        })
    return out


__all__ = [
    "CAUSE_VERDICTS",
    "ENGINES",
    "ENGINE_HOLMESGPT",
    "ENGINE_NONE",
    "ENGINE_PARALLEL",
    "ENGINE_PLAYBOOK",
    "JUDGED_VERDICTS",
    "OUTCOME_ACTIVE",
    "OUTCOME_DONE",
    "OUTCOME_INCOMPLETE",
    "OUTCOME_UNAVAILABLE",
    "TOP_K",
    "VERDICT_NOT_LISTED",
    "VERDICT_RANKED",
    "VERDICT_TOP1",
    "VERDICT_UNDETERMINABLE",
    "accuracy_metrics",
    "engine_of",
    "export_candidates",
    "hypotheses_of",
    "hypothesis_ranks",
    "label_rank",
    "outcome_of",
    "validate_label",
    "weekly_report",
]
