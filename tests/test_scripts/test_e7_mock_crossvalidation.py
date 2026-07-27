"""Plan 66 Wave 1-C — E7(텍스트·주석 신호) × 목업 생성기 교차 검증.

Wave 1-A(`scripts/mock_polestar_events.py`)가 만드는 시나리오 페이로드를 Wave 1-B(E7)
코드 경로에 태워, Plan 60 §17.9 수용 기준 ①③④⑤⑥이 **목업 페이로드로 실제 재현되는지**를
대조한다. 라이브 alarm_server/Redis 불필요 — payload → `_build_alarm_event_from_payload`
→ 도메인 함수(annotation_signal·notification_policy·correlation) 단위 경로로 검증한다.

접점(§17.9 수용 기준 ↔ Plan 65 시나리오):
  ① S3 semantic-dup(11): 재발신 주석(`=> 담당자`·`서비스 영향 없음`)을 extract_annotation_signal이
     operator_ack/resolution으로 추출 + annotation_harvest_enabled=on에서 record_recurrence에
     하베스트(재통보 0=append 감사만).
  ③ S8 비알람: is_operational_alarm이 실알람(sev3-page UPS)엔 True. **비알람(승인요청)엔 False** —
     단 목업 카탈로그에 비알람 시나리오가 부재하여(GAP-1) Plan 65 §2.4 S8 텍스트로 최소 재구성.
  ④ S8 이질 포맷: 목업 설비 페이로드(SAE·facility.UPS)가 format_tolerant on/off 양쪽에서 크래시·
     드롭 없이 처리. `||(장애)` 네트워크 포맷은 목업 카탈로그에 부재(GAP-2)하여 재구성으로 검증.
  ⑤ S5 사이트 상관: signature_tokens 사이트 차원 off=비트동일·on=차원 추가. **단 목업 cascade
     시나리오는 사이트명을 conditionLog에만 실어(GAP-2) extract_site_token 추출 0** — 재구성 검증.
  ⑥ 회귀: 전 E7 플래그 off에서 목업 페이로드 판정이 E7 도입 전과 비트동일.

**목업 재구성 원칙(Known Mistakes 실측 우선)**: 카탈로그에 없는 S8 비알람·`||(장애)` 포맷은
목업 **자체 빌더 make_payload**로 최소 재구성하되(스키마 동일), 텍스트 출처(Plan 65 §2.4)와
카탈로그 부재 사유를 주석에 명시한다. 구현 미수정(검증만) — 갭은 보고로만 처리.
"""

from __future__ import annotations

from types import SimpleNamespace

from scripts.mock_polestar_events import SCENARIOS_BY_NAME, make_payload
from src.alarm.application.alarm_worker import AlarmWorker
from src.alarm.domain.annotation_signal import extract_annotation_signal
from src.alarm.domain.correlation import extract_site_token, signature_tokens
from src.alarm.domain.notification_policy import (
    TIER_DASHBOARD,
    TIER_PAGE,
    TIER_SUPPRESS,
    TIER_TICKET,
    decide_notification,
    is_operational_alarm,
)
from src.api.routes.alarm import _build_alarm_event_from_payload

RUN_ID = "xval"
DB_ID = "polestar_pg"


# ── 공용 헬퍼 ─────────────────────────────────────────────────
def _steps(name: str) -> list:
    """목업 시나리오의 Step 리스트(고정 카탈로그·번호 계약)를 빌드한다."""
    return SCENARIOS_BY_NAME[name].build(RUN_ID, DB_ID)


def _event(name: str, idx: int = 0, *, format_tolerant: bool = False):
    """목업 시나리오 step[idx]의 payload를 AlarmEvent로 변환한다(E7-c 경로 경유)."""
    payload = _steps(name)[idx].payload
    return _build_alarm_event_from_payload(payload, format_tolerant=format_tolerant)


