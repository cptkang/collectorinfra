# Plan 73 — 폼필 결정적 경로 + 멀티턴 HITL 폼필 (v2)

- 작성일: 2026-07-30 (v2 개정: 사용자 검토 의견 반영 — form_profiles YAML 철회,
  유사어·HITL 중심 재설계)
- 상태: **종결(2026-08-03, §13·D-149)** — Phase 1~3 구현·라이브 게이트 전체 통과.
  Phase 4는 D-149로 축소 확정(재분류, 이관·제거 철회), Phase 2b/층3은 수요 실증 시
  재개. 잔존 백로그는 §13 참조
- 선행: Plan 72 (결정적 엔진 3존 그린 완료), Plan 70/D-140 (존 역질문 배선),
  Plan 63 (과적합 분리), D-067/D-068, D-142~D-145
- 신규 결정 예정: D-146 (폼필 결정적 계약화 + LLM 추론 매핑의 채움 금지),
  D-147 (폼필 단일 태스크 고정), D-148 (멀티턴 HITL 폼필 + 확인 이력)

---

## 0. 배경 — 문제의 구조적 정의

### 0.1 라이브 실측이 보여준 패턴 (Plan 72, 2026-07-27~30, 8라운드)

| 증상 | 원인 층 |
|---|---|
| VARCHAR_FORMAT SQL·월 역방향 (1차) | LLM 폴백이 경로를 가로챔 |
| Excel 6칼럼 동일값 (3차) | LLM 매핑 N:1 → writer 역매핑 |
| TPMC에 메모리 용량 (4차) | LLM 매핑 캐시 전이 |
| `c.category` 쿼리 전멸 (5연속) | LLM 매핑(타 테이블 칼럼)의 조립 계약 위반 |
| 서버 목록 필드별·런별 상이 (7차~) | 전 구간 LLM 경로 (월 시리즈 없어 결정적 미발동) |
| **단순 양식 GROUP BY 에러 (2026-07-30)** | **경로 선택 자체가 LLM 매핑에 종속** |

마지막 건: 단일 헤더 양식(서버 이름·IP·OS·CPU 코어 수·메모리 용량)을
B0+CM_YD+CM_GP로 실행 시 B0만 성공, CM 두 DB는 `column "r.name" must appear in
the GROUP BY clause`. 실패 SQL은 결정적 조립기 산출물이 아니라(조립기는 별칭
`c`/`cc`/`s` + 전 SELECT 집계 + GROUP BY COALESCE — 구조적으로 이 에러 불가)
LLM 폴백 산출물이며, `r` 별칭의 출처는 폴백 프롬프트 예시다. 경로 분기
`use_multi_resource_pivot = (child_eav or month_series)`에서 child_eav 성립이
per-DB LLM 매핑·캐시 상태에 종속된 것이 근본 원인.

### 0.2 문제의 재정의

"양식마다 새 에러"의 원인은 양식 지식 부족이 아니라 다음 두 가지다.

1. **경로 결정의 비결정성** — 어느 DB·어느 런이 결정적 경로를 탈지가 LLM 산출물에
   걸려 있고, 조건 미달 시 폼필 계약(서버당 1행, GROUP BY 정합, 필드명=행 키)을
   보장하지 않는 LLM 폴백으로 떨어진다.
2. **매핑 실패의 침묵 처리** — field_mapper는 이미 3단계(프롬프트 힌트 → Redis
   유사어 → LLM 추론, `mapping_sources`로 출처 구분)인데, 유사어가 못 푼 필드를
   **LLM 추론이 침묵으로 메운다**. 라이브 실패의 대부분(TPMC 오염, c.category,
   acl_id, 도입일자=epoch)이 이 llm_inferred 매핑에서 발생했다.

### 0.3 오버피팅 판정 기준 (v1 유지)

- **양식별 지식**: 도메인 요구사항 — 코드에 있으면 오버피팅, 데이터/이력에 있으면 설정.
  판별 지표: **새 양식 지원에 필요한 코드 변경 = 0줄**.
- **구조 능력**(파서·조립기·인식기·계약 검증): 양식 무관 일반 능력 — 그대로 재사용.

### 0.4 v1 → v2 변경 요지 (사용자 검토 의견 4건 반영)

| v1 | 검토 의견 | v2 |
|---|---|---|
| 층3: 수작성 form_profiles YAML | 수많은 양식을 프로필로 관리하는 것은 비현실적. 이름/내용 충돌 규칙 불명확 | **철회.** 지식 저장소를 ①기존 유사어(문맥 무관 지식) ②HITL 확인 이력(자동 축적)으로 대체 — 사람이 관리하는 프로필 목록 자체를 없앰 |
| 층4: 질의 내 지시 오버라이드만 | 매칭 실패 시 사용자에게 되묻는 멀티턴 폼필은 안 되는가 | **채택·확장.** 미해결 필드를 역질문으로 승격(선례: D-140 clarification, pending_synonym_registrations 멀티턴). 지시 오버라이드는 그 부분집합 |
| (암묵) LLM 추론 매핑을 채움에 사용 | 유사어로 처리하면 되지 않는가 | 유사어 매핑(+힌트)은 채움에 사용, **llm_inferred 매핑은 폼필에서 채움 금지 — 역질문 후보로 강등**(핵심 신규 원칙) |
| — | DeepAgent 회귀(vLLM/GPU) 검토 중 | §7에 비교 분석 — 회귀 반대, 단 의사결정 게이트 제안 |

---

## 1. 목표 · 비목표

### 목표

1. 양식 채우기 요청은 **항상** 결정적 조립 경로로 처리한다(경로 비결정성 제거).
2. 폼필에서 확신 없는 매핑(llm_inferred)은 채우지 않는다 — **공란+사유** 또는
   **역질문**으로 귀결. "에러"라는 범주를 "계약된 공란/질문"으로 대체.
3. 매핑 실패 필드는 멀티턴 역질문으로 사용자에게 묻고, 답변을 검증 후 적용한다.
4. 사용자 확인된 매핑은 양식 시그니처 단위로 영속화하여 재질문을 없앤다
   (수작성 프로필의 역할을 자동 축적 이력이 대체).
5. 폼필 오케스트레이션을 단일 태스크로 고정한다(B0 2배 행 해결).

### 비목표

- Word 템플릿 멀티로우 표, 비EAV DB 폼필 결정화, 미수집 도메인 신규 수집 (v1 동일).
- **LLM 산출물의 자동 영속화** — 확인 이력의 쓰기 게이트는 사용자 명시 답변뿐
  (Known Mistakes: 유사어 자동 등록 = 오염 자기강화 루프).
- 전역 유사어의 양식 문맥 의존 지식 등록 — '처리능력'처럼 양식에 따라 의미가 바뀌는
  단어를 전역 등록하면 4차 라운드(TPMC에 메모리 용량) 사고가 제도화된다(D-145 유지).

---

## 2. 설계 — 매핑 우선순위 3+1층

사용자 제안(사용자 > 유사어 > 기존 패턴)을 골격으로 채택하되, 문맥 의존 지식의
안전한 저장 위치로 "확인 이력"을 유사어 옆에 추가한다.

```
[우선순위 높음]
 1. 사용자   — 질의 내 명시 지시 + 역질문 답변 (턴 스코프, 검증 후 적용)
 2. 지식     — 2a. 확인 이력(양식 시그니처 스코프, 사용자 확인만 축적)
              2b. 유사어·힌트(기존 field_mapper 1·2단계, 문맥 무관 지식)
 3. 기존 패턴 — 결정적 조립 엔진 + 구조 인식기(월 시리즈 등) + 계약 검증(FIX-15)
[채움 금지]
 ×. llm_inferred 매핑 — 폼필에서는 채움에 쓰지 않고 역질문 후보로만 사용
```

### 2.1 경로 결정화: 게이트 확장 (D-146 예정) — v1 층1 유지

결정적 피벗 발동 조건을 **"폼필 intent(template_structure) + eav_pattern 존재"**로
확장(월 시리즈·child_eav 불요). 단일(`query_generator.py` `_try_build_form_fill_pivot_sql`
게이트)·멀티(`multi_db_executor.py` `use_multi_resource_pivot`) 대칭 수정.
eav_pattern 필수 유지로 비폴스타 DB 무영향. 0.1의 GROUP BY 건 근본 수정.

**신규 원칙(D-146에 포함)**: 폼필 경로에서 `mapping_sources[field] == "llm_inferred"`인
매핑은 조립 SELECT에 넣지 않는다(공란 처리, 미해결 목록에 등재). 유사어·힌트 출처
매핑과 구조 인식기(월 시리즈)·기존 확정 규칙(처리능력/비고/Vendor+Model — Plan 72
검증 자산)만 채움에 사용한다. llm_inferred 차단으로 생기는 공란은 2.3의 역질문이
받는다 — 침묵 오염(TPMC·acl_id·epoch류)의 원천 차단.

### 2.2 폼필 단일 태스크 고정 (D-147 예정) — v1 층2 유지

`intent_planner.py` 계층 A에 ③.5 추가: template_structure 존재 → `data_query`
단일 태스크(LLM 분해 우회, mapped_db_ids 있으면 db_ids 승계). 파일 없는 "양식
채워줘"류는 안내 응답으로 단락(첨부 요청 + 지원 형식).

### 2.3 멀티턴 HITL 폼필 (D-148 예정) — v2 신규 중심축

**흐름**:

1. **1차 런**: 게이트 확장 경로로 채울 수 있는 것(유사어·힌트·확인 이력·인식기)은
   채우고, 미해결 필드(미매핑 + llm_inferred 강등 + FIX-15 제외분)를 수집한다.
