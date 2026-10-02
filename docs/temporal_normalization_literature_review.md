# 한국어 질의 시간 표현 정규화 → SQL 조건 — 문헌·산업 조사

> **작성일**: 2026-09-29 · **소비처**: `plans/122` §10(트랙 T)·§11
> **목적**: "지난달"·"반년"·"최근 30일"·"이번 분기"·"11월부터 2월까지" 같은 시간 표현을 정규화해 SQL 조건으로 바꾸는 설계의 근거를 얻는다. 대상은 한국어 인프라 모니터링 질의(PostgreSQL/DB2 · 통계 테이블 `cmm_metric_stat_[h,d,m]` · 월 키 `stat_date` 'YYYYMM')다.
> **도구**: arXiv API(export.arxiv.org) · ACL Anthology · PMLR·ICLR·NeurIPS 페이지 · 공식 문서 원문 · GitHub 소스. OpenAlex·Semantic Scholar·DBLP는 rate limit 때문에 쓰지 않았다. **인용수는 근거로 쓰지 않았다.**
> **표기**: **[확인]** 원문·소스 직접 대조 · **[추정]** 추론 · **[미확인]** 확인하지 못함.
> 조사는 읽기 전용 에이전트가 수행했다. 이 문서는 그 보고를 옮긴 것이다.

---

## 0. 핵심 결론

1. **"LLM은 슬롯만 뽑고 날짜 계산은 코드가 한다"는 방식은 여러 문헌이 같은 방향으로 지지한다.**
   - 동료심사 문헌:
     - TempEval-3(2013): 정규화 단계에서는 참가 시스템 전부가 규칙 기반이었다.
     - i2b2 2012 오류 분석: 상위 3개 시스템의 값 오류 중 69~85%가 상대 표현이었다.
     - PAL(ICML 2023): 날짜 이해 과제 64.8 → 76.2.
     - SCATE 코드 생성(NeurIPS 2025): Claude 3.7이 구간을 직접 내면 0.38, 실행 가능한 코드를 내면 0.49.
   - 2026년 프리프린트 2건: 날짜 산술을 결정적으로 처리한 하이브리드가 LLM 직접 생성을 크게 앞섰다(Pair F1 0.997 대 0.53 · 법률 기한 90.2% 대 61.2%).
   - **한계**: 근거는 전부 영어 뉴스·임상·법률 텍스트다. 한국어 질의 + text-to-SQL에 대한 직접 근거는 없다(공백).
2. **LLM이 가장 약한 곳은 월 단위 산술 · 기간(duration) 계산 · 하루 오차다.**
   - TempReason(ACL 2023): ChatGPT 연도 계산 99.6 EM 대 월 단위 30.5 EM.
   - Test of Time(ICLR 2025): Duration 13~16%, 오답의 21~25%가 정답과 하루 차이.
   - 월 통계(`stat_date` 'YYYYMM') 도메인이 바로 이 약점에 걸린다.
3. **「완결 기간 대 진행 중 기간」과 「주 시작 요일」에는 산업 표준이 없다.** Looker · Power BI · Tableau · Cube · dateparser · Duckling이 같은 표현을 다르게 해석한다(§1 표). 제품 정책으로 명시하고 해석 결과를 사용자에게 보여 줘야 한다. 문헌이 정답을 주지 않는다.
4. **"시간 단위 최근 30일"이 한 달로 뭉개진 결함은 표시 단위(grain)와 기간을 한 슬롯에 섞은 결과다.** Cube·MetricFlow는 `granularity`와 `dateRange`를 따로 둔다. Cube는 구간이 grain 경계에 맞지 않으면 최대공약수 grain(월 → 일·시간)으로 내려간다 [확인].
5. **리터럴 경계 + 컬럼 무가공 조건은 DB 공식 문서가 지지한다.**
   - Db2 11.5: "컬럼에 식을 씌우지 말고 역식을 쓰라." 그렇지 않으면 인덱스 start/stop 키를 못 쓰고 선택도 추정이 틀어진다.
   - PostgreSQL: B-tree는 `< <= = >= >` 비교에서 쓰인다.
   - **뉘앙스**: PostgreSQL에서 `col >= CURRENT_DATE - INTERVAL …`은 STABLE 함수라 인덱스를 쓸 수 있다 [확인]. 그래서 **DB 현재시각 함수를 금지하는 근거는 성능보다 재현성 · 판정 가능성 · 방언 차이**(PG `INTERVAL` 대 Db2 `CURRENT DATE - 1 MONTH`)다.
