# ITAM 외부망 모의 DB (plans/140 W4 · D-311 ②⑤ · G-4)

내부망 벤치 반출물로 **외부망에서 쿼리 자동 생성을 시험**할 MariaDB 모의 DB다. 내부망 ITAM DB의 구조(테이블·컬럼·타입·
NULL·기본키·주석)를 그대로 만들고, 2회차 반출부터는 치환 코드값·값 형식·채택 관계로 합성 행을 채운다.

> **커밋 금지** — 저장소에는 생성기(`generate.py`)·compose·읽기 전용 계정 스크립트·이 README만 둔다. DDL·치환 코드값·합성 행은
> 전부 `generated/`(이 디렉터리의 `.gitignore`)에 생긴다. 생성기는 `generated/` · 저장소 밖 · git 무시 경로가 아니면 쓰기를 거부한다.
> 치환값은 외부망 테스트 자료로만 쓰고 내부망으로 가는 설정 파일(`config/…`)에 옮기지 않는다(D-311 ②).

> **증명 범위** — 이 DB의 값은 합성이다. 생성된 SQL이 **실행되는지·조인 키가 맞는지**는 보여 주지만, 결과 건수·값이
> 내부망과 같다는 근거가 되지 않는다. 로컬 2테이블 샌드박스(`testdata/itam/` · 3307)와는 별개다.

| 파일 | 성격 |
|---|---|
| `generate.py` | 반출 카탈로그 → `generated/01_schema.sql`(빈 스키마) · `generated/02_rows.sql`(합성 행) · `generated/09_readonly_user.sql`(사본) |
| `readonly_user.sql` | 수기 — SELECT 전용 `itam_sim_ro`(값 0) |
| `docker-compose.yml` | `mariadb:11.4` · `127.0.0.1:3308` · DB `INST1` · 초기화 디렉터리 = `generated/` |
| `generated/` | **생성물 · gitignore** — 직접 수정하지 않는다 |

## 생성 순서

| 회차 | 반출물에 있는 것 | 만드는 것 |
|---|---|---|
| 1회차(`20261006-152938`) | 이름·타입(길이 없음)·NULL | **빈 스키마만** — 108테이블 · 1,988컬럼. 형식·관계·코드 근거가 없으므로 합성 행은 만들지 않는다 |
| 2회차(W2 이후) | + 기본키·타입 길이·채택 관계·컬럼 `profile`·DB 주석 · `code_samples.yaml` | 빈 스키마(길이·기본키·주석 반영) + 합성 행 |

```bash
# 저장소 루트에서 — 반출 경로는 디렉터리(schema_catalog.yaml·code_samples.yaml이 있는 곳) 또는 카탈로그 파일
.venv/bin/python testdata/itam_closed_sim/generate.py ddl  results/itam_bench/<run>/
.venv/bin/python testdata/itam_closed_sim/generate.py rows results/itam_bench/<run>/ --rows 20 --seed 140
#   --code-samples PATH  치환 코드값 파일(기본: 카탈로그 옆 code_samples.yaml)
#   --out DIR            출력 디렉터리(기본 generated/ — 저장소 밖·gitignore 경로만 허용)
```

`ddl`은 쓰기 전에 MariaDB 한도(행 크기 65,535바이트 · 기본키 3,072바이트)를 정적으로 점검하고, 생성한 DDL을
`src/schema_cache/ddl_schema_parser.parse_ddl(…, "mariadb")`로 다시 읽어 테이블·컬럼 수가 카탈로그와 같은지 확인한다.

### 기본 타입 표 (타입 길이가 없을 때)

반출 타입에 길이가 있으면(`char(10)`) 그대로 쓴다. 없으면 값이 잘리지 않는 쪽의 보수적 기본값을 쓴다
(`generate.py`의 `DEFAULT_TYPE_ARGS`와 같다 — 테스트가 고정).

| 반출 타입 | DDL | 근거 |
|---|---|---|
| `char` | `CHAR(100)` | CHAR 최대 255 · 코드·식별자·문자열 일시가 들어가는 칸 |
| `varchar` | `VARCHAR(500)` | 1회차 최대 테이블(CHAR 29·VARCHAR 22·DECIMAL 17)이 utf8mb4 행 한도 65,535바이트 안에 드는 상한 |
| `decimal` | `DECIMAL(38,10)` | 정수 28자리 · 소수 10자리(MariaDB 무인자 기본 `DECIMAL(10,0)`은 소수를 버린다) |

### 합성 행 값 규칙 (우선순위)

