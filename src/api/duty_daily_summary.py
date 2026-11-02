"""첫 화면 「지난 24시간 · 내 담당」 요약 집계 (plans/201 W6 · D-354).

원천은 노이즈 결정 기록(`DecisionStore` JSONL)의 판정 레코드뿐이다 — 새 수집도, 새 저장도 없다.
이 모듈은 레코드를 **한 건씩** 받아 세는 순수 로직만 둔다(파일·요청·인가를 모른다).

- 존 범위·SUPPRESS 공개 판정은 호출처가 넘기는 `visible`(알람 SSE와 같은 `event_visible_to`)이 한다
  — 여기서 인가 규칙을 다시 쓰지 않는다. 담당 영역은 인가가 아니라 **표시 범위**다.
- 담당 겹침 = 발생 영역(`resource_type`) 또는 원인 단서(제니퍼 정규화 유형
  `stage_evidence.apm_policy.event_type_norm`)가 사용자 담당과 겹침 — 판정은 domain `duty_overlap`이고 화면
  (`app.js` `dutyBadgesOf`)과 같은 규칙이다. 프로세스 kind 단서는 결정 기록에 없으므로 쓰지 않는다.
  `resource_type`이 없는 레코드(수동 조사 플래그 off 환경)는 발생 영역이 없다.
- 「확인 필요」는 PAGE·TICKET 중 최신 3건(크기 고정 힙) · 「반복」은 지문별 2건 이상 중 최다 1종이다.
- 내보내는 식별 필드는 알람 SSE가 같은 사용자에게 이미 싣는 것(`server_name`·`alarm_name`·`resource_type`)만이다
  — hostname·IP·condition_log·analysis는 싣지 않는다. `server_name`은 게이트가 `server_name or hostname`으로
  채워 hostname일 수 있다 — 같은 사용자의 알람 SSE와 같은 범위(page·ticket만)다.
- 원인 단서 비대칭: 여기는 `apm_policy.event_type_norm`을 쓰는데, 이 값은 게이트가 `raw.apm.event_type_norm`이
  없으면 알람 이름을 정규화해 채운다. 알람 SSE의 제니퍼 유형 키(W3 notifier)는 `raw.apm.event_type_norm`만 쓴다. 결정
  기록에는 값의 출처 표지가 없으므로 맞추지 않는다 — 제니퍼 외 알람 이름이 유형 사전과 같으면 요약에서만
  단서로 겹칠 수 있다(쓰기 경로는 바꾸지 않는다).
"""

from __future__ import annotations

import heapq
from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Any

from src.domain.duty_areas import DutyAreaVocabulary, DutyOverlap, duty_overlap

#: 집계 창(시간).
WINDOW_HOURS = 24
#: 「확인 필요」 상한.
NEEDS_ATTENTION_TOP_N = 3
#: 「반복」으로 보는 최소 건수.
REPEAT_MIN_COUNT = 2
#: 건수를 세는 티어(표시 순서). SUPPRESS는 관리자에게만 키를 둔다.
COUNTED_TIERS: tuple[str, ...] = ("page", "ticket", "dashboard")
SUPPRESS_TIER = "suppress"
#: 「확인 필요」 대상 티어.
ATTENTION_TIERS: frozenset[str] = frozenset({"page", "ticket"})


def _apm_type_norm(rec: dict[str, Any]) -> object:
    """판정 레코드의 제니퍼 정규화 유형(`stage_evidence.apm_policy.event_type_norm`) — 없으면 None."""
    evidence = rec.get("stage_evidence")
    policy = evidence.get("apm_policy") if isinstance(evidence, dict) else None
    return policy.get("event_type_norm") if isinstance(policy, dict) else None


def record_overlap(
    rec: dict[str, Any], vocab: DutyAreaVocabulary, user_areas: Iterable[str]
) -> DutyOverlap:
    """판정 레코드 하나와 사용자 담당의 겹침(순수 — 판정은 domain `duty_overlap`)."""
    return duty_overlap(
        vocab, user_areas, resource_type=rec.get("resource_type"), event_type=_apm_type_norm(rec)
    )


def _epoch(rec: dict[str, Any]) -> float:
    """기록 시각(epoch초) — 파싱 실패·없음은 0(가장 오래된 것으로 본다)."""
    raw = rec.get("ts")
    if not raw:
        return 0.0
    try:
        return datetime.fromisoformat(str(raw)).timestamp()
    except (ValueError, TypeError, OverflowError, OSError):
        return 0.0


