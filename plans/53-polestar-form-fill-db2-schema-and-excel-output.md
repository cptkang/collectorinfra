# 53. 파일 업로드 양식 채우기 — 폴스타 DB2 스키마 오류 · 공동존 서버 식별자 NULL · Excel 산출물 누락 분석 및 개선

> 작성일: 2026-07-01
> 상위/관련 계획: `plans/19-excel-csv-llm-pipeline.md`, `plans/23-ui-progress-and-excel-fix.md`, `plans/35-excel-empty-data-fix.md`, `plans/52-polestar-b0-token-overflow-and-replan-misdiagnosis.md`
> 관련 결정: D-046(hostname graceful 폴백), D-050(EAV 피벗 HAVING — null은 데이터 부재가 아니라 SQL 오류일 수 있음), D-051(DB2 방언 FETCH FIRST), D-053(b0 db_id 라우팅·엔진별 방언·isolated 승격), D-047/2026-06-26(진단 메시지를 일반 문구로 덮지 말 것)
> 신규 결정(본 계획에서 부여): **D-057**(멀티DB SQL 생성의 엔진·스키마 인지 — b0/DB2 스키마 한정), **D-058**(공동존 서버 식별자 NULL 대응 — `COALESCE(name, hostname)` + 생성 SQL 우선 진단), **D-059**(폼필 실패 시 침묵적 CSV 강등 금지 — 사유 노출)
> ※ 번호 정정 규칙(Known Mistakes 2026-06-25): `grep -oE "D-0[0-9]{2}" docs/02_decision.md` 최댓값 **D-056** 확인 → 다음 빈 번호 D-057/D-058/D-059 부여. 구현 시 `docs/02_decision.md`에 정식 등재한다.

---

## 0.1 [실측 확정 2026-07-03] 증상 C 진짜 원인 — 오케스트레이션 경로에서 `uploaded_file` 누락

라이브에서 "데이터는 잘 끌어오는데 CSV로만 제공"되고 `output_generator`가
"양식 구조 또는 원본 파일이 없어 파일 생성 불가"를 남기는 것이 확인됨. 실측 원인:

- 오케스트레이션 경로의 두 상태 인계 지점(`subagents._make_isolated_input`,
  `result_aggregator._build_output_generator_state`)이 `template_structure`·`file_type`·`csv_sheet_data`는
  전파하면서 **`uploaded_file`(원본 파일 바이너리)만 빠뜨림** → output_generator의
  `if not template or not uploaded_file` 조건에서 uploaded_file이 None → CSV 강등.
- **비대칭 전파(D-053 계열)**: 양식 "구조"는 넘어가서 칼럼 파싱은 됐는데, 실제로 채울 "원본 파일"이 없음.
- **수정**: 두 지점에 `uploaded_file` 추가. output_generator 진단을 어느 필드가 없는지(template vs uploaded_file)
  명시하도록 강화. 회귀 테스트 `test_subagents.py::test_isolated_input_propagates_form_fill_fields`.
- **주의(배포)**: 이 오케스트레이션 코드는 `main`에 없고 `multiintent` 전용. 폐쇄망 런타임이 main+multiintent
  파일 혼합이면 `subagents.py`·`result_aggregator.py`·`output_generator.py`를 **함께** 반영해야 함.

---

## 0.2 [실측 확정 2026-07-08] 은행존(b0) 서버명·호스트명·IP 구분 (D-061)

라이브 폼필에서 "서버 이름"·"호스트네임"이 둘 다 등록명으로 출력됨. `b0_query.py` 실측 결과:
- `name`=등록명(대개 `"<호스트명> (<설명>)"`, 공백 유무 무관, **일부는 name==hostname**),
  `hostname`·`ipaddress` 직접 컬럼=클린 값.
- **수정(`polestar_b0.yaml` 단일 파일)**: EAV Hostname synonyms에서 "서버명" 제거, `column_synonyms`(서버명→name,
  호스트명→hostname) 추가, 가이드/예시에 name(등록명) vs hostname(직접) vs ipaddress(직접) 구분 시연.