6. **한국어용 기성 도구는 사실상 없다** [확인]. 자체 규칙이 필요하다.
   - Duckling KO: '지난 N달·주·시간'만 처리한다. '개월'·'최근'·'반기' 패턴이 없다.
   - dateparser ko: '지난달'을 한 시점으로 본다. 기간(span) 기능은 영어뿐이다.
   - Recognizers-Text KO: DateTime이 "Specs-only(지원 보류)"다.
   - Chrono: 한국어 로케일이 없다.
   - HeidelTime: 한국어는 자동 생성 리소스(`auto-korean`)만 있다.

---

## 1. 주제별 요약

### 1.1 시간 표현 인식·정규화 (TimeML/TIMEX3 · 태거)

- **표준**: TimeML 1.2 TIMEX3는 상대 표현을 **"함수 + 기준 시각"**으로 정의한다 [확인].
  - "last week" → `value="XXXX-WXX" temporalFunction="true" anchorTimeID=t2`. 문서 작성 시각(DCT)에 기대어 해석한다.
  - "two weeks ago" → `type=DURATION value=P2W` + `beginPoint`/`endPoint`.
  - `mod`(START/MID/END/APPROX…)로 수식한다.
- **규칙 대 학습**:
  - TempEval-3: 인식은 비슷했고, 정규화는 참가 시스템 전부가 규칙 기반이었다. 값 F1은 HeidelTime-t 77.61 · SUTime 67.38이다.
  - i2b2 2012: 값 오류의 69~85%가 상대 표현이었다. DATE/DURATION 구분과 기준 시각 식별이 핵심이었다.
- **한국어**:
  - KTimeML(2009, 형태소 단위 stand-off) · Korean TimeBank(LREC 2016).
  - Jeong et al.(CoNLL 2015): 범위·유형은 ML, 값은 규칙 112개인 하이브리드. 값 F1 67.08.
  - **정영섭 외(2022)**: 상대 시간을 연·월·일·주·시·분·초별 `+N/−N/0` 속성으로 정규화했다(약 70%). **우리 슬롯 설계와 거의 같은 형태인 국내 동료심사 선례다.**
- **모호성 — 도구마다 해석이 다르다** [확인]

| 도구 | 표현 → 해석 |
|---|---|
| Looker | "3 days" = 오늘(부분) + 이전 2일 · "3 days ago for 3 days" = 완결 3일 · "last month" = 전월 전체 · 주 시작 월요일(변경 가능) |
| Power BI | "Last 2 Months"(7/20 기준) = 5/21~7/20 · "Last 2 Months (Calendar)" = 5/1~6/30 · UTC · 주는 일요일 시작 고정 |
| Tableau | "Last" 기간은 진행 중인 현재 단위를 통째로 포함 |
| Cube | "last 6 months" 현재 날짜 미포함 · "from 7 days ago to now" 포함 |
| dateparser 1.3 | "past month" = 30일 롤링 · "last week" = 직전 달력 주(월요일 시작) |
| Duckling KO | "지난 2달"(기준 2013-02-12) = [2012-12-01, 2013-02-01) — 완결 달력 월 2개 |

- **시사점**:
  - 슬롯에 **완결성(complete / rolling / to_date)**과 **기준 시각**을 명시한다.
  - 해석 정책은 코드 표로 고정하고 결과를 노출한다.
  - '반년'(롤링 6개월) 대 '상·하반기'(달력 반기), 연도 넘김은 어떤 문헌도 정하지 않는다 — **정책**으로 정한다.

### 1.2 LLM의 날짜 산술 신뢰성

- **ToT(ICLR 2025)**: arXiv v1 표 8 기준, Claude-3-Sonnet / GPT-4 / Gemini 1.5 Pro 순이다.
  - AddSubtract 58.6 / 76.3 / 71.1
  - Duration 15.0 / 16.0 / 13.5
  - 흔한 오류: 하루 차이 · 월 수 세기 방향 · 윤년
- **TempReason(ACL 2023)**: ChatGPT 연도 99.6 EM 대 월 단위 산술 30.5 EM.
- **PAL(ICML 2023)**: DATE 과제 CoT 64.8 → PAL 76.2(Python `relativedelta` 실행) · **PoT(TMLR 2023)**: 계산과 추론을 분리한다.
- **보조(프리프린트)**
  - PRIMETIME: 파싱·산술 원시 연산이 불안정하다.
  - Date Fragments: '20250312'처럼 붙은 날짜의 토큰 분절이 최대 10점 하락과 상관이 있다.
  - Overthinking: 장고형 모델이 단순 날짜 과제에서 5~20배 느리다.
