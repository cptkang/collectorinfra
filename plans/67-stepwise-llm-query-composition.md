# 67. 단계적 LLM 쿼리 조립 + 경직성 해소 리팩토링 — deep agents vs semantic routing 비교 검토

> 작성일: 2026-07-29
> **성격**: 검토(framework 비교) + 리팩토링 계획. 요청 취지 — "사용자 프롬프트를 분석해 추출할 컬럼들을 하나씩 요청 내용과 관련 스키마로 LLM을 통해 도출하여 전체 쿼리를 만들어가는 방식으로 쿼리 생성 단계에서 LLM 역할을 확대. 기반 프레임워크는 deep agents와 현행 semantic routing 방식을 비교 검토. 현 구현의 경직성 분석과 deep agent tools 재활용 가능성 포함."
> **대상 기능**: `src/nodes/query_generator.py`(1062L), `src/nodes/semantic_compiler.py`(802L), `src/nodes/schema_analyzer.py`(1296L), `src/orchestration/{deep_agent,deepagents_tools}.py`, `config/{db_profiles,semantic_models}/*.yaml`, `src/db_adapters/polestar/{prompts,assembler}.py`, `src/routing/domain_config.py`, 위치 키워드 소비처 6곳(§1.2-c)
> **선행/근거 문서**: `docs/deterministic_sql_composition_review.md`(컬럼 조합 기법 근거), `docs/text2sql_quality_research.md`(분해식 생성 §2.1·복잡도 분기 R6), `plans/61-text2sql-candidate-selection.md`(트랙 C·E1 실측), `plans/48·49`(deepagents 오케스트레이션), `plans/63-polestar-overfit-decoupling.md`(3계층 원칙)
> **관련 결정**: D-076(SMQ 결정적 조합 — 본 계획의 확장 대상), D-067·D-099(이중 조립 엔진 금지·기존 컴파일러로 편입), D-037(deepagents 오케스트레이션 — semantic_router 삭제 금지), D-035(결정적=판단·LLM=보조), D-004(키워드 라우팅 재도입 금지), D-088~D-091(공용 계층 DB-agnostic·어댑터 레지스트리), D-066(단일/멀티 경로 대칭), D-092~D-095·D-100(deepagents 운영 결함 가드), D-127(과금 API 건별 승인)
> **신규 결정**: **D-130 등재 완료**(HITL 승인 fail-closed — Phase 0-3 ⑧, 2026-07-29). 예약(착수 시 등재): **D-128**(단계적 컬럼 도출 루프 — 트랙 S) · **D-131**(SQL 지식 정본 일원화 + DB 레지스트리 — 트랙 R) · **D-132**(alarm 주석 분류 LLM 전환 — D-035 domain 경계 예외) · **D-133**(표현·명칭 표준화 트랙 N). ※ v7까지의 예약(R=D-129·alarm=D-130·N=D-131)은 **Plan 68의 D-129 등재로 순차 재부여**(2026-07-29 실측 — 안내 라인 `docs/02_decision.md` 갱신 완료). ※ 채번 주의: `docs/02_decision.md:8`의 안내 라인("최대 D-118")은 **stale** — `grep -n "^## D-"` 실측 최댓값 **D-127** 기준. 등재 시 안내 라인도 함께 갱신할 것. 결번(D-052, D-078~081 등)·예약(D-105, D-115) 재사용 금지, 등재 직전 재확인.
> **상태**: **전 트랙 구현 + E1 A/B 평가 완료(2026-08-05, v16)** — E1 판정: 공통 채점 10건 EX **동률 7/10**(stepwise 승 1·패 1) → **stepwise 기본 OFF 유지**(ON 전환 근거 없음, 승 패턴인 기간 필터 성능 통계형은 S-IR 확장의 실효 근거로 보존). 부수 성과: 실 Gemini(thinking 계열) content 블록 리스트로 **라이브 경로 전멸이던 잠복 결함을 공용 유틸+직접 소비처 전수 교정**(bd29707·b04c5dd). 잔여 후속: v15 후속 등재 목록 + gp 픽스처 골드 3건(gp-007/008/011) 데이터 보강.
> **개정 이력**:
> - v1(2026-07-29) — 최초 작성. 단계적 컬럼 도출 루프 중심, 경직성 해소는 §3.2 병행 항목으로만 언급(①지식 복제는 semantic_models 자동 생성만, ②신규 DB 편입 축소 미포함).
> - v2(2026-07-29) — **사용자 지시로 경직성 해소를 정식 트랙 R로 승격**: ①지식 4중 복제 정본 일원화(904L 프롬프트·db_profiles 포함), ②신규 DB 편입 9곳+→≤2곳 축소 + 위치 키워드 튜플 6곳 사본 단일화, ③한글 정규식 이관, ④가드 계측·축소를 명시 범위·성공 기준으로 편입. 계획을 트랙 S(단계적 도출)·트랙 R(경직성 해소) 2트랙으로 재구성, D-129 예약 추가.
> - v3(2026-07-29) — **정규식 전수 검토(`docs/regex_llm_conversion_review.md`, 210곳 실측) 반영으로 R3 정정**: "전면 LLM 이관" → "선별 전환 + 2단 폴백". LLM 전환 정당 지점은 3곳뿐(시트명·유사어 등록 의사·HITL 승인 의사), 기간·건수(A1~A6)는 정규식 우선 + **폐기되던 LLM 산출물(`time_range`/`limit`) 폴백**(신규 LLM 호출 0건), A7~A9 SMQ 교정 정규식은 "LLM 시도→실패→정규식 복귀" 실측 이력이 있어 R4 계측 대상으로만. 키워드 substring 리스트 8곳을 R3 범위에 편입. Phase 0 결함 목록에 HITL 승인 fail-open 등 2건 추가.
> - v4(2026-07-29) — **사용자 인터뷰 결과 반영(§0.3 게이트 갱신)**: ②Gemini 스모크 **보류**(Phase 0 착수 직전 재문의), ③EX 검증은 **DB 적재 선행** 확정(적재는 사용자 측 준비), ④`annotation_signal.py` **전면 LLM 전환** 결정(D-035 domain 경계 변경 — D-130 예약, R3-(v) 편입). ①확장 vs 대체는 "성능 관점 분석 보고" 요청 → **§2.4 신설**(분석 결론: 확장 우위 — 측정된 성능 병목이 선택·판정 단계에 있고 컴파일 단계의 오류 기여는 0), 최종 결정 대기.
> - v5(2026-07-29) — **SMQ 유연성 분석(§2.5 신설, 사용자 요청)**: IR 표현력 한계 8가지 실측(count/sum 부재·GROUP BY 고정·필터 안전장 1개(코드-YAML 불일치)·order_by/limit IR 부재·op 제약·패턴 결합 불가·파생 불가·카탈로그 닫힘). 판정 = **방식은 적절(닫힌 IR+폴백 구조), 부족한 것은 IR 완성도** — 미커버 다수가 비정형이 아니라 "IR이 못 담는 정형 수요"(런타임 커버 34.6%의 구조 원인). **SMQ IR 확장 5건(S-IR1~5)을 트랙 S에 편입**, Phase S3-1 구체화. 확장 한계선(SQL 재발명 금지 가드) 명시.
> - v6(2026-07-29) — **표현·명칭 표준화 문헌 조사(`docs/standardization_literature_review.md`) + 사용자 인터뷰로 트랙 N 신설(D-131 예약)**: 채택 = **N2 질의 이력 검색**(성공 확정 질의-SQL 쌍 인덱싱 — 문헌 최대 효과 +40.2pt) + **N4 계층 taxonomy**(평면 동의어의 precision 붕괴 완화 — 핵심 용어부터 단계화). **미채택** = N1 임베딩 후보 생성기 재배치·N3 동의어 공급원 교체(추후 재검토 가능). 임베딩 모델은 **측정(IP-4) 선행 후 결정**(인터뷰 확정) — N2 초기 구현은 어휘·퍼지 검색으로 시작하고 임베딩 승격은 측정 후 판단. R1은 시맨틱 레이어 실증(+17~23%p, "선언 1곳 소비 N곳" 상용 5종 일치)으로 근거 보강.
> - v7(2026-07-29) — **폴스타 편향 검토(`docs/polestar_bias_review.md`) 반영 + 최종 인터뷰 4건 확정**: ①**확장 확정**(§0.3-1 게이트 해소 — §2.4·§2.5 분석 지지), ②편향 9건 **전부 편입**(스키마 리터럴 4건 R2 합류·운영 리터럴/mcp_server 게이트 4건 Phase 0 결함 ⑪~⑭·alarm domain 1건 R3-(v) 인접), ③**overfit_check 검사 범위 확대를 R2 완료 조건으로 편입**(사각지대 9건이 어떤 게이트에도 미검출이었음), ④**Phase 0 결함 수정부터 착수**(LLM 호출 없는 코드 작업 — Gemini 스모크·DB 적재는 별도).
> - v16(2026-08-05) — **E1 A/B 평가 완료(트랙 S 최종 성적표)**: 로컬 gp 샌드박스 15문항, 격리 worktree(7d4237e+파서 수정 2커밋), 모델 gemini-3.5-flash-lite(구 2.5-flash-lite 서버 퇴역 404로 인터뷰 교체), 설명 자동생성 OFF 동일 조건. **결과**: 공통 채점 10건 EX **7/10 동률** — stepwise 승 gp-009(지난달 기간 필터 성능 통계 — S2 라이브에서 확인한 pattern-B 시간 필터 갭의 개선 실증), 패 gp-002(서버 구성 나열형). 전체 EX baseline 70.0%(7/10) vs stepwise 63.6%(7/11), subset 70.0% vs 72.7%, 평균 지연 64.6s vs 38.2s. gp-014는 stepwise만 검증 소진 실패(HAVING 서버 식별 컬럼 가드), gp-015 baseline은 일시 ReadError로 우열 판정 불가. **SMQ 정확도(1/6 대비)·커버리지(34.6% 대비) 축은 orchestration 경로 하네스에서 smq_scored=0으로 미산출** — EX 축만으로 판정. **판정: stepwise 기본 OFF 유지**(EX 우위 없음 — D-128 "활성화는 E1 후" 조건 미충족). 미채점: gp-007/008/011(+baseline gp-014) 골드 0행(픽스처 리터럴 서버명 부재). **측정 중 실사고 2건**(known_mistakes 등재): ①`.env` 퇴역 모델(D-021 금지 계열 잔존)로 2.5h 실패 공회전 ②content 블록 리스트 — S2의 지점별 정규화가 비대칭이어서 공용 유틸 진입부(bd29707)+직접 소비처 4곳(b04c5dd, semantic_compiler `parse_smq_response` 사전 strip이 baseline 전건 SKIP 원인) 전수 교정으로 **실 Gemini 라이브 경로 복구**. 산출물: `eval_results/e1_20260804/`(미추적 보존), D-022 재검토 등재(005fe3f)·mcp 가드 제거는 f15ac46 기커밋 확인.
> - v15(2026-07-30) — **잔여 3웨이브(N4·D-132·프롬프트 잔여 블록) 완료 → Plan 67 코드 작업 전량 소진**: ①N4 taxonomy(parent 3건 — 사용률·코어·모델("모델"은 alias 규칙을 "형제 둘 이상 주장 시 미등록"으로 좁혀 결속 불변 확인 후 편입), 상위어 전체 제시 채택 — cov.reason 사용자 미도달 실측으로 재질의안 기각, 기본 OFF) ②D-132 등재(alarm 주석 LLM — application 계층·enum 소비·강등 폴백·기본 OFF, ON은 과금 별도 승인 + 라이브 벤치 1회 동반) ③프롬프트 16블록 마커화(13블록 정본 렌더 바이트 일치 실증 — Template B measure는 제외 지시가 실측 반증되어 유지 채택), hi 조인 키 교정+값 컬럼 조인 validator(플래그 뒤 — **즉시 적용은 프로필 3파일 few-shot 동반 교정+b0 실측 전제**). **후속 등재**: severity_map 정본 상위집합 확장(knowledge), `TEXT2SQL_PROMPT_KNOWLEDGE_RENDER`·`QUERY_INTENT_LLM_ASSIST`의 RELOADABLE_KEYS 판정(타 세션 소유 — HYPERNYM은 등재 완료), taxonomy의 LLM 프롬프트 노출(옵트인 별건). 중단 이력: 세 에이전트가 네트워크(ENOTFOUND)·인증(401)·과부하(529)로 3중 중단 후 디스크 실측 재개로 유실 0 완주. 설정 카탈로그 241 정합. **잔여 = 사용자 측 2건**(DB 적재→E1 평가, Redis 기동→질의 이력 적재) + plans/69 충돌 확인.
> - v14(2026-07-30) — **R3 웨이브 완료·D-136 등재 → Plan 67 구현분 전체 종결**: ①A1~A6 2단 폴백(신규 LLM 호출 0·완결 월 절단·발동 계측·단일/멀티/폼필 대칭 — **폼필 포함은 팀 리드 승인 근거의 실측 오류를 에이전트가 정정해 반전한 결정**, known mistakes 등재) ②LLM 보조 3곳 옵트인(시트명·등록 의사 재질의 전환·승인 의사 2중 키(코드 상수 0.8+승인 어휘 보강)+감사 로그 — 전부 결정적 폴백·D-130 fail-closed 불변) ③"전체(적으로)" 오탐 완화(경계 판정 단일 출처화) ④멀티 경로 배선 대칭 실측 테스트·subagents llm 실전달. `QUERY_INTENT_LLM_ASSIST` 기본 False — **ON 전환은 인터뷰 대기**. 채번: 타 세션의 D-134(plans/69 예약)·D-135(등재) 선점을 존중해 **D-136으로 재부여 등재**. 잔여 = E1 평가(DB 적재 후)·질의 이력 실 적재(Redis)·N4·D-132(alarm 주석 LLM)·프롬프트 잔여 블록·b0 EAV 예제 실측.
> - v13(2026-07-30) — **S3 코어 완료(S-IR1~5 + R4 계측 14종, 새 엔진 0)**: 골든 바이트 보존 + 신규 43건 + 기준선 대조 회귀 0. **커버리지 확장 즉시 활성 = 사용자 인터뷰 결정**(옵트인 관행의 예외 — 조립 형태는 골드 검증 패턴과 동일, EX는 DB 적재 후 실측. 종전 LLM 폴백이던 서버명·가용성 필터, 월별 분해, count/전역 집계, 알람 랭킹이 결정적 컴파일로 편입). 라이브 실증된 time 필터는 normalize에서 결정적 승격(프롬프트 의존 없음). 후속: agg 표기 field_validator 흡수(유효값 검증 유지). 부수 발견 처리: resource_type 필터 무시(카운터 가시화·현행 유지)·패턴 C def INNER 조인 건수 영향(EX 확인 항목). **R3 웨이브 착수(인터뷰)** — 2단 폴백·LLM 전환 3곳·오탐 완화. ※타 세션이 plans/69(쿼리 생성 구조 리팩토링) 작성 중 — 완료 후 중복·충돌 확인 필요.
> - v12(2026-07-30) — **stepwise ON 라이브 스모크 PASS(D-127 건별 승인) + 실 결함 1건 수정 + S3 착수(인터뷰)**: ①실 Gemini의 리스트 content로 파서 TypeError → 루프 전건 llm_error 폴백(목 미검출) — `_message_text` 정규화로 수정 후 재실행: **패턴 A 질의 전 구간 완주**(분해→도구 5회→SMQ 정확 선택→coverage inside→SQL 조립, 3라운드 12.9s). ②**IR 한계 라이브 재현**: 패턴 B에서 SMQ 선택은 정확(measures 4종+time_grain)했으나 LLM이 기간을 `time` 필터로 표현 → 미지원 필터 폴백 — §2.5 한계 4번(시간 표현 IR 부재)의 실증, **S3 입력**(기간 필터의 stat_month 승격 또는 프롬프트 지시). ③서버 필터 질의는 max_rounds 가드 정상 작동(사유 폴백) — S-IR4 대상 + 마감 유도 개선 여지. known mistakes 등재(목 shape 가정).
> - v11(2026-07-30) — **S2 완료·D-128 등재**: column_deriver(분해 1콜+bind_tools 루프·가드 3중) + compile_from_nl 진입 분기(컴파일러·판정 무변경) + 4경로 대칭(실측: 진입점 2개 수렴 — compile_from_nl 단일 분기) + N2 대칭 이관(`select_fewshot_examples` 공유 헬퍼·멀티 경로 배선) + smq_derivation 관측. 검증: OFF 골든+1방 프롬프트 sha256 동일·ON 결정적 목 34건·기준선 대조 회귀 0. **기존 비대칭 2건 기록**(멀티 경로 value_index 미전달·B/D 경로 smq_derivation 상위 미전파 — S2 이전 기원, 필요 시 별건). S2 자기교정 1건(`_column_synonyms` 추정 키)과 신규 DB 편입 체크리스트의 레지스트리 흡수를 `docs/18_known_mistakes.md`에 등재. impl-r2 최종 보고 소급 반영: 리허설은 AST 단언(가짜 DB `acme_dc1` 코드 수정 0 검증), 의도된 프롬프트 변경 2곳(semantic_router 렌더 — 어휘 3→9개·general_inference) 승인.
> - v10(2026-07-30) — **R1·R2·N2·S1 전 트랙 완료·등재**: implementer 4개 병렬(중간에 impl-r2 네트워크 중단 — 잔여 1줄(tools overfit 편입)·최종 검증은 팀 리드 통합 패스가 마무리). 검증 = 전체 스위트 HEAD 기준선 대조 실패 집합 완전 동일(43건 사전 존재분, 신규 0/해소 0). **범위 조정 기록**: ①R1-5(db_profiles 오버레이 YAML 분할) 보류 — gp↔yd 3줄 차이로 실익 부족, 병합 함수·테스트만 확보 ②semantic_models YAML은 폴백 사본+synonym_seeds 원천으로 유지(삭제 금지) ③Template A 빈 EAV 예제 드리프트는 R1이 교정 완료, 단 `polestar_b0.yaml:456` 동종 예제는 **b0 실측 후 판단**(D-058/D-061은 gp 실측 — 무단 수정 금지) ④프롬프트 잔여 블록(diff≠0) 옵트인 전환은 후속 ⑤N2 대칭 주입(멀티·인라인·deepagents 경로)은 S2 공유 헬퍼에서 ⑥PolestarAdapter `classify_metric_field` 위임 훅 추가(S1 후속, source="adapter" 전환) ⑦R1 부수 관찰(후속 판단): 프롬프트 Template B `hi` 서브쿼리가 값 컬럼(ipaddress)을 조인 키로 사용 — 구조적으로 취약, `hi.id`(=COALESCE(platform_resource_id,id)) 조인이 정확(EAV→direct 교정 시 범위 한정으로 미수정, 프롬프트 잔여 블록 후속과 함께 판단). D-131·D-133 등재(D-128·D-132 예약 유지).
> - v9(2026-07-29) — **Phase S1(tools 계층) 복원**: v2 재구성 때 v1 Phase 1의 2·3항(fine-grained tools·validate_sql 추출)이 Phase 목록에서 누락됐던 것을 발견, S2 선행 작업으로 §5에 복원하고 R1·R2·N2와 병행 착수.
> - v8(2026-07-29) — **Phase 0-3 완료 + D-번호 재부여**: implementer 3개 병렬로 결함 14건 처리 완료(회귀 0 — worktree HEAD 대조 실측 2회). 특이사항: ⑧은 prefix 매칭 제거까지 포함해 fail-closed로 구현(+`GET /admin/settings`의 `DB_CONNECTION_STRING` 평문 노출 **추가 발견·수정**, graph.py `route_after_approval`도 fail-closed 보강) → **D-130 등재**. **⑦은 삭제 보류로 종결** — Plan 68(D-129, 설정 카탈로그)이 해당 필드를 "미소비 뱃지 UI 노출·삭제는 별건"으로 등재하여 충돌(CLAUDE.md 충돌 시 임의 진행 금지) → 미소비 사실 주석 명시로 대체, 삭제는 Plan 68 소관과 함께 별도 판단. **예약 재부여**: Plan 68의 D-129 등재로 R=D-131·alarm=D-132·N=D-133 순차 이동(§7·관련 문서 참조 갱신). 부수: 설정 카탈로그 224→226필드(D-129 부기), `.env.example`의 `polestar_b0` process URL 미반영 차이 발견(사용자 확인 필요 — Phase 0-1 항목 아님).

