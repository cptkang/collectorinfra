"""plans/153 W0 — 노이즈 관제 응답 flags-off 비트 동일 기준선.

`GET /noise/decisions` · `/noise/decisions/{alarm_id}`(사용자 경로)와 같은 핸들러의 운영자 경로
(`/admin/noise/*`) 응답 모양을 골든으로 고정한다. 이후 Wave(W1 `analysis`·`summary.stages[]` 보강 ·
W2 `investigation` 블록)가 플래그·데이터 유무로 필드를 더해도, **기본값(off)·데이터 없음**에서는
응답이 지금과 같아야 한다(plans/153 §3 ⑥).

시드는 `noise_gate/tests/test_plan153_w0_baseline.py`가 실 게이트 노드로 떠 둔 판정 레코드 골든
(`decision_records.golden.json`)이다 — 기록 시각(`ts`)만 고정값으로 채워 넣고, 응답 비교에서는 뺀다.

골든 재생성은 **응답을 의도적으로 바꾼 경우에만** 한다:
    python -m tests.test_api.test_plan153_w0_console_baseline --write
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tests.test_api.test_noise_dashboard_api import (
    _make_client,
    _make_config,
    _make_user_client,
    _seed_decisions,
)

TESTDATA = Path(__file__).resolve().parents[2] / "noise_gate" / "testdata" / "plan153"
RECORDS_GOLDEN = TESTDATA / "decision_records.golden.json"
CONSOLE_GOLDEN = TESTDATA / "console_responses.golden.json"

# 응답 비교에서 빼는 키 — 시드 시각(창 필터 때문에 실행 시각 기준으로 채운다).
_NONDETERMINISTIC_KEYS = frozenset({"ts"})

# 케이스 ID → (경로 종류, 요청 경로). ID는 골든 키라 바꾸지 않는다.
_REQUESTS: dict[str, tuple[str, str]] = {
    "user_list": ("user", "/api/v1/noise/decisions"),
    "operator_list": ("operator", "/api/v1/admin/noise/decisions"),
    "user_trace_polestar": ("user", "/api/v1/noise/decisions/P150-CPU-1"),
    "operator_trace_jennifer": (
        "operator", "/api/v1/admin/noise/decisions/jennifer:p150q0000000001"
    ),
    "user_trace_analysis_failed": ("user", "/api/v1/noise/decisions/P150-ERR-1"),
}


def _seed(tmp_path: Path) -> None:
    """골든 판정 레코드를 정렬 순서대로 1분 간격으로 기록한다(최신 = 마지막 케이스)."""
    records = json.loads(RECORDS_GOLDEN.read_text(encoding="utf-8"))
    base = datetime.now(UTC) - timedelta(minutes=len(records) + 1)
    _seed_decisions(tmp_path, [
        {"ts": (base + timedelta(minutes=i)).isoformat(), **rec}
        for i, (_, rec) in enumerate(sorted(records.items()))
    ])


def _strip(value: Any) -> Any:
    """응답에서 비결정 키를 재귀적으로 뺀다."""
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if k not in _NONDETERMINISTIC_KEYS}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def _responses(tmp_path: Path) -> dict[str, Any]:
    _seed(tmp_path)
    config = _make_config(tmp_path)
    clients = {"user": _make_user_client(config), "operator": _make_client(config)}
    out: dict[str, Any] = {}
    for case, (kind, path) in _REQUESTS.items():
        resp = clients[kind].get(path)
        assert resp.status_code == 200, (case, resp.status_code, resp.text)
        out[case] = _strip(resp.json())
    return out


@pytest.mark.parametrize("case", sorted(_REQUESTS))
def test_console_response_matches_golden(tmp_path: Path, case: str):
    golden = json.loads(CONSOLE_GOLDEN.read_text(encoding="utf-8"))
    assert _responses(tmp_path)[case] == golden[case]


def test_list_shape_and_operator_only_field(tmp_path: Path):
    body = _responses(tmp_path)
    for case in ("user_list", "operator_list"):
        assert set(body[case]) == {"items", "total", "page", "size", "facets"}
        assert body[case]["total"] == 4
    # condition_log는 운영자 경로에서만(fail-closed · G-2 (c)).
    assert all("condition_log" not in item for item in body["user_list"]["items"])
    assert all("condition_log" in item for item in body["operator_list"]["items"])
    # 기본 off — W1(`analysis`)·W2(`investigation`) 가산 키가 없다.
    for item in body["user_list"]["items"] + body["operator_list"]["items"]:
        assert "analysis" not in item and "investigation" not in item


def test_trace_shape(tmp_path: Path):
    body = _responses(tmp_path)
    for case in ("user_trace_polestar", "operator_trace_jennifer", "user_trace_analysis_failed"):
        assert set(body[case]) == {"decision", "timeline"}
        assert "investigation" not in body[case]["decision"]
        assert "analysis" not in body[case]["decision"]


def test_golden_covers_every_case():
    golden = json.loads(CONSOLE_GOLDEN.read_text(encoding="utf-8"))
    assert set(golden) == set(_REQUESTS)


def _write_golden() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        body = _responses(Path(tmp))
    CONSOLE_GOLDEN.write_text(
        json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"골든 갱신: {CONSOLE_GOLDEN}")


if __name__ == "__main__":
    if "--write" in sys.argv:
        _write_golden()
    else:
        print(__doc__)
