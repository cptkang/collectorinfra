"""관리자 DB 구조 백그라운드 잡 러너 (plans/104 §3.2 R5).

점검·분석·등록처럼 무거운 관리자 작업을 요청 핸들러 밖 asyncio 태스크로 돌리고,
상태를 Redis에 남긴다.

Redis 키:
  admin:db_structure:job:{job_id}  -> JSON 잡 레코드(TTL 7일)
  admin:db_structure:lock:{db_id}  -> JSON 락 {job_id, kind, by, boot_id, started_at}
                                      (SET NX EX 3600)

잡 레코드: `job_id, kind, db_id, status, params, by, boot_id, started_at, finished_at,
progress{done,total,label}, result, error`. status = running · succeeded · failed · interrupted.

프로세스가 재기동되면 진행 중이던 태스크는 사라진다. `BOOT_ID`(프로세스마다 새 값)가 다른 running
레코드는 조회 시 `interrupted`로 바꿔 저장하고, 그 잡이 쥔 락은 풀어 준다. 서버는 단일 프로세스로
기동한다(`src/main.py` `uvicorn.run` — workers 미지정)는 전제다.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from src.schema_cache.redis_cache import RedisSchemaCache
from src.schema_cache.structure_store import (
    StructureStoreUnavailable,
    validate_db_id,
)

logger = logging.getLogger(__name__)

BOOT_ID = uuid.uuid4().hex

JOB_KEY_PREFIX = "admin:db_structure:job:"
LOCK_KEY_PREFIX = "admin:db_structure:lock:"
JOB_TTL_SECONDS = 7 * 86400
LOCK_TTL_SECONDS = 3600

# 실행 중 태스크 강참조(GC로 태스크가 사라지지 않게)
_RUNNING_TASKS: set[asyncio.Task[None]] = set()


class JobConflictError(RuntimeError):
    """같은 db_id의 잡이 이미 진행 중이다."""


def _now_iso() -> str:
    """현재 시각(로컬 시간대 오프셋 포함 ISO 8601, 초 단위)."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _job_key(job_id: str) -> str:
    return f"{JOB_KEY_PREFIX}{job_id}"


def _lock_key(db_id: str) -> str:
    return f"{LOCK_KEY_PREFIX}{db_id}"


class JobContext:
    """잡 본문(`work`)에 넘기는 실행 문맥 — 진행률 보고용."""

    def __init__(self, runner: AdminJobRunner, job_id: str, db_id: str, kind: str) -> None:
        self._runner = runner
        self.job_id = job_id
        self.db_id = db_id
        self.kind = kind

    async def progress(self, done: int, total: int, label: str = "") -> None:
        """진행률을 잡 레코드에 기록한다. 기록 실패는 WARNING 로그만 남기고 잡은 계속한다."""
        try:
            await self._runner._update(
                self.job_id,
                progress={"done": done, "total": total, "label": label},
            )
        except Exception as e:
            logger.warning("잡 진행률 기록 실패 (job_id=%s): %s", self.job_id, e)


