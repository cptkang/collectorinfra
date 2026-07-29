"""영향 프로세스 선별·마스킹(Plan 47-1) 단위 테스트.

classify_alarm_kind(알람 종류 판정), select_top_processes(CPU/메모리 정렬·상위 N),
mask_args(민감정보 마스킹 — 회귀 고정), 빈 list/누락 필드 처리,
AlarmConfig.get_process_api_base_url(db_id별 매핑)을 검증한다.
"""

from __future__ import annotations

from src.alarm.domain.process_rank import (
    classify_alarm_kind,
    mask_args,
    select_top_processes,
)
from src.config import AlarmConfig
from tests.test_alarm_pattern import make_event


class TestClassifyAlarmKind:
    def test_cpu_by_resource_type(self):
        ev = make_event(resource_type="server.Cpus", alarm_name="사용률 임계 초과")
        assert classify_alarm_kind(ev) == "cpu"

    def test_cpu_by_alarm_name(self):
        ev = make_event(resource_type="server.Server", alarm_name="CPU 사용률 임계 초과")
        assert classify_alarm_kind(ev) == "cpu"

    def test_memory_korean(self):
        ev = make_event(resource_type="server.Server", alarm_name="메모리 사용률 임계 초과")
        assert classify_alarm_kind(ev) == "memory"

    def test_memory_english_mem(self):
        ev = make_event(resource_type="server.Memory", alarm_name="mem usage high")
        assert classify_alarm_kind(ev) == "memory"

    # ── Plan 60 E6: kind 확장 (disk/network/process/log 신규 판정) ──
    def test_disk_kind(self):
        ev = make_event(resource_type="server.Disks", alarm_name="디스크 사용률 임계 초과")
        assert classify_alarm_kind(ev) == "disk"

    def test_disk_english_filesystem(self):
        ev = make_event(resource_type="server.Disks", alarm_name="filesystem usage high")
        assert classify_alarm_kind(ev) == "disk"

    def test_network_kind(self):
        ev = make_event(resource_type="network.NMSNode", alarm_name="트래픽 임계 초과")
        assert classify_alarm_kind(ev) == "network"

    def test_network_bandwidth(self):
        ev = make_event(resource_type="server.Server", alarm_name="bandwidth 초과")
        assert classify_alarm_kind(ev) == "network"

    def test_process_kind(self):
        ev = make_event(resource_type="server.Server", alarm_name="프로세스다운 감지")
        assert classify_alarm_kind(ev) == "process"

    def test_process_service_down(self):
        ev = make_event(resource_type="server.Server", alarm_name="service down")
        assert classify_alarm_kind(ev) == "process"

    def test_log_kind(self):
        ev = make_event(resource_type="server.LogMonitor", alarm_name="에러 로그 감지")
        assert classify_alarm_kind(ev) == "log"

    def test_unmatched_returns_none(self):
        ev = make_event(resource_type="server.Foo", alarm_name="알 수 없는 이벤트")
        assert classify_alarm_kind(ev) is None

    def test_cpu_priority_over_others(self):
        # cpu/memory 우선 판정 — disk 키워드가 있어도 cpu가 먼저 매칭(순서 보존)
        ev = make_event(resource_type="server.Cpus", alarm_name="disk io wait 로 인한 CPU 상승")
        assert classify_alarm_kind(ev) == "cpu"

    def test_case_insensitive(self):
        ev = make_event(resource_type="SERVER.CPUS", alarm_name="LOAD")
        assert classify_alarm_kind(ev) == "cpu"


class TestMaskArgs:
    """민감정보 마스킹 회귀 고정 — 평문 노출 금지 (Plan 47-1 §9)."""

    def test_password_equals_masked(self):
        out = mask_args("--password=s3cr3tP@ss -jar app.jar")
        assert "s3cr3tP@ss" not in out
        assert "password" in out.lower()
        assert "***" in out

    def test_password_space_masked(self):
        out = mask_args("mysql --user root --password secretvalue")
        assert "secretvalue" not in out
        assert "***" in out

    def test_token_masked(self):
        out = mask_args("svc --token abc123def456 --port 8080")
        assert "abc123def456" not in out
        assert "8080" in out  # 비민감 인자는 보존

    def test_api_key_variants_masked(self):
        for arg in ("--api_key=KEY123", "--api-key KEY123", "--apikey=KEY123"):
            out = mask_args(arg)
            assert "KEY123" not in out, arg

    def test_secret_and_credential_masked(self):
        out = mask_args("--secret=topsecret --credential mycred")
        assert "topsecret" not in out
        assert "mycred" not in out

    def test_connection_string_password_masked(self):
        out = mask_args("jdbc:postgresql://appuser:dbpassword@10.0.0.1:5432/prod")
        assert "dbpassword" not in out
        assert "appuser" in out          # 사용자명은 보존
        assert "10.0.0.1" in out         # 호스트는 보존

    def test_non_sensitive_preserved(self):
        out = mask_args("-Xmx8g -jar app.jar --port 9000")
        assert out == "-Xmx8g -jar app.jar --port 9000"

    def test_truncation_with_ellipsis(self):
        long = "x" * 200
        out = mask_args(long, max_len=120)
        assert out.endswith("…")
        assert len(out) <= 121

    def test_empty_args(self):
        assert mask_args("") == ""


