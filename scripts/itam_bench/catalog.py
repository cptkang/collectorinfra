"""시나리오·컬럼 정책 로더 · 프롬프트 린트 · 스키마 카탈로그 (plans/135 §3.2·§3.4·§3.5.1 · W0·W1).

시나리오는 **사용자 프롬프트**다(D-301 ①). 로더가 린트로 막는 것:
  - 테이블·컬럼 식별자 · SQL 용어(사용자는 그런 말을 치지 않는다 — 넣으면 「ITAM 프롬프트가 사용자
  말을
    알아듣는가」를 재지 못한다)
  - 사람 이름(카나리아)·사번 모양·연락처 류 — 프롬프트는 로그에 그대로 남는 개인정보 통로다
  - 첫 턴 요청 본문의 대상 DB·스키마 지정(`query` 외 키)

컬럼 정책은 **기본 거부**다 — 정책 파일에 없는 컬럼은 `unclassified`이고 `general`은 정책 파일로만
준다. 휴리스틱(`pii_suggestion`)은 정책 밖 컬럼을 `pii` 쪽으로만 올린다(§3.5.1).

스키마 카탈로그는 조회문이 아니라 구조화 YAML 이다(D-301 ③). 벤치는 스키마를 얻으려고 DB에 조회문을
따로 날리지 않는다 — 입력은 파일 스키마 캐시 · `scripts/itam_erd.py` 스냅숏 · 샌드박스 전사본 ·
「DB 구조」 탭 저장소(`structure_store` — 스냅샷·DDL 주석·최신 P1 초안 · plans/140 W2 · D-311 ①)
중 하나다. P1 초안의 값(코드값·라벨)은 메모리에만 두고 카탈로그에는 비율·수·판정만 싣는다.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from itertools import zip_longest
from pathlib import Path
from typing import Any

import yaml

from . import DB_ID, REPO_ROOT

#: 기록 등급 — 엄격도 오름차순(여럿이면 가장 엄격한 것을 쓴다 · §3.5.1 「결과 열 → 원 컬럼 해석」).
#: `identifier`(plans/149 W5 · G-1 (b)) — 호스트명·ID 같은 식별자 칸. 값은 run 일관 가짜 값이고
#: 원값은 사람 값처럼 vault 에 모아 관문이 훑는다. 별칭·판정 키(`oracle.key`)로는 쓸 수 있다.
#: 값 쪽이 `unclassified`보다 엄격(수집 + 치환)해 그 위다 — 관계 등가류(`effective_policy`)에서
#: 정책 밖 같은 키 칸(다른 테이블의 `서버호스트명`)도 `identifier`로 올라가 조인 양쪽이 같은
#: 등급이다.
GRADES: tuple[str, ...] = (
    "general",
    "network",
    "amount",
    "free_text",
    "unclassified",
    "identifier",
    "pii",
)
#: 판정 키로 쓸 수 있는 등급 — 상세의 `identifier` 키 값은 가짜 값으로 나간다
#: (`judge.sanitize_detail`).
KEY_GRADES: frozenset[str] = frozenset({"general", "identifier"})
GRADE_RANK: dict[str, int] = {grade: rank for rank, grade in enumerate(GRADES)}
#: 정책 파일에 적을 수 있는 등급(`unclassified`는 「정책에 없음」의 뜻이라 적지 않는다).
POLICY_GRADES: frozenset[str] = frozenset(GRADES) - {"unclassified"}

ENVS: frozenset[str] = frozenset({"sandbox", "closed"})
FIRST_TURN_SEND_KEYS: frozenset[str] = frozenset({"query"})
LATER_TURN_SEND_KEYS: frozenset[str] = frozenset({"query", "selected_db_ids", "selected_sources"})
REPLY_SEND_KEYS: frozenset[str] = frozenset({"selected_db_ids", "selected_sources"})
TURN_KEYS: frozenset[str] = frozenset({"send", "expect", "reply_to"})
EXPECT_KEYS: frozenset[str] = frozenset(
    {
        "db_ids",
        "oracle",
        "observe",
        "count_rows_ok",
        "key_columns",
        "gold_tables",
    }
)
SCENARIO_KEYS: frozenset[str] = frozenset(
    {
        "id",
        "title",
        "category",
        "env",
        "traps",
        "key_columns",
        "gold_tables",
        "turns",
    }
)
_ID_RE = re.compile(r"^ITAM-[0-9]{2,3}[A-Za-z0-9-]*$")

#: 프롬프트 린트 — SQL 용어. 영문은 단어 경계, 한글은 부분 일치(조사가 붙는다).
_SQL_TERMS_EN = re.compile(
    r"(?i)(?<![A-Za-z])(select|where|join|group\s+by|order\s+by|limit|union|having|sql)(?![A-Za-z])"
)
_SQL_TERMS_KO = ("조인", "컬럼", "칼럼", "테이블", "쿼리", "스키마")
#: 사번·사용자번호 모양(영문 1자 + 숫자 6~7 · 숫자 7자리 단독) — 프롬프트에 사람 식별자를 넣지
#: 않는다.
_PERSON_ID_RE = re.compile(r"(?<![A-Za-z0-9])(?:[A-Za-z]\d{6,7}|\d{7})(?![A-Za-z0-9])")

#: 개인정보 휴리스틱(§3.5.1 ②) — 이름은 **토큰 단위**로만 본다(부분 문자열 금지 · §2.2 L5).
_PII_NAME_TOKENS = frozenset({"emnm", "empid", "uno", "email", "phone", "tel", "addr", "rrn"})
_PII_COMMENT_WORDS = (
    "성명",
    "이름",
    "직원",
    "사번",
    "사용자번호",
    "담당자",
    "연락처",
    "전화",
    "휴대",
    "이메일",
    "주소",
    "주민",
    "생년",
)

#: 스키마 절 금지 형식(§3.4 · D-301 ③) — 누출 관문 ④와 같은 목록이다.
SCHEMA_QUERY_FORMS = re.compile(
    r"(?i)create\s+table|show\s+create|show\s+columns|information_schema|\bdescribe\b"
)


class CatalogError(ValueError):
    """로드 실패 — 사유 목록을 함께 싣는다."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("\n".join(errors))
        self.errors = errors


def strictest(grades: Iterable[str]) -> str:
    """가장 엄격한 등급. 비었으면 `unclassified`."""
    found = [grade for grade in grades if grade in GRADE_RANK]
    return max(found, key=GRADE_RANK.__getitem__) if found else "unclassified"


def name_tokens(name: str) -> list[str]:
    """camelCase·snake_case 이름 → 소문자 토큰.

    `sysRegiUno` → sys·regi·uno · `hWSportEndYmd` → h·w·sport·end·ymd.
    """
    tokens: list[str] = []
    for part in re.split(r"[_\W]+", str(name)):
        tokens.extend(re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z0-9]+|[A-Z]+", part))
    return [token.lower() for token in tokens if token]


def pii_suggestion(name: str, comment: str = "") -> bool:
    """정책 밖 컬럼이 사람 정보로 보이는가(올리기 전용 제안 — `general`은 만들지 않는다)."""
    if _PII_NAME_TOKENS & set(name_tokens(name)):
        return True
    return any(word in (comment or "") for word in _PII_COMMENT_WORDS)


# --- 컬럼 정책 ------------------------------------------------------------------


@dataclass(frozen=True)
class ColumnPolicy:
    """검토된 컬럼 기록 등급 + 누출 카나리아(§3.5.1 · §3.5.5 ①)."""

    db_id: str
    scope: str
    #: 테이블 → {컬럼(원래 표기): 등급}
    tables: Mapping[str, Mapping[str, str]]
    canary_literals: tuple[str, ...] = ()
    canary_patterns: tuple[re.Pattern[str], ...] = ()

    def _by_folded(self) -> dict[str, dict[str, str]]:
        index: dict[str, dict[str, str]] = {}
        for table, columns in self.tables.items():
            for column, grade in columns.items():
                index.setdefault(column.casefold(), {})[table.casefold()] = grade
        return index

    def grade(self, column: str, table: str | None = None) -> str:
        """컬럼 등급 — 컬럼 이름은 대소문자 무시(MariaDB 컬럼 이름 규칙).

        테이블을 모르면 그 이름을 가진 컬럼 중 가장 엄격한 등급이다.
        """
        by_table = self._by_folded().get(str(column).casefold())
        if not by_table:
            return "unclassified"
        if table is not None:
            bare = str(table).rsplit(".", 1)[-1].casefold()
            if bare in by_table:
                return by_table[bare]
        return strictest(by_table.values())

    def column_names(self) -> set[str]:
        return {column for columns in self.tables.values() for column in columns}

    def table_names(self) -> set[str]:
        return set(self.tables)

    def canary_hits(self, text: str) -> int:
        """텍스트 안 카나리아 건수(값은 돌려주지 않는다)."""
        if not text:
            return 0
        hits = sum(text.count(literal) for literal in self.canary_literals if literal)
        return hits + sum(len(pattern.findall(text)) for pattern in self.canary_patterns)


