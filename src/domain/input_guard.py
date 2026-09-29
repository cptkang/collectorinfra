"""입력 가드 판정기 — 비조회 입력 · 단위 의심 · 조건 충돌 (plans/123 S-1 · S-2 · S-6).

사용자 원문과 파서 조건만 보고 결정적으로 판정하는 순수 함수 셋이다. LLM 0.

- S-2 `classify_non_query_input`: 조회가 아닌 입력(빈 입력 · 붙여넣은 SQL · 쓰기 요청 ·
  프롬프트 탈취 · 계정 요구)을 kind로 판정한다. 섀도 — 응답을 바꾸지 않고 로그로만
  쓰인다(123·G-8 (c)).
- S-1 `unit_suspects`: 사용자 값의 단위가 지표에 비해 이상한 곳(「메모리 64MB 이상」)을
  찾는다. 고지만 하고 조회는 요청 그대로 한다(D-264 ⑤ 불변).
- S-6 `condition_conflicts`: 파서 `filter_conditions`에서 같은 열의 수치 구간이 공집합인
  곳을 찾는다. 섀도(123·G-8 (c)).

**규칙은 좁게**(123 RK-3) — 정상 조회 질의를 잡느니 놓친다. 넓히는 것은 run R5 섀도 측정 뒤다.
kind 문자열의 정본은 `src.domain.disclosure`다.

계층: domain — 순수 · I/O·LLM·전역 상태 0.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from src.domain.disclosure import (
    BLANK_INPUT,
    CREDENTIAL_REQUEST,
    PROMPT_INJECTION,
    SQL_INPUT,
    WRITE_REQUEST,
)

#: 판정 근거 스팬의 최대 길이(로그용).
_MATCHED_MAX = 80


def _clip(span: str) -> str:
    """로그용 스팬을 최대 길이로 자른다."""
    return span[:_MATCHED_MAX]


# ── S-2 비조회 입력 ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GuardVerdict:
    """비조회 입력 판정 한 건 (plans/123 S-2)."""

    #: disclosure 상수 중 하나(`BLANK_INPUT` · `SQL_INPUT` · `WRITE_REQUEST` · …).
    kind: str
    #: 판정 근거가 된 원문 스팬(로그용 · 최대 80자).
    matched: str


#: 문자·숫자(유니코드) — 하나도 없으면 빈 입력이다(기호·공백만).
_WORD_CHAR = re.compile(r"[^\W_]")

# SQL 규칙은 키워드 뒤에 ASCII 식별자가 올 때만 잡는다 — 「select 문 작성법」·「DELETE FROM 뜻」처럼
# 뒤가 한국어면 SQL에 관한 자연어다.
_SQL_WRITE_HEAD = re.compile(
    r"^(?:DELETE\s+FROM|DROP\s+(?:TABLE|DATABASE|VIEW|INDEX)(?:\s+IF\s+EXISTS)?"
    r"|UPDATE\s+[A-Za-z_\"][\w.\"]*\s+SET|INSERT\s+INTO|TRUNCATE(?:\s+TABLE)?"
    r"|ALTER\s+TABLE|CREATE\s+TABLE|(?:GRANT|REVOKE)\s+.+?\s+(?:ON|TO|FROM))"
    r"\s+[A-Za-z_\"]",
    re.IGNORECASE | re.DOTALL,
)
_SQL_STACKED_WRITE = re.compile(
    r";\s*(?:DROP|DELETE|UPDATE|INSERT|ALTER|TRUNCATE|CREATE)\s+[A-Za-z_\"]", re.IGNORECASE
)
_SQL_SELECT = re.compile(r"^SELECT\s.+?\bFROM\s+[A-Za-z_\"(]", re.IGNORECASE | re.DOTALL)
_SQL_WITH = re.compile(r"^WITH\s.*?\bAS\s*\(.*?\bSELECT\b", re.IGNORECASE | re.DOTALL)

_PROMPT_INJECTION = re.compile(
    r"(?:이전|앞의|위의|기존|모든)\s*(?:지시|명령|규칙|지침|프롬프트)\S*\s*(?:을|를|은|는)?"
    r"\s*(?:모두|전부|다)?\s*무시"
    r"|시스템\s*프롬프트"
    r"|ignore\s+(?:all\s+)?(?:previous|prior|above)\s+(?:instructions|prompts?)"
    r"|system\s+prompt"
    r"|너의\s*(?:규칙|지시)\S*\s*(?:을|를)?\s*(?:알려|보여|출력)",
    re.IGNORECASE,
)

#: 자격 증명 명사 — 「암호화」는 설정 항목이라 뺀다.
_CREDENTIAL_NOUN = re.compile(
    r"비밀번호|패스워드|암호(?!화)|password|passwd|credential|api\s*key|시크릿|secret"
    r"|접속\s*계정|로그인\s*계정|접속\s*정보|계정\s*정보",
    re.IGNORECASE,
)
_CREDENTIAL_ASK = re.compile(r"알려|보여|줘|뭐야|뭐지|무엇|출력|조회")
#: 계정 인벤토리 질의(「비밀번호 만료일이 지난 계정」) 표지 — 있으면 계정 요구로 보지 않는다.
_CREDENTIAL_INVENTORY = re.compile(r"만료|정책|변경일|변경\s*이력|복잡도")
_SENTENCE_SPLIT = re.compile(r"[.!?\n。]+")

#: 정상 관리 명령(「캐시 삭제해줘」·「유사어 삭제」·「기억 삭제」·「양식 삭제」) 표지.
_WRITE_ADMIN_EXEMPT = re.compile(r"캐시|유사어|기억|양식")
# 대상 명사 — 「행」·「로우」는 「은행」·「실행」·「플로우」 속 글자를 잡지 않게
# 앞 글자가 한글이면 뺀다.
_WRITE_TARGET = (
    r"(?:테이블|데이터베이스|데이터|레코드|디비|스키마|(?<![가-힣])(?:행|로우)"
    r"|(?<![A-Za-z0-9_])(?:rows?|tables?|db)(?![A-Za-z0-9_]))"
)
_WRITE_VERB = (
    r"(?:삭제|지워|지우|드롭|수정|변경|업데이트|(?:drop|truncate|update|delete)(?![A-Za-z]))"
)
# 명령형 — 뒤에 한글이 이어지면(「변경해서」·「수정해야」) 명령이 아니다.
_IMPERATIVE = (
    r"(?:해\s*주세요|해\s*줘요?|해라|하라|하세요|시켜\s*주세요|시켜\s*줘요?|시켜|주세요|줘요?|해)"
    r"(?![가-힣])"
)
# 대상 명사와 쓰기 동사가 붙어 있을 때만(조사·「전부」류만 허용) 잡는다 —
# 「데이터 조회 기간 변경해줘」·「결과 테이블 정렬 변경해줘」처럼 명사가 동사의 목적어가 아닌
# 질의를 거른다.
# 「삭제된」·「변경 이력」·「수정일」 같은 수식형은 명령형이 뒤따르지 않아 자연히 빠진다.
_WRITE_REQUEST_NL = re.compile(
    _WRITE_TARGET
    + r"\s*(?:을|를|은|는|이|가|도|만)?\s*(?:전부|모두|전체|다|싹)?\s*"
    + _WRITE_VERB
    + r"\s*(?:"
    + _IMPERATIVE
    + r"|[.!]*\s*$)",
    re.IGNORECASE,
)


def classify_non_query_input(text: str | None) -> GuardVerdict | None:
    """조회가 아닌 입력을 판정한다 — 첫 매치를 돌려주고 없으면 None (plans/123 S-2).

    판정 순서: 빈 입력 → 붙여넣은 SQL(쓰기 → 조회) → 프롬프트 탈취 → 계정 요구 → 자연어 쓰기 요청.
    섀도 판정이다 — 호출부는 결과로 응답을 바꾸지 않고 로그로만 남긴다(123·G-8 (c)).
    """
    raw = (text or "").strip()
    if not _WORD_CHAR.search(raw):
        return GuardVerdict(BLANK_INPUT, _clip(raw))

    if _SQL_WRITE_HEAD.match(raw):
        return GuardVerdict(WRITE_REQUEST, _clip(raw))
    stacked = _SQL_STACKED_WRITE.search(raw)
    if stacked:
        return GuardVerdict(WRITE_REQUEST, _clip(raw[stacked.start():]))
    if _SQL_SELECT.match(raw) or _SQL_WITH.match(raw):
        return GuardVerdict(SQL_INPUT, _clip(raw))

    injection = _PROMPT_INJECTION.search(raw)
    if injection:
        return GuardVerdict(PROMPT_INJECTION, _clip(injection.group(0)))

    if not _CREDENTIAL_INVENTORY.search(raw):
        for sentence in _SENTENCE_SPLIT.split(raw):
            if _CREDENTIAL_NOUN.search(sentence) and _CREDENTIAL_ASK.search(sentence):
                return GuardVerdict(CREDENTIAL_REQUEST, _clip(sentence.strip()))

    if not _WRITE_ADMIN_EXEMPT.search(raw):
        write = _WRITE_REQUEST_NL.search(raw)
        if write:
            return GuardVerdict(WRITE_REQUEST, _clip(write.group(0).strip()))
    return None


# ── S-1 단위 의심 ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class UnitSuspect:
    """사용자 값 단위 의심 한 건 (plans/123 S-1)."""

    #: 원문 숫자 그대로("64", "965.5").
    value: str
    #: 원문 단위 그대로("MB", "메가" 등).
    unit: str
    #: "memory" | "disk_capacity".
    metric: str
    #: 원문의 숫자+단위 스팬("64MB").
    span: str


# 메가·킬로는 「메가헤르츠」·「킬로비트」를 잡지 않는다.
# ASCII 단위 뒤에 영문이 이어지면(「MBps」) 뺀다.
_UNIT_VALUE = re.compile(
    r"(?<![\d.])(\d+(?:\.\d+)?)\s*"
    r"(MB|mb|Mb|KB|kb|Kb|메가바이트|킬로바이트|메가(?!헤르츠|비트)|킬로(?!헤르츠|비트))"
    r"(?![A-Za-z])"
)
_KB_UNITS = frozenset({"KB", "kb", "Kb", "킬로바이트", "킬로"})
#: MB 계열은 이 값 미만일 때만 의심한다(GB 단위로 1 미만).
_MB_SUSPECT_BELOW = 1024

# 「램」은 「프로그램」, ram은 program 속 글자를 잡지 않는다.
_MEMORY_CONTEXT = re.compile(
    r"메모리|(?<![가-힣])램|(?<![A-Za-z])(?:ram|memory)(?![A-Za-z])", re.IGNORECASE
)
_DISK_CONTEXT = re.compile(
    r"(?:디스크|스토리지|storage|disk)\s*(?:용량|크기|사이즈)", re.IGNORECASE
)
#: 프로세스·JVM·컨테이너 메모리는 MB 단위가 정상이다 — 원문에 있으면 판정하지 않는다.
_UNIT_EXEMPT = re.compile(
    r"프로세스|process|힙|heap|jvm|스레드|thread|컨테이너|container|버퍼|buffer|캐시",
    re.IGNORECASE,
)
#: 절 경계 — 문맥어와 숫자 사이에 있으면 같은 절이 아니다.
_CLAUSE_BREAK = re.compile(r"[,，]|그리고|및")
#: 같은 절로 보는 거리(문맥어가 숫자 앞 · 숫자 뒤).
_CONTEXT_BEFORE_MAX = 20
_CONTEXT_AFTER_MAX = 10


def _context_distance(text: str, pattern: re.Pattern[str], start: int, end: int) -> int | None:
    """숫자 스팬 `[start, end)`와 같은 절에 있는 문맥어까지의 최단 거리. 없으면 None."""
    best: int | None = None
    for m in pattern.finditer(text):
        if m.end() <= start:
            gap, limit = text[m.end():start], _CONTEXT_BEFORE_MAX
        elif m.start() >= end:
            gap, limit = text[end:m.start()], _CONTEXT_AFTER_MAX
        else:
            continue
        if len(gap) > limit or _CLAUSE_BREAK.search(gap):
            continue
        if best is None or len(gap) < best:
            best = len(gap)
    return best


def unit_suspects(text: str | None) -> list[UnitSuspect]:
    """용량 지표에 붙은 MB·KB 값 중 서버 용량으로 보기 어려운 것을 찾는다 (plans/123 S-1).

    - 같은 절의 문맥어가 메모리면 `memory`, 디스크 용량이면 `disk_capacity`(둘 다면 가까운 쪽).
    - MB 계열은 값 < 1024일 때만, KB 계열은 항상 의심이다.
    - 고지 전용이다 — 조회는 요청 그대로 한다(D-264 ⑤).
    """
    if not text or _UNIT_EXEMPT.search(text):
        return []
    out: list[UnitSuspect] = []
    for m in _UNIT_VALUE.finditer(text):
        value, unit = m.group(1), m.group(2)
        if unit not in _KB_UNITS and float(value) >= _MB_SUSPECT_BELOW:
            continue
        memory = _context_distance(text, _MEMORY_CONTEXT, m.start(), m.end())
        disk = _context_distance(text, _DISK_CONTEXT, m.start(), m.end())
        if memory is not None and (disk is None or memory <= disk):
            metric = "memory"
        elif disk is not None:
            metric = "disk_capacity"
        else:
            continue
        out.append(UnitSuspect(value=value, unit=unit, metric=metric, span=m.group(0)))
    return out


_METRIC_LABEL = {"memory": "서버 메모리 용량", "disk_capacity": "디스크 용량"}


def render_unit_suspect(s: UnitSuspect) -> str:
    """단위 의심 고지 문구 — 조회는 요청 그대로 했음을 함께 알린다 (plans/123 S-1 · D-264 ⑤)."""
    return (
        f"'{s.span}'은(는) {_METRIC_LABEL[s.metric]}으로는 매우 작은 값입니다. "
        "요청하신 값 그대로 조회했습니다 — GB를 뜻하셨다면 단위를 바꿔 다시 질의해 주세요."
    )


# ── S-6 조건 충돌 ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ConditionConflict:
    """같은 열의 수치 조건이 공집합인 충돌 한 건 (plans/123 S-6)."""

    field: str
    #: 사람이 읽는 조건 표기("> 90", "< 10") — 입력 순서.
    conditions: tuple[str, ...]


_NUMERIC_OPS = frozenset({"=", ">", ">=", "<", "<="})


def _as_number(value: Any) -> float | None:
    """수치 조건 값을 float로 — int/float(bool 제외) · 숫자 문자열("90", "90.5", "90%")만."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        num = float(value)
    elif isinstance(value, str):
        try:
            num = float(value.strip().removesuffix("%").strip())
        except ValueError:
            return None
    else:
        return None
    return num if math.isfinite(num) else None