2. **역질문 응답**: 산출물(부분 채움 Excel)과 함께 미해결 필드를 구조화 질문으로
   제시한다. 필드별 선택지: ①공란 유지 ②DB 항목 지정(후보 제시 — llm_inferred가
   추천으로 강등되어 여기서 재활용: "LLM 추천: cmm_resource.description") ③값 직접
   입력 ④이 답을 기억(확인 이력 저장 여부).
3. **답변 턴**: 사용자 답변을 구조화 파싱(v1 층4의 지시 파싱과 동일 기계) →
   **존재성 결정적 검증**(FIX-15 게이트 재사용 — entity 칼럼/EAV 속성 해석 가능 여부)
   → 통과분만 매핑에 적용해 재조립·재채움. 해석 불가 답변은 거부 사유와 함께 재질문.
4. **질의 내 즉시 지시**("비고에는 등록명을 넣어줘")는 같은 파서·검증기를 1차 런
   전에 적용 — 역질문의 선불형일 뿐 별도 기계가 아니다.

**기존 배선 재사용(신규 인프라 최소화)**:

- 역질문 UX: D-140 `clarification_needed`(intent_planner → query.py → 프론트) 선례.
- 멀티턴 대기 상태: `pending_synonym_registrations` 선례와 동형의
  `pending_form_fill`(파싱된 template_structure + 부분 매핑 + 미해결 목록 보존,
  intent_planner 계층 A pre-check로 답변 턴 라우팅). 요청 스코프 상태 명시 초기화
  원칙(Known Mistakes) 준수 — 폼필 완료·이탈 턴에 자기정리.
- 미해결 필드 소스: field_mapper가 이미 산출하는 `mapping_sources`·매핑 리포트.

### 2.4 확인 이력 (수작성 프로필의 대체) — v2 신규

역질문 답변 중 사용자가 "기억"을 선택한 항목을 영속화한다.

- **키 = 양식 시그니처**: **헤더 행의 필드명 집합만**으로 계산(데이터 행·파일명·
  시트명 불포함 — 사용자가 값 일부를 미리 채워도 시그니처 불변). 필드명은 정규화
  후 해시: 공백·개행 제거, 소문자화(라틴), 전각/반각 통일(정규화 스펙은 단위
  테스트로 고정 — "IP 주소"="IP주소"). → **"이름 같고 내용 다름"**은 별개 이력,
  **"이름 다르고 내용 같음"**은 같은 이력, **띄어쓰기 교정·값 선입력**은 동일
  시그니처 유지.
- **시그니처 드리프트(필드 추가·삭제·문구 개정)**: 정확 일치 실패 시 최근접
  시그니처(집합 유사도 ≥ 임계)의 **필드 단위 이력을 "추천"으로 강등**해 역질문
  드롭다운에 표시 — **자동 적용은 정확 일치만**(오적용 안전측). 사용자 확정 시
  새 시그니처로 저장. 항목에 `last_used`·적중 횟수를 기록해 미사용 중복 이력의
  식별·정리(운영 경로)를 지원.
- **값 = {필드명: 확인된 소스}**: `column/eav/child_eav/concat/blank(+사유)/literal`
  — 조립기 기존 능력의 부분집합 어휘(엔진/지식 분리, v1 C5 유지).
- **쓰기 게이트**: 사용자 명시 확인 + 존재성 검증 통과분만. LLM 산출물 직접 저장
  금지. 저장·적용 내역은 응답에 노출(침묵 금지).
- **저장소**: Redis(기존 캐시 계층) + 로컬 YAML 폴백 — 유사어 저장 구조 동형.
  단 유사어와 키 공간 분리(양식 시그니처 스코프): 전역 질의에 영향 0.
- **관리 규칙**: 사람이 관리하는 목록이 아니라 사용 이력의 캐시다. 잘못 기억됐으면
  다음 런의 역질문 ④에서 갱신(최신 확인이 승리). 삭제는 기존 캐시 관리 명령 경로.
- Plan 72의 3양식 확정 규칙(처리능력·비고·Vendor+Model)은 검증된 코드 자산으로
  유지하고 이관하지 않는다 — 확인 이력은 **새 양식**의 축적 수단.

### 2.5 응답 계약 (D-144 확장)

폼필 응답: ①기준월(해당 시) ②채움 통계 ③공란 필드+사유(정책/매핑 불성립/데이터
부재 구분) ④사용자 지시·이력 적용 내역 ⑤미해결 필드 역질문(있을 때).

---

## 3. 오버피팅 방지 계약 (릴리스 게이트)

| # | 지표 | 검증 |
|---|---|---|
| C1 | 새 양식 지원 = 코드 0줄 (역질문 1회 + 이력 축적으로 수렴) | Phase 3 게이트: 신규 가상 양식 시나리오 |
| C2 | 기관명·양식명 하드코딩 0건 | `grep -rn "금감원\|FSS" src/` = 0 |
| C3 | LLM 산출물의 자동 영속화 0건 (쓰기 게이트 = 사용자 확인) | 코드 리뷰 + 쓰기 경로 테스트 |
| C4 | 확인 이력·역질문 부재 시 현행 동일 동작 | 기존 회귀 전체 통과 |
| C5 | 이력 소스 어휘 = 조립기 기존 능력의 부분집합 | 어휘 화이트리스트(미지 소스 거부) |
| C6 | 전역 유사어 오염 0건 (양식 문맥 지식의 전역 등록 금지) | D-145 유지, 등록 경로 grep |

## 4. 트레이드오프 · 사이드이펙트 · 리스크

| # | 항목 | 내용 | 완화책 |
|---|---|---|---|
| R1 | **1차 런 커버리지 축소** | llm_inferred 채움 금지로 첫 런 공란 증가(예: 서버목록의 IP·OS가 유사어에 없으면 공란+질문) | 의도된 트레이드오프 — 침묵 오답(TPMC·acl_id·epoch)을 질문으로 치환. 자주 쓰는 문맥 무관 필드는 유사어 시드 보강(1회)으로 1차 커버리지 회복. 이력 축적 후엔 질문 자체가 소멸 |
| R2 | **역질문 UX 비용** | 새 양식 첫 런에 턴 1회 추가 | 감사자료 특성상 수용 가능(오답 제출 비용 ≫ 턴 1회). 이력 재사용으로 2회차부터 0. 미해결 0건이면 질문 없이 완료 |
| R3 | **멀티턴 상태 회귀** | pending_form_fill이 체크포인터 델타 병합·스킵 경로 자기정리 원칙을 어기면 상태 오염 | Known Mistakes 원칙 준수를 게이트 항목화: 라우트 명시 초기화 + 이탈 턴 자기정리 + thread_id 프론트 배선 실측 |
| R4 | **확인 이력 오염** | 사용자가 잘못 확인한 매핑이 고착 | 존재성 검증 선행 + 적용 내역 매 런 노출 + 역질문 ④로 갱신 가능(최신 승리) + 캐시 관리 명령으로 삭제 |
| R5 | **게이트 확장 오발동** | 데이터 파일 업로드까지 피벗화 | form intent = template_structure(양식 파서 산출물) 한정 — 데이터 파일은 CSV 변환 경로라 미생성. 회귀 고정 |
| R6 | **답변 파싱 오해석** | 자연어 답변의 과추출/오추출 | 구조화 선택지 우선(①~④) + structured output + 존재성 검증 이중 게이트 + 적용 내역 명시. 파싱 실패 = 재질문(안전측) |
| R7 | **단일 태스크 고정의 표현력 손실** | 폼필+타 작업 복합 요청 단일화 | 1차 수용, 복합 수요 실측 시 확장(v1 동일) |

## 5. Phase 구성 · 게이트

### Phase 1 — 경로 결정화 (2.1 + 2.2) : 즉효 범위, 선배포 후보

- 게이트 확장(단일/멀티 대칭) + llm_inferred 채움 금지 + intent_planner ③.5 +
  파일 없는 폼필 안내. 수정 3~4파일 + 단순 양식 픽스처(0.1 케이스).
- **게이트 1**: ①단순 양식 3존 라이브 GROUP BY 에러 소멸 + 3존 결정적 조립 로그
  ②B0 폼필 태스크 1개 ③CPU·메모리 양식 3존 그린 유지 ④신규 테스트 실패 0.
- 주의: 이 시점의 llm_inferred 강등은 공란+사유까지만(역질문은 Phase 2) —
  공란 증가가 게이트 1 라이브에서 관찰될 수 있음을 사전 공지.

### Phase 2 — 멀티턴 HITL (2.3) + 질의 내 지시

- pending_form_fill 상태·역질문 응답·답변 파서·존재성 검증·재조립.
- **게이트 2**: ①미해결 필드 역질문 → 답변 → 재채움 e2e(3존 중 1존 라이브)
  ②"처리능력에는 메모리 용량" 즉시 지시가 CPU 양식에서 반영+내역 명시
  ③해석 불가 답변 거부+재질문 ④이탈 턴 상태 자기정리 실측.

### Phase 3 — 확인 이력 (2.4)

- 시그니처 키 저장·재사용·갱신·삭제(롤백 3경로) + 응답 노출.
- **게이트 3**: ①같은 양식 2회차 런에서 재질문 0 + 이력 적용 내역 노출
  ②신규 가상 양식을 역질문 1회+이력만으로 지원(C1 실증) ③이력 삭제→역질문
  부활 실측 ④C2~C6 통과.

### Phase 4 (선택) — 기존 양식 오버피팅의 소거: 동결 → 이관 → 제거

