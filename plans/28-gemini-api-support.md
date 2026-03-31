# Plan 28: Gemini API 프로바이더 추가

## 배경

현재 Ollama(로컬 오픈소스 LLM) 기반으로 동작하는데, 환각(hallucination) 현상으로 코드 정상 동작 여부 판단이 어렵다. 임시로 Google Gemini API를 사용하여 검증할 수 있도록 프로바이더를 추가한다.

## 목표

- `.env`에서 `LLM_PROVIDER=gemini`로 전환만 하면 Gemini API 사용 가능
- API 키는 `.encenv` 파일에서 별도 관리 (`.gitignore` 등록, git 업로드 방지)
- 기존 ollama/fabrix 코드에 영향 없음
- 모든 노드는 기존과 동일하게 `create_llm()` 호출 — 변경 불필요

## 현재 구조 분석

### LLM 팩토리 패턴 (변경 지점이 최소화되는 구조)

```
src/config.py    — LLMConfig(provider: "ollama"|"fabrix"|"gemini", ...)
src/llm.py       — create_llm() → provider별 분기 → BaseChatModel 반환
src/clients/     — ollama_client.py, fabrix_client.py, fabrix_kbgenai.py
```

모든 노드(10개+)는 `from src.llm import create_llm`만 사용하므로, `src/llm.py`와 `src/config.py`만 수정하면 된다.

### 호출 흐름

```
nodes/*.py → create_llm(config) → config.llm.provider 확인
  ├─ "ollama"  → LLMAPIClient (커스텀 HTTP)
  ├─ "fabrix"  → FabriXAPIClient / KBGenAIChat
  └─ "gemini"  → ChatGoogleGenerativeAI (langchain-google-genai)  ← 추가
```

## 구현 계획

### 1단계: 의존성 추가 (`pyproject.toml`)

```toml
[project.optional-dependencies]
gemini = [
    "langchain-google-genai>=2.0.0",
]
```

optional로 추가하여 Gemini를 사용하지 않는 환경에서는 설치 불필요.

### 2단계: 설정 확장 (`src/config.py`)

`LLMConfig` 수정:

```python
class LLMConfig(BaseSettings):
    provider: Literal["ollama", "fabrix", "gemini"] = "ollama"  # gemini 추가
    model: str = "llama3.1:8b"

    # 기존 Ollama/FabriX 설정 유지...

    # Gemini 설정 추가
    gemini_api_key: str = ""
    gemini_model: str = ""  # 비어있으면 LLM_MODEL 사용

    def model_post_init(self, __context: object) -> None:
        # 기존 로직 유지...
        # Gemini API 키
        if not self.gemini_api_key:
            self.gemini_api_key = os.getenv("GOOGLE_API_KEY", "")
```

### 3단계: LLM 팩토리 확장 (`src/llm.py`)

`_create_gemini()` 함수 추가:

```python
def create_llm(config: AppConfig) -> BaseChatModel:
    provider = config.llm.provider
    if provider == "ollama":
        return _create_ollama(config)
    elif provider == "fabrix":
        return _create_fabrix(config)
    elif provider == "gemini":
        return _create_gemini(config)
    else:
        raise ValueError(f"지원하지 않는 LLM 프로바이더: {provider}")


def _create_gemini(config: AppConfig) -> BaseChatModel:
    """Google Gemini LLM 클라이언트를 생성한다."""
    from langchain_google_genai import ChatGoogleGenerativeAI

    api_key = config.llm.gemini_api_key
    if not api_key:
        raise ValueError(
            "Gemini API 키가 설정되지 않았습니다. "
            ".env에 LLM_GEMINI_API_KEY 또는 GOOGLE_API_KEY를 추가하세요."
        )

    model = config.llm.gemini_model or config.llm.model
    logger.info("Gemini LLM 초기화: model=%s", model)

    return ChatGoogleGenerativeAI(
        model=model,
        google_api_key=api_key,
        temperature=0.0,
        convert_system_message_to_human=True,
    )
```

`ChatGoogleGenerativeAI`는 LangChain의 `BaseChatModel`을 상속하므로 별도 커스텀 클라이언트 없이 바로 사용 가능. `bind_tools()`, 비동기 호출 등 LangChain 표준 인터페이스를 모두 지원한다.

