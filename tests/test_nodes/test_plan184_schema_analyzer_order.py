"""plans/184 J3 — schema_analyzer 보충 테이블 순서가 결정적이다.

종전에는 `allowed_tables` 보충과 알람 핵심 테이블 보충·복원이 set을 순회해, 보충 순서(→
`relevant_tables`·프롬프트 접두)가 해시 시드마다 달랐다. 새 동작: `allowed_tables` 보충은 프로필
기재 순서, 알람 핵심 테이블은 고정 순서(`cmm_alarm` → `cmm_alarm_def` → `cmm_alarm_active`).
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

# 패키지 `src.nodes`가 같은 이름의 노드 함수를 재노출하므로 모듈은 import_module로 잡는다.
sa = importlib.import_module("src.nodes.schema_analyzer")

_ROOT = Path(__file__).resolve().parents[2]
_ALLOWED = ["zz_last", "aa_first", "mm_mid", "qq_x", "bb_y", "kk_z"]
_ALARM_CORE = ["cmm_alarm", "cmm_alarm_def", "cmm_alarm_active"]


def _schema(names: list[str]) -> SimpleNamespace:
    return SimpleNamespace(tables={f"s.{n}": object() for n in names})


def _supplemented() -> list[str]:
    """LLM이 프로필 테이블을 하나도 고르지 않았을 때의 보충 결과."""
    ctx = SimpleNamespace(
        db_id="d1", cache_mgr=None, parsed={}, query_targets=[], app_config=None,
    )
    choice = SimpleNamespace(
        query_syn_tables=set(), table_selection=None,
    )
    return asyncio.run(sa._apply_allowed_tables(
        ctx, _schema(_ALLOWED + ["other"]), ["s.other_llm_pick"], choice,
        {"allowed_tables": [t.upper() for t in _ALLOWED]}, None,
    ))


def _alarm_restored() -> list[str]:
    """`alarm_allowed_tables` 필터 결과가 비어 핵심 테이블로 복원되는 경로."""
    return sa._apply_alarm_tables(
        _schema(_ALARM_CORE + ["x1"]), ["s.x1"], {"alarm_allowed_tables": ["none_t"]},
    )


def _alarm_supplemented() -> list[str]:
    """핵심 알람 테이블 누락 보충 경로."""
    return sa._apply_alarm_tables(_schema(_ALARM_CORE + ["x1"]), ["s.x1"], None)


def test_allowed_supplement_follows_profile_order() -> None:
    assert _supplemented() == [f"s.{n}" for n in _ALLOWED]


def test_alarm_core_tables_fixed_order() -> None:
    assert _alarm_restored() == [f"s.{n}" for n in _ALARM_CORE]
    assert _alarm_supplemented() == ["s.x1"] + [f"s.{n}" for n in _ALARM_CORE]


def test_order_stable_across_hash_seeds() -> None:
    """해시 시드를 달리한 프로세스 3개에서 세 경로의 결과가 모두 같다."""
    code = (
        "import json, logging; logging.disable(logging.CRITICAL);"
        "from tests.test_nodes import test_plan184_schema_analyzer_order as m;"
        "print(json.dumps([m._supplemented(), m._alarm_restored(), m._alarm_supplemented()]))"
    )
    outs = []
    for seed in ("0", "1", "2"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        proc = subprocess.run(
            [sys.executable, "-c", code], cwd=_ROOT, env=env,
            capture_output=True, text=True, check=True,
        )
        outs.append(json.loads(proc.stdout.strip().splitlines()[-1]))
    assert outs[0] == outs[1] == outs[2]