현재 코드에 남는 양식 지식(오버피팅 표면)의 전수: `apply_capacity_scope_rule`
('처리능력'), `apply_remark_server_name_rule`('비고'), `find_vendor_model_concat`
('제조사(모델명)'류), `correct_servername_hostname_mapping`('서버명'). 파서·인식기·
조립기·계약 검증은 구조 일반형으로 해당 없음.

1. **동결(Phase 1부터 즉시)**: 신규 양식 지식의 코드 유입 금지(C1) — 오버피팅
   총량이 위 4건에서 더 늘지 않음을 계약화.
2. **이관(Phase 3 완료 후)**: 금감원 3양식의 확정 매핑을 해당 양식 시그니처의
   확인 이력 **시드 항목**으로 등록(사용자 확정 이력이므로 쓰기 게이트 정합).
3. **제거(동등성 게이트 후)**: 시드 경로가 3존 그린을 재현하면 규칙 함수 4종을
   삭제 — 코드의 양식 지식 0줄 도달. 게이트 미통과 시 규칙 유지(검증 자산 보호).

각 Phase 전달물에 폐쇄망 배포 프로토콜 포함: 반영 파일 목록 + 확인 grep 심볼 +
판별용 신규 로그 라인.

## 6. 테스트 계획 (요지)

- 경로 매트릭스(form intent × eav_pattern × child_eav × mapping_sources) 단위 테스트.
- 단순 양식 픽스처 e2e: 조립 SQL 스냅샷(별칭 `c`·GROUP BY COALESCE 단언).
- llm_inferred 강등: 채움 제외 + 미해결 등재 + 사유 노출.
- HITL: pending 상태 수명(생성→답변→정리/이탈), 답변 파싱, 검증 통과/거부, 재조립.
- 확인 이력: 시그니처 정규화(이름 무관 동치), 쓰기 게이트(사용자 확인 없인 저장 0),
  갱신(최신 승리), 부재 시 현행 동일(C4).

## 7. DeepAgent 회귀(자체 tool calling, AWS GPU + vLLM) 대안 검토

팀장 검토 중인 대안에 대한 의견. 결론: **폼필 문제의 해법으로는 반대**, 단
역량 확장 인프라로서의 검토는 별개 사안으로 분리 권고.

### 7.1 실증 데이터의 방향

8라운드 라이브의 모든 실패는 LLM에 재량이 있던 지점에서 났고, 모든 수정은 재량을
제거하는 방향이었다(§0.1). DeepAgent는 LLM 재량을 **확대**하는 구조다 — 도구 선택·
호출 순서·SQL 작성·결과 해석 전부가 모델 판단. 더 강한 모델은 평균 품질을 올리지만
**런 간 재현성은 보장하지 않는다**. 감사 제출 자료의 요구는 평균이 아니라 재현성
(같은 질의 = 같은 산출물)이며, 이는 모델 등급과 무관하게 결정적 경로의 속성이다.
프런티어급 모델을 쓰는 조직들도 감사·정산류 산출물은 결정적 파이프라인으로
처리한다 — vLLM으로 서빙 가능한 공개 모델이 그 예외가 될 근거는 없다.

### 7.2 비용·보안·운영

- **데이터 반출**: 폐쇄망·은행 인프라 데이터(호스트명·IP·구성 정보 — 그 자체가 감사
  대상)를 AWS GPU로 보내는 것은 망분리 원칙과 충돌하는 별도 보안 심사 사안.
  FabriX PII 필터링에 민감한 조직 맥락에서 승인 리스크가 크고, 승인돼도 기간이 길다.
- **비용 비대칭**: 현 문제(새 양식 → 새 에러)의 해소 비용은 Phase 1 코드 3~4파일 +
  Phase 2 역질문 배선이다. 대안의 비용은 GPU 임대 + vLLM 운영(모델 선정·서빙·갱신·
  모니터링) + 보안 심사 + **검증된 자산(3존 그린 엔진·파서·조립기·43건 테스트) 폐기 후
  재구축**이다.
- **FabriX 한계와의 관계**: 95K 입력·tool calling 부재는 실재하나, 결정적 경로는
  바로 그 한계를 우회하려고 설계된 것이고 실제로 우회한다(조립·검증·채움에 LLM 불요).
  한계가 아픈 곳은 폼필이 아니라 탐색형 작업이다.

### 7.3 권고

1. **의사결정 게이트 제안**: DeepAgent 회귀 판단을 Phase 1~2 게이트 결과 이후로 유예.
   "새 양식 → 새 에러"가 "새 양식 → 채움+질문"으로 바뀌는지가 실측되면, 회귀의
   근거 문제 자체가 소멸하는지 확인 가능하다. 유예 비용은 낮다(Phase 1은 소규모).
2. **분리 권고**: 자체 vLLM/GPU는 "폼필 해법"이 아니라 "모델 역량 확장"(긴 컨텍스트,
   tool calling 기반 탐색형 기능) 안건으로 별도 평가. 도입하더라도 감사 산출물은
   결정적 경로 유지가 정답이며, 두 결정은 직교한다 — 묶으면 폼필 일정이 인프라
   일정에 인질로 잡힌다.

## 8. 기존 결정과의 정합

- D-067(단일 조립 엔진)·D-068(LLM 우회): 정합 — 엔진 신설 없음, 우회 범위 확장.
- D-140(역질문 배선)·pending_* 멀티턴 선례: 재사용 — 신규 패러다임 없음.
- D-144: 사유 어휘 확장(정책/사용자 지시/이력) — 정합.
- D-145(요청 스코프 격리·전역 등록 금지): **강화** — llm_inferred 채움 금지 +
  쓰기 게이트(사용자 확인)로 원칙이 코드 계약이 됨. 확인 이력은 양식 시그니처
  스코프라 전역 오염 없음.
- CLAUDE.md Phase 3 로드맵(멀티턴·human-in-the-loop 승인): 본 계획 2.3이 그 구현.

## 9. 미결 확인 사항 (사용자 결정 필요 — 선택지별 효과 상술)

### Q1. Phase 1 선배포 여부

- (2026-07-30 확인: B0 2배 행은 내부 반영으로 현재 미발생 — Phase 1의 잔여 가치는
  ①단순 양식 GROUP BY 에러 해소(경로 결정화) ②llm_inferred 침묵 오염 차단이며,
  단일 태스크 고정(③.5)은 재발 방지 계약으로 유지.)
- **결정 내용**: 게이트 확장 + llm_inferred 채움 금지 + 단일 태스크 고정 + 파일 없는
  폼필 안내를, 역질문(Phase 2)보다 먼저 폐쇄망에 배포할지.
- **선배포(기본안)**: 단순 양식 GROUP BY 에러가 즉시 해소. CPU/메모리
  3종 양식은 확정 규칙+인식기 경로라 결과 불변(회귀 테스트 고정). 단점: 새 양식
  첫 런의 공란 증가를 보완할 역질문이 아직 없어, Phase 2 배포까지는 공란+사유만
  노출됨(현재는 그 자리가 DB 에러 또는 침묵 오답이므로 순개선이긴 함).
- **묶음 배포**: 공란 증가와 역질문이 함께 도착해 UX 일관. 단점: 단순 양식 건이
  Phase 2 완료까지 라이브에서 계속 죽어 있음.
- 기존 3양식 오버피팅 우려에 대한 해소 경로는 **Phase 4(동결→이관→제거)** 참조 —
  동결(코드 유입 금지)은 Phase 1부터 즉시 발효.

### Q2. 역질문 UX 형태 (v2.1 — 구조화 패널로 확정 제안)

- **구조화 패널(기본안, 사용자 편의 지적 반영)**: D-140 존 역질문의 기존 구현
  (app.js — 체크박스 패널 + 자연어 재조합 없이 `selected_db_ids` 구조화 필드 재전송)
  과 동형으로, 미해결 필드별 위젯 패널을 채팅 버블에 렌더:
  - 필드마다 라디오 3택 — `공란 유지`(기본 선택) / `DB 항목 선택`(드롭다운: 유사어
    후보 + LLM 추천 + entity 안전 칼럼·EAV 속성, 한글 라벨 병기) / `직접 입력`(텍스트박스)
  - 필드마다 `이 답을 기억` 체크박스 — 기억의 주체는 §2.4 **확인 이력**(Redis
    `formfill:history:{양식 시그니처}`, 양식 스코프). 미체크 시 이번 턴에만 적용.
    문맥 무관 후보에는 별도의 `유사어 등록` 옵션이 추가 노출(전역 — §9 Q4의
    2계층 구분)
  - 하단 [적용] 버튼 → 답변을 **`form_fill_answers` 구조화 필드로 POST**
    (`selected_db_ids` 동형 배관). 선택 요약을 사용자 메시지로 에코(이력 가독성 —
    기존 선례 재사용).
  - **번호·특수기호 타이핑, 복사/붙여넣기 전무. LLM 답변 파싱도 전무**(구조화
    페이로드라 R6 리스크가 웹 UI 경로에서 소멸 — 존재성 검증만 수행).
- **텍스트 회신(폴백)**: API/CLI 등 패널 없는 클라이언트용으로만 자연어 답변 파싱
  경로 유지(structured output + 존재성 검증).

### Q3. 확인 이력 저장 처리방식 · 롤백 (v2.1 상술)

- **저장 기본값 = 옵트인**: `기억` 체크박스를 명시 선택한 답변만 영속화. C3 쓰기
  게이트 보수 유지. (자동 저장은 일회성 의도 고착 위험으로 비채택.)
