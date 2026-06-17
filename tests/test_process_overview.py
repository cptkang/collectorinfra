"""공유 프로세스 도메인(Plan 48 §5.1) 단위 테스트.

build_process_overview(cpu/memory/both 정렬·상위 N·집계), 마스킹 회귀 고정(패스워드·토큰·
접속문자열 비노출), 빈 list/누락 필드/0건/top_n 초과 안전 처리, resolve_metric_from_text 분기를
검증한다.
"""

from __future__ import annotations

from datetime import datetime

from src.domain.process import (
    ProcessOverview,
    build_process_overview,
    resolve_metric_from_text,
)


def _proc(
    name: str,
    pid: int,
    user: str,
    *,
    p100cpu=None,
    pcpu=None,
    pmem=None,
    args: str = "",
) -> dict:
    """테스트용 원시 프로세스 dict 생성 (None 필드는 누락 처리)."""
    item: dict = {"name": name, "pid": pid, "ppid": 1, "user": user, "args": args}
    if p100cpu is not None:
        item["p100cpu"] = p100cpu
    if pcpu is not None:
        item["pcpu"] = pcpu
    if pmem is not None:
        item["pmem"] = pmem
    return item


_NOW = datetime(2026, 6, 16, 12, 0, 0)


class TestBuildOverviewCpu:
    def test_cpu_sort_and_top_n(self):
        raw = [
            _proc("a", 1, "root", p100cpu=10.0, pmem=1.0),
            _proc("b", 2, "root", p100cpu=90.0, pmem=2.0),
            _proc("c", 3, "app", p100cpu=50.0, pmem=3.0),
        ]
        ov = build_process_overview(
            raw, "cpu", 2, source_host="h1", captured_at=_NOW
        )
        assert isinstance(ov, ProcessOverview)
        assert ov.metric == "cpu"
        assert ov.source_host == "h1"
        assert ov.captured_at == _NOW
        assert ov.total_count == 3
        # p100cpu 내림차순 상위 2 → b(90), c(50)
        assert [p.name for p in ov.top_by_cpu] == ["b", "c"]
        assert ov.cpu_top_share == 140.0
        # cpu 메트릭이면 top_by_mem 비움
        assert ov.top_by_mem == []
        assert ov.mem_top_share == 0.0

    def test_cpu_pcpu_fallback_when_p100cpu_missing(self):
        # p100cpu 누락 → pcpu 폴백 정렬
        raw = [
            _proc("a", 1, "root", pcpu=10.0),
            _proc("b", 2, "root", pcpu=80.0),
        ]
        ov = build_process_overview(
            raw, "cpu", 5, source_host="h", captured_at=None
        )
        assert [p.name for p in ov.top_by_cpu] == ["b", "a"]
        # p100cpu 미존재 → 0.0, share 합은 0
        assert ov.cpu_top_share == 0.0


class TestBuildOverviewMemory:
    def test_memory_sort_and_share(self):
        raw = [
            _proc("a", 1, "root", pmem=5.0),
            _proc("b", 2, "app", pmem=40.0),
            _proc("c", 3, "app", pmem=15.0),
        ]
        ov = build_process_overview(
            raw, "memory", 2, source_host="h", captured_at=None
        )
        assert ov.metric == "memory"
        assert [p.name for p in ov.top_by_mem] == ["b", "c"]
        assert ov.mem_top_share == 55.0
        assert ov.top_by_cpu == []
        assert ov.cpu_top_share == 0.0


class TestBuildOverviewBoth:
    def test_both_fills_cpu_and_mem(self):
        raw = [
            _proc("a", 1, "root", p100cpu=10.0, pmem=30.0),
            _proc("b", 2, "app", p100cpu=70.0, pmem=5.0),
        ]
        ov = build_process_overview(
            raw, "both", 1, source_host="h", captured_at=None
        )
        assert [p.name for p in ov.top_by_cpu] == ["b"]
        assert [p.name for p in ov.top_by_mem] == ["a"]
        assert ov.cpu_top_share == 70.0
        assert ov.mem_top_share == 30.0