- **시사점**: LLM에게 계산된 ISO 날짜를 받는 현행 2순위 폴백은 문헌상 가장 약한 지점이다. 폴백 출력을 **계산 전 슬롯**으로 바꾼다.
- **한계** [미확인]: 2025~26년 최신 모델의 단순 오프셋(지난달 등) 정확도를 직접 잰 벤치마크는 찾지 못했다.

### 1.3 LLM 기반 정규화

- **SCATE 코드 생성(NeurIPS 2025)**:
  - Claude 3.7: 코드 0.49 대 구간 직접 0.38. GPT-4.1 코드 0.51. 증강 학습 Qwen2.5-0.5B 0.59.
  - "LLM은 기호 표현, 실행기는 계산" 구조를 직접 지지한다.
  - 절대 정확도가 0.5 안팎이라 **추출 자체가 병목**이다.
- **법률 기한 프리프린트**: 오류는 산술이 아니라 "기간이 어느 사건에서 시작하는가"(= 기준 시각 슬롯)에서 났다.
- **Let Me Speak Freely?(EMNLP 2024 Industry)**: 형식 제약은 추론을 해치고 분류에는 도움이 된다 → 열거형(enum) 슬롯은 JSON 스키마로 강제하고, 같은 호출에서 날짜 계산은 시키지 않는다.
- **규칙과의 결합**:
  - Escribano et al.(프리프린트): 정규화할 수 있는 표현만 검출하는 편이 유리했다.
  - jaROTE(EMNLP 2026 Industry 채택 — arXiv comment 기준): 일본어에서 규칙 파이프라인이 LLM과 경쟁했다.
  - Jeong 2015: 한국어 하이브리드.
  - → "규칙 1순위, LLM은 규칙이 못 잡은 표현의 슬롯만"을 지지한다.
- **자기검증**: 시간 정규화에서 LLM 자기검증 효과를 잰 동료심사 연구는 찾지 못했다 [미확인]. 대안은 실행 가능성 검증(슬롯 → 구간 계산 성공)과 결정적 가드다.

### 1.4 text-to-SQL에서의 시간 처리

- **EHRSQL(NeurIPS 2022 D&B)**:
  - 시간 템플릿 분류 체계: 필터(global/within/exact) × 표현(절대/상대/혼합) × 단위 × 구간(in/since/until) × 옵션(first/last).
  - **현재 시각을 고정**("2105-12-31 23:59:00")해 상대 표현 평가를 재현 가능하게 했다.
  - 주의: 템플릿 SQL은 컬럼에 함수를 씌운다(SQLite 목적 — 인덱스 불가).
- **BIRD(NeurIPS 2023)**:
  - 값 설명 증거가 질문의 70.1%에 필요했다.
  - 증거 예시 "August of 2012 means … '201208'"은 우리 `stat_date` 'YYYYMM'과 같은 형식이다.
  - CHESS 오류 부록의 "Malformed Date": 같은 'YYYYMM' 텍스트 컬럼에 LLM이 `strftime`을 적용해 틀렸다 [확인].
- **Spider 2.0(ICLR 2025)**:
  - 방언 함수 오류 10.3%(DATE_TRUNC 등).
  - lite 판은 "year-to-date"를 명시형으로 재작성했다 — 정규 질문 재작성 사례다.
- **DIN-SQL · CHESS · MAC-SQL**: 전용 시간 정규화 단계가 없다(본문 키워드 검사 [확인]).
- **DART-SQL(Findings ACL 2024)**: 질문 재작성은 하지만 시간 전용은 아니다.
- **BIRD-Python(프리프린트)**: 필터 조건 오류가 최빈(SQL 32.7%)이고, 원인으로 "모호한 시간 지칭"을 명시했다.
- **슬롯 선확정 사례**: STRATOS(프리프린트 · SQL 생성 전 시공간 모호성 해소) · SPC(프리프린트 · 계획은 LLM, grain 낮추기와 SQL 조립은 코드 · 97.4% 대 55.3% · 교란 있음) · EHRSQL 템플릿.

### 1.5 시맨틱 레이어·BI (산업)

- **Cube**:
  - `timeDimensions{dimension, dateRange, granularity}`로 기간과 grain을 분리한다.
  - 상대 문자열은 Chrono로 파싱한다(영어 전용). AI API는 "raw SQL을 생성하지 않는다".
  - 사전 집계 매칭: 구간이 grain 경계에 안 맞으면 최대공약수 grain · 끝 포함 구간 · 타임존 일치.
- **dbt MetricFlow**:
  - time spine이 필요하다. `where`에 ISO 리터럴을 쓴다(상대 구문 없음).
  - CLI `--start-time`/`--end-time`은 끝 포함이고 pushdown된다.
  - dbt-mcp 이슈 #339: 에이전트가 UNIX 타임스탬프를 잘못된 날짜로 읽었다.
