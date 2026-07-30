"""운영자 주석 LLM 분류기 단위 테스트 (Plan 67 R3-(v) · D-132).

검증 축:
  A. domain 계약 — `AnnotationLabel` enum 소비(`signal_from_labels`), 미지 라벨 무시.
  B. 분류 정확도(목 LLM) — 정규식이 놓치는 손글 표현("이상무"·"문제없음")을 정답 분류.
     대조로 정규식 폴백이 실제로 그 표현을 놓치는 것도 함께 고정한다(전환의 근거).
  C. 캐시 — 동일 주석 재분류 0회, 항목 수 상한(FIFO 축출) + TTL 만료 sweep.
  D. 강등 — 타임아웃·LLM 예외·JSON 계약 위반 시 정규식 분류로 강등 + 사유 로그(침묵 금지).
  E. 워커 배선 — 플래그 OFF(기본) 시 분류기 미생성·정규식 경로 비트동일, ON 시 분류기 경유.
  F. 게이트 무회귀 — 신호 형태(3 bool dict)가 동일해 `decide_notification` 판정 로직 무변경
     (Plan 52 벤치마크 D-048/D-049 대상 경로를 목 분류기로 재확인).

실 LLM 호출은 없다(전부 목 — D-127).
"""

from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace

from src.alarm.application.alarm_worker import AlarmWorker
from src.alarm.application.annotation_classifier import AnnotationClassifier
from src.alarm.domain.annotation_signal import (
    AnnotationLabel,
    AnnotationSignal,
    extract_annotation_signal,
    signal_from_labels,
)
from src.alarm.domain.notification_policy import (
    TIER_DASHBOARD,
    TIER_PAGE,
    TIER_SUPPRESS,
    decide_notification,
)
from src.config import NoiseGateConfig

# ─── 목 LLM ────────────────────────────────────────────────────────────────
# 운영자 주석 표현 → 정답 라벨(사람이 읽은 판정). 목 LLM은 이 표를 substring 조회해
# 프롬프트 계약(JSON `{"labels": [...]}`)대로 응답한다.
_TRUTH: dict[str, list[str]] = {
    "작업예정입니다": ["planned_work"],
    "이상무": ["resolution"],
    "문제없음": ["resolution"],
    "담당자 통화 완료, 이상무": ["resolution", "operator_ack"],
    "cpu=95 (threshold 90)": [],
}


class _LabelLLM:
    """정답표를 조회해 응답하는 목 LLM(호출 횟수 계측)."""

    def __init__(self) -> None:
        self.calls = 0

    async def ainvoke(self, messages):  # noqa: ANN001, ANN201
        self.calls += 1
        user = messages[-1]["content"]
        labels: list[str] = []
        # 가장 구체적인(긴) 표현부터 조회 — "담당자 통화 완료, 이상무"가 "이상무"에 먼저
        # 걸려 부분 정답이 되는 것을 막는다.
        for phrase in sorted(_TRUTH, key=len, reverse=True):
            if phrase in user:
                labels = _TRUTH[phrase]
                break
        return SimpleNamespace(content=json.dumps({"labels": labels}))


class _ScriptedLLM:
    """고정 문자열을 그대로 반환하는 목 LLM(계약 위반·환각 응답 재현용)."""

    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = 0

    async def ainvoke(self, messages):  # noqa: ANN001, ANN201
        self.calls += 1
        return SimpleNamespace(content=self.content)


class _SlowLLM:
    """타임아웃을 유발하는 목 LLM."""

    def __init__(self, delay: float = 1.0) -> None:
        self.delay = delay

    async def ainvoke(self, messages):  # noqa: ANN001, ANN201
        await asyncio.sleep(self.delay)
        return SimpleNamespace(content='{"labels": []}')


class _BoomLLM:
    """호출 시 예외를 올리는 목 LLM(서빙 장애 재현)."""

    async def ainvoke(self, messages):  # noqa: ANN001, ANN201
        raise RuntimeError("서빙 불가")


