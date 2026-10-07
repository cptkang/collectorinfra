# 139. ITAM 내부망 1회차 결과 교정 — 테이블 관리 정보 정의 기반 조회 대상 선별 · 입력 한도 초과 · 오류 응답 오표면화

> **작성일**: 2026-10-06 · **v1.2** · **v1.3**(구현 기록 §9 · 2026-10-06)
> **상태**: **WIP — W1~W7 구현 완료(사용자 커밋 `9cc8c6b`·`8f9b1c4`·`bac814c` · W7 문서·시드·시나리오는 작업 트리) · 독립 검증·보안 감사 1라운드 교정 완료 · 잔여: 내부망 2회차(시드 정의 가져오기 → 정의서 대조 → 승인 → `--run --env closed`) · §9** · ~~TODO — 계획 확정(코드 0)~~ · 게이트 G-1~G-7·상한 답 받음(인터뷰 2026-10-06 · §5) · 시드 자산 2파일 git 추적
> - **통지(2026-10-07 · `plans/140` · D-311 · 상태 불변)**: 외부망 빌더가 1회차 반출로 `config/db_profiles/itam.yaml`을 썼다(조회 대상 98 — `tcdmsif81` 기본 제외 · 시드 정의 108 · 검증 오류 0 · 코드값 없음 · 로컬 승인본은 `testdata/itam/db_profile.local_sandbox.yaml`). 사용자 커밋 뒤 반입하면 2회차 잔여의 「시드 정의 가져오기」는 반입본으로 대신하고, 순서는 반입 → 「DB 구조」 탭 P1 실행(필수) → 코드값·코드 라벨만 승인 → 정의서 대조·승인 → `--run --env closed`(`plans/135` 부록 A v1.5)
> **요청(사용자 2026-10-06)**:
> ① *"「20261006-152938」 폴더에 135번 실행 결과가 있다. 수정계획을 수립하라."* — 화면 보고: ITAM 질의가 *"An exception occurred in GptOssAdapter.llm_call: Input tokens must be <= 95232. Given: 96858."* 와 *"존재하지 않는 테이블 참조: orchestrator"* 로 끝남(v1.0).
> ② *"테이블에 따라 해당 테이블에서 관리하는 정보를 먼저 정의하여 관리하고 LLM을 통해 사용자의 프롬프트에 맞는 조회대상 테이블을 선별하고 해당 테이블을 통해 쿼리를 생성하는 방식으로 동작해야 한다. 이 방식으로 계획을 업데이트하라."* (v1.1 — §4)
> ③ *"수집된 결과 파일(20261006-152938)의 정보를 기준으로 itam_schema.json과 테이블별 정보를 정리하여 관리하도록 파일을 생성하거나 정보를 생성하라."* → §4.2 시드 자산
> **입력**: `results/itam_bench/20261006-152938/`(반출 5파일 — `run.json`·`report.md`·`trace.jsonl`·`schema_catalog.yaml`·`leak_check.json` · 누출 관문 통과 · `.gitignore` 대상) — `~/Downloads/20261006-152938`과 동일(`diff -rq` 0).
> **관련 계획**: `plans/135`(ITAM 벤치 — 이 run의 생산자) · `plans/133`(스키마 자산 자동 생성 — A2 「조회 대상 테이블」이 99개를 승인 · 정의 생성은 그 흐름에 붙인다) · `plans/104`(「DB 구조」 탭 — 질의 경로는 읽기만) · `plans/137`(한글 식별자 — 잔여 「폐쇄망 ITAM 실 질의 검증」이 같은 결함으로 막힘) · `plans/119` Q-5(테이블 선택 생략 플래그) · `plans/114` P-4①(강제 보충) · `plans/132`·`plans/122`(통지) · `plans/57`(같은 증상의 선례)
> **관련 결정**: **D-159**(토큰 예산·백엔드 예외 감지 — FIX-B 「단일 경로는 강등만」·대안 기각 「단일 `_llm_select_relevant_tables`를 멀티에 이식」 두 조항을 D-308로 부분 개정) · **D-227**(질의 경로는 구조를 읽기만 — 정의 생성은 관리자 탭) · **D-294**(자산 자동 생성 — 새 자산 키·병합 규칙) · D-051 · D-066(경로 대칭) · D-153 후속2·D-155 · D-301(벤치 계약 ③⑥ · 부기 G-7 「정의서로 싱크」) · D-255(매뉴얼) · D-303(회귀)
> **D-번호**: **D-308**(`docs/decisions/D-308.md` · 확정 · 구현 완료 · 내부망 확인 대기 · 검증 교정 부기) · D-159 부분 개정 · D-294 부기 · D-301 부기(W6)

---

## 0. 증거 규칙

- 과금 호출 0 · 서버 기동 0 · DB 호출 0. 실측은 반출 파일 읽기와 스크래치 스크립트(LLM 0 · DB 0)뿐이다.
- run 커밋 `879b99654fc6`(dirty)은 **로컬 저장소에 없다**(폐쇄망 반입 사본의 커밋). 코드 근거는 로컬 `main`(`71d7ff6`) 기준이며, 폐쇄망 사본과 해당 함수가 같다고 가정한다 — 재실행 전 사용자 반입본이 이 계획의 수정분을 포함하는지 확인한다.
- 응답 원문은 반출되지 않는다(D-301). 화면 문구는 사용자 사진으로만 확인했다.

## 1. 실행 결과 요약 (run `20261006-152938`)

환경 `closed` · 2단 `intent_orchestration` · 워커 `fabrix` / 오케스트레이터 `vllm` · 시나리오 14건 15턴 · 전부 관찰(observe).

| 묶음 | 턴 | 결과 | 비고 |
|---|---:|---|---|
| ITAM 단일 DB 턴 | 12 | **전부 SQL 0건** — `disclosure_kinds: non_sql` · 재시도 1 · 결과 `empty` · 턴당 36~52초 | 12턴 모두 **같은 99테이블·698컬럼 이름**을 받음(`schema_context.presented_tables`) · 표본 행 있음 · 의미 보유 698/698 |
| 되물음 | 2 (ITAM-102·105) | `clarification` · DB 없음 · 23ms | `routing_miss` — 「자산관리」를 말하지 않은 질문 |
| 멀티 DB | 1 (ITAM-114) | SQL 4건 성공(ITAM `tcdmsif90` 7행 + 폴스타 3존) · 132초 | `fabricated` 판정 — §2.5 |

결과적으로 **1회차의 목적(서비스↔서버 연결 위치를 반출 로그로 찾기 — `plans/135` G-7)은 SQL 관측 턴이 ITAM 0건이라 달성되지 않았다.** 재실행이 필요하다.

## 2. 원인 사슬 (실측)

### 2.1 조회 대상 테이블 99개가 매 질의 프롬프트에 전부 실린다 — **근본 원인**

