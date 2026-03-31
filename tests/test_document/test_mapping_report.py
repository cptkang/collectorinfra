"""매핑 보고서 생성 및 파싱 테스트."""

from __future__ import annotations

import pytest

from src.document.mapping_report import generate_mapping_report, parse_mapping_report


class MockMappingResult:
    """테스트용 MappingResult mock 객체."""

    def __init__(self) -> None:
        self.column_mapping: dict[str, str | None] = {
            "서버명": "CMM_RESOURCE.HOSTNAME",
            "IP주소": "CMM_RESOURCE.IP_ADDRESS",
            "비고": None,
        }
        self.db_column_mapping: dict[str, dict[str, str]] = {
            "polestar": {
                "서버명": "CMM_RESOURCE.HOSTNAME",
                "IP주소": "CMM_RESOURCE.IP_ADDRESS",
            }
        }
        self.mapping_sources: dict[str, str] = {
            "서버명": "synonym",
            "IP주소": "llm_inferred",
            "비고": "",
        }
        self.mapped_db_ids: list[str] = ["polestar"]


class TestGenerateMappingReportBasic:
    """generate_mapping_report 기본 테스트."""

    def test_generate_mapping_report_basic(self) -> None:
        """MappingResult mock 객체로 보고서 생성시 '매핑 결과 요약' 테이블이 포함된다."""
        mock_result = MockMappingResult()
        field_names = ["서버명", "IP주소", "비고"]

        md = generate_mapping_report(
            field_names=field_names,
            mapping_result=mock_result,
            template_name="test.xlsx",
        )

        assert "매핑 결과 요약" in md
        assert "| # | 양식 필드 | 매핑 대상 | DB | 매핑 방법 | 신뢰도 |" in md
        assert "서버명" in md
        assert "IP주소" in md
        assert "비고" in md
        assert "test.xlsx" in md
        # 매핑 성공 통계: 2/3
        assert "2/3" in md

    def test_generate_mapping_report_with_llm_details(self) -> None:
        """llm_inference_details가 있을 때 'LLM 추론 매핑 상세' 섹션이 포함된다."""
        mock_result = MockMappingResult()
        field_names = ["서버명", "IP주소", "비고"]

        llm_details = [
            {
                "field": "IP주소",
                "db_id": "polestar",
                "column": "CMM_RESOURCE.IP_ADDRESS",
                "matched_synonym": "ip_addr",
                "confidence": "high",
                "reason": "IP 주소 관련 컬럼으로 의미적으로 일치",
            }
        ]

        md = generate_mapping_report(
            field_names=field_names,
            mapping_result=mock_result,
            template_name="test.xlsx",
            llm_inference_details=llm_details,
        )

        assert "LLM 추론 매핑 상세" in md
        assert "IP주소" in md
        assert "IP 주소 관련 컬럼으로 의미적으로 일치" in md
        assert "ip_addr" in md
        assert "high" in md

    def test_generate_mapping_report_unmapped_fields(self) -> None:
        """매핑 안 된 필드가 '(매핑 불가)'로 표시된다."""
        mock_result = MockMappingResult()
        field_names = ["서버명", "IP주소", "비고"]

        md = generate_mapping_report(
            field_names=field_names,
            mapping_result=mock_result,
        )

        # '비고' 필드는 column_mapping에서 None이므로 "(매핑 불가)"로 표시
        assert "(매핑 불가)" in md
        # 양식명이 없으면 "(알 수 없음)"
        assert "(알 수 없음)" in md


class TestParseMappingReport:
    """parse_mapping_report 테스트."""

    def test_parse_mapping_report_roundtrip(self) -> None:
        """generate -> parse -> 결과 리스트의 field/column/db_id가 원본과 일치한다."""
        mock_result = MockMappingResult()
        field_names = ["서버명", "IP주소", "비고"]

        md = generate_mapping_report(
            field_names=field_names,
            mapping_result=mock_result,
            template_name="roundtrip.xlsx",
        )

        parsed = parse_mapping_report(md)

        assert len(parsed) == 3

        # field명 확인
        fields = [p["field"] for p in parsed]
        assert "서버명" in fields
        assert "IP주소" in fields
        assert "비고" in fields

        # 매핑된 필드 확인
        server_row = next(p for p in parsed if p["field"] == "서버명")
        assert server_row["column"] == "CMM_RESOURCE.HOSTNAME"
        assert server_row["db_id"] == "polestar"

        ip_row = next(p for p in parsed if p["field"] == "IP주소")
        assert ip_row["column"] == "CMM_RESOURCE.IP_ADDRESS"
        assert ip_row["db_id"] == "polestar"

    def test_parse_mapping_report_with_unmapped(self) -> None:
        """'(매핑 불가)' 항목이 column=None으로 파싱된다."""
        mock_result = MockMappingResult()
        field_names = ["서버명", "IP주소", "비고"]

        md = generate_mapping_report(
            field_names=field_names,
            mapping_result=mock_result,
        )

        parsed = parse_mapping_report(md)

        bigo_row = next(p for p in parsed if p["field"] == "비고")
        assert bigo_row["column"] is None
        assert bigo_row["db_id"] is None  # "-" -> None

    def test_parse_mapping_report_empty(self) -> None:
        """빈 문자열/잘못된 MD가 주어지면 빈 리스트를 반환한다."""
        assert parse_mapping_report("") == []
        assert parse_mapping_report("  ") == []
        assert parse_mapping_report("# 제목만 있는 MD") == []
        assert parse_mapping_report("잘못된 형식의 텍스트\n아무 테이블 없음") == []
