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

**시스템 축 일반화(plans/102 §3.4 · D-224 ⑤).** 모듈 끝의 `SystemProbe`·`plan_probe`·
`decide_systems`는 같은 판정을 (시스템, 존) 축으로 넓힌다 — 식별자가 어느 시스템(그 안의
어느 DB)에 있는지로 조회할 시스템을 고르거나, 조회하지 않고 사유를 낸다. 위의 존 축 API는
바뀌지 않는다.

계층: domain (`scripts/arch_check.py` `src.domain` 매핑) — 순수 · I/O·LLM 0.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

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


# ──────────────────────────────────────────────
# 시스템 축 일반화 (plans/102 §3.4 · D-224 ⑤)
# ──────────────────────────────────────────────
#
# 존 축 판정이 "어느 존에 있나"였다면 이 절은 "어느 시스템(그 안의 어느 DB)에 있나"다.
# 식별자 소재를 시스템마다 고정 조회로 확인한 결과(`SystemProbe`)와 질의에 필요한 시스템
# (`ProbePlan`)으로 결정표(§3.4)를 판정한다. 시스템 이름·라벨은 전부 인자로 받는다 — 이 모듈은
# 어떤 시스템이 있는지 모른다(스키마·제품 리터럴 0).

#: 시스템별 소재 상태. **NOT_FOUND(확인했는데 없다)와 UNVERIFIED(확인하지 못했다)는 합치지 않는다.**
SYSTEM_FOUND = "found"
SYSTEM_NOT_FOUND = "not_found"
SYSTEM_UNVERIFIED = "unverified"

#: 판정 모드 — 필요한 답변 영역이 시스템 몇 개에 걸치는가.
MODE_SINGLE = "single"        # 한 시스템 소유
MODE_BOTH = "both"            # 두 시스템 이상 소유
MODE_AMBIGUOUS = "ambiguous"  # 소유가 모호한 영역만(G-1) — 정본은 기본값일 뿐이다

#: 동작.
ACTION_QUERY = "query"  # 조회할 시스템·DB를 확정한다(발견된 DB로 좁힌다)
ACTION_HALT = "halt"    # 조회하지 않고 사유를 낸다
ACTION_KEEP = "keep"    # 판정 근거가 없다 — 라우팅을 바꾸지 않고 사유만 남긴다

#: 결정표(§3.4) 행 코드 — 경과 노트·트레이스·테스트가 행 단위로 대조한다.
ROW_OWNER_FOUND = "owner_found"                        # ① 한 시스템 소유 · 소유 시스템에서 발견
ROW_OWNER_MISSING_ELSEWHERE = "owner_missing_elsewhere"  # ② 소유 시스템 미발견 · 다른 시스템 발견
ROW_ALL_FOUND = "all_found"                            # ③ 두 시스템 모두 · 양쪽 발견
ROW_PARTIAL_FOUND = "partial_found"                    # ④ 두 시스템 모두 · 한쪽만 발견
ROW_AMBIGUOUS_ONE_FOUND = "ambiguous_one_found"        # ⑤ 소유 모호 · 한쪽만 발견
ROW_AMBIGUOUS_BOTH_FOUND = "ambiguous_both_found"      # ⑥ 소유 모호 · 양쪽 발견
ROW_UNVERIFIED = "unverified"                          # ⑦ 조회 실패로 판정이 갈렸다
#: 결정표 밖 — 확인할 수 있는 곳을 다 확인했는데 어디에도 없다. 조회를 막지 않는다
#: (아래 `decide_systems`).
ROW_NONE_FOUND = "none_found"

#: 미발견 샘플 기본 개수(G-6 기본 가정).
DEFAULT_SAMPLE = 10