def _cfg(**over) -> SimpleNamespace:
    """분류기용 최소 설정(.env 누수 차단 — 검증 대상 필드만 명시)."""
    base = dict(
        enable_noise_gate=True,
        annotation_llm_classification_enabled=True,
        annotation_llm_timeout_seconds=5.0,
        annotation_llm_cache_max=500,
        annotation_llm_cache_ttl_seconds=3600,
    )
    base.update(over)
    return SimpleNamespace(noise_gate=SimpleNamespace(**base))


# ═════════════════════════════════════════════════════════════
# A. domain 계약 — 분류 결과 enum 소비
# ═════════════════════════════════════════════════════════════
class TestSignalFromLabels:
    def test_labels_map_to_signal_fields(self):
        s = signal_from_labels(
            [AnnotationLabel.PLANNED_WORK, AnnotationLabel.RESOLUTION]
        )
        assert (s.planned_work, s.resolution, s.operator_ack) == (True, True, False)
        assert s.has_signal() is True

    def test_empty_labels_is_empty_signal(self):
        assert signal_from_labels([]) == AnnotationSignal()
        assert signal_from_labels([]).has_signal() is False

    def test_unknown_label_ignored(self):
        # 분류기 환각·프로토콜 오차가 domain 불변식을 깨지 못한다(모르는 라벨 = 신호 없음).
        unknown = ["planned_work", None, 3]  # 라벨 문자열·None·정수 = 전부 enum 아님
        assert signal_from_labels(unknown).has_signal() is False  # type: ignore[arg-type]

    def test_label_values_match_signal_dict_keys(self):
        # 라벨 값 == to_dict 키(감사 필드와 1:1) — 3분류 고정.
        assert {label.value for label in AnnotationLabel} == set(
            AnnotationSignal().to_dict()
        )


# ═════════════════════════════════════════════════════════════
# B. 분류 정확도 — 정규식이 놓치는 손글 표현
# ═════════════════════════════════════════════════════════════
class TestClassificationAccuracy:
    def test_regex_fallback_misses_handwritten_resolution(self):
        # 전환 근거 실측(regex 검토 §5.4 정정): "작업예정입니다"는 현행 정규식에 **매칭된다**
        # (`작업\s*예정`). 미매칭은 "이상무"·"문제없음" 2건 — 이 둘이 LLM 전환의 실제 이득.
        assert extract_annotation_signal("작업예정입니다").planned_work is True
        assert extract_annotation_signal("이상무").has_signal() is False
        assert extract_annotation_signal("문제없음").has_signal() is False

    async def test_llm_classifies_unmatched_expressions(self):
        clf = AnnotationClassifier(_cfg(), llm=_LabelLLM())
        assert (await clf.classify("작업예정입니다")).planned_work is True
        assert (await clf.classify("이상무")).resolution is True
        assert (await clf.classify("문제없음")).resolution is True
        assert clf.degradations == 0  # 강등 없이 LLM 경로로 판정

    async def test_llm_classifies_multiple_labels(self):
        clf = AnnotationClassifier(_cfg(), llm=_LabelLLM())
        s = await clf.classify("담당자 통화 완료, 이상무")
        assert (s.resolution, s.operator_ack) == (True, True)
        assert s.planned_work is False

    async def test_machine_text_yields_empty_signal(self):
        clf = AnnotationClassifier(_cfg(), llm=_LabelLLM())
        assert (await clf.classify("cpu=95 (threshold 90)")).has_signal() is False

    async def test_blank_text_skips_llm(self):
        llm = _LabelLLM()
        clf = AnnotationClassifier(_cfg(), llm=llm)
        assert (await clf.classify("")).has_signal() is False
        assert (await clf.classify("   ")).has_signal() is False
        assert llm.calls == 0 and clf.calls == 0  # 빈 텍스트에 과금 호출 금지

    async def test_hallucinated_label_ignored_not_degraded(self):
        # 계약(labels 리스트)은 지켰고 값만 미지 → 그 라벨만 무시(강등 아님).
        clf = AnnotationClassifier(
            _cfg(), llm=_ScriptedLLM('{"labels": ["urgent", "resolution"]}')
        )
        s = await clf.classify("이상무")
        assert (s.resolution, s.planned_work, s.operator_ack) == (True, False, False)
        assert clf.degradations == 0


