# 211. 솔루션 간 호스트 이름 매칭 — 정규형 일치(L0) 공용화 · `hostname(업무명)` 꼬리 처리 · LIKE는 후보 전용

> **작성일**: 2026-11-03 · **v1.0**
> **상태**: **WIP** — W1~W5 구현(2026-11-04 · 플래그 기본 off) · 계획 끝 모듈 단위 회귀 = 원래 실패 29 · 이번 변경 귀속 0(`범위: 모듈 단위 — 전체 미실행`) · 알려진 위험 2건 교정(2026-11-04 · D-363 부기 3 — `exact` 숫자 사이 구분자 · FQDN 끝 점 값) · 잔여: W0 폐쇄망 확인(칸 등록명/호스트명·괄호 앞 공백·정규형 충돌·행 수) · ITAM P1 반출 뒤 빌더 재실행 · MariaDB 한글 식별자 확인 · 원 `plans/209`(main 선점으로 재부여)
> **계획 시작 SHA**: `914b91fe`
> **회귀 대상 파일**: 계획이 바꾼 파일 42개(가드 교정 4개 포함 — `regress.py --base 914b91fe --files …`)
> **성격**: 행동 변경(D-337 ⑥) — 구조 리팩터와 섞지 않는다. 패키지별 플래그 기본 off = 현행 비트 동일.
> **관련 결정**: **D-363**(이 계획의 정본) · D-348(APM → 폴스타 연결 학습 — ② 확정 연결만 자동 · ⑥ 공용 정규화) · D-224(폴스타 ↔ 자산관리 교차 — ② 값 기반 키 · ㉰ FQDN끼리는 도메인까지 비교 · ④ 모호 제외) · D-188(오진이 미표시보다 나쁘다) · D-061(등록명·호스트명·IP 구분) · D-139·D-274(패키지 경계 — import 0) · D-089(DB별 SQL은 어댑터에) · D-290(업무명 근거 — 후속 연결 후보)
> **패키지**: 본체 `src/domain/`·`src/orchestration/`·`src/db_adapters/` · `noise_gate/` · `mcp_server/` · `apm_gateway/` · `config/db_profiles/itam.yaml`

## 1. 발단

사용자 요청(2026-11-03): *"apm, sms, itam의 hostname은 like로 비교가 되어야 한다. 3가지 솔루션들 간의 이름이 약간씩 다를 수가 있다."* (SMS = 폴스타 · `plans/187` 용어)

검토 뒤 사용자 확정(같은 날):

| # | 질문 | 확정 |
|---|---|---|
| Q1 | LIKE 결과를 자동으로 쓰는가 | **아니다** — LIKE는 후보만 내고 사람이 확정한다(D-348 ② 유지) |
| Q2 | `-`·`_` 차이를 같은 이름으로 보는가 | **본다**(정규형에서 제거) |
| Q3 | 실제 이름 차이 패턴 | **`hostname(업무명)`** 형태로 쓰는 시스템이 하나 있다 → **폴스타 · 반각 괄호 `(`**(사용자 2026-11-03 *"폴스타에 있다. 괄호를 쓴다."*) |

## 2. 실측 — 지금 호스트 이름을 비교하는 곳 (2026-11-03 · `914b91fe`)

| 위치 | 현행 비교 | 이 계획에서 |
|---|---|---|
| `mcp_server/mcp_server/polestar_tools.py:296` `build_anchor_lookup_sql` (HolmesGPT·플레이북 경로 · 호출 `:645`) | `CR.NAME = x OR CR.HOSTNAME = x` — 대소문자 구분 정확 일치 | L0 (W2) |
| `apm_gateway/apm_gateway/application/resolver.py:129-360` | 소문자 + 짧은 이름 · override → `host_name` → `exact` → `prefix` → `regex` | `_host_equal`·`exact`를 L0로 (W3) |
| `src/domain/host_names.py` (D-348 ⑥ 공용) | 소문자·FQDN 첫 레이블·K8s 워크로드·토큰·유사도 | L0 정규형 함수 추가 (W1) |
| `src/domain/entity_key.py` (D-224 ②) | RFC 1123 레이블 판정 → casefold | **`web01(업무)`는 `unknown`이라 키 컬럼 판정(80%)에서 빠진다** — 꼬리 제거 뒤 판정 (W1) |
| `noise_gate/infrastructure/polestar_hostname_resolver.py:158·214` · `src/db_adapters/polestar/entity_probe.py:144` | `IN` + `LIKE 'x.%'` · 토큰 `LIKE '%t%'`(후보) | 소재 프로브는 L0, 토큰 LIKE는 그대로 L3 (W4) |
| `config/db_profiles/itam.yaml` | `entity_keys` **없음** — D-224 키 브리지가 ITAM 쪽에서 동작하지 않는다 | 서버 원장 키 선언 (W5) |
| `src/db_adapters/polestar/prompts.py:471` (text2sql LLM 지시) | `SVR.NAME ILIKE '%x%'` | **범위 밖**(§7) |

