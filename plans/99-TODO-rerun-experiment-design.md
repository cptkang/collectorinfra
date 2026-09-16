# 99. 재측정 실험 설계 — 무엇을 답하려고 10시간을 쓰는가

> **작성일**: 2026-09-16
> **성격**: 실험 설계 · **상태: 설계(실행 전 · 사용자 승인 대기)** — 파일명 `-TODO`
> **선행 계획**: `plans/96`(분석 정본 · W2 가 이 문서다) · `plans/94` §15~§18(하네스 · X-1~X-4·Y·O·V21~V27) · `plans/98`(제품 수정 · CU-1~CU-18)
> **관련 결정**: **D-218**(측정 신뢰성 계약) · **D-220**(게이트 4건 확정 후속) · D-217·D-216(하네스) · **D-127**(과금 승인) · D-211 ⑪(내부망 승인 면제) · D-003(읽기 전용)
> **▶ 돌리는 방법만 필요하면 §3.5 「실행 가이드」 한 절만 읽으면 된다** — **`.env` 사전 설정** · 0~6단계 명령 · **Windows 편**이 그 안에 있다. §0~§3.4 는 설계 근거, §4~§8 은 점검·판정·제약이다.
> Windows 일반 준비(venv·인코딩·포트 제외 대역·ACL·`ibm-db`)는 **`plans/94` 부록 A** 가 정본이고, §3.5 의 Windows 편은 **이 실험에 고유한 것만** 다룬다.
> **실측 기준**: 소요·턴 수는 run `20260915-131903` 의 `raw.jsonl` **유효 280턴**을 직접 집계했다. 무효 103턴은 분모에서 뺐다.

---

## 0. 이 실험이 답하는 것 · 답하지 못하는 것

**답하는 것 5가지**

| # | 질문 | 판정 방법 |
|---|---|---|
| 1 | **측정이 이제 신뢰할 수 있나** | 무효 턴 0건 · 자동 판정률 34% → 60% 이상 |
| 2 | **랜딩한 수정이 실제로 고쳤나** | CU-1·CU-2·CU-12·CU-3·CU-16 대응 시나리오가 합격으로 넘어가는가 |
| 3 | **허위 불합격이 사라졌나** | `plans/96` §3 의 14건이 합격 또는 단언 교정으로 소멸 |
| 4 | **조용한 오답이 남았나** | P-1·P-3 전건 소멸(반복 ≥3) · 신규 `silent_wrong` 0 |
| 5 | **성능 레버가 어디 있나** | `llm_calls`·`tokens` 표본 > 0 → *"느린 것"* 과 *"여러 번 부르는 것"* 분리 |

**답하지 못하는 것(설계상 — 기대하지 말 것)**

- **정본 1단(`deep_agent`) 경로** — 지난 run 은 2단으로 강등돼 돌았다(`degraded_reason=flag_off`). **이번에도 폐쇄망 `.env` 가 그대로면 같은 단으로 돈다.** §4 E-0 을 먼저 확인할 것
- **G-5·G-6·G-8·G-9·G-10 미결 항목** — 이 실험은 그 답을 주지 않는다(§6)
- **회귀 비교** — 지난 run 은 D-216·D-217 이전이라 **노드 지연 의미가 다르고**(D-217 주의 ⑥) 무효율 26.9% 다. **이번 run 이 기준선이 된다**

---

## 1. 실험 3종 — 비용 순서대로

> **비싼 것을 먼저 돌리지 않는다.** E-1·E-2 가 E-3 의 전제를 바꿀 수 있다.

| ID | 실험 | 비용 | 무엇을 푸는가 | 선행 |
|---|---|---|---|---|
| **E-1** | **DB 단순 조회 2건** | 수 분 · 읽기 전용 · LLM 0 | **G-5 · G-8** 게이트 | 폐쇄망 DB 접근 |
| **E-2** | **표적 재현 3건**(`--only` 1건씩) | 약 10분 | **조사 J-1·J-2·J-3** | 로그 레벨 상향 |
| **E-3** | **전 시나리오 재측정** | **약 8~11시간** | §0 의 질문 1~5 | W1-c 랜딩 · E-1·E-2 결론 |

### E-1 — DB 조회 2건 (G-5·G-8)

읽기 전용 SQL 2개다. 결과만 있으면 게이트 2건이 닫힌다.

