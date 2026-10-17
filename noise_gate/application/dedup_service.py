"""노이즈 게이트 중복 제거 서비스 (plans/177 W3).

`AlarmWorker`에서 alarm_id dedup · 핑거프린트 재발생 dedup(재발생 감사 적재 포함) · 의미적
근접중복 후보 주석 · 제니퍼 지속 조건 카운터의 dedup 미리보기·만료 sweep·키 상한 책임을 옮긴다.
본문은 `AlarmWorker._<이름>`에서 동작 그대로 옮겼고, 워커에는 같은 이름의 위임 메서드가 남아
`_process`·테스트 seam이 된다.

상태(`_gate_dedup`·`_recent_event_texts`·`_apm_persist`·`_apm_persist_pending`·
`_apm_sent_severity`)·협력자(`_decision_store`·`_embedding_provider`)·키 상한
`_APM_PERSIST_MAX_KEYS`(테스트가 `alarm_worker`에 patch)는 워커와 `alarm_worker` 모듈이 계속
소유한다 — 서비스는 `DedupHost`를 거쳐 **접근 시점**의 값을 읽고 쓴다(생성 시점에 붙잡지 않는다).
"""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable
from typing import Any, Protocol

from noise_gate.domain.alarm import AlarmEvent
from noise_gate.domain.annotation_signal import AnnotationSignal, extract_annotation_signal

# 이동 전과 같은 로거 이름 — 운영 로그 `%(name)s`와 테스트 caplog 로거 필터를 보존한다.
logger = logging.getLogger("noise_gate.application.alarm_worker")

# (Plan 60 B-7 L-2) db_id 스코프별 최근 이벤트 텍스트 deque 상한 — 근접중복 후보 비교 대상
# 개수(maxlen). 스코프별 in-memory 성장 차단(Known Mistakes 키·값 bound). 만료 sweep과 병행.
# `alarm_worker`가 같은 이름으로 재노출한다(patch 지점 없음 · plans/177).
_RECENT_TEXTS_MAX_PER_SCOPE = 50

# (plans/144 Q-4) 지문 dedup 레코드의 마지막 통보 심각도 키 — dedup_severity_rise_bypass가 켜져
# 있을 때만 기록한다(off면 레코드 모양이 종전과 같다). `alarm_worker`가 재노출한다.
_DEDUP_SEVERITY_KEY = "notified_severity"


def _dedup_severity_rises(rec: dict[str, Any], severity: int) -> bool:
    """들어온 심각도가 레코드의 마지막 통보 심각도보다 높은가. 키 없는 구 레코드는 False."""
    notified = rec.get(_DEDUP_SEVERITY_KEY)
    return notified is not None and severity > notified


def _dedup_meta(rec: dict[str, Any]) -> dict[str, Any]:
    """dedup 레코드의 재발생 메타 스냅샷 — 심각도 키는 빼서 recurrence 메타 모양을 종전대로 둔다."""
    return {k: v for k, v in rec.items() if k != _DEDUP_SEVERITY_KEY}


class DedupHost(Protocol):
    """서비스가 접근 시점에 읽고 쓰는 워커 쪽 상태·협력자·seam (plans/177 W3)."""

    @property
    def config(self) -> Any: ...

    @property
    def gate_dedup(self) -> dict[str, dict[str, Any]]: ...

    @property
    def decision_store(self) -> Any: ...

    @property
    def embedding_provider(self) -> Any: ...

    @property
    def recent_event_texts(self) -> dict[str, deque[tuple[str, str, float]]]: ...

    @property
    def apm_persist(self) -> dict[str, tuple[int, deque[float]]]: ...

    @property
    def apm_persist_pending(self) -> dict[str, float]: ...

    @property
    def apm_sent_severity(self) -> dict[str, tuple[int, float]]: ...

    @property
    def evict_oldest(self) -> Callable[[dict[str, Any], Callable[[Any], float], str], None]: ...