- **호스트명은 name 파싱 금지·직접 컬럼 사용**(name==hostname 서버 + 구조 불규칙). name==hostname 서버는
  두 칼럼 동일값이 정상(회귀 아님). column_synonyms는 LLM·D-038 빌더 경로 모두 적용. 상세: D-061.

---

## 0. 구현 반영 현황 및 방향성 확정 (2026-07-02, 사용자 검토 반영)

### D-057 — 스키마 해소 방식 우선순위 (확정)

b0 접속 정보(폐쇄망 테스트): `POLESTAR_B0_CONNECTION=DATABASE=DSNISA;HOSTNAME=10.37.26.51;PORT=26500;PROTOCOL=TCPIP;UID=SDQ000;PWD=###;`
실측: `SYSCAT.TABLES.TABSCHEMA='POLESTAR'`, `CURRENT SCHEMA=SDQ000`(=연결 UID).

1. **[최선·운영, 본 리포 밖]** mcp_server `.env`의 연결 문자열에 **`CURRENTSCHEMA=POLESTAR` 추가**
   (`...;UID=SDQ000;PWD=###;CURRENTSCHEMA=POLESTAR;`). IBM CLI Driver가 접속 시 CURRENT SCHEMA를
   POLESTAR로 고정 → **무스키마 참조가 전 경로에서 자동 해소**(LLM SQL·hostname 리졸버·`polestar_history`·alarm·
   향후 코드 전부). 앱은 물리 스키마명과 분리(schema-agnostic) 유지. **← 진짜 근본. 운영 조치로 반영 권고.**
2. **[플랜 B]** 앱 세션 앞단에서 `SET CURRENT SCHEMA POLESTAR` 실행 — **실현성 주의**: 우리 앱은 DBHub(읽기전용)
   `execute_sql`만 쓰고 `_validate_sql_simple`이 비-SELECT를 차단하므로 SET 문은 그대로는 막힘 → 연결 초기화
   단계에 넣어야 하며 이는 사실상 1과 수렴. 1을 못 할 때의 대안.
3. **[인-리포 안전망·현재 구현됨]** `domain_config.db_schema=POLESTAR`로 명시 한정(`POLESTAR.cmm_resource`).
   즉시 동작하고 gp/yd(`polestar.`)와 일관. 1이 적용되면 중복이나 **무해**(명시 한정은 CURRENT SCHEMA와 무관하게
   항상 성립). 단 앱이 물리 스키마명을 알게 되고, 배선한 경로(multi_db_executor·리졸버)만 커버.

**권고**: 1을 운영 반영(모든 경로 커버) + 3은 그때까지의 안전망·자기문서화(생성 SQL에 스키마 노출)로 유지.
1 확인 후 앱을 완전 schema-agnostic으로 단순화(3 제거)할지는 선택. **지금 추가 코드 변경 불필요.**

### D-058 — 순서 조정 (사용자 지적 반영: "057 먼저")

yd `name` NULL의 H1(피벗 SQL 오류)/H2(데이터 미채움) 판별은 **파이프라인이 정상 동작해 실제 생성 SQL과
데이터를 관찰할 수 있어야** 정확하다(D-050 원칙: 추정 말고 생성 SQL 먼저). 따라서 D-058은 **잠정(graceful
degradation)** 으로 격하한다:
- `COALESCE(name, hostname)`는 H1·H2 양쪽에서 안전하므로 **임시 유지**하되 **확정 수정 아님**.
- **057 해소 후** 처리현황(D-039)의 생성 SQL + `COUNT(*) FILTER(WHERE name IS NOT NULL)`로 H1/H2 확정 →
  H1이면 피벗 SQL 교정(COALESCE로 덮지 말 것), H2면 COALESCE 확정.

### 구현 완료 (스키마 값과 무관한 부분)