@dataclass(frozen=True)
class SystemProbe:
    """한 시스템의 소재 확인 결과 — (시스템, DB) 축.

    `hits`는 식별자(표시형) → 발견된 db_id들, `errors`는 조회 실패 db_id → 사유다. 시스템 단위
    사유(매니페스트 없음·조회 가능한 DB 없음)는 키 `""`로 싣는다. `unchecked`는 이 시스템이 받지
    않는 키 종류라 **확인할 수 없었던** 식별자 — 미발견으로 세지 않는다.
    """

    system: str
    label: str = ""
    identifiers: tuple[str, ...] = ()
    probed_db_ids: tuple[str, ...] = ()
    hits: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    errors: Mapping[str, str] = field(default_factory=dict)
    unchecked: tuple[str, ...] = ()
    #: db_id → 사용자 표시 라벨(실패 사유 표기용). 없으면 db_id.
    db_labels: Mapping[str, str] = field(default_factory=dict)

    @property
    def status(self) -> str:
        """발견이 하나라도 있으면 FOUND, 없는데 실패·미조회가 있으면 UNVERIFIED, 아니면 NOT_FOUND.

        조회한 DB가 0개면 **확인한 것이 없으므로** UNVERIFIED다 — "없다"가 아니다.
        """
        if any(self.hits.get(i) for i in self.identifiers):
            return SYSTEM_FOUND
        if self.errors or not self.probed_db_ids:
            return SYSTEM_UNVERIFIED
        if self.unchecked and len(self.unchecked) == len(self.identifiers):
            return SYSTEM_UNVERIFIED
        return SYSTEM_NOT_FOUND

    def found_db_ids(self) -> tuple[str, ...]:
        """발견된 DB(조회 순서)."""
        found = {d for i in self.identifiers for d in self.hits.get(i, ())}
        return tuple(d for d in self.probed_db_ids if d in found)

    def failed_db_ids(self) -> tuple[str, ...]:
        """조회에 실패한 DB(조회 순서)."""
        return tuple(d for d in self.probed_db_ids if d in self.errors)

    def scope_db_ids(self) -> tuple[str, ...]:
        """이 시스템을 조회할 때의 DB 스코프 — 발견된 DB + **확인하지 못한 DB**.

        실패한 DB를 빼면 "그 DB에는 없다"고 판정한 셈이 된다. 조회 대상에 남겨 두면 그 DB의
        결과나 실패 사유가 응답에 드러난다.
        """
        keep = set(self.found_db_ids()) | set(self.failed_db_ids())
        return tuple(d for d in self.probed_db_ids if d in keep)

    def missing(self) -> tuple[str, ...]:
        """확인했는데 찾지 못한 식별자(확인 불가 식별자는 제외)."""
        skip = set(self.unchecked)
        return tuple(i for i in self.identifiers if i not in skip and not self.hits.get(i))

    def failure_text(self) -> str:
        """조회 실패 사유 표기 — `라벨(사유)` 나열."""
        parts: list[str] = []
        for key, reason in self.errors.items():
            parts.append(f"{self.db_labels.get(key, key)}({reason})" if key else reason)
        if not self.probed_db_ids and not self.errors:
            parts.append("조회 가능한 DB 없음")
        return ", ".join(parts)


@dataclass(frozen=True)
class ProbePlan:
    """소재 확인 계획 — 무엇이 필요하고 어디를 비교할 수 있는가.

    `required`: SINGLE·AMBIGUOUS는 소유(정본) 시스템 1개, BOTH는 필요한 시스템 전부.
    `others`: 필요한 시스템 밖에서 소재를 확인할 수 있는 시스템(호출부가 인가·활성으로 거른 것).
    """

    mode: str
    required: tuple[str, ...]
    others: tuple[str, ...] = ()


@dataclass(frozen=True)
class SystemDecision:
    """결정표 판정 결과."""

    action: str
    row: str
    #: QUERY일 때 조회할 시스템(계획 순서)
    systems: tuple[str, ...] = ()
    #: QUERY일 때 조회할 DB — 시스템별 `scope_db_ids`의 합
    db_ids: tuple[str, ...] = ()
    #: 사용자에게 보일 사유(HALT면 이 문구가 곧 응답이다)
    message: str = ""
    #: 확인하지 못한 시스템 — 판정과 별개로 항상 노출한다
    unverified: tuple[str, ...] = ()


def plan_probe(
    required_systems: Sequence[str],
    *,
    ambiguous: bool,
    available_systems: Sequence[str],
) -> ProbePlan | None:
    """필요한 시스템과 모호 여부로 판정 모드를 정한다. 확인할 이유가 없으면 None.

    - 필요한 시스템 2개 이상 → BOTH (§3.4 발동 (a))
    - 필요한 영역이 전부 소유 모호 → AMBIGUOUS, 정본 + 비교할 다른 시스템 (발동 (c))
    - 한 시스템 소유 → SINGLE, 소유 시스템에서 못 찾으면 다른 시스템을 본다 (발동 (b)).
      **비교할 다른 시스템이 없으면 None** — 한 시스템만 있는 배포에서는 시스템 선택이 없다.
    """
    required = tuple(dict.fromkeys(s for s in required_systems if s))
    if not required:
        return None
    if len(required) >= 2:
        return ProbePlan(mode=MODE_BOTH, required=required)
    others = tuple(s for s in dict.fromkeys(available_systems) if s and s not in required)
    if not others:
        return None
    return ProbePlan(
        mode=MODE_AMBIGUOUS if ambiguous else MODE_SINGLE,
        required=required[:1],
        others=others,
    )


