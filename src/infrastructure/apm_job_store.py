"""APM 작업 장부 — 사용자가 맡긴 장기 조회 작업의 소유자·상태 기록 (plans/134 W0-B M-10·M-11 ·
SPEC-apm-question-coverage §3.8).

게이트웨이가 작업을 실행하고 결과를 보관한다(`apm_gateway` 작업 도구). 본체는 **누가 어느 작업을
맡겼는지**만 이 장부에 남기고, 작업 API는 이 장부로 소유자를 먼저 확인한 뒤 게이트웨이를 부른다.

- 저장소: Redis(키 TTL = 보관 기간) — Redis에 닿지 않으면 프로세스 메모리(재기동 시 사라진다 —
  기록의 `ledger = "memory"`로 드러내고 응답이 알린다).
- 키: `apm:job:<job_id>`(기록 JSON) · `apm:jobs:owner:<sub>`(그 사용자의 작업 ID 목록 JSON).
- 보관 기간: 게이트웨이가 알려 준 `expires_at`이 있으면 그 시각까지, 없으면(진행 중)
  `RETENTION_SECONDS`(게이트웨이 `APM_ARTIFACT_RETENTION_SECONDS` 기본값과 같다).
- 서버는 단일 프로세스로 기동한다는 전제다(`src/schema_cache/admin_jobs.py`와 같다) — 사용자별
  목록 갱신은 프로세스 안 잠금으로 직렬화한다.

계층: infrastructure.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

#: 기록 보관 기본값(초) — 게이트웨이 결과 보관 기본(24시간)과 같다. 조회 범위 축소 수단이 아니다.
RETENTION_SECONDS = 86400
JOB_KEY_PREFIX = "apm:job:"
OWNER_KEY_PREFIX = "apm:jobs:owner:"
LEDGER_REDIS = "redis"
LEDGER_MEMORY = "memory"
#: 게이트웨이 작업 ID 형식(uuid4 hex) — 형식 밖 값은 키로 쓰지 않는다.
_JOB_ID = re.compile(r"^[0-9a-f]{32}$")


def is_job_id(value: object) -> bool:
    """게이트웨이가 발급한 작업 ID 형식(32자 16진수)인가."""
    return isinstance(value, str) and bool(_JOB_ID.match(value))


def _epoch(value: Any) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except ValueError:
        return None


class ApmJobStore:
    """작업 장부 — Redis 우선, 닿지 않으면 프로세스 메모리."""

    def __init__(
        self,
        redis_cache: Any | None,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """장부를 만든다.

        Args:
            redis_cache: `get_json`·`set_json`·`ensure_connected`를 가진 Redis 캐시
                (`RedisSchemaCache`). None이면 메모리만 쓴다.
            clock: 벽시계(초) — 테스트가 바꾼다.
        """
        self._redis = redis_cache
        self._clock = clock
        self._memory: dict[str, tuple[float, dict[str, Any]]] = {}
        self._lock = asyncio.Lock()

    async def _redis_ready(self) -> Any | None:
        if self._redis is None:
            return None
        try:
            return self._redis if await self._redis.ensure_connected() else None
        except Exception as e:  # noqa: BLE001 — 연결 실패는 메모리로 내려가고 사유를 남긴다
            logger.warning("APM 작업 장부 Redis 연결 실패 — 메모리 장부를 쓴다: %s", e)
            return None

    def _ttl(self, record: dict[str, Any]) -> int:
        expires = _epoch(record.get("expires_at"))
        if expires is None:
            return RETENTION_SECONDS
        return max(60, int(expires - self._clock()) + 60)

    def _sweep_memory(self) -> None:
        now = self._clock()
        for job_id in [k for k, (until, _) in self._memory.items() if until <= now]:
            self._memory.pop(job_id, None)

    async def register(self, record: dict[str, Any]) -> str:
        """작업 기록을 남기고 저장소 종류(`redis`·`memory`)를 돌려준다.

        기록에는 `job_id`·`owner_sub`가 있어야 한다. 같은 작업을 다시 등록하면 덮어쓴다.
        """
        job_id = str(record.get("job_id") or "")
        if not is_job_id(job_id):
            raise ValueError("작업 ID 형식이 아니다")
        owner = str(record.get("owner_sub") or "")
        async with self._lock:
            redis = await self._redis_ready()
            if redis is not None:
                try:
                    stored = {**record, "ledger": LEDGER_REDIS}
                    ttl = self._ttl(stored)
                    await redis.set_json(f"{JOB_KEY_PREFIX}{job_id}", stored, ex=ttl)
                    key = f"{OWNER_KEY_PREFIX}{owner}"
                    ids = await redis.get_json(key)
                    ids = [i for i in ids if is_job_id(i)] if isinstance(ids, list) else []
                    # 보관 기간이 지나 기록 키가 사라진 ID는 목록에서 걷는다 — 꾸준히 쓰는 사용자의
                    # 목록이 끝없이 길어지지 않게(목록은 보관 기간 안의 작업 수로 묶인다).
                    live = [i for i in ids
                            if i != job_id and await redis.get_json(f"{JOB_KEY_PREFIX}{i}")]
                    await redis.set_json(key, [*live, job_id], ex=RETENTION_SECONDS)
                    # Redis가 복구됐으면 메모리에 남은 옛 사본을 지운다 — get·list가 메모리를 먼저
                    # 읽어 옛 상태를 돌려주지 않게.
                    self._memory.pop(job_id, None)
                    return LEDGER_REDIS
                except Exception as e:  # noqa: BLE001 — Redis 명령 실패는 메모리로 남긴다
                    logger.warning("APM 작업 장부 Redis 기록 실패 — 메모리에 남긴다: %s", e)
            self._sweep_memory()
            stored = {**record, "ledger": LEDGER_MEMORY}
            self._memory[job_id] = (self._clock() + self._ttl(stored), stored)
            return LEDGER_MEMORY

    async def get(self, job_id: str) -> dict[str, Any] | None:
        """기록(없거나 보관 기간이 지났으면 None)."""
        if not is_job_id(job_id):
            return None
        self._sweep_memory()
        held = self._memory.get(job_id)
        if held is not None:
            return dict(held[1])
        redis = await self._redis_ready()
        if redis is None:
            return None
        try:
            record = await redis.get_json(f"{JOB_KEY_PREFIX}{job_id}")
        except Exception as e:  # noqa: BLE001 — 읽기 실패는 없음으로 본다(사유는 로그)
            logger.warning("APM 작업 장부 Redis 읽기 실패: %s", e)
            return None
        return record if isinstance(record, dict) else None

    async def update(self, job_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        """기록의 칸을 갱신한다(게이트웨이 상태 캐시) — 없으면 None."""
        record = await self.get(job_id)
        if record is None:
            return None
        record.update(fields)
        record.pop("ledger", None)
        backend = await self.register(record)
        return {**record, "ledger": backend}

    async def list_for(self, owner_sub: str) -> list[dict[str, Any]]:
        """그 사용자의 작업 기록 — 최근 등록 순."""
        owner = str(owner_sub or "")
        self._sweep_memory()
        found: dict[str, dict[str, Any]] = {
            job_id: dict(record)
            for job_id, (_, record) in self._memory.items()
            if record.get("owner_sub") == owner
        }
        redis = await self._redis_ready()
        if redis is not None:
            try:
                ids = await redis.get_json(f"{OWNER_KEY_PREFIX}{owner}")
                for job_id in ids if isinstance(ids, list) else []:
                    if not is_job_id(job_id) or job_id in found:
                        continue
                    record = await redis.get_json(f"{JOB_KEY_PREFIX}{job_id}")
                    if isinstance(record, dict) and record.get("owner_sub") == owner:
                        found[job_id] = record
            except Exception as e:  # noqa: BLE001 — 목록 읽기 실패는 메모리분만 돌려준다
                logger.warning("APM 작업 장부 Redis 목록 읽기 실패: %s", e)
        return sorted(
            found.values(), key=lambda r: str(r.get("registered_at") or ""), reverse=True
        )


_STORE: ApmJobStore | None = None


def _redis_cache_of(app_config: Any) -> Any | None:
    """서버가 이미 쓰는 Redis 캐시(스키마 캐시 매니저 소유)를 빌린다 — 없으면 None."""
    try:
        from src.schema_cache.cache_manager import get_cache_manager

        return get_cache_manager(app_config).structure_store.redis_cache
    except Exception as e:  # noqa: BLE001 — 캐시 매니저를 만들 수 없는 설정이면 메모리 장부
        logger.warning("APM 작업 장부: Redis 캐시를 얻지 못해 메모리 장부를 쓴다: %s", e)
        return None


def get_apm_job_store(app_config: Any) -> ApmJobStore:
    """모듈 싱글톤 장부(처음 부를 때 서버 Redis 캐시를 붙인다)."""
    global _STORE
    if _STORE is None:
        _STORE = ApmJobStore(_redis_cache_of(app_config))
    return _STORE


def set_apm_job_store(store: ApmJobStore | None) -> None:
    """장부를 바꾼다(테스트·재설정용 — None이면 다음 호출에서 다시 만든다)."""
    global _STORE
    _STORE = store


__all__ = [
    "LEDGER_MEMORY",
    "LEDGER_REDIS",
    "RETENTION_SECONDS",
    "ApmJobStore",
    "get_apm_job_store",
    "is_job_id",
    "set_apm_job_store",
]