- **D-057 메커니즘**: `src/routing/domain_config.py`(db_schema 필드), `src/routing/db_schema.py`(신규 헬퍼),
  `src/nodes/multi_db_executor.py`(`_generate_sql(db_id)`+규칙 주입), `polestar_hostname_resolver.py`(`_table` db_schema 우선),
  `config/db_profiles/polestar_b0.yaml`(예시 `POLESTAR.` 한정). ← 안전망(3)으로 유지.
- **D-059**: `src/nodes/output_generator.py`(실패 시 `{"reason":...}` 반환·사유 노출). ← 확정.
- **테스트**: `tests/test_routing/test_db_schema.py`(신규), `test_process_hostname_resolve.py`·`test_output_generator.py` 갱신. arch_check exit 0.
- `docs/02_decision.md`에 D-057/D-058/D-059 등재.

---

## 0.5 구현 단계 (Phased Rollout)

> **게이트 원칙**: 각 단계는 앞 단계의 **검증 통과** 후에만 다음으로 진행한다. 추정으로 다음 단계에 착수하지
> 않는다(Known Mistakes 2026-06-30: "진입·게이트별로 어디서 끊기는지부터 확정" / D-050: "생성 SQL 먼저").
> 각 단계 검증이 실패하면 해당 단계 안에서 대안(폴백)을 적용하고 재검증한 뒤에만 넘어간다.

### Phase 0 — 선(先)적용 완료분 (독립적, 다음 단계를 블록하지 않음)

| 항목 | 상태 |
|------|------|
| D-059 폼필 실패 사유 노출 (`output_generator`) | ✅ 구현·테스트 완료 |
| D-057 인-리포 안전망: 명시 한정 메커니즘(`db_schema`/`db_schema.py`/규칙 주입/리졸버) | ✅ 구현·테스트 완료 |
| D-058 잠정 폴백: `COALESCE(name, hostname)` 예시 (무해, 확정 아님) | ✅ 임시 반영 |

→ 이 세 가지는 이미 반영됐고 값(스키마·데이터 원인)과 무관하므로 Phase 1/2를 기다리지 않는다.

### Phase 1 — D-057 스키마 해소 확정 (최우선, 다른 진단의 전제)

**목표**: b0 양식 채우기가 `SQL0204N` 없이 실행되어 행을 반환한다.

1. **조치 1-a [운영, 최선]**: mcp_server `.env`의 `POLESTAR_B0_CONNECTION` 끝에 `CURRENTSCHEMA=POLESTAR;` 추가
   (`...;UID=SDQ000;PWD=###;CURRENTSCHEMA=POLESTAR;`). → 전 경로 무스키마 참조 자동 해소.
2. **조치 1-b [이미 됨]**: 앱 명시 한정(안전망)이 함께 동작(중복이나 무해).

> **✅ 검증 게이트 1** (통과해야 Phase 2 진행):
> - b0 폼필 재실행 → `SQL0204N` 소멸, 행(row) 반환 확인.
> - 처리현황(D-039)의 **생성 SQL**에서 `cmm_resource`가 `POLESTAR` 스키마로 해소되는지 확인.
> - **실패 시 대안**: 조치 1-a 불가면 플랜 B(연결 초기화 단계 `SET CURRENT SCHEMA POLESTAR`; DBHub 읽기전용
>   차단 여부 확인)로 전환하거나, 앱 명시 한정(1-b)만으로 재확인 후 게이트 재판정.

### Phase 2 — D-058 yd 서버 식별자 원인 진단 및 확정 (게이트 1 통과 후)

**전제**: Phase 1로 파이프라인이 정상 동작해 실제 생성 SQL·데이터를 관찰할 수 있어야 진단이 정확하다.

1. **진단 2-a**: yd 폼필 실행 → 처리현황의 **생성 SQL** 확보 + 아래 카운트 실행
   `SELECT COUNT(*) FILTER (WHERE name IS NOT NULL) AS with_name, COUNT(*) AS total
    FROM polestar.cmm_resource WHERE resource_type='server.Server' AND dtime IS NULL;`