---

## 0. 핵심 결론과 논점 재정의

### 0.1 실측 결론: 요청한 두 축 모두 "이미 존재"한다

| 요청 축 | 기존 구현 | 운영 상태 (실측) |
|---|---|---|
| "LLM이 컬럼을 도출해 쿼리를 조립" | **D-076 트랙 C**: LLM이 자연어→SMQ(dimension/measure/filter **선택만**) → `check_coverage` 결정적 판정 → `compile_smq` 코드 조립 (`semantic_compiler.py`) | `.env:369` `TEXT2SQL_SEMANTIC_COMPOSE=true` **ON** (config 기본값은 False) |
| "deep agents 기반 프레임워크" | **D-037**: deepagents 0.6.10 `create_deep_agent`, 오케스트레이터/워커 분리, tool 6종 (`orchestration/deep_agent.py:113`) | `.env:170,176` **ON** |

따라서 본 계획의 논점은 "신규 도입 여부"가 아니라 다음 둘이다:

> **트랙 S**: 기존 SMQ 트랙 C의 "1방 SMQ 선택"을 "단계적·도구 기반 컬럼 도출 루프"로 확장 (LLM 역할 확대의 실현 지점). 루프의 실행 기반은 deepagents 자율 루프 vs 현행 고정 그래프 내 tool-loop 중 선택(§2).
> **트랙 R**: 경직성의 실제 원인(§1.2 ①~④ — 지식 4중 복제, 신규 DB 9곳+ 수정, 한글 정규식 해석, 가드 누적)을 구조적으로 해소. LLM 역할 확대만으로는 이들이 해결되지 않으므로 별도 트랙으로 명시한다.