```sql
-- G-5: LOB 속성의 stringvalue NULL 비율. 0% 면 stringvalue 단독이 안전하고,
--      0% 가 아니면 COALESCE 가 필요하다(= B-09 단언이 과하다).
SELECT COUNT(*) AS total,
       SUM(CASE WHEN stringvalue IS NULL THEN 1 ELSE 0 END) AS null_cnt
FROM   polestar.core_config_prop
WHERE  name = 'OSParameter';

-- G-8: 은행존 장비명에 LIKE 와일드카드(_ %)가 있는가. 있으면 OR 3분기에 ESCAPE 절이 필요하다.
SELECT COUNT(*) AS wildcard_names
FROM   POLESTAR.cmm_resource
WHERE  resource_type = 'server.Server' AND dtime IS NULL
  AND  (name LIKE '%\_%' ESCAPE '\' OR name LIKE '%\%%' ESCAPE '\');
```

> G-5 는 `polestar_cm_gp`(PostgreSQL), G-8 은 `polestar_b0`(DB2)다. **G-8 은 0건이면 그대로 두면 되고, 1건 이상이면 CU-1 의 OR 3분기에 `ESCAPE` 를 추가한다.**

### E-2 — 표적 재현 3건 (J-1·J-2·J-3)

**전 스위트가 아니다.** 시나리오 1건씩, 로그를 켜고 돈다.

| 조사 | 명령 | 봐야 할 것 |
|---|---|---|
| **J-1** | `--only H-10` | 월 피벗이 **결정적 조립에 진입조차 못 한 것**인지 **진입 후 폴백**인지. 폴백이면 사유가, 미진입이면 판정 조건이 원인이다 |
| **J-2** | `--only B-06` | 455초를 어느 경로로 돌았고 **왜 전체 타임아웃 가드에 안 걸렸는지**(`query.py` 가드 5지점 대조) |
| **J-3** | `--only G-01` | 승계 실패가 **승격 경로 부재**인가 **이번 턴 파싱의 덮어쓰기**인가(턴2→턴3 `previous_entities`·top-level hostname) |

**셋 다 실 LLM 을 탄다** — 폐쇄망 `fabrix` 라 승인은 면제지만(D-211 ⑪) **시간·서버 부하는 실재한다.** 합계 약 10분.

### E-3 — 전 시나리오 재측정

§2~§5 가 이 실험의 설계다.

---

## 2. E-3 규모와 예산 — 실측 기반

### 2.1 지난 run 의 군별 실측 (유효 280턴)

| 군 | 시나리오 | 턴 | 소요(h) | 턴당(분) |
|---|---:|---:|---:|---:|
| K 부하 | 10 | 71 | 1.75 | 1.5 |
| **R1 복합** | 10 | 33 | **1.66** | **3.0** |
| L 유사어 | 32 | 32 | 0.91 | 1.7 |
| R2 오용 | 17 | 35 | 0.71 | 1.2 |
| B 구성 | 12 | 12 | 0.36 | 1.8 |
| A 라우팅 | 12 | 12 | 0.33 | 1.6 |
| C 성능 | 13 | 13 | 0.29 | 1.3 |
| H 폼필 | 17 | 17 | 0.28 | 1.0 |
| E 복합 | 7 | 7 | 0.27 | 2.3 |
| G 멀티턴 | 4 | 8 | 0.25 | 1.8 |
| D 알람 | 6 | 6 | 0.20 | 2.0 |
| I 폼필HITL | 8 | 9 | 0.19 | 1.3 |
| F 존HITL | 9 | 15 | 0.17 | 0.7 |
| J 가드 | 10 | 10 | 0.16 | 1.0 |
| **계** | | **280** | **7.52** | 1.6 |

**R3·R4(48+48턴)는 한 번도 측정된 적이 없다** — 예산에 R2 턴당 1.2분을 적용하면 **약 +2.1h**.

### 2.2 이번 run 의 증감 요인

| 요인 | 방향 | 근거 |
|---|---|---|
| **R3·R4 첫 측정** | **+2.1h** | 96턴 신규 |
| **G-3 존 역질문 확대** | **+**(왕복 증가) | 알람·복합에 역질문 1턴 + 답변 1턴. D군 6 + E·R1 다수 |
| **G-3 팬아웃 감소** | **−**(큰 폭) | 팬아웃 턴 p50 131.5s vs 단일 59.9s. 66턴 중 상당수가 절반 이하로 |
| **D군 `optin_alarm`** | **+**(서버 기동 1회) | 프로파일 1개 = 기동 1회 |
| CU-16 상한 | − (미세) | 무제한 SQL 실행 제거 |

