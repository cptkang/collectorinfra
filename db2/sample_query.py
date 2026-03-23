"""
DB2 데이터베이스 조회 샘플 코드

ibm_db_sa (SQLAlchemy 드라이버) 또는 ibm-db 직접 사용 가능.
여기서는 두 가지 방식 모두 예시를 보여줍니다.

설치:
    pip install ibm-db ibm-db-sa sqlalchemy
"""

import ibm_db

# ──────────────────────────────────────────────
# 접속 정보
# ──────────────────────────────────────────────
DB2_CONFIG = {
    "database": "infradb",
    "hostname": "localhost",
    "port": "50000",
    "uid": "db2inst1",
    "password": "db2pass2024",
    "protocol": "TCPIP",
}

CONN_STR = (
    f"DATABASE={DB2_CONFIG['database']};"
    f"HOSTNAME={DB2_CONFIG['hostname']};"
    f"PORT={DB2_CONFIG['port']};"
    f"PROTOCOL={DB2_CONFIG['protocol']};"
    f"UID={DB2_CONFIG['uid']};"
    f"PWD={DB2_CONFIG['password']};"
)


# ──────────────────────────────────────────────
# 방법 1: ibm_db 직접 사용
# ──────────────────────────────────────────────
def query_with_ibm_db():
    """ibm_db 모듈을 직접 사용한 조회 예제"""
    conn = ibm_db.connect(CONN_STR, "", "")
    print("=== ibm_db 직접 사용 ===\n")

    try:
        # 1) 전체 서버 목록
        print("[1] 전체 서버 목록")
        sql = "SELECT id, hostname, ip_address, os, location, purpose, status FROM servers ORDER BY id"
        stmt = ibm_db.exec_immediate(conn, sql)
        row = ibm_db.fetch_assoc(stmt)
        while row:
            print(f"  {row['ID']:>2} | {row['HOSTNAME']:<15} | {row['IP_ADDRESS']:<15} | {row['PURPOSE'] or '':<10} | {row['STATUS']}")
            row = ibm_db.fetch_assoc(stmt)
        print()

        # 2) CPU 사용률 상위 서버
        print("[2] CPU 사용률 상위 서버 (최근 메트릭 기준)")
        sql = """
            SELECT s.hostname, c.usage_pct, c.load_avg_1m, c.collected_at
            FROM cpu_metrics c
            JOIN servers s ON s.id = c.server_id
            ORDER BY c.usage_pct DESC
            FETCH FIRST 5 ROWS ONLY
        """
        stmt = ibm_db.exec_immediate(conn, sql)
        row = ibm_db.fetch_assoc(stmt)
        while row:
            print(f"  {row['HOSTNAME']:<15} | CPU: {row['USAGE_PCT']:>6}% | Load 1m: {row['LOAD_AVG_1M']:>5}")
            row = ibm_db.fetch_assoc(stmt)
        print()

        # 3) 메모리 사용률 80% 이상 서버
        print("[3] 메모리 사용률 80% 이상 서버")
        sql = """
            SELECT s.hostname, m.total_gb, m.used_gb, m.usage_pct, m.swap_used_gb
            FROM memory_metrics m
            JOIN servers s ON s.id = m.server_id
            WHERE m.usage_pct >= 80
            ORDER BY m.usage_pct DESC
        """
        stmt = ibm_db.exec_immediate(conn, sql)
        row = ibm_db.fetch_assoc(stmt)
        while row:
            print(f"  {row['HOSTNAME']:<15} | {row['USED_GB']:>6}/{row['TOTAL_GB']:>6} GB ({row['USAGE_PCT']}%) | Swap: {row['SWAP_USED_GB']} GB")
            row = ibm_db.fetch_assoc(stmt)
        print()

        # 4) 디스크 사용률 70% 이상 파티션
        print("[4] 디스크 사용률 70% 이상 파티션")
        sql = """
            SELECT s.hostname, d.mount_point, d.total_gb, d.used_gb, d.usage_pct
            FROM disk_metrics d
            JOIN servers s ON s.id = d.server_id
            WHERE d.usage_pct >= 70
            ORDER BY d.usage_pct DESC
        """
        stmt = ibm_db.exec_immediate(conn, sql)
        row = ibm_db.fetch_assoc(stmt)
        while row:
            print(f"  {row['HOSTNAME']:<15} | {row['MOUNT_POINT']:<12} | {row['USED_GB']:>8}/{row['TOTAL_GB']:>8} GB ({row['USAGE_PCT']}%)")
            row = ibm_db.fetch_assoc(stmt)
        print()

        # 5) 네트워크 트래픽 요약
        print("[5] 네트워크 트래픽 요약 (MB 단위)")
        sql = """
            SELECT s.hostname, n.interface,
                   DECIMAL(n.in_bytes / 1048576.0, 12, 2) AS in_mb,
                   DECIMAL(n.out_bytes / 1048576.0, 12, 2) AS out_mb,
                   n.in_errors, n.out_errors
            FROM network_metrics n
            JOIN servers s ON s.id = n.server_id
            ORDER BY n.in_bytes + n.out_bytes DESC
        """
        stmt = ibm_db.exec_immediate(conn, sql)
        row = ibm_db.fetch_assoc(stmt)
        while row:
            print(f"  {row['HOSTNAME']:<15} | {row['INTERFACE']:<6} | In: {row['IN_MB']:>10} MB | Out: {row['OUT_MB']:>10} MB | Errors: {row['IN_ERRORS']}/{row['OUT_ERRORS']}")
            row = ibm_db.fetch_assoc(stmt)
        print()

    finally:
        ibm_db.close(conn)