def _cfg(**over) -> SimpleNamespace:
    """decide_notification용 정책 config(E7 플래그 미설정=getattr False = off)."""
    base = dict(
        suppress_max_severity=2,
        importance_value_map={"HIGH": "높음"},
        resolved_to_dashboard=False,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _ctx(**over) -> dict:
    """noise_ctx(중요도 높음·정상 컨텍스트) 기본형."""
    ctx = {
        "importance_id": "HIGH", "maintenance": False, "noti_policy": None,
        "parent_avail_status": None, "source": "polestar_db",
    }
    ctx.update(over)
    return ctx


def _planned(**kw) -> dict:
    """extract_annotation_signal.to_dict() 형태의 주석 dict(코로보레이션 게이팅 입력)."""
    base = {"planned_work": False, "resolution": False, "operator_ack": False}
    base.update(kw)
    return base


class _RecCaptureStore:
    """record_recurrence 호출 인자를 포착하는 스파이(하베스트 감사 검증용·재통보 없음)."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def record_recurrence(self, **kwargs) -> None:
        self.calls.append(kwargs)


def _worker(*, harvest: bool) -> AlarmWorker:
    """annotation_harvest 플래그만 설정한 최소 워커(_record_recurrence 단위 경로용)."""
    w = AlarmWorker(SimpleNamespace(noise_gate=SimpleNamespace(
        recurrence_audit_every_n=1, annotation_harvest_enabled=harvest,
    )))
    w._decision_store = _RecCaptureStore()
    return w


# ═════════════════════════════════════════════════════════════
# ① S3 semantic-dup — 주석 재발신 하베스팅 (extract_annotation_signal + 워커)
# ═════════════════════════════════════════════════════════════
class TestS3AnnotationHarvestCrossval:
    def test_mock_semantic_dup_recurrence_markers_extracted(self):
        """목업 semantic-dup step2 재발신 주석에서 operator_ack·resolution 추출.

        실측 conditionLog: '[95 % (> 95 %)] => 담당자 ○○○ 수석차장. 서비스 영향 없음.'
        → '=> 담당자'(operator_ack) + '서비스 영향 없음'(resolution). planned_work 부재.
        """
        ev = _event("semantic-dup", idx=1)  # step2 = 표현만 다른 유사 알람(재발신 주석)
        sig = extract_annotation_signal(ev.condition_log)
        assert sig.operator_ack is True
        assert sig.resolution is True
        assert sig.planned_work is False
        assert sig.has_signal() is True

    def test_mock_original_alarm_has_no_annotation(self):
        """목업 semantic-dup step1(원 알람)은 주석 없음 → 빈 신호(하위호환)."""
        ev = _event("semantic-dup", idx=0)
        assert extract_annotation_signal(ev.condition_log).has_signal() is False

    def test_worker_harvest_on_attaches_annotation_no_renotify(self):
        """harvest on: 재발신 주석을 record_recurrence(annotation=…)로 append 적재(재통보 0).

        호출은 1회(append 감사만) — 재통보 채널 발송 아님. annotation에 resolution·operator_ack
        하베스트 + chattering='repeating'(E7-d ISA-18.2 라벨).
        """
        ev = _event("semantic-dup", idx=1)
        w = _worker(harvest=True)
        w._record_recurrence("fp-mock", ev, {"count": 2, "first_seen": 1.0})
        assert len(w._decision_store.calls) == 1  # append 감사만(재통보 0)
        ann = w._decision_store.calls[0]["annotation"]
        assert ann["resolution"] is True
        assert ann["operator_ack"] is True
        assert ann["chattering"] == "repeating"
        assert w._decision_store.calls[0]["alarm_id"] == ev.alarm_id

    def test_worker_harvest_off_bit_identical(self):
        """harvest off(기본): record_recurrence 호출에 annotation kwarg 부재(비트동일)."""
        ev = _event("semantic-dup", idx=1)
        w = _worker(harvest=False)
        w._record_recurrence("fp-mock", ev, {"count": 2, "first_seen": 1.0})
        assert "annotation" not in w._decision_store.calls[0]

    def test_s3_resolution_not_demoted_without_planned_work(self):
        """S3 resolution+operator_ack만으로는 DASHBOARD 강등 안 됨(planned_work 부재).

        step7.7 코로보레이션 게이팅은 planned_work를 필수로 요구 — S3 재발신(계획작업 아님)은
        하베스트/첨부만 되고 강등되지 않는다(§17.3 — 텍스트 단독 억제강화 금지).
        """
        ev = _event("semantic-dup", idx=1)  # severity=1(SEV_THRESHOLD)
        sig = extract_annotation_signal(ev.condition_log)
        cfg = _cfg(annotation_planned_suppress=True)
        d = decide_notification(ev, None, None, _ctx(), cfg, annotation=sig.to_dict())
        # sev1 × 높음 = TICKET(매트릭스) — 강등 없음.
        assert d.tier == TIER_TICKET


# ═════════════════════════════════════════════════════════════
# ③ S8 비알람 사전분류 — is_operational_alarm + 게이트 step0.5
# ═════════════════════════════════════════════════════════════
class TestS8NonAlarmFilterCrossval:
    def test_mock_sev3_ups_is_operational_true(self):
        """목업 sev3-page(설비 UPS 실알람)는 is_operational_alarm=True(알람 마커 '경고·미만')."""
        ev = _event("sev3-page", idx=0, format_tolerant=True)
        assert is_operational_alarm(ev) is True

    def test_mock_sev3_ups_pages_even_with_filter_on(self):
        """비알람 필터 on이어도 실알람 sev3는 step0.5 미억제 → step3 PAGE(단락 불변)."""
        ev = _event("sev3-page", idx=0, format_tolerant=True)
        cfg = _cfg(non_alarm_filter_enabled=True)
        d = decide_notification(ev, None, None, _ctx(), cfg)
        assert d.tier == TIER_PAGE and "심각도3" in d.reason

    def test_all_mock_scenarios_are_operational_alarms(self):
        """목업 카탈로그 시나리오 페이로드는 실알람(is_operational_alarm=True) — non-alarm 제외.

        GAP-1 해소(Plan 66 E7 커버리지): [13] non-alarm은 **의도적 비알람**(승인요청)이라
        is_operational_alarm=False가 정상이므로 이 전수 검사에서 제외한다(그 False 판정은
        TestE7CoverageScenariosClosed에서 별도 고정). 나머지 실알람 시나리오는 비알람 오분류 0.
        """
        for name, scen in SCENARIOS_BY_NAME.items():
            if scen.stub or name == "non-alarm":
                continue
            for st in scen.build(RUN_ID, DB_ID):
                ev = _build_alarm_event_from_payload(st.payload, format_tolerant=True)
                assert is_operational_alarm(ev) is True, f"{name}: 비알람 오분류"

    def test_reconstructed_s8_non_alarm_suppressed(self):
        """재구성 S8 비알람(Plan 65 §2.4 실측 텍스트)은 is_operational_alarm=False → step0.5 SUPPRESS.

        make_payload(목업 자체 빌더)로 카탈로그 부재분(GAP-1)을 최소 재구성. 텍스트 출처:
        Plan 65 §2.4 S8 '내부Cloud ○○○님의 Cloud PC 사양변경 승인바랍니다'(승인요청·비알람).
        """
        payload = make_payload(
            db_id=DB_ID, server_name="내부Cloud", severity=1,
            alarm_name="내부Cloud ○○○님의 Cloud PC 사양변경 승인바랍니다",
            alarm_id="RECON-nonalarm-1",
        )
        ev = _build_alarm_event_from_payload(payload, format_tolerant=True)
        assert is_operational_alarm(ev) is False
        cfg = _cfg(non_alarm_filter_enabled=True)
        d = decide_notification(ev, None, None, _ctx(), cfg)
        assert d.tier == TIER_SUPPRESS and "비운영 알람" in d.reason

    def test_reconstructed_non_alarm_kept_when_filter_off(self):
        """필터 off(기본): 재구성 비알람도 현행 매트릭스 판정(비트동일·회귀 0)."""
        payload = make_payload(
            db_id=DB_ID, server_name="내부Cloud", severity=1,
            alarm_name="내부Cloud ○○○님의 Cloud PC 사양변경 승인바랍니다",
            alarm_id="RECON-nonalarm-2",
        )
        ev = _build_alarm_event_from_payload(payload, format_tolerant=True)
        d = decide_notification(ev, None, None, _ctx(), _cfg())
        assert d.tier != TIER_SUPPRESS  # 마커 필터 미적용 → sev1×높음=TICKET


# ═════════════════════════════════════════════════════════════
# ④ S8 이질 포맷 파서 견고성 — _build_alarm_event_from_payload
# ═════════════════════════════════════════════════════════════
class TestS8HeterogeneousFormatCrossval:
    def test_mock_facility_payload_no_crash_off_and_on(self):
        """목업 설비 페이로드(SAE·facility.UPS·호스트 접두 없음)가 off/on 양쪽 크래시·드롭 0.

        sev3-page의 serverName='SAE 0011649'·resourceType='facility.UPS'는 호스트 접두
        관례를 벗어난 이질 형식(S8). 양 경로에서 severity=3 보존·이벤트 생성 성공.
        """
        payload = _steps("sev3-page")[0].payload
        off = _build_alarm_event_from_payload(payload, format_tolerant=False)
        on = _build_alarm_event_from_payload(payload, format_tolerant=True)
        assert off.severity == 3 and on.severity == 3
        assert off.is_clear is False and on.is_clear is False
        assert on.server_name == "SAE 0011649"

    def test_all_mock_payloads_build_without_crash(self):
        """목업 전 시나리오 페이로드가 format_tolerant on/off 양쪽에서 크래시 없이 이벤트화."""
        for name, scen in SCENARIOS_BY_NAME.items():
            if scen.stub:
                continue
            for st in scen.build(RUN_ID, DB_ID):
                for ft in (False, True):
                    ev = _build_alarm_event_from_payload(st.payload, format_tolerant=ft)
                    assert ev.alarm_id == st.alarm_id, f"{name}: alarm_id 손실"

    def test_reconstructed_network_format_no_crash_and_site_token(self):
        """재구성 `||(장애)` 네트워크 포맷(Plan 65 §2.4 S5/S8)은 off 크래시 0·on 사이트 토큰 노출.

        ⚠ GAP-2: `<장비ID>.<도메인>||(장애) <사이트명>` 네트워크 장비 포맷은 목업 카탈로그에
        **부재**(cascade 시나리오는 noise-test-* 픽스처 호스트명 사용). make_payload로 재구성.
        """
        payload = make_payload(
            db_id=DB_ID, server_name="S8530JUM-4331-1.sotori.com||(장애) 세종대",
            severity=2, alarm_name="가용성", alarm_id="RECON-net-1",
        )
        off = _build_alarm_event_from_payload(payload, format_tolerant=False)  # 크래시 0
        assert off.severity == 2
        on = _build_alarm_event_from_payload(payload, format_tolerant=True)
        assert on.raw_payload.get("_site_token") == "세종대"
        assert "_site_token" not in payload  # 원본 payload 불변


# ═════════════════════════════════════════════════════════════
# ⑤ S5 사이트 상관 차원 — extract_site_token + signature_tokens
# ═════════════════════════════════════════════════════════════
class TestS5SiteCorrelationCrossval:
    def test_signature_tokens_site_off_bit_identical(self):
        """correlation_site_dimension off(extra=''): signature_tokens 현행과 비트동일."""
        base = signature_tokens("가용성", "network.Switch", "")
        assert signature_tokens("가용성", "network.Switch", "", extra="") == base

    def test_signature_tokens_site_on_adds_dimension(self):
        """on(extra=사이트토큰): 사이트 토큰이 상관 차원으로 추가된다."""
        toks = signature_tokens("가용성", "network.Switch", "", extra="세종대")
        assert "세종대" in toks
        assert "세종대" not in signature_tokens("가용성", "network.Switch", "")

    def test_mock_cascade_site_token_not_extracted_gap(self):
        """⚠ GAP-2: 목업 cascade 시나리오에서 사이트 토큰이 추출되지 않음(0).

        목업 _build_cascade는 사이트명 '세종대'를 **conditionLog 텍스트에만** 싣고
        (serverName='noise-test-high'·resourceName='상위-네트워크'), extract_site_token은
        serverName/resourceName의 `||(장애) <사이트명>` 마커만 파싱하므로 추출 결과가 빈 문자열.
        → 목업 cascade는 E7-d 사이트 상관 차원을 end-to-end로 구동하지 못함(재구성 필요).
        """
        payload = _steps("cascade")[0].payload  # step1 상위(root) 네트워크 자원
        site = extract_site_token(
            str(payload.get("serverName", "")), str(payload.get("resourceName", ""))
        )
        assert site == ""  # 추출 0 — 갭 고정
        ev = _build_alarm_event_from_payload(payload, format_tolerant=True)
        assert ev.raw_payload.get("_site_token") is None

    def test_reconstructed_network_site_feeds_signature_dimension(self):
        """재구성 `||(장애)` 페이로드는 사이트 토큰 추출 → signature_tokens 사이트 차원 반영.

        E7-c 추출 → E7-d 상관의 end-to-end 접점을 재구성 페이로드로 검증한다.
        """
        payload = make_payload(
            db_id=DB_ID, server_name="S8530JUM-4331-1.sotori.com||(장애) 세종대",
            severity=2, alarm_name="가용성", alarm_id="RECON-net-2",
        )
        ev = _build_alarm_event_from_payload(payload, format_tolerant=True)
        site = ev.raw_payload.get("_site_token")
        assert site == "세종대"
        # 워커가 이 site 토큰을 extra로 주입하면(on) 상관 차원에 반영된다.
        toks_on = signature_tokens(ev.alarm_name, ev.resource_type, "", extra=site)
        toks_off = signature_tokens(ev.alarm_name, ev.resource_type, "", extra="")
        assert "세종대" in toks_on
        assert "세종대" not in toks_off


# ═════════════════════════════════════════════════════════════
# ⑥ 회귀 — 전 E7 플래그 off에서 목업 페이로드 판정 비트동일
# ═════════════════════════════════════════════════════════════
class TestAllFlagsOffRegressionCrossval:
    def test_build_event_bit_identical_all_mock_payloads(self):
        """format_tolerant off/on: 목업 전 페이로드(정상 severity)의 severity·is_clear 비트동일."""
        for name, scen in SCENARIOS_BY_NAME.items():
            if scen.stub:
                continue
            for st in scen.build(RUN_ID, DB_ID):
                off = _build_alarm_event_from_payload(st.payload, format_tolerant=False)
                on = _build_alarm_event_from_payload(st.payload, format_tolerant=True)
                assert (off.severity, off.is_clear) == (on.severity, on.is_clear), name

    def test_decide_notification_bit_identical_when_e7_off(self):
        """E7 플래그 전부 off: annotation을 넘겨도 목업 이벤트 tier/reason/priority 불변.

        semantic-dup step2(주석 보유)를 대표로, E7-a 입력(annotation)을 넘겨도 플래그 off면
        E7 도입 전과 동일 판정(회귀 0). noise_ctx는 양쪽 동일하게 두고 annotation만 격리한다
        (change_nearby/correlated는 E5/E2 입력이라 회귀 격리 대상이 아님).
        """
        ev = _event("semantic-dup", idx=1)
        sig = extract_annotation_signal(ev.condition_log)
        cfg = _cfg()  # 전 E7 플래그 미설정 = off
        base = decide_notification(ev, None, None, _ctx(), cfg)
        withann = decide_notification(
            ev, None, None, _ctx(), cfg, annotation=sig.to_dict(),
        )
        assert (withann.tier, withann.reason, withann.priority) == (
            base.tier, base.reason, base.priority
        )

    def test_is_operational_alarm_no_effect_when_filter_off(self):
        """non_alarm_filter off: is_operational_alarm 판정과 무관하게 매트릭스 판정(비트동일)."""
        # 재구성 비알람이라도 필터 off면 억제 안 됨.
        payload = make_payload(
            db_id=DB_ID, server_name="내부Cloud", severity=2,
            alarm_name="내부Cloud ○○○님의 Cloud PC 사양변경 승인바랍니다",
            alarm_id="RECON-reg-1",
        )
        ev = _build_alarm_event_from_payload(payload, format_tolerant=True)
        d = decide_notification(ev, None, None, _ctx(), _cfg())
        assert d.tier == TIER_PAGE  # sev2×높음=PAGE(비알람 필터 미적용)

    def test_sev3_short_circuit_unchanged_for_mock_ups(self):
        """목업 sev3-page: 전 E7 플래그 off에서 심각도3 단락 PAGE 불변."""
        ev = _event("sev3-page", idx=0)
        d = decide_notification(ev, None, None, _ctx(), _cfg())
        assert d.tier == TIER_PAGE and "심각도3" in d.reason


# ═════════════════════════════════════════════════════════════
# GAP 해소 — 카탈로그 신규 시나리오([13] non-alarm · [14] net-site-cascade)로 E7 실구동
# (Plan 66 E7 커버리지 보완: Wave 1-C가 make_payload 재구성으로만 검증하던 GAP-1/GAP-2를
#  이제 **번호로 선택 가능한 카탈로그 시나리오**가 실제로 E7을 구동함을 고정한다.)
# ═════════════════════════════════════════════════════════════
class TestE7CoverageScenariosClosed:
    def test_catalog_non_alarm_is_non_operational(self):
        """[13] non-alarm 카탈로그 시나리오 → is_operational_alarm=False (GAP-1 해소).

        승인/안내성 텍스트(알람 마커 부재 + 비알람 마커 존재)로 비운영 판정된다.
        """
        ev = _event("non-alarm", idx=0, format_tolerant=True)
        assert is_operational_alarm(ev) is False

    def test_catalog_non_alarm_step0_5_suppress_when_filter_on(self):
        """[13] non-alarm: non_alarm_filter on → 게이트 step0.5 SUPPRESS('비운영 알람')."""
        ev = _event("non-alarm", idx=0, format_tolerant=True)
        d = decide_notification(ev, None, None, _ctx(), _cfg(non_alarm_filter_enabled=True))
        assert d.tier == TIER_SUPPRESS and "비운영 알람" in d.reason

    def test_catalog_non_alarm_kept_when_filter_off(self):
        """[13] non-alarm: 필터 off(기본)면 매트릭스 판정 — 억제 안 됨(회귀 0)."""
        ev = _event("non-alarm", idx=0, format_tolerant=True)
        d = decide_notification(ev, None, None, _ctx(), _cfg())
        assert d.tier != TIER_SUPPRESS

    def test_catalog_net_site_cascade_extracts_site_token(self):
        """[14] net-site-cascade: 각 스텝 serverName의 ||(장애) 마커 → '세종대' 추출(GAP-2 해소).

        기존 [9] cascade는 사이트명을 conditionLog에만 실어 추출 0이었으나(재구성 필요), 신규
        시나리오는 serverName에 마커를 실어 format_tolerant 파싱 시 _site_token이 노출된다.
        """
        steps = SCENARIOS_BY_NAME["net-site-cascade"].build(RUN_ID, DB_ID)
        assert len(steps) >= 2
        for idx in range(len(steps)):
            ev = _event("net-site-cascade", idx=idx, format_tolerant=True)
            assert ev.raw_payload.get("_site_token") == "세종대"
            assert "_site_token" not in steps[idx].payload  # 원본 payload 불변

    def test_catalog_net_site_cascade_feeds_signature_dimension(self):
        """[14] net-site-cascade: 추출 site 토큰이 signature_tokens 사이트 차원에 반영(E7-d 접점)."""
        ev = _event("net-site-cascade", idx=0, format_tolerant=True)
        site = ev.raw_payload.get("_site_token")
        toks_on = signature_tokens(ev.alarm_name, ev.resource_type, "", extra=site)
        toks_off = signature_tokens(ev.alarm_name, ev.resource_type, "")
        assert "세종대" in toks_on
        assert "세종대" not in toks_off

    def test_catalog_net_site_cascade_is_operational_alarm(self):
        """[14] net-site-cascade: 실제 장애 알람이므로 is_operational_alarm=True(오분류 방지)."""
        for idx in range(len(SCENARIOS_BY_NAME["net-site-cascade"].build(RUN_ID, DB_ID))):
            ev = _event("net-site-cascade", idx=idx, format_tolerant=True)
            assert is_operational_alarm(ev) is True

    def test_catalog_net_site_cascade_off_path_no_crash(self):
        """[14] net-site-cascade: format_tolerant off면 site 토큰 미노출·크래시 0(비트동일)."""
        for idx in range(len(SCENARIOS_BY_NAME["net-site-cascade"].build(RUN_ID, DB_ID))):
            ev = _build_alarm_event_from_payload(
                SCENARIOS_BY_NAME["net-site-cascade"].build(RUN_ID, DB_ID)[idx].payload,
                format_tolerant=False,
            )
            assert ev.severity == 2
            assert ev.raw_payload.get("_site_token") is None
