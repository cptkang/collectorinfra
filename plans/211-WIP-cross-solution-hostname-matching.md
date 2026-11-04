# 211. 솔루션 간 호스트 이름 매칭 — 정규형 일치(L0) 공용화 · `hostname(업무명)` 꼬리 처리 · LIKE는 후보 전용

> **작성일**: 2026-11-03 · **v1.0**
> **상태**: **WIP** — W1~W5 구현(2026-11-04 · 플래그 기본 off) · 계획 끝 모듈 단위 회귀 = 원래 실패 29 · 이번 변경 귀속 0(`범위: 모듈 단위 — 전체 미실행`) · 알려진 위험 2건 교정(2026-11-04 · D-363 부기 3 — `exact` 숫자 사이 구분자 · FQDN 끝 점 값) · 잔여: W0 폐쇄망 확인(칸 등록명/호스트명·괄호 앞 공백·정규형 충돌·행 수) · ITAM P1 반출 뒤 빌더 재실행 · MariaDB 한글 식별자 확인 · 원 `plans/209`(main 선점으로 재부여) · **W6 구현(2026-11-04 · §9 — 연계 생성 노드 주소 우선 · ITAM은 마지막 · 알람 3″순 · 새 플래그 없음 · 수용 1~9 충족 · W6 끝 모듈 단위 회귀 원래 실패 0 · 이번 변경 귀속 0(`범위: 모듈 단위 — 전체 미실행`) · D-362 부기 4 · D-363 부기 4 · 결과 §9.7)** · W6 잔여: 게이트웨이 작업 승격 실 게이트웨이 확인 · W0 ⓐ~ⓓ
> **계획 시작 SHA**: `914b91fe`
> **회귀 대상 파일**: 계획이 바꾼 파일 42개(가드 교정 4개 포함 — `regress.py --base 914b91fe --files …`) · W6 27개(`regress.py --base 8608c8ee --files …` — 목록 §9.7)
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
| **W6** (2026-11-04 추가 · §9) | APM↔SMS 연계(`plans/210`·D-362)의 비동기 생성·관리자 「다시 찾기」·스캔·알람 경로를 **노드 주소 → VM 일치 → ITAM** 순으로 맞추고, SMS hostname 색인을 정규형(L0)으로 | `noise_gate/` · `src/api/routes/admin_apm_host_map.py` | §9.5 |

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

## 9. W6 — APM↔SMS 연계: 노드 주소 우선 · ITAM은 마지막 (2026-11-04 추가)

사용자 지시(2026-11-04): *"apm sms 연계 기능에서 apm의 인스턴스 이름을 이용하여 host address를 조회하고 host address를 이용하여 폴스타의 서버를 찾을 수 있지만 현재 실패로 나오고 있다. 현재는 아마 itam을 기준으로만 찾기 때문인것으로 보인다. 확인하여 기능을 수정할 수 있도록 개선 계획을 211번 계획에 추가하라."*

대상 기능: `plans/210`·D-362 연계 정보 DB(관리자 탭 「APM·SMS 연계」 · `meta.hints.polestar_link` `basis=apm_map`). 이 계획(D-363)과는 ④(§9.4 — SMS hostname 색인)에서 겹친다.

### 9.1 실측 — 사용자 추정이 맞다 (2026-11-04 · `8608c8ee`)

연계 행을 만드는 경로 4개 중 **2개는 ITAM 사슬만** 돌리고, 나머지 2개도 노드 주소를 「이미 들고 있을 때」만 쓴다. 인스턴스 이름으로 노드 주소(`hostAddress`)를 새로 묻는 경로는 연계 생성 쪽에 없다(채팅 보기 `apm.node_address`·`restore_apm_block`에만 있다).