2. **분기 확정**:
   - **H1 (SQL 구성 오류)**: 피벗이 엉뚱한 행에서 name 집계 / WHERE로 server.Server 행이 GROUP BY 전 제거 등
     → 피벗·HAVING 교정(D-050 패턴). **COALESCE로 덮지 말고 SQL을 고친다.**
   - **H2 (데이터 미채움)**: server.Server 행의 name이 실제 NULL → **`COALESCE(name, hostname)` 확정**(Phase 0 임시 반영분 승격).

> **✅ 검증 게이트 2** (통과해야 Phase 3 진행):
> - yd 폼필에서 **서버명 컬럼이 채워지고** 개별 서버가 식별됨.
> - 채워진 Excel 산출물이 정상 다운로드됨(CSV만 남는 강등 아님).

### Phase 3 — 산출물·회귀 최종 확인

- b0·yd 각 케이스에서 **채워진 Excel이 실제 생성·다운로드**되는지 확인.
  채울 수 없는 경우에만 D-059 사유 메시지 + CSV로 강등되는지 확인.
- 엔진별(DB2 b0 / PostgreSQL gp·yd) 폼필 회귀 테스트 고정
  (`tests/test_excel_fill_pipeline.py`·`test_query_to_excel_mapping.py` 확장, DB2 방언·스키마 스냅샷).
- 확정된 D-058 결과를 `docs/02_decision.md` D-058 상태(잠정→확정)와 본 문서에 반영.

### 요약 (담당·게이트)

| Phase | 조치 | 담당 | 게이트(다음 진행 조건) |
|-------|------|------|------------------------|
| 0 | D-059·D-057 안전망·D-058 잠정 폴백 | 개발 | (완료, 블록 없음) |
| 1 | `CURRENTSCHEMA=POLESTAR` 연결 반영 | 운영 | b0 폼필 `SQL0204N` 소멸·행 반환 |
| 2 | yd H1/H2 진단 후 확정 수정 | 개발 | yd 서버명 채워짐·서버 식별 가능 |
| 3 | Excel 산출물·엔진별 회귀 확인 | 개발/검증 | b0·yd Excel 정상 생성 |

---

## 1. 배경 — 보고된 3가지 증상

기존 기능(파일 업로드 → 업로드한 양식(Excel/Word) 채우기)을 폴스타 대상으로 사용하던 중 다음이 발견됨.

| # | 대상 | 증상 |
|---|------|------|
| **A** | 은행존(`polestar_b0`, DB2) | 양식 채우기 중 실행 에러:<br>`src.nodes.multi_db_executor: DB 'polestar_b0' 실행 에러: [IBM][CLI Driver][DB2/LINUXX8664] SQL0204N "SDQ000.CMM_RESOURCE" is an undefined name. SQLSTATE=42704 SQLCODE=-204` |
| **B** | 여의도(`polestar_cm_yd`, PostgreSQL) | 데이터는 조회되나 `cmm_resource.name`(서버명)이 **전부 NULL** → 개별 서버를 식별할 수 없음 |
| **C** | 위 A·B 케이스 공통 | 조회 데이터의 **CSV 다운로드만** 제공되고, 실제 **업로드한 Excel 양식에 채워진 결과물**을 받을 수 없음 |

---

## 2. 실행 경로(코드 흐름)

파일 업로드 양식 채우기는 **표준 그래프가 아닌 멀티DB(시멘틱 라우팅) 경로**를 탄다. `polestar_b0` 등 폴스타 다중 DB가 대상이면:

```
/query/file (또는 /query/file/stream)              # src/api/routes/query.py
  → graph.ainvoke(initial_state)                   # uploaded_file, file_type, csv_sheet_data 주입
    context_resolver → input_parser → field_mapper
      → semantic_router
        → multi_db_executor  ← [증상 A·B 발생 지점] # DB별 스키마분석→SQL생성→검증→실행
          → result_merger → result_organizer        # ← [증상 B·C 게이트]
            → output_generator                       # ← [증상 C 발생 지점] Excel/Word 채우기
              → END
```