**순 효과는 상쇄에 가깝다.** 팬아웃 감소(−)가 역질문 왕복(+)보다 클 가능성이 높지만 **확정할 수 없다.**

### 2.3 예산

| 항목 | 값 |
|---|---|
| **반복 1회 기준** | **8~11시간**(7.52 + 2.1 ± 증감) |
| **반복 3회(권장)** | **24~33시간** — 현실적이지 않다 |
| **선택적 반복**(§3.2) | **10~13시간** |

> **8시간을 넘으므로 X-2(`--segment`)가 없으면 또 토큰이 죽는다.** W1-c 랜딩이 E-3 의 하드 전제다.

---

## 3. E-3 설계 결정

### 3.1 프로파일 — 2개

| 프로파일 | 대상 | 근거 |
|---|---|---|
| `baseline` | D군 제외 전건 | 운영 `.env` 그대로 |
| `optin_alarm` | **D군 6건** | G-1 확정 — 결정적 경로가 D-05 를 **실제로 고치는지** 측정 |

**나머지 3개 옵트인 프로파일(`optin_query`·`optin_invest`·`optin_realtime`)은 이번에 돌리지 않는다.** 서버 기동이 3회 더 붙고, 지금 답하려는 질문 5가지 중 어느 것도 그 프로파일을 필요로 하지 않는다. `plans/96` V-1 의 커버리지 갭으로는 남는다.

### 3.2 반복 — 선택적 3회

전건 3회는 24~33시간으로 비현실적이다. **결론이 반복에 의존하는 시나리오만 3회** 돈다.

| 대상 | 반복 | 근거 |
|---|---:|---|
| **조용한 오답 관련**(B-04·B-09·H-10·H-11·H-15·K-04) | **3** | *"고쳤다"* 를 1회 통과로 말할 수 없다 |
| **CU 대응 시나리오**(J-03·B-07·B-08·D-02·D-04·D-05·A-01·I-01~I-06) | **3** | 같은 이유 |
| K군(이미 `replay` 5회 내장) | 1 | 카탈로그가 이미 반복한다 |
| 나머지 | 1 | |

> `plans/94` **Y-6**(반복 전건 실패 = 결정적)이 이 설계의 전제다. 3회 전건 동일이면 `불안정` 이 아니라 **결정적**으로 분류된다.

### 3.3 세그먼트

**X-2 의 크기 기준은 시나리오 수가 아니라 예상 소요여야 한다** — 군별 턴당 소요가 0.7분(F)~3.0분(R1)으로 **4배** 벌어진다. 시나리오 수로 자르면 세그먼트마다 소요가 들쭉날쭉해 토큰 수명 관리가 무의미해진다.

**권고: 세그먼트당 약 2시간**(토큰 수명 8h 의 25%). 4~6조각.
**제약**(`plans/94` §15.4): 서버 재기동 금지 · cold 묶음 첫 세그먼트 · 체크포인트 공유 · `raw.jsonl` 단일 파일 · teardown 은 run 단위.

### 3.4 실행 순서

```
0. E-0 사전 점검(§4)  ← 실패하면 여기서 멈춘다
1. E-1 DB 조회 2건    → G-5·G-8 확정
2. E-2 표적 재현 3건  → J-1·J-2·J-3 결론
3. (E-1·E-2 결과로 계획이 바뀌면 여기서 반영)
4. E-3 전 시나리오    → §0 질문 1~5
5. 분석 · 회귀 기준선 선언
```

---

## 3.5 ▶ 실행 가이드 — 이 절만 보고 돌린다

> 커밋 `12093ea`·`e2c3b79`·`56274a7`·`dfcdd7e`(2026-09-16) 기준. **명령은 전부 저장소 루트에서 돈다.**
> 하네스 자체의 일반 사용법은 `plans/94` 「실행 가이드」가 정본이고, 여기는 **이 실험을 돌리는 절차**다.

### `.env` 사전 설정 — 러너가 대신 해주지 않는 것