| # | 경로 | 위치 | 현행 | 결과 |
|---|---|---|---|---|
| ① | 알람 miss → 비동기 생성(`itam_request`) | `noise_gate/application/apm_host_map_scan.py:480` `handle_itam_request` | 도메인을 `apm_instance_map(domain_id)`로 다시 읽고(행에 `host_address`가 있어도) **버리고** `resolve_itam_chain`만(`:552`) → domain 행 | ITAM 업무↔서버 근거가 약해(`plans/210` F7 「조건부(약함)」) 대부분 **`state=failed`·`basis=itam`** — 화면의 「실패」 |
| ② | 관리자 「다시 찾기」(domain 행) | `src/api/routes/admin_apm_host_map.py:453` `_proposal_for` (`:501`) | domain 범위는 **ITAM 사슬만** | 같은 실패 재생산 |
| ③ | 관리자 「다시 찾기」(instance·workload 행) | 같은 파일 `:541` | `plan_scan_proposals` — **스캔 스냅샷의** 노드 주소만 · 지금 묻지 않음 | 스냅샷에 노드 주소 `ok`가 없으면 `no_match` |
| ④ | 사전 스캔 일괄 생성 | `noise_gate/domain/apm_host_map_scan.py:189` · `:231` | 노드 주소 스캐너가 쓸 수 있을 때(`ok`·`partial`)만 node_address 규칙 · 노드 IP가 SMS 색인에 없으면 직접 조회 폴백 없음 · 못 이은 사유 집계 없음 · 하나도 못 이으면 ITAM | 노드 주소 스캐너가 `disabled`·`failed`면 ITAM만 → `failed` |
| ⑤ | 알람 고속 경로 3·3'순 | `noise_gate/application/apm_host_map_lookup.py:290` | **페이로드에 실려 온** `host_address`(`ok`)만 · 없거나 `not_found`·`error`면 3순 통째 생략 → 7순 miss → ①로 | 폴러가 알람 시점에 노드 주소를 못 실은 알람은 ITAM으로만 감 |

**실패가 굳는 고리**: ①이 domain 행을 `failed`로 쓰면 ⓐ 다음 알람의 7순 사유가 `apm_map_failed`가 되고(`decide_from_map`) ⓑ `next_retry_at`(`NOISE_APM_HOST_MAP_RETRY_HOURS` 24)이 ① 전체를 막는다(`:513` — 재시도 대기가 ITAM 단계가 아니라 **처리기 입구**에 있다). 노드 주소로 이을 수 있게 고쳐도 24시간 동안 다시 시도하지 않는다.

부수 결함:
- 인벤토리 행은 `apm_instance_map`이 준 `host_address`를 싣고도 상태를 빈칸으로 둔다(`noise_gate/infrastructure/apm_host_map_scanners.py:240`) — 노드 주소 스캐너가 실패하면 그 값이 규칙에 쓰이지 않는다(상태를 모르므로 쓰지 않는 현행이 맞다 · W6에서도 유지 · 대신 ①·②에서 노드 주소 도구로 상태를 받는다).
- SMS hostname 색인 키가 `normalize_hostname`(`noise_gate/domain/apm_host_map.py:668` · 사본 `apm_host_map_cache.py` `sms_by_host`) — 폴스타의 `hostname(업무명)` 값(D-363 부기 1)은 VM 호스트명 일치(vm_exact)에서 영원히 빠진다. W4가 소재 프로브만 정규형으로 바꾸고 이 색인은 범위에 없었다.

### 9.2 목표 순서 — 모든 생성 경로 공통