# ═════════════════════════════════════════════════════════════
# C. 캐시 — 동일 주석 재분류 방지 + bound + TTL sweep
# ═════════════════════════════════════════════════════════════
class TestCache:
    async def test_same_text_classified_once(self):
        llm = _LabelLLM()
        clf = AnnotationClassifier(_cfg(), llm=llm)
        first = await clf.classify("이상무")
        second = await clf.classify("  이상무  ")  # 정규화(strip) 후 동일 키
        assert first == second
        assert llm.calls == 1 and clf.calls == 1 and clf.cache_hits == 1

    async def test_cache_bounded_by_max(self):
        clf = AnnotationClassifier(_cfg(annotation_llm_cache_max=2), llm=_LabelLLM())
        for text in ("이상무", "문제없음", "작업예정입니다"):
            await clf.classify(text)
        assert len(clf._cache) == 2  # 가장 오래된 키 FIFO 축출

    async def test_cache_disabled_when_max_zero(self):
        llm = _LabelLLM()
        clf = AnnotationClassifier(_cfg(annotation_llm_cache_max=0), llm=llm)
        await clf.classify("이상무")
        await clf.classify("이상무")
        assert len(clf._cache) == 0 and llm.calls == 2

    async def test_expired_entries_swept(self):
        # 값 bound와 별개로 **키 만료 sweep**도 동작해야 한다(무한 증식 차단).
        llm = _LabelLLM()
        clf = AnnotationClassifier(
            _cfg(annotation_llm_cache_ttl_seconds=0.01), llm=llm
        )
        await clf.classify("이상무")
        assert len(clf._cache) == 1
        await asyncio.sleep(0.02)
        await clf.classify("문제없음")  # sweep 선행 → 만료 키 제거
        assert len(clf._cache) == 1
        assert llm.calls == 2 and clf.cache_hits == 0


# ═════════════════════════════════════════════════════════════
# D. 강등 — 실패 시 정규식 분류 + 사유 로그
# ═════════════════════════════════════════════════════════════
class TestDegradation:
    async def test_timeout_degrades_to_regex(self, caplog):
        clf = AnnotationClassifier(
            _cfg(annotation_llm_timeout_seconds=0.01), llm=_SlowLLM(0.5)
        )
        with caplog.at_level(logging.WARNING):
            s = await clf.classify("계획정지 작업으로 서비스 영향 없음")
        # 정규식 분류 결과와 동일(강등) + 사유 로그(침묵 강등 금지).
        assert s == extract_annotation_signal("계획정지 작업으로 서비스 영향 없음")
        assert s.planned_work is True and s.resolution is True
        assert clf.degradations == 1
        assert any("강등" in r.getMessage() for r in caplog.records)

    async def test_llm_exception_degrades_to_regex(self, caplog):
        clf = AnnotationClassifier(_cfg(), llm=_BoomLLM())
        with caplog.at_level(logging.WARNING):
            s = await clf.classify("정기 점검 예정")
        assert s.planned_work is True  # 정규식 마커로 회복
        assert clf.degradations == 1
        assert any("서빙 불가" in r.getMessage() for r in caplog.records)

    async def test_contract_violation_degrades_to_regex(self, caplog):
        # labels 키 부재·비리스트 → 계약 위반(강등). 빈 배열은 정상 응답이라 강등 아님.
        clf = AnnotationClassifier(_cfg(), llm=_ScriptedLLM('{"result": "ok"}'))
        with caplog.at_level(logging.WARNING):
            s = await clf.classify("이상무")
        assert s.has_signal() is False  # 정규식은 "이상무"를 놓친다(강등 결과 그대로)
        assert clf.degradations == 1

    async def test_non_json_response_degrades(self):
        clf = AnnotationClassifier(_cfg(), llm=_ScriptedLLM("분류할 수 없습니다"))
        s = await clf.classify("서비스 영향 없음")
        assert s.resolution is True and clf.degradations == 1

    async def test_empty_labels_is_not_degradation(self):
        clf = AnnotationClassifier(_cfg(), llm=_ScriptedLLM('{"labels": []}'))
        s = await clf.classify("cpu=95")
        assert s.has_signal() is False and clf.degradations == 0

    async def test_llm_creation_failure_degrades_once(self, monkeypatch, caplog):
        # create_llm 실패(설정 오류) → 영구 강등(호출마다 재시도하지 않음).
        import src.llm as llm_module

        created = {"n": 0}

        def _boom(config, **kw):  # noqa: ANN001, ANN202
            created["n"] += 1
            raise ValueError("지원하지 않는 LLM 프로바이더")

        monkeypatch.setattr(llm_module, "create_llm", _boom)
        clf = AnnotationClassifier(_cfg())
        with caplog.at_level(logging.WARNING):
            assert (await clf.classify("정기 점검")).planned_work is True
            assert (await clf.classify("정기 점검 2회차")).planned_work is True
        assert created["n"] == 1 and clf.degradations == 2


