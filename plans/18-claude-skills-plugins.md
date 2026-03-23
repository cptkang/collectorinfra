# 18. Claude Code 스킬 및 플러그인 활용 계획

> 작성일: 2026-03-23
> 목적: 프로젝트 개발/테스트에 활용 가능한 Claude Code 스킬·플러그인을 분석하고 활용 방안 정리

---

## 1. 프로젝트 기술 요소와 스킬 매핑

| 프로젝트 기술 요소 | 관련 스킬/플러그인 | 우선순위 |
|---|---|---|
| FastAPI 서버 + Web UI (HTML/JS) | **webapp-testing** (Playwright), **frontend-design** | 높음 |
| Excel 양식 파싱/생성 (openpyxl) | **xlsx** | 높음 |
| Word 양식 파싱/생성 (python-docx) | **docx** | 높음 |
| MCP 서버 (`mcp_server/`) | **mcp-builder** | 높음 |
| 코드 품질·리뷰 | **code-review**, **simplify** | 중간 |
| 프로젝트 문서 관리 | **claude-md-management** | 중간 |
| 커스텀 자동화 | **skill-creator** | 낮음 |

---

## 2. Tier 1: 핵심 스킬 (개발·테스트 직접 지원)

### 2.1 webapp-testing (Playwright 플러그인)

**용도**: FastAPI 서버의 API 엔드포인트 및 Web UI 기능 테스트

**활용 시나리오**:
- `/api/v1/query` 엔드포인트에 자연어 질의 전송 → 응답 검증
- `/api/v1/health` 헬스체크 확인
- 운영자 인증 플로우 (`/admin/login` → JWT 토큰 → `/api/v1/admin/*`)
- 파일 업로드/다운로드 (`/api/v1/query` multipart, `/api/v1/query/{id}/download`)
- 대화 세션 API (`/api/v1/conversation/*`) 멀티턴 시나리오

**사용 가능한 도구**:
```
browser_navigate     → API 엔드포인트 접근
browser_fill_form    → 로그인 폼 입력
browser_click        → UI 버튼 클릭
browser_file_upload  → Excel/Word 양식 파일 업로드 테스트
browser_snapshot     → 페이지 상태 캡처
browser_network_requests → API 호출 모니터링
browser_console_messages → JS 에러 감지
browser_evaluate     → API 응답 JSON 검증
```

**적용 대상 파일**:
- `src/api/server.py` — 서버 설정 검증
- `src/api/routes/query.py` — 쿼리 엔드포인트 E2E 테스트
- `src/api/routes/admin.py`, `admin_auth.py` — 운영자 API 테스트
- `src/api/routes/conversation.py` — 멀티턴 대화 테스트
- `static/` (Phase 4) — UI 렌더링/인터랙션 테스트

### 2.2 xlsx 스킬

**용도**: Excel 양식 템플릿 생성·검증, 파서/라이터 테스트 지원

**활용 시나리오**:
- 테스트용 Excel 양식 템플릿 생성 (헤더 행, 병합 셀, 수식 포함)
- `src/document/excel_parser.py`가 올바르게 구조를 추출하는지 검증
- `src/document/excel_writer.py`가 데이터를 채운 결과 파일 검증
- 멀티시트 양식 테스트 (`tests/test_document/test_excel_multisheet.py` 지원)
- 서버명/IP/CPU 사용률 등 인프라 데이터 양식 템플릿 프로토타이핑

**적용 대상 파일**:
- `src/document/excel_parser.py` — 헤더 감지, 셀 범위 분석
- `src/document/excel_writer.py` — 데이터 채우기, 서식 보존
- `src/document/field_mapper.py` — 필드↔컬럼 시멘틱 매핑 검증

### 2.3 docx 스킬

**용도**: Word 양식 템플릿 생성·검증, 파서/라이터 테스트 지원

**활용 시나리오**:
- `{{서버명}}`, `{{IP주소}}` 등 플레이스홀더가 포함된 Word 양식 생성
- 테이블 구조가 있는 인프라 보고서 양식 프로토타이핑
- `src/document/word_parser.py`의 플레이스홀더 추출 검증
- `src/document/word_writer.py`의 스타일 보존 여부 확인
- 한글(Korean) 필드명 ↔ DB 컬럼 매핑 테스트 데이터 준비

**적용 대상 파일**:
- `src/document/word_parser.py` — `{{placeholder}}` 패턴 감지
- `src/document/word_writer.py` — 데이터 충전, 스타일 유지
- `tests/test_document/test_word_parser.py`, `test_word_writer.py`

### 2.4 mcp-builder 스킬

**용도**: `mcp_server/` 패키지의 MCP 서버 개발 가이드

**활용 시나리오**:
- `mcp_server/mcp_server/tools.py`의 도구 정의 개선 (파라미터 스키마, 설명)
- FastMCP 기반 서버 패턴 최적화 (`mcp_server/mcp_server/server.py`)
- 새 MCP 도구 추가 시 베스트 프랙티스 적용
- 보안 설정 (`mcp_server/mcp_server/security.py`) 점검
- 멀티 DB 소스 설정의 MCP 레벨 지원 방안 검토

