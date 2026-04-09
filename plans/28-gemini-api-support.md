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


---

# Verification Report

# Verification Report: Plan 28 -- Gemini API Provider Addition

**Date**: 2026-03-25
**Verifier**: QA Agent (verifier.md)
**Plan**: plans/28-gemini-api-support.md
**Scope**: src/config.py, src/llm.py, pyproject.toml, .env.example, .encenv.example, .gitignore

---

## 1. Test Results Summary

| Metric | Value |
|--------|-------|
| Total tests | 34 |
| Passed | 34 |
| Failed | 0 |
| Test file | `tests/test_llm_gemini.py` |
| Execution time | 1.05s |

### Test Categories

| Category | Tests | Status |
|----------|-------|--------|
| LLMConfig Gemini field validation | 8 | PASS |
| GOOGLE_API_KEY fallback (model_post_init) | 3 | PASS |
| env_file list configuration | 2 | PASS |
| create_llm() factory dispatch | 3 | PASS |
| _create_gemini() validation (missing key) | 2 | PASS |
| Model fallback logic (gemini_model empty) | 2 | PASS |
| ChatGoogleGenerativeAI parameter passing | 2 | PASS |
| Environment variable mapping (LLM_ prefix) | 2 | PASS |
| .encenv file config presence | 3 | PASS |
| Node code impact (signature unchanged) | 2 | PASS |
| .gitignore .encenv registration | 1 | PASS |
| .encenv.example file validation | 3 | PASS |
| pyproject.toml gemini dependency | 1 | PASS |

---

## 2. Architecture Compliance (arch-check)

```
Checked files: 65 (66 including new llm.py -- counted as part of infrastructure layer)
Total imports: 196
Allowed imports: 196
Errors: 0
Warnings: 0
```

**Result**: All dependencies comply with Clean Architecture layer rules.

`src/llm.py` is correctly classified as `infrastructure` layer. All nodes (`src/nodes/*.py`, `application` layer) import `create_llm` from `src.llm` (`infrastructure`), which is a permitted `application -> infrastructure` dependency.

### Dependency Matrix (excerpt, relevant layers)

| From \ To | config | infrastructure |
|-----------|--------|---------------|
| infrastructure | 10 (allowed) | - |
| application | 13 (allowed) | 43 (allowed) |
| orchestration | 1 (allowed) | 2 (allowed) |

---

## 3. Code Review Findings

### 3.1 Code Structure: Plan Compliance

All 6 files specified in Plan 28 have been modified/created as planned:

| File | Plan Spec | Actual | Status |
|------|-----------|--------|--------|
| `pyproject.toml` | Add `gemini` optional dep | `gemini = ["langchain-google-genai>=2.0.0"]` added | PASS |
| `src/config.py` | Add gemini fields to LLMConfig | `provider` Literal, `gemini_api_key`, `gemini_model`, `env_file` list | PASS |
| `src/llm.py` | Add `_create_gemini()`, gemini branch in `create_llm()` | Implemented as planned | PASS |
| `.env.example` | Add Gemini env var block | Gemini section with comments added | PASS |
| `.encenv.example` | New file for sensitive keys | Created with all sensitive keys | PASS |
| `.gitignore` | Add `.encenv` | `.encenv` on line 2 | PASS |

### 3.2 Configuration Loading Verification

**env_file list usage** (pydantic-settings v2):
- `DotenvType = Path | str | Sequence[Path | str]` -- officially supported in pydantic-settings v2
- `env_file=[".env", ".encenv"]` is correct syntax
- File loading order: later files override earlier ones (`.encenv` values take precedence over `.env`)
- Applied to: `LLMConfig`, `AdminConfig`, `RedisConfig` -- all classes with sensitive key fields

**LLM_ prefix mapping**:
- `LLM_GEMINI_API_KEY` env var maps to `gemini_api_key` field via `env_prefix="LLM_"` -- correct
- `LLM_GEMINI_MODEL` env var maps to `gemini_model` field -- correct
- Verified in test: `TestEnvVarMapping::test_env_vars_with_llm_prefix_loaded` -- PASS

**GOOGLE_API_KEY fallback**:
- `model_post_init()` checks `if not self.gemini_api_key` then falls back to `os.getenv("GOOGLE_API_KEY", "")`
- This provides a secondary fallback after pydantic-settings env loading
- Note: `ChatGoogleGenerativeAI` itself also reads `GOOGLE_API_KEY` env var internally, so there is a dual fallback path (both config-level and library-level)

**gemini_model empty fallback**:
- `model = config.llm.gemini_model or config.llm.model` -- correct Python truthy evaluation
- Empty string is falsy, falls back to `config.llm.model` ("llama3.1:8b" by default)

### 3.3 LLM Factory Verification

**_create_gemini() implementation**:
- Lazy import of `langchain_google_genai.ChatGoogleGenerativeAI` -- correct (avoids import error when package not installed)
- API key validation before object creation -- raises `ValueError` with clear Korean message
- Parameter passing to `ChatGoogleGenerativeAI`:
  - `model`: gemini_model or fallback to llm.model
  - `google_api_key`: string passed directly (will be coerced to `SecretStr` by pydantic internally)
  - `temperature=0.0`: appropriate for deterministic SQL generation
  - `convert_system_message_to_human=True`: **see issue M-001 below**

