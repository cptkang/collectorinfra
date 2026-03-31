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