def pending_systems(plan: ProbePlan, probes: Mapping[str, SystemProbe]) -> tuple[str, ...]:
    """아직 확인하지 않은 시스템 중 **지금** 확인해야 할 것.

    SINGLE은 소유 시스템을 먼저 보고, 확인했는데 없을 때만 다른 시스템을 본다(G-5 기본 가정 —
    소유 시스템 우선). BOTH·AMBIGUOUS는 비교가 목적이라 한 번에 전부 본다.
    """
    if plan.mode == MODE_SINGLE:
        owner = plan.required[0]
        if owner not in probes:
            return (owner,)
        if probes[owner].status != SYSTEM_NOT_FOUND:
            return ()
        return tuple(s for s in plan.others if s not in probes)
    return tuple(s for s in plan.required + plan.others if s not in probes)


def classify_systems(probes: Sequence[SystemProbe]) -> str:
    """시스템 축 3분 판정 — 한 시스템에서 발견 RESOLVED · 여럿 AMBIGUOUS · 없음 NOT_FOUND.

    존 축 `classify`와 같은 어휘다. 확인 실패는 여기서 구분하지 않고 `SystemProbe.status`가
    보존한다.
    """
    found = [p for p in probes if p.status == SYSTEM_FOUND]
    if len(found) == 1:
        return RESOLVED
    if len(found) > 1:
        return AMBIGUOUS
    return NOT_FOUND


def _labels(probes: Sequence[SystemProbe]) -> str:
    return ", ".join(p.label or p.system for p in probes)


def _sample(items: Sequence[str], n: int) -> str:
    shown = ", ".join(items[:n])
    extra = len(items) - n
    return f"{shown} 외 {extra}건" if extra > 0 else shown


def _unverified_sentence(probes: Sequence[SystemProbe]) -> str:
    parts = []
    for p in probes:
        detail = p.failure_text()
        parts.append(f"{p.label or p.system}({detail})" if detail else (p.label or p.system))
    return f"{', '.join(parts)}에서는 등록 여부를 확인하지 못했습니다 — '없음'이 아닙니다."


def _detail_sentences(probes: Sequence[SystemProbe], n: int) -> list[str]:
    """발견된 시스템 안의 부분 사실 — 찾지 못한 식별자·확인하지 못한 DB."""
    out: list[str] = []
    for p in probes:
        label = p.label or p.system
        miss = p.missing()
        if miss:
            out.append(f"{label}에서 찾지 못한 대상 {len(miss)}건: {_sample(miss, n)}.")
        failed = p.failed_db_ids()
        if failed:
            names = ", ".join(f"{p.db_labels.get(d, d)}({p.errors[d]})" for d in failed)
            out.append(f"{label}의 일부 DB는 확인하지 못해 조회 대상에 남겼습니다: {names}.")
    return out


def _join(*parts: Any) -> str:
    flat: list[str] = []
    for part in parts:
        if isinstance(part, str):
            if part:
                flat.append(part)
        else:
            flat.extend(x for x in part if x)
    return " ".join(flat)


def _scope(probes: Sequence[SystemProbe]) -> tuple[str, ...]:
    out: list[str] = []
    for p in probes:
        for d in p.scope_db_ids():
            if d not in out:
                out.append(d)
    return tuple(out)


def _unprobed(system: str) -> SystemProbe:
    return SystemProbe(system=system, label=system, errors={"": "확인 결과 없음"})


