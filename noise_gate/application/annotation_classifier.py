"""운영자 주석 3분류 LLM 분류기 (Plan 67 R3-(v) · D-132 — application 계층).

`condition_log`/`description`에 운영자가 손으로 덧붙인 한국어 주석에서 계획작업·해소·
운영자 인지 신호를 LLM으로 판정한다. 기존 정규식 분류(`domain/annotation_signal.py`)는
어휘 밖 표현을 놓쳤다("이상무"·"문제없음" 미매칭 — `docs/regex_llm_conversion_review.md` §5.4).

**계층(D-035 예외의 조건)**: LLM 의존은 이 모듈(application)까지만 들어온다. domain은
`AnnotationLabel` enum을 받아 `signal_from_labels`로 신호를 조립할 뿐이며 분류 수단을 알지
않는다. 정규식 분류는 삭제하지 않고 **강등 폴백**으로 남는다.

운영 제약(계획서 §3.2 R3-(v) ①~③):
    - **옵트인**: `annotation_llm_classification_enabled` 기본 OFF. 알람 유입량만큼 과금
      호출이 발생하므로 ON 전환은 운영 결정이다(D-127). OFF면 워커가 이 분류기를 만들지
      않으므로 기존 정규식 경로가 비트동일하게 동작한다(회귀 0).
    - **캐시**: 정규화 텍스트의 SHA-256 키로 판정 결과를 재사용해 동일 주석(재발신·동일
      점검 공지 다발)의 재분류를 막는다. 항목 수 상한(`annotation_llm_cache_max`) + TTL
      만료 sweep(`annotation_llm_cache_ttl_seconds`) 양쪽을 둔다(값 bound만으론 키가 무한
      증식 — Known Mistakes). 워커는 스트림을 순차 소비하므로 크로스이벤트 배치는 게이트
      지연만 늘린다 → "동일 주석 재분류 방지"는 캐시로 달성한다.
    - **강등**: 타임아웃·LLM 예외·JSON 파싱 실패는 정규식 분류로 강등하고 **사유를 로그로
      남긴다**(침묵 강등 금지). 강등 횟수는 `degradations`로 계측한다.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Any, Optional

from noise_gate.domain.annotation_signal import (
    AnnotationLabel,
    AnnotationSignal,
    extract_annotation_signal,
    signal_from_labels,
)
from noise_gate.prompts.annotation_classifier import (
    ANNOTATION_CLASSIFIER_SYSTEM_PROMPT,
    ANNOTATION_CLASSIFIER_USER_TEMPLATE,
)
from src.utils.json_extract import extract_json_from_response

logger = logging.getLogger(__name__)

# 분류 대상 텍스트 상한(문자). 운영자 주석은 한두 문장이며, 초과분은 기계 로그 덩어리일
# 가능성이 높다 — 프롬프트 비용·지연 가드로 앞부분만 사용한다.
_MAX_TEXT_CHARS = 1000


def _setting(gate, name: str, default: float) -> float:  # noqa: ANN001
    """설정값을 읽되 **0을 기본값으로 대체하지 않는다**(0 = 비활성이라는 유효한 지시).

    경량 설정(테스트 SimpleNamespace 등)에서 필드가 없거나 None이면 default를 쓴다.
    """
    value = getattr(gate, name, None)
    return default if value is None else value


def _cache_key(text: str) -> str:
    """정규화(strip) 텍스트의 SHA-256 16진 다이제스트 — 동일 주석 재분류 방지 키."""
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def _parse_labels(content: Any) -> Optional[list[AnnotationLabel]]:
    """LLM 응답에서 라벨 리스트를 추출한다(계약 위반 시 None → 호출부가 강등).

    계약은 `{"labels": ["planned_work", ...]}`. 알 수 없는 라벨 문자열은 무시하고(환각 방어),
    `labels` 키 자체가 없거나 리스트가 아니면 None(강등 사유)으로 처리한다. 빈 배열은 정상
    응답("마커 없음")이므로 빈 리스트를 반환한다 — None과 구분해야 한다.
    """
    parsed = extract_json_from_response(str(content or ""))
    if not isinstance(parsed, dict):
        return None
    raw = parsed.get("labels")
    if not isinstance(raw, list):
        return None
    labels: list[AnnotationLabel] = []
    for item in raw:
        try:
            labels.append(AnnotationLabel(str(item).strip().lower()))
        except ValueError:
            logger.debug("주석 분류 — 알 수 없는 라벨 무시: %r", item)
    return labels


class AnnotationClassifier:
    """운영자 주석을 LLM으로 3분류하고, 실패 시 정규식 분류로 강등한다.

    워커가 인스턴스 하나를 보유·재사용한다(단일 워커·순차 소비 전제라 락 없음). LLM은 첫
    분류 시점에 `create_llm`으로 지연 획득한다 — 생성 실패도 강등 경로로 흡수한다.
    """

    def __init__(
        self,
        config,  # noqa: ANN001 — AppConfig
        *,
        llm=None,  # noqa: ANN001 — BaseChatModel (테스트 주입용 목)
    ) -> None:
        """분류기를 초기화한다.

        Args:
            config: AppConfig — `noise_gate.annotation_llm_*` 설정과 LLM 생성에 사용한다.
            llm: LLM 인스턴스를 직접 주입할 때 사용한다(테스트 목). 미지정이면 첫 분류 시
                `src.llm.create_llm(config)`로 알람 파이프라인과 동일한 경로로 획득한다.
        """
        self._config = config
        self._llm = llm
        self._llm_failed = False  # LLM 생성 실패 후 재시도 폭주 방지(영구 강등)
        gate = getattr(config, "noise_gate", None)
        # 0은 유효한 설정값이다(캐시 비활성·sweep 비활성) — `or 기본값`으로 삼키지 않는다.
        self._timeout = float(_setting(gate, "annotation_llm_timeout_seconds", 3.0))
        self._cache_max = int(_setting(gate, "annotation_llm_cache_max", 500))
        self._cache_ttl = float(
            _setting(gate, "annotation_llm_cache_ttl_seconds", 3600)
        )
        # 해시 키 → (라벨 튜플, 적재 시각). 삽입 순서 = FIFO 축출 순서.
        self._cache: dict[str, tuple[tuple[AnnotationLabel, ...], float]] = {}
        self.calls = 0          # LLM 호출 시도 수(캐시 히트·빈 텍스트 제외) — 계측
        self.cache_hits = 0     # 캐시 히트 수 — 계측
        self.degradations = 0   # 정규식 강등 횟수 — 계측(로그와 함께 관측성 확보)

    async def classify(self, text: str) -> AnnotationSignal:
        """주석 텍스트를 분류해 AnnotationSignal을 반환한다(실패 시 정규식 강등).

        빈 텍스트는 LLM을 호출하지 않고 빈 신호를 반환한다(정규식 경로와 동일). 캐시 히트는
        LLM을 호출하지 않는다. 어떤 실패도 예외를 올리지 않는다 — 주석 신호는 게이트 판정
        입력이므로 분류 실패가 알람 처리를 멈춰선 안 된다(graceful).

        Args:
            text: 운영자 주석 자유 텍스트(condition_log 또는 description).

        Returns:
            분류 결과 신호. 강등 시 `extract_annotation_signal(text)`과 동일한 값.
        """
        if not text or not str(text).strip():
            return AnnotationSignal()
        source = str(text)[:_MAX_TEXT_CHARS]

        key = _cache_key(source)
        now = time.time()
        self._sweep(now)
        cached = self._cache.get(key)
        if cached is not None:
            self.cache_hits += 1
            return signal_from_labels(cached[0])

        llm = self._get_llm()
        if llm is None:
            return self._degrade(source, "LLM 미가용")

        self.calls += 1
        try:
            response = await asyncio.wait_for(
                llm.ainvoke(
                    [
                        {
                            "role": "system",
                            "content": ANNOTATION_CLASSIFIER_SYSTEM_PROMPT,
                        },
                        {
                            "role": "user",
                            "content": ANNOTATION_CLASSIFIER_USER_TEMPLATE.format(
                                text=source
                            ),
                        },
                    ]
                ),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            return self._degrade(source, f"타임아웃({self._timeout}s)")
        except Exception as e:  # noqa: BLE001 — 분류 실패는 강등으로 흡수(사유 로그)
            return self._degrade(source, f"LLM 호출 실패: {e}")

        labels = _parse_labels(getattr(response, "content", response))
        if labels is None:
            return self._degrade(source, "응답 JSON 계약 위반(labels 부재)")

        self._store(key, tuple(labels), now)
        return signal_from_labels(labels)

    def _get_llm(self):  # noqa: ANN202
        """LLM을 지연 획득한다(알람 파이프라인과 동일한 `create_llm` 경로 재사용).

        생성 실패 시 사유를 로그로 남기고 이후에는 재시도하지 않는다(호출마다 실패 비용을
        치르지 않기 위한 영구 강등 — 설정 오류는 재시도로 낫지 않는다).
        """
        if self._llm is not None or self._llm_failed:
            return self._llm
        try:
            from src.llm import create_llm

            self._llm = create_llm(self._config)
        except Exception:
            self._llm_failed = True
            logger.exception("주석 분류 LLM 생성 실패 — 정규식 분류로 영구 강등")
        return self._llm

    def _degrade(self, text: str, reason: str) -> AnnotationSignal:
        """정규식 분류로 강등하고 사유를 로그로 남긴다(침묵 강등 금지)."""
        self.degradations += 1
        logger.warning("주석 LLM 분류 강등 → 정규식 분류 사용: %s", reason)
        return extract_annotation_signal(text)

    def _store(
        self, key: str, labels: tuple[AnnotationLabel, ...], now: float
    ) -> None:
        """분류 결과를 캐시에 적재한다(상한 초과 시 가장 오래된 키부터 FIFO 축출)."""
        if self._cache_max <= 0:
            return
        self._cache[key] = (labels, now)
        while len(self._cache) > self._cache_max:
            self._cache.pop(next(iter(self._cache)))

    def _sweep(self, now: float) -> None:
        """TTL 만료 캐시 항목을 제거한다(키 무한 증식 차단 — 값 bound와 별개 가드)."""
        if self._cache_ttl <= 0:
            return
        expired = [k for k, (_labels, ts) in self._cache.items() if now - ts >= self._cache_ttl]
        for k in expired:
            del self._cache[k]
