# 06. FastAPI 엔드포인트 설계

> 기존 `src/api/` 분석 및 확장 계획

---

## 1. 현재 구현 분석

### 1.1 서버 (src/api/server.py)

- `create_app()` 팩토리 패턴
- CORS 미들웨어 (모든 오리진 허용 -- 운영 시 제한 필요)
- `lifespan`에서 로깅 설정 + 그래프 빌드 + config 공유
- 라우트: `/api/v1/health`, `/api/v1/query`

### 1.2 스키마 (src/api/schemas.py)

- `QueryRequest`: query, output_format, thread_id
- `QueryResponse`: query_id, status, response, has_file, file_name, executed_sql, row_count, processing_time_ms
- `HealthResponse`: status, version, db_connected, timestamp
- `ErrorResponse`: error, detail, query_id

### 1.3 라우트

- `POST /api/v1/query` -- 자연어 질의 처리 (동기, 최대 60초)
- `GET /api/v1/query/{query_id}/result` -- 비동기 결과 조회
- `GET /api/v1/query/{query_id}/download` -- 파일 다운로드 (Phase 2)
- `GET /api/v1/health` -- 헬스체크

### 1.4 결과 저장소

- `_results_store: OrderedDict` -- LRU 인메모리 (최대 1000건)
- `_store_result()` 함수로 관리

---

## 2. 전체 엔드포인트 설계 (Phase별)

| Phase | 메서드 | 경로 | 설명 | 구현 상태 |
|-------|--------|------|------|----------|
| 1 | POST | `/api/v1/query` | 자연어 질의 처리 | 구현됨 |
| 1 | GET | `/api/v1/query/{query_id}/result` | 비동기 결과 조회 | 구현됨 |
| 1 | GET | `/api/v1/health` | 헬스체크 | 구현됨 |
| 2 | POST | `/api/v1/query/file` | 양식 파일 + 질의 | 미구현 |
| 2 | GET | `/api/v1/query/{query_id}/download` | 파일 다운로드 | 구현됨 (스텁) |
| 3 | GET | `/api/v1/history` | 쿼리 히스토리 | 미구현 |
| 3 | POST | `/api/v1/templates` | 양식 템플릿 등록 | 미구현 |
| 3 | GET | `/api/v1/templates` | 템플릿 목록 조회 | 미구현 |
| 3 | DELETE | `/api/v1/templates/{id}` | 템플릿 삭제 | 미구현 |
| 3 | POST | `/api/v1/query/{query_id}/approve` | SQL 실행 승인 (HITL) | 미구현 |

---

## 3. Phase 2: 파일 업로드 엔드포인트

### 3.1 요청 스키마

```python
# src/api/schemas.py (추가)

class FileQueryRequest(BaseModel):
    """양식 파일 기반 질의 요청. POST /api/v1/query/file"""
    query: str = Field(
        ..., min_length=1, max_length=2000,
        description="자연어 질의 (파일에 대한 설명 또는 추가 조건)",
    )
    output_format: OutputFormat = Field(
        default=OutputFormat.TEXT,
        description="출력 형식 (자동 감지됨)",
    )
    thread_id: Optional[str] = Field(
        default=None,
        description="세션 ID",
    )
```

### 3.2 라우트 구현

```python
# src/api/routes/query.py (추가)

from fastapi import UploadFile, File, Form


@router.post(
    "/query/file",
    response_model=QueryResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
async def process_file_query(
    request: Request,
    query: str = Form(..., min_length=1, max_length=2000),
    file: UploadFile = File(...),
    thread_id: Optional[str] = Form(None),
) -> QueryResponse:
    """양식 파일과 함께 질의를 처리한다.

    지원 형식: .xlsx, .docx
    """
    # 1. 파일 타입 검증
    file_ext = _get_file_extension(file.filename)
    if file_ext not in ("xlsx", "docx"):
        raise HTTPException(
            status_code=400,
            detail=f"지원하지 않는 파일 형식입니다: {file_ext}. xlsx 또는 docx만 지원합니다.",
        )

    # 2. 파일 크기 검증 (최대 10MB)
    file_bytes = await file.read()
    if len(file_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="파일 크기가 10MB를 초과합니다.")

    # 3. 초기 State 생성
    query_id = str(uuid.uuid4())
    start_time = time.time()

    graph = request.app.state.graph
    initial_state = create_initial_state(
        user_query=query,
        uploaded_file=file_bytes,
        file_type=file_ext,
    )

    thread_config = {
        "configurable": {"thread_id": thread_id or query_id}
    }

    # 4. 그래프 실행
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(graph.invoke, initial_state, thread_config),
            timeout=120,  # 파일 처리는 120초
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="처리 시간이 초과되었습니다.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    elapsed_ms = (time.time() - start_time) * 1000

    response_data = {
        "query_id": query_id,
        "status": "completed",
        "response": result.get("final_response", ""),
        "has_file": result.get("output_file") is not None,
        "file_name": result.get("output_file_name"),
        "executed_sql": result.get("generated_sql"),
        "row_count": len(result.get("query_results", [])),
        "processing_time_ms": elapsed_ms,
    }
    _store_result(query_id, {**response_data, "output_file": result.get("output_file")})

    return QueryResponse(**response_data)


def _get_file_extension(filename: str | None) -> str:
    """파일 확장자를 추출한다."""
    if not filename:
        return ""
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
```

