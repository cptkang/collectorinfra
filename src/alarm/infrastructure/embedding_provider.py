"""알람 로컬 임베딩 provider (Plan 60 B-7 · §15.3 L-2/L-4 토대 · §15.4 D-035 경계).

**관측성·주석·설명 전용**: 이 provider는 두 텍스트/후보 간 코사인 유사도(float)만 산출한다.
결정적 게이트 판정(SUPPRESS/PAGE)·억제 지문(compute_fingerprint)을 절대 바꾸지 않는다 —
임베딩은 재발 근접중복 주석(L-2)·토폴로지+텍스트 융합 주석(L-4)이 소비할 유사도 신호만
제공하고, 판단 로직은 담지 않는다(§15.4 D-035 불변식).

계층: infrastructure. domain·config를 import하지 않고(model_path·timeout은 생성자 인자로 주입),
외부 패키지(sentence-transformers·numpy)만 **lazy** 의존한다. 워커/enricher가
`_build_metric_baseline` 패턴으로 생성·주입하고, 테스트는 fake 모델을 주입한다.

폐쇄망 확정 설계(§15.4 · Known Mistakes 폐쇄망·다운로드 금지):
- sentence-transformers는 **메서드 내부 lazy import** — 미설치 환경에서도 모듈 import만으로 앱이
  죽지 않고, 최초 사용 시 경고 1회 후 inert(None) 유지한다(재시도 폭주 금지).
- 모델은 **로컬 디렉토리 경로에서만** 로드: ``model_path``가 설정됐고 ``os.path.isdir`` True일
  때만 로드 시도. 미설정·비디렉토리(=hub 이름)이면 다운로드를 **시도하지 않고** inert.
- 로드 직전 ``HF_HUB_OFFLINE=1``·``TRANSFORMERS_OFFLINE=1``을 환경에 설정한다
  (belt-and-suspenders — 로컬 디렉토리에서만 로드하는 위 가드에 더해 이중으로 다운로드 차단).
- 로드·인코딩 실패도 경고 1회 후 inert(None) — 침묵 강등 금지(사유 가시화).

옵트인: provider 활성 여부는 config 플래그(semantic_dedup_annotation_enabled 등)가 게이팅하며,
호출부는 inert(None) 시 임베딩 주석을 건너뛰어 기존 경로가 비트 동일하게 동작한다(회귀 0).

유사도 계산 패턴은 검증된 로컬 임베딩 모듈(``src/schema_cache/synonym_semantic.py`` E5-4/D-084)을
재사용한다: lazy ``SentenceTransformer(model_path, device="cpu")`` → ``encode`` → numpy L2 정규화 →
bounded LRU 캐시 → numpy 코사인. 여기에 **더 엄격한 local-dir-only 가드**를 추가한다.
"""

from __future__ import annotations

import logging
import os
import threading
import unicodedata
from collections import OrderedDict
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 텍스트→단위벡터 LRU 캐시 기본 상한. 384차원 float32 기준 약 30MB — 알람 메시지 다양성보다
# 넉넉하되 무한 성장을 차단한다(in-memory dict bound, Known Mistakes). 생성자로 조정 가능.
_DEFAULT_CACHE_MAX = 20_000


def build_event_text(
    alarm_name: str, resource_type: str, condition_log: str
) -> str:
    """알람 이벤트 필드를 임베딩 유사도 비교용 텍스트로 조립한다 (L-2/L-4 공용 단일 출처).

    L-2(워커 근접중복 주석)와 L-4(enricher 토폴로지+텍스트 융합 주석)가 **동일한 텍스트
    규약**으로 비교하도록 한 곳에서 조립한다. 빈 필드는 건너뛰고 공백으로 연결하며, 소문자·
    NFC 정규화는 provider의 `_normalize`가 담당한다(여기서는 필드 결합만).

    Args:
        alarm_name: 알람 이름(${alarmName}).
        resource_type: 자원 타입(${resourceType}).
        condition_log: 이 알람이 울린 실제 값(${conditionLog}).

    Returns:
        공백으로 연결된 비교 텍스트(모든 필드가 비면 빈 문자열).
    """
    return " ".join(
        str(p) for p in (alarm_name, resource_type, condition_log) if p
    ).strip()