class AdminJobRunner:
    """소스(db_id)당 동시 1개로 관리자 잡을 실행·조회한다."""

    def __init__(self, redis_cache: RedisSchemaCache | None) -> None:
        """러너를 만든다.

        Args:
            redis_cache: 잡 상태·락을 둘 Redis 스키마 캐시(None이면 잡 시작 불가)
        """
        self._redis_cache = redis_cache

    @property
    def redis_cache(self) -> RedisSchemaCache | None:
        """이 러너가 쓰는 Redis 스키마 캐시."""
        return self._redis_cache

    async def _require(self) -> RedisSchemaCache:
        if self._redis_cache is None or not await self._redis_cache.ensure_connected():
            raise StructureStoreUnavailable(
                "Redis에 연결할 수 없어 관리자 잡을 실행·조회할 수 없습니다"
            )
        return self._redis_cache

    async def _update(self, job_id: str, **fields: Any) -> dict[str, Any] | None:
        redis_cache = await self._require()
        record = await redis_cache.get_json(_job_key(job_id))
        if not isinstance(record, dict):
            return None
        record.update(fields)
        await redis_cache.set_json(_job_key(job_id), record, ex=JOB_TTL_SECONDS)
        return record

    async def _acquire_lock(
        self, redis_cache: RedisSchemaCache, db_id: str, lock: dict[str, Any]
    ) -> None:
        key = _lock_key(db_id)
        if await redis_cache.set_json(key, lock, ex=LOCK_TTL_SECONDS, nx=True):
            return
        holder = await redis_cache.get_json(key)
        holder_job = holder.get("job_id") if isinstance(holder, dict) else None
        if isinstance(holder, dict) and holder.get("boot_id") != BOOT_ID:
            # 이전 프로세스가 남긴 락 — 그 잡을 interrupted로 정리하고 락을 넘겨받는다.
            if holder_job:
                await self.get(str(holder_job))
            await redis_cache.delete_keys(key)
            if await redis_cache.set_json(key, lock, ex=LOCK_TTL_SECONDS, nx=True):
                logger.info(
                    "이전 프로세스의 잡 락 회수: db_id=%s, 이전 job_id=%s", db_id, holder_job
                )
                return
            holder = await redis_cache.get_json(key)
            holder_job = holder.get("job_id") if isinstance(holder, dict) else None
        raise JobConflictError(
            f"{db_id}에 진행 중인 관리자 작업이 있습니다(job_id={holder_job})"
        )

    async def _release_lock(
        self, redis_cache: RedisSchemaCache, db_id: str, job_id: str
    ) -> None:
        key = _lock_key(db_id)
        holder = await redis_cache.get_json(key)
        if isinstance(holder, dict) and holder.get("job_id") == job_id:
            await redis_cache.delete_keys(key)

    async def start(
        self,
        *,
        kind: str,
        db_id: str,
        by: str | None,
        params: dict[str, Any],
        work: Callable[[JobContext], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        """잡을 시작하고 running 레코드를 즉시 반환한다.

        Raises:
            JobConflictError: 같은 db_id의 잡이 진행 중
            StructureStoreUnavailable: Redis 미연결
            ValueError: db_id 형식 오류
        """
        validate_db_id(db_id)
        redis_cache = await self._require()
        job_id = uuid.uuid4().hex
        started_at = _now_iso()
        await self._acquire_lock(
            redis_cache,
            db_id,
            {
                "job_id": job_id,
                "kind": kind,
                "by": by,
                "boot_id": BOOT_ID,
                "started_at": started_at,
            },
        )
        record = {
            "job_id": job_id,
            "kind": kind,
            "db_id": db_id,
            "status": "running",
            "params": params,
            "by": by,
            "boot_id": BOOT_ID,
            "started_at": started_at,
            "finished_at": None,
            "progress": {"done": 0, "total": 0, "label": ""},
            "result": None,
            "error": None,
        }
        try:
            await redis_cache.set_json(_job_key(job_id), record, ex=JOB_TTL_SECONDS)
        except Exception:
            await self._release_lock(redis_cache, db_id, job_id)
            raise

        ctx = JobContext(self, job_id, db_id, kind)
        task = asyncio.create_task(self._run(ctx, work), name=f"admin-job-{kind}-{db_id}")
        _RUNNING_TASKS.add(task)
        task.add_done_callback(_RUNNING_TASKS.discard)
        logger.info("관리자 잡 시작: job_id=%s, kind=%s, db_id=%s, by=%s", job_id, kind, db_id, by)
        return dict(record)

    async def _run(
        self, ctx: JobContext, work: Callable[[JobContext], Awaitable[dict[str, Any]]]
    ) -> None:
        status = "failed"
        result: Any = None
        error: str | None = None
        cancelled = False
        try:
            result = await work(ctx)
            status = "succeeded"
        except asyncio.CancelledError:
            cancelled = True
            error = "작업이 취소되었습니다"
        except Exception as e:
            logger.exception(
                "관리자 잡 실패: job_id=%s, kind=%s, db_id=%s", ctx.job_id, ctx.kind, ctx.db_id
            )
            error = f"{type(e).__name__}: {e}"
        finally:
            await self._finish(ctx, status, result, error)
        if cancelled:
            raise asyncio.CancelledError()

    async def _finish(self, ctx: JobContext, status: str, result: Any, error: str | None) -> None:
        try:
            redis_cache = await self._require()
        except StructureStoreUnavailable as e:
            logger.error(
                "관리자 잡 종료 기록 실패: job_id=%s, status=%s: %s", ctx.job_id, status, e
            )
            return
        finished_at = _now_iso()
        try:
            try:
                await self._update(
                    ctx.job_id, status=status, result=result, error=error, finished_at=finished_at
                )
            except (TypeError, ValueError) as e:
                # 결과가 JSON으로 직렬화되지 않으면 실패로 기록한다(결과를 조용히 버리지 않는다).
                await self._update(
                    ctx.job_id,
                    status="failed",
                    result=None,
                    error=f"잡 결과 직렬화 실패: {e}",
                    finished_at=finished_at,
                )
            logger.info("관리자 잡 종료: job_id=%s, status=%s", ctx.job_id, status)
        except Exception as e:
            logger.error(
                "관리자 잡 종료 기록 실패: job_id=%s, status=%s: %s", ctx.job_id, status, e
            )
        finally:
            try:
                await self._release_lock(redis_cache, ctx.db_id, ctx.job_id)
            except Exception as e:
                logger.error(
                    "관리자 잡 락 해제 실패: db_id=%s, job_id=%s: %s", ctx.db_id, ctx.job_id, e
                )

    async def get(self, job_id: str) -> dict[str, Any] | None:
        """잡 레코드를 반환한다(없으면 None).

        running인데 `boot_id`가 현재 프로세스와 다르면 `interrupted`로 바꿔 저장하고 반환한다.

        Raises:
            StructureStoreUnavailable: Redis 미연결
        """
        redis_cache = await self._require()
        record = await redis_cache.get_json(_job_key(job_id))
        if not isinstance(record, dict):
            return None
        if record.get("status") == "running" and record.get("boot_id") != BOOT_ID:
            record["status"] = "interrupted"
            record["error"] = record.get("error") or "서버 재기동으로 작업이 중단되었습니다"
            record["finished_at"] = record.get("finished_at") or _now_iso()
            await redis_cache.set_json(_job_key(job_id), record, ex=JOB_TTL_SECONDS)
            db_id = record.get("db_id")
            if isinstance(db_id, str) and db_id:
                await self._release_lock(redis_cache, db_id, job_id)
            logger.info("관리자 잡 중단 표시: job_id=%s, db_id=%s", job_id, db_id)
        return record


_runner: AdminJobRunner | None = None


def get_job_runner(cache_mgr: Any) -> AdminJobRunner:
    """모듈 싱글톤 잡 러너를 반환한다(캐시 매니저의 Redis 캐시가 바뀌면 다시 만든다).

    Args:
        cache_mgr: `SchemaCacheManager` — `structure_store.redis_cache`를 쓴다
    """
    global _runner
    redis_cache = cache_mgr.structure_store.redis_cache
    if _runner is None or _runner.redis_cache is not redis_cache:
        _runner = AdminJobRunner(redis_cache)
    return _runner
