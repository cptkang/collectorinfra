#!/bin/bash
# DB2 post-start 스크립트: database manager 기동 후 DB 활성화
# /var/custom에 마운트되어 DB2 컨테이너 초기화 시 자동 실행됨

echo "[post-start] Ensuring DB2 database manager is running..."
su - db2inst1 -c "db2start" 2>/dev/null || true

echo "[post-start] Activating infradb..."
su - db2inst1 -c "db2 activate db infradb" 2>/dev/null || true

echo "[post-start] Setting DB2AUTOSTART=YES..."
su - db2inst1 -c "db2set DB2AUTOSTART=YES" 2>/dev/null || true

# init SQL 실행 (테이블이 없을 때만)
TABLE_CHECK=$(su - db2inst1 -c "db2 connect to infradb > /dev/null 2>&1 && db2 -x \"SELECT count(*) FROM syscat.tables WHERE tabschema='DB2INST1' AND tabname='SERVERS'\"" 2>/dev/null | tr -d ' ')

if [ "$TABLE_CHECK" = "0" ] || [ -z "$TABLE_CHECK" ]; then
    echo "[post-start] Tables not found. Running init scripts..."
    if [ -f /var/custom/01_schema.sql ]; then
        su - db2inst1 -c "db2 connect to infradb && db2 -tvf /var/custom/01_schema.sql" 2>&1
    fi
    if [ -f /var/custom/02_seed_data.sql ]; then
        su - db2inst1 -c "db2 connect to infradb && db2 -tvf /var/custom/02_seed_data.sql" 2>&1
    fi
    echo "[post-start] Init scripts completed."
else
    echo "[post-start] Tables already exist ($TABLE_CHECK). Skipping init."
fi

echo "[post-start] Done."