### 4단계: `.encenv` 파일 추가 (API 키 전용)

민감 키를 `.env`와 분리하여 `.encenv`에서 관리한다. `.gitignore`에 등록하여 git 업로드를 방지한다.

```env
# .encenv — 민감 키 전용 (git 추적 안 됨)
LLM_GEMINI_API_KEY=AIza...your-key...
```

`LLMConfig`, `AdminConfig`, `RedisConfig`의 `model_config.env_file`을 `[".env", ".encenv"]`로 변경하여 양쪽 파일 모두 로드한다.

### 5단계: `.env.example` 업데이트

```env
# === LLM 설정 ===
# provider: ollama | fabrix | gemini
LLM_PROVIDER=ollama

# ... 기존 설정 유지 ...

# Gemini 설정 (LLM_PROVIDER=gemini 시)
# API 키는 .encenv 파일에서 관리 (LLM_GEMINI_API_KEY)
# 권장: gemini-2.0-flash (안정), gemini-3.1-pro (최신)
LLM_GEMINI_MODEL=gemini-2.0-flash
```

### 6단계: 사용 예시

**Ollama 사용 (기본, 기존과 동일):**
```env
# .env
LLM_PROVIDER=ollama
LLM_MODEL=llama3.1:8b
```

**Gemini로 전환:**
```env
# .env
LLM_PROVIDER=gemini
LLM_GEMINI_MODEL=gemini-2.0-flash

# .encenv (git 미추적)
LLM_GEMINI_API_KEY=AIza...your-key...
```

## 수정 대상 파일 요약

| 파일 | 변경 내용 |
|------|----------|
| `pyproject.toml` | `[project.optional-dependencies]`에 `gemini` 추가 |
| `src/config.py` | `LLMConfig.provider`에 `"gemini"` 추가, Gemini 설정 필드 추가, `env_file`에 `.encenv` 추가 |
| `src/llm.py` | `_create_gemini()` 함수 추가, `create_llm()`에 분기 추가 |
| `.env.example` | Gemini 관련 환경변수 주석 추가 |
| `.encenv.example` | 민감 키 전용 환경파일 템플릿 신규 생성 |
| `.gitignore` | `.encenv` 추가 |

**변경 불필요:**
- `src/nodes/*.py` — 모두 `create_llm()` 경유하므로 변경 없음
- `src/clients/*.py` — langchain-google-genai가 클라이언트 역할을 하므로 별도 클라이언트 불필요
- `src/graph.py` — 변경 없음

## 고려사항

### langchain-google-genai 선택 이유

- `BaseChatModel` 상속 → `bind_tools()`, `ainvoke()` 등 LangChain 표준 인터페이스 완전 지원
- 별도 커스텀 HTTP 클라이언트 구현 불필요 (Ollama와 달리)
- tool calling, structured output 등 LangGraph 기능과 호환

### Gemini 모델 권장

`gemini-2.5-flash`/`gemini-2.5-pro`는 2026-06-17 deprecated 예정이므로 사용하지 않는다.

| 모델 | 용도 | 비고 |
|------|------|------|
| `gemini-2.0-flash` | 빠른 검증, 비용 효율적 (**기본 권장**) | 안정 버전, 범용 |
| `gemini-3.1-pro` | 복잡한 쿼리, 높은 정확도 필요 시 | 최신, 추론 특화 |
| `gemini-3.1-flash-lite` | 대량 처리, 최저 비용 | 경량 모델 |

### 주의사항

- Gemini API는 외부 네트워크 접근 필요 (폐쇄망에서는 사용 불가)
- API 키는 `.encenv`에서 관리 (`.gitignore` 등록 완료)
- `convert_system_message_to_human=True`: Gemini가 system message를 제한적으로 지원하는 버전이 있을 수 있으므로 안전 옵션

## 검증 방법

1. `pip install -e ".[gemini]"`로 의존성 설치
2. `.env`에서 `LLM_PROVIDER=gemini`, `LLM_GEMINI_MODEL=gemini-2.0-flash` 설정
3. `.encenv`에 `LLM_GEMINI_API_KEY=AIza...` 설정
4. 서버 기동 후 간단한 자연어 쿼리로 SQL 생성 확인
5. Ollama 결과와 비교하여 환각 여부 판단