- **Tableau VizQL Data Service(Tableau MCP 질의 경로)**: `periodType` + `dateRangeType(LAST|CURRENT|NEXT|LASTN|NEXTN|TODATE)` + `rangeN` + `anchorDate`. **우리가 원하는 슬롯 스키마와 사실상 같다.**
- **Looker Studio**: 커넥터에 확정된 YYYY-MM-DD가 전달된다. 기본값은 "오늘 제외 최근 28일".
- **Power BI**: 기준을 Today / Last date(데이터 최신일) / First date 중 고른다.
- **dbt 2026 벤치마크**: 시맨틱 레이어 98.2~100% 대 text-to-SQL 84.1~90.0%. 시맨틱 레이어는 명시적 오류를 내고, text-to-SQL은 **그럴듯한 오답을 조용히** 낸다.
- **시사점**: "LLM은 구조화된 기간 토큰, 컴파일러는 SQL" 패턴이 업계에 확립돼 있다. 단 자연어 기간 문자열(Cube)은 한국어에 쓸 수 없다 → **열거형 토큰**(Tableau VDS 방식)이 적합하다.

### 1.6 성능 — sargable 조건과 grain

- **PostgreSQL 18**:
  - B-tree는 `< <= = >= >`에서 쓰인다. 컬럼 식에는 같은 식의 식 인덱스가 필요하다.
  - 제약 배제는 상수에서만 동작한다(CURRENT_TIMESTAMP 같은 비불변 함수는 최적화 불가). 파티션 가지치기는 실행 시점에도 가능하다.
  - STABLE 함수(current_timestamp 계열)는 인덱스 스캔 조건에 안전하다.
  - OVERLAPS는 반개구간 `start <= time < end`다. 월 더하기는 말일로 보정된다.
- **Db2 11.5**: 로컬 조건에서 컬럼 식을 피하고 역식을 쓴다(그렇지 않으면 start/stop 키 불가 · 선택도 부정확 · 비용 증가). 레이블 기간 문법(`+ 2 MONTHS`)은 말일로 보정한다. MQT 자동 라우팅이 있다.
- **grain**: 데이터 큐브 격자(Harinarayan et al., SIGMOD 1996) — 질의는 자기 grain 이하에서 롤업해야 답할 수 있다.
- [추정]:
  - 월 테이블로 "최근 30일"을 답하면 경계가 어긋나 grain·기간이 모두 오답이 된다.
  - 'YYYYMM' 텍스트 키는 폭이 같아 사전식 비교와 시간순이 일치하므로 `stat_date >= '202503' AND < '202509'`는 sargable하다.

### 1.7 평가

- TempEval-3: 검출 F1과 값 F1(= F1 × 값 정확도).
- Laparra et al.(TACL 2018): **시간 구간 단위 채점**.
- SemEval-2018 Task 6: 구간 F1 0.70(참가 1팀).
- EHRSQL: 기준 시각 고정 + EX + 응답 거부 지표.
- Zhong et al.(EMNLP 2020): 증류 테스트 스위트. exact-match 거짓 음성 평균 2.5% · 최악 8.1%.
- **권고** [추정]: 여러 기준 시각의 테스트 스위트 — 월말 · 1월(연 넘김) · 윤년 2월 · 주 경계 · 분기 경계 · 매월 1일 · 이번 달 미적재.

---

## 2. 설계 권고와 지지도