- **저장 구조**: Redis `formfill:history:{양식 시그니처}` Hash — 항목 값에
  `{source, value, confirmed_at, prev}` 보존. `prev`(직전 확인값)로 1단계 즉시 복원
  가능. 로컬 YAML 폴백은 유사어 저장 구조 동형.
- **가시성(오염 검지의 전제)**: 이력이 적용될 때마다 응답에
  `[확인 이력 적용] 도입일자=공란(2026-07-30 확인)` 형식으로 매 런 노출 — 잘못
  저장된 값은 다음 런에서 반드시 보인다(침묵 적용 금지).
- **되돌리기 3경로**:
  1. **대화 명령** — "도입일자 기억 삭제해줘 / 다시 물어봐" → 기존
     cache_management 태스크 계열의 결정적 처리(intent_planner 계층 A 선례)로
     항목 삭제. 삭제된 필드는 다음 런에 역질문 부활.
  2. **즉시 지시로 갱신** — 잘못 채워진 걸 본 사용자가 "도입일자는 X로" 지시 →
     존재성 검증 통과 시 이력 갱신(최신 승리, `prev` 보존 → "이전 값으로 되돌려줘"
     지원).
  3. **운영자 직접** — RedisInsight GUI로 키 조회·삭제(폐쇄망 가용 실측 완료).
- **적용 시 재검증**: 저장 시 검증과 별개로 매 적용 시 존재성 재검증(FIX-15 게이트)
  — 스키마 변경 등으로 무효해진 이력은 자동 무시 + 사유 노출 + 역질문 부활
  (오염 이력의 자동 무해화).

### Q4. 유사어 활용 (v2.2 재정의 — 일괄 시드 배치 철회, 실효성 점검 + HITL 등록 제안으로)

- **실측(2026-07-30)**: 문맥 무관 필드 유사어는 `config/synonym_seeds/polestar.yaml`
  에 **대부분 이미 등록돼 있다** — IP주소→ipaddress, 서버명/서버 이름→name(등록명 —
  확정 규칙과 정합), 호스트명→hostname, CPU 코어 수→EAV:LOGICALCORE, 메모리
  용량→EAV:TotalSize, 제조사→EAV:Vendor, 모델명→EAV:Model, OS/운영체제→
  EAV:OSType, OS버전→EAV:OSVerson. 사용자 지적("이미 정의돼 있지 않나") 그대로.
  또한 시드 파일은 **생성물(직접 편집 금지)** — 단일 출처는 semantic model의
  aliases이며 `scripts/synonym_seeds.py derive → load`(합집합 병합)로 반영한다.
  폴스타 3개 DB는 동일 솔루션이므로 공용 원천 1회 추가로 계열 전체에 파생된다.
- **따라서 Q4를 재정의**:
  1. **기존 등록 실효성 점검(Phase 1)**: 등록돼 있는데도 단순 양식이 LLM 경로로
     떨어진 원인 규명 — 유사어 조회 정규화가 "IP 주소"(공백형)↔"IP주소"를 커버하는지,
     CM DB들의 Redis 시드 로드·`_structure_meta` 상태 확인. 신규 등록이 아니라
     **있는 지식이 폼필에 실제로 작동하는지**의 문제일 가능성이 높다.
  2. **런타임 HITL 유사어 등록 제안(Phase 2, 사용자 제안 채택)**: 역질문/지시로
     필드의 확정 대상이 **순수 칼럼·EAV 속성**(문맥 무관 후보)일 때, 패널에 선택
     옵션으로 제시 — "이 필드의 대상: cmm_resource.ipaddress. **기존 등록 유사어**:
     IP, IPaddress, IP주소. 새 표현 'IP 주소'를 영구 등록할까요?" 기존 등록 현황을
     보여주고 사용자가 승인한 것만 등록. 기존 `pending_synonym_registrations`
     멀티턴 등록 흐름(field_mapper에 기구현)을 재사용한다.
- **기억의 2계층 구분(패널에서 명시)**: `이 답을 기억`(체크박스) = **양식 시그니처
  스코프 확인 이력**(해당 양식에만). `유사어 등록`(별도 옵션, 문맥 무관 후보에만
  노출) = **전역**(폴스타 계열 전 질의). 문맥 의존어(`처리능력`·`용도`·`구분`·
  `비고`·`도입일자`·`서버명`)는 유사어 등록 옵션을 **표시하지 않는다**(결정적
  차단 목록) — 전역 오염 원천 봉쇄(D-145, 4차 라운드 교훈).

---

## 10. 구현 이력

### Phase 1 — 구현 완료 (2026-07-30, D-146/D-147 등재)

**수정 파일 3개(src) + 테스트 3개**:

| 파일 | 변경 |
|---|---|
| `src/nodes/query_generator.py` | `_try_build_form_fill_pivot_sql` — ①`form_intent = bool(template_structure)` 게이트 추가(월시리즈·자식EAV 없어도 조립, eav_pattern 필수 유지) ②llm_inferred 매핑 채움 제외(값 None화 + `mapping_updates` 강제 None, 집계어 명시 사용률은 metric_fields로 회수) ③식별 컬럼 주입 조건에 form_intent 추가 |
| `src/nodes/multi_db_executor.py` | `_generate_sql`에 `form_intent`/`mapping_sources` 파라미터 신설(호출부 2곳 전달) — ①시맨틱 컴파일 게이트에 `not form_intent` ②진입 조건에 `or form_intent` ③llm_inferred 강등(form_intent+eav_pattern+비재시도 한정, form_fill_out.mapping_updates 강제 None) ④피벗 게이트 `(child_eav or month_series or (form_intent and eav_pattern))` ⑤강등 사용률류 pivot_metric_fields 회수 ⑥식별 컬럼 주입 대칭 |
| `src/orchestration/intent_planner.py` | ③.5 template_structure → data_query 단일 task(LLM 분해 우회), ③.6 양식 명사+채움 동사 & 파일 부재 → general_inference 안내 단락 |
| `tests/test_nodes/test_form_month_series.py` | `TestD116FormIntentGate`(단일·멀티 게이트 매트릭스, 단순 양식 파서→조립 e2e, 식별 주입) + `TestLlmInferredDemotion`(강등·회수·비폼필 무영향·멀티 대칭) 신규 12건. `test_non_form_query_unchanged`는 D-146 계약으로 갱신(파일 없는 일반 질의만 None) |
| `tests/test_orchestration/test_intent_planner.py` | ③.5/③.6 pre-check 3건(단일 고정·안내 단락·명사 단독 미발동) |
| `tests/test_generic_path/test_generic_llm_mapping.py` | (부수) `read_text(encoding="utf-8")` — Windows cp949 기본 인코딩이 UTF-8 JSON 컬렉션을 중단시키던 기존재 결함 수정 |

**검증**: 폼필·intent_planner 테스트 68건 통과(신규 15건 포함), arch_check 위반 0
(기존 WARN 1건 유지). 전체 회귀 2,849 통과 / 실패·에러는 전부 기존재 — 의심 subset
(query_generator_mapping·generic_path·pipeline·overfit_check 등 14개 모듈)을
`git worktree add <dir> HEAD` 클린 사본과 동일 명령으로 대조, **실패 목록 완전 일치
(신규 실패 0)**. 잔여 실패는 환경 의존(e2e 실DB/Redis, API 키, 인코딩 계열).
집계어 없는 bare "사용률"은 의도적으로 회수하지 않음(평균/피크 모호 → 공란+역질문
후보 — `classify_metric_field` 보수 계약 유지). C2(기관명 하드코딩) grep 통과.

**신규 로그 라인(폐쇄망 판별용)**:
- `폼필 결정적 계약 경로(D-146): 월시리즈·자식EAV 없음 — 게이트 확장으로 조립` (단일)
- `DB '<id>': 폼필 결정적 계약 경로(D-146) — …` (멀티, DB별)
- `폼필 llm_inferred 매핑 N건 채움 제외(D-146, 역질문 후보): [...]` (단일/멀티)
- `intent_planner: template_structure 감지, data_query 단일 task (폼필 고정, D-147)`
- `intent_planner: 파일 없는 폼필 요청 감지 — 안내 응답 단락(D-147)`

**유사어 실효성 점검(Q4-1) 코드 측 결과**: 유사어 매칭은 공백 양방향 정규화 기구현
(`src/document/field_mapper.py` — `word_norm.replace(" ","") == field_no_space`,
"IP 주소"↔"IP주소" 커버). 단순 양식이 CM DB들에서 LLM 경로로 떨어진 원인은 코드
정규화가 아니라 **폐쇄망 상태**(CM DB Redis 유사어 시드 로드 여부, 스키마 캐시
`_structure_meta.known_attributes` 상세 여부)일 가능성이 높음 — 게이트 1 실측 시
RedisInsight로 함께 확인(§5 Phase 1 게이트).

### Phase 1 게이트 1 — 1차 라이브 실측 (2026-07-30) 및 Phase 1.1 수정

**성과**: ①단순 양식 GROUP BY 에러 3존 소멸(결정적 조립 확인 — B0는 5필드 전부
정상 채움) ②메모리 양식 3존 그린 유지 ③CM_YD CPU 정상 ④파일 없는 폼필의 ③.6
라우팅 발동 확인(작업 진행에 안내 지시문 노출).

**이슈와 판정**:

