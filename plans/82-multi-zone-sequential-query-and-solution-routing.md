# 82. 복합 질의 실행 그룹 — 존 순차 조회 · 대상 소재 탐색 · 솔루션 축 파이프라인 · 범위 사전 선택

> 작성일: 2026-08-28 | **개정**: v6 (2026-08-28 — 요구 5: 급증 조건 표현 §6.9~§6.13 · Wave 9)
> **상태**: **1차 구현 완료** (2026-08-28 · D-176 등재) — Wave 1·2·3·3.5 착지.
> **Wave 8·9 구현 완료** (2026-08-28 · D-176 **후속1·후속2** 등재) — 요구 4(0건 원인 진단)·요구 5(급증 조건 표현).
> 둘 다 **플래그 기본 OFF**(`TEXT2SQL_EMPTY_DIAGNOSIS_ENABLED` · `TEXT2SQL_SPIKE_CONDITION_ENABLED`) —
> OFF면 0건 응답 문구가 **바이트 동일**하고 `_try_deterministic` 반환이 불변이다(회귀 0).
> 급증은 **파일시스템 축 한정**이고, 절대 임계 미명시·주 단위 요청은 조립하지 않고 **사유를 응답에 남긴다**
> (미반영 사실은 Wave 8의 G-4 경고가 안전망으로 알린다 — 두 Wave가 여기서 합성된다).
> **Wave 5·6.5 구현 완료** (2026-08-28 · D-176 **후속3·후속4** 등재) — 요구 2(존 순회 탐색)·요구 3(범위 사전 선택).
> 둘 다 **기본 ON**(`COMPOSITE_HOST_DISCOVERY_ENABLED` · `COMPOSITE_SCOPE_SELECT_ENABLED`).
> ★ 사용자 확정이 **계획서를 4건 개정**했다: U9(존 축도 묻는다 — §5.3 불변식 1 개정) ·
> U11(시간 임계 없음 — §5.4 `SCOPE_SELECT_MIN_SECONDS` 폐기) · U12(탐색 캐시 60초 기본 on) ·
> U13(범위 질문 지금 구현). 근거는 D-176 후속4 참조.
> **2차 잔여**: Wave 4·6·7(`group-artifacts`·`group-ui`·`solution-pipeline`)은 §14 **U1·U2·U3·U8** 확정 후.
> `ZONE_GROUP_EXCLUSIVE`는 **기본 on 유지**
> (존 동시 조회는 아직 열리지 않았다 — §11 PII 미종결 + Wave 6 승인 게이트).
> 실행 산출물: `CAPABILITY-MAP-execution-groups.md` · `SPEC-{multi-dialect-guard,group-registry,prior-scope-wiring,group-runner}.md`
> · `SPEC-{empty-answer-diagnosis,spike-condition}.md` · `tasks/plan-82.md` · `tasks/todo-82.md`
> · `tasks/plan-82-wave89.md` · `tasks/todo-82-wave89.md`
>
> **요구 1 (사용자, 2026-08-28)**: *"폴스타 조회시 공동존과 은행존의 db가 서로 달라 동시에 조회시
> 쿼리가 db종류에 맞게 작성이 안되는 문제가 발생한다. … 공동존과 은행존을 같이 조회하고 각각을 UI와
> 각각 파일로 저장하는 방식으로 … 은행존을 먼저 조회하고 조회가 완료되면 공동존을 조회하는 방식으로
> 처리하도록 수정하라. 또한 향후 폴스타 이외에 apm이나 dpm 등의 다른 모니터링 솔루션과 연동되면
> 각각의 솔루션들을 순차적으로 조회하고 종합적인 응답을 줄 수 있도록 시멘틱 라우팅 처리가 되어야 한다."*
>
> **요구 2 (사용자, 2026-08-28 — v2 개정 사유)**: *"'abd00 서버의 프로세스를 조회하라'라고 프롬프트를
> 요청하면 복합 질의 측면에서 은행존 폴스타, 공동존 폴스타를 순회하면서 해당 서버가 있는 위치를
> 조회하고 해당 위치의 정보와 함께 프로세스 리스트를 조회하는 단계로 순차적으로 진행되어야 한다.
> … 예를 들어 'abd00 서버의 WAS의 메모리 사용량을 보여줘' 라고 하면 은행존, 공동존의 폴스타를
> 조회하여 해당 서버의 위치를 파악하고 파악된 서버의 WAS 정보를 APM 솔루션을 통해 메모리 사용량을
> 조회하는 방식으로 복합적인 처리가 되어야 한다."*
>
> **요구 3 (사용자, 2026-08-28 — v3 개정 사유)**: *"복합적인 조회가 진행되어야 되는 경우 사용자에게
> 질의 하는 방법으로 조회시간을 감소 시킬 수 있다. … 다단계 복합 질의의 경우 전체를 조회할 것인지
> 선택한 솔루션만 조회할 것인지 사용자에게 선택하도록 하는 방식을 제공하여 조회 시간 감소를 위한
> 기능을 추가하라."*
>
> **요구 4 (사용자, 2026-08-28 — v5 개정 사유)**: *"'CPU 사용률 80% 이상인 서버 중에 파일시스템 사용률
> 갑자기 80% 이상으로 상승한 리스트' 라고 했을 때, CPU 80% 넘는 게 없는지 / 넘는 것 중 파일시스템이
> 80% 넘는 게 없는지 / 갑자기 80이 된 게 없는지 **사용자가 확인할 수 있어야 한다**. 현재는 조회된 내용이
> 없다고만 나오는데 **왜 안 나오는지 정보를 보여줄 수 있도록** 수정되어야 한다."*
>
> **요구 5 (사용자, 2026-08-28 — v6 개정 사유)**: *"급증의 경우 일정기간(1달 또는 1주일) 전에
> 용량 대비 사용률이 많이 높아졌다면 급증으로 볼 수 있다. 이런 조건을 판단하여 자료를 조회하는
> 방식으로 처리하는 것은 가능한지 검토하여 계획을 업데이트하라."*
>
> **v6의 판정 ★** (§6.9~§6.13 신설): **가능하다 — 데이터는 이미 있고, 파이프라인이 막고 있다.**
> v5는 급증 판정을 *"시계열 기준선 정의가 필요한 별건"* 으로 미뤘는데, **미룬 이유가 소멸했다** —
> 별건 규모였던 근거는 *"무엇 대비 급증인가"* 의 미정이었고 **사용자가 그것을 확정**했다.
> 실측 결과: ①시계열 통계가 **3단(`_h`/`_d`/`_m`)으로 존재**하고 세 프로파일 전부 `allowed_tables` 등재
> ②★*"용량 대비 사용률"* 은 **이미 계산되어 저장돼 있다**(`server.FileSystems`+`Utilization` = 퍼센트,
> `used/total` 불필요) ③기간 비교는 조건부 집계로 **표준 SQL 표현 가능**(self-join 불필요)
> ④★**그런데 파이프라인이 명시적으로 막고 있다** — 컴파일러가 *"전월 대비 증감"* 을 커버리지 밖으로
> 선언하고, 폴백 경로엔 few-shot **0건**이며, `build_stat_month_block`이 **단일 기간을 강제**해 정면 충돌한다.
> 즉 **G-4는 데이터 부재가 아니라 표현 경로의 부재**이며, *"안 됐다고 말하기"* 에서 *"되게 하기"* 로 옮길 수 있다.
> 다만 *"많이 높아졌다"* 를 SQL로 옮기기 전에 **세 갈래**를 정해야 하고(§6.10), 그중 **집계 축**이
> 가장 위험하다 — 프로파일 few-shot의 **서버 단위 AVG를 그대로 따르면 급증이 희석돼 놓친다.**
>
> **v5의 핵심 ★** (§6 신설): 이것은 **§4·§5와 다른 축**이다 — 실행 그룹은 *어디서*(존·솔루션),
> 이 절은 *무엇으로*(조건 단계) 걸러지는가를 다룬다. 그리고 **확립된 DB 연구 문제**다:
> **MFS/XSS**(Minimal Failing / maXimal Succeeding Subquery · Godfrey IJCIS 1997 인용 114 `EMPTY-MFS-01`)
> 로 이미 정식화돼 있고, **조건 N개면 후속 질의 N회로 끊긴 지점을 찾는 단순 알고리즘**이 존재한다.
> **§5.1의 비용 실측이 이것을 값싸게 만든다** — 존당 SQL p50 49~53ms이므로 `COUNT` 프로브 3~4회 =
> **~200ms · LLM 0회**이고, **0건일 때만 발동**하므로 정상 경로 비용은 0이다.
> 실측 결손 5건 중 **가장 위험한 것은 G-4** — *"갑자기 상승"* 을 표현할 수단이 코드에 **0건**이라
> LLM이 단순 임계로 눌러도 **침묵**한다. G-1~G-3은 "왜 0건인지 모른다"지만 G-4는 **"내 조건이
> 무시됐는지도 모른다"** 로, 0건의 의미 자체가 달라진다.
>
> **v4의 문헌 검토 결과 ★** (§7 신설): 저장소의 기존 하네스 자산(`plans/78` — ETCLOVG 7계층 · P1~P15 ·
> 문헌 615편 기반 내부 명세)을 렌즈로 82를 판정했다. **설계 방향은 정합**하며, 특히 §5.5의
> 「계측 → 질문」 순서는 **P13(측정이 최적화보다 먼저)** 과 정확히 일치한다 — `plans/78` v4가 위반해
> v5에서 정정한 바로 그 지점을 82는 처음부터 지켰다. 82의 하네스 프로파일은 **L·O 중심**(78은 E·G 중심).
> 기존 자산에 **비어 있던 영역**(사용자에게 되묻는 행위의 비용·효과)은 신규 검색으로 보강했고,
> 그 결과 **정정 6건**이 나왔다(§7.6). 설계 변경 2건:
> **① 부분 결과 즉시 노출을 질문과 동급의 1급 해법으로 승격** — `Online Aggregation`(SIGMOD 1997 ·
> 인용 928)이 확립한 패턴이며 사용자 개입 0·정보 손실 0으로 질문보다 우월하다. 요구 1의 "은행존 먼저"와
> 자연 결합한다(먼저 조회한 것을 먼저 보여준다).
> **② 질문 게이트에 `answerable`(품질) 축 추가** — 무조건 묻기는 **세션 시간을 약 2배로 늘린다**는
> 89명 실험 측정치가 있다(IPM 2022). 저품질 CQ = 사용자가 답을 모르는 질문 → **존 축은 묻지 않고
> 솔루션 축만 묻는다**로 확정.
>
> **v3의 핵심 실측 ★**: 시간 절감은 **묻는 위치에 따라 부호가 바뀐다.** `logs/audit-*.jsonl` 918건 집계
> 결과 **존당 SQL 실행은 p50 ~50ms**(b0 49 / gp 49 / yd 53)로 사실상 공짜이고, 비용은 **스키마 분석
> (존당 ≤20s)·LLM SQL 생성(스키마 키당 1회, 프롬프트 90k~136k tok)** 에 있다. 따라서 ①**탐색 단계에
> 존을 물으면 150ms를 아끼려 사용자를 수 초 붙잡는 순손실**이고 ②절감이 실제로 나는 곳은
> **솔루션 축**(폴스타/APM/DPM 각각의 백엔드 왕복·응답 조립)이다. 요구 3은 **솔루션 축 범위 선택**으로
> 구현하고, 존 축은 기존 `zone_select`(D-143)를 재사용한다. 상세·비용 모델은 **§5**.
>
> **v2의 핵심 정정 ★**: v1은 실행 그룹을 **전부 동등(peer)** 으로 가정하고 *"그룹마다 섹션·그룹마다 파일"*
> 을 규칙으로 삼았다. **요구 2의 두 예시는 그 형태가 아니다** — 앞 그룹이 **뒤 그룹의 조회 범위를 정하는
> 파이프라인**이고, 산출물은 **하나**(위치 정보를 부기한 프로세스 목록)다. v1 규칙을 그대로 적용하면
> abd00이 없는 존에서 **빈 파일이 하나 더 생긴다.** 따라서 실행 그룹에 **관계(kind)** 축을 넣어
> `peer`(열거)와 `pipeline`(탐색→조회)을 구분하고, **산출물 정책을 kind가 결정**하게 한다.
>
> **관련 결정**: D-004(LLM 전용 시멘틱 라우팅) · D-006(런타임 활성 DB 단일 출처) · D-035(결정적 게이트=판단) ·
> D-041/D-046(실시간 프로세스 1급 의도 · 서버명→hostname 해소) · D-053(사본 금지) · D-057(DB2 스키마 한정) ·
> D-066(단일/멀티 경로 대칭) · D-094(sub-task 스코프) · D-119(관측 읽기 경계 = `mcp_server`) · D-131(레지스트리 단일 등록점) ·
> **D-143 후속3(존 그룹 상호배타 — 이 계획이 개정)** · D-153 후속1·2 · D-154 · **D-155 후속1~5(PII 차단 — 미종결)** ·
> D-159 · D-161(승격-폐기 동반) · D-168(미들웨어 OS 근사 식별) · D-169(라우터 출력 계약) · D-171(공통 모듈 계층) ·
> **D-175(호스트 가용성 사전 판정 — `plans/81`, 작업 트리에 구현 진행 중)**
> **신규 결정 예약**: **D-176** (§14 · `docs/02_decision.md` 「채번 이력」 등재 완료)
> **관련 계획**: `plans/78`(조회↔조사 배선 — **W1·W2·W7-1 완료, W7-2(APM) 미착수**) · `plans/80`(78·79 공동 실행 계약) ·
> `plans/81`(호스트 가용성 — **동시 편집 충돌 주의 §12.1**) · `plans/55`(APM·DPM 멀티소스 로드맵) · `plans/75` §4 · `plans/73` · `plans/79`
> **실측 기준**: 현 브랜치 `multiintent` HEAD `c7d47e8` + **미커밋 작업 트리**(2026-08-28 확인 — §12.1)
> **비용 실측 근거**: `logs/audit-*.jsonl` `query_execution` 918건 집계(§5.1) · `.env` 실측값(§10.2·§12.2)
> **문헌 근거**: §7(검토) · §17(서지) — 동료심사 8건 + 기존 자산(`plans/78` §3.4·§12) 재사용
> **주의**: 본문의 파일:라인 인용은 2026-08-28 작업 트리 기준이다. `plans/81`·`plans/83` 구현이
> 동시 진행 중이라(§12.1) 라인 번호는 이동할 수 있다 — **착수 시 심볼명으로 재확인**할 것.

---

## 0. 요약 — 무엇을 만드는가

```
사용자 질의
   │
   ├─ [라우팅] 실행 그룹 목록 생성 (순서 + 관계)
   │
   ├─ [자동 축소] capabilities/requires 매칭으로 그룹 감소 — 질문보다 먼저 (§5.4)
   │
   ├─ [범위 게이트] 자동으로 못 좁히고 + 답을 알 수 있는 축 + 비용 임계 초과 → 범위 선택 역질문 (§5)
   │     · 축: **솔루션만**(존 축은 answerable=false — §7.4-②)  · 기본값: **전체 조회**
   │     · 탐색 그룹은 비용 산정 제외(존당 ~50ms 실측)
   │     · 모호성 해소 질문(zone_select)이 있으면 비발동 — 2연속 질문 금지
   │     · 좁히면 미조회 범위를 명시하고 재확장 1클릭 제공(§5.3 불변식 6)
   │
   │   ┌ 열거형(peer) ─────────────────────────────────────────────┐
   │   │ "은행존과 공동존의 모든 서버 OS"                             │
   │   │   G1 은행존(order 10) → G2 공동존(order 20)                 │
   │   │   산출: 존별 섹션 2개 + 존별 파일 2개 + 종합                  │
   │   └──────────────────────────────────────────────────────────┘
   │
   │   ┌ 탐색형(pipeline) ─────────────────────────────────────────┐
   │   │ "abd00 서버의 프로세스를 조회하라"                            │
   │   │   G0 탐색(은행존→공동존 순회, 고정 SQL) ⇒ 위치·hostname 확정  │
   │   │        └→ G1 프로세스 조회(확정된 존의 API만 호출)            │
   │   │   산출: 결과 1개 + "위치: 공동존 김포" 부기 + 탐색 경과        │
   │   │                                                            │
   │   │ "abd00 서버의 WAS 메모리 사용량"                             │
   │   │   G0 탐색(폴스타 존 순회) ⇒ 위치 확정                        │
   │   │        └→ G1 미들웨어 식별(78 W7-1 완료 자산)                │
   │   │              └→ G2 APM 메모리 조회(78 W7-2 · APM 도입 종속)  │
   │   └──────────────────────────────────────────────────────────┘
   │
   ├─ [종합] 그룹 관계에 맞는 응답 조립
   │
   └─ [0건이면] 조건 퍼널 진단 (§6) — 여기서만 발동, 정상 경로 비용 0
         COUNT 프로브 계단으로 **어느 조건에서 끊겼는지**(MFS/XSS) 지목
         + **표현되지 못한 조건**("갑자기 상승")의 미반영 사실 노출 ★
         + 그룹별 퍼널 합성(존마다 끊긴 지점이 다르다)
```

두 형태는 **다른 기능이 아니라 같은 실행 그룹의 두 관계**다. 존/솔루션은 그룹의 *축*이고,
peer/pipeline은 그룹 간 *관계*다. 이 둘을 분리해야 APM·DPM 편입이 그룹 하나 추가로 끝난다.

**범위 사전 선택(§5)은 그 위에 얹는 비용 게이트**다 — 그룹 목록이 확정된 뒤, *좁힐 여지가 있고
비용 임계를 넘을 때만* 축 하나를 골라 묻는다. 그룹 모델이 없으면 "무엇을 얼마나 아끼는지"를 계산할
대상 자체가 없으므로, 순서상 §4가 §5의 선행조건이다.

**0건도 답이다** — 조건이 여럿인 질의가 0건을 낼 때 *"데이터가 없습니다"* 는 문헌 기준으로
**불충분한 응답**이다(협조적 응답 원칙 · `claims.md` C9). §6이 그 축을 담당하며, §4의
`group_results[*].row_count`를 재사용해 **존별로** 끊긴 지점을 낸다.

**그리고 질문은 마지막 수단이다** — 문헌 검토(§7.4) 결과 절감 순서는
**부분 결과 즉시 노출 → 자동 축소 → 승계 → 계측 → 질문**으로 확정했다. 앞의 셋은 사용자 개입이
0이고 정보 손실도 없다. 무조건 묻기는 세션 시간을 약 2배로 늘린다는 측정치가 있다(IPM 2022).

---

## 1. 요구의 전제 검증 — 실측 정정 3건

### 1.1 "쿼리가 DB 종류에 맞게 작성되지 않는다" — 생성 문제가 아니다

방언 힌트는 **이미 DB별로 주입된다**(`_build_multi_engine_hint`: DB2면 `FETCH FIRST` 지시 + 스키마
한정 D-057). b0(DB2/`POLESTAR`)와 gp·yd(PG/`polestar`)는 스키마 키가 달라 SQL도 각각 독립 생성된다
(`sql_by_schema[(engine, schema)]`, `multi_db_executor.py:471-478`).

실제 결함은 **검증 비대칭**이다:

| | 방언 검사 | 실행오류 후 재생성 |
|---|---|---|
| 단일 DB | **있음** — 행 제한 절 부재 시 엔진별 자동 보정(`sql_validation.py:194-197`) | **있음** (`query_executor`→`query_generator` 백엣지) |
| 멀티 DB | **없음** — `_validate_sql_simple`(SELECT 여부·금지 키워드·한글 잔존·`dtime IS NULL`·LEFT JOIN 강등만) | **없음** — `graph.py:543` 무조건 전진 |

전환 스위치는 이미 있다: `TEXT2SQL_MULTI_FULL_VALIDATION`(`config.py:314`, **기본 `False`**,
`.env`·`.env.example` 모두 미설정). 즉 DB2 대상에 `LIMIT`이 나오면 **검증을 통과하고 실행 시점에
죽으며 재생성 기회가 없다.** → **Wave 1 선행 필수.**

### 1.2 순차 실행은 이미 있고, 없는 것은 **순서 보장**이다

`multi_db_executor`는 이미 `for target in targets`로 순차 순회한다(`:545-546`). 문제는 순서가
`targets.sort(key=relevance_score, reverse=True)`(`semantic_router.py:298`) — **LLM 자기보고 점수**에
달려 있다는 점이다. 여기에 "은행존 먼저"를 의존하면 LLM 비결정성에 순서를 맡긴다.
또한 **그룹 경계 자체가 없다** — b0→gp→yd가 평평한 한 루프라 *"은행존 조회가 완료되면"* 이라는
시점이 존재하지 않는다.

### 1.3 ★ 탐색형은 **막다른 안내로 끝난다** (v2 신규 실측)

`"abd00 서버의 프로세스를 조회하라"`(첫 턴, 위치어 없음)의 현행 경로:

| 단계 | 코드 | 결과 |
|---|---|---|
| 1 | `intent_planner._coerce_process_intent` (`:112`) | `data_query` → **`process_query`로 결정적 교정**(D-041/D-046). 정상 |
| 2 | `_zone_clarification_or_none_task` (`subagents.py:239`) | `if task.get("agent") != "data_query": return None` → **존 선택 UI조차 뜨지 않는다** |
| 3 | `run_process_query` → `_resolve_db_id` (`process_query.py:84`) | ①`task.db_ids` 없음 ②`previous_db_ids` 없음(첫 턴) ③위치어 매칭 0건 → **`None`** |
| 4 | `run_process_query:703-706` | *"프로세스 조회 대상 DB(위치)를 식별하지 못했습니다. 위치(예: 김포/여의도)를 지정해 주세요."* → **막다른 안내** |

**그런데 순회 탐색은 기술적으로 이미 가능하다** — 운영 `.env` 실측:

```
ALARM_PROCESS_API_BASE_URLS_CSV=polestar_cm_gp=<url>,polestar_cm_yd=<url>,polestar_b0=<url>
```

**세 존 전부 프로세스 API가 매핑돼 있다.** 위치만 알면 바로 조회할 수 있는데, 위치를 찾아보지
않고 사용자에게 되묻는다.

### 1.4 왜 `data_query`는 되고 `process_query`는 안 되는가 — 전제의 붕괴 지점

`_zone_clarification_or_none_task`에는 이런 비발동 조건이 있다:

```python
# 서버명 지목 질의는 존이 결과에 영향 없음(§4.2 ⓐ) — hostname으로 어차피 특정됨
if has_host_identifier_filter(parsed):
    return None
```

이 전제는 **SQL 경로에서만 참**이다. `data_query`는 여러 존에 **동시에 SQL을 던지고** hostname WHERE
필터가 알아서 맞는 존에서만 행을 돌려준다 — **팬아웃이 암묵적 탐색 역할**을 한다.