**적용 대상 파일**:
- `mcp_server/mcp_server/server.py` — FastMCP 서버 구성
- `mcp_server/mcp_server/tools.py` — MCP 도구 정의
- `mcp_server/mcp_server/config.py` — DB 연결 설정
- `mcp_server/mcp_server/security.py` — SQL 검증 보안

---

## 3. Tier 2: 워크플로우 지원 스킬

### 3.1 code-review

**용도**: PR 코드 리뷰 자동화

**활용 시나리오**:
- 보안 취약점 감지 (SQL 인젝션 방지 로직 누락 등)
- LangGraph 노드 간 상태 계약 위반 감지
- 비동기 코드의 리소스 해제 누락 확인
- 테스트 커버리지 점검

**호출 방법**: `/code-review` 명령

### 3.2 simplify

**용도**: 기존 코드의 중복·복잡도 개선

**활용 시나리오**:
- 프롬프트 모듈(`src/prompts/*.py`) 간 공통 패턴 정리
- 노드 코드(`src/nodes/*.py`)의 에러 핸들링 패턴 통합
- 테스트 코드 중복 제거 (conftest 활용도 개선)

**호출 방법**: `/simplify` 명령

### 3.3 claude-md-management

**용도**: CLAUDE.md 최신 상태 유지

**활용 시나리오**:
- 새 모듈/노드 추가 시 아키텍처 설명 갱신
- 명령어 가이드 업데이트 (새 스크립트, 환경변수)
- Phase 진행에 따른 현황 업데이트

**호출 방법**: `/claude-md-improver` 또는 `/revise-claude-md`

---

## 4. Tier 3: 상황적 활용 스킬

### 4.1 frontend-design

**적용 시점**: Phase 4 (UI 화면 구현) 시 활용
- `static/index.html` — 사용자 질의 화면 디자인
- `static/admin/dashboard.html` — 운영자 대시보드 디자인
- 반응형 레이아웃, 다크/라이트 테마 지원

### 4.2 skill-creator

**적용 시점**: 반복 작업이 누적될 때 커스텀 스킬 생성
- 예: "파이프라인 테스트 실행" 스킬 (pytest 특정 마커 + 환경 설정 자동화)
- 예: "스키마 캐시 갱신" 스킬 (Redis 연결 + CLI 실행 + 검증)

### 4.3 loop

**적용 시점**: 장기 실행 태스크 모니터링
- 예: 통합 테스트 실행 중 서버 헬스체크 모니터링
- 예: Redis 캐시 상태 주기적 확인

---

## 5. 활용 매트릭스: 개발 Phase별 스킬 매핑

| Phase | 주요 작업 | 권장 스킬 |
|---|---|---|
| Phase 1 (NL→SQL) | 파이프라인 테스트, API 테스트 | webapp-testing, code-review |
| Phase 2 (문서처리) | Excel/Word 양식 생성·검증 | **xlsx**, **docx**, webapp-testing |
| Phase 3 (안정화) | 멀티턴, 승인 플로우, 감사 | webapp-testing, code-review, simplify |
| Phase 4 (UI) | 화면 구현·테스트 | **frontend-design**, **webapp-testing** |
| MCP 서버 개발 | 도구 추가, 보안 강화 | **mcp-builder** |
| 전 Phase | 코드 품질, 문서 관리 | code-review, simplify, claude-md |

---

## 6. Playwright 플러그인 테스트 시나리오 상세

FastAPI 서버(`uvicorn src.api.server:app --port 8040`)를 기동한 상태에서 실행한다.

### 6.1 헬스체크

```
browser_navigate → http://localhost:8040/api/v1/health
→ JSON 응답: {"status": "healthy"} 확인
```

### 6.2 자연어 쿼리 E2E

```
browser_navigate → http://localhost:8040/docs  (Swagger UI)
→ /api/v1/query 엔드포인트에 {"query": "서버 목록 조회"} POST
→ 응답에 SQL 생성·실행·결과 포함 확인
```

### 6.3 파일 업로드 (Phase 2)

```
browser_navigate → http://localhost:8040/  (사용자 화면)
browser_file_upload → test_template.xlsx 첨부
browser_click → 제출 버튼
→ 다운로드 링크 생성 확인
```

### 6.4 운영자 인증 플로우

```
browser_navigate → http://localhost:8040/admin/login
browser_fill_form → username/password 입력
browser_click → 로그인
→ /admin 대시보드로 리다이렉트 확인
→ 스키마 캐시 관리 API 호출 가능 확인
```

---

## 7. 참고: 스킬 호출 방법

| 스킬 | 호출 | 설명 |
|---|---|---|
| webapp-testing | Playwright MCP 도구 직접 사용 | `browser_navigate`, `browser_click` 등 |
| xlsx | `/xlsx` 또는 Excel 관련 작업 요청 | 자동 트리거 |
| docx | `/docx` 또는 Word 관련 작업 요청 | 자동 트리거 |
| mcp-builder | MCP 서버 구축 요청 시 자동 트리거 | |
| code-review | `/code-review` | PR 리뷰 |
| simplify | `/simplify` | 코드 최적화 |
| claude-md | `/revise-claude-md` 또는 `/claude-md-improver` | CLAUDE.md 갱신 |
| frontend-design | UI/프론트엔드 구축 요청 시 자동 트리거 | |
| skill-creator | `/skill-creator` | 커스텀 스킬 생성 |