핵심 파일:
- `src/nodes/multi_db_executor.py` — DB별 `_generate_sql()`(LLM) → `execute_sql()`
- `src/nodes/result_organizer.py` — 데이터 충분성 판단, `column_mapping`/`resolved_mapping` 생성
- `src/nodes/output_generator.py::_generate_document_file()` — 실제 Excel/Word 파일 생성
- `src/document/excel_writer.py::fill_excel_template()` — openpyxl 채우기
- `src/alarm/infrastructure/polestar_hostname_resolver.py::_table()/build_hostname_sql()` — **이미 엔진·스키마 인지가 구현된** 참조 구현(D-051/D-053)
- `src/static/js/app.js` — 다운로드 링크 렌더링(`has_file`/`row_count` 기반)

---

## 3. 증상별 근본 원인 분석

### 3.1 증상 A — b0(DB2) `SDQ000.CMM_RESOURCE is an undefined name` [확정]

**직접 원인**: `multi_db_executor._generate_sql()`(`multi_db_executor.py:273-472`)가 LLM으로 SQL을 생성할 때 `db_engine`을 **텍스트 힌트로만** 전달한다:

```python
db_engine_hint = f"현재 대상 DB 엔진: **{db_engine.upper()}** — 이 엔진의 SQL 문법을 사용하세요."
```

스키마 한정(qualification)은 **강제하지 않는다**. 그 결과 LLM이 `cmm_resource`를 **무스키마(unqualified)**로 생성 → DB2가 이를 `CURRENT SCHEMA`(연결 계정 `SDQ000`)로 해소 → `SDQ000.CMM_RESOURCE`는 실재하지 않아 `SQL0204N (-204)`.

**근본 원인(구조적)**: 엔진·스키마 인지 SQL 조립은 **`polestar_hostname_resolver.build_hostname_sql()` 한 곳에만** 존재한다(D-051/D-053, `_table()`):

```python
def _table(db_id, name, db_engine="postgresql"):
    if db_engine == "db2":
        return name                    # DB2 = CURRENT SCHEMA (무스키마)
    schema = _SCHEMA_BY_DB_ID.get(db_id, _DEFAULT_SCHEMA)  # PG = 'polestar.'
    return f"{schema}.{name}"
```

**LLM 기반 일반 SQL 생성 경로(`multi_db_executor`/`query_generator`)에는 이 인지가 이식되지 않았다.** 즉 실시간 프로세스/알람 경로(고정 SQL)만 b0 방언이 적용되고, **양식 채우기(LLM 생성)는 미적용**. 이는 D-053(2026-06-30) 교훈의 재발이다 — *"한 경로만 검증하면 다른 엔진/다른 경로 회귀를 놓친다."*

**추가 모순**: b0 프로필(`config/db_profiles/polestar_b0.yaml`)의 `query_examples`는 PostgreSQL식 `polestar.cmm_resource` 접두사를 쓰면서 동시에 DB2식 `FETCH FIRST n ROWS ONLY`를 쓴다. 방언이 **혼재**되어 있어 LLM에게 일관된 신호를 주지 못한다. (Plan 52 §1 task 3에서도 동일 `SDQ000.CMM_RESOURCE` 에러가 관측되었고 당시 "환각 테이블"로 기록되었으나, 실제로는 **무스키마 → CURRENT SCHEMA 해소 실패**가 정체다.)

**미확정 핵심(반드시 라이브 확인)**: b0의 실제 폴스타 테이블이 **어느 DB2 스키마**에 있는가?
- `SDQ000`(CURRENT SCHEMA = 연결 계정)에는 없음이 에러로 확정됨.
- `polestar_hostname_resolver._table()`은 "DB2는 CURRENT SCHEMA로 해소되면 된다"고 **가정**하나, 이 가정 자체가 b0에서 검증된 적 없음(D-053 시점에도 1차 원인은 db_id 라우팅 누락이었고, 방언 분기는 "그 뒤 단계라 실행 기회조차 없었음"으로 기록). → **연결 계정과 테이블 소유 스키마가 다르면** 무스키마 참조는 b0에서 근본적으로 실패한다.
- 조치 전 반드시 실측: `SELECT CURRENT SCHEMA FROM SYSIBM.SYSDUMMY1;` 및 `SELECT tabschema, tabname FROM SYSCAT.TABLES WHERE tabname='CMM_RESOURCE';` 로 **정확한 스키마 S**를 확정한다(추정 금지 — Known Mistakes 2026-06-30).