> **원칙: 러너가 주입하는 것은 건드리지 말고, 러너가 못 정하는 것만 사람이 정한다.**
> 러너는 기동할 때마다 아래를 **자기가 주입한다** — `.env` 에 쓰지 마라(써도 러너 값이 이긴다).
> `ALARM_ENABLED=false`(`runner.ISOLATION_ENV`) · `CHECKPOINT_DB_URL`(run 전용 격리 · 운영 `checkpoints.db` 오염 방지) ·
> 프로파일 플래그(`config/scenarios/profiles.yaml` — 예: `optin_alarm` 의 `TEXT2SQL_ALARM_DETERMINISTIC`).

#### 반드시 확인할 것 (없으면 run 이 헛돈다)

| 키 | 값 | 왜 |
|---|---|---|
| `LLM_PROVIDER` | `fabrix`(폐쇄망) | **`fabrix`·`ollama` 면 승인·`RUN_E2E` 없이 실 실행**된다(D-216·D-211 ⑪). `gemini` 등 외부면 `RUN_E2E=1` + **건별 사용자 승인**이 필요하다(D-127) |
| `ACTIVE_DB_IDS` | 측정 대상 DB | 러너가 이 값으로 `closed`/`sandbox` 를 자동 판정한다. 비어 있으면 환경 판정이 어긋나 시나리오가 통째로 보류된다 |
| `AUTH_ENABLED` | `true`(운영) | **`true` 인데 계정이 없으면 프로파일이 INVALID** 로 서고 run 이 시작도 못 한다(`runner.py:542`). 내장 테스트 계정이 서버에 없으면 `--user`/`--password` 를 넘긴다 |
| `AUTH_JWT_EXPIRE_HOURS` | 기본 `8` | **T-b 선제 갱신의 유일한 근거다.** 러너가 `AuthConfig.jwt_expire_hours` 를 읽어 그 **80% 경과 시** 턴 경계에서 재발급한다(`runner.py:569 jwt_lifetime_sec`). **읽지 못하면 선제 갱신을 아예 하지 않는다** — 모르는 채로 주기를 정하는 것 자체가 추정이기 때문이다. 이 값이 실제 서버 발급 수명과 다르면 8시간 넘는 run 에서 또 401 이 난다 |
| `DB_BACKEND` | `dbhub`(운영) | `dbhub` 면 **MCP 서버가 따로 떠 있어야** 한다(별도 프로세스·별도 cwd). 안 떠 있으면 전 시나리오가 조회 실패다 |

#### 사다리 단을 정하는 3종 — **E-0-1 의 대상**

| 키 | 1단(`deep_agent`)을 원하면 | 비고 |
|---|---|---|
| `ENABLE_DEEPAGENTS_PACKAGE` | `true` | **이것만으로는 부족하다** |
| `ORCHESTRATOR_PROVIDER` | `vllm` 또는 `gemini` | `vllm` 이면 `ORCHESTRATOR_BASE_URL` 의 `/v1/models` 헬스체크를 통과해야 한다. **`gemini` 면 외부 과금 API 라 D-127 승인 대상**이다 |
| `ORCHESTRATOR_BASE_URL` | vLLM 서빙 주소 | `vllm` 일 때만 |

> **run `20260915-131903` 은 `degraded_reason=flag_off` 로 2단에 머물렀다** — 첫 줄에서 떨어졌다는 뜻이다.
> 플래그를 켜도 오케스트레이터가 없으면 `orchestrator_unavailable`·`package_missing` 으로 **결국 2단으로 강등된다**(`src/observability/ladder.py:70-86`).
> **`deepagents` 패키지 자체는 설치돼 있다**(0.6.10 실측). 2·3단 플래그(`ENABLE_INTENT_ORCHESTRATION`·`ENABLE_SEMANTIC_ROUTING`)는 **tri-state** 라 미입력이면 `ACTIVE_DB_IDS` 등록 여부로 자동 결정된다 — 고정하려면 명시한다.

#### 쓰는 방법 — 함정 3가지

1. **인라인 주석 금지.** `.env` 계열은 `KEY=value  # 설명` 을 파싱하지 못한다. 주석은 **별도 줄**에 쓰고, **특히 빈 값 뒤에 붙이지 마라**
2. **list/dict 는 JSON 배열.** `ACTIVE_DB_IDS=["polestar_cm_gp","polestar_b0"]` — 쉼표 구분 문자열은 파싱 에러다
3. **OS 환경변수가 `.env` 를 덮는다.** 셸에 남은 값이 파일 값을 이긴다. Windows PowerShell 에서 `Get-ChildItem Env:` 로, POSIX 에서 `env | grep` 로 확인하라 — **파일만 고치고 왜 안 먹는지 헤매는 것이 가장 흔한 함정이다**