## 3. 매칭 규칙

### 3.1 정규형 `canonical_host_key(value)`

순서대로 적용한다. 결과가 빈 문자열이면 키가 아니다(비교하지 않음).

1. 앞뒤 공백 제거
2. **업무명 꼬리 제거** — 첫 `(` 앞부분만 남기고 그 앞 공백을 지운다(`webap01 (인터넷뱅킹)` → `webap01`). 뗀 업무명은 표시용으로 따로 돌려준다(`split_business_suffix`).
3. 소문자 · 끝 `.` 제거
4. IPv4·IPv6이면 정규 표기 그대로 반환(D-224 ②)
5. 레이블마다 `-`·`_` 제거(Q2)
6. 결과가 `[a-z0-9.]`만이 아니면 빈 문자열 — SQL LIKE 조립 대상에서도 빠진다(와일드카드 차단)

### 3.2 같은 호스트 판정 `host_keys_match(a, b)`

- 둘 다 FQDN → **전체 정규형이 같아야 일치**(D-224 ㉰ — 도메인이 다르면 다른 호스트)
- 그 밖 → 첫 레이블이 같으면 일치

### 3.3 단계

| 단계 | 규칙 | 자동 사용 |
|---|---|---|
| L0 정규형 일치 | §3.2 | ✅ **대상이 정확히 1건일 때만** — 2건 이상이면 「모호」로 후보 표시(D-224 ④) |
| L1 IP 일치 | 폴스타 `ipaddress` · ITAM `IP주소내용` · 제니퍼 `hostAddress`(D-355) | ✅ 1건일 때(현행 유지) |
| L2 확정 연결 | D-348 사람 선택 저장소 | ✅ (현행 유지) |
| L3 LIKE 후보 | 정규형 토큰 포함·접두 LIKE | ❌ 후보만 · 사람이 확정(Q1) |

### 3.4 SQL 형태 (DB 쪽 값 정규화 — PostgreSQL·DB2·MariaDB 공통 함수만)

`N(col) = REPLACE(REPLACE(LOWER(TRIM(col)), '-', ''), '_', '')`, 입력 정규형 `n`(첫 레이블 `s`):

- 입력이 단일 레이블: `N(col) = s OR N(col) LIKE 's.%' OR N(col) LIKE 's(%' OR N(col) LIKE 's (%'`
- 입력이 FQDN: `N(col) = n OR N(col) = s OR N(col) LIKE 's(%' OR N(col) LIKE 's (%'` (저장값이 다른 도메인 FQDN이면 불일치 — §3.2)

**괄호 꼬리 분기(`LIKE 's(%'`·`LIKE 's (%'`)는 폴스타 SQL에만 둔다**(Q3 — 꼬리는 폴스타 값에만 있다). ITAM SQL(W5)은 `N(col) = s OR N(col) LIKE 's.%'`(FQDN 입력은 `= n OR = s`)만 쓴다 — 폴스타에서 넘어온 `web01(업무)` 입력은 Python 쪽 §3.1-2가 이미 뗀다. 제니퍼 쪽은 SQL이 없다(게이트웨이 메모리 비교).

`n`은 §3.1-6에 따라 `[a-z0-9.]`만이므로 `_`·`%` 와일드카드가 섞이지 않는다. 컬럼 함수 적용으로 인덱스를 못 탄다 — 대상 테이블 행 수를 W0에서 확인한다.

## 4. Wave

