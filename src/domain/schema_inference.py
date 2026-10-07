"""스키마 자산 추론 — 관계·값 형식·코드·주석 열거·식별 키·규칙 (D-294 · plans/133 W1).

스키마(스냅샷)와 읽기 전용 조회로 얻은 **값 표본·집계**만 받아 자산 제안을 만드는 순수 함수
모음이다. DB에 묻지 않고(조회는 `schema_cache.schema_probe`) LLM도 부르지 않는다 — 같은 입력이면
같은 출력이다.

- 관계: 선언 FK + 기본키 일치 추론(부모 기본키 컬럼이 전부 자식에 있으면 자식 → 부모). 테이블 절반
  넘게 나오는 공통 컬럼만으로 된 기본키는 부모에서 빼고, 진부분집합 후보·같은 기본키 테이블 쌍은
  관계선 대신 「같은 기본키 군」으로 묶는다. `scripts/itam_erd.py`와 같은 규칙이다 — 스크립트는 단독
  실행 설계라 이 모듈을 import하지 않고, 두 구현의 결과 동등성을 테스트로 묶는다. 테이블이 몇
  개뿐이면 함께 있는 컬럼이 전부 공통이 되므로 `min_tables_for_common` 미만에서는 공통 컬럼 제외를
  끌 수 있다.
- 값 형식: `YYYYMMDD`·`YYYYMMDDHH24MISS` 문자열 날짜 · IPv4 · 호스트명 · 플래그 · 쉼표 다중값 비율.
- 코드값: 이름·주석 단서로 후보를 고르고(값 조회는 호출자), 주석의 `1:정상, 2:장애` 열거를 읽는다.
- 이름 일치 관계 후보(plans/140 W1-2): 기본키 없는 테이블끼리 같은 이름의 식별자형 컬럼(값 조회는
  호출자).
- 자산 조립 순수 함수(관계 채택 행 · 쿼리 규칙과 식별 키 후보 · `entity_keys` · 주석 유사어) —
  P1과 외부망 빌더가 같이 쓴다.

계층: domain. 특정 DB의 스키마 리터럴을 두지 않는다(`overfit_check` 스캔 대상).
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

#: 공통 컬럼 판정 비율 — 이 비율을 넘는 테이블에 나오는 컬럼만으로 된 기본키는 부모가 아니다
DEFAULT_COMMON_RATIO = 0.5
#: 값 형식 판정 하한(표본 중 이 비율 이상이 형식에 맞아야 한다)
FORMAT_RATIO = 0.95
#: 식별 키 판정 하한
ENTITY_KEY_RATIO = 0.9
#: 코드 컬럼으로 보는 서로 다른 값 수 상한
CODE_MAX_DISTINCT = 50

_DATE8_RE = re.compile(r"^(19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])$")
_DATETIME14_RE = re.compile(
    r"^(19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])([01]\d|2[0-3])[0-5]\d[0-5]\d$"
)
_IPV4_OCTET = r"(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
_IPV4_RE = re.compile(rf"^({_IPV4_OCTET}\.){{3}}{_IPV4_OCTET}$")
_HOST_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_HOSTNAME_RE = re.compile(rf"^(?=.{{1,253}}$){_HOST_LABEL}(?:\.{_HOST_LABEL})*$")
_MULTI_SPLIT_RE = re.compile(r"\s*[,;]\s*")
_FLAG_SETS: tuple[frozenset[str], ...] = (
    frozenset({"Y", "N"}), frozenset({"0", "1"}), frozenset({"T", "F"}),
)

# 코드성 컬럼 단서 — 이름 끝 조각 · 주석 낱말
_CODE_NAME_RE = re.compile(
    r"(?i)(?:^|_|[a-z])(cd|code|yn|type|typ|stat|status|gb|gbn|div|kind|cls|grp|flag|lvl|grade)$"
)
_CODE_WORDS = r"코드|구분|여부|상태|유형|종류|등급|분류|단계"
_CODE_COMMENT_RE = re.compile(_CODE_WORDS)
# 한글 이름은 끝 낱말로 본다(plans/140 W1-1) — 라틴 이름 판정은 그대로다
_CODE_NAME_KO_RE = re.compile(rf"(?:{_CODE_WORDS})$")
_CODE_TYPE_RE = re.compile(
    r"^(char|character|varchar|character varying|nchar|nvarchar|graphic|vargraphic|"
    r"tinyint|smallint|int|integer|mediumint|bit|boolean|bool|enum|set)\b",
    re.IGNORECASE,
)
_HOST_HINT_RE = re.compile(r"(?i)host|hst|호스트|서버\s*명")
_IP_HINT_RE = re.compile(r"(?i)(^|_|[a-z])ip|아이피|ip\s*주소")
# 주석 열거 — `1:정상, 2:장애` · `Y=사용/N=미사용` · `(01:서버,02:네트워크)`
_ENUM_PAIR_RE = re.compile(
    r"([A-Za-z0-9]{1,10})\s*[:=]\s*([^,/;|()\[\]:=]{1,30}?)\s*(?=[,/;|)\]]|$)"
)
# 주석 라벨 — 괄호·콜론·쉼표 앞 첫 마디
_LABEL_CUT_RE = re.compile(r"[(\[:,/;]")
_BACKTICK_RE = re.compile(r"`([^`\n]{1,128})`")
# 이름 일치 관계 후보의 식별자형 이름 끝(plans/140 W1-2)
# — 라틴 `_id`·`No`·`cd`류 · 한글 `번호`·`코드`·`식별자`·`ID`
_ID_NAME_RE = re.compile(
    r"(?i:(?:^|_)(?:id|no|cd|code))$|[a-z0-9](?:Id|ID|No|NO|Cd|CD|Code)$"
    r"|[\uac00-\ud7a3](?:ID|Id|id)$|(?:번호|코드|식별자)$"
)
_KEY_STRING_TYPE_RE = re.compile(
    r"^(char|character|varchar|character varying|nchar|nvarchar|varchar2|text|graphic|"
    r"vargraphic)\b",
    re.IGNORECASE,
)
_KEY_NUMBER_TYPE_RE = re.compile(
    r"^(tinyint|smallint|int|integer|mediumint|bigint|decimal|numeric|number|dec)\b",
    re.IGNORECASE,
)
_DOTTED_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_$#]*)\.([A-Za-z_][A-Za-z0-9_$#]*)\b")


# ──────────────────────────────────────────────
# 관계 추론
# ──────────────────────────────────────────────


@dataclass(frozen=True)
class InferredRelation:
    """자식 → 부모 관계 1건.

    Attributes:
        child: 자식 테이블 키
        parent: 부모 테이블 키
        child_columns: 자식 쪽 컬럼(실제 표기)
        parent_columns: 부모 쪽 컬럼(실제 표기 · 선언 FK에서 모르면 빈 튜플)
        origin: ``declared``(선언 FK) · ``inferred``(기본키 일치 추론)
    """

    child: str
    parent: str
    child_columns: tuple[str, ...]
    parent_columns: tuple[str, ...]
    origin: str


@dataclass
class TableShape:
    """관계 추론 입력 — 컬럼 이름 · 기본키 · 선언 FK ``(자식 컬럼들, 부모 테이블, 부모 컬럼들)``."""

    columns: list[str]
    primary_key: list[str] = field(default_factory=list)
    foreign_keys: list[tuple[tuple[str, ...], str, tuple[str, ...]]] = field(default_factory=list)


def table_family(table_name: str) -> str:
    """테이블 군 — 끝자리 숫자를 뗀 접두(예: ``ABC12`` → ``ABC``)."""
    match = re.match(r"^(.*?[A-Za-z_])\d+$", table_name)
    return match.group(1) if match else table_name


def common_columns(tables: Mapping[str, TableShape], ratio: float) -> set[str]:
    """테이블 비율 ``ratio``를 넘게 등장하는 컬럼(소문자)."""
    if not tables:
        return set()
    counts: Counter[str] = Counter()
    for shape in tables.values():
        counts.update({c.lower() for c in shape.columns})
    limit = ratio * len(tables)
    return {name for name, count in counts.items() if count > limit}


def infer_relations(
    tables: Mapping[str, TableShape],
    common_ratio: float = DEFAULT_COMMON_RATIO,
    *,
    min_tables_for_common: int = 0,
) -> tuple[list[InferredRelation], list[list[str]]]:
    """선언 FK + 기본키 일치 추론 관계와 「같은 기본키 군」을 돌려준다.

    - 부모 P의 기본키 컬럼이 전부 자식 C에 있으면 C → P (이름 비교는 대소문자 무시).
    - P의 기본키가 공통 컬럼으로만 이뤄졌으면 부모 후보에서 뺀다.
    - C와 P의 기본키 집합이 같으면 관계선 대신 같은 기본키 군으로 묶는다.
    - C의 부모 후보 중 기본키가 다른 후보의 진부분집합이면 뺀다(더 구체적인 부모를 거쳐 이어진다).
    - 기본키가 같은 부모 후보가 여럿이면 이름순 첫째만 잇는다.
    - 선언 FK가 있는 (자식, 부모) 쌍은 추론하지 않는다.

    Args:
        min_tables_for_common: 테이블이 이 수보다 적으면 공통 컬럼 제외를 하지 않는다 — 테이블이
            몇 개뿐이면 두 테이블에 함께 있는 컬럼이 전부 「공통」이 돼 관계가 하나도 안 나온다
            (기본 0 = `scripts/itam_erd.py`와 같은 동작)

    Returns:
        (관계 목록 — (부모, 자식, origin) 순 정렬, 같은 기본키 군 목록)
    """
    common = common_columns(tables, common_ratio) if len(tables) >= min_tables_for_common else set()
    relations: list[InferredRelation] = []
    declared_pairs: set[tuple[str, str]] = set()
    for child, shape in tables.items():
        for child_cols, parent, parent_cols in shape.foreign_keys:
            relations.append(
                InferredRelation(child, parent, tuple(child_cols), tuple(parent_cols), "declared")
            )
            declared_pairs.add((child, parent))

    pk_sets = {
        name: frozenset(c.lower() for c in shape.primary_key)
        for name, shape in tables.items()
        if shape.primary_key
    }
    parents = {name: pk for name, pk in pk_sets.items() if pk - common}
    col_maps = {name: {c.lower(): c for c in shape.columns} for name, shape in tables.items()}

    same_key: dict[frozenset[str], set[str]] = defaultdict(set)
    for child, cols in col_maps.items():
        candidates: list[str] = []
        for parent, pk in parents.items():
            if parent == child or not pk <= cols.keys():
                continue
            if pk_sets.get(child) == pk:
                same_key[pk].update({parent, child})
                continue
            candidates.append(parent)
        representative: dict[frozenset[str], str] = {}
        for parent in sorted(candidates):
            representative.setdefault(parents[parent], parent)
        for parent in candidates:
            pk = parents[parent]
            if representative[pk] != parent:
                continue
            if any(pk < parents[other] for other in candidates if other != parent):
                continue
            if (child, parent) in declared_pairs:
                continue
            parent_cols = tuple(tables[parent].primary_key)
            child_cols = tuple(cols[c.lower()] for c in parent_cols)
            relations.append(InferredRelation(child, parent, child_cols, parent_cols, "inferred"))

    relations.sort(key=lambda r: (r.parent, r.child, r.origin))
    groups = sorted(sorted(g) for g in same_key.values())
    return relations, groups


def shapes_from_snapshot(snapshot: Mapping[str, Any]) -> dict[str, TableShape]:
    """`schema_snapshot.build_snapshot` 결과를 관계 추론 입력으로 바꾼다.

    스냅샷 FK(``"col->table.col"``)는 컬럼 단위라 복합 FK도 컬럼마다 1건으로 들어온다.
    """
    shapes: dict[str, TableShape] = {}
    for key, data in (snapshot.get("tables") or {}).items():
        if not isinstance(data, Mapping):
            continue
        columns: Mapping[str, Any] = data.get("columns") or {}
        fks: list[tuple[tuple[str, ...], str, tuple[str, ...]]] = []
        for raw in data.get("foreign_keys") or []:
            column, _, target = str(raw).partition("->")
            table, _, target_column = target.rpartition(".")
            if column and table:
                fks.append(((column,), table, (target_column,) if target_column else ()))
        shapes[str(key)] = TableShape(
            columns=list(columns),
            primary_key=[c for c, attrs in columns.items()
                         if isinstance(attrs, Mapping) and attrs.get("primary_key")],
            foreign_keys=fks,
        )
    return shapes


def key_type_class(data_type: str | None) -> str | None:
    """키 비교용 타입 군 — ``string``(문자열) · ``number``(정수·소수) · 그 밖은 None."""
    text = str(data_type or "").strip()
    if _KEY_STRING_TYPE_RE.match(text):
        return "string"
    if _KEY_NUMBER_TYPE_RE.match(text):
        return "number"
    return None


def is_identifier_name(name: str) -> bool:
    """이름 끝이 식별자형(``ID``·``번호``·``코드``·``식별자`` · 라틴 ``id``·``no``·``cd``류)인지."""
    return bool(_ID_NAME_RE.search(name))


@dataclass(frozen=True)
class NameMatchColumn:
    """이름 일치 관계 후보 컬럼(plans/140 W1-2).

    Attributes:
        name: 컬럼 이름(소문자 · 비교 키)
        type_class: ``string`` · ``number``
        members: 참여 테이블과 그 테이블의 실제 컬럼 표기 ``(테이블, 컬럼)`` — 선언 기본키가 없는
            테이블만 · 테이블 이름순
        table_count: 그 이름이 나오는 범위 테이블 수(타입·기본키 무관)
    """

    name: str
    type_class: str
    members: tuple[tuple[str, str], ...]
    table_count: int


def name_match_columns(
    columns: Mapping[str, Sequence[tuple[str, str]]],
    primary_keyed: Iterable[str] = (),
    common_ratio: float = DEFAULT_COMMON_RATIO,
) -> list[NameMatchColumn]:
    """기본키 없는 테이블 쌍의 관계 후보 컬럼을 고른다(값 조회는 호출자).

    - 범위 테이블에서 같은 이름(대소문자 무시)이 2개 이상 · 범위 테이블 수 × ``common_ratio``
      **미만** 테이블에 나오고, 이름 끝이 식별자형인 컬럼.
    - 같은 타입 군(문자열끼리 · 정수/소수끼리)으로 묶는다 — 군이 다른 테이블끼리는 잇지 않는다.
    - 선언 기본키가 있는 테이블(``primary_keyed``)은 참여하지 않는다 — 그 쌍은 종전 경로(선언 ·
      기본키 일치 추론 · 같은 기본키 군)가 맡는다. 참여 테이블이 2개 미만이면 후보가 아니다.

    Args:
        columns: 범위 테이블 ``{테이블: [(컬럼, 타입)]}``
        primary_keyed: 선언 기본키가 있는 테이블
        common_ratio: 공통 컬럼 판정 비율

    Returns:
        (이름, 타입 군) 순 정렬된 후보 목록
    """
    keyed = set(primary_keyed)
    by_name: dict[str, dict[str, tuple[str, str]]] = defaultdict(dict)
    for table, cols in columns.items():
        for column, data_type in cols:
            by_name[column.lower()].setdefault(table, (column, data_type))
    limit = common_ratio * len(columns)
    out: list[NameMatchColumn] = []
    for name in sorted(by_name):
        found = by_name[name]
        if not (2 <= len(found) < limit):
            continue
        if not any(is_identifier_name(column) for column, _t in found.values()):
            continue
        groups: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for table, (column, data_type) in found.items():
            type_class = key_type_class(data_type)
            if type_class and table not in keyed:
                groups[type_class].append((table, column))
        for type_class in sorted(groups):
            members = tuple(sorted(groups[type_class]))
            if len(members) >= 2:
                out.append(NameMatchColumn(name, type_class, members, len(found)))
    return out


def accepted_relationships(evidence: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """관계 근거 항목 → 채택 관계 행 ``{"from", "to", "origin", "overlap"}``(컬럼 쌍 단위).

    근거 항목은 ``child``·``parent``·``child_columns``·``parent_columns``·``origin``·``overlap``·
    ``accepted``를 가진다. 채택(``accepted``)된 항목만 근거 순서대로 펼친다.
    """
    rows: list[dict[str, Any]] = []
    for item in evidence:
        if not item.get("accepted"):
            continue
        for child_col, parent_col in zip(
            item.get("child_columns") or [], item.get("parent_columns") or []
        ):
            rows.append({
                "from": f"{item['child']}.{child_col}", "to": f"{item['parent']}.{parent_col}",
                "origin": item.get("origin"), "overlap": item.get("overlap"),
            })
    return rows


# ──────────────────────────────────────────────
# 값 형식
# ──────────────────────────────────────────────


@dataclass(frozen=True)
class ValueProfile:
    """값 표본의 형식 비율(값 자체는 담지 않는다).

    Attributes:
        total: 판정에 쓴 값 수(빈 값 제외)
        date8: ``YYYYMMDD`` 비율 · datetime14: ``YYYYMMDDHH24MISS`` 비율
        ipv4: IPv4 비율(다중값 칸은 조각이 전부 IPv4여야 맞음) · hostname: 호스트명 형식 비율
        multi_value: 쉼표·세미콜론 다중값 비율 · mixed_case: 같은 값의 대소문자 변형이 섞였는지
        flag: 값 집합이 플래그(Y/N · 0/1 · T/F)이면 그 값들, 아니면 빈 튜플
    """

    total: int
    date8: float = 0.0
    datetime14: float = 0.0
    ipv4: float = 0.0
    hostname: float = 0.0
    multi_value: float = 0.0
    mixed_case: bool = False
    flag: tuple[str, ...] = ()


def classify_values(values: Iterable[Any]) -> ValueProfile:
    """값 표본의 형식 비율을 계산한다(문자열화 · 앞뒤 공백 제거 · 빈 값 제외)."""
    texts = [str(v).strip() for v in values if v is not None and str(v).strip()]
    total = len(texts)
    if not total:
        return ValueProfile(total=0)

    def ratio(predicate: Any) -> float:
        return sum(1 for t in texts if predicate(t)) / total

    def all_parts(pattern: re.Pattern[str]) -> Any:
        return lambda t: all(pattern.match(p) for p in _MULTI_SPLIT_RE.split(t) if p)

    def is_hostname(text: str) -> bool:
        return all(
            _HOSTNAME_RE.match(p) and not p.replace(".", "").isdigit()
            for p in _MULTI_SPLIT_RE.split(text) if p
        )

    folded: dict[str, set[str]] = defaultdict(set)
    for t in texts:
        folded[t.casefold()].add(t)
    distinct = {t.upper() for t in texts}
    flag = next((tuple(sorted(s)) for s in _FLAG_SETS if distinct and distinct <= s), ())
    return ValueProfile(
        total=total,
        date8=ratio(_DATE8_RE.match),
        datetime14=ratio(_DATETIME14_RE.match),
        ipv4=ratio(all_parts(_IPV4_RE)),
        hostname=ratio(is_hostname),
        multi_value=ratio(lambda t: bool(_MULTI_SPLIT_RE.search(t))),
        mixed_case=any(len(variants) > 1 for variants in folded.values()),
        flag=flag,
    )


# ──────────────────────────────────────────────
# 코드값 · 주석
# ──────────────────────────────────────────────


def is_code_candidate(name: str, data_type: str, comment: str | None = None) -> bool:
    """코드성 컬럼 후보인지 — 짧은 문자열·작은 정수 타입이면서 이름·주석 단서가 있어야 한다.

    이름 단서는 라틴 끝 조각(``cd``·``yn``·``type``…)이나 한글 끝 낱말(``코드``·``구분``·
    ``여부``…)이다.
    """
    if not _CODE_TYPE_RE.match(str(data_type or "").strip()):
        return False
    return bool(
        _CODE_NAME_RE.search(name) or _CODE_NAME_KO_RE.search(name)
        or (comment and _CODE_COMMENT_RE.search(comment))
    )


def parse_comment_enum(comment: str | None) -> dict[str, str]:
    """주석의 코드 열거를 ``{값: 라벨}``로 읽는다(2쌍 이상일 때만 — 우연한 ``a:b`` 하나는
    버린다)."""
    if not comment:
        return {}
    pairs: dict[str, str] = {}
    for value, label in _ENUM_PAIR_RE.findall(comment):
        label = label.strip()
        if label and value not in pairs:
            pairs[value] = label
    return pairs if len(pairs) >= 2 else {}


def comment_label(comment: str | None, max_length: int = 20) -> str | None:
    """주석의 첫 마디를 라벨(유사어 후보)로 — 괄호·콜론·쉼표 앞까지, 길면 None."""
    if not comment:
        return None
    head = _LABEL_CUT_RE.split(comment.strip(), maxsplit=1)[0].strip()
    if not head or len(head) > max_length:
        return None
    return head


def comment_synonyms(comments: Mapping[str, str]) -> dict[str, list[str]]:
    """컬럼 주석 → 유사어 시드 ``{"table.column": [라벨]}`` (P1과 외부망 빌더가 같이 쓴다).

    라벨은 `comment_label`이고, 컬럼 이름과 같으면(대소문자 무시) 싣지 않는다. 테이블 주석(점 없는
    키)은 건너뛴다. 입력 순서를 지킨다.
    """
    out: dict[str, list[str]] = {}
    for key, text in comments.items():
        table, _, column = key.rpartition(".")
        label = comment_label(text)
        if table and column and label and label.casefold() != column.casefold():
            out[key] = [label]
    return out


def code_value_labels(
    values: Sequence[str], comment_enum: Mapping[str, str], code_table: Mapping[str, str] | None
) -> dict[str, str]:
    """코드값 → 라벨 — 주석 열거 우선, 없으면 공통코드 테이블 라벨(값 표기는 그대로 둔다)."""
    labels: dict[str, str] = {}
    for value in values:
        label = comment_enum.get(value) or (code_table or {}).get(value)
        if label:
            labels[value] = label
    return labels


# ──────────────────────────────────────────────
# 식별 키 · 규칙 문장
# ──────────────────────────────────────────────


def entity_key_kind(name: str, comment: str | None, profile: ValueProfile) -> str | None:
    """컬럼이 교차 질의 식별 키(``hostname``·``ip``)인지 — 이름·주석 단서와 값 형식 비율이 함께
    맞아야 한다."""
    hint_text = f"{name} {comment or ''}"
    if profile.total == 0:
        return None
    if _IP_HINT_RE.search(hint_text) and profile.ipv4 >= ENTITY_KEY_RATIO:
        return "ip"
    if (
        _HOST_HINT_RE.search(hint_text)
        and profile.hostname >= ENTITY_KEY_RATIO
        and profile.ipv4 < 0.5
    ):
        return "hostname"
    return None


def rules_and_entity_candidates(
    columns: Mapping[str, Mapping[str, Any]],
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    """컬럼별 값 형식에서 쿼리 규칙 목록과 식별 키 후보를 만든다.

    Args:
        columns: ``{"table.column": {"type", "comment", "profile": ValueProfile|None, "error"}}`` —
            ``profile``이 None인 컬럼(조회 못 함)은 건너뛴다

    Returns:
        (쿼리 규칙 목록 — 컬럼 순서대로, 식별 키 후보 ``{테이블: {종류: {"column", "ratio",
        "multi_value"}}}`` — 같은 테이블·종류는 뒤 컬럼이 이긴다)
    """
    rules: list[str] = []
    entity: dict[str, dict[str, Any]] = {}
    for key, item in columns.items():
        profile: ValueProfile | None = item.get("profile")
        if profile is None:
            continue
        table, _, column = key.rpartition(".")
        rules.extend(query_rules_for_column(table, column, profile))
        kind = entity_key_kind(column, item.get("comment"), profile)
        if kind:
            entity.setdefault(table, {})[kind] = {
                "column": column, "ratio": profile.ipv4 if kind == "ip" else profile.hostname,
                "multi_value": profile.multi_value >= 0.05,
            }
    return rules, entity


def entity_keys_asset(
    entity: Mapping[str, Mapping[str, Any]], rows: Mapping[str, int | None]
) -> dict[str, Any] | None:
    """식별 키 후보 중 호스트명을 가진(있으면 IP도 가진) 테이블 하나를 골라 `entity_keys` 자산으로.

    Args:
        entity: `rules_and_entity_candidates`의 식별 키 후보
        rows: 테이블 행 수(모르면 None) — IP 보유 → 행 수 큰 순 → 이름순으로 고른다
    """
    candidates = [t for t, kinds in entity.items() if "hostname" in kinds]
    if not candidates:
        return None
    table = sorted(
        candidates, key=lambda t: ("ip" not in entity[t], -(rows.get(t) or 0), t)
    )[0]
    kinds = entity[table]
    keys: list[dict[str, Any]] = []
    # 호스트명은 DNS처럼 대소문자를 가리지 않고 비교한다
    keys.append({"type": "hostname", "column": kinds["hostname"]["column"], "priority": 1,
                 "compare": "casefold"})
    if "ip" in kinds:
        ip: dict[str, Any] = {"type": "ip", "column": kinds["ip"]["column"], "priority": 2}
        if kinds["ip"]["multi_value"]:
            ip["multi_value"] = True
        keys.append(ip)
    return {"entity": "server", "table": table, "keys": keys}


def query_rules_for_column(table: str, column: str, profile: ValueProfile) -> list[str]:
    """값 형식에서 결정적 쿼리 규칙 문장을 만든다(해당 없으면 빈 목록)."""
    ref = f"`{table}.{column}`"
    rules: list[str] = []
    if profile.total == 0:
        return rules
    if profile.date8 >= FORMAT_RATIO:
        rules.append(
            f"{ref}는 'YYYYMMDD' 형식의 문자열 날짜다 — 기간 조건은 같은 형식 문자열로 비교한다"
            f"(예: `{column} BETWEEN '20260901' AND '20260930'`). 날짜 함수·CAST로 바꾸지 않는다."
        )
    elif profile.datetime14 >= FORMAT_RATIO:
        rules.append(
            f"{ref}는 'YYYYMMDDHH24MISS'(14자리) 형식의 문자열 일시다 — 기간 조건은 같은 형식 "
            f"문자열로 비교한다(예: `{column} >= '20260901000000'`)."
        )
    if profile.flag:
        rules.append(f"{ref}는 플래그 값 {', '.join(repr(v) for v in profile.flag)} 중 하나다.")
    if profile.multi_value >= 0.05:
        rules.append(
            f"{ref}에는 값 여러 개가 쉼표로 이어진 행이 있다 — 한 값을 찾을 때는 `=` 대신 "
            "구분자를 고려한 `LIKE` 비교를 쓴다."
        )
    if profile.mixed_case:
        rules.append(f"{ref}는 대소문자 표기가 섞여 있다 — 값 비교는 대소문자를 무시한다(`UPPER`).")
    return rules


def referenced_identifiers(text: str) -> set[str]:
    """글 속 식별자 후보 — 백틱 안 낱말과 ``table.column`` 표기(소문자 · 검증용)."""
    found: set[str] = set()
    for token in _BACKTICK_RE.findall(text or ""):
        word = token.strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$#]*(\.[A-Za-z_][A-Za-z0-9_$#]*)?", word):
            found.add(word.lower())
    for table, column in _DOTTED_RE.findall(text or ""):
        found.add(f"{table}.{column}".lower())
    return found
