#!/usr/bin/env python3
"""Text-to-SQL EX(Execution Accuracy) 평가 하네스 - Plan 61 / E1(D-072).

폴스타 자연어→SQL 파이프라인의 품질을 **결과집합 동치(EX)** 로 배치 채점하는 오프라인 CLI.
순수 신규 파일이며 런타임 경로(src/)를 변경하지 않는다(오프라인 배치 도구).

핵심 기능
---------
- 골드셋 로드/검증: `testdata/text2sql_gold/*.yaml`
- EX 채점(`execution_match`): Spider/BIRD 방식 참조 - 정렬 무관 멀티셋, 컬럼순서·별칭 무관, 부동소수 tolerance
- 3경로 구동(§7 경로 커버리지): `--path {graph,orchestration,multidb}`
- A/B 축: `--synonym-fuzzy`, `--value-retrieval`, `--candidate-count`, `--selection`, `--semantic-compose`
  (os.environ 플래그 세팅 + `load_config.cache_clear()`로 파이프라인 토글)
- 트랙 C 전용 축: SMQ 생성 정확도(훅), 커버리지율(coverage=inside 비율)
- 오프라인/폐쇄망: `--dry-run`(미실행 통계) / `--mock`(주입형 mock) / 실제(접속 실패 시 graceful 스킵)

사용 예
-------
    python scripts/eval_text2sql.py --dry-run
    python scripts/eval_text2sql.py --mock
    python scripts/eval_text2sql.py --mock --ab semantic_compose
    python scripts/eval_text2sql.py --path orchestration --db gp        # 실제(접속 필요, 없으면 스킵)
    python scripts/eval_text2sql.py --mock --json
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean
from typing import Any, Optional, Protocol

import yaml

# ──────────────────────────────────────────────
# 상수
# ──────────────────────────────────────────────

CATEGORIES = ("server_config", "performance", "alarm", "complex", "unhandled")
COVERAGES = ("inside", "outside")
PATHS = ("graph", "orchestration", "multidb")
SELECTIONS = ("consistency", "llm", "hybrid")

# 골드셋 SQL 안전성(SELECT 전용) 검사용 금지 키워드 - 문장 선두/독립 토큰만 위반으로 본다.
_FORBIDDEN_STMT = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|merge|grant|revoke|"
    r"replace|call|exec|execute)\b"
)

# A/B 축 → 파이프라인 플래그 키 / 환경변수 매핑.
# 환경변수는 (config.py가 해당 플래그를 아직 노출하지 않아도) 무해하게 세팅된다 - 전방호환.
FLAG_ENV: dict[str, str] = {
    "multi_candidate": "TEXT2SQL_MULTI_CANDIDATE",
    "candidate_count": "TEXT2SQL_CANDIDATE_COUNT",
    "selection": "TEXT2SQL_SELECTION",
    "synonym_fuzzy": "SYNONYM_FUZZY_MATCH",
    "value_retrieval": "SYNONYM_VALUE_RETRIEVAL",
    "semantic_compose": "TEXT2SQL_SEMANTIC_COMPOSE",
}

# `--ab <axis>` 지원 축과 baseline/variant 플래그.
AB_AXES = ("synonym_fuzzy", "value_retrieval", "candidate_count", "selection", "semantic_compose")


# ──────────────────────────────────────────────
# 데이터 모델
# ──────────────────────────────────────────────

@dataclass
class GoldItem:
    """골드셋 단일 항목."""

    id: str
    query: str
    db_id: str
    gold_sql: str
    category: str
    coverage: str
    gold_result_signature: Optional[dict] = None
    gold_smq: Optional[dict] = None
    notes: str = ""
    source_file: str = ""
    # 채점 모드: strict(결과집합 완전 동치, 기본) | subset(골드 컬럼 값이 pred의 부분집합이면
    # 통과 - 알람 뷰처럼 파이프라인이 표준 컬럼+α를 내는 것이 정상인 항목에 선언)
    scoring: str = "strict"
    # 의도된 FAIL 사유(빈 값=일반 채점). 알려진 파이프라인 결함을 감시하는 항목에 선언 -
    # FAIL이 정상 상태이며 골드를 pred에 맞추면 안 된다는 표식(사용자 원칙 2026-07-21:
    # "의도한 false-negative라면 굳이 PASS로 맞출 필요 없음"). PASS로 전환되면 결함이
    # 수정된 것이므로 리포트가 해제 검토를 안내한다. 집계에는 일반 FAIL과 동일하게 계상.
    known_fail: str = ""


@dataclass
class PredictionResult:
    """예측기(파이프라인 또는 mock)의 산출."""

    sql: Optional[str]
    smq: Optional[dict] = None
    retries: int = 0
    llm_calls: int = 0
    tokens: int = 0
    latency_ms: float = 0.0
    skipped: bool = False
    error: Optional[str] = None


@dataclass
class ItemResult:
    """항목별 채점 결과."""

    id: str
    db_id: str
    category: str
    coverage: str
    ex_scored: bool
    ex_pass: Optional[bool]
    ex_skip_reason: Optional[str]
    smq_scored: bool
    smq_pass: Optional[bool]
    retries: int
    llm_calls: int
    tokens: int
    latency_ms: float
    pred_sql: Optional[str]
    error: Optional[str]
    # FAIL 진단용(폐쇄망에서 원인 특정) - 골드/예측 결과의 행수·컬럼수
    gold_row_count: Optional[int] = None
    pred_row_count: Optional[int] = None
    gold_col_count: Optional[int] = None
    pred_col_count: Optional[int] = None
    # 보조 지표: 컬럼 스코프 무시 값 일치(EX-subset) - strict FAIL이어도 값은 맞는지
    ex_subset_pass: Optional[bool] = None
    # FAIL 진단: pred에서 값 일치 상대를 못 찾은 골드 컬럼명(행수 불일치면 None)
    subset_unmatched_cols: Optional[list] = None
    # 의도된 FAIL 사유(골드 known_fail 전사) - FAIL이 정상 상태인 감시 항목 표식
    known_fail: str = ""


@dataclass
class BatchReport:
    """한 실행(플래그 조합 1개)의 항목 결과 모음."""

    label: str
    items: list[ItemResult] = field(default_factory=list)


# ──────────────────────────────────────────────
# 골드셋 로드 / 검증
# ──────────────────────────────────────────────

def _default_gold_dir() -> Path:
    """기본 골드셋 디렉터리(리포지토리 testdata/text2sql_gold)를 반환한다."""
    return Path(__file__).resolve().parent.parent / "testdata" / "text2sql_gold"


def load_goldset(gold_dir: str | Path, db_filter: Optional[str] = None) -> list[GoldItem]:
    """골드셋 YAML 파일들을 로드해 GoldItem 목록으로 반환한다.

    Args:
        gold_dir: 골드셋 디렉터리 경로.
        db_filter: 'gp'|'yd'|'b0'|'all'|None - 특정 파일만 로드(파일명 접두).

    Returns:
        GoldItem 목록.
    """
    gold_dir = Path(gold_dir)
    items: list[GoldItem] = []
    for yaml_path in sorted(gold_dir.glob("*.yaml")):
        stem = yaml_path.stem
        if db_filter and db_filter != "all" and stem != db_filter:
            continue
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        raw_items = data.get("items", []) if isinstance(data, dict) else []
        for raw in raw_items:
            items.append(
                GoldItem(
                    id=raw.get("id", ""),
                    query=raw.get("query", ""),
                    db_id=raw.get("db_id", ""),
                    gold_sql=raw.get("gold_sql", ""),
                    category=raw.get("category", ""),
                    coverage=raw.get("coverage", ""),
                    gold_result_signature=raw.get("gold_result_signature"),
                    gold_smq=raw.get("gold_smq"),
                    notes=raw.get("notes", ""),
                    source_file=yaml_path.name,
                    scoring=raw.get("scoring", "strict"),
                    known_fail=str(raw.get("known_fail", "") or ""),
                )
            )
    return items


def _strip_sql_comments(sql: str) -> str:
    """SQL 주석(라인/블록)을 제거한다(안전성 검사 전처리)."""
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return sql


def is_select_only(sql: str) -> bool:
    """SQL이 단일 SELECT/WITH 읽기전용 문인지 검사한다(D-003 안전장치).

    Args:
        sql: 검사할 SQL 문자열.

    Returns:
        읽기전용 단일 조회문이면 True.
    """
    body = _strip_sql_comments(sql or "").strip().rstrip(";").strip()
    if not body:
        return False
    # 다중 문장 금지(문장 구분 세미콜론이 본문 중간에 존재)
    if ";" in body:
        return False
    first = re.split(r"\s+", body, maxsplit=1)[0].lower()
    if first not in ("select", "with"):
        return False
    # 데이터 변경 키워드가 독립 토큰으로 존재하면 거부(update_time 등은 \b 경계로 제외됨)
    if _FORBIDDEN_STMT.search(body.lower()):
        return False
    return True


def validate_goldset(items: list[GoldItem]) -> list[str]:
    """골드셋 스키마 위반을 문자열 목록으로 반환한다(빈 목록 = 통과).

    검사 항목: 필수 필드 존재, id 유일성, category/coverage enum, gold_sql SELECT 전용.
    """
    errors: list[str] = []
    seen: set[str] = set()
    for i, item in enumerate(items):
        where = item.id or f"#{i}({item.source_file})"
        if not item.id:
            errors.append(f"[{where}] id 누락")
        elif item.id in seen:
            errors.append(f"[{where}] id 중복")
        else:
            seen.add(item.id)
        if not item.query:
            errors.append(f"[{where}] query 누락")
        if not item.db_id:
            errors.append(f"[{where}] db_id 누락")
        if not item.gold_sql:
            errors.append(f"[{where}] gold_sql 누락")
        elif not is_select_only(item.gold_sql):
            errors.append(f"[{where}] gold_sql이 SELECT 전용 단일 조회문이 아님")
        if item.category not in CATEGORIES:
            errors.append(f"[{where}] category 값 오류: {item.category!r} (허용: {CATEGORIES})")
        if item.coverage not in COVERAGES:
            errors.append(f"[{where}] coverage 값 오류: {item.coverage!r} (허용: {COVERAGES})")
        if item.scoring not in ("strict", "subset"):
            errors.append(f"[{where}] scoring 값 오류: {item.scoring!r} (허용: strict|subset)")
    return errors


# ──────────────────────────────────────────────
# EX 채점 (순수 함수) - Spider/BIRD 결과집합 동치 참조
# ──────────────────────────────────────────────

def _float_decimals(tol: float) -> int:
    """tolerance로부터 반올림 소수 자릿수를 도출한다."""
    if tol <= 0:
        return 12
    return max(0, int(round(-math.log10(tol))))


def _norm_cell(value: Any, decimals: int) -> tuple:
    """셀 값을 타입-태그된 정규형 튜플로 변환한다(부동소수 tolerance 적용).

    - None은 고유 sentinel로 구분(빈 문자열과 다름)
    - 정수/실수/Decimal은 실수로 통일 후 반올림(100 == 100.0)
    - 나머지는 trim된 문자열
    """
    if value is None:
        return ("null",)
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, int):
        return ("num", round(float(value), decimals))
    if isinstance(value, float):
        return ("num", round(value, decimals))
    # Decimal 등 실수형 문자열 변환 가능한 경우
    try:
        from decimal import Decimal

        if isinstance(value, Decimal):
            return ("num", round(float(value), decimals))
    except Exception:  # pragma: no cover - decimal는 표준
        pass
    return ("str", str(value).strip())


def _norm_row(row: Any, decimals: int, ignore_column_order: bool) -> tuple:
    """행(dict/list/tuple/scalar)을 정규형 셀 튜플로 변환한다."""
    if isinstance(row, dict):
        cells = list(row.values())
    elif isinstance(row, (list, tuple)):
        cells = list(row)
    else:
        cells = [row]
    normed = [_norm_cell(c, decimals) for c in cells]
    if ignore_column_order:
        normed.sort(key=repr)
    return tuple(normed)


def execution_match(
    gold_rows: Optional[list],
    pred_rows: Optional[list],
    *,
    float_tol: float = 1e-6,
    ignore_column_order: bool = True,
    order_sensitive: bool = False,
) -> bool:
    """두 결과집합이 동치인지 판정한다(EX 채점).

    Spider/BIRD 하네스 방식 참조:
    - 정렬 무관 멀티셋 비교(order_sensitive=False, 기본) - ORDER BY 무시
    - 컬럼 순서·별칭 무관(ignore_column_order=True, 기본) - 행 내 값 정렬로 흡수
    - 부동소수 tolerance(float_tol)

    Args:
        gold_rows: 골드 SQL 결과행(dict/tuple 목록). None이면 채점 불가.
        pred_rows: 예측 SQL 결과행. None이면 채점 불가.
        float_tol: 부동소수 허용 오차.
        ignore_column_order: 행 내 컬럼 순서/별칭 차이를 무시할지.
        order_sensitive: 행 순서를 엄격 비교할지(ORDER BY 검증용).

    Returns:
        결과집합이 동치이면 True. 한쪽이라도 None이면 False.
    """
    if gold_rows is None or pred_rows is None:
        return False
    decimals = _float_decimals(float_tol)
    g = [_norm_row(r, decimals, ignore_column_order) for r in gold_rows]
    p = [_norm_row(r, decimals, ignore_column_order) for r in pred_rows]
    if order_sensitive:
        return g == p
    return Counter(g) == Counter(p)


def _labeled_col_counters(rows: list, decimals: int) -> tuple[list[str], list[Counter]]:
    """행 목록을 (컬럼 라벨, 컬럼별 값 멀티셋 Counter) 쌍으로 변환한다(dict 행은 키, tuple 행은 col{i})."""
    first = rows[0]
    if isinstance(first, dict):
        keys = list(first.keys())
        return keys, [
            Counter(_norm_cell((r or {}).get(k), decimals) for r in rows) for k in keys
        ]
    width = len(first) if isinstance(first, (list, tuple)) else 1

    def _cell(row: Any, i: int) -> Any:
        return row[i] if isinstance(row, (list, tuple)) else row

    labels = [f"col{i}" for i in range(width)]
    return labels, [
        Counter(_norm_cell(_cell(r, i), decimals) for r in rows) for i in range(width)
    ]


def column_subset_unmatched(
    gold_rows: Optional[list],
    pred_rows: Optional[list],
    *,
    float_tol: float = 1e-6,
) -> Optional[list[str]]:
    """subset 판정에서 pred에 값 멀티셋 일치 상대가 없는 골드 컬럼명 목록을 반환한다(FAIL 진단).

    행수가 다르거나 결과가 없으면 None(컬럼 단위 진단 무의미 - 행수부터 원인).
    빈 리스트 = 전 골드 컬럼이 pred에서 값 일치 상대를 찾음(subset 통과).
    골드 중복 컬럼(예: 알람 뷰의 장비명=리소스명, 값 동일)이 같은 pred 컬럼에 대응하는 것을
    허용한다 - 값 멀티셋이 같으면 재사용해도 의미상 동치(폐쇄망 실측 2026-07-21 yd-006:
    1:1 강제 시 pred가 이름 컬럼을 하나만 내면 값이 다 맞아도 subset 실패).
    """
    if gold_rows is None or pred_rows is None or len(gold_rows) != len(pred_rows):
        return None
    if not gold_rows:
        return []
    decimals = _float_decimals(float_tol)
    g_labels, g_cols = _labeled_col_counters(gold_rows, decimals)
    _, p_cols = _labeled_col_counters(pred_rows, decimals)
    return [
        label for label, gc in zip(g_labels, g_cols)
        if not any(pc == gc for pc in p_cols)
    ]


def column_subset_match(
    gold_rows: Optional[list],
    pred_rows: Optional[list],
    *,
    float_tol: float = 1e-6,
) -> bool:
    """골드의 각 컬럼이 pred 컬럼들의 부분집합으로 값 일치하는지 판정한다(보조 지표 EX-subset).

    실측(2026-07-20 yd): 파이프라인이 질의 밖 컬럼을 추가하고 그 수도 실행마다 흔들려
    (7↔8, 12↔13) strict EX가 컬럼 스코프에서 전멸 - 값 정확성과 스코프 규율을 분리 측정한다.
    판정: 행수 동일 + 골드의 각 컬럼 값 멀티셋이 pred의 어떤 컬럼 값 멀티셋과 일치(골드
    중복 컬럼은 같은 pred 컬럼 재사용 허용). 행 단위 정합의 컬럼 단위 근사이나 실데이터
    오탐 확률은 무시 가능. strict EX를 대체하지 않는 보조 지표이며, ``scoring: subset``
    선언 항목에서만 통과 기준으로 승격된다.
    """
    unmatched = column_subset_unmatched(gold_rows, pred_rows, float_tol=float_tol)
    return unmatched is not None and not unmatched


# ──────────────────────────────────────────────
# SMQ 생성 정확도 (트랙 C 훅) - 골드 SMQ가 있을 때만 채점
# ──────────────────────────────────────────────

def _smq_norm_list(value: Any) -> frozenset:
    """SMQ 리스트 필드를 순서 무관 비교 가능한 frozenset으로 정규화한다."""
    if not value:
        return frozenset()
    out: list[str] = []
    for v in value:
        if isinstance(v, dict):
            out.append(json.dumps(v, sort_keys=True, ensure_ascii=False))
        else:
            out.append(str(v))
    return frozenset(out)


def smq_match(gold_smq: Optional[dict], pred_smq: Optional[dict]) -> bool:
    """골드 SMQ와 예측 SMQ의 일치 여부를 판정한다(dimension·measure·필터·시간창·패턴).

    골드/예측 중 하나라도 없으면 False(채점 대상 아님은 호출부에서 분리).
    """
    if not gold_smq or not pred_smq:
        return False
    if str(gold_smq.get("pattern")) != str(pred_smq.get("pattern")):
        return False
    for key in ("dimensions", "measures", "resource_types", "entities", "filters"):
        if _smq_norm_list(gold_smq.get(key)) != _smq_norm_list(pred_smq.get(key)):
            return False
    if gold_smq.get("time_grain") != pred_smq.get("time_grain"):
        return False
    if bool(gold_smq.get("active_only")) != bool(pred_smq.get("active_only")):
        return False
    return True


# ──────────────────────────────────────────────
# 커버리지율 집계 (트랙 C 축)
# ──────────────────────────────────────────────

def coverage_stats(items: list[GoldItem]) -> dict:
    """골드셋의 커버리지율(coverage=inside 비율)과 카테고리 분포를 집계한다."""
    total = len(items)
    inside = sum(1 for i in items if i.coverage == "inside")
    by_category: dict[str, dict[str, int]] = {}
    for i in items:
        cell = by_category.setdefault(i.category, {"inside": 0, "outside": 0, "total": 0})
        if i.coverage in ("inside", "outside"):
            cell[i.coverage] += 1
        cell["total"] += 1
    return {
        "total": total,
        "inside": inside,
        "outside": total - inside,
        "coverage_rate": (inside / total) if total else 0.0,
        "by_category": by_category,
        "by_db": _count_by(items, lambda i: i.db_id),
    }


def _count_by(items: list[GoldItem], key) -> dict[str, int]:
    """항목을 키 함수 기준으로 카운트한다."""
    out: dict[str, int] = {}
    for i in items:
        out[key(i)] = out.get(key(i), 0) + 1
    return out


# ──────────────────────────────────────────────
# 예측기 / 실행기 프로토콜
# ──────────────────────────────────────────────

class Predictor(Protocol):
    """자연어 질의 → SQL 예측기."""

    def predict(self, item: GoldItem, flags: dict) -> PredictionResult: ...


class Executor(Protocol):
    """SQL → 결과행 실행기(EX 채점용)."""

    def execute(self, sql: str, db_id: str) -> Optional[list]: ...


def _canon_sql(sql: str) -> str:
    """SQL을 공백·대소문자 정규화한다(mock 결정적 실행/동치 키용)."""
    return re.sub(r"\s+", " ", (sql or "").strip()).lower().rstrip(";").strip()


class MockExecutor:
    """결정적 mock 실행기 - 동일 SQL은 동일 결과행, 다른 SQL은 (대개) 다른 결과행.

    실제 DB 없이 EX 채점 로직을 구동하기 위한 주입형 실행기.
    fail_sql_substrings에 포함된 토큰이 SQL에 있으면 실행 실패(None)를 흉내낸다.
    """

    def __init__(self, fail_sql_substrings: Optional[list[str]] = None) -> None:
        self.fail_sql_substrings = fail_sql_substrings or []

    def execute(self, sql: str, db_id: str) -> Optional[list]:
        """SQL을 결정적 합성 결과행으로 매핑한다(실패 흉내면 None)."""
        canon = _canon_sql(sql)
        for tok in self.fail_sql_substrings:
            if tok.lower() in canon:
                return None
        seed = int(hashlib.sha1(f"{db_id}|{canon}".encode("utf-8")).hexdigest()[:8], 16)
        # 동일 canon → 동일 seed → 동일 결과행(멀티셋 동치)
        n_rows = (seed % 3) + 1
        rows = []
        for k in range(n_rows):
            rows.append({"c0": (seed + k) % 1000, "c1": round(((seed >> (k + 1)) % 10000) / 100.0, 2)})
        return rows


class MockPredictor:
    """주입형 mock 예측기(오프라인 시연·단위 테스트용).

    정책(결정적):
    - coverage=inside: 골드 SQL을 그대로 반환(EX 통과 유도).
    - coverage=outside: pass_outside_when 축 중 하나라도 flags에서 활성이면 골드 SQL,
      아니면 오답 SQL("SELECT 1 AS mock_wrong")을 반환(EX 실패 유도) → A/B 개선 시연.
    - 골드 SMQ가 있으면 그대로 예측 SMQ로 반환(SMQ 정확도 100% 시연).

    비용 지표(retries/llm_calls/tokens/latency)는 flags로부터 결정적으로 합성한다.
    """

    WRONG_SQL = "SELECT 1 AS mock_wrong"

    def __init__(self, pass_outside_when: Optional[set[str]] = None, emit_smq: bool = True) -> None:
        self.pass_outside_when = pass_outside_when or set()
        self.emit_smq = emit_smq

    def predict(self, item: GoldItem, flags: dict) -> PredictionResult:
        """mock 예측을 수행한다."""
        if item.coverage == "inside":
            sql = item.gold_sql
            correct = True
        else:
            enabled = any(flags.get(k) for k in self.pass_outside_when)
            sql = item.gold_sql if enabled else self.WRONG_SQL
            correct = enabled
        candidate_count = int(flags.get("candidate_count") or 1)
        multi = bool(flags.get("multi_candidate")) or candidate_count > 1
        llm_calls = candidate_count if multi else 1
        if bool(flags.get("selection")) and flags.get("selection") in ("llm", "hybrid"):
            llm_calls += 1  # 선택 에이전트 1회
        retries = 0 if correct else 1
        smq = item.gold_smq if (self.emit_smq and item.gold_smq) else None
        return PredictionResult(
            sql=sql,
            smq=smq,
            retries=retries,
            llm_calls=llm_calls,
            tokens=max(1, len(sql) // 4),
            latency_ms=5.0 + retries * 3.0 + llm_calls * 1.5,
        )


class UnavailablePredictor:
    """실제 파이프라인 접속 불가 시의 안전 예측기 - 항상 스킵을 반환한다."""

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def predict(self, item: GoldItem, flags: dict) -> PredictionResult:
        """항상 skipped 예측을 반환한다."""
        return PredictionResult(sql=None, skipped=True, error=self.reason)


# ──────────────────────────────────────────────
# 실제 파이프라인 어댑터 (best-effort, 접속 실패 시 graceful 스킵)
# ──────────────────────────────────────────────

def _add_repo_root_to_path() -> None:
    """리포지토리 루트를 sys.path에 추가한다(src.* 지연 임포트용)."""
    root = str(Path(__file__).resolve().parent.parent)
    if root not in sys.path:
        sys.path.insert(0, root)


_EVAL_LOOP: Optional[asyncio.AbstractEventLoop] = None

# MCP SSE 세션 종료 소음 시그니처 - anyio cancel scope는 태스크 경계를 넘는 정리를 허용하지
# 않아, 세션 잔여 태스크가 GC/shutdown에서 정리될 때 아래 문구의 비치명 예외를 루프 핸들러로
# 흘린다(채점 결과에는 무영향 - 항목 작업은 이미 완료/스킵 기록됨). 그 외 예외는 기본 처리.
_MCP_TEARDOWN_SIGNATURES = (
    "cancel scope",
    "unhandled errors in a TaskGroup",
    "GeneratorExit",
)


def _mute_mcp_teardown_noise(loop: Any, ctx: dict) -> None:
    """MCP SSE 세션 종료 소음만 억제하는 루프 예외 핸들러(그 외는 기본 핸들러 위임)."""
    texts = [str(ctx.get("message", ""))]
    stack = [ctx.get("exception")]
    while stack:
        exc = stack.pop()
        if exc is None:
            continue
        texts.append(f"{type(exc).__name__}: {exc}")
        stack.extend(list(getattr(exc, "exceptions", []) or []))
    blob = " | ".join(texts)
    if any(sig in blob for sig in _MCP_TEARDOWN_SIGNATURES):
        return
    loop.default_exception_handler(ctx)


def _run_async(coro: Any) -> Any:
    """배치 전체가 하나의 이벤트 루프를 공유해 코루틴을 실행한다.

    항목별 `asyncio.run()` 금지 - MCP SSE 세션(anyio cancel scope)은 이벤트 루프 종료 시
    잔존 태스크가 다른 태스크에서 강제 취소되며 `RuntimeError: Attempted to exit cancel
    scope in a different task ...`를 뿜는다(폐쇄망 실측 2026-07-16). 항목마다 루프를
    만들고 닫으면(예측 1 + 골드/생성 SQL 실행 2 = 항목당 루프 3개) 이 종료 경로가 계속
    반복된다. 서비스 런타임(단일 uvicorn 루프)과 동형인 **단일 공유 루프**로 실행한다.
    """
    global _EVAL_LOOP
    if _EVAL_LOOP is None or _EVAL_LOOP.is_closed():
        _EVAL_LOOP = asyncio.new_event_loop()
        asyncio.set_event_loop(_EVAL_LOOP)
        _EVAL_LOOP.set_exception_handler(_mute_mcp_teardown_noise)
    return _EVAL_LOOP.run_until_complete(coro)


def _close_eval_loop() -> None:
    """공유 루프를 조용히 정리한다(배치 종료 시 1회 - 잔존 SSE 태스크 소음 억제)."""
    global _EVAL_LOOP
    loop = _EVAL_LOOP
    _EVAL_LOOP = None
    if loop is None or loop.is_closed():
        return
    try:
        loop.set_exception_handler(lambda _l, _ctx: None)
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.run_until_complete(loop.shutdown_asyncgens())
    except Exception:
        pass
    finally:
        try:
            loop.close()
        except Exception:
            pass


class RealExecutor:
    """DBHub/DB 클라이언트를 통한 실제 SQL 실행기(best-effort).

    폐쇄망/CI에서 접속 실패 시 execute()가 None을 반환하고, 러너는 EX를 스킵한다.
    """

    def __init__(self) -> None:
        self._available: Optional[bool] = None
        self.last_error: Optional[str] = None

    def execute(self, sql: str, db_id: str) -> Optional[list]:
        """SQL을 실제 DB에서 실행한다. 실패/미가용이면 None(사유는 last_error에 보존)."""
        self.last_error = None
        try:
            _add_repo_root_to_path()
            import asyncio

            from src.config import load_config
            from src.dbhub.client import DBHubClient  # type: ignore

            cfg = load_config()
            # 대상 DB를 source_name으로 지정한 설정 사본으로 클라이언트를 만든다.
            dbhub_cfg = cfg.dbhub.model_copy(update={"source_name": db_id})

            async def _run() -> Optional[list]:
                client = DBHubClient(dbhub_cfg, cfg.query)
                try:
                    await client.connect()
                    result = await client.execute_sql(sql)
                    return list(getattr(result, "rows", []) or [])
                finally:
                    close = getattr(client, "close", None)
                    if close:
                        maybe = close()
                        if asyncio.iscoroutine(maybe):
                            await maybe

            return _run_async(_run())
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None


class PipelinePredictor:
    """실제 파이프라인(3경로)을 구동하는 예측기(best-effort).

    path: graph | orchestration | multidb. 접속/의존 실패 시 모든 예외를 삼켜 skipped 반환한다
    (폐쇄망 CI graceful degrade). 정상 시 생성 SQL을 추출해 PredictionResult로 반환한다.
    """

    def __init__(self, path: str) -> None:
        if path not in PATHS:
            raise ValueError(f"path는 {PATHS} 중 하나여야 합니다: {path!r}")
        self.path = path

    def predict(self, item: GoldItem, flags: dict) -> PredictionResult:
        """선택 경로로 파이프라인을 실행하고 생성 SQL을 반환한다(실패 시 skipped)."""
        try:
            _add_repo_root_to_path()
            import asyncio
            import time

            from src.config import load_config
            from src.llm import create_llm  # type: ignore

            cfg = load_config()
            llm = create_llm(cfg)
            state = {
                "user_query": item.query,
                "active_db_id": item.db_id,
                "target_databases": [{"db_id": item.db_id}],
                "retry_count": 0,
            }

            async def _run() -> dict:
                # graph 외 경로는 상류 전처리(input_parser)를 재현해 parsed_requirements를
                # 채운다 - 운영에서는 오케스트레이터가 채워주는 키를 query_generator가
                # `state["parsed_requirements"]`로 직접 인덱싱하므로, 생략 시 전 항목이
                # KeyError로 스킵된다(폐쇄망 실측 2026-07-16). graph 경로는 그래프 내부에서
                # input_parser가 실행되므로 불필요.
                if self.path != "graph":
                    from src.nodes.input_parser import input_parser  # type: ignore

                    state.update(await input_parser(state, llm=llm, app_config=cfg))
                    # 상류 재현 2: 운영 orchestration은 알람 질의에 routing_intent를 결정적으로
                    # 세팅한다(D-076 후속3 `_make_isolated_input`/`has_alarm_signal`). 이를 건너뛰면
                    # allowed_tables가 알람 테이블을 제외해 LLM이 "알람 테이블이 없다"는 자연어로
                    # 응답 - yd-006 검증 소진의 실제 원인(폐쇄망 실측 2026-07-20).
                    from src.orchestration.intent_planner import has_alarm_signal  # type: ignore

                    if has_alarm_signal(item.query):
                        state["routing_intent"] = "alarm_query"
                if self.path == "orchestration":
                    from src.orchestration.subagents import _run_single_db_pipeline  # type: ignore

                    return await _run_single_db_pipeline(state, llm, cfg)
                if self.path == "multidb":
                    from src.nodes.multi_db_executor import multi_db_executor  # type: ignore

                    return await multi_db_executor(state, llm=llm, app_config=cfg)
                # graph
                from src.graph import build_graph  # type: ignore

                graph = build_graph(cfg)
                return await graph.ainvoke(state)

            t0 = time.perf_counter()
            out = _run_async(_run())
            latency = (time.perf_counter() - t0) * 1000.0
            # 파이프라인이 검증 실패(재시도 소진)로 끝났으면 그 SQL을 실행하지 않고 사유를
            # 그대로 노출한다 - 실패 SQL을 실행하면 DB 구문 오류가 사유를 덮어써 validator
            # 가드(D-087 등) 발동 여부를 원격에서 구분할 수 없다(폐쇄망 실측 2026-07-20).
            vr = out.get("validation_result") if isinstance(out, dict) else None
            if isinstance(vr, dict) and vr.get("passed") is False:
                reason = out.get("error_message") or vr.get("reason") or "검증 실패"
                return PredictionResult(
                    sql=None, skipped=True,
                    error=f"파이프라인 검증 실패(재시도 소진): {reason}",
                    retries=int(out.get("retry_count", 0) or 0),
                )
            sql = self._extract_sql(out)
            if not sql:
                return PredictionResult(sql=None, skipped=True, error="생성 SQL 추출 실패")
            return PredictionResult(
                sql=sql,
                retries=int(out.get("retry_count", 0) or 0),
                llm_calls=len(out.get("query_attempts", []) or []) or 1,
                tokens=max(1, len(sql) // 4),
                latency_ms=latency,
            )
        except Exception as exc:  # 폐쇄망/미접속 graceful 스킵
            return PredictionResult(sql=None, skipped=True, error=f"{type(exc).__name__}: {exc}")

    @staticmethod
    def _extract_sql(out: dict) -> Optional[str]:
        """파이프라인 결과 state에서 생성 SQL을 추출한다(단일/멀티 경로 대응)."""
        if not isinstance(out, dict):
            return None
        if out.get("generated_sql"):
            return str(out["generated_sql"])
        attempts = out.get("query_attempts") or []
        for att in reversed(attempts):
            sql = att.get("sql") if isinstance(att, dict) else getattr(att, "sql", None)
            if sql:
                return str(sql)
        db_results = out.get("db_results") or {}
        # 멀티 경로: 첫 성공 DB의 SQL은 별도 노출이 없어 attempts 우선. 없으면 None.
        if db_results:
            return None
        return None


# ──────────────────────────────────────────────
# 플래그 적용 (os.environ + config 캐시 무효화)
# ──────────────────────────────────────────────

def set_pipeline_flags(flags: dict) -> None:
    """A/B 플래그를 os.environ에 세팅하고 config 캐시를 무효화한다.

    config.py가 해당 플래그를 아직 노출하지 않아도 환경변수 세팅은 무해하다(전방호환).
    config 임포트 자체가 실패해도(폐쇄망 의존 부재) 조용히 통과한다.
    """
    for key, env in FLAG_ENV.items():
        if key not in flags:
            continue
        val = flags[key]
        if isinstance(val, bool):
            os.environ[env] = "true" if val else "false"
        elif val is None:
            os.environ.pop(env, None)
        else:
            os.environ[env] = str(val)
    try:
        _add_repo_root_to_path()
        from src.config import load_config

        load_config.cache_clear()
    except Exception:
        pass


# ──────────────────────────────────────────────
# 배치 실행 / 집계
# ──────────────────────────────────────────────

def run_batch(
    items: list[GoldItem],
    predictor: Predictor,
    executor: Executor,
    *,
    label: str = "run",
    flags: Optional[dict] = None,
    float_tol: float = 1e-6,
    apply_flags: bool = False,
) -> BatchReport:
    """골드셋을 배치 실행·채점한다.

    Args:
        items: 골드 항목.
        predictor: 질의→SQL 예측기.
        executor: SQL→결과행 실행기(EX 채점).
        label: 리포트 라벨(A/B 축 구분).
        flags: 파이프라인 플래그(예측기에 전달·집계 라벨).
        float_tol: EX 부동소수 tolerance.
        apply_flags: True면 실행 전 os.environ 플래그 세팅(실제 경로용).

    Returns:
        BatchReport.
    """
    flags = flags or {}
    if apply_flags:
        set_pipeline_flags(flags)
    report = BatchReport(label=label)
    for item in items:
        pred = predictor.predict(item, flags)
        ex_scored = False
        ex_pass: Optional[bool] = None
        skip_reason: Optional[str] = None
        g_n = p_n = g_c = p_c = None
        ex_subset: Optional[bool] = None
        unmatched_cols: Optional[list] = None
        if pred.skipped or not pred.sql:
            skip_reason = pred.error or "예측 미가용"
        else:
            gold_rows = executor.execute(item.gold_sql, item.db_id)
            gold_err = getattr(executor, "last_error", None)
            pred_rows = executor.execute(pred.sql, item.db_id) if gold_rows is not None else None
            pred_err = getattr(executor, "last_error", None)
            if gold_rows is None:
                # 골드 실패는 파이프라인 문제가 아니라 골드셋 결함 후보 - 구분해 표기
                skip_reason = f"골드 SQL 실행 실패(골드셋 결함 가능): {gold_err or '미접속'}"
            elif pred_rows is None:
                skip_reason = f"생성 SQL 실행 실패: {pred_err or '미접속'}"
            else:
                ex_scored = True
                ex_pass = execution_match(gold_rows, pred_rows, float_tol=float_tol)
                if ex_pass:
                    ex_subset = True
                else:
                    unmatched_cols = column_subset_unmatched(
                        gold_rows, pred_rows, float_tol=float_tol
                    )
                    ex_subset = unmatched_cols is not None and not unmatched_cols
                # scoring: subset 항목은 값 부분집합 일치를 통과로 인정한다(알람 뷰처럼
                # 파이프라인이 표준 컬럼+α를 내는 것이 정상인 유형 - 컬럼 수가 실행마다
                # 흔들려(9->10) strict로는 영원히 쫓아갈 수 없음, 폐쇄망 실측 2026-07-20)
                if not ex_pass and item.scoring == "subset" and ex_subset:
                    ex_pass = True
                # FAIL 진단 재료(폐쇄망) - 행수·첫 행 컬럼수
                g_n, p_n = len(gold_rows), len(pred_rows)
                g_c = len(gold_rows[0]) if gold_rows else 0
                p_c = len(pred_rows[0]) if pred_rows else 0
        smq_scored = bool(item.gold_smq and pred.smq)
        smq_pass = smq_match(item.gold_smq, pred.smq) if smq_scored else None
        report.items.append(
            ItemResult(
                id=item.id,
                db_id=item.db_id,
                category=item.category,
                coverage=item.coverage,
                ex_scored=ex_scored,
                ex_pass=ex_pass,
                ex_skip_reason=skip_reason,
                smq_scored=smq_scored,
                smq_pass=smq_pass,
                retries=pred.retries,
                llm_calls=pred.llm_calls,
                tokens=pred.tokens,
                latency_ms=pred.latency_ms,
                pred_sql=pred.sql,
                error=pred.error,
                gold_row_count=g_n,
                pred_row_count=p_n,
                gold_col_count=g_c,
                pred_col_count=p_c,
                ex_subset_pass=ex_subset,
                subset_unmatched_cols=unmatched_cols,
                known_fail=item.known_fail,
            )
        )
    return report


def aggregate(report: BatchReport) -> dict:
    """BatchReport를 집계 지표 dict로 요약한다."""
    items = report.items
    n = len(items)
    scored = [r for r in items if r.ex_scored]
    passed = [r for r in scored if r.ex_pass]
    smq_scored = [r for r in items if r.smq_scored]
    smq_pass = [r for r in smq_scored if r.smq_pass]

    by_category: dict[str, dict[str, int]] = {}
    for r in items:
        cell = by_category.setdefault(r.category, {"scored": 0, "pass": 0})
        if r.ex_scored:
            cell["scored"] += 1
            if r.ex_pass:
                cell["pass"] += 1

    subset_passed = [r for r in scored if r.ex_pass or r.ex_subset_pass]
    return {
        "label": report.label,
        "total": n,
        "ex_scored": len(scored),
        "ex_pass": len(passed),
        "ex_skipped": n - len(scored),
        "ex_rate": (len(passed) / len(scored)) if scored else None,
        "ex_subset_pass": len(subset_passed),
        "ex_subset_rate": (len(subset_passed) / len(scored)) if scored else None,
        "avg_retries": round(mean([r.retries for r in items]), 3) if items else 0.0,
        "total_llm_calls": sum(r.llm_calls for r in items),
        "avg_llm_calls": round(mean([r.llm_calls for r in items]), 3) if items else 0.0,
        "total_tokens": sum(r.tokens for r in items),
        "avg_latency_ms": round(mean([r.latency_ms for r in items]), 2) if items else 0.0,
        "smq_scored": len(smq_scored),
        "smq_pass": len(smq_pass),
        "smq_rate": (len(smq_pass) / len(smq_scored)) if smq_scored else None,
        "by_category": by_category,
        # 항목별 상세 - 폐쇄망에서 `--json > out.json`으로 저장해 현지에서 FAIL을 진단한다
        "items": [
            {
                "id": r.id,
                "status": "PASS" if r.ex_pass else ("SKIP" if not r.ex_scored else "FAIL"),
                "skip_reason": r.ex_skip_reason,
                "rows_gold": r.gold_row_count,
                "rows_pred": r.pred_row_count,
                "cols_gold": r.gold_col_count,
                "cols_pred": r.pred_col_count,
                "ex_subset_pass": r.ex_subset_pass,
                "pred_sql": r.pred_sql,
            }
            for r in items
        ],
    }


def build_ab_report(baseline: BatchReport, variant: BatchReport, axis: str) -> dict:
    """baseline vs variant 집계 비교(A/B 축)를 dict로 생성한다."""
    a = aggregate(baseline)
    b = aggregate(variant)
    delta = None
    if a["ex_rate"] is not None and b["ex_rate"] is not None:
        delta = round(b["ex_rate"] - a["ex_rate"], 4)
    return {"axis": axis, "baseline": a, "variant": b, "ex_rate_delta": delta}


# ──────────────────────────────────────────────
# 출력
# ──────────────────────────────────────────────

def _fmt_rate(v: Optional[float]) -> str:
    """비율을 퍼센트 문자열로 포맷한다(None=N/A)."""
    return "N/A" if v is None else f"{v * 100:.1f}%"


def print_dry_run(items: list[GoldItem]) -> None:
    """dry-run: 골드셋 검증 결과·통계만 출력한다."""
    errors = validate_goldset(items)
    cov = coverage_stats(items)
    print("\n=== Text-to-SQL 골드셋 (dry-run) ===\n")
    print(f"  총 항목: {cov['total']}건")
    print(f"  DB 분포: {cov['by_db']}")
    print(f"  커버리지: inside {cov['inside']} / outside {cov['outside']}  (커버리지율 {_fmt_rate(cov['coverage_rate'])})")
    print("  카테고리 분포(inside/outside/total):")
    for cat in CATEGORIES:
        cell = cov["by_category"].get(cat)
        if cell:
            print(f"    - {cat:<14} {cell['inside']}/{cell['outside']}/{cell['total']}")
    smq_count = sum(1 for i in items if i.gold_smq)
    print(f"  골드 SMQ 보유 항목: {smq_count}건 (SMQ 정확도 축 입력)")
    print()
    if errors:
        print(f"  [검증 실패] 위반 {len(errors)}건:")
        for e in errors:
            print(f"    - {e}")
    else:
        print("  [검증 통과] 골드셋 스키마 위반 0건.")
    print()


def print_report(report: BatchReport, verbose: bool = False) -> None:
    """단일 실행 리포트를 사람이 읽는 형식으로 출력한다."""
    agg = aggregate(report)
    print(f"\n=== EX 리포트: {agg['label']} ===\n")
    print(f"  총 {agg['total']}건 | EX 채점 {agg['ex_scored']} | 통과 {agg['ex_pass']} | 스킵 {agg['ex_skipped']}")
    print(f"  EX 정확도: {_fmt_rate(agg['ex_rate'])}")
    print(
        f"  EX-subset(컬럼 스코프 무시, 보조): {agg['ex_subset_pass']}/{agg['ex_scored']}"
        f" ({_fmt_rate(agg['ex_subset_rate'])}) - strict FAIL 중 값은 일치하는 항목 포함"
    )
    print(f"  SMQ 정확도: {_fmt_rate(agg['smq_rate'])} (채점 {agg['smq_scored']}건)")
    print(
        f"  비용(모니터링): 평균 재시도 {agg['avg_retries']} | 총 LLM 호출 {agg['total_llm_calls']} "
        f"| 평균 LLM {agg['avg_llm_calls']} | 총 토큰(추정) {agg['total_tokens']} | 평균 지연 {agg['avg_latency_ms']}ms"
    )
    print("  카테고리별 EX(pass/scored):")
    for cat in CATEGORIES:
        cell = agg["by_category"].get(cat)
        if cell and cell["scored"]:
            print(f"    - {cat:<14} {cell['pass']}/{cell['scored']}")
    if verbose:
        print("\n  항목별:")
        for r in report.items:
            status = "PASS" if r.ex_pass else ("SKIP" if not r.ex_scored else "FAIL")
            extra = f" ({r.ex_skip_reason})" if r.ex_skip_reason else ""
            if r.known_fail and status == "FAIL":
                status = "FAIL:의도"
            print(f"    [{status}] {r.id:<8} {r.coverage:<7} {r.category:<14}{extra}")
            if r.known_fail and r.ex_pass:
                print(
                    f"        [주의] 의도된 FAIL 항목이 PASS - 감시하던 결함"
                    f"({r.known_fail})이 수정된 것. 골드 known_fail 해제를 검토하라."
                )
            if status == "FAIL:의도":
                print(f"        의도된 FAIL(골드를 pred에 맞추지 말 것): {r.known_fail}")
            if status.startswith("FAIL"):
                # 진단: 행수/컬럼수 불일치 유형을 즉시 판별(폐쇄망 - 로그 반출 불가 전제)
                hint = " <- 골드 0행(골드셋 결함 후보, --check-gold로 정비)" if r.gold_row_count == 0 else ""
                if r.ex_subset_pass:
                    hint += " [값일치·컬럼스코프만 과잉]"
                print(
                    f"        rows(gold/pred)={r.gold_row_count}/{r.pred_row_count} "
                    f"cols(gold/pred)={r.gold_col_count}/{r.pred_col_count}{hint}"
                )
                if r.subset_unmatched_cols:
                    print(
                        "        subset 미일치 골드컬럼: "
                        + ", ".join(str(c) for c in r.subset_unmatched_cols)
                    )
                if r.pred_sql:
                    one_line = " ".join(r.pred_sql.split())
                    print(f"        pred_sql: {one_line[:400]}")
    print()


def dump_sqls(path: str, reports: list[BatchReport]) -> None:
    """항목별 생성 SQL 전문과 진단 수치를 텍스트 파일로 저장한다(폐쇄망 현지 열람용).

    verbose 콘솔 출력은 400자 절단이라 WHERE/JOIN 등 후반부가 안 보인다(실측 요청
    2026-07-20). 파일은 UTF-8로 저장하며 반출하지 않고 현지에서 열람하는 용도.
    """
    with open(path, "w", encoding="utf-8") as f:
        for rep in reports:
            f.write(f"===== {rep.label} =====\n")
            for r in rep.items:
                status = "PASS" if r.ex_pass else ("SKIP" if not r.ex_scored else "FAIL")
                f.write(
                    f"\n--- {r.id} [{status}] rows(gold/pred)={r.gold_row_count}/{r.pred_row_count} "
                    f"cols(gold/pred)={r.gold_col_count}/{r.pred_col_count}"
                    f"{' [값일치-스코프만]' if (r.ex_subset_pass and not r.ex_pass) else ''}\n"
                )
                if r.ex_skip_reason:
                    f.write(f"[skip] {r.ex_skip_reason}\n")
                if r.known_fail:
                    f.write(f"[known_fail] {r.known_fail}\n")
                if r.subset_unmatched_cols:
                    f.write(
                        "[subset 미일치 골드컬럼] "
                        + ", ".join(str(c) for c in r.subset_unmatched_cols) + "\n"
                    )
                f.write("[pred_sql]\n")
                f.write((r.pred_sql or "(없음)").strip() + "\n")
    print(f"  [저장] 생성 SQL 전문: {path}")


def print_ab_report(ab: dict) -> None:
    """A/B 비교 표를 출력한다."""
    a, b = ab["baseline"], ab["variant"]
    print(f"\n=== A/B 비교 - 축: {ab['axis']} ===\n")
    header = f"  {'지표':<22}{'baseline':>14}{'variant':>14}"
    print(header)
    print("  " + "-" * (22 + 28))
    rows = [
        ("EX 정확도", _fmt_rate(a["ex_rate"]), _fmt_rate(b["ex_rate"])),
        ("EX 통과/채점", f"{a['ex_pass']}/{a['ex_scored']}", f"{b['ex_pass']}/{b['ex_scored']}"),
        ("SMQ 정확도", _fmt_rate(a["smq_rate"]), _fmt_rate(b["smq_rate"])),
        ("총 LLM 호출", str(a["total_llm_calls"]), str(b["total_llm_calls"])),
        ("평균 지연(ms)", str(a["avg_latency_ms"]), str(b["avg_latency_ms"])),
        ("총 토큰(추정)", str(a["total_tokens"]), str(b["total_tokens"])),
    ]
    for name, av, bv in rows:
        print(f"  {name:<22}{av:>14}{bv:>14}")
    if ab["ex_rate_delta"] is not None:
        print(f"\n  EX 정확도 변화(variant-baseline): {ab['ex_rate_delta'] * 100:+.1f}%p")
    print()


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────

def _build_flags(args: argparse.Namespace) -> dict:
    """CLI 인자로부터 파이프라인 플래그 dict를 조립한다."""
    flags: dict = {}
    if args.synonym_fuzzy:
        flags["synonym_fuzzy"] = True
    if args.value_retrieval:
        flags["value_retrieval"] = True
    if args.semantic_compose:
        flags["semantic_compose"] = True
    if args.candidate_count and args.candidate_count > 1:
        flags["candidate_count"] = args.candidate_count
        flags["multi_candidate"] = True
    if args.selection:
        flags["selection"] = args.selection
    return flags


def _make_predictor(args: argparse.Namespace, pass_outside_when: Optional[set[str]] = None) -> Predictor:
    """모드에 따른 예측기를 생성한다."""
    if args.mock:
        return MockPredictor(pass_outside_when=pass_outside_when)
    if args.dry_run:  # dry-run은 예측기 미사용(방어)
        return UnavailablePredictor("dry-run")
    return PipelinePredictor(args.path)


def _make_executor(args: argparse.Namespace) -> Executor:
    """모드에 따른 실행기를 생성한다."""
    if args.mock:
        return MockExecutor()
    return RealExecutor()


def main(argv: Optional[list[str]] = None) -> int:
    """CLI 진입점."""
    # cp949 콘솔에서 인코딩 불가 문자(em-dash, non-breaking hyphen 등 - LLM 출력이 에러
    # 메시지에 섞여 들어옴)로 배치가 죽지 않도록 출력 스트림을 치환 모드로 전환한다.
    # 개별 문자열 정리는 끝이 없음(실측: — -> 수정 -> ‑ 재발, 2026-07-20).
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(errors="replace")
        except Exception:
            pass
    parser = argparse.ArgumentParser(description="Text-to-SQL EX 평가 하네스 (Plan 61 / E1)")
    parser.add_argument("--gold-dir", default=str(_default_gold_dir()), help="골드셋 디렉터리")
    parser.add_argument("--db", default="all", help="대상 DB 파일(gp|yd|b0|all)")
    parser.add_argument("--path", choices=PATHS, default="orchestration", help="실행 경로")
    parser.add_argument("--dry-run", action="store_true", help="파이프라인 미실행, 골드셋 검증·통계만")
    parser.add_argument("--check-gold", action="store_true",
                        help="골드 SQL만 실 DB에서 전건 실행해 골드셋 자체를 검증(0행/실행실패=결함 후보)")
    parser.add_argument("--mock", action="store_true", help="주입형 mock 예측기/실행기로 구동")
    parser.add_argument("--synonym-fuzzy", action="store_true", help="E5-1 유연 매칭 토글")
    parser.add_argument("--value-retrieval", action="store_true", help="E5-2 값 검색 토글")
    parser.add_argument("--semantic-compose", action="store_true", help="E6 결정적 조합 토글")
    parser.add_argument("--candidate-count", type=int, default=1, help="E2 다중 후보 수 N")
    parser.add_argument("--selection", choices=SELECTIONS, default=None, help="E4 선택 전략")
    parser.add_argument("--ab", choices=AB_AXES, default=None, help="A/B 비교 축(baseline off vs variant on)")
    parser.add_argument("--float-tol", type=float, default=1e-6, help="EX 부동소수 tolerance")
    parser.add_argument("--json", action="store_true", help="JSON 출력")
    parser.add_argument("--verbose", "-v", action="store_true", help="항목별 상세 출력")
    parser.add_argument("--log-level", default=None,
                        help="파이프라인 내부 로그 레벨(INFO 등) - '시맨틱 결정적 컴파일' 발동 여부 등은 INFO에만 찍힘")
    parser.add_argument("--sql-dump", default=None, metavar="PATH",
                        help="항목별 생성 SQL 전문을 텍스트 파일로 저장(폐쇄망 현지 진단용 - verbose의 400자 절단 없이 전문)")
    args = parser.parse_args(argv)

    if args.log_level:
        import logging as _logging
        _logging.basicConfig(
            level=getattr(_logging, args.log_level.upper(), _logging.INFO),
            format="%(levelname)s %(name)s: %(message)s",
        )

    items = load_goldset(args.gold_dir, db_filter=args.db)
    if not items:
        print(f"[오류] 골드 항목이 없습니다: {args.gold_dir} (db={args.db})", file=sys.stderr)
        return 2

    # dry-run: 검증·통계만
    if args.dry_run:
        errors = validate_goldset(items)
        if args.json:
            print(json.dumps({"stats": coverage_stats(items), "errors": errors}, ensure_ascii=False, indent=2))
        else:
            print_dry_run(items)
        return 1 if errors else 0

    # 골드셋 검증(실행 모드에서도 선행)
    errors = validate_goldset(items)
    if errors:
        print(f"[오류] 골드셋 검증 실패 {len(errors)}건 - 실행 중단:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 2

    executor = _make_executor(args)

    # 골드셋 실측 검증: 골드 SQL만 실행해 0행·실행실패를 결함 후보로 리포트.
    # 폐쇄망 실측(2026-07-20)에서 골드가 실 DB 기준 0행/LIMIT 절단으로 판명 - EX 0%의
    # 주원인이 골드셋 결함일 때 파이프라인 진단보다 먼저 이 모드로 골드를 정비한다.
    if args.check_gold:
        print(f"\n=== 골드셋 실측 검증 ({len(items)}건) ===\n")
        bad = 0
        for item in items:
            rows = executor.execute(item.gold_sql, item.db_id)
            if rows is None:
                err = str(getattr(executor, "last_error", None) or "미접속")
                print(f"  [ERR ] {item.id:<8} 실행 실패: {err[:250]}")
                bad += 1
            elif not rows:
                print(f"  [ZERO] {item.id:<8} 0행 - 실 데이터와 불일치(결함 후보)")
                bad += 1
            else:
                cols = len(rows[0]) if not isinstance(rows[0], (int, float, str)) else 1
                print(f"  [OK  ] {item.id:<8} rows={len(rows)} cols={cols}")
        print(f"\n  결함 후보 {bad}건 / {len(items)}건\n")
        return 1 if bad else 0

    # A/B 모드
    if args.ab:
        base_flags: dict = {}
        var_flags = _build_flags(args)
        # 축 자체 variant를 강제(예: --ab synonym_fuzzy면 variant에 synonym_fuzzy=True)
        if args.ab == "candidate_count":
            n = args.candidate_count if args.candidate_count > 1 else 3
            var_flags = {**var_flags, "candidate_count": n, "multi_candidate": True}
            base_flags = {"candidate_count": 1}
        elif args.ab == "selection":
            var_flags = {**var_flags, "selection": args.selection or "hybrid"}
            base_flags = {"selection": "consistency"}
        else:
            var_flags = {**var_flags, args.ab: True}
            # baseline은 축을 **명시적으로 off** - 비우면 프로세스/.env의 기존 플래그를
            # 상속해 baseline=variant가 될 수 있다(폐쇄망 .env에 TEXT2SQL_SEMANTIC_COMPOSE
            # =true가 있으면 A/B가 무의미해지는 실측 결함 2026-07-20).
            base_flags = {args.ab: False}
        predictor = _make_predictor(args, pass_outside_when={args.ab})
        base = run_batch(items, predictor, executor, label=f"baseline({args.ab}=off)", flags=base_flags,
                         float_tol=args.float_tol, apply_flags=not args.mock)
        var = run_batch(items, predictor, executor, label=f"variant({args.ab}=on)", flags=var_flags,
                        float_tol=args.float_tol, apply_flags=not args.mock)
        ab = build_ab_report(base, var, args.ab)
        if args.json:
            print(json.dumps(ab, ensure_ascii=False, indent=2))
        else:
            print_ab_report(ab)
            if args.verbose:
                # A/B에서도 항목별 진단(rows/cols/pred_sql)을 노출 - variant(결정적 컴파일)의
                # FAIL 원인 특정에 필수(폐쇄망 실측 2026-07-20: 요약만으로는 원인 불가지)
                print_report(base, verbose=True)
                print_report(var, verbose=True)
        if args.sql_dump:
            dump_sqls(args.sql_dump, [base, var])
        return 0

    # 단일 실행
    flags = _build_flags(args)
    predictor = _make_predictor(args, pass_outside_when=set(flags.keys()))
    label = f"{'mock' if args.mock else args.path}"
    if flags:
        label += " [" + ",".join(sorted(flags.keys())) + "]"
    report = run_batch(items, predictor, executor, label=label, flags=flags,
                       float_tol=args.float_tol, apply_flags=not args.mock)
    if args.json:
        print(json.dumps(aggregate(report), ensure_ascii=False, indent=2))
    else:
        print_report(report, verbose=args.verbose)
        skipped = sum(1 for r in report.items if not r.ex_scored)
        if skipped and not args.mock:
            print(f"  [알림] {skipped}건 EX 스킵 - 실제 DB/LLM 미접속(폐쇄망/CI)일 수 있습니다.\n")
    if args.sql_dump:
        dump_sqls(args.sql_dump, [report])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        _close_eval_loop()
