"""지표 필드 분류 도구 검증 (Plan 67 Phase S1 §4.2, D-088/D-089).

공용 코어는 특정 DB의 분류 규칙을 모르고 어댑터 레지스트리 경유로만 얻는다.
어댑터 조회는 목으로 대체해 전역 레지스트리를 오염시키지 않는다.
"""

from __future__ import annotations

import src.tools.metrics as metrics_module
from src.tools.metrics import SOURCE_ADAPTER, SOURCE_GENERIC, classify_metric_field


class FakeAdapter:
    """분류 훅을 노출하는 어댑터 목."""

    name = "fake"

    def classify_metric_field(self, field: str):
        if "cpu" in field.lower():
            return ("res.cpu", "AVG", "avg_val")
        return None


class HookLessAdapter:
    """분류 훅이 없는 어댑터 목(훅 미구현 DB)."""

    name = "hookless"


class TestAdapterPath:
    def test_adapter_hook_result_returned(self, monkeypatch):
        monkeypatch.setattr(metrics_module, "get_adapter", lambda *a, **k: FakeAdapter())
        result = classify_metric_field("CPU 평균", db_id="x", adapter_db_ids={"x"})
        assert result == {
            "field": "CPU 평균",
            "resource_type": "res.cpu",
            "agg_function": "AVG",
            "value_column": "avg_val",
            "source": SOURCE_ADAPTER,
        }

    def test_adapter_says_not_metric(self, monkeypatch):
        monkeypatch.setattr(metrics_module, "get_adapter", lambda *a, **k: FakeAdapter())
        assert classify_metric_field("서버 이름", db_id="x", adapter_db_ids={"x"}) is None


class TestGenericFallback:
    def test_no_adapter_degrades_to_surface_form(self, monkeypatch):
        """담당 어댑터가 없으면 표면어 판정으로 강등하고 그 사실을 source로 알린다."""
        monkeypatch.setattr(metrics_module, "get_adapter", lambda *a, **k: None)
        result = classify_metric_field("메모리 최고")
        assert result["source"] == SOURCE_GENERIC
        assert result["resource_type"] is None

    def test_hookless_adapter_degrades(self, monkeypatch):
        monkeypatch.setattr(metrics_module, "get_adapter", lambda *a, **k: HookLessAdapter())
        result = classify_metric_field("CPU 평균", db_id="x", adapter_db_ids={"x"})
        assert result["source"] == SOURCE_GENERIC

    def test_non_metric_returns_none(self, monkeypatch):
        monkeypatch.setattr(metrics_module, "get_adapter", lambda *a, **k: None)
        assert classify_metric_field("메모리 용량") is None

    def test_empty_field(self):
        assert classify_metric_field("") is None