- 반출 카탈로그 `approved_profile.allowed_tables` = **99개**(108테이블 중 · 제외 9 = `tcdmsam53·55·59·60·61·64`·`tcdmscm12·34·55`). `plans/133` A2(「조회 대상 테이블」)로 승인된 값이다 — 자산 생성 P1은 행 수가 0이 아닌 테이블을 전부 후보로 낸다(`asset_generation_service._assemble`). 폴스타 프로필은 5개다.
- 단일 경로 `schema_analyzer`는 `allowed_tables`가 있으면 ①LLM 선택 중 허용 밖을 제거하고 ②**허용 목록 중 LLM이 고르지 않은 테이블을 전부 보충한다**(`src/nodes/schema_analyzer.py:653-719` · `_supplement_bare = set(_allowed)` — plans/114 P-4①). 99개면 **LLM 선택이 무력화되고 항상 99개**가 된다.
- 이어서 99개 각각에 실데이터 표본 5행을 붙인다(`_collect_live_samples` · `:290` · 총량 20초). 서버 상태에는 컬럼 설명도 698/698 실렸다(Redis 설명 — §2.4).

### 2.2 단일 경로 토큰 예산 가드가 이 크기를 통과시킨다

`query_generator.py:769-806`(D-159 FIX-B의 단일 경로 몫)은 **시스템 프롬프트만** 추정하고, 예산(90,000) 초과 시 **유사어·설명만** 빼며, 그래도 넘으면 **로그만 남기고 그대로 보낸다**. D-159는 "단일 경로는 relevant 게이트로 이미 좁아 발동이 이례적"이라는 전제로 정했다 — §2.1이 그 전제를 깼다.

반출 카탈로그 구조로 시스템 프롬프트를 조립한 추정치(스크래치 · LLM 0 · DB 0 · 표본 값은 한글 8자 자리표 · 설명은 고정 문장 자리표):

| 테이블 | 표본 | 설명 | 추정 토큰 |
|---|---|---|---:|
| 99 | ✓ | ✓ | 119,507 |
| 99 | ✓ | — | **82,215** ← 가드 1단(설명 제거) 뒤 · 예산 90K **통과** |
| 99 | — | — | 17,937 |
| 10 | ✓ | ✓ | 13,318 |
| 3 | ✓ | ✓ | 3,019 |

→ FabriX 실보고는 **96,858 > 95,232**였다(사용자 프롬프트 미포함 · 추정 계수 과소 — 자리표 기반이라 비율은 참고치). **테이블을 좁히면 3~13K**다.

### 2.3 FabriX 한도 초과 문구가 SQL로 검증되어 엉뚱한 안내가 나간다

- FabriX는 한도 초과를 HTTP 오류가 아니라 **응답 본문**으로 돌려준다(`prompt_blocks.py:672` 주석 · D-159).
- 멀티 경로는 이 문구를 감지해 「LLM 백엔드 입력 토큰 한도 초과」로 구분하고 재시도를 멈춘다(`multi_db_executor.py:153-166`·`:2820-2832` · D-159 FIX-C). **단일 경로 검증(`src/sql_validation.py` `validate_sql`)에는 이 감지가 없다** — 경로 비대칭(D-066 위반). `query_generator.py:771` 주석은 단일 경로도 "백엔드 예외 감지가 사후 방어한다"고 적었지만 실재하지 않는다.
- 그래서 오류 문구가 SQL로 검증됐다: `…Error occurred from orchestrator…`의 `from orchestrator`를 FROM 절로 읽어 「존재하지 않는 테이블 참조: orchestrator」, 산문 판정(`is_non_sql_prose`)으로 「조회 엔진이 남긴 설명」 안내, 산문 재시도 1회로 **같은 크기 프롬프트를 한 번 더 호출**.

### 2.4 벤치 카탈로그의 의미 보유 0 ↔ 실행 상태 698 — 측정 불일치

- 카탈로그 `summary.columns_with_meaning: 0` — 카탈로그는 파일 캐시 `_descriptions`만 읽는다(`scripts/itam_bench/catalog.py:704-711`). 서버는 설명을 **Redis 우선**으로 읽는다(`cache_manager.get_descriptions` `:589-605`).
- 파일에 설명이 없는 이유 후보(**추정 — W6에서 재현 테스트로 확정**): `PersistentSchemaCache.save()`(`persistent_cache.py:149-205`)는 파일 전체를 `schema`만으로 다시 써서 `_descriptions`·`_synonyms`가 사라진다.

### 2.5 그 밖의 관찰 (이 계획은 통지만)

| 턴·대상 | 관찰 | 처분 |
|---|---|---|
| ITAM-102·105 | 「자산관리」 없이 「담당 부서」·「유지보수 계약 만료일」 → 소스 되물음 | `plans/132` 통지 · W6-e |
| ITAM-114 | 「**자산관리에서** 지난주 CPU 사용률 추이」 → ITAM + 폴스타 3존 조회 | `plans/132` 통지 |
| ITAM-114 | ITAM `tcdmsif90`(가상화 호스트 수집 이력)에 `기준년월일`·`CPU사용률`이 실재 — 「자산 DB엔 추이가 없다」는 시나리오 전제가 성립하지 않음 | **G-6** |
| ITAM-114 | 폴스타 쪽이 「지난주」를 월 통계로 조회 | `plans/122` 통지 |
| 리포트 | §9 「로컬 MLX 값이라」 문구가 FabriX run에도 고정 · closed 정책에 「누출 관문 시험 불성립」 경고 | W6-b·c |
| 조회 대상 | `tcdmsif81`(가상화 관리 서버)에 `계정명`·`계정비밀번호EC`(암호화 비밀번호) — 조회 대상 99개에 포함 | 내부망 관리자에게 조회 대상 제외 권고(W7) |

### 2.6 테이블 단위 의미가 없어 LLM이 고를 근거가 컬럼 이름뿐이다

- 반출 카탈로그: 테이블 의미 0/108 · 파일 컬럼 의미 0/1,988 · 관계 0 · 같은 기본키 군 0 · 기본키 표시 0(108테이블 모두).
- 테이블 이름은 코드형(`tcdms` + 군 접두 `if` 51 · `am` 25 · `cm` 25 · `gt` 3 · `br` 2 · `hr` 2 + 번호)이다. 반면 컬럼 이름은 한글 의미어다(평균 18.4개 · 최대 68).
- 현행 선택 프롬프트(`_llm_select_relevant_tables` · `schema_analyzer.py:951-1039`)는 「테이블명: [컬럼 15개]」만 싣는다. 서버·네트워크·가상화는 **현행 / 수집 적재본 / 날짜별 수집 이력**이 거의 같은 컬럼의 3종 쌍둥이다(예: `tcdmsif72`·`74`·`73`). 그래서 컬럼 15개로는 「지금 상태」와 「추이」를 가릴 근거가 없다.
- 선택이 실패하면 **전체 테이블을 반환**한다(`:1029-1039`). 결정적 후처리로 줄이는 장치가 없다.

## 3. 목표 · 비목표