| # | 증상 | 판정 | 조치 |
|---|---|---|---|
| L1 | 단순 양식: CM 2개 DB가 server_name/hostname(주입 식별 칼럼)만 채움 — 양식 필드 미채움 | per-DB 매핑 기아 — CM의 유사어가 Redis에 없거나 llm_inferred뿐(D-146 강등). B0는 유사어 적중으로 전부 채움 | **폐쇄망 점검**: RedisInsight에서 CM 유사어 키 확인 → 없으면 `synonym_seeds.py load --db polestar_cm_gp/_cm_yd`. 코드 수정 아님 |
| L2 | 서버 양식 3존 data_insufficient + LLM 폴백이 계약(공란·규칙) 덮음(서버명=등록명, 비고=IP) | **구조 확정**: FIX-6 억제가 월 앵커 런에만 적용 — 월 시리즈 없는 폼필의 의도적 공란이 부족 판정→재시도→결정적 스킵→LLM 덮음 | **FIX-A**: result_organizer 억제를 폼필 턴 전체(template_structure+행 존재)로 확장 |
| L3 | 파일 없는 폼필 최종 응답이 "죄송합니다…" 일반 오류 | **코드 확정**: 안내 지시문을 general_inference LLM에 재서술시키다 LLM 호출 실패 → 오류 폴백(general_inference.py:241) | **FIX-B**: 고정 안내문을 task.direct_response로 결정적 반환(LLM 무통과) |
| L4 | B0 CPU 양식: "결과 생성 중 오류: 'NoneType' object has no attribute 'get'" | 발생 지점 특정 불가 — result_aggregator 래퍼가 exc_info 없이 메시지만 기록. 후보: output_generator의 None 불안전 `.get` 2곳(형태 일치, 미확정) | **FIX-C**: exc_info=True 추가(다음 라운드 traceback 확보) + 후보 2곳 or-폴백. **근본 확정은 재실측 traceback으로** |
| L5 | GP+YD CPU: 월 데이터는 채워지나 제조사(모델명)·비고 공란(이전 라운드는 정상) | **미확정** — 조립 로그(regular=/concat=/D-146/llm_inferred 제외 라인)와 실행 SQL 필요. 가설(검증 대기): 결정적 SQL 1차 검증 실패→error_context 재생성→LLM 폴백(월 리터럴 블록만 유지) | 로그 요청. 추정 가드 추가 안 함(진단 프로토콜) |
| L6 | 로그에 D-147 미검출 | 정상 가능성 — 업로드 턴은 ③(mapped_db_ids)이 선행 발동해 "mapped_db_ids 감지"로 기록됨. ③.6은 발동 실증(L3) | 재검증 시 "폼필 고정"·"안내 응답 단락" 문구로 검색 |

**Phase 1.1 수정 파일**: `src/nodes/result_organizer.py`(FIX-A),
`src/orchestration/intent_planner.py`+`src/orchestration/subagents.py`(FIX-B),
`src/orchestration/result_aggregator.py`+`src/nodes/output_generator.py`(FIX-C).
테스트 71건 통과(신규 3건: 폼필 턴 억제·direct_response LLM 미호출·None 안전),
인접 회귀 신규 0(기존재 3건 동일), arch_check 0.

### Phase 1 게이트 1 — 2차 라이브 실측 (2026-07-31): **전 시나리오 통과 → 게이트 1 통과**

| 시나리오 | 결과 |
|---|---|
| 서버 양식 3존 (FIX-A) | ✅ data_insufficient 소멸, 채움 적절(서버명=등록명·비고=ipaddress는 현행 계약 — Phase 2/3 대상) |
| 파일 없는 폼필 (FIX-B) | ✅ 고정 안내문 정상(LLM 미호출이라 토큰 스트리밍 없음 — 의도) |
| B0 CPU (L4 NoneType) | ✅ 정상 — FIX-C None 안전화가 원인이었던 것으로 방증(traceback 불요) |
| 단순 양식 3존 (L1) | ✅ CM 매핑 기아 해소(유사어 시드 로드 효과) |
| GP+YD CPU (L5) | ✅ 정상 — 제조사·비고 회귀 재현 안 됨 |

**잔여 관찰 1건(오염 의심 — Phase 2 착수 전 확인)**: 서버 양식에서 field_mapper
Pass 2가 `[동의어] 정확 매칭 확정(서브 테이블)`로 '비고'→IPAM_INFO.DESCRIPTION,
'구분'→PCM_SERVICE_LICENSE_INFO.CLOUD_TYPE 등을 확정 — 이 매핑들은 FIX-15가 조립
직전에 제외(entity 외 테이블 직접 칼럼)해 결과 미반영(정상 동작). 단 **비고가
ipaddress로 채워진 경로**는 별도 매핑이 SQL에 실렸다는 뜻 — Redis에 '비고'→
ipaddress류 학습 유사어가 등록돼 있는지(과거 LLM 등록 흐름의 오염 후보) 확인 필요.

---

## 11. Phase 2 구현 설계 (확정 — 2026-07-31 착수)

### 11.1 범위 확정 (Phase 2a)

- **포함**: 미해결 필드 수집 → 역질문 페이로드(구조화 패널) → `form_fill_answers`
  구조화 답변 수신 → 존재성 검증 → 오버라이드 적용 재실행(결정적). **LLM 파싱 0**
  (패널 경로는 구조화 페이로드라 파싱 자체가 없음).
- **제외(후속)**: 자연어 답변 파싱(API/CLI 폴백 — Phase 2b), `기억` 영속화(Phase 3
  확인 이력 — 패널에 체크박스 UI는 두되 Phase 2a에서는 미표시), 안내성 문구
  스트리밍 통일성 검토(백로그, low — 사용자 제기 2026-07-31).

### 11.2 데이터 흐름

```
[1차 폼필 런]
 query_generator/multi (D-146 경로)
   └─ form_fill_candidates 산출(schema_info: entity 안전 칼럼 + EAV known_attributes 한글 라벨)
 output_generator (fill_stats 계산 후)
   ├─ unresolved = 채움 0건 필드 − 월 시리즈 필드 − 사용자 지정 공란
   ├─ 응답에 form_fill_clarification {fields, candidates} 첨부 (D-140 응답 동형)
   └─ state.pending_form_fill = {uploaded_file, file_type, original_query, unresolved}
[프론트 패널]
 필드별 라디오: 공란 유지(기본) / DB 항목 선택(드롭다운=candidates) / 직접 입력(텍스트)
   └─ [적용] → POST form_fill_answers={field: {action, value}} + thread_id (선택 요약 에코)
[답변 턴]
 route: form_fill_answers 감지 → followup delta에 pending의 uploaded_file/file_type 복원
   └─ input_parser가 template 재파싱 → ③.5 단일 task → 결정적 경로
 query_generator/multi: resolve_form_fill_answers(검증) → 오버라이드 최우선 적용
   ├─ blank → 강제 None + 사유 "사용자 지정 공란"
   ├─ column/eav → 존재성 검증(FIX-15 동일 게이트) 통과분만 매핑 주입
   ├─ literal → SQL 제외, state.form_fill_literals → writer가 전 데이터 행 동일값 기입
   └─ 검증 탈락 → 사유 + 미해결 잔존(재역질문)
 output_generator: 적용/탈락 내역 응답 명시, 미해결 0이면 pending_form_fill 자기정리
```

### 11.3 상태 계약 (Known Mistakes — 요청 스코프 명시 관리)

| 키 | 수명 | 정리 지점 |
|---|---|---|
| `form_fill_answers` | 요청 스코프 | create_followup_input에서 매 턴 None(이번 턴 값만 route가 주입) |
| `form_fill_overrides` | 요청 스코프(검증 통과분) | 노드 산출 — 다음 턴 미승계(followup 초기화) |
| `form_fill_literals` | 요청 스코프 | 〃 |
| `form_fill_candidates` | 요청 스코프 | 〃 |
| `pending_form_fill` | **멀티턴 보존**(pending_synonym_registrations 동형) | ①답변 적용 후 미해결 0 ②새 파일 업로드 턴(교체) |

- 월 시리즈 필드·결합 필드는 답변 대상이 아님(검증에서 거부 — 구조 채움 영역).
- 오버라이드는 규칙(D-145 확정 규칙)보다 **우선**(사용자 층이 최상위 — §2 우선순위).

### 11.4 게이트 2 (재검증 기준)

①서버 양식 1차 런 응답에 미해결 필드 패널 표시 ②패널에서 "용도=직접 입력 X,
구분=공란 유지, 도입일자=DB 항목 선택" 후 적용 → 재채움 산출물에 반영 + 적용 내역
응답 명시 ③검증 불가 답변(실재하지 않는 칼럼) 거부+사유 ④답변 없는 일반 질의
턴에서 pending 오염 없음 ⑤기존 CPU/메모리/단순 양식 결과 불변.

### Phase 2 — 구현 완료 (2026-07-31, D-148 등재; 폐쇄망 게이트 2 실측 대기)

**수정 파일(배포 대상 10개)**:

