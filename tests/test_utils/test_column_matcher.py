"""column_matcher 유틸리티 단위 테스트.

resolve_column_key, build_resolved_mapping, camel_to_snake, _is_close_match의
동작을 검증한다.
"""

from __future__ import annotations

import pytest

from src.utils.column_matcher import (
    _is_close_match,
    build_resolved_mapping,
    camel_to_snake,
    resolve_column_key,
)


# ──────────────────────────────────────────────
# camel_to_snake
# ──────────────────────────────────────────────
class TestCamelToSnake:
    def test_simple_camel(self) -> None:
        assert camel_to_snake("OSType") == "os_type"

    def test_serial_number(self) -> None:
        assert camel_to_snake("SerialNumber") == "serial_number"

    def test_already_snake(self) -> None:
        assert camel_to_snake("already_snake") == "already_snake"

    def test_lowercase(self) -> None:
        assert camel_to_snake("hostname") == "hostname"

    def test_consecutive_uppercase(self) -> None:
        assert camel_to_snake("OSVerson") == "os_verson"

    def test_agent_version(self) -> None:
        assert camel_to_snake("AgentVersion") == "agent_version"

    def test_ipaddress(self) -> None:
        # IPAddress: "IP" + "Address" -> "ip_address"
        assert camel_to_snake("IPAddress") == "ip_address"


# ──────────────────────────────────────────────
# _is_close_match
# ──────────────────────────────────────────────
class TestIsCloseMatch:
    def test_identical(self) -> None:
        assert _is_close_match("abc", "abc") is True

    def test_one_char_substitution(self) -> None:
        # verson vs version: 's' -> different position
        assert _is_close_match("osverson", "osversion") is True

    def test_one_char_insertion(self) -> None:
        assert _is_close_match("abc", "abcd") is True

    def test_one_char_deletion(self) -> None:
        assert _is_close_match("abcd", "abc") is True

    def test_two_char_difference(self) -> None:
        assert _is_close_match("abc", "axyz") is False

    def test_empty_strings(self) -> None:
        assert _is_close_match("", "") is True

    def test_single_char_vs_empty(self) -> None:
        assert _is_close_match("a", "") is True

    def test_two_chars_vs_empty(self) -> None:
        assert _is_close_match("ab", "") is False


# ──────────────────────────────────────────────
# resolve_column_key
# ──────────────────────────────────────────────

RESULT_KEYS = {
    "resource_id",
    "resource_name",
    "resource_type",
    "cmm_resource_hostname",
    "cmm_resource_ipaddress",
    "resource_description",
    "os_type",
    "serial_number",
    "agent_version",
    "os_version",
    "model",
    "vendor",
}


class TestResolveColumnKey:
    """resolve_column_key의 5+1단계 매칭을 검증한다."""

    def test_exact_match(self) -> None:
        assert resolve_column_key("os_type", RESULT_KEYS) == "os_type"

    def test_table_dot_column_hostname(self) -> None:
        """table.column -> table_column 매칭 (점을 언더스코어로 대체)."""
        result = resolve_column_key("cmm_resource.hostname", RESULT_KEYS)
        assert result == "cmm_resource_hostname"

    def test_table_dot_column_ipaddress(self) -> None:
        result = resolve_column_key("cmm_resource.ipaddress", RESULT_KEYS)
        assert result == "cmm_resource_ipaddress"

    def test_eav_ostype(self) -> None:
        """EAV:OSType -> os_type CamelCase->snake_case 매칭."""
        result = resolve_column_key("EAV:OSType", RESULT_KEYS)
        assert result == "os_type"

    def test_eav_serial_number(self) -> None:
        result = resolve_column_key("EAV:SerialNumber", RESULT_KEYS)
        assert result == "serial_number"

    def test_eav_osversion_typo(self) -> None:
        """EAV:OSVerson (원본 DB 오타) -> os_version 매칭."""
        result = resolve_column_key("EAV:OSVerson", RESULT_KEYS)
        assert result == "os_version"

    def test_eav_model(self) -> None:
        result = resolve_column_key("EAV:Model", RESULT_KEYS)
        assert result == "model"

    def test_eav_vendor(self) -> None:
        result = resolve_column_key("EAV:Vendor", RESULT_KEYS)
        assert result == "vendor"

    def test_eav_agent_version(self) -> None:
        result = resolve_column_key("EAV:AgentVersion", RESULT_KEYS)
        assert result == "agent_version"

    def test_exact_match_priority(self) -> None:
        """정확 매칭이 우선순위를 가지는지 확인."""
        keys = {"OSType", "os_type"}
        assert resolve_column_key("OSType", keys) == "OSType"

    def test_no_match(self) -> None:
        assert resolve_column_key("nonexistent_column", RESULT_KEYS) is None

    def test_empty_inputs(self) -> None:
        assert resolve_column_key("", RESULT_KEYS) is None
        assert resolve_column_key("os_type", set()) is None

    def test_case_insensitive(self) -> None:
        assert resolve_column_key("OS_TYPE", RESULT_KEYS) == "os_type"


# ──────────────────────────────────────────────
# build_resolved_mapping
# ──────────────────────────────────────────────
class TestBuildResolvedMapping:
    """build_resolved_mapping의 통합 동작을 검증한다."""

    COLUMN_MAPPING = {
        "서버명": "cmm_resource.hostname",
        "IP": "cmm_resource.ipaddress",
        "OS": "EAV:OSType",
        "Serial 번호": "EAV:SerialNumber",
        "소분류": "EAV:OSVerson",
        "일련번호": None,
        "CPU": None,
    }

    def test_resolved_and_unresolved(self) -> None:
        resolved, unresolved = build_resolved_mapping(
            self.COLUMN_MAPPING, RESULT_KEYS,
        )
        # None-mapped fields should be preserved as None
        assert resolved["일련번호"] is None
        assert resolved["CPU"] is None

        # Mapped fields should be resolved to actual result keys
        assert resolved["서버명"] == "cmm_resource_hostname"
        assert resolved["IP"] == "cmm_resource_ipaddress"
        assert resolved["OS"] == "os_type"
        assert resolved["Serial 번호"] == "serial_number"
        assert resolved["소분류"] == "os_version"

        # No unresolved fields (all matched)
        assert unresolved == []

    def test_unresolved_field(self) -> None:
        """해석 불가능한 매핑은 unresolved로 반환."""
        mapping = {"필드A": "totally_unknown_col", "필드B": None}
        resolved, unresolved = build_resolved_mapping(mapping, RESULT_KEYS)
        assert "필드A" in unresolved
        assert "필드B" not in unresolved
        # 원본 값이 유지됨
        assert resolved["필드A"] == "totally_unknown_col"

    def test_empty_mapping(self) -> None:
        resolved, unresolved = build_resolved_mapping({}, RESULT_KEYS)
        assert resolved == {}
        assert unresolved == []
