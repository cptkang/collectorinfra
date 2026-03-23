#!/bin/bash
# PostgreSQL 테스트 환경 구성 스크립트
# Docker Compose를 사용하여 PostgreSQL을 시작하고 스키마/데이터를 초기화한다.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== 서버 모니터링 DB 테스트 환경 구성 ==="

# 1. 기존 컨테이너 정리
echo "[1/4] 기존 컨테이너 정리..."
docker compose down -v 2>/dev/null || true

# 2. PostgreSQL 컨테이너 시작
echo "[2/4] PostgreSQL 컨테이너 시작..."
docker compose up -d

# 3. 헬스체크 대기
echo "[3/4] DB 초기화 대기 중..."
for i in $(seq 1 30); do
    if docker compose exec -T postgres pg_isready -U infra_user -d infra_db > /dev/null 2>&1; then
        echo "  DB 준비 완료!"
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "  ERROR: DB 시작 타임아웃"
        exit 1
    fi
    sleep 1
done

# 4. 데이터 확인
echo "[4/4] 데이터 확인..."
docker compose exec -T postgres psql -U infra_user -d infra_db -c "
SELECT '서버: ' || COUNT(*) FROM servers
UNION ALL
SELECT 'CPU 메트릭: ' || COUNT(*) FROM cpu_metrics
UNION ALL
SELECT '메모리 메트릭: ' || COUNT(*) FROM memory_metrics
UNION ALL
SELECT '디스크 메트릭: ' || COUNT(*) FROM disk_metrics
UNION ALL
SELECT '네트워크 메트릭: ' || COUNT(*) FROM network_metrics;
"

echo ""
echo "=== 환경 구성 완료 ==="
echo "접속 정보:"
echo "  Host: localhost"
echo "  Port: 5433"
echo "  Database: infra_db"
echo "  User: infra_user"
echo "  Password: infra_pass_2024"
echo "  Connection: postgresql://infra_user:infra_pass_2024@localhost:5433/infra_db"
