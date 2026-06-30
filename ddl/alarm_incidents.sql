-- 알람 incident 라이프사이클 테이블
-- Plan 52 §9.1 / D-049: ack/incident 라이프사이클 계측 (PostgreSQL 단일 저장소)
--
-- firing(PAGE)→ack(확인)→resolved(해소) 상태 전이를 영속하여
-- MTTA/MTTR/사건전환율을 시간창 집계로 산출한다.
-- 기동 시 lifespan의 _ensure_incident_tables(pool)가 자동 생성한다(IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS alarm_incidents (
    id          BIGSERIAL PRIMARY KEY,
    fingerprint VARCHAR(128) NOT NULL,
    alarm_id    VARCHAR(64),
    db_id       VARCHAR(64),
    server_name VARCHAR(255),
    severity    INTEGER,
    priority    VARCHAR(20),
    tier        VARCHAR(20),
    status      VARCHAR(20) NOT NULL DEFAULT 'open',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    acked_at    TIMESTAMPTZ,
    acked_by    VARCHAR(100),
    resolved_at TIMESTAMPTZ,
    resolution  VARCHAR(20)
);

CREATE INDEX IF NOT EXISTS idx_alarm_incidents_status ON alarm_incidents(status);
CREATE INDEX IF NOT EXISTS idx_alarm_incidents_fp_open ON alarm_incidents(fingerprint) WHERE status = 'open';
CREATE INDEX IF NOT EXISTS idx_alarm_incidents_created_at ON alarm_incidents(created_at DESC);
