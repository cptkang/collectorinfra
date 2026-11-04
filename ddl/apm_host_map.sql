-- APM·SMS 연계 매핑 · 스캔 실행 기록 · 인벤토리 스냅샷
-- plans/210 §3.3 · §3.5 / D-362 (D-049: 기록자는 API 프로세스뿐 — 워커는 Redis Stream으로 넘긴다)
--
-- 매핑(apm_host_map)은 PG 정본이고 Redis `apmhostmap:*`는 읽기 사본이다.
-- 스냅샷 4표는 행마다 run_id를 달고, 「현재 스냅샷」 포인터(apm_host_map_snapshot_current)를
-- 스캐너가 성공했을 때만 교체한다(실패 스캐너는 이전 스냅샷 유지).
-- 기동 시 lifespan의 _ensure_apm_host_map_tables(pool)가 자동 생성한다(IF NOT EXISTS ·
-- NOISE_APM_HOST_MAP_ENABLED=true 이고 auth 풀이 있을 때만).

CREATE TABLE IF NOT EXISTS apm_host_map (
    id               BIGSERIAL PRIMARY KEY,
    source_id        VARCHAR(64)  NOT NULL,
    domain_id        VARCHAR(64)  NOT NULL,
    scope            VARCHAR(16)  NOT NULL,
    match_key        VARCHAR(255) NOT NULL DEFAULT '',
    domain_name      VARCHAR(255),
    instance_example VARCHAR(255),
    origin           VARCHAR(16)  NOT NULL,
    basis            VARCHAR(16)  NOT NULL,
    state            VARCHAR(16)  NOT NULL DEFAULT 'active',
    verified         BOOLEAN      NOT NULL DEFAULT FALSE,
    targets          JSONB        NOT NULL DEFAULT '[]'::jsonb,
    evidence         JSONB        NOT NULL DEFAULT '{}'::jsonb,
    note             TEXT,
    created_by       VARCHAR(100),
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_by       VARCHAR(100),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    last_used_at     TIMESTAMPTZ,
    last_seen_at     TIMESTAMPTZ,
    next_retry_at    TIMESTAMPTZ,
    version          INTEGER      NOT NULL DEFAULT 1,
    CONSTRAINT uq_apm_host_map_key UNIQUE (source_id, domain_id, scope, match_key)
);
CREATE INDEX IF NOT EXISTS idx_apm_host_map_state ON apm_host_map(state);
CREATE INDEX IF NOT EXISTS idx_apm_host_map_updated_at ON apm_host_map(updated_at DESC);

CREATE TABLE IF NOT EXISTS apm_host_map_scan_run (
    run_id        VARCHAR(64)  PRIMARY KEY,
    requested_by  VARCHAR(100),
    started_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    finished_at   TIMESTAMPTZ,
    status        VARCHAR(16)  NOT NULL DEFAULT 'running',
    scanners      JSONB        NOT NULL DEFAULT '{}'::jsonb,
    link_summary  JSONB        NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_apm_host_map_scan_run_started_at
    ON apm_host_map_scan_run(started_at DESC);

CREATE TABLE IF NOT EXISTS apm_host_map_snapshot_current (
    snapshot    VARCHAR(32)  PRIMARY KEY,
    run_id      VARCHAR(64)  NOT NULL,
    swapped_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS apm_inventory (
    id                  BIGSERIAL PRIMARY KEY,
    run_id              VARCHAR(64)  NOT NULL,
    source_id           VARCHAR(64),
    domain_id           VARCHAR(64),
    domain_name         VARCHAR(255),
    domain_description  TEXT,
    instance_id         VARCHAR(64),
    instance_name       VARCHAR(255),
    ip                  VARCHAR(64),
    host_name           VARCHAR(255),
    workload_key        VARCHAR(255),
    host_address        VARCHAR(64),
    host_address_status VARCHAR(32)
);
CREATE INDEX IF NOT EXISTS idx_apm_inventory_run_id ON apm_inventory(run_id);

CREATE TABLE IF NOT EXISTS sms_inventory (
    id           BIGSERIAL PRIMARY KEY,
    run_id       VARCHAR(64)  NOT NULL,
    db_id        VARCHAR(64),
    resource_id  VARCHAR(64),
    server_name  VARCHAR(255),
    hostname     VARCHAR(255),
    ip           VARCHAR(64),
    nic_ips      TEXT[]
);
CREATE INDEX IF NOT EXISTS idx_sms_inventory_run_id ON sms_inventory(run_id);

CREATE TABLE IF NOT EXISTS itam_snapshot_app (
    id        BIGSERIAL PRIMARY KEY,
    run_id    VARCHAR(64)  NOT NULL,
    app_code  VARCHAR(64),
    app_name  VARCHAR(255)
);
CREATE INDEX IF NOT EXISTS idx_itam_snapshot_app_run_id ON itam_snapshot_app(run_id);

CREATE TABLE IF NOT EXISTS itam_snapshot_server (
    id        BIGSERIAL PRIMARY KEY,
    run_id    VARCHAR(64)  NOT NULL,
    app_code  VARCHAR(64),
    app_name  VARCHAR(255),
    hostname  VARCHAR(255),
    ips       TEXT[],
    active    BOOLEAN,
    clue_text TEXT
);
ALTER TABLE itam_snapshot_server ADD COLUMN IF NOT EXISTS clue_text TEXT;
CREATE INDEX IF NOT EXISTS idx_itam_snapshot_server_run_id ON itam_snapshot_server(run_id);
