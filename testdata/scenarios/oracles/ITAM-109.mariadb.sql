-- 오라클 정본 ITAM-109 · MariaDB(itam 폐쇄망) — plans/146 W5 (1) (ITAM 질의 벤치 · 하네스 시나리오 아님)
-- 대상 시나리오: ITAM-109 「6개월 안에 지원 종료(EOS)되는 서버 알려줘」
-- 정답: tcdmsif79 하드웨어 또는 소프트웨어 지원 종료일(문자형 YYYYMMDD)이 앵커 날짜~6개월 뒤(경계 포함)인 서버
-- compare: keyset(서버호스트명) — 키 열은 3회차 카탈로그 log_policy unclassified라 실행 시 키 값은 건수로만 남는다(keys_recorded false)
-- 권장 spec: {id: ITAM-109, compare: keyset, key: [[서버호스트명, sevrHostName, …]], db_ids: [itam]}
-- 검수: 모의 DB(127.0.0.1:3308 · 합성 행 — 근거 0)에서 구문·실행만 확인 · 내부망 --check-oracle 미실행 — 내부망에서 0행·오류면 시나리오를 observe로 되돌린다
-- 근거: results/itam_bench/20261007-174622 3회차 ITAM-109(tcdmsif79 단독 · HW/SW OR · 148행) · 샌드박스 정본 ITAM-09와 같은 날짜 식(CURDATE() 대신 :today · :today_ymd)
SELECT DISTINCT `서버호스트명`
FROM `tcdmsif79`
WHERE `하드웨어지원종료년월일` BETWEEN :today_ymd AND DATE_FORMAT(DATE_ADD(:today, INTERVAL 6 MONTH), '%Y%m%d')
   OR `소프트웨어지원종료년월일` BETWEEN :today_ymd AND DATE_FORMAT(DATE_ADD(:today, INTERVAL 6 MONTH), '%Y%m%d')
ORDER BY `서버호스트명`
LIMIT 10000
