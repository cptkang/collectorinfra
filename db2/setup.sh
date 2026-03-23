#!/bin/bash
# DB2 컨테이너 시작 및 초기화 스크립트
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== DB2 컨테이너 시작 ==="
docker compose up -d

echo ""
echo "DB2 초기화 대기 중... (최초 실행 시 2~5분 소요)"
echo ""

# DB2가 준비될 때까지 대기 (최대 5분)
MAX_WAIT=300
ELAPSED=0
while [ $ELAPSED -lt $MAX_WAIT ]; do
    if docker exec infra_db2 su - db2inst1 -c "db2 connect to infradb" >/dev/null 2>&1; then
        echo "DB2 준비 완료!"
        break
    fi
    echo "  대기 중... (${ELAPSED}s)"
    sleep 10
    ELAPSED=$((ELAPSED + 10))
done

if [ $ELAPSED -ge $MAX_WAIT ]; then
    echo "ERROR: DB2 초기화 타임아웃 (${MAX_WAIT}s)"
    echo "로그 확인: docker logs infra_db2"
    exit 1
fi

echo ""
echo "=== 스키마 생성 ==="
docker exec infra_db2 su - db2inst1 -c "db2 connect to infradb && db2 -tvf /var/custom/01_schema.sql"

echo ""
echo "=== 샘플 데이터 적재 ==="
docker exec infra_db2 su - db2inst1 -c "db2 connect to infradb && db2 -tvf /var/custom/02_seed_data.sql"

echo ""
echo "=== 완료! ==="
echo "접속 정보:"
echo "  Host:     localhost"
echo "  Port:     50000"
echo "  Database: infradb"
echo "  User:     db2inst1"
echo "  Password: db2pass2024"