def load_policy(path: Path) -> ColumnPolicy:
    """`column_policy.yaml` → `ColumnPolicy`. 모르는 등급·같은 테이블 안 중복 컬럼은 거부한다."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    errors: list[str] = []
    tables: dict[str, dict[str, str]] = {}
    for table, grades in (data.get("tables") or {}).items():
        if not isinstance(grades, dict):
            errors.append(f"{table}: 등급 → 컬럼 목록 매핑이어야 한다")
            continue
        columns: dict[str, str] = {}
        for grade, names in grades.items():
            if grade not in POLICY_GRADES:
                errors.append(f"{table}: 모르는 등급 {grade!r} — 허용 {sorted(POLICY_GRADES)}")
                continue
            for name in names or []:
                if str(name).casefold() in {c.casefold() for c in columns}:
                    errors.append(f"{table}.{name}: 등급이 두 번 적혔다")
                    continue
                columns[str(name)] = grade
        tables[str(table)] = columns
    canaries = data.get("canaries") or {}
    patterns: list[re.Pattern[str]] = []
    for raw in canaries.get("patterns") or []:
        try:
            patterns.append(re.compile(str(raw)))
        except re.error as exc:
            errors.append(f"카나리아 패턴 {raw!r} 컴파일 실패: {exc}")
    if errors:
        raise CatalogError(errors)
    return ColumnPolicy(
        db_id=str(data.get("db_id") or DB_ID),
        scope=str(data.get("scope") or ""),
        tables=tables,
        canary_literals=tuple(str(x) for x in canaries.get("literals") or []),
        canary_patterns=tuple(patterns),
    )


# --- 프롬프트 린트 ---------------------------------------------------------------


def lint_prompt(text: str, *, identifiers: Iterable[str], policy: ColumnPolicy) -> list[str]:
    """사용자 프롬프트 위반 사유(빈 목록 = 통과 · §3.2.2).

    걸린 값 자체는 사유에 싣지 않는다(사람 정보).

    비ASCII 이름(운영 한글 컬럼 `취득금액`·`서버호스트명`)은 사용자 말과 같은 낱말이라 산문 속
    부분 문자열로는 잡지 않고, 코드 꼴(백틱 `` `이름` `` · 한정 `표.이름`·`이름.칸`)일 때만 잡는다
    (plans/149 W5). ASCII 이름(`sevrHostName`)은 종전대로 낱말 경계 일치다.
    """
    from src.security.pii_filter import scan_pii

    problems: list[str] = []
    for name in sorted({str(n) for n in identifiers if n}, key=str.casefold):
        escaped = re.escape(name)
        pattern = (
            rf"(?i)(?<![A-Za-z0-9_]){escaped}(?![A-Za-z0-9_])"
            if name.isascii()
            else rf"`{escaped}`|(?<=\w\.){escaped}(?!\w)|(?<!\w){escaped}(?=\.\w)"
        )
        if re.search(pattern, text):
            problems.append(f"테이블·컬럼 식별자 `{name}` — 사용자 말로 바꾼다")
    for match in _SQL_TERMS_EN.finditer(text):
        problems.append(f"SQL 용어 `{match.group(1)}`")
    for word in _SQL_TERMS_KO:
        if word in text:
            problems.append(f"SQL 용어 `{word}`")
    if policy.canary_hits(text):
        problems.append("사람 이름·식별자(카나리아) — 프롬프트에 사람 정보를 넣지 않는다")
    if _PERSON_ID_RE.search(text):
        problems.append("사번·사용자번호 모양 — 프롬프트에 사람 식별자를 넣지 않는다")
    rules = sorted({hit.name for hit in scan_pii(text, unmask=False)})
    if rules:
        problems.append(f"개인정보 규칙 {rules} — 프롬프트에 넣지 않는다")
    return problems


# --- 시나리오 -------------------------------------------------------------------


@dataclass(frozen=True)
class Turn:
    index: int  # 1부터
    send: Mapping[str, Any]
    expect: Mapping[str, Any]
    reply_to: str | None = None

    @property
    def query(self) -> str:
        return str(self.send.get("query") or "")

    @property
    def oracle(self) -> dict[str, Any] | None:
        spec = self.expect.get("oracle")
        return dict(spec) if isinstance(spec, dict) else None

    @property
    def observe(self) -> dict[str, Any] | None:
        spec = self.expect.get("observe")
        return dict(spec) if isinstance(spec, dict) else None

    @property
    def judged(self) -> bool:
        return self.oracle is not None or self.observe is not None


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    category: str
    env: tuple[str, ...]
    traps: tuple[str, ...]
    key_columns: tuple[Any, ...]
    gold_tables: tuple[str, ...]
    turns: tuple[Turn, ...] = field(default_factory=tuple)

    def key_columns_for(self, turn: Turn) -> list[list[str]]:
        """턴의 핵심 컬럼 — 항목마다 「같은 뜻」 후보 목록.

        턴 `expect.key_columns`가 시나리오 값을 덮는다.
        """
        refs = turn.expect.get("key_columns", self.key_columns)
        return [[str(ref)] if isinstance(ref, str) else [str(x) for x in ref] for ref in refs or []]

    def gold_tables_for(self, turn: Turn) -> list[str]:
        return [str(t) for t in turn.expect.get("gold_tables", self.gold_tables) or []]


def _ref_names(ref: Any) -> list[str]:
    if isinstance(ref, str):
        return [ref]
    if isinstance(ref, list):
        return [str(name) for name in ref]
    return []


def _check_oracle(
    spec: dict[str, Any], *, where: str, db_ids: list[str] | None, policy: ColumnPolicy
) -> list[str]:
    """하네스 명세 검사(`validate_oracle_spec`) + 벤치 규칙.

    키 = general|identifier(`KEY_GRADES`) · 값 = general|amount.
    """
    from scripts.scenario.oracle import validate_oracle_spec

    errors = [
        f"{where} oracle: {e}" for e in validate_oracle_spec(spec, scenario_id=where, db_ids=db_ids)
    ]
    folded = {name.casefold(): name for name in policy.column_names()}
    for ref in spec.get("key") or []:
        known = [folded[n.casefold()] for n in _ref_names(ref) if n.casefold() in folded]
        if not known:
            errors.append(
                f"{where} oracle.key {ref!r}: 카탈로그 컬럼 이름이 하나도 없다 — 키를 컬럼에 묶는다"
            )
        for name in known:
            if policy.grade(name) not in KEY_GRADES:
                errors.append(
                    f"{where} oracle.key: `{name}`은 {policy.grade(name)} 등급 — 키는 "
                    "general·identifier만(불일치 상세가 키 값을 싣는다 · plans/135 §3.5.6 · "
                    "identifier 키 값은 가짜 값 · plans/149 W5)"
                )
    if "value" in spec:
        for name in _ref_names(spec["value"]):
            if name.casefold() in folded and policy.grade(name) not in ("general", "amount"):
                errors.append(
                    f"{where} oracle.value: `{name}`은 {policy.grade(name)} 등급 — "
                    "값은 general·amount만"
                )
    return errors


def _check_turn(
    scenario_id: str, raw: Any, index: int, *, identifiers: set[str], policy: ColumnPolicy
) -> tuple[Turn | None, list[str]]:
    where = f"{scenario_id} 턴{index}"
    if not isinstance(raw, dict):
        return None, [f"{where}: 매핑이어야 한다"]
    errors = [f"{where}: 정의 밖 키 {sorted(set(raw) - TURN_KEYS)}"] if set(raw) - TURN_KEYS else []
    send = raw.get("send") or {}
    expect = raw.get("expect") or {}
    if not isinstance(send, dict) or not isinstance(expect, dict):
        return None, errors + [f"{where}: send·expect 는 매핑이어야 한다"]
    allowed = FIRST_TURN_SEND_KEYS if index == 1 else LATER_TURN_SEND_KEYS
    if set(send) - allowed:
        errors.append(
            f"{where}: 요청 본문 키 {sorted(set(send) - allowed)} — 첫 턴은 query 만"
            "(대상 DB·스키마 고정 주입 금지 · D-301 ①)"
        )
    if REPLY_SEND_KEYS & set(send) and raw.get("reply_to") != "clarification":
        errors.append(f"{where}: selected_* 는 되묻기 응답 턴(reply_to: clarification)에서만")
    if index == 1 and not str(send.get("query") or "").strip():
        errors.append(f"{where}: 첫 턴 query 가 비었다")
    if send.get("query"):
        errors += [
            f"{where} 프롬프트: {p}"
            for p in lint_prompt(str(send["query"]), identifiers=identifiers, policy=policy)
        ]
    if set(expect) - EXPECT_KEYS:
        errors.append(f"{where}: expect 정의 밖 키 {sorted(set(expect) - EXPECT_KEYS)}")
    if "oracle" in expect and "observe" in expect:
        errors.append(f"{where}: oracle 과 observe 를 함께 쓰지 않는다")
    db_ids = expect.get("db_ids")
    if db_ids is not None and not (
        isinstance(db_ids, list) and all(isinstance(d, str) for d in db_ids)
    ):
        errors.append(f"{where}: db_ids 는 문자열 목록이다")
    if isinstance(expect.get("oracle"), dict):
        errors += _check_oracle(
            expect["oracle"],
            where=where,
            db_ids=list(expect["oracle"].get("db_ids") or db_ids or []) or None,
            policy=policy,
        )
    elif "oracle" in expect:
        errors.append(f"{where}: oracle 은 매핑이다")
    observe = expect.get("observe")
    if observe is not None and not (
        isinstance(observe, dict)
        and str(observe.get("what") or "").strip()
        and isinstance(observe.get("no_data", False), bool)
    ):
        errors.append(f"{where}: observe 는 {{what: 문자열, no_data: 불리언}} 이다")
    if "count_rows_ok" in expect and not (
        isinstance(expect["count_rows_ok"], bool)
        and (expect.get("oracle") or {}).get("compare") == "value"
    ):
        errors.append(f"{where}: count_rows_ok 는 compare=value 오라클에만 쓰는 불리언이다")
    turn = Turn(index=index, send=dict(send), expect=dict(expect), reply_to=raw.get("reply_to"))
    return turn, errors


def _check_columns(where: str, refs: Any, policy: ColumnPolicy, known: set[str]) -> list[str]:
    if refs is None:
        return []
    if not isinstance(refs, list):
        return [f"{where}: key_columns 는 목록이다"]
    folded = {c.casefold() for c in known}
    errors = []
    for ref in refs:
        for name in _ref_names(ref) or [repr(ref)]:
            if name.casefold() not in folded:
                errors.append(f"{where}: key_columns 의 `{name}` 은 카탈로그에 없다")
    return errors


def load_scenarios(
    path: Path, policy: ColumnPolicy, *, identifiers: Iterable[str] | None = None
) -> list[Scenario]:
    """`scenarios.yaml` → 시나리오 목록. 위반이 하나라도 있으면 `CatalogError`(사유 전부)."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    known_columns = set(policy.column_names())
    idents = set(identifiers) if identifiers is not None else known_columns | policy.table_names()
    known_tables = {t.casefold() for t in policy.table_names()}
    errors: list[str] = []
    scenarios: list[Scenario] = []
    seen: set[str] = set()
    for raw in data.get("scenarios") or []:
        if not isinstance(raw, dict):
            errors.append(f"시나리오 항목이 매핑이 아니다 — {raw!r}")
            continue
        sid = str(raw.get("id") or "")
        if not _ID_RE.match(sid):
            errors.append(f"id {sid!r} — `ITAM-NN` 형식")
        if sid in seen:
            errors.append(f"{sid}: id 중복")
        seen.add(sid)
        if set(raw) - SCENARIO_KEYS:
            errors.append(f"{sid}: 정의 밖 키 {sorted(set(raw) - SCENARIO_KEYS)}")
        env = tuple(str(e) for e in raw.get("env") or [])
        if not env or set(env) - ENVS:
            errors.append(f"{sid}: env 는 {sorted(ENVS)} 의 비지 않은 목록")
        errors += _check_columns(sid, raw.get("key_columns"), policy, known_columns)
        gold = [str(t) for t in raw.get("gold_tables") or []]
        errors += [
            f"{sid}: gold_tables 의 `{t}` 는 카탈로그에 없다"
            for t in gold
            if t.casefold() not in known_tables
        ]
        turns: list[Turn] = []
        raw_turns = raw.get("turns")
        if not isinstance(raw_turns, list) or not raw_turns:
            errors.append(f"{sid}: turns 가 비었다")
            raw_turns = []
        for index, raw_turn in enumerate(raw_turns, start=1):
            turn, turn_errors = _check_turn(sid, raw_turn, index, identifiers=idents, policy=policy)
            errors += turn_errors
            if turn is None:
                continue
            errors += _check_columns(
                f"{sid} 턴{index}", turn.expect.get("key_columns"), policy, known_columns
            )
            errors += [
                f"{sid} 턴{index}: gold_tables 의 `{t}` 는 카탈로그에 없다"
                for t in turn.expect.get("gold_tables") or []
                if str(t).casefold() not in known_tables
            ]
            turns.append(turn)
        if turns and not any(turn.judged for turn in turns):
            errors.append(f"{sid}: oracle·observe 가 있는 턴이 하나도 없다")
        scenarios.append(
            Scenario(
                id=sid,
                title=str(raw.get("title") or ""),
                category=str(raw.get("category") or ""),
                env=env,
                traps=tuple(str(t) for t in raw.get("traps") or []),
                key_columns=tuple(raw.get("key_columns") or []),
                gold_tables=tuple(gold),
                turns=tuple(turns),
            )
        )
    if not scenarios and not errors:
        errors.append("시나리오가 0건이다")
    if errors:
        raise CatalogError(errors)
    return scenarios


