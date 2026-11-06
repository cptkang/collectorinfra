-- 업무 서비스 · 구성원 · 후보 · 계산 실행 기록 · 보강 속성 · 상태 전이 기록
-- plans/215 §3.5 · §4.5 / D-368 (D-049: 기록자는 API 프로세스뿐 — 워커는 Redis 사본만 읽는다)
--
-- 서비스·구성원은 PG 정본이고 Redis `bizsvc:*`는 읽기 사본이다(적용·기동 시 전체 재구성).
-- 구성원은 UNIQUE(kind, member_key)라 SMS 서버 1대는 서비스 1개에만 속한다.
-- 후보는 candidate_key UNIQUE로 거절을 기억한다(다시 내지 않는다).
-- 보존: 실행 기록 90일 · 상태 전이 90일 · target_gone 자동 구성원 180일 · 보강 속성 최근 run 3개분.
-- 기동 시 lifespan의 _ensure_biz_service_tables(pool)가 자동 생성한다(IF NOT EXISTS ·
-- NOISE_BIZ_SERVICE_ENABLED=true 이고 auth 풀이 있을 때만).

CREATE TABLE IF NOT EXISTS biz_service (
    id            BIGSERIAL PRIMARY KEY,
    service_key   VARCHAR(128) NOT NULL,
    display_name  VARCHAR(255) NOT NULL,
    tier          SMALLINT     CHECK (tier IS NULL OR tier BETWEEN 1 AND 3),
    description   VARCHAR(500),
    owner_org     VARCHAR(60),
    origin        VARCHAR(16)  NOT NULL CHECK (origin IN ('auto', 'manual')),
    state         VARCHAR(16)  NOT NULL DEFAULT 'active'
                  CHECK (state IN ('active', 'ambiguous', 'disabled')),
    verified      BOOLEAN      NOT NULL DEFAULT FALSE,
    monitored     BOOLEAN      NOT NULL DEFAULT FALSE,
    evidence      JSONB        NOT NULL DEFAULT '{}'::jsonb,
    note          TEXT,
    created_by    VARCHAR(100),
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_by    VARCHAR(100),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    version       INTEGER      NOT NULL DEFAULT 1,
    last_run_id   VARCHAR(64),
    CONSTRAINT uq_biz_service_key UNIQUE (service_key)
);
CREATE INDEX IF NOT EXISTS idx_biz_service_state ON biz_service(state);
CREATE INDEX IF NOT EXISTS idx_biz_service_monitored ON biz_service(monitored) WHERE monitored;

CREATE TABLE IF NOT EXISTS biz_service_member (
    id             BIGSERIAL PRIMARY KEY,
    service_id     BIGINT       NOT NULL REFERENCES biz_service(id) ON DELETE CASCADE,
    kind           VARCHAR(16)  NOT NULL
                   CHECK (kind IN ('sms_server', 'apm_instance', 'apm_domain', 'apm_business')),
    member_key     VARCHAR(512) NOT NULL,
    db_id          VARCHAR(64),
    resource_id    VARCHAR(64),
    server_name    VARCHAR(255),
    hostname       VARCHAR(255),
    ip             VARCHAR(64),
    zone           VARCHAR(64),
    source_id      VARCHAR(64),
    domain_id      VARCHAR(64),
    domain_name    VARCHAR(255),
    instance_name  VARCHAR(255),
    prefix_code    VARCHAR(64),
    origin         VARCHAR(16)  NOT NULL CHECK (origin IN ('auto', 'manual')),
    basis          VARCHAR(32)  NOT NULL,
    confidence     VARCHAR(16)  NOT NULL DEFAULT 'inferred'
                   CHECK (confidence IN ('confirmed', 'inferred')),
    state          VARCHAR(16)  NOT NULL DEFAULT 'active'
                   CHECK (state IN ('active', 'excluded', 'target_gone')),
    apm_map_id     BIGINT,
    layer          VARCHAR(16)
                   CHECK (layer IS NULL OR layer IN ('web', 'was', 'db', 'batch', 'etc')),
    layer_origin   VARCHAR(16)  CHECK (layer_origin IS NULL OR layer_origin IN ('auto', 'manual')),
    evidence       JSONB        NOT NULL DEFAULT '{}'::jsonb,
    gone_at        TIMESTAMPTZ,
    created_by     VARCHAR(100),
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_by     VARCHAR(100),
    updated_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_biz_service_member_key UNIQUE (kind, member_key)
);
CREATE INDEX IF NOT EXISTS idx_biz_service_member_service ON biz_service_member(service_id);
CREATE INDEX IF NOT EXISTS idx_biz_service_member_gone
    ON biz_service_member(gone_at) WHERE state = 'target_gone';

