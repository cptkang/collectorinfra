"""Redis 데이터 Export.

현재 Redis의 모든 데이터를 JSON 파일로 내보낸다.
키 타입(string, hash)에 따라 적절히 직렬화하며,
내보낸 파일은 importer.py로 다른 Redis에 복원할 수 있다.

사용법:
    python -m tools.redis_migration.exporter \
        --host localhost --port 6380 \
        --output tools/migdata
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


def _export_key(client: Any, key: str) -> dict:
    """단일 키의 데이터를 딕셔너리로 내보낸다."""
    key_type = client.type(key)
    ttl = client.pttl(key)  # 밀리초 단위 TTL

    entry: dict = {
        "key": key,
        "type": key_type,
        "ttl_ms": ttl if ttl > 0 else -1,
    }

    if key_type == "string":
        entry["value"] = client.get(key)
    elif key_type == "hash":
        entry["value"] = client.hgetall(key)
    elif key_type == "list":
        entry["value"] = client.lrange(key, 0, -1)
    elif key_type == "set":
        entry["value"] = sorted(client.smembers(key))
    elif key_type == "zset":
        # [(member, score), ...] 형태로 내보내기
        entry["value"] = [
            {"member": m, "score": s}
            for m, s in client.zrange(key, 0, -1, withscores=True)
        ]
    else:
        entry["value"] = None
        logger.warning("지원하지 않는 키 타입: %s (key=%s)", key_type, key)

    return entry


def export_redis(
    host: str = "localhost",
    port: int = 6380,
    db: int = 0,
    password: str = "",
    ssl: bool = False,
    output_dir: str = "tools/migdata",
    patterns: list[str] | None = None,
) -> Path:
    """Redis 데이터를 JSON 파일로 내보낸다.

    Args:
        host: Redis 호스트
        port: Redis 포트
        db: DB 번호
        password: 비밀번호
        ssl: SSL 사용 여부
        output_dir: 출력 디렉토리
        patterns: 내보낼 키 패턴 목록 (None이면 전체)

    Returns:
        생성된 JSON 파일 경로
    """
    client = _connect(host, port, db, password, ssl)
    logger.info("Redis 연결 성공: %s:%d/%d", host, port, db)

    # 키 스캔
    if patterns is None:
        patterns = ["*"]

    all_keys: set[str] = set()
    for pattern in patterns:
        cursor = 0
        while True:
            cursor, keys = client.scan(cursor=cursor, match=pattern, count=100)
            all_keys.update(keys)
            if cursor == 0:
                break

    sorted_keys = sorted(all_keys)
    logger.info("내보낼 키 수: %d", len(sorted_keys))

    # 데이터 내보내기
    export_data: dict = {
        "metadata": {
            "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "source": f"{host}:{port}/{db}",
            "total_keys": len(sorted_keys),
            "redis_version": client.info("server").get("redis_version", "unknown"),
        },
        "keys": [],
    }

    for i, key in enumerate(sorted_keys, 1):
        try:
            entry = _export_key(client, key)
            export_data["keys"].append(entry)
            if i % 10 == 0:
                logger.info("  진행: %d/%d", i, len(sorted_keys))
        except Exception as e:
            logger.error("키 내보내기 실패: %s — %s", key, e)

    client.close()

    # 파일 저장
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    filename = f"redis_export_{timestamp}.json"
    filepath = out_path / filename

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(export_data, f, ensure_ascii=False, indent=2)

    size_kb = filepath.stat().st_size / 1024
    logger.info("내보내기 완료: %s (%.1f KB, %d keys)", filepath, size_kb, len(sorted_keys))

    # manifest 파일 — 키 목록 요약
    manifest_path = out_path / f"manifest_{timestamp}.txt"
    with open(manifest_path, "w", encoding="utf-8") as f:
        f.write(f"# Redis Export Manifest\n")
        f.write(f"# exported_at: {export_data['metadata']['exported_at']}\n")
        f.write(f"# source: {export_data['metadata']['source']}\n")
        f.write(f"# total_keys: {len(sorted_keys)}\n")
        f.write(f"# data_file: {filename}\n\n")
        for entry in export_data["keys"]:
            ttl_str = f"ttl={entry['ttl_ms']}ms" if entry["ttl_ms"] > 0 else "persistent"
            if entry["type"] == "hash":
                field_count = len(entry["value"]) if entry["value"] else 0
                f.write(f"{entry['type']:8s} {ttl_str:20s} fields={field_count:<6d} {entry['key']}\n")
            elif entry["type"] == "string":
                val_len = len(entry["value"]) if entry["value"] else 0
                f.write(f"{entry['type']:8s} {ttl_str:20s} len={val_len:<8d} {entry['key']}\n")
            else:
                f.write(f"{entry['type']:8s} {ttl_str:20s} {entry['key']}\n")

    logger.info("매니페스트 생성: %s", manifest_path)
    return filepath


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Redis 데이터를 JSON 파일로 내보내기",
    )
    parser.add_argument("--host", default="localhost", help="Redis 호스트 (기본: localhost)")
    parser.add_argument("--port", type=int, default=6380, help="Redis 포트 (기본: 6380)")
    parser.add_argument("--db", type=int, default=0, help="DB 번호 (기본: 0)")
    parser.add_argument("--password", default="", help="Redis 비밀번호")
    parser.add_argument("--ssl", action="store_true", help="SSL 사용")
    parser.add_argument("--output", default="tools/migdata", help="출력 디렉토리 (기본: tools/migdata)")
    parser.add_argument("--patterns", nargs="*", help="내보낼 키 패턴 (기본: 전체)")
    parser.add_argument("--verbose", "-v", action="store_true", help="상세 로그")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    export_redis(
        host=args.host,
        port=args.port,
        db=args.db,
        password=args.password,
        ssl=args.ssl,
        output_dir=args.output,
        patterns=args.patterns,
    )


if __name__ == "__main__":
    main()