def select(
    scenarios: list[Scenario], *, env: str, only: Iterable[str] | None = None
) -> list[Scenario]:
    """환경·ID 필터. 모르는 ID 를 지목하면 ValueError(조용히 0건이 되지 않게)."""
    wanted = [s.strip() for s in only or [] if s.strip()]
    known = {s.id for s in scenarios}
    missing = [sid for sid in wanted if sid not in known]
    if missing:
        raise ValueError(f"모르는 시나리오 {missing}")
    return [s for s in scenarios if env in s.env and (not wanted or s.id in wanted)]


# --- 스키마 카탈로그 (W1 · §3.4) ---------------------------------------------------

_LEN_RE = re.compile(r"\(\s*(\d+)")


def _length(type_text: str) -> int | None:
    match = _LEN_RE.search(type_text or "")
    return int(match.group(1)) if match else None


def value_kind(
    name: str, type_text: str, *, grade: str = "unclassified", is_key: bool = False
) -> str:
    """원 타입 → 질문·분석에 쓰는 값 종류(§3.4).

    길이를 모르면(캐시 `char`) 이름 토큰으로 보강한다.
    """
    base = (type_text or "").split("(")[0].strip().lower()
    length = _length(type_text)
    tokens = name_tokens(name)
    last = tokens[-1] if tokens else ""
    is_text = base in ("char", "varchar", "nchar", "nvarchar", "character", "text")
    if is_text and last == "ymd" and length in (None, 8):
        return "date_text_yyyymmdd"
    if is_text and last == "ym" and length in (None, 6):
        return "month_text_yyyymm"
    if is_text and last in ("yms", "dttm"):
        return "datetime_text"
    if is_text and last == "yn" and length in (None, 1):
        return "flag_yn"
    if grade == "amount" or last in ("amt", "cnpr"):
        return "amount"
    if base in (
        "decimal",
        "numeric",
        "int",
        "integer",
        "bigint",
        "smallint",
        "tinyint",
        "float",
        "double",
        "real",
        "number",
    ):
        return "number"
    if is_key or last in ("id", "no", "uno", "empid", "uniqno", "serno"):
        return "identifier"
    if is_text and (last.endswith("cd") or last == "dstcd"):
        return "code_text"
    if base == "varchar" or base == "text":
        return "free_text"
    return "text" if is_text else (base or "unknown")


