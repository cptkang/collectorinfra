"""FabriX PII 필터 진단 유틸 테스트 (D-155).

차단 원인 특정(섹션별 로컬 스캔)·스트림 조립 재검사 근거·로그 상세를 검증한다.
전부 로컬 규칙 기반 — 네트워크·LLM 미사용.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

import src.security.pii_filter as pii_filter
from src.security.pii_filter import (
    diagnose_blocked_prompt,
    dump_blocked_payload,
    is_filter_blocked,
    log_filter_block_if_any,
    scan_account_suspects,
    scan_pii,
    scrub_pii,
)


@pytest.fixture
def no_dump(monkeypatch):
    """로그 테스트에서 실제 파일 덤프를 차단한다(repo 오염 방지)."""
    monkeypatch.setattr(pii_filter, "is_block_dump_enabled", lambda: False)

# 핸드폰번호 룰855(2026-08-05 개정): 동일 구분자 형식(010-1234-5678 등) 탐지
_PHONE_SAMPLE = '"contact": "010-1234-5678"'
_BLOCK_NOTICE = "The content was blocked by the filter"


class TestDiagnoseBlockedPrompt:
    def test_attributes_finding_to_section(self):
        """PII가 있는 섹션명이 진단 문자열에 함께 노출된다."""
        diag = diagnose_blocked_prompt({
            "시스템(샘플)": f"### CMM_RESOURCE\n{_PHONE_SAMPLE}",
            "사용자(질의)": "모든 서버 OS 종류 확인",
        })
        assert "시스템(샘플)" in diag
        assert "핸드폰번호" in diag
        # PII 없는 섹션은 진단에 나타나지 않는다
        assert "사용자(질의)" not in diag

    def test_context_identifies_source_value(self):
        """앞뒤 문맥으로 어느 필드/값에서 걸렸는지 식별 가능하다(마스킹 유지)."""
        diag = diagnose_blocked_prompt(
            {"시스템(샘플)": _PHONE_SAMPLE}
        )
        assert "contact" in diag  # 컬럼명이 문맥으로 노출
        assert "010-1234-5678" not in diag  # 원문은 마스킹

    def test_no_match_guides_rule_update(self):
        """전 섹션 일치 없음이면 서버측 규칙 확대 안내를 반환한다."""
        diag = diagnose_blocked_prompt({"시스템": "SELECT 1", "사용자": "OS 확인"})
        assert "일치 없음" in diag
        assert "PII_RULES" in diag

    def test_empty_sections_ignored(self):
        diag = diagnose_blocked_prompt({"빈 섹션": "", "널 유사": None or ""})
        assert "일치 없음" in diag


class TestAssembledStreamDetectionRationale:
    """차단 안내문이 청크로 쪼개지면 라인 단위 검사가 놓친다 — 조립 재검사 근거 고정."""

    def test_split_chunks_pass_individually_but_assembled_detected(self):
        chunks = ["The content was ", "blocked by ", "the filter"]
        # 라인(청크) 단위로는 어느 조각도 차단으로 감지되지 않는다
        assert all(not is_filter_blocked(raw_text=c) for c in chunks)
        # 조립 전문은 감지된다 — kbgenai _stream/_astream 조립 재검사가 필요한 이유
        assert is_filter_blocked(raw_text="".join(chunks))


class TestLogFilterBlockDetail:
    def test_sectioned_scan_in_log(self, caplog, no_dump):
        """prompt_sections를 주면 [PII-FILTER] 로그에 섹션별 진단이 실린다."""
        with caplog.at_level(logging.WARNING):
            blocked = log_filter_block_if_any(
                result={"content": _BLOCK_NOTICE},
                prompt_sections={"시스템(샘플)": _PHONE_SAMPLE},
                where="test",
            )
        assert blocked is True
        joined = " ".join(r.getMessage() for r in caplog.records)
        assert "[PII-FILTER]" in joined
        assert "시스템(샘플)" in joined
        assert "핸드폰번호" in joined

    def test_full_raw_response_in_log(self, caplog, no_dump):
        """정규화 파서가 모르는 키도 원문 응답 JSON 전문으로 로그에 노출된다(D-155 후속1)."""
        with caplog.at_level(logging.WARNING):
            log_filter_block_if_any(
                result={
                    "status": "FILTER_INVALID",
                    "newPolicyField": 871,  # 파서 미인지 가상 키 — 원문 노출로 확인 가능해야 함
                },
                where="test",
            )
        joined = " ".join(r.getMessage() for r in caplog.records)
        assert "원문 응답" in joined
        assert "newPolicyField" in joined
        assert "871" in joined

    def test_policy_id_detail_when_reasons_present(self, caplog, no_dump):
        """filterBlockReason이 오면 policy_id 해석(유형명)이 탐지 상세에 실린다."""
        with caplog.at_level(logging.WARNING):
            log_filter_block_if_any(
                result={
                    "status": "FILTER_INVALID",
                    "filterBlockReason": {
                        "ko": "개인정보가 포함되어 있습니다",
                        "policy_id": 855,
                        "filter_log_id": "flog-123",
                    },
                },
                where="test",
            )
        joined = " ".join(r.getMessage() for r in caplog.records)
        assert "핸드폰번호" in joined  # policy_id 855 → 유형명 해석
        assert "flog-123" in joined
        assert "개인정보가 포함되어" in joined

    def test_not_blocked_returns_false(self, caplog, no_dump):
        assert log_filter_block_if_any(result={"content": "SELECT 1"}) is False


class TestPolicyTargetedDiagnosis:
    """서버 사유(policy_id)의 룰을 프롬프트에 역적용하는 표적 대조 (D-155 후속5).

    기대 동작: ①사유 유형의 룰로 걸린 값을 특정 ②매칭 0건이면 "알려진 필터로는
    차단 불가한 프롬프트가 차단됨(서버 필터 변경 의심)" 명시 ③미상 유형은 대응 룰
    없음 명시. 덤프에는 대조 결과가 함께 기록된다.
    """

    @staticmethod
    def _blocked(policy_id):
        return {
            "status": "FILTER_INVALID",
            "filter_block_reason": {
                "ko": "개인정보탐지",
                "policy_id": policy_id,
                "message": "The content was blocked by the filter",
                "result_code": "FR-400",
                "filter_log_id": "9999",
            },
        }

    def test_matched_value_identified_by_policy_rule(self, caplog, no_dump):
        """계좌번호 정책 차단 + 프롬프트에 계좌형 값 → 그 값이 원인으로 특정된다."""
        with caplog.at_level(logging.WARNING):
            log_filter_block_if_any(
                result=self._blocked(856),  # 운영분석질문 계좌번호
                prompt='SELECT * 예시\n"acct": "6565-01-02-233123"\nLIMIT 100',
                where="test",
            )
        joined = " ".join(r.getMessage() for r in caplog.records)
        assert "원인 값 대조" in joined
        assert "계좌번호: 프롬프트 매칭 1건" in joined
        assert "acct" in joined  # 문맥으로 어느 값인지 식별
        assert "6565-01-02-233123" not in joined  # 기본 마스킹

    def test_unmatchable_block_flags_server_filter_change(self, caplog, no_dump):
        """핸드폰 정책 차단인데 프롬프트에 핸드폰 형태가 없음 → 서버 필터 변경 의심 명시(2-1)."""
        with caplog.at_level(logging.WARNING):
            log_filter_block_if_any(
                result=self._blocked(855),  # 핸드폰번호
                prompt="SELECT hostname FROM servers LIMIT 100",
                where="test",
            )
        joined = " ".join(r.getMessage() for r in caplog.records)
        assert "차단될 수 없는 프롬프트가 차단됨" in joined
        assert "변경/추가된 것으로 판단" in joined

    def test_unknown_policy_reports_no_local_rule(self, caplog, no_dump):
        """정책표 밖 policy_id → 로컬 대응 룰 없음(신규/미상 필터) 명시."""
        with caplog.at_level(logging.WARNING):
            log_filter_block_if_any(
                result=self._blocked(999),
                prompt="SELECT 1",
                where="test",
            )
        joined = " ".join(r.getMessage() for r in caplog.records)
        assert "로컬 대응 룰 없음" in joined

    def test_dump_includes_diagnosis(self, tmp_path):
        """덤프 파일에 policy/rule id와 원인 값 대조 결과가 함께 기록된다."""
        path = dump_blocked_payload(
            response_text="blocked",
            prompt="p",
            detail="계좌번호(policy_id=856, log_id=9999)",
            diagnosis="계좌번호: 프롬프트 매칭 1건 → ...",
            dump_dir=str(tmp_path),
        )
        content = Path(path).read_text(encoding="utf-8")
        assert "policy/rule id" in content
        assert "856" in content
        assert "원인 값 대조: 계좌번호: 프롬프트 매칭 1건" in content


class TestDumpBlockedPayload:
    def test_writes_prompt_and_response(self, tmp_path):
        """섹션·응답 전문이 무마스킹으로 덤프되고 경로가 반환된다."""
        path = dump_blocked_payload(
            response_text=_BLOCK_NOTICE,
            prompt_sections={"시스템(샘플)": _PHONE_SAMPLE},
            detail="차단 상세 미제공",
            where="_agenerate",
            dump_dir=str(tmp_path),
        )
        assert path is not None
        content = (tmp_path / path.split("\\")[-1].split("/")[-1]).read_text(encoding="utf-8")
        assert _BLOCK_NOTICE in content
        assert "010-1234-5678" in content  # 원문 무마스킹(대조용) — 반출 금지 명기 전제
        assert "반출 금지" in content
        assert "시스템(샘플)" in content

    def test_disabled_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.setattr(pii_filter, "is_block_dump_enabled", lambda: False)
        assert dump_blocked_payload(
            response_text="x", prompt="y", dump_dir=str(tmp_path)
        ) is None
        assert list(tmp_path.iterdir()) == []


class TestScanScrubBasics:
    """기존 규칙 회귀 고정(진단 개편 후에도 스캔·스크럽 시맨틱 불변)."""

    def test_scan_detects_phone(self):
        matches = scan_pii(_PHONE_SAMPLE)
        assert any(m.name == "핸드폰번호" for m in matches)

    def test_scrub_masks_phone_preserving_format(self):
        out = scrub_pii(_PHONE_SAMPLE)
        assert "010-1234-5678" not in out
        assert "contact" in out  # 비-PII 텍스트는 무변경

    def test_scrub_passes_clean_text(self):
        text = "hostname=web01 os=RHEL8 cpu=4"
        assert scrub_pii(text) == text


class TestRevisedRules2026:
    """2026-08-05 공식 가이드 개정 규칙 반영 (docs/pii_filtering_rules.md 동기)."""

    @pytest.mark.parametrize("phone", [
        "01012345678",
        "010 1234 5678",
        "010-1234-5678",
        "010.1234.5678",
        "011-123-4567",
    ])
    def test_phone_revised_formats_detected(self, phone):
        assert any(m.name == "핸드폰번호" for m in scan_pii(phone)), phone

    def test_phone_mixed_separator_not_detected(self):
        """혼합 형식(010-12345678)은 서버 개정 룰과 동일하게 비탐지(미러링)."""
        assert not any(m.name == "핸드폰번호" for m in scan_pii("010-12345678"))

    @pytest.mark.parametrize("account", [
        "6565-01-02-233123",
        "6471 01 02 233123",
        "123-456-789012",
    ])
    def test_account_revised_formats_detected(self, account):
        assert any(m.name == "계좌번호" for m in scan_pii(account)), account

    @pytest.mark.parametrize("text", [
        "12-34-567-89012",          # 2-2-3-5 공식 예외
        "2026-08-05",               # 날짜(숫자 8자리 < 10)
        "2026-08-05 12:30:45",      # 타임스탬프(콜론으로 자릿수 창 단절)
        "A6565-01-02-233123",       # 단일 알파벳 접두 접수번호
    ])
    def test_account_exclusions_not_detected(self, text):
        assert not any(m.name == "계좌번호" for m in scan_pii(text)), text

    # 인프라 데이터에서 계좌번호(851) 룰에 걸리는 실형태 카탈로그(D-155 후속3 —
    # 공동존/PG 경로 "계좌 유사값 부재" 실측 대응). 유선 전화는 핸드폰 룰(855)이
    # 아니라 계좌 룰로 차단된다는 점이 육안 진단의 맹점.
    @pytest.mark.parametrize("text", [
        '"tel": "02-1234-5678"',       # 유선 전화(서울 2-4-4 = 10자리)
        '"contact": "031-123-4567"',   # 유선 전화(지역 3-3-4 = 10자리)
        "fax: 0505-123-4567",          # 팩스/평생번호(4-3-4 = 11자리)
        "line_no: 12-3456-789012",     # 회선·자산·계약 코드(2-4-6 = 12자리)
        "mgmt: 192-168-10-115",        # IP 하이픈 표기(3-3-2-3 = 11자리)
        "code: 6471 01 02 233123",     # 공백 구분 코드
    ])
    def test_account_infra_data_shapes_detected(self, text):
        assert any(m.name == "계좌번호" for m in scan_pii(text)), text

    @pytest.mark.parametrize("text", [
        "담당: 홍길동 (02-123-4567)",   # 9자리 — 10자리 미만
        "mgmt: 192-168-0-11",          # 9자리
        "os_build: 10-0-17763-1234",   # 1자리 그룹 포함(그룹 최소 2자리)
        "mac: 00-1A-2B-3C-4D-5E",      # 16진수 문자 혼입
        "| 123 | 456 | 7890 |",        # 파이프 구분(구분자 클래스 밖)
    ])
    def test_account_infra_data_shapes_not_detected(self, text):
        assert not any(m.name == "계좌번호" for m in scan_pii(text)), text


class TestAccountSuspects:
    """계좌번호(851) 광폭 매칭 의심 형태 — 날짜·타임스탬프 (D-155 후속3)."""

    _SAMPLE_LINE = '"created_at": "2026-06-17 02:30:45", "id": 123'

    def test_timestamp_in_digit_heavy_line_reported(self):
        suspects = scan_account_suspects(self._SAMPLE_LINE, unmask=True)
        assert suspects
        assert "2026-06-17 02:30:45" in suspects[0].masked

    def test_db2_timestamp_reported(self):
        suspects = scan_account_suspects(
            '"dtime": "2026-08-05-14.30.45.123456"', unmask=True
        )
        assert suspects

    def test_short_date_only_line_not_reported(self):
        """총 숫자 10자리 미만 라인은 창을 못 채우므로 의심 비보고."""
        assert scan_account_suspects("start: 2026-06-17") == []

    def test_diagnose_includes_suspect_tier(self):
        diag = diagnose_blocked_prompt({"시스템(샘플)": self._SAMPLE_LINE})
        assert "[의심]" in diag
        assert "SECURITY_PII_SCRUB_SUSPECT_DATES" in diag

    def test_scrub_defangs_dates_when_enabled(self, monkeypatch):
        """플래그 ON이면 날짜 구분자를 점으로 치환 — 값·자릿수는 보존(마스킹 아님)."""
        monkeypatch.setattr(pii_filter, "is_scrub_suspect_dates_enabled", lambda: True)
        out = scrub_pii(self._SAMPLE_LINE)
        assert "2026.06.17.02:30:45" in out
        assert '"id": 123' in out
        # DB2 타임스탬프도 무해화 → 정식 계좌 룰 마스킹 대신 형식 보존
        out2 = scrub_pii('"dtime": "2026-08-05-14.30.45.123456", "seq": 99')
        assert "2026.08.05.14.30.45.123456" in out2

    def test_scrub_preserves_dates_when_disabled(self, monkeypatch):
        monkeypatch.setattr(pii_filter, "is_scrub_suspect_dates_enabled", lambda: False)
        out = scrub_pii(self._SAMPLE_LINE)
        assert "2026-06-17 02:30:45" in out  # 일반 타임스탬프는 현행 무변경


class TestResponseShapeParsing:
    """가이드 실측 응답 shape — snake_case·FR-200/400·APIM (D-155 후속2)."""

    _BLOCKED_CHUNK = {
        "content": "The content was blocked by the filter",
        "filter_block_reason": {
            "ko": "개인정보",
            "en": "Personal Information",
            "policy_id": "364",
            "message": "The content was blocked by the filter",
            "result_code": "FR-400",
            "filter_log_id": "3132268",
        },
        "status": "FILTER_INVALID",
        "event_status": "CHUNK",
    }

    _PASSED_CHUNK = {
        "content": "요청을 처리하기 위한 API 후보를 0개 발견하였습니다.",
        "filter_block_reason": {
            "ko": None,
            "en": None,
            "policy_id": None,
            "message": "The content was passed",
            "result_code": "FR-200",
            "filter_log_id": None,
        },
        "status": "SUCCESS",
        "event_status": "STATUS",
    }

    def test_snake_case_blocked_chunk_detected(self):
        assert is_filter_blocked(self._BLOCKED_CHUNK) is True

    def test_passed_chunk_with_fr200_reason_not_blocked(self):
        """통과 라인에도 filter_block_reason(FR-200)이 실린다 — 차단 오인 금지."""
        assert is_filter_blocked(self._PASSED_CHUNK) is False

    def test_snake_case_policy_id_in_log(self, caplog, no_dump):
        with caplog.at_level(logging.WARNING):
            log_filter_block_if_any(result=self._BLOCKED_CHUNK, where="test")
        joined = " ".join(r.getMessage() for r in caplog.records)
        assert "364" in joined
        assert "FR-400" in joined
        assert "개인정보" in joined
        assert "3132268" in joined

    def test_apim_block_detected_with_rule_name(self, caplog, no_dump):
        apim = (
            "{'detail': \"Error code: 400 - {'error': '민감정보 감지됨: ', "
            "'rule_name': '핸드폰번호', 'catched_text': '010-1234-5678'}\"}"
        )
        assert is_filter_blocked(raw_text=apim) is True
        with caplog.at_level(logging.WARNING):
            log_filter_block_if_any(raw_text=apim, where="test")
        joined = " ".join(r.getMessage() for r in caplog.records)
        assert "핸드폰번호" in joined
        assert "010-1234-5678" not in joined  # catched_text는 스크럽되어 노출
