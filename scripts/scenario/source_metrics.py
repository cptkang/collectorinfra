"""4소스 교차 조회 지표 (plans/125 M-3) — 정의와 계산. LLM·서버 0 · `raw.jsonl` 행을 읽기만 한다.

**지표 정의서**

| 지표 | 정의 | 입력 |
|---|---|---|
| 필수 소스 재현율 | `requires_sources` 중 조회한 소스 비율(턴 평균) | `db_ids`·`source_status` |
| 불필요 호출률 | 조회한 소스 중 `requires_sources` 밖 소스 비율 | 같음 |
| 「없음」 판정 | 기권 기대 턴이 실제로 아무 소스도 조회하지 않은 비율 | 같음 |
| 연결 P/R | 연결 장부(계획 요약 `link`) 판정을 정답표와 대조한 정밀도·재현율 | `link`·정답표 |
| 미연결 고지율 | 장부에 미연결·모호가 있는 턴 중 응답이 그 사실을 말한 비율 | `link`·응답 본문 |
| 최종 답 점수 | 완전 1 · 수용 0.5 · 누락 0 · **오답 −1**(사람 판독 라벨) 평균 | 판독 라벨 |

「조회한 소스」 = SQL DB(`db_ids`) ∪ 비SQL 시스템(`source_status` 가 `ok`·`empty`·`partial` 인
것 — 조회 시도가 실패·창 밖·미가용이면 조회하지 않은 것으로 센다). 하네스는 제품 모듈을 import
하지 않는다.

계층: 하네스(scripts) — 판정(`func_verdict`)을 바꾸지 않는 보조 지표다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

#: 최종 답 점수표(plans/125 §6 — 오답은 −1).
ANSWER_SCORES: dict[str, float] = {"complete": 1.0, "accept": 0.5, "missing": 0.0, "wrong": -1.0}
#: 비SQL 시스템 조회로 세는 상태.
QUERIED_STATUSES = frozenset({"ok", "empty", "partial"})
#: 미연결 고지로 읽는 문구(연결 장부 한 줄 `ledger_line`).
_UNLINKED_MARKERS = ("미연결", "모호")


def _tasks(row: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    summary = row.get("plan_summary")
    tasks = summary.get("tasks") if isinstance(summary, Mapping) else None
    return [t for t in tasks or [] if isinstance(t, Mapping)]


def observed_sources(row: Mapping[str, Any]) -> set[str]:
    """이 턴에 실제로 조회한 소스 — SQL DB id ∪ 조회가 성립한 비SQL 시스템 코드."""
    found = {str(d) for d in row.get("db_ids") or [] if d}
    for task in _tasks(row):
        for code in task.get("source_status") or []:
            system, _, status = str(code).partition(":")
            if system and status in QUERIED_STATUSES:
                found.add(system)
    return found


def source_selection(rows: Iterable[Mapping[str, Any]],
                     required: Mapping[str, Iterable[str]]) -> dict[str, float | int | None]:
    """필수 소스 재현율 · 불필요 호출률(턴 평균). `required` = 시나리오 id → `requires_sources`."""
    recalls: list[float] = []
    extras: list[float] = []
    for row in rows:
        need = set(required.get(str(row.get("scenario_id")), ()) or ())
        seen = observed_sources(row)
        if need:
            recalls.append(len(need & seen) / len(need))
        if seen:
            extras.append(len(seen - need) / len(seen))
    return {
        "turns": len(recalls),
        "required_recall": sum(recalls) / len(recalls) if recalls else None,
        "unnecessary_rate": sum(extras) / len(extras) if extras else None,
    }


def abstention_accuracy(
    rows: Iterable[Mapping[str, Any]], abstain_ids: Iterable[str],
) -> float | None:
    """기권 기대 시나리오에서 어떤 소스도 조회하지 않은 턴 비율(「없음」 판정)."""
    ids = set(abstain_ids)
    picked = [row for row in rows if str(row.get("scenario_id")) in ids]
    if not picked:
        return None
    return sum(1 for row in picked if not observed_sources(row)) / len(picked)


def link_precision_recall(
    predicted: Mapping[str, str], truth: Mapping[str, str],
) -> dict[str, float]:
    """연결 판정 정밀도·재현율 — 키 → `linked`·`unlinked`·`ambiguous`·`not_queried`.

    양성 = `linked`. 정밀도 = 맞게 연결한 키 / 연결했다고 한 키 · 재현율 = 맞게 연결한 키 / 정답
    연결 키.
    """
    said = {k for k, v in predicted.items() if v == "linked"}
    real = {k for k, v in truth.items() if v == "linked"}
    hit = len(said & real)
    return {
        "precision": hit / len(said) if said else 1.0,
        "recall": hit / len(real) if real else 1.0,
    }


def unlinked_disclosure_rate(rows: Iterable[Mapping[str, Any]]) -> float | None:
    """장부에 미연결·모호가 있는 턴 중 응답이 그 사실을 말한 비율(목표 100%)."""
    flagged = []
    for row in rows:
        counts: dict[str, int] = {}
        for task in _tasks(row):
            for bucket in (task.get("link") or {}).values():
                if isinstance(bucket, Mapping):
                    for label, n in bucket.items():
                        counts[str(label)] = counts.get(str(label), 0) + int(n or 0)
        if counts.get("unlinked") or counts.get("ambiguous"):
            text = str(row.get("response_text") or "")
            flagged.append(any(m in text for m in _UNLINKED_MARKERS))
    return sum(flagged) / len(flagged) if flagged else None


def final_answer_score(labels: Iterable[str]) -> float | None:
    """사람 판독 라벨(`complete`·`accept`·`missing`·`wrong`) 평균 점수. 모르는 라벨은 거부한다."""
    values = []
    for label in labels:
        if label not in ANSWER_SCORES:
            raise ValueError(f"알 수 없는 판독 라벨: {label!r} (허용 {sorted(ANSWER_SCORES)})")
        values.append(ANSWER_SCORES[label])
    return sum(values) / len(values) if values else None


__all__ = [
    "ANSWER_SCORES", "abstention_accuracy", "final_answer_score", "link_precision_recall",
    "observed_sources", "source_selection", "unlinked_disclosure_rate",
]
