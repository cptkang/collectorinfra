"""Redis 데이터 마이그레이션 엔진.

소스 Redis의 키를 스캔하여 타겟 Redis로 복사한다.
키 타입(string, hash, list, set, zset, stream)에 따라 적절한 명령을 사용하며,
TTL이 설정된 키는 TTL도 함께 복사한다.
"""

from __future__ import annotations

import fnmatch
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import redis

from tools.redis_migration.config import MigrationConfig, RedisEndpoint

logger = logging.getLogger(__name__)


@dataclass
class MigrationStats:
    """마이그레이션 통계."""

    total_scanned: int = 0
    migrated: int = 0
    skipped_existing: int = 0
    skipped_excluded: int = 0
    failed: int = 0
    verified_ok: int = 0
    verified_fail: int = 0
    elapsed_seconds: float = 0.0
    failed_keys: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """통계 요약 문자열을 반환한다."""
        lines = [
            "=== 마이그레이션 결과 ===",
            f"  스캔 키 수       : {self.total_scanned}",
            f"  마이그레이션 성공 : {self.migrated}",
            f"  건너뜀 (기존 존재): {self.skipped_existing}",
            f"  건너뜀 (제외 패턴): {self.skipped_excluded}",
            f"  실패             : {self.failed}",
        ]
        if self.verified_ok or self.verified_fail:
            lines.append(f"  검증 성공        : {self.verified_ok}")
            lines.append(f"  검증 실패        : {self.verified_fail}")
        lines.append(f"  소요 시간        : {self.elapsed_seconds:.1f}초")
        if self.failed_keys:
            lines.append(f"  실패 키 목록     : {self.failed_keys[:20]}")
            if len(self.failed_keys) > 20:
                lines.append(f"    ... 외 {len(self.failed_keys) - 20}건")
        return "\n".join(lines)


def _connect(endpoint: RedisEndpoint) -> redis.Redis:
    """동기 Redis 클라이언트를 생성·연결한다."""
    client = redis.Redis(
        host=endpoint.host,
        port=endpoint.port,
        db=endpoint.db,
        password=endpoint.password or None,
        ssl=endpoint.ssl,
        socket_timeout=endpoint.socket_timeout,
        decode_responses=False,  # 바이너리 모드 (원본 보존)
    )
    client.ping()
    return client


def _matches_any(key: str, patterns: list[str]) -> bool:
    """키가 패턴 목록 중 하나라도 매칭되는지 확인한다."""
    for pat in patterns:
        if fnmatch.fnmatch(key, pat):
            return True
    return False


def _copy_key(
    src: redis.Redis,
    dst: redis.Redis,
    key: bytes,
    preserve_ttl: bool,
    overwrite: bool,
) -> bool:
    """단일 키를 소스에서 타겟으로 복사한다.

    DUMP/RESTORE 방식을 사용하여 모든 데이터 타입을 지원한다.

    Args:
        src: 소스 Redis 클라이언트
        dst: 타겟 Redis 클라이언트
        key: 복사할 키 (bytes)
        preserve_ttl: TTL 보존 여부
        overwrite: 기존 키 덮어쓰기 여부

    Returns:
        복사 성공 여부
    """
    try:
        # 타겟에 이미 존재하면 overwrite가 아닌 경우 스킵
        if not overwrite and dst.exists(key):
            return False

        # DUMP로 직렬화
        dumped = src.dump(key)
        if dumped is None:
            return False

        # TTL 가져오기 (밀리초)
        ttl_ms = 0
        if preserve_ttl:
            pttl = src.pttl(key)
            if pttl > 0:
                ttl_ms = pttl

        # RESTORE로 복원
        if overwrite:
            dst.delete(key)
        dst.restore(key, ttl_ms, dumped, replace=False)
        return True

    except redis.exceptions.ResponseError as e:
        # BUSYKEY — 키가 이미 존재하는 경우 (replace=False)
        if "BUSYKEY" in str(e):
            return False
        raise


def _verify_key(src: redis.Redis, dst: redis.Redis, key: bytes) -> bool:
    """소스와 타겟에서 키의 타입과 크기가 일치하는지 검증한다."""
    try:
        src_type = src.type(key)
        dst_type = dst.type(key)
        if src_type != dst_type:
            return False

        # 타입별 크기 비교
        type_name = src_type.decode() if isinstance(src_type, bytes) else src_type
        if type_name == "string":
            return src.strlen(key) == dst.strlen(key)
        elif type_name == "hash":
            return src.hlen(key) == dst.hlen(key)
        elif type_name == "list":
            return src.llen(key) == dst.llen(key)
        elif type_name == "set":
            return src.scard(key) == dst.scard(key)
        elif type_name == "zset":
            return src.zcard(key) == dst.zcard(key)
        else:
            # stream 등 기타 타입은 존재 여부만 확인
            return dst.exists(key) > 0
    except Exception:
        return False


