"""실 DB 읽기 전용 오라클 — 수동 판정 자동화 (plans/122 트랙 O · O-1~O-4 · D-275 ⑨ G-9).

시나리오 턴이 끝난 뒤(계측 구간 밖 · 직렬) 러너가 **정본 SQL**(`testdata/scenarios/oracles/
{id}.{pg|db2}.sql`)을 대상 DB 에서 읽기 전용으로 돌려 정답을 구하고, 시스템 결과 행
(`Observation.result` — `download-csv`)과 비교한다. 정적 정답표(`source: fixture` — M군
`m_cross_system_oracle.yaml`)도 같은 비교기로 판정한다(plans/122 §13.2 X-4).

공개 API (배선 에이전트가 이 이름·시그니처로 쓴다 — `assertions.py`·`catalog.py`·`runner.py`):
  ORACLE_DIR · ORACLE_SPEC_KEYS · validate_oracle_spec · anchor_values · render_sql ·
  run_oracle · evaluate_oracle

안전 (D-003 · G-9 — 전부 지킨다):
  ① 러너 측 `is_select_only`(`scripts/eval_text2sql.py`) 선검사 ② MCP readonly(서버가 주석을
  지운 뒤 검사하므로 태그가 거부되지 않는다 — `mcp_server/mcp_server/security.py`) ③ 집계·키만 ·
  마지막 절 `LIMIT`(PG)/`FETCH FIRST n ROWS ONLY`(DB2) 필수 · 상한 10,000 ④ 러너 측 전체
  타임아웃(`asyncio.wait_for`) ⑤ `DB_BACKEND=direct` 면 비활성(direct 클라이언트는 db_id 를
  무시하고 readonly 검사가 없다 — `src/db/__init__.py`·`src/db/client.py`).
  **DB2 는 DB 레벨 timeout 이 없다** — 러너 타임아웃은 기다림만 끊고 서버 측 질의는 계속 돈다.
  그래서 DB2 정본은 대형 스캔을 하지 않는다(정본 머리 주석 · 폐쇄망 1회 사람 검수 후 동결).

감사 공백 보완 (G-9): 오라클 SQL 은 운영 감사(`query_executed`)에 남지 않는다. 모든 SQL 머리에
`/* scenario-oracle run=<id> scn=<id> */` 를 붙이고, 실행마다 run 디렉터리 `oracle_log.jsonl`
에 1줄(태그·db·SQL·행 수·ms·status)을 남긴다. **행 원문은 싣지 않는다.**

판정 계약: 오라클 실패(SQL 오류·0행·타임아웃·direct·결과 미수집) = **보류**(불합격 아님 —
`eval_text2sql` 의 "골드 실패 = 골드셋 결함" 분류와 같다). 오라클과 시스템 불일치 = 불합격 +
차이(행 수 · 키 차집합 상위 10 — 값은 키만).

`src/` 는 수정하지 않는다. 이 모듈은 운영 경로에 배선되지 않는다(하네스 전용).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from collections import Counter
from collections.abc import Callable, Coroutine, Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, TypeVar

import yaml

from scripts.eval_text2sql import column_subset_unmatched, is_select_only

from . import REPO_ROOT, utf8_open

__all__ = [
    "ORACLE_DIR",
    "ORACLE_SPEC_KEYS",
    "validate_oracle_spec",
    "anchor_values",
    "render_sql",
    "run_oracle",
    "evaluate_oracle",
]

# --- 상수 (plans/122 O-1·O-2) ------------------------------------------------

#: 오라클 SQL 정본 폴더 — 파일명 `{id}.{pg|db2}.sql`(엔진별 한 벌).
ORACLE_DIR: Path = REPO_ROOT / "testdata" / "scenarios" / "oracles"

#: `source: fixture` 의 기본 정답표(plans/121 TP-0.7 · 122 §13.2 X-4).
DEFAULT_FIXTURE = "testdata/scenarios/fixtures/m_cross_system_oracle.yaml"

#: `expect.oracle` 하위 키 전체. 이 밖의 키는 로더가 거부한다
#: (조용한 무시 = pass 누수 · D-275 주의 ③).
#:   id        정본 SQL 파일 id(source=sql) 또는 정답표 시나리오 id(source=fixture) — 필수
#:   source    sql(기본) | fixture
#:   db_ids    오라클을 돌릴 DB(레지스트리 db_id) — source=sql
#:   compare   count | keyset | rowset | argmax | value — 필수
#:   key       열 참조 목록(각 참조 = 열 이름 또는 **같은 뜻의** 별칭 목록)
#:             — keyset·argmax 필수 · value 선택
#:   value     값 열 참조 — argmax·value 필수
#:   tol       수치 허용오차(기본 0.01 — rowset·argmax·value)
#:   top       argmax 상위 N(기본 1)
#:   match     keyset 집합 관계 equal(기본) | subset(시스템 ⊆ 오라클) | superset | disjoint
#:   system    count 의 시스템 쪽 — result(기본 · 결과 행 수) | row_counts_by_db(감사 DB 별 행 수)
#:   snapshot  once(기본) | pre_post(턴 전·후 두 번 — count·keyset(equal)·value 만 · O-4)
#:   fixture   정답표 경로(REPO_ROOT 기준 · 기본 DEFAULT_FIXTURE) — source=fixture
#:   field     정답표 시나리오 안 점 경로(예: producer.expected) — source=fixture 필수
ORACLE_SPEC_KEYS: frozenset[str] = frozenset({
    "id", "source", "db_ids", "compare", "key", "value", "tol", "top", "match",
    "system", "snapshot", "fixture", "field",
})

COMPARES: tuple[str, ...] = ("count", "keyset", "rowset", "argmax", "value")
SOURCES: tuple[str, ...] = ("sql", "fixture")
MATCHES: tuple[str, ...] = ("equal", "subset", "superset", "disjoint")
SNAPSHOTS: tuple[str, ...] = ("once", "pre_post")
SYSTEM_COUNTS: tuple[str, ...] = ("result", "row_counts_by_db")
FIXTURE_COMPARES: tuple[str, ...] = ("keyset", "count")
PRE_POST_COMPARES: tuple[str, ...] = ("count", "keyset", "value")

#: 레지스트리 엔진 → 정본 파일 접미사. 여기 없는 엔진(mariadb 등)은 오라클 대상이 아니다.
ENGINE_SUFFIX: dict[str, str] = {"postgresql": "pg", "db2": "db2"}

#: 오라클 행 상한 — MCP `max_rows`(10,000)와 같다. MCP 는 전량을 읽은 뒤 자르므로(`tools.py`)
#: SQL 의 LIMIT/FETCH FIRST 가 실제 상한이다.
MAX_LIMIT = 10000
DEFAULT_TOL = 0.01
DIFF_LIMIT = 10
#: count 오라클의 관례 열 — 있으면 그 합이 오라클 수, 없으면 행 수(plans/122 O-2).
COUNT_COLUMN = "n"
SOURCE_DB_COLUMN = "_source_db"
#: CLI 수동 실행 로그(run 디렉터리 밖 — results/scenario 아래에 두면 최신 run 으로 오인된다).
CLI_LOG = REPO_ROOT / "logs" / "scenario_oracle" / "oracle_log.jsonl"

KST = timezone(timedelta(hours=9))
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
#: 자리표 `:name` — `::numeric`(PG 캐스트)·`'10:30'`(시각 리터럴)은 잡지 않는다.
_PLACEHOLDER = re.compile(r"(?<![:\w]):([a-z][a-z0-9_]*)\b")
_TAG_UNSAFE = re.compile(r"[^A-Za-z0-9_.:+-]")
_PG_LIMIT = re.compile(r"\blimit\s+(\d+)\s*;?\s*$", re.I)
_DB2_FETCH = re.compile(r"\bfetch\s+first\s+(\d+)\s+rows?\s+only\s*;?\s*$", re.I)
_ANY_LIMIT = re.compile(r"\blimit\b", re.I)
_NUMERIC = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")

_T = TypeVar("_T")


# --- 상대 기간 앵커 (plans/122 §9.2 「상대 기간」 · §10.3.1 정책 표) ------------

def _parse_anchor(anchor_at: str) -> datetime:
    """앵커(턴 송신 시각 — KST ISO 8601)를 KST 시각으로 읽는다. 시간대가 없으면 KST 로 본다."""
    if not isinstance(anchor_at, str) or not anchor_at.strip():
        raise ValueError(f"앵커가 비었다 - {anchor_at!r}")
    moment = datetime.fromisoformat(anchor_at.strip())
    if moment.tzinfo is None:
        return moment.replace(tzinfo=KST)
    return moment.astimezone(KST)


def _month_shift(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def anchor_values(anchor_at: str) -> dict[str, str]:
    """앵커 시각(KST)에서 자리표 값을 계산한다(plans/122 O-1). 키에는 `:` 를 붙이지 않는다.

    정책은 §10.3.1(G-15)을 따른다 — 기준 시각 = 턴 송신 시각(KST) · 지난달 = 직전 완결 월 ·
    이번 달 = 1일~어제(일 통계 · D-201). 월 경계는 트랙 T 해석기의 `relative_window` 가
    정한다(plans/122 §9.2 「§10 해석기와 같은 함수」 — 하네스 판정 H-2 와 오라클 자리표가
    같은 정책 표를 쓴다).

    - `this_month` 시작 → month_start·anchor_month · `last_month` 시작 → prev_month·
      prev_month_start · `last_n_months`(n=N) 시작 → month_minus_N(직전 완결 N개월 창의
      첫 달 = N개월 전 달)
    - 해석기로 표현되지 않아 직접 계산하는 것: today·yesterday(월 정책이 아닌 날짜 산술 —
      앵커 날짜와 그 전날) · next_month_start(`relative_window` 에 「다음 달」 종류가 없다.
      `month_span` 은 연도를 「미래가 아닌 가장 최근」으로 추론해 다음 달을 **작년** 같은
      달로 푼다)

    Returns:
        anchor_at(KST ISO 초) · today·yesterday·month_start·prev_month_start·next_month_start
        (YYYY-MM-DD) · today_ymd·yesterday_ymd·month_start_ymd(YYYYMMDD) · anchor_month·prev_month
        (YYYYMM) · month_minus_1 ~ month_minus_12(YYYYMM · 1 = prev_month).

    Raises:
        ValueError: 앵커를 읽지 못했다.
    """
    from src.domain.time_spec import relative_window

    moment = _parse_anchor(anchor_at)
    today = moment.date()
    yesterday = today - timedelta(days=1)
    month_start, _ = relative_window("this_month", moment)
    prev_start, _ = relative_window("last_month", moment)
    next_y, next_m = _month_shift(today.year, today.month, 1)
    values = {
        "anchor_at": moment.replace(microsecond=0).isoformat(),
        "today": today.isoformat(),
        "today_ymd": today.strftime("%Y%m%d"),
        "yesterday": yesterday.isoformat(),
        "yesterday_ymd": yesterday.strftime("%Y%m%d"),
        "month_start": month_start.isoformat(),
        "month_start_ymd": month_start.strftime("%Y%m%d"),
        "anchor_month": month_start.strftime("%Y%m"),
        "prev_month": prev_start.strftime("%Y%m"),
        "prev_month_start": prev_start.isoformat(),
        "next_month_start": f"{next_y:04d}-{next_m:02d}-01",
    }
    for back in range(1, 13):
        start, _ = relative_window("last_n_months", moment, n=back)
        values[f"month_minus_{back}"] = start.strftime("%Y%m")
    return values


# --- 렌더 · 정적 검사 ----------------------------------------------------------

def _tag_token(text: str) -> str:
    """태그에 넣을 id 를 안전 문자로 좁힌다(`*/` 로 주석을 닫는 주입 차단)."""
    return _TAG_UNSAFE.sub("_", str(text or "-"))[:80] or "-"


def _strip_full_line_comments(sql: str) -> str:
    """줄 전체가 `--` 주석인 줄(정본 머리 주석)을 뺀다. 본문 안 주석은 정본에 쓰지 않는다."""
    return "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))


def render_sql(sql: str, *, anchor_at: str, run_id: str, scenario_id: str) -> str:
    """정본 SQL → 실행 SQL. 자리표를 **따옴표 리터럴로** 치환하고 머리에 오라클 태그를 붙인다.

    `:anchor_month` → `'202609'`. DB 현재시각 함수(`CURRENT_DATE`)를 쓰지 않는다 — 월말 run 에서
    시스템과 오라클이 다른 달을 보지 않게 앵커 리터럴로 고정한다(plans/122 §9.2). 줄 전체 `--`
    주석과 끝 세미콜론은 뺀다(DB2 `exec_immediate` 는 끝 세미콜론을 받지 않을 수 있다).

    Raises:
        ValueError: 모르는 자리표 · 자리표가 있는데 앵커를 읽지 못했다.
    """
    body = _strip_full_line_comments(sql).strip()
    while body.endswith(";"):
        body = body[:-1].rstrip()
    values: dict[str, str] | None = None

    def substitute(match: re.Match[str]) -> str:
        nonlocal values
        if values is None:
            values = anchor_values(anchor_at)
        name = match.group(1)
        if name not in values:
            raise ValueError(f"모르는 자리표 :{name} - 허용: {', '.join(sorted(values))}")
        return f"'{values[name]}'"

    body = _PLACEHOLDER.sub(substitute, body)
    tag = f"/* scenario-oracle run={_tag_token(run_id)} scn={_tag_token(scenario_id)} */"
    return f"{tag}\n{body}"


def _without_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return re.sub(r"--[^\n]*", " ", sql)


def _limit_of(sql: str, engine: str) -> tuple[int | None, str | None]:
    """(상한, 오류). 마지막 절이 엔진 행 상한이어야 한다.

    PG `LIMIT n` · DB2 `FETCH FIRST n ROWS ONLY`(DB2 정본에 LIMIT 금지).
    """
    body = _without_comments(sql).strip()
    if engine == "db2":
        if _ANY_LIMIT.search(body):
            return None, "DB2 정본에 LIMIT 을 쓰지 않는다 - FETCH FIRST n ROWS ONLY 를 쓴다"
        found = _DB2_FETCH.search(body)
        want = "FETCH FIRST n ROWS ONLY"
    else:
        found = _PG_LIMIT.search(body)
        want = "LIMIT n"
    if not found:
        return None, f"마지막 절이 {want} 가 아니다(행 상한 필수 · G-9)"
    limit = int(found.group(1))
    if not 1 <= limit <= MAX_LIMIT:
        return None, f"행 상한 {limit} 이 1~{MAX_LIMIT} 밖이다"
    return limit, None


def _engine_of(db_id: str) -> str | None:
    """레지스트리 db_id → 엔진(`config/db_registry.yaml` 단일 출처). 모르는 db 면 None."""
    try:
        from src.routing.domain_config import get_domain_by_id
    except Exception:  # noqa: BLE001 — 레지스트리를 못 읽으면 엔진을 모르는 것과 같다
        return None
    domain = get_domain_by_id(db_id)
    return domain.db_engine if domain else None


def oracle_sql_path(oracle_id: str, engine: str) -> Path | None:
    """정본 파일 경로. 오라클 대상이 아닌 엔진이면 None."""
    suffix = ENGINE_SUFFIX.get(engine)
    return ORACLE_DIR / f"{oracle_id}.{suffix}.sql" if suffix else None


def _display(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _prepare(oracle_id: str, db_id: str, *, anchor_at: str, run_id: str,
             scenario_id: str) -> tuple[str | None, str | None, int | None, str]:
    """(실행 SQL, 오류, 상한, 엔진). 파일·자리표·SELECT 전용·행 상한을 통과해야 SQL 이 나온다."""
    engine = _engine_of(db_id) or ""
    path = oracle_sql_path(oracle_id, engine)
    if path is None:
        return None, f"{db_id}: 오라클 대상 엔진이 아니다(엔진={engine or '모름'})", None, engine
    if not path.is_file():
        return None, f"{db_id}: 정본 파일이 없다 - {_display(path)}", None, engine
    try:
        rendered = render_sql(path.read_text(encoding="utf-8"), anchor_at=anchor_at,
                              run_id=run_id, scenario_id=scenario_id)
    except (OSError, ValueError) as exc:
        return None, f"{db_id}: 렌더 실패 - {exc}", None, engine
    if not is_select_only(rendered):
        return None, f"{db_id}: SELECT 전용 단일 조회문이 아니다(D-003)", None, engine
    limit, error = _limit_of(rendered, engine)
    if error:
        return None, f"{db_id}: {error}", None, engine
    return rendered, None, limit, engine


# --- 명세 검사 (로더용 · plans/122 O-2) ----------------------------------------

def _ref_ok(ref: Any) -> bool:
    if isinstance(ref, str):
        return bool(ref.strip())
    return (isinstance(ref, list) and bool(ref)
            and all(isinstance(name, str) and name.strip() for name in ref))


def _walk(data: Any, dotted: str) -> tuple[bool, Any]:
    node = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
    return True, node


def _fixture_values(spec: dict[str, Any]) -> tuple[list[Any] | None, str | None]:
    """(정답표 목록, 오류). 경로는 REPO_ROOT 기준이며 목록 원소는 스칼라여야 한다."""
    relative = str(spec.get("fixture") or DEFAULT_FIXTURE)
    path = (REPO_ROOT / relative).resolve()
    if REPO_ROOT.resolve() not in path.parents:
        return None, f"fixture 경로가 저장소 밖이다 - {relative}"
    if not path.is_file():
        return None, f"fixture 파일이 없다 - {relative}"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return None, f"fixture 를 읽지 못했다 - {relative}: {exc}"
    scenarios = data.get("scenarios") if isinstance(data, dict) else None
    entry = scenarios.get(spec.get("id")) if isinstance(scenarios, dict) else None
    if not isinstance(entry, dict):
        return None, f"fixture 에 시나리오 {spec.get('id')!r} 가 없다 - {relative}"
    field_path = spec.get("field")
    if not isinstance(field_path, str) or not field_path.strip():
        return None, f"field 는 점 경로 문자열이어야 한다 - {field_path!r}"
    found, values = _walk(entry, field_path.strip())
    if not found:
        return None, f"fixture {spec.get('id')} 에 field 경로가 없다 - {field_path}"
    if not isinstance(values, list) or any(isinstance(v, (dict, list)) for v in values):
        return None, f"field {field_path} 는 스칼라 목록이어야 한다 - {values!r}"
    return values, None


def validate_oracle_spec(spec: Any, *, scenario_id: str, db_ids: list[str] | None) -> list[str]:
    """`expect.oracle` 모양 오류 목록(빈 목록이면 통과) — 로더가 로드 시점에 부른다.

    source=sql: 대상 DB(인자 `db_ids` — 없으면 spec `db_ids`)의 엔진 전부에 `{id}.{pg|db2}.sql` 이
    있고, 자리표가 전부 알려진 것이며, SELECT 전용 · 마지막 절 행 상한을 지키는가.
    source=fixture: 정답표 파일 · 시나리오 id · field 경로가 있는가. 정의 밖 하위 키는 거부한다.
    `oracle:` 키를 넣었는데 평가가 조용히 건너뛰면 그 턴은 pass 로 샌다(D-275 주의 ③) — 그래서
    파일·엔진 커버리지를 여기서 막는다.
    """
    if not (isinstance(spec, dict) and spec):
        return [f"oracle 은 비지 않은 매핑이어야 한다 - {spec!r}"]
    errors: list[str] = []
    unknown = sorted(str(key) for key in spec if key not in ORACLE_SPEC_KEYS)
    if unknown:
        errors.append(f"정의 밖 키 {unknown} - 허용: {', '.join(sorted(ORACLE_SPEC_KEYS))}")
    oracle_id = spec.get("id")
    if not (isinstance(oracle_id, str) and _ID_PATTERN.match(oracle_id)):
        errors.append(f"id 는 영숫자·._- 로 된 문자열이어야 한다 - {oracle_id!r}")
        oracle_id = None
    source = spec.get("source", "sql")
    if source not in SOURCES:
        errors.append(f"source 는 {'|'.join(SOURCES)} 중 하나다 - {source!r}")
        return errors
    compare = spec.get("compare")
    allowed = FIXTURE_COMPARES if source == "fixture" else COMPARES
    if compare not in allowed:
        errors.append(f"compare 는 {'|'.join(allowed)} 중 하나다(source={source}) - {compare!r}")

    key = spec.get("key")
    if "key" in spec and not (isinstance(key, list) and key and all(_ref_ok(ref) for ref in key)):
        errors.append(f"key 는 열 참조(이름 또는 같은 뜻의 별칭 목록)의 비지 않은 목록이다"
                      f" - {key!r}")
    if compare in ("keyset", "argmax") and "key" not in spec:
        errors.append(f"compare={compare} 는 key 가 필요하다")
    if compare in ("count", "rowset") and "key" in spec:
        errors.append(f"compare={compare} 는 key 를 쓰지 않는다")
    if "value" in spec and not _ref_ok(spec["value"]):
        errors.append(f"value 는 열 이름 또는 같은 뜻의 별칭 목록이다 - {spec['value']!r}")
    if compare in ("argmax", "value") and "value" not in spec:
        errors.append(f"compare={compare} 는 value 가 필요하다")
    if compare in ("count", "keyset", "rowset") and "value" in spec:
        errors.append(f"compare={compare} 는 value 를 쓰지 않는다")
    if "tol" in spec:
        tol = spec["tol"]
        if isinstance(tol, bool) or not isinstance(tol, (int, float)) or tol < 0:
            errors.append(f"tol 은 0 이상의 수다 - {tol!r}")
    if "top" in spec:
        top = spec["top"]
        if compare != "argmax":
            errors.append("top 은 compare=argmax 에서만 쓴다")
        elif isinstance(top, bool) or not isinstance(top, int) or top < 1:
            errors.append(f"top 은 1 이상의 정수다 - {top!r}")
    if "match" in spec:
        if compare != "keyset":
            errors.append("match 는 compare=keyset 에서만 쓴다")
        elif spec["match"] not in MATCHES:
            errors.append(f"match 는 {'|'.join(MATCHES)} 중 하나다 - {spec['match']!r}")
    if "system" in spec:
        if compare != "count":
            errors.append("system 은 compare=count 에서만 쓴다")
        elif spec["system"] not in SYSTEM_COUNTS:
            errors.append(f"system 은 {'|'.join(SYSTEM_COUNTS)} 중 하나다 - {spec['system']!r}")
    snapshot = spec.get("snapshot", "once")
    if snapshot not in SNAPSHOTS:
        errors.append(f"snapshot 은 {'|'.join(SNAPSHOTS)} 중 하나다 - {snapshot!r}")
    elif snapshot == "pre_post":
        if source == "fixture" or compare not in PRE_POST_COMPARES:
            errors.append(f"snapshot=pre_post 는 source=sql · compare {'|'.join(PRE_POST_COMPARES)}"
                          f" 에서만 쓴다(compare={compare})")
        elif compare == "keyset" and spec.get("match", "equal") != "equal":
            errors.append("snapshot=pre_post 의 keyset 은 match=equal 만 쓴다(전·후 사이 판정)")

    if source == "fixture":
        for name in ("db_ids", "system", "snapshot"):
            if name in spec:
                errors.append(f"{name} 는 source=fixture 에서 쓰지 않는다")
        if compare == "keyset" and isinstance(key, list) and len(key) != 1:
            errors.append("fixture keyset 의 key 는 열 참조 1개다(정답표 목록은 스칼라)")
        if oracle_id:
            _values, error = _fixture_values(spec)
            if error:
                errors.append(f"{error} (카탈로그 {scenario_id})")
        return errors

    for name in ("fixture", "field"):
        if name in spec:
            errors.append(f"{name} 는 source=fixture 에서만 쓴다")
    spec_dbs = spec.get("db_ids")
    if "db_ids" in spec and not (isinstance(spec_dbs, list) and spec_dbs
                                 and all(isinstance(d, str) and d for d in spec_dbs)):
        errors.append(f"db_ids 는 db_id 문자열의 비지 않은 목록이다 - {spec_dbs!r}")
        spec_dbs = None
    targets = list(db_ids) if db_ids else (list(spec_dbs) if isinstance(spec_dbs, list) else [])
    if not targets:
        errors.append(f"대상 DB 가 없다 - 인자 db_ids 도 spec db_ids 도 없다"
                      f"(카탈로그 {scenario_id})")
    if oracle_id is None:
        return errors
    for db_id in dict.fromkeys(targets):
        _sql, error, _limit, _engine = _prepare(
            oracle_id, db_id, anchor_at="2026-01-15T10:00:00+09:00",
            run_id="validate", scenario_id=scenario_id)
        if error:
            errors.append(error)
    return errors


# --- 실행 (O-1) -----------------------------------------------------------------

def _open_client(cfg: Any, db_id: str) -> Any:
    """DB 클라이언트 컨텍스트(`src.db.get_db_client`). 테스트는 이 함수를 가짜로 바꾼다.

    `get_db_client` 는 `finally` 에서 `DBHubClient.disconnect()` 를 부른다 — 실패·타임아웃(취소)
    에서도 연결이 닫힌다(`RealExecutor` 의 세션 누수 선례 — plans/122 O-1).
    """
    from src.db import get_db_client

    return get_db_client(cfg, db_id=db_id)


def _run_coroutine(factory: Callable[[], Coroutine[Any, Any, _T]]) -> _T:
    """동기 러너에서 코루틴을 돌린다. 이미 루프가 돌고 있으면 별도 스레드의 새 루프로 돈다."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(factory())).result()