### 0.2 왜 확장이 필요한가 — 실측 약점 (plans/61 상태 라인, 2026-07-15 E1 하네스)

- **SMQ 생성 정확도 1/6** — "LLM이 컬럼만 고르게 하면 안전하다"는 전제에서, 1방 선택 자체가 자주 틀림. 단계적 도출로 개선을 노리는 지점이 정확히 여기다.
- **선언 커버리지 76.9% vs 런타임 판정 커버리지 34.6%** — 선언상 inside인데 coverage_router가 outside로 떨어뜨린 11건(dimension 확장 후보).
- 결정적 조립은 **구조적 환각(잘못된 조인·미존재 컬럼)을 0으로** 만들지만 **의미 오답(엉뚱한 컬럼 선택)은 그대로 남는다**(D-076 주의 항목). 트랙 S의 성공 기준은 이 두 수치의 개선이다(§6.1).

### 0.3 착수 전 사용자 확인 항목 (게이트) — 2026-07-29 인터뷰 결과 반영

1. **확장 vs 대체**: ~~분석 후 결정 대기~~ → **확장 확정(2026-07-29 인터뷰)** — §2.4 성능 분석·§2.5 유연성 분석 모두 확장 지지. D-128 + S-IR1~5로 진행.
2. **Gemini + tool-calling 스모크 승인 (D-127)**: **보류(인터뷰 결정)** — Phase 0 착수 직전에 재문의. 승인 전 실 호출 금지 유지.
3. **EX 하네스 DB 적재**: **적재 선행 확정(인터뷰 결정)** — 착수 전 테스트 DB 데이터 적재(사용자 측 준비)로 EX 전후 측정을 확보한다. plans/63 실측(2026-07-21)의 위양성 상태(gold·pred 0행) 해소가 Phase 0-2 판정 기준.
4. **`annotation_signal.py` LLM 전환**: **전면 전환 확정(인터뷰 결정)** — D-035 domain 계층 "LLM 미개입" 경계의 예외 결정(D-130 예약). 범위·설계는 R3-(v).

---

## 1. 현 구현 경직성 진단 (실측)

### 1.1 "LLM 기반이 아니다"는 절반만 맞다

`query_generator()` 본체(`query_generator.py:227-470`)는 우선순위 3단 분기다:

```
① deterministic_sql = _try_build_form_fill_pivot_sql(...)   # 폼필 EAV 피벗 — LLM 완전 우회 (진입 조건 좁음)
② semantic_sql      = await compile_from_nl(...)            # 트랙 C: LLM은 SMQ 선택만, 코드가 조립
③ else              → 거대 프롬프트 1방 LLM 생성             # _build_system_prompt + _build_user_prompt → ainvoke 1회
```

"LLM이 SQL을 쓰는" 코드는 사실상 `ainvoke` 1줄이고, 주변 2,500줄 이상이 프롬프트 조립·결정적 조립·후처리 가드다. LLM 호출 분포 실측(주요 노드): `input_parser` 2회 / `schema_analyzer` 3곳 / `semantic_compiler` 1회(SMQ) / `semantic_router` 1회 / `query_validator`·`query_executor`·`context_resolver`는 **순수 파이썬**.

### 1.2 진짜 경직성의 원인 — 트랙 R의 대상 ①~④ + 부수 항목

| # | 증상 | 실측 근거 | 해소 계획 |
|---|---|---|---|
| **①** | **동일 SQL 지식 4중 복제** — 스키마 1건 변경 시 4곳 동기화 | 프롬프트 `polestar/prompts.py` 904L(SQL 예제 15블록) / 조립기 `assembler.py` 391L / `config/db_profiles/*.yaml` 555L×3(gp vs yd diff **5줄**) / `config/semantic_models/*.yaml`(profiles와 실질 1줄 차이, 헤더가 "복제해 작성" 자인) | **R1** (§5) |
| **②** | **신규 DB 편입 = 9곳 이상 동시 수정** + **위치 키워드 튜플 6곳 사본** | db_profiles + semantic_models + `domain_config.py` + `zones.py:15-18` + `process_query.py:56-60`(`_LOCATION_DB_HINTS`) + `field_mapper.py:210/218-223` + `input_parser.py:35` + `context_resolver.py:40` + `subagents.py:64-65` + semantic_router few-shot. `("공동존","김포","여의도",…)` 튜플이 `input_parser.py:35`·`field_mapper.py:210`·`context_resolver.py:40`·`subagents.py:64`·`process_query.py:56`·`general_inference.py:178-186` 6곳에 각각 존재 | **R2** (§5) |
| **③** | **한국어 표면어를 정규식으로 해석** | `_MONTHLY_BREAKDOWN_RE`(`월별\|(?<!개)월간`, `semantic_compiler.py:642`), `_CPU_CAPACITY_RE`(조사 `[,·와과및]`까지 열거, `:648`), `_ABS_MONTH_RE`·`_N_MONTHS_RE`·`_TOP_N_RE`(`query_gen_common.py:21-143`). YAML description 끝 `[resource_type: …]` 주석을 정규식 파싱(`assembler.py:42`) | **R3** (§5) |
| **④** | **LLM 비결정성 교정 가드의 누적** | `normalize_smq`(`semantic_compiler.py:652-686`) — LLM 흔들림 실측 3회를 각각 별도 if문으로 교정. `correct_servername_hostname_mapping`, `_check_left_join_where_demotion`(D-085), `_find_bare_hangul_tokens`(D-104) 등 같은 계열. **가드가 늘수록 경직성이 누적되는 구조** | **R4** (§5) |
| (e) | 결정적 커버리지가 실질적으로 매우 좁음 | `_PATTERN_AB_SAFE_FILTER_FIELDS = {"resource_type"}`, `_PATTERN_C_SAFE_FILTER_FIELDS = {"ALARMSEVERITY"}` — 그 외 필터 전부 LLM 폴백. 월별 분해도 게이트로 폴백 강제(`semantic_compiler.py:771-777`) | 트랙 S Phase 3 |
| (f) | 폴스타 리터럴이 어댑터 경계 밖으로 누수 | `cmm_resource` 89회·`server.Server` 98회·`polestar` 254회(43/183 파일). 공용 계층 침범: `utils/query_gen_common.py`(9회), `nodes/query_generator.py`(7회), `semantic_compiler.py:418/424/535` 폴백 기본값 | **범위 밖**(Plan 63 후속) — 단 신규 유입은 `overfit_check.py --ci`가 차단, R1·R2 신규 코드는 어댑터/레지스트리 경계 준수 |

**진단**: 경직성 해소의 핵심은 "LLM 역할 확대" 그 자체가 아니라 ①**지식 정본 일원화(수기 사본 4곳 → 정본 1곳 + 생성)**, ②**DB 등록 단일점화**, ③**표면어 해석의 LLM 이관 + 결정적 검증 유지**, ④**가드의 계측 기반 축소**다. LLM 역할 확대(트랙 S)는 ③·④의 전제 조건이 된다(루프가 탐색·해석을 대체해야 정규식·가드를 줄일 수 있다).

### 1.3 참고 — semantic routing의 실체

`src/routing/semantic_router.py`(374L)는 임베딩이 아니라 **LLM 프롬프트 분류**다(docstring 명시, 벡터 연산 0줄). 진짜 임베딩은 `schema_cache/synonym_semantic.py` 한 곳뿐이며 `SYNONYM_SEMANTIC_MATCH` 기본 False로 미실행. 용어 구분: `semantic_router`=LLM 의도분류 / `semantic_compiler`=결정적 SQL 조립 / `synonym_semantic`=임베딩. 본 계획에서 "semantic routing 방식"은 **고정 LangGraph 파이프라인 전체**를 가리키는 것으로 해석한다.

---

## 2. 프레임워크 비교 — deep agents vs 현행 고정 그래프

### 2.1 비교 근거 (2026-07 조사, deepagents 0.6.10 로컬 실측 포함)