**`process_query`·APM·DPM은 단일 대상 API**다. base_url을 하나 골라야 하므로 "어느 존인가"를
**먼저 알아야 한다.** 즉 관측 솔루션이 늘어날수록 이 전제는 더 자주 깨진다 — 요구 2가 지적한 지점이
정확히 여기다.

| 경로 | 대상 선택 | 위치 미해소 시 |
|---|---|---|
| `data_query` (SQL) | 다중 DB 팬아웃 가능 | 팬아웃이 탐색을 겸함 (동작함) |
| `process_query` (REST) | **단일 base_url** | **막다른 안내** |
| APM·DPM (향후, REST) | **단일 엔드포인트** | 같은 막다름이 재발 |

---

## 2. 선행 결과 → 후속 스코프 배관의 결손 (★ v2 핵심 발견)

탐색형을 성립시키려면 *"앞 단계가 찾은 존"* 이 *"뒤 단계의 조회 대상"* 으로 흘러야 한다.
**부품은 전부 있는데 배관 두 곳이 끊겨 있다.**

### 2.1 있는 것

| 자산 | 상태 | 위치 |
|---|---|---|
| `TargetRef`에 **`db_id` 필드** | 있음 (`{server_name, hostname, ip, db_id}`) | `src/utils/prior_targets.py:83-87` |
| 병합 행의 **출처 태그 `_source_db`** | 있음 (`_merge_results`가 행마다 부착) | `multi_db_executor.py:2126` |
| 선행 결과 → 대상 해소 | 있음 (`build_prior_targets` · `resolve_investigation_targets`) | `prior_targets.py:187` / `process_query.py:167` |
| task 의존 계약 | 있음 (`depends_on`=실행 순서, `input_from`=데이터 의존) | `orchestration/schemas.py:29-46` |
| 위상 정렬 | 있음 (`topological_levels`) | `agent_orchestrator.py:169` |
| 다대상 hostname 조회 1쿼리 | 있음 (`lookup_many` — IN절) | `noise_gate/infrastructure/polestar_hostname_resolver.py:271` ※§12.1 |

### 2.2 끊긴 곳 ① — 출처 태그를 읽지 않는다

```python
# prior_targets.py:257-262 — 행별 출처를 보지 않고 호출부가 준 db_id 하나를 전 대상에 찍는다
ref = (TargetRef(hostname=text, db_id=db_id)
       if is_hostname_col else TargetRef(server_name=text, db_id=db_id))
```

```python
# subagents.py:_build_prior_targets_for_task:869-873
db_ids = res.get("target_db_ids") or []
resolution = build_prior_targets(rows, db_id=db_ids[0] if db_ids else state.get("active_db_id"), ...)
#                                            ^^^^^^^^^^ 팬아웃하면 첫 DB(=b0)로 전부 도장
```

**결과**: abd00이 공동존 김포에 있어도 선행 결과에서 만들어진 `TargetRef.db_id`는 `polestar_b0`가
된다. 후속 단계가 이 값을 믿으면 **엉뚱한 존의 API를 친다.** 행에는 `_source_db="polestar_cm_gp"`가
**이미 들어 있는데도** 읽지 않는다.

### 2.3 끊긴 곳 ② — 대상 DB 결정이 선행 결과를 보지 않는다

`_resolve_db_id`(`process_query.py:84-143`)의 우선순위는 **①`task.db_ids` ②`previous_db_ids`
③위치어 매칭** 셋뿐이다. **`prior_targets`/`prior_rows`는 후보에 없다.** 즉 앞 단계가 존을 정확히
찾아내도 프로세스 조회는 그 값을 쓰지 않는다.

### 2.4 귀결

이 두 곳(각각 수십 줄 규모)이 **탐색형 전체를 막고 있는 실질 병목**이다. 그룹 축·UI·파일 분리보다
**작고, 독립적이며, 지금 고쳐도 회귀가 없다.** → **Wave 3.5로 분리해 조기 착지**한다.

---

## 3. 현행 동작 실측 — 열거형(요구 1) 측면

### 3.1 상호배타 게이트 — 발동 지점 4곳

| # | 위치 | 동작 |
|---|---|---|
| ① | `api/routes/query.py:696` `_zone_clarification_or_none` (텍스트 pre-gate) | 혼합 선택·혼합 텍스트 → 존 선택창 재요청 |
| ② | `api/routes/query.py:617` `_file_zone_clarification_or_none` (파일/폼필 pre-gate) | 동일(`has_file=True`) |
| ③ | `routing/semantic_router.py:362` `_zone_clarification_or_none_router` (레거시 그래프 후단) | 동일 |
| ④ | `orchestration/subagents.py:204` `_zone_clarification_or_none_task` (트랙 A 후단) | 동일 — **단 `data_query` 전용**(§1.4) |

공용 판정·페이로드는 `src/utils/query_gen_common.py` 단일 출처
(`mixed_zone_groups`·`has_mixed_zone_group_terms`·`build_zone_clarification`·
`rewrite_zone_mentions_for_selection`·`ZONE_CLARIFY_OPTIONS`). 프론트는 `app.js:1230`에서
`group_exclusive`로 그룹 간 라디오를 강제한다.

### 3.2 상호배타의 근거는 2층이고, 한 층만 무효화됐다

| # | 근거 | 상태 |
|---|---|---|
| ① | **담당 조직 분리** — 존 조합 실수요 없음(사용자 확정 2026-08-05) | **사용자가 번복 — 소멸** |
| ② | **FabriX PII 필터 차단 회피** — `B0+YD 정상 · GP 단독 정상 · GP+YD 정상 · **B0+GP만 차단**`. 로컬 PII 스캔 무일치 | **미종결(D-155 후속1~5) — 살아 있다** |

②는 §11에서 별도로 다룬다.

### 3.3 결과 분리 — 원재료는 있고 소비처가 없다

| 자산 | 상태 |
|---|---|
| `db_results: {db_id: rows}` | DB별 이미 분리 보존 |
| 병합 행 `_source_db` 태그 | 있음 |
| `db_result_summary`(display_name·row_count·columns) | `result_merger.py:45-53`에서 **생성 즉시 버려진다**(반환 dict 미포함) |
| `organized_data` / `final_response` / UI | 전부 단일 구조 |

### 3.4 파일 산출 — 단일 파일이 5곳에 박혀 있다

| # | 지점 | 현행 |
|---|---|---|
| ① | `state.py:245-246` | `output_file` / `output_file_name` — 단수 |
| ② | `output_generator._generate_document_file:613` | `organized_data.rows` 전량으로 템플릿 1회 채움 |
| ③ | `result_aggregator._merge_finalized:585-587` · `_synthesize_finalized:639-641` | *"output_file이 있는 첫 task의 파일을 우선 채택"* — **2번째 이후 파일 침묵 손실**(기존 결함) |
| ④ | `api/routes/query.py` | `has_file`/`file_name` 단수 · `/download` 단일 · `/download-csv`는 병합 전량 |
| ⑤ | `app.js:804-818, 1140-1152` | 다운로드 링크 1개 |

`fill_excel_template(file_data=uploaded_file, ...)`은 원본 바이트를 매 호출 새로 받으므로
**rows만 바꿔 N회 재호출이 안전**하다.

### 3.5 라우팅 — 솔루션 축이 없다

`config/db_registry.yaml`의 현재 축은 `zones`(RBAC 단위) · `families`(경쟁 지역 배제 판정 전용) ·
`locations`(표면어→db_ids) · `databases`뿐이다. **실행 순서·그룹 경계·백엔드 종류(SQL/REST/MCP)를
표현하는 축이 없다.** 라우터 출력도 `target_databases` 평면 리스트 하나다.

`plans/55`가 APM·DPM 백엔드를 *"벤더 DB 읽기전용 **또는 REST API**"* 로 예고하므로, db_id 리스트만으로는
표현 불가하다 — **그룹의 실행 주체가 달라진다.**

### 3.6 미들웨어(WAS) — 절반은 이미 있다

요구 2의 WAS 예시는 `plans/78` W7이 이미 다룬다:

| 단계 | 상태 | 자산 |
|---|---|---|
| 미들웨어 **식별**(OS 근사) | **완료**(78 W7-1 · D-168) | `src/domain/middleware.py` · `config/middleware_signatures.yaml` · `SPEC-middleware-os-identification.md` |
| 미들웨어 **지표**(힙·GC·스레드풀·메모리) | **미착수**(78 W7-2 · APM 도입 종속) | APM은 `mcp_server`의 **세 번째 소스**로 편입(D-119 경계 확장, 78 §4.7.2) |

→ **82는 W7-2를 구현하지 않는다.** 82의 책임은 *"탐색 → 식별 → 지표"* 를 잇는 **파이프라인 계약**이고,
G2·G3의 실체는 78 소관이다(§13 범위 표).

---

## 4. 설계 — 실행 그룹(execution group) + 관계(kind)

### 4.1 개념 모델

```python
{
  "group_key": "polestar:bank",     # f"{solution}:{zone_group or '-'}"
  "solution":  "polestar",
  "zone_group":"bank",
  "label":     "은행존",
  "db_ids":    ["polestar_b0"],
  "backend":   "sql",               # sql | rest | mcp
  "order":     10,                  # 그룹 간 순서 — 레지스트리가 정본
  "kind":      "peer",              # peer | discovery | dependent   ★ v2 신규
  "depends_on": [],                 # dependent/discovery 소비 그룹이 채운다
  "scope_from": None,               # 앞 그룹이 확정한 대상 스코프의 출처 group_key
}
```

**설계 원칙 5개**

1. **존 그룹과 솔루션은 같은 타입이다.** 존만을 위한 특수 경로를 만들지 않는다 — APM/DPM이 오면
   그룹 하나가 늘어날 뿐이어야 한다.
2. **순서는 설정에서 온다. LLM에서 오지 않는다.** `query_order`/`order`가 정본(D-035).
   `relevance_score`는 **후보 채택 여부**에만 쓰고 **정렬에서 분리**한다.
3. **관계(kind)가 산출물 정책을 결정한다** ★ — §4.6.
4. **그룹은 실패 격리 단위다.** 한 그룹 실패가 다른 그룹을 죽이지 않고 사유는 그 그룹에 구조화 노출.
5. **단수 필드는 유지하되 폐기 기한을 둔다.** `output_file`/`output_file_name` → `output_files[0]`
   별칭, 폐기 기한 **2027-02-20**(D-161 ①).

### 4.2 레지스트리 확장 (`config/db_registry.yaml`) — 추가 전용

```yaml
# ── 관측 솔루션 — 실행 그룹의 1차 축.
solutions:
  - code: polestar
    label: 폴스타(인프라 모니터링)
    order: 10
    backend: sql
    family: polestar                 # 기존 families 참조(사본 아님)
    # 이 솔루션이 답할 수 있는 관측 능력 — 솔루션 파이프라인 구성의 결정적 재료(§4.5)
    capabilities: [server_spec, server_usage, alarm, host_location, process_list]
  # 향후(현재 미등록):
  # - code: apm
  #   label: APM(애플리케이션 성능)
  #   order: 20
  #   backend: rest
  #   capabilities: [was_metric, jvm_heap, thread_pool, transaction]
  #   requires: [host_location]      # ★ 선행 능력 — 파이프라인 간선의 정본
  # - code: dpm
  #   label: DPM(DB 성능)
  #   order: 30
  #   backend: rest
  #   capabilities: [db_session, slow_query, lock_wait]
  #   requires: [host_location]

# ── 존 그룹 — 솔루션 내부의 2차 축. 담당 조직·엔진이 갈리는 경계.
zone_groups:
  - code: bank
    label: 은행존
    solution: polestar
    zones: [bankjon]
    query_order: 10        # ★ 사용자 요구: 은행존 선행
  - code: common
    label: 공동존
    solution: polestar
    zones: [gongjon]
    query_order: 20
```

**`zones:` 선언 순서는 재정렬하지 않는다.** `all_zones()`는 알림 RBAC 선택지 순서를 만들고 있어
용도가 다르다 — 두 순서를 한 리스트에 겹치면 한쪽 요구가 다른 쪽을 흔든다.
`ZONE_CLARIFY_OPTIONS`의 `group` 필드는 이미 같은 값이므로 **레지스트리 파생으로 전환**한다(D-131 정합).

**`capabilities`/`requires`가 솔루션 파이프라인의 정본이다** — 어휘는 데이터, 판정은 코드.
"WAS 메모리 → APM"이라는 지식이 코드 리터럴로 흩어지지 않는다.

### 4.3 탐색 그룹(discovery) — 존 순회 대상 소재 확정

**무엇이 아닌가**: text2sql 파이프라인이 아니다. LLM을 쓰지 않는다.

**무엇인가**: 인가된 존을 `query_order` 순으로 순회하며 **고정 SELECT 1건**으로 `cmm_resource`에서
입력 식별자(서버명 또는 hostname)의 소재를 찾는다.

```
G0 discovery(hostname="abd00", 순회=[은행존 b0, 공동존 gp, 공동존 yd])
   ├─ b0 : SELECT ... WHERE name='abd00' OR hostname='abd00'  → 0건
   ├─ gp : 동일                                                → 1건 ⇒ hostname=abd00.kb, server_name=…, avail=…
   └─ yd : 동일                                                → 0건
   ⇒ resolved_targets = [{hostname, server_name, db_id: polestar_cm_gp, zone_label: "공동존 김포", availability}]
```

| 항목 | 결정 | 근거 |
|---|---|---|
| 재사용 자산 | `PolestarHostnameResolver.resolve_with_status` / `lookup_many`(IN절 1쿼리) · `build_hostname_sql` / `build_host_status_sql` | 새로 만들지 않는다. **§12.1 소유권 주의** |
| 순회 범위 | **인가된 존만** — `state.allowed_db_ids` ∩ 활성 폴스타 존 | 없으면 권한 밖 존의 서버 존재 여부가 새어나간다 |
| 순회 순서 | `query_order`(은행존 → 공동존) | 사용자 요구와 동일 계약 |
| 조기 종료 | **하지 않는다(전수 순회)** — 다만 `COMPOSITE_DISCOVERY_EARLY_EXIT`로 옵트인 | 동명 호스트가 여러 존에 존재할 수 있고, 첫 히트로 끊으면 그 사실이 은폐된다 |
| 0건 | 막다른 에러 아님 — *"인가된 3개 존(은행존·공동존 김포·여의도)에서 'abd00'을 찾지 못했습니다"* + 순회한 존 목록 명시 | 침묵 금지. 현행 *"위치를 지정해 주세요"* 보다 정보량이 크다 |
| 2건 이상 | **양쪽 다 제시하고 사용자에게 되묻는다**(기존 존 선택 UI 재사용 — 선택지를 발견된 존으로 좁힘) | 임의 선택은 오답을 정답처럼 만든다 |
| 가용성 | 같은 행의 `avail_status`·`is_maintenance`로 판정 병기(D-175) | **DB 왕복이 늘지 않는다** — Plan 81이 이미 컬럼을 추가 |
| 비용 | 존당 SELECT 1회 · LLM 0회 | text2sql 파이프라인 대비 무시할 수준 |

### 4.4 탐색 그룹을 **누가** 만드는가 — 결정적 전개

| 안 | 방식 | 판정 |
|---|---|---|
| (a) LLM DAG 분해 | `intent_planner` 프롬프트를 확장해 LLM이 discovery task를 만들게 한다 | **기각** — 파이프라인 성립 자체가 LLM 비결정성에 종속. 실패 시 다시 막다른 안내로 회귀하고 원인 특정이 어렵다 |
| **(b) 결정적 전개** | 조건 충족 시 **코드가** discovery 그룹을 앞에 붙인다 | **채택** |

**발동 조건 (전부 AND — 결정적)**

1. 대상 그룹의 `backend != "sql"`이거나 솔루션이 `requires: [host_location]`을 선언 —
   *단일 대상 API라 존을 하나 골라야 한다*
2. 질의에 **호스트 식별자가 있다** — `has_host_identifier_filter(parsed)` 재사용(§1.4에서 전제가
   깨진 바로 그 판정을 **반대 방향으로** 쓴다)
3. 위치가 **미해소** — `_resolve_db_id`의 ①②③이 전부 실패
4. 대상 후보 존이 2개 이상

**선례**: `_coerce_process_intent`(`intent_planner.py:112`)가 이미 같은 방식이다 — LLM 분류를
결정적으로 교정한다. 82는 그 다음 칸(*어디서 찾을지*)을 같은 원칙으로 채운다.
**역할 분담**: LLM은 *"무엇을 조회할지"*, 코드는 *"어디서 찾을지"*.

### 4.5 솔루션 파이프라인 — WAS 예시의 전개

`"abd00 서버의 WAS의 메모리 사용량을 보여줘"`

```
[요구 능력 판정]  질의 → 필요 capability = was_metric (+ 암묵 host_location)
[requires 해소]   apm.requires = [host_location] → host_location 제공 솔루션 = polestar
[그룹 전개]
   G0 discovery   polestar / 존 순회       kind=discovery
   G1 middleware  polestar(OS 근사) 또는 apm  kind=dependent  scope_from=G0   ← 78 W7-1 완료 자산
   G2 was_metric  apm                       kind=dependent  scope_from=G1   ← 78 W7-2 (미착수)
[실행]            G0 → G1 → G2 순차. 각 그룹은 앞 그룹이 확정한 스코프만 본다
[종합]            단일 답변 + "위치: 공동존 김포 / WAS: JBoss EAP 7.4(pid 1234)" 부기
```

- **APM 미등록 상태의 동작**: `solutions`에 apm이 없으면 G2가 만들어지지 않는다 → *"WAS 메모리 지표를
  제공하는 관측 솔루션이 연동되지 않았습니다. 현재 확인 가능한 것: 위치·프로세스 존재·기동 인자"* 로
  **부분 응답 + 사유 명시**(침묵 금지). 현행은 폴스타에서 WAS 메모리를 환각 조회할 위험이 있다.
- **DPM도 동일 형태**: `requires: [host_location]` → G0 재사용, G1 없이 G2로 직행.

### 4.6 ★ 산출물 정책은 kind가 결정한다 (v1 정정)

| 관계 | UI | 파일 | CSV |
|---|---|---|---|
| **peer**(열거) | 그룹별 섹션 + 종합 블록 | **그룹별 N개** (`result_은행존_*.xlsx`, `result_공동존_*.xlsx`) | 전량 + 그룹별 |
| **discovery** | 결과 본문이 아니라 **경과·근거**로 표기 (*"은행존→공동존 순회, 공동존 김포에서 발견"*) | **생성하지 않는다** | — |
| **dependent** | 최종 그룹 결과 1개 + 선행 그룹의 확정 정보 부기 | **1개** | 1개 |

**v1의 "그룹마다 파일"을 무조건 적용하면 탐색형에서 빈 파일이 생긴다.** 이것이 v2 개정의 직접 사유다.
그룹이 1개면 모든 산출물이 **현행과 바이트 동일**해야 한다(회귀 0 골든).

### 4.7 State 확장 (`src/state.py`)

```python
# 실행 그룹(D-176) — 순서 + 관계를 가진 실행 단위.
execution_groups: Optional[list[dict]]      # §4.1 구조. order 정렬 확정 상태로 저장
group_results:   Optional[dict[str, dict]]  # {group_key: {rows, row_count, errors, sqls, elapsed_ms, degraded_reason}}
resolved_scope:  Optional[dict]             # discovery 산출 — {targets: [TargetRef], searched: [db_id], found_in: [db_id]}
output_files:    Optional[list[dict]]       # [{name, bytes, group_key, label, total_filled}]
# 호환 별칭 — output_files[0]. 폐기 기한 2027-02-20 (D-161 ①)
output_file: Optional[bytes]
output_file_name: Optional[str]
```

- 위 4개는 **요청 스코프** — 라우트에서 매 턴 명시 초기화한다(Known Mistakes: 체크포인터는 델타만 병합).
- `resolved_scope`는 `prior_targets`와 **다른 것**이다: `prior_targets`는 *"앞 task가 낸 행에서 뽑은 대상"*,
  `resolved_scope`는 *"탐색이 확정한 대상 + 순회 경과"*. 후자는 순회한 존 목록을 함께 실어 0건·다중 히트
  안내의 근거가 된다.

### 4.8 라우팅 — 거부 게이트 → 분할기 + 전개기

```
[현행]  mixed_zone_groups(ids) → True → 존 선택창 재요청 (거부)
[개선]  partition_execution_groups(ids)      → [G_bank, G_common]        (분할·정렬)
        expand_discovery_groups(groups, ctx) → [G0_discovery, *groups]   (결정적 전개)
```

- 둘 다 `src/utils/query_gen_common.py`에 두어 **4개 발동 지점(§3.1)이 공유**한다(D-053).
- `_zone_group_exclusive_or_none`은 **삭제하지 않고 플래그 종속으로 남긴다** —
  `ZONE_GROUP_EXCLUSIVE=true`면 현행 거부(즉시 롤백 경로), `false`면 분할.
- `_zone_clarification_or_none_task`의 `agent != "data_query"` 조기 반환은 **유지**한다 —
  process_query는 존 선택창이 아니라 **탐색 그룹**이 답이다(되묻지 않는 것이 개선).
- `rewrite_zone_mentions_for_selection`(D-154)은 **양쪽 그룹 선택 시 자동 비발동**이 이미 성립한다
  (`selected_groups={bank,common}` → `non_selected_terms` 공집합 → 원문 유지,
  `query_gen_common.py:1310-1322` 실측). **추가 수정 불필요.**
- 정렬 분리: `semantic_router.py:298`의 `relevance_score` 정렬은 **그룹 내부 순서**로만 남긴다.

### 4.9 실행 — 그룹 루프

`multi_db_executor`를 그룹 인지로 만든다. 두 안 비교:

| 안 | 장점 | 단점 | 판정 |
|---|---|---|---|
| **A. 노드 내부 그룹 루프** | 그래프 배선 무변경 · `sql_by_schema`가 그룹 스코프로 자연 격리(gp/yd 공유 유지, b0 분리) · **LLM 클라이언트도 그룹별 신규** → §11.1 H1 검증 가능 | 노드 체류 시간 증가 → SSE 무이벤트 구간 확대(§12.3) | **채택** |
| B. 그룹당 그래프 노드 분리 | SSE 이벤트 자연 발생 | 그룹 수가 런타임 결정인데 그래프는 빌드 타임 배선 → 사다리 4단 재배선 | 기각 |