**목표**
1. **테이블마다 「관리하는 정보」를 정의해 자산으로 관리한다** — 관리자 「DB 구조」 탭에서 초안(가져오기·LLM) → 검토·수정 → 승인 · 버전·되돌리기(D-227 ⑤ · D-294 ④와 같은 규칙).
2. **질의 시 LLM이 그 정의를 보고 질문에 맞는 조회 대상 테이블을 고른다** — `allowed_tables`는 후보 범위(필터)로만 쓰고 강제 보충하지 않는다.
3. **고른 테이블(과 그 정의)만으로 SQL을 생성한다** — 단일·멀티 경로 대칭(D-066).
4. 그래도 한도를 넘으면 **보내기 전에** 줄이거나 멈춘다(W2). 백엔드 오류 응답은 원인 그대로 알리고 재시도하지 않는다(W1).
5. 벤치가 선별 결과·프롬프트 크기·설명 출처를 서버가 실제 쓴 것과 같게 기록한다(W6).
6. **정의가 없는 DB(폴스타 4종)는 비트 동일**(G-1).

**비목표**: 질의 경로에서 정의를 만들거나 고치기(D-227 ①⑧ — 질의 경로는 읽기만) · 컬럼 설명 생성(`plans/133` A3) · 토큰 추정 계수 변경(W6-d 기록 뒤) · 소스 선별(`plans/132`) · FabriX 한도 상향.

## 4. 설계

### 4.1 흐름 — 사용자 지시 3단계와 작업 단위

```
[관리자 「DB 구조」 탭 — W3]                               [질의 경로 — 읽기만]
스키마 스냅샷 + P1 근거 ──→ 정의 초안 ──→ 검토·수정 ──→ 승인 ──→ 프로필 table_definitions
 (가져오기 · LLM 묶음 초안)     (결정적 검증)   (테이블 단위)      (버전·되돌리기)        │
                                                                                         ▼
질문 ─→ 후보 = 스키마 ∩ allowed_tables ─→ ①정의 목록으로 LLM 선별(W4) ─→ 검증·조인 보완·상한
     ─→ ②고른 테이블의 스키마·표본·설명 + 정의 블록만으로 SQL 생성(W5) ─→ 검증·실행
                      (안전망: 예산 사다리 W2 · 백엔드 오류 감지 W1)
```

| 사용자 지시 | 작업 |
|---|---|
| 테이블별 관리 정보를 먼저 정의하여 관리 | **W3**(자산·생성·승인) · 시드 §4.2 |
| LLM으로 프롬프트에 맞는 조회 대상 테이블 선별 | **W4** |
| 선별한 테이블로 쿼리 생성 | **W5** |

### 4.2 시드 자산 (요청 ③ · 2026-10-06 작성 · git 추적 — G-7 · 미커밋)

| 파일 | 내용 | 만든 방법 · 검증 |
|---|---|---|
| `testdata/itam_bench/closed/itam_schema.json` | 내부망 ITAM 스키마 108테이블·1,988컬럼 — **앱 스키마 캐시 형식**(`_cache_version` 1 · 지문 · `schema.tables` · `_db_description`) | 반출 카탈로그를 앱 저장 함수 `PersistentSchemaCache.save()`로 기록 → 같은 클래스 `load()`로 다시 읽어 108·1,988 확인. 카탈로그에 없는 정보는 비어 있다: 타입 길이(`char`·`varchar`·`decimal`만) · 기본키 · 관계 · 행 수 · 표본 · 컬럼 설명 |
| `testdata/itam_bench/closed/table_definitions.yaml` | 테이블별 관리 정보 **초안**(status `draft`) — 24개 업무 영역 · 108테이블마다 `group`·`kind`·`manages`·`key_columns`·`related`(157건)·`notes` · 조회 대상 밖 9개 `allowed: false` | 컬럼 이름만 보고 작성(테이블 주석·값 미확인 · 이름만으로 판단한 것은 「(추정)」). 스크래치 대조 오류 0: 108테이블 일치 · 대표 컬럼 실존 · 연결 후보 테이블·컬럼 실존 · `allowed` 표시가 프로필과 일치 |

- `kind`는 §2.6의 쌍둥이 문제를 직접 다룬다: `현행`(`활성화여부` 있음) · `수집적재`(같은 구성 · `활성화여부` 없음 — 적재본 추정) · `수집이력`(`기준년월일` + 수집이력ID).
- `related`는 **이름이 같은 컬럼으로 추정한 연결 후보**다. 값 겹침은 확인하지 않았다(D-294 ② 채택 기준 ≥ 0.9 미측정) — 승인 관계 자산이 아니다.
- `notes`에 SQL 생성에 필요한 표기 차이를 적었다. 예: 메모리 사용률 컬럼은 `tcdmsif72`·`73`에서 `서버메모리사용율`(율), `tcdmsif80`에서 `서버메모리사용률`(률)이다. 비밀값·개인정보 컬럼도 표시했다.
- **서비스↔서버 연결 단서(`plans/135` G-7 1회차 목적)**: 서버 테이블에는 어플리케이션코드 컬럼이 없다. 연결 후보는 서버의 `그룹경로내용`·`용도내용`·`구성항목설명내용`과 스토리지 할당의 `업무명`이다 → 2회차 관찰 질문·정답 SQL의 출발점.
- 실측: 이 정의로 만든 선별 후보 목록(조회 대상 99개 · 「이름: 관리 정보 [대표 컬럼]」)은 추정 **6,257토큰**이다. 현행 「컬럼 15개」 목록은 6,949토큰(99개)·7,608토큰(108개)이다. 정의를 써도 선별 호출이 커지지 않는다.

### 4.3 정의 자산 형식 (W3 · 런타임)

프로필 키 `table_definitions`(설계 기본값 — §4.5). 테이블당:

| 필드 | 필수 | 프롬프트 사용 | 결정적 검증 |
|---|---|---|---|
| `manages` | ✓ | 선별(W4) · 생성 정의 블록(W5) | 1~300자 · 중괄호·코드 펜스·백틱 SQL 금지 · 빈 값 거절 |
| `kind` | — | 선별 | 위 목록 값만 |
| `key_columns` | — | 선별 | 그 테이블에 실존 · ≤ 10 |
| `related` | — | 선별(조인 상대 힌트) | 상대 테이블 실존 |
| `notes` | — | 생성 정의 블록(선별된 테이블만) | ≤ 300자 · 금지 문자 동일 |
| `origin` | ✓ | — | `import`·`llm`·`comment`·`manual` |

`group`은 관리 화면 묶음용이다(프롬프트 미사용). **값(표본·코드값)은 정의에 넣지 않는다** — LLM 초안 입력에도 표본을 넣지 않는다(코드값 라벨은 D-294 자산 범위 안에서만).

### 4.4 작업 단위