### 3.2 증상 B — yd `cmm_resource.name` 전부 NULL [원인 2갈래·SQL 우선 확인 필요]

yd 프로필(`polestar_cm_yd.yaml`)의 **★ 공동존 전용 규칙**은 "서버명/장비명 = `cmm_resource.name`(기본 식별자), hostname은 명시 시에만"으로 강제한다(`:168-189`). 따라서 field_mapper가 양식의 "서버명"을 `cmm_resource.name`으로 매핑하고, 생성 SQL이 `r.name`(또는 `MAX(CASE WHEN resource_type='server.Server' THEN c.name END)`)을 서버 식별 키로 사용한다. 이 값이 전부 NULL이면 개별 서버를 구분할 수 없다.

원인은 두 갈래이며, **먼저 생성된 SQL을 확인**해야 한다(D-050 원칙: *"필드 null은 데이터 부재가 아니라 조회 SQL 오류일 수 있다 — null 진단 시 먼저 생성 SQL을 확인"*):

- **H1(SQL 구성 오류·D-050류)**: 피벗에서 `name`을 잘못된 `resource_type` 행에서 집계하거나, 단일 서버 필터를 `WHERE c.name=...`로 걸어 `server.Server` 행이 GROUP BY 전에 제거 → `MAX(CASE WHEN resource_type='server.Server' THEN c.name END)` 이 NULL. yd `query_examples`에는 HAVING 패턴이 이미 있으나(`:351-375`), LLM이 양식 채우기(다수 서버·다수 컬럼) 상황에서 이를 따르지 않았을 수 있음.
- **H2(데이터 실측)**: yd는 개발/스테이징 환경이라 `server.Server` 행의 `name` 컬럼이 실제로 미채워져 있고 `hostname`만 채워져 있을 수 있음. 이 경우 yd 프로필의 "**기본값 = name**" 전제가 이 환경에서 성립하지 않는다.

**확인 절차(추정 금지)**:
1. 실행된 생성 SQL 로그 확인(D-039 처리현황/`query_attempts`).
2. 진단 카운트: `SELECT COUNT(*) FILTER (WHERE name IS NOT NULL) AS with_name, COUNT(*) FILTER (WHERE hostname IS NOT NULL) AS with_host, COUNT(*) AS total FROM polestar.cmm_resource WHERE resource_type='server.Server' AND dtime IS NULL;`
3. H1이면 `query_generator`/yd 예시의 HAVING·피벗 규칙을 강화, H2면 아래 3.2 개선안(식별자 폴백)을 적용.

### 3.3 증상 C — CSV만 제공되고 채워진 Excel이 없음 [확정 — 침묵적 강등]

**직접 원인**: `output_generator._generate_document_file()`(`output_generator.py:339-428`)이 **`None`을 반환**하면 `output_file`이 세팅되지 않고, API 응답 `has_file = (output_file is not None) = False`가 되어, `app.js`가 Excel 다운로드 링크를 **감추고** CSV 링크만 노출한다(CSV는 `row_count>0`이면 항상 노출).

`_generate_document_file()`이 `None`을 반환하는 분기(각 분기에 `logger.warning`은 있으나 **사용자에게는 전달 안 됨**):
1. `template` 또는 `uploaded_file` 없음(`:362-364`).
2. `effective_mapping`(= `resolved_mapping or column_mapping`) 없음(`:366-368`).
3. `fill_excel_template()` 예외(`:425-428`).

