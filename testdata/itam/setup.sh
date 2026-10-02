#!/bin/bash
# 자산관리(ITAM) 로컬 MariaDB 샌드박스 구성 스크립트 (plans/95 트랙 S)
# Docker Compose로 MariaDB를 새로 띄우고 스키마·합성 시드·읽기 전용 계정을 초기화한다.
# 시드 날짜가 init 시점 상대값이라 매번 볼륨까지 지우고(down -v) 다시 만든다.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

RO_USER=itam_ro
RO_PASS=itam_ro_pass_2024

echo "=== 자산관리(ITAM) MariaDB 샌드박스 구성 ==="

# 1. 기존 컨테이너·볼륨 정리
echo "[1/4] 기존 컨테이너 정리..."
docker compose down -v 2>/dev/null || true

# 2. MariaDB 컨테이너 시작
echo "[2/4] MariaDB 컨테이너 시작..."
docker compose up -d

# 3. 준비 대기 — 초기화 중 임시 서버는 TCP를 열지 않고 itam_ro는 03 스크립트가 만든다.
#    그래서 itam_ro의 TCP 접속 성공 = init 스크립트 전부 완료 + 본 서버 기동이다.
echo "[3/4] DB 초기화 대기 중..."
for i in $(seq 1 60); do
    if docker compose exec -T itam-mariadb mariadb -h127.0.0.1 -u"$RO_USER" -p"$RO_PASS" \
        -e "SELECT 1" > /dev/null 2>&1; then
        echo "  DB 준비 완료!"
        break
    fi
    if [ "$i" -eq 60 ]; then
        echo "  ERROR: DB 시작 타임아웃 (docker compose logs itam-mariadb 확인)"
        exit 1
    fi
    sleep 2
done

# 4. 데이터·권한 확인 — database명은 compose 환경변수 한 곳(G-2)에서 읽는다
echo "[4/4] 데이터·권한 확인..."
DB_NAME=$(docker compose exec -T itam-mariadb printenv MARIADB_DATABASE | tr -d '\r')
docker compose exec -T itam-mariadb mariadb -h127.0.0.1 -u"$RO_USER" -p"$RO_PASS" "$DB_NAME" -e "
SELECT DATABASE() AS db, @@version AS version, @@lower_case_table_names AS lctn, @@collation_server AS collation;
SELECT 'TCDMSIF80' AS tbl, COUNT(*) AS n FROM TCDMSIF80
UNION ALL
SELECT 'TCDMSIF79', COUNT(*) FROM TCDMSIF79;
SHOW GRANTS FOR CURRENT_USER;
"

echo ""
echo "=== 환경 구성 완료 ==="
echo "접속 정보 (읽기 전용):"
echo "  Host: localhost"
echo "  Port: 3307"
echo "  Database: $DB_NAME"
echo "  User: $RO_USER"
echo "  Password: $RO_PASS"
echo "  mcp_server/.env: ITAM_CONNECTION=mariadb://$RO_USER:$RO_PASS@localhost:3307/$DB_NAME"
