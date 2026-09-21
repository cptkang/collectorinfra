"""값 기반 서버 식별 키 판정 · 정규화 · 매칭 등급 (plans/102 §3.2·§3.3 · D-224 ②③④).

**무엇을 하나.** 선행 조회 결과의 어느 컬럼이 서버 키인지를 **컬럼 이름이 아니라 값**으로
판정하고(호스트명·FQDN·IPv4·IPv6), 시스템 사이 비교에 쓸 정규형을 만든다. 대상 시스템에서 찾은
값과 대조해 일치 등급(link / possible / non_link / ambiguous)을 매긴다.

**왜 이름이 아니라 값인가.** 종전 판정(`query_gen_common.is_server_identity_col` · D-100)은
정해진 컬럼명 목록이라, 이름이 다른 자산관리 결과는 "식별 컬럼 없음"으로 후속 조회가 막혔고
IP는 키 후보에 아예 없었다(plans/102 §1.2 실행 확인). 목록에 컬럼을 더하는 것은 공용 계층에
특정 스키마 리터럴을 넣는 일이라 답이 아니다(D-179) — 이 모듈은 **컬럼명을 모른 채 값만 본다.**

| 값 판정 | 규칙 | 정규형 |
|---|---|---|
| `ipv4`·`ipv6` | `ipaddress.ip_address()` 파싱 성공 | IPv4 그대로 · IPv6 압축 소문자(RFC 5952) |
| `fqdn` | 레이블 2개 이상 · 레이블마다 RFC 1123 · 전체 ≤253 · 영문자 1+ | casefold(RFC 4343) |
| `hostname` | 레이블 1개 · 위 구문 · 영문자 1개 이상 · **2자 이상** | casefold |
| `unknown` | 그 외 — 키로 쓰지 않는다 | — |

FQDN의 단축명은 첫 레이블이다(`short_name`).

- **영문자 1개 이상**: RFC 1123은 숫자만 있는 레이블을 허용하지만 그러면 숫자 ID 컬럼이 키로
  오인된다(D-100 `id` 오수집과 같은 계열).
- **단일 레이블 2자 이상**: 한 글자 이름은 서버 대장에서 쓰이지 않고, 허용하면 `Y`/`N` 플래그나
  `N/A`(구분자로 `N`·`A`가 된다) 같은 자리표시 값이 호스트명 컬럼으로 판정된다.
- **키 계열(family)**: 매니페스트는 `hostname`(hostname·fqdn)과 `ip`(ipv4·ipv6) 두 계열로 키를
  선언한다. 한 컬럼에 단축명과 FQDN이 섞이는 것은 정상이므로 **컬럼 판정은 계열 단위**로 한다.
- **다중값 셀**: `[,;\\s/]+`로 나눠 토큰마다 판정한다 — 한 칸에 IP 여러 개인 행은 IP를
  여러 개 가진다.
- **추측하지 않는다**: 컬럼 표본에서 한 계열이 80% 미만이면 `unknown`, 한 키가 대상의 서로 다른
  엔터티 2개 이상에 걸리면 `ambiguous`로 결과에 넣지 않는다(P4).

계층: domain — 순수 · I/O·LLM 0 · 스키마 리터럴 0.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

#: 값 판정 결과.
KEY_IPV4 = "ipv4"
KEY_IPV6 = "ipv6"
KEY_FQDN = "fqdn"
KEY_HOSTNAME = "hostname"
KEY_UNKNOWN = "unknown"

#: 키 계열 — 매니페스트 `keys[].type`이 받는 값이다.
FAMILY_HOSTNAME = "hostname"
FAMILY_IP = "ip"
KEY_FAMILIES: tuple[str, ...] = (FAMILY_HOSTNAME, FAMILY_IP)

#: 매칭 등급 (Fellegi–Sunter 3분 + 모호 — plans/102 §3.3-④).
GRADE_LINK = "link"
GRADE_POSSIBLE = "possible"
GRADE_NON_LINK = "non_link"
GRADE_AMBIGUOUS = "ambiguous"

#: 컬럼 계열 판정 표본 상한·임계(plans/102 §3.2 — P5).
COLUMN_SAMPLE_SIZE = 50
COLUMN_TYPE_THRESHOLD = 0.8

_FAMILY_OF: dict[str, str] = {
    KEY_HOSTNAME: FAMILY_HOSTNAME,
    KEY_FQDN: FAMILY_HOSTNAME,
    KEY_IPV4: FAMILY_IP,
    KEY_IPV6: FAMILY_IP,
}

_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
_HAS_LETTER_RE = re.compile(r"[A-Za-z]")
_CELL_SPLIT_RE = re.compile(r"[,;\s/]+")
_MAX_NAME_LEN = 253


# ──────────────────────────────────────────────
# 값 판정 · 정규화
# ──────────────────────────────────────────────

def classify_value(value: Any) -> str:
    """값 하나(토큰)의 키 타입을 판정한다. 셀 분해는 하지 않는다(`typed_tokens` 참조)."""
    if value is None or isinstance(value, bool):
        return KEY_UNKNOWN
    text = str(value).strip()
    if not text:
        return KEY_UNKNOWN
    try:
        addr = ipaddress.ip_address(text)
    except ValueError:
        addr = None
    if addr is not None:
        return KEY_IPV4 if addr.version == 4 else KEY_IPV6
    name = text[:-1] if text.endswith(".") else text  # 절대 표기의 끝 점 하나 허용
    if not name or len(name) > _MAX_NAME_LEN or not _HAS_LETTER_RE.search(name):
        return KEY_UNKNOWN
    labels = name.split(".")
    if not all(_LABEL_RE.match(label) for label in labels):
        return KEY_UNKNOWN
    if len(labels) == 1:
        return KEY_HOSTNAME if len(name) >= 2 else KEY_UNKNOWN
    return KEY_FQDN


def family_of(key_type: str) -> str | None:
    """값 판정 → 키 계열(`hostname`·`ip`). `unknown`이면 None."""
    return _FAMILY_OF.get(key_type)


def normalize_value(value: Any, key_type: str | None = None) -> str:
    """비교용 정규형. 타입을 모르면 판정부터 한다. `unknown`이면 빈 문자열."""
    kt = key_type or classify_value(value)
    text = str(value).strip() if value is not None else ""
    if kt in (KEY_IPV4, KEY_IPV6):
        try:
            return ipaddress.ip_address(text).compressed.lower()
        except ValueError:
            return ""
    if kt in (KEY_HOSTNAME, KEY_FQDN):
        return (text[:-1] if text.endswith(".") else text).casefold()
    return ""


def short_name(normalized_host: str) -> str:
    """정규화된 호스트명의 단축명(첫 레이블). 단일 레이블이면 그대로."""
    return normalized_host.split(".", 1)[0] if normalized_host else ""


def is_short_name_match(a: str, b: str) -> bool:
    """정규화된 두 호스트명이 **FQDN ↔ 단일 레이블** 단축명으로만 일치하는가(`possible`).

    양쪽이 모두 FQDN이면 도메인이 다른 별개 호스트다 — 단축명이 같아도 일치로 보지 않는다
    (다른 도메인의 같은 단축명 충돌 — plans/102 §6).
    """
    if not a or not b or a == b or ("." in a and "." in b):
        return False
    return short_name(a) == short_name(b)


@dataclass(frozen=True)
class TypedKey:
    """판정·정규화된 키 토큰 하나."""

    key_type: str
    normalized: str
    raw: str

    @property
    def family(self) -> str | None:
        return family_of(self.key_type)


def split_cell(value: Any) -> list[str]:
    """다중값 셀을 토큰으로 나눈다(X-T6). 빈 토큰은 버린다."""
    if value is None or isinstance(value, bool):
        return []
    return [t for t in _CELL_SPLIT_RE.split(str(value).strip()) if t]


def typed_tokens(value: Any) -> list[TypedKey]:
    """셀을 나눠 **키로 쓸 수 있는** 토큰만 판정·정규화해 돌려준다(`unknown` 제외)."""
    out: list[TypedKey] = []
    for token in split_cell(value):
        kt = classify_value(token)
        if kt == KEY_UNKNOWN:
            continue
        out.append(TypedKey(key_type=kt, normalized=normalize_value(token, kt), raw=token))
    return out


def cell_family(value: Any) -> str | None:
    """셀의 키 계열. 토큰이 전부 같은 계열일 때만 그 계열, 하나라도 섞이거나 판정 불가면 None."""
    tokens = split_cell(value)
    if not tokens:
        return None
    families = {family_of(classify_value(t)) for t in tokens}
    if len(families) != 1 or None in families:
        return None
    return next(iter(families))


# ──────────────────────────────────────────────
# 컬럼 계열 판정 (P5)
# ──────────────────────────────────────────────

def classify_column(
    values: Iterable[Any],
    *,
    sample_size: int = COLUMN_SAMPLE_SIZE,
    threshold: float = COLUMN_TYPE_THRESHOLD,
) -> str | None:
    """비어 있지 않은 값 최대 `sample_size`개를 보고 컬럼의 키 계열을 판정한다.

    한 계열이 표본의 `threshold` 이상이면 그 계열, 미달이면 None — 추측하지 않는다.
    """
    sampled = 0
    counts: dict[str, int] = {}
    for value in values:
        if value is None or str(value).strip() == "":
            continue
        sampled += 1
        fam = cell_family(value)
        if fam:
            counts[fam] = counts.get(fam, 0) + 1
        if sampled >= sample_size:
            break
    if not sampled:
        return None
    for fam, n in counts.items():
        if n / sampled >= threshold:
            return fam
    return None


@dataclass(frozen=True)
class KeyColumn:
    """선행 결과에서 키로 인정한 컬럼 하나."""

    column: str
    family: str
    #: 이름 힌트(호출부가 넘긴 판정)와 값 계열이 함께 성립하면 True — 강한 키.
    strong: bool
    distinct: int
    #: 출처 DB 매니페스트가 **키로 선언한** 컬럼인가 — 값·이름 휴리스틱보다 앞선다.
    declared: bool = False


def detect_key_columns(
    rows: Sequence[Mapping[str, Any]],
    *,
    name_hint: Any | None = None,
    exclude: Iterable[str] = (),
    declared: Iterable[str] = (),
) -> list[KeyColumn]:
    """행 목록에서 값으로 키 컬럼을 찾아 우선순으로 돌려준다.

    순서: **선언 키**(출처 DB 매니페스트) → 강한 키(이름 힌트 ∧ 값 계열) → 서로 다른 값 수
    내림차순 → 첫 행의 컬럼 순서. 같은 값만 반복되는 분류성 컬럼이 서버 키를 밀어내지 않게
    하려는 순서다.

    **선언이 먼저인 이유**: 값 판정은 영문자가 든 단일 레이블을 전부 호스트명 계열로 받으므로
    코드값(`Z99`)·심각도(`critical`)·OS명 컬럼도 키 후보가 된다. 출처 DB가 "이것이 서버 키다"
    라고 선언했으면 추측보다 그 선언이 이긴다. 선언이 없을 때만 값·이름 휴리스틱으로 내려간다.

    Args:
        rows: 선행 결과 행
        name_hint: `(column) -> bool` — 이름만으로 서버 식별 컬럼인가(호출부의 D-100 판정).
            도메인은 이름 규칙을 모른다(스키마 리터럴 0).
        exclude: 판정에서 뺄 컬럼(출처 태그 등)
        declared: 출처 DB 매니페스트가 선언한 키 컬럼(대소문자 무시)
    """
    dict_rows = [r for r in rows if isinstance(r, Mapping)]
    if not dict_rows:
        return []
    skip = set(exclude)
    declared_lower = {str(c).lower() for c in declared}
    columns: list[str] = []
    for row in dict_rows:
        for col in row.keys():
            if col not in skip and col not in columns:
                columns.append(col)
    found: list[tuple[int, KeyColumn]] = []
    for position, col in enumerate(columns):
        values = [row.get(col) for row in dict_rows]
        fam = classify_column(values)
        if fam is None:
            continue
        distinct = len({
            tk.normalized for v in values for tk in typed_tokens(v) if tk.family == fam
        })
        strong = bool(name_hint(col)) if callable(name_hint) else False
        found.append((
            position,
            KeyColumn(
                column=str(col), family=fam, strong=strong, distinct=distinct,
                declared=str(col).lower() in declared_lower,
            ),
        ))
    found.sort(
        key=lambda item: (not item[1].declared, not item[1].strong, -item[1].distinct, item[0])
    )
    return [kc for _, kc in found]


def collect_family_values(
    rows: Sequence[Mapping[str, Any]], column: str, family: str,
) -> list[TypedKey]:
    """한 컬럼에서 해당 계열 토큰을 정규형 기준 중복 없이 행 순서대로 모은다."""
    seen: set[str] = set()
    out: list[TypedKey] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        for tk in typed_tokens(row.get(column)):
            if tk.family != family or tk.normalized in seen:
                continue
            seen.add(tk.normalized)
            out.append(tk)
    return out


# ──────────────────────────────────────────────
# 키 매니페스트 (plans/102 §3.3-① — 데이터 스키마만. 파일 로드는 infrastructure)
# ──────────────────────────────────────────────

#: 행 다중도 — 한 엔터티가 대상 테이블에서 여러 행을 가질 수 있는가(X-T5).
ROW_MULTIPLICITY_ONE = "one"
ROW_MULTIPLICITY_PER_IP = "per_ip"


class ManifestError(ValueError):
    """매니페스트 구조 오류."""


@dataclass(frozen=True)
class ManifestKey:
    """대상 DB가 받는 키 하나 — 계열·컬럼·우선순위·비교 방식."""

    family: str
    column: str
    priority: int
    #: `casefold`(대소문자 무시) · `exact`. IP 계열은 정규형 비교라 이 값의 영향이 없다.
    compare: str = "casefold"
    #: 한 칸에 값이 여러 개일 수 있는가(X-T6) — SQL은 넓게 좁히고 코드가 토큰으로 확정한다.
    multi_value: bool = False


@dataclass(frozen=True)
class EntityKeyManifest:
    """DB 하나의 서버 엔터티 키 선언(`entity_keys` 블록)."""

    db_id: str
    entity: str
    table: str
    keys: tuple[ManifestKey, ...] = ()
    row_multiplicity: str = ROW_MULTIPLICITY_ONE
    #: 선언 출처(파일 경로) — 경과 노트·로그 표기용.
    source: str = ""

    def keys_by_priority(self) -> tuple[ManifestKey, ...]:
        return tuple(sorted(self.keys, key=lambda k: (k.priority, k.column)))

    def key_for(self, family: str) -> ManifestKey | None:
        """해당 계열의 최우선 키."""
        for key in self.keys_by_priority():
            if key.family == family:
                return key
        return None

    def families(self) -> tuple[str, ...]:
        """받는 계열을 우선순으로(중복 제거)."""
        out: list[str] = []
        for key in self.keys_by_priority():
            if key.family not in out:
                out.append(key.family)
        return tuple(out)


def parse_manifest(db_id: str, data: Any, *, source: str = "") -> EntityKeyManifest:
    """`entity_keys` 블록(dict)을 매니페스트로 만든다. 구조가 틀리면 `ManifestError`.

    조용히 일부만 받아들이지 않는다 — 키 대응이 틀린 매니페스트는 0건을 **정상 결과처럼** 만든다.
    """
    if not isinstance(data, Mapping):
        raise ManifestError(f"{db_id}: entity_keys는 매핑이어야 합니다")
    entity = str(data.get("entity") or "").strip()
    table = str(data.get("table") or "").strip()
    if not entity or not table:
        raise ManifestError(f"{db_id}: entity_keys.entity·table은 필수입니다")
    raw_keys = data.get("keys")
    if not isinstance(raw_keys, list) or not raw_keys:
        raise ManifestError(f"{db_id}: entity_keys.keys가 비었습니다")
    keys: list[ManifestKey] = []
    for i, raw in enumerate(raw_keys):
        if not isinstance(raw, Mapping):
            raise ManifestError(f"{db_id}: entity_keys.keys[{i}]는 매핑이어야 합니다")
        fam = str(raw.get("type") or "").strip()
        col = str(raw.get("column") or "").strip()
        if fam not in KEY_FAMILIES:
            raise ManifestError(
                f"{db_id}: entity_keys.keys[{i}].type은 {KEY_FAMILIES} 중 하나입니다: {fam!r}"
            )
        if not col:
            raise ManifestError(f"{db_id}: entity_keys.keys[{i}].column이 비었습니다")
        compare = str(raw.get("compare") or "casefold").strip()
        if compare not in ("casefold", "exact"):
            raise ManifestError(
                f"{db_id}: entity_keys.keys[{i}].compare는 casefold|exact입니다: {compare!r}"
            )
        try:
            priority = int(raw.get("priority", i + 1))
        except (TypeError, ValueError) as exc:
            raise ManifestError(f"{db_id}: entity_keys.keys[{i}].priority는 정수입니다") from exc
        keys.append(ManifestKey(
            family=fam, column=col, priority=priority, compare=compare,
            multi_value=bool(raw.get("multi_value", False)),
        ))
    multiplicity = str(data.get("row_multiplicity") or ROW_MULTIPLICITY_ONE).strip()
    if multiplicity not in (ROW_MULTIPLICITY_ONE, ROW_MULTIPLICITY_PER_IP):
        raise ManifestError(
            f"{db_id}: entity_keys.row_multiplicity는 one|per_ip입니다: {multiplicity!r}"
        )
    return EntityKeyManifest(
        db_id=db_id, entity=entity, table=table, keys=tuple(keys),
        row_multiplicity=multiplicity, source=source,
    )


# ──────────────────────────────────────────────
# 매칭 등급 (P3 · plans/102 §3.3-④⑤)
# ──────────────────────────────────────────────

@dataclass(frozen=True)
class TargetEntity:
    """대상 시스템에서 찾은 엔터티 하나 — 같은 엔터티의 여러 행(IP별 행)은 호출부가 묶어 넘긴다."""

    entity_id: str
    #: 이 엔터티가 가진 해당 계열 키의 정규형 전부
    keys: frozenset[str]


@dataclass(frozen=True)
class MatchReport:
    """선행 키 N개의 등급별 판정. 합계는 항상 N이다(D5 센서)."""

    family: str
    link: dict[str, tuple[str, ...]] = field(default_factory=dict)
    possible: dict[str, tuple[str, ...]] = field(default_factory=dict)
    non_link: tuple[str, ...] = ()
    ambiguous: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return len(self.link) + len(self.possible) + len(self.non_link) + len(self.ambiguous)

    @property
    def coverage(self) -> float:
        """(일치 + 가능한 일치) / N — 키 브리지 커버리지(P6). N=0이면 0.0."""
        return (len(self.link) + len(self.possible)) / self.total if self.total else 0.0

    def included_entities(self, *, include_possible: bool = True) -> tuple[str, ...]:
        """결과에 포함할 대상 엔터티 — link(+possible). ambiguous는 넣지 않는다."""
        out: list[str] = []
        groups = [self.link] + ([self.possible] if include_possible else [])
        for group in groups:
            for ids in group.values():
                for eid in ids:
                    if eid not in out:
                        out.append(eid)
        return tuple(out)


def grade_matches(
    source_keys: Sequence[str],
    targets: Sequence[TargetEntity],
    *,
    family: str,
) -> MatchReport:
    """선행 키(정규형) 각각을 대상 엔터티와 대조해 등급을 매긴다.

    - link: 정규형 완전 일치(호스트명 casefold · IP 토큰)
    - possible: 호스트 계열에서 단축명끼리만 일치(FQDN ↔ 단일 레이블 등 — G-2 기본 가정)
    - ambiguous: 같은 등급에서 **서로 다른 엔터티 2개 이상**에 걸림 → 결과에 넣지 않는다
    - non_link: 어디에도 없음

    완전 일치가 하나라도 있으면 단축명 후보는 보지 않는다(더 강한 증거가 이긴다).
    """
    link: dict[str, tuple[str, ...]] = {}
    possible: dict[str, tuple[str, ...]] = {}
    ambiguous: dict[str, tuple[str, ...]] = {}
    non_link: list[str] = []
    seen: set[str] = set()
    for key in source_keys:
        if not key or key in seen:
            continue
        seen.add(key)
        exact = tuple(dict.fromkeys(t.entity_id for t in targets if key in t.keys))
        if exact:
            (ambiguous if len(exact) > 1 else link)[key] = exact
            continue
        if family == FAMILY_HOSTNAME:
            loose = tuple(dict.fromkeys(
                t.entity_id for t in targets if any(is_short_name_match(key, k) for k in t.keys)
            ))
            if loose:
                (ambiguous if len(loose) > 1 else possible)[key] = loose
                continue
        non_link.append(key)
    return MatchReport(
        family=family, link=link, possible=possible, non_link=tuple(non_link), ambiguous=ambiguous,
    )


def render_match_note(
    report: MatchReport, *, target_label: str, key_label: str, sample: int = 10,
) -> str:
    """경과 노트 한 줄.

    *"선행 N대 → <대상>: 일치 a · 가능 b · 미발견 c(샘플 …) · 모호 d · 키=hostname"*
    """
    parts = [
        f"일치 {len(report.link)}",
        f"가능 {len(report.possible)}",
    ]
    miss = f"미발견 {len(report.non_link)}"
    if report.non_link:
        miss += f"({', '.join(report.non_link[:sample])})"
    parts.append(miss)
    amb = f"모호 {len(report.ambiguous)}"
    if report.ambiguous:
        amb += f"({', '.join(list(report.ambiguous)[:sample])})"
    parts.append(amb)
    return f"선행 {report.total}대 → {target_label}: " + " · ".join(parts) + f" · 키={key_label}"