**증상 A·B와의 연결**:
- **A(b0)**: b0가 유일 대상이면 `multi_db_executor`에서 실행 실패 → `db_results` 비어 `query_results=[]`. `result_organizer._check_data_sufficiency`가 "부족"으로 판정하면 `column_mapping=None, resolved_mapping=None`(`result_organizer.py:71-83`) → `effective_mapping=None` → **분기 2로 `None`** → Excel 미생성. (rows=0이라 CSV도 비지만, 링크 노출 로직상 Excel은 사라짐.)
- **B(yd)**: rows는 있으나 식별자 NULL로 데이터 충분성/매핑 해석이 불완전해지면 `resolved_mapping`이 비고, `column_mapping`도 조건에 따라 `None`이 되어 **분기 2로 `None`** → Excel 미생성, CSV만 남음.

**근본 원인(설계)**: 양식 채우기가 불가능해졌을 때 시스템이 **사유를 노출하지 않고 조용히 CSV로 강등**한다. 사용자는 "왜 Excel이 안 나오는지" 알 수 없다. 이는 Known Mistakes 2026-06-26(결정적 진단 메시지를 일반 빈-결과 문구로 덮지 말 것)과 동일한 "실패 원인 은닉" 안티패턴이다.

---

## 4. 개선 방안

### 4.1 [D-057] 멀티DB SQL 생성의 엔진·스키마 인지 — b0/DB2 스키마 한정 (증상 A)

- **(선결) 실제 DB2 스키마 확정**: §3.1 실측 쿼리로 b0 폴스타 테이블의 소유 스키마 `S`를 확인.
- **연결 계정과 스키마가 다르면**: 다음 중 택1로 **결정적 한정**을 적용(LLM 자율 판단에 맡기지 않음).
  - (a) 연결 시 `SET CURRENT SCHEMA S`를 실행하도록 b0 클라이언트/커넥션 초기화에 반영(가장 간단·전역 일관).
  - (b) 또는 `_SCHEMA_BY_DB_ID['polestar_b0'] = S`로 명시 등록하고, `_table()`의 DB2 분기를 "무스키마"가 아니라 "명시 스키마 있으면 한정"으로 수정. 동시에 **동일 헬퍼를 `multi_db_executor`/`query_generator`의 SQL 생성 경로에서도 사용**(현재는 hostname resolver 전용).
- **LLM 프롬프트 정합**: `_generate_sql()`에 엔진별 **스키마 접두사 규칙**을 명시 주입(DB2면 `S.` 또는 무스키마 규칙, PostgreSQL이면 `polestar.`). `db_engine_hint`를 텍스트 힌트에서 **강제 규칙 + 스키마 상수**로 승격.
- **b0 프로필 방언 정합화**: `polestar_b0.yaml`의 `query_examples`에서 PostgreSQL식 `polestar.` 접두사를 **DB2 규칙(확정 스키마 `S.` 또는 무스키마)**으로 교체. `FETCH FIRST`와 스키마 표기의 **혼재 제거**.
- **가드**: DB2 대상 생성 SQL에 대해 `LIMIT` → `FETCH FIRST` 자동 교정, 스키마 미한정 테이블 참조 시 경고/교정하는 후처리(선택, 방어선).

### 4.2 [D-058] 공동존 서버 식별자 NULL 대응 (증상 B)

- **(선결) 생성 SQL 우선 확인**(D-050): H1(SQL 오류) vs H2(데이터) 판별. **H1이면** yd `query_examples`/`query_generator`의 "단일/다중 서버 식별은 WHERE 금지·HAVING 사용, 서버 식별 컬럼은 `server.Server` 행에서 피벗" 규칙을 강화(D-050 패턴 재사용).
- **H2(데이터로 name이 비어 있음)이면**: 서버 식별자를 **`COALESCE(r.name, r.hostname)`**(공동존 폴스타 한정)로 폴백. yd 프로필 `query_guide`의 "★ 기본값=name" 규칙에 *"단, `name`이 NULL이면 `hostname`으로 폴백하여 식별"* 단서를 추가하고, 대표 `query_examples`(서버 종합/목록)의 식별자 컬럼을 `COALESCE(c.name, c.hostname)`로 수정. 출력에는 `name`/`hostname`을 함께 노출.
- **field_mapper 정합**: 양식 "서버명" → 결과 키 매핑 시, 식별자 컬럼 alias를 폴백 결과(`COALESCE`)와 일치시켜 채우기 단계에서 NULL로 비지 않도록.
- gp(김포)도 동일 리스크가 있는지 확인하고 필요 시 동일 폴백 적용(엔진/환경별 회귀 방지 — D-053 교훈).

