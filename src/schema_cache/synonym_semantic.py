"""임베딩 의미 검색 (Plan 61 트랙 B / E5-4 / D-084).

폐쇄망 확정 설계(plans/61 §E5-4):
- 임베딩 백엔드 2종(``cfg.synonym.semantic_backend``):
  - ``local``(기본): 경량 다국어 sentence-transformers 모델을 **오프라인 반입해
    인프로세스 CPU 상주**. 들어오는 질의어는 미리 알 수 없어 사전계산 벡터
    반입만으로는 불완전하므로, 질의를 실시간 임베딩할 모델이 폐쇄망 내부에
    상주해야 한다.
  - ``vllm``(옵션): 별도 vLLM 서버에 임베딩 모델을 로딩해 OpenAI 호환
    ``/v1/embeddings``로 호출(폐쇄망 내부 서버 전제). 다중 인스턴스가 모델을
    공유하거나 로컬 CPU 상주 비용을 피하고 싶을 때 선택한다.
- 벡터 인덱스는 **인프로세스 numpy 코사인**(Redis 무변경 — D-019·D-051 불변).
  사전 단어 임베딩은 최초 사용 시 배치 계산 후 프로세스 내 LRU 캐시에 상주한다.
- **계단식 검색의 마지막 단**: 정확일치 → E5-1 퍼지 → 임베딩. 확정 임계
  (``cfg.synonym.semantic_confidence_min``) 미만은 확정 매핑이 아닌 후보 제시
  (LLM 발견·승인 루프 D-012)로 위임한다 — 임베딩 단독 매칭 금지.

가용성: 백엔드 요건(local: ``SYNONYM_SEMANTIC_MODEL_PATH`` / vllm:
``SYNONYM_SEMANTIC_VLLM_BASE_URL``·``SYNONYM_SEMANTIC_VLLM_MODEL``) 미충족이거나
의존성(``pip install .[semantic]`` — vllm 백엔드는 numpy만 필수) 미반입이면
**경고 로그를 1회 남기고** 무매칭을 반환한다(침묵적 강등 금지 — 사유 가시화).
런타임 임베딩 실패(vLLM 미서빙 등)도 경고 후 무매칭으로 강등한다(질의 크래시 금지).
플래그 OFF 시 호출부가 이 모듈을 임포트하지 않는다(기존 경로 바이트 무변경).
"""

from __future__ import annotations

import logging
import threading
import unicodedata
from collections import OrderedDict
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 단어→임베딩 LRU 캐시 상한. 384차원 float32 기준 약 30MB — 사전 규모(수천)보다
# 넉넉하되 무한 성장을 차단한다(in-memory dict bound, Known Mistakes).
_EMBED_CACHE_MAX = 20_000
# 유사어·토큰 최소 길이(호출부 계단과 동일 가드 — 1글자 과잉 매칭 차단)
_MIN_TERM_LEN = 2
# vLLM 임베딩 호출 타임아웃(초) — 미서빙 시 질의 경로 장시간 블로킹 방지
_VLLM_TIMEOUT_S = 15

_lock = threading.Lock()
_embedder: Any = None        # SentenceTransformer 인스턴스(로드 성공 시)
_embedder_failed = False     # 로드 실패 확정 플래그(경고 1회 후 재시도 방지)
_embed_cache: "OrderedDict[str, Any]" = OrderedDict()  # 정규화 텍스트 -> 단위 벡터


def _reset_state_for_tests() -> None:
    """모듈 전역 상태를 초기화한다(테스트 격리 전용)."""
    global _embedder, _embedder_failed
    with _lock:
        _embedder = None
        _embedder_failed = False
        _embed_cache.clear()


def _normalize(s: str) -> str:
    """NFC 정규화 + 공백 축약 + 소문자화(flex_match와 동일 규약 — 캐시 키 일관성)."""
    if not s:
        return ""
    s = unicodedata.normalize("NFC", str(s))
    return " ".join(s.split()).lower()