# ──────────────────────────────────────────────
# 방법 2: SQLAlchemy 사용
# ──────────────────────────────────────────────
def query_with_sqlalchemy():
    """SQLAlchemy + ibm_db_sa 드라이버를 사용한 조회 예제"""
    from sqlalchemy import create_engine, text

    engine = create_engine(
        f"db2+ibm_db://{DB2_CONFIG['uid']}:{DB2_CONFIG['password']}"
        f"@{DB2_CONFIG['hostname']}:{DB2_CONFIG['port']}/{DB2_CONFIG['database']}"
    )

    print("=== SQLAlchemy 사용 ===\n")

    with engine.connect() as conn:
        # 서버별 최신 리소스 현황 요약
        print("[종합] 서버별 리소스 현황")
        result = conn.execute(text("""
            SELECT
                s.hostname,
                s.purpose,
                c.usage_pct AS cpu_pct,
                m.usage_pct AS mem_pct,
                c.load_avg_1m
            FROM servers s
            LEFT JOIN LATERAL (
                SELECT server_id, usage_pct, load_avg_1m
                FROM cpu_metrics
                WHERE server_id = s.id
                ORDER BY collected_at DESC
                FETCH FIRST 1 ROWS ONLY
            ) c ON 1=1
            LEFT JOIN LATERAL (
                SELECT server_id, usage_pct
                FROM memory_metrics
                WHERE server_id = s.id
                ORDER BY collected_at DESC
                FETCH FIRST 1 ROWS ONLY
            ) m ON 1=1
            WHERE s.status = 'active'
            ORDER BY c.usage_pct DESC NULLS LAST
        """))

        print(f"  {'서버':<15} | {'용도':<10} | {'CPU%':>6} | {'MEM%':>6} | {'Load 1m':>8}")
        print(f"  {'-'*15}-+-{'-'*10}-+-{'-'*6}-+-{'-'*6}-+-{'-'*8}")
        for row in result:
            cpu = f"{row.cpu_pct:>6}" if row.cpu_pct else "   N/A"
            mem = f"{row.mem_pct:>6}" if row.mem_pct else "   N/A"
            load = f"{row.load_avg_1m:>8}" if row.load_avg_1m else "     N/A"
            print(f"  {row.hostname:<15} | {row.purpose or '':<10} | {cpu} | {mem} | {load}")

    engine.dispose()


# ──────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print(" DB2 인프라 모니터링 DB 조회 샘플")
    print("=" * 60)
    print()

    # 방법 1: ibm_db 직접 사용
    query_with_ibm_db()

    # 방법 2: SQLAlchemy 사용 (주석 해제하여 실행)
    # query_with_sqlalchemy()
