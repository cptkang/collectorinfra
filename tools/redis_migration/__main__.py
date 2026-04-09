"""Redis 마이그레이션 CLI 진입점.

사용법:
    # 기본 실행 (소스 → 타겟 전체 마이그레이션)
    python -m tools.redis_migration \
        --source-host 10.0.0.1 --source-port 6379 \
        --target-host 10.0.0.2 --target-port 6379

    # dry-run (실제 쓰기 없이 대상 키 확인)
    python -m tools.redis_migration \
        --source-host 10.0.0.1 \
        --target-host 10.0.0.2 \
        --dry-run

    # 특정 패턴만 마이그레이션
    python -m tools.redis_migration \
        --source-host 10.0.0.1 \
        --target-host 10.0.0.2 \
        --patterns "schema:*" "synonyms:*"

    # 기존 키 덮어쓰기
    python -m tools.redis_migration \
        --source-host 10.0.0.1 \
        --target-host 10.0.0.2 \
        --overwrite

    # YAML 설정 파일 사용
    python -m tools.redis_migration --config migration.yaml
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from tools.redis_migration.config import MigrationConfig, RedisEndpoint
from tools.redis_migration.migrator import RedisMigrator

logger = logging.getLogger("tools.redis_migration")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="redis-migration",
        description="Redis 데이터 마이그레이션 도구 (collectorinfra 스키마 캐시 전용)",
    )

    # 소스 Redis
    src = parser.add_argument_group("소스 Redis")
    src.add_argument("--source-host", default="localhost", help="소스 호스트 (기본: localhost)")
    src.add_argument("--source-port", type=int, default=6379, help="소스 포트 (기본: 6379)")
    src.add_argument("--source-db", type=int, default=0, help="소스 DB 번호 (기본: 0)")
    src.add_argument("--source-password", default="", help="소스 비밀번호")
    src.add_argument("--source-ssl", action="store_true", help="소스 SSL 사용")

    # 타겟 Redis
    tgt = parser.add_argument_group("타겟 Redis")
    tgt.add_argument("--target-host", required=True, help="타겟 호스트 (필수)")
    tgt.add_argument("--target-port", type=int, default=6379, help="타겟 포트 (기본: 6379)")
    tgt.add_argument("--target-db", type=int, default=0, help="타겟 DB 번호 (기본: 0)")
    tgt.add_argument("--target-password", default="", help="타겟 비밀번호")
    tgt.add_argument("--target-ssl", action="store_true", help="타겟 SSL 사용")

    # 마이그레이션 옵션
    opts = parser.add_argument_group("마이그레이션 옵션")
    opts.add_argument(
        "--patterns", nargs="*",
        default=["schema:*", "synonyms:*", "csv_cache:*"],
        help="마이그레이션할 키 패턴 (기본: schema:* synonyms:* csv_cache:*)",
    )
    opts.add_argument(
        "--exclude", nargs="*", default=[],
        help="제외할 키 패턴",
    )
    opts.add_argument("--dry-run", action="store_true", help="실제 쓰기 없이 대상 키만 출력")
    opts.add_argument("--overwrite", action="store_true", help="타겟에 이미 존재하는 키 덮어쓰기")
    opts.add_argument("--no-verify", action="store_true", help="마이그레이션 후 검증 건너뛰기")
    opts.add_argument("--no-preserve-ttl", action="store_true", help="TTL 보존 안 함")
    opts.add_argument("--batch-size", type=int, default=100, help="SCAN 배치 크기 (기본: 100)")

    # 설정 파일
    parser.add_argument("--config", type=str, help="YAML 설정 파일 경로")

    # 로깅
    parser.add_argument("--verbose", "-v", action="store_true", help="상세 로그 출력")

    return parser.parse_args()


def _load_yaml_config(path: str) -> MigrationConfig:
    """YAML 설정 파일에서 MigrationConfig를 로드한다."""
    import yaml

    with open(path) as f:
        data = yaml.safe_load(f)

    source_data = data.get("source", {})
    target_data = data.get("target", {})

    return MigrationConfig(
        source=RedisEndpoint(
            host=source_data.get("host", "localhost"),
            port=source_data.get("port", 6379),
            db=source_data.get("db", 0),
            password=source_data.get("password", ""),
            ssl=source_data.get("ssl", False),
            socket_timeout=source_data.get("socket_timeout", 10),
        ),
        target=RedisEndpoint(
            host=target_data.get("host", "localhost"),
            port=target_data.get("port", 6379),
            db=target_data.get("db", 0),
            password=target_data.get("password", ""),
            ssl=target_data.get("ssl", False),
            socket_timeout=target_data.get("socket_timeout", 10),
        ),
        key_patterns=data.get("patterns", ["schema:*", "synonyms:*", "csv_cache:*"]),
        exclude_patterns=data.get("exclude", []),
        dry_run=data.get("dry_run", False),
        scan_batch_size=data.get("batch_size", 100),
        overwrite=data.get("overwrite", False),
        preserve_ttl=data.get("preserve_ttl", True),
        verify=data.get("verify", True),
    )


def _build_config_from_args(args: argparse.Namespace) -> MigrationConfig:
    """CLI 인자에서 MigrationConfig를 생성한다."""
    return MigrationConfig(
        source=RedisEndpoint(
            host=args.source_host,
            port=args.source_port,
            db=args.source_db,
            password=args.source_password,
            ssl=args.source_ssl,
        ),
        target=RedisEndpoint(
            host=args.target_host,
            port=args.target_port,
            db=args.target_db,
            password=args.target_password,
            ssl=args.target_ssl,
        ),
        key_patterns=args.patterns,
        exclude_patterns=args.exclude,
        dry_run=args.dry_run,
        scan_batch_size=args.batch_size,
        overwrite=args.overwrite,
        preserve_ttl=not args.no_preserve_ttl,
        verify=not args.no_verify,
    )


def main() -> None:
    args = _parse_args()

    # 로깅 설정
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 설정 로드
    if args.config:
        config_path = Path(args.config)
        if not config_path.exists():
            logger.error("설정 파일을 찾을 수 없습니다: %s", args.config)
            sys.exit(1)
        logger.info("YAML 설정 파일 로드: %s", args.config)
        config = _load_yaml_config(args.config)
    else:
        config = _build_config_from_args(args)

    # 설정 출력
    logger.info("소스: %s", config.source.display())
    logger.info("타겟: %s", config.target.display())
    logger.info("키 패턴: %s", config.key_patterns)
    if config.exclude_patterns:
        logger.info("제외 패턴: %s", config.exclude_patterns)
    logger.info("dry-run: %s / overwrite: %s / verify: %s / preserve-ttl: %s",
                config.dry_run, config.overwrite, config.verify, config.preserve_ttl)

    # 실행
    migrator = RedisMigrator(config)
    try:
        migrator.connect()
        stats = migrator.run()
        print()
        print(stats.summary())

        if stats.failed > 0:
            sys.exit(1)
    except KeyboardInterrupt:
        logger.info("사용자에 의해 중단되었습니다.")
        sys.exit(130)
    except Exception as e:
        logger.error("마이그레이션 실패: %s", e)
        sys.exit(1)
    finally:
        migrator.close()


if __name__ == "__main__":
    main()