class _VLLMEmbedder:
    """vLLM OpenAI 호환 ``/v1/embeddings`` 클라이언트(sentence-transformers
    ``encode`` 인터페이스 호환).

    별도 vLLM 서버에 임베딩 모델을 로딩해 서빙하는 옵션 백엔드. ``transport``는
    테스트 주입용(httpx.MockTransport).
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        verify_ssl: bool = True,
        transport: Any = None,
    ) -> None:
        import httpx

        self._model = model
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            verify=verify_ssl,
            timeout=_VLLM_TIMEOUT_S,
            transport=transport,
        )

    def encode(
        self,
        texts: list[str],
        batch_size: int = 64,
        show_progress_bar: bool = False,
    ) -> list[list[float]]:
        vectors: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            chunk = texts[i : i + batch_size]
            resp = self._client.post(
                "/embeddings", json={"model": self._model, "input": chunk}
            )
            resp.raise_for_status()
            data = sorted(resp.json()["data"], key=lambda d: d["index"])
            vectors.extend(d["embedding"] for d in data)
        return vectors


def _load_embedder() -> Optional[Any]:
    """임베딩 백엔드를 lazy 로드한다(미가용 사유는 warning으로 1회 가시화).

    ``semantic_backend=local``(기본)은 sentence-transformers 모델을 인프로세스
    CPU로 상주시키고, ``vllm``은 별도 vLLM 서버의 /v1/embeddings 클라이언트를 만든다.
    """
    global _embedder, _embedder_failed
    if _embedder is not None:
        return _embedder
    if _embedder_failed:
        return None
    with _lock:
        if _embedder is not None or _embedder_failed:
            return _embedder

        from src.config import load_config

        syn_cfg = load_config().synonym
        try:
            import numpy  # noqa: F401 — 인덱스 연산 필수 의존성 선확인
        except ImportError as e:
            logger.warning(
                "E5-4 의미 검색 의존성 미반입(%s) — pip install .[semantic] 후 사용 가능. "
                "임베딩 단계를 무매칭으로 건너뜁니다.", e,
            )
            _embedder_failed = True
            return None

        if syn_cfg.semantic_backend == "vllm":
            if not syn_cfg.semantic_vllm_base_url or not syn_cfg.semantic_vllm_model:
                logger.warning(
                    "E5-4 의미 검색 vllm 백엔드 활성이나 SYNONYM_SEMANTIC_VLLM_BASE_URL/"
                    "SYNONYM_SEMANTIC_VLLM_MODEL 미설정 — 임베딩 단계를 무매칭으로 건너뜁니다."
                )
                _embedder_failed = True
                return None
            _embedder = _VLLMEmbedder(
                syn_cfg.semantic_vllm_base_url,
                syn_cfg.semantic_vllm_model,
                verify_ssl=syn_cfg.semantic_vllm_verify_ssl,
            )
            logger.info(
                "E5-4 임베딩 백엔드 준비 완료(vLLM): %s, model=%s",
                syn_cfg.semantic_vllm_base_url, syn_cfg.semantic_vllm_model,
            )
            return _embedder

        # local 백엔드(기본) — sentence-transformers 모델 인프로세스 CPU 상주
        model_path = syn_cfg.semantic_model_path
        if not model_path:
            logger.warning(
                "E5-4 의미 검색 활성(SYNONYM_SEMANTIC_MATCH=true)이나 "
                "SYNONYM_SEMANTIC_MODEL_PATH 미설정 — 임베딩 단계를 무매칭으로 건너뜁니다."
            )
            _embedder_failed = True
            return None
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            logger.warning(
                "E5-4 의미 검색 의존성 미반입(%s) — pip install .[semantic] 후 사용 가능. "
                "임베딩 단계를 무매칭으로 건너뜁니다.", e,
            )
            _embedder_failed = True
            return None
        try:
            _embedder = SentenceTransformer(model_path, device="cpu")
        except Exception as e:
            logger.warning(
                "E5-4 임베딩 모델 로드 실패(경로=%s): %s — 임베딩 단계를 무매칭으로 건너뜁니다.",
                model_path, e,
            )
            _embedder_failed = True
            return None
        logger.info("E5-4 임베딩 모델 로드 완료(local, CPU 상주): %s", model_path)
        return _embedder


def _encode(texts: list[str]) -> Optional[Any]:
    """텍스트 목록을 L2 정규화 임베딩 행렬(np.ndarray)로 반환한다(미가용 시 None).

    이미 임베딩한 텍스트는 LRU 캐시에서 재사용하고, 신규 텍스트만 배치 임베딩한다
    (사전은 최초 1회 배치, 이후 질의어 1회 임베딩 — 계획 §E5-4 성능 설계).
    """
    embedder = _load_embedder()
    if embedder is None:
        return None
    import numpy as np

    with _lock:
        cached = {t: _embed_cache[t] for t in texts if t in _embed_cache}
        for t in cached:
            _embed_cache.move_to_end(t)
    missing = list(dict.fromkeys(t for t in texts if t not in cached))

    computed: dict[str, Any] = {}
    if missing:
        try:
            raw = embedder.encode(missing, batch_size=64, show_progress_bar=False)
        except Exception as e:
            # 런타임 임베딩 실패(vLLM 미서빙·타임아웃 등) — 질의 경로를 크래시시키지
            # 않고 이번 호출만 무매칭으로 강등하되 사유를 가시화한다(침묵 강등 금지).
            logger.warning(
                "E5-4 임베딩 계산 실패 — 임베딩 단계를 무매칭으로 건너뜁니다: %s", e
            )
            return None
        vecs = np.asarray(raw, dtype=np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0.0] = 1.0
        vecs = vecs / norms
        computed = dict(zip(missing, vecs))
        with _lock:
            for t, v in computed.items():
                _embed_cache[t] = v
                _embed_cache.move_to_end(t)
            while len(_embed_cache) > _EMBED_CACHE_MAX:
                _embed_cache.popitem(last=False)

    return np.vstack([cached[t] if t in cached else computed[t] for t in texts])


def semantic_match_candidates(
    term: str,
    candidate_map: dict[str, list[str]],
) -> list[tuple[str, float]]:
    """term과 후보 사전의 의미 유사도를 key별 최고점으로 집계해 내림차순 반환한다.

    폼필 필드 매핑(E5-4 Pass 4)용. 호출부가 확정 임계·가드(is_servername_to_hostname
    등)를 적용할 수 있도록 전 key의 점수를 반환한다(최고 1건만 자르지 않음).

    Args:
        term: 기준 문자열(필드명 등)
        candidate_map: {key(table.column 등): [비교 단어들]}

    Returns:
        [(key, 최고 코사인 유사도)] 내림차순. 모델 미가용·입력 부재 시 빈 리스트.
    """
    term_n = _normalize(term)
    if len(term_n) < _MIN_TERM_LEN or not candidate_map:
        return []

    pairs: list[tuple[str, str]] = []
    for key, words in candidate_map.items():
        for w in words or []:
            w_n = _normalize(w)
            if len(w_n) >= _MIN_TERM_LEN:
                pairs.append((key, w_n))
    if not pairs:
        return []

    mat = _encode([term_n] + [w for _, w in pairs])
    if mat is None:
        return []
    sims = mat[1:] @ mat[0]

    best: dict[str, float] = {}
    for (key, _w), s in zip(pairs, sims):
        score = float(s)
        if score > best.get(key, -1.0):
            best[key] = score
    return sorted(best.items(), key=lambda kv: kv[1], reverse=True)


def semantic_tables_matching_query(
    db_syns: dict[str, list[str]],
    query_tokens: list[str],
    min_score: float,
) -> set[str]:
    """질의 토큰과 임베딩 코사인이 임계 이상인 등록 유사어의 테이블 집합을 반환한다.

    텍스트 질의 allowed_tables 동적 보완(E5-4 마지막 단)용. 호출부
    (``_synonym_tables_matching_query``)와 동일한 가드를 적용한다:
    ``.`` 없는 키 제외, 길이 2 미만 유사어·토큰 제외.

    Args:
        db_syns: {"table.column": [synonym, ...]} 형태의 등록 유사어
        query_tokens: 질의에서 추출한 토큰(호출부에서 분리)
        min_score: 확정 인정 코사인 임계

    Returns:
        테이블명(소문자) 집합. 모델 미가용·입력 부재 시 빈 집합(호출부 동작 무변경).
    """
    tokens = [t for t in (_normalize(t) for t in query_tokens) if len(t) >= _MIN_TERM_LEN]
    if not tokens or not db_syns:
        return set()

    pairs: list[tuple[str, str]] = []
    for col_key, syns in db_syns.items():
        if "." not in col_key:
            continue
        for s in syns or []:
            s_n = _normalize(s)
            if len(s_n) >= _MIN_TERM_LEN:
                pairs.append((col_key, s_n))
    if not pairs:
        return set()

    mat = _encode(tokens + [w for _, w in pairs])
    if mat is None:
        return set()
    tok_mat = mat[: len(tokens)]
    word_mat = mat[len(tokens):]
    sims = word_mat @ tok_mat.T
    best_per_word = sims.max(axis=1)
    best_token_idx = sims.argmax(axis=1)

    tables: set[str] = set()
    details: list[str] = []
    for i, (col_key, word) in enumerate(pairs):
        score = float(best_per_word[i])
        if score < min_score:
            continue
        tables.add(col_key.split(".", 1)[0].lower())
        details.append(
            f"'{word}'→{col_key}(토큰='{tokens[int(best_token_idx[i])]}', "
            f"코사인={score:.2f})"
        )
    if details:
        # 계측(E5-계측): 임베딩 단은 점수를 아는 유일한 지점 — 콘솔에서 적절성 검증용
        logger.info(
            "[동의어] 임베딩 의미 매칭 %d건: %s", len(details), "; ".join(details)
        )
    return tables