| 파일 | 변경 |
|---|---|
| `src/state.py` | HITL 키 6종(form_fill_answers/overrides/literals/candidates/clarification + pending_form_fill) + followup 요청 스코프 초기화(pending은 멀티턴 보존) |
| `src/db_adapters/polestar/assembler.py` | `build_form_fill_candidates`(entity 칼럼+EAV 속성 후보, 스키마 실측) + `resolve_form_fill_answers`(존재성 검증, blank/column/eav/literal, 월 필드 보호, 탈락 사유) |
| `src/nodes/query_generator.py` | 답변 오버라이드 최우선 적용(규칙·결합·metric 회수보다 우선) + candidates/overrides/literals extra_return 승격 |
| `src/nodes/multi_db_executor.py` | 대칭 적용(form_fill_answers 파라미터, form_fill_out 승격) |
| `src/nodes/output_generator.py` | `_build_form_fill_hitl`(미해결 수집 — 전 필드 0건이면 미발동 D-050) + form_fill_clarification/pending_form_fill 반환 + 답변 적용/거부 내역 사유 + writer literal 전달 |
| `src/document/excel_writer.py` | `literal_values` — 해당 열 전 데이터 행 동일값 기입(fill_stats 반영, 수식 셀 보호) |
| `src/orchestration/subagents.py` | 폼필 산출물 res 승격(form_month_anchor 포함 — **기존 비대칭 결함 교정**: orchestration에서 기준월 안내가 미동작이었음) |
| `src/orchestration/result_aggregator.py` | `_build_output_state` 폼필 키 전달 + `_finalize_task`/단일 task 분기에서 clarification·pending 운반 |
| `src/api/schemas.py` + `src/api/routes/query.py` | QueryRequest.form_fill_answers 수신 → pending에서 원본 파일 복원 delta(D-064 초기화 계약 위에 복원), 응답 4곳(텍스트/파일 × JSON/SSE)에 form_fill_clarification 첨부 |
| `src/static/js/app.js` | `renderFormFillPanel`(필드별 공란/DB 항목 드롭다운/직접 입력 위젯, zone-clarify 스타일 재사용) → form_fill_answers 구조화 재전송 + 선택 요약 에코, 새 질의 시 자기 비활성 |

**검증**: HITL 테스트 11건 신규(검증기 분할/보호 필드/후보 스키마·화이트리스트/단일·멀티
오버라이드 대칭/writer 상수/미해결 수집/라우트 복원·무시) — 전부 통과. 폼필 전체 65건
그린, 인접 모듈(orchestration/document/api) 신규 실패 0(기존재 6건 동일), arch_check 0.

**배포 확인 grep**: `grep -rn "D-148" src/ | wc -l` (10파일 매칭),
`grep -n "form_fill_answers" src/api/schemas.py` (1건).

**게이트 2 재검증 시나리오(§11.4)**: ①서버 양식 업로드 → 응답 하단에 미해결 필드
패널 표시(로그: `폼필 역질문 발행(D-148)`) ②패널에서 "용도=직접 입력 '웹서버',
도입일자=DB 항목, 구분=공란 유지" 후 [선택한 방법으로 다시 채우기] → 재채움 산출물
반영 + 응답에 [사용자 답변 적용 내역] ③존재하지 않는 항목 답변 거부 사유 표시
④같은 thread에서 일반 질의 → 패널 비활성·오염 없음 ⑤CPU/메모리/단순 양식 결과 불변.

### Phase 2 게이트 2 — 1차 실측 (2026-07-31) 및 FIX-16

- **결과**: 패널 미표시(시나리오 ① 실패 → ②~④ 테스트 불가). 단 CPU-YD [기준월 안내]
  최초 표시 확인 — orchestration 전파 사슬(res 승격→aggregator→응답 텍스트)은 동작 실증.
- **원인(코드 확정, FIX-16)**: SSE `done` 이벤트가 response_data를 그대로 내보내지
  않고 필드를 골라 담는 별도 dict — `form_fill_clarification`을 response_data에만
  넣고 **done 이벤트 6곳(텍스트/파일 × astream 1차·ainvoke 폴백) 누락**. UI는 스트림
  경로라 페이로드가 프론트에 도달하지 못함. astream 1차 경로의 response_data 2곳도
  `output.get(...)` 기반 추가.
- **교훈**: 신규 응답 필드는 "response_data에 넣었다"가 아니라 **클라이언트가 실제로
  읽는 이벤트(done)까지 넣었는지**를 검증해야 한다 — done 이벤트가 필드를 골라 담는
  구조라 response_data 추가만으로는 전달되지 않는다.
- 재배포: `src/api/routes/query.py` 1개(+app.js 강력 새로고침 재확인).

### Phase 2 게이트 2 — 2차 실측 (2026-07-31) 및 FIX-17

- **진전**: FIX-16 반영으로 패널 표시·답변 전송·재실행 진입까지 동작(시나리오 ①~②
  전반). 사용자 답변: 도입일자→column CTIME, 적용 솔루션명→literal 'dbsafer',
  추가인증수단→literal '지문인증', 나머지 공란.
- **실패**: 답변 턴에서 field_mapper가 `priority_db_ids 공백` 경고 → 전 DB 유사어·
  설명이 LLM 프롬프트에 포함 → HTTP 413(FabriX 95K) 반복 → 출력 정지. YD 조회가
  B0를 배회.
- **원인(코드 확정, FIX-17)**: 답변 턴 파이프라인 입력이 패널 고정 문구라 원 질의의
  **위치 힌트(여의도) 유실** → priority 공백 → 프롬프트 폭증(b0 토큰폭증과 동일 계열).
  텍스트 경로라 폼필 LIMIT(100k) 미적용 결함도 동반.
- **수정**: 답변 턴 delta에 pending의 `original_query`를 user_query로 복원(1차 런과
  입력 동등 → 동일 라우팅·매핑 재현) + `resolved_limit=100_000` 복원. 에코 문구는
  messages(이력)에만 유지. 테스트 갱신(입력 동등성 단언 2건 추가), 82건 통과.
- 재배포: `src/api/routes/query.py` 1개. 확인 grep:
  `grep -n "FIX-17" src/api/routes/query.py` (1건) 또는
  `grep -c "form_fill_clarification" src/api/routes/query.py` = 12.

### Phase 2 게이트 2 — 3차 실측 (2026-07-31) 및 FIX-18

- **진전**: FIX-17 발동 로그 확인(`원본 파일·원 질의 복원`) — 413·B0 배회 해소 판단.
- **실패**: 답변 턴이 조기 타임아웃. **원인(코드 확정)**: 답변 턴은 텍스트 엔드포인트
  경유라 `query_timeout`(기본 60s)이 적용 — 폼필 재실행은 파일 런과 동일 부하
  (라이브 150~250s)인데 파일 런의 `file_query_timeout`보다 짧은 한도로 절단.
- **수정(FIX-18)**: 텍스트 엔드포인트 3곳(비스트리밍 ainvoke·SSE per-event fetch·
  SSE 폴백 ainvoke)에서 `form_fill_answers` 턴이면 `file_query_timeout` 적용.
- **관찰(설계 확인)**: `비고`가 패널에 없음 — 패널은 **채움 0건** 필드만 묻는데
  비고는 (의문의 ipaddress 매핑으로) 채워지고 있어 미해결이 아님. 잘못 채워진
  필드의 교정 수단은 현재 없음 → Phase 2b(질의 내 즉시 지시) 범위 + 비고→ipaddress
  매핑 출처 아티팩트 확보(생성 SQL의 `AS "비고"` SELECT 대상, per-DB 유사어 키).
- 재배포: `src/api/routes/query.py` 1개. 확인 grep: `grep -c "effective_timeout"
  src/api/routes/query.py` = 5.

### Phase 2 게이트 2 — 4차 실측 (2026-07-31) 및 FIX-19

- **증상**: 답변 턴이 완주(FIX-18 유효)하지만 답변이 반영되지 않고 동일 역질문 패널
  무한 반복. FIX-17 로그(원 질의 복원)는 정상 — 라우팅(여의도) 유지 확인.
- **원인(코드 확정)**: 오케스트레이션 격리 경계 `_make_isolated_input`(SubAgent S3)이
  **화이트리스트 선별 복사**인데 `form_fill_answers` 누락 — 라우트가 복원한 답변이
  task 파이프라인(s)에 도달하지 못해 오버라이드 미적용 → 1차 런과 동일 결과.
- **수정(FIX-19)**: 화이트리스트에 `form_fill_answers` 추가. 테스트 1건(경계 통과 단언).
- **교훈(docs/18 등재)**: 신규 state 키는 다층 경계(채널→followup→격리→res 승격→
  output state→응답 모델→done 이벤트→프론트) 전부가 선별 복사 구조 — **체크리스트
  전수 점검** 없이는 같은 유실이 반복된다(이번 라운드에서 3연속 실증).
- 재배포: `src/orchestration/subagents.py` 1개. 확인 grep:
  `grep -n "FIX-19" src/orchestration/subagents.py` (1건).

### Phase 2 게이트 2 — 5차 실측 (2026-07-31): **통과**

패널 입력 → [사용자 답변 적용 내역] 응답 표시 → **엑셀 반영 확인**(도입일자=DB 항목,
솔루션명·추가인증=직접 입력, 나머지 공란 유지). FIX-16~19 누적으로 HITL 사슬
전 구간(생성→done 이벤트→패널→구조화 재전송→pending 복원→격리 통과→검증→
오버라이드 적용→writer 상수→사유 노출) 라이브 실증. **Phase 2 완료.**

잔여(후속 Phase): ①Phase 3 확인 이력("이 답을 기억" 영속화·롤백 3경로) ②Phase 2b
질의 내 즉시 지시(잘못 채워진 필드 교정 — '비고'=ipaddress류) ③Phase 4 규칙 4종
페이드아웃 ④아티팩트 대기: 생성 SQL의 `AS "비고"` SELECT 대상(출처 확정용).

### §2.4 개정 (2026-07-31, 사용자 검토 반영): 확인 이력 = TTL 단기 캐시

- **오버피팅 재검토**: 메커니즘 자체는 계약(C1~C6) 안 — 양식별 코드 0줄, 쓰기 게이트=
  사용자 확인, 시그니처 스코프 격리. 단 **무기한 축적은 상태 부패·관리 부채**(양식
  개정 후 stale 이력, 미사용 누적, 운영자가 파악 못 하는 상태)를 만든다 — 사용자
  직감이 유효한 지점. "관리 대상이 아니라 사용 이력의 캐시"라는 원래 주장이 참이
  되려면 캐시답게 **만료**가 있어야 한다.