def _foreign_keys_from_cache(
    table_name: str,
    columns: Iterable[Mapping[str, Any]],
    relationships: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """선언 FK — `schema.relationships`와 컬럼 `references`("T.c")의 합집합.

    대상 테이블별로 묶어 `{columns, ref_table, ref_columns}`(`scripts/itam_erd.py` 스냅숏과 같은
    모양)로 돌려준다.
    """
    pairs: dict[str, list[tuple[str, str]]] = {}

    def add(column: str, target: str) -> None:
        ref_table, _, ref_column = str(target).rpartition(".")
        if column and ref_table and ref_column:
            bucket = pairs.setdefault(ref_table, [])
            if (column, ref_column) not in bucket:
                bucket.append((column, ref_column))

    for rel in relationships:
        source, _, column = str(rel.get("from") or "").rpartition(".")
        if source == table_name:
            add(column, str(rel.get("to") or ""))
    for col in columns:
        if col.get("foreign_key") and col.get("references"):
            add(str(col.get("name")), str(col["references"]))
    return [
        {
            "columns": [c for c, _ in found],
            "ref_table": ref_table,
            "ref_columns": [r for _, r in found],
        }
        for ref_table, found in pairs.items()
    ]


def _synonym_counts(synonyms: Any) -> dict[str, int]:
    """유사어 → 컬럼별 **건수만**(낱말은 싣지 않는다). 값은 낱말 목록 또는 `{"words": [...]}`."""
    counts: dict[str, int] = {}
    for key, value in (synonyms or {}).items() if isinstance(synonyms, Mapping) else ():
        words = value.get("words") if isinstance(value, Mapping) else value
        if isinstance(words, (list, tuple)) and words:
            counts[str(key)] = len(words)
    return counts


def _normalize_cache(data: Mapping[str, Any]) -> dict[str, Any]:
    """「DB 구조」 탭이 쓰는 파일 스키마 캐시(`.cache/schema/{db_id}_schema.json`) 전체를 읽는다.

    - 컬럼 `foreign_key`·`references` · `schema.relationships`(선언 FK) → 관계 `declared`
    - `_descriptions`(설명 적용본) → 의미. **DB 주석 유래인지 LLM 생성인지 파일에 출처가 없다**
      (`cache_description` 하나로 표기)
    - `_synonyms`(유사어) → **컬럼별 건수만**. 운영자 등록어는 무엇이든 들어올 수 있고(사람 이름·값
      포함 가능), 프롬프트 개선에 필요한 것은 「그 컬럼에 유사어가 있는가」라서 낱말은 싣지 않는다
    - `_db_description` · `_db_description_origin`(manual·llm) → DB 설명
    - `sample_data`는 읽지 않는다(값)
    """
    schema = data.get("schema") or {}
    relationships = [r for r in schema.get("relationships") or [] if isinstance(r, Mapping)]
    tables: dict[str, dict[str, Any]] = {}
    for name, table in (schema.get("tables") or {}).items():
        raw_columns = [c for c in table.get("columns") or [] if isinstance(c, Mapping)]
        columns = [
            {
                "name": str(col.get("name")),
                "type": str(col.get("type") or ""),
                "nullable": bool(col.get("nullable", True)),
                "is_key": bool(col.get("primary_key", False)),
                "comment": "",
            }
            for col in raw_columns
        ]
        tables[str(name)] = {
            "comment": "",
            "rows_estimate": table.get("row_count_estimate"),
            "primary_key": [c["name"] for c in columns if c["is_key"]],
            "columns": columns,
            "foreign_keys": _foreign_keys_from_cache(str(name), raw_columns, relationships),
        }
    description = data.get("_db_description")
    return {
        "tables": tables,
        "descriptions": dict(data.get("_descriptions") or {}),
        "synonym_counts": _synonym_counts(data.get("_synonyms")),
        "db_description": (
            {"text": str(description), "origin": data.get("_db_description_origin")}
            if description
            else None
        ),
    }


def _normalize_snapshot(data: Mapping[str, Any]) -> dict[str, Any]:
    tables: dict[str, dict[str, Any]] = {}
    for table in data.get("tables") or []:
        pk = [str(c) for c in table.get("primary_key") or []]
        columns = [
            {
                "name": str(col.get("name")),
                "type": str(col.get("column_type") or col.get("data_type") or ""),
                "nullable": bool(col.get("nullable", True)),
                "is_key": str(col.get("name")) in pk,
                "comment": str(col.get("comment") or ""),
            }
            for col in table.get("columns") or []
        ]
        tables[str(table.get("name"))] = {
            "comment": str(table.get("comment") or ""),
            "rows_estimate": table.get("rows_estimate"),
            "primary_key": pk,
            "columns": columns,
            "foreign_keys": list(table.get("foreign_keys") or []),
        }
    return {"tables": tables, "descriptions": {}}


def _normalize_transcript(data: Mapping[str, Any]) -> dict[str, Any]:
    tables: dict[str, dict[str, Any]] = {}
    for name, table in (data.get("tables") or {}).items():
        pk = [str(c) for c in table.get("primary_key") or []]
        columns = [
            {
                "name": str(col.get("var")),
                "type": str(col.get("type") or ""),
                "nullable": bool(col.get("nullable", True)),
                "is_key": str(col.get("var")) in pk,
                # 전사본 `ko`(벤더 시트 정의)는 시스템이 보는 의미가 아니다 — 의미 출처로 쓰지
                # 않는다.
                "comment": "",
            }
            for col in table.get("columns") or []
        ]
        tables[str(name)] = {
            "comment": "",
            "rows_estimate": table.get("row_count_estimate"),
            "primary_key": pk,
            "columns": columns,
            "foreign_keys": [],
        }
    return {"tables": tables, "descriptions": {}}


def load_schema_source(
    kind: str,
    *,
    db_id: str = DB_ID,
    path: Path | None = None,
    cache_dir: Path | None = None,
    cfg: Any = None,
    store: Any = None,
) -> dict[str, Any]:
    """스키마 입력을 정규화한다 — **파일·Redis 키만 읽는다**(조회문 0 · D-301 ③).

    kind: `schema_cache`(파일 스키마 캐시 · 기본) | `snapshot`(`scripts/itam_erd.py` 스냅숏 JSON) |
          `transcript`(샌드박스 전사본 YAML) | `structure_store`(「DB 구조」 탭 저장소 —
          `load_structure_store_source` · `cfg` 또는 `store` 필요).

    Raises:
        FileNotFoundError: 입력 파일이 없다.
        ValueError: 모르는 kind · 캐시 포맷 불일치.
    """
    if kind == "structure_store":
        return load_structure_store_source(db_id=db_id, cfg=cfg, store=store, cache_dir=cache_dir)
    if kind == "schema_cache":
        from src.schema_cache.persistent_cache import PersistentSchemaCache

        directory = cache_dir or (REPO_ROOT / ".cache" / "schema")
        data = PersistentSchemaCache(cache_dir=str(directory)).load(db_id)
        if not data:
            raise FileNotFoundError(
                f"파일 스키마 캐시가 없다(또는 포맷 불일치) — {directory}/{db_id}_schema.json"
            )
        normalized = _normalize_cache(data)
        normalized["source"] = "schema_cache"
    elif kind == "snapshot":
        if path is None:
            raise ValueError("snapshot 은 파일 경로가 필요하다(--schema-snapshot)")
        normalized = _normalize_snapshot(json.loads(Path(path).read_text(encoding="utf-8")))
        normalized["source"] = f"snapshot:{Path(path).name}"
    elif kind == "transcript":
        target = path or (REPO_ROOT / "testdata" / "itam" / "schema.yaml")
        normalized = _normalize_transcript(
            yaml.safe_load(Path(target).read_text(encoding="utf-8")) or {}
        )
        normalized["source"] = "transcript"
    else:
        raise ValueError(f"모르는 스키마 입력 {kind!r}")
    if not normalized["tables"]:
        raise ValueError(f"{normalized['source']}: 테이블이 0개다")
    return normalized


# --- 「DB 구조」 탭 저장소 입력 (plans/140 W2-1 · D-311 ①) ------------------------------

#: P1 근거가 없을 때 리포트 첫머리 경고(고정 문구).
P1_MISSING_WARNING = "P1 근거 없음 — 자산 없는 기준선"
P1_HASH_MISMATCH = "P1 초안의 스냅샷 해시가 현 스냅샷과 다르다 — 초안 뒤 스키마가 바뀌었을 수 있다"
P1_OUTDATED_DRAFT = "P1 초안이 구버전 빌드(b1fabf4 이전)로 생성됨 — 내부망에서 P1 재실행 필요"
P1_MALFORMED_DRAFT = "P1 초안 형식이 손상됨 — 손상된 초안은 쓰지 않음 · 내부망에서 P1 재실행 필요"
P1_DESCRIPTION_DRAFT_MALFORMED = "P1 설명 초안 형식이 손상됨 — 설명 초안은 쓰지 않음"
#: P1 초안 ID 형식 — 누출 관문 `code_samples.yaml` 머리 칸(`redact._CODE_SAMPLES_ID`)과 같다.
#: redact 가 이 모듈을 가져오므로 여기서 가져올 수 없다(순환) — 같은 패턴을 테스트가 고정한다.
P1_DRAFT_ID_FORM = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
#: P1 초안에서 소비처(카탈로그 조립·치환 코드값)가 순회하는 칸 — 있으면 목록이어야 한다.
P1_EVIDENCE_LIST_KEYS: tuple[str, ...] = (
    "columns",
    "relationships",
    "allowed_tables",
    "code_columns",
)
#: 목록 항목 안에서 소비처가 순회하는 칸 — 있으면 목록이어야 한다.
P1_ITEM_LIST_KEYS: dict[str, tuple[str, ...]] = {
    "columns": ("flag",),
    "relationships": ("child_columns", "parent_columns"),
}
#: 현행 P1 초안 표지 — b1fabf4(plans/140)가 evidence `columns`·`budget.requested/cap`을 더했다.
P1_CURRENT_EVIDENCE_KEYS: tuple[str, ...] = ("columns",)
P1_CURRENT_BUDGET_KEYS: tuple[str, ...] = ("requested", "cap")
#: P1 예산 칸 중 카탈로그·run.json 에 싣는 것(`skipped_sample`은 이름 목록이라 뺀다).
P1_BUDGET_KEYS: tuple[str, ...] = ("limit", "used", "skipped", "requested", "cap")


def _resolve(directory: Any, default: str) -> Path:
    path = Path(directory or default)
    return path if path.is_absolute() else REPO_ROOT / path


def app_structure_store(cfg: Any) -> Any:
    """앱(`SchemaCacheManager.structure_store`)과 같은 설정의 `StructureStore`.

    Redis 는 백엔드가 `redis`일 때만 붙인다(앱과 같다) · 백업 루트는 스키마 파일 캐시 디렉터리의
    형제 `structure` · 프로필 디렉터리는 `config/db_profiles` — 상대 경로는 저장소 루트 기준이다.
    """
    from src.schema_cache.structure_store import DEFAULT_PROFILES_DIR, StructureStore

    settings = getattr(cfg, "schema_cache", None)
    redis_cache = None
    if str(getattr(settings, "backend", "") or "") == "redis":
        from src.schema_cache.redis_cache import RedisSchemaCache

        redis_cache = RedisSchemaCache(redis_config=cfg.redis, schema_cache_config=settings)
    cache_dir = _resolve(getattr(settings, "cache_dir", None), ".cache/schema")
    return StructureStore(
        redis_cache,
        cache_dir.parent / "structure",
        profiles_dir=_resolve(DEFAULT_PROFILES_DIR, str(DEFAULT_PROFILES_DIR)),
    )


async def _read_structure_store(store: Any, db_id: str, *, owned: bool) -> dict[str, Any]:
    """저장소에서 스냅샷 · DDL 주석 · 최신 P1 초안 · 그 설명 초안을 읽는다(읽기만 · 쓰기 0).

    실패는 예외 없이 `{"error": 사유}`(고정 문구)로 돌려 폴백에 넘긴다 — 원 예외 메시지는 싣지
    않는다.
    """
    redis_cache = getattr(store, "redis_cache", None)
    try:
        if hasattr(store, "redis_cache"):
            if redis_cache is None:
                return {"error": "Redis 백엔드 아님"}
            if not await redis_cache.ensure_connected():
                return {"error": "Redis 연결 불가"}
        record = await store.load_snapshot(db_id)
        if not record or not ((record.get("snapshot") or {}).get("tables")):
            return {"error": "스냅샷 없음"}
        comments = dict(await store.load_ddl_comments(db_id) or {})
        # 매핑이 아닌 항목은 종류를 알 수 없어 P1 초안 후보가 아니다 — 건너뛰되 손상으로 알린다
        # (예외로 스냅샷까지 잃지 않는다). 형식이 손상된 P1 초안은 통째로 버린다(초안 없음과 같다
        # — 그 안의 값을 부분적으로 믿지 않는다 · 설명 초안도 읽지 않는다).
        listed = list(await store.list_asset_drafts(db_id))
        draft = next(
            (d for d in listed if isinstance(d, Mapping) and d.get("kind") == "profile"), None
        )
        malformed = any(not isinstance(d, Mapping) for d in listed)
        if draft is not None and p1_malformed(draft):
            draft, malformed = None, True
        descriptions: dict[str, str] = {}
        description_malformed = False
        description_id = draft.get("description_draft_id") if draft else None
        if description_id and not isinstance(description_id, str):
            description_malformed = True
        elif description_id:
            # 설명 초안이 손상됐으면 그것만 버린다(설명 없음) — 스냅샷·P1 은 지킨다
            description = await store.get_description_draft(db_id, description_id)
            per_tables = (
                description.get("descriptions") if isinstance(description, Mapping) else None
            )
            if description is not None:
                description_malformed = not isinstance(description, Mapping) or not _is_optional(
                    per_tables, Mapping
                )
            for per_table in _mapping(per_tables).values():
                if isinstance(per_table, Mapping):
                    descriptions.update(
                        {str(k): v for k, v in per_table.items() if v and isinstance(v, str)}
                    )
        return {
            "record": record,
            "comments": comments,
            "draft": draft,
            "descriptions": descriptions,
            "malformed": malformed,
            "description_malformed": description_malformed,
        }
    except Exception as exc:  # noqa: BLE001 — Redis 실패는 파일 폴백으로 넘긴다
        return {"error": f"Redis 읽기 실패:{type(exc).__name__}"}
    finally:
        if owned and redis_cache is not None:
            await redis_cache.disconnect()


def _foreign_keys_from_snapshot(raw: Iterable[Any]) -> list[dict[str, Any]]:
    """스냅샷 FK(`"col->table.col"` — 컬럼 단위) → 대상 테이블별
    `{columns, ref_table, ref_columns}`."""
    grouped: dict[str, dict[str, list[str]]] = {}
    for item in raw or []:
        column, _, target = str(item).partition("->")
        ref_table, _, ref_column = target.strip().rpartition(".")
        if column.strip() and ref_table and ref_column:
            entry = grouped.setdefault(ref_table, {"columns": [], "ref_columns": []})
            entry["columns"].append(column.strip())
            entry["ref_columns"].append(ref_column)
    return [{"ref_table": t, **pair} for t, pair in grouped.items()]


def _normalize_structure_store(data: Mapping[str, Any]) -> dict[str, Any]:
    """저장소 읽기 결과 → 정규화 스키마.

    타입은 스냅샷 원문 그대로(정규화 표기 — 길이가 빠져 있다) · 주석은 DDL 주석이 먼저, 없으면 P1
    설명 초안(둘 다 DB 주석) · 행 수 추정은 P1 `allowed_tables[].rows`.
    """
    comments: Mapping[str, str] = data.get("comments") or {}
    descriptions: Mapping[str, str] = data.get("descriptions") or {}
    evidence = (data.get("draft") or {}).get("evidence") or {}
    rows = {
        str(row.get("table")): row
        for row in evidence.get("allowed_tables") or []
        if isinstance(row, Mapping)
    }
    tables: dict[str, dict[str, Any]] = {}
    for name, table in (data["record"]["snapshot"].get("tables") or {}).items():
        name = str(name)
        raw_columns = table.get("columns") or {}
        columns = []
        for col_name, meta in raw_columns.items():
            meta = meta if isinstance(meta, Mapping) else {}
            key = f"{name}.{col_name}"
            columns.append(
                {
                    "name": str(col_name),
                    "type": str(meta.get("type") or ""),
                    "nullable": bool(meta.get("nullable", True)),
                    "is_key": bool(meta.get("primary_key", False)),
                    "comment": str(comments.get(key) or descriptions.get(key) or ""),
                }
            )
        row = rows.get(name) or {}
        tables[name] = {
            "comment": str(comments.get(name) or row.get("comment") or ""),
            "rows_estimate": row.get("rows"),
            "primary_key": [c["name"] for c in columns if c["is_key"]],
            "columns": columns,
            "foreign_keys": _foreign_keys_from_snapshot(table.get("foreign_keys") or []),
        }
    return {"tables": tables, "descriptions": {}}


def _mapping(value: Any) -> Mapping[str, Any]:
    """매핑이면 그대로, 아니면(없음·형식 손상) 빈 매핑."""
    return value if isinstance(value, Mapping) else {}


def _is_optional(value: Any, kinds: type | tuple[type, ...]) -> bool:
    """없거나(None) 기대한 형이면 참."""
    return value is None or isinstance(value, kinds)


def p1_malformed(draft: Any) -> bool:
    """P1 초안 형식 손상 — 소비처(카탈로그 조립·치환 코드값·run.json 지문)가 매핑·목록으로 읽는
    칸이 다른 형이면 참.

    `draft_id`는 누출 관문 식별자 형식(`P1_DRAFT_ID_FORM`)이어야 한다(관문이 거르면 run 이 멈춘다).
    초안·`evidence`·`evidence.budget`·`assets`·`assets.code_values`·`assets.code_labels`는 매핑,
    `P1_EVIDENCE_LIST_KEYS`·`P1_ITEM_LIST_KEYS`·코드값 목록은 목록이어야 한다. **없는 칸(None)은
    손상이 아니다** — 표지 키 부재는 구버전 판정(`p1_outdated`) 몫이다.
    """
    if not isinstance(draft, Mapping):
        return True
    draft_id = draft.get("draft_id")
    if draft_id is not None and not (
        isinstance(draft_id, str) and P1_DRAFT_ID_FORM.match(draft_id)
    ):
        return True
    evidence = draft.get("evidence")
    if not _is_optional(evidence, Mapping):
        return True
    evidence = _mapping(evidence)
    if not _is_optional(evidence.get("budget"), Mapping):
        return True
    for key in P1_EVIDENCE_LIST_KEYS:
        rows = evidence.get(key)
        if not _is_optional(rows, (list, tuple)):
            return True
        for row in rows or []:
            if isinstance(row, Mapping) and any(
                not _is_optional(row.get(k), (list, tuple)) for k in P1_ITEM_LIST_KEYS.get(key, ())
            ):
                return True
    assets = draft.get("assets")
    if not _is_optional(assets, Mapping):
        return True
    assets = _mapping(assets)
    code_values = assets.get("code_values")
    if not _is_optional(code_values, Mapping) or not _is_optional(
        assets.get("code_labels"), Mapping
    ):
        return True
    return any(not _is_optional(v, (list, tuple)) for v in _mapping(code_values).values())


def p1_summary(draft: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """카탈로그 최상위 `p1` — 초안 메타·예산만(값 0). 초안이 매핑이 아니면 None."""
    if not draft or not isinstance(draft, Mapping):
        return None
    evidence = _mapping(draft.get("evidence"))
    budget = _mapping(evidence.get("budget"))
    return {
        "draft_id": draft.get("draft_id"),
        "created_at": draft.get("created_at"),
        "status": draft.get("status"),
        "snapshot_hash": draft.get("snapshot_hash"),
        "engine": draft.get("engine"),
        "offline": bool(evidence.get("offline")),
        "budget": {k: budget.get(k) for k in P1_BUDGET_KEYS},
    }


def p1_outdated(draft: Mapping[str, Any]) -> bool:
    """P1 초안이 b1fabf4 이전 빌드로 만들어졌나 — 현행 evidence 표지 키가 하나라도 없으면 참.

    매핑이 아닌 칸은 표지가 없는 것으로 본다(형식 손상 판정은 `p1_malformed` 몫 — 로더가 먼저 본다).
    """
    if not isinstance(draft, Mapping):
        return True
    evidence = _mapping(draft.get("evidence"))
    budget = _mapping(evidence.get("budget"))
    return any(k not in evidence for k in P1_CURRENT_EVIDENCE_KEYS) or any(
        k not in budget for k in P1_CURRENT_BUDGET_KEYS
    )


def p1_asset(draft: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """`run.json` `assets.p1` — 초안 지문(assets·evidence 정규 JSON sha256 앞 12자)·시각·예산."""
    summary = p1_summary(draft)
    if summary is None or not isinstance(draft, Mapping):
        return None
    canonical = json.dumps(
        {"assets": draft.get("assets"), "evidence": draft.get("evidence")},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return {
        "draft_id": summary["draft_id"],
        "fingerprint": hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12],
        "created_at": summary["created_at"],
        "status": summary["status"],
        "budget": summary["budget"],
    }


def load_structure_store_source(
    *,
    db_id: str = DB_ID,
    cfg: Any = None,
    store: Any = None,
    cache_dir: Path | None = None,
) -> dict[str, Any]:
    """「DB 구조」 탭 저장소 입력(plans/140 W2-1 · D-311 ①) — 스냅샷·DDL 주석·최신 P1 초안.

    - 스냅샷이 없거나 Redis 를 못 쓰면 파일 스키마 캐시로 내려간다(`source: schema_cache`).
    - 스냅샷은 있는데 P1 초안이 없으면 스냅샷 구조만 쓴다(`source: structure_store`).
    - 두 경우 모두 `p1: None` · `p1_fallback: 사유`(고정 문구) — 리포트 첫머리 경고 재료다.
    - P1 초안의 `snapshot_hash`가 현 스냅샷 해시와 다르면 `p1_warnings`에 남긴다.
    - P1 초안이 구버전 빌드 모양(`p1_outdated`)이면 `p1_warnings`에 따로 남긴다.
    - P1 초안 형식이 손상됐으면(`p1_malformed` · 매핑 아닌 초안 항목 포함) 초안 없음과 같게 진행하고
      `p1_warnings`에 `P1_MALFORMED_DRAFT`를 남긴다(구버전과 구별 · 고정 문구).
    - 설명 초안 형식이 손상됐으면 설명 초안만 버리고(스냅샷·P1 유지)
      `P1_DESCRIPTION_DRAFT_MALFORMED`를 남긴다. 문자열 아닌 설명 값은 그 항목만 건너뛴다.

    P1 초안 원본(코드값·라벨 포함)은 `_p1_draft`에 **메모리로만** 둔다 — 카탈로그 조립과 치환
    코드값 생성(`code_samples`)의 재료이고 산출물에 그대로 쓰지 않는다.

    Raises:
        FileNotFoundError: 폴백할 파일 스키마 캐시도 없다.
    """
    owned = store is None
    if owned:
        if cfg is None:
            raise ValueError("structure_store 는 설정(cfg) 또는 저장소(store)가 필요하다")
        store = app_structure_store(cfg)
    data = asyncio.run(_read_structure_store(store, db_id, owned=owned))
    if data.get("error"):
        reason = str(data["error"])
        directory = cache_dir
        if directory is None and cfg is not None:
            directory = _resolve(getattr(cfg.schema_cache, "cache_dir", None), ".cache/schema")
        try:
            normalized = load_schema_source("schema_cache", db_id=db_id, cache_dir=directory)
        except (FileNotFoundError, ValueError) as exc:
            raise FileNotFoundError(f"structure_store 불가({reason}) · {exc}") from None
        normalized.update(p1=None, p1_fallback=reason, p1_warnings=[], _p1_draft=None)
        return normalized
    normalized = _normalize_structure_store(data)
    normalized["source"] = "structure_store"
    draft = data.get("draft")
    warnings: list[str] = []
    if draft and draft.get("snapshot_hash") != data["record"].get("hash"):
        warnings.append(P1_HASH_MISMATCH)
    if draft and p1_outdated(draft):
        warnings.append(P1_OUTDATED_DRAFT)
    fallback = "P1 초안 없음"
    if data.get("malformed"):
        warnings.append(P1_MALFORMED_DRAFT)
        fallback = "P1 초안 형식 손상"
    if data.get("description_malformed"):
        warnings.append(P1_DESCRIPTION_DRAFT_MALFORMED)
    normalized.update(
        p1=p1_summary(draft),
        p1_fallback=None if draft else fallback,
        p1_warnings=warnings,
        _p1_draft=draft,
    )
    if not normalized["tables"]:
        raise ValueError("structure_store: 테이블이 0개다")
    return normalized


async def _read_redis_annotations(
    redis_cache: Any, db_id: str, *, owned: bool
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Redis 설명·유사어 → (상태 `ok`|`unavailable`, 설명, 유사어).

    연결·읽기 실패는 예외 없이 `unavailable`로 돌려 파일 폴백에 넘긴다.
    """
    try:
        if not await redis_cache.ensure_connected():
            return "unavailable", {}, {}
        descriptions = await redis_cache.load_descriptions(db_id)
        synonyms = await redis_cache.load_synonyms(db_id)
        return "ok", dict(descriptions or {}), dict(synonyms or {})
    except Exception:  # noqa: BLE001 — 서버도 Redis 실패를 파일 폴백으로 넘긴다
        return "unavailable", {}, {}
    finally:
        if owned:
            await redis_cache.disconnect()


def apply_server_annotations(
    schema: dict[str, Any],
    *,
    cfg: Any,
    db_id: str = DB_ID,
    cache_dir: Path | None = None,
    redis_cache: Any = None,
) -> dict[str, Any]:
    """컬럼 설명·유사어를 서버와 같은 순서(Redis → 파일)로 읽어 `schema`에 덮어쓴다(plans/139 W6-a).

    서버(`SchemaCacheManager.get_descriptions`·`get_synonyms`)는 백엔드가 redis 이고 연결되며 결과가
    비지 않으면 Redis 값을, 아니면 파일 스키마 캐시 값을 쓴다 — 설명·유사어 각각. 조회문 0(키
    읽기만). 출처는 `annotation_sources`에 남긴다: `redis`(`off` 백엔드 file · `unavailable`
    연결·읽기 실패 · `ok`) · `descriptions`·`synonyms`(`redis`|`file`|`none`). 유사어는 건수만
    남는다.
    """
    from src.schema_cache.persistent_cache import PersistentSchemaCache

    settings = getattr(cfg, "schema_cache", None)
    redis_state, redis_desc, redis_syn = "off", {}, {}
    if str(getattr(settings, "backend", "") or "") == "redis":
        owned = redis_cache is None
        if owned:
            from src.schema_cache.redis_cache import RedisSchemaCache

            redis_cache = RedisSchemaCache(redis_config=cfg.redis, schema_cache_config=settings)
        redis_state, redis_desc, redis_syn = asyncio.run(
            _read_redis_annotations(redis_cache, db_id, owned=owned)
        )
    directory = Path(cache_dir or getattr(settings, "cache_dir", None) or ".cache/schema")
    if not directory.is_absolute():
        directory = REPO_ROOT / directory
    # 디렉터리가 없으면 만들지 않는다(enabled=False → 항상 빈 값)
    file_cache = PersistentSchemaCache(cache_dir=str(directory), enabled=directory.is_dir())

    def pick(from_redis: dict[str, Any], load_file: Any) -> tuple[dict[str, Any], str]:
        if from_redis:
            return from_redis, "redis"
        from_file = dict(load_file(db_id) or {})
        return from_file, "file" if from_file else "none"

    descriptions, desc_origin = pick(redis_desc, file_cache.load_descriptions)
    synonyms, syn_origin = pick(redis_syn, file_cache.load_synonyms)
    schema["descriptions"] = descriptions
    schema["synonym_counts"] = _synonym_counts(synonyms)
    schema["annotation_sources"] = {
        "redis": redis_state,
        "descriptions": desc_origin,
        "synonyms": syn_origin,
    }
    return schema


def _fingerprint_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    except OSError:
        return None


def asset_fingerprints(
    db_id: str = DB_ID, *, repo_root: Path = REPO_ROOT, descriptions: int = 0
) -> dict[str, Any]:
    """이 run 에 적용된 ITAM 프롬프트 자산의 지문(개선 전후 비교 기준 · §3.4).

    프로필은 **머리 주석을 옮기지 않는다** — 승인자 칸이 있다(§2.4 v1.2). 지문(내용 해시 앞 12자)과
    `source`·`environment` 키만 남긴다.
    """
    from src.schema_cache.asset_store import ASSET_PATHS

    profile_path = repo_root / "config" / "db_profiles" / f"{db_id}.yaml"
    profile: dict[str, Any] | None = None
    fingerprint = _fingerprint_file(profile_path)
    if fingerprint:
        try:
            body = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            body = {}
        profile = {
            "fingerprint": fingerprint,
            "source": body.get("source") if isinstance(body, dict) else None,
            "environment": body.get("environment") if isinstance(body, dict) else None,
        }
    assets: dict[str, Any] = {"profile": profile}
    for kind, template in sorted(ASSET_PATHS.items()):
        print_fp = _fingerprint_file(repo_root / template.format(db_id=db_id))
        assets[kind] = {"fingerprint": print_fp} if print_fp else None
    knowledge = _fingerprint_file(repo_root / "config" / "knowledge" / db_id / "catalog.yaml")
    assets["knowledge_catalog"] = {"fingerprint": knowledge} if knowledge else None
    assets["column_descriptions"] = int(descriptions)
    return assets


#: 승인 프로필에서 **구조로** 싣는 키 · 건수만 싣는 키(값·문장이 들어 있다). 나머지 키는 이름만.
PROFILE_COUNT_KEYS: tuple[str, ...] = ("query_rules", "query_examples", "patterns")
PROFILE_COLUMN_COUNT_KEYS: tuple[str, ...] = ("code_values", "code_labels")
_VERSION_FILE = re.compile(r"^v(\d+)\.yaml$")


def _bare(name: str) -> str:
    return str(name).rsplit(".", 1)[-1]


def load_profile(db_id: str = DB_ID, *, repo_root: Path = REPO_ROOT) -> dict[str, Any] | None:
    """승인 프로필(`config/db_profiles/{db_id}.yaml`) 본문 — **메모리 전용**(판정·구조 추출 재료).

    YAML 로 읽으므로 머리 주석(승인자 칸)은 들어오지 않는다. 없으면 None.
    """
    path = repo_root / "config" / "db_profiles" / f"{db_id}.yaml"
    try:
        body = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    return body if isinstance(body, dict) else None


def _profile_version(db_id: str, repo_root: Path) -> int | None:
    """「DB 구조」 탭 승인 버전 번호.

    파일 이름만 본다 — 버전 파일 안의 승인자 `by`는 읽지 않는다.
    """
    directory = repo_root / ".cache" / "structure" / db_id / "versions"
    try:
        numbers = [
            int(m.group(1)) for p in directory.iterdir() if (m := _VERSION_FILE.match(p.name))
        ]
    except OSError:
        return None
    return max(numbers) if numbers else None


def profile_structure(
    profile: Mapping[str, Any] | None, *, db_id: str = DB_ID, repo_root: Path = REPO_ROOT
) -> dict[str, Any] | None:
    """승인 프로필의 자산 키를 **구조만** 남긴다(plans/135 v1.4 · D-294 자산 키).

    - 그대로: `allowed_tables`(테이블 이름) · `entity_keys`(식별 키 컬럼·종류) · `relationships`
      (조인 컬럼 쌍·출처·겹침 비율)
    - 건수만: `code_values`·`code_labels`(컬럼별 — 코드값·라벨은 데이터다) · `query_rules`·
      `query_examples`·`patterns`(문장·예시 SQL 에 값이 섞일 수 있다) · `query_guide`(글자 수)
    - 테이블 정의(`table_definitions`): 사람이 쓴 정의 7칸(plans/140 W2-3 · `group` 감사 M-2) — 누출
      관문 규칙에 걸린 행은 `gate_table_definitions`가 빼고
      `table_definitions_withheld`에 수만 남긴다
    - 그 밖의 키는 이름만
    """
    if not profile:
        return None
    entity = profile.get("entity_keys") if isinstance(profile.get("entity_keys"), Mapping) else None
    keys_allowed = ("type", "column", "priority", "compare", "multi_value")
    out: dict[str, Any] = {
        "source": profile.get("source"),
        "environment": profile.get("environment"),
        "version": _profile_version(db_id, repo_root),
        "allowed_tables": (
            [str(t) for t in profile["allowed_tables"]]
            if isinstance(profile.get("allowed_tables"), list)
            else None
        ),
        "entity_keys": None
        if entity is None
        else {
            "entity": entity.get("entity"),
            "table": entity.get("table"),
            "keys": [
                {k: key.get(k) for k in keys_allowed if k in key}
                for key in entity.get("keys") or []
                if isinstance(key, Mapping)
            ],
        },
        "relationships": [
            {k: rel.get(k) for k in ("from", "to", "origin", "overlap") if k in rel}
            for rel in profile.get("relationships") or []
            if isinstance(rel, Mapping)
        ],
        "counts": {
            **{key: len(profile.get(key) or []) for key in PROFILE_COUNT_KEYS},
            **{
                key: {
                    str(col): len(values or {}) for col, values in (profile.get(key) or {}).items()
                }
                for key in PROFILE_COLUMN_COUNT_KEYS
                if isinstance(profile.get(key), Mapping)
            },
            "query_guide_chars": len(str(profile.get("query_guide") or "")),
        },
    }
    out.update(_definition_structure(profile))
    known = {
        "source",
        "environment",
        "allowed_tables",
        "entity_keys",
        "relationships",
        "query_guide",
        "table_definitions",
        *PROFILE_COUNT_KEYS,
        *PROFILE_COLUMN_COUNT_KEYS,
    }
    out["other_keys"] = sorted(str(k) for k in profile if k not in known)
    return out


#: 승인 프로필 테이블 정의에서 반출하는 칸(plans/140 W2-3 · `group`은 감사 M-2 — 내부망 편집 묶음이
#: 반입 병합에서 사라지지 않게).
DEFINITION_EXPORT_FIELDS: tuple[str, ...] = (
    "manages",
    "kind",
    "key_columns",
    "related",
    "notes",
    "origin",
    "group",
)


def _definition_structure(profile: Mapping[str, Any]) -> dict[str, Any]:
    """`table_definitions` 반출 칸(`DEFINITION_EXPORT_FIELDS`) · 보류 수(관문 전 0).

    키가 없으면 None.
    """
    from src.domain.table_definitions import PROFILE_KEY

    definitions = profile.get(PROFILE_KEY)
    if not isinstance(definitions, Mapping):
        return {"table_definitions": None, "table_definitions_withheld": 0}
    return {
        "table_definitions": {
            str(name): {k: entry[k] for k in DEFINITION_EXPORT_FIELDS if k in entry}
            for name, entry in definitions.items()
            if isinstance(entry, Mapping)
        },
        "table_definitions_withheld": 0,
    }


def _string_leaves(node: Any) -> Iterable[str]:
    """중첩 구조의 키·값 문자열(관문 사전 대조용)."""
    if isinstance(node, Mapping):
        for key, value in node.items():
            yield str(key)
            yield from _string_leaves(value)
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from _string_leaves(value)
    elif node is not None:
        yield str(node)


def gate_table_definitions(
    catalog: Mapping[str, Any],
    reject: Callable[[str], bool],
) -> dict[str, Any]:
    """승인 프로필 테이블 정의 행마다 관문 규칙(`reject`)을 미리 대 보고 걸린 행을 뺀다.

    행의 잎 하나하나와 잎을 이어 붙인 글을 함께 본다(관문의 「이어 붙인 잎」 검사와 같은 쪽).
    걸린 행은 이름도 싣지 않고 `table_definitions_withheld`에 수만 더한다. 새 카탈로그를 돌려준다.
    사람이 쓴 정의 글 속 P1 원 코드값·라벨은 대조하지 않는다 — 그대로 반출한다(D-311 부기 ⓑ
    2026-10-07 사용자 결정 「정의 글은 그대로 반출해도 된다」).
    """
    structure = catalog.get("approved_profile")
    definitions = (structure or {}).get("table_definitions")
    if not isinstance(definitions, Mapping):
        return dict(catalog)
    kept: dict[str, Any] = {}
    withheld = int((structure or {}).get("table_definitions_withheld") or 0)
    for name, row in definitions.items():
        leaves = list(_string_leaves({name: row}))
        if any(reject(leaf) for leaf in leaves) or reject("\n".join(leaves)):
            withheld += 1
            continue
        kept[name] = row
    return {
        **catalog,
        "approved_profile": {
            **(structure or {}),
            "table_definitions": kept,
            "table_definitions_withheld": withheld,
        },
    }


# --- P1 근거 → 카탈로그 칸 (plans/140 W2-2) -----------------------------------------

#: P1 오류 범주 — 원 예외 메시지(SQL·값이 섞일 수 있다)는 싣지 않는다.
ERROR_CATEGORIES: tuple[str, ...] = (
    "예산 초과",
    "후보 상한 초과",
    "부모 유일성 없음",
    "DB 연결 없음",
)
_EXCEPTION_PREFIX = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\s*:")


def error_category(raw: Any) -> str | None:
    """P1 오류 문구 → 범주(`예산 초과`·`후보 상한 초과`·`부모 유일성 없음`·`DB 연결 없음`·
    `식별자 거부`·`조회 실패:<예외 클래스 이름>`). 클래스 이름을 못 읽으면 `조회 실패:미상`."""
    if raw is None or raw == "":
        return None
    text = str(raw).strip()
    for category in ERROR_CATEGORIES:
        if text.startswith(category):
            return category
    if "식별자" in text:
        return "식별자 거부"
    match = _EXCEPTION_PREFIX.match(text)
    return f"조회 실패:{match.group(1).rsplit('.', 1)[-1]}" if match else "조회 실패:미상"


#: 값 형식 비율 칸(P1 `evidence.columns[]`).
FORMAT_KEYS: tuple[str, ...] = ("date8", "datetime14", "ipv4", "hostname", "multi_value")


def _column_profile(item: Mapping[str, Any]) -> dict[str, Any]:
    """P1 컬럼 근거 1행 → 카탈로그 `columns[i].profile`(비율·수·판정 — 값 0)."""
    return {
        "candidate": item.get("candidate"),
        "code": bool(item.get("code")),
        "distinct": item.get("distinct"),
        "truncated": bool(item.get("truncated")),
        "total": item.get("total"),
        "formats": {k: item.get(k) for k in FORMAT_KEYS},
        "mixed_case": bool(item.get("mixed_case")),
        "flag": [str(v) for v in item.get("flag") or []],
        "entity_key": item.get("entity_key"),
        "error": error_category(item.get("error")),
    }


def _p1_relations(draft: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """P1 `evidence.relationships[]` → 카탈로그 관계 항목(`kind: p1`)."""
    out: list[dict[str, Any]] = []
    evidence = (draft or {}).get("evidence") or {}
    for rel in evidence.get("relationships") or []:
        if not isinstance(rel, Mapping) or not rel.get("child"):
            continue
        out.append(
            {
                "from": str(rel["child"]),
                "to": None if rel.get("parent") is None else str(rel["parent"]),
                "columns": [
                    list(pair)
                    for pair in zip_longest(
                        rel.get("child_columns") or [], rel.get("parent_columns") or []
                    )
                ],
                "kind": "p1",
                "origin": rel.get("origin"),
                "overlap": rel.get("overlap"),
                "sampled": rel.get("sampled"),
                "accepted": bool(rel.get("accepted")),
                "unique_parent": rel.get("unique_parent"),
                "error": error_category(rel.get("error")),
            }
        )
    return out


def _relations(
    schema: Mapping[str, Any], profile: Mapping[str, Any] | None
) -> tuple[list[dict[str, Any]], list[list[str]]]:
    """관계 3종 — 선언 FK(`declared`) · 기본키 일치 추론(`inferred` — `scripts/itam_erd.py`
    `infer_relations` 재사용) · 승인 프로필(`approved_profile` — 출처·겹침 비율 동반).

    「공통 컬럼(테이블 절반 초과에 등장)만으로 된 기본키는 부모 후보 제외」 규칙은 108테이블용이다.
    테이블이 2개면 두 테이블에 다 있는 컬럼이 전부 「공통」이 돼 3열 동일 키 군까지 사라진다 —
    그때는 비율을 1.0(제외 없음)으로 둔다.
    """
    from scripts.itam_erd import DEFAULT_COMMON_RATIO, infer_relations

    snapshot = {
        "tables": [
            {
                "name": name,
                "columns": [{"name": c["name"]} for c in table["columns"]],
                "primary_key": list(table["primary_key"]),
                "foreign_keys": list(table["foreign_keys"]),
            }
            for name, table in sorted(schema["tables"].items())
        ]
    }
    ratio = DEFAULT_COMMON_RATIO if len(snapshot["tables"]) > 2 else 1.0
    inferred, groups = infer_relations(snapshot, common_ratio=ratio)
    relations: list[dict[str, Any]] = []
    for name, table in sorted(schema["tables"].items()):
        for fk in table["foreign_keys"]:
            relations.append(
                {
                    "from": name,
                    "to": str(fk.get("ref_table")),
                    "columns": [
                        list(pair)
                        for pair in zip(
                            fk.get("columns") or [], fk.get("ref_columns") or [], strict=False
                        )
                    ],
                    "kind": "declared",
                }
            )
    relations += [
        {"from": r.child, "to": r.parent, "columns": [[c, c] for c in r.columns], "kind": r.kind}
        for r in inferred
        if r.kind == "inferred"
    ]
    grouped: dict[tuple[str, str, Any], dict[str, Any]] = {}
    for rel in (profile or {}).get("relationships") or []:
        child, _, child_col = str(rel.get("from") or "").rpartition(".")
        parent, _, parent_col = str(rel.get("to") or "").rpartition(".")
        if not (child and parent and child_col and parent_col):
            continue
        key = (child, parent, rel.get("origin"))
        entry = grouped.setdefault(
            key,
            {
                "from": child,
                "to": parent,
                "columns": [],
                "kind": "approved_profile",
                "origin": rel.get("origin"),
            },
        )
        entry["columns"].append([child_col, parent_col])
        if rel.get("overlap") is not None:
            entry["overlap"] = min(entry.get("overlap", 1.0), float(rel["overlap"]))
    relations += list(grouped.values())
    return relations, [list(group) for group in groups]


def _table_manages(profile: Mapping[str, Any] | None) -> dict[str, str]:
    """승인 프로필 `table_definitions`의 테이블별 `manages` → {맨 이름 소문자: 문장}.

    plans/139 W6-g. 비었거나 문자열이 아닌 `manages`는 건너뛴다.
    """
    from src.domain.table_definitions import PROFILE_KEY

    definitions = (profile or {}).get(PROFILE_KEY)
    if not isinstance(definitions, Mapping):
        return {}
    return {
        _bare(str(name)).casefold(): entry["manages"].strip()
        for name, entry in definitions.items()
        if isinstance(entry, Mapping)
        and isinstance(entry.get("manages"), str)
        and entry["manages"].strip()
    }


def build_schema_catalog(
    schema: Mapping[str, Any],
    policy: ColumnPolicy,
    *,
    assets: Mapping[str, Any],
    db_id: str = DB_ID,
    profile: Mapping[str, Any] | None = None,
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    """정규화 스키마 + 정책 + 자산 지문 + 승인 프로필 구조 → `schema_catalog.yaml` 본문(§3.4 (1)).

    **테이블을 거르지 않는다**(운영 108테이블 전부 — 사용자 확정 2026-10-06). 값은 싣지 않는다(표본
    행 0 — 입력에 `sample_data`가 있어도 읽지 않는다 · 코드값·라벨·유사어는 건수만). 의미가 빈
    컬럼이 곧 plans/133 A3(설명) 작업 목록이다. 테이블 의미는 승인 프로필 `table_definitions`의
    `manages`가 먼저, 없으면 DB 주석이다(plans/139 W6-g).

    `structure_store` 입력이면 P1 근거를 더한다(plans/140 W2-2): 최상위 `p1`·`p1_fallback`·
    `p1_warnings` · 관계 `kind: p1` · 컬럼 `profile`(비율·수·판정 · 오류는 범주만) · 주석 코드 열거
    쌍 수 `comment_enum`. P1 의 코드값·라벨은 싣지 않는다.
    """
    from src.domain.schema_inference import parse_comment_enum

    descriptions = {
        str(k).casefold(): str(v) for k, v in (schema.get("descriptions") or {}).items()
    }
    synonyms = {str(k).casefold(): n for k, n in (schema.get("synonym_counts") or {}).items()}
    structure = (
        profile_structure(profile, db_id=db_id, repo_root=repo_root)
        if profile is not None
        else None
    )
    relations, groups = _relations(schema, profile)
    draft = schema.get("_p1_draft")
    relations += _p1_relations(draft)
    profiles = {
        str(item.get("key")): _column_profile(item)
        for item in ((draft or {}).get("evidence") or {}).get("columns") or []
        if isinstance(item, Mapping) and item.get("key")
    }
    allowed = (
        {_bare(t).casefold() for t in structure["allowed_tables"]}
        if structure and structure.get("allowed_tables") is not None
        else None
    )
    entity_table = _bare(str(((structure or {}).get("entity_keys") or {}).get("table") or ""))
    manages = _table_manages(profile)
    kept_definitions = (structure or {}).get("table_definitions")
    # 보류한 정의의 `manages`는 테이블 의미로도 싣지 않는다
    if isinstance(kept_definitions, Mapping):
        kept_names = {_bare(str(n)).casefold() for n in kept_definitions}
        manages = {k: v for k, v in manages.items() if k in kept_names}
    tables: dict[str, Any] = {}
    total = with_meaning = unclassified = with_synonyms = tables_with_meaning = 0
    comment_enums = profiled = code_columns = 0
    for name in sorted(schema["tables"]):
        table = schema["tables"][name]
        columns = []
        for col in table["columns"]:
            grade = policy.grade(col["name"], name)
            meaning, source = None, "none"
            key = f"{name}.{col['name']}".casefold()
            if col.get("comment"):
                meaning, source = col["comment"], "db_comment"
            elif key in descriptions:
                meaning, source = descriptions[key], "cache_description"
            entry: dict[str, Any] = {
                "name": col["name"],
                "meaning": meaning,
                "meaning_source": source,
                "value_kind": value_kind(
                    col["name"], col["type"], grade=grade, is_key=col["is_key"]
                ),
                "type": col["type"],
                "nullable": col["nullable"],
                "log_policy": grade,
            }
            if synonyms.get(key):
                entry["synonyms"] = synonyms[key]
                with_synonyms += 1
            enum_pairs = len(parse_comment_enum(meaning)) if meaning else 0
            if enum_pairs:
                entry["comment_enum"] = enum_pairs
                comment_enums += 1
            column_profile = profiles.get(f"{name}.{col['name']}")
            if column_profile is not None:
                entry["profile"] = column_profile
                profiled += 1
                code_columns += column_profile["code"]
            if grade == "unclassified":
                unclassified += 1
                if pii_suggestion(col["name"], meaning or ""):
                    entry["policy_suggestion"] = "pii"
            total += 1
            with_meaning += meaning is not None
            columns.append(entry)
        table_meaning, table_source = None, "none"
        if manages.get(_bare(name).casefold()):
            table_meaning, table_source = manages[_bare(name).casefold()], "table_definitions"
        elif table.get("comment"):
            table_meaning, table_source = table["comment"], "db_comment"
        tables_with_meaning += table_meaning is not None
        tables[name] = {
            "meaning": table_meaning,
            "meaning_source": table_source,
            "rows_estimate": table.get("rows_estimate"),
            "key": list(table["primary_key"]),
            "allowed": None if allowed is None else _bare(name).casefold() in allowed,
            "entity_key_table": bool(entity_table) and _bare(name) == entity_table,
            "relations": [r for r in relations if r["from"] == name],
            "columns": columns,
        }
    return {
        "db_id": db_id,
        "source": schema["source"],
        "assets": dict(assets),
        "meaning_sources": {
            "db_comment": "DB 주석(스냅숏·structure_store 입력일 때만 구별된다)",
            "cache_description": "「DB 구조」 탭 설명 적용본(서버와 같은 순서 Redis → 파일 — "
            "읽은 곳은 annotation_sources) — DB 주석 유래인지 LLM 생성인지 저장본에 "
            "출처가 없어 구별할 수 없다",
            "table_definitions": "승인 프로필 테이블 정의(table_definitions.manages) — "
            "테이블 의미만 · 컬럼 의미보다 앞선다",
            "none": "의미 없음 — plans/133 A3 설명 작업 대상",
        },
        "annotation_sources": schema.get("annotation_sources"),
        "db_description": schema.get("db_description"),
        "p1": schema.get("p1"),
        "p1_fallback": schema.get("p1_fallback"),
        "p1_warnings": list(schema.get("p1_warnings") or []),
        "approved_profile": structure,
        "summary": {
            "tables": len(tables),
            "tables_with_meaning": tables_with_meaning,
            "columns": total,
            "columns_with_meaning": with_meaning,
            "columns_with_synonyms": with_synonyms,
            "unclassified_columns": unclassified,
            "relations": dict(Counter(r["kind"] for r in relations)),
            "comment_enum_columns": comment_enums,
            "p1_profiled_columns": profiled,
            "p1_code_columns": code_columns,
        },
        "same_key_groups": groups,
        "tables": tables,
    }


def schema_form_violations(text: str) -> int:
    """스키마 절의 금지 형식(DDL·`information_schema` 등) 건수 — 누출 관문 ④·W1 단언이 같이 쓴다."""
    return len(SCHEMA_QUERY_FORMS.findall(text or ""))
