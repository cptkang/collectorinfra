"""급증 의도·임계 해석 (Plan 82 W9-T7 · SPEC-spike-condition).

기본 임계를 쓴 사실과 그 값은 **응답에 반드시 노출**되어야 하므로(§6.12 ②),
`delta_source`가 판정 결과에 실제로 실리는지를 단언한다.

LLM 0 — 급증 판정은 결정적이다(D-035).
"""

from __future__ import annotations

import pytest

from src.domain.change_terms import ChangeTerms, load_change_terms, resolve_spike_request


class TestSpikeIntent:
    @pytest.mark.parametrize("query", [
        "파일시스템 사용률이 갑자기 80% 이상으로 상승한 서버",
        "사용률이 급증한 파일시스템",
        "디스크가 급등한 서버 목록",
        "사용률이 치솟은 파일시스템",
    ])
    def test_declared_terms_trigger(self, query):
        assert resolve_spike_request(query) is not None

    @pytest.mark.parametrize("query", [
        "CPU 사용률 80% 이상인 서버",
        "지난달 파일시스템 사용률 평균",
        "",
        None,
    ])
    def test_absent_terms_return_none(self, query):
        assert resolve_spike_request(query) is None


class TestThreshold:
    def test_default_threshold_is_flagged_for_disclosure(self):
        result = resolve_spike_request("사용률이 갑자기 오른 파일시스템")

        assert result.delta_pp == load_change_terms().default_delta_pp == 20
        assert result.delta_source == "default", "기본값을 썼다는 사실이 응답에 실려야 한다"

    @pytest.mark.parametrize("query,expected", [
        ("전월 대비 30%p 이상 상승한 급증 파일시스템", 30),
        ("급증한 것 중 25% 이상 올라간 것", 25),
        ("급등한 것 중 15% 이상 증가", 15),
        ("전월 대비 40%p 급증", 40),
    ])
    def test_explicit_threshold_wins(self, query, expected):
        result = resolve_spike_request(query)

        assert result.delta_pp == expected
        assert result.delta_source == "explicit"

    def test_absolute_threshold_is_not_mistaken_for_a_delta(self):
        """★ "80% 이상**으로** 상승"의 80은 **도달 수준**이지 차분이 아니다.

        요구 4 원문이 정확히 이 형태다 — 여기서 80을 차분으로 읽으면 +80%p 상승만
        찾게 되어 결과가 조용히 비어버린다.
        """
        result = resolve_spike_request("파일시스템 사용률이 갑자기 80% 이상으로 상승한 리스트")

        assert result.delta_pp == 20
        assert result.delta_source == "default"

    def test_decimal_threshold(self):
        assert resolve_spike_request("급증 중 12.5% 이상 상승").delta_pp == 12.5


class TestInjection:
    def test_terms_can_be_injected(self):
        injected = ChangeTerms(spike_terms=["폭주"], default_delta_pp=7)

        assert resolve_spike_request("트래픽 폭주", injected).delta_pp == 7
        assert resolve_spike_request("트래픽 급증", injected) is None

    def test_matched_term_is_reported(self):
        assert resolve_spike_request("사용률이 급증한 서버").matched_term == "급증"