# ═════════════════════════════════════════════════════════════
# E. 워커 배선 — 플래그 OFF 기본 = 정규식 경로 비트동일
# ═════════════════════════════════════════════════════════════
def _event(condition_log: str = "", description: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        condition_log=condition_log, description=description,
        alarm_id="A-1", severity=2, is_clear=False,
    )


class TestWorkerWiring:
    def test_flag_default_off(self):
        # 기본 OFF — 알람 유입량만큼 과금 호출이 생기므로 ON 전환은 운영 결정(D-127).
        field = NoiseGateConfig.model_fields["annotation_llm_classification_enabled"]
        assert field.default is False

    def test_classifier_not_built_when_flag_off(self):
        w = AlarmWorker(_cfg(annotation_llm_classification_enabled=False))
        assert w._build_annotation_classifier() is None

    def test_classifier_not_built_when_gate_off(self):
        w = AlarmWorker(_cfg(enable_noise_gate=False))
        assert w._build_annotation_classifier() is None

    def test_classifier_built_when_flag_on(self):
        w = AlarmWorker(_cfg())
        assert isinstance(w._build_annotation_classifier(), AnnotationClassifier)

    async def test_signal_uses_regex_when_classifier_absent(self):
        # 분류기 미주입(기본) → 기존 결정적 추출과 동일 결과(비트동일·회귀 0).
        w = AlarmWorker(_cfg(annotation_llm_classification_enabled=False))
        text = "계획정지 작업으로 서비스 이상없음"
        assert await w._annotation_signal(_event(text)) == extract_annotation_signal(text)

    async def test_signal_falls_back_to_description(self):
        w = AlarmWorker(_cfg(annotation_llm_classification_enabled=False))
        s = await w._annotation_signal(_event("", "정기 점검 예정"))
        assert s.planned_work is True

    async def test_signal_uses_classifier_when_present(self):
        w = AlarmWorker(_cfg())
        w._annotation_classifier = AnnotationClassifier(_cfg(), llm=_LabelLLM())
        # 정규식은 놓치는 표현 → 분류기 경유가 실제로 배선됐음을 증명.
        assert (await w._annotation_signal(_event("이상무"))).resolution is True


class _RecCaptureStore:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def record_recurrence(self, **kw) -> None:  # noqa: ANN003
        self.calls.append(kw)


class TestRecurrenceHarvestSignalInjection:
    def _worker(self) -> AlarmWorker:
        w = AlarmWorker(
            SimpleNamespace(
                noise_gate=SimpleNamespace(
                    recurrence_audit_every_n=1, annotation_harvest_enabled=True
                )
            )
        )
        w._decision_store = _RecCaptureStore()
        return w

    def test_injected_signal_is_used(self):
        # LLM 분류는 async라 sync 적재 경로에는 값으로 주입한다(D-132).
        w = self._worker()
        w._record_recurrence(
            "fp", _event("이상무"), {"count": 2, "first_seen": 1.0},
            signal=signal_from_labels([AnnotationLabel.RESOLUTION]),
        )
        ann = w._decision_store.calls[0]["annotation"]
        assert ann["resolution"] is True and ann["chattering"] == "repeating"

    def test_without_signal_falls_back_to_regex(self):
        # 미주입 호출은 기존과 비트동일(정규식 추출) — "이상무"는 정규식이 놓친다.
        w = self._worker()
        w._record_recurrence("fp", _event("이상무"), {"count": 2, "first_seen": 1.0})
        ann = w._decision_store.calls[0]["annotation"]
        assert ann == {"chattering": "repeating"}