class RedisMigrator:
    """Redis 데이터 마이그레이터."""

    def __init__(self, config: MigrationConfig) -> None:
        self._config = config
        self._src: redis.Redis | None = None
        self._dst: redis.Redis | None = None

    def connect(self) -> None:
        """소스·타겟 Redis에 연결한다."""
        logger.info("소스 Redis 연결: %s", self._config.source.display())
        self._src = _connect(self._config.source)
        logger.info("소스 Redis 연결 성공")

        logger.info("타겟 Redis 연결: %s", self._config.target.display())
        self._dst = _connect(self._config.target)
        logger.info("타겟 Redis 연결 성공")

    def close(self) -> None:
        """연결을 종료한다."""
        if self._src:
            self._src.close()
        if self._dst:
            self._dst.close()

    def scan_keys(self) -> list[bytes]:
        """소스 Redis에서 마이그레이션 대상 키를 스캔한다.

        Returns:
            대상 키 목록 (bytes)
        """
        assert self._src is not None, "connect()를 먼저 호출하세요"

        patterns = self._config.key_patterns
        batch_size = self._config.scan_batch_size
        all_keys: set[bytes] = set()

        if not patterns:
            # 패턴이 없으면 전체 스캔
            patterns = ["*"]

        for pattern in patterns:
            cursor: Any = 0
            while True:
                cursor, keys = self._src.scan(
                    cursor=cursor, match=pattern, count=batch_size
                )
                all_keys.update(keys)
                if cursor == 0:
                    break

        # 제외 패턴 적용
        if self._config.exclude_patterns:
            filtered: list[bytes] = []
            for k in all_keys:
                key_str = k.decode("utf-8", errors="replace")
                if not _matches_any(key_str, self._config.exclude_patterns):
                    filtered.append(k)
            return sorted(filtered)

        return sorted(all_keys)

    def run(self) -> MigrationStats:
        """마이그레이션을 실행한다.

        Returns:
            마이그레이션 통계
        """
        assert self._src is not None and self._dst is not None, (
            "connect()를 먼저 호출하세요"
        )

        stats = MigrationStats()
        start = time.time()

        # 1. 대상 키 스캔
        keys = self.scan_keys()
        stats.total_scanned = len(keys)
        logger.info("마이그레이션 대상 키: %d개", len(keys))

        if self._config.dry_run:
            logger.info("[DRY-RUN] 실제 쓰기를 수행하지 않습니다.")
            for k in keys:
                key_str = k.decode("utf-8", errors="replace")
                key_type = self._src.type(k)
                type_str = (
                    key_type.decode()
                    if isinstance(key_type, bytes)
                    else str(key_type)
                )
                logger.info("  [DRY-RUN] %s (type=%s)", key_str, type_str)
            stats.migrated = 0
            stats.elapsed_seconds = time.time() - start
            return stats

        # 2. 키별 복사
        for i, key in enumerate(keys, 1):
            key_str = key.decode("utf-8", errors="replace")

            # 제외 패턴 재확인
            if self._config.exclude_patterns and _matches_any(
                key_str, self._config.exclude_patterns
            ):
                stats.skipped_excluded += 1
                continue

            # 기존 키 존재 확인 (overwrite=False일 때)
            if not self._config.overwrite and self._dst.exists(key):
                stats.skipped_existing += 1
                logger.debug("건너뜀 (기존 존재): %s", key_str)
                continue

            try:
                ok = _copy_key(
                    self._src,
                    self._dst,
                    key,
                    preserve_ttl=self._config.preserve_ttl,
                    overwrite=self._config.overwrite,
                )
                if ok:
                    stats.migrated += 1
                    if i % 100 == 0:
                        logger.info("  진행: %d/%d 완료", i, len(keys))
                else:
                    stats.skipped_existing += 1
            except Exception as e:
                stats.failed += 1
                stats.failed_keys.append(key_str)
                logger.error("키 복사 실패: %s — %s", key_str, e)

        # 3. 검증
        if self._config.verify and stats.migrated > 0:
            logger.info("마이그레이션 검증 시작...")
            for key in keys:
                key_str = key.decode("utf-8", errors="replace")
                if not self._dst.exists(key):
                    # 건너뛴 키는 검증 대상에서 제외
                    continue
                if _verify_key(self._src, self._dst, key):
                    stats.verified_ok += 1
                else:
                    stats.verified_fail += 1
                    logger.warning("검증 실패: %s", key_str)

        stats.elapsed_seconds = time.time() - start
        return stats
