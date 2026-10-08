"""제니퍼 소스 선택 사다리(plans/147 §4.1 · D-322) — APM task가 조회할 소스 집합을 정한다.

순수 함수만 둔다(입출력·로그 없음 — 상태는 기동 점검이 채우는 사용 가능 소스 보관 하나뿐).
호출은 `apm_query`(application)·질의 라우트(요청 검증)가 한다.

- 소스 단어는 레지스트리 `solutions[apm].sources[].terms`만 본다(위치 표면어 `locations` 비참조).
  이 단어는 시스템 유사어가 아니다 — `source_alias_terms()`·위치 힌트에 넣지 않는다(D-293).
- 매칭 규칙은 `term_in_text`와 같다(라틴 단어 경계·대소문자 무시 · D-271 제외어) + 긴 단어 우선.
- 못 찾으면 넓히지 않는다(D-290 ⑥) — 미연결 소스만 걸리면 다른 소스로 넓히지 않고 안내만 한다.
- 「전체」는 `ALL_SOURCES`(`"*"`) 하나로 표현한다 — 화면 선택(`screen_ids`) ·
  승계 값(`SourceScope.ids`) · 되묻기 선택지(`SourceDecision.choices`)에서 같다.
  소스 id 형식(소문자 슬러그)과 겹치지 않는다.

계층: infrastructure (routing).
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass, field

from src.routing.registry import SourceSpec
from src.utils.query_gen_common import term_spans

# 「전체」 표지 — 소스 id 규칙(`^[a-z][a-z0-9_]{0,15}$`)에 걸리지 않아 실제 id와 섞이지 않는다.
ALL_SOURCES = "*"
ALL_LABEL = "전체"

# 사용 가능 소스(기동 정합 점검 결과 — plans/147 §4.4) · None = 레지스트리 전부 사용 가능
# (점검 전·점검 실패 포함). 기동 시 1회 채우고 같은 프로세스에서는 읽기만 한다.
_available: frozenset[str] | None = None


def set_available_apm_sources(ids: Collection[str] | None) -> None:
    """사용 가능 소스 id를 보관한다 — None이면 레지스트리 전부 사용 가능으로 되돌린다."""
    global _available
    _available = None if ids is None else frozenset(str(i) for i in ids)


def get_available_apm_sources() -> frozenset[str] | None:
    """보관한 사용 가능 소스 id — None = 레지스트리 전부 사용 가능."""
    return _available


@dataclass(frozen=True)
class MatchResult:
    """원문 소스 단어 매칭 결과.

    Attributes:
        source_ids: 걸린 소스 id(레지스트리 선언 순서)
        evidence: 소스 id → 그 소스를 걸리게 한 단어(원문 등장 순서)
    """

    source_ids: tuple[str, ...] = ()
    evidence: dict[str, tuple[str, ...]] = field(default_factory=dict, hash=False)


@dataclass(frozen=True)
class SourceScope:
    """대화 승계 값 — 직전 선택(plans/147 §4.3 `apm_source_scope`).

    Attributes:
        ids: 소스 id 목록 · 「전체」면 `(ALL_SOURCES,)`(쓸 때마다 사용 가능 소스 전부로 푼다)
        basis: 선택 근거 `selected` | `answered` | `named` | `all`
    """

    ids: tuple[str, ...]
    basis: str


@dataclass(frozen=True)
class SourceDecision:
    """사다리 결과.

    Attributes:
        action: `query`(조회) | `ask`(조회 0 + 되묻기) | `unavailable`(조회 0 + 미연결 안내) |
            `unscoped`(좁히지 않음 — 소스 <2 배포 등 현행 그대로)
        source_ids: 조회할 소스 id(선언 순서 · 「전체」면 사용 가능 소스 전부)
        basis: `selected`(화면) | `answered`(되묻기 답) | `named`(단어·분해) | `inherited`(승계) |
            `all`(「전체」 선택) | `none`(지목 없음·좁히지 않음)
        evidence: 근거 단어(단어 매치일 때만)
        choices: 되묻기 선택지 id(선언 순서 · 「전체」를 내면 끝에 `ALL_SOURCES`)
        question: 되묻기 문구(`ask`일 때만)
        notices: 고지 문자열(좁힘 근거 · 미연결 안내)
        scope_update: 승계 갱신 값 — 단 1·2 성립 시 새 값 · 단 3이면 받은 승계 그대로 ·
            되묻기·미연결·좁히지 않음이면 None(호출자는 기존 승계를 바꾸지 않는다)
    """

    action: str
    source_ids: tuple[str, ...] = ()
    basis: str = "none"
    evidence: tuple[str, ...] = ()
    choices: tuple[str, ...] = ()
    question: str = ""
    notices: tuple[str, ...] = ()
    scope_update: SourceScope | None = None


# ── 단어 매칭 ──────────────────────────────────────────────────────────────


def match_source_terms(text: str, sources: Sequence[SourceSpec]) -> MatchResult:
    """원문에서 소스 단어를 찾는다 — `term_in_text` 규칙 + 긴 단어 우선.

    원문에서 걸린 구간이 **다른 더 긴 단어의 구간 안에** 들면 그 짧은 매치는 세지 않는다
    (예: 「〇〇 제니퍼」가 걸리면 그 안의 「〇〇」는 따로 세지 않는다). 같은 단어를 여러 소스가
    가지면 그 소스 모두 걸린다.
    """
    if not text:
        return MatchResult()
    spans: dict[str, list[tuple[int, int]]] = {}
    for spec in sources:
        for term in spec.terms:
            if term not in spans:
                spans[term] = term_spans(term, text)
    all_spans = [(s, e) for found in spans.values() for s, e in found]

    def _covered(start: int, end: int) -> bool:
        return any(
            s <= start and end <= e and (e - s) > (end - start) for s, e in all_spans
        )

    first_pos: dict[str, int] = {}
    for term, found in spans.items():
        kept = [s for s, e in found if not _covered(s, e)]
        if kept:
            first_pos[term] = min(kept)
    ids: list[str] = []
    evidence: dict[str, tuple[str, ...]] = {}
    for spec in sources:
        hit = sorted((t for t in spec.terms if t in first_pos), key=lambda t: first_pos[t])
        if hit:
            ids.append(spec.id)
            evidence[spec.id] = tuple(hit)
    return MatchResult(source_ids=tuple(ids), evidence=evidence)


# ── 문구 ──────────────────────────────────────────────────────────────────


def source_label(source_id: str, sources: Sequence[SourceSpec]) -> str:
    """소스 표시 이름 — 레지스트리 label · 없으면 id(「전체」 표지는 「전체」)."""
    if source_id == ALL_SOURCES:
        return ALL_LABEL
    for spec in sources:
        if spec.id == source_id:
            return spec.label or spec.id
    return source_id


def _labels(ids: Sequence[str], sources: Sequence[SourceSpec]) -> str:
    return " · ".join(source_label(i, sources) for i in ids)


def _topic_particle(word: str) -> str:
    """주제 조사 — 끝 글자가 받침 있는 한글이면 「은」, 아니면 「는」."""
    last = word[-1:] if word else ""
    if "가" <= last <= "힣" and (ord(last) - ord("가")) % 28:
        return "은"
    return "는"


def narrow_notice(
    ids: Sequence[str], sources: Sequence[SourceSpec], *, basis: str,
    evidence: Sequence[str] = (),
) -> str:
    """좁힘 고지 1줄 — 「〇〇 제니퍼만 조회했습니다(근거: 「…」)」 · 「…(이전 선택 승계)」 ·
    「…(화면 선택)」."""
    head = f"{_labels(ids, sources)}만 조회했습니다"
    if basis == "named":
        if evidence:
            return head + "(근거: " + "·".join(f"「{w}」" for w in evidence) + ")"
        return head + "(근거: 질문 해석)"
    if basis == "inherited":
        return head + "(이전 선택 승계)"
    if basis == "answered":
        return head + "(되묻기 답)"
    return head + "(화면 선택)"


def ask_question(choices: Sequence[str], sources: Sequence[SourceSpec]) -> str:
    """되묻기 문구 — 「A · B 중 어느 것을 조회할까요?」(「전체」 표지는 「전체」로 적는다)."""
    return f"{_labels(choices, sources)} 중 어느 것을 조회할까요?"


def unavailable_notice(ids: Sequence[str], sources: Sequence[SourceSpec]) -> str:
    """미연결 안내 — 「〇〇 제니퍼는 연결되지 않아 조회하지 않았습니다」."""
    names = _labels(ids, sources)
    return f"{names}{_topic_particle(names)} 연결되지 않아 조회하지 않았습니다"


# ── 사다리 ────────────────────────────────────────────────────────────────


def _known(ids: Sequence[str], sources: Sequence[SourceSpec]) -> tuple[str, ...]:
    """레지스트리에 있는 id만 선언 순서로(모르는 id는 버린다)."""
    wanted = set(ids)
    return tuple(s.id for s in sources if s.id in wanted)


def _resolve(
    ids: tuple[str, ...],
    *,
    basis: str,
    sources: Sequence[SourceSpec],
    usable: tuple[str, ...],
    evidence: tuple[str, ...] = (),
    scope: SourceScope | None,
    ask_on_many: bool,
) -> SourceDecision:
    """지목된 소스(레지스트리 id)를 사용 가능 소스로 거른 뒤 조회·되묻기·미연결 안내로 정한다.

    거른 뒤 0개 = 미연결 안내(넓히지 않음 · D-290 ⑥) · 1개 = 조회 · 2개 이상 = `ask_on_many`면
    되묻기, 아니면 조회. 빠진 미연결 소스는 안내를 함께 단다. `scope`는 조회일 때만 싣는다.
    """
    kept = tuple(i for i in ids if i in usable)
    dropped = tuple(i for i in ids if i not in usable)
    missing = (unavailable_notice(dropped, sources),) if dropped else ()
    if not kept:
        return SourceDecision(
            action="unavailable", basis=basis, evidence=evidence, notices=missing,
        )
    if len(kept) >= 2 and ask_on_many:
        return SourceDecision(
            action="ask", basis=basis, evidence=evidence, choices=kept,
            question=ask_question(kept, sources), notices=missing,
        )
    narrowed = len(kept) < len(usable)
    notices = (
        (narrow_notice(kept, sources, basis=basis, evidence=evidence),) if narrowed else ()
    ) + missing
    return SourceDecision(
        action="query", source_ids=kept, basis=basis, evidence=evidence, notices=notices,
        scope_update=scope,
    )


def select_apm_sources(
    sources: Sequence[SourceSpec],
    *,
    available: Collection[str] | None = None,
    screen_ids: Sequence[str] = (),
    screen_basis: str = "selected",
    text: str = "",
    llm_ids: Sequence[str] = (),
    inherited: SourceScope | None = None,
    prior_target_ids: Sequence[str] = (),
) -> SourceDecision:
    """APM task의 소스 선택 사다리(plans/147 §4.1) — 위에서부터 성립하는 첫 단만 쓴다.

    1. 화면 선택·되묻기 답(`screen_ids` · `ALL_SOURCES` = 「전체」) — 여러 개여도 되묻지 않는다.
    2. 이번 턴 원문의 소스 단어(`text`) — 단어 0건일 때만 분해 LLM `llm_ids`를 쓴다(단어가 이긴다).
       걸린 소스 1개 = 조회 · 2개 이상 = 되묻기(선택지 = 걸린 소스).
    3. 승계 — 지시어 후속이면 직전 대상 행의 소스(`prior_target_ids`) 우선, 아니면 `inherited`.
    4. 지목 없음 — 사용 가능 소스 ≥2면 되묻기(선택지 = 사용 가능 소스 + 「전체」) · 1개면 조회.

    모든 단은 레지스트리 ∩ `available`(None = 전부 사용 가능)로 거른다. 거른 뒤 비면 다음 단으로
    넘어가지 않고 미연결 안내로 끝낸다(D-290 ⑥ — 넓히지 않음). 단 3도 같다: 승계 소스가 미연결이면
    다른 소스로 넘기지 않는다 — `available`은 기동 점검 결과라 같은 프로세스에서는 조회했던 소스가
    미연결로 바뀌지 않고(재기동·설정 변경 뒤에만 생긴다), 조용히 단 4로 넘기면 사용 가능 소스 1개
    배포에서 다른 소스를 지목 없이 조회하게 된다. 사용자는 새 단어·화면 선택으로 바꾼다.

    좁히지 않음(`unscoped` · 현행 바이트 불변): 레지스트리 소스 <2 · 레지스트리 소스 중 사용 가능한
    것이 0(게이트웨이 단일 설정 `default` 등 — 소스 1개 배포와 같다).

    Args:
        sources: 레지스트리 소스 표(`sources_of("apm")`)
        available: 사용 가능 소스 id(정합 점검) — None = 전부 사용 가능(점검 실패 포함)
        screen_ids: 이번 턴 화면 선택·되묻기 답 id(`ALL_SOURCES` 포함 가능)
        screen_basis: `screen_ids`의 근거 — `selected`(화면) | `answered`(되묻기 답)
        text: 소스 단어를 찾을 원문(호출자가 원문·task 질의를 합쳐 넘긴다)
        llm_ids: 분해 LLM `sources` 검증 후 id
        inherited: 대화 승계 값
        prior_target_ids: 지시어 후속일 때 직전 대상 행의 소스 id(아니면 빈 값)
    """
    if len(sources) < 2:
        return SourceDecision(action="unscoped")
    usable = tuple(s.id for s in sources if available is None or s.id in available)
    if not usable:
        return SourceDecision(action="unscoped")

    # 단 1 — 화면 선택·되묻기 답
    if ALL_SOURCES in screen_ids:
        return SourceDecision(
            action="query", source_ids=usable, basis="all",
            scope_update=SourceScope(ids=(ALL_SOURCES,), basis="all"),
        )
    picked = _known(screen_ids, sources)
    if picked:
        return _resolve(
            picked, basis=screen_basis, sources=sources, usable=usable,
            scope=SourceScope(ids=tuple(i for i in picked if i in usable), basis=screen_basis),
            ask_on_many=False,
        )

    # 단 2 — 이번 턴 소스 단어(우선) · 분해 LLM sources(보조)
    match = match_source_terms(text, sources)
    if match.source_ids:
        named = match.source_ids
        # 근거 단어는 조회할(사용 가능) 소스의 것만 — 전부 미연결이면 걸린 소스 전부의 것
        cited = [i for i in named if i in usable] or list(named)
        evidence = tuple(dict.fromkeys(w for i in cited for w in match.evidence[i]))
    else:
        named = _known(llm_ids, sources)
        evidence = ()
    if named:
        return _resolve(
            named, basis="named", sources=sources, usable=usable, evidence=evidence,
            scope=SourceScope(ids=tuple(i for i in named if i in usable), basis="named"),
            ask_on_many=True,
        )

    # 단 3 — 승계(지시어 후속이면 직전 대상 행의 소스 우선)
    prior = _known(prior_target_ids, sources)
    if prior:
        return _resolve(
            prior, basis="inherited", sources=sources, usable=usable, scope=inherited,
            ask_on_many=False,
        )
    if inherited is not None:
        ids = usable if ALL_SOURCES in inherited.ids else _known(inherited.ids, sources)
        if ids:
            return _resolve(
                ids, basis="inherited", sources=sources, usable=usable, scope=inherited,
                ask_on_many=False,
            )

    # 단 4 — 지목 없음
    if len(usable) >= 2:
        choices = (*usable, ALL_SOURCES)
        return SourceDecision(
            action="ask", choices=choices, question=ask_question(choices, sources),
        )
    return SourceDecision(action="query", source_ids=usable)
