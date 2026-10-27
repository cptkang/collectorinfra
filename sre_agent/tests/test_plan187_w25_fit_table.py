"""plans/187 W25 — 단계 표 길이 맞춤(`_fit_table`)의 이분 탐색.

종전 선형 탐색(한 행씩 줄이며 다시 직렬화)과 결과가 같은지 무작위 표로 대조하고, 큰 표에서
행 수의 제곱 비용이 사라졌는지 확인한다(sre_agent 코어 100% — 내부망 실측 2026-10-23).
"""

from __future__ import annotations

import random
import time
from typing import Any

from sre_agent.application.playbook_prompts import _TRUNC, _dump, _fit_table


def _linear(section: dict[str, Any], budget: int) -> str:
    """종전 구현 그대로(대조 기준)."""
    table = section.get("table") or {}
    rows = list(table.get("rows") or [])
    extra = {k: section.get(k) for k in ("missing", "reason") if section.get(k)}
    keep = len(rows)
    while True:
        payload: dict[str, Any] = {
            "columns": list(table.get("columns") or []), "rows": rows[:keep], **extra,
        }
        if keep < len(rows):
            payload["rows_omitted"] = len(rows) - keep
        text = _dump(payload)
        if len(text) <= budget or keep == 0:
            break
        keep -= 1
    if len(text) > budget:
        text = text[: max(0, budget - len(_TRUNC))] + _TRUNC
    return text


def _row(rng: random.Random) -> Any:
    kind = rng.randrange(4)
    if kind == 0:
        return rng.randrange(10)                      # 1자 행 — 가장 짧은 경우
    if kind == 1:
        return [f"svr-{rng.randrange(1000)}", rng.random() * 100]
    if kind == 2:
        return {"name": "가" * rng.randrange(0, 30), "cpu": rng.randrange(100)}
    return "x" * rng.randrange(0, 80)


def test_matches_linear_search_on_random_tables() -> None:
    rng = random.Random(187)
    for _ in range(400):
        n = rng.choice([0, 1, 2, 9, 10, 11, 99, 100, 101, rng.randrange(0, 300)])
        section: dict[str, Any] = {"table": {"columns": ["a", "b"],
                                             "rows": [_row(rng) for _ in range(n)]}}
        if rng.random() < 0.3:
            section["missing"] = "일부 결손"
        if rng.random() < 0.2:
            section["table"] = {}
        budget = rng.choice([0, 10, 40, 200, 1000, 5000, rng.randrange(0, 20000)])
        assert _fit_table(section, budget) == _linear(section, budget)


def test_large_table_is_not_quadratic() -> None:
    rows = [[f"proc-{i}", i * 1.1, "java -Xmx4g -jar /app/service.jar --profile=prod"]
            for i in range(20000)]
    section = {"table": {"columns": ["name", "cpu", "cmd"], "rows": rows}}
    started = time.perf_counter()
    text = _fit_table(section, 6000)
    # 종전 선형 탐색은 5,000행에서 약 9초(맥북) — 20,000행이면 수 분이다
    assert time.perf_counter() - started < 2.0
    assert len(text) <= 6000 and '"rows_omitted":' in text