| W | 내용 | 파일 | 결정 |
|---|---|---|---|
| **W1** | **백엔드 오류 응답 감지 — 단일 경로 대칭**(v1.0 그대로). 멀티의 마커 3종·구분 접두·판정을 공용 함수 하나로 옮기고(후보 `src/sql_validation.py`) 멀티는 그 함수를 쓴다(비트 동일). `validate_sql`은 SELECT 검사 **전에** 판정해 사유 하나만 낸다. `query_validator`는 `backend_error` 표지 · `is_non_sql_prose` 제외 · 그래프 경로·2단 단일 루프(`subagents.py:934-963`)는 **재생성 0회** · 종결 사유 `regen_stop.reason = "backend_limit"` → `replanner._REGEN_STOP_REASON_TEXT` 문구 · `[토큰예산]` 로그에 FabriX `Given`·한도와 자기 추정치. **W4의 선별 호출에도 같은 판정을 쓴다.** | `src/sql_validation.py` · `src/nodes/{multi_db_executor,query_validator,query_generator}.py` · `src/graph.py` · `src/orchestration/{subagents,replanner}.py` | D-159 FIX-C 단일 확장 |
| **W2** | **단일 경로 예산 사다리 — 멀티와 같게**(G-4 · v1.0 W3). 시스템 + 사용자 프롬프트 추정 · 초과 시 ①유사어·설명 ②**표본** 제거 ③**호출 없이** `PromptBudgetExceeded` → W1과 같은 종결. 예산 안이면 바이트 무변경. 상태 `prompt_budget`(수치·단계·테이블 수·표본 유무). **정의가 승인되기 전에도 ITAM 재실행이 돌게 하는 안전망**(99테이블·표본 없음 추정 17,937). | `src/nodes/query_generator.py` · (필요 시) `src/nodes/prompt_blocks.py` | D-308(G-4 · D-159 개정) |
| **W3** | **테이블 정의 자산 — 정의·생성·검토·승인**. ①프로필 키 `table_definitions`(§4.3) · `profile_merge`에 테이블 단위 병합 규칙(사람 값 `manual` 보존 · `import`/`llm`/`comment`만 교체 — D-227 S5와 같은 원칙). ②「DB 구조」 탭 자산 화면에 **「테이블 정의」 표**(영역·테이블·관리 정보·대표 컬럼·성격·출처 · 편집 가능 · 테이블 단위 선택 승인). ③초안 경로 3가지: **가져오기**(§4.2 형식 YAML — 결정적 검증 뒤 초안) · **주석**(P1 카탈로그 테이블 주석 → `comment`) · **LLM 묶음 초안**(주석 없는 테이블만 · 입력 = 테이블명·컬럼명/타입·컬럼 설명·코드 라벨·승인 관계·DB 설명 · 군 접두 단위 10개씩 · 동시성 `admin_llm_concurrency`(=2) · 예상 호출 수 사전 표시 · 실패 묶음만 재실행 · ITAM 99개 ≈ 10~12회). ④검증 실패 행은 승인 불가(409). ⑤준비도 경고 1항목: 조회 대상 > 10인데 정의가 없거나 정의 범위가 조회 대상의 80% 미만이면 경고만(G-7 (a) 방식). ⑥매뉴얼(D-255): 관리자 「스키마 자산 자동 생성」 절에 「테이블 정의」 · 캡처 갱신. | `src/schema_cache/asset_generation_service.py` · `src/domain/profile_merge.py` · `src/domain/table_definitions.py`(신규 · 순수 검증·렌더) · `src/prompts/asset_generation.py` · `src/domain/db_readiness.py` · `src/api/routes/db_structure.py` · `src/static/js/admin-db-structure.js` · `scripts/manual/{features.yaml,content/admin.md,captures.yaml}` | D-308(D-294 부기 — 새 자산 키) |
| **W4** | **정의 기반 LLM 테이블 선별 — 단일·멀티 공용**. 발동: 그 DB 프로필에 `table_definitions`가 있음(G-1). ①후보 = 스키마 ∩ `allowed_tables`(있으면) — **강제 보충 없음**. 정의가 없는 후보는 「이름 [컬럼 앞 10개]」 줄로 함께 싣는다. ②프롬프트(`src/prompts/table_selection.py` 신규): 질문(멀티는 `sub_query_context` 덧붙임) + DB 설명 + 후보 목록(이름순 — KV 캐시 · TP-11.10) + `kind` 안내(「추이·과거 시점 = 수집이력 · 지금 상태 = 현행 · 수집적재는 고르지 않음」) + 규칙(필요한 최소 테이블 · 조인 상대 포함 · 최대 K개) → JSON `{"tables": [...]}`(쉼표 목록도 받음). ③결정적 후처리: 실존 ∩ 후보(맨 이름 비교) · `related`·승인 관계로 **선별 테이블 둘을 잇는 중간 테이블만** 보완 · 상한 K(설정 `cfg.text2sql.schema_table_select_max` · `TEXT2SQL_SCHEMA_TABLE_SELECT_MAX`, 기본 8 — 넘으면 LLM 순서대로 자르고 로그). ④실패(예외·W1 백엔드 오류·유효 0개) → **G-2**(기본: 정의·컬럼명 어휘 매칭 상위 K로 대체 + `[테이블선별] 폴백` 로그 → 0개면 SQL 생성 없이 안내 종결 · 전체 반환 금지). ⑤Q-5 생략(`_table_select_skip_enabled`)·EAV 보충·강제 보충은 정의 모드에서 건너뛴다. alarm_query 분기는 현행. ⑥상태 `table_selection` = {mode, source(`llm`/`lexical`/`none`), candidates, selected, bridged} — 값 없음. ⑦멀티: `_gate_schema_tables`(`multi_db_executor.py:1373`) 자리에서 같은 함수(G-3). | `src/nodes/table_selection.py`(신규 · application) · `src/nodes/schema_analyzer.py` · `src/nodes/multi_db_executor.py` · `src/prompts/table_selection.py` · `src/domain/table_definitions.py` · `src/config.py`(K) · `src/state.py` | D-308(G-1~G-3 · D-159 대안 기각 「단일 LLM 선택의 멀티 이식」 개정 · plans/114 P-4①은 정의 모드에서만 미적용) |
| **W5** | **선별 테이블로만 SQL 생성**. ①스키마·표본·설명·유사어 재료를 선별 테이블로만 만든다(현행 구조 — `relevant`가 좁아지면 따라 좁아짐 · 표본 수집도 선별 테이블만). ②**정의 블록**: 선별 테이블의 `manages`·`notes`를 「테이블 용도」 블록으로 스키마 앞에 싣는다 — 공용 빌더(`prompt_blocks`) 하나를 단일 `_build_system_prompt`·멀티 프롬프트 같은 자리에서 호출(D-066). 정의 없는 DB는 빈 블록 → 바이트 불변. ③선별 밖 테이블을 참조한 SQL은 기존 검증(「존재하지 않는 테이블 참조」)이 재생성으로 돌린다 — 재선별은 하지 않는다(위험 §7). | `src/nodes/prompt_blocks.py` · `src/nodes/query_generator.py` · `src/nodes/multi_db_executor.py` | D-308 |
| **W6** | **벤치 보강**(`plans/135` 소관 · D-301 부기). a) 설명·유사어 출처를 서버와 같은 순서(Redis → 파일)로 · 파일 설명 유실 재현 테스트 → 재현되면 `save()`가 부가 필드 보존(G-5) b) §9 지연 문구 평면별 c) closed 정책 카나리아 「해당 없음」 d) 측정 연결점이 `prompt_budget`·`table_selection`도 복사(호출 1곳 유지) → `trace.jsonl` `schema_context.prompt_tokens_est`·`budget_stage`·`backend_reported_tokens`·`selection_source`·`selected_count` e) 되물음 종류(칩 여부·후보 소스 id) f) 실패 분류 `backend_limit`·`selection_none` g) 카탈로그 `tables.*.meaning` ← 승인 `table_definitions.manages`(출처 표기) | `scripts/itam_bench/{catalog,report,judge,_serve,__main__}.py` · `src/schema_cache/persistent_cache.py`(G-5 조건부) | D-301 부기 |
| **W7** | **통지·시나리오·반입 안내**. `plans/132`(되물음·명시 소스 4곳) · `plans/122`(「지난주」→ 월 통계) · `plans/137` 잔여는 재실행으로 확인 · ITAM-114 기대값 유지(G-6 — 사용률 정본은 관측 DB · ITAM 수집 이력 답은 오답 → 기대 응답 「자산관리에는 사용률 추이가 없다 — 관측에서 조회할지」 · 라우팅 교정은 `plans/132`에 통지) · 시드 정의의 사용률 컬럼 보유 13테이블(`tcdmsif72`·`73`·`74`·`80`·`85`~`93`) `notes`에 「사용률 정본 아님 — 관측 DB」 표기 · 2회차 관찰 질문에 서비스↔서버 연결 단서(§4.2) 반영 · 내부망 관리자 절차 안내: 시드 정의 가져오기 → 내부망 정의서와 대조·수정 → 승인 · `tcdmsif81` 조회 대상 제외 권고 | 계획서 3개 부기 · `testdata/itam_bench/scenarios.closed.yaml` · `plans/135` §9 | — |