**Return type**: `ChatGoogleGenerativeAI` extends `BaseChatModel` via `_BaseGoogleGenerativeAI + BaseChatModel`. All LangChain standard interfaces (`invoke`, `ainvoke`, `bind_tools`, etc.) are supported.

### 3.4 Existing Code Impact

**Node code unchanged**: All 8 node files + 2 infrastructure files that call `create_llm()` use the identical pattern:
```python
from src.llm import create_llm
llm = create_llm(app_config)  # or create_llm(config)
```

No changes required. The `create_llm()` signature (`config: AppConfig -> BaseChatModel`) is preserved. Verified by:
- Grep: 23 call sites across `src/nodes/`, `src/routing/`, `src/api/`, `src/graph.py`
- Test: `TestNodeCodeUnchanged::test_create_llm_signature_unchanged` -- PASS

**Ollama path**: `_create_ollama()` unchanged, dispatched when `provider="ollama"` (default)
**FabriX path**: `_create_fabrix()` unchanged, dispatched when `provider="fabrix"`

---

## 4. Discovered Issues

### M-001: `convert_system_message_to_human` is Deprecated [Minor]

**Location**: `src/llm.py:78`

**Finding**: The `convert_system_message_to_human=True` parameter is **deprecated** in the installed `langchain-google-genai` (v4.x series). The library source code contains:

```python
# langchain_google_genai/chat_models.py:603-608
if convert_system_message_to_human:
    warnings.warn(
        "The 'convert_system_message_to_human' parameter is deprecated and will be "
        "removed in a future version. Use system instructions instead.",
        DeprecationWarning,
        stacklevel=2,
    )
```

**Impact**: Functionally it still works (system message is merged into first human message). However:
1. A `DeprecationWarning` will be emitted on every LLM call
2. The parameter will be removed in a future version
3. Modern Gemini models (gemini-2.0+) natively support `system_instruction` parameter

**Recommendation**: Remove `convert_system_message_to_human=True`. The library now handles system messages via `system_instruction` by default (extracted in `_parse_chat_history` when the flag is False). If backward compatibility with very old Gemini models is needed, consider making this configurable.

**Severity**: **Minor** -- functional correctness is not affected, but generates deprecation warnings and will break on future library upgrades.

---

### M-002: `google_api_key` Parameter Type Mismatch (Cosmetic) [Minor]

**Location**: `src/llm.py:75`

**Finding**: The `_create_gemini()` function passes `google_api_key=api_key` where `api_key` is a plain `str`. The `ChatGoogleGenerativeAI.google_api_key` field is typed as `SecretStr | None`. Pydantic will automatically coerce the string to `SecretStr`, so this works correctly at runtime. However, static type checkers (mypy strict mode) may flag this as a type mismatch.

**Impact**: None at runtime. May cause mypy warnings under strict mode.

**Recommendation**: Either wrap the key in `SecretStr(api_key)` or pass it via the alias `api_key=api_key` instead of `google_api_key=api_key`.

**Severity**: **Minor** -- cosmetic type safety concern only.

---

### M-003: .encenv File Priority vs model_post_init Redundancy [Minor]

**Location**: `src/config.py:56-57`

**Finding**: The `model_post_init` fallback for `GOOGLE_API_KEY` is a secondary defense, but the env file loading order means:
1. pydantic-settings reads `.env` first, then `.encenv` (later overrides earlier)
2. If `LLM_GEMINI_API_KEY` is in `.encenv`, it populates `gemini_api_key`
3. `model_post_init` then checks `os.getenv("GOOGLE_API_KEY")` only if `gemini_api_key` is still empty

This is logically correct and provides a good UX for users who have `GOOGLE_API_KEY` set globally (e.g., for Google Cloud SDK). No action needed, but the fallback chain could be documented more clearly.

**Severity**: **Minor** -- documentation/clarity concern only.

---

## 5. Security Verification

| Check | Result |
|-------|--------|
| `.encenv` in `.gitignore` | PASS -- line 2 of `.gitignore` |
| `.encenv.example` contains no actual keys | PASS -- all values are empty |
| API key not logged | PASS -- `_create_gemini()` logs only model name, not key |
| Sensitive key separation | PASS -- `.encenv.example` lists all sensitive keys (Gemini, FabriX, Ollama, Admin, Redis) |

---

## 6. Issue Summary

| ID | Severity | Component | Description | Status |
|----|----------|-----------|-------------|--------|
| M-001 | Minor | src/llm.py:78 | `convert_system_message_to_human` deprecated in langchain-google-genai v4.x | **Fixed** — 파라미터 제거 완료 |
| M-002 | Minor | src/llm.py:75 | `google_api_key` receives str instead of SecretStr (auto-coerced) | Open |
| M-003 | Minor | src/config.py:56 | GOOGLE_API_KEY fallback chain documentation could be clearer | Open |

**No Critical or Major issues found.**

---

## 7. Conclusion

Plan 28 (Gemini API Provider Addition) has been implemented correctly and completely. All 6 target files match the plan specification. The factory pattern design ensures zero impact on existing ollama/fabrix code paths. All 34 unit tests pass, and the architecture dependency check reports 0 violations.

Three minor issues were identified:
1. A deprecated parameter that will generate warnings (M-001)
2. A cosmetic type annotation mismatch (M-002)
3. A documentation clarity opportunity (M-003)

None of these affect functional correctness. The implementation is **approved** for merge with the recommendation to address M-001 before the next langchain-google-genai major version upgrade.