def _text(value: object) -> str:
    return str(value or "").strip()


class SummaryStopped(Exception):
    """집계 중단 요청(시간 초과 뒤 스레드 정리)."""


def summarize_duty_records(
    records: Iterable[dict[str, Any]],
    *,
    vocab: DutyAreaVocabulary,
    user_areas: Iterable[str],
    visible: Callable[[dict[str, Any]], bool],
    include_suppress: bool,
    should_stop: Callable[[], bool] | None = None,
    newest_first: bool = False,
) -> dict[str, Any]:
    """판정 레코드를 한 건씩 세어 `{tiers, needs_attention, top_repeat}`를 만든다(순수 · 레코드를 쌓지 않음).

    - `visible`: 알람 SSE 공개 판정(존 범위 · 비관리자 SUPPRESS 제외) — 통과한 레코드만 센다.
    - `include_suppress`: True(관리자)면 `tiers`에 `suppress` 키를 둔다. False면 키 자체가 없고
      SUPPRESS 레코드는 어디에도 세지 않는다(`visible`이 이미 거르지만 여기서도 거른다).
    - `should_stop`: 참이 되면 `SummaryStopped`로 멈춘다(호출처 시간 초과 뒤 스레드 정리용). 순회가 같은
      신호로 조용히 끝난 경우도 끝에서 한 번 더 확인해 부분 집계를 성공으로 내지 않는다.
    - `newest_first`: 입력이 최신부터면 True — 정렬 키는 기록 시각(`ts`)이고 입력 순서는 같은 시각끼리의
      순위에만 쓰이므로(늦게 쓰인 쪽이 최신) 순서 방향만 알려 주면 결과가 입력 방향과 무관하다.
    """
    areas = list(user_areas)
    tiers: dict[str, int] = {tier: 0 for tier in COUNTED_TIERS}
    if include_suppress:
        tiers[SUPPRESS_TIER] = 0
    heap: list[tuple[float, int, dict[str, Any]]] = []
    repeats: dict[str, list[Any]] = {}  # fingerprint → [건수, (마지막 epoch, 순위), 마지막 알람 이름]
    seq = 0
    for rec in records:
        if should_stop is not None and should_stop():
            raise SummaryStopped
        tier = _text(rec.get("tier"))
        if tier == SUPPRESS_TIER and not include_suppress:
            continue
        if not visible(rec):
            continue
        overlap = record_overlap(rec, vocab, areas)
        if not overlap.mine:
            continue
        if tier in tiers:
            tiers[tier] += 1
        epoch = _epoch(rec)
        seq += 1
        order = -seq if newest_first else seq
        if tier in ATTENTION_TIERS:
            item = {
                "ts": _text(rec.get("ts")),
                "tier": tier,
                "server_name": _text(rec.get("server_name")),
                "alarm_name": _text(rec.get("alarm_name")),
                "resource_type": _text(rec.get("resource_type")),
                "occurrence": overlap.occurrence,
                "hint": overlap.hint,
                "hint_label": overlap.hint_label,
            }
            entry = (epoch, order, item)
            if len(heap) < NEEDS_ATTENTION_TOP_N:
                heapq.heappush(heap, entry)
            elif entry[:2] > heap[0][:2]:
                heapq.heapreplace(heap, entry)
        fingerprint = _text(rec.get("fingerprint"))
        if fingerprint:
            slot = repeats.get(fingerprint)
            if slot is None:
                repeats[fingerprint] = [1, (epoch, order), _text(rec.get("alarm_name"))]
            else:
                slot[0] += 1
                if (epoch, order) >= slot[1]:
                    slot[1] = (epoch, order)
                    slot[2] = _text(rec.get("alarm_name")) or slot[2]
    if should_stop is not None and should_stop():
        raise SummaryStopped
    needs_attention = [item for _, _, item in sorted(heap, key=lambda e: e[:2], reverse=True)]
    top_repeat: dict[str, Any] | None = None
    best: list[Any] | None = None
    for slot in repeats.values():
        if slot[0] < REPEAT_MIN_COUNT:
            continue
        if best is None or (slot[0], slot[1]) > (best[0], best[1]):
            best = slot
    if best is not None:
        top_repeat = {"alarm_name": best[2], "count": best[0]}
    return {"tiers": tiers, "needs_attention": needs_attention, "top_repeat": top_repeat}