**순서**: W1 → W2 → W3 → W4 → W5 → W6 → W7. W1·W2만 반입해도 오표면화·헛재시도가 사라지고 ITAM 단일 턴이 표본 없는 99테이블(≈18K)로 돈다 — 정의 승인 전 2회차를 먼저 돌릴 수 있다. W3~W5가 사용자 지시의 본 방식이다.

### 4.5 설계 기본값 (상한은 사용자 확정 · 나머지는 기본값 — 이의가 있으면 알려 주세요)

| 항목 | 기본값 | 다른 안 |
|---|---|---|
| 정의 저장 위치 | 프로필 키 `table_definitions` — 두 경로가 이미 읽는 `_load_manual_profile`·`apply_profile` 버전·되돌리기를 그대로 쓴다 | 지식 파일 `config/knowledge/{db_id}/table_definitions.yaml`(`AssetFileStore`) — 프로필이 길어지지 않지만 로더·배선 추가 |
| 선별 상한 K | **8 (설정 키 · 사용자 확정 2026-10-06)** | 5 · 상한 없음(사다리만) |
| 승인 단위 | 테이블 단위 선택(D-227 G-8 (a)와 같음) | 전체 일괄 |
| LLM 초안 실행 | 관리자 명시 실행만(D-227 ②) | — |

## 5. 게이트 — 사용자 결정 (인터뷰 2026-10-06 · 전부 답 받음 → D-308)

| G | 질문 | 선택지 (권고 ★) · **답** |
|---|---|---|
| **G-1** | 정의 기반 선별을 **어느 DB에** 적용할까? | ★(a) **정의가 승인된 DB만**(지금은 ITAM) — 폴스타 4종은 정의가 없으니 현행(강제 보충 포함) 비트 동일 · 폴스타도 나중에 정의를 승인하면 같은 방식으로 바뀜 / (b) 폴스타도 이번에 정의를 써서 함께 전환 — 폴스타 응답이 바뀌므로 시나리오 재측정 필요 / (c) 조회 대상이 10개 넘는 DB에만 → **답: (a) 정의 승인 DB만** |
| **G-2** | LLM 선별이 실패하면(오류·엉뚱한 이름만 반환) 어떻게 할까? | ★(a) **질문 단어를 정의·컬럼 이름과 맞춰 상위 K개로 대체**(로그·표지 남김) → 그것도 0개면 SQL을 만들지 않고 「질문에 맞는 테이블을 찾지 못했습니다 — 무엇(자산·계약·장애 등)을 찾는지 알려 주세요」 안내 / (b) 대체 없이 바로 안내 / (c) 후보 전체로 진행 + 예산 사다리(지금과 비슷 — 비권고) → **답: (a) 어휘 매칭 대체 → 0개면 안내** |
| **G-3** | 여러 DB를 함께 조회할 때(멀티 경로)도 ITAM은 같은 방식으로 고를까? | ★(a) **함께** — 같은 함수 · D-159 대안 기각 「단일 LLM 선택의 멀티 이식」(근거: 프로필 DB는 조회 대상 목록이 지배적이라 결정적 필터로 같은 결과 — ITAM 99개에서 성립 안 함) 개정 · 멀티 질의당 ITAM 선별 호출 1회 추가 / (b) 단일만 — 멀티는 지금처럼 99개 필터 + 사다리 → **답: (a) 함께(단일·멀티 같은 함수)** |
| **G-4** | 단일 경로도 한도를 넘으면 보내지 않고 멈출까?(D-159 개정) | ★(a) **멀티와 같은 사다리**(설명 → 표본 제거 → 호출 없이 명시 실패) + 사용자 프롬프트 포함 추정 · 예산 90K 유지 / (b) (a) + FabriX가 한도 초과로 답하면 표본을 빼고 1회만 재호출 / (c) 현행 유지 → **답: (a) 멀티와 같은 사다리** |
| **G-5** | 벤치 카탈로그의 설명 출처를 서버와 맞출까? | ★(a) **Redis → 파일 순으로 읽고(조회문 0), 파일 설명 유실이 재현되면 제품 `save()`도 부가 필드 보존** / (b) 카탈로그만 / (c) 리포트 경고만 → **답: (a) 벤치 + 앱 `save()`(재현 시)** |
| **G-6** | 「자산관리에서 지난주 CPU 사용률 추이」에 ITAM 가상화 호스트 수집 이력(`기준년월일`·`CPU사용률`)으로 답하면 정답인가? | (a) **자산관리를 명시했으면 ITAM 답도 정상** → 시나리오의 「데이터 없음」 기대 제거 / (b) 사용률 정본은 관측 DB → ITAM 답은 오답 · 별도 라우팅 교정 — **사용자 업무 판단** → **답: (b) 오답 — 사용률 정본은 관측 DB** → ITAM-114 기대값 유지 · 라우팅 교정은 `plans/132` |
| **G-7** | 반출 유래 시드 2파일(§4.2 — 내부망 108테이블 이름·컬럼 이름 · 값 없음)을 **git으로 관리**할까? 저장소 원격은 GitHub이고, 지금 추적 중인 closed 파일 2개(`scenarios.closed.yaml`·`column_policy.closed.yaml`)에는 운영 테이블 이름이 없다 | (a) 추적 — 반입·대조 이력이 남고 W3 가져오기 테스트가 실제 모양을 쓴다 / (b) **로컬만**(`.gitignore`에 `testdata/itam_bench/closed/`) — 운영 구조가 원격에 올라가지 않음 · 반입은 파일 복사 / (c) 정의 파일만 추적 · 스키마 JSON은 로컬만 — **보안 판단은 사용자 몫이라 권고 없음** → **답: (a) 둘 다 추적** — 값·반출 원본(`results/`)은 넣지 않음 · W3·W4 테스트에 실제 모양 사용 가능 |