def _now_kst() -> str:
    return datetime.now(KST).replace(microsecond=0).isoformat()


def _write_log(log_path: Path | None, records: list[dict[str, Any]]) -> str | None:
    """oracle_log.jsonl 에 줄을 붙인다. 실패해도 예외를 올리지 않고 사유를 돌려준다."""
    if log_path is None or not records:
        return None
    try:
        with utf8_open(Path(log_path), "a") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception as exc:  # noqa: BLE001 — 기록 실패가 판정을 죽이면 안 된다
        return f"{type(exc).__name__}: {exc}"
    return None


class _OracleDbError(Exception):
    """DB 한 곳의 오라클 실패(사유는 로그에 이미 남았다)."""


def run_oracle(spec: dict[str, Any], *, db_ids: list[str], anchor_at: str, run_id: str,
               scenario_id: str, log_path: Path, timeout_sec: float = 30.0,
               phase: str = "post", cfg: Any = None) -> dict[str, Any]:
    """오라클 SQL 을 대상 DB 마다 **직렬로** 읽기 전용 실행한다. 절대 예외를 올리지 않는다.

    Returns:
        {"status": "ok"|"unavailable", "reason": str|None, "rows_by_db": {db_id: [dict]},
         "elapsed_ms": float, "phase": phase, "limit_by_db": {db_id: int}}.
        `rows_by_db` 는 `DataMasker(cfg.security).mask_rows` 를 거친 행이다(시스템 CSV 와 대칭).
        **행 원문이므로 raw.jsonl 에 그대로 싣지 않는다** — 판정 결과만 싣는다(G-4).
        DB 하나라도 실패하면 unavailable 이고 행을 돌려주지 않는다(마스킹 전 부분 행 유출 방지 —
        DB 별 행 수·사유는 oracle_log.jsonl 에 있다).
    """
    started = time.perf_counter()
    outcome: dict[str, Any] = {
        "status": "unavailable", "reason": None, "rows_by_db": {},
        "elapsed_ms": 0.0, "phase": phase, "limit_by_db": {},
    }
    records: list[dict[str, Any]] = []
    oracle_id = spec.get("id") if isinstance(spec, dict) else None
    tag = f"scenario-oracle run={_tag_token(run_id)} scn={_tag_token(scenario_id)}"

    def record(db_id: str, engine: str, sql: str | None, status: str,
               rows: int | None = None, ms: float | None = None,
               reason: str | None = None) -> None:
        records.append({
            "ts": _now_kst(), "tag": tag, "run": run_id, "scn": scenario_id, "oracle": oracle_id,
            "phase": phase, "db": db_id, "engine": engine, "anchor_at": anchor_at, "sql": sql,
            "rows": rows, "ms": None if ms is None else round(ms, 1), "status": status,
            "reason": reason,
        })

    try:
        if not isinstance(spec, dict) or spec.get("source", "sql") != "sql":
            outcome["reason"] = "source=sql 명세가 아니다(fixture 는 DB 를 조회하지 않는다)"
            return outcome
        if not (isinstance(oracle_id, str) and _ID_PATTERN.match(oracle_id)):
            outcome["reason"] = f"오라클 id 가 올바르지 않다 - {oracle_id!r}"
            return outcome
        targets = list(dict.fromkeys(db_ids or []))
        if not targets:
            outcome["reason"] = "대상 DB 가 없다"
            return outcome
        if cfg is None:
            from src.config import load_config

            cfg = load_config()
        if getattr(cfg, "db_backend", None) == "direct":
            outcome["reason"] = ("DB_BACKEND=direct - direct 클라이언트는 db_id 를 무시하고 "
                                 "readonly 검사가 없어 오라클을 비활성한다(G-9)")
            for db_id in targets:
                record(db_id, _engine_of(db_id) or "", None, "skipped", reason=outcome["reason"])
            return outcome

        prepared: list[tuple[str, str, str, int]] = []
        problems: list[str] = []
        for db_id in targets:
            sql, error, limit, engine = _prepare(oracle_id, db_id, anchor_at=anchor_at,
                                                 run_id=run_id, scenario_id=scenario_id)
            if error or sql is None or limit is None:
                problems.append(error or f"{db_id}: 준비 실패")
                record(db_id, engine, sql, "rejected", reason=error)
            else:
                prepared.append((db_id, engine, sql, limit))
        if problems:
            outcome["reason"] = "; ".join(problems)
            return outcome

        rows_by_db: dict[str, list[dict[str, Any]]] = {}
        progress: dict[str, Any] = {}

        async def execute_all() -> None:
            for db_id, engine, sql, _limit in prepared:
                progress.update(db=db_id, engine=engine, sql=sql, t0=time.perf_counter())
                try:
                    async with _open_client(cfg, db_id) as client:
                        result = await client.execute_sql(sql)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 — DB 별 실패는 사유로 남긴다
                    elapsed = (time.perf_counter() - progress["t0"]) * 1000
                    progress.clear()
                    record(db_id, engine, sql, "error", ms=elapsed,
                           reason=f"{type(exc).__name__}: {exc}")
                    raise _OracleDbError(f"{db_id}: {type(exc).__name__}: {exc}") from exc
                elapsed = (time.perf_counter() - progress["t0"]) * 1000
                progress.clear()
                rows = [dict(row) for row in (getattr(result, "rows", None) or [])
                        if isinstance(row, dict)]
                if getattr(result, "truncated", False):
                    record(db_id, engine, sql, "error", rows=len(rows), ms=elapsed,
                           reason="max_rows 절단")
                    raise _OracleDbError(f"{db_id}: 오라클 결과가 max_rows 로 잘렸다")
                rows_by_db[db_id] = rows
                record(db_id, engine, sql, "ok", rows=len(rows), ms=elapsed)

        async def bounded() -> None:
            await asyncio.wait_for(execute_all(), timeout=timeout_sec)

        try:
            _run_coroutine(bounded)
        except TimeoutError:
            if progress:
                record(progress["db"], progress["engine"], progress["sql"], "timeout",
                       ms=(time.perf_counter() - progress["t0"]) * 1000,
                       reason=f"러너 전체 타임아웃 {timeout_sec}s")
            outcome["reason"] = (f"타임아웃 {timeout_sec}s"
                                 + (f" ({progress['db']} 실행 중)" if progress else ""))
            return outcome
        except _OracleDbError as exc:
            outcome["reason"] = f"오라클 SQL 실패 - {exc}"
            return outcome

        from src.security.data_masker import DataMasker

        masker = DataMasker(cfg.security)
        outcome["rows_by_db"] = {db: masker.mask_rows(rows) for db, rows in rows_by_db.items()}
        outcome["limit_by_db"] = {db_id: limit for db_id, _e, _s, limit in prepared}
        outcome["status"] = "ok"
        return outcome
    except Exception as exc:  # noqa: BLE001 — 계약: 절대 예외를 올리지 않는다
        outcome["status"] = "unavailable"
        outcome["reason"] = f"오라클 실행 오류 - {type(exc).__name__}: {exc}"
        return outcome
    finally:
        outcome["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
        log_error = _write_log(log_path, records)
        if log_error:
            outcome["log_error"] = log_error


# --- 비교 어댑터 (O-2 — 숫자 강제 · 마스킹 대칭 · `_source_db` 분리) ------------

def _norm_value(value: Any) -> Any:
    """① CSV 문자열 → 숫자 강제. 빈 칸·None → None · 숫자꼴 문자열·Decimal·int → float ·
    그 밖은 strip.

    CSV 는 None 을 빈 칸으로 쓰고 숫자를 문자열로 쓴다. `eval_text2sql._norm_cell` 은 `"12.34"` 와
    `12.34` 를 다르게 보므로 양쪽을 같은 함수로 먼저 맞춘다(plans/122 §9.2 「비교」 ①).
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    if _NUMERIC.match(text):
        try:
            return float(text)
        except ValueError:
            return text
    return text


def _key_cell(value: Any) -> str:
    """키 비교용 문자열 — 숫자는 정수면 정수꼴(4.0 = '4.0' = 4)."""
    normed = _norm_value(value)
    if normed is None:
        return ""
    if isinstance(normed, float):
        return str(int(normed)) if normed.is_integer() else repr(round(normed, 6))
    return str(normed)


def _resolve(ref: Any, header: Iterable[str]) -> str | None:
    """열 참조(이름 또는 별칭 목록) → 머리글의 열 이름. 정확 일치 먼저, 다음 대소문자 무시."""
    columns = [str(column) for column in header]
    names = [str(name) for name in (ref if isinstance(ref, list) else [ref])]
    for name in names:
        if name in columns:
            return name
    folded = {column.casefold(): column for column in columns}
    for name in names:
        if name.casefold() in folded:
            return folded[name.casefold()]
    return None


def _number(value: Any) -> float | None:
    normed = _norm_value(value)
    return normed if isinstance(normed, float) else None


def _hold(compare: Any, reason: str, **extra: Any) -> tuple[str, dict[str, Any]]:
    return "hold", {"compare": compare, "reason": reason, **extra}


def _oracle_header(rows_by_db: dict[str, list[dict[str, Any]]]) -> list[str]:
    header: list[str] = []
    for rows in rows_by_db.values():
        for row in rows[:1]:
            header.extend(str(column) for column in row if column not in header)
    return header


def _system_rows(result: dict[str, Any] | None) -> tuple[dict[str, Any] | None, str | None]:
    """결과 행 관측 → ({header, rows, truncated, total}, 보류 사유)."""
    if not isinstance(result, dict):
        return None, "결과 행 미수집(러너가 download-csv 를 받지 않았다)"
    status = result.get("status")
    if status not in ("ok", "empty"):
        reason = result.get("reason") or "사유 없음"
        return None, f"결과 행을 받지 못했다(status={status} · {reason})"
    rows = [row for row in result.get("rows") or [] if isinstance(row, dict)]
    header = [str(column) for column in result.get("columns") or []]
    for row in rows[:1]:
        header.extend(str(column) for column in row if str(column) not in header)
    total = result.get("total_rows")
    truncated = bool(result.get("truncated"))
    known = isinstance(total, int) and not isinstance(total, bool)
    return {
        "header": header, "rows": rows, "truncated": truncated,
        "total": total if isinstance(total, int) and known else len(rows),
        # 잘린 결과에서 total_rows 가 없으면 전체 행 수를 모른다(받은 행 수 ≠ 전체).
        "total_known": known or not truncated,
    }, None


def _oracle_count(rows: list[dict[str, Any]]) -> int:
    """count 오라클 값 — 관례 열 `n` 이 있으면 그 합, 없으면 행 수."""
    if rows and COUNT_COLUMN in rows[0]:
        return int(sum(_number(row.get(COUNT_COLUMN)) or 0 for row in rows))
    return len(rows)


def _limit_hit(outcome: dict[str, Any]) -> list[str]:
    limits = outcome.get("limit_by_db") or {}
    return sorted(db for db, rows in (outcome.get("rows_by_db") or {}).items()
                  if isinstance(limits.get(db), int) and len(rows) >= limits[db])


def _key_tuples(rows: list[dict[str, Any]], columns: list[str],
                tag: str | None = None) -> set[tuple[str, ...]]:
    prefix = (tag,) if tag is not None else ()
    return {prefix + tuple(_key_cell(row.get(column)) for column in columns) for row in rows}


def _set_diff(oracle: set[tuple[str, ...]], system: set[tuple[str, ...]]) -> dict[str, Any]:
    missing = sorted(oracle - system)
    extra = sorted(system - oracle)
    return {
        "oracle_keys": len(oracle), "system_keys": len(system),
        "missing_count": len(missing), "extra_count": len(extra),
        "missing": [list(key) for key in missing[:DIFF_LIMIT]],
        "extra": [list(key) for key in extra[:DIFF_LIMIT]],
    }


def _keyset_verdict(match: str, oracle: set[tuple[str, ...]],
                    system: set[tuple[str, ...]]) -> tuple[str, dict[str, Any]]:
    detail = {"compare": "keyset", "match": match, **_set_diff(oracle, system)}
    if match == "subset":
        ok = system <= oracle
    elif match == "superset":
        ok = system >= oracle
    elif match == "disjoint":
        overlap = sorted(system & oracle)
        detail["overlap_count"] = len(overlap)
        detail["overlap"] = [list(key) for key in overlap[:DIFF_LIMIT]]
        ok = not overlap
    else:
        ok = system == oracle
    return ("pass" if ok else "fail"), detail


def _evaluate_fixture(spec: dict[str, Any], result: dict[str, Any] | None) -> tuple[str, Any]:
    compare = spec.get("compare")
    values, error = _fixture_values(spec)
    if error or values is None:
        return _hold(compare, f"정답표를 읽지 못했다 - {error}")
    system, reason = _system_rows(result)
    if system is None:
        return _hold(compare, reason or "결과 행 없음")
    if compare == "count":
        if not system["total_known"]:
            return _hold(compare, "결과가 잘렸고 전체 행 수를 모른다")
        detail = {"compare": "count", "oracle": len(values), "system": system["total"]}
        return ("pass" if system["total"] == len(values) else "fail"), detail
    match = str(spec.get("match", "equal"))
    if system["truncated"] and match in ("equal", "superset"):
        return _hold(compare, "결과가 잘려 전체 키 집합을 알 수 없다")
    oracle_keys: set[tuple[str, ...]] = {(_key_cell(value),) for value in values}
    if not system["rows"]:
        return _keyset_verdict(match, oracle_keys, set())
    column = _resolve((spec.get("key") or [None])[0], system["header"])
    if column is None:
        return _hold(compare, "시스템 결과에서 key 열을 찾지 못했다 - 별칭 목록 보강 필요",
                     header=system["header"])
    return _keyset_verdict(match, oracle_keys, _key_tuples(system["rows"], [column]))


def evaluate_oracle(spec: dict[str, Any], outcome: dict[str, Any] | None,
                    result: dict[str, Any] | None, *, pre: dict[str, Any] | None = None,
                    row_counts_by_db: dict[str, int] | None = None) -> tuple[str, Any]:
    """오라클 결과와 시스템 결과를 비교한다 → ("pass"|"fail"|"hold", 상세).

    Args:
        spec: `expect.oracle`.
        outcome: `run_oracle` 결과(턴 후 · `phase="post"`). source=fixture 면 무시하고 정답표를
            직접 읽는다(과거 run 재판정에도 DB 가 필요 없다).
        result: `Observation.result` 모양 — {"status", "columns", "rows": [{열: 문자열}],
            "total_rows", "truncated", "reason"}. 폼필 산출물(xlsx)도 이 모양으로 넘기면
            같게 판정한다.
        pre: `snapshot: pre_post` 의 턴 전 `run_oracle` 결과(O-4). 시스템 값이 전·후 사이에
            드는가를 본다.
        row_counts_by_db: `compare: count` · `system: row_counts_by_db` 의 시스템 쪽 —
            `Observation.row_counts_by_db`(감사 `query_executed` DB 별 행 수 · CSV 불필요).

    오라클 실패·0행·결과 미수집·열 미해결 → hold(불합격 아님 · 수동 사유 `oracle_unavailable`).
    불일치 → fail + 상세(행 수 · 키 차집합 상위 10 — 값은 키만 · 행 원문 없음).
    평가 중 예외도 올리지 않고 hold 로 돌려준다(러너 run 을 죽이지 않는다).
    """
    try:
        return _evaluate(spec, outcome, result, pre, row_counts_by_db)
    except Exception as exc:  # noqa: BLE001 — 판정기 결함이 run 을 죽이면 안 된다
        compare = spec.get("compare") if isinstance(spec, dict) else None
        return _hold(compare, f"오라클 평가 오류 - {type(exc).__name__}: {exc}")


def _evaluate(spec: Any, outcome: dict[str, Any] | None, result: dict[str, Any] | None,
              pre: dict[str, Any] | None,
              row_counts_by_db: dict[str, int] | None) -> tuple[str, Any]:
    if not isinstance(spec, dict):
        return _hold(None, "oracle 명세가 매핑이 아니다")
    compare = spec.get("compare")
    if spec.get("source", "sql") == "fixture":
        return _evaluate_fixture(spec, result)
    if not isinstance(outcome, dict):
        return _hold(compare, "오라클 미실행")
    if outcome.get("status") != "ok":
        return _hold(compare, f"오라클 불가 - {outcome.get('reason') or '사유 없음'}")
    rows_by_db: dict[str, list[dict[str, Any]]] = {
        str(db): [row for row in rows if isinstance(row, dict)]
        for db, rows in (outcome.get("rows_by_db") or {}).items()
    }
    if not any(rows_by_db.values()):
        return _hold(compare, "오라클 0행(정답을 구하지 못했다)")
    before: dict[str, list[dict[str, Any]]] | None = None
    if spec.get("snapshot") == "pre_post":
        if not isinstance(pre, dict) or pre.get("status") != "ok":
            reason = pre.get("reason") if isinstance(pre, dict) else "미실행"
            return _hold(compare, f"턴 전 오라클 불가 - {reason or '사유 없음'}")
        before = {str(db): list(rows) for db, rows in (pre.get("rows_by_db") or {}).items()}
        if not any(before.values()):
            return _hold(compare, "턴 전 오라클 0행")
    tol = float(spec.get("tol", DEFAULT_TOL))

    # 행 모집단을 쓰는 비교는 오라클이 행 상한에 닿으면 정답 집합이 잘렸을 수 있다 → 보류.
    counts_rows = compare == "count" and not any(
        rows and COUNT_COLUMN in rows[0] for rows in rows_by_db.values())
    limit_hit = _limit_hit(outcome) + (_limit_hit(pre) if before is not None and pre else [])
    if limit_hit and (counts_rows or compare in ("keyset", "rowset")):
        return _hold(compare, f"오라클이 행 상한에 닿았다({', '.join(limit_hit)})"
                              " - 정답이 잘렸을 수 있다")

    if compare == "count" and spec.get("system") == "row_counts_by_db":
        return _count_by_audit(rows_by_db, before, row_counts_by_db)
    system, reason = _system_rows(result)
    if system is None:
        return _hold(compare, reason or "결과 행 없음")
    per_db = SOURCE_DB_COLUMN in system["header"]
    if compare == "count":
        return _count_by_result(rows_by_db, before, system, per_db)
    if compare == "keyset":
        return _compare_keyset(spec, rows_by_db, before, system, per_db)
    if compare == "rowset":
        return _compare_rowset(rows_by_db, system, per_db, tol)
    if compare == "argmax":
        return _compare_argmax(spec, rows_by_db, system, per_db, tol)
    if compare == "value":
        return _compare_value(spec, rows_by_db, before, system, tol)
    return _hold(compare, f"모르는 compare - {compare!r}")


def _count_by_audit(rows_by_db: dict[str, list[dict[str, Any]]],
                    pre_rows: dict[str, list[dict[str, Any]]] | None,
                    audit: dict[str, int] | None) -> tuple[str, Any]:
    """오라클 DB 별 수 ↔ 감사 `row_counts_by_db`. 합이 아니라 **DB 별로** 맞아야 통과한다.

    합만 보면 한 DB 의 +1 과 다른 DB 의 -1 이 상쇄된다(`_source_db` 분리 · plans/122 §9.2 ③).
    """
    if not isinstance(audit, dict) or not audit:
        return _hold("count", "감사 row_counts_by_db 없음(시스템 쪽 행 수 미관측)")
    system = {str(db): int(count) for db, count in audit.items()}
    oracle = {db: _oracle_count(rows) for db, rows in rows_by_db.items()}
    detail: dict[str, Any] = {
        "compare": "count", "system_source": "row_counts_by_db", "oracle": oracle,
        "system": system, "oracle_total": sum(oracle.values()),
        "system_total": sum(system.values()),
    }
    if pre_rows is not None:
        before = sum(_oracle_count(rows) for rows in pre_rows.values())
        low, high = sorted((before, detail["oracle_total"]))
        detail.update(pre_total=before, low=low, high=high)
        return ("pass" if low <= detail["system_total"] <= high else "fail"), detail
    mismatch = sorted(db for db in set(oracle) | set(system) if oracle.get(db) != system.get(db))
    detail["mismatch_dbs"] = mismatch
    return ("fail" if mismatch else "pass"), detail


def _count_by_result(rows_by_db: dict[str, list[dict[str, Any]]],
                     pre_rows: dict[str, list[dict[str, Any]]] | None,
                     system: dict[str, Any], per_db: bool) -> tuple[str, Any]:
    if not system["total_known"]:
        return _hold("count", "결과가 잘렸고 전체 행 수를 모른다")
    oracle = {db: _oracle_count(rows) for db, rows in rows_by_db.items()}
    detail: dict[str, Any] = {
        "compare": "count", "system_source": "result", "oracle": oracle,
        "oracle_total": sum(oracle.values()), "system_total": system["total"],
    }
    if pre_rows is not None:
        before = sum(_oracle_count(rows) for rows in pre_rows.values())
        low, high = sorted((before, detail["oracle_total"]))
        detail.update(pre_total=before, low=low, high=high)
        return ("pass" if low <= system["total"] <= high else "fail"), detail
    if per_db and not system["truncated"]:
        by_db = Counter(str(row.get(SOURCE_DB_COLUMN) or "") for row in system["rows"])
        detail["system"] = dict(by_db)
        mismatch = sorted(db for db in set(oracle) | set(by_db)
                          if oracle.get(db, 0) != by_db.get(db, 0))
        detail["mismatch_dbs"] = mismatch
        return ("fail" if mismatch else "pass"), detail
    return ("pass" if system["total"] == detail["oracle_total"] else "fail"), detail


def _compare_keyset(spec: dict[str, Any], rows_by_db: dict[str, list[dict[str, Any]]],
                    pre_rows: dict[str, list[dict[str, Any]]] | None,
                    system: dict[str, Any], per_db: bool) -> tuple[str, Any]:
    match = str(spec.get("match", "equal"))
    if system["truncated"] and match in ("equal", "superset"):
        return _hold("keyset", "결과가 잘려 전체 키 집합을 알 수 없다")
    refs = list(spec.get("key") or [])
    oracle_header = _oracle_header(rows_by_db)
    oracle_columns = [_resolve(ref, oracle_header) for ref in refs]
    if None in oracle_columns:
        return _hold("keyset", "오라클 결과에 key 열이 없다 - 정본 SQL 별칭 확인",
                     header=oracle_header)
    ocols = [column for column in oracle_columns if column is not None]

    def oracle_keys(source: dict[str, list[dict[str, Any]]]) -> set[tuple[str, ...]]:
        keys: set[tuple[str, ...]] = set()
        for db, rows in source.items():
            keys |= _key_tuples(rows, ocols, db if per_db else None)
        return keys

    expected = oracle_keys(rows_by_db)
    if system["rows"]:
        system_columns = [_resolve(ref, system["header"]) for ref in refs]
        if None in system_columns:
            return _hold("keyset", "시스템 결과에서 key 열을 찾지 못했다 - 별칭 목록 보강 필요",
                         header=system["header"])
        scols = [column for column in system_columns if column is not None]
        observed: set[tuple[str, ...]] = set()
        for row in system["rows"]:
            tag = (str(row.get(SOURCE_DB_COLUMN) or ""),) if per_db else ()
            observed.add(tag + tuple(_key_cell(row.get(column)) for column in scols))
    else:
        observed = set()
    if pre_rows is None:
        return _keyset_verdict(match, expected, observed)
    before = oracle_keys(pre_rows)
    stable, either = before & expected, before | expected
    detail = {"compare": "keyset", "match": "equal", "snapshot": "pre_post",
              **_set_diff(either, observed)}
    lost = sorted(stable - observed)
    foreign = sorted(observed - either)
    detail.update(stable_keys=len(stable), union_keys=len(either),
                  lost_stable=[list(key) for key in lost[:DIFF_LIMIT]],
                  outside_union=[list(key) for key in foreign[:DIFF_LIMIT]])
    return ("pass" if not lost and not foreign else "fail"), detail


def _normalized_rows(rows: list[dict[str, Any]], drop: Iterable[str] = ()) -> list[dict[str, Any]]:
    dropped = set(drop)
    return [{column: _norm_value(value) for column, value in row.items() if column not in dropped}
            for row in rows]


def _compare_rowset(rows_by_db: dict[str, list[dict[str, Any]]], system: dict[str, Any],
                    per_db: bool, tol: float) -> tuple[str, Any]:
    """오라클 열마다 값 멀티셋이 시스템 어떤 열과 일치하는가 + 행 수 동일
    (`eval_text2sql.column_subset_unmatched` 재사용).

    열 이름·별칭과 무관하게 값으로 대응한다 — 시스템 CSV 의 열 이름은 LLM 재량이다(plans/94 §1.2 ·
    `eval_text2sql` subset 판정).
    """
    if system["truncated"]:
        return _hold("rowset", "결과가 잘려 행 집합을 비교할 수 없다")
    groups: dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]] = {}
    if per_db:
        system_by_db: dict[str, list[dict[str, Any]]] = {}
        for row in system["rows"]:
            system_by_db.setdefault(str(row.get(SOURCE_DB_COLUMN) or ""), []).append(row)
        for db in sorted(set(rows_by_db) | set(system_by_db)):
            groups[db] = (rows_by_db.get(db, []), system_by_db.get(db, []))
    else:
        groups["*"] = ([row for rows in rows_by_db.values() for row in rows], system["rows"])
    by_db: dict[str, Any] = {}
    ok = True
    for db, (gold, pred) in groups.items():
        unmatched = column_subset_unmatched(
            _normalized_rows(gold), _normalized_rows(pred, drop=[SOURCE_DB_COLUMN]), float_tol=tol)
        entry: dict[str, Any] = {"oracle_rows": len(gold), "system_rows": len(pred)}
        if unmatched is None:
            entry["mismatch"] = "행 수 다름"
            ok = False
        elif unmatched:
            entry["unmatched_columns"] = unmatched
            ok = False
        by_db[db] = entry
    detail = {"compare": "rowset", "tol": tol, "per_db": per_db,
              "oracle_rows": sum(len(g) for g, _p in groups.values()),
              "system_rows": len(system["rows"]), "by_db": by_db}
    return ("pass" if ok else "fail"), detail


def _pairs(rows: list[dict[str, Any]], key_columns: list[str], value_column: str,
           tag: str | None) -> list[tuple[tuple[str, ...], float]]:
    prefix = (tag,) if tag is not None else ()
    out: list[tuple[tuple[str, ...], float]] = []
    for row in rows:
        value = _number(row.get(value_column))
        if value is not None:
            out.append((prefix + tuple(_key_cell(row.get(c)) for c in key_columns), value))
    return out


def _compare_argmax(spec: dict[str, Any], rows_by_db: dict[str, list[dict[str, Any]]],
                    system: dict[str, Any], per_db: bool, tol: float) -> tuple[str, Any]:
    """상위 N 키 집합 + 그 값(tol). 동점은 허용 집합으로 흡수한다.

    시스템 CSV 는 재정렬 전 원본이 저장되므로(`result_organizer`) 하네스가 값 열로 다시 정렬한다.
    값 열을 못 찾아도 시스템 행이 정확히 N 개면 그 키만 판정한다(값 대조 생략 — 상세에 적는다).
    """
    if system["truncated"]:
        return _hold("argmax", "결과가 잘려 상위 N 을 다시 정렬할 수 없다")
    refs = list(spec.get("key") or [])
    oracle_header = _oracle_header(rows_by_db)
    ocols = [_resolve(ref, oracle_header) for ref in refs]
    ovalue = _resolve(spec.get("value"), oracle_header)
    if None in ocols or ovalue is None:
        return _hold("argmax", "오라클 결과에 key·value 열이 없다 - 정본 SQL 별칭 확인",
                     header=oracle_header)
    oracle_pairs: list[tuple[tuple[str, ...], float]] = []
    for db, rows in rows_by_db.items():
        oracle_pairs += _pairs(rows, [c for c in ocols if c], ovalue, db if per_db else None)
    if not oracle_pairs:
        return _hold("argmax", "오라클 value 가 전부 비었다")
    oracle_pairs.sort(key=lambda pair: pair[1], reverse=True)
    top = int(spec.get("top", 1))
    n = min(top, len(oracle_pairs))
    threshold = oracle_pairs[n - 1][1]
    must = {key for key, value in oracle_pairs if value > threshold + tol}
    allowed = {key for key, value in oracle_pairs if value >= threshold - tol}
    oracle_value: dict[tuple[str, ...], float] = {}
    for key, value in oracle_pairs:
        oracle_value.setdefault(key, value)
    detail: dict[str, Any] = {
        "compare": "argmax", "top": n, "tol": tol, "per_db": per_db,
        "oracle_top": [[*key, round(value, 4)] for key, value in oracle_pairs[:n]][:DIFF_LIMIT],
        "tie_candidates": len(allowed),
    }
    if not system["rows"]:
        detail["system_top"] = []
        return "fail", detail
    scols = [_resolve(ref, system["header"]) for ref in refs]
    if None in scols:
        return _hold("argmax", "시스템 결과에서 key 열을 찾지 못했다 - 별칭 목록 보강 필요",
                     header=system["header"])
    svalue = _resolve(spec.get("value"), system["header"])

    def system_key(row: dict[str, Any]) -> tuple[str, ...]:
        tag = (str(row.get(SOURCE_DB_COLUMN) or ""),) if per_db else ()
        return tag + tuple(_key_cell(row.get(c)) for c in scols if c)

    system_values: dict[tuple[str, ...], float] = {}
    if svalue is not None:
        ranked: list[tuple[tuple[str, ...], float]] = []
        for row in system["rows"]:
            number = _number(row.get(svalue))
            if number is not None:
                ranked.append((system_key(row), number))
        ranked.sort(key=lambda pair: pair[1], reverse=True)
        system_top = [key for key, _value in ranked[:n]]
        system_values = dict(ranked[:n])
    elif len(system["rows"]) == n:
        system_top = [system_key(row) for row in system["rows"]]
        detail["value_check"] = "시스템 value 열 미해결 - 행이 정확히 N 개라 키만 판정했다"
    else:
        return _hold("argmax", "시스템 결과에서 value 열을 찾지 못했다 - 별칭 목록 보강 필요",
                     header=system["header"])
    chosen = set(system_top)
    detail["system_top"] = [list(key) for key in system_top][:DIFF_LIMIT]
    missing_must = sorted(must - chosen)
    not_allowed = sorted(chosen - allowed)
    diffs = [
        {"key": list(key), "oracle": round(oracle_value[key], 4), "system": round(value, 4)}
        for key, value in system_values.items()
        if key in oracle_value and abs(value - oracle_value[key]) > tol + 1e-9
    ]
    detail.update(missing_must=[list(k) for k in missing_must[:DIFF_LIMIT]],
                  not_allowed=[list(k) for k in not_allowed[:DIFF_LIMIT]],
                  value_diffs=diffs[:DIFF_LIMIT])
    ok = len(chosen) == n and not missing_must and not not_allowed and not diffs
    return ("pass" if ok else "fail"), detail


def _scalar(rows_by_db: dict[str, list[dict[str, Any]]],
            ref: Any) -> tuple[float | None, str | None]:
    rows = [row for rows in rows_by_db.values() for row in rows]
    if len(rows) != 1:
        return None, f"오라클이 스칼라(전 DB 합쳐 1행)가 아니다 - {len(rows)}행"
    column = _resolve(ref, list(rows[0]))
    if column is None:
        return None, "오라클 결과에 value 열이 없다 - 정본 SQL 별칭 확인"
    value = _number(rows[0].get(column))
    if value is None:
        return None, "오라클 value 가 수가 아니다"
    return value, None


def _compare_value(spec: dict[str, Any], rows_by_db: dict[str, list[dict[str, Any]]],
                   pre_rows: dict[str, list[dict[str, Any]]] | None,
                   system: dict[str, Any], tol: float) -> tuple[str, Any]:
    oracle, error = _scalar(rows_by_db, spec.get("value"))
    if oracle is None:
        return _hold("value", error or "오라클 값 없음")
    detail: dict[str, Any] = {"compare": "value", "tol": tol, "oracle": oracle}
    low = high = oracle
    if pre_rows is not None:
        before, error = _scalar(pre_rows, spec.get("value"))
        if before is None:
            return _hold("value", f"턴 전 오라클 - {error}")
        low, high = sorted((before, oracle))
        detail.update(snapshot="pre_post", pre=before, low=low, high=high)
    if not system["rows"]:
        detail["system"] = None
        return "fail", detail
    if len(system["rows"]) != 1:
        count = len(system["rows"])
        return _hold("value", f"시스템 결과가 1행이 아니다({count}행) - 스칼라 비교 불가")
    column = _resolve(spec.get("value"), system["header"])
    if column is None:
        return _hold("value", "시스템 결과에서 value 열을 찾지 못했다 - 별칭 목록 보강 필요",
                     header=system["header"])
    value = _number(system["rows"][0].get(column))
    detail["system"] = value
    if value is None:
        return "fail", detail
    return ("pass" if low - tol - 1e-9 <= value <= high + tol + 1e-9 else "fail"), detail


# --- 수동 실행 CLI (O-3 DB2 폐쇄망 1회 검수 · O-5 G-3 판정) ---------------------

def _cli(argv: list[str] | None = None) -> int:
    """정본 1건을 대상 DB 에서 읽기 전용으로 돌려 행 수를 보인다(사람 검수용).

    예) python -m scripts.scenario.oracle C-06-g3 --db polestar_b0 --db polestar_cm_gp
    `--show-rows` 면 마스킹된 행을 최대 20행 보인다(기본은 행 수만). 로그는 `--log` 에 남는다.
    """
    parser = argparse.ArgumentParser(prog="python -m scripts.scenario.oracle",
                                     description="시나리오 오라클 정본 수동 실행(읽기 전용)")
    parser.add_argument("oracle_id")
    parser.add_argument("--db", action="append", required=True, help="대상 db_id (반복 가능)")
    parser.add_argument("--anchor-at", default=None, help="앵커(KST ISO) - 기본: 지금")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--log", default=str(CLI_LOG))
    parser.add_argument("--show-rows", action="store_true")
    args = parser.parse_args(argv)
    anchor = args.anchor_at or _now_kst()
    outcome = run_oracle({"id": args.oracle_id, "source": "sql"}, db_ids=args.db, anchor_at=anchor,
                         run_id="cli", scenario_id=args.oracle_id, log_path=Path(args.log),
                         timeout_sec=args.timeout, phase="cli")
    print(f"oracle={args.oracle_id} anchor={anchor} status={outcome['status']} "
          f"elapsed_ms={outcome['elapsed_ms']}")
    if outcome.get("reason"):
        print(f"  reason: {outcome['reason']}")
    for db_id, rows in outcome["rows_by_db"].items():
        print(f"  {db_id}: {len(rows)}행 (상한 {outcome['limit_by_db'].get(db_id)})")
        if args.show_rows:
            for row in rows[:20]:
                print(f"    {json.dumps(row, ensure_ascii=False, default=str)}")
    print(f"  log: {args.log}")
    return 0 if outcome["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(_cli())