1. **채택 관계**(`relations[]` 중 `kind: p1` · `accepted: true`만) — 자식 컬럼은 부모 테이블의 생성 행 하나에서 함께 복사한다(복합 조인 키 일관). 부모가 먼저 만들어진다.
2. **주석 코드 열거** — DB 주석에 `1:정상, 2:장애` 같은 쌍이 2개 이상이면 그 값(정의 유래 — 원 코드라 치환값과 어긋날 수 있다 · plans/140 §5-3)
3. **치환 코드값** — `code_samples.yaml`의 `substitution: ok` 값(`exhausted`·`flag`는 값이 없다)
4. **플래그** — 카탈로그 `profile.flag`(Y/N·0/1·T/F)
5. **값 형식** — `profile.formats` 비율 0.95 이상: `date8`(YYYYMMDD) · `datetime14`(YYYYMMDDHHMMSS) · `ipv4`(문서용 대역 192.0.2.0/24·198.51.100.0/24·203.0.113.0/24) · `hostname`(`sim-0001`)
6. **타입·이름 기반** — 문자 = `컬럼명+번호`(길이를 넘으면 `S번호`) · 숫자 = 정수/소수 · 사람 이름·주민번호·전화 형태는 만들지 않는다

기본키(복합이면 묶음)와 유일 부모 컬럼(`unique_parent`가 false가 아닌 관계의 부모 쪽)은 중복 없이 만든다. 유한 값만으로
유일성을 채울 수 없으면 그 테이블은 행이 모자라고 `rows` 출력에 「행 부족」으로 표시된다. 키·관계가 아닌 NULL 허용
컬럼은 약 15%가 NULL이다. 같은 입력·같은 `--seed`면 같은 SQL이 나온다.

## 기동 · 종료 (사용자 실행)

```bash
cd testdata/itam_closed_sim
docker compose up -d                    # generated/의 SQL은 볼륨이 비어 있을 때 첫 기동에만 실행된다
docker compose exec -T itam-closed-sim mariadb -h127.0.0.1 -uitam_sim_ro -pitam_sim_ro_pass INST1 \
  -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='INST1'"
docker compose down -v                  # 종료 + 볼륨 삭제 — generated/를 다시 만들었으면 반드시 -v로 내렸다 올린다
```

| 항목 | 값 |
|---|---|
| 컨테이너 · 볼륨 | `itam_closed_sim_mariadb` · `itam_closed_sim_data` (샌드박스 `itam_mariadb`와 분리) |
| Host / Port | `127.0.0.1` / `3308` (루프백만) |
| Database | `INST1` |
| 조회 계정 | `itam_sim_ro` / `itam_sim_ro_pass` — `GRANT SELECT ON INST1.*`만 |
| root | `itam_sim_root_pass` — 초기화 전용 · MCP에 쓰지 않는다 |

## 로컬 `itam` 대상을 모의 DB로 바꾸기 (사용자 실행 · 직접 고치지 않음)

레지스트리(`config/db_registry.yaml`)·루트 `.env`는 바꾸지 않는다. MCP 서버의 `itam` 소스 연결 문자열만 바꾼다.

1. `mcp_server/.env`의 기존 줄을 적어 둔다(되돌릴 때 쓴다):
   `ITAM_CONNECTION=mariadb://itam_ro:…@localhost:3307/INST1`
2. 그 줄을 다음으로 바꾼다:
   `ITAM_CONNECTION=mariadb://itam_sim_ro:itam_sim_ro_pass@127.0.0.1:3308/INST1`
3. 로컬 MCP 서버(9099)를 재기동한다 — `.env`는 기동 때만 읽는다.
4. 본체 스키마 캐시가 108테이블본인지 확인한다(`.cache/schema/itam_schema.json` · plans/139 §4.2). 2테이블 샌드박스본으로 돌아가 있으면
   「DB 구조」 탭에서 다시 읽거나 W3 빌더(`--install-cache`)로 갱신한다.

**되돌리기**: 1에서 적어 둔 3307 줄로 복원 → MCP 서버 재기동 → 필요하면 `docker compose down -v`. 2테이블 샌드박스 캐시는
`.cache/schema/itam_schema.json.bak-sandbox-20260917`에 있다.

## 검증

`tests/test_testdata/test_plan140_w4_closed_sim.py` — 도커·DB 접속 없이 합성 이름 픽스처로 DDL(인용·NOT NULL·PK·주석 이스케이프·기본
타입)·행(관계 일관·유일·치환값·주석 열거 우선·flag·형식·결정성·NULL)·출력 경로 가드·compose 바인딩을 고정한다. 1회차 반출
원본(`results/itam_bench/20261006-152938/` · gitignore)이 로컬에 있으면 108테이블 DDL 재현도 확인한다. **MariaDB 엔진 실행
(CREATE TABLE 실제 수락)은 사용자가 컨테이너를 띄운 뒤 확인한다.**