## 6. 검증

| 단계 | 방법 | 기준 |
|---|---|---|
| W1 단위 | 가짜 LLM이 FabriX 오류 문구를 돌려줌(단일 그래프 · 2단 단일 루프 · 멀티 · W4 선별 호출) | 사유 = 「LLM 백엔드 입력 토큰 한도 초과…」 · 재생성 0 · 「orchestrator」·「조회 엔진이 남긴 설명」 없음 · `tests/test_nodes/test_multi_gate_token_budget.py` 그대로 통과 |
| W2 단위 | 예산 초과 합성 스키마 | 사다리 순서 · 3단 LLM 호출 0 · 예산 안 바이트 동일(`prompt_render_diff.py`) |
| W3 단위 | 가짜 LLM · 가짜 자산 DB | 가져오기·주석·LLM 3경로 초안 · 검증 실패 409 · `manual` 보존 병합 · 되돌리기 바이트 동일 · 폴스타 프로필 5종 병합 불변 · 준비도 경고 · `tests/test_manual` |
| W4 단위 | §4.2 시드 2파일(G-7 — 실제 모양) 또는 합성 99테이블 + 정의 + 가짜 선별 | 정의 모드: 선별 3 → relevant 3(강제 보충 없음) · 범위 밖 제거 · 다리 테이블 보완 · K 자르기 · 실패 → 어휘 대체 → 0이면 안내 · Q-5 미적용 · 정의 없는 DB = 기존 테스트 비트 동일 · 단일·멀티 같은 결과 |
| W5 단위 | 같은 합성 | 정의 블록이 단일·멀티 같은 자리 · 정의 없는 DB 바이트 불변 · 표본 수집 대상 = 선별 테이블 |
| W6 단위 | 기존 벤치 테스트 + 새 필드 | 누출 관문 통과 · 새 필드 값 없음 · `save()` 뒤 `_descriptions` 유지(G-5 (a)) |
| 반출 구조 재현 | 스크래치(LLM 0 · DB 0) — §4.2 시드 사용 | 선별 후보 목록 ≈ 6.3K · 선별 3~8개 경로 시스템 프롬프트 ≤ 13K |
| 회귀 | `python scripts/regress.py --base <세션 시작 SHA> --files <자기 파일>` (D-303) | 모듈 단위 · 정적 게이트 통과 · 보고에 범위 명시 |
| MLX | **선별 4문항 + 정의 초안 1묶음 · 1회 · 예상 약 5분** — 바뀐 프롬프트 2종(선별·정의 초안)만. 샌드박스 2테이블이라 선별 정확도가 아니라 **출력 형식(JSON 파싱)·지시 준수만** 본다 | 두 평면 `mlx` 확인 뒤(`--show-env`) · 승인 불요(D-240) · 정확도·지연 결론은 내부망 |
| 폐쇄망(사용자) | 반입 → ①W1·W2만으로 2회차 가능 ②관리자: 시드 정의 가져오기 → 정의서 대조·수정 → 승인 → `python -m scripts.itam_bench --run --env closed` | ITAM 단일 턴 `non_sql`·`backend_limit` 0 · SQL 관측 턴 ≥ 10 · `selection_source=llm` 비율 · `presented_tables`와 2회차 `gold_tables` 대조(선별 재현율) · 턴당 지연 감소 |

## 7. 위험

- **선별 누락**: LLM이 필요한 테이블을 빼면 SQL 검증이 재생성만 반복한다(재선별 없음 · W5 ③). 2회차 `gold_tables` 대조로 재현율을 재고, 낮으면 「검증이 선별 밖 테이블을 지목하면 1회 재선별」을 후속으로 검토한다.
- **초안 정의의 오류**: §4.2 정의는 컬럼 이름만 보고 썼다(「(추정)」 다수 · `수집적재` 성격은 가설). 내부망 정의서 대조 전에는 선별을 잘못 이끌 수 있다 → W7 절차에서 대조 후 승인한다.
- **연결 후보 오탐**: `related`는 이름 일치 추정이다. 값 겹침(D-294 ②) 확인 전에는 다리 테이블 보완(W4 ③)에 승인 관계를 우선하고, `related`는 선별 힌트로만 쓴다.
- 추정 계수(ASCII 4자 · 비ASCII 1.5자/토큰)는 이번 run에서 과소로 보이나 근거가 1건이다 — W6-d 기록 뒤에 판단한다(D-159 「주의」).
- 작업 트리에 병행 세션 변경이 있다(`plans/122`·`136`·INDEX·`src/config.py`·`src/state.py` 등) — 구현 시 `git status` 재확인 · 자기 파일만 회귀 기준(`--files`).

## 8. 변경 이력

| 버전 | 날짜 | 내용 |
|---|---|---|
| v1.0 | 2026-10-06 | 작성 — run `20261006-152938` 반출물 분석 · 원인 사슬 §2 · W1~W5 · G-1~G-5 · D-308 예약 |
| v1.1 | 2026-10-06 | 사용자 지시 ②③ 반영 — **테이블 관리 정보 정의 → LLM 선별 → 선별 테이블로 생성** 구조로 개편: §2.6(테이블 의미 부재) · §4.1 흐름 · §4.2 시드 2파일(`itam_schema.json` · `table_definitions.yaml` — 대조 오류 0 · 선별 목록 6,257토큰 실측) · §4.3 자산 형식 · W3 정의 자산 · W4 정의 기반 선별(단일·멀티 공용) · W5 선별 테이블 생성 · W2(예산 사다리)는 안전망으로 앞당김. **v1.0 G-1(강제 보충 상한 10)은 폐기** — 정의 모드에서는 강제 보충 자체가 없다. 게이트 재편 G-1~G-7(G-7 시드 파일 git 추적 신설) · `tcdmsif81` 비밀번호 컬럼 관찰 추가 |
| v1.2 | 2026-10-06 | 인터뷰 답 반영 — G-1 (a) · G-2 (a) · G-3 (a) · G-4 (a) · G-5 (a) · **G-6 (b) 사용률 정본은 관측 DB**(ITAM-114 기대값 유지 · W7 통지·시드 `notes`) · **G-7 (a) 시드 2파일 git 추적** · 상한 K=8 확정 · D-308 등재(D-159 부분 개정 · D-294 부기) · 「G-7」 표기 중 `plans/135` 소관 2곳을 구분 |
| v1.3 | 2026-10-06 | **구현**(W1~W7 · §9) — 사용자 지시 *"138번 계획을 구현하라."*(병합 재부여 전 번호) · Wave별 구현 에이전트 6 · 독립 검증·보안 감사 1라운드(발견 F-1~F-8 → F-5 제외 교정) · MLX 선별 4문항 + 정의 초안 1묶음 1회 · W7: 132·122·135 통지 · 시드 `notes` 13테이블 · 2회차 관찰 4건(ITAM-115~118) · 파일명 `-TODO` → `-WIP`(잔여 = 내부망 2회차) |
| v1.4 | 2026-10-07 | **W2 안전망 교정** — 사용자 보고 *"itam 조회시 아직도 토큰 한도 초과 오류가 발생"* · 원인 = 추정기 과소(ITAM 프롬프트 o200k 실측/추정 1.28~1.47)로 사다리가 1단에서 멈춤 → `estimate_prompt_tokens` 조각 단위 교체 (D-159·D-308 부기 · 같은 프롬프트가 2단 약 22.7K로 전송) · `tcdmsif81` 시드 notes 정리 · `docs/18` 항목 |