class AlarmEmbeddingProvider:
    """로컬 sentence-transformers 모델 기반 알람 텍스트 유사도 provider(주석 전용).

    DI로 생성·주입한다(워커/enricher가 configurable로 주입, 테스트는 fake 모델 주입).
    모델 미가용(미설치·경로부재·로드/인코딩 실패)이면 **inert** — 모든 조회 메서드가 None을
    반환하고 앱을 크래시시키지 않는다. 상태(모델·실패플래그·캐시)는 인스턴스 격리되며,
    lazy 로드는 thread lock으로 보호한다.
    """

    def __init__(
        self,
        model_path: str,
        *,
        timeout_seconds: float = 2.0,
        cache_max: int = _DEFAULT_CACHE_MAX,
    ) -> None:
        """provider를 생성한다(모델은 최초 사용 시 lazy 로드).

        Args:
            model_path: 폐쇄망 오프라인 반입 모델의 **로컬 디렉토리** 경로. 미설정("")·
                비디렉토리(hub 이름 등)이면 로드하지 않고 inert(다운로드 금지).
            timeout_seconds: 임베딩 hot-path 예산(초). 호출부(L-2/L-4)가 조회를 예산 내로
                제한하는 데 참조하는 계약값 — provider는 이 값을 보관한다.
            cache_max: 텍스트→단위벡터 LRU 캐시 상한(메모리 가드).
        """
        self._model_path = model_path or ""
        self._timeout_seconds = timeout_seconds
        self._cache_max = cache_max
        self._lock = threading.Lock()
        self._embedder: Any = None          # SentenceTransformer(또는 주입 fake) — 로드 성공 시
        self._embedder_failed = False       # 로드 실패 확정 플래그(경고 1회 후 재시도 방지)
        # 정규화 텍스트 -> 단위 벡터(np.ndarray). LRU: 접근 시 move_to_end, 상한 초과 시 popitem.
        self._cache: "OrderedDict[str, Any]" = OrderedDict()

    def _reset_state_for_tests(self) -> None:
        """인스턴스 상태를 초기화한다(테스트 격리 전용)."""
        with self._lock:
            self._embedder = None
            self._embedder_failed = False
            self._cache.clear()

    def is_available(self) -> bool:
        """모델이 로드 가능(가용)하면 True, inert이면 False."""
        return self._load_embedder() is not None

    @staticmethod
    def _normalize(s: str) -> str:
        """NFC 정규화 + 공백 축약 + 소문자화(캐시 키 일관성 — synonym_semantic와 동일 규약)."""
        if not s:
            return ""
        s = unicodedata.normalize("NFC", str(s))
        return " ".join(s.split()).lower()

    def _load_embedder(self) -> Optional[Any]:
        """임베딩 모델을 lazy 로드한다(미가용 사유는 warning으로 1회 가시화, 이후 None 지속).

        local-dir-only 가드: model_path가 설정됐고 os.path.isdir True일 때만 로드를 시도하며,
        비디렉토리(hub 이름 등)이면 다운로드를 시도하지 않고 inert. 로드 직전 offline 환경변수를
        설정해 이중으로 런타임 다운로드를 차단한다(belt-and-suspenders).
        """
        if self._embedder is not None:
            return self._embedder
        if self._embedder_failed:
            return None
        with self._lock:
            if self._embedder is not None or self._embedder_failed:
                return self._embedder

            try:
                import numpy  # noqa: F401 — 코사인·정규화 필수 의존성 선확인
            except ImportError as e:
                logger.warning(
                    "B-7 알람 임베딩 의존성 미반입(%s) — pip install .[semantic] 후 사용 가능. "
                    "임베딩 주석을 건너뜁니다(판정 무변경).", e,
                )
                self._embedder_failed = True
                return None

            # local-dir-only 가드: 로컬 디렉토리가 아니면(미설정/hub 이름) 다운로드 시도 없이 inert.
            if not self._model_path or not os.path.isdir(self._model_path):
                logger.warning(
                    "B-7 알람 임베딩 모델 경로가 로컬 디렉토리가 아님(경로=%r) — 미설정이거나 hub "
                    "이름일 수 있습니다. 폐쇄망 다운로드를 시도하지 않고 임베딩 주석을 건너뜁니다.",
                    self._model_path,
                )
                self._embedder_failed = True
                return None

            # 로드 직전 offline 환경변수 설정 — local-dir-only 가드에 더한 이중 다운로드 차단.
            os.environ["HF_HUB_OFFLINE"] = "1"
            os.environ["TRANSFORMERS_OFFLINE"] = "1"

            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as e:
                logger.warning(
                    "B-7 알람 임베딩 의존성 미반입(%s) — pip install .[semantic] 후 사용 가능. "
                    "임베딩 주석을 건너뜁니다(판정 무변경).", e,
                )
                self._embedder_failed = True
                return None

            try:
                self._embedder = SentenceTransformer(self._model_path, device="cpu")
            except Exception as e:  # noqa: BLE001 — 로드 실패는 삼키지 않고 로그 후 inert
                logger.warning(
                    "B-7 알람 임베딩 모델 로드 실패(경로=%s): %s — 임베딩 주석을 건너뜁니다.",
                    self._model_path, e,
                )
                self._embedder_failed = True
                return None

            logger.info(
                "B-7 알람 임베딩 모델 로드 완료(local, CPU 상주): %s", self._model_path
            )
            return self._embedder

    def _encode(self, texts: list[str]) -> Optional[Any]:
        """텍스트 목록을 L2 정규화 임베딩 행렬(np.ndarray)로 반환한다(미가용 시 None).

        이미 임베딩한 텍스트는 LRU 캐시에서 재사용하고, 신규 텍스트만 배치 임베딩한다.
        인코딩 실패(모델 예외)는 이번 호출만 None으로 강등하되 사유를 가시화한다(침묵 강등 금지).
        """
        embedder = self._load_embedder()
        if embedder is None:
            return None
        import numpy as np

        with self._lock:
            cached: dict[str, Any] = {}
            for t in texts:
                v = self._cache.get(t)
                if v is not None:
                    self._cache.move_to_end(t)
                    cached[t] = v
        missing = list(dict.fromkeys(t for t in texts if t not in cached))

        computed: dict[str, Any] = {}
        if missing:
            try:
                raw = embedder.encode(missing, batch_size=64, show_progress_bar=False)
            except Exception as e:  # noqa: BLE001 — 인코딩 실패는 이번 호출만 None으로 강등
                logger.warning(
                    "B-7 알람 임베딩 계산 실패 — 임베딩 주석을 건너뜁니다: %s", e
                )
                return None
            vecs = np.asarray(raw, dtype=np.float32)
            if vecs.ndim == 1:
                vecs = vecs.reshape(1, -1)
            norms = np.linalg.norm(vecs, axis=1, keepdims=True)
            norms[norms == 0.0] = 1.0
            vecs = vecs / norms
            computed = dict(zip(missing, vecs))
            with self._lock:
                for t, v in computed.items():
                    self._cache[t] = v
                    self._cache.move_to_end(t)
                while len(self._cache) > self._cache_max:
                    self._cache.popitem(last=False)

        return np.vstack([cached[t] if t in cached else computed[t] for t in texts])

    def similarity(self, text_a: str, text_b: str) -> Optional[float]:
        """두 텍스트의 코사인 유사도(0~1)를 반환한다. 모델 미가용·빈 입력이면 None.

        단위 벡터 간 코사인은 [-1, 1]이나 주석 계약상 [0, 1]로 클램프한다(음의 유사도는 0).
        """
        a = self._normalize(text_a)
        b = self._normalize(text_b)
        if not a or not b:
            return None
        mat = self._encode([a, b])
        if mat is None:
            return None
        score = float(mat[0] @ mat[1])
        return max(0.0, min(1.0, score))

    def most_similar(
        self, query: str, candidates: list[str]
    ) -> Optional[tuple[int, float]]:
        """query와 candidates 중 최고 유사도인 후보의 (원본 인덱스, 점수 0~1)을 반환한다.

        L-2(근접중복 주석)·L-4(토폴로지+텍스트 융합)가 소비하는 API. 모델 미가용·빈 입력이면
        None. 반환 인덱스는 정규화 후 빈 값을 건너뛰기 전의 **원본 candidates 인덱스**다.
        """
        q = self._normalize(query)
        valid = [
            (i, norm)
            for i, norm in enumerate(self._normalize(c) for c in candidates)
            if norm
        ]
        if not q or not valid:
            return None
        mat = self._encode([q] + [c for _, c in valid])
        if mat is None:
            return None
        sims = mat[1:] @ mat[0]
        best_local = int(sims.argmax())
        best_idx = valid[best_local][0]
        best_score = max(0.0, min(1.0, float(sims[best_local])))
        return (best_idx, best_score)