#### 확인 명령

```bash
# POSIX — 값이 아니라 키만 본다(비밀값 노출 방지)
grep -nE "^(LLM_PROVIDER|ACTIVE_DB_IDS|AUTH_ENABLED|AUTH_JWT_EXPIRE_HOURS|DB_BACKEND|ENABLE_DEEPAGENTS_PACKAGE|ORCHESTRATOR_PROVIDER|ORCHESTRATOR_BASE_URL|ENABLE_INTENT_ORCHESTRATION|ENABLE_SEMANTIC_ROUTING)=" .env
env | grep -E "^(LLM_|AUTH_|ADMIN_|ORCHESTRATOR_|ENABLE_|ACTIVE_|DB_BACKEND)" || echo "셸 오염 없음"
```

```powershell
# Windows — 같은 확인
Select-String -Path .env -Pattern '^(LLM_PROVIDER|ACTIVE_DB_IDS|AUTH_ENABLED|AUTH_JWT_EXPIRE_HOURS|DB_BACKEND|ENABLE_DEEPAGENTS_PACKAGE|ORCHESTRATOR_PROVIDER|ORCHESTRATOR_BASE_URL|ENABLE_INTENT_ORCHESTRATION|ENABLE_SEMANTIC_ROUTING)='
Get-ChildItem Env: | Where-Object Name -Match '^(LLM_|AUTH_|ADMIN_|ORCHESTRATOR_|ENABLE_|ACTIVE_|DB_BACKEND)' | Select-Object Name, Value
```

> **`.env` 를 실험이 임의로 바꾸지 않는다.** 사다리 플래그 변경(H-2)은 **사람 결정**이고, 바꿨으면 그 사실을 run 기록에 남긴다 — 설정이 다른 run 끼리는 회귀 비교가 성립하지 않는다.

---

### 0단계 — 무과금 사전 점검 (LLM 0 · 서버 0)

```bash
python -m scripts.scenario --dry-run          # 카탈로그 218건 · 군 16개
pytest tests/test_scenario tests/test_scripts -q
python scripts/arch_check.py --ci && python scripts/overfit_check.py --ci
```

기준선(2026-09-16 실측): `--dry-run` **218건** · `pytest` **669 passed / 2 skipped** · 게이트 **둘 다 exit 0**.
**여기서 어긋나면 폐쇄망에 가기 전에 멈춘다.**

### 1단계 — E-0 사전 점검 (폐쇄망 · §4)

```bash
# E-0-1 사다리 단 — 이 한 줄이 판정표 전체의 해석을 바꾼다
grep -E "^(ENABLE_DEEPAGENTS_PACKAGE|ORCHESTRATOR_PROVIDER|ORCHESTRATOR_BASE_URL)=" .env
python -m src.main --server   # 기동 로그의 "오케스트레이션 사다리 확정: tier=... degraded_reason=..."

# E-0-3 분할 실행 실동작 (무과금)
python -m scripts.scenario --mock --group D --segment 2
python -m scripts.scenario --mock --resume-failed <직전_RUN_ID>

# E-0-6 디스크 — 지난 run 이 체크포인트 1.49GB + 산출물
df -h .
```

`tier=deep_agent` 가 아니면 **정본 1단은 이번에도 측정되지 않는다**(§0). `degraded_reason` 을 기록하고 H-2 를 먼저 정한다.

### 2단계 — E-1 DB 조회 2건 (수 분 · 읽기 전용 · LLM 0)

§1 의 SQL 2개를 그대로 돌린다. **게이트 G-5·G-8 이 닫힌다.**

### 3단계 — E-2 표적 재현 3건 (약 10분 · 실 LLM)

```bash
python -m scripts.scenario --only H-10    # J-1 월 피벗: 미진입인가 폴백인가
python -m scripts.scenario --only B-06    # J-2 455초가 왜 타임아웃 가드에 안 걸렸나
python -m scripts.scenario --only G-01    # J-3 승계 실패: 승격 부재인가 덮어쓰기인가
```