## 9. 구현 기록 (v1.3 · 2026-10-06 · 세션 시작 `71d7ff6` · 사용자 커밋 `9cc8c6b`(W1~W6·검증) · `42deda2`(병합 — 138 → 139 · D-305 → D-308 재부여) · `8f9b1c4`·`bac814c`(검증 교정) · W7은 작업 트리)

### 9.1 Wave별 결과

| W | 결과 | 주요 파일 · 테스트 |
|---|---|---|
| W1 | **완료** — 공용 판정 `detect_llm_backend_error`·`LLMBackendError`(멀티 문구 비트 동일) · `validate_sql`이 SELECT 검사 전에 사유 하나만 낸다 · `query_validator` `backend_error` 표지·`backend_limit_hit`·`[토큰예산]` 로그(보고 토큰·한도·자체 추정) · 그래프·2단 단일 루프·멀티 모두 재생성 0 · 종결 사유 `backend_limit` | `src/sql_validation.py` · `src/nodes/{query_validator,multi_db_executor}.py` · `src/graph.py` · `src/orchestration/{subagents,replanner}.py` · `test_plan139_backend_error.py` 22 |
| W2 | **완료** — `_fit_single_prompt_budget`(시스템+사용자 추정 · ①유사어·설명 ②표본 ③`PromptBudgetExceeded` → LLM 0) · state `prompt_budget`(두 생성자에서 초기화) · 예산 안 바이트 동일 | `src/nodes/{query_generator,prompt_blocks}.py` · `src/state.py` · `test_plan139_single_budget_ladder.py` 13 |
| W3 | **완료** — 도메인 `table_definitions`(종류 12 · 한도 · 가져오기 파싱) · 테이블 단위 병합(`manual` 보존) · 준비도 C11 · 초안 3경로(가져오기·주석·LLM 묶음 — 추정 먼저 · 실패 묶음 재실행) · 편집 → `manual` · 승인 409 · API 4경로(관리자 전용) · 화면 · **매뉴얼 A-67**(캡처 `dbs-table-definitions`) | `src/domain/{table_definitions,profile_merge,db_readiness}.py` · `src/schema_cache/{asset_generation_service,db_structure_service}.py` · `src/prompts/asset_generation.py` · `src/api/routes/db_structure.py` · `src/static/js/admin-db-structure.js` · 테스트 46 + 16 + 37 |
| W4 | **완료** — `select_tables`(단일 `schema_analyzer`·멀티 `_analyze_schema` 공용 · 후보 = 스키마 ∩ `allowed_tables` · 강제 보충·Q-5·EAV 보충 없음 · 중간 테이블은 승인 관계 → `related` 순 · 상한 8 · 실패 → 어휘 대체(가중 manages 3·대표 컬럼 2·notes 1·컬럼 1 · 수집적재 제외) → 0이면 `selection_none` 안내 종결) · state `table_selection` · 설정 `TEXT2SQL_SCHEMA_TABLE_SELECT_MAX` | `src/nodes/table_selection.py` · `src/prompts/table_selection.py` · `src/config.py` · `.env.example` · `config/settings_help/text2sql.yaml` · `test_plan139_table_selection.py` 44 |
| W5 | **완료** — 「테이블 용도」 블록(`build_table_purpose_block` · 선별 테이블의 `manages`·`notes`) · 단일·멀티 `schema=` 맨 앞 같은 자리 · 정의 없는 DB 빈 블록 | `src/nodes/prompt_blocks.py` · `test_plan139_definition_block.py` 33 |
| W6 | **완료** — §6 W6 a~f 전부 · G-5 유실 **재현됨** → `save()`가 `schema` 밖 키 보존 · D-301 부기 | `src/schema_cache/persistent_cache.py` · `scripts/itam_bench/{catalog,__main__,report,_serve,judge}.py` · `test_plan139_w6_persistent_extras.py` · `test_itam_bench_plan139_w6.py` 38 |
| 검증·감사 | **1라운드** — 수용 기준 §6 충족(폐쇄망 제외) · 경로 대칭 실제 노드 3경로 · 요청 스코프 2턴 체크포인터 · 발견 8건 중 7건 교정(F-1 한글 민감 컬럼 표본 마스킹 · F-2 YAML 앵커/별칭·깊은 중첩 422 · F-3 NFKC·서식 문자·`~~~`·영역 30자 · F-4 질의 경로 정의 재정제 · F-6 블록 머리말 · F-7 어휘 대체에서 수집적재 제외 · F-8 LLM 대표 컬럼 10개 절단) · F-5는 범위 밖(§9.4) | `src/security/data_masker.py` 외 · `test_plan139_verify_paths.py` 9 · `test_plan139_findings_fix.py` 35 · `test_plan139_findings_fix_assets.py` 31 · 매뉴얼 `admin.md` 검증 문단 · `config/settings_help/security.yaml` |
| W7 | **완료** — 132·122·135 통지 · ITAM-114 기대값 유지 · 시드 `notes` 13테이블 「사용률 정본 아님 — 관측 DB」 · 2회차 관찰 4건(ITAM-115~118 — 용도 기재 · 분류 경로 · 스토리지 업무 이름 · 스토리지 → 서버) · 내부망 관리자 절차(135 부록 A 2a) | `testdata/itam_bench/closed/table_definitions.yaml` · `testdata/itam_bench/scenarios.closed.yaml` · `tests/test_scripts/test_itam_bench_closed_kit.py`(개수 14 → 18) |

### 9.2 검증