def _proc(name, pid, p100cpu=0.0, pcpu=0.0, pmem=0.0, rss=0, args="", **extra):
    d = {
        "name": name, "pid": pid, "ppid": 1, "user": "root",
        "p100cpu": p100cpu, "pcpu": pcpu, "pmem": pmem, "rss": rss, "args": args,
    }
    d.update(extra)
    return d


class TestSelectTopProcesses:
    def test_cpu_sorted_by_p100cpu_desc(self):
        raw = [
            _proc("a", 1, p100cpu=10.0),
            _proc("b", 2, p100cpu=80.0),
            _proc("c", 3, p100cpu=40.0),
        ]
        top, total = select_top_processes(raw, "cpu", 2)
        assert total == 3
        assert [p.name for p in top] == ["b", "c"]

    def test_cpu_falls_back_to_pcpu_when_no_p100cpu(self):
        raw = [
            _proc("a", 1, pcpu=5.0),
            _proc("b", 2, pcpu=50.0),
        ]
        # p100cpu 키 제거 → pcpu 폴백
        for r in raw:
            del r["p100cpu"]
        top, _ = select_top_processes(raw, "cpu", 2)
        assert top[0].name == "b"

    def test_memory_sorted_by_pmem_desc(self):
        raw = [
            _proc("a", 1, pmem=9.0),
            _proc("b", 2, pmem=38.0),
            _proc("c", 3, pmem=7.0),
        ]
        top, total = select_top_processes(raw, "memory", 5)
        assert total == 3
        assert [p.name for p in top] == ["b", "a", "c"]

    def test_top_n_limits_results(self):
        raw = [_proc(f"p{i}", i, pmem=float(i)) for i in range(20)]
        top, total = select_top_processes(raw, "memory", 5)
        assert len(top) == 5
        assert total == 20

    def test_args_masked_in_selection(self):
        raw = [_proc("db", 1, pmem=50.0, args="--password=leakme -jar a.jar")]
        top, _ = select_top_processes(raw, "memory", 1)
        assert "leakme" not in top[0].args
        assert "***" in top[0].args

    def test_empty_list(self):
        top, total = select_top_processes([], "cpu", 5)
        assert top == []
        assert total == 0

    def test_none_list(self):
        top, total = select_top_processes(None, "cpu", 5)
        assert top == []
        assert total == 0

    def test_missing_fields_default_to_zero(self):
        raw = [{"name": "x", "pid": 1}]  # 대부분 필드 누락
        top, total = select_top_processes(raw, "cpu", 5)
        assert total == 1
        p = top[0]
        assert p.p100cpu == 0.0 and p.pmem == 0.0 and p.rss == 0
        assert p.user == ""
        assert p.args == ""

    def test_duplicate_names_kept_separately(self):
        raw = [_proc("java", 100, p100cpu=30.0), _proc("java", 200, p100cpu=20.0)]
        top, total = select_top_processes(raw, "cpu", 5)
        assert total == 2
        assert {p.pid for p in top} == {100, 200}

    def test_string_numeric_fields_parsed(self):
        raw = [_proc("x", "1", p100cpu="55.5", rss="2048")]
        top, _ = select_top_processes(raw, "cpu", 1)
        assert top[0].pid == 1
        assert top[0].p100cpu == 55.5
        assert top[0].rss == 2048


class TestGetProcessApiBaseUrl:
    def _cfg(self, **kw):
        # `.env` 누수 차단 — 기본값·명시 CSV만으로 판정한다.
        return AlarmConfig(_env_file=None, **kw)

    def test_default_has_no_hardcoded_host(self):
        """기본값은 공란 — 운영 호스트를 코드에 두지 않는다 (Plan 67 Phase 0 ⑪).

        매핑은 `.env`의 ALARM_PROCESS_API_BASE_URLS_CSV로만 주입한다.
        """
        cfg = self._cfg()
        assert cfg.process_api_base_urls_csv == ""
        assert cfg.get_process_api_base_url("polestar_cm_gp") is None

    def test_env_supplied_mapping(self):
        """`.env`로 주입한 db_id별 매핑이 그대로 조회된다."""
        cfg = self._cfg(
            process_api_base_urls_csv=(
                "polestar_cm_gp=http://gp.example.internal,"
                "polestar_cm_yd=http://yd.example.internal"
            )
        )
        assert cfg.get_process_api_base_url("polestar_cm_gp") == "http://gp.example.internal"
        assert cfg.get_process_api_base_url("polestar_cm_yd") == "http://yd.example.internal"

    def test_unmapped_db_id_returns_none(self):
        cfg = self._cfg(process_api_base_urls_csv="polestar_cm_gp=http://gp.example.internal")
        assert cfg.get_process_api_base_url("polestar") is None
        assert cfg.get_process_api_base_url("polestar_b0") is None

    def test_custom_csv(self):
        cfg = self._cfg(process_api_base_urls_csv="a=http://x,b=http://y")
        assert cfg.get_process_api_base_url("a") == "http://x"
        assert cfg.get_process_api_base_url("b") == "http://y"
        assert cfg.get_process_api_base_url("c") is None

    def test_malformed_entries_ignored(self):
        cfg = self._cfg(process_api_base_urls_csv="garbage,a=http://x,")
        assert cfg.get_process_api_base_url("a") == "http://x"
