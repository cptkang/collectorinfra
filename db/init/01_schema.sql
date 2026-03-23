-- ============================================================
-- 서버 모니터링 시스템 DB 스키마
-- 5개 도메인: servers, cpu_metrics, memory_metrics,
--             disk_metrics, network_metrics
-- ============================================================

-- 서버 기본 정보
CREATE TABLE servers (
    id            SERIAL PRIMARY KEY,
    hostname      VARCHAR(128) NOT NULL UNIQUE,
    ip_address    VARCHAR(45)  NOT NULL,
    os            VARCHAR(64)  NOT NULL,          -- e.g. "Ubuntu 22.04", "CentOS 7"
    location      VARCHAR(128),                   -- e.g. "서울 IDC-A", "부산 DR"
    purpose       VARCHAR(128),                   -- e.g. "웹서버", "DB서버", "배치서버"
    cpu_cores     INT          NOT NULL DEFAULT 4,
    memory_total_gb NUMERIC(10,2) NOT NULL DEFAULT 16,
    status        VARCHAR(16)  NOT NULL DEFAULT 'active',  -- active / inactive / maintenance
    created_at    TIMESTAMP    NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMP    NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_servers_hostname ON servers (hostname);
CREATE INDEX idx_servers_status   ON servers (status);

-- CPU 메트릭
CREATE TABLE cpu_metrics (
    id            BIGSERIAL PRIMARY KEY,
    server_id     INT          NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    collected_at  TIMESTAMP    NOT NULL DEFAULT NOW(),
    core_count    INT          NOT NULL,
    usage_pct     NUMERIC(5,2) NOT NULL,           -- 0.00 ~ 100.00
    system_pct    NUMERIC(5,2),
    user_pct      NUMERIC(5,2),
    idle_pct      NUMERIC(5,2),
    load_avg_1m   NUMERIC(6,2),
    load_avg_5m   NUMERIC(6,2),
    load_avg_15m  NUMERIC(6,2)
);

CREATE INDEX idx_cpu_server_time ON cpu_metrics (server_id, collected_at DESC);

-- 메모리 메트릭
CREATE TABLE memory_metrics (
    id            BIGSERIAL PRIMARY KEY,
    server_id     INT          NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    collected_at  TIMESTAMP    NOT NULL DEFAULT NOW(),
    total_gb      NUMERIC(10,2) NOT NULL,
    used_gb       NUMERIC(10,2) NOT NULL,
    free_gb       NUMERIC(10,2) NOT NULL,
    usage_pct     NUMERIC(5,2)  NOT NULL,          -- 0.00 ~ 100.00
    swap_total_gb NUMERIC(10,2),
    swap_used_gb  NUMERIC(10,2),
    cached_gb     NUMERIC(10,2),
    buffers_gb    NUMERIC(10,2)
);

CREATE INDEX idx_mem_server_time ON memory_metrics (server_id, collected_at DESC);

-- 디스크 메트릭
CREATE TABLE disk_metrics (
    id            BIGSERIAL PRIMARY KEY,
    server_id     INT          NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    collected_at  TIMESTAMP    NOT NULL DEFAULT NOW(),
    mount_point   VARCHAR(256) NOT NULL,           -- e.g. "/", "/data", "/var/log"
    filesystem    VARCHAR(128),                    -- e.g. "ext4", "xfs"
    total_gb      NUMERIC(12,2) NOT NULL,
    used_gb       NUMERIC(12,2) NOT NULL,
    free_gb       NUMERIC(12,2) NOT NULL,
    usage_pct     NUMERIC(5,2)  NOT NULL,
    inode_usage_pct NUMERIC(5,2)
);

CREATE INDEX idx_disk_server_time ON disk_metrics (server_id, collected_at DESC);

-- 네트워크 메트릭
CREATE TABLE network_metrics (
    id            BIGSERIAL PRIMARY KEY,
    server_id     INT          NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    collected_at  TIMESTAMP    NOT NULL DEFAULT NOW(),
    interface     VARCHAR(64)  NOT NULL,           -- e.g. "eth0", "bond0"
    in_bytes      BIGINT       NOT NULL DEFAULT 0,
    out_bytes     BIGINT       NOT NULL DEFAULT 0,
    in_packets    BIGINT       DEFAULT 0,
    out_packets   BIGINT       DEFAULT 0,
    in_errors     INT          DEFAULT 0,
    out_errors    INT          DEFAULT 0,
    bandwidth_mbps NUMERIC(10,2)                   -- 인터페이스 대역폭
);

CREATE INDEX idx_net_server_time ON network_metrics (server_id, collected_at DESC);