- **deepagents 0.6.10**: `create_deep_agent(model, tools, *, system_prompt, subagents, state_schema, interrupt_on, checkpointer, …) → CompiledStateGraph`. LangGraph 위 프리셋 하네스라 기존 그래프에 노드로 래핑하는 현행 방식(D-037)이 프레임워크 의도와 일치. `async_create_deep_agent`는 **0.6.10에 부재**(Plan 48/49에 표기 있으면 폐기). state 접근은 `InjectedState`가 아니라 **`ToolRuntime`** 주입이 현행 권장.
- **공식 text2sql 예제 존재**: `examples/text-to-sql-agent` — `list_tables`/`get_schema`/`query_checker`/`execute_query` 4종 tool. 우리 schema_analyzer/query_validator/query_executor와 1:1 대응.
- **FlexSQL(arXiv 2605.02815)**: 스키마를 앞단에 1회 검색하는 고정 파이프라인 대신 **추론 도중 언제든 스키마 탐색·값 확인·검증 쿼리를 허용** → Spider2-Snow 65.4%로 강한 베이스라인 상회. **"탐색 시점 비고정" 자체가 정확도 기여 요인**이라는 실증. 반면 column-by-column 조립을 LLM 자율에 맡긴 사례는 미발견 — **조립은 결정적, 탐색만 유연하게**가 근거 우위.
- **비용**: 단순 과제 1건 비교에서 deepagents가 고정 그래프 대비 토큰 약 20배(2.5k→48k), 지연은 오히려 짧음(7.6s→4.7s). **단일 과제 측정이라 일반화 금물**이나 토큰 방향성은 복수 출처 일치.
- **운영 이력**: 우리 deepagents 경로에서 결함 5건을 결정적 가드로 이미 봉합(D-092~D-095, D-100 — 조기 종료 은폐, 빈 응답 재개 `_MAX_RESUME_ATTEMPTS=3` 등). 프로덕션 팀 보고 기준 에이전트 정상 동작률 약 80%.
- **Gemini 리스크**: 0.6.10 built-in 프로파일에 **Google/Gemini 항목 없음**(anthropic 3종·openai 2종뿐 — `profiles/_builtin_profiles.py` 실측). upstream에 `MALFORMED_FUNCTION_CALL`+빈 응답 이슈 2건(#417·#119, **closed as not planned**), 특히 built-in `write_todos` 바인딩에서 발생. `register_provider_profile`로 자작 우회 가능(beta API). 단정 불가 — **실측만이 답이며 D-127 승인 대상**.

### 2.2 3안 비교

| | A안: deepagents 전면 자율 (쿼리 생성까지 자율 루프에 위임) | B안: 현행 유지 + 프롬프트만 개선 | **C안: 하이브리드 (권고)** |
|---|---|---|---|
| 구조 | 오케스트레이터가 fine-grained tool로 스키마 탐색→컬럼 선택→SQL 작성 전부 수행 | 고정 그래프 그대로, ③LLM 1방 프롬프트 보강 | 바깥 고정 그래프 유지 + **query 생성 내부에 단계적 컬럼 도출 tool-loop 신설**, 조립은 SMQ 컴파일러 |
| 결정성/재현성 | 실행 경로 매번 상이 — D-035·Known Mistakes("LLM 비결정성에 정합성 의존 금지") 정면 충돌 | 유지 | 조립·검증은 결정적 유지, 탐색만 개방 |
| 정확도 근거 | 자율 조립의 근거 부족 | 1/6 정확도 개선 없음 | FlexSQL "탐색 비고정" 실증 방향과 일치 |
| 토큰/지연 | 토큰 대폭 증가(방향성 20배급) | 불변 | 컬럼 도출 단계만 증가 — 복잡도 게이트(R6)로 통제 |
| 기존 자산 | 폼필 피벗·방언 분기·가드 후처리 손실 위험 | 전부 유지 | 전부 유지 + tool로 재노출 |
| 경직성 ①~④ | 해소 안 됨(지식 복제·DB 편입은 프레임워크 무관) | 해소 안 됨 | 트랙 R로 정면 해소 |
| 기존 결정 정합 | D-067(이중 엔진 금지)·D-035 충돌 | 충돌 없음(개선도 없음) | D-076 확장·D-099 편입 원칙 그대로 |
| Gemini 리스크 | built-in tool 바인딩 이슈 직격 | 없음 | 루프를 자체 tool-calling으로 구현 시 회피 가능 |

**주의**: 경직성 ①~④는 어느 프레임워크를 골라도 저절로 해결되지 않는다(deepagents로 전면 전환해도 프롬프트 사본·DB 등록 지점·정규식은 그대로 남는다). 프레임워크 선택과 경직성 해소는 **독립 문제**이며, 그래서 트랙 R을 분리했다.

### 2.3 권고: C안 — 그리고 루프 실행 기반은 "LangGraph 노드 내 자체 tool-loop"

- **deepagents는 현행 위치(의도 오케스트레이션 상위 계층, D-037)에 유지**한다. semantic_router·`_INTENT_ROUTE_MAP`도 삭제 금지(D-037 주의사항).
- **단계적 컬럼 도출 루프는 query 생성 단계 내부의 자체 tool-calling 루프**(LangChain `bind_tools` + while, 상한 고정)로 구현한다. deepagents 서브에이전트로 감싸는 안은 Phase 4 조건부 검토로 미룬다. 이유:
  1. deepagents built-in tool(`write_todos`, 파일시스템 8종)이 additive로 딸려 들어와 토큰·Gemini 바인딩 리스크를 키움 — 컬럼 도출에는 불필요.
  2. 서브에이전트 경계에서 **커스텀 state 변경 유실 보고**(upstream 포럼)가 있어 멀티턴 승계 신호(hostname/db_id)가 위험.
  3. 자체 루프면 반복 상한·타임아웃·감사 로그를 기존 방식(결정적 가드)으로 직접 통제 — D-092~095 유형의 재발 지점을 만들지 않음.
  4. 삽입 지점 원칙(plans/61 v11 §3: 그래프 엣지가 아닌 **노드 함수/공유 헬퍼**) 준수 — SQL 생성 4경로(§5 Phase S2) 전부에 대칭 주입 가능.

### 2.4 확장 vs 대체 — 성능 관점 분석 (2026-07-29 사용자 요청)

**분석 틀**: "성능"을 두 축으로 본다 — (1) 쿼리 품질(SMQ 정확도·커버리지·EX), (2) 실행 성능(지연·토큰).

**핵심: 측정된 성능 병목의 위치.** 현행 트랙 C 파이프는 3단이다:

```
NL ─→ [①LLM 1방 SMQ 선택] ─→ [②coverage 판정] ─→ [③결정적 컴파일] ─→ SQL
       오류 기여: 1/6 오답        34.6% (선언 76.9%       오류 기여 ≈ 0
       (실측 최대 병목)           대비 격차 — 과소 판정)    (구조 환각 0, 골든 46건 고정,
                                                        폼필 13건 바이트 보존)
```

품질 손실은 ①·② 단계에 집중되어 있고, ③단계(컴파일러)의 오류 기여는 실측상 0에 가깝다. **"대체"는 오류 기여가 없는 ③까지 갈아엎는 선택이고, "확장"은 오류가 실측된 ①·②만 교체하는 선택이다.** 성능 개선의 인과 지점만 보면 결론은 자명하다.

| 성능 축 | 확장 (①·② 교체, ③ 보존) | 대체-(a) agentic 엔진 (LLM이 SQL까지 작성) | 대체-(b) 새 결정적 엔진 재작성 |
|---|---|---|---|
| SMQ 선택 정확도 (1/6) | 도구 탐색 후 선택(값·스키마 실증) — FlexSQL 실증(+10%p대 상대 개선) 방향 일치 | 개선 근거 없음 — 자율 조립 우위 문헌 미발견(§2.1) | ①단계는 동일 문제 그대로 (엔진 재작성과 무관) |
| 커버리지 (34.6%) | 카탈로그 자동 생성 + 동적 safe filter로 판정 폭 확대 | 커버리지 개념 자체가 소멸 — 전 질의가 LLM 자유 조립으로 | 재작성해도 판정 로직 품질은 별개 문제 |
| 구조적 환각 (현재 0) | **0 유지** (③ 보존 + 방언 분기·D-085/D-096~099 가드 그대로) | 사전 차단 상실 — validator 사후 검증에만 의존, LLM 자유생성 EX 42.9% 실측이 상한 참고치 | 재작성 초기 회귀 리스크 (골든 46건 재작성) |
| 토큰/지연 | 복잡 질의만 루프 진입(복잡도 게이트) — 단순 질의는 기존 1방 경로 무증가 | 토큰 방향성 20배급(§2.1), 전 질의 적용 | 토큰 무증가, 단 개발 기간 동안 개선 0 |
| 전후 측정 가능성 | **같은 ③을 공유하므로 ①단계 변화만 분리 측정 가능** (E1 SMQ 정확도 축이 정확히 이 용도) | 파이프 전체가 바뀌어 원인 분리 불가 | EX 재측정 필요하나 개선 가설 자체가 없음 |
| 기존 결정 | D-067·D-099 정합 | D-067·D-076·D-099·D-100 폐기 수반 | D-067 위반(이중 엔진) 또는 대개편 |

**주의 — "자유생성 42.9% > semantic ON 28.6~35.7%니까 1방 SMQ를 버리자"는 논거는 성립하지 않는다.** 이 역전은 골드셋↔시맨틱모델 규약 모순(gp-001~003의 gold_sql `platform.server%` vs gold_smq `server.Server`가 서로소 모집단)으로 진단된 것이지 순수 품질 차가 아니다(plans/61 상태 라인). 또한 deepagents 비교 실측에서 자율 루프의 **지연**은 오히려 짧았으나(4.7s vs 7.6s) 단일 과제 측정이라 일반화할 수 없고, 우리 그래프는 전 노드 순회가 아니라 조건 분기로 이미 스킵이 있다.

**결론: 성능 관점에서 확장 우위.** 확장은 실측 병목(①선택 ②판정)을 직접 공격하면서 유일하게 검증된 강점(③구조 환각 0)을 보존하고, 개선 여부를 분리 측정할 수 있다. 대체는 (a)형이면 구조 환각 방어선을 상실하고 토큰이 커지며, (b)형이면 병목이 아닌 구간의 재작성이라 품질 개선 기대치가 0이다.

**대체가 정당해지는 조건**(확장 실측 후 재평가 트리거): Phase S3 측정에서 단계적 도출로도 SMQ 정확도가 개선되지 않고, 원인이 선택 오류가 아니라 **SMQ 표현력 한계**(현 dimension/measure/filter 구조로 표현 불가한 질의 형태)로 판명되는 경우. 그 경우에도 1순위는 엔진 대체가 아니라 **SMQ 스키마 확장**(중간 표현 강화 — 서브쿼리·윈도우 함수 등 표현 추가)이다.

### 2.5 SMQ 방식의 유연성 분석 (2026-07-29 사용자 요청)

**분석 틀**: 유연성 = 사용자 요구 스펙트럼(수요) 대비 표현 가능한 질의 형태(공급). SMQ는 설계상 **닫힌 세계**(카탈로그에 있는 것만 조립)이므로, 시스템 전체 유연성은 「SMQ 커버 범위 × 폴백 품질」로 결정된다.

**실측 1 — SMQ IR(중간표현)의 표현력 한계 8가지** (`semantic_compiler.py:50-83` 스키마 + `:195-270` 커버리지 판정 실측):

| # | 한계 | 근거 | 막히는 수요 (골드셋 실례) |
|---|---|---|---|
| 1 | 집계가 avg/max/min 3종뿐 — **COUNT/SUM 부재** | `SMQMeasure.agg`, `_AGG_FN` | gp-003 "서버 수를 조회해줘" — 최빈 형태인데 표현 불가 |
| 2 | **GROUP BY 고정**(서버당 1행 피벗 단일형) — 시계열 분해·전역 단일값 불가 | `_compile_ab` 단일 GROUP BY, 월별 게이트(`:771`) | gp-012 "3개월간 월간 통계"(월별 행), gp-013 "전 서버 통틀은 평균 단일 값" |
| 3 | 필터 안전장 극소 — 코드상 A/B는 `{resource_type}` 1개뿐. **YAML `filterable` 5필드 선언과 불일치** | `_PATTERN_AB_SAFE_FILTER_FIELDS`(`:200`) vs `polestar.yaml:54` | gp-006 "cocm-hdkapp01 서버의 …" 서버명 필터, 측정치 임계("사용률 80% 이상") 전부 폴백 — **선언 76.9% vs 판정 34.6% 격차의 구조적 원인** |
| 4 | **ORDER BY·LIMIT이 IR에 없음** — 정렬 방향은 `_resolve_ranking`이 표면 키워드(`_RANK_*_MARKERS`)로, LIMIT은 정규식으로 별도 결정 | SMQ 필드 목록(`:73-83`), `:444` | 정렬·상한이 IR 밖 표면어 의존 — 정규식 경직성(R3)과 동일 문제의 다른 얼굴 |
| 5 | 필터 op 6종(eq/ne/in/like/gte/lte), **AND 결합만** | `SMQFilter`(`:62-67`) | BETWEEN·OR·NOT·IS NULL 조건 불가 |
| 6 | **패턴 간 결합 불가** — A+B(설정+성능)는 단일 피벗으로 지원되나 C(알람)×B(성능) 조인 불가, C에 집계 없음 | 패턴별 분리 컴파일 | gp-015 "알람 최다 발생 상위 10 서버"(알람 count+랭킹) |
| 7 | 파생 표현 불가 — 전월 대비 증감·비율·자기조인 | IR에 표현 수단 없음 | gp-014 "유사한 사양 서버 찾기" |
| 8 | **카탈로그 닫힘** — dimension 21·measure 4 밖은 전부 미커버, LOB 속성 제외, 확장은 사람 승인 루프(D-012)로만 | `_coverage_ab` 미정의 즉시 outside | E1의 "dimension 확장 후보 11건" |

**실측 2 — 이것은 결함이 아니라 명시된 설계다.** `check_coverage` docstring(`:212-216`): "보수적 판정(과설계 방지) — 커버리지 내는 컴파일러가 **정확히** 조립할 수 있는 형태로만 한정하고, 나머지(HAVING 서버필터·동적 날짜·집계 over 알람·LOB)는 폴백에 맡긴다." D-076의 계약 자체가 "커버리지 내 결정적, 밖은 3단 폴백"이며, 골드셋도 outside 6건을 의도적으로 포함해 큐레이션됐다(낙관 편향 통제).

**실측 3 — 그러나 수요 대비 공급이 실제로 부족하다.** 운영자 검증 질의 중심의 골드셋 26건에서조차 런타임 커버 34.6% — 정형 수요의 약 2/3가 폴백(LLM 자유생성, EX 상한 42.9%)으로 흘러간다. 그리고 미커버 유형의 다수(count·시계열·서버명 필터·임계·알람 집계)는 **형태가 고정적이고 반복되는 정형 수요**다 — 비정형이라 폴백에 맡긴 게 아니라 IR이 미완성이라 못 담는 것이다.

**판정: SMQ "방식"은 유연성 확보 구조로서 적절하다 — 부족한 것은 방식이 아니라 IR의 완성도다.**
- 방식 적절성: "정형 다수는 닫힌 IR로 결정적 조립 + 비정형 꼬리는 폴백" 구조는 유연성과 정합성을 분리해 각각 다른 수단으로 얻는 설계이며, 유연성을 자율 LLM 조립로 얻는 대안은 §2.4에서 기각됐다(구조 환각 방어 상실). 진짜 비정형(자기조인·교차 DB 비교·다단 통지 조인)을 SMQ에 욱여넣는 것이야말로 과설계다.
- 구현 미완성: 한계 1~5는 SQL 재발명이 아니라 **고정 형태의 반복 수요**이므로 IR 확장 대상이다. 아래 5건을 트랙 S에 편입한다(→ Phase S3-1 구체화):

| # | SMQ IR 확장 | 해소하는 한계 | 비고 |
|---|---|---|---|
| S-IR1 | `agg`에 count/sum + 전역 집계(식별 dimension 생략 허용) | 1, 2(전역) | gp-003·gp-013 유형. 컴파일러 GROUP BY 생략 분기 |
| S-IR2 | `time_breakdown` 필드(월별/일별 GROUP BY 행 분해) | 2(시계열) | 월별 게이트(`:771`) 해소 — 기존 S3 항목과 동일 건 |
| S-IR3 | `order_by`/`limit` IR 승격 — stepwise 루프에서 LLM이 선택, 컴파일러가 카탈로그 검증 | 4 | `_RANK_*_MARKERS`·`_TOP_N_RE` 표면어 의존 제거(R3-(iii) 연결) |
| S-IR4 | 필터 안전장을 YAML `filterable` 선언과 일치(서버명 eq/like) + 측정치 임계 HAVING + 값 인덱스 실증 게이트 | 3, 5(일부) | 기존 S3-1 "동적 확대"의 구체화. 선언-판정 불일치 해소 |
| S-IR5 | 패턴 C 집계(서버별 알람 count + 랭킹) | 6(일부) | gp-015 유형 — 조인·집계 형태 고정적이라 결정적 조립 가능 |

- **확장 한계선(슬리퍼리 슬로프 가드)**: IR 확장 판단 기준은 "형태가 고정적이고 반복 실측된 수요만 IR로, 1회성·조합 폭발 수요는 폴백". 서브쿼리·임의 조인·OR 조합 일반화까지 가면 IR이 SQL을 재발명하는 것 — 그 지점부터는 폴백이 정답이다. 확장 각 건은 D-099 원칙(새 엔진 금지, 기존 컴파일러 편입) + 골든 회귀 추가와 함께.

---

## 3. 목표 아키텍처

### 3.1 트랙 S — 단계적 컬럼 도출 루프 (트랙 C 확장: "1방 SMQ" → "누적 SMQ")

```
parsed_requirements
   │
   ▼
[1] 요구 분해 (LLM): 질의를 필요 필드 단위로 분해
      예: "김포존 서버별 7월 CPU 사용률 상위 10대"
        → [서버 식별자, 위치 필터=김포, 기간=2026-07, 측정치=CPU 사용률, 정렬/상한=top 10]
   │
   ▼
[2] 필드별 컬럼 도출 루프 (LLM + tools, 필드 하나씩):
      tool: search_catalog / lookup_synonym / search_value_index /
            get_table_schema / get_sample_data / check_smq_coverage / resolve_time_range
      → 필드마다 {dimension|measure|filter, 근거, confidence}를 SMQ에 누적
      → 미해결 필드는 재탐색(상한 N회) 후 미커버 명시(침묵 폴백 금지)
   │
   ▼
[3] check_coverage (결정적, 기존): 누적 SMQ 전체 판정
   │ inside                          │ outside
   ▼                                 ▼
[4] compile_smq (결정적, 기존 확장)   기존 3단 폴백(트랙 A → 신뢰도 게이트 → 사람검토)
   │
   ▼
[5] query_validator (기존, 무변경)
```

- **[1]·[2]가 LLM 역할 확대 지점**이다. 기존 1방 SMQ 선택(`compile_from_nl` 내 `ainvoke` 1회)을 다회 도구 탐색으로 바꿔, LLM이 스키마·동의어·실값을 **확인하고 나서** 컬럼을 고르게 한다(1/6 정확도의 개선 가설).
- **[3]·[4]는 결정적 유지** — SQL 텍스트는 여전히 코드가 조립(D-076·D-067). 방언 분기(PostgreSQL/DB2)·LIMIT·`::numeric` 캐스트 등 기존 컴파일러 자산 그대로.
- 루프 산출물(SMQ + 필드별 근거)은 state에 구조화 저장 → 감사·평가·HITL 노출 재료.

### 3.2 트랙 R — 경직성 해소 목표 구조

**R1 지식 정본 일원화 (Single Source of Truth)** — §1.2-①

```
[정본 1곳]                          [생성/렌더 산출물 — 수기 편집 금지]
스키마 캐시 structure_meta           ┌ semantic_models 카탈로그 (dimensions/measures)
 (patterns·query_guide·             ├ 프롬프트 스키마·few-shot 블록
  query_examples·column_synonyms)   │   → polestar/prompts.py는 틀(템플릿)만 남기고
 + config/knowledge/{db_id}/  ──────┤     지식 데이터는 정본에서 렌더
   (큐레이션 오버라이드, diff만)      ├ db_profiles → 공통 베이스 + db별 diff 오버레이
                                    └ (조립기 assembler.py는 코드 자산 — 생성 대상 아님,
                                       카탈로그의 resource_type 메타를 참조하도록 정리)
```

지식 1건 수정(예: 컬럼 별칭 추가) 시 수정 지점이 **4곳 → 정본 1곳**이 되는 것이 목표. gp/yd처럼 5줄만 다른 프로필은 diff 오버레이로 축소된다.

**R2 DB 레지스트리 일원화** — §1.2-②

```
config/db_registry.yaml (단일 등록점, 신규)
  - db_id, engine, base_url env alias, db_schema
  - zone/위치 키워드, location hints, 도메인 설명, 활성 여부
        │ 로더가 주입 (각 소비처는 레지스트리 참조로 교체, 동작 불변)
        ▼
  domain_config.DB_DOMAINS · zones.py · _LOCATION_DB_HINTS ·
  field_mapper/input_parser/context_resolver/subagents/general_inference의 위치 튜플(6곳→정의 1곳) ·
  semantic_router few-shot의 DB 나열(렌더 시 참조)
```

- 신규 DB 편입 체크리스트(Known Mistakes 4항목: 위치 힌트·base_url·방언·db_schema)가 레지스트리 항목으로 흡수된다. 목표: **신규 DB 1개 편입 시 수정 파일 ≤2(레지스트리 + .env)**.
- **D-004 경계 명시**: 레지스트리의 위치 키워드는 라우팅 의도 분류에 쓰지 않는다(스키마 링킹·프롬프트 렌더 재료로만) — "키워드 기반 분류 재도입 금지"와 충돌하지 않음.
- **D-089 경계 명시**: 어댑터 훅 확장이 아니라 설정 일원화이므로 "두 번째 어댑터 전 추상화 확장 금지"(Plan 63 §9)와 충돌하지 않음.

**R3 표면어 해석 경직성 해소 (선별 전환 + 2단 폴백)** — §1.2-③. ※ (v)의 D-번호는 v8에서 D-130→**D-132**로 재부여됨. 전수 검토(`docs/regex_llm_conversion_review.md`) 결과 **전면 LLM 이관은 부적합** — A1~A9 정규식 옆에 "LLM 시도→실패→정규식 복귀" 실측 기록이 코드 주석으로 남아 있다(월별 과포획 4725→1820행 붕괴, BETWEEN 월 재계산 등 — D-068 2차 정정·D-076 후속4). 대신:
- (i) **A1~A6(기간·건수 해석) 2단 폴백**: 정규식 1순위 유지 + 미매칭 시 **이미 계산되고 폐기되는** `parsed_requirements["time_range"]`/`["limit"]`(input_parser LLM 산출물 — 현재 SQL 경로 소비 0건 실측)으로 폴백. 신규 LLM 호출 0건(D-127 무관), 폴백 발동 로그로 미커버율 계측. "지난 반년"·"백 건" 류 회복.
- (ii) **LLM 전환은 3곳만**: 시트명 추출(`input_parser.py:460-468`)·유사어 등록 의사(`synonym_registrar.py:169-179` — "2번만 빼고 전부"가 정반대 해석되는 현행 결함)·HITL 승인 의사(`api/routes/query.py:97` — 단 fail-open 기본값 수정 선행, Phase 0-3). 셋 다 산출물이 SQL 직접 입력이 아니고 재질의로 회복 가능.
- (iii) **키워드 substring 리스트 8곳 편입**: `_RANK_DESC/ASC_MARKERS`(ORDER BY 방향 직접 결정)·`_ALL_QUERY_KEYWORDS`류(전체 스코프 — "전체적으로" 오탐)는 트랙 S 루프 산출물로 대체 검토, `_LOCATION_DB_HINTS`·`_LOCATION_HINT_TERMS`는 R2 레지스트리로 이관. `_COMPLEX_KEYWORDS`(전략 분기 — 오분기 비용 낮음)·지시어 곱집합(멀티턴 원칙 정합)·`_PRIOR_REF_MARKERS`(D-086 근거)는 유지.
- (iv) 트랙 S ON 경로에서 시간·상한 해석은 `resolve_time_range` **도구 호출로 일원화**(도구 내부는 동일 결정 로직 — LLM이 해석을 재계산하지 않음). 정규식은 검증 게이트(dtime 필터 확인 등)로 강등, stepwise OFF 폴백 경로에서는 잔존(회귀 없음). `assembler.py:42`의 YAML description 주석 파싱은 R1 카탈로그의 구조화 필드(`resource_type:` 명시 키)로 대체. A13/A14 질의 토큰화는 LLM이 아니라 형태소 분석기(kiwi 등) 검토 — 폐쇄망 반입 행정 확인 필요(별건).
- (v) **`annotation_signal.py` 전면 LLM 전환 (D-130, 2026-07-29 인터뷰 확정)**: 운영자 손글 한국어 주석(`condition_log`/`description`)의 계획작업·해소·인지 신호 분류를 정규식 키워드에서 LLM 분류로 전환. **D-035 domain 계층 "LLM 미개입" 경계의 예외 결정**이므로 등재 시 D-035에 예외 명시. 설계 제약: ①domain 계층에 LLM 의존을 직접 넣지 않고 **분류기를 application 계층에 두고 domain은 분류 결과(enum)를 소비**하는 구조로 계층 규칙(arch_check) 준수 ②알람 유입량만큼 LLM 호출이 발생하므로 배치·캐시(동일 주석 재분류 방지)·타임아웃 폴백(실패 시 기존 키워드 분류로 강등, 사유 로그) 필수 ③노이즈 게이트 판정에 들어가는 신호이므로 분류 프롬프트 변경 시 Plan 52 벤치마크(D-048/D-049) 재측정. 알람 경로라 텍스트2SQL 트랙과 독립 — 별도 착수 가능. ④인접 처리(편향 검토 §2-9): `alarm/domain/anomaly.py:31-34` `METRIC_SOURCE_BY_KIND` 폴스타 스키마 상수를 설정 주입으로 이동(domain 계층 벤더 중립화) — 본 항목 착수 시 함께.

**R4 가드 계측·축소** — §1.2-④: `normalize_smq`·`correct_servername_hostname_mapping` 등 교정 가드에 발동 카운터(audit dict) 추가 → stepwise ON/OFF 발동률 비교 → **발동 0 확인된 가드부터 단계 축소**(계측 없이 삭제 금지 — Known Mistakes "금지 규칙은 유지할 정상 동작 명시 재확인"). 가드의 존재 이유였던 LLM 흔들림이 도구 탐색(값 실증 후 선택)으로 줄어드는지가 검증 대상.

### 3.3 트랙 N — 표현·명칭 표준화 (D-131, 2026-07-29 인터뷰 채택)

근거: `docs/standardization_literature_review.md`(문헌 조사) + `docs/synonym_management_analysis.md`(내부 IP-1~IP-5). 시맨틱 레이어 실증 +17~23%p(arXiv:2604.25149), 질의 이력 검색 +40.2pt(arXiv:2606.28387)가 핵심 수치. **채택 항목은 N2·N4 두 건**이며, N1(임베딩 후보 생성기 재배치)·N3(동의어 공급원 교체)은 이번 범위에서 미채택(문헌 근거는 조사 문서에 보존 — 추후 재검토 가능).

**N2 질의 이력 검색 (Verified Query Repository형)**

```
[원천 — 검증된 질의-SQL 쌍만]                [소비]
① 골드셋 26건(testdata/text2sql_gold)        ┌ 트랙 S 루프 tool: search_query_history
② db_profiles query_examples(운영자 검증)  ──┤   (유사 질의의 검증 SQL·SMQ를 참조 근거로)
③ 운영 실행 성공 이력 — 단 "실행 성공"만으론  └ 폴백(③LLM 1방) 프롬프트 few-shot 동적 선택
   불충분, 사람 확인 채널 통과분만 편입          (고정 few-shot → 유사도 상위 예시 교체)
```

- 검색 방식: **초기 구현은 어휘·퍼지 검색**(기존 `flex_match` 재활용, 신규 인프라 0). 문헌 수치 +40.2pt는 임베딩 검색 기준이므로, **임베딩 승격은 IP-4 계측 후 모델 결정과 함께 판단**(인터뷰 확정: 측정 선행).
- 오염 방지: ③운영 이력의 자동 편입 금지 — 등재는 사람 확인 게이트 경유(Known Mistakes "쓰기 지점 결정적 차단"과 동일 원칙, Snowflake VQR 구조).
- 저장: 스키마 캐시 계층에 `query_history:{db_id}` 신설(Redis, 기존 캐시 매니저 패턴 답습).

**N4 계층 taxonomy (평면 동의어 → 상위어/하위어)**

- R1 정본(카탈로그)의 aliases를 평면 리스트에서 계층 구조로 확장: 예 "사용률" ⊃ {CPU 사용률, 메모리 사용률, 파일시스템 사용률} — 상위어 단독 언급 시 하위 후보를 되묻기/전체 제시(모호성 명시), 하위어는 정확 매칭.
- 근거: 평면 동의어의 precision 붕괴(Tunkelang — "메모리 사용률"↔"디스크 사용률" 오연결 사고 예방), OpenMetadata 계층 glossary 사상.
- **단계화(ontology drift 경고 반영)**: 전면 조화 금지 — 핵심 용어(measure 4종·최빈 dimension)부터 안정화하고 점진 확장. R1 카탈로그 스키마에 `parent` 필드 추가로 구현(별도 저장소 신설 없음).
- 의존: R1 선행 필수(정본이 생긴 뒤 그 스키마를 확장).

**공통 검증**: N2는 폴백 경로 EX·few-shot 적중률로, N4는 상위어 질의 모호성 처리(되묻기 발동) 시나리오 테스트로 측정. 둘 다 기본 OFF 옵트인.

---

## 4. 기존 코드의 tools 재활용 매핑

### 4.1 현행 deepagents tools의 문제 — 입도

`orchestration/deepagents_tools.py::build_tools()`의 tool 6종은 전부 `async def _run(sub_query: str) -> str` 단일 시그니처(파이프라인 통째 래핑)다. **컬럼 단위 도출에는 그대로 쓸 수 없고**, 아래 fine-grained 계층을 신설한다. (부수 발견: `prompts/orchestrator.py:19-25`가 tool 6종 중 `query_live_processes`를 누락 — Phase 0에서 수정.)

### 4.2 즉시 tool화 가능 (순수 함수, AgentState 결합 0)

| 신설 tool | 재활용 함수 (위치) | 용도 |
|---|---|---|
| `search_catalog` | `render_catalog`(`semantic_compiler.py:607`) + R1 자동 생성 카탈로그 | 선택 가능 dimension/measure 검색 |
| `lookup_synonym` | `flex_match_score`/`best_flex_match`(`utils/flex_match.py:90/136`), `semantic_match_candidates`(`synonym_semantic.py:232`), `rank_synonym_candidates`(`synonym_governance.py:39`) | 용어→컬럼 후보 (정확→퍼지→임베딩 계단) |
| `search_value_index` | `search_value_index`(`schema_cache/value_index.py:199`) | 필터 리터럴 실존 검증 |
| `check_smq_coverage` | `check_coverage`(`semantic_compiler.py:207`) | 누적 SMQ의 중간/최종 판정 |
| `resolve_time_range` | `resolve_stat_month_range`(`query_gen_common.py:35`), `resolve_query_limit`(`:146`) | 기간·상한 해석 (R3 이관 수용처) |
| `get_table_schema` / `get_sample_data` | `PostgresClient` Protocol(`db/interface.py:33-55`) — 이미 db_id 단일 인자 추상화 | 실 스키마·샘플 확인 (FlexSQL형 탐색) |
| `classify_metric_field` | `assembler.py:66` (폴스타 어댑터 경유) | 필드→(resource_type, agg, val_col) |

### 4.3 얕은 래핑 필요

- `query_validator` — state에서 실제로 읽는 것은 `generated_sql`/`schema_info`/`active_db_engine` 등 5개뿐 → **`validate_sql(sql, schema_info, engine)` 순수 함수 추출** 후 tool `validate_sql_draft`로 노출(루프 내 사전 검증용). 노드는 추출 함수를 호출하도록 리팩토링(동작 불변, 기존 테스트로 회귀 확인).
- `SchemaCacheManager.get_schema/get_structure_meta/get_synonyms(db_id)` — 이미 단일 인자, 캐시 계층 유지한 채 tool 래핑.

### 4.4 재작업 없이는 tool화 부적합 (루프에 넣지 않음)

`query_generator`(state 12+필드), `schema_analyzer`(1296L, 캐시 3단+HITL), `multi_db_executor`(959L), `subagents._run_single_db_pipeline` — 이들은 루프의 **호출자**이지 tool이 아니다.

---

## 5. 단계별 구현 계획 — 2트랙 통합 시퀀스

> 공통 검증 게이트(전 Phase): ① 골든 회귀 `tests/text2sql/`(34+8+4건, 폼필 피벗 13건 바이트 보존 포함) ② `python scripts/overfit_check.py --ci`(기준선 43토큰) ③ `python scripts/arch_check.py --ci` ④ 신규 기능 전부 **기본 OFF 옵트인 플래그**(회귀 없는 증분 — Plan 61 방식 답습) ⑤ 실 LLM 호출 검증은 D-127 건별 승인.
>
> 의존 관계: `Phase 0 → R1 ┬→ S2 → S3(R3·R4 포함) → (조건부) S4`
> `            ├ R2 (독립 — 병행 착수 가능)`
> `            ├ N2 (독립 — 병행 착수 가능, 임베딩 승격만 IP-4 계측 후)`
> `            └ N4 (R1 이후 — 정본 카탈로그 스키마 확장)`

### Phase 0 — 선행 검증 + 기반 결함 수정 (실패 시 이후 무의미)

| # | 항목 | 판정 기준 |
|---|---|---|
| 0-1 | **Gemini(gemini-3.5-flash) + langchain tool-calling 스모크** — 자체 루프 방식이므로 deepagents built-in tool 없이 `bind_tools` 다회 호출만 검증. (선택) deepagents 경로 스모크는 별도 승인 | **✅ PASS (2026-07-29, 건별 승인 후 RUN_E2E=1 실행)** — tool 호출 5회(lookup×4+add×1) 3라운드 완주, 4.7s, 최종 한국어 응답 정상. `create_llm(provider_override="gemini")`+`bind_tools` 경로 실측 |
| 0-2 | EX 하네스 DB 적재 확인(§0.3-3) | gold SQL 실행이 0행이 아님 |
| 0-3 | 결함 수정(분석 중 발견): ①`semantic_compiler.py:598` `try_semantic_compile`의 `server_scope` NameError(호출부 0건 죽은 코드 — 삭제 또는 파라미터 추가) ②`prompts/orchestrator.py` `query_live_processes` 누락 ③`deep_agent.py:224/366` `recursion_limit` 미지정 ④`state.py`에 `prior_rows` TypedDict 미선언(4곳 사용 중) ⑤`cache_manager.get_schema_or_fetch` 타입힌트 4-튜플 vs 실제 5-튜플 ⑥`redis_cache.invalidate()`가 `column_value_index` 키 미삭제 ⑦`OrchestratorConfig` 미참조 필드 3개 정리 ⑧**`api/routes/query.py:71-100` `_parse_approval` fail-open — 기본값 `approve`라 오탐 시 미승인 SQL 실행**(보안 관련, `reject`/재질의로 반전) ⑨`api/routes/admin.py:246` DB 접속 문자열 파싱이 비밀번호를 평문 캡처(마스킹/폐기 처리) ⑩`utils/json_extract.py` 존재에도 JSON 추출 정규식 6곳 인라인 복제 — 공용화(SQL 코드블록 추출 2벌 통합 포함, D-066 대칭 주의) ⑪`config.py:424-426` 운영 호스트 URL 기본값 하드코딩 — 공란화+`.env` 전용(편향 검토 §2-4) ⑫`api/routes/alarm.py:49` `db_id` 기본값 `"polestar_b0"` 고정 — 설정화(§2-5) ⑬`mcp_server/security.py` `validate_polestar_domain()` DB 무관 무조건 적용 — 프로필/플래그 게이트(§2-6, 현행 동작 보존 기본값) ⑭`mcp_server/server.py:122` `register_polestar_tools` 무게이트 등록 — 설정 게이트(§2-7) | 기존 테스트 그린 + 항목별 단위 테스트 |

### Phase R1 — 지식 정본 일원화 (§3.2-R1, 경직성 ①)

1. `src/schema_cache/catalog_builder.py`(신규): structure_meta + column_synonyms + `config/knowledge/{db_id}/` 오버라이드 → dimensions/measures 카탈로그 생성. 기존 `config/semantic_models/*.yaml`과의 **diff 리포트 스크립트로 동등성 실측** 후 YAML을 오버라이드 전용으로 강등.
2. **프롬프트 지식 렌더 전환**: `polestar/prompts.py`(904L)의 스키마 설명·SQL 예제 15블록 중 카탈로그·query_examples(structure_meta)와 중복되는 부분을 정본 렌더로 교체 — 문자열 상수는 템플릿 틀만 잔존. 전환 전후 **렌더된 프롬프트 diff 리포트**(변경은 전건 사유 명시)로 회귀 통제.
3. **db_profiles 오버레이화**: 공통 베이스 + db별 diff(gp/yd 5줄)로 재구성. 로더가 병합하여 기존 소비 코드에는 동일 shape 제공(소비 코드 무변경).
4. `assembler.py:42` `_RESOURCE_TYPE_RE`의 description 주석 파싱을 카탈로그 구조화 필드로 대체(R3 선행분).
- **검증**: 카탈로그 diff 0(또는 차이 전건 사유 명시) + 렌더 프롬프트 diff 통제 + 골든 회귀. **시나리오 테스트**: "컬럼 별칭 1건 추가" 리허설에서 수정 파일 수 **4→1** 실측.

### Phase R2 — DB 레지스트리 일원화 (§3.2-R2, 경직성 ②) ※ R1과 독립, 병행 가능

1. `config/db_registry.yaml`(신규 단일 등록점) + 로더(`src/config` 계층). 기존 소비처를 레지스트리 참조로 교체: `domain_config.DB_DOMAINS` / `zones.py` / `process_query._LOCATION_DB_HINTS` / 위치 키워드 튜플 6곳 / `input_parser.py:35` / `context_resolver.py:40` / `subagents.py:64-65` / semantic_router few-shot 렌더.
2. 위치 키워드 튜플은 레지스트리 파생 단일 정의로 통합 — 6곳 import 교체(동작 불변).
3. D-004·D-089 경계 준수(§3.2-R2 명시 사항).
4. **편향 검토 발견분 합류(2026-07-29 인터뷰, `docs/polestar_bias_review.md` §2)**: ①`schema_cache/cache_manager.py:1113,1119` `allowed_tables=["cmm_resource"]` 폴백 ②`document/field_mapper.py:540` `CORE_TABLES` ③`document/field_mapper.py:63-75` `_schema_uses_metric_stat_pivot`(structure_meta `type=="eav"` 패턴 판정으로 교체) ④`scripts/synonym_seeds.py:79` db_id 접미사 휴리스틱 — 전부 레지스트리/구조 메타 참조로 전환.
5. **overfit_check 검사 범위 확대 = R2 완료 조건(인터뷰 확정)**: `src/document/` → `src/schema_cache/` → `mcp_server/mcp_server/`(`polestar_tools.py` EXCLUDE 대칭) → `src/alarm/domain/` 선별 편입 + **운영 리터럴 신규 카테고리**(`kbonecloud`·`sotori` 등 도메인 하드코딩 — 현행 패턴으로는 미게이트) 추가.
- **검증**: 기존 테스트 그린(동작 불변) + **신규 DB 편입 리허설 테스트**: 가짜 db_id 1개 추가 시 수정 파일 수 **≤2**(레지스트리+.env) 자동 검증 + 위치 튜플 정의처 grep **1곳** 단언 + 확대된 overfit_check 기준선 0 신규 유입.

### Phase N — 표현·명칭 표준화 (트랙 N, §3.3) ※ N2는 독립 병행 가능

1. **N2 질의 이력 저장소·검색**: `query_history:{db_id}` 신설, 골드셋+query_examples 초기 적재 → `search_query_history` tool(어휘·퍼지) + 폴백 프롬프트 few-shot 동적 선택. 운영 이력 편입은 사람 확인 게이트 경유(자동 편입 금지).
2. **IP-4 적중률 계측 부착**(`docs/synonym_management_analysis.md` 선행 권고): 동의어·이력 검색 적중률 로그 → 임베딩 승격(모델 선정 포함) 판단 근거 확보.
3. **N4 계층 taxonomy**(R1 완료 후): R1 카탈로그 스키마에 `parent` 필드 추가, measure 4종·최빈 dimension부터 계층화. 상위어 단독 질의는 모호성 명시(되묻기/전체 제시).
- **검증**: N2 — 폴백 경로 few-shot 적중률·EX 전후. N4 — 상위어 모호성 시나리오 테스트. 둘 다 기본 OFF 옵트인.

### Phase S1 — fine-grained tools 계층 (S2 선행 — §4.2·4.3의 구현, v9에서 복원)

> v2 재구성 때 v1 Phase 1의 2·3항(tools 계층·validate_sql 추출)이 Phase 목록에서 누락된 것을 2026-07-29 발견·복원. R1과 병행 가능(카탈로그 의존 tool은 주입 가능하게 설계).

1. `src/tools/`(신규 패키지): §4.2 표의 fine-grained tool 정의 — `search_catalog`(카탈로그 주입식 — R1 완료 전엔 기존 semantic model `render_catalog` 기반), `lookup_synonym`(정확→퍼지→임베딩 계단, 임베딩은 현행 OFF 존중), `search_value_index`, `check_smq_coverage`, `resolve_time_range`, `get_table_schema`/`get_sample_data`(DB Protocol 경유), `classify_metric_field`(어댑터 레지스트리 경유 — 코어는 폴스타 무지, D-089). LangChain `@tool` 래퍼 포함(S2 `bind_tools` 소재). 공용 계층 DB-agnostic(D-088) — overfit_check 통과 필수.
2. `validate_sql(sql, schema_info, engine)` 순수 함수 추출(§4.3): `query_validator` 노드에서 상태 결합 없는 검증 코어를 분리, 노드는 추출 함수를 호출(동작 불변 — 기존 validator 테스트 그린으로 검증). tool `validate_sql_draft`로 노출.
3. `scripts/arch_check.py` 계층 맵에 `src/tools/` 등록(기존 계층 규칙 준수 — 의존 방향: tools는 utils·schema_cache·db 계층을 참조 가능, nodes·orchestration은 tools 참조 가능).
- **검증**: 기존 validator·text2sql 테스트 그린(동작 불변) + tool별 단위 테스트 + `overfit_check --ci`·`arch_check --ci`.

### Phase S2 — 단계적 컬럼 도출 루프 (트랙 S 본체, R1 카탈로그 의존)

1. `src/nodes/column_deriver.py`(신규): §3.1의 [1]요구 분해 + [2]필드별 루프. `bind_tools` + while(반복 상한 config, 전체 타임아웃 가드 — Known Mistakes "per-call만으론 무력화" 반영), 필드별 근거를 `state["smq_derivation"]`(TypedDict 선언)에 누적.
2. `semantic_compiler.compile_from_nl`에 진입 분기: `TEXT2SQL_STEPWISE_DERIVATION=true`(신규 플래그, 기본 False)면 1방 SMQ 대신 루프 산출 SMQ 사용. **컴파일러·커버리지 판정은 무변경.**
3. **4경로 대칭 주입**(D-066): `query_generator` 단일 / `subagents.py` 인라인 / `multi_db_executor._generate_sql` / deepagents 경로 — 공유 헬퍼 레벨에서 주입하고, 4경로 각각 발동 여부를 로그로 실측(비대칭이 Known Mistakes 1순위 반복 실수).
4. 미해결 필드는 구조화 사유와 함께 기존 3단 폴백으로 — **침묵 폴백 금지**, `text2sql_fallback` state 재활용.
- **검증**: 골든 회귀(플래그 OFF 시 바이트 동일) + 플래그 ON 오프라인 목 테스트(도구 호출 시퀀스 결정적 목) + 라이브 스모크(D-127 승인 후).

### Phase S3 — 커버리지 확대 + R3·R4 + 평가

1. **SMQ IR 확장 5건(§2.5 S-IR1~5)**: count/sum·전역 집계, `time_breakdown`(월별 게이트 해소), `order_by`/`limit` IR 승격, 필터 안전장 확대(YAML `filterable` 정합 + 측정치 임계 HAVING + 값 인덱스 실증 게이트), 패턴 C 집계 랭킹. 각 건 골든 회귀 추가, D-099 편입 원칙 준수.
2. **R3**(§3.2-R3 세부 순서대로): A1~A6 2단 폴백 + 발동 계측 → LLM 전환 3곳(시트명·등록 의사·승인 의사) → 키워드 리스트 대체 검토(`_RANK_*_MARKERS`·전체 스코프). stepwise ON 경로는 `resolve_time_range` 도구 일원화, OFF 폴백 경로 잔존.
3. **R4**: 교정 가드 발동 카운터 부착 → stepwise ON/OFF 발동률 비교 → 발동 0 가드부터 축소(계측 없이 삭제 금지).
4. **E1 하네스 전후 측정**: SMQ 정확도(1/6 대비), 런타임 커버리지(34.6% 대비), EX(performance·alarm 0% 대비), 토큰/지연 계측(`sql_candidates`·`query_attempts`·audit dict 재활용).
- **판정**: §6.1 성공 기준 미달 시 플래그 기본 OFF 유지, 원인 분석으로 회귀.

### Phase S4 (조건부) — deepagents 서브에이전트 이전 검토

Phase S2 실측에서 루프의 컨텍스트 오염(다회 도구 결과 누적)이 문제로 확인되는 경우에만: `create_deep_agent(subagents=[…])`의 격리 컨텍스트로 컬럼 도출 루프를 이전. 선행 실측 2건 필수 — ①`state_schema` 커스텀 필드의 서브에이전트 경계 유지 여부 ②Gemini built-in tool 바인딩. 착수 시 별도 채번.

---

## 6. 성공 기준과 리스크

### 6.1 성공 기준

**트랙 S (Phase S3 판정)**

| 지표 | baseline (2026-07-15 실측) | 목표 |
|---|---|---|
| SMQ 생성 정확도 | 1/6 | 골드셋 확장 후 유의미 개선(수치 목표는 골드셋 확장 시 확정) |
| 런타임 판정 커버리지 | 34.6% (선언 76.9%) | 선언 대비 격차 절반 이하 |
| EX (performance·alarm) | 0% | >0 실증 |
| 토큰/질의 | 현행 1방 대비 | 증가 상한 설정(복잡도 게이트로 단순 질의는 루프 미진입 — R6 연계) |

**트랙 R (Phase R1·R2·S3 판정 — 전건 리허설 테스트로 자동 검증)**

| 지표 | baseline (실측) | 목표 |
|---|---|---|
| 지식 1건 수정 시 수정 파일 수 | 4곳 (프롬프트/조립기 메타/db_profiles/semantic_models) | **1곳** (정본) |
| 신규 DB 1개 편입 시 수정 파일 수 | 9곳+ | **≤2** (레지스트리+.env) |
| 위치 키워드 튜플 정의처 | 6곳 사본 | **1곳** (grep 단언) |
| 표면어 해석 정규식 (A1~A6) | 해석 주체 · 미매칭 표현 침묵 소실 | 2단 폴백으로 미커버 회복 + 발동률 계측, stepwise ON 경로는 도구 일원화 |
| 오답 유발 해석 지점 (시트명·등록 의사·승인 의사) | 정규식 오탐("2번만 빼고"→정반대, fail-open 승인) | LLM 전환 3곳 + fail-open 반전 |
| 교정 가드(`normalize_smq` 등) 발동률 | 미계측 | 계측 도입 + stepwise ON에서 감소 실증 → 발동 0 가드 축소 |

### 6.2 리스크

1. **의미 오답이 도구 탐색으로도 안 줄 수 있음** — 탐색 근거를 요구해도 LLM이 잘못 고를 수 있다. 완화: 필드별 confidence + 값 인덱스 실증 필터, 미달 시 사람검토 폴백(기존 3단 유지).
2. **토큰·지연 증가** — 필드 수 × 도구 왕복. 완화: 복잡도 게이트(단순 질의는 기존 1방 유지), 카탈로그 프리필터로 도구 호출 수 절감, per-필드 캐시.
3. **R1 렌더 전환의 프롬프트 회귀** — 프롬프트 바이트가 바뀌면 LLM 출력 분포가 달라질 수 있다. 완화: 렌더 diff 전건 사유 명시 + 골든 회귀 + (가능 시) EX 전후 측정. 전환은 db 1개(gp)부터 단계 적용.
4. **R2 소비처 교체 누락** — 6곳 튜플·9곳 등록처 중 누락 시 비대칭. 완화: 교체 전 워드 경계 grep 전수 목록화(Known Mistakes "부재 단정 전 실측") + 정의처 1곳 grep 단언 테스트를 CI에 추가.
5. **4경로 비대칭** — 공유 헬퍼 주입 + 경로별 발동 로그 실측을 Phase S2 완료 조건에 포함.
6. **Gemini tool-calling 불안정** — Phase 0-1에서 조기 판정. 실패 시 오케스트레이터용 모델 재검토(vLLM Qwen 폴백은 현행 존재).
7. **EX 검증 불능 상태**(§0.3-3) — DB 적재 전에는 오프라인 골든 회귀만으로 판정하게 됨을 명시 승인.

---

## 7. 결정 등재 예정 (착수 시)

- **D-128 (트랙 S)**: 단계적 컬럼 도출 루프(Stepwise Column Derivation) — 트랙 C(D-076) 확장으로서, LLM의 역할을 "1방 SMQ 선택"에서 "도구 기반 다회 탐색·필드별 누적 선택"으로 확대. SQL 조립·커버리지 판정·검증은 결정적 유지(D-067·D-035 정합). 루프 실행 기반은 LangGraph 노드 내 자체 tool-loop(deepagents 서브에이전트 이전은 Phase S4 조건부). 기본 OFF 옵트인(`TEXT2SQL_STEPWISE_DERIVATION`).
- **D-130 (등재 완료, 2026-07-29)**: HITL SQL 승인 파서·라우팅 fail-closed 전환 — Phase 0-3 ⑧ 구현과 함께 등재(`docs/02_decision.md` D-130).
- **D-131 (트랙 R)** *(구 D-129 예약 — Plan 68 등재 충돌로 재부여)*: SQL 지식 정본 일원화 + DB 레지스트리 — 지식 사본 4곳을 정본 1곳+생성 산출물로 전환(R1), DB 등록·위치 키워드를 `config/db_registry.yaml` 단일점으로 통합(R2). D-004(키워드는 라우팅 분류에 미사용)·D-089(어댑터 훅 확장 아님) 경계 명시.
- **D-132 (R3-(v))** *(구 D-130 예약)*: alarm 운영자 주석 분류 LLM 전환 — 2026-07-29 사용자 인터뷰 결정. D-035 domain 경계 예외(분류기는 application 계층 배치, domain은 enum 소비). 실패 시 키워드 분류 강등 폴백.
- **D-133 (트랙 N)** *(구 D-131 예약)*: 표현·명칭 표준화 — N2 질의 이력 검색(검증 쌍만 편입, 사람 확인 게이트) + N4 계층 taxonomy(R1 카탈로그 `parent` 필드, 핵심 용어부터 단계화). N1·N3은 미채택 기록. 임베딩 승격은 IP-4 계측 후 별도 판단.
- 등재 시 `docs/02_decision.md:8` 채번 안내 라인 동시 갱신(D-130 등재 시 갱신 완료 — 예약 선점 명시됨).

## 8. 참조

- 코드: `src/nodes/query_generator.py:227-470`(3단 분기), `src/nodes/semantic_compiler.py`(SMQ·커버리지·컴파일·정규식), `src/db_adapters/polestar/assembler.py:228`(`build_multi_resource_pivot_sql`), `src/db_adapters/polestar/prompts.py`(904L), `src/orchestration/deepagents_tools.py:278-311`(현행 tool 입도), `src/routing/{semantic_router,domain_config}.py`, `src/utils/query_gen_common.py`, `src/schema_cache/{value_index,synonym_semantic}.py`, `src/db/interface.py:33-55`, 위치 튜플 6곳(§1.2-②)
- 문서: `docs/02_decision.md`(D-004·D-035·D-037·D-067·D-072~076·D-085~100·D-127), `docs/regex_llm_conversion_review.md`(**R3 근거 — 정규식 210곳 전수 분류·LLM 전환 판정**), `docs/standardization_literature_review.md`(**트랙 N 근거 — 표준화 문헌 조사·채택/미채택 기록**), `docs/polestar_bias_review.md`(**편향 9건·검사 확대 근거 — R2-4·5, Phase 0 ⑪~⑭**), `docs/synonym_management_analysis.md`(IP-1~IP-5), `docs/deterministic_sql_composition_review.md`, `docs/text2sql_quality_research.md`(§2.1 DIN-SQL/MAC-SQL·R6), `plans/61`(§2.1 4경로·E1 실측·v11 삽입 지점 원칙), `plans/48·49`(deepagents), `plans/63`(어댑터·overfit_check·EX 무효 기록)
- 외부: deepagents 0.6.10(`create_deep_agent` 시그니처·profiles 로컬 실측), `github.com/langchain-ai/deepagents/tree/main/examples/text-to-sql-agent`, FlexSQL arXiv 2605.02815, deepagents issues #417·#119(Gemini `MALFORMED_FUNCTION_CALL`)
