-- 사용자 인증 및 감사 로그 테이블
-- Plan 39: 사용자 로그인 및 인증 시스템

-- 사용자 테이블
CREATE TABLE IF NOT EXISTS auth_users (
    user_id         VARCHAR(50) PRIMARY KEY,
    username        VARCHAR(100) NOT NULL,
    hashed_password VARCHAR(256) NOT NULL,
    role            VARCHAR(20) NOT NULL DEFAULT 'user',
    status          VARCHAR(20) NOT NULL DEFAULT 'active',
    department      VARCHAR(100),
    allowed_db_ids  TEXT[],
    auth_method     VARCHAR(20) NOT NULL DEFAULT 'local',
    login_fail_count INTEGER NOT NULL DEFAULT 0,
    last_login_at   TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 감사 로그 테이블
CREATE TABLE IF NOT EXISTS audit_logs (
    id              BIGSERIAL PRIMARY KEY,
    event_type      VARCHAR(50) NOT NULL,
    user_id         VARCHAR(50),
    detail          JSONB,
    ip_address      VARCHAR(45),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_logs_user_id ON audit_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_event_type ON audit_logs(event_type);
CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at ON audit_logs(created_at DESC);