### 4.3 [D-059] 폼필 실패 시 침묵적 CSV 강등 금지 — 사유 노출 (증상 C)

- **사유 구조화**: `_generate_document_file()`이 `None`을 반환할 때 **반환 사유 코드**(`no_template`/`no_mapping`/`fill_error`/`no_data`)를 함께 산출하고, `output_generator`가 이를 응답 메시지·이벤트에 실어 사용자에게 노출한다. "요청하신 양식을 채우지 못했습니다 — 사유: (매핑 실패/데이터 부족/…)"처럼 **CSV만 남은 이유를 설명**.
- **부분 채우기 보장**: rows·mapping이 있으면 일부 컬럼이 NULL이어도 Excel은 반드시 생성(현재 `total_filled==0`이어도 `bytes` 반환하므로 이 경로는 유지). `None` 반환은 "정말 채울 수 없을 때"로 국한.
- **프론트 반영**: `app.js`에서 `has_file=false`이면서 양식 업로드가 있었던 경우, "채워진 양식을 제공할 수 없어 원본 데이터(CSV)만 제공합니다" 안내와 사유를 함께 표시.
- **로그 → 사용자 승격**: 이미 존재하는 `logger.warning`(각 `None` 분기)을 진단 채널(D-039 처리현황)로도 노출해 원인 추적을 가능하게 한다.

---

## 5. 검증 계획

1. **b0 스키마 실측**: §3.1 카탈로그 쿼리로 실제 스키마 `S` 확정 → 조치(a/b) 적용 후 b0 양식 채우기 재실행하여 `SQL0204N` 소멸 확인.
2. **경로 회귀 고정**: 엔진별(DB2 b0 / PostgreSQL gp·yd) 양식 채우기 단위·통합 테스트 추가. `tests/test_excel_fill_pipeline.py`·`tests/test_query_to_excel_mapping.py` 확장, DB2 방언(FETCH FIRST·스키마 한정) 스냅샷 고정.
3. **yd 식별자**: 진단 카운트로 H1/H2 판별 → 폴백 적용 후 서버명 컬럼이 채워지는지, 개별 서버가 구분되는지 확인.
4. **산출물 노출**: b0 실패·yd 식별자 NULL 각 케이스에서 (a) 사유가 사용자에게 노출되는지, (b) 채울 수 있는 경우 Excel이 실제로 생성·다운로드되는지 확인.
5. **로그 우선 원칙 준수**(Known Mistakes 2026-06-30): 안쪽 단계부터 추정 수정 금지 — 진입·게이트별 로그로 어디서 끊기는지 먼저 확정한 뒤 각 개선을 적용.

---

## 6. Known Mistakes 연계 / 재발 방지

- 본 건은 **D-053(2026-06-30)의 미완결부**다: b0 방언·라우팅은 실시간 프로세스/알람(고정 SQL) 경로만 고쳐졌고, **LLM 기반 양식 채우기(멀티DB) 경로는 미적용**이었다. → *"멀티 엔진 환경에서 한 경로만 검증하면 다른 경로 회귀를 놓친다"* 재확인.
- **null은 데이터 부재가 아닐 수 있다(D-050)**: yd `name` NULL을 데이터로 단정하지 말고 생성 SQL을 먼저 볼 것.
- **실패 원인 은닉 금지(2026-06-26)**: 침묵적 CSV 강등을 사유 노출로 교체.
- 구현 완료 시 `docs/02_decision.md`에 D-057/D-058/D-059를 정식 등재하고, `CLAUDE.md` Known Mistakes에 "양식 채우기 경로의 엔진·스키마 미적용" 항목을 추가한다.