- **영속화 실체 확정**: 파일 저장 아님. **Redis 키 + TTL(EXPIRE)** 단독 — 로컬 YAML
  폴백 철회(자체 만료 로직이 필요해져 복잡도만 증가. Redis 유실 시 복구 비용=패널
  재답변 1회라 수용). 기존 스키마 캐시·유사어와 동일 인프라, 신규 없음.
- **TTL 정책**: 기본 **7일 sliding**(적용 시 TTL 리셋 — 사용이 곧 재확인). `.env`
  설정값(form_fill_memory_ttl_days)으로 조정, 0=기능 OFF. 비대칭 근거: 만료 비용
  (재질문 1회 ≈ 1분) ≪ 부패 지속 비용(감사자료 오기재). 주간 반복 업무는 sliding으로
  유지되고, 분기 주기(금감원류)는 만료 후 1회 재답변 — 수용.
- **범위 축소(오버피팅 우려 정합)**: Phase 3 = 층1(자동 노출·저장 확인) + 층2(파일
  첨부 조회) + 개별/양식 단위 삭제 + 도움말. 층3(전역 목록·번호 조작)·사용자 별명은
  후순위 보류(TTL 단기 캐시에 과한 관리 UI). last_used 하우스키핑·운영 정리 경로는
  TTL이 대체(구현 단순화).

---

## 12. Phase 3 구현 이력 (2026-07-31 — 확인 이력 = TTL 단기 캐시)

### 구현 내용 (§2.4 개정 반영)

- **저장소**: Redis 단독 `formfill:memory:{signature}` + **TTL sliding**(기본 7일,
  `QUERY_FORM_MEMORY_TTL_DAYS`, 0=기능 OFF). 파일 폴백 없음. 적용 시 TTL 연장 +
  use_count 증가("사용이 곧 재확인"). 저장 형식: display_name(시트 제목 우선)·
  original_query·created_at·use_count·fields{field: {action, value, confirmed_at}}.
- **시그니처**: `form_signature`(schema_utils) — 헤더 필드 집합만(NFC+공백 제거+
  소문자) 정렬 해시. 값 선입력·띄어쓰기 교정·파일명 변경에 불변.
- **적용**: 단일(query_generator)·멀티(multi_db_executor) 대칭 — 이력을 답변 형식
  (origin="memory")으로 로드해 이번 턴 답변 **아래**에 병합(`{**memory, **answers}`
  — 사용자 층 최상위 유지). 존재성 검증은 매 적용 시 재수행(스키마 변경 시 자동
  무해화 — 거부 사유 노출).
- **저장 게이트(C3)**: 패널 "이 답을 기억" 체크(옵트인) → `form_fill_remember` →
  output_generator가 **적용된 answer-origin 오버라이드만** 저장(memory 재적용분·
  검증 탈락분·LLM 산출물 제외). 저장/실패 모두 응답 명시([기억 저장]/[기억 저장 안 됨]).
- **사유 분리**: [사용자 답변 적용 내역](origin=answer) vs [확인 이력 적용]
  (origin=memory, "변경: 필드명 기억 삭제" 안내 병기).
- **조회·삭제(층2)**: intent_planner ③.45 — 파일 첨부 + "기억" 명사 + 조회/삭제
  동사 → 결정적 direct_response(LLM 미호출, ③.5보다 선행). 필드 특정=질의 내 필드명
  결정적 매칭(특정 실패 시 삭제하지 않고 현황 안내 — 침묵 오삭제 방지), 전체 삭제는
  "전부/전체/모두" 명시 필요 + 삭제 내용 전문 표시(저비용 복구).
- **도움말**: general_inference [지원 가능한 조회 유형]에 양식 채우기·기억 관리 등재.

### 배포 파일 (14개)

config.py / utils/schema_utils.py / schema_cache/redis_cache.py /
**schema_cache/form_memory.py(신규)** / db_adapters/polestar/assembler.py /
nodes/query_generator.py / nodes/multi_db_executor.py / nodes/output_generator.py /
nodes/general_inference.py / orchestration/intent_planner.py /
orchestration/result_aggregator.py / api/schemas.py / api/routes/query.py /
static/js/app.js / state.py

**배포 확인 grep**: `grep -rln "form_memory" src/ | wc -l`(9+),
`grep -n "form_fill_remember" src/api/schemas.py`(1건),
`grep -c "이 답을 기억" src/static/js/app.js`(1건).

### 검증

폼필·intent_planner 테스트 98건 통과(신규 16건: 시그니처 불변성 3, 저장소 계약 4
— save/load/touch·병합·삭제·TTL 0 OFF, 사유 분리·저장 게이트 3, 조회/삭제 라우팅 5,
경계 1). arch_check 0. form_fill_remember 경계 체크리스트 전수 점검(docs/18 방지책
적용 — 채널·초기화 2곳·_build_output_state·프론트; 격리/res 승격/done은 비대상 확인).

### 게이트 3 재검증 시나리오

①서버 양식 + 패널 답변 + **"이 답을 기억" 체크** → 응답에 `[기억 저장] '…'에 N개
항목…(유효 7일)` (로그: `폼필 확인 이력 저장(D-148)`) ②**같은 양식 재업로드** →
패널 없이(또는 축소되어) 자동 반영 + `[확인 이력 적용]` 표시 (로그: `폼필 확인
이력 적용(D-148) … TTL 7일 연장`) ③파일 첨부 + "이 양식에 기억된 답 보여줘" →
목록 표시(채우기 미실행) ④"도입일자 기억 삭제" → 삭제 확인 + 다음 런에 도입일자
재질문 ⑤기억 없이(체크 안 함) 답변 → 다음 런에 다시 질문(저장 안 됨 확인)
⑥CPU/메모리 양식 결과 불변.

### Phase 3 게이트 3 — 1차 실측 (2026-08-03) 및 FIX-20

- **통과**: ①기억 저장 ②같은 양식 재업로드 자동 반영 ⑥TTL sliding(3일 전 저장분
  재사용 시 TTL 초기화 실측 — sliding 계약 라이브 검증).
- **실패**: ③이력 조회("이 양식에 기억된 답 보여줘")가 **파일 경로 존 역질문**
  ("대상 존을 선택해 주세요")에 가로채임 — 이력 조회·삭제는 DB 조회가 없어 존이
  불필요한데 pre-flight가 그래프(③.45) 앞에서 발동(④⑤는 ③ 의존으로 미검).
- **수정(FIX-20)**: 판정 단일 출처 `is_form_memory_command`(intent_planner — ③.45
  게이트와 동일 키워드)를 `_file_zone_clarification_or_none`이 선행 확인해 기억
  명령이면 존 역질문 스킵. 테스트 1건(스킵/비스킵 양방향).
- 재배포: `src/api/routes/query.py` + `src/orchestration/intent_planner.py` 2개.
  확인 grep: `grep -n "is_form_memory_command" src/api/routes/query.py` (1건).

### FIX-20 사이드이펙트 분석 (2026-08-03, 사용자 요청)

| # | 검토 항목 | 판정 |
|---|---|---|
| S1 | **'(주)기억장치' 오매칭** — 메모리 관용 명사(인식기 문맥 명사)가 '기억'+'보여'에 부분 매칭 → 정상 폼필("주기억장치 사용현황 보여줘")이 존 역질문 스킵+③.45 이력 조회로 오탈취 | **실제 결함 — 즉시 교정**: `_memory_query_normalized`("기억장치" 제거 후 매칭)를 판정·③.45 양쪽(단일 출처)에 적용, 테스트 2건 |
| S2 | 복합 명령("기억 삭제하고 다시 채워줘") — 이력 처리만 수행되고 채우기는 미실행 | 수용(문서화): ③.45가 ③.5보다 선행하는 설계 자체의 성질(FIX-20 무관). 채우기는 다음 턴 — direct_response가 후속 행동 안내 포함 |
| S3 | 이력 명령이 키워드에 안 걸리는 표현("기억한 거 뭐야") | 과소 매칭은 안전측 — 존 역질문이 기존대로 발동(불필요한 질문 1회 UX 저하일 뿐, 존 선택 후 ③.45가 처리) |
| S4 | 텍스트 경로(파일 없는 이력 명령) | FIX-20 무관(파일 경로 함수만 수정). 파일 없는 이력 조회는 층3 보류 상태의 기존 공백 |
| S5 | 손상 파일 + 이력 명령(template 파싱 실패 → ③.45 미발동 → LLM 분해 배회) | 기존에도 존 역질문 후 같은 경로 — FIX-20이 악화시키지 않음. 희귀 케이스, 미조치 |
| S6 | 존 역질문 기존 계약(Plan 70 12종) | 무영향 — 이력 키워드 없는 질의는 조기 반환 미발동. test_zone_selection 전체 통과 재확인 |
| S7 | 계층 방향(api→orchestration import) | 허용 방향(orchestration이 안쪽), arch_check 0 |

### Phase 3 게이트 3 — 2차 실측 (2026-08-03) 및 FIX-21

- **실패**: "기억된 답 보여줘/저장된 값을 알려줘"가 이력 조회로 가지 못하고 **B0
  채움/조회를 시도**(1번 런은 그 과정에서 FabriX security filter API 오류 동반 —
  잘못된 경로에서 발생한 부수 증상, FIX-21로 경로 자체가 사라짐. 정상 채움에서
  재발하면 별도 추적).