```python
async def multi_db_executor(state, *, llm=None, app_config=None) -> dict:
    groups = state.get("execution_groups") or _single_group_fallback(state)
    group_results, scope = {}, state.get("resolved_scope")
    for g in groups:                                   # ★ 순차. order 확정 상태
        if g["kind"] == "discovery":
            scope = await _run_discovery(g, state, app_config)   # LLM 0회·고정 SQL
            _emit_group_progress(g, scope)   # 탐색은 경과만 — 부분 결과 아님
            if not scope["targets"]:
                return _discovery_miss(g, scope)       # 사유·순회 존 목록 노출
            continue
        run = await _prepare_multi_run(_scoped_state(state, g, scope), llm, app_config)
        for target in _targets_of(g, state, scope):    # scope가 있으면 그 존만
            await _run_single_target(target, run)
        await _retro_recover_same_schema(run)          # D-153 소급 복구 — 그룹 내부로 이동
        group_results[g["group_key"]] = _collect_group(run, g)
        _emit_group_packet(g, group_results[g["group_key"]])   # ★ 진행 표시 + 부분 결과
    return _assemble(state, groups, group_results, scope)
```

**★ `_emit_group_packet` — 진행 표시가 아니라 「부분 결과」다** (문헌 정정 ② · §7.4-⑤)

그룹이 끝나는 즉시 **그 그룹의 결과 자체**를 SSE로 내보낸다(행 수·요약·표). 종전 v3 설계는 이것을
*"체감 지연 감소(실 시간은 동일)"* 로 적었으나, `Online Aggregation`(SIGMOD 1997 · 인용 928) 이후
**부분 결과 즉시 제시는 긴 질의의 정통 해법**이며 사용자 개입도 정보 손실도 없다. 은행존을 먼저
조회하는 요구 1과 결합해 **먼저 조회한 것을 먼저 보여준다.**

- 최종 `final_response`는 여전히 종합 응답이다 — 부분 결과는 **추가**이지 대체가 아니다(append-only).
- peer 그룹에만 적용한다. `discovery`는 결과가 아니라 경과이고, `dependent`는 앞 그룹만으로는
  답이 되지 않는다(부분 결과가 오해를 만든다).
- SSE 무이벤트 구간 차단(§12.3)이라는 기존 목적도 그대로 달성한다.

- **D-153 소급 복구를 그룹 내부로 이동**한다. 복구원 판정 키가 `(engine, schema)`라 그룹을 넘나들 수
  없다(b0↔gp는 애초에 키가 달라 복구 불가 — D-153 후속1 ③이 확인). 그룹 내부가 의미상 정확하다.
- `query_results`(전역 병합)는 **계속 생성**한다 — `/download-csv`·`row_count`·기존 테스트 호환.

---

## 5. 범위 사전 선택 — 조회 시간 감소 (요구 3)

> **요구 3 (사용자, 2026-08-28)**: *"복합적인 조회가 진행되어야 되는 경우 사용자에게 질의 하는 방법으로
> 조회시간을 감소 시킬 수 있다. … 전체를 조회할껀지 은행존, 공동존을 사용자에게 선택하게 할 것인지를
> 사용자에게 질의하면 조회시간을 감소시킬 수 있다. 이런 다단계 복합 질의의 경우 전체를 조회할 것인지
> 선택한 솔루션만 조회할 것인지 사용자에게 선택하도록 하는 방식을 제공하라."*

원리는 옳다. 다만 **어느 단계에 물어야 절감이 나는지**는 실측이 갈랐다 — 아래 비용 구조를 먼저 고정한 뒤
발동 지점을 정한다. 잘못된 단계에 물으면 절감은커녕 왕복만 는다.

### 5.1 ★ 비용 실측 — 시간은 존 수가 아니라 **LLM·스키마 분석**에 든다

`logs/audit-*.jsonl`의 `query_execution` 이벤트 918건 집계(실측 2026-08-28):

| source_name | n | p50 | p90 | max |
|---|---:|---:|---:|---:|
| `polestar_b0` | 15 | **49ms** | 55ms | 56ms |
| `polestar_cm_gp` | 30 | **49ms** | 67ms | 76ms |
| `polestar_cm_yd` | 11 | **53ms** | 61ms | 156ms |
| `polestar`(레거시 단일) | 743 | 59ms | 85ms | 455ms |

**3초를 넘긴 것은 918건 중 9건**이며 전부 스키마 캐시·LLM 산출 이상 케이스다.
즉 **SQL 실행 자체는 존을 늘려도 사실상 공짜다**(존당 ~50ms).

시간이 드는 곳은 따로 있다:

| 비용 항목 | 단위 | 규모 근거 |
|---|---|---|
| **LLM SQL 생성** | **스키마 키(engine, schema)당 1회** | 프롬프트 90k~136k 토큰 실측(D-159 — cm_gp 136,707tok). 존 3개라도 b0(DB2)+gp·yd(PG 공유) = **2회** |
| **스키마 분석**(MCP `search_objects`·`get_sample_data`) | **존당 1회** | D-154가 호출당 8s·총 20s로 bound. 운영 `.env`는 `DBHUB_MCP_CALL_TIMEOUT=600` |
| **응답 생성·종합**(LLM) | 그룹 수에 비례 | 그룹별 서술 + 종합 합성 |
| SQL 실행 | 존당 ~50ms | 위 실측 — **무시 가능** |
| **탐색(discovery)** | 존당 고정 SELECT 1회, **LLM 0회** | §4.3 — 3존 ≈ **150ms** |

**비용 모델**

```
T ≈ Σ(그룹) [ 스키마분석(존 수 × ≤20s) + LLM생성(스키마 키 수 × 수 초~수십 초) + 응답(1회) ]
   + 탐색(존 수 × ~50ms)          ← 사실상 0
```

### 5.2 귀결 — 물어야 할 곳과 물으면 손해인 곳

| 단계 | 존/솔루션 하나 줄일 때 절감 | 질문 비용(사람 왕복) | 판정 |
|---|---|---|---|
| **탐색(discovery)** | **~50ms** | 수 초 ~ 수 분 | **묻지 않는다 — 순손실** |
| **data_query peer 그룹**(text2sql) | 스키마 분석 ≤20s + LLM 생성 1회 | 동일 | **묻는다 — 이미 D-143 존 역질문이 수행** |
| **솔루션 축**(폴스타/APM/DPM) | 솔루션당 백엔드 왕복 + 응답 조립 1회 | 동일 | **묻는다 — ★ 이 계획의 신규 이득** |

★ **요구 3의 WAS 예시에 대한 정정**: *"은행존, 공동존의 폴스타를 각각 조회해야 되지만"* 이라는 전제는
§4의 설계에서는 성립하지 않는다. `"abd00 서버의 WAS의 메모리 사용량"` 의 폴스타 단계는 **전량 조회가
아니라 탐색(고정 SELECT ~50ms×3)** 이고, 그 직후 **파이프라인이 1개 존으로 좁혀진다**. 여기에 존 질문을
붙이면 150ms를 아끼려고 사용자를 수 초 붙잡는다 — **순손실**이다.

**그러나 요구 3의 취지는 그대로 유효하다.** 같은 질의에서 진짜 비용은 **솔루션 축**에 있다:
`"abd00의 성능 상태를 보여줘"` 처럼 능력이 여러 솔루션에 걸치면 폴스타+APM+DPM이 **각각 왕복**하고
각각 응답을 조립한다. **여기가 물어야 할 자리다.**

> 덧붙여, 시간 절감의 더 큰 레버는 질문이 아니라 **탐색 결과 재사용**이다 — §5.6.

### 5.3 설계 — 범위 선택 역질문(`scope_select`)

**형태**: D-143 `zone_select`와 **동형의 stateless clarification**. 프론트가 원문 + 구조화 필드를
재전송하고, 라우팅은 그 필드로 결정적 고정된다(자연어 재조합·LLM 재해석 0).

```json
{
  "kind": "scope_select",
  "question": "이 질의는 관측 솔루션 3곳을 순차 조회합니다(예상 40~90초). 범위를 좁히시겠습니까?",
  "axis": "solution",
  "options": [
    {"key": "__all__",  "label": "전체 조회 (권장 — 어디에 있는지 모를 때)", "default": true,
     "estimate": {"groups": 3, "seconds_lo": 40, "seconds_hi": 90}},
    {"key": "polestar", "label": "폴스타만 (인프라 사양·사용량·프로세스)",
     "estimate": {"groups": 1, "seconds_lo": 10, "seconds_hi": 25}},
    {"key": "apm",      "label": "APM만 (WAS·JVM·트랜잭션)",
     "estimate": {"groups": 1, "seconds_lo": 10, "seconds_hi": 25}},
    {"key": "dpm",      "label": "DPM만 (세션·슬로우쿼리·락)",
     "estimate": {"groups": 1, "seconds_lo": 10, "seconds_hi": 25}}
  ],
  "multi": true,
  "original_query": "…"
}
```

응답은 `selected_scope: {"axis": "solution", "keys": ["polestar", "apm"]}` 구조화 필드로 재전송한다
(`selected_db_ids` 선례 동형 — `semantic_router`·`intent_planner`가 LLM 라우팅을 우회해 고정).

**불변식 5개**

1. **답을 알 수 있는 축만 묻는다 + "전체 조회"가 항상 첫 선택지이고 기본값이다** ★ (정정 ⑥)
   `"abd00 서버"`라고만 말한 사용자는 그 서버가 어느 존인지 **모를 수 있다** — 모르는 것을 물으면
   §1.3이 지적한 막다름이 형태만 바꿔 재발한다. 좁히기는 **사용자가 아는 경우의 선택적 최적화**이지
   진행 조건이 아니다.
   문헌은 이를 정량화한다 — **저품질 CQ(의도를 좁히지 못하는 질문)는 사용자를 방해하고 세션 시간을
   약 2배로 늘린다**(IPM 2022, 89명 실험 · §7.4-①). 따라서 기본값 제공만으로는 부족하고
   **`answerable` 판정을 게이트에 넣어 발동 자체를 막는다**(§5.4). 판정 기준:
   *질문의 품질 = 사용자가 답을 아는가 × 답이 비용을 줄이는가* — 둘 다 참이어야 묻는다.
2. **모호성 해소 질문과 구분한다.**
   | | 기존 `zone_select`(D-143) | 신규 `scope_select` |
   |---|---|---|
   | 성격 | **모호성 해소** — 존을 특정할 수 없어 진행 불가 | **성능 최적화** — 전부 할 수 있지만 시간이 든다 |
   | 기본 진행 | 불가(답해야 진행) | **가능**(건너뛰면 전체 조회) |
   | 문구 | "지정되지 않았습니다" | "범위를 좁히시겠습니까?" |
   둘이 같은 턴에 겹치면 **모호성 해소가 우선**이고 `scope_select`는 발동하지 않는다(2연속 질문 금지).
3. **비용 임계를 넘을 때만 묻는다** — §5.4.
4. **한 번만 묻고 승계한다** — 확정된 `selected_scope`는 멀티턴 승계 신호로 보존하고, 같은 스레드의
   후속 턴은 재질문하지 않는다(`previous_db_ids` 승계와 동형). 사용자가 새 범위 신호를 주면 갱신.
5. **비대화 채널은 항상 비발동** — 배치·평가·API 직접 호출은 `zone_clarification_allowed`와 같은
   채널 게이트를 공유한다(D-143 후속2 선례).
6. **좁힌 사실과 미조회 범위를 기록하고 재확장 경로를 준다** ★ (정정 ③)
   범위 축소는 **정보 손실이 복구되지 않는 절단**이다 — ETCLOVG 실무 체크리스트 ③이 *"압축 시
   정보 손실·출처를 기록"* 하라고 요구하는 대상이며, `plans/78` W2-7도 "원문 전량 보존"을 상쇄
   조건으로 달았다. 82의 상쇄 장치는 둘이다:
   ① **응답 말미에 미조회 범위 명시** — *"공동존만 조회했습니다. 은행존은 조회하지 않았습니다."*
   ② **재확장 1클릭** — *"은행존도 조회"* 버튼(원문 + 확장된 `selected_scope` 재전송).
   감사 로그에도 `scope_narrowed: {selected, skipped}`를 남긴다(침묵 절단 금지).

### 5.4 언제 묻는가 — 결정적 비용 게이트

```python
def scope_question_or_none(groups, ctx, config) -> dict | None:
    if not config.composite.scope_select_enabled:        return None   # 기본 off
    if not ctx.get("zone_clarification_allowed"):        return None   # 비대화 채널
    if ctx.get("selected_scope") or ctx.get("selected_db_ids"): return None  # 재개 턴
    if _succeeded_scope(ctx):                            return None   # 멀티턴 승계
    if _ambiguity_question_pending(ctx):                 return None   # 모호성 해소 우선

    groups = auto_narrow(groups, ctx)                    # ★ 정정 ④ — 자동 축소가 먼저
    billable = [g for g in groups if g["kind"] != "discovery"]         # ★ 탐색은 비용에서 제외
    axis = _widest_axis(billable)                        # solution 우선, 없으면 zone_group
    if len(_distinct(billable, axis)) < 2:               return None   # 좁힐 여지 없음
    if not _answerable(axis, ctx):                       return None   # ★ 정정 ⑥ — 품질 축
    if estimate_seconds(billable) < config.composite.scope_select_min_seconds: return None
    return build_scope_clarification(billable, axis, ...)
```

**`auto_narrow` — 묻기 전에 자동으로 좁힌다** (정정 ④ · 근거 §7.4-④)

질의가 요구하는 `capabilities`로 그룹을 걸러낸다 — `"WAS 메모리"` 는 `was_metric`을 요구하므로
**폴스타는 `host_location` 제공자로만 남고 지표 그룹은 APM 하나**가 된다. 이 단계에서 `axis`의
후보가 1개로 줄면 **질문은 발동하지 않는다.** 분산 검색의 자원 선택·샤드 절단이 같은 일을
질의별 자동 추정으로 해결하며(CIKM 2012), RouteLLM은 **사용자 개입 0**으로 비용 −85%를 얻는다(P12).
**자동으로 얻을 수 있는 절감을 사용자에게 묻는 것은 순손실이다.**

**`_answerable` — 사용자가 답을 알 수 있는 축인가** (정정 ⑥ · 근거 §7.4-②)

| 축 | 판정 | 근거 |
|---|---|---|
| `solution` | **묻는다** | 사용자가 관점(앱·DB·인프라)을 이미 표현했다 — 어휘가 질의에 있다 |
| `zone_group` | **묻지 않는다**(자동/탐색) | 서버명만 준 사용자는 소재를 모른다. 존을 아는 사용자는 이미 위치어를 쓰며 그 경우 D-065가 결정적으로 고정한다 |

판정은 **결정적**이다 — 축별 상수 테이블이며 LLM 추론이 아니다(D-035).

| 게이트 | 값 | 근거 |
|---|---|---|
| `COMPOSITE_SCOPE_SELECT_ENABLED` | **기본 off** | 켜기 전 현행 동작 비트 동일 |
| `COMPOSITE_SCOPE_SELECT_MIN_SECONDS` | **초기 30** (잠정) | 임계는 **계측 후 정산**한다 — §5.5. `MIN_RELEVANCE_SCORE=0.3`이 근거 없이 무기한 실동작한 전례(D-174 ②)를 반복하지 않는다 |
| 축 우선순위 | `solution` **만**(정정 ⑥) | `zone_group`은 `answerable=false` — 존을 아는 사용자는 위치어를 쓰고 D-065가 고정한다. 모르는 사용자에게 묻는 것이 저품질 CQ다 |
| 탐색 그룹 | **비용 산정에서 제외** | 존당 ~50ms 실측 |
| `COMPOSITE_SCOPE_SELECT_ENABLED` 만료일 | **2027-02-20** (정정 ⑤ · D-161 ①) | 도래 시 삭제 또는 사유부 연장. 하네스 구성요소는 모델·시스템이 좋아지면 순비용이 된다(P15) |
| 발동률 관측 | **필수** — 세션당·전체 질의 대비 발동 비율(정정 ⑤) | 반복 노출은 신경·행동 수준에서 주의를 급감시킨다(MISQ 2018 · JMIS 2016). 발동률을 못 보면 습관화를 통제할 수 없다 |

`estimate_seconds`는 **추정이 아니라 관측 기반**이어야 한다 — §5.5.

### 5.5 예상 시간은 어떻게 아는가 — 계측이 선행한다

사용자에게 *"예상 40~90초"* 를 보여주려면 그 숫자의 출처가 있어야 한다. **없으면 만들지 말고, 만들기
전에는 문구에서 뺀다**(환각 금지).

| 단계 | 내용 |
|---|---|
| **S-A 계측** | 그룹 실행마다 `group_results[*].elapsed_ms`를 남긴다(§4.9 `_collect_group`이 이미 수집). `trace_collector`의 단계 `elapsed_ms`(D-141)와 같은 형식 |
| **S-B 집계** | `(solution, zone_group, kind, backend)`별 p50/p90을 롤링 집계. 저장은 기존 `investigation_metrics` 형식 재사용(신규 저장소 0) |
| **S-C 표기** | 표본 **n≥20**이면 `p50~p90`을 범위로 표시, 미만이면 **시간 문구를 생략**하고 그룹 수만 표시(*"관측 솔루션 3곳을 순차 조회합니다"*) |
| **S-D 임계 정산** | 분포가 쌓이면 `SCOPE_SELECT_MIN_SECONDS`를 p50 기준으로 재설정. 정산 전까지 30은 **잠정값임을 코드 주석·문서에 명시** |

계측(S-A·S-B)은 **질문 기능과 무관하게 단독으로 유용하다** — 어느 그룹이 느린지 모르면 §12.2 지연
대응도 추정이 된다. 그래서 Wave 3에 넣고 질문은 Wave 6.5로 뒤에 둔다.

### 5.6 ★ 질문보다 큰 절감 레버 — 재사용

물어서 아끼는 것보다 **다시 하지 않아서 아끼는 것**이 크고, 사용자를 붙잡지 않는다.

| 레버 | 절감 | Tier | 상태 |
|---|---|---|---|
| **★ 그룹별 부분 결과 즉시 노출** (정정 ②) | **첫 결과까지의 시간을 그룹 1개분으로 단축** — 은행존이 끝나면 그 결과를 바로 보여준다. 사용자가 만족하면 이후 대기가 무의미해진다 | 0~1 | §4.9 `_emit_group_packet` — **"진행 표시"에서 「결과 노출」로 승격** |
| **자동 축소**(`capabilities`/`requires`) | 질문 없이 그룹 수 감소 | 0 | §4.5 — §5.4 `auto_narrow`로 게이트 앞에 배치(정정 ④) |
| **탐색 결과 멀티턴 승계** | 같은 스레드 재질의 시 탐색 0회 | 0 | `previous_db_ids`/`previous_entities` 배관 존재(`context_resolver.py:107-130`) — `resolved_scope` 승계만 추가 |
| **동일 스키마 SQL 재사용** | gp·yd가 LLM 생성 1회 공유 | — | **이미 구현**(`sql_by_schema`) |
| **스키마 캐시** | 존별 스키마 분석 생략 | — | 이미 구현(`src/schema_cache/`) |
| 탐색 결과 단기 캐시 | 짧은 시간 내 재질의 시 순회 생략 | **2** | `investigation_cache`(TTL 기본 **0=끔**, D-172). **히트율 계측 전에는 켜지 않는다**(정정 ① · P13) — 히트 시 **수집 시각 표기 필수**(침묵 금지) |

**설계 순서**(문헌 근거 §7.4-④·⑤): **부분 결과 노출 → 자동 축소 → 승계 → 계측 → 그 다음에 질문.**
질문을 먼저 넣으면 **사용자 개입 없이 없앨 수 있었던 비용까지 사용자에게 전가**한다.
Tier 2 항목(단기 캐시)은 계측 뒤로 보낸다 — *"Tier 1의 추적·귀책 능력이 없으면 Tier 2의 최적화가
이득인지 손실인지 판별할 방법이 없다."*

**★ 부분 결과 노출이 질문보다 우월한 이유** (§7.4-⑤ 표 요약): 사용자 개입 0 · 정보 손실 0 ·
사용자가 답을 몰라도 무관 · 첫 결과까지의 시간이 더 짧음 · 습관화 무관.
**요구 1의 "은행존을 먼저 조회"와 자연스럽게 결합한다** — 먼저 조회한 것을 먼저 보여주면 된다.
`Online Aggregation`(SIGMOD 1997 · 인용 928)이 이 문제의 정통 해법으로 확립한 패턴이다.

---

## 6. 빈 결과(0건) 원인 진단 · 급증 조건 표현 (요구 4·5)

> **요구 4 (사용자, 2026-08-28)**: *"'지난 1달간 서버의 cpu 사용률이 80% 이상인 서버 중에 파일시스템
> 사용률 갑자기 80% 이상으로 상승한 리스트를 보여줘' 라고 했을때 지난 1달간 cpu 사용률이 80% 넘는것이
> 없는지? 넘는 것 중에 파일시스템의 사용률이 80% 넘는게 없는지 갑자기 80로 된게 없는것이 없는지
> 사용자는 확인할 수 있어야 한다. 현재는 조회된 내용이 없다고만 나오는데 왜 안나오는지에 대한 정보를
> 보여줄 수 있도록 수정되어야 한다."*

**이것은 §4·§5와 다른 축이다.** 실행 그룹은 *어디서*(존·솔루션) 조회하는가를 다루고, 이 절은
*무엇으로*(조건 단계) 걸러지는가를 다룬다. 둘 다 "0건인데 왜인지 모른다"에 답하지만 축이 다르며,
**함께 써야 완결된다** — §6.6.

### 6.1 현행 동작 실측 — 정형문 하나로 끝난다

`src/nodes/output_generator.py:266` `_generate_empty_result_response`:

```python
response = f"조건에 해당하는 {targets} 데이터가 없습니다."
if filters:
    response += "\n\n다음과 같은 방법을 시도해보세요:"
    response += "\n- 필터 조건을 완화해보세요 (예: 임계값 낮추기)"
```

**어느 조건에서 끊겼는지 말하지 않는다.** *"필터 조건을 완화해보세요"* 는 조건이 3개일 때
**어느 것을 완화해야 하는지**를 사용자가 알 수 없게 한다.

### 6.2 실측 결손 5건