---

## 4. 현재 코드 개선 사항

### 4.1 헬스체크 개선

현재 `health.py`에서 매번 `get_dbhub_client()`를 생성하여 헬스체크한다. `db_backend`가 `"direct"`일 때 DBHub 클라이언트를 사용하므로 항상 실패한다.

```python
# src/api/routes/health.py (개선)
from src.db import get_db_client

@router.get("/health", response_model=HealthResponse)
async def health_check(request: Request) -> HealthResponse:
    config = request.app.state.config
    db_connected = False

    try:
        async with get_db_client(config) as client:
            db_connected = await client.health_check()
    except Exception:
        pass

    return HealthResponse(
        status="healthy" if db_connected else "unhealthy",
        version="0.1.0",
        db_connected=db_connected,
        timestamp=datetime.now(),
    )
```

### 4.2 CORS 설정 환경변수화

```python
# src/config.py (추가)
class ServerConfig(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = ["*"]  # 운영: ["https://your-domain.com"]

    model_config = {"env_prefix": "API_", "env_file": ".env", "extra": "ignore"}
```

```python
# src/api/server.py (개선)
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.server.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**주의:** `create_app()`에서 `config`에 접근하려면 팩토리 구조를 약간 변경해야 한다:

```python
def create_app(config: AppConfig | None = None) -> FastAPI:
    if config is None:
        config = load_config()

    app = FastAPI(...)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.server.cors_origins,
        ...
    )
    # ...
    return app
```

### 4.3 요청 타임아웃 설정 분리

현재 하드코딩된 `timeout=60`을 설정으로 분리:

```python
# src/config.py
class ServerConfig(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = ["*"]
    query_timeout: int = 60           # 텍스트 질의 타임아웃 (초)
    file_query_timeout: int = 120     # 파일 질의 타임아웃 (초)
```

### 4.4 그래프 실행 방식 개선

현재 `asyncio.to_thread(graph.invoke, ...)`로 동기 `invoke`를 스레드에서 실행한다. 그래프의 노드가 모두 `async` 함수이므로 `graph.ainvoke`를 직접 사용하는 것이 더 적절하다:

```python
# 개선 전
result = await asyncio.wait_for(
    asyncio.to_thread(graph.invoke, initial_state, thread_config),
    timeout=60,
)

# 개선 후
result = await asyncio.wait_for(
    graph.ainvoke(initial_state, thread_config),
    timeout=config.server.query_timeout,
)
```

---

## 5. Phase 3: 히스토리 API

### 5.1 스키마

```python
class HistoryItem(BaseModel):
    query_id: str
    user_query: str
    executed_sql: Optional[str]
    status: str
    row_count: Optional[int]
    processing_time_ms: Optional[float]
    created_at: datetime


class HistoryResponse(BaseModel):
    items: list[HistoryItem]
    total: int
    page: int
    page_size: int
```

### 5.2 라우트

```python
@router.get("/history", response_model=HistoryResponse)
async def get_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> HistoryResponse:
    """쿼리 실행 히스토리를 조회한다."""
    # Phase 3: 감사 로그 DB에서 조회
    ...
```

---

## 6. Phase 3: Human-in-the-loop (SQL 승인)

### 6.1 워크플로우

```
1. POST /api/v1/query -> 그래프 실행
2. query_validator 통과 후 -> 인터럽트 발생
3. 응답: {"status": "awaiting_approval", "sql": "SELECT ...", "query_id": "..."}
4. POST /api/v1/query/{query_id}/approve -> 승인/거부
5. 승인 시 그래프 재개 -> query_executor 실행
```

### 6.2 스키마

```python
class ApprovalRequest(BaseModel):
    approved: bool
    feedback: Optional[str] = None  # 거부 시 피드백


class AwaitingApprovalResponse(BaseModel):
    query_id: str
    status: str = "awaiting_approval"
    generated_sql: str
    message: str = "다음 SQL을 실행하시겠습니까?"
```

---

## 7. 응답 코드 정리

| 상태 코드 | 용도 |
|----------|------|
| 200 | 성공 |
| 400 | 잘못된 요청 (빈 쿼리, 지원하지 않는 파일 형식 등) |
| 404 | 결과 없음 (query_id로 조회 시) |
| 500 | 서버 내부 에러 |
| 504 | 처리 시간 초과 |

---

## 8. 기존 코드 대비 변경 사항 요약

| 항목 | 현재 | 변경 |
|------|------|------|
| 파일 업로드 | 미구현 | `POST /api/v1/query/file` 추가 (Phase 2) |
| 헬스체크 | DBHub 전용 | `get_db_client()` 사용 (backend 따라 분기) |
| CORS | `allow_origins=["*"]` 하드코딩 | 환경변수 `API_CORS_ORIGINS`로 제어 |
| 타임아웃 | 60초 하드코딩 | `ServerConfig.query_timeout`, `file_query_timeout` |
| 그래프 실행 | `asyncio.to_thread(graph.invoke)` | `graph.ainvoke()` 직접 호출 |
| 히스토리 | 미구현 | Phase 3에서 `GET /api/v1/history` 추가 |
| SQL 승인 | 미구현 | Phase 3에서 `POST /api/v1/query/{id}/approve` 추가 |
