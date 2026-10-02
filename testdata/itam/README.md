# 자산관리(ITAM) 로컬 MariaDB 샌드박스

`plans/95` 트랙 S(§4.6)의 로컬 픽스처다. `mcp_server`의 MariaDB 지원(드라이버·인트로스펙션·권한·방언 실패 양상)을
운영 자산 DB 없이 **실 엔진으로** 검증한다.

> **증명 범위** — 이 DB는 전사본(`schema.yaml`)에서 파생된다. **엔진 동작은 증명하지만 운영 사실은 증명하지 못한다.**
> 물리 식별자(G-4)·database명(G-2)·코드값(G-5)·호스트 키 정합(G-6)·서버 변수(G-13)는 게이트로만 확정한다.
>
> **파생 금지** — 이 DB에서 `config/db_profiles/itam.yaml`·`config/knowledge/itam/`·`config/synonym_seeds/itam.yaml`을
> 추출하지 않는다(자동 구조 분석 `source: auto` · `scripts/synonym_seeds.py derive` 산출물 커밋 금지). 같은 전사본을
> 자기 자신과 비교하는 순환 검증이 된다. 합성 코드값은 `Z9…` 표식이라 새어 들어가면 바로 드러난다.

## 빠른 시작

```bash
testdata/itam/setup.sh     # down -v → up → itam_ro TCP 접속 대기 → 행수·권한 출력
```

시드 날짜가 init 시점 상대값이므로 **매번 볼륨까지 지우고 다시 만든다**. 컨테이너를 오래 두면 경계 행이 늙는다.

| 파일 | 성격 |
|---|---|
| `schema.yaml` | 벤더 시트 전사본(정본 아님 · 수정 금지 규칙은 파일 헤더) |
| `generate_init.py` | 전사본 → `init/01_schema.sql`·`init/02_seed.sql` 생성기 |
| `init/01_schema.sql` · `init/02_seed.sql` | **생성물** — 직접 수정 금지. 재생성 diff 0은 `tests/test_testdata/test_itam_generate_init.py`가 고정 |
| `init/03_readonly_user.sql` | 수기 — SELECT 전용 `itam_ro` |
| `docker-compose.yml` · `setup.sh` | 컨테이너 구성 · 기동 스크립트 |

## 접속 정보

| 항목 | 값 |
|---|---|
| 컨테이너 | `itam_mariadb` (`mariadb:11.4`) |
| Host / Port | `localhost` / `3307` |
| Database | `INST1` — **G-2 미확정 가정**(바꿀 곳은 `docker-compose.yml`의 `MARIADB_DATABASE` 한 곳) |
| MCP 계정 | `itam_ro` / `itam_ro_pass_2024` — `GRANT SELECT ON INST1.*`만 보유 |
| root | `itam_root_pass_2024` — 초기화 전용. **MCP에 쓰지 않는다** |

`mcp_server/.env`에 다음 한 줄을 넣으면 소스 `itam`이 활성화된다(루트 `.env`의 `ACTIVE_DB_IDS`는 건드리지 않는다 —
plans/95 W-12 전에는 MCP 계층 직접 호출까지만 검증한다).

```
ITAM_CONNECTION=mariadb://itam_ro:itam_ro_pass_2024@localhost:3307/INST1
```

통합 테스트: `cd mcp_server && RUN_DOCKER_IT=1 ../.venv/bin/python -m pytest tests/test_mariadb_integration.py`

## 가정으로 고정한 서버 조건 (G-13 확정 전)

| 변수 | 값 | 이유 |
|---|---|---|
| `lower_case_table_names` | `0` | Linux 기본 — 테이블명 대소문자 구분(§3.4-b) 재현. named volume이라 macOS 파일시스템 영향 없음 |
| `character_set_server` / `collation_server` | `utf8mb4` / `utf8mb4_general_ci` | 11.4 기본 collation(uca1400)에 기대지 않고 명시 |
| `sql_mode` | 이미지 기본값 | `STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION` (실측) — `PIPES_AS_CONCAT`·`ANSI_QUOTES` 없음 |

식별자는 전사본 `var`(camelCase)·시트 표기(대문자 테이블명)를 그대로 쓴 **G-4 미확정 가정**이다.

## 로컬 오라클 — plans/95 §6-1 질의 3종

파이프라인과 무관하게 **사람이 쓴 SQL**과 기대 결과다. 날짜가 상대값이라 기대 결과는 호스트명(= 어느 설계 행인가)으로 적는다.
`CURDATE()`는 **컨테이너 시계(UTC)** 기준이다 — 한국 시간 09시 이전에는 하루 전 날짜가 기준이 된다.

### ① 지원 종료일이 6개월 내인 서버

```sql
SELECT a.sevrHostName, b.hWSportEndYmd, b.sWSportEndYmd
FROM TCDMSIF80 a
JOIN TCDMSIF79 b
  ON a.groupCoCd = b.groupCoCd AND a.sevrHostName = b.sevrHostName AND a.iPCtnt = b.iPCtnt
WHERE b.hWSportEndYmd BETWEEN DATE_FORMAT(CURDATE(), '%Y%m%d') AND DATE_FORMAT(CURDATE() + INTERVAL 6 MONTH, '%Y%m%d')
   OR b.sWSportEndYmd BETWEEN DATE_FORMAT(CURDATE(), '%Y%m%d') AND DATE_FORMAT(CURDATE() + INTERVAL 6 MONTH, '%Y%m%d')
ORDER BY a.sevrHostName;
```

