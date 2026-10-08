"""데이터 소스 명시 인식 — 사용자가 이름(유사어 포함)으로 지목한 데이터 소스를 결정적으로 푼다
(plans/132 N-1·N-2 · D-293).

입력은 입력 파서가 만든 `target_db_hints`뿐이다(원문은 입력 파서가 레지스트리 유사어로 결정적 보강 —
D-065와 같은 방식). 힌트를 레지스트리 시스템으로 해소한다:

- **비DB 시스템**(제니퍼·문서·Prometheus — `solutions[].aliases`): 이름·유사어가 힌트에 있으면 그
  시스템
- **DB 시스템**(폴스타·자산관리 등 — `databases[].aliases`): `resolve_priority_db_ids`로 등록 DB를
  풀고 소유 시스템(`system_of`, 없으면 db_id)으로 묶는다

활성 판정은 시스템 단위다 — 폴스타의 한 존 DB가 비활성이어도 폴스타 시스템에 활성 DB가 있으면
활성이다 (존 사이 처분은 위치 힌트 규칙 D-246·TP-1.11a 소관). 비DB 시스템의 활성은 호출부가
넘긴다(이 계층은 처리기를 모른다).

D-004 경계: 등록된 시스템 이름의 인식이지 의도 분류가 아니다(D-004 부기 · D-281 ⑦). 이 모듈은 소스를
**고르지** 않는다 — 사용자가 이미 고른 소스를 알아볼 뿐이다.

계층: infrastructure (routing).
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass

from src.routing.location_hints import resolve_priority_db_ids
from src.routing.registry import get_registry
from src.utils.query_gen_common import term_in_text

KIND_NON_DB = "non_db"
KIND_DB = "db"


@dataclass(frozen=True)
class SourceMention:
    """사용자가 지목한 데이터 소스 1건.

    Attributes:
        hint: 사용자가 쓴 표현(입력 파서 힌트) — 안내 문구에는 이것만 싣는다(D-264).
        system: 시스템 키 — 비DB 솔루션 코드 · DB 소유 시스템(`system_of`) · 없으면 db_id.
        kind: `non_db` | `db`.
        db_ids: DB 시스템이면 힌트가 해소된 등록 db_id(선언 순서).
    """

    hint: str
    system: str
    kind: str
    db_ids: tuple[str, ...] = ()


def _non_db_system_of(hint: str) -> str | None:
    """힌트가 비DB 시스템의 이름·유사어를 담으면 그 시스템 코드(선언 순서 첫 일치)."""
    reg = get_registry()
    low = hint.strip().lower()
    for spec in reg.non_db_systems():
        for alias in reg.solution_aliases(spec.code):
            if low == alias.strip().lower() or term_in_text(alias, hint):
                return spec.code
    return None


def resolve_source_mentions(hints: Iterable[object] | None) -> list[SourceMention]:
    """힌트 → 지목된 데이터 소스 목록(힌트 순서 · 같은 힌트·시스템 중복 제거).

    해소되지 않는 힌트(서버명·일반어 등)는 버린다 — 소스 지목이 아니다.
    """
    reg = get_registry()
    registered = list(reg.db_ids())
    out: list[SourceMention] = []
    seen: set[tuple[str, str]] = set()
    for raw in hints or ():
        hint = str(raw or "").strip()
        if not hint:
            continue
        system = _non_db_system_of(hint)
        if system is not None:
            if (hint, system) not in seen:
                seen.add((hint, system))
                out.append(SourceMention(hint=hint, system=system, kind=KIND_NON_DB))
            continue
        by_system: dict[str, list[str]] = {}
        for db_id in resolve_priority_db_ids([hint], registered):
            by_system.setdefault(reg.system_of(db_id) or db_id, []).append(db_id)
        for sys_key, db_ids in by_system.items():
            if (hint, sys_key) not in seen:
                seen.add((hint, sys_key))
                out.append(SourceMention(hint=hint, system=sys_key, kind=KIND_DB,
                                         db_ids=tuple(db_ids)))
    return out


def mention_in_text(mention: SourceMention, text: str) -> bool:
    """task 질의가 이 지목을 가리키는가 — 사용자 표현 또는 그 시스템의 이름·유사어가 들어 있다."""
    if not text:
        return False
    if term_in_text(mention.hint, text) or mention.hint.lower() in text.lower():
        return True
    if mention.kind == KIND_NON_DB:
        return any(term_in_text(a, text) for a in get_registry().solution_aliases(mention.system))
    return False


def mentions_for_task(
    mentions: Iterable[SourceMention], task_query: str, *, single_task: bool
) -> list[SourceMention]:
    """한 task가 가리키는 지목만 고른다 — 단일 task 계획은 원문 전체(task 질의가 이름을
    빠뜨려도)."""
    items = list(mentions)
    if single_task:
        return items
    return [m for m in items if mention_in_text(m, task_query)]


def is_mention_active(
    mention: SourceMention, *, active_db_ids: Collection[str], active_non_db: Collection[str],
) -> bool:
    """지목된 시스템에 지금 조회 가능한 구성원이 있는가(시스템 단위).

    DB 시스템은 같은 소유 시스템의 활성 DB가 하나라도 있으면 활성이다(존 사이 처분은 위치 힌트
    규칙).
    """
    if mention.kind == KIND_NON_DB:
        return mention.system in active_non_db
    reg = get_registry()
    return any((reg.system_of(d) or d) == mention.system for d in active_db_ids)


def source_notice_text(hints: Iterable[str], *, unsupported_path: bool = False) -> str:
    """조회하지 않았다는 안내 — 사용자가 쓴 표현만 싣는다(레지스트리 표시명 비노출 · D-264)."""
    names = ", ".join(f"「{h}」" for h in dict.fromkeys(hints))
    reason = "이 조회 경로에서 처리하지 않아" if unsupported_path else "현재 연결되어 있지 않아"
    return (f"요청하신 {names} 데이터 소스는 {reason} 조회하지 않았습니다. "
            "다른 데이터 소스의 값으로 대신 답하지 않았습니다.")


def utilization_notice_text(hints: Iterable[str], *, kind: str | None = None) -> str:
    """사용률을 갖지 않은 소스에 사용량·사용률·사용 추이를 물었을 때의 안내(D-308 ⑨ · plans/132 G-1
    「안내만」).

    사용자가 쓴 표현만 싣는다(D-264). 지목이 없으면(분류가 고른 소스뿐) 소스 이름 없이 안내한다.
    ``kind``(`utilization_kind`)가 ``trend``면 「사용 추이」, 그 밖은 「사용량·사용률」로 부른다 —
    현재값을 물었는데 추이로, 추이를 물었는데 현재값으로 답하지 않는다.
    """
    trend = kind == "trend"
    what = "사용 추이" if trend else "사용량·사용률"
    topic = what + ("는" if trend else "은")
    names = ", ".join(f"「{h}」" for h in dict.fromkeys(hints))
    if not names:
        return (f"요청하신 {what} 정보는 조회 대상으로 정해진 데이터 소스에 없어 조회하지 "
                f"않았습니다. {topic} 관측 데이터가 기준이라, 관측 데이터에서 조회하려면 다시 "
                "질문해 주세요.")
    return (f"요청하신 {what} 정보는 {names}에 없습니다. {topic} 관측 데이터가 기준이라, "
            f"관측 데이터에서 조회하려면 {names} 없이 다시 질문해 주세요.")


def _area_names(labels: Iterable[str]) -> str:
    return " · ".join(dict.fromkeys(label for label in labels if label))


def area_notice_text(labels: Iterable[str]) -> str:
    """답변 영역의 소유 소스가 연결되지 않아 조회하지 않았다는 안내(plans/132 G-7 (a)).

    사용자가 소스를 말하지 않은 질의라 소스 이름 대신 **영역 설명**(레지스트리 `capabilities` 라벨 —
    벤더 중립)만 싣는다(D-264).
    """
    return (f"요청하신 정보({_area_names(labels)})를 가진 데이터 소스가 현재 연결되어 있지 않아 "
            "조회하지 않았습니다. 다른 데이터 소스의 값으로 대신 답하지 않았습니다.")


def area_partial_notice_text(labels: Iterable[str]) -> str:
    """일부 영역의 소유 소스만 연결되지 않았을 때의 사유 노트(조회는 다른 영역으로 진행)."""
    return (f"요청하신 정보 중 {_area_names(labels)}은(는) 그 정보를 가진 데이터 소스가 현재 "
            "연결되어 있지 않아 조회하지 않았습니다.")


def source_choice_question(labels: Iterable[str]) -> str:
    """소스 선택 칩 질문(plans/132 N-10) — 코드가 만든다(LLM 0)."""
    return (f"요청하신 정보({_area_names(labels)})는 여러 데이터 소스에 있습니다. "
            "조회할 데이터 소스를 선택해 주세요.")


def non_db_synonym_context(
    hints: Iterable[object] | None, previous_sources: Iterable[object] | None,
) -> str | None:
    """채팅 유사어 등록이 **비DB 소스 맥락**이면 그 소스의 표시 이름, 아니면 None(plans/132 G-15).

    유사어는 DB 공용 사전(폴스타·ITAM 공유)과 비DB 소스(제니퍼·문서 등 — 소유 패키지 설정 파일)로
    나뉜다(G-13). 판정:

    - 이번 턴에 소스를 지목했으면 그것만 본다 — 비DB 지목만 있으면 비DB 맥락(사용자가 쓴 표현을
      돌려준다 · D-264). DB 지목이 하나라도 있으면 DB 맥락.
    - 지목이 없으면 직전 턴이 닿은 소스(`conversation_context.previous_sources` — 2단 계획 출구가
      남긴 비DB 시스템)가 있을 때 비DB 맥락(레지스트리 표시명). 없으면 DB 맥락(현행 — DB를 못
      정해도 묻지 않고 DB 공용 사전).
    """
    mentions = resolve_source_mentions(hints)
    if mentions:
        if any(m.kind == KIND_DB for m in mentions):
            return None
        return mentions[0].hint
    reg = get_registry()
    for system in previous_sources or ():
        if reg.is_non_db_system(str(system)):
            return reg.system_label(str(system))
    return None


def non_db_synonym_guidance(name: str) -> str:
    """비DB 소스 맥락의 채팅 유사어 등록 안내(G-15) — DB 사전에 넣지 않았다는 사실을 함께 알린다."""
    return (f"「{name}」 데이터 소스의 유사어는 채팅으로 등록하지 않습니다 — 관리자가 그 소스의 "
            "설정 파일에서 관리합니다(관리자 매뉴얼 「데이터 소스 이름·유사어 관리」). DB 유사어 "
            "사전에도 넣지 않았습니다. DB 조회에 쓸 유사어라면 DB 이름을 함께 적어 다시 요청해 "
            "주세요.")


__all__ = [
    "KIND_DB",
    "KIND_NON_DB",
    "SourceMention",
    "area_notice_text",
    "non_db_synonym_context",
    "non_db_synonym_guidance",
    "area_partial_notice_text",
    "is_mention_active",
    "mention_in_text",
    "mentions_for_task",
    "resolve_source_mentions",
    "source_choice_question",
    "source_notice_text",
    "utilization_notice_text",
]