| Wave | 내용 | 대상 | 검증 |
|---|---|---|---|
| **W0** (폐쇄망 · 코드 0) | ~~① 어느 시스템~~ → 폴스타(확인) · 칸이 등록명(`name`)인지 호스트명(`hostname`)인지 ~~② 전각 괄호~~ → 반각(확인) · 괄호 앞 공백 유무 ③ 정규형 충돌 건수(아래 SQL) ④ 대상 테이블 행 수 | 사용자 | 결과를 §8에 기입 · 칸을 몰라도 W2·W4는 두 칸 모두 비교하므로 진행 가능 |
| **W1** | `canonical_host_key`·`split_business_suffix`·`host_keys_match`·`build_canonical_host_condition`(§3.4) — `src/domain/host_names.py`. `entity_key.classify_value`가 업무명 꼬리를 뗀 뒤 판정(플래그 on일 때만) | `src/domain/` · 플래그 `HOST_CANONICAL_MATCH_ENABLED`(본체 · 기본 off) | 벡터 테스트(§5) · off 비트 동일 |
| **W2** | `build_anchor_lookup_sql`을 §3.4로 — `NAME`·`HOSTNAME` 양쪽. 2행 이상이면 첫 행을 고르지 않고 모호로 돌려준다(현행 다건 처리 실측 후 결정) | `mcp_server/` 사본 정규화 + 플래그(기본 off) | DB2·PG 방언 SQL 테스트 · 경계 테스트 |
| **W3** | `resolver._host_equal`·`exact` 규칙을 §3.2로 | `apm_gateway/` 사본 정규화 + 플래그(기본 off) | 기존 resolver 테스트 + 벡터 |
| **W4** | 소재 프로브(`build_host_probe_sql`·`entity_probe.py`)를 §3.4로 · D-348 후보 등급 「일치」를 §3.2로 · 토큰 LIKE(L3)는 변경 없음 | `noise_gate/` · `src/db_adapters/polestar/` | 후보 등급 테스트 |
| **W5** | ITAM `entity_keys` — 서버 원장 `서버호스트명`(compare `canonical`) · `IP주소내용`(ip). 매니페스트에 compare `canonical` 추가 → `build_scope_condition`이 §3.4 조립 | `config/db_profiles/itam.yaml` · `src/domain/entity_key.py` · `src/pipeline/key_bridge.py` | 키 브리지 테스트 · MariaDB 한글 식별자 인용 실측 |

정규화 사본(W2·W3)은 패키지 경계(D-139·D-274 — import 0) 때문에 둔다. **같은 입력 벡터 → 같은 키**를 세 패키지 테스트가 각각 단언한다(벡터 표는 §5를 정본으로 복사).

## 5. 테스트 벡터 (정본)

| 입력 a | 입력 b | 기대 |
|---|---|---|
| `WEBAP01` | `webap01` | 일치 |
| `web-ap01` | `web_ap01` | 일치 |
| `webap01.corp.local` | `webap01` | 일치 |
| `webap01(인터넷뱅킹)` | `webap01` | 일치 · 업무명 `인터넷뱅킹` |
| `webap01 (인터넷뱅킹)` | `WEB-AP01.corp.local` | 일치 |
| `webap01.a.local` | `webap01.b.local` | **불일치**(D-224 ㉰) |
| `webap01` | `webap010` | **불일치** |
| `(인터넷뱅킹)` | 아무거나 | 키 아님 |
| `10.1.2.3` | `10.1.2.3` | 일치(IP) |
| `web%01` | — | 키 아님 · SQL 조립 안 함 |

## 6. 회귀

D-303 — 계획 끝에 `python scripts/regress.py --base 914b91fe --files <바꾼 파일…>` · Wave 중간은 자기 테스트 + `--no-tests`. `mcp_server`·`apm_gateway`는 `../.venv/bin/python -m pytest`.

## 7. 범위 밖

- **text2sql LLM 생성 SQL의 `ILIKE '%x%'`**(`src/db_adapters/polestar/prompts.py:471`) — 사용자가 이름 일부로 서버를 찾는 조회 기능이라 L3 금지를 적용하면 기능이 줄어든다. 이 계획은 **서버 하나를 확정해 다른 시스템과 잇는 경로**만 다룬다.
- ITAM과의 확정 연결 저장소(L2 확장) — L0·L1로 남는 불일치를 W0 뒤에 보고 판단한다.
- 꼬리 업무명을 D-290 업무명 근거로 쓰는 것 — 후속 후보.
- 매뉴얼(D-255) — 화면·사용법 변화 없음(매칭 정확도만 바뀜). 후보 화면 문구가 바뀌면 그때 갱신한다.

## 8. W0 확인 항목 (폐쇄망)

정규형 충돌 확인 SQL(폴스타 PostgreSQL 예 — 괄호 꼬리 제거는 PG `split_part`로 근사):

```sql
SELECT REPLACE(REPLACE(LOWER(TRIM(split_part(split_part(hostname, '(', 1), '.', 1))), '-', ''), '_', '') AS k,
       COUNT(*) AS n
  FROM polestar.cmm_resource
 WHERE resource_type = 'server.Server' AND dtime IS NULL
 GROUP BY 1 HAVING COUNT(*) > 1;
```

| 항목 | 결과 |
|---|---|
| `hostname(업무명)` 위치 | **폴스타**(2026-11-03 사용자) · 칸(`name`/`hostname`) 미확인 — 위 SQL을 `name` 칸에도 돌린다 |
| 괄호 종류·공백 | **반각 `(`**(2026-11-03 사용자) · 괄호 앞 공백 미확인 — 두 형태 모두 처리하므로 구현 차단 아님 |
| 정규형 충돌 건수(시스템별) | (미확인) |
| 대상 테이블 행 수 | (미확인) |