**전 스위트가 아니다.** 폐쇄망 `fabrix` 라 승인은 면제지만(D-211 ⑪) 시간·서버 부하는 실재한다.

### 4단계 — E-3 전 시나리오 재측정 (8~11시간)

```bash
# 권장 — 군별로 끊어 검수하며 진행(중간에 멈춰도 --resume 으로 이어진다)
python -m scripts.scenario --group A --group B --group C --group D
python -m scripts.scenario --group E --group F --group G --group H --group I --group J
python -m scripts.scenario --group K --group L
python -m scripts.scenario --group R1 --group R2 --group R3 --group R4

# 또는 완주 — 세그먼트로 실패 반경만 줄인다
python -m scripts.scenario --segment 40
```

> **`--segment` 는 만료 방어 수단이 아니다.** 토큰 만료는 **T-b**(수명 80% 시점 턴 경계 선제 갱신)가 막는다.
> `--segment` 의 값은 실패 반경·진행 가시성·명시적 재개점이고, 크기 기준은 **시나리오 수**다(`plans/94` ⑩-0 근거).
> 균등 소요가 필요하면 `--segment` 가 아니라 **`--group`** 을 쓴다 — 군별 턴당 소요가 0.7분(F)~3.0분(R1)으로 4배 벌어진다.

### 5단계 — 중단·복구

```bash
python -m scripts.scenario --resume <RUN_ID>          # 성공 턴은 건너뛰고 이어서
python -m scripts.scenario --resume-failed <RUN_ID>   # 무효·오류만 새 run 으로
```

**`--resume-failed` 는 구 형식 적재본도 복구한다** — `invalid` 판정이 없던 시절의 `error`/`fail` 행을 `\bhttp\s+(401|403)\b` 로 식별한다(X-1). run `20260915-131903` 의 103턴이 그 대상이다.

### 6단계 — 리포트·분석 (무과금)

```bash
python -m scripts.scenario --report <RUN_ID>
python -m scripts.scenario --analyze <RUN_ID>
```

**리포트에서 가장 먼저 볼 것 3가지**

| 순서 | 볼 것 | 어긋나면 |
|---|---|---|
| 1 | 최상단 **무효 턴 경고**(T-e) | 5% 초과면 그 run 은 회귀 비교 대상이 아니다 |
| 2 | 1절 위 **사다리 강등 경고**(O-c) | 정본 1단이 아니면 판정표 해석이 달라진다 |
| 3 | `bottleneck.md` 의 **llm_calls 불가 문구**(O-b) | 문구가 사라졌으면 `plans/56` 이 뚫린 것 — 고정 문구를 고쳐야 한다 |

### Windows 에서 돌릴 때 — 달라지는 것만

> **일반 준비(venv · 인코딩 · 포트 제외 대역 · 절전 억제 · ACL · `ibm-db`)는 `plans/94` 부록 A.3 이 정본이다.** 여기는 **이 실험의 명령**을 Windows 로 옮긴 것과 **이 실험에 고유한 주의**만 적는다.

#### 실험 전 한 번 — 이 세 줄이 없으면 한글 출력에서 run 이 죽는다

```powershell
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
chcp 65001
```

run `20260915-131903` 의 provenance 가 **`pythonutf8: (미설정)` · `console_codepage: 활성 코드 페이지: 949`** 였다. 그 run 은 살아남았지만 **다음 run 도 그러리라는 보장은 없다** — cp949 콘솔에서 em-dash 하나가 `UnicodeEncodeError` 로 8시간짜리를 죽인다.

#### 단계별 명령 대조

| 단계 | POSIX | Windows (PowerShell) |
|---|---|---|
| 0단계 점검 | `python -m scripts.scenario --dry-run` | 동일 |
| | `pytest tests/test_scenario tests/test_scripts -q` | 동일 |
| 1단계 `.env` 확인 | `grep -nE "^(LLM_PROVIDER\|...)=" .env` | `Select-String -Path .env -Pattern '^(LLM_PROVIDER\|...)='` |
| | `env \| grep -E "^(LLM_\|AUTH_\|...)"` | `Get-ChildItem Env: \| Where-Object Name -Match '^(LLM_\|AUTH_\|...)'` |
| 1단계 서버 기동 | `python -m src.main --server` | 동일 (**`Ctrl+C` 대신 `Ctrl+Break`**) |
| 1단계 디스크 | `df -h .` | `Get-PSDrive C \| Select-Object Used, Free` |
| 2~4단계 실행 | `python -m scripts.scenario ...` | 동일 |
| 중단 | `Ctrl+C` | **`Ctrl+Break`** — `Ctrl+C` 는 자식 서버를 남긴다 |
| 잔여 프로세스 정리 | `pkill -f "src.main"` | `taskkill /IM python.exe /T /F` (**같은 PC 의 다른 python 도 죽는다 — PID 확인 후 쓸 것**) |

