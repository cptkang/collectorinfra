"""Redis 데이터 Import.

exporter.py로 내보낸 JSON 파일을 읽어 타겟 Redis에 복원한다.

사용법:
    python -m tools.redis_migration.importer \
        --host 10.0.0.2 --port 6379 \
        --input tools/migdata/redis_export_20260401_090000.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _connect(host: str, port: int, db: int, password: str, ssl: bool):
    """Redis에 연결한다."""
    import redis

    client = redis.Redis(
        host=host,
        port=port,
        db=db,
        password=password or None,
        ssl=ssl,
        socket_timeout=10,
        decode_responses=True,
    )
    client.ping()
    return client


def _import_key(
    client: Any,
    entry: dict,
    overwrite: bool,
    preserve_ttl: bool,
) -> str:
    """단일 키를 Redis에 복원한다.

    Returns:
        "ok", "skipped", "failed"
    """
    key = entry["key"]
    key_type = entry["type"]
    value = entry["value"]
    ttl_ms = entry.get("ttl_ms", -1)

    if value is None:
        return "skipped"

    # 기존 키 존재 확인
    if not overwrite and client.exists(key):
        return "skipped"

    try:
        if overwrite:
            client.delete(key)

        if key_type == "string":
            if preserve_ttl and ttl_ms > 0:
                client.set(key, value, px=ttl_ms)
            else:
                client.set(key, value)

        elif key_type == "hash":
            if value:
                client.hset(key, mapping=value)
                if preserve_ttl and ttl_ms > 0:
                    client.pexpire(key, ttl_ms)

        elif key_type == "list":
            if value:
                client.rpush(key, *value)
                if preserve_ttl and ttl_ms > 0:
                    client.pexpire(key, ttl_ms)

        elif key_type == "set":
            if value:
                client.sadd(key, *value)
                if preserve_ttl and ttl_ms > 0:
                    client.pexpire(key, ttl_ms)

        elif key_type == "zset":
            if value:
                # value: [{"member": "...", "score": 1.0}, ...]
                mapping = {item["member"]: item["score"] for item in value}
                client.zadd(key, mapping)
                if preserve_ttl and ttl_ms > 0:
                    client.pexpire(key, ttl_ms)
        else:
            logger.warning("지원하지 않는 키 타입: %s (key=%s)", key_type, key)
            return "failed"

        return "ok"

    except Exception as e:
        logger.error("키 복원 실패: %s — %s", key, e)
        return "failed"


def import_redis(
    input_file: str,
    host: str = "localhost",
    port: int = 6379,
    db: int = 0,
    password: str = "",
    ssl: bool = False,
    overwrite: bool = False,
    preserve_ttl: bool = True,
    dry_run: bool = False,
) -> dict:
    """JSON 파일에서 Redis로 데이터를 복원한다.

    Args:
        input_file: export된 JSON 파일 경로
        host: 타겟 Redis 호스트
        port: 타겟 Redis 포트
        db: DB 번호
        password: 비밀번호
        ssl: SSL 사용 여부
        overwrite: 기존 키 덮어쓰기
        preserve_ttl: TTL 보존
        dry_run: 실제 쓰기 없이 확인만

    Returns:
        결과 통계 딕셔너리
    """
    filepath = Path(input_file)
    if not filepath.exists():
        logger.error("파일을 찾을 수 없습니다: %s", input_file)
        sys.exit(1)

    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    metadata = data.get("metadata", {})
    keys_data = data.get("keys", [])

    logger.info("Export 파일 로드: %s", filepath.name)
    logger.info("  원본: %s", metadata.get("source", "unknown"))
    logger.info("  내보낸 시각: %s", metadata.get("exported_at", "unknown"))
    logger.info("  키 수: %d", len(keys_data))

    if dry_run:
        logger.info("[DRY-RUN] 실제 쓰기를 수행하지 않습니다.")
        for entry in keys_data:
            ttl_str = f"ttl={entry.get('ttl_ms', -1)}ms" if entry.get("ttl_ms", -1) > 0 else "persistent"
            logger.info("  [DRY-RUN] %s (type=%s, %s)", entry["key"], entry["type"], ttl_str)
        return {"total": len(keys_data), "ok": 0, "skipped": 0, "failed": 0, "dry_run": True}

    client = _connect(host, port, db, password, ssl)
    logger.info("타겟 Redis 연결 성공: %s:%d/%d", host, port, db)

    stats = {"total": len(keys_data), "ok": 0, "skipped": 0, "failed": 0}
    start = time.time()

    for i, entry in enumerate(keys_data, 1):
        result = _import_key(client, entry, overwrite, preserve_ttl)
        stats[result] += 1

        if i % 10 == 0:
            logger.info("  진행: %d/%d", i, len(keys_data))

    elapsed = time.time() - start
    client.close()

    logger.info("=== Import 결과 ===")
    logger.info("  총 키 수  : %d", stats["total"])
    logger.info("  복원 성공 : %d", stats["ok"])
    logger.info("  건너뜀    : %d", stats["skipped"])
    logger.info("  실패      : %d", stats["failed"])
    logger.info("  소요 시간 : %.1f초", elapsed)

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="JSON 파일에서 Redis로 데이터 복원",
    )
    parser.add_argument("--input", required=True, help="export된 JSON 파일 경로")
    parser.add_argument("--host", default="localhost", help="타겟 Redis 호스트 (기본: localhost)")
    parser.add_argument("--port", type=int, default=6379, help="타겟 Redis 포트 (기본: 6379)")
    parser.add_argument("--db", type=int, default=0, help="DB 번호 (기본: 0)")
    parser.add_argument("--password", default="", help="Redis 비밀번호")
    parser.add_argument("--ssl", action="store_true", help="SSL 사용")
    parser.add_argument("--overwrite", action="store_true", help="기존 키 덮어쓰기")
    parser.add_argument("--no-preserve-ttl", action="store_true", help="TTL 보존 안 함")
    parser.add_argument("--dry-run", action="store_true", help="실제 쓰기 없이 확인만")
    parser.add_argument("--verbose", "-v", action="store_true", help="상세 로그")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    import_redis(
        input_file=args.input,
        host=args.host,
        port=args.port,
        db=args.db,
        password=args.password,
        ssl=args.ssl,
        overwrite=args.overwrite,
        preserve_ttl=not args.no_preserve_ttl,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