| # | 결손 | 실측 근거 |
|---|---|---|
| **G-1** | **단일 SQL 경로에는 중간 카운트가 없다** — 조건이 `WHERE cpu>=80 AND fs>=80`로 합쳐지면 단계별 잔존 건수가 계산조차 되지 않는다 | 단일 task → 단일 SQL 구조 |
| **G-2** | **복합 경로는 중간 카운트를 갖고 있는데 버린다** — `intent_planner`가 이 질의 형태를 t1(CPU) → t2(파일시스템, `input_from=[t1]`)로 분해하는 규칙이 **이미 있다**(`prompts/intent_planner.py` 예시 3·3-1). t1의 행 수는 `task_results`에 있는데 0건 응답에 반영되지 않는다 | `prompts/intent_planner.py:138-168` · `_prior_result_rows` |
| **G-3** | **0건 응답이 조건과 무관한 정형문**이다 | §6.1 |
| **G-4** ★ | **"갑자기 상승"을 표현할 수단이 아예 없다** — 급증·변화율 판정 코드가 **0건**이다. LLM이 이를 단순 `>= 80`으로 눌러버리면 **의미 손실이 침묵**하고, 사용자는 세 번째 조건이 반영됐는지조차 알 수 없다 | `src/` 전체에 급증/spike/변화율 판정 부재(grep 0건) |
| **G-5** | **0건인데 재생성을 돈다** — `aggregation`이 있으면 `_check_data_sufficiency`가 0건을 부족으로 판정해 `data_insufficient` 재시도로 간다. 데이터가 진짜 없으면 재생성은 무의미하고 토큰만 쓴다 | `result_organizer.py:312-316` |

**G-4가 가장 위험하다.** G-1~G-3은 *"왜 0건인지 모른다"* 이지만, G-4는 *"내가 요청한 조건이
무시됐는지도 모른다"* 다 — 0건의 의미 자체가 달라진다.

### 6.3 ★ 이것은 확립된 DB 연구 문제다 — MFS / XSS

30년 된 **협조적 응답(cooperative answering)** 영역이며, 우리가 발명할 것이 아니라 **차용할 것**이다.

> **Godfrey, "Minimization in Cooperative Response to Failing Database Queries"**
> IJCIS 6(2), 1997 · 인용 114 (`EMPTY-MFS-01`)
> **Fokou, Jean, Hadjali & Baron, "Handling failing RDF queries: from diagnosis to relaxation"**
> Knowledge and Information Systems 50(1), 2016 (`EMPTY-DIAG-01`)

| 개념 | 정의 | 사용자가 보게 될 것 |
|---|---|---|
| **XSS** (maXimal Succeeding Subquery) | 결과가 남는 **최대** 조건 부분집합 | *"CPU 80% 이상: **12대**"* |
| **MFS** (Minimal Failing Subquery) | 이미 0건이 되는 **최소** 조건 부분집합 | *"+ 파일시스템 80% 이상: **0대** ← 여기서 끊겼습니다"* |

**알고리즘 사실 2건**(`claims.md` C10):

1. **조건이 N개면 후속 질의 N회로 MFS/XSS를 찾을 수 있다** — 단순 순차 알고리즘이 존재한다.
   즉 **조건을 하나씩 더해가며 `COUNT(*)`를 재는 계단 프로브**가 교과서적 방법이다.
2. **모든 MFS를 찾는 것은 NP-hard이나 K를 고정하면 다항** → **프로브 상한은 정당한 설계**다.

**그리고 §5.1의 비용 실측이 이 방법을 값싸게 만든다** — 존당 SQL 실행 **p50 49~53ms**(감사로그 918건).
조건 3개면 `COUNT` 프로브 3~4회 = **약 150~200ms · LLM 0회**. 0건일 때만 발동하므로 정상 경로 비용은 **0**이다.

### 6.4 설계 — 조건 퍼널 프로브

```
질의: 지난 1달 CPU>=80 → 그중 파일시스템>=80 → 그중 급증
0건 확인 후에만 발동:

  P0  기간만 (조건 0개)                     → 전체 대상 1,204대
  P1  + CPU >= 80                           →            12대   ← XSS
  P2  + 파일시스템 >= 80                    →             0대   ← MFS(끊긴 지점)
  P3  + 급증                                → (표현 불가 — §6.5)

응답:
  조건에 해당하는 서버가 없습니다. 단계별로 확인한 결과는 다음과 같습니다.

  | 단계 | 조건                          | 잔존 |
  |------|-------------------------------|------|
  |  0   | 지난 1달 대상 서버            | 1,204 |
  |  1   | CPU 사용률 80% 이상           |    12 |
  |  2   | + 파일시스템 사용률 80% 이상  |     0 |  ← 여기서 끊겼습니다

  ⚠ "갑자기 상승"(급증) 조건은 현재 SQL로 표현할 수 없어 **조회에 반영되지 않았습니다** —
     위 결과는 급증 여부를 따지지 않은 것입니다.

  → 2단계를 완화하려면: 파일시스템 임계값을 낮추거나(예: 70%) 기간을 넓혀 보세요.
```

**설계 제약 6건**

| # | 제약 | 근거 |
|---|---|---|
| 1 | **0건일 때만 발동** — 결과가 있으면 프로브를 돌리지 않는다 | 정상 경로 비용 0 |
| 2 | **LLM을 쓰지 않는다** — 조건 제거는 SQL 텍스트 조작이 아니라 `filter_conditions`/SMQ 레벨에서 **코드가 재조립**한다 | D-035 · Known Mistakes(*"스키마·조인이 고정된 쿼리는 코드가 runnable SQL을 직접 조립"*) |
| 3 | **프로브 수 상한 K**(기본 5) — 초과 시 절단하고 **절단 사실을 응답에 명시** | C10 ②(NP-hard) · 침묵 절단 금지 |
| 4 | **프로브 실패는 침묵하지 않는다** — 실패하면 *"단계별 진단을 수행하지 못했습니다(사유)"* 로 표기하고 현행 정형문으로 강등 | 침묵 폴백 금지 |
| 5 | **표현 불가 조건은 별도 노출**(§6.5) — MFS/XSS로는 진단되지 않는 축이다 | `claims.md` C10 주의 |
| 6 | **`COUNT(*)`만 던진다** — 행을 가져오지 않는다(PII·토큰·전송량 0) | D-003 읽기전용 · 마스킹 정책 |

**조건 순서**: 사용자 질의의 **서술 순서**를 따른다(`filter_conditions` 배열 순서). LLM이 정한 순서가
아니라 원문 순서여야 사용자가 자기 문장과 대조할 수 있다 — 순서를 바꾸면 MFS 지점이 달라져 오해를 만든다.

### 6.5 ★ G-4 — 표현되지 못한 조건을 드러낸다

MFS/XSS는 **SQL로 표현된 조건들** 사이에서 끊긴 지점을 찾는다. *"갑자기 상승"* 처럼 애초에
**표현되지 못한 조건**은 그 틀로 진단되지 않는다 — 그런데 사용자에게는 이것이 더 중요한 정보다.

| 상황 | 현행 | 개선 |
|---|---|---|
| 급증 조건이 `>= 80`으로 눌렸다 | 침묵 — 사용자는 급증이 반영된 줄 안다 | *"급증 조건은 반영되지 않았습니다"* 명시 |
| 급증 조건이 통째로 탈락했다 | 침묵 | 동일 |