class TestAggregates:
    def test_distinct_users_and_top_user(self):
        raw = [
            _proc("a", 1, "root", p100cpu=1.0),
            _proc("b", 2, "root", p100cpu=2.0),
            _proc("c", 3, "root", p100cpu=3.0),
            _proc("d", 4, "app", p100cpu=4.0),
        ]
        ov = build_process_overview(
            raw, "cpu", 10, source_host="h", captured_at=None
        )
        assert ov.distinct_users == 2
        assert ov.top_user_by_count == ("root", 3)


class TestMaskingRegression:
    def test_password_token_connstring_never_plaintext(self):
        args = (
            "java -jar app --password=secret123 token=abc "
            "jdbc:postgresql://u:pw@h/db"
        )
        raw = [_proc("svc", 1, "app", p100cpu=99.0, pmem=99.0, args=args)]
        ov = build_process_overview(
            raw, "both", 5, source_host="h", captured_at=None
        )
        for proc in ov.top_by_cpu + ov.top_by_mem:
            assert "secret123" not in proc.args
            assert "abc" not in proc.args
            assert "pw@" not in proc.args
            # 마스킹 치환 토큰 존재 확인
            assert "***" in proc.args
            # 키는 보존
            assert "password=" in proc.args


class TestSafetyEdges:
    def test_empty_list(self):
        ov = build_process_overview(
            [], "both", 5, source_host="h", captured_at=None
        )
        assert ov.total_count == 0
        assert ov.top_by_cpu == []
        assert ov.top_by_mem == []
        assert ov.cpu_top_share == 0.0
        assert ov.mem_top_share == 0.0
        assert ov.distinct_users == 0
        assert ov.top_user_by_count is None

    def test_none_list(self):
        ov = build_process_overview(
            None, "cpu", 5, source_host="h", captured_at=None  # type: ignore[arg-type]
        )
        assert ov.total_count == 0
        assert ov.top_by_cpu == []

    def test_missing_fields_default_safe(self):
        # name/user/메트릭 모두 누락
        raw = [{"pid": 7}]
        ov = build_process_overview(
            raw, "both", 5, source_host="h", captured_at=None
        )
        assert ov.total_count == 1
        proc = ov.top_by_cpu[0]
        assert proc.name == ""
        assert proc.user == ""
        assert proc.p100cpu == 0.0
        assert proc.pmem == 0.0
        assert proc.args == ""
        # user 누락 → 집계 0
        assert ov.distinct_users == 0
        assert ov.top_user_by_count is None

    def test_top_n_exceeds_total(self):
        raw = [_proc("a", 1, "root", p100cpu=1.0)]
        ov = build_process_overview(
            raw, "cpu", 100, source_host="h", captured_at=None
        )
        assert len(ov.top_by_cpu) == 1

    def test_top_n_zero(self):
        raw = [_proc("a", 1, "root", p100cpu=1.0)]
        ov = build_process_overview(
            raw, "cpu", 0, source_host="h", captured_at=None
        )
        assert ov.top_by_cpu == []
        assert ov.cpu_top_share == 0.0


class TestResolveMetricFromText:
    def test_cpu(self):
        assert resolve_metric_from_text("CPU 많이 먹는 프로세스") == "cpu"

    def test_memory_korean(self):
        assert resolve_metric_from_text("메모리 많이 쓰는 프로세스") == "memory"

    def test_memory_english(self):
        assert resolve_metric_from_text("top memory consumers") == "memory"
        assert resolve_metric_from_text("high mem usage") == "memory"

    def test_both_default(self):
        assert resolve_metric_from_text("프로세스 목록 보여줘") == "both"

    def test_empty(self):
        assert resolve_metric_from_text("") == "both"

    def test_cpu_priority_over_memory(self):
        # cpu가 먼저 검사되므로 둘 다 포함 시 cpu
        assert resolve_metric_from_text("cpu 메모리 둘다") == "cpu"