- **정의 없는 DB 비트 동일(G-1)**: `prompt_render_diff --ci` 차이 0(데이터 조회 13,959자 · 알람 24,103자) · 6프로필(polestar 4종·itam·test_db) × 2문항 × 단일/멀티 × 선별/생성 하네스 바이트 동일(`PYTHONHASHSEED=0` · 기준 worktree 대조).
- **회귀**(D-303 · 모듈 단위): 검증 라운드 본체 파일·폴더 342 — 통과 8,365 · 실패 1 · 에러 19 · 건너뜀 49 · 실패 귀속 전부 「원래 실패」(`test_e2e_polestar.py` 19 실 DB 픽스처 · `test_plan104_local_sandbox_profile_gate` 1) · 정적 게이트 diff 줄 위반은 모두 `plans/122` 줄. 교정 라운드 통과 2,194 · 실패 0(ruff 1줄 `query_generator.py:1003` UP045는 `1ac2f93` 줄). W7 통과 349 · 실패 0 · 정적 게이트 통과. 각 라운드 마지막 줄 `범위: 모듈 단위 — 전체 미실행`. W4·검증 라운드는 `[전체 회귀 권고]`(허브 모듈 `src.config` 87% · 직접 선택 55%)를 냈고 전체는 돌리지 않았다.
- **정적 게이트**: arch_check 위반 0 · 경고 106(기준 101 · +5는 `table_selection` application→application import) · overfit_check 신규 유입 0(공용 계층에 ITAM 테이블명 리터럴 0).
- **MLX**(D-240 · 두 평면 `mlx` 확인 · 검증자가 캐시 모델 `Qwen3.5-9B-OptiQ-4bit`를 127.0.0.1:8080에 직접 띄우고 끝나고 종료): 선별 4문항 — JSON 파싱 4/4 · 후보 안 이름 4/4 · 상한 이하 4/4 · 출처 `llm` · 문항당 25~28초. 정의 초안 1묶음 — 응답 10/10 파싱 · 유효 4 · 무효 6(전부 대표 컬럼 11~12개 → F-8로 절단 처리). 합계 2분 27초. 정확도·지연 결론은 내지 않는다.
- **반출 구조 재현**(LLM 0 · DB 0): 선별 후보 목록 **7,437토큰**(§6 기준 ≈6.3K보다 18% 큼 — 렌더러가 줄마다 `[성격]`·`연결:`을 싣는다 · 계획 형식으로 줄이면 5,919) · 선별 3/5/8개 시스템 프롬프트 1,881/3,570/4,693(≤13K · 표본 5행 가정 3,496/6,888/9,997).

### 9.3 계획과 다르게 한 것

- **용도 블록이 정의를 읽는 함수**: `table_selection.definitions_of` 대신 같은 위치(`_structure_meta[PROFILE_KEY]`)를 도메인 키로 직접 읽는다 — 노드 간 import가 arch 경고를 1건 더 늘려서다. 같은 위치·같은 의미는 대조 테스트로 고정했다.
- **한글 민감 표현은 설정이 아니라 `data_masker` 내장**(F-1): 운영 `.env`가 `SECURITY_SENSITIVE_COLUMNS`를 영문 6종으로 덮어써 설정 기본값을 바꿔도 운영에 닿지 않는다. 폴스타 조회 대상 컬럼과 대조해 걸리는 컬럼 0건을 확인한 뒤 적용했다.
- **질의 경로 정제는 테이블 단위로 뺀다**(F-4): 한 칸이라도 어긋나면 그 테이블 정의를 통째로 뺀다(필드 단위 아님) — 승인 검증과 W5 테스트가 이미 그 방식이다.
- **시드 `manages` 2곳 문구 정리**(W7): `tcdmsif73` 「사용률 추이·과거 시점 질문용」 → 「과거 시점 질문용」 · `tcdmsif90` 「호스트 사용률 추이 질문용」 삭제 — G-6 (b)와 반대로 선별을 이끄는 문구라 `notes` 추가와 함께 고쳤다. `tcdmsif90` `notes`는 1회차 오답 사실을 적었다.
- **설정 카탈로그 개수 단언**(`test_settings_catalog.py` 391 → 392): 병행 `plans/122` 세션도 같은 줄을 쓰는 공유 단언이다 — `plans/122`에 통지했다.

### 9.4 잔여 · 내부망

| 잔여 | 처분 |
|---|---|
| **내부망 2회차**(사용자) | 반입 → 관리자 「DB 구조」 탭 시드 정의 가져오기 → 내부망 정의서와 대조·수정 → 테이블 단위 승인(`tcdmsif81` 조회 대상 제외 권고) → `python -m scripts.itam_bench --run --env closed`(18건 · 19턴). 기준: ITAM 단일 턴 `non_sql`·`backend_limit` 0 · SQL 관측 턴 ≥ 10 · `selection_source=llm` 비율 · 턴당 지연 |
| 2회차 반입 뒤(우리) | 정답 SQL·`gold_tables` 작성 → 선별 재현율 · `[토큰예산]` 로그로 토큰 추정 계수 대조(D-159 주의 · 2026-10-07 조각 단위 추정기로 교체됨 — 교체 뒤 값과 FabriX `Given` 대조) · 재현율이 낮으면 「검증이 선별 밖 테이블을 지목하면 1회 재선별」 검토(§7) · `plans/137` 잔여(폐쇄망 ITAM 실 질의)도 같은 run으로 확인 |
| 선별 프롬프트 7.4K | 예산 안이라 그대로 — 2회차 지연을 보고 줄 형식 축소(5.9K) 검토 |
| F-5 기존 인증 결함 | `src/api/dependencies.py` `_verify_user_token`이 `type` 클레임을 보지 않는다(공용 인증 · 이 계획 전부터) — 별도 작업 |
| 마스킹 범위 밖 | 관리자 설명 생성기 표본 · `structure_meta.samples` · EAV 값 수준 비밀(속성명이 비밀번호인 행의 값) · 「암호」 부분 매칭으로 「암호화여부」 같은 컬럼도 가려짐 |
| `selection_none` 안내 | 안내 문구가 정제하지 않은 원본 `group`을 읽는다 — 파일을 직접 고친 경우에만 해당(승인 경로는 30자·금지 검사) |
| 분류 중복 | 벤치에서 `backend_limit` 턴이 `no_sql`로도 함께 잡힐 수 있다 — 2회차 리포트에서 확인 |
| 카탈로그 DB 설명 | 벤치 카탈로그의 DB 설명은 아직 파일만 읽는다(컬럼 설명·유사어만 Redis → 파일) |
| 기존 비결정성 | `schema_analyzer` 강제 보충의 set 순회로 폴스타 relevant 순서가 `PYTHONHASHSEED`에 따라 바뀐다(이 계획 전부터 · KV 캐시 재사용에 불리 — `plans/121` TP-11.10) |
| arch 경고 +5 | 노드 4곳(`schema_analyzer`·`query_generator`·`query_validator`·`multi_db_executor`)이 `table_selection`을, `table_selection`이 `sql_validation`을 import(application → application) — 선별은 노드 안 호출이라 그래프 라우팅으로 풀 구조가 아니어서 그대로 둔다 |

### 9.5 측정 못 한 것

- 내부망 FabriX/vllm에서의 선별 정확도·재현율·지연 — MLX 9B는 형식만 봤다.
- 실제 108테이블 정의(내부망 정의서 대조 후)로 만든 선별 목록 크기 — 시드 초안 기준 수치만 있다.
- `related`의 값 겹침(D-294 ② ≥ 0.9) — 반출물에 값이 없다.
