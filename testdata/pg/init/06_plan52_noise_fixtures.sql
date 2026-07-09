-- Plan 52 (알람 노이즈 캔슬링, Phase E1) 테스트 픽스처
-- polestar_noise_context.py 의 고정 SQL이 조회하는 신호를 변량으로 갖춘 결정적 데이터.
--   - cmm_resource(server.Server): 중요도(importance_id) 高/中/低 + 유지보수(is_maintenance) 변량
--   - cmm_alarm_def + cmm_alarm_def_noti: 통보정책 有/無 2종
-- 재적용 가능(idempotent): 고정 id(95000xx) 선삭제 후 삽입. 실데이터/더미와 id 충돌 없음.
-- 중요도 매핑(테스트 정책): importance_id 1=낮음, 2=보통, 3=높음.

DELETE FROM polestar.cmm_alarm_def_noti WHERE id = 9500001;
DELETE FROM polestar.cmm_alarm_def      WHERE id IN (9500001, 9500002);
DELETE FROM polestar.cmm_resource       WHERE id IN (9500001, 9500002, 9500003, 9500004);

-- 서버 본체(server.Server) 4종 — DTIME IS NULL(미삭제), NAME으로 식별
INSERT INTO polestar.cmm_resource
  (dtype, id, acl_id, importance_id, inheritstatus, invisible, name, resource_key, resource_type, dtime, is_maintenance)
VALUES
  ('PlatformResource', 9500001, 0, 3, 0, 0, 'noise-test-high',  'noise-test-high-key',  'server.Server', NULL, 0),
  ('PlatformResource', 9500002, 0, 2, 0, 0, 'noise-test-med',   'noise-test-med-key',   'server.Server', NULL, 0),
  ('PlatformResource', 9500003, 0, 1, 0, 0, 'noise-test-low',   'noise-test-low-key',   'server.Server', NULL, 0),
  ('PlatformResource', 9500004, 0, 3, 0, 0, 'noise-test-maint', 'noise-test-maint-key', 'server.Server', NULL, 1);

-- 알람 정의 2종: 통보정책 有('noti') / 無('nonoti')
INSERT INTO polestar.cmm_alarm_def (dtype, id, enabled, name, masterdefinition_id)
VALUES
  ('AlarmDefinition', 9500001, 1, 'noise-test-alarm-noti',   9500001),
  ('AlarmDefinition', 9500002, 1, 'noise-test-alarm-nonoti', 9500002);

-- 통보정의: noti 알람에만 연결. 조인키 DN.DEFINITION_ID = D.MASTERDEFINITION_ID(=9500001)
INSERT INTO polestar.cmm_alarm_def_noti (id, notimethod, notitarget, definition_id)
VALUES
  (9500001, 'email', 'ops-team', 9500001);