**판정 방법**(결정적): 원문에 **변화·급증 어휘**(갑자기·급증·급등·치솟·spike·튀는 등)가 있는데
`filter_conditions`에 대응 조건이 없거나 단순 임계 비교로만 존재하면 **미반영으로 표기**한다.
어휘 목록은 코드 리터럴이 아니라 **선언 파일**에 둔다(하네스 문서 표 29 G — *"정책이 코드가 아닌
선언적 파일에 있는가"*).

> **v5에서는** 급증 판정 구현을 *"시계열 기준선 정의가 필요한 별건"* 으로 미루고 여기서는
> **"안 됐다고 말하기"** 까지만 했다. 말하지 않는 것이 못 하는 것보다 나쁘다 — 못 하면 사용자가
> 다른 방법을 찾지만, 침묵하면 **틀린 답을 믿는다**.
>
> **v6에서 이 유보는 해제된다(요구 5 · §6.9)** — 미룬 근거가 *"무엇 대비 급증인가의 미정"* 이었고
> 사용자가 그것을 확정했다. 급증이 표현되면 이 절의 "미반영 경고"는 **퍼널의 정식 단계로 승격**한다(§6.13).
> **그럼에도 이 절은 폐기되지 않는다** — 플래그 off · 어휘 미등재 · 주 단위 보존기간 미달일 때의
> **안전망**으로 남는다.

### 6.6 실행 그룹과의 합성 — 두 축을 함께 본다

존이 여럿이면 퍼널을 **그룹별로** 낸다. 전역 퍼널보다 정보량이 크다:

```
| 단계                          | 은행존 | 공동존 |
|-------------------------------|--------|--------|
| 지난 1달 대상 서버            |   412  |   792  |
| CPU 사용률 80% 이상           |    12  |     0  |  ← 공동존은 여기서 끊김
| + 파일시스템 사용률 80% 이상  |     0  |     —  |  ← 은행존은 여기서 끊김
```

`group_results[*].row_count`(§4.9 · **1차에 이미 구현됨**)가 0단계 행 수를 이미 갖고 있으므로,
프로브는 그룹별로 돌리고 결과를 이 표로 합친다. **그룹 축은 재구현하지 않는다.**

### 6.7 G-5 — 0건 재생성을 끊는다

데이터가 진짜 없어서 0건이면 SQL 재생성은 이득이 없다. 퍼널 진단이 **XSS를 찾아낸 경우**
(= 상위 단계에는 데이터가 있다 = SQL이 정상 동작한다)는 **재생성 대상에서 제외**한다.

반대로 **P0(조건 0개)조차 0건**이면 그건 데이터 부재가 아니라 **SQL·스코프 오류 신호**다
(테이블·기간·존이 틀렸다) — 이때는 재생성이 정당하다. Known Mistakes의
*"필드 null은 데이터 부재가 아니라 생성 SQL 오류일 수 있음"* 이 여기 적용된다.

| P0 결과 | 해석 | 조치 |
|---|---|---|
| P0 > 0, 중간에서 끊김 | **정상 동작 · 데이터 없음** | 재생성 **중단** · 퍼널 표시 |
| P0 = 0 | 스코프·SQL 의심 | 재생성 **허용**(현행) + *"대상 자체가 0건 — 기간·존·테이블 확인 필요"* 표기 |

### 6.8 범위 — 이 절이 하는 것과 하지 않는 것

| 항목 | 판정 |
|---|---|
| 조건 단계별 잔존 건수(퍼널) 계산·표시 | **한다** |
| 끊긴 지점(MFS) 지목 · 완화 제안 | **한다** |
| 표현 불가 조건의 미반영 사실 노출 | **한다** ★ |
| 0건 재생성 중단 판정 | **한다** |
| 그룹별 퍼널 합성 | **한다**(§4.9 자산 재사용) |
| **급증·변화율 판정 구현** | **§6.9로 이관**(요구 5) — v5의 유보 해제. 이 절(요구 4)은 퍼널까지 |
| 자동 조건 완화 후 **재조회** | **하지 않는다** — 제안까지만. 임의 완화는 사용자가 묻지 않은 답을 준다 |
| 학습형 완화 추천(`EMPTY-ML-01` KDD 2004) | **미채택** — 라벨 부재 · D-035 |

### 6.9 ★ 급증 조건 — 실현 가능성 판정 (요구 5)

**판정: 가능하다. 데이터는 이미 있고, 파이프라인이 막고 있다.**

#### 실측 근거 4건

| # | 실측 | 출처 |
|---|---|---|
| ① | **시계열 통계가 3단으로 존재**하고 세 프로파일 전부 `allowed_tables`에 등재돼 있다 — `cmm_metric_stat_h`(YYYYMMDDHH) · `_d`(YYYYMMDD) · `_m`(YYYYMM). 각 행에 `min_val`·`avg_val`·`max_val` | `config/db_profiles/polestar_{b0,cm_gp,cm_yd}.yaml` |
| ② | ★ **"용량 대비 사용률"은 이미 계산되어 저장돼 있다** — `resource_type='server.FileSystems'` + `definition_name='Utilization'` 이 **퍼센트 사용률**이다. `used/total`을 따로 구할 필요가 없다 | 프로파일 b0 `:260` · gp `:268` · 카탈로그 `:92` |
| ③ | 기간 비교는 **표준 SQL로 표현 가능**하다 — 같은 테이블을 두 `stat_date`로 조건부 집계하면 **self-join 없이 1회 스캔**으로 기준·비교 기간을 나란히 낸다. 두 엔진 공통 문법 | — |
| ④ | ★ **그런데 현재 파이프라인이 명시적으로 막고 있다** | 아래 표 |

**④의 내역 — 세 겹으로 막혀 있다:**

| 겹 | 실측 | 위치 |
|---|---|---|
| 1 | 시맨틱 컴파일러가 *"전월 대비 증감"* 을 **커버리지 밖**(`{"pattern": "none"}`)으로 명시 선언 | `src/prompts/semantic_compiler.py:52` |
| 2 | 폴백되는 LLM 자유 생성 경로에 기간 비교 **few-shot 0건**(프로파일 예시 7건 중 없음) | `config/db_profiles/*.yaml` `query_examples` |
| 3 | `build_stat_month_block`이 **단일 기간 등호/BETWEEN을 강제**하고 *"`_h`/`_d`로 대체 금지"* 까지 지시 — 두 기간 비교와 **정면 충돌** | `src/utils/query_gen_common.py:179-215` |

**즉 G-4(급증 판정 코드 0건)의 원인은 데이터 부재가 아니라 표현 경로의 부재다.**
그리고 3겹은 *"막혀 있다"* 가 아니라 **"단일 기간을 전제로 설계됐다"** 는 뜻이다 — 확장 지점이 분명하다.

> **문헌 없음(정직한 기록)**: 이 판정을 문헌으로 뒷받침하려 두 차례 검색했으나(`docs/literature/search_log.md`
> 세션 3) 전부 노이즈였다. **이것은 연구 문제가 아니라 스키마·의미론 문제**다 — §6.3의 MFS/XSS와 달리
> 인용으로 강화할 대상이 아니고, 실측으로 답하는 것이 맞다. 실패한 두 질의는 자산으로 기록했다.

### 6.10 ★ "많이 높아졌다"의 조작적 정의 — 세 갈래가 결과를 바꾼다

사용자 정의를 SQL로 옮기기 전에 **결정할 것이 세 개** 있다. 어느 쪽을 고르는지에 따라 같은
데이터에서 **완전히 다른 목록**이 나온다. 여기서 틀리면 기능이 동작하면서 조용히 틀린 답을 준다.

#### ① 차분(%p) 대 비율(배수) — **차분이 맞다**

| 사례 | 차분 | 비율 | 운영상 의미 |
|---|---|---|---|
| 5% → 10% | +5%p | **2.0배** | 없다 — 여유가 90% |
| 75% → 85% | **+10%p** | 1.13배 | **크다** — 임계 접근 |

사용률은 **이미 퍼센트**라 비율로 읽으면 **저사용 파일시스템이 상위를 점령**한다. 게다가 사용자 원
질의는 *"파일시스템 사용률 갑자기 **80% 이상으로** 상승"* — **급증 AND 절대 임계 도달**의 합성이다.
절대 임계 게이트가 이미 붙어 있어 저사용 노이즈는 자동 배제된다.
→ **차분(%p) 기본 + 절대 임계 병행**(둘 다 만족해야 급증).

#### ② 기준 시점 — 직전 기간 1개 대 과거 N기간 분포

사용자안은 **단일 직전 기간**("1달 전")이다. 약점은 실재한다:

| 기준 기간이 | 결과 |
|---|---|
| 이미 높았다 (85% → 90%) | 급증을 **놓친다** (차분 +5%p) |
| 비정상 낮았다 (장애로 20% → 정상 복귀 70%) | 정상 복귀를 **급증으로 잡는다** |

분포 기준선(과거 N개월 평균±편차)이 이 둘을 완화한다. **그럼에도 v1은 사용자안(단일 직전 기간)을 쓴다.**
근거: 이 기능은 *"왜 안 나오는지 알고 싶다"*(요구 4)에서 출발했으므로 **설명 가능성이 핵심 가치**다.
*"7월 62% → 8월 85%, +23%p"* 는 사용자가 그대로 검산할 수 있고 *"과거 6개월 평균+2σ 초과"* 는 못 한다.
분포 기준선은 **창 크기·편차 배수라는 새 비결정 지점을 둘 더** 만든다(D-174 ② 전례 — 근거 없는
임계가 무기한 실동작).

#### ③ ★ 집계 축 — 서버 단위 평균은 급증을 희석한다

한 서버에 파일시스템이 여럿이다(`/`, `/var`, `/data`…). `cmm_metric_stat_*`은 **리소스(파일시스템) 단위
행**인데, 프로파일 few-shot 예시(b0 `:431`)는 **서버 단위 AVG 피벗**이다.

```
/var  30% → 90%   (+60%p — 급증)
/     40% → 42%
/data 20% → 21%
──────────────────────────────
서버 AVG 30% → 51%   ← 서버 단위로 접으면 임계 80%에 못 미쳐 **놓친다**
```

**급증 질의는 파일시스템 단위 행을 유지**하거나 **MAX로 집계**해야 한다. few-shot이 정답을 가리는
경우이며, Known Mistakes의 *"프롬프트 강제가 프로필 few-shot 예시와 경쟁해 반복 실패하면 그 쿼리
형태는 결정적 조립 대상"* 이 정확히 적용된다.

### 6.11 설계 — 결정적 조립 (LLM 우회)

**LLM 자유 생성이 아니라 코드가 runnable SQL을 직접 조립한다.** 근거 2건:
①조인 구조가 완전히 고정돼 있다(`cmm_resource` ×2 + `cmm_metric_stat_*`) — Known Mistakes
*"스키마·조인이 고정된 쿼리(폼필 피벗 등)는 코드가 runnable SQL을 직접 조립"* ②프롬프트로 하면
§6.9 ④-3의 **단일 기간 강제와 경쟁**한다(한 문서가 서로 다른 기간 규칙을 동시 지시하는 상태).

#### 조립 형태 — 조건부 집계 (self-join 불필요 · 1회 스캔)

PostgreSQL(gp/yd):

```sql
SELECT svr.name AS server_name, r.name AS filesystem,
       ROUND(MAX(CASE WHEN s.stat_date = :cur  THEN s.max_val END)::numeric, 2) AS cur_pct,
       ROUND(MAX(CASE WHEN s.stat_date = :base THEN s.max_val END)::numeric, 2) AS base_pct,
       ROUND((MAX(CASE WHEN s.stat_date = :cur  THEN s.max_val END)
            - MAX(CASE WHEN s.stat_date = :base THEN s.max_val END))::numeric, 2) AS delta_pp
  FROM cmm_resource r
  JOIN cmm_resource svr ON r.platform_resource_id = svr.id AND svr.resource_type = 'server.Server'
  JOIN cmm_metric_stat_m s ON r.id = s.resource_id
 WHERE r.resource_type = 'server.FileSystems' AND s.definition_name = 'Utilization'
   AND s.stat_date IN (:base, :cur) AND s.max_val BETWEEN 0 AND 1000
   AND r.dtime IS NULL AND svr.dtime IS NULL
 GROUP BY svr.name, r.name
HAVING MAX(CASE WHEN s.stat_date = :cur THEN s.max_val END) >= :threshold
   AND MAX(CASE WHEN s.stat_date = :cur  THEN s.max_val END)
     - MAX(CASE WHEN s.stat_date = :base THEN s.max_val END) >= :delta_pp
 ORDER BY delta_pp DESC
 LIMIT 100;
```

- `GROUP BY … r.name` 이 **§6.10 ③**(파일시스템 단위 유지)을 코드로 못 박는 지점이다.
- `HAVING` 두 항이 **§6.10 ①**(차분 + 절대 임계 병행)이다.
- `max_val BETWEEN 0 AND 1000` 은 프로파일이 요구하는 **쓰레기 값 상한 게이트**(기존 규약 준수).

DB2(b0)는 **세 지점이 다르다** — 이미 착지한 **Wave 1(방언 그물)이 지키는 대상**이다:

| 지점 | PostgreSQL | DB2 |
|---|---|---|
| 소수 보존 | `::numeric`(집계 후) | `CAST(… AS DOUBLE)` **집계 내부** |
| 행 제한 | `LIMIT 100` | `FETCH FIRST 100 ROWS ONLY` |
| 스키마 한정 | `polestar` | **`POLESTAR.`** 대문자 |

#### 배관

| 대상 | 변경 |
|---|---|
| `src/utils/query_gen_common.py` | `resolve_comparison_periods(user_query, today)` 신설 — `resolve_stat_month_range`의 형제. **(기준, 비교) 쌍**을 낸다("1달 전 대비" → 직전월−1, 직전월) |
| `config/change_terms.yaml` | **Wave 8 신설 파일을 확장** — 급증 어휘 + `default_delta_pp` + `default_baseline`(month) 선언. 정책은 코드가 아니라 선언 파일(하네스 표 29 G) |
| `src/db_adapters/polestar/assembler.py` | 비교 SQL 조립기(엔진 분기) |
| `config/knowledge/_base/catalog.yaml` · `src/prompts/semantic_compiler.py` | `pattern_b.comparison: {baseline, delta_pp}` 추가 — 컴파일러가 `{"pattern":"none"}` 대신 이 패턴으로 받는다 |

**`build_stat_month_block`은 비교 모드에서 주입하지 않는다** — 단일 기간 강제와 배타다. 폴백 경로용으로는
두 기간을 `IN` 필터로 못 박은 **비교 전용 블록**을 따로 쓴다.

### 6.12 한계 — 반드시 응답에 표기할 것 3건

침묵적 폴백 금지(Known Mistakes)를 여기 적용한다. **못 하는 것보다 말하지 않는 것이 나쁘다**(§6.5와 동일 원칙).

| # | 한계 | 왜 생기는가 | 응답 표기 |
|---|---|---|---|
| ① | **용량 변경을 대조하지 못한다** | 용량(`server.Disks/TotalSize`)은 EAV **현재값**이라 **과거 시점 용량이 없다**. 증설은 사용률을 낮춰 급증을 **상쇄해 누락**시키고, 축소는 **급증 오탐**을 만든다 | *"용량 변경 여부는 대조하지 않았습니다"* |
| ② | **기본 임계값을 썼다** | 질의에 *"많이"* 만 있고 수치가 없으면 선언 파일 기본값(제안 **+20%p**)을 쓴다 | *"급증 기준: 전월 대비 +20%p(기본값)"* — **값을 반드시 노출** |
| ③ | ★ **주 단위는 보존기간 미확인** | `week` grain이 `metric_tables`에 **없다**(hour/day/month만). 주 비교는 `_d`를 7일 집계해야 하는데 **세 프로파일 전부** *"`_d`/`_h`는 보관 기간이 짧아 과거 이력이 누락됩니다"* 라고 명시하고, **실제 보존일수는 저장소에 기록이 없다** | 주 단위 요청 시 *"주 단위 비교는 일별 통계 보존기간 확인 후 제공"* + **월 단위 대체 제안** |

③의 해소는 **읽기 전용 프로브 1회**로 끝난다:

```sql
SELECT MIN(stat_date), MAX(stat_date), COUNT(DISTINCT stat_date) FROM cmm_metric_stat_d;
```

**과금 외부 API가 아니므로 D-127 승인 게이트 대상은 아니지만, 운영 DB 접근이므로 사용자 확정 사항으로
둔다(U17).** 14일 이상이면 주 단위를 열고, 미달이면 **월 단위만** 제공하고 그 사실을 응답에 표기한다.
**"1주일 전 대비"를 이 값 없이 약속하지 않는다** — 약속하고 조용히 누락시키는 것이 최악이다.

### 6.13 요구 4와의 합성 — 급증이 퍼널의 정식 단계가 된다

급증이 **표현되면** G-4의 "미반영 경고"가 **퍼널의 한 단계로 승격**한다. 사용자 원 질의가 그대로 읽힌다:

```
| 단계                                   | 은행존 | 공동존 |
|----------------------------------------|--------|--------|
| 지난 1달 대상 파일시스템                |  1,204 |  2,310 |
| CPU 사용률 80% 이상 서버                |     12 |      0 |  ← 공동존은 여기서 끊김
| + 파일시스템 사용률 80% 이상            |      3 |      — |
| + 전월 대비 +20%p 이상 상승(급증)       |      0 |      — |  ← 은행존은 여기서 끊김

급증 기준: 전월 대비 +20%p(기본값) · 용량 변경 여부는 대조하지 않았습니다
```

이것이 요구 4와 요구 5가 **같은 §6에 있는 이유**다 — 요구 5는 요구 4가 노출한 결손(G-4)의 해소이고,
해소되면 요구 4의 퍼널을 **한 단계 더 정확하게** 만든다. 두 요구는 경쟁하지 않고 합성된다.


---

## 7. 문헌 검토 — 하네스 엔지니어링 관점 적합성 판정

> **목적**: §4·§5의 설계가 하네스 엔지니어링 문헌·원칙에 정합하는지 판정하고, **어긋난 곳을 고친다.**
> 저장소의 기존 문헌 자산(`plans/78` §3.4·§12 — ETCLOVG 7계층 · P1~P15 · 하네스 문헌 615편 기반
> 내부 명세)을 **렌즈로 재사용**하며, 그 자산에 **비어 있던 영역(사용자에게 묻는 행위의 비용·효과)** 은
> 신규 검색으로 보강했다(§7.4 · 서지 §17).
>
> **판정 요약**: 설계 방향은 **대체로 정합**하다. 특히 §5.5의 「계측 → 질문」 순서는 P13(측정 우선)과
> 정확히 일치하며, 이는 `plans/78` v4가 위반해 v5에서 정정한 바로 그 지점이다. **다만 문헌이 요구하는
> 정정 6건이 나왔고(§7.6), 그중 2건은 설계 변경**이다 — ①질문 게이트에 **품질(답변 가능성) 축 누락**
> ②**점진 결과 노출**이 질문의 1급 대안인데 "체감 완화"로 과소평가.

### 7.1 하네스 프로파일 판정 — 세 결정 축

`plans/78` §4.6.1과 같은 축으로 **82 자신의** 프로파일을 판정한다. 이 판정이 계층 투자 순위를 정한다.

| 축 | 82의 값 | 근거 | 함의 |
|---|---|---|---|
| **폭발 반경** | **낮음~중간** | 전부 읽기 전용 SELECT·REST GET. 78과 달리 **실호스트 명령이 없다**. 다만 탐색이 **인가 밖 존의 서버 존재 여부를 노출**할 수 있다(§12.4) | **G 최소 1건 필수**(존 RBAC), E는 얇게 |
| **과제 지평** | **낮음** | 복합 질의 1턴 완결(범위 질문 시 2턴). 장기 실행 아님 | C(압축·장기 메모리) 투자 순위 낮음 |
| **자율성** | **낮음** | 사용자 질의 응답형. 매 요청이 개입 지점 | O·V 부담 상대적으로 낮음 — **단 §5가 O를 필수로 끌어올린다** |

**판정: 82는 L·O 중심 프로파일이다** (78은 E·G 중심).

- **L(Lifecycle)** — 실행 그룹·관계(kind)·순차 제어·탐색→조회 파이프라인이 전부 L이다. 82의 본체다.
- **O(Observability)** — §5의 비용 게이트가 **관측을 선택이 아니라 전제**로 만든다. 그룹별 소요를
  못 보면 임계값이 근거 없는 상수가 된다. 78에서 O가 "Tier 1 선행조건"이었던 것과 같은 구조다.
- **G** — 탐색이 T(능력)를 넓히므로 P11(능력–제어 결합)에 따라 G를 함께 넓힌다 → 존 RBAC.
- **C** — 지평이 짧아 압축·메모리는 범위 밖. 단 **§5.6 재사용(캐시)** 은 C 항목이며 Tier 2다(§7.3).

### 7.2 ETCLOVG 커버리지 매핑

| | 계층 | 82가 하는 것 | 상태 |
|---|---|---|---|
| **E** Execution | — | 신규 없음(기존 프로세스·venv 경계 유지) | 해당 없음 |
| **T** Tooling | 탐색이라는 **새 능력** 추가 · `backend: sql\|rest\|mcp` 디스패치 | **확장** — P14 제약 준수 필요(§7.3-②) |
| **C** Context | 그룹별 스코프 격리(`_scoped_state`) · 탐색 결과 승계 | 부수적 |
| **L** Lifecycle | **실행 그룹·kind·순차 제어·파이프라인·범위 게이트** | **본체** |
| **O** Observability | 그룹별 `elapsed_ms`·p50/p90(S-A·S-B) · 진행 이벤트 | **신규 — §5의 전제** |
| **V** Verification | **Wave 1 방언 그물**(멀티 경로 검증 비대칭 해소) · 탐색 0건/다중 히트 판정 | **신규 — 기존 갭 해소** |
| **G** Governance | 탐색 순회 범위 = `allowed_db_ids` 교집합 · 미조회 범위 기록 | **최소 1건 필수** |

**갭 판정**: 78이 찾은 갭이 E·V·G에 몰렸던 것과 달리, 82는 **V·O가 원래 비어 있던 것을 스스로 채운다**
(Wave 1·Wave 3). 남는 갭은 **G 1건**(존 RBAC — §12.4에 이미 반영)과 **C 1건**(캐시 히트율 미관측 —
§7.3-① 정정 대상)이다.

### 7.3 P1~P15 대조 — 정합 / 정정

| 원칙 | 82의 상태 | 판정 |
|---|---|---|
| **P2** 해석은 LLM(닫힌 선택), **확정은 코드** | §4.4 탐색 그룹 전개는 결정적(LLM 미개입). 대상 값은 고정 SQL 결과 | **정합** |
| **P11** 능력(T)↑ ⇒ 제어(G)↑ | 탐색(T) 추가 + 존 RBAC(G) **같은 Wave**(Wave 5) | **정합** |
| **P12** 비싼 경로는 소수 질의만 (RouteLLM: 강모델 14% 호출로 비용 −85%) | §5.4 비용 게이트가 같은 원리 | **정합** — 단 §7.4-④ 참조(자동 라우팅이 질문보다 먼저) |
| **P13** 측정(Tier 1)이 최적화(Tier 2)보다 먼저 | 계측=Wave 3, 범위 선택(사전 라우팅=Tier 2)=Wave 6.5 | **정합 ★** — 78 v4가 위반했던 지점을 82는 처음부터 지켰다 |
| **P14** 런타임에 **도구 목록을 흔들지 않는다**(KV 캐시) | 그룹 축이 도구 목록을 바꿀 여지가 있고 **불변식으로 명문화되지 않았다** | **정정 ②** |
| **P15** 하네스는 쌓는 만큼 덜어낸다 | 플래그 5종·State 4필드·축 2개 추가. 만료일은 `output_file` 별칭에만 | **정정 ⑤** |
| ETCLOVG 체크리스트 ① 계층별이 아니라 **시스템 변경으로 통합 테스트** | §10는 단위 중심 | **정정 ④** |
| ETCLOVG 체크리스트 ③ **압축 시 정보 손실·출처 기록** | 탐색 0건/다중 히트는 기록. **범위 축소로 미조회된 범위는 미기록** | **정정 ③ ★** |
| ETCLOVG ③ (재적용 · 요구 4) | **조건 탈락도 정보 손실**이다 — 표현 못 한 조건을 침묵하면 사용자가 틀린 답을 믿는다 | **§6.5로 반영** |
| ETCLOVG ⑤ **재현율 우선**(먼저 다 포착하고 정밀도를 올린다) | §4.3 전수 순회 기본값 = 재현율 우선 | **정합** |
| Tier 규율(캐시=Tier 2) | §5.6이 **탐색 단기 캐시를 Wave 6.5 선행**으로 배치 — 히트율 계측 없이 켜는 순서 | **정정 ①** |

### 7.4 ★ 「사용자에게 묻는 행위」의 문헌 — 기존 자산에 없던 영역

`plans/78`의 문헌은 **에이전트가 무엇을 할지**에 집중하며, **사용자에게 되묻는 행위의 비용·효과**는
2차 인용 한 줄(*안드로이드 권한 대화상자 — 주의 17%·정확 이해 3%*)뿐이다. §5는 그 행위가 본체이므로
해당 문헌을 보강했다. 이 분야는 **대화형 정보검색(conversational IR)의 clarifying question(CQ)** 연구로
20년 가까이 축적되어 있다.

**① 무조건 묻는 것은 해롭다 — 측정치가 있다** ★

> **Zou et al., "Asking Clarifying Questions: To benefit or to disturb users in Web search?"**
> Information Processing & Management 60(2), 2022 · `10.1016/j.ipm.2022.103176` · 89명 사용자 실험
>
> - *"always showing all CQs may be risky and **low-quality CQs do disturb users**"*
> - *"only showing high-quality CQs while **hiding other CQs receives better gains with less effort**"*
> - *"always showing low-quality CQs ... comes with the cost of **nearly twice as much time** as they
>   spend in a session with fewer shown CQs"*
> - 인용(Aliannejadi et al. 2021a): *"asking more CQs does not always return the same amount of gain
>   (i.e., **lower rate of gain**)"*

**§5.4의 선택적 게이트가 이 처방과 정확히 일치한다.** 그리고 §5.1이 계산한 "탐색 단계에 물으면
150ms를 아끼려 사용자를 붙잡는다"는 판단은 이 문헌이 **세션 시간 약 2배**로 정량화한 위험의 특수 사례다.

**② 그러나 게이트에 「품질」 축이 빠져 있다 — 정정 ⑥ ★**

위 문헌의 핵심 변수는 **비용이 아니라 CQ의 품질**이다. 82의 §5.4 게이트는 **비용만** 본다.
문헌이 말하는 저품질 CQ = *사용자의 의도를 좁히지 못하는 질문*이며, 실무적으로는
**사용자가 답을 모르는 질문**이 그 전형이다.

이 축으로 82의 두 후보를 재판정하면:

| 축 | 사용자가 답을 아는가 | 답이 비용을 줄이는가 | 품질 | 판정 |
|---|---|---|---|---|
| **존** (`"abd00 서버의 프로세스"`) | **모를 수 있다** — 서버명만 준 사용자는 소재를 모르기 때문에 묻는 것이다 | 150ms(§5.1) | **저품질** | **묻지 않는다** |
| **솔루션** (`"WAS 메모리"`) | **안다** — 앱 관점을 원한다고 이미 말했다 | 백엔드 왕복 + 응답 조립 | **고품질** | **묻는다** |

→ §5.4 게이트에 **`answerable` 판정을 비용과 AND로 추가**한다(§7.6 정정 ⑥). 종전에는 §5.3 불변식 1
("전체 조회가 기본값")이 이를 **완화**했을 뿐 **발동을 막지는 못했다.**

**③ 반복하면 무의미해진다 — 습관화**

> **Vance et al., "Tuning Out Security Warnings: A Longitudinal Examination of Habituation Through
> fMRI, Eye Tracking, and Field Experiments"** · MIS Quarterly 42(2), 2018 · `10.25300/misq/2018/14124`
> **Anderson et al., "From Warning to Wallpaper: Why the Brain Habituates to Security Warnings"**
> · Journal of Management Information Systems 33(3), 2016 · `10.1080/07421222.2016.1243947`

반복 노출은 신경·행동 수준에서 주의를 급감시킨다(fMRI·시선추적·현장실험 3중 측정).
§5.3 불변식 4(**한 번만 묻고 승계**)가 이에 대응하지만, **발동률 자체를 관측하지 않으면 통제할 수 없다.**
→ **발동률을 Wave 3 계측 지표에 추가**하고 상한을 운영 관측 항목으로 둔다(§7.6 정정 ⑤).

**④ 자동으로 좁힐 수 있으면 묻지 않는다 — 선택적 탐색·자원 선택**

물을지 말지 이전에, **자동 축소가 어디까지 가능한가**가 먼저다. 이 문제는 분산 검색에서
**자원 선택(resource selection)·샤드 절단(shard cutoff)** 으로 오래 다뤄졌다.

> **Kulkarni, Tigelaar, Hiemstra & Callan — "Shard ranking and cutoff estimation for topically
> partitioned collections"** · CIKM 2012 · `10.1145/2396761.2396833` — 어느 샤드를 몇 개까지
> 조회할지를 **질의별로 자동 추정**
> **RouteLLM** (ICLR 2025 · P12) — 강모델 호출 14%로 비용 −85%, **사용자 개입 0**

82에서 자동 축소의 실체는 **`capabilities`/`requires` 매칭**(§4.5)이다 — `"WAS 메모리"` 질의는
*폴스타=위치 해소용, 지표=APM* 으로 **자동으로 좁혀지며 질문이 필요 없다.**
→ §5에 **자동 축소를 질문의 선행 단계로 명시**한다(§7.6 정정 ④). 질문은 자동 판정이 불가능한
잔여에만 쓴다.

**⑥ ★ 빈 결과 진단은 30년 된 확립 영역이다 — 발명하지 말고 차용한다** (요구 4 · §6)

> **Godfrey, "Minimization in Cooperative Response to Failing Database Queries"**
> IJCIS 6(2), 1997 · **인용 114** (`EMPTY-MFS-01`)
> **Fokou, Jean, Hadjali & Baron, "Handling failing RDF queries: from diagnosis to relaxation"**
> Knowledge and Information Systems 50(1), 2016 (`EMPTY-DIAG-01`)

> "When a query fails, it is more cooperative to **identify the cause of failure** rather than just
> reporting the empty answer set." (`claims.md` C9)

**MFS/XSS**로 정식화돼 있고, **조건 N개면 후속 질의 N회로 끊긴 지점을 찾는 단순 알고리즘**이 있다
(모든 MFS 열거는 NP-hard이나 K 고정 시 다항 — `claims.md` C10). 우리 설계의 `COUNT` 계단 프로브는
그 알고리즘이며, **프로브 상한 K는 문헌이 지지하는 설계**다.

**문헌이 다루지 않는 축이 하나 있다** — 원 문헌은 *SQL로 표현된 조건 집합* 사이의 실패를 다루므로,
**애초에 표현되지 못한 조건**(*"갑자기 상승"*)은 MFS/XSS로 진단되지 않는다. 그래서 §6.5를 별도로 둔다.
이 구분을 흐리면 "진단했다"면서 정작 사용자가 요청한 조건이 빠진 것을 놓친다.

**⑤ ★ 대기 시간의 정통 해법은 질문이 아니라 「점진적 결과」다** — 정정 ②(설계 변경)

> **Hellerstein, Haas & Wang, "Online Aggregation"** · SIGMOD 1997 · `10.1145/253260.253291` ·
> **인용 928** — 긴 질의를 끝까지 기다리지 않고 **부분 결과를 즉시·계속 제시**하고, 사용자가
> 충분하다고 판단하면 중단한다. 이후 progressive data analysis 계열로 이어진다
> (Dagstuhl 18411 · `10.4230/dagrep.8.10.1`).

82는 이미 `_emit_group_progress`(§4.9)를 갖고 있으나 **"체감 지연 감소(실 시간은 동일)"** 로
과소평가해 두었다. 문헌 관점에서 이것은 **질문과 동급의 1급 해법**이며, 오히려 우월하다:

| | 범위 사전 선택(질문) | 점진 결과 노출 |
|---|---|---|
| 사용자 개입 | **필요**(왕복 1회 + 재개 턴 프롬프트 재구성) | **없음** |
| 정보 손실 | **있음**(미선택 범위 미조회 — ETCLOVG ③ 위반 위험) | **없음**(전부 조회) |
| 사용자가 답을 몰라도 | **막힌다** | 무관 |
| 첫 결과까지의 시간 | 질문 왕복 후 첫 그룹 | **첫 그룹 완료 즉시** |
| 습관화 | 대상 | 무관 |

**은행존이 먼저 완료되면 그 결과를 즉시 보여주면 된다** — 이것이 요구 1의 "은행존을 먼저 조회"와
자연스럽게 결합하며, 사용자가 만족하면 공동존 조회 중 이탈해도 손실이 없다.
→ **`_emit_group_progress`를 "진행 표시"에서 「부분 결과 노출」로 승격**한다(§7.6 정정 ②).

### 7.5 렌즈의 지위 — 근거로 쓰는 것과 점검에만 쓰는 것

- **동료심사 문헌**(IPM 2022 · MISQ 2018 · JMIS 2016 · SIGMOD 1997 · CIKM 2012 · SIGIR 2019 ·
  WWW 2020 · ICLR 2025 등, §17.1)은 **설계 근거**로 쓴다.
- **ETCLOVG 7계층 / P1~P15 / 내부 명세**(`plans/78` §3.4·§12.4)는 **커버리지 점검 렌즈**로만 쓴다 —
  서베이는 TMLR 심사 중(preprint)이고, Tier·세 축·Must/Should/May 구분은 내부 문서의 재조직이다
  (78 §12.4가 명시). **2026-08-28 재확인 시도에서 OpenReview 페이지는 봇 검증으로 접근 불가**했으므로
  게재 상태는 78의 기재(TMLR under review)를 그대로 승계하며, **이 계획의 어떤 결정도 그 상태에
  의존하지 않는다.**
- **인용수는 레코드 단위 하한값**이다 — 같은 논문의 arXiv본·학회본이 분산되거나 학회본이 미색인된다
  (78 §12 서두의 실측 교훈). 문헌 선정은 인용수가 아니라 **게재처의 동료심사 여부 + 82와의 논리적
  적합성**으로 했다.

### 7.6 문헌이 요구한 정정 6건 — 적용 내역

| # | 정정 | 근거 | 적용 위치 |
|---|---|---|---|
| **①** | **탐색 단기 캐시를 §5.6 선행에서 분리** — 히트율 계측 전에는 켜지 않는다(Tier 2) | P13 · Tier 규율 · D-172 | §5.6 표 · Wave 6.5 선행 조건 · U12 |
| **②** ★ | **`_emit_group_progress`를 「부분 결과 노출」로 승격** — 그룹 완료 즉시 그 그룹의 결과를 실제로 내보낸다. 질문과 동급 대안 | **Online Aggregation(SIGMOD 1997·928)** · progressive data analysis | §4.9 · §5.6 · Wave 3 · Wave 7(UI) |
| **③** | **범위 축소 시 미조회 범위를 응답·감사에 기록**하고 **재확장 1클릭**을 제공 | ETCLOVG 체크리스트 ③(압축 손실·출처 기록) · 78 W2-7의 상쇄 조건 선례 | §5.3 불변식 6(신설) · Wave 6.5 |
| **④** | **자동 축소(`capabilities`/`requires`)를 질문의 선행 단계로 명시** — 질문은 자동 판정 불가 잔여에만 | **CIKM 2012 shard cutoff** · **RouteLLM(P12)** | §5.4 게이트 순서 · §5.6 표 |
| **⑤** | **신규 플래그 전부에 만료일 + 발동률 관측 지표 추가** | **P15** · D-161 ① · 습관화(MISQ 2018·JMIS 2016) | §5.4 표 · Wave 3 계측 · §12.5 |
| **⑥** ★ | **질문 게이트에 `answerable`(품질) 축을 비용과 AND로 추가** — 사용자가 답을 모르는 축은 묻지 않는다 | **IPM 2022**(저품질 CQ = 방해 · 세션 시간 ~2배) | §5.3 불변식 1 강화 · §5.4 게이트 |

**설계 방향 자체는 유지된다** — 실행 그룹·kind 구분·순차 실행·탐색·솔루션 파이프라인·비용 게이트는
전부 문헌과 정합하다. 정정은 **누락 보완(①③⑤⑥)과 우선순위 재배치(②④)** 이며, 이는 78 v5가
받은 정정("설계 의도 변경 없음 — 착수 순서와 구현 방식 정정이 핵심")과 같은 성격이다.

---

## 8. 실행 단계 (Wave)

각 Wave는 그 단계에서 멈춰도 회귀가 없어야 한다. 플래그 기본값 전환은 마지막 Wave에서만.

### Wave 1 — 멀티 경로 방언 그물 복원 (§1.1) · **선행 필수**

| 항목 | 내용 |
|---|---|
| 대상 | `multi_db_executor._validate_sql` · `config.py:314` |
| 작업 | ①`TEXT2SQL_MULTI_FULL_VALIDATION=true` 위양성 실측(`[멀티검증강화]` 거부 사유 집계) ②위양성 있으면 **최소 교집합**부터 기본 ON — 최우선은 **행 제한 절 엔진 방언**(`sql_validation._add_limit_clause` 재사용) ③멀티 경로 **실행 오류 후 재생성 1회**(단일 경로 백엣지와 대칭 — D-066) |
| 검증 | b0 대상에 `LIMIT`을 심은 픽스처가 자동 보정 또는 재생성됨 / 기존 멀티 테스트 회귀 0 / `arch_check --ci` 0 |

### Wave 2 — 레지스트리 축 + 분할기 (코드 동작 변화 0)

| 항목 | 내용 |
|---|---|
| 대상 | `config/db_registry.yaml` · `src/routing/registry.py` · `src/utils/query_gen_common.py` |
| 작업 | `solutions`(+`capabilities`/`requires`)·`zone_groups` 선언 / `registry.solutions()`·`zone_groups()`·`query_order()`·`capability_providers()` 파생 API / `partition_execution_groups()` / `ZONE_CLARIFY_OPTIONS.group`을 레지스트리 파생으로 전환 |
| 검증 | `partition_execution_groups([b0, gp, yd]) == [bank(b0), common(gp,yd)]` 순서 단언 / 기존 존 테스트 전량 그린(게이트 미변경) / D-131 리허설(`TestNewDBOnboardingRehearsal`) 그린 / `overfit_check` 0 |

### Wave 3 — 그룹 순차 실행 + 그룹별 결과 수집 (플래그 off 기본)

| 항목 | 내용 |
|---|---|
| 대상 | `src/state.py` · `src/nodes/multi_db_executor.py` · `src/nodes/result_merger.py` |
| 작업 | State 필드 추가(§4.7) / 그룹 루프(§4.9) / D-153 소급 복구 그룹 내부 이동 / `result_merger`가 버리던 `db_result_summary` 승격 반환 / **`_emit_group_packet`**(부분 결과 즉시 노출 — 문헌 정정 ②) / **그룹 계측 S-A·S-B**(§5.5 — `elapsed_ms` 수집 + `(solution, zone_group, kind, backend)`별 p50/p90 롤링 집계, `investigation_metrics` 형식 재사용) |
| 검증 | 그룹 1개 = **기존 동작 바이트 동일**(골든) / 그룹 2개 실행 순서가 `query_order`대로(mock 단언) / 그룹 실패 격리 / `sql_by_schema` 그룹 스코프 격리 / **계측이 그룹별로 분리 기록됨**(질문 기능과 무관하게 단독 유용 — §5.5) / **peer 그룹 완료마다 부분 결과가 실제로 방출됨**(정정 ② — `discovery`·`dependent`는 방출하지 않음) / 최종 응답은 append-only(부분 결과가 종합을 대체하지 않음) |

### Wave 3.5 — ★ 선행 스코프 전달 결손 수정 (§2) · **작고 독립적, 즉시 이득**

| 항목 | 내용 |
|---|---|
| 대상 | `src/utils/prior_targets.py` · `src/orchestration/subagents.py` · `src/orchestration/process_query.py` |
| 작업 | ①`build_prior_targets`가 **행별 `_source_db`를 db_id로 우선 사용**(없으면 현행 폴백 — 회귀 0) ②`_build_prior_targets_for_task`의 `db_ids[0]` 도장을 폴백으로 강등 ③`_resolve_db_id` 우선순위에 **`prior_targets[].db_id` 추가**(현행 ①②③보다 앞 — 이번 턴 선행 결과가 직전 턴 승계보다 강하다) |
| 검증 | 팬아웃 결과(`_source_db`가 b0·gp 혼재)에서 TargetRef의 db_id가 **행별로 다르게** 매겨짐 / `_source_db` 없는 행은 현행 동작 동일 / 프로세스 조회가 선행 결과의 존을 사용 / `prior_targets_enabled=false`면 **비트 동일** |
| 왜 분리하나 | 그룹 축·UI·파일과 **무관하게** 지금 고쳐도 이득이고, 이것 없이는 어떤 파이프라인도 엉뚱한 존을 친다 |

### Wave 4 — 그룹별 산출물 (kind별 정책)

| 항목 | 내용 |
|---|---|
| 대상 | `result_organizer` · `output_generator` · `result_aggregator` · `api/schemas.py` · `api/routes/query.py` |
| 작업 | 그룹별 `organized_data` / `_generate_document_file` **peer 그룹만** 반복(§4.6) / `output_files` + 단수 호환 별칭 / `_merge_finalized`·`_synthesize_finalized`의 "첫 파일만 채택" 해소 / `GET /query/{id}/download/{index}` · `/download-csv?group=` / `_results_store` 만료 sweep |
| 검증 | peer 2그룹 폼필에서 **파일 2개**가 각각 해당 존 행만 담음(**전 칼럼 확인** — 미리보기 일부 금지) / **dependent 파이프라인은 파일 1개**(빈 파일 0) / 그룹 1개 시 파일명·바이트 현행 동일 / 복합 task 2파일 보존(기존 결함 회귀 테스트) |

### Wave 5 — ★ 탐색 그룹 (요구 2의 1차 목표)

| 항목 | 내용 |
|---|---|
| 대상 | `src/utils/query_gen_common.py`(전개기) · `src/orchestration/process_query.py` · discovery 실행부 · `noise_gate/infrastructure/polestar_hostname_resolver.py`(**읽기 재사용만** — §12.1) |
| 작업 | `expand_discovery_groups()` 결정적 전개(§4.4) / `_run_discovery` 존 순회(인가 존 한정·`query_order`·`lookup_many` 재사용) / 0건·다중 히트 처리 / 가용성 병기(D-175) / `resolved_scope` → 후속 그룹 스코프 주입 |
| 플래그 | `COMPOSITE_DISCOVERY_ENABLED`(기본 **off** — 켜기 전 현행 안내 유지) · `COMPOSITE_DISCOVERY_EARLY_EXIT`(기본 off) |
| 검증 | `"abd00 서버의 프로세스를 조회하라"`(위치어 없음)가 **은행존→공동존 순회 후 발견 존의 API를 호출** / 미발견 시 순회한 존 목록을 명시한 안내(현행 *"위치를 지정해 주세요"* 대체) / 2존 발견 시 되묻기 / **인가 밖 존은 순회하지 않음**(RBAC 테스트) / 플래그 off면 **비트 동일** |

### Wave 6 — UI + 플래그 전환 (**사용자 승인 게이트**)

| 항목 | 내용 |
|---|---|
| 대상 | `src/static/js/app.js` · `.env`/`.env.example` |
| 작업 | peer 그룹 섹션·다중 다운로드 / **★ 부분 결과 점진 렌더**(그룹 완료 즉시 그 섹션을 채운다 — 정정 ②) / **탐색 경과 표시**(순회한 존·발견 존·가용성) / 존 선택 체크박스 복원 + 안내 *"함께 선택하면 은행존을 먼저 조회한 뒤 공동존을 조회하며, 완료된 존의 결과를 먼저 보여줍니다(각각 별도 파일)"* / 처리 현황 그룹 표시 / **`ZONE_GROUP_EXCLUSIVE` 기본값 `true`→`false`** |
| 선행조건 | **§11 PII 필터 판정** — 미해결이면 플래그는 `.env` 옵트인만, 기본값 유지 |
| 검증 | 폐쇄망 라이브 2종: ①은행존+공동존 동시 선택 → 존별 섹션 2개 + 파일 2개 ②`"abd00 서버의 프로세스"` → 탐색 경과 + 결과 1개 |

### Wave 6.5 — ★ 범위 사전 선택 (요구 3) · **재사용·계측 이후**

| 항목 | 내용 |
|---|---|
| 대상 | `src/utils/query_gen_common.py`(페이로드·게이트) · `src/api/routes/query.py`(4개 진입점) · `src/routing/semantic_router.py`·`src/orchestration/intent_planner.py`(`selected_scope` 결정적 고정) · `src/config.py` · `src/static/js/app.js` |
| 선행 | **§5.6 무개입 레버 3종**(부분 결과 노출 · 자동 축소 · 탐색 결과 승계) + **Wave 3 계측(S-A·S-B)** — 순서를 뒤집으면 재사용으로 없앨 수 있던 비용을 사용자에게 전가한다(§7.4-④·⑤). **탐색 단기 캐시는 선행에서 제외**(Tier 2 — 히트율 계측 후, 정정 ①) |
| 작업 | ①`build_scope_clarification` + `scope_question_or_none` 게이트(§5.4) — **`auto_narrow` 선행 + `_answerable` AND**(정정 ④⑥) ②`selected_scope` 구조화 필드 왕복(`selected_db_ids` 선례 동형 — LLM 재해석 0) ③**모호성 해소 질문 우선** 배타 판정(2연속 질문 금지) ④멀티턴 승계(한 번만 묻는다) ⑤S-C 표기 규칙(표본 n≥20 미만이면 **시간 문구 생략**) ⑥프론트 렌더(기본 선택 = 전체 조회) ⑦**미조회 범위 명시 + 재확장 1클릭 + 감사 `scope_narrowed` 기록**(정정 ③) ⑧**발동률 계측 지표**(정정 ⑤ — 습관화 통제) |
| 플래그 | `COMPOSITE_SCOPE_SELECT_ENABLED`(기본 **off**) · `COMPOSITE_SCOPE_SELECT_MIN_SECONDS`(초기 **30 — 잠정**, S-D 정산 대상) |
| 검증 | 좁힐 여지 1개면 비발동 / **탐색 그룹만 있으면 비발동** / 임계 미만 비발동 / **`auto_narrow`가 1개로 줄이면 비발동**(정정 ④) / **`axis=zone_group`은 항상 비발동**(정정 ⑥) / `zone_select`와 동시 발동 시 `zone_select` 우선 / 재개 턴·승계 턴 비발동 / **표본 부족 시 시간 문구 부재**(환각 금지) / **좁힌 턴의 응답에 미조회 범위 문구·재확장 버튼·감사 레코드 존재**(정정 ③) / 비대화 채널 비발동 / 플래그 off면 **비트 동일** |
| 왜 마지막인가 | 질문은 **사용자 시간을 쓰는** 최적화다. 비용 0인 재사용과 계측을 먼저 넣지 않으면 임계값이 추정치가 되고(D-174 ② 전례), 아낄 필요 없던 비용까지 묻게 된다 |

### Wave 7 — 솔루션 파이프라인 (요구 2의 2차 목표 · APM/DPM 준비)

| 항목 | 내용 |
|---|---|
| 대상 | `src/routing/semantic_router.py` · `src/prompts/semantic_router.py` · `intent_planner` · 그룹 실행자 디스패치 |
| 작업 | 라우터 출력에 `solution` 축 추가(D-169 계약 확장 — **절 상수 방식 유지, 렌더 바이트 동일 골든 보존**) / `capabilities`·`requires` 기반 결정적 파이프라인 전개(§4.5) / `backend: rest` 그룹 실행자 훅 / 미제공 능력의 **부분 응답 + 사유 명시** / 종합 응답 프롬프트에 "솔루션별 관점 차이" 지시 |
| 비범위 | **APM·DPM 커넥터 실체는 `plans/78` W7-2 + `plans/55` 소관.** 82는 *그룹 하나 추가로 끝나는 구조*까지만 만들고 등록 0건 상태의 회귀 0을 단언한다 |
| 범위 선택 연동 | 솔루션이 2개 이상 등록되면 Wave 6.5의 `axis="solution"` 게이트가 **자동으로 유효**해진다(신규 배선 0 — `capabilities` 파생). 등록 1개 상태에서는 "좁힐 여지 없음"으로 비발동 |
| 검증 | 가짜 솔루션(`fake_apm`, `backend: rest`, `requires: [host_location]`) 등록 시 G0→G1→G2 전개·순차 실행 성립 / **미등록 상태에서 프롬프트 렌더 바이트 동일** / WAS 질의가 apm 미등록 시 부분 응답 + 사유 |

### Wave 8 — ★ 빈 결과 원인 진단 (요구 4) · **1차 자산 위에 독립 착지**

| 항목 | 내용 |
|---|---|
| 대상 | `src/nodes/output_generator.py`(0건 응답) · `src/nodes/result_organizer.py`(G-5 재생성 판정) · `src/domain/empty_answer.py`(**신규** — 퍼널 조립 순수 로직) · `src/nodes/condition_probe.py`(**신규** — COUNT 프로브 실행) · `config/change_terms.yaml`(**신규** — 변화·급증 어휘 선언) |
| 선행 | **1차(Wave 1·2·3·3.5)만**. Wave 4~7과 무관하게 착지 가능하다 — 단일 그룹에서도 퍼널이 성립하고, 그룹별 합성은 `group_results`(1차 구현분)를 읽기만 한다 |
| 작업 | ①`filter_conditions` 누적 프로브(`COUNT(*)`, 상한 K=5, LLM 0회) ②XSS/MFS 판정 + 퍼널 표 조립 ③**표현 불가 조건 미반영 표기**(G-4 · 선언 어휘 대조) ④그룹별 퍼널 합성 ⑤G-5 재생성 판정(P0>0이면 중단, P0=0이면 허용+스코프 경고) ⑥프로브 실패 시 현행 정형문으로 강등 + 사유 표기 |
| 플래그 | `TEXT2SQL_EMPTY_DIAGNOSIS_ENABLED`(기본 **off**) · `TEXT2SQL_EMPTY_DIAGNOSIS_MAX_PROBES`(기본 5) · 만료일 **2027-02-20**(P15) |
| 비용 | 0건일 때만 · 조건 수 × `COUNT` 1회 ≈ **150~200ms**(§5.1 실측 기반) · LLM 0회 |
| 검증 | 조건 3개 질의가 2단계에서 끊기면 **표에 1,204 → 12 → 0과 "여기서 끊겼습니다"** 가 나온다 / 결과가 있으면 **프로브 0회**(mock 호출 수 단언) / 프로브 상한 초과 시 절단 사실 노출 / **변화 어휘가 있는데 대응 조건이 없으면 미반영 경고**(G-4) / P0=0이면 재생성 허용·P0>0이면 중단 / 프로브 실패 시 현행 문구로 강등 + 사유 / 그룹 2개면 존별 열 분리 / 플래그 off면 **비트 동일** |

### Wave 9 — ★ 급증 조건 표현 (요구 5) · **Wave 8 다음**

| 항목 | 내용 |
|---|---|
| 대상 | `src/utils/query_gen_common.py`(`resolve_comparison_periods` 신설) · `src/db_adapters/polestar/assembler.py`(비교 SQL 조립 · 엔진 분기) · `config/change_terms.yaml`(**Wave 8 파일 확장** — 임계·기준 선언) · `src/prompts/semantic_compiler.py` + `config/knowledge/_base/catalog.yaml`(`pattern_b.comparison`) |
| 선행 | **Wave 8** — `config/change_terms.yaml`(변화 어휘 선언)을 공유하고, 급증 단계가 §6.13에서 **퍼널의 한 단계로 합성**된다. 그 외에는 1차 자산만 필요(U1~U13과 무관) |
| 작업 | ①`(기준, 비교)` 기간 쌍 해석 ②조건부 집계 비교 SQL **결정적 조립**(엔진 분기 — `::numeric`/`CAST DOUBLE` · `LIMIT`/`FETCH FIRST` · `POLESTAR.` 한정) ③**차분(%p) + 절대 임계 병행** 판정(§6.10 ①) ④**파일시스템 단위 행 유지**(서버 AVG 접기 금지 — §6.10 ③) ⑤**한계 3건 응답 표기**(§6.12) ⑥`build_stat_month_block` **비교 모드 배제**(단일 기간 강제와 배타) ⑦컴파일러 커버리지 밖 선언에서 `comparison` 분리 |
| 플래그 | `TEXT2SQL_SPIKE_CONDITION_ENABLED`(기본 **off**) · `TEXT2SQL_SPIKE_DEFAULT_DELTA_PP`(기본 20) · 만료일 **2027-02-20**(P15) |
| 비용 | 프로브 없음 — **본 질의 SQL 한 건이 두 기간을 한 번에 스캔**한다(self-join·추가 왕복 0). LLM 호출 **감소**(결정적 조립이 생성 단계를 우회) |
| 범위 밖 | **분포 기준선**(과거 N기간 평균±편차 — §6.10 ②) · **주 단위**(U17 실측 후) · **용량 변경 대조**(과거 용량 부재 — §6.12 ①) · CPU/메모리로의 확장(같은 조립기로 가능하나 이번 범위는 파일시스템) |
| 검증 | 조립 SQL이 **엔진별로 세 지점 다르게** 나온다(캐스트·행제한·스키마) / **파일시스템 단위 행 유지**(서버 AVG로 접히면 실패) / 차분과 절대 임계를 **둘 다** 만족해야 포함(5%→10% 배제 · 75%→85% 포함) / 임계 미명시 시 **기본값이 응답에 노출** / 주 단위 요청은 **월 단위 대체 제안 + 사유** / `build_stat_month_block` **미주입** 단언 / 기준 기간 데이터 부재 시 급증 판정 불가 사실 노출(NULL 침묵 금지) / 플래그 off면 **비트 동일** |

---

## 9. 착수 순서와 독립성

```
Wave 1  ─┬──────────────────────────────────────────────────────→ (방언 그물 · 단독 조회에도 이득)
Wave 2  ─┴─ Wave 3 ─┬─ Wave 4 ─┬──────────────────→ (열거형 = 요구 1)
            (계측)   │          └─ Wave 6 ★승인 게이트
Wave 3.5 ───────────┴─ Wave 5 ─┴─ Wave 7 ─────────→ (탐색형·솔루션 = 요구 2)
                          │            │
                          └─ §5.6 재사용 ┴─ Wave 6.5 ─→ (범위 선택 = 요구 3)

Wave 8 ──(1차만 선행)──┬───────────────────────────────→ (0건 진단 = 요구 4)
                       └─ Wave 9 ─────────────────────→ (급증 조건 = 요구 5)
```

- **Wave 1·3.5는 다른 것을 기다리지 않는다.** 둘 다 현행 결함 수정이고 회귀 0이다.
- **요구 2(탐색형)의 최소 경로는 `3.5 → 5`** — 그룹 UI·파일 분리 없이도 성립한다.
- **요구 1(열거형)의 최소 경로는 `1 → 2 → 3 → 4 → 6`.**
- Wave 7은 APM 등록 이전에도 **구조·테스트까지** 착지 가능하다(78 트랙 B 선례와 동형).
- **요구 3(범위 선택)의 경로는 `3(계측) → 5.6 재사용 → 6.5`** 다. Wave 7(솔루션 2개 이상 등록) 이후에야
  `axis="solution"`이 실제로 발동하지만, **존 축 폴백과 게이트·페이로드·왕복은 그 전에 착지 가능**하다.
- **순서를 뒤집지 않는다**: 재사용(비용 0·사용자 무개입) → 계측(임계 근거) → 질문(사용자 시간 소모).
- **Wave 8(요구 4)은 Wave 4~7과 독립**이다 — 1차 자산만으로 성립하므로 **U1~U13 확정을 기다리지 않는다.**
  단일 그룹에서도 퍼널이 나오고, 그룹별 합성은 이미 구현된 `group_results`를 읽기만 한다.
  **요구 4는 2차 대기 중에 먼저 착지시킬 수 있는 유일한 항목**이다.
- **Wave 9(요구 5)는 Wave 8 다음**이다 — 변화 어휘 선언 파일을 공유하고, 급증 단계가 퍼널에 합성된다
  (§6.13). 반대 방향 의존은 없다: Wave 8은 Wave 9 없이도 완결된다(급증은 "미반영" 표기로 처리 — §6.5).
  **`8 → 9` 는 2차(U1~U13)와 무관한 독립 레인**이며, 둘 다 플래그 off 기본이라 승인 게이트가 없다.

---

## 10. 검증 전략

### 10.1 단위·통합 (전부 mock — LLM·네트워크 미사용)

| 스위트 | 내용 |
|---|---|
| `tests/test_semantic_routing/test_registry_config.py` | `solutions`·`zone_groups`·`capabilities`/`requires` 파생 API · `query_order` |
| `tests/test_orchestration/test_execution_groups.py` (신규) | `partition_execution_groups` 순서·경계 · 그룹 1개 폴백 · `expand_discovery_groups` 발동 조건 4항 |
| `tests/test_nodes/test_multi_db_group_loop.py` (신규) | 그룹 순차 순서 · 실패 격리 · `sql_by_schema` 그룹 스코프 |
| `tests/test_composite/test_prior_scope_db_id.py` (신규) | **행별 `_source_db` → TargetRef.db_id** · `_source_db` 부재 시 폴백 동일 · `_resolve_db_id` 우선순위 |
| `tests/test_composite/test_discovery_group.py` (신규) | 존 순회 순서 · 0건 안내 문구 · 다중 히트 되묻기 · **인가 밖 존 미순회** · 플래그 off 비트 동일 |
| `tests/test_document/test_group_output_files.py` (신규) | peer 그룹별 파일 N개(**전 칼럼** 대조) · **dependent는 1개** · 그룹 1개 시 파일명 동일 |
| `tests/test_api/test_group_download.py` (신규) | `/download/{index}` · `/download-csv?group=` · 구 엔드포인트 = index 0 |
| `tests/test_orchestration/test_scope_select.py` (신규) | 비용 게이트 4조건(§5.4) · **탐색 그룹만이면 비발동** · `zone_select` 우선 배타 · 재개·승계 턴 비발동 · 비대화 채널 비발동 · **표본 n<20이면 시간 문구 부재** · `selected_scope` 결정적 고정 · 플래그 off 비트 동일 |
| `tests/test_observability/test_group_metrics.py` (신규) | 그룹별 `elapsed_ms` 분리 기록 · p50/p90 집계 · 표본 수 노출 |
| `tests/test_domain/test_empty_answer.py` (신규 · Wave 8) | 퍼널 조립 순수 로직 — XSS/MFS 판정 · 상한 절단 표기 · 변화 어휘 대조(G-4) · P0=0 분기 |
| `tests/test_nodes/test_condition_probe.py` (신규 · Wave 8) | **결과 있으면 프로브 0회** · `COUNT`만 던짐(행 미조회) · 프로브 실패 시 강등+사유 · 조건 순서=원문 순서 · 그룹별 합성 |
| `tests/test_orchestration/test_zone_group_exclusive.py` (기존 13종) | **플래그 on 회귀 0** + off 분할 신규 |
| `tests/test_orchestration/test_zone_post_gate.py` (기존 20건) | 후단 게이트 비발동 경계 불변 |
| 골든 | 그룹 1개 경로의 응답·파일·라우터 프롬프트 **바이트 동일** |

### 10.2 ★ 시스템 수준 수용 시나리오 (문헌 정정 ④ · ETCLOVG 체크리스트 ①)

> *"Harness layers are coupled in ways that make local optimization fragile."* — 계층별 단위 그린은
> **전체 시스템 품질을 보장하지 않는다.** 서베이의 실무 체크리스트 첫 항목은 *"계층별이 아니라
> 시스템 변경으로 통합 테스트"* 다. Wave별 단위 테스트(§10.1) 위에 **질의 형태 3종의 종단 시나리오**를
> 수용 기준으로 둔다.

| # | 시나리오 | 통과 기준 |
|---|---|---|
| **S1 열거형** | `"은행존과 공동존의 모든 서버 OS 종류/버전"` | 은행존 → 공동존 순차 실행 · **은행존 결과가 공동존 완료 전에 화면에 나타남**(정정 ②) · 존별 파일 2개(전 칼럼 대조) · 종합 응답 · 한 존 실패 시 다른 존 정상 + 사유 노출 |
| **S2 탐색형** | `"abd00 서버의 프로세스를 조회하라"` (위치어 없음) | **범위 질문 없이** 은행존→공동존 순회 · 발견 존의 API만 호출 · 위치 부기 · 파일 1개(빈 파일 0) · 미발견 시 순회한 존 목록 명시 · **인가 밖 존 미순회** |
| **S3 솔루션 파이프라인** | `"abd00 서버의 WAS 메모리 사용량"` (가짜 apm 등록) | 탐색 → 미들웨어 식별 → APM 조회 순차 · `auto_narrow`로 폴스타 지표 그룹 제거 · apm 미등록 시 **부분 응답 + 사유**(환각 0) |
| **S4 질문 경로** | 솔루션 3개 등록 + 임계 초과 질의 | 질문 1회만 발동 · 기본값 전체 조회로 진행 가능 · 좁힌 턴에 **미조회 범위 문구 + 재확장 버튼 + 감사 레코드** · 같은 스레드 후속 턴 재질문 0 |
| **S5 0건 진단** | `"지난 1달 CPU 80% 이상 서버 중 파일시스템 갑자기 80% 이상"` (데이터 없음) | 단계별 잔존 표 · **끊긴 지점 지목** · **급증 조건 미반영 경고**(G-4) · 완화 제안 · 결과 있으면 **프로브 0회** · 플래그 off면 현행 문구 |
| **S6 급증 조건** | `"지난 1달간 CPU 80% 이상 서버 중 파일시스템 사용률 갑자기 80% 이상 상승한 리스트"` (요구 4·5 원 질의 · 데이터 있음) | 급증이 **퍼널의 정식 단계**로 나타남(미반영 경고 아님) · **파일시스템 단위 행**(서버 AVG 아님) · **기본 임계값 노출** · 용량 변경 미대조 각주 · b0는 `FETCH FIRST`+집계내 `CAST DOUBLE`, gp는 `LIMIT`+`::numeric` · 주 단위 요청 시 월 단위 대체 제안 |
| **S0 회귀** | 그룹 1개로 귀결되는 기존 질의 전량 | 응답·파일·프롬프트 **바이트 동일** |

**S1~S4는 개별 Wave의 수용 기준이 아니라 계획 전체의 수용 기준**이다 — 각 Wave가 그린이어도
S1~S4가 실패하면 계층 결합 문제이며, 그 경우 **단일 Wave를 고치지 말고 결합 지점을 찾는다**(P11).


### 10.3 활성 DB 전제 — 개발 환경으로는 검증할 수 없다

```
.env:67          ACTIVE_DB_IDS=polestar                                    ← 레거시 단일 id
.env.example:223 # ACTIVE_DB_IDS=polestar_b0,polestar_cm_gp,polestar_cm_yd  ← 운영 형태
```

`build_zone_clarification`은 활성 DB 교집합을 취하므로 `active={"polestar"}`면 선택지 0개 → `None` →
**존 역질문 자체가 발동하지 않는다.** `docs/18_known_mistakes.md:75`가 같은 함정을 기록했다.
→ **프로세스 한정 `ACTIVE_DB_IDS` override**(`.env` 미수정 — 기존 선례) + 폐쇄망 라이브 2단.
**개발 환경 그린은 수용 근거가 아니다.**

### 10.4 상시 게이트

```bash
python -m pytest -q --ignore=tests/e2e        # 본체 + noise_gate
python scripts/arch_check.py --ci             # 계층 규칙
python scripts/overfit_check.py               # 폴스타 과적합 재유입 차단
git worktree add <dir> HEAD                   # 기준선 대조 (git stash 금지)
```

---

## 11. 선행조건 — FabriX PII 필터 b0+gp 차단 (미종결)

### 11.1 실측된 사실과 아직 모르는 것

| 사실 | 출처 |
|---|---|
| `B0+YD 정상 · GP 단독 정상 · GP+YD 정상 · **B0+GP만 차단**` | D-143 후속3 |
| 로컬 PII 규칙 스캔 무일치 — 서버측 규칙이 더 넓음 | D-155, 후속1 |
| ISO 타임스탬프 단독 probe **통과** → "날짜부 단독 매칭" 기각, **조합/총량 의존**으로 좁혀짐 | D-155 후속4 |
| 클라이언트 응답에 탐지 문자열이 원리적으로 없음 — 확정 경로는 `filter_log_id` → FabriX 관리 콘솔 | D-155 후속4 ① |
| 진단 자산 완비: `dump_blocked_payload` · `scripts/pii_probe.py`(이등분+ddmin) · 섹션별 진단 | D-155 후속1~5 |

**유력 가설 하나를 코드 실측으로 기각했다**: `prior_block`·`value_index`는 `_prepare_multi_run`에서
**직전 턴/선행 task 결과로부터** 만들어지며(`multi_db_executor.py:231-238`) 같은 런의 b0 결과가 gp
프롬프트로 흘러들지 않는다. 따라서 *"b0의 DB2 타임스탬프가 gp 프롬프트에 섞인다"* 는 이 경로로는 성립하지 않는다.

| # | 남은 가설 | 검증 방법 (기보유 자산) |
|---|---|---|
| H1 | **서버측 세션·누적 의존** — b0 요청이 FabriX 정책 카운터를 올리고 후속 gp 요청이 차단. D-155 후속4의 "조합/총량 의존"과 정합 | 같은 프로세스에서 b0 → gp 연속 전송 재현. **그룹별 LLM 클라이언트 신규 생성**(§4.9 A안)으로 세션이 갈리는지 대조 |
| H2 | 스키마 캐시 백필이 b0 실행 후 gp 프롬프트 재료를 바꾼다 | 캐시 비활성 대조 |
| H3 | 재현 불안정 — 정책 갱신 시점과 겹친 우연 | 3회 반복 재현율 |

### 11.2 차단 해결 없이도 개선인 이유

| 항목 | 현행(상호배타) | 개선 후(그룹 격리) |
|---|---|---|
| 동시 조회 요청 | **거부** — 두 번 질의해야 함 | 수락 — 순차 실행 |
| b0 성공·gp 차단 시 | (조합 자체가 불가) | **은행존 결과 정상 제공 + 공동존 섹션에 차단 사유 명시** |
| 사용자가 아는 것 | "동시 조회는 안 된다"(이유 불명) | "공동존이 PII 필터로 막혔다"(사유·`filter_log_id` 안내) |
| 진단 재료 | 조합 불가로 재현 불능 | 매 실행이 H1 재현 시도가 되고 덤프가 쌓인다 |

**탐색형은 이 이슈와 무관하다** — discovery는 LLM을 쓰지 않는 고정 SQL이고, 후속 그룹은 존 하나로
좁혀져 b0+gp 조합이 성립하지 않는다. **Wave 3.5·5는 PII 게이트 밖이다.**

### 11.3 승인 게이트

- **Wave 1~5는 PII 이슈와 무관 — 승인 없이 진행 가능** (회귀 0, 단독 조회에도 이득).
- **Wave 6의 플래그 기본값 전환은 사용자 승인 사항**:
  - **(a) 옵트인 우선** — 기본값 `true` 유지, 운영 `.env`에서 `ZONE_GROUP_EXCLUSIVE=false`. 되돌리기 = env 한 줄. **권고안**
  - (b) 기본값 즉시 전환 — 부분 배포 환경(`getattr` 폴백)이 자동으로 새 동작을 받으므로 배포 순서 관리 필요
- **§11.1 H1 재현은 FabriX 실 호출**을 수반한다 → **D-127 과금 외부 API 승인 게이트.
  건마다 사용자 승인 후 `RUN_E2E=1`.** 에이전트가 임의 실행하지 않는다.

---

## 12. 위험과 되돌리기

### 12.1 ★ `plans/81`과의 동시 편집 충돌 (작업 트리 실측 2026-08-28)

`git status` 실측 — **Plan 81 구현이 이 작업 트리에 미커밋 상태로 진행 중**이다:

```
 M noise_gate/infrastructure/polestar_hostname_resolver.py   (+189줄 — resolve_with_status / lookup_many / HostLookup)
 M src/config.py                                             (+13줄 — availability_precheck_* 3종)
?? src/domain/host_availability.py
?? tests/test_composite/test_host_availability.py, test_host_status_lookup.py
```

Wave 5(탐색 그룹)는 **같은 파일을 재사용**한다. 소유권 계약:

| 자산 | 소유자 | 82의 접근 |
|---|---|---|
| `polestar_hostname_resolver.py` (`resolve_with_status`·`lookup_many`·`HostLookup`) | **`plans/81`(D-175)** | **읽기 재사용만.** 시그니처 변경·필드 추가 금지 |
| `src/domain/host_availability.py` | `plans/81` | 판정 결과 소비만 |
| `CompositeConfig.availability_*` | `plans/81` | 참조만. 82는 `COMPOSITE_DISCOVERY_*`를 **새 키로** 추가 |
| 존 순회 루프·`resolved_scope` | **`plans/82`(D-176)** | 신규 |

**착수 전 필수**: Plan 81 변경이 커밋된 뒤 `git worktree add <dir> HEAD`로 기준선을 잡고 시작한다
(Known Mistakes: 클린 기준선은 `git stash`가 아니라 격리 사본). 병렬 트리에서 회귀가 나면
**자기 파일만 얹어 책임 소재를 확정**한다.

### 12.2 지연 시간

| 형태 | 증가분 | 완화 |
|---|---|---|
| 열거형 2그룹 | LLM SQL 생성 1회→2회 + 스키마 분석 1존→3존(존당 ≤20s, D-154 bound) | 순차는 요구사항 — 병렬화하지 않는다. 그룹 진행 이벤트로 체감 완화 + **범위 선택(§5)로 사용자가 좁힐 수 있게** |
| 탐색형 | **존당 SELECT ~50ms(실측 §5.1), LLM 0회** | 무시할 수준. `lookup_many`는 IN절 1쿼리. **범위 질문 비발동 대상** |
| SQL 실행 자체 | p50 49~53ms/존 (감사로그 918건 실측) | 존 수 증가의 비용 요인이 아님 — 최적화 대상 아님 |
| 솔루션 파이프라인 | 그룹 수 × 그룹 비용 | 각 그룹이 이미 좁혀진 스코프만 조회 |

현재 상한: `API_QUERY_TIMEOUT=240` / `API_FILE_QUERY_TIMEOUT=300`(`.env:119,123`).
Wave 6 라이브 계측 후 상향 판단 — **추정으로 올리지 않는다.**

### 12.3 SSE 무이벤트 구간 확대

SSE는 **이벤트 fetch당** 타임아웃 가드만 있고 **전체 타임아웃 가드가 없다**(`query.py:920-931` 실측).
Known Mistakes의 *"장시간 실행 경로는 전체 타임아웃 가드 필수"* 에 해당하는 **기존 갭**이며, 그룹 루프가
노드 체류 시간을 늘려 이를 확대한다.
→ **`_emit_group_packet`을 Wave 3 수용 기준에 포함**한다. 빼면 D-154가 겪은 SSE 타임아웃이 재발한다.
→ 스트림 전체 타임아웃 가드 추가는 **이 계획 밖**(별건 — 스코프 확대 방지).

### 12.4 탐색 고유 위험

| 위험 | 대응 |
|---|---|
| **존 RBAC 누출** — 권한 밖 존을 순회하면 그 존의 서버 존재 여부가 새어나간다 | 순회 범위 = `state.allowed_db_ids` ∩ 활성 폴스타 존. **테스트로 고정**(§10.1) |
| 동명 호스트 다중 존 | 임의 선택 금지 — 발견 존으로 좁힌 선택 UI로 되묻기 |
| 탐색 실패의 침묵 강등 | 0건은 **순회한 존 목록을 명시**한 안내. 예외는 사유와 함께 현행 안내로 강등(침묵 금지) |
| 존 수 증가 시 비용 선형 증가 | `lookup_many` IN절 1쿼리 + 옵트인 조기 종료 |
| **범위 질문이 오히려 느려짐** — 사용자 왕복이 절감보다 크다 | 비용 게이트(§5.4)로 임계 미만·좁힐 여지 없음·탐색 전용은 비발동. 임계는 계측 후 정산(S-D) |
| **사용자가 모르는 것을 묻는다** — abd00이 어느 존인지 모를 수 있다 | "전체 조회"를 **첫 선택지·기본값**으로 고정(§5.3 불변식 1). 좁히기는 선택적 최적화이지 진행 조건이 아니다 |
| **2연속 질문** — 모호성 해소 후 범위 선택이 또 뜬다 | `zone_select` 우선 배타 판정 + 멀티턴 승계로 한 번만 묻는다(§5.3 불변식 2·4) |
| **예상 시간 환각** — 근거 없는 "약 40초"를 보여준다 | 표본 n≥20 미만이면 시간 문구를 **생략**하고 그룹 수만 표기(S-C) |
| **퍼널 프로브가 오히려 느려짐** — 조건이 많으면 프로브가 늘어난다 | 0건일 때만 발동 + 상한 K=5 + `COUNT`만(행 미조회). 존당 SQL p50 ~50ms 실측 기준 최악 ~250ms |
| **퍼널이 틀린 원인을 지목** — 프로브 SQL이 원 SQL과 다른 스코프를 보면 오진이 된다 | 프로브는 원 `filter_conditions`/SMQ에서 **조건만 제거**해 재조립(코드) — 테이블·조인·기간은 원본 유지. 재조립 실패 시 진단 포기(강등) |
| **G-4 오탐** — 변화 어휘가 있는데 실제로는 조건이 반영된 경우 경고가 뜬다 | 판정은 *어휘 존재 AND 대응 조건 부재*. 오탐의 대가는 불필요한 주의 문구이고, 누락의 대가는 **틀린 답을 믿는 것**이라 비대칭 — 오탐 쪽으로 기운다 |

### 12.5 되돌리기 경로

| Wave | 되돌리기 |
|---|---|
| 1 | `TEXT2SQL_MULTI_FULL_VALIDATION=false` (env 한 줄) |
| 2 | 레지스트리 `solutions`/`zone_groups` 블록 제거 — 소비처가 없으면 파생 API가 빈 목록 |
| 3~4 | `execution_groups`가 1개면 현행 경로와 동일 — 분할기를 단일 그룹 반환으로 되돌리면 무효화 |
| 3.5 | `_source_db` 우선 사용을 폴백으로 되돌림(1줄 조건) |
| 5 | `COMPOSITE_DISCOVERY_ENABLED=false` (env 한 줄) |
| 6 | `ZONE_GROUP_EXCLUSIVE=true` (env 한 줄) |
| 6.5 | `COMPOSITE_SCOPE_SELECT_ENABLED=false` (env 한 줄) — 계측(S-A·S-B)은 남겨도 무해 |
| 8 | `TEXT2SQL_EMPTY_DIAGNOSIS_ENABLED=false` (env 한 줄) — 0건 응답이 현행 정형문으로 복귀 |

### 12.6 ★ 덜어내기 계획 — P15 (문헌 정정 ⑤)

> *"하네스는 쌓는 만큼 덜어낸다"* — 모든 하네스 구성요소는 **모델·시스템 한계에 대한 가정**이며,
> 그 한계가 사라지면 순비용이 된다(P15 · ETCLOVG 실무 체크리스트 ⑤ · D-161 ①).
> 82는 플래그 3종·State 4필드·레지스트리 축 2개를 추가하므로 **처음부터 제거 계획을 붙인다.**

| 신설 항목 | 만료일 | 도래 시 판단 | 제거 조건 |
|---|---|---|---|
| `output_file`/`output_file_name` 단수 별칭 | **2027-02-20** | 삭제 또는 사유부 연장 | 모든 소비처가 `output_files` 사용 |
| `COMPOSITE_DISCOVERY_ENABLED` | **2027-02-20** | **상수화(항상 on)** 유력 | 탐색이 기본 동작으로 확정되면 플래그 자체가 부채 |
| `COMPOSITE_DISCOVERY_EARLY_EXIT` | **2027-02-20** | 실사용 0이면 삭제 | 전수 순회로 확정(재현율 우선 — ETCLOVG ⑤) |
| `COMPOSITE_SCOPE_SELECT_ENABLED` | **2027-02-20** | **삭제 후보 1순위** | 아래 판정 참조 |
| `COMPOSITE_SCOPE_SELECT_MIN_SECONDS` | 정산 후 재평가 | 계측 분포로 확정 | 질문 기능 제거 시 동반 삭제 |
| `TEXT2SQL_MULTI_FULL_VALIDATION` | 기본 ON 전환 시 | **상수화** | 위양성 0 확인 후 |
| `TEXT2SQL_EMPTY_DIAGNOSIS_ENABLED` | **2027-02-20** | **상수화(항상 on)** 유력 | 0건 진단이 기본 동작으로 확정되면 플래그가 부채 — *"데이터 없음"만 내보내는 동작으로 되돌릴 이유가 없다* |
| `ZONE_GROUP_EXCLUSIVE` | ② 종결 시점 또는 2027-02-20 중 이른 쪽 | 삭제 | PII 차단 원인 종결 |

**범위 선택(질문)이 가장 먼저 제거 후보인 이유** — 그 존재 근거가 **다른 개선으로 소멸할 수 있다**:

1. **부분 결과 노출(정정 ②)** 이 체감 대기를 충분히 줄이면 질문의 효용이 사라진다.
2. **자동 축소(정정 ④)** 가 넓어지면(솔루션 `capabilities` 정밀화) 물을 여지가 줄어든다.
3. **백엔드가 빨라지면**(스키마 캐시 히트율 상승 · 프롬프트 축소 · 모델 개선) 임계에 걸리지 않는다.

판정 방법은 P13에 따라 일률적으로 **"제거하고 A/B로 측정한다"** 이며, Wave 3 계측이 그 전제다.
**발동률이 임계 이하로 떨어지면 그 자체가 제거 신호**다 — 거의 발동하지 않는 게이트는 코드 부채다.

| 7 | `solutions` 등록 1개(polestar) 유지 = 파이프라인 축 미발동 |

---

## 13. 범위 — 82가 하는 것과 하지 않는 것

| 항목 | 소관 |
|---|---|
| 실행 그룹 축·관계(kind)·순서 계약 | **82** |
| 존 순차 실행 · 존별 산출물 | **82** |
| 탐색 그룹(호스트 소재 확정) · 선행 스코프 전달 배관 | **82** |
| 솔루션 파이프라인 전개(`capabilities`/`requires`) · 종합 응답 | **82** |
| **범위 사전 선택**(`scope_select` 페이로드·비용 게이트·왕복) · **그룹 계측** · **부분 결과 노출** | **82** |
| **빈 결과 조건 퍼널 진단**(MFS/XSS · 표현 불가 조건 노출 · 0건 재생성 판정) | **82**(§6 · Wave 8) |
| **급증·변화율 판정 구현**(시계열 기준선) | **별건** — §6은 "안 됐다고 말하기"까지만 |
| 하네스 문헌 자산(ETCLOVG · P1~P15 · 내부 명세) | `plans/78` — 82는 **렌즈로 재사용**하고 신규 영역(CQ 문헌)만 보강(§7·§17) |
| 호스트 **가용성 판정** 자체 (`resolve_with_status`·`host_availability`) | `plans/81`(D-175) — 82는 소비만 |
| 미들웨어 **식별** (OS 근사) | `plans/78` W7-1 (**완료** · D-168) — 82는 그룹으로 배선만 |
| APM **커넥터·지표 조회** | `plans/78` W7-2 (미착수 · APM 도입 종속) + `plans/55` |
| DPM 커넥터 | `plans/55` |
| 조사 경로 선택(조회 vs 조사) | `plans/78` W3-2·3 (`plans/80` WU-18 · S-2 선행) |
| SSE 전체 타임아웃 가드 | **별건** |
| FabriX PII 차단 원인 종결 | D-155 후속 — 82는 회피·격리만 |

---

## 14. 사용자 확정 필요 사항

| # | 항목 | 선택지 | 권고 |
|---|---|---|---|
| U1 | Wave 6 플래그 전환 방식 | (a) 옵트인 우선 / (b) 기본값 즉시 전환 | **(a)** — 되돌리기 1줄, PII 미종결 상태에서 안전 |
| U2 | §11.1 H1 재현 실험(FabriX 실 호출) | 승인 / 보류 | 보류해도 Wave 1~5 진행 가능. 승인 시 D-127에 따라 **건마다** |
| U3 | 열거형 파일 단위 | (a) 그룹 2개(은행존 / 공동존[김포+여의도 합본]) / (b) DB 3개 | **(a)** — 요구 문구가 "공동존과 은행존"이고 gp/yd는 스키마·SQL을 공유 |
| U4 | 탐색 조기 종료 | (a) 전수 순회(기본) / (b) 첫 히트에서 중단 | **(a)** — 동명 호스트 다중 존을 은폐하지 않는다. (b)는 옵트인 플래그로 제공 |
| U5 | 탐색 다중 히트 | (a) 되묻기 / (b) 전부 조회해 병렬 제시 | **(a)** — 프로세스 조회는 대상이 하나여야 의미가 있다 |
| U6 | 탐색 0건 시 | (a) 순회 존 목록 명시한 안내 / (b) 존 선택 UI | **(a)** — 존을 골라도 없는 서버는 없다. 되묻기는 사용자를 한 번 더 헛돌린다 |
| U7 | 종합 응답 생성 | (a) LLM 합성 1회 + 결정적 폴백 / (b) 결정적 이어붙이기만 | **(a)** — D-062 선례, 실패 시 폴백 보장 |
| U8 | 3개 이상 peer 그룹 UI | (a) 섹션 나열 / (b) 탭 | (a) — 그룹 수가 런타임 결정이고 인쇄·복사에 유리 |
| **U9** | **범위 질문의 발동 축** | (a) 솔루션 축만 · 존 축은 `answerable=false`로 **발동 금지** / (b) 존 축도 새 질문으로 통합 | **(a) — 문헌으로 확정**(§7.4-②): 저품질 CQ는 세션 시간 ~2배(IPM 2022). 존 축은 사용자가 답을 모를 수 있어 저품질이다 |
| **U10** | **범위 질문 미응답 시** | (a) 전체 조회로 진행(기본값 있음) / (b) 답할 때까지 대기 | **(a)** — 성능 최적화 질문이 진행을 막으면 모호성 해소 질문과 구분이 사라진다(§5.3 불변식 2) |
| **U11** | **`SCOPE_SELECT_MIN_SECONDS` 초기값** | (a) 30초(잠정) 후 계측으로 정산 / (b) 계측 완료까지 기능 자체를 열지 않음 | **(a)** — 플래그 기본 off이므로 잠정값이 실동작하지 않는다. 단 정산(S-D)을 D-176에 기한과 함께 명시 |
| **U14** ★ | **Wave 8(0건 진단)을 2차 대기와 무관하게 먼저 착지시킬 것인가** | (a) 즉시 착수 — 1차 자산만 필요하고 U1~U13과 독립 / (b) 2차와 함께 | **(a)** — 요구 4는 현행 동작이 결함(문헌 기준 불충분한 응답)이고, 회귀 0·플래그 off 기본이라 승인 게이트가 없다. **2차 대기 중 먼저 착지시킬 수 있는 유일한 항목** |
| **U15** ★**개정(v6)** | **급증(변화율) 판정을 구현할 것인가** — *v5의 권고 (a)가 뒤집혔다* | (a) 하지 않는다 — "미반영" 표기까지만 / (b) **월 단위 한정으로 구현**(권고) / (c) 월+주 단위 동시 | **(b)** — v5가 (a)를 권고한 근거는 *"시계열 기준선 정의가 사용자 확정 사항"* 이었고 **요구 5가 그것을 확정**했다. 실측 결과 데이터는 이미 있고(§6.9 ①②) **표현 경로만 막혀 있다**(④). (c)는 `_d` 보존기간 미확인이라 분리한다(U17) |
| **U17** ★ | **일별 통계(`cmm_metric_stat_d`) 보존기간 실측을 승인하는가** | (a) 승인 — **읽기 전용** `MIN/MAX/COUNT(DISTINCT stat_date)` 1회(권고) / (b) 실측 없이 월 단위만 제공 | **(a)** — 과금 API가 아니라 읽기 전용 1회이고(**D-127 대상 아님**), 이 값 없이는 *"1주일 전 대비"* 를 **약속할 수 없다**. 14일 미달이면 (b)로 확정하고 그 사실을 응답에 표기한다 |
| **U18** | **급증 기본 임계(Δ%p) 값** | (a) **+20%p**(권고) / (b) +10%p / (c) 기본값 없음 — 매번 명시 요구 | **(a)** — 절대 임계(80%)가 병행 게이트라 저사용 노이즈는 이미 배제된다(§6.10 ①). (c)는 *"갑자기"* 라고만 쓴 질의를 **다시 0건으로 돌린다**. 선택한 값은 **응답에 항상 노출**(§6.12 ②) |
| **U19** | **집계 축** | (a) **파일시스템 단위 행 유지**(권고) / (b) 서버 단위 MAX / (c) 서버 단위 AVG(현행 few-shot) | **(a)** — 어느 파일시스템이 급증했는지가 조치에 필요한 정보다. **(c)는 급증을 희석해 놓친다**(§6.10 ③ — `/var` 30→90%가 서버 AVG 51%로 눌려 임계 미달) |
| **U16** | **조건 완화 후 자동 재조회** | (a) 제안까지만(권고) / (b) 자동으로 완화해 재조회하고 "완화된 결과"로 제시 | **(a)** — 임의 완화는 사용자가 묻지 않은 답을 주는 것이다. (b)는 완화 폭·완화 대상 선택이 또 하나의 비결정 지점이 된다 |
| **U13** ★ | **부분 결과 노출을 먼저 넣고 범위 질문은 보류할 것인가** (문헌 정정 ②) | (a) 부분 결과(Wave 3)를 먼저 배포·관측한 뒤 **질문 필요성을 재판정** / (b) 계획대로 둘 다 구현 | **(a)** — 문헌상 부분 결과가 질문보다 우월하고(개입 0·손실 0·첫 결과 더 빠름), 이것만으로 요구 3의 목적이 달성될 수 있다. 질문은 그 뒤에도 남는 대기가 측정되면 착수 |
| **U12** | **탐색 결과 단기 캐시**(§5.6) | (a) TTL 기본 0 유지(옵트인) · **Wave 6.5 선행에서 제외** / (b) 탐색만 짧은 TTL(예: 60s) 기본 on | **(a)** — D-172가 같은 판단을 이미 내렸고(실시간 조회의 조용한 캐시 금지), **P13이 이를 강화한다**: 캐시는 Tier 2이므로 히트율 계측 전에 켜면 이득/손실 판별 불가(정정 ①) |

---

## 15. 신규 결정 예약 — D-176

착수 시 `docs/02_decision.md`에 **D-176**으로 등재한다(예약은 「채번 이력」 등재 완료 — 계획서에만 적은
예약은 효력 없음, D-161 부기).

**D-176 예정 내용**: 실행 그룹(execution group) + 관계(kind) 도입 —
① 존 그룹 상호배타(D-143 후속3) **개정**
② 순차 실행 순서의 정본을 레지스트리 `query_order`/`order`로 이관(LLM `relevance_score` 종속 해소)
③ 그룹별 산출물(`output_files`) 도입 및 단수 필드 폐기 기한(2027-02-20) 부여
④ **선행 결과의 출처(`_source_db`)를 대상 스코프(`TargetRef.db_id`)의 정본으로 승격**
⑤ **탐색 그룹(discovery)** — 단일 대상 API 경로의 위치 미해소를 되묻기가 아니라 인가된 존 순회로 해소
⑥ 솔루션 축의 라우터 출력 계약 확장(D-169 연장) 및 `capabilities`/`requires` 기반 결정적 파이프라인 전개
⑦ **범위 사전 선택(`scope_select`)** — 비용 게이트로 발동하는 **성능 최적화 질문**을 모호성 해소 질문과
   구분해 신설. 불변식: 기본값=전체 조회 · 탐색 그룹은 비용 산정 제외 · 모호성 해소 우선 · 한 번만 묻고 승계 ·
   표본 부족 시 예상 시간 문구 생략. **임계값 `SCOPE_SELECT_MIN_SECONDS=30`은 잠정값이며 계측 분포로
   정산한다(기한: 계측 표본 n≥20 확보 후 첫 릴리스)** — 근거 없는 임계의 무기한 실동작(D-174 ② 전례) 차단
⑨ **빈 결과 원인 진단(조건 퍼널)** — 0건일 때 `COUNT` 계단 프로브로 **MFS/XSS**(끊긴 지점·마지막
   성공 단계)를 지목하고, **SQL로 표현되지 못한 조건의 미반영 사실을 별도 노출**한다. 0건일 때만 발동
   (정상 경로 비용 0) · LLM 0회 · 프로브 상한 K. P0>0이면 재생성을 **중단**하고 P0=0이면 스코프 오류로
   보아 허용한다. **급증 판정 구현은 범위 밖** — "안 됐다고 말하기"까지다.

⑧ **절감 레버의 우선순위 확정** — **부분 결과 즉시 노출 → 자동 축소 → 승계 → 계측 → 질문**.
   앞 셋은 사용자 개입 0·정보 손실 0이므로 질문보다 앞선다. 범위 축소 시 **미조회 범위 기록 +
   재확장 경로**를 필수로 하고(ETCLOVG 체크리스트 ③), 신규 플래그 전부에 **만료일 2027-02-20**을
   부여한다(P15 · D-161 ①). **범위 선택은 제거 후보 1순위**이며 판정은 "제거하고 A/B 측정"이다

**D-143 후속3 개정 명시** (D-161 ① 승격-폐기 동반):
- 근거 ①(담당 조직 분리·실수요 없음)은 **사용자 재확정으로 소멸**(2026-08-28).
- 근거 ②(FabriX PII b0+gp 차단)는 **미종결 유지** — 상호배타 코드는 삭제하지 않고
  `ZONE_GROUP_EXCLUSIVE` 뒤에 존치, **폐기 기한 = ② 종결 시점 또는 2027-02-20 중 이른 쪽**.

**문헌 근거 등재**(§7·§17): 「사용자에게 되묻는 행위」는 대화형 정보검색의 clarifying question 연구가
20년 축적한 주제이며, **무조건 묻기는 세션 시간을 약 2배로 늘린다**(IPM 2022, 89명 실험).
따라서 ①질문은 **선택적 게이트 뒤에만** 두고 ②게이트에 **비용 AND 품질(`answerable`)** 을 함께 걸고
③**부분 결과 즉시 노출**(Online Aggregation, SIGMOD 1997)을 질문보다 **앞선 1급 해법**으로 둔다.
하네스 관점 판정: 82의 프로파일은 **L·O 중심**이며(78은 E·G 중심), **P13(측정 우선) 준수 · P14·P15
정정 반영**(§7.3·§7.6 정정 6건).

**비용 구조 실측 등재**(추정 금지 근거): 존당 SQL 실행 p50 49~53ms(감사로그 918건) — 존 수 증가의
비용은 SQL이 아니라 **스키마 분석(존당 ≤20s)·LLM 생성(스키마 키당 1회, 90k~136k tok)** 에 있다.
이 사실이 "어느 단계에 물을 것인가"를 결정한다.

**`_zone_clarification_or_none_task`의 전제 개정 명시**:
*"서버명 지목 질의는 존이 결과에 영향 없음"* 은 **SQL 팬아웃 경로에 한정된 참**이며, 단일 대상 API
경로(`process_query`·APM·DPM)에서는 성립하지 않는다 — 그 경로의 답은 존 선택 되묻기가 아니라 **탐색**이다.

---

## 16. 관련 문서

| 문서 | 관계 |
|---|---|
| `plans/78-composite-query-host-diagnostics-orchestration.md` | 대상 해소(W1)·fan-out(W2)·미들웨어(W7) — 82가 **재사용하고 배선**한다. W7-2(APM)는 78 소관 |
| `plans/81-host-availability-precheck.md` | 가용성 판정 — **동시 편집 충돌 주의(§12.1)**. 82는 소비만 |
| `plans/55-multi-source-observability-roadmap.md` | APM·DPM 상위 로드맵. 82는 얹힐 **그룹 축**만 만든다 |
| `plans/80-78-79-joint-execution-contract.md` | 78·79 자산 소유권·게이트. Wave 7 착수 전 §8 순서 계약 확인 |
| `plans/75-realtime-usage-api-and-ux-review.md` §4 | 존 역질문 원안 — `selected_db_ids` 결정적 고정 계약 |
| `plans/73-formfill-deterministic-path-and-profiles.md` | 폼필 산출물·HITL 역질문 — Wave 4 그룹 집계 대상 |
| `plans/79-semantic-routing-improvement.md` | 라우터 출력 계약(D-169). Wave 7은 그 확장이며 **렌더 바이트 동일 골든**을 깨지 않는다 |
| `SPEC-middleware-os-identification.md` · `docs/24_middleware_profile_spec.md` | 미들웨어 식별 계약(W7-1 완료 자산) |
| `docs/pii_filtering_rules.md` | §11 PII 필터 규칙·정책 ID 표 |
| `docs/02_decision.md` | D-143 후속3 · D-153 · D-154 · D-155 · D-159 · D-161 · D-168 · D-175 |
| `docs/18_known_mistakes.md` | 경로 비대칭 · `ACTIVE_DB_IDS` 함정(:75) · 신규 DB 편입(:103) |
| `plans/78` §3.4·§12 | **하네스 문헌 자산의 정본** — ETCLOVG 7계층 · P1~P15 · 내부 명세 · 615편 기반. 82는 렌즈로 재사용(§7) |
| `AIOps/article/refuser/` (**본 저장소 밖**) | 하네스 문헌 615편·서베이 원문 — 2차 근거. 경로 주의 |

---

## 17. 참고문헌

> **선정 기준**: 인용수가 아니라 **게재처의 동료심사 여부 + 82와의 논리적 적합성**. 인용수는
> OpenAlex **레코드 단위 하한값**이다(2026-08-28 조회) — 같은 논문의 arXiv본·학회본이 별도
> 레코드로 분산되거나 학회본이 미색인되는 사례가 확인됐다(`plans/78` §12 서두의 실측 교훈).
>
> **서지 정본은 `docs/literature/`다** (2026-08-28 개설) — 저자·게재·DOI·OpenAlex ID·**시점별 인용수**는
> `docs/literature/bibliography.csv`, **원문 인용문과 인용 강도 등급(A~D)** 은 `docs/literature/claims.md`,
> 검색 질의·교훈은 `docs/literature/search_log.md`가 갖는다. 인용수 갱신은 `refresh.sh`.
> 아래 표는 그 정본의 **발췌**이며, 계획서가 갖는 것은 **해석**이다(식별자/해석 분리 — D-053).
> `entry_id`로 상호 참조한다.
>
> **기존 자산 재사용**: 하네스 엔지니어링 문헌 본체(ETCLOVG 7계층 · P1~P15 · 문헌 615편 기반
> 내부 명세 · IPIGuard·Task Shield·RouteLLM·LLMLingua·MemGPT 등)의 **정본은 `plans/78` §12**이다.
> 여기 다시 옮기지 않는다(D-053 사본 금지). 아래는 **82가 신규로 확보한 영역**과 82가 직접 근거로
> 쓰는 항목만 싣는다.

### 17.1 동료심사 문헌 — 82 신규 확보 (「사용자에게 묻는 행위」의 비용·효과)

| `entry_id` | 문헌 | 게재 | DOI | 인용 | 82 반영 |
|---|---|---|---|---|---|
| `CQ-COST-01` | **Zou, Sun, Long, Aliannejadi & Kanoulas — Asking Clarifying Questions: To benefit or to disturb users in Web search?** | Information Processing & Management 60(2), **2022** | `10.1016/j.ipm.2022.103176` | 14 | **§7.4-① · 정정 ⑥** — 89명 사용자 실험. *저품질 CQ는 사용자를 방해*하고 무조건 표시는 **세션 시간 ~2배**. *선택적 표시가 더 적은 노력으로 더 큰 이득*. **§5.4 게이트의 1차 근거** |
| `PROG-01` | **Hellerstein, Haas & Wang — Online Aggregation** | **SIGMOD 1997** | `10.1145/253260.253291` | **928** | **§7.4-⑤ · 정정 ②(설계 변경)** — 부분 결과 즉시·계속 제시가 긴 질의의 정통 해법. `_emit_group_packet` 승격 근거 |
| `HABIT-01` | **Vance, Jenkins, Anderson, Bjornn & Kirwan — Tuning Out Security Warnings: A Longitudinal Examination of Habituation Through fMRI, Eye Tracking, and Field Experiments** | **MIS Quarterly** 42(2), 2018 | `10.25300/misq/2018/14124` | 134 | **§7.4-③ · 정정 ⑤** — 반복 노출이 신경·행동 수준에서 주의를 급감시킴(3중 측정). **발동률 관측·한 번만 묻기의 근거** |
| `HABIT-02` | **Anderson, Vance, Kirwan, Jenkins & Eargle — From Warning to Wallpaper: Why the Brain Habituates to Security Warnings and What Can Be Done About It** | **Journal of Management Information Systems** 33(3), 2016 | `10.1080/07421222.2016.1243947` | 98 | §7.4-③ — 위 항목의 선행 연구. 반복 경고의 효력 감쇠 기제 |
| `AUTONARROW-01` | **Kulkarni, Tigelaar, Hiemstra & Callan — Shard ranking and cutoff estimation for topically partitioned collections** | **CIKM 2012** | `10.1145/2396761.2396833` | 45 | **§7.4-④ · 정정 ④** — 어느 샤드를 몇 개까지 조회할지의 **질의별 자동 추정**. 82 `auto_narrow`의 계보이며 *"묻지 않고 좁힌다"* 의 근거 |
| `CQ-GEN-01` | **Zamani, Dumais, Craswell, Bennett & Lueck — Generating Clarifying Questions for Information Retrieval** | **WWW 2020** | `10.1145/3366423.3380126` | 222 | §7.4 — CQ 생성·표시의 표준 문제 정의 |
| `CQ-SEL-01` | **Aliannejadi, Zamani, Crestani & Croft — Asking Clarifying Questions in Open-Domain Information-Seeking Conversations** | **SIGIR 2019** | `10.1145/3331184.3331265` | 202 | §7.4 — CQ 선택 문제의 정초 |
| `CQ-ANS-01` | **Krasakis, Aliannejadi, Voskarides & Kanoulas — Analysing the Effect of Clarifying Questions on Document Ranking in Conversational Search** | **ICTIR 2020** | `10.1145/3409256.3409817` | 46 | §7.4 — *"명료화 라운드 전체를 세밀하게 다뤄야 한다"*. **답변 품질이 결과 품질을 좌우**한다는 근거 |
| `CQ-EVAL-01` | **Sekulić, Aliannejadi & Crestani — Evaluating Mixed-initiative Conversational Search Systems via User Simulation** | **WSDM 2022** | `10.1145/3488560.3498440` | 55 | §5.5 · §7.4 — **질문 발동 임계를 사용자 시뮬레이션으로 평가**하는 방법론(S-D 정산의 후보 절차) |
| `CQ-DATA-01` | **Zamani, Lueck, Chen, Quispe, Luu & Craswell — MIMICS: A Large-Scale Data Collection for Search Clarification** | **CIKM 2020** | `10.1145/3340531.3412772` | 58 | §7.4 — 대규모 명료화 데이터셋(학습형 예측기의 전제 — 82 미채택 근거) |
| `EMPTY-MFS-01` | **Godfrey — Minimization in Cooperative Response to Failing Database Queries** | International Journal of Cooperative Information Systems 6(2), **1997** | `10.1142/s0218843097000070` | **114** | **§6.3 · §7.4-⑥ — MFS/XSS 정초.** 협조적 응답 원칙과 N회 질의 알고리즘의 1차 출처 |
| `EMPTY-DIAG-01` | **Fokou, Jean, Hadjali & Baron — Handling failing RDF queries: from diagnosis to relaxation** | Knowledge and Information Systems 50(1), **2016** | `10.1007/s10115-016-0941-0` | 19 | **§6.3 · §6.4** — *진단 → 완화* 파이프라인. 82의 「퍼널 표시 후 완화 제안」 구조와 동형 |
| `EMPTY-RELAX-01` | **Koudas, Li, Tung & Vernica — Relaxing join and selection queries** | **VLDB 2006** | `10.5555/1182635.1164146` | 97 | §6 — 조건 완화 기법. 82는 **제안까지만**(자동 재조회 미채택 — U16) |
| `EMPTY-DUAL-01` | **Bosc, Hadjali & Pivert — Empty versus overabundant answers to flexible relational queries** | Fuzzy Sets and Systems 159(12), **2008** | `10.1016/j.fss.2008.01.007` | 40 | §6 — 빈 답과 과다 답을 **한 문제의 양면**으로 다룬다(82의 LIMIT 절단 안내와 대칭 축) |

### 17.2 보조 근거 (preprint · 개관)

| `entry_id` | 문헌 | 출처 | 82 반영 |
|---|---|---|---|
| `CQ-RISK-01` | Wang & Ai — Controlling the Risk of Conversational Search via Reinforcement Learning | [arXiv 2101.06327](https://arxiv.org/abs/2101.06327) | §7.4 — **묻기 vs 결과 표시를 위험 통제 문제로 정식화**. 82의 게이트와 같은 문제이나 **학습형**이라 미채택(§17.3) |
| `CQ-NEED-01` | Aliannejadi et al. — ConvAI3 / ClariQ: Generating Clarifying Questions for Open-Domain Dialogue Systems | `10.48550/arxiv.2009.11352` | §7.4 — **clarification need 등급**(묻을 필요의 정도) 개념 |
| `—` | Guo, Zhang, Reddy, Alikhani — Abg-CoQA: Clarifying Ambiguity in Conversational Question Answering | AKBC 2021 | §7.4 — 모호성 기반 명료화(82는 **비용·품질 기반**이라는 차이) |
| `PROG-02` | Progressive Data Analysis (Dagstuhl Seminar 18411) | `10.4230/dagrep.8.10.1` | §7.4-⑤ — Online Aggregation 계보의 현대적 정리 |
| `EMPTY-ML-01` | Muslea — Machine learning for online query relaxation (KDD 2004 · 인용 60 · **동료심사**) | `10.1145/1014052.1014081` | §6.8 — **미채택 대조군**. 완화 추천을 학습형으로 가는 경로. 라벨 부재 · D-035로 82는 결정적 계단 프로브 채택 |
| `—` | **Agent Harness Engineering: A Survey** (ETCLOVG 7계층) | [OpenReview eONq7FdiHa](https://openreview.net/forum?id=eONq7FdiHa) | **§7.1·§7.2 — 커버리지 점검 렌즈로만 사용.** 게재 상태는 `plans/78` 기재(TMLR under review)를 승계 — **2026-08-28 재확인 시도는 봇 검증으로 실패**했고, 82의 어떤 결정도 이 상태에 의존하지 않는다 |

### 17.3 검토 후 미채택 — 학습형 CQ 예측기

IPM 2022는 **transformer 기반 예측기**로 "어느 CQ를 물을지"를 고르라고 제안하고, Wang & Ai(2021)는
같은 문제를 **강화학습**으로 다룬다. 82는 **결정적 게이트**를 택했다.

| 근거 | 내용 |
|---|---|
| **라벨이 없다** | 학습형은 세션 로그 + 만족도 라벨을 전제한다. 82의 계측(S-A·S-B)은 소요 시간만 남기며, 만족도 수집은 별건이다 |
| **D-035** | 결정적 게이트가 판단하고 LLM은 보조한다는 기존 결정. 질문을 낼지 말지를 학습 모델에 맡기면 비결정성이 UX 경로에 들어온다 |
| **P13** | 학습형은 그 자체가 Tier 2 최적화다 — 계측이 먼저다 |
| **축이 2개뿐이다** | 82의 축은 `solution`·`zone_group` 둘이고 그중 하나는 문헌 근거로 배제됐다(§7.4-②). 선택지 2개에 학습기를 붙일 이유가 없다 |

**재검토 조건**: 솔루션이 4개 이상 등록되고 발동률·만족도 로그가 쌓이면 다시 판단한다.

### 17.4 기존 자산과의 관계

| 자산 | 정본 | 82의 사용 |
|---|---|---|
| ETCLOVG 7계층 · 계층 결합 3제약 · 실무 체크리스트 | `plans/78` §3.4.1 | §7.1(프로파일) · §7.2(커버리지) · 정정 ③④ |
| P1~P15 원칙표 | `plans/78` §3.5(P 표) | §7.3 대조 — P2·P11·P12·P13 정합 / P14·P15 정정 |
| Tier 0/1/2 로드맵 · 세 결정 축 · KV 캐시 규칙 | `plans/78` §3.4.3 (내부 명세 기반) | §7.1·§7.3 — **실무 판단 틀로만**(학술 근거 아님, 78 §12.4 명시) |
| RouteLLM(P12) · LLMLingua · MemGPT · IPIGuard 등 | `plans/78` §12.1·§12.2 | 참조만 — 재수록하지 않는다 |
| 하네스 문헌 615편 · 서베이 원문 | `AIOps/article/refuser/` (**본 저장소 밖**) | 2차 근거. 경로 주의 |
| **82가 신규 확보한 서지·인용문·검색 로그** | **`docs/literature/`** | **정본** — 이 절은 발췌. 갱신은 `refresh.sh`. 세션 1=되묻기 비용(CQ·습관화·점진결과·자원선택 13건), 세션 2=**빈 결과 진단(MFS/XSS 5건)** |