def _is_empty_interval(conds: list[tuple[str, float]]) -> bool:
    """하한·상한·등호로 만든 구간이 공집합인가 — 「=v」는 「>=v」이면서 「<=v」로 본다."""
    lo, lo_incl = -math.inf, True
    hi, hi_incl = math.inf, True
    for op, v in conds:
        if op in (">", ">=", "="):
            incl = op != ">"
            if v > lo or (v == lo and not incl):
                lo, lo_incl = v, incl
        if op in ("<", "<=", "="):
            incl = op != "<"
            if v < hi or (v == hi and not incl):
                hi, hi_incl = v, incl
    return lo > hi or (lo == hi and not (lo_incl and hi_incl))


def condition_conflicts(filter_conditions: list[dict[str, Any]] | None) -> list[ConditionConflict]:
    """파서 조건에서 같은 열의 수치 구간이 공집합인 곳을 찾는다 — 필드당 최대 1건 (plans/123 S-6).

    op가 `= > >= < <=`이고 값이 수치인 조건만 본다(`!=`·LIKE·IN·비수치는 무시).
    필드는 대소문자·앞뒤 공백을 무시해 묶는다. 섀도 판정이다(123·G-8 (c)).
    """
    if not isinstance(filter_conditions, list):
        return []
    # 정규화 필드 → (원 필드명, [(op, 수치, 원 값)])
    groups: dict[str, tuple[str, list[tuple[str, float, Any]]]] = {}
    for cond in filter_conditions:
        if not isinstance(cond, dict):
            continue
        field = str(cond.get("field") or "").strip()
        op = str(cond.get("op") or "").strip()
        num = _as_number(cond.get("value"))
        if not field or op not in _NUMERIC_OPS or num is None:
            continue
        groups.setdefault(field.lower(), (field, []))[1].append((op, num, cond.get("value")))

    out: list[ConditionConflict] = []
    for field, items in groups.values():
        if _is_empty_interval([(op, num) for op, num, _ in items]):
            conditions = tuple(f"{op} {str(raw).strip()}" for op, _, raw in items)
            out.append(ConditionConflict(field=field, conditions=conditions))
    return out
