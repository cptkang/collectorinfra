"""호스트 가용성 판정 도메인 단위 테스트 (Plan 81 T1 · D-175).

핵심 불변식 두 가지를 고정한다:
    ① **차단은 `avail_status == 1` 하나뿐** — unknown 계열은 전부 통과시킨다(fail-open).
       이 게이트의 유일한 회귀 위험이 "정상 조회를 막는 것"이기 때문이다.
    ② **"전원 off" 단정 금지** — avail_status는 Power off와 통신 이슈를 구분하지 못한다.
"""

import pytest

from src.domain.host_availability import (
    REASON_DOWN,
    REASON_LOOKUP_FAILED,
    REASON_MAINTENANCE,
    REASON_NOT_REGISTERED,
    REASON_OK,
    REASON_STATUS_UNKNOWN,
    REASON_STATUS_UNRECOGNIZED,
    STATE_AVAILABLE,
    STATE_MAINTENANCE,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    describe,
    judge_availability,
)


class TestDecisionTable:
    """SPEC §3.2 결정표 전 행."""

    def test_정상_서버는_available(self):
        av = judge_availability(avail_status=0)
        assert (av.state, av.reason) == (STATE_AVAILABLE, REASON_OK)
        assert av.blocks_collection is False
        assert av.is_notable is False

    def test_down_서버는_unavailable이고_차단한다(self):
        av = judge_availability(avail_status=1)
        assert (av.state, av.reason) == (STATE_UNAVAILABLE, REASON_DOWN)
        assert av.blocks_collection is True

    def test_점검_상태는_maintenance이고_차단하지_않는다(self):
        """G-5 확정: 점검 중이어도 서버는 살아 있을 수 있다 — 경고 후 진행."""
        av = judge_availability(avail_status=0, is_maintenance=1)
        assert (av.state, av.reason) == (STATE_MAINTENANCE, REASON_MAINTENANCE)
        assert av.blocks_collection is False
        assert av.is_notable is True

    def test_알수없음_코드는_unknown이고_차단하지_않는다(self):
        av = judge_availability(avail_status=2)
        assert (av.state, av.reason) == (STATE_UNKNOWN, REASON_STATUS_UNKNOWN)
        assert av.blocks_collection is False

    def test_미등록_대상은_unknown(self):
        av = judge_availability(found=False)
        assert (av.state, av.reason) == (STATE_UNKNOWN, REASON_NOT_REGISTERED)
        assert av.blocks_collection is False

    def test_조회_실패는_판정하지_않고_통과시킨다(self):
        """조회 실패는 '가용하지 않다'가 아니다 — 종전 경로로 진행(fail-open)."""
        av = judge_availability(lookup_failed=True, avail_status=1)
        assert (av.state, av.reason) == (STATE_UNKNOWN, REASON_LOOKUP_FAILED)
        assert av.blocks_collection is False

    def test_다운_중_점검등록은_차단_판정이_우선한다(self):
        av = judge_availability(avail_status=1, is_maintenance=1)
        assert av.state == STATE_UNAVAILABLE
        assert av.evidence["maintenance"] is True


class TestFailOpen:
    """차단은 DOWN 하나뿐 — 나머지는 전부 진행."""

    @pytest.mark.parametrize("status", [2, 3, 9, -1, None, "", "abc"])
    def test_DOWN이_아닌_값은_어느_것도_차단하지_않는다(self, status):
        assert judge_availability(avail_status=status).blocks_collection is False

    def test_규약_밖_코드는_unrecognized로_구분한다(self):
        """막지는 않되 사유는 남긴다 — 침묵 폴백 금지."""
        av = judge_availability(avail_status=7)
        assert av.reason == REASON_STATUS_UNRECOGNIZED
        assert av.evidence["avail_status"] == 7


class TestTypeCoercion:
    """DB 드라이버 편차(문자열·bool·공백) 흡수 — DB2/PG 양쪽 대응."""

    @pytest.mark.parametrize("raw", [1, "1", " 1 "])
    def test_문자열_숫자도_DOWN으로_판정한다(self, raw):
        assert judge_availability(avail_status=raw).state == STATE_UNAVAILABLE

    @pytest.mark.parametrize("raw", [0, "0", " 0 "])
    def test_문자열_0도_정상으로_판정한다(self, raw):
        assert judge_availability(avail_status=raw).state == STATE_AVAILABLE

    def test_is_maintenance_문자열도_해석한다(self):
        assert judge_availability(avail_status=0, is_maintenance="1").state == STATE_MAINTENANCE


class TestDescribe:
    """사용자 문구 — 단정 금지 · 재시도 유도 금지."""

    def test_정상은_문구를_붙이지_않는다(self):
        assert describe(judge_availability(avail_status=0), "svr-01") == ""

    def test_다운_문구는_전원_off를_단정하지_않는다(self):
        msg = describe(judge_availability(avail_status=1, as_of="2026-08-28 10:00:00"), "svr-01")
        assert "비정상(중지/통신이상)" in msg
        assert "확인 시각 2026-08-28 10:00:00" in msg
        assert "전원" not in msg and "power" not in msg.lower()

    def test_다운_문구는_재시도를_유도하지_않는다(self):
        """§1.1 ① 오안내의 직접 수정 — 재시도해도 결과가 같다."""
        msg = describe(judge_availability(avail_status=1), "svr-01")
        assert "다시 시도" not in msg

    def test_점검_문구(self):
        msg = describe(judge_availability(avail_status=0, is_maintenance=1), "svr-01")
        assert "점검(maintenance)" in msg

    def test_미등록_문구는_서버명_확인을_유도한다(self):
        msg = describe(judge_availability(found=False), "svr-01")
        assert "찾지 못했습니다" in msg and "서버명" in msg

    def test_다운이면서_점검이면_점검_사실도_함께_알린다(self):
        msg = describe(judge_availability(avail_status=1, is_maintenance=1), "svr-01")
        assert "비정상(중지/통신이상)" in msg and "점검(maintenance)" in msg

    def test_조회_실패는_문구를_만들지_않는다(self):
        """진단은 로그로 남기고 사용자에게는 종전 경로 결과만 보인다."""
        assert describe(judge_availability(lookup_failed=True), "svr-01") == ""


class TestSerialization:
    def test_to_dict는_체크포인터가_나를_수_있는_형태다(self):
        import json

        av = judge_availability(avail_status=1, is_maintenance=0, as_of="2026-08-28 10:00:00")
        payload = av.to_dict()
        assert json.loads(json.dumps(payload)) == payload
        assert payload["state"] == STATE_UNAVAILABLE