CREATE TABLE IF NOT EXISTS biz_service_candidate (
    id                BIGSERIAL PRIMARY KEY,
    kind              VARCHAR(16)  NOT NULL CHECK (kind IN ('member', 'merge', 'apm_unlinked')),
    candidate_key     VARCHAR(512) NOT NULL,
    service_id        BIGINT       REFERENCES biz_service(id) ON DELETE CASCADE,
    other_service_id  BIGINT       REFERENCES biz_service(id) ON DELETE CASCADE,
    member_kind       VARCHAR(16),
    member_key        VARCHAR(512),
    signals           JSONB        NOT NULL DEFAULT '[]'::jsonb,
    evidence          JSONB        NOT NULL DEFAULT '{}'::jsonb,
    status            VARCHAR(16)  NOT NULL DEFAULT 'open'
                      CHECK (status IN ('open', 'accepted', 'rejected')),
    run_id            VARCHAR(64),
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    decided_by        VARCHAR(100),
    decided_at        TIMESTAMPTZ,
    CONSTRAINT uq_biz_service_candidate_key UNIQUE (candidate_key)
);
CREATE INDEX IF NOT EXISTS idx_biz_service_candidate_status
    ON biz_service_candidate(status, kind);

CREATE TABLE IF NOT EXISTS biz_service_run (
    run_id        VARCHAR(64)  PRIMARY KEY,
    requested_by  VARCHAR(100),
    scan_run_id   VARCHAR(64),
    mode          VARCHAR(16)  NOT NULL CHECK (mode IN ('preview', 'apply')),
    status        VARCHAR(16)  NOT NULL DEFAULT 'running'
                  CHECK (status IN ('running', 'done', 'failed', 'aborted')),
    summary       JSONB        NOT NULL DEFAULT '{}'::jsonb,
    started_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    finished_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_biz_service_run_started_at ON biz_service_run(started_at DESC);

CREATE TABLE IF NOT EXISTS biz_service_attr (
    id                 BIGSERIAL PRIMARY KEY,
    run_id             VARCHAR(64)  NOT NULL,
    db_id              VARCHAR(64)  NOT NULL,
    resource_id        VARCHAR(64)  NOT NULL,
    tree_group_id      VARCHAR(64),
    tree_group_name    VARCHAR(255),
    tree_code          VARCHAR(255),
    registered_at      TIMESTAMPTZ,
    depends            JSONB        NOT NULL DEFAULT '[]'::jsonb,
    contract_no        VARCHAR(100),
    contract_name      VARCHAR(255),
    maint_contract_no  VARCHAR(100),
    owner_branch       VARCHAR(255),
    CONSTRAINT uq_biz_service_attr_key UNIQUE (run_id, db_id, resource_id)
);
CREATE INDEX IF NOT EXISTS idx_biz_service_attr_run_id ON biz_service_attr(run_id);

CREATE TABLE IF NOT EXISTS biz_service_state_log (
    id          BIGSERIAL PRIMARY KEY,
    service_id  BIGINT       NOT NULL REFERENCES biz_service(id) ON DELETE CASCADE,
    from_state  VARCHAR(32),
    to_state    VARCHAR(32)  NOT NULL,
    reason      JSONB        NOT NULL DEFAULT '{}'::jsonb,
    at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_biz_service_state_log_service
    ON biz_service_state_log(service_id, at DESC);
CREATE INDEX IF NOT EXISTS idx_biz_service_state_log_at ON biz_service_state_log(at);