class DedupService:
    """alarm_id·핑거프린트 dedup과 근접중복 주석·제니퍼 지속 조건 정리를 맡는다 (plans/177 W3)."""

    def __init__(self, host: DedupHost) -> None:
        """워커 쪽 접근점 `host`를 받는다 — 값은 메서드가 부를 때마다 host에서 읽는다."""
        self._host = host

    def is_duplicate(self, event: AlarmEvent, dedup: dict[str, float]) -> bool:
        """중복 알람 여부를 확인하고 dedup 딕셔너리를 갱신한다.

        동일 alarm_id가 dedup_ttl_seconds 내에 이미 처리된 경우 True를 반환한다.
        만료된 항목은 함께 정리한다.

        Args:
            event: 확인할 알람 이벤트
            dedup: alarm_id → 마지막 처리 시각 딕셔너리 (in-memory)

        Returns:
            True이면 중복 (처리 건너뜀), False이면 신규 처리
        """
        now = time.time()
        ttl = self._host.config.alarm.dedup_ttl_seconds
        last = dedup.get(event.alarm_id)
        if last is not None and now - last < ttl:
            return True
        dedup[event.alarm_id] = now
        # 만료 항목 정리 (메모리 누수 방지)
        expired = [k for k, v in dedup.items() if now - v >= ttl]
        for k in expired:
            del dedup[k]
        return False

    def is_duplicate_fingerprint(
        self, fingerprint: str, now: float, severity: int, *, alarm_id: str = ""
    ) -> tuple[bool, dict[str, Any] | None]:
        """게이트 활성 시 핑거프린트 기반 재발생 dedup + count 집계 (Plan 52 §6.1 · Plan 60 E1).

        동일 핑거프린트(db_id+server+alarm_name+resource)가 재통보 간격(TTL) 내에
        이미 처리되었으면 (True, 재발생 메타)를 반환한다(재통보 억제). 만료 항목은
        함께 정리한다. alarm_id dedup(is_duplicate)과 완전히 별개 경로다(_gate_dedup).

        **억제 판정 로직(TTL·심각도 분기)은 현행과 비트 동일**하다 — 집계(count/first_seen/
        last_seen)만 추가했다. TTL 비교 기준은 반드시 `last_notified`(마지막 *통보* 시각,
        중복 판정 시 갱신하지 않음 → 고정창 유지)다. `last_seen`(중복마다 갱신)으로 비교하면
        슬라이딩 창으로 변질되어 지속 재발 알람이 영원히 재통보되지 않는 회귀가 발생한다(§3.3).

        TTL은 심각도별로 분리한다(§6.1):
            - severity==3 → sev3_repeat_interval_seconds (미해결 sev3 재통보 단축용)
            - 그 외        → repeat_interval_seconds (기본 4h)
            기본값은 둘 다 14400(동일)이라 무변경(회귀 0). 운영 시 sev3만 단축 가능.
            (최초 발생 sev3는 항상 PAGE — 이 dedup은 *이미 PAGE한 같은 알람의 반복 빈도*
            조절이지 발송 판단 억제가 아니다, §4.8/§6.1.)

        심각도 상승 우회(plans/144 Q-4 · `dedup_severity_rise_bypass`): 켜져 있으면 레코드에 마지막
        통보 심각도를 기록하고, TTL 안이라도 들어온 심각도가 그보다 **높으면** 비중복 경로(레코드
        리셋 · 새 심각도 기록)로 보낸다 — 같은 지문 sev2 통보 뒤의 sev3가 버려져 D-048 「심각도3
        절대 PAGE」가 깨지지 않게 한다. 같거나 낮은 심각도는 종전대로 버리므로 창당 추가 통과는
        최대 2회(1→2→3)다. 심각도 키가 없는 레코드(플래그 off 때 만든 것)는 우회하지 않는다.
        플래그 off(경량 설정 포함)면 레코드에 키를 두지 않아 종전과 비트 동일하다.

        Args:
            fingerprint: compute_fingerprint(event) 결과
            now: 현재 시각(time.time())
            severity: 알람 심각도 (TTL 분기 · 심각도 상승 우회용)
            alarm_id: 심각도 상승 우회 로그용 알람 id

        Returns:
            (is_dup, meta) 튜플.
            - is_dup=True(재발생 중복): meta=현재 창의 집계 스냅샷(count 포함).
            - is_dup=False(신규/재통보): meta=리셋 직전 창 메타(직전 창 count>1일 때만,
              그 외 None). 대표 알람 "직전 재발 후 재통보" 표기용.
        """
        repeat_ttl = self._host.config.noise_gate.repeat_interval_seconds
        if severity == 3:
            # getattr 가드 — 경량 설정(테스트 SimpleNamespace 등)은 공통 간격으로 폴백(무변경).
            ttl = getattr(
                self._host.config.noise_gate,
                "sev3_repeat_interval_seconds",
                repeat_ttl,
            )
        else:
            ttl = repeat_ttl
        bypass = bool(self._host.config.noise_gate.dedup_severity_rise_bypass)
        rec = self._host.gate_dedup.get(fingerprint)
        # 억제 판정 — 반드시 last_notified(통보 시각) 기준(고정창). 갱신하지 않는다.
        if rec is not None and now - rec["last_notified"] < ttl:
            if bypass and _dedup_severity_rises(rec, severity):
                logger.info(
                    "심각도 상승 — 지문 dedup 우회: fingerprint=%s %d→%d alarm_id=%s",
                    fingerprint,
                    rec[_DEDUP_SEVERITY_KEY],
                    severity,
                    alarm_id,
                )
            else:
                # 억제(중복) — 통보 시각은 고정, 집계(count)·정리(last_seen)만 갱신.
                rec["count"] += 1
                rec["last_seen"] = now
                return True, _dedup_meta(rec)
        # 신규(비중복 · TTL 만료 후 재통보 · 심각도 상승 우회) — 리셋 직전 창 메타를 prev로 캡처.
        # prev는 직전 창 count>1(억제 이력 있음)일 때만 의미 있다(대표 알람 표기용).
        prev: dict[str, Any] | None = None
        if rec is not None and rec["count"] > 1:
            prev = _dedup_meta(rec)
        new_rec: dict[str, Any] = {
            "first_seen": now,
            "last_notified": now,
            "last_seen": now,
            "count": 1,
        }
        if bypass:
            new_rec[_DEDUP_SEVERITY_KEY] = severity
        self._host.gate_dedup[fingerprint] = new_rec
        # 만료 sweep은 last_seen 기준(연속 재발 레코드의 count 보존) — 현행(통보시각 기준)보다
        # 레코드 수명이 길어지는 메모리 의미 변화(§3.3). 판정(last_notified)과 정리(last_seen) 상이.
        expired = [
            k for k, v in self._host.gate_dedup.items() if now - v["last_seen"] >= ttl
        ]
        for k in expired:
            del self._host.gate_dedup[k]
        return False, prev

    def record_recurrence(
        self,
        fingerprint: str,
        event: AlarmEvent,
        rec_meta: dict[str, Any] | None,
        *,
        signal: AnnotationSignal | None = None,
    ) -> None:
        """재발생 억제를 decision_store에 감사 적재한다 (Plan 60 E1 · E7-a · graceful).

        억제된 재발생은 그래프에 진입하지 않아 gate 감사 사각지대였다(§3.1). 워커가 직접
        type="recurrence" 레코드를 적재해 "억제≠삭제"를 강화한다. decision_store 미주입
        (게이트 off/생성 실패)이거나 메타가 없으면 스킵한다(회귀 0). recurrence_audit_every_n
        샘플링으로 적재 빈도를 조절한다(기본 1=매번, count % N == 0일 때만).

        (Plan 60 E7-a §17.3) annotation_harvest_enabled면 ACK 이전에 event 텍스트에서
        계획작업/해소/운영자접수 주석을 결정적으로 추출(extract_annotation_signal)해
        annotation 필드로 적재한다 — 재발신이 실어 온 텍스트 신호를 재통보 없이 원 인시던트에
        보존한다. (§17.6 E7-d) 재발 억제는 ISA-18.2 'repeating' chattering으로 감사 라벨링한다
        (판정 무변경·관측성). 두 확장 모두 annotation_harvest_enabled=False면 미적용(비트동일).

        (Plan 67 R3-(v) · D-132) 호출부가 `signal=`로 이미 산출한 신호를 넘기면 그 값을 쓴다
        (LLM 분류는 async라 sync인 이 메서드에서 수행할 수 없다). 미지정이면 기존대로 정규식
        추출을 수행한다 — 분류기 미주입 경로·직접 호출과 비트동일.
        """
        if self._host.decision_store is None or rec_meta is None:
            return
        every_n = getattr(self._host.config.noise_gate, "recurrence_audit_every_n", 1)
        if every_n < 1:
            every_n = 1
        count = rec_meta.get("count", 0)
        if count % every_n != 0:
            return
        # (E7-a·E7-d) 주석 하베스팅 + chattering 라벨. annotation_harvest_enabled=False(기본)면
        # 아래 kwargs에 annotation을 넣지 않아 record_recurrence 호출이 현행과 **비트동일**(회귀 0).
        kwargs: dict[str, Any] = {
            "fingerprint": fingerprint,
            "count": count,
            "first_seen_ts": rec_meta.get("first_seen"),
            "alarm_id": event.alarm_id,
        }
        if getattr(self._host.config.noise_gate, "annotation_harvest_enabled", False):
            sig = signal
            if sig is None:
                sig = extract_annotation_signal(
                    getattr(event, "condition_log", "")
                    or getattr(event, "description", "")
                    or ""
                )
            harvested = sig.to_dict() if sig.has_signal() else {}
            # ISA-18.2 repeating(복구 후 재발) 감사 라벨(§17.6) — 판정 무변경·관측성만.
            harvested["chattering"] = "repeating"
            kwargs["annotation"] = harvested
        self._host.decision_store.record_recurrence(**kwargs)

    def annotate_semantic_dedup(
        self, event: AlarmEvent, fingerprint: str, now: float
    ) -> dict[str, Any] | None:
        """의미적 근접중복 **후보 주석**을 산출한다 (Plan 60 B-7 L-2 · §15.4 D-035 경계).

        SHA1 지문이 다르지만 의미적으로 유사한(메시지 표현만 다른) 재발을 로컬 임베딩 유사도로
        감지해 "재발 count 병합 후보(검토용)" 주석을 만든다(GPTrace 패턴). **결정적 dedup 판정
        (compute_fingerprint)은 불변** — 이 주석은 판정에 영향을 주지 않는 병렬 관측이며 감사·설명
        전용이다(억제/티어 판정과 무관, D-035 불변식).

        동작(옵트인·hot-path 저비용):
            - provider 미주입(두 플래그 off/생성 실패)·inert(모델 미가용)이면 즉시 None 반환하고
              최근 텍스트 상태도 건드리지 않는다(비용 0·회귀 0).
            - 현재 이벤트 텍스트(build_event_text)와 db_id 스코프의 최근 이벤트 텍스트들 중 최고
              유사 후보를 provider.most_similar로 찾고, 유사도가
              임계(embedding_similarity_threshold) 이상이고 매칭 후보의 지문이 **다르면** 주석
              dict를 만든다.
            - 판정 후 현재 (fingerprint, text, ts)를 스코프 deque에 추가하고, 만료 항목 popleft·빈
              스코프 키 삭제로 sweep한다(상한은 deque maxlen).

        Args:
            event: 현재(비-fingerprint-dup, 비해소) 알람 이벤트.
            fingerprint: compute_fingerprint(event) 결과(현재 이벤트 지문).
            now: 현재 시각(time.time()).

        Returns:
            근접중복 후보 주석 dict `{"semantic_near_dup": {matched_fp, similarity, hint}}`
            또는 조건 미충족 시 None(임계 미만·동일 지문·후보 없음·provider 미가용).
        """
        provider = self._host.embedding_provider
        # provider 미주입(off/생성 실패) 또는 inert(모델 미가용) → 즉시 skip(비용 0, 상태 무변경).
        if provider is None or not provider.is_available():
            return None

        from noise_gate.infrastructure.embedding_provider import build_event_text

        current_text = build_event_text(
            event.alarm_name, event.resource_type, event.condition_log
        )
        if not current_text:
            return None

        scope = event.db_id or ""
        # 근접중복 판정 창(TTL) — 재발 dedup과 동일한 재통보 간격을 재사용(그 창 밖 텍스트는
        # 근접중복 후보 의미가 없음). getattr 가드로 경량 설정(테스트)도 안전.
        ttl = float(getattr(self._host.config.noise_gate, "repeat_interval_seconds", 14400))

        annotation: dict[str, Any] | None = None
        dq = self._host.recent_event_texts.get(scope)
        if dq:
            # 만료·현재와 동일 텍스트가 아닌 후보만 비교 대상으로 스냅샷(원본 인덱스 정렬 유지).
            entries = [e for e in dq if now - e[2] < ttl]
            if entries:
                best = provider.most_similar(
                    current_text, [text for _fp, text, _ts in entries]
                )
                if best is not None:
                    best_idx, score = best
                    matched_fp, _matched_text, _matched_ts = entries[best_idx]
                    threshold = float(
                        getattr(
                            self._host.config.noise_gate,
                            "embedding_similarity_threshold",
                            0.87,
                        )
                    )
                    # 임계 이상 + **다른 지문**일 때만 후보 주석(동일 지문은 결정적 dedup이 처리).
                    if score >= threshold and matched_fp != fingerprint:
                        annotation = {
                            "semantic_near_dup": {
                                "matched_fp": matched_fp,
                                "similarity": round(score, 4),
                                "hint": "의미적 근접중복 — 재발 count 병합 후보(검토용)",
                            }
                        }

        # 현재 이벤트를 최근 텍스트에 추가(자기 자신과 매칭 방지를 위해 판정 후 추가).
        if dq is None:
            dq = deque(maxlen=_RECENT_TEXTS_MAX_PER_SCOPE)
            self._host.recent_event_texts[scope] = dq
        dq.append((fingerprint, current_text, now))

        # 만료 sweep — 전 스코프의 좌측(오래된) 만료 항목 popleft + 빈 스코프 키 삭제(형제 상태
        # _flap_last_seen·_gate_dedup 정리 루프와 일관, Known Mistakes 키 만료 sweep 의무).
        for other in self._host.recent_event_texts.values():
            while other and now - other[0][2] >= ttl:
                other.popleft()
        empty = [k for k, d in self._host.recent_event_texts.items() if not d]
        for k in empty:
            del self._host.recent_event_texts[k]

        return annotation

    def apm_dedup_active(self, fingerprint: str, now: float) -> bool:
        """심각도 2 이벤트가 지문 dedup에 버려질지 미리 본다(상태 변경 없음).

        `is_duplicate_fingerprint`의 비-sev3 판정(last_notified 고정창 · repeat_interval ·
        심각도 상승 우회)과 같다.
        """
        rec = self._host.gate_dedup.get(fingerprint)
        ttl = self._host.config.noise_gate.repeat_interval_seconds
        if rec is None or now - rec["last_notified"] >= ttl:
            return False
        bypass = bool(self._host.config.noise_gate.dedup_severity_rise_bypass)
        return not (bypass and _dedup_severity_rises(rec, 2))

    def sweep_apm_persistence(self, now: float) -> None:
        """지속 조건 카운터·보류 지문·보낸 심각도의 만료 키를 정리한다 (Known Mistakes — 데몬 dict).

        카운터는 마지막 발생이 자기 창 밖이면 키째 지우고, 보류 지문·보낸 심각도는
        repeat_interval_seconds(그 뒤엔 dedup이 어차피 풀린다) 밖이면 지운다.
        """
        expired = [
            k
            for k, (window, win) in self._host.apm_persist.items()
            if not win or now - win[-1] > window
        ]
        for k in expired:
            del self._host.apm_persist[k]
        ttl = getattr(self._host.config.noise_gate, "repeat_interval_seconds", 14400)
        stale = [k for k, ts in self._host.apm_persist_pending.items() if now - ts >= ttl]
        for k in stale:
            del self._host.apm_persist_pending[k]
        stale = [k for k, (_, ts) in self._host.apm_sent_severity.items() if now - ts >= ttl]
        for k in stale:
            del self._host.apm_sent_severity[k]

    def cap_apm_persistence(self) -> None:
        """카운터·보류 지문·보낸 심각도 키가 상한을 넘으면 가장 오래된 지문부터 버린다(경고)."""
        self._host.evict_oldest(self._host.apm_persist, lambda v: v[1][-1], "지속 조건 카운터")
        self._host.evict_oldest(self._host.apm_persist_pending, lambda v: v, "재판정 보류 지문")
        self._host.evict_oldest(self._host.apm_sent_severity, lambda v: v[1], "보낸 심각도 기록")
