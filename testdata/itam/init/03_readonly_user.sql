-- ============================================================================
-- MCP 연결 전용 읽기 전용 계정 (plans/95 §4.6.3 · D-003 DB층)
-- ============================================================================
-- 공식 이미지의 MARIADB_USER는 MARIADB_DATABASE에 **전체 권한**을 받는다 — 그 계정을 MCP에
-- 연결하면 읽기 전용 검증이 거짓 통과한다. 그래서 SELECT만 가진 계정을 따로 만든다.
-- database명은 하드코딩하지 않는다: 엔트리포인트가 이 스크립트를 MARIADB_DATABASE를 기본
-- database로 실행하므로 DATABASE()가 그 값이다(G-2 확정 시 docker-compose.yml 한 곳만 바꾼다).
CREATE USER 'itam_ro'@'%' IDENTIFIED BY 'itam_ro_pass_2024';
SET @grant_sql = CONCAT('GRANT SELECT ON `', DATABASE(), '`.* TO ''itam_ro''@''%''');
PREPARE grant_stmt FROM @grant_sql;
EXECUTE grant_stmt;
DEALLOCATE PREPARE grant_stmt;