#### 이 실험에 고유한 Windows 주의 5가지

| # | 주의 | 근거 |
|---|---|---|
| 1 | **절전이 8~11시간 run 을 끊는다.** `powercfg /change standby-timeout-ac 0` 로 억제하고 **run 뒤 원복**한다. 지난 run 의 provenance 는 전원 구성표가 **「균형 조정」** 이었다 | A.2 · `run.json` 실측 |
| 2 | **바이러스 검사 제외를 걸어라.** 실시간 검사가 `results/scenario/` 의 잦은 쓰기(체크포인트 **1.49 GB** · xlsx 수십 개)를 훑으면 측정이 흔들린다. 지난 run 은 `av_exclusion: 미확인` 이었다 | A.2 · `run.json` 실측 |
| 3 | **경로 길이 260자.** `results/scenario/<run_id>/artifacts/<시나리오>-<턴>-result_<타임스탬프>.xlsx` 가 깊다. 저장소를 `C:\AIOps\...` 처럼 **얕은 경로**에 둔다 | A.2 · 지난 run 산출물 경로 실측 |
| 4 | **`--segment` 는 Windows 에서 더 유용하다.** 콘솔 창이 닫히거나 원격 세션이 끊겨도 세그먼트 경계가 명시적 재개점이 된다. 다만 **만료 방어는 여전히 T-b 소관**이다 | ⑩-0 |
| 5 | **DB2(`polestar_b0`) 대상이면 `ibm-db` 를 먼저 확인한다.** 루트 venv 에 없고 `mcp_server/pyproject.toml` 에만 선언돼 있다 | A.3 · CLAUDE.md |

```powershell
# 1·2·3 한 번에 점검
powercfg /query SCHEME_CURRENT | Select-String "전원 구성표 GUID"
Get-MpPreference | Select-Object -ExpandProperty ExclusionPath   # 없으면 빈 출력
(Resolve-Path .).Path.Length                                      # 여유 있게 40자 이하 권장
python -c "import ibm_db" 2>$null; if ($LASTEXITCODE -ne 0) { "ibm-db 미설치 — DB2 대상이면 설치 필요" }
```

#### run 뒤 원복

```powershell
powercfg /change standby-timeout-ac 30    # 원래 값으로
powercfg /change monitor-timeout-ac 10
```

---

### 실행 중 금지

- **`.env` 를 실험이 임의로 바꾸지 않는다** — H-2 는 사람 결정이다(§7)
- **결과로 자동 조치하지 않는다** — 분석기는 제안 문서만 낸다(D-212 ⑥)
- **1회 관측으로 처방하지 않는다** — 반복 대상은 §3.2 가 정한다

---

## 4. E-0 사전 점검 — 이것을 통과하지 못하면 10시간을 버린다

> 지난 run 은 **8시간을 쓰고 27%를 버렸다.** 그 비용의 대부분은 사전 점검 한 줄로 막을 수 있었다.

| ID | 점검 | 실패 시 |
|---|---|---|
| **E-0-1** | **사다리 단** — 기동 로그 `record_ladder_resolution` 1줄. 지난 run 은 `tier=intent_orchestration degraded_reason=flag_off` 였다 | 정본 1단을 측정하려면 `.env` 를 고쳐야 한다. **의도한 강등인지 먼저 확정**(O-c) |
| **E-0-2** | **토큰 수명** — `AuthConfig.jwt_expire_hours` 실효값. 코드 기본은 8 | 세그먼트 크기를 수명의 25% 이하로 |
| **E-0-3** | **X-2·X-3 실동작** — `--segment` 로 2조각, `--resume-failed` 로 지난 run 103턴 |  랜딩 미완이면 E-3 중단 |
| **E-0-4** | **`optin_alarm` 설정 에코** — 기동 후 `TEXT2SQL_ALARM_DETERMINISTIC` 실효값이 `true` 인지 | 주입 실패면 D군 측정이 무의미(프로파일 INVALID) |
| **E-0-5** | **감사 로그 표준출력** — `executed_sqls`·`llm_calls` 수집이 tail 에 의존한다(D-217 주의 ③) | SQL 0건 → 단언 공회전 |
| **E-0-6** | **디스크** — 지난 run 이 **1.49 GB 체크포인트 + 31 xlsx**. 반복 3회 대상이 늘면 더 커진다 | 여유 5 GB 이상 확보 |
| **E-0-7** | **작업 트리 clean** — 지난 run 은 `dirty: true` 로 돌아 재현 근거가 약하다. 지금 **3개 세션의 미커밋이 섞여 있다** | 커밋 후 실행(§7) |