| # | 권고 | 지지도 | 근거 |
|---|---|---|---|
| R1 | **기준 시각(anchor)을 코드가 요청마다 고정**한다. `now`(KST)와 `data_latest`(최신 적재 `stat_date`) 중 무엇을 기준으로 할지 정책화 | 강 | TimeML DCT/anchorTimeID · EHRSQL current_time 고정 · Tableau anchorDate · Power BI Last date · Temporally Blind(Findings ACL 2026 — 타임스탬프를 줘도 정렬률 ≤65%) |
| R2 | **규칙 1순위, LLM은 미커버 표현의 슬롯만** 뽑는다. 슬롯은 아래 참고. JSON 스키마 + enum. **현 LLM ISO `time_range` 폴백을 슬롯 폴백으로 교체** | 중~강 | TempEval-3 · i2b2 · SCATE · PAL/PoT · EHRSQL 분류 · Tableau VDS · Let Me Speak Freely · 하이브리드 프리프린트 2건 |
| R3 | **결정적 해석기가 반개구간 `[start, end)`를 계산**한다. 완결성 · 주 시작 · 연 넘김(과거 우선) · '반년' 대 '반기'를 정책 표로 고정하고 해석 결과를 응답에 노출 | 계산 방식 강 / 어떤 해석이 옳은가 없음 | PG OVERLAPS · 도구 간 불일치(§1.1 표). 반례: Cube·MetricFlow는 끝 포함 방식이다 — 반개구간이 유일한 업계 표준은 아니다. 장점은 말일 계산 제거 [추정] |
| R4 | **grain = f(display_grain, 구간 경계 정렬)**. 월 테이블은 구간이 월 경계에 맞을 때만 쓴다 · 롤링 "최근 30일"은 일·시간 테이블 · "시간 단위"는 시간 테이블 강제 | 산업 강 · 학술 간접 | Cube 최대공약수 매칭 · 데이터 큐브 격자 · Power BI(부분 월 회피) |
| R5 | **SQL 조건은 리터럴 경계, 컬럼 무가공.** 가능하면 코드가 WHERE를 조립·주입하거나, 생성 후 **검증기가 시간 조건을 파싱해 해석 결과와 대조**한다. `CURRENT_DATE`·`NOW()`·`CURRENT DATE`·`INTERVAL`은 결정적으로 거부한다. 「기간 없음」 기본값도 코드 정책으로 두고 노출 | 강(DB 문서) / 중(컴파일 강제) | Db2 역식 가이드 · PG B-tree·제약 배제 · 방언 차이 · SPC·시맨틱 레이어 · Looker Studio 28일·Power BI Last date 선례 |
| R6 | **평가 2층**: (a) 해석기 — 구간 정확 일치 · 슬롯 정확도 (b) 파이프라인 — 기준 시각 고정 EX + 다중 기준 시각 스위트 + SQL 정적 검사(컬럼측 함수·DB 현재시각 함수 0건) | 중 | TempEval-3 · Laparra 2018 · EHRSQL · Zhong 2020 |

R2의 슬롯 필드:
- `relation`: last · this · next · ago · since · until · between · to_date · absolute
- `n`
- `unit`: hour · day · week · month · quarter · half · year
- `completeness`: complete · rolling · to_date
- `anchor`: now · data_latest · explicit
- 절대 시작·끝(Y, M, D, H)
- `display_grain`: hour · day · month · none

**반례·한계**
- SCATE 절대 정확도가 약 0.5다. 슬롯 추출 자체가 병목이므로, 모호하면 되묻거나 기본값을 적용하고 그 사실을 알린다.
- 컴파일 방식은 적용 범위(coverage) 밖에서 실패한다 → 사유를 밝히고 폴백한다(프로젝트의 "침묵적 폴백 금지"와 같다).
- SPC·dbt 비교는 통제 실험이 아니다(시맨틱 아티팩트 추가 효과가 섞였다).
- 모든 정량 근거는 영어 기반이다. 한국어로 옮겨도 성립한다는 것은 추정이다.

---

## 3. 자료 목록

### (A) 동료심사 문헌

