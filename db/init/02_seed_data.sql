-- ============================================================
-- 테스트용 샘플 데이터
-- 서버 10대, 최근 7일간 시간별 메트릭 데이터
-- ============================================================

-- 서버 10대
INSERT INTO servers (hostname, ip_address, os, location, purpose, cpu_cores, memory_total_gb, status) VALUES
('web-prod-01',  '10.0.1.11', 'Ubuntu 22.04', '서울 IDC-A', '웹서버',      8,  32, 'active'),
('web-prod-02',  '10.0.1.12', 'Ubuntu 22.04', '서울 IDC-A', '웹서버',      8,  32, 'active'),
('api-prod-01',  '10.0.2.21', 'Ubuntu 22.04', '서울 IDC-A', 'API서버',     16, 64, 'active'),
('api-prod-02',  '10.0.2.22', 'Ubuntu 22.04', '서울 IDC-A', 'API서버',     16, 64, 'active'),
('db-master-01', '10.0.3.31', 'CentOS 7',     '서울 IDC-A', 'DB서버',      32, 128, 'active'),
('db-slave-01',  '10.0.3.32', 'CentOS 7',     '서울 IDC-A', 'DB서버',      32, 128, 'active'),
('batch-01',     '10.0.4.41', 'Rocky Linux 9', '부산 DR',   '배치서버',     16, 64, 'active'),
('monitor-01',   '10.0.5.51', 'Ubuntu 22.04', '서울 IDC-A', '모니터링서버', 4,  16, 'active'),
('cache-01',     '10.0.6.61', 'Ubuntu 22.04', '서울 IDC-A', '캐시서버',     8,  64, 'active'),
('backup-01',    '10.0.7.71', 'Rocky Linux 9', '부산 DR',   '백업서버',     8,  32, 'inactive');

-- CPU 메트릭 (서버별 최근 7일, 6시간 간격 = 28개 포인트)
INSERT INTO cpu_metrics (server_id, collected_at, core_count, usage_pct, system_pct, user_pct, idle_pct, load_avg_1m, load_avg_5m, load_avg_15m)
SELECT
    s.id,
    ts,
    s.cpu_cores,
    -- 서버 용도별 기본 사용률 + 시간대 변동 + 랜덤
    LEAST(100, GREATEST(1, CASE
        WHEN s.purpose = '웹서버'      THEN 45 + 20 * sin(extract(epoch from ts) / 43200) + (random() * 15)
        WHEN s.purpose = 'API서버'     THEN 55 + 15 * sin(extract(epoch from ts) / 43200) + (random() * 15)
        WHEN s.purpose = 'DB서버'      THEN 65 + 10 * sin(extract(epoch from ts) / 43200) + (random() * 10)
        WHEN s.purpose = '배치서버'     THEN 30 + 40 * sin(extract(epoch from ts) / 86400) + (random() * 20)
        WHEN s.purpose = '캐시서버'     THEN 25 + 10 * sin(extract(epoch from ts) / 43200) + (random() * 10)
        ELSE 15 + (random() * 20)
    END))::numeric(5,2),
    (5 + random() * 15)::numeric(5,2),
    (20 + random() * 30)::numeric(5,2),
    (30 + random() * 40)::numeric(5,2),
    (s.cpu_cores * 0.3 + random() * s.cpu_cores * 0.5)::numeric(6,2),
    (s.cpu_cores * 0.25 + random() * s.cpu_cores * 0.4)::numeric(6,2),
    (s.cpu_cores * 0.2 + random() * s.cpu_cores * 0.3)::numeric(6,2)
FROM servers s
CROSS JOIN generate_series(
    NOW() - interval '7 days',
    NOW(),
    interval '6 hours'
) AS ts
WHERE s.status = 'active';

-- 메모리 메트릭
INSERT INTO memory_metrics (server_id, collected_at, total_gb, used_gb, free_gb, usage_pct, swap_total_gb, swap_used_gb, cached_gb, buffers_gb)
SELECT
    s.id,
    ts,
    s.memory_total_gb,
    (s.memory_total_gb * CASE
        WHEN s.purpose = 'DB서버'      THEN 0.80 + random() * 0.15
        WHEN s.purpose = '캐시서버'     THEN 0.75 + random() * 0.15
        WHEN s.purpose = 'API서버'     THEN 0.55 + random() * 0.20
        WHEN s.purpose = '웹서버'      THEN 0.50 + random() * 0.20
        ELSE 0.40 + random() * 0.25
    END)::numeric(10,2) AS used_gb,
    0 AS free_gb, -- 아래에서 계산
    0 AS usage_pct, -- 아래에서 계산
    (s.memory_total_gb * 0.25)::numeric(10,2),
    (s.memory_total_gb * 0.25 * random() * 0.3)::numeric(10,2),
    (s.memory_total_gb * 0.1 + random() * s.memory_total_gb * 0.15)::numeric(10,2),
    (s.memory_total_gb * 0.02 + random() * s.memory_total_gb * 0.05)::numeric(10,2)
