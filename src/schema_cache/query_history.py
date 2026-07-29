"""검증된 질의-SQL 이력 저장소·검색 (Plan 67 트랙 N / N2, D-133).

문헌 조사(`docs/standardization_literature_review.md`)에서 단일 기법 최대 효과(+40.2pt,
arXiv:2606.28387)로 확인된 **질의 이력 검색**(Verified Query Repository형)의 저장·검색
계층이다. 소비처는 두 곳을 상정한다:

  ① 폴백(LLM 1방) 프롬프트의 few-shot 동적 선택 — 본 계획에서 배선(query_generator)
  ② 트랙 S 컬럼 도출 루프의 ``search_query_history`` tool — Phase S2에서 배선

핵심 원칙:

- **자동 편입 금지**: 운영 실행이 성공했다는 사실만으로는 편입하지 않는다. 쓰기는
  ``build_entry``/``QueryHistoryStore.save_entries`` 명시 호출로만 가능하며, 출처
  ``verified_runtime``은 사람 확인 표식(``confirmed_by``) 없이는 결정적으로 거부한다
  (Snowflake VQR 구조 / Known Mistakes "쓰기 지점에서 결정적 차단").
- **읽기전용 SQL만**: SELECT/WITH 이외의 문장은 저장 자체를 거부한다(D-003 계열).
- **검색은 어휘·퍼지**: 임베딩을 쓰지 않는다. 임베딩 승격은 IP-4 계측(본 모듈의
  ``search_metrics_snapshot``) 후 별도 판단한다(계획서 §3.3-N2 "측정 선행").

Redis 키 네이밍(``redis_cache.py`` 관행 답습):

  query_history:{db_id}   -> Hash (entry_id -> 항목 JSON)

항목 스키마: ``{query, sql, smq(선택), source, verified_at}``.
``entry_id``는 query+sql의 안정 해시라 동일 쌍의 재적재가 중복을 만들지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections import deque
from typing import Any, Iterable

from src.utils.flex_match import flex_match_score

logger = logging.getLogger(__name__)

#: Redis 키 접두사. 전체 키는 ``query_history:{db_id}``.
QUERY_HISTORY_KEY_PREFIX = "query_history:"

#: 편입 허용 출처.
ALLOWED_SOURCES: tuple[str, ...] = ("gold", "profile_example", "verified_runtime")

#: 사람 확인 게이트가 필요한 출처(운영 실행 이력) — confirmed_by 없이 편입 불가.
HUMAN_GATED_SOURCES: tuple[str, ...] = ("verified_runtime",)

#: 저장 거부 대상 SQL 키워드(읽기전용 강제).
_WRITE_KEYWORDS: tuple[str, ...] = (
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE", "CREATE",
    "GRANT", "REVOKE", "MERGE",
)

#: 검색 토큰 최소 길이(1글자 토큰은 과잉 매칭 — flex_match의 가드와 같은 취지).
_MIN_TOKEN_LEN = 2

#: 토큰 단위 매칭을 인정할 최소 신뢰도(조사 흡수는 flex_match의 부분어 단계가 담당).
_TOKEN_MIN_SCORE = 0.85

_TOKEN_SPLIT_RE = re.compile(r"[\s,./()\[\]{}'\"`~!@#$%^&*=+:;?<>|\\-]+")


# ──────────────────────────────────────────────
# 1. 항목 생성·검증 (쓰기 게이트)
# ──────────────────────────────────────────────

def _is_readonly_sql(sql: str) -> bool:
    """SELECT/WITH로 시작하고 쓰기 키워드가 없는 문장인지 판정한다.

    주석(``--``/``/* */``)을 제거한 뒤 판정하여 주석 위장 우회를 막는다.

    Args:
        sql: 검사할 SQL 문자열

    Returns:
        읽기전용으로 인정되면 True
    """
    if not sql:
        return False
    stripped = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    stripped = re.sub(r"--[^\n]*", " ", stripped).strip()
    if not stripped:
        return False
    upper = stripped.upper()
    if not upper.startswith(("SELECT", "WITH")):
        return False
    return not any(
        re.search(rf"\b{kw}\b", upper) for kw in _WRITE_KEYWORDS
    )


def make_entry_id(query: str, sql: str) -> str:
    """질의-SQL 쌍의 안정 식별자를 만든다(재적재 시 중복 방지).

    Args:
        query: 자연어 질의
        sql: 검증된 SQL

    Returns:
        16자리 16진 해시 문자열
    """
    payload = f"{(query or '').strip()}\n{(sql or '').strip()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def build_entry(
    query: str,
    sql: str,
    *,
    source: str,
    smq: dict | None = None,
    verified_at: str | None = None,
    confirmed_by: str | None = None,
) -> dict:
    """이력 항목을 검증하며 생성한다(유일한 정상 쓰기 경로).

    운영 실행 이력(``verified_runtime``)은 ``confirmed_by``(사람 확인 표식) 없이는
    거부한다 — "실행 성공"만으로는 편입 근거가 되지 않는다(계획서 §3.3-N2).

    Args:
        query: 자연어 질의(한국어)
        sql: 검증된 SQL(SELECT/WITH 전용)
        source: 출처 — gold | profile_example | verified_runtime
        smq: 트랙 C 중간표현(선택)
        verified_at: 검증 시각 ISO8601(미지정 시 현재 시각)
        confirmed_by: 사람 확인 주체(운영자 ID 등). 게이트 대상 출처에 필수.

    Returns:
        저장 가능한 항목 딕셔너리

    Raises:
        ValueError: 필수값 누락·미허용 출처·쓰기 SQL·사람 확인 게이트 미통과 시
    """
    q = (query or "").strip()
    s = (sql or "").strip()
    if not q:
        raise ValueError("질의(query)가 비어 있어 이력에 편입할 수 없습니다")
    if not s:
        raise ValueError("SQL이 비어 있어 이력에 편입할 수 없습니다")
    if source not in ALLOWED_SOURCES:
        raise ValueError(
            f"허용되지 않은 출처: {source!r} (허용: {', '.join(ALLOWED_SOURCES)})"
        )
    if not _is_readonly_sql(s):
        raise ValueError("읽기전용(SELECT/WITH)이 아닌 SQL은 이력에 편입할 수 없습니다")
    if source in HUMAN_GATED_SOURCES and not (confirmed_by or "").strip():
        raise ValueError(
            f"출처 {source!r}는 사람 확인(confirmed_by) 없이 편입할 수 없습니다 "
            "— 실행 성공만으로는 자동 편입 금지"
        )

    entry: dict[str, Any] = {
        "query": q,
        "sql": s,
        "source": source,
        "verified_at": verified_at or time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    if smq:
        entry["smq"] = smq
    if confirmed_by:
        entry["confirmed_by"] = confirmed_by.strip()
    return entry


def _validate_entry(entry: dict) -> dict:
    """저장 직전 항목을 재검증한다(쓰기 지점 결정적 차단).

    ``build_entry``를 우회해 조립된 dict가 들어와도 동일 규칙을 적용한다.

    Args:
        entry: 검증할 항목

    Returns:
        검증을 통과한 항목(정규화본)

    Raises:
        ValueError: 규칙 위반 시
    """
    return build_entry(
        entry.get("query", ""),
        entry.get("sql", ""),
        source=entry.get("source", ""),
        smq=entry.get("smq"),
        verified_at=entry.get("verified_at"),
        confirmed_by=entry.get("confirmed_by"),
    )


# ──────────────────────────────────────────────
# 2. 저장소 (Redis)
# ──────────────────────────────────────────────

class QueryHistoryStore:
    """``query_history:{db_id}`` Redis Hash 저장소.

    스키마 캐시(``SchemaCacheManager``)와 독립적인 모듈이며, 연결은 동일한
    ``RedisConfig``를 사용한다. 연결 실패는 삼키지 않고 예외로 전파한다
    (침묵 실패 금지 — 적재 스크립트가 사유를 그대로 노출해야 한다).
    """

    def __init__(
        self,
        redis_config: Any,
        *,
        redis_client: Any | None = None,
    ) -> None:
        """저장소를 초기화한다.

        Args:
            redis_config: RedisConfig 인스턴스
            redis_client: 이미 연결된 redis 클라이언트(테스트 주입용, 선택)
        """
        self._redis_config = redis_config
        self._redis: Any = redis_client
        self._connected = redis_client is not None

    @staticmethod
    def key(db_id: str) -> str:
        """DB별 이력 Redis 키를 반환한다."""
        return f"{QUERY_HISTORY_KEY_PREFIX}{db_id}"

    @property
    def connected(self) -> bool:
        """연결 여부를 반환한다."""
        return self._connected and self._redis is not None

    async def connect(self) -> None:
        """Redis에 연결한다(실패 시 예외 전파).

        Raises:
            Exception: redis 패키지 부재·연결 실패 등 원인 예외 그대로
        """
        if self.connected:
            return
        import redis.asyncio as aioredis

        self._redis = aioredis.Redis(
            host=self._redis_config.host,
            port=self._redis_config.port,
            db=self._redis_config.db,
            password=self._redis_config.password or None,
            ssl=self._redis_config.ssl,
            socket_timeout=self._redis_config.socket_timeout,
            decode_responses=True,
        )
        await self._redis.ping()
        self._connected = True

    async def disconnect(self) -> None:
        """Redis 연결을 종료한다."""
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:  # noqa: BLE001 — 종료 실패는 무시(재사용 없음)
                pass
            self._redis = None
            self._connected = False

    async def save_entries(
        self,
        db_id: str,
        entries: Iterable[dict],
        *,
        replace: bool = False,
    ) -> int:
        """검증된 항목을 저장한다(명시 쓰기 경로).

        Args:
            db_id: DB 식별자
            entries: 저장할 항목들(각 항목은 ``build_entry`` 규칙을 만족해야 함)
            replace: True이면 기존 키를 지우고 새로 쓴다(기본은 병합)

        Returns:
            저장된 항목 수

        Raises:
            RuntimeError: Redis 미연결
            ValueError: 항목 검증 실패(전건 거부 — 부분 저장 없음)
        """
        if not self.connected:
            raise RuntimeError(
                "Redis에 연결되지 않아 질의 이력을 저장할 수 없습니다 "
                "(connect() 선행 필요)"
            )
        validated = [_validate_entry(e) for e in entries]
        if not validated and not replace:
            return 0

        mapping = {
            make_entry_id(e["query"], e["sql"]): json.dumps(e, ensure_ascii=False)
            for e in validated
        }
        key = self.key(db_id)
        if replace:
            await self._redis.delete(key)
        if mapping:
            await self._redis.hset(key, mapping=mapping)
        logger.info(
            "질의 이력 저장: db_id=%s, 항목=%d (replace=%s)",
            db_id, len(mapping), replace,
        )
        return len(mapping)

    async def load_entries(self, db_id: str) -> list[dict]:
        """DB별 이력 항목 전체를 로드한다.

        Args:
            db_id: DB 식별자

        Returns:
            항목 리스트(질의 문자열 기준 정렬 — 검색 순위의 결정성 확보)
        """
        if not self.connected:
            return []
        try:
            raw = await self._redis.hgetall(self.key(db_id))
        except Exception as e:  # noqa: BLE001
            logger.warning("질의 이력 로드 실패 (db_id=%s): %s", db_id, e)
            return []
        entries: list[dict] = []
        for entry_id, data in (raw or {}).items():
            try:
                parsed = json.loads(data)
            except (TypeError, ValueError):
                logger.warning("질의 이력 항목 파싱 실패: id=%s", entry_id)
                continue
            if isinstance(parsed, dict) and parsed.get("query") and parsed.get("sql"):
                parsed.setdefault("id", entry_id)
                entries.append(parsed)
        entries.sort(key=lambda e: (e.get("query", ""), e.get("id", "")))
        return entries

    async def count(self, db_id: str) -> int:
        """DB별 이력 항목 수를 반환한다."""
        if not self.connected:
            return 0
        try:
            return int(await self._redis.hlen(self.key(db_id)))
        except Exception as e:  # noqa: BLE001
            logger.warning("질의 이력 개수 조회 실패 (db_id=%s): %s", db_id, e)
            return 0

    async def delete(self, db_id: str) -> bool:
        """DB별 이력을 삭제한다(운영자 명시 호출 전용).

        Args:
            db_id: DB 식별자

        Returns:
            삭제 성공 여부
        """
        if not self.connected:
            return False
        try:
            await self._redis.delete(self.key(db_id))
            logger.info("질의 이력 삭제: db_id=%s", db_id)
            return True
        except Exception as e:  # noqa: BLE001
            logger.error("질의 이력 삭제 실패 (db_id=%s): %s", db_id, e)
            return False


_store: QueryHistoryStore | None = None


async def get_query_history_store(
    app_config: Any | None = None,
) -> QueryHistoryStore:
    """이력 저장소 싱글톤을 반환한다(미연결이면 연결 시도).

    Args:
        app_config: 앱 설정(None이면 로드)

    Returns:
        연결된 QueryHistoryStore

    Raises:
        Exception: Redis 연결 실패 원인 예외 그대로
    """
    global _store
    if _store is None:
        if app_config is None:
            from src.config import load_config

            app_config = load_config()
        _store = QueryHistoryStore(app_config.redis)
    if not _store.connected:
        await _store.connect()
    return _store


def reset_query_history_store(store: QueryHistoryStore | None = None) -> None:
    """저장소 싱글톤을 교체/리셋한다(테스트용)."""
    global _store
    _store = store


# ──────────────────────────────────────────────
# 3. 어휘·퍼지 검색 (임베딩 미사용)
# ──────────────────────────────────────────────

def tokenize_query(text: str) -> list[str]:
    """질의를 검색 토큰으로 분해한다(2글자 이상만).

    Args:
        text: 자연어 질의

    Returns:
        토큰 리스트(중복 제거, 입력 순서 보존)
    """
    tokens = [t for t in _TOKEN_SPLIT_RE.split(text or "") if len(t) >= _MIN_TOKEN_LEN]
    return list(dict.fromkeys(tokens))


def query_similarity(a: str, b: str) -> float:
    """두 질의의 어휘·퍼지 유사도(0.0~1.0)를 반환한다.

    토큰별 최고 유연 매칭 점수의 정밀도·재현율 조화평균(F1형)이다. 한국어 조사
    변형("서버의"↔"서버")은 ``flex_match``의 부분어 단계가 흡수한다. 토큰이 없으면
    문장 전체 유연 매칭으로 강등한다.

    Args:
        a: 질의 1(사용자 질의)
        b: 질의 2(이력 항목 질의)

    Returns:
        0.0(무관) ~ 1.0(동일) 사이 유사도
    """
    ta = tokenize_query(a)
    tb = tokenize_query(b)
    if not ta or not tb:
        return flex_match_score(a, b)

    def _directional(src: list[str], dst: list[str]) -> float:
        total = 0.0
        for token in src:
            best = max((flex_match_score(token, other) for other in dst), default=0.0)
            if best >= _TOKEN_MIN_SCORE:
                total += best
        return total / len(src)

    precision = _directional(ta, tb)
    recall = _directional(tb, ta)
    if precision + recall <= 0.0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def rank_history_entries(
    entries: Iterable[dict],
    user_query: str,
    *,
    top_k: int,
    min_score: float,
) -> list[dict]:
    """이력 항목을 사용자 질의와의 유사도로 정렬해 상위 top_k를 반환한다(순수 함수).

    동점 항목은 질의 문자열로 2차 정렬해 결과를 결정적으로 만든다.

    Args:
        entries: 이력 항목들
        user_query: 사용자 자연어 질의
        top_k: 반환 상한
        min_score: 채택 최소 유사도(미만 제외)

    Returns:
        ``score`` 필드가 추가된 항목 리스트(유사도 내림차순)
    """
    if top_k <= 0:
        return []
    scored: list[dict] = []
    for entry in entries or []:
        query = entry.get("query") or ""
        sql = entry.get("sql") or ""
        if not query or not sql:
            continue
        score = query_similarity(user_query, query)
        if score < min_score:
            continue
        scored.append({**entry, "score": round(score, 6)})
    scored.sort(key=lambda e: (-e["score"], e.get("query", "")))
    return scored[:top_k]


async def search_query_history(
    db_id: str,
    user_query: str,
    *,
    top_k: int,
    min_score: float,
    store: QueryHistoryStore | None = None,
) -> list[dict]:
    """검증된 질의 이력에서 유사 질의 상위 top_k를 검색한다(어휘·퍼지).

    Redis 미가용·이력 없음은 빈 리스트로 반환한다(호출부는 기존 고정 few-shot 유지).
    적중률은 IP-4 계측에 기록된다(``search_metrics_snapshot``).

    Args:
        db_id: DB 식별자
        user_query: 사용자 자연어 질의
        top_k: 반환 상한
        min_score: 채택 최소 유사도
        store: 저장소(미지정 시 싱글톤)

    Returns:
        ``score`` 포함 항목 리스트(빈 리스트면 무적중)
    """
    if not db_id or not (user_query or "").strip():
        return []
    if store is None:
        try:
            store = await get_query_history_store()
        except Exception as e:  # noqa: BLE001 — 검색 실패가 SQL 생성을 막지 않는다
            logger.warning("질의 이력 저장소 연결 실패(검색 생략): %s", e)
            _record_search(db_id, candidates=0, results=[], error=str(e))
            return []

    entries = await store.load_entries(db_id)
    results = rank_history_entries(
        entries, user_query, top_k=top_k, min_score=min_score
    )
    _record_search(db_id, candidates=len(entries), results=results)
    return results


def to_query_examples(results: Iterable[dict]) -> list[dict]:
    """검색 결과를 few-shot 예시 블록 입력(question/sql) 형태로 변환한다.

    Args:
        results: ``search_query_history`` 결과

    Returns:
        ``[{"question": ..., "sql": ...}, ...]``
    """
    return [
        {"question": r.get("query", ""), "sql": (r.get("sql") or "").rstrip()}
        for r in results or []
        if r.get("query") and r.get("sql")
    ]


# ──────────────────────────────────────────────
# 4. IP-4 적중률 계측 (임베딩 승격 판단 근거)
# ──────────────────────────────────────────────
# `docs/synonym_management_analysis.md` IP-4 선행 권고 — 검색 호출 수·후보 수·상위
# 유사도·채택 여부를 누적한다. 상위 유사도 표본은 최근 N건으로 bound한다(무제한 누적
# 금지 — Known Mistakes "in-memory 누적은 bound 필수").

_TOP_SCORE_SAMPLE_LIMIT = 50

_METRICS: dict[str, Any] = {
    "searches": 0,        # 검색 호출 수
    "hits": 0,            # 1건 이상 임계 통과한 검색 수
    "misses": 0,          # 무적중 검색 수
    "errors": 0,          # 저장소 오류로 검색 불가한 수
    "candidates": 0,      # 스캔한 이력 항목 누적 수
    "adoptions": 0,       # 프롬프트에 실제 주입된 횟수
    "adopted_examples": 0,  # 주입된 예시 누적 수
    "top_score_sum": 0.0,
    "top_score_max": 0.0,
    "top_scores": deque(maxlen=_TOP_SCORE_SAMPLE_LIMIT),
    "by_db": {},          # {db_id: {"searches", "hits", "adoptions"}}
}


def _db_metrics(db_id: str) -> dict[str, int]:
    """DB별 계측 슬롯을 반환한다(없으면 생성)."""
    by_db: dict[str, dict[str, int]] = _METRICS["by_db"]
    slot = by_db.get(db_id)
    if slot is None:
        slot = {"searches": 0, "hits": 0, "adoptions": 0}
        by_db[db_id] = slot
    return slot


def _record_search(
    db_id: str,
    *,
    candidates: int,
    results: list[dict],
    error: str | None = None,
) -> None:
    """검색 1회의 적중 정보를 계측·로깅한다(IP-4)."""
    _METRICS["searches"] += 1
    _METRICS["candidates"] += candidates
    slot = _db_metrics(db_id)
    slot["searches"] += 1

    if error is not None:
        _METRICS["errors"] += 1
        return

    if results:
        top = float(results[0].get("score") or 0.0)
        _METRICS["hits"] += 1
        _METRICS["top_score_sum"] += top
        _METRICS["top_score_max"] = max(_METRICS["top_score_max"], top)
        _METRICS["top_scores"].append(top)
        slot["hits"] += 1
        logger.info(
            "[이력검색] db=%s 후보=%d 적중=%d 상위유사도=%.3f",
            db_id, candidates, len(results), top,
        )
    else:
        _METRICS["misses"] += 1
        logger.info("[이력검색] db=%s 후보=%d 적중=0(임계 미달)", db_id, candidates)


def record_adoption(db_id: str, example_count: int) -> None:
    """검색 결과가 프롬프트에 실제 주입됐음을 계측한다(IP-4 채택 여부).

    Args:
        db_id: DB 식별자
        example_count: 주입된 예시 수
    """
    if example_count <= 0:
        return
    _METRICS["adoptions"] += 1
    _METRICS["adopted_examples"] += example_count
    _db_metrics(db_id)["adoptions"] += 1
    logger.info("[이력검색] db=%s few-shot 채택: 예시 %d건", db_id, example_count)


def search_metrics_snapshot() -> dict:
    """누적 검색 계측 스냅샷을 반환한다(IP-4 판단 근거).

    Returns:
        호출·적중·채택 수와 상위 유사도 통계
    """
    searches = _METRICS["searches"]
    hits = _METRICS["hits"]
    return {
        "searches": searches,
        "hits": hits,
        "misses": _METRICS["misses"],
        "errors": _METRICS["errors"],
        "hit_rate": (hits / searches) if searches else 0.0,
        "candidates": _METRICS["candidates"],
        "adoptions": _METRICS["adoptions"],
        "adopted_examples": _METRICS["adopted_examples"],
        "top_score_avg": (_METRICS["top_score_sum"] / hits) if hits else 0.0,
        "top_score_max": _METRICS["top_score_max"],
        "top_score_samples": list(_METRICS["top_scores"]),
        "by_db": {k: dict(v) for k, v in _METRICS["by_db"].items()},
    }


def reset_search_metrics() -> None:
    """계측을 초기화한다(테스트·측정 구간 분리용)."""
    _METRICS.update({
        "searches": 0, "hits": 0, "misses": 0, "errors": 0, "candidates": 0,
        "adoptions": 0, "adopted_examples": 0,
        "top_score_sum": 0.0, "top_score_max": 0.0,
    })
    _METRICS["top_scores"].clear()
    _METRICS["by_db"] = {}


__all__ = [
    "ALLOWED_SOURCES",
    "HUMAN_GATED_SOURCES",
    "QUERY_HISTORY_KEY_PREFIX",
    "QueryHistoryStore",
    "build_entry",
    "get_query_history_store",
    "make_entry_id",
    "query_similarity",
    "rank_history_entries",
    "record_adoption",
    "reset_query_history_store",
    "reset_search_metrics",
    "search_metrics_snapshot",
    "search_query_history",
    "to_query_examples",
    "tokenize_query",
]
