"""마이그레이션 설정.

소스/타겟 Redis 접속 정보 및 마이그레이션 옵션을 관리한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RedisEndpoint:
    """Redis 접속 정보."""

    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: str = ""
    ssl: bool = False
    socket_timeout: int = 10

    def display(self) -> str:
        """접속 정보를 마스킹하여 표시한다."""
        pw = "***" if self.password else "(none)"
        ssl_tag = " [SSL]" if self.ssl else ""
        return f"{self.host}:{self.port}/{self.db} pw={pw}{ssl_tag}"


@dataclass
class MigrationConfig:
    """마이그레이션 설정."""

    source: RedisEndpoint = field(default_factory=RedisEndpoint)
    target: RedisEndpoint = field(default_factory=RedisEndpoint)

    # 마이그레이션할 키 패턴 (빈 리스트이면 전체)
    key_patterns: list[str] = field(default_factory=lambda: [
        "schema:*",
        "synonyms:*",
        "csv_cache:*",
    ])

    # 제외할 키 패턴
    exclude_patterns: list[str] = field(default_factory=list)

    # dry-run 모드 (실제 쓰기 없이 대상 키만 출력)
    dry_run: bool = False

    # 배치 크기 (SCAN 반복 시 한 번에 가져올 키 수)
    scan_batch_size: int = 100

    # 파이프라인 배치 크기 (쓰기 시 pipeline으로 묶을 키 수)
    pipeline_batch_size: int = 50

    # 타겟에 이미 존재하는 키 덮어쓰기 여부
    overwrite: bool = False

    # TTL 보존 여부
    preserve_ttl: bool = True

    # 마이그레이션 후 검증 여부
    verify: bool = True