| # | 문헌 | 게재 | 핵심 · 시사점 |
|---|---|---|---|
| 1 | Strötgen & Gertz, *HeidelTime* | SemEval-2010 · https://aclanthology.org/S10-1071/ | 규칙 기반 · 코드와 언어 리소스 분리 · TempEval-2 1위 → 한국어 규칙·사전을 코드와 분리하는 구조 |
| 2 | Strötgen & Gertz, *A Baseline Temporal Tagger for all Languages* | EMNLP 2015 · https://aclanthology.org/D15-1063/ | 200+ 언어 자동 리소스. 한국어는 `auto-korean`뿐 → 그대로 쓰기 어렵다 |
| 3 | Chang & Manning, *SUTime* | LREC 2012 · https://aclanthology.org/L12-1122/ | 결정적 규칙(TokensRegex) · 값 F1 67.38 |
| 4 | UzZaman et al., *TempEval-3* | SemEval-2013 · https://aclanthology.org/S13-2001/ | 정규화는 참가 전부 규칙 기반 — *"normalisation is currently (and perhaps inherently) done best by rule-engineered systems"* · 값 F1 지표 |
| 5 | Im et al., *KTimeML* | ALR7 2009 · https://aclanthology.org/W09-3417/ | 한국어 TimeML · 형태소 경계 처리 필수('지난달'·'3개월간') |
| 6 | Jeong et al., *Temporal Information Extraction from Korean Texts* | CoNLL 2015 · https://aclanthology.org/K15-1028/ | ML(범위·유형) + 규칙 112개(값) · 값 F1 67.08 · 수사 표현·음력·'초중반'이 난제 |
| 7 | Jeong et al., *Korean TimeML and Korean TimeBank* | LREC 2016 · https://aclanthology.org/L16-1055/ | 평가 데이터 후보(뉴스 도메인이라 질의 도메인과 다르다) |
| 8 | 정영섭 외, *상대적 시간정보의 규칙기반 정규화* | 한국컴퓨터정보학회논문지 27(12) 2022 · https://koreascience.or.kr/article/JAKO202203449917132.page | 연·월·일·주·시·분·초 상대 속성(+N/−N/0) · 약 70% — **슬롯 설계의 국내 선례** |
| 9 | Laparra et al., *From Characters to Time Intervals* | TACL 2018 · https://aclanthology.org/Q18-1025/ | 구간 단위 채점 |
| 10 | Laparra et al., *SemEval-2018 Task 6* | https://aclanthology.org/S18-1011/ | 조합형 정규화의 어려움(구간 F1 0.70) |
| 11 | Ding et al., *ARTime* | Findings EMNLP 2021(arXiv comment) · https://arxiv.org/abs/2108.13658 | 정규화 = 연산 시퀀스 → 슬롯 → 연산 구조 |
| 12 | Olex & McInnes, 상대 시간 표현 해소(임상) | Frontiers 2022 · https://pmc.ncbi.nlm.nih.gov/articles/PMC9638055/ | 값 오류의 69~85%가 상대 표현 |
| 13 | Su et al., *A Semantic Parsing Framework for End-to-End Time Normalization*(SCATE) | NeurIPS 2025 · https://arxiv.org/abs/2507.06450 | 코드 0.49 대 구간 직접 0.38 — **가장 직접적인 근거** |
| 14 | Su et al., Transformer 기반 시간 정보 추출 리뷰 | EMNLP 2025 · https://aclanthology.org/2025.emnlp-main.1467/ | 개관(본문 세부 미검토) |
| 15 | Tan et al., *TempReason* | ACL 2023 · https://arxiv.org/abs/2306.08952 | 연도 99.6 대 월 단위 30.5 EM — 월 통계 도메인 위험 |
| 16 | Fatemi et al., *Test of Time* | ICLR 2025 · https://proceedings.iclr.cc/paper_files/paper/2025/hash/eb7295a8bc613b375726659c2ecd6f14-Abstract-Conference.html | Duration 13.5~16% · 하루 오차 21~25% |
| 17 | Chu et al., *TimeBench* | ACL 2024 · https://arxiv.org/abs/2311.17667 | 사람과 격차 큼(간접) |
| 18 | Wang & Zhao, *TRAM* | Findings ACL 2024 · https://arxiv.org/abs/2310.00835 | 간접 |
| 19 | Wei et al., *TIME* | NeurIPS 2025(arXiv comment) · https://arxiv.org/abs/2505.12891 | 추론 모델도 한계(간접) |
| 20 | Chen et al., *TimeQA* | NeurIPS D&B 2021 · https://arxiv.org/abs/2108.06314 | 관련성 낮음 |
| 21 | Srivastava et al., *BIG-bench* | TMLR · https://arxiv.org/abs/2206.04615 | date_understanding |
| 22 | Gao et al., *PAL* | ICML 2023 · https://proceedings.mlr.press/v202/gao23f.html | DATE 64.8 → 76.2 |
| 23 | Chen et al., *Program of Thoughts* | TMLR 2023 · https://arxiv.org/abs/2211.12588 | 계산·추론 분리 |
| 24 | Tam et al., *Let Me Speak Freely?* | EMNLP 2024 Industry · https://aclanthology.org/2024.emnlp-industry.91/ | enum 슬롯은 JSON · 계산은 분리 |
| 25 | Cheng et al., *Your LLM Agents are Temporally Blind* | Findings ACL 2026(arXiv comment) · https://arxiv.org/abs/2510.23853 | 기준 시각은 코드가 주입 |
| 26 | Piryani et al., *It's High Time* | ACL 2026(jref) · https://arxiv.org/abs/2505.20243 | 시간 QA 서베이 |
| 27 | Lee et al., *EHRSQL* | NeurIPS D&B 2022 · https://arxiv.org/abs/2301.07695 | 시간 템플릿 분류 · 고정 기준 시각 평가 |
| 28 | Lee et al., *EHRSQL 2024 Shared Task* | ClinicalNLP@NAACL 2024 · https://aclanthology.org/2024.clinicalnlp-1.62/ | 해석 불가 시 거부·되묻기 |
| 29 | Li et al., *BIRD* | NeurIPS 2023 · https://arxiv.org/abs/2305.03111 | 'YYYYMM' 형식 지식은 주입 대상 |
| 30 | Lei et al., *Spider 2.0* | ICLR 2025 · https://arxiv.org/abs/2411.07763 | DB 날짜 함수는 LLM 오류원 → 리터럴 |
| 31 | Pourreza & Rafiei, *DIN-SQL* | NeurIPS 2023 · https://arxiv.org/abs/2304.11015 | 전용 시간 단계 없음 |
| 32 | Wang et al., *MAC-SQL* | COLING 2025 · https://arxiv.org/abs/2312.11242 | 값 예시 주입은 유효하나 불충분 |
| 33 | Mao et al., *DART-SQL* | Findings ACL 2024 · https://aclanthology.org/2024.findings-acl.120/ | 해석된 기간을 정규 문장으로 재작성하는 보조 수단 |
| 34 | Zhong et al., *Distilled Test Suites* | EMNLP 2020 · https://aclanthology.org/2020.emnlp-main.29/ | 다중 기준 시각 스위트의 방법론 근거 |
| 35 | Harinarayan et al., *Implementing Data Cubes Efficiently* | SIGMOD 1996 · https://dl.acm.org/doi/10.1145/235968.233333 | grain 격자 |
| 36 | Yasuda & Ishihara, *jaROTE* | EMNLP 2026 Industry(arXiv comment · 논문집 미확인) · https://arxiv.org/abs/2609.09569 | 교착어에서도 규칙 우선 |
| 37 | Tan et al., *Sonar-TS* | ICML 2026(arXiv comment) · https://arxiv.org/abs/2602.17001 | 주변 참고 |