---

## 5. 완료 판정 — `plans/96` §9 를 이 실험으로 측정한다

| # | 기준 | 지난 run | 목표 |
|---|---|---|---|
| 1 | 무효 턴 | 103 (26.9%) | **0** |
| 2 | 자동 판정률 | 34% | **60% 이상** |
| 3 | 허위 불합격 | ≥14 | **0** |
| 4 | 「과잉 거부 의심」 | 26 (전건 허위) | **유효 턴 기준 산출** |
| 5 | 조용한 오답 | 2종 6회 재현 | **P-1·P-3 소멸**(반복 3회 전건) |
| 6 | `llm_calls`·`tokens` | 표본 0 | **표본 > 0** 또는 불가 사유 고정 노출 |
| 7 | p50 wall | 62.6초 | **30초 이하** |
| 8 | 60초 초과 비율 | 53% | **20% 이하** |
| 9 | 사다리 단 | 2단(강등) | 1단 측정 **또는 강등 사유 문서화** |

> **7·8 은 이번에 달성되지 않을 수 있다.** CU-4(replanner)·CU-5(result_aggregator)가 게이트·선행에 막혀 미착수이고, 그 둘이 지연의 **40.6%** 다. 이번 run 의 역할은 *"달성"* 이 아니라 **O-b 로 레버를 특정하는 것**이다.

---

## 6. 이 실험이 풀지 못하는 게이트

| 게이트 | 왜 이 실험으로 안 풀리나 | 무엇이 필요한가 |
|---|---|---|
| **G-5** LOB 컬럼 | 시나리오는 SQL 형태만 본다 | **E-1** DB 조회 |
| **G-8** LIKE 이스케이프 | 같음 | **E-1** DB 조회 |
| **G-6** 서술 비용 처분 | 측정은 되나 **처분은 정책** | O-b 결과 + 사람 판단 |
| **G-9** 알람 임계값 의미 | *"CPU 임계값 초과 알람"* 의 정의 문제 | 사람 판단(D-202 4차와의 정합) |
| **G-10** 조립기 건수 전달 | 같음 | 사람 판단 |

**G-9 는 E-3 전에 정하는 편이 낫다** — 미정이면 D-05 단언이 무엇을 재는지 모르는 채로 돈다.

---

## 7. 실행 전 사람 결정 3건

| ID | 질문 | 왜 사람이 정하나 |
|---|---|---|
| **H-1** | **커밋 시점·범위** — 지금 3개 세션의 미커밋이 섞여 있고 `dirty` 상태로 돌면 재현 근거가 약하다(E-0-7) | 세션 간 조율 |
| **H-2** | **정본 1단(`deep_agent`)을 측정할 것인가** — `.env` 를 고쳐야 한다. 안 고치면 이번에도 2단이다 | 운영 설정 변경 |
| **H-3** | **선택적 반복 3회 범위**(§3.2) — 8~11h 가 10~13h 가 된다 | 폐쇄망 단말 점유 시간 |

---

## 8. 이 실험이 하지 않는 것

- **제품을 고치지 않는다.** 실행 중 발견은 `plans/98` 로 간다
- **결과로 자동 조치하지 않는다.** 분석기는 제안 문서만 낸다(D-212 ⑥)
- **1회 관측으로 처방하지 않는다.** 반복 없는 시나리오의 실패는 `불안정`이다(§3.2 가 반복 대상을 고른 이유)
- **`.env` 를 실험이 임의로 바꾸지 않는다.** H-2 는 사람 결정이다