FROM servers s
CROSS JOIN generate_series(
    NOW() - interval '7 days',
    NOW(),
    interval '6 hours'
) AS ts
WHERE s.status = 'active';

-- free_gb, usage_pct 보정
UPDATE memory_metrics SET
    free_gb = (total_gb - used_gb)::numeric(10,2),
    usage_pct = ((used_gb / total_gb) * 100)::numeric(5,2);

-- 디스크 메트릭 (서버별 마운트포인트 2~3개)
-- / 파티션
INSERT INTO disk_metrics (server_id, collected_at, mount_point, filesystem, total_gb, used_gb, free_gb, usage_pct, inode_usage_pct)
SELECT
    s.id,
    ts,
    '/',
    'ext4',
    100,
    (100 * (0.40 + random() * 0.30))::numeric(12,2),
    0,
    0,
    (20 + random() * 30)::numeric(5,2)
FROM servers s
CROSS JOIN generate_series(
    NOW() - interval '7 days',
    NOW(),
    interval '6 hours'
) AS ts
WHERE s.status = 'active';

-- /data 파티션 (DB/배치/백업 서버만)
INSERT INTO disk_metrics (server_id, collected_at, mount_point, filesystem, total_gb, used_gb, free_gb, usage_pct, inode_usage_pct)
SELECT
    s.id,
    ts,
    '/data',
    'xfs',
    CASE WHEN s.purpose = 'DB서버' THEN 2000 ELSE 500 END,
    (CASE WHEN s.purpose = 'DB서버' THEN 2000 ELSE 500 END * (0.50 + random() * 0.40))::numeric(12,2),
    0,
    0,
    (10 + random() * 20)::numeric(5,2)
FROM servers s
CROSS JOIN generate_series(
    NOW() - interval '7 days',
    NOW(),
    interval '6 hours'
) AS ts
WHERE s.status = 'active'
  AND s.purpose IN ('DB서버', '배치서버', '백업서버');

-- 디스크 free_gb, usage_pct 보정
UPDATE disk_metrics SET
    free_gb = (total_gb - used_gb)::numeric(12,2),
    usage_pct = ((used_gb / total_gb) * 100)::numeric(5,2);

-- 네트워크 메트릭 (eth0 인터페이스)
INSERT INTO network_metrics (server_id, collected_at, interface, in_bytes, out_bytes, in_packets, out_packets, in_errors, out_errors, bandwidth_mbps)
SELECT
    s.id,
    ts,
    'eth0',
    -- in_bytes: 서버 용도별 트래픽량 (6시간 동안의 누적)
    (CASE
        WHEN s.purpose = '웹서버'  THEN 5e9 + random() * 10e9
        WHEN s.purpose = 'API서버' THEN 3e9 + random() * 8e9
        WHEN s.purpose = 'DB서버'  THEN 2e9 + random() * 5e9
        WHEN s.purpose = '캐시서버' THEN 4e9 + random() * 6e9
        ELSE 0.5e9 + random() * 2e9
    END)::bigint,
    (CASE
        WHEN s.purpose = '웹서버'  THEN 8e9 + random() * 15e9
        WHEN s.purpose = 'API서버' THEN 5e9 + random() * 10e9
        WHEN s.purpose = 'DB서버'  THEN 3e9 + random() * 6e9
        WHEN s.purpose = '캐시서버' THEN 6e9 + random() * 8e9
        ELSE 0.3e9 + random() * 1e9
    END)::bigint,
    (1e6 + random() * 5e6)::bigint,
    (1e6 + random() * 5e6)::bigint,
    (random() * 5)::int,
    (random() * 3)::int,
    CASE
        WHEN s.purpose IN ('DB서버', 'API서버') THEN 10000
        ELSE 1000
    END
FROM servers s
CROSS JOIN generate_series(
    NOW() - interval '7 days',
    NOW(),
    interval '6 hours'
) AS ts
WHERE s.status = 'active';