### (B) 프리프린트 (동료심사 미확인)

| # | 문헌 | 핵심 · 시사점 |
|---|---|---|
| 1 | Escribano et al. 2023 · https://arxiv.org/abs/2304.14221 | 정규화 가능한 표현만 검출이 유리 → 미해석 표현은 되묻기·폴백 |
| 2 | Laufer et al. 2026 · https://arxiv.org/abs/2605.26560 | 결정적 날짜 산술 Pair F1 0.997 대 GPT-4o-mini 0.53(합성 코퍼스) |
| 3 | Zhyrko et al. 2026 · https://arxiv.org/abs/2608.15270 | 달력 엔진 90.2% 대 직접 61.2% · 오류는 기준 사건 선택 |
| 4 | Gaere & Wangenheim 2025 *PRIMETIME* · https://arxiv.org/abs/2504.16155 | 원시 연산 불안정 → 코드로 |
| 5 | Bhatia et al. 2025 *Date Fragments* · https://arxiv.org/abs/2505.16088 | 'YYYYMM'을 LLM 입출력에 그대로 싣지 말 것 · 표시는 사람이 읽는 형식 |
| 6 | Bhatia et al. 2024 *DateLogicQA* · https://arxiv.org/abs/2412.13377 | 보조 |
| 7 | Zhang et al. 2025 · https://arxiv.org/abs/2510.07880 | 장고형 모델 5~20배 지연 → 응답시간 목표상 코드 계산 유리 |
| 8 | Hu et al. 2026 *BIRD-Python* · https://arxiv.org/abs/2601.15728 | 시간 필터가 오류 1순위(32.7%) |
| 9 | Zhang et al. 2026 *STRATOS* · https://arxiv.org/abs/2607.03501 | 슬롯 선확정 선례 |
| 10 | Yi Ai 2026 *SPC* · https://arxiv.org/abs/2608.16663 | grain 낮추기·SQL 조립을 코드로 · 97.4% 대 55.3%(38문항 · 인과 주장 아님) |
| 11 | Rumiantsau & Fokeev 2026 · https://arxiv.org/abs/2604.25149 | 시맨틱 문서 주입 +17~23%p(강제보다 약함 [추정]) |
| 12 | Hertzberg et al. 2026 *QUEST* · https://arxiv.org/abs/2605.05525 | 헬스케어 질의의 80.4%에 시간 차원 |

### (C) 산업·공식 문서 (접근일 2026-09-29 · 원문 확인)