| 순 | 규칙 | 근거 칸 | 자동 사용 |
|---|---|---|---|
| N1 | 인스턴스의 노드 주소(`ok`)가 **없으면 게이트웨이에 묻는다** — `apm_node_addresses(source_ids=[src], domain_ids=[dom], instance_ids=[…])` 1회(W9 호출 예산 안) | `status`·`host_address`·`pod` | — |
| N2 | 노드 IP → SMS IP 색인(대표 IP + NIC IP) **1대** | `basis=node_address` | ✅ instance 행 + workload 합집합 |
| N3 | 색인 miss(스냅샷 없음 포함) → `node_address_link` 직접 조회(전 존 정확 일치 · 알람 3'순과 같은 함수) | `basis=node_address` | ✅ 1대일 때 |
| V | 파드가 아닌 인스턴스: `ip`·`host_name` → SMS 색인 → miss면 `vm_exact_link` | `basis=vm_exact` | ✅ 1대 · 2대 이상 `ambiguous` |
| I | **도메인의 어떤 인스턴스도 N·V로 잇지 못했을 때만** ITAM 사슬 | `basis=itam` | 현행(추정) |

- 노드 주소 상태는 `ok`만 쓴다(D-355 ⑦ — `stale` 자동 연결 금지 · `NODE_ADDRESS_LINK_STATUSES` 그대로).
- 2대 이상은 고르지 않는다(D-224 ④ · D-188).
- 게이트웨이 도구가 없으면(`JENNIFER_NODE_ADDRESS_ENABLED` off → `Unknown tool`) N1은 `node_address_disabled` 사유로 건너뛰고 V·I로 간다 — 침묵 금지(사유를 evidence에).

### 9.3 변경

| 축 | 대상 | 변경 |
|---|---|---|
| **A** 비동기 생성 | `handle_itam_request` → 처리 본체를 `handle_link_request`로(메시지 타입 `itam_request`는 그대로 받는다 — 스트림 호환) | 도메인 1개에 §9.2 전 순서. 노드 주소는 `apm_node_addresses(domain_ids=[dom])` 1회로 받아 `apm_instance_map` 행에 `instance_key`로 합친다(스캔 `_store_inventory`와 같은 병합 — 공용 함수로 뽑는다). 이은 행은 `plan_scan_proposals`(순수 함수) 재사용으로 instance·workload 제안을 만들고 `apply_auto_many`. **전제 완화**: SMS 스냅샷이 없으면 N3·V 직접 조회로 진행 · ITAM 스냅샷이 없으면 I만 건너뜀(지금은 셋 중 하나만 없어도 `no_inventory`로 전부 중단). **재시도 대기는 I 단계에만** 적용 — N·V는 매번 시도(직접 조회 예산 `NOISE_APM_HOST_MAP_LIVE_BUDGET_SECONDS` 안 · 처리기는 API 프로세스라 알람 지연과 무관). |
| **B** 관리자 다시 찾기 | `_proposal_for` | domain 행 = A와 같은 처리 본체(미리보기는 저장 없이 제안만 · `apply=true`만 저장 — 현행 규칙). instance·workload 행 = 스냅샷 노드 주소가 `ok`가 아니면 그 인스턴스만 `instance_ids`로 N1 → N2·N3. 응답에 단계별 결과(`tried`)를 싣는다. |
| **C** 스캔 | `plan_scan_proposals` · `_generate` | 노드 IP 색인 miss·2대 이상·노드 주소 상태 비`ok`를 `link_summary`에 센다(`node_ok`·`node_ip_unmatched`·`node_ip_ambiguous`·`node_not_ok`) — 실패 사유가 실행 기록에 보이게. 스캔에서는 N3 직접 조회를 하지 않는다(SMS 스냅샷이 전 존을 이미 읽었으므로 색인 miss = 미등록 · 존이 `unchecked`면 그 존만 사유). |
| **D** 알람 3''순 | `resolve_map_link` 3순 앞 | 페이로드 노드 주소가 없거나 `ok`가 아니고(`missing`·`not_found`·`error`·`stale`) 파드(`apm.pod is True` 또는 워크로드 키 성립)이며 `apm.instance_id`가 있으면, 워커가 `apm_node_addresses(instance_ids=[id])` 1회(3'·5와 **같은 예산**) → `ok`면 3·3'순으로. 게이트웨이 클라이언트는 `build_apm_gateway_client`(MCP 경계만 · D-274). (§9.6 Q1 확정) |
| **E** 실패 표시 정리 | 저장소 쓰기 규칙 · `decide_from_map` · 관리 목록 | 같은 도메인에 N·V로 이은 auto instance/workload 행이 생기면 그 도메인의 auto·미확인 domain 행(`failed`·`ambiguous`)은 지우지 않고 남기되 「검토 필요」·7순 사유(`apm_map_failed`)에서 뺀다 — 노드 주소로 이어진 도메인을 「실패」로 보이지 않게. evidence에 `tried`(`node_address: ok 3/5 · unmatched 2` · `vm_exact: -` · `itam: failed`)를 남겨 관리자가 어디서 끊겼는지 본다. |
| **F** 정규형 색인(D-363 연결) | `build_sms_index` · `ApmHostMapCache.sms_by_host`·사본 재구성 · `vm_exact_match` 호출부 | `HOST_CANONICAL_MATCH_ENABLED` on이면 hostname 색인 키를 `canonical_host_key`로(§3.1 — 업무명 꼬리 제거·`-`·`_` 제거) · FQDN끼리는 전체 일치(§3.2). off = 현행 키 비트 동일. 사본 키 공간이 바뀌므로 플래그 전환 시 사본 전체 재구성(기동 재구성 경로 재사용). |

플래그: 새 플래그 없음 — A·B·C·E는 `NOISE_APM_HOST_MAP_AUTO_ENABLED`(기존 · 기본 off) 안의 동작 교정, D도 같은 플래그 안, F는 D-363 `HOST_CANONICAL_MATCH_ENABLED`. **두 플래그 off = 현행 비트 동일**(기존 plan210 테스트 무수정 통과로 고정).

### 9.4 수용 기준 (테스트로 고정 · 가짜 게이트웨이·가짜 실행기)

1. 알람 miss → `itam_request` · 도메인 인스턴스 3개 중 2개 노드 주소 `ok`(폴스타 1대씩 일치) → instance 행 2·workload 행 1 `basis=node_address` `active` · **ITAM 사슬 호출 0** · domain 행 미생성.
2. 같은 상황에서 노드 IP가 SMS 스냅샷에 없음 → `node_address_link` 직접 조회로 이음(가짜 프로브 호출 단언).
3. 노드 주소 도구 미등록 → evidence `node_address_disabled` · VM 일치 → ITAM 순으로 진행.
4. 기존 domain 행이 `failed`·`next_retry_at` 미래 → N·V는 실행되어 instance 행 생성 · ITAM만 건너뜀 · 다음 알람 2순 hit · 7순 사유에 `apm_map_failed` 없음.
5. 관리자 domain 행 「다시 찾기」 미리보기 → `basis=node_address` 제안 · 저장 0 · `apply=true` → 저장.
6. 스캔 실행 기록 `link_summary`에 노드 주소 사유 카운터.
7. (D) 페이로드 `host_address_status=not_found`인 파드 알람 → 노드 주소 도구 1회 → 3순 linked · 예산 초과 시 다음 순(현행 예산 로그).
8. (F) 폴스타 hostname `web01(인터넷뱅킹)` · APM `host_name=WEB-01` → 플래그 on 일치 · off 불일치(현행).
9. 플래그 off(두 개) → plan210 기존 테스트·스냅샷 비트 동일.

### 9.5 Wave 범위·회귀

| 단계 | 대상 파일(예상) | 검증 |
|---|---|---|
| W6-1 A·E | `noise_gate/application/apm_host_map_scan.py` · `noise_gate/domain/apm_host_map_scan.py` · `noise_gate/domain/apm_host_map.py` · `noise_gate/infrastructure/apm_host_map_scanners.py`(병합 공용) · `noise_gate/infrastructure/apm_host_map_repository.py`(필요 시) | 수용 1~4 |
| W6-2 B | `src/api/routes/admin_apm_host_map.py` · `src/static/js/admin-apm-host-map.js`(`tried` 표시 시) | 수용 5 · 화면이 바뀌면 매뉴얼(D-255) |
| W6-3 C | `noise_gate/domain/apm_host_map_scan.py` · `noise_gate/application/apm_host_map_scan.py` | 수용 6 |
| W6-4 D | `noise_gate/application/apm_host_map_lookup.py` · `noise_gate/infrastructure/apm_gateway_client.py`(`apm_node_addresses` 메서드) | 수용 7 |
| W6-5 F | `noise_gate/domain/apm_host_map.py` · `noise_gate/infrastructure/apm_host_map_cache.py` | 수용 8 · §5 벡터 재사용 |

- 회귀: D-303 — W6 끝 `python scripts/regress.py --base <W6 시작 SHA> --files <W6이 바꾼 파일…>` 1회. 게이트웨이 코드는 바꾸지 않는다(기존 `apm_node_addresses` `instance_ids`·`domain_ids` 인자 재사용 · W8·W9 계약).
- 결정 기록: 구현 시 D-362 부기(생성 순서 노드 주소 우선 · 재시도 대기 ITAM 한정 · 알람 3''순) + D-363 부기(SMS hostname 색인 정규형).
- W0 폐쇄망 확인(코드 전 · 사용자): ⓐ 「실패」 행의 `basis`가 `itam`인지(관리자 탭 필터 `basis=itam`·`state=failed`) ⓑ 로그 `APM·SMS 연계 ITAM 요청 처리 … state=failed`·`apm_map_lookup … order=7` 빈도 ⓒ 최근 스캔 실행 기록의 `scanners.node_address.status`·`link_summary.skipped` ⓓ 실패 도메인 하나에 대해 채팅 「<인스턴스> 파드가 도는 노드 주소」 → 노드 IP가 폴스타 서버/NIC IP로 1대 일치하는지(`plans/210` W0-2). ⓓ가 일치하면 이 Wave로 해소되는 경우다.

### 9.6 확정 (2026-11-04 · 사용자 *"권고에 맞게 정리하고 구현하라."*)

| # | 질문 | 확정 |
|---|---|---|
| Q1 | 알람 처리(워커) 중에 게이트웨이에 노드 주소를 직접 물을 것인가(D) — 알람 1건 판정에 최대 예산 5초가 더해질 수 있다 | **묻는다**(확정) — 3'·5와 같은 예산을 나눠 써서 상한은 늘지 않고, 묻지 않으면 그 알람은 ITAM 추정으로만 가서 이번 「실패」가 남는다. 거절하면 D만 빼고 A가 다음 알람부터 잇는다. |
| Q2 | ITAM 사슬을 「아무것도 못 이었을 때만」으로 내리는 데 이견이 있는가(일부 인스턴스만 이어진 도메인은 ITAM을 돌리지 않음) | **현행 스캔 규칙(`not linked_any`)과 같게**(확정) — 하나라도 이어지면 ITAM을 돌리지 않는다. |

### 9.7 결과 (2026-11-04 · W6 시작 SHA `8608c8ee` · 커밋 없음)

**구현 요지**
- **A 비동기 생성** — `handle_link_request`(`handle_itam_request`는 별칭 · 메시지 타입 `itam_request` 그대로)가 `plan_domain_link`(계획 · 쓰기 0) → `apply_domain_link`(저장)로 §9.2 순서를 돈다. N1은 `fetch_node_addresses`(`apm_node_addresses` 1회 · 50개 묶음 · 사유 `ok`·`node_address_disabled`·`node_address_error:<kind>`), 병합은 스캐너와 같은 `parse_node_addresses`·`merge_node_addresses`. 재시도 대기는 I 단계만(`tried.itam=skipped:retry_wait`). evidence `tried`에 단계별 결과.
- **B 관리자 다시 찾기** — 자동 생성 on: domain 행은 A와 같은 본체(게이트웨이 인스턴스 읽기 실패 시 스냅샷 · `tried.instances=snapshot:<사유>`), instance·workload 행은 스냅샷 노드 주소가 `ok`가 아니면 그 인스턴스만 N1. 게이트웨이 읽기 상한 `_RESOLVE_READ`(대기 5 · 호출 10 · 마감 15초). 응답에 `proposals`·`tried` · 화면에 단계 줄·제안 목록(`textContent`만). 자동 생성 off: 종전 스냅샷 경로(`_legacy_proposal_for`) · 응답 키 불변 · 게이트웨이·직접 조회 0.
- **C 스캔** — `link_summary`에 `node_ok`·`node_ip_unmatched`·`node_ip_ambiguous`·`node_not_ok` · 화면은 0이 아닌 카운터만.
- **D 알람 3″순** — 자동 생성 on · 정수 `instance_id` · 파드 · 페이로드 노드 주소 비`ok`일 때 `apm_node_addresses(instance_ids=[id])` 1회(3′·5와 같은 예산). `wait_seconds = max(0, min(남은 예산, 클라이언트 호출 상한) − 1초)`로 게이트웨이 작업 승격 · 작업 핸들은 읽지 않고 `job_handle` 사유로 다음 순 · 예외는 `node_unchecked`. 되적재 evidence `node_address_from=3''`.
- **E 실패 표시 정리** — N·V로 이은 도메인의 auto·미확인 domain 행(`failed`·`ambiguous`)에 `evidence.superseded`(지우지 않음 · 버전 조건 UPDATE · 이미 달린 행은 다시 쓰지 않음) → 「검토 필요」·7순 사유·사본에서 제외. ITAM이 같은 결과로 다시 쓰면 해제.
- **F 정규형 색인** — `HOST_CANONICAL_MATCH_ENABLED` on이면 `build_sms_index`·`RedisApmHostMapCache(canonical=)`·`resolve_itam_chain(canonical=)`의 hostname 키를 `canonical_host_key`로(본체·워커 기동 시 1회 해석 · 기동 사본 재구성).
- **⑤ 보강(verifier Major 교정)** — 관리자 행(manual·verified)에 같은 제안이 `scan_suggests`로 이미 있으면 `WRITE_SKIP`(요청마다 `version`이 올라 관리자 저장이 `version_conflict`로 거절되던 것).
- 매뉴얼(D-255): 관리자 A-90 갱신 — 진단 순서 3·7순(3″순 조건 포함) · `RETRY_HOURS`는 ITAM 단계만 · `HOST_CANONICAL_MATCH_ENABLED` 행 · 노드 주소 카운터 · 다시 찾기 단계 줄·자동 생성 off 동작 · 대체된 실패 행 · `src/static/manual/*` 재생성 · `tests/test_manual` 통과.

**수용 기준(§9.4)**

| # | 결과 | 테스트 |
|---|---|---|
| 1 | 충족 | `test_plan211_w6_link.py::test_node_address_links_instances_without_itam` · 이음매 `test_plan211_w6_verify.py::test_seam_link_rows_through_real_cache_to_alarm_lookup` |
| 2 | 충족 | `test_index_miss_uses_live_node_lookup` · `test_live_lookup_absent_or_over_budget_moves_on` · `test_vm_live_lookup_for_non_pod_index_miss` |
| 3 | 충족 | `test_node_tool_unregistered_goes_vm_then_itam` · `test_fetch_node_addresses_reasons` · 관리자 `test_domain_node_tool_unregistered_goes_vm_then_itam` |
| 4 | 충족 | `test_retry_wait_blocks_only_itam_and_supersedes` · `test_superseded_marker_*` · `test_review_reasons_skip_superseded_rows` · `test_review_sql_excludes_superseded` |
| 5 | 충족 | `test_plan211_w6_admin.py::test_domain_preview_proposes_node_address_and_saves_nothing` · `test_domain_apply_writes_all_caches_supersedes_and_audits` · `test_instance_row_asks_node_address_for_that_instance_only` |
| 6 | 충족 | `test_scan_link_summary_counts_node_reasons` · `test_ui_scan_summary_shows_nonzero_node_counters_only` |
| 7 | 충족 | `test_plan211_w6_alarm.py::test_not_found_pod_links_via_index_after_gateway` · `test_budget_exceeded_goes_to_next_order` · `test_shared_budget_upper_bound_not_raised` · `test_plan211_w6_verify.py::test_alarm_wait_seconds_is_capped_by_client_call_timeout` · 이음매 `test_seam_alarm_learn_is_accepted_by_consumer_and_hits_next` |
| 8 | 충족 | `test_sms_index_canonical_key` · `test_canonical_fqdn_rule_and_off_identity` · `test_cache_sms_by_host_canonical` · `test_itam_chain_canonical_hostname` |
| 9 | 충족 | plan210 기존 테스트 통과(W6 의도 변경 5건 제외 — 아래 이탈 ①) · `test_plan_scan_proposals_defaults_are_unchanged` · `test_auto_off_makes_no_gateway_call` · `test_auto_off_preview_is_legacy_snapshot_shape` · `test_plan211_w6_verify_admin.py::test_auto_off_domain_preview_makes_no_gateway_or_live_calls` |

신규 테스트 74건(`noise_gate/tests/test_plan211_w6_link.py` 27 · `…_alarm.py` 26 · `tests/test_api/test_plan211_w6_admin.py` 13 · verifier `noise_gate/tests/test_plan211_w6_verify.py` 5 · `tests/test_api/test_plan211_w6_verify_admin.py` 3).

**회귀(D-303)**: W6 끝 `regress.py --base 8608c8ee --files <W6 25개>` 1회 — 통과 3,032 · 실패 0 · 에러 0 · 건너뜀 1 · 실패 귀속 없음 · 정적 게이트 전부 통과 · `범위: 모듈 단위 — 전체 미실행`(권고 블록 없음). 교정 라운드(verifier Major 1·Minor 3) 뒤 교정 파일 4개 모듈 선택분 재실행 — 통과 791 · 실패 0 · `범위: 모듈 단위 — 전체 미실행`. W6 테스트 파일 교차 묶음 131 통과.

**이탈**
1. plan210 기존 테스트 5건을 고쳤다(§9.3 「무수정 통과」 이탈) — 재시도 대기를 처리기 입구에서 ITAM 단계로 옮긴 ⑬ 개정(Q 확정 범위)과 스냅샷 없는 요청의 진행이 그 테스트들이 단언하던 동작 자체다: `test_plan210_w5_scan.py::test_itam_request_without_snapshot_writes_nothing` · `test_plan210_fix_round.py` before_retry·after_retry_or_without_row·retry_wait_is_acked_by_consumer · `test_plan210_verify_fast_path.py::test_itam_request_respects_next_retry_at`. verifier가 약화 아님으로 판정. 두 플래그 off 경로를 단언하는 테스트는 무수정.
2. ITAM이 같은 결과로 domain 행을 다시 쓰면(`WRITE_SAME`) superseded 표지를 명시 해제한다(계획에 없던 규칙 — 다시 실패 행으로 보이게).
3. B 게이트웨이 인스턴스 읽기 실패 시 스냅샷으로 진행(사유는 `tried.instances`) · 다시 찾기 읽기 상한을 비동기 생성보다 좁게(15초) 두었다.

**W6 회귀 대상 파일(27)**: `noise_gate/application/apm_host_map_lookup.py` · `noise_gate/application/apm_host_map_scan.py` · `noise_gate/application/nodes/investigation_trigger.py` · `noise_gate/domain/apm_host_map.py` · `noise_gate/domain/apm_host_map_itam.py` · `noise_gate/domain/apm_host_map_scan.py` · `noise_gate/infrastructure/apm_gateway_client.py` · `noise_gate/infrastructure/apm_host_map_cache.py` · `noise_gate/infrastructure/apm_host_map_repository.py` · `noise_gate/infrastructure/apm_host_map_scanners.py` · `noise_gate/tests/plan210_w5_fakes.py` · `noise_gate/tests/test_plan210_fix_round.py` · `noise_gate/tests/test_plan210_verify_fast_path.py` · `noise_gate/tests/test_plan210_w5_scan.py` · `noise_gate/tests/test_plan211_w6_alarm.py` · `noise_gate/tests/test_plan211_w6_link.py` · `noise_gate/tests/test_plan211_w6_verify.py` · `scripts/manual/content/admin.md` · `scripts/manual/features.yaml` · `src/api/routes/admin_apm_host_map.py` · `src/api/server.py` · `src/static/js/admin-apm-host-map.js` · `src/static/manual/admin.html` · `src/static/manual/help_index.json` · `src/static/manual/user.html` · `tests/test_api/test_plan211_w6_admin.py` · `tests/test_api/test_plan211_w6_verify_admin.py`

**잔여**
- 폐쇄망 W0 ⓐ~ⓓ(§9.5) — 특히 ⓓ 노드 IP ↔ 폴스타 서버/NIC IP 1대 일치.
- 게이트웨이 작업 승격(`wait_seconds`)의 실 게이트웨이 확인 — 가짜 게이트웨이로만 단언.
- 소비자 learn 경로(알람 3″·3′ 되적재)는 superseded 표지를 달지 않는다 — 다음 연계 요청·스캔에서 정리.
- 워커가 3″순 알람마다 게이트웨이 클라이언트를 새로 만든다(연결 재사용 없음 · 알람 빈도가 높으면 검토).
- (W6 이전부터) 관리 API `_resolve`의 409 거절이 `denied` 감사로 남지 않는다.
