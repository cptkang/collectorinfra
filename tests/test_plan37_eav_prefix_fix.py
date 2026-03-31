"""Plan 37: Synonym 통합 관리 및 EAV 접두사 비교 오류 수정 테스트."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.document.field_mapper import (
    MappingResult,
    _apply_eav_synonym_mapping,
    _synonym_match,
)
from src.utils.schema_utils import normalize_field_name


# =============================================================================
# 1. normalize_field_name 단위 테스트
# =============================================================================


class TestNormalizeFieldName:
    """normalize_field_name() 정규화 함수 테스트."""

    def test_newline_replaced_with_space(self) -> None:
        """줄바꿈이 공백으로 치환된다."""
        assert normalize_field_name("서버\n명") == "서버 명"

    def test_tab_replaced_with_space(self) -> None:
        """탭이 공백으로 치환된다."""
        assert normalize_field_name("서버\t명") == "서버 명"

    def test_carriage_return_newline(self) -> None:
        """\\r\\n이 공백으로 치환된다."""
        assert normalize_field_name("서버\r\n명") == "서버 명"

    def test_multiple_spaces_collapsed(self) -> None:
        """연속 공백이 단일 공백으로 축소된다."""
        assert normalize_field_name("CPU  사용률") == "CPU 사용률"

    def test_leading_trailing_spaces_stripped(self) -> None:
        """앞뒤 공백이 제거된다."""
        assert normalize_field_name("  공백  ") == "공백"

    def test_mixed_whitespace(self) -> None:
        """줄바꿈 + 다중 공백이 정규화된다."""
        assert normalize_field_name("서버\n  명") == "서버 명"

    def test_empty_string(self) -> None:
        """빈 문자열은 빈 문자열로 반환된다."""
        assert normalize_field_name("") == ""

    def test_normal_string_unchanged(self) -> None:
        """정상 문자열은 변경되지 않는다."""
        assert normalize_field_name("서버명") == "서버명"

    def test_unicode_nfc_normalization(self) -> None:
        """Unicode NFC 정규화가 적용된다."""
        import unicodedata

        # NFD로 분해된 한글이 NFC로 결합된다
        nfd = unicodedata.normalize("NFD", "가")
        nfc = unicodedata.normalize("NFC", "가")
        assert normalize_field_name(nfd) == nfc


# =============================================================================
# 2. word_writer EAV 처리 테스트
# =============================================================================


class TestWordWriterGetValueFromRow:
    """word_writer._get_value_from_row EAV 접두사 처리 테스트."""

    def test_eav_prefix_exact_match(self) -> None:
        """EAV:OSType -> OSType으로 정확히 매칭된다."""
        from src.document.word_writer import _get_value_from_row

        result = _get_value_from_row({"OSType": "Linux"}, "EAV:OSType")
        assert result == "Linux"

    def test_eav_prefix_case_insensitive(self) -> None:
        """EAV:OSType -> ostype으로 대소문자 무시 매칭된다."""
        from src.document.word_writer import _get_value_from_row

        result = _get_value_from_row({"ostype": "Linux"}, "EAV:OSType")
        assert result == "Linux"

    def test_table_column_format_preserved(self) -> None:
        """기존 table.column 형식이 정상 동작한다."""
        from src.document.word_writer import _get_value_from_row

        result = _get_value_from_row(
            {"hostname": "srv01"}, "CMM_RESOURCE.hostname"
        )
        assert result == "srv01"

    def test_exact_key_match(self) -> None:
        """정확한 키가 있으면 바로 반환된다."""
        from src.document.word_writer import _get_value_from_row

        result = _get_value_from_row(
            {"EAV:OSType": "Linux", "OSType": "Windows"}, "EAV:OSType"
        )
        assert result == "Linux"

    def test_no_match_returns_none(self) -> None:
        """매칭되지 않으면 None을 반환한다."""
        from src.document.word_writer import _get_value_from_row

        result = _get_value_from_row({"unrelated": "value"}, "EAV:OSType")
        assert result is None


# =============================================================================
# 3. excel_writer EAV 폴백 테스트
# =============================================================================


class TestExcelWriterGetValueEavFallback:
    """excel_writer._get_value_from_row EAV 폴백 테스트."""

    def test_eav_fallback_strips_prefix(self) -> None:
        """EAV 접두사가 폴백 매칭 시 제거된다."""
        from src.document.excel_writer import _get_value_from_row

        result = _get_value_from_row({"OSType": "Linux"}, "EAV:OSType")
        assert result == "Linux"

    def test_eav_fallback_case_insensitive(self) -> None:
        """EAV 폴백에서 대소문자 무시 매칭된다."""
        from src.document.excel_writer import _get_value_from_row

        result = _get_value_from_row({"ostype": "Linux"}, "EAV:OSType")
        assert result == "Linux"

    def test_table_column_still_works(self) -> None:
        """기존 table.column 폴백이 정상 동작한다."""
        from src.document.excel_writer import _get_value_from_row

        result = _get_value_from_row(
            {"hostname": "srv01"}, "CMM_RESOURCE.hostname"
        )
        assert result == "srv01"


# =============================================================================
# 4. EAV synonym global 통합 테스트
# =============================================================================


class TestApplyEavSynonymMappingWithGlobal:
    """_apply_eav_synonym_mapping에서 global_synonyms 병합 테스트."""

    def test_global_synonyms_merged(self) -> None:
        """global_synonyms의 추가 단어로 매칭 성공."""
        remaining = {"운영체제 종류"}
        eav_synonyms = {"OSType": ["운영체제", "OS종류"]}
        # global에만 "운영체제 종류"가 등록되어 있는 경우
        global_synonyms = {"OSType": ["운영체제 종류"]}
        result = MappingResult()

        _apply_eav_synonym_mapping(
            remaining,
            eav_synonyms,
            result,
            eav_db_id="polestar",
            global_synonyms=global_synonyms,
        )

        assert "운영체제 종류" not in remaining
        assert (
            result.db_column_mapping["polestar"]["운영체제 종류"] == "EAV:OSType"
        )

    def test_no_global_synonyms_backward_compatible(self) -> None:
        """global_synonyms=None이면 기존 동작과 동일하다."""
        remaining = {"OS종류"}
        eav_synonyms = {"OSType": ["OS종류"]}
        result = MappingResult()

        _apply_eav_synonym_mapping(
            remaining, eav_synonyms, result, eav_db_id="polestar"
        )

        assert "OS종류" not in remaining
        assert result.db_column_mapping["polestar"]["OS종류"] == "EAV:OSType"

    def test_global_synonyms_no_duplicate_words(self) -> None:
        """global과 eav_names에 동일 단어가 있어도 중복 없이 비교."""
        remaining = {"운영체제"}
        eav_synonyms = {"OSType": ["운영체제"]}
        global_synonyms = {"OSType": ["운영체제", "OS 종류"]}
        result = MappingResult()

        _apply_eav_synonym_mapping(
            remaining,
            eav_synonyms,
            result,
            eav_db_id="polestar",
            global_synonyms=global_synonyms,
        )

        assert "운영체제" not in remaining
        assert (
            result.db_column_mapping["polestar"]["운영체제"] == "EAV:OSType"
        )


# =============================================================================
# 6. synonym 매칭 정규화 테스트
# =============================================================================


class TestSynonymMatchNormalization:
    """_synonym_match에서 정규화가 적용되는지 테스트."""

    def test_normalized_word_matches(self) -> None:
        """synonym 단어에 줄바꿈이 있어도 정규화 후 매칭된다."""
        # field_lower는 이미 정규화된 값이 전달됨
        result = _synonym_match(
            "서버 명",
            {"servers.hostname": ["서버\n명", "호스트명"]},
        )
        assert result == "servers.hostname"

    def test_normalized_multiple_spaces(self) -> None:
        """다중 공백이 있는 synonym도 매칭된다."""
        result = _synonym_match(
            "cpu 사용률",
            {"cpu.usage": ["CPU  사용률"]},
        )
        assert result == "cpu.usage"


# =============================================================================
# 7. 회귀 테스트
# =============================================================================


class TestRegressions:
    """기존 동작이 유지되는지 확인하는 회귀 테스트."""

    def test_table_column_synonym_match_preserved(self) -> None:
        """기존 table.column 형식 synonym 매칭이 정상 동작한다."""
        result = _synonym_match(
            "서버명",
            {"servers.hostname": ["서버명", "호스트명"]},
        )
        assert result == "servers.hostname"

    def test_column_name_direct_match(self) -> None:
        """컬럼명 자체로도 매칭된다."""
        result = _synonym_match(
            "hostname",
            {"servers.hostname": ["서버명"]},
        )
        assert result == "servers.hostname"

    def test_eav_synonym_without_global_unchanged(self) -> None:
        """global_synonyms 없이 EAV 매칭이 기존과 동일하게 동작한다."""
        remaining = {"운영체제", "제조사"}
        eav_synonyms = {
            "OSType": ["운영체제"],
            "Vendor": ["제조사"],
        }
        result = MappingResult()

        _apply_eav_synonym_mapping(
            remaining, eav_synonyms, result, eav_db_id="polestar"
        )

        assert len(remaining) == 0
        assert (
            result.db_column_mapping["polestar"]["운영체제"] == "EAV:OSType"
        )
        assert (
            result.db_column_mapping["polestar"]["제조사"] == "EAV:Vendor"
        )
        assert result.mapping_sources["운영체제"] == "eav_synonym"
        assert result.mapping_sources["제조사"] == "eav_synonym"

    def test_eav_attr_name_normalize_match(self) -> None:
        """EAV 속성명 자체도 정규화 후 매칭된다."""
        remaining = {"ostype"}
        eav_synonyms = {"OSType": ["운영체제"]}
        result = MappingResult()

        _apply_eav_synonym_mapping(
            remaining, eav_synonyms, result, eav_db_id="polestar"
        )

        assert "ostype" not in remaining
        assert (
            result.db_column_mapping["polestar"]["ostype"] == "EAV:OSType"
        )