- **표준**
  - TimeML 1.2(https://www.cs.brandeis.edu/~cs112/cs112-2004/annPS/TimeML12wp.htm)
  - ISO 24617-1:2012(존재만 확인 · 본문 미열람)
- **한국어 도구**
  - Duckling KO(https://github.com/facebook/duckling/tree/main/Duckling/Time/KO)
    - "지난 2달" → [2012-12-01, 2013-02-01) · "지난 2주" → 월요일 시작 · "지난 24시간" → 시간 grain
    - 월 grain 정규식은 '달'뿐이고, '개월'·'최근'·'반기'가 없다 → 부분 참고
  - Recognizers-Text(KO DateTime Specs-only)
  - dateparser(`ko.py` '지난달' = 한 시점 · 1.3.0 `RETURN_TIME_SPAN` 영어 전용 · `RELATIVE_BASE`·`PREFER_DATES_FROM` 개념은 참고할 만함)
  - Chrono(한국어 없음) · HeidelTime(`auto-korean`뿐)
- **시맨틱 레이어**
  - Cube
    - 질의 형식: https://docs.cube.dev/reference/core-data-apis/rest-api/query-format
    - **사전 집계 매칭 — grain 선택 규칙의 산업 근거**: https://docs.cube.dev/docs/pre-aggregations/matching-pre-aggregations
    - AI API "does not generate raw SQL"
  - dbt MetricFlow(time spine · `--start-time`/`--end-time` 끝 포함 · JDBC where) · dbt 블로그 2026-04-07(시맨틱 98~100% 대 84~90% · 명시적 오류 대 조용한 오답) · dbt-mcp #339
- **BI 도구**
  - Looker 필터 표현식(부분·완결 차이 · 월요일 시작 기본) · Looker Studio 커넥터(기본 최근 28일 · 오늘 제외)
  - Power BI 상대 날짜(Months 대 Months(Calendar) · UTC · 일요일 시작 · Last date 기준)
  - Tableau 상대 날짜("Last"는 현재 단위 포함) · **VizQL Data Service `periodType`/`dateRangeType`/`rangeN`/`anchorDate` — 슬롯 스키마 선례**
- **DB 공식 문서**
  - PostgreSQL 18: 인덱스 유형 · 식 인덱스 · 파티셔닝(제약 배제 · 실행 시점 가지치기) · 함수 변동성(STABLE 인덱스 조건 허용) · 날짜 함수(말일 보정 · OVERLAPS 반개구간)
  - Db2 11.5: 컬럼 식 회피(https://www.ibm.com/docs/en/db2/11.5.x?topic=statements-avoiding-expressions-over-columns-in-local-predicates) · 레이블 기간과 말일 보정 · MQT
- **부정 확인**(시간 처리 언급 없음): Google Cloud "Techniques for improving text-to-SQL"(2025-05-17) · Snowflake Cortex Analyst 개요

---

## 4. 공백 — 동료심사 문헌을 찾지 못한 주제

- 한국어 상대 시간 표현 + text-to-SQL 결합(한국어 text-to-SQL 연구는 있으나 시간 표현을 다룬 것은 0건).
- 사용자 질의 도메인의 시간 정규화 벤치마크 — EHRSQL(영어 · 템플릿)뿐. 인프라 모니터링 도메인은 없다.
- 「슬롯 추출 후 코드 계산」 대 「LLM이 SQL 날짜식 직접 생성」을 EX로 비교한 text-to-SQL 실험.
- 롤업 테이블(grain) 자동 선택과 LLM text-to-SQL 정확도.
- 한국어 사용자 해석 선호('지난 주' 대 '최근 7일' · '이번 달' 부분 포함 · '반년' · 연 넘김 · 주 시작 요일)에 대한 언어학·HCI 조사.
- 최신 LLM(2025~26)의 단순 상대 날짜 해석 정확도를 직접 잰 벤치마크.

→ 공백 자체가 보수적 설계의 근거다. 해석 정책은 사람이 확정하고, 해석 결과를 사용자에게 노출하며, 자체 골드셋으로 측정한다.

## 5. 미확인

- Test of Time 수치는 arXiv v1 표 8 기준이다(ICLR 최종본 미대조).
- Duckling KO의 '개월'·'최근'·'반기' 미지원은 소스 검색 기반 추정이다(실행 미검증).
- Cube Chrono 해석의 정확한 구간은 실행 미검증이다.
- Tableau "Anchor relative to" UI 문구는 미확인이다(VDS `anchorDate`만 확인).
- Db2 데이터 파티션 제거 문서와 `CURRENT DATE` 최적화 시점은 미확인이다.
- 정영섭 2022의 단위별 정확도는 초록 외 미확인이다(약 70%만 인용).
- ISO 24617-1 본문은 유료라 미열람이다.
- 2026년 채택 표기(Findings ACL 2026 · EMNLP 2026 Industry · ICML 2026 · NeurIPS 2025 TIME)는 arXiv comment 기준이다. SCATE(NeurIPS 2025)만 neurips.cc에서 확인했다.
- R1~R6 가운데 문헌 없이 추론한 부분은 추정이다 — 다중 기준 시각 스위트 · 'YYYYMM' 사전식 비교 · `data_latest` 기준 시각 · 연 넘김 과거 우선.
