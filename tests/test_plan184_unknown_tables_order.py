"""plans/184 M5 — 「존재하지 않는 테이블 참조」 문구의 테이블 순서가 결정적이다.

종전에는 미존재 테이블을 set에 모아 그대로 이어 붙여, 둘 이상이면 해시 시드마다 순서가 달랐다.
백틱 엔진 경로(`check_catalog_references`)처럼 정렬해 고정한다.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from src.sql_validation import validate_sql

_ROOT = Path(__file__).resolve().parents[1]
_UNKNOWN = ("zeta_t", "alpha_t", "mid_t", "beta_t", "omega_t", "gamma_t")
_SQL = "SELECT 1 FROM known_t k " + " ".join(
    f"JOIN {t} ON {t}.id = k.id" for t in _UNKNOWN
)
_SCHEMA = {"tables": {"known_t": {"columns": [{"name": "id"}]}}}


def _unknown_error() -> str:
    errors = validate_sql(_SQL, _SCHEMA).errors
    hits = [e for e in errors if e.startswith("존재하지 않는 테이블 참조:")]
    assert len(hits) == 1, errors
    return hits[0]


def test_unknown_tables_listed_in_sorted_order() -> None:
    """미존재 테이블은 이름순으로 나열된다(입력 순서·해시 순서와 무관)."""
    assert _unknown_error() == f"존재하지 않는 테이블 참조: {', '.join(sorted(_UNKNOWN))}"


def test_unknown_tables_message_stable_across_hash_seeds() -> None:
    """해시 시드를 달리한 프로세스에서도 문구가 바이트 동일하다."""
    code = (
        "from tests.test_plan184_unknown_tables_order import _unknown_error;"
        "print(_unknown_error())"
    )
    outs = set()
    for seed in ("0", "1", "2"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        proc = subprocess.run(
            [sys.executable, "-c", code], cwd=_ROOT, env=env,
            capture_output=True, text=True, check=True,
        )
        outs.add(proc.stdout.strip())
    assert len(outs) == 1