- **원인(코드 확정)**: ③.45를 ③(mapped_db_ids) **뒤에** 배치 — 업로드 턴은
  field_mapper가 항상 mapped_db_ids를 세팅하므로 ③이 data_query 단일 task로 조기
  반환, 이력 명령이 영영 도달 불가. 테스트가 못 잡은 이유 = 테스트 state에
  mapped_db_ids 부재(라이브 업로드 턴과 불일치).
- **수정(FIX-21)**: 이력 명령 판정을 ②.7로 이동(②.5 selected_db_ids·③보다 선행 —
  이력 명령은 DB 라우팅 자체가 불필요). 테스트 state에 mapped_db_ids+selected_db_ids
  선행 상태를 넣어 우선순위를 계약으로 고정(오케스트레이션 전체 317건 통과).
- 재배포: `src/orchestration/intent_planner.py` 1개.
  확인 grep: `grep -n "FIX-21" src/orchestration/intent_planner.py` (1건).

### Phase 3 게이트 3 — 3차 실측 (2026-08-03) 및 FIX-22

- **진전**: FIX-21로 이력 조회 경로 진입 성공(일반 안내 task 실행 확인).
- **실패**: **replanner(재계획)**가 direct_response 결과를 LLM 재평가에 넘겨 "기억된
  답 7건이 존재하나 실제 내용이 제공되지 않았다"고 오판 → 후속 DB 조회(B0) task
  생성 → 채우기 회귀. (기존 R-A4 가드는 general_inference 중복 재답변만 차단 —
  data_query 후속은 통과.)
- **수정(FIX-22)**: 전 task가 결정적 direct_response면 replanner가 LLM 재평가 없이
  종료(최종 응답 계약 — 파일 없는 안내 ③.6도 동일 혜택, LLM 호출 1회 절약).
  테스트 1건(재평가 미호출 단언), 오케스트레이션 318건 통과.
- 재배포: `src/orchestration/replanner.py` 1개.
  확인 grep: `grep -n "FIX-22" src/orchestration/replanner.py` (1건).

### Phase 3 게이트 3 — 4차 실측 (2026-08-03) 및 FIX-23

- **관찰**: ①조회는 동작(말투 매뉴얼체 지적 → 존댓말 문장형으로 개선) ②"구분 기억
  삭제"가 성공 안내를 냈으나 **Redis 항목 잔존**(사용자 RedisInsight 실측) ③재조회
  시 '구분' 재표시 ④채우기에서 재적용.
- **원인(정황 확정)**: 삭제 턴은 **파일 재첨부 없이** 전송됨(1·3번만 "양식 업로드
  후") — template 부재로 ②.7 미발동 → LLM 분해 → general_inference가 "삭제했다"고
  **환각 성공 안내**. 실제 삭제는 실행된 적 없음. 최악의 실패 형태(침묵 no-op +
  거짓 확인).
- **수정(FIX-23)**: ①`last_form_signature`(멀티턴 보존 state) — 모든 양식 턴(②.5/③/
  ③.5/②.7)이 시그니처를 보존해, 파일 없는 "기억 보여줘/삭제"가 **직전 양식을
  결정적으로 특정** ②시그니처도 없으면 LLM으로 새지 않고 결정적 안내(환각 차단)
  ③form_memory에 signature 직접 지정 로드/삭제 ④필드 삭제 저장-백 실패를 성공으로
  보고하지 않음(반환값 검사) ⑤조회 응답 존댓말 개선.
- 테스트 5건 추가(무파일 삭제=직전 시그니처 실행·컨텍스트 유지, 무시그니처=안내,
  채우기 턴 시그니처 보존, 저장-백 실패=0건 보고, 문구), 368건 통과·arch_check 0.
- 재배포 3개: `src/orchestration/intent_planner.py`, `src/schema_cache/form_memory.py`,
  `src/state.py`. 확인 grep: `grep -n "last_form_signature" src/state.py` (2건+).

### Phase 3 게이트 3 — 5차 실측 (2026-08-03): **재검증 시나리오 전체 정상** + FIX-24

- **게이트 3 핵심 시나리오 전부 통과**(저장·자동 재사용·TTL sliding·조회·삭제·
  무저장 재질문 — 사용자 확인). 잔여 관찰 2건:
- **FIX-24(낭비 실행)**: 이력 조회 턴 로그에 field_mapper의 전 DB 유사어 LLM 발견
  413 재시도 — 그래프 순서상 field_mapper가 intent_planner(②.7 단락)보다 먼저 전체
  매핑을 수행하는데 그 산출물은 전부 버려짐 + 이력 질의는 위치어가 없어 priority
  공백→413. 수정: 판정 함수를 utils.query_gen_common으로 이동(단일 출처, 계층
  정방향)하고 field_mapper가 이력 명령 턴이면 매핑 스킵(LLM 미호출). 최종 동작은
  이전에도 정상이었고(단락 로그 확인) 이번 수정은 지연·토큰 낭비 제거.
- **비고 자동 채움(미해결 지속)**: 출처 미확정 매핑이 SQL에 실림 — 아티팩트(생성
  SQL의 `AS "비고"` SELECT 대상) 확보 시 데이터 정리로 즉시 중단 가능(비고가
  미해결로 떨어져 패널 관리 대상化). 구조 해법은 Phase 2b(질의 내 즉시 지시).
- 재배포 3개: `src/utils/query_gen_common.py`, `src/orchestration/intent_planner.py`,
  `src/nodes/field_mapper.py`. 확인 grep: `grep -n "FIX-24" src/nodes/field_mapper.py`.

### '비고' 자동 채움 근본 원인 확정 및 교정 (2026-08-03, FIX-25)

- **아티팩트(사용자)**: 서버 양식 런의 SQL에 `AS "비고"` 없음 + CSV 4칼럼뿐(서버명·
  IP·OS(버전정보)·제조사(모델명)) — 그런데 Excel 비고는 IP값으로 채워짐.
- **재현·확정(로컬)**: writer `_get_value_from_row` 5단계(부분 문자열 폴백)가 행 키
  "IP"(2글자)를 매핑 문자열에 포함 매칭 — 'descr**ip**tion'·'**ip**address'·
  'IPAM_INFO.DESCR**IP**TION' 전부 적중. 비고의 낡은 매핑이 무엇이든(FIX-15가 SQL
  에서 제외해도 state 매핑 잔존) IP 값이 채워짐 — 존·런 무관 재현되던 이유.
- **교정(FIX-25, 2겹)**: ①불변식 — FIX-15가 SQL에서 제외한 필드는 state 매핑도
  강제 None(월/결합/llm_inferred와 동일 패턴; 단일/멀티 대칭) → 비고가 공란+미해결로
  떨어져 **패널 관리 대상**이 됨(사용자 요구 "칼럼으로써 관리" 충족: 패널에서
  공란/직접 입력/DB 항목+기억 지정 가능) ②방어 — writer 부분 매칭에 최소 길이 3
  가드(2글자 키 오매칭 원천 차단; 정상 IP 채움은 4단계 역매핑으로 성립함을 테스트로
  고정).
- 테스트 3건(오매칭 후보 전수 차단·정상 IP 역매핑 생존·제외 필드 매핑 None 불변식),
  461건 통과(document 계열 회귀 0).
- 재배포 3개: `src/document/excel_writer.py`, `src/nodes/query_generator.py`,
  `src/nodes/multi_db_executor.py`. 확인 grep: `grep -n "FIX-25"
  src/document/excel_writer.py` (1건).

---

## 13. Plan 73 종결 (2026-08-03, D-149)

### B1~B3 최종 처리 (사용자 승인)

- **B1(Phase 4) 축소 확정**: 이관·제거 **철회** — 규칙 4종을 도메인 일반 규칙로
  재분류(D-149). 근거: ①TTL 캐시는 부정 규칙(처리능력 강제 공란)의 이관처 불가
  (만료=보호 소실) ②4종 모두 기관·양식 리터럴이 아닌 필드명 의미론 ③실질 목표
  (새 양식=코드 0줄)는 게이트 3에서 실증 완료. 동결 계약(C1)은 유지.
- **B2(질의 내 즉시 지시)**: 보류 — 수요 실증 시 착수(비고 건 해소로 긴급성 소멸,
  대체 경로 = 기억 삭제→패널 재지정 2턴).
- **B3(자연어 답변 폴백)**: 보류 유지(웹 UI 단일 운영).

### 최종 상태 요약

- **구현 완료·라이브 검증**: D-146(경로 결정화+llm_inferred 채움 금지),
  D-147(단일 task+파일 없는 안내), D-148(HITL 역질문+구조화 답변+TTL 확인 이력+
  조회/삭제), 안정화 FIX-16~25.
- **우려 대조**: "양식마다 코드·에러 증가" → 역전(새 양식 코드 0줄 실증, 에러→계약된
  공란/질문, 지식은 사용자 확인 기반 자동 축적·자동 만료). DeepAgent 회귀 의사결정
  게이트의 판정 재료 확보(§7).
- **잔존 백로그**(Plan 73 밖):
  1. **field_mapper의 LLM 매핑 즉시 Redis 등록 경로** — 오염 자기강화 잔여
     (비고→description류의 원 출처 추정). 폼필은 D-146/FIX-25로 무해화됐으나 일반
     질의 영향은 별도 검토 대상.
  2. 안내성 문구 스트리밍 통일성(low).
  3. FabriX security filter 오류 — 정상 런 재발 시 추적.
  4. 서브 테이블 유사어 정리(선택), Word/3단 헤더(비범위 유지), CM_YD ">" 라우팅
     (재현 불가 종결 보류).
