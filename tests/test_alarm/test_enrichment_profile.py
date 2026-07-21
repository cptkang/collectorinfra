"""kind별 L1 보강 프로파일(Plan 60 E6 §16) 순수 매핑·요지 조립·CSV 오버라이드 테스트.

resolve_profile(kind→프로파일), build_summary(요지 문자열), parse_profile_map_csv(오버라이드)를
결정적 순수 함수로 검증한다. 데이터 조회는 없다.
"""

from __future__ import annotations

from src.alarm.domain.enrichment_profile import (
    EnrichmentProfile,
    build_summary,
    parse_profile_map_csv,
    resolve_profile,
)


class TestResolveProfile:
    def test_disk_profile(self):
        p = resolve_profile("disk")
        assert p is not None
        assert p.kind == "disk"
        assert p.title == "용량/마운트 상위 소비"
        assert p.has_l1_data is True

    def test_network_profile_has_l1_data(self):
        assert resolve_profile("network").has_l1_data is True

    def test_process_profile_no_l1_data(self):
        # 데이터 소스 미확정 → 요지 제목만(첨부 데이터 없음)
        p = resolve_profile("process")
        assert p.has_l1_data is False
        assert p.title == "생존·재시작 이력"

    def test_log_profile_no_l1_data(self):
        p = resolve_profile("log")
        assert p.has_l1_data is False
        assert p.title == "조건 로그 시그니처"

    def test_cpu_memory_profiles_defined(self):
        # cpu/memory도 정의됨(요지 조립·테스트용) — has_l1_data=True
        assert resolve_profile("cpu").has_l1_data is True
        assert resolve_profile("memory").has_l1_data is True

    def test_case_insensitive(self):
        assert resolve_profile("DISK").kind == "disk"

    def test_none_kind_returns_none(self):
        assert resolve_profile(None) is None
        assert resolve_profile("") is None

    def test_unknown_kind_returns_none(self):
        assert resolve_profile("gpu") is None


class TestBuildSummary:
    def test_title_and_signals_joined(self):
        out = build_summary("용량/마운트 상위 소비", ("호스트 프로세스 상위(참고)",))
        assert out == "용량/마운트 상위 소비 — 호스트 프로세스 상위(참고)"

    def test_multiple_signals(self):
        out = build_summary("제목", ("a", "b"))
        assert out == "제목 — a · b"

    def test_empty_signals_returns_title(self):
        assert build_summary("제목", ()) == "제목"

    def test_profile_fields_reusable(self):
        p = resolve_profile("network")
        assert build_summary(p.title, p.signals).startswith("연결/트래픽 상위 — ")


class TestParseProfileMapCsv:
    def test_single_override(self):
        assert parse_profile_map_csv("disk=커스텀 제목") == {"disk": "커스텀 제목"}

    def test_multiple_overrides(self):
        out = parse_profile_map_csv("disk=A,log=B")
        assert out == {"disk": "A", "log": "B"}

    def test_empty_returns_empty_dict(self):
        assert parse_profile_map_csv("") == {}

    def test_malformed_entries_ignored(self):
        out = parse_profile_map_csv("garbage,disk=A,=nokey,log=")
        assert out == {"disk": "A"}

    def test_kind_lowercased(self):
        assert parse_profile_map_csv("DISK=A") == {"disk": "A"}


class TestResolveWithOverride:
    def test_override_replaces_title_only(self):
        override = parse_profile_map_csv("disk=사용자 정의 디스크 요지")
        p = resolve_profile("disk", override)
        assert p.title == "사용자 정의 디스크 요지"
        # signals·has_l1_data는 기본 유지
        assert p.signals == resolve_profile("disk").signals
        assert p.has_l1_data is True

    def test_override_missing_kind_uses_default(self):
        override = parse_profile_map_csv("network=X")
        p = resolve_profile("disk", override)
        assert p.title == "용량/마운트 상위 소비"

    def test_override_none_uses_default(self):
        assert resolve_profile("disk", None).title == "용량/마운트 상위 소비"

    def test_returned_profile_is_enrichment_profile(self):
        assert isinstance(resolve_profile("disk"), EnrichmentProfile)
