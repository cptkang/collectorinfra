-- ============================================================
-- 서버 모니터링 시스템 DB2 스키마
-- 5개 도메인: servers, cpu_metrics, memory_metrics,
--             disk_metrics, network_metrics
-- ============================================================

-- 서버 기본 정보
CREATE TABLE servers (
    id            INT NOT NULL GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    hostname      VARCHAR(128) NOT NULL,
    ip_address    VARCHAR(45)  NOT NULL,
    os            VARCHAR(64)  NOT NULL,
    location      VARCHAR(128),
    purpose       VARCHAR(128),
    cpu_cores     INT          NOT NULL DEFAULT 4,
    memory_total_gb DECIMAL(10,2) NOT NULL DEFAULT 16,
    status        VARCHAR(16)  NOT NULL DEFAULT 'active',
    created_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_servers_hostname UNIQUE (hostname)
);

CREATE INDEX idx_servers_hostname ON servers (hostname);
CREATE INDEX idx_servers_status   ON servers (status);

-- CPU 메트릭
CREATE TABLE cpu_metrics (
    id            BIGINT NOT NULL GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    server_id     INT          NOT NULL,
    collected_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    core_count    INT          NOT NULL,
    usage_pct     DECIMAL(5,2) NOT NULL,
    system_pct    DECIMAL(5,2),
    user_pct      DECIMAL(5,2),
    idle_pct      DECIMAL(5,2),
    load_avg_1m   DECIMAL(6,2),
    load_avg_5m   DECIMAL(6,2),
    load_avg_15m  DECIMAL(6,2),
    CONSTRAINT fk_cpu_server FOREIGN KEY (server_id) REFERENCES servers(id) ON DELETE CASCADE
);

CREATE INDEX idx_cpu_server_time ON cpu_metrics (server_id, collected_at DESC);

-- 메모리 메트릭
CREATE TABLE memory_metrics (
    id            BIGINT NOT NULL GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    server_id     INT          NOT NULL,
    collected_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    total_gb      DECIMAL(10,2) NOT NULL,
    used_gb       DECIMAL(10,2) NOT NULL,
    free_gb       DECIMAL(10,2) NOT NULL,
    usage_pct     DECIMAL(5,2)  NOT NULL,
    swap_total_gb DECIMAL(10,2),
    swap_used_gb  DECIMAL(10,2),
    cached_gb     DECIMAL(10,2),
    buffers_gb    DECIMAL(10,2),
    CONSTRAINT fk_mem_server FOREIGN KEY (server_id) REFERENCES servers(id) ON DELETE CASCADE
);

CREATE INDEX idx_mem_server_time ON memory_metrics (server_id, collected_at DESC);

-- 디스크 메트릭
CREATE TABLE disk_metrics (
    id            BIGINT NOT NULL GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    server_id     INT          NOT NULL,
    collected_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    mount_point   VARCHAR(256) NOT NULL,
    filesystem    VARCHAR(128),
    total_gb      DECIMAL(12,2) NOT NULL,
    used_gb       DECIMAL(12,2) NOT NULL,
    free_gb       DECIMAL(12,2) NOT NULL,
    usage_pct     DECIMAL(5,2)  NOT NULL,
    inode_usage_pct DECIMAL(5,2),
    CONSTRAINT fk_disk_server FOREIGN KEY (server_id) REFERENCES servers(id) ON DELETE CASCADE
);

CREATE INDEX idx_disk_server_time ON disk_metrics (server_id, collected_at DESC);

-- 네트워크 메트릭
CREATE TABLE network_metrics (
    id            BIGINT NOT NULL GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    server_id     INT          NOT NULL,
    collected_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    interface     VARCHAR(64)  NOT NULL,
    in_bytes      BIGINT       NOT NULL DEFAULT 0,
    out_bytes     BIGINT       NOT NULL DEFAULT 0,
    in_packets    BIGINT       DEFAULT 0,
    out_packets   BIGINT       DEFAULT 0,
    in_errors     INT          DEFAULT 0,
    out_errors    INT          DEFAULT 0,
    bandwidth_mbps DECIMAL(10,2),
    CONSTRAINT fk_net_server FOREIGN KEY (server_id) REFERENCES servers(id) ON DELETE CASCADE
);

CREATE INDEX idx_net_server_time ON network_metrics (server_id, collected_at DESC);