def decide_systems(
    plan: ProbePlan,
    probes: Mapping[str, SystemProbe],
    *,
    sample: int = DEFAULT_SAMPLE,
) -> SystemDecision:
    """결정표(§3.4)를 판정한다. 입력만으로 결정되며 부작용이 없다.

    | 모드 | 확인 결과 | 동작 · 행 |
    |---|---|---|
    | SINGLE | 소유 발견 | QUERY 소유 · ① |
    | SINGLE | 소유 미발견 · 다른 시스템 발견 | **HALT** · ② |
    | BOTH | 전부 발견 | QUERY 전부 · ③ |
    | BOTH | 일부만 발견 | QUERY 발견분 · ④ |
    | AMBIGUOUS | 한쪽만 발견 | QUERY 발견된 쪽 · ⑤ |
    | AMBIGUOUS | 정본 포함 여럿 발견 | QUERY 정본 + 다른 시스템에도 있음 병기 · ⑥ |
    | 어느 모드든 | 확인 실패가 판정을 가름 | ⑦ — 실패한 시스템을 **미발견으로 치지 않는다** |
    | 어느 모드든 | 확인한 곳 전부 미발견 | KEEP · 표 밖(`none_found`) |

    ⑦의 동작: 소유(정본) 시스템을 확인하지 못했거나, 발견이 없고 실패만 있으면 **KEEP**(현행 라우팅
    유지 + 사유). BOTH에서 일부 발견 + 일부 실패면 실패한 시스템을 **조회 대상에 남긴다**(QUERY).

    `none_found`를 HALT로 하지 않는 이유: 확인은 고정 규칙(대소문자 무시 완전 일치·단축명·IP)이라
    본 조회가 다른 표기로 찾을 여지가 있고, 다른 시스템에 있다는 **반대 증거**도 없다. ②만 HALT인
    것은 다른 시스템에서 찾았다는 증거가 있기 때문이다.
    """
    def get(system: str) -> SystemProbe:
        return probes.get(system) or _unprobed(system)

    checked = [get(s) for s in plan.required + plan.others if s in probes or s in plan.required]
    unverified = tuple(p.system for p in checked if p.status == SYSTEM_UNVERIFIED)
    unverified_probes = [p for p in checked if p.status == SYSTEM_UNVERIFIED]
    unverified_note = _unverified_sentence(unverified_probes) if unverified_probes else ""

    def verdict(
        action: str, row: str, chosen: Sequence[SystemProbe] = (), message: str = "",
    ) -> SystemDecision:
        return SystemDecision(
            action=action,
            row=row,
            systems=tuple(p.system for p in chosen) if action == ACTION_QUERY else (),
            db_ids=_scope(chosen) if action == ACTION_QUERY else (),
            message=message,
            unverified=unverified,
        )

    if plan.mode == MODE_SINGLE:
        owner = get(plan.required[0])
        if owner.status == SYSTEM_FOUND:
            return verdict(ACTION_QUERY, ROW_OWNER_FOUND, [owner], _join(
                f"{owner.label}에서 확인해 {owner.label}을(를) 조회합니다.",
                _detail_sentences([owner], sample),
            ))
        if owner.status == SYSTEM_UNVERIFIED:
            return verdict(ACTION_KEEP, ROW_UNVERIFIED, message=unverified_note)
        others = [get(s) for s in plan.others if s in probes]
        found = [p for p in others if p.status == SYSTEM_FOUND]
        if found:
            return verdict(ACTION_HALT, ROW_OWNER_MISSING_ELSEWHERE, message=_join(
                f"{owner.label}에 등록되지 않은 서버입니다({_labels(found)}에는 있음): "
                f"{_sample(owner.missing(), sample)}.",
                unverified_note,
            ))
        if unverified_probes:
            return verdict(ACTION_KEEP, ROW_UNVERIFIED, message=_join(
                f"{owner.label}에서 찾지 못했습니다: {_sample(owner.missing(), sample)}.",
                unverified_note,
            ))
        return verdict(ACTION_KEEP, ROW_NONE_FOUND, message=(
            f"{_labels([owner] + others)}에서 찾지 못했습니다: {_sample(owner.missing(), sample)} "
            "(서버명 철자와 조회 권한 범위를 확인해 주세요)."
        ))

    if plan.mode == MODE_BOTH:
        required = [get(s) for s in plan.required]
        found = [p for p in required if p.status == SYSTEM_FOUND]
        absent = [p for p in required if p.status == SYSTEM_NOT_FOUND]
        failed = [p for p in required if p.status == SYSTEM_UNVERIFIED]
        if not found:
            if failed:
                return verdict(ACTION_KEEP, ROW_UNVERIFIED, message=unverified_note)
            ids = sorted({i for p in absent for i in p.missing()})
            return verdict(ACTION_KEEP, ROW_NONE_FOUND, message=(
                f"{_labels(absent)}에서 찾지 못했습니다: {_sample(ids, sample)} "
                "(서버명 철자와 조회 권한 범위를 확인해 주세요)."
            ))
        absent_ids = sorted({i for p in absent for i in p.missing()})
        absent_note = (
            f"{_labels(absent)}에 등록되지 않은 서버입니다({_labels(found)}에는 있음): "
            f"{_sample(absent_ids, sample)}."
            if absent else ""
        )
        if failed:
            # 확인하지 못한 시스템을 빼면 "그곳에는 없다"고 판정한 셈이다 — 조회 대상에 남긴다.
            chosen = [p for p in required if p.status != SYSTEM_NOT_FOUND]
            return verdict(ACTION_QUERY, ROW_UNVERIFIED, chosen, _join(
                f"{_labels(found)}에서 확인했습니다.", absent_note, unverified_note,
                "확인하지 못한 시스템도 조회 대상에 남깁니다.",
                _detail_sentences(found, sample),
            ))
        if absent:
            return verdict(ACTION_QUERY, ROW_PARTIAL_FOUND, found, _join(
                absent_note, f"{_labels(found)}만 조회합니다.",
                _detail_sentences(found, sample),
            ))
        return verdict(ACTION_QUERY, ROW_ALL_FOUND, found, _join(
            f"{_labels(found)} 모두에서 확인해 함께 조회합니다.",
            _detail_sentences(found, sample),
        ))

    # MODE_AMBIGUOUS
    canon = get(plan.required[0])
    if canon.status == SYSTEM_UNVERIFIED:
        return verdict(ACTION_KEEP, ROW_UNVERIFIED, message=unverified_note)
    others = [get(s) for s in plan.others if s in probes]
    found_others = [p for p in others if p.status == SYSTEM_FOUND]
    if canon.status == SYSTEM_FOUND:
        if found_others:
            return verdict(ACTION_QUERY, ROW_AMBIGUOUS_BOTH_FOUND, [canon], _join(
                f"{canon.label}와(과) {_labels(found_others)} 모두에 등록돼 있어 기본 정본인 "
                f"{canon.label}을(를) 조회합니다({_labels(found_others)}에도 있음).",
                unverified_note,
                _detail_sentences([canon], sample),
            ))
        absent = [p for p in others if p.status == SYSTEM_NOT_FOUND]
        return verdict(ACTION_QUERY, ROW_AMBIGUOUS_ONE_FOUND, [canon], _join(
            f"{canon.label}에서만 확인돼 {canon.label}을(를) 조회합니다"
            + (f"({_labels(absent)}에는 없음)." if absent else "."),
            unverified_note,
            _detail_sentences([canon], sample),
        ))
    if found_others:
        return verdict(ACTION_QUERY, ROW_AMBIGUOUS_ONE_FOUND, found_others, _join(
            f"{canon.label}에는 없고 {_labels(found_others)}에서 확인돼 "
            f"{_labels(found_others)}을(를) 조회합니다.",
            unverified_note,
            _detail_sentences(found_others, sample),
        ))
    if unverified_probes:
        return verdict(ACTION_KEEP, ROW_UNVERIFIED, message=_join(
            f"{canon.label}에서 찾지 못했습니다: {_sample(canon.missing(), sample)}.",
            unverified_note,
        ))
    return verdict(ACTION_KEEP, ROW_NONE_FOUND, message=(
        f"{_labels([canon] + others)}에서 찾지 못했습니다: {_sample(canon.missing(), sample)} "
        "(서버명 철자와 조회 권한 범위를 확인해 주세요)."
    ))


def system_trace_payload(
    plan: ProbePlan, probes: Mapping[str, SystemProbe], decision: SystemDecision,
) -> dict[str, Any]:
    """`state.entity_probe`에 실을 순수 dict — 무엇을 어디서 확인했고 어디가 실패했는지."""
    return {
        "mode": plan.mode,
        "required": list(plan.required),
        "others": list(plan.others),
        "state": classify_systems(list(probes.values())),
        "action": decision.action,
        "row": decision.row,
        "systems": list(decision.systems),
        "db_ids": list(decision.db_ids),
        "unverified": list(decision.unverified),
        "message": decision.message,
        "probes": {
            name: {
                "label": p.label,
                "status": p.status,
                "probed_db_ids": list(p.probed_db_ids),
                "found_db_ids": list(p.found_db_ids()),
                "errors": dict(p.errors),
                "missing": list(p.missing()),
                "unchecked": list(p.unchecked),
            }
            for name, p in probes.items()
        },
    }
