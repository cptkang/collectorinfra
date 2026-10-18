"""`_zone_label` 3벌 출력 특성화 — 레지스트리 전 db_id + 미등록 db_id (plans/166 W0 · X4 · U-1).

plans/166 W0 특성화 스냅샷 — 단계 2(plans/163)만 갱신 가능, 단계 3 이후 갱신 금지(갱신이
필요하면 리팩터가 아니다). 기대값은 「맞는 값」이 아니라 「지금 값」이다(D-337 ⑤).

대상 3벌(db_id → 사용자 표시 라벨):

- `realtime_usage`: `src/nodes/realtime_usage.py` `_zone_label`
  — 존 선택지 라벨 → 레지스트리 표시명 → db_id
- `host_sweep`: `src/orchestration/host_sweep.py` `_zone_label`
  — `query_gen_common._ZONE_LABEL_BY_DB` → db_id
- `query_route`: `src/api/routes/query.py` `_zone_display_label`
  — 존 선택지 라벨 → 레지스트리 표시명 → db_id

입력은 `config/db_registry.yaml`에 등록된 전 db_id + 미등록 2개(`nonexistent_db`, `""`)다.
스냅샷에는 db_id별 3벌 출력과 함께 「세 출력이 모두 같은 db_id」(`same_db_ids`)·「하나라도 다른
db_id」(`differing_db_ids`) 목록을 남긴다 — W1이 U-1(같은 db_id 집합에서만 정본으로 합침)
판정에 쓴다.

환경 의존: 레지스트리 표시명은 `src.routing.domain_config.DB_DOMAINS`(모듈 임포트 시
`config/db_registry.yaml`에서 1회 생성)에서 온다. 적재 경로는 고정 파일이고 `ACTIVE_DB_IDS`·`.env`를
읽지 않는다(실측 — `registry.load_registry`·`domain_config.build_domains`에 env 조회 없음).
다른 테스트의 `DB_DOMAINS` 교체 누수를 막으려고 매 테스트에서 레지스트리 정본으로 다시 만든
목록을 주입한다.

스냅샷 (재)생성: `UPDATE_PLAN162_SNAPSHOT=1 python -m pytest <이 파일>`
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import src.routing.domain_config as domain_config
from src.api.routes.query import _zone_display_label
from src.nodes.realtime_usage import _zone_label as realtime_zone_label
from src.orchestration.host_sweep import _zone_label as host_sweep_zone_label
from src.routing.registry import load_registry

SNAPSHOT_PATH = (
    Path(__file__).resolve().parent.parent / "snapshots" / "plan166_w0" / "zone_label.json"
)

#: 미등록 db_id 입력(폴백 경로 — 3벌의 조회 순서 차이가 드러나는 곳)
UNREGISTERED_DB_IDS: tuple[str, ...] = ("nonexistent_db", "")

_IMPLS: dict[str, Callable[[str], str]] = {
    "realtime_usage": realtime_zone_label,
    "host_sweep": host_sweep_zone_label,
    "query_route": _zone_display_label,
}


def _registry_db_ids() -> list[str]:
    """레지스트리 정본 파일의 db_id(선언 순서)."""
    return [entry.db_id for entry in load_registry().databases]


@pytest.fixture(autouse=True)
def _pin_domains(monkeypatch: pytest.MonkeyPatch) -> None:
    """`DB_DOMAINS`를 레지스트리 정본에서 새로 만든 목록으로 고정한다(다른 테스트 누수 차단)."""
    monkeypatch.setattr(domain_config, "DB_DOMAINS", domain_config.build_domains())


def _collect() -> dict[str, Any]:
    inputs = [*_registry_db_ids(), *UNREGISTERED_DB_IDS]
    labels = {db_id: {name: fn(db_id) for name, fn in _IMPLS.items()} for db_id in inputs}
    same = [d for d in inputs if len(set(labels[d].values())) == 1]
    differing = [d for d in inputs if len(set(labels[d].values())) > 1]
    return {
        "inputs": inputs,
        "labels": labels,
        "same_db_ids": same,
        "differing_db_ids": differing,
    }


def _dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def test_zone_label_outputs_match_snapshot() -> None:
    """3벌의 db_id별 출력과 동일/상이 db_id 목록이 스냅샷과 같다."""
    current = _collect()

    if os.environ.get("UPDATE_PLAN162_SNAPSHOT") == "1":
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_text(_dump(current), encoding="utf-8")
        pytest.skip(f"스냅샷을 갱신했다: {SNAPSHOT_PATH}")

    assert SNAPSHOT_PATH.exists(), (
        f"스냅샷이 없다: {SNAPSHOT_PATH} — UPDATE_PLAN162_SNAPSHOT=1로 최초 채록하라"
    )
    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    # 레지스트리에 DB가 더해지거나 빠지면 「전 db_id」 전제가 깨진다 — 입력 집합부터 대조한다.
    assert current["inputs"] == expected["inputs"], (
        "레지스트리 db_id 목록이 스냅샷 채록 때와 다르다 — U-1 판정 입력이 바뀌었다"
    )
    changed = sorted(d for d in current["labels"] if current["labels"][d] != expected["labels"][d])
    assert not changed, f"라벨 출력이 바뀐 db_id: {changed}"
    assert current["same_db_ids"] == expected["same_db_ids"]
    assert current["differing_db_ids"] == expected["differing_db_ids"]
