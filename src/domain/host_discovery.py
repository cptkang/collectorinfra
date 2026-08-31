"""존 순회 대상 소재 확정 — 결과 판정 (Plan 82 Wave 5 · D-176 후속3).

**무엇을 하나.** 존을 순회해 모은 결과(`SweepOutcome`)를 보고 *찾았다 / 여럿이다 / 없다* 를
판정하고, 각 경우에 사용자가 봐야 할 사실을 구조화해 낸다. **순회 자체는 하지 않는다** —
실행은 `src/orchestration/host_sweep.py` 소관이고 이 모듈은 입력만으로 결정되는 순수 판정이다.

**왜 이 판정이 필요한가.** 현행은 `_resolve_db_id`가 실패하면 *"위치(예: 김포/여의도)를 지정해
주세요"* 라는 **막다른 안내**로 끝난다(`process_query.py:718-721`). 그런데 운영 `.env`는 세 존
전부 프로세스 API가 매핑돼 있어 **순회 탐색이 기술적으로 가능하다** — 즉 사용자에게 물을 이유가
없는 것을 묻고 있었다.

**세 결과를 구분하는 것이 이 모듈의 존재 이유다.**

| 판정 | 뜻 | 사용자가 받는 것 |
|---|---|---|
| `resolved` | 한 존에서만 찾았다 | 되묻지 않고 그 존으로 진행 |
| `ambiguous` | 두 존 이상에서 찾았다 | **발견된 존으로 좁힌** 선택지로 되묻기(U5) |
| `not_found` | 인가된 존을 다 돌았는데 없다 | **순회한 존 목록** + 조회 실패 존 구분(U6) |

★ **"없다"와 "확인하지 못했다"를 절대 합치지 않는다.** 조회가 실패한 존을 "없음"에 섞으면
사용자는 *다 찾아봤는데 없다* 로 읽는다 — 그것이 침묵 강등이다.

계층: domain (`scripts/arch_check.py` `src.domain` 매핑) — 순수 · I/O·LLM 0.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from src.domain.host_availability import HostAvailability

#: 판정 결과 코드.
RESOLVED = "resolved"
AMBIGUOUS = "ambiguous"
NOT_FOUND = "not_found"


@dataclass(frozen=True)
class ZoneHit:
    """한 존에서 찾은 대상 1건."""

    db_id: str
    zone_label: str
    hostname: str = ""
    server_name: str = ""
    availability: Optional[HostAvailability] = None

    def display_name(self) -> str:
        """사용자에게 보일 이름 — hostname 우선, 없으면 등록명."""
        return self.hostname or self.server_name or ""


@dataclass(frozen=True)
class SweepOutcome:
    """순회 결과 전체.

    `swept`는 **실제로 순회한 존 라벨**이다(인가 필터를 통과한 것만). 0건일 때 이 목록이
    곧 답이므로 순회하지 않은 존을 여기 넣으면 사용자에게 거짓을 말하게 된다.

    `errors`는 존별 조회 실패 사유다 — `hits`가 비었다고 해서 "없다"가 아니다.
    """

    identifier: str
    swept: tuple[str, ...] = ()
    hits: tuple[ZoneHit, ...] = ()
    errors: dict[str, str] = field(default_factory=dict)

    def failed_labels(self) -> tuple[str, ...]:
        """조회에 실패한 존의 라벨(사유 표기용)."""
        return tuple(self.errors)


@dataclass(frozen=True)
class DiscoveryVerdict:
    """판정 결과. `state`는 RESOLVED · AMBIGUOUS · NOT_FOUND 중 하나."""

    state: str
    outcome: SweepOutcome
    #: RESOLVED일 때만 채워진다.
    hit: Optional[ZoneHit] = None
    #: AMBIGUOUS일 때 되물을 후보(발견된 존만 — 전체 존이 아니다).
    candidates: tuple[ZoneHit, ...] = ()

    @property
    def db_id(self) -> Optional[str]:
        return self.hit.db_id if self.hit else None


def classify(outcome: SweepOutcome) -> DiscoveryVerdict:
    """순회 결과를 판정한다. 입력만으로 결정되며 부작용이 없다.

    Args:
        outcome: `host_sweep.sweep_zones` 산출물

    Returns:
        판정. 히트 0건이면 조회 실패 존이 있든 없든 `NOT_FOUND`이며, 그 구분은
        `outcome.errors`가 보존한다(응답 문구가 나눠 쓴다).
    """
    hits = tuple(outcome.hits)
    if len(hits) == 1:
        return DiscoveryVerdict(state=RESOLVED, outcome=outcome, hit=hits[0])
    if len(hits) > 1:
        return DiscoveryVerdict(state=AMBIGUOUS, outcome=outcome, candidates=hits)
    return DiscoveryVerdict(state=NOT_FOUND, outcome=outcome)


def render_not_found(outcome: SweepOutcome) -> str:
    """0건 안내 문구 — **순회한 존을 전부 밝힌다**(U6).

    현행 *"위치를 지정해 주세요"* 보다 정보량이 큰 이유는 두 가지다: ①사용자가 서버명
    오타·권한 밖 존 가능성을 즉시 판단할 수 있고 ②존을 골라도 결과가 같다는 사실이
    드러나 헛된 왕복이 생기지 않는다.
    """
    swept = [z for z in outcome.swept if z not in outcome.errors.values()]
    checked = ", ".join(outcome.swept) or "확인 가능한 존이 없습니다"
    text = (
        f"인가된 {len(outcome.swept)}개 존({checked})에서 "
        f"'{outcome.identifier}'을(를) 찾지 못했습니다."
    )
    if outcome.errors:
        failed = ", ".join(f"{k}({v})" for k, v in outcome.errors.items())
        text += (
            f"\n\n⚠ 다음 존은 **조회 자체가 실패**해 존재 여부를 확인하지 못했습니다 — "
            f"'없음'이 아닙니다: {failed}"
        )
    if not outcome.errors and swept:
        text += "\n\n서버명 철자와 조회 권한 범위를 확인해 주세요."
    return text


def render_ambiguous(verdict: DiscoveryVerdict) -> str:
    """다중 히트 안내 — 어느 존에서 각각 찾았는지 밝힌다.

    임의로 하나를 고르지 않는 이유: 프로세스 조회는 대상이 하나여야 의미가 있고,
    잘못 고르면 **오답이 정답처럼** 나간다(사용자는 다른 존의 서버를 보고 있다는 것을 모른다).
    """
    lines = [
        f"'{verdict.outcome.identifier}'이(가) {len(verdict.candidates)}개 존에서 발견됐습니다. "
        "조회할 존을 선택해 주세요."
    ]
    for hit in verdict.candidates:
        name = hit.display_name()
        suffix = f" — {name}" if name and name != verdict.outcome.identifier else ""
        lines.append(f"- {hit.zone_label}{suffix}")
    return "\n".join(lines)


def candidate_db_ids(verdict: DiscoveryVerdict) -> list[str]:
    """되물을 선택지를 **발견된 존으로 좁힌다**(U5).

    전체 존을 다시 보여주면 사용자는 방금 시스템이 확인한 사실(어디에 있는지)을
    다시 추측해야 한다 — 탐색을 한 의미가 사라진다.
    """
    seen: list[str] = []
    for hit in verdict.candidates:
        if hit.db_id and hit.db_id not in seen:
            seen.append(hit.db_id)
    return seen


def trace_payload(verdict: DiscoveryVerdict) -> dict[str, Any]:
    """`state.discovery_trace`에 실을 순수 dict(체크포인터 직렬화 대상).

    무엇을 어디서 찾았는지·어디가 실패했는지를 남긴다 — 0건 진단(D-176 후속1)과 같은
    원칙이다: **끊긴 지점이 특정되어야 한다.**
    """
    return {
        "identifier": verdict.outcome.identifier,
        "state": verdict.state,
        "swept": list(verdict.outcome.swept),
        "errors": dict(verdict.outcome.errors),
        "hits": [
            {
                "db_id": h.db_id,
                "zone_label": h.zone_label,
                "hostname": h.hostname,
                "server_name": h.server_name,
            }
            for h in verdict.outcome.hits
        ],
    }