기대: **`svr-was-01`**(HW +3개월) · **`svr-was-02`**(SW +2개월) · **`svr-was-03`**(HW 정확히 +6개월 — 경계 포함).
밖: `svr-was-04`(+7개월) · `svr-was-05`(이미 종료) · `svr-was-06`(79 짝 없음 — `LEFT JOIN`이면 NULL로 나온다).

### ② 유지보수 계약이 이번 분기에 만료되는 서버

```sql
SELECT sevrHostName, manmenCtrcEndYmd
FROM TCDMSIF80
WHERE manmenCtrcEndYmd
  BETWEEN DATE_FORMAT(MAKEDATE(YEAR(CURDATE()), 1) + INTERVAL (QUARTER(CURDATE()) - 1) QUARTER, '%Y%m%d')
      AND DATE_FORMAT(MAKEDATE(YEAR(CURDATE()), 1) + INTERVAL QUARTER(CURDATE()) QUARTER - INTERVAL 1 DAY, '%Y%m%d')
ORDER BY sevrHostName;
```

기대: **`svr-db-01`**(분기 첫날) · **`svr-db-02`**(분기 마지막 날) · **`svr-db-03`**(분기 첫날 +1개월).
밖: `svr-db-04`(분기 첫날 -1일) · `svr-db-05`(분기 마지막 날 +1일).

### ③ 경과년수 5년 이상 노후 서버

```sql
SELECT sevrHostName, elapsNoy FROM TCDMSIF80 WHERE elapsNoy >= 5 ORDER BY sevrHostName;
```

기대: **`svr-db-07`**(5년 — 경계 포함) · **`svr-db-08`**(7년). 밖: `svr-db-06`(4년) · `svr-db-09`(NULL).
노후교체기기 구분코드(`osoaRplacMchtlDstcd`)는 코드값 미확보(G-5)라 오라클에 쓰지 않는다.

위 세 결과는 2026-09-17 `setup.sh` 직후 실측으로 확인했다.

## 시드 설계 요약 (`generate_init.py` `_DESIGN`)

- **호스트 30개** = 폴스타 로컬 샌드박스(`testdata/pg`)의 `svr-web/was/db-01~10`과 같은 이름·IP.
- **호스트 키 4형**(G-6 · 교차 질의 병합): `svr-web-01` 정확 일치 · `SVR-WEB-02` 대소문자만 다름 ·
  `svr-web-03.synth.example` FQDN · `svr-web-04` 한 칸에 다중 IP.
- **사용률 판별값(T5)**: CPU·메모리·스토리지 사용률 소수부 `.37` 고정. 폴스타 샌드박스의 수치 컬럼 63개·EAV 값에는
  `.37`이 0건이다(2026-09-17 실측) — 응답 숫자만으로 어느 DB가 답했는지 가린다.
- **합성 코드값**: 코드 컬럼 전부 `Z`·`Z9`·`Z99`·`Z999`. **민감 컬럼**: 금액은 합성 수치, 담당자명은 성명 형태 합성값.

## 리허설 기록 — PG 방언 SQL을 흘렸을 때 (2026-09-17 · MariaDB 11.4.13 · 기본 sql_mode)

단언이 아니라 `config/db_profiles/itam.yaml` `query_guide` 방언 규칙(W-6)의 재료다.

| PG 방언 | 결과 | 분류 |
|---|---|---|
| `sevrCPUUseRt::numeric` | `ERROR 1064` 구문 오류 | 오류 — 재생성 회귀가 발동한다 |
| `INTERVAL '1 day'` | `ERROR 1064` 구문 오류 | 오류 |
| `'a' \|\| 'b'` · `sevrHostName \|\| '-x'` | **`0`** (OR 연산으로 해석) | **침묵 오답** |
| `"sevrHostName"` | **문자열 리터럴 `sevrHostName`** · `WHERE "sevrHostName" = 'svr-web-01'`는 **0행** | **침묵 오답** |

침묵 오답 2종은 SQL 에러 회귀조차 발동하지 않으므로 프롬프트 규칙만으로 두지 말고, 반복 실패가 실측되면
결정적 교정 후보로 올린다(plans/95 §4.2).

그 밖의 관찰:

- 소문자 `tcdmsif80` 조회는 `ERROR 1146`이고, `information_schema`의 `table_name` 비교도 대소문자를 구분해
  인트로스펙션 결과가 0건이다(describe와 SELECT의 판정이 갈리지 않는다).
- `utf8mb4_general_ci`에서 `sevrHostName = 'svr-web-02'`가 **`SVR-WEB-02`와 일치**한다 — 호스트 키 비교가 대소문자를
  무시하는지는 운영 collation(G-13)에 달렸다(G-6과 짝).
- `itam_ro`의 `INSERT`·`CREATE TABLE`은 `ERROR 1142`(권한 없음) — MCP `readonly` 검증과 독립으로 DB층이 막는다.
