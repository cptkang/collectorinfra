-- ============================================================
-- 샘플 데이터
-- ============================================================

-- 서버 데이터
INSERT INTO servers (hostname, ip_address, os, location, purpose, cpu_cores, memory_total_gb, status)
VALUES
    ('web-prod-01', '10.0.1.10', 'Ubuntu 22.04', '서울 IDC-A', '웹서버', 8, 32.00, 'active'),
    ('web-prod-02', '10.0.1.11', 'Ubuntu 22.04', '서울 IDC-A', '웹서버', 8, 32.00, 'active'),
    ('db-prod-01',  '10.0.2.10', 'CentOS 7',     '서울 IDC-A', 'DB서버', 16, 128.00, 'active'),
    ('db-prod-02',  '10.0.2.11', 'CentOS 7',     '부산 DR',    'DB서버', 16, 128.00, 'active'),
    ('batch-01',    '10.0.3.10', 'Rocky Linux 9', '서울 IDC-A', '배치서버', 4, 16.00, 'active'),
    ('mon-01',      '10.0.4.10', 'Ubuntu 22.04', '서울 IDC-A', '모니터링서버', 4, 16.00, 'active'),
    ('api-prod-01', '10.0.5.10', 'Ubuntu 22.04', '서울 IDC-A', 'API서버', 8, 64.00, 'active'),
    ('api-prod-02', '10.0.5.11', 'Ubuntu 22.04', '부산 DR',    'API서버', 8, 64.00, 'maintenance');

-- CPU 메트릭
INSERT INTO cpu_metrics (server_id, collected_at, core_count, usage_pct, system_pct, user_pct, idle_pct, load_avg_1m, load_avg_5m, load_avg_15m)
VALUES
    (1, CURRENT_TIMESTAMP - 5 MINUTES, 8, 45.20, 12.30, 32.90, 54.80, 3.20, 2.80, 2.50),
    (1, CURRENT_TIMESTAMP - 10 MINUTES, 8, 52.10, 14.50, 37.60, 47.90, 4.10, 3.50, 3.00),
    (2, CURRENT_TIMESTAMP - 5 MINUTES, 8, 38.50, 10.20, 28.30, 61.50, 2.80, 2.40, 2.10),
    (3, CURRENT_TIMESTAMP - 5 MINUTES, 16, 72.80, 18.60, 54.20, 27.20, 11.50, 10.20, 9.80),
    (3, CURRENT_TIMESTAMP - 10 MINUTES, 16, 68.30, 16.40, 51.90, 31.70, 10.80, 9.50, 9.20),
    (4, CURRENT_TIMESTAMP - 5 MINUTES, 16, 25.10, 8.30, 16.80, 74.90, 3.50, 3.20, 3.00),
    (5, CURRENT_TIMESTAMP - 5 MINUTES, 4, 85.60, 22.40, 63.20, 14.40, 3.80, 3.50, 3.20),
    (7, CURRENT_TIMESTAMP - 5 MINUTES, 8, 55.30, 15.80, 39.50, 44.70, 4.50, 4.00, 3.60);

-- 메모리 메트릭
INSERT INTO memory_metrics (server_id, collected_at, total_gb, used_gb, free_gb, usage_pct, swap_total_gb, swap_used_gb, cached_gb, buffers_gb)
VALUES
    (1, CURRENT_TIMESTAMP - 5 MINUTES, 32.00, 24.50, 7.50, 76.56, 4.00, 0.50, 5.20, 1.30),
    (2, CURRENT_TIMESTAMP - 5 MINUTES, 32.00, 22.80, 9.20, 71.25, 4.00, 0.30, 4.80, 1.10),
    (3, CURRENT_TIMESTAMP - 5 MINUTES, 128.00, 112.50, 15.50, 87.89, 16.00, 2.30, 25.60, 8.40),
    (4, CURRENT_TIMESTAMP - 5 MINUTES, 128.00, 45.20, 82.80, 35.31, 16.00, 0.10, 18.30, 6.20),
    (5, CURRENT_TIMESTAMP - 5 MINUTES, 16.00, 14.20, 1.80, 88.75, 2.00, 1.50, 2.10, 0.40),
    (7, CURRENT_TIMESTAMP - 5 MINUTES, 64.00, 48.30, 15.70, 75.47, 8.00, 1.20, 12.40, 3.60);

-- 디스크 메트릭
INSERT INTO disk_metrics (server_id, collected_at, mount_point, filesystem, total_gb, used_gb, free_gb, usage_pct, inode_usage_pct)
VALUES
    (1, CURRENT_TIMESTAMP - 5 MINUTES, '/', 'ext4', 100.00, 45.30, 54.70, 45.30, 12.50),
    (1, CURRENT_TIMESTAMP - 5 MINUTES, '/var/log', 'ext4', 50.00, 38.20, 11.80, 76.40, 8.30),
    (3, CURRENT_TIMESTAMP - 5 MINUTES, '/', 'xfs', 200.00, 85.60, 114.40, 42.80, 5.20),
    (3, CURRENT_TIMESTAMP - 5 MINUTES, '/data', 'xfs', 2000.00, 1650.00, 350.00, 82.50, 15.80),
    (5, CURRENT_TIMESTAMP - 5 MINUTES, '/', 'ext4', 100.00, 72.40, 27.60, 72.40, 22.10),
    (7, CURRENT_TIMESTAMP - 5 MINUTES, '/', 'ext4', 200.00, 95.80, 104.20, 47.90, 9.40);

-- 네트워크 메트릭
INSERT INTO network_metrics (server_id, collected_at, interface, in_bytes, out_bytes, in_packets, out_packets, in_errors, out_errors, bandwidth_mbps)
VALUES
    (1, CURRENT_TIMESTAMP - 5 MINUTES, 'eth0', 1258000000, 3845000000, 920000, 2810000, 0, 0, 1000.00),
    (2, CURRENT_TIMESTAMP - 5 MINUTES, 'eth0', 1105000000, 3210000000, 810000, 2350000, 2, 0, 1000.00),
    (3, CURRENT_TIMESTAMP - 5 MINUTES, 'bond0', 5620000000, 2480000000, 4120000, 1820000, 0, 0, 10000.00),
    (4, CURRENT_TIMESTAMP - 5 MINUTES, 'bond0', 890000000, 420000000, 650000, 310000, 0, 0, 10000.00),
    (7, CURRENT_TIMESTAMP - 5 MINUTES, 'eth0', 2340000000, 4560000000, 1720000, 3340000, 1, 0, 1000.00);