# ═════════════════════════════════════════════════════════════
# F. 게이트 무회귀 — 신호 형태 동일, 판정 로직 무변경(Plan 52 D-048/D-049)
# ═════════════════════════════════════════════════════════════
def _gate_cfg(**over) -> SimpleNamespace:
    base = dict(
        suppress_max_severity=2,
        importance_value_map={"HIGH": "높음"},
        resolved_to_dashboard=False,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _gate_event(severity: int = 2) -> SimpleNamespace:
    return SimpleNamespace(
        severity=severity, is_clear=(severity == 0), db_id="db1",
        server_name="srv-1", alarm_name="CPU", resource_name="r1", hostname="h1",
    )


def _gate_ctx(**extra) -> dict:
    ctx = {
        "importance_id": "HIGH", "maintenance": False, "noti_policy": None,
        "parent_avail_status": None, "source": "polestar_db",
    }
    ctx.update(extra)
    return ctx


class TestGateDecisionNoRegression:
    async def test_same_signal_same_decision_regex_vs_llm(self):
        # 두 경로가 같은 신호를 내면 게이트 판정도 같다(판정 로직 무변경 — 신호 산출만 교체).
        text = "계획정지 작업으로 서비스 이상없음"
        clf = AnnotationClassifier(
            _cfg(),
            llm=_ScriptedLLM('{"labels": ["planned_work", "resolution"]}'),
        )
        llm_sig = await clf.classify(text)
        regex_sig = extract_annotation_signal(text)
        assert llm_sig == regex_sig

        cfg = _gate_cfg(annotation_planned_suppress=True)
        d_llm = decide_notification(
            _gate_event(2), None, None, _gate_ctx(), cfg,
            annotation=llm_sig.to_dict(),
        )
        d_regex = decide_notification(
            _gate_event(2), None, None, _gate_ctx(), cfg,
            annotation=regex_sig.to_dict(),
        )
        assert (d_llm.tier, d_llm.reason, d_llm.priority) == (
            d_regex.tier, d_regex.reason, d_regex.priority
        )
        assert d_llm.tier == TIER_DASHBOARD  # planned + resolution 코로보레이션 강등

    async def test_llm_recovered_signal_reaches_gate(self):
        # 정규식이 놓쳤던 "이상무"를 분류기가 회복하면 코로보레이션이 성립한다.
        # 정규식 경로는 planned 단독 → 강등 없음(텍스트 단독 억제 금지 규칙 유지).
        text = "계획정지 작업 진행, 이상무"
        clf = AnnotationClassifier(_cfg(), llm=_LabelLLM())
        # 목 정답표는 "이상무" → resolution. planned는 정규식 마커와 별개로 LLM 판정.
        llm_sig = signal_from_labels(
            [AnnotationLabel.PLANNED_WORK, AnnotationLabel.RESOLUTION]
        )
        assert (await clf.classify(text)).resolution is True

        cfg = _gate_cfg(annotation_planned_suppress=True)
        regex_sig = extract_annotation_signal(text)
        assert regex_sig.planned_work is True and regex_sig.resolution is False
        assert (
            decide_notification(
                _gate_event(2), None, None, _gate_ctx(), cfg,
                annotation=regex_sig.to_dict(),
            ).tier
            == TIER_PAGE
        )
        assert (
            decide_notification(
                _gate_event(2), None, None, _gate_ctx(), cfg,
                annotation=llm_sig.to_dict(),
            ).tier
            == TIER_DASHBOARD
        )

    async def test_severity3_short_circuit_unaffected_by_classifier(self):
        # 심각도3 단락은 분류 수단과 무관하게 불변(D-003·안전 규칙).
        clf = AnnotationClassifier(
            _cfg(),
            llm=_ScriptedLLM('{"labels": ["planned_work", "resolution"]}'),
        )
        sig = await clf.classify("계획작업 중, 이상무")
        cfg = _gate_cfg(annotation_planned_suppress=True)
        d = decide_notification(
            _gate_event(3), None, None, _gate_ctx(change_nearby=True), cfg,
            annotation=sig.to_dict(),
        )
        assert d.tier == TIER_PAGE and "심각도3" in d.reason

    async def test_plan52_matrix_cells_unchanged_with_classifier(self):
        """Plan 52 벤치마크 매트릭스 셀(D-048/D-049) 재측정 — 목 분류기 경유 판정 무회귀.

        `scripts/noise_gate_scenario_test.py`의 매트릭스 시나리오(sev3→PAGE, sev2×높음→PAGE,
        sev1×낮음→DASHBOARD, 유지보수→SUPPRESS)를 **분류기가 신호를 산출한 상태**로 게이트에
        통과시켜 티어가 그대로인지 고정한다. 코로보레이션(planned+해소/변경/상관)이 없으면
        주석은 강등을 일으키지 않으므로(텍스트 단독 억제 금지) 매트릭스는 불변이어야 한다.
        실 LLM 호출 없음(목 — D-127). 라이브 시나리오 실행은 서버·Redis·픽스처·과금 호출이
        필요해 이 단위 재측정으로 대체한다.
        """
        clf = AnnotationClassifier(_cfg(), llm=_LabelLLM())
        sig = await clf.classify("작업예정입니다")  # planned_work 단독(코로보레이션 없음)
        assert sig.planned_work is True and sig.resolution is False
        ann = sig.to_dict()
        cfg = _gate_cfg(annotation_planned_suppress=True)

        cells = [
            # (severity, importance_id, maintenance, 기대 티어)
            (3, "HIGH", False, TIER_PAGE),
            (2, "HIGH", False, TIER_PAGE),
            (1, "LOW", False, TIER_DASHBOARD),
            (2, "HIGH", True, TIER_SUPPRESS),
        ]
        cfg_map = _gate_cfg(
            annotation_planned_suppress=True,
            importance_value_map={"HIGH": "높음", "LOW": "낮음"},
        )
        for severity, importance, maintenance, expected in cells:
            ctx = _gate_ctx(importance_id=importance, maintenance=maintenance)
            base = decide_notification(
                _gate_event(severity), None, None, ctx, cfg_map
            )
            withann = decide_notification(
                _gate_event(severity), None, None, ctx, cfg_map, annotation=ann
            )
            assert withann.tier == base.tier == expected, (
                severity, importance, maintenance, base.tier, withann.tier
            )
            assert withann.reason == base.reason
        # 코로보레이션이 성립하면(planned+resolution) 의도된 강등만 발생 — 셀 규칙과 독립.
        demoted = decide_notification(
            _gate_event(2), None, None, _gate_ctx(), cfg,
            annotation=signal_from_labels(
                [AnnotationLabel.PLANNED_WORK, AnnotationLabel.RESOLUTION]
            ).to_dict(),
        )
        assert demoted.tier == TIER_DASHBOARD

    async def test_gate_ignores_annotation_when_suppress_flag_off(self):
        # annotation_planned_suppress off(기본) → 분류기가 신호를 내도 판정 불변(비트동일).
        clf = AnnotationClassifier(
            _cfg(),
            llm=_ScriptedLLM('{"labels": ["planned_work", "resolution"]}'),
        )
        sig = await clf.classify("계획작업, 이상무")
        cfg = _gate_cfg()
        base = decide_notification(_gate_event(2), None, None, _gate_ctx(), cfg)
        withann = decide_notification(
            _gate_event(2), None, None, _gate_ctx(), cfg, annotation=sig.to_dict()
        )
        assert (withann.tier, withann.reason, withann.priority) == (
            base.tier, base.reason, base.priority
        )
