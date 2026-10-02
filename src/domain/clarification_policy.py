"""되묻기 판정 — 슬롯 기반 결정적 명확화 게이트의 「소스」 슬롯 (plans/132 N-6·N-10 · G-8 (a)).

`plans/106` H1(슬롯 게이트)이 판정 함수를 두기로 한 자리다(H1.1 — 이 모듈).
H1 착수 전이라 132 W2가 **소스 슬롯 하나만** 먼저 둔다 — H1이 착수하면 다른 슬롯을 이 모듈에 더하고
이 판정을 그대로 가져간다(G-8 (a) 이관).

소스 슬롯은 명시 소스가 없는 조회 task 하나에 대해 「어느 데이터 소스가 답하나」를 정한다. 입력은
분해 LLM이 낸 **답변 영역**의 정본 소유 시스템(레지스트리 · 등록 전체 — G-7 (a))과 그 시스템의 활성·
권한 상태뿐이다 — 질의 원문을 받지 않는다(D-004). 판정 순서(`plans/132` §6.2 ③ · §6.5):

    명시 소스(호출부가 먼저 처리) > 단독 소유 > 기억(스레드 선택 · 확인된 사례) > 소스 선택 칩

- 소유 시스템 0개 → 그대로(영역 없음 · 종전 경로 — K-3 칸 누락 폴백)
- 1개 → 비활성이면 **안내만**(조회 0 — 다른 소스로 대신 답하지 않는다 · G-1·G-7) · 활성 비DB면 그
  처리기로 고정 · 활성 DB면 그대로(종전 SQL 분류 — DB 사이 처분은 소유 교정·존 게이트 몫)
- 2개+ → 활성이 하나도 없으면 안내만
- 2개+이고 전부 DB 시스템 → 그대로(DB 사이 교차 조회는 종전 SQL 경로 · plans/102) — 비활성 소유자는
  사유 노트 재료로만 돌려준다
- 2개+이고 비DB가 섞임 → 기억이 후보를 가리키면 그것 · 권한 안 활성 후보가 둘 이상이면 **묻는다**
  (LLM 0 — 문구·선택지는 호출부가 코드로 만든다) · 하나면 묻지 않고 그것

**순수**하다 — LLM 0회 · `src.domain.*` 외 내부 import 0(`arch_check` domain 규칙).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

#: 판정 동작 — 그대로 둔다 · 처리기 고정 · 안내만 · 소스 선택 칩.
KEEP = "keep"
PIN = "pin"
NOTICE = "notice"
ASK = "ask"


@dataclass(frozen=True)
class SourceOwner:
    """답변 영역의 정본 소유 시스템 1개와 지금 상태."""

    system: str
    non_db: bool
    active: bool
    allowed: bool


@dataclass(frozen=True)
class SourceSlotDecision:
    """소스 슬롯 판정 결과.

    Attributes:
        action: KEEP | PIN | NOTICE | ASK
        system: PIN·KEEP(골라 둔 DB 시스템)의 대상 시스템 — 없으면 None
        candidates: ASK의 선택지(권한 안 활성 시스템 · 소유 순서)
        unavailable: 조회하지 못하는 소유 시스템(NOTICE 대상 · 고른 소스 밖 비활성) — 사유 노트용
        reason: 판정 근거 코드(로그·테스트용)
    """

    action: str
    system: str | None = None
    candidates: tuple[str, ...] = ()
    unavailable: tuple[str, ...] = ()
    reason: str = ""


def decide_source_slot(
    owners: Sequence[SourceOwner], *, remembered: Sequence[str] = (),
) -> SourceSlotDecision:
    """명시 소스가 없는 조회 task 하나의 소스를 정한다(모듈 docstring의 순서).

    Args:
        owners: task 답변 영역의 정본 소유 시스템(영역 순서 · 중복 없음)
        remembered: 기억된 선택(스레드 선택 → 확인된 사례 순) — 단독 소유 판정 뒤에만 쓴다

    Returns:
        판정 결과
    """
    if not owners:
        return SourceSlotDecision(KEEP, reason="no_area")
    inactive = tuple(o.system for o in owners if not o.active)
    if len(owners) == 1:
        owner = owners[0]
        if not owner.active:
            return SourceSlotDecision(NOTICE, unavailable=inactive, reason="owner_inactive")
        if owner.non_db:
            return SourceSlotDecision(PIN, system=owner.system, reason="single_owner")
        return SourceSlotDecision(KEEP, system=owner.system, reason="single_db_owner")
    active = [o for o in owners if o.active]
    if not active:
        return SourceSlotDecision(NOTICE, unavailable=inactive, reason="all_inactive")
    if not any(o.non_db for o in owners):
        return SourceSlotDecision(KEEP, unavailable=inactive, reason="db_only")
    allowed = [o for o in active if o.allowed]
    by_system = {o.system: o for o in allowed}
    for system in remembered:
        if system in by_system:
            return _chosen(by_system[system], inactive, reason="remembered")
    if len(allowed) >= 2:
        return SourceSlotDecision(
            ASK, candidates=tuple(o.system for o in allowed), unavailable=inactive,
            reason="ambiguous",
        )
    # 권한 안 활성 후보가 하나뿐이면 묻지 않는다(선택지 하나짜리 질문 금지). 권한 안 후보가 없으면
    # 첫 활성 소유자로 보낸다 — 처리기의 인가가 종전 거부 문구로 끝낸다(소스명 비노출 · D-264 ②).
    return _chosen((allowed or active)[0], inactive, reason="single_candidate")


def _chosen(owner: SourceOwner, inactive: tuple[str, ...], *, reason: str) -> SourceSlotDecision:
    action = PIN if owner.non_db else KEEP
    return SourceSlotDecision(action, system=owner.system, unavailable=inactive, reason=reason)


__all__ = [
    "ASK",
    "KEEP",
    "NOTICE",
    "PIN",
    "SourceOwner",
    "SourceSlotDecision",
    "decide_source_slot",
]
