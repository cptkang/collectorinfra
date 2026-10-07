-- ============================================================================
-- 모의 DB 읽기 전용 계정 (plans/140 W4 · D-003 DB층) — 값 0 · 커밋 대상
-- ============================================================================
-- generate.py ddl이 generated/09_readonly_user.sql로 복사한다(01 스키마 · 02 합성 행 뒤에 실행).
-- 공식 이미지의 MARIADB_USER는 MARIADB_DATABASE 전체 권한이라 쓰지 않는다 — SELECT만 가진 계정.
-- database명은 하드코딩하지 않는다: 엔트리포인트가 MARIADB_DATABASE를 기본 database로 실행한다.
CREATE USER 'itam_sim_ro'@'%' IDENTIFIED BY 'itam_sim_ro_pass';
SET @grant_sql = CONCAT('GRANT SELECT ON `', DATABASE(), '`.* TO ''itam_sim_ro''@''%''');
PREPARE grant_stmt FROM @grant_sql;
EXECUTE grant_stmt;
DEALLOCATE PREPARE grant_stmt;
