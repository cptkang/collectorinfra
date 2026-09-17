# 100. 맥북(Apple Silicon) 로컬 테스트 LLM — MLX provider 추가

> **작성일**: 2026-09-17 · **v2**(같은 날 — Phase 0 조사 J-1~J-4 실측 완료 반영) · **v3**(같은 날 — 구현 완료 · Phase 4 로컬 종단 검증 결과 반영)
> **성격**: 구현 계획 · **상태: 부분 구현(CU-1~CU-7 랜딩 · §7 권장안 채택 D-222 · 잔여 = Phase 4 다도구 루프 품질 판단(R-1) · 캐시 상한 조정 · Ollama 비교 — §5 Phase 4 결과 · §9)** — 파일명 `-WIP`
> **선례 계획**: `plans/28`(Gemini provider 추가 — Literal 확장·팩토리 분기) · `plans/49` §4.7(Gemini 오케스트레이터 테스트 모드 — 설정만으로 전환, 운영 기본값 유지)
> **풀려는 제약**: `plans/99` L-1(정본 1단 `deep_agent` 측정에는 "`vllm` 인프라 또는 `gemini` D-127 건별 승인"이 필요하다 — `plans/99`:436)
> **관련 결정**: D-021(Gemini provider) · D-037(deepagents · 워커 override) · D-042(Qwen no-think) · D-060(SSL 토글) · **D-127**(과금 승인) · D-169(구조화 출력 모드 자동 선택) · **D-174**(LLM 평면 운영 정책 · 개발 측정치 기준선 인용 금지) · D-181(공유 venv 의존성 파손) · D-198(총상한 부기) · **D-211 ⑨**·**D-216 ①**(내부망 승인 면제 — 개정 대상)
> **신규 결정**: **D-222**(2026-09-17 본문 등재 — 사용자 구현 지시로 §7 권장안 채택 · D-211 ⑨·D-216 ① 개정). 등재 직전 `## D-` 헤더·「변경 이력」·「채번 이력」 세 곳 재실측 최댓값 D-221.
> **실측 기준**: 브랜치 `multiintent` · HEAD `c64ef98` · 2026-09-17 · 장비 Apple M1 Max / 메모리 32GB / macOS 26.6.2 · 루트 venv Python 3.12.11 · 외부 패키지는 **`mlx-lm` 0.31.3 wheel 소스를 직접 읽었다**. **Phase 0은 실 MLX 서버로 측정했다**(`uvx` 임시 환경 — 루트 venv·`uv tool` 설치 없음, 측정 후 서버 종료).
> **▶ 결정만 필요하면 §7 「사용자 확정 게이트」만 읽으면 된다. 실측 근거는 §5 Phase 0에 모았다.**

---

## 0. 한 줄 결론

MLX는 **앱 프로세스 안에 넣지 않는다.** `mlx_lm.server`(OpenAI 호환 HTTP)를 루프백에 따로 띄우고, 앱은
**이미 운영 vLLM 오케스트레이터가 쓰는 `ChatOpenAI` 경로**로 붙는다. 코드 변경은 네 갈래다.

1. provider 값 `mlx`를 워커·오케스트레이터 두 평면에 추가한다.
2. 서버 기본값 512토큰 절단을 막기 위해 `max_tokens`를 명시한다.
3. 과금 게이트가 **오케스트레이터 평면도 판정하도록** 보강한다.
4. 설정 카탈로그·예시 env·문서를 맞춘다.

`LLM_PROVIDER`·`ORCHESTRATOR_PROVIDER`를 `mlx`로 바꾸지 않으면 **운영 경로(fabrix·vllm)는 비트 동일**이다.

**Phase 0 실측(v2)이 설계 전제 4건을 확정했다.**

1. `max_tokens`를 보내지 않으면 정확히 **512토큰에서 `finish_reason=length`**로 끊긴다.
2. `chat_template_kwargs`를 보내지 않으면 Qwen3.5가 **2048토큰 전부를 reasoning에 쓰고 `content`가 빈 문자열**이다.
3. `bind_tools` tool call 파싱은 **5/5**다.
4. `ChatOpenAI` 이름의 워커는 instructor `TOOLS` 모드에서 **실제로 실패**하고, 서브클래스 이름이면 성공한다.

바뀐 것은 둘이다. 콜드 prefill 실측(초당 320~370토큰)에 맞춰 `LLM_MLX_TIMEOUT` 기본값을 **600초**로 올렸고, 프롬프트 캐시가 **13GB**까지 커지는 것을 보고 캐시 상한을 권장에 넣었다.

---

## 1. 현재 상태 실측 — 왜 필요한가

| # | 사실 | 근거 |
|---|---|---|
| S-1 | 개발 `.env`는 워커·오케스트레이터가 **둘 다 Gemini**다: `LLM_PROVIDER=gemini` · `ORCHESTRATOR_PROVIDER=gemini` · `ENABLE_DEEPAGENTS_PACKAGE=true`. 실 실행마다 과금이 발생하고 D-127 건별 승인이 필요하다 | `.env` 실측(키 값만 grep) |
| S-2 | 기존 로컬 경로인 `ollama`는 **워커 평면에만** 있다. 오케스트레이터 provider는 `Literal["vllm","gemini"]`뿐이다 | `src/config.py:23` · `src/config.py:103` |
| S-3 | Ollama 클라이언트 `LLMAPIClient`는 네이티브 `/api/chat` 형식이고 `_generate`만 구현한다. 그래서 `astream_text`가 **단일 청크로 폴백**하고, 토큰 SSE가 실제로는 흐르지 않는다 | `src/clients/ollama_client.py:28` · `:260` · `src/llm.py:40-42` |
| S-4 | 장비 HF 캐시에 `mlx-community/Qwen3.5-9B-OptiQ-4bit`(5.6GB, `model_type=qwen3_5`)가 **이미 있다**. 운영 오케스트레이터 기본 모델(`Qwen3.5-9B`)과 같은 계열이다. 로컬 Ollama 0.33.1(`/v1/models` 응답 확인)에는 `qwen3.5:2b`·`0.8b`만 있다 | `~/.cache/huggingface/hub` · `src/config.py:105` · `ollama list` |
| S-5 | 루트 venv에는 `mlx`·`mlx_lm`이 **없다**. 다른 파이썬(3.12.7·3.14.0)에도 없다 | `importlib.util.find_spec` 실측 |
| S-6 | 저장소 전체에서 `mlx`·Metal·MPS 언급은 **0건**이다(`.venv` 제외) | grep |

**이 계획이 여는 것**: 과금 없이 로컬에서 ① 단일 DB 경로 워커 실행 ② 정본 1단(`deep_agent`) 사다리 **기능** 검증 ③ 토큰 스트리밍 SSE를 확인할 수 있다.
**열지 않는 것**: 운영 기준선. 워커가 FabriX가 아니라 9B 4bit 모델이므로 정확도·지연 수치는 운영 기준선으로 인용하지 않는다(D-174 부기, `docs/02_decision.md:1560-1564`).

---

## 2. 외부 실측 — `mlx-lm` 0.31.3 서버가 주는 것 · 주지 않는 것

출처는 `mlx_lm-0.31.3-py3-none-any.whl` 소스다(경로는 `mlx_lm/` 기준). "Phase 0" 표기는 실 서버로 확인한 행이다.

| 항목 | 실측 | 근거 | 계획 영향 |
|---|---|---|---|
| 엔드포인트 | `POST /v1/chat/completions` · `/v1/completions` · `GET /v1/models` · `GET /health` | `server.py:1106-1107` · `:1624-1626` | 기존 `vllm_healthy`(`GET {base_url}/models`)를 **그대로** 쓸 수 있다 |
| `/v1/models` · `/health` | HF 캐시에서 MLX로 보이는 repo를 나열하고, 적재 여부와 무관하게 200을 준다. **Phase 0**: 기동 8초 만에 `/health` 200, 캐시 모델 4종 나열, 첫 요청(적재 포함) 3.2초 | `server.py:1643` `handle_models_request` | health는 "서버가 떠 있다"까지만 보장한다. 모델 ID가 맞는지는 보장하지 않는다(§3.5) |
| thinking 토글 | 요청 바디의 `chat_template_kwargs`를 받아 서버 기본값(`--chat-template-args`)과 병합한다 | `server.py:1192` · `:544-551` | 기존 `extra_body={"chat_template_kwargs":{"enable_thinking":…}}` 규약(D-042)이 그대로 통한다. **Phase 0: 미전송이면 content가 빈다**(§5 J-1 ②) |
| tool calling | 채팅 템플릿으로 파서를 자동 추론한다. Qwen3.5 템플릿(`<tool_call>\n<function=`)은 `qwen3_coder`로 잡힌다. 응답에 `message.tool_calls`, `finish_reason="tool_calls"`가 실린다 | `tokenizer_utils.py:548-575` · `server.py:1360` · `:1505` | **Phase 0: `ChatOpenAI.bind_tools` 5/5 파싱 성공**(§5 J-1 ③) |
| **최대 생성 토큰** | CLI `--max-tokens` **기본 512**. 요청의 `max_completion_tokens`/`max_tokens`가 우선한다 | `server.py:1842-1845` · `:1169-1172` | **클라이언트가 반드시 보내야 한다. Phase 0: 미전송 시 512에서 `length`로 절단**(§5 J-1 ①) |
| 구조화 출력 | `response_format`·`json_schema` 처리가 없다(grep 0건) | `server.py` | 현 코드에 `with_structured_output`·`response_format` 사용처가 0건이라 영향 없음 |
| reasoning | 추론 텍스트를 `message.reasoning` 필드로 분리한다 | `server.py:1358` | `ChatOpenAI`는 이 필드를 읽지 않는다. thinking은 **기본 off** |
| 모델 적재 | 메모리에 모델 **1개**만 둔다. 요청의 `model`이 현재 적재 키와 다르면 그 값으로 **재적재**(HF repo면 다운로드 시도)한다. `"default_model"`은 `--model`로 매핑된다 | `server.py:393` · `:1163` · `:315` | **Phase 0**: 교대 재적재 3.6~5.3초 · 잘못된 ID는 `HF_HUB_OFFLINE=1`에서 즉시 404 |
| 프롬프트 캐시 | 서로 다른 KV 캐시 최대 `--prompt-cache-size`(기본 10)개. `--prompt-cache-bytes`(단위 `M`/`G` 허용)로 바이트 상한 | `server.py:1871-1881` · `utils.py:60-69` | **Phase 0: 접두 재사용 확인(질문만 바뀌어도 16K토큰 중 16,087 적중) · 캐시가 13.33GB까지 커짐** → 상한 권장(§3.6 · R-10) |
| 동시성 | `ThreadingHTTPServer`를 쓰고, draft 모델·seed가 없으면 배치 디코딩한다 | `server.py:14` · `:685-686` | **Phase 0: 동시 3건 1.1초** — 병렬 서브에이전트 요청 수용 |
| 운영 부적합 | 서버 스스로 "not recommended for production"을 출력한다(Phase 0 기동 로그에서도 확인) | `server.py:1724` | **로컬 테스트 전용**이라는 이 계획의 경계와 일치한다 |
| 의존성 | `mlx>=0.31.2`(Darwin 한정) · `transformers>=5.0.0` 등 | wheel `METADATA` | 루트 venv에 `pip install --dry-run` 결과 **신규 설치 5종만**(`mlx-0.32.2`·`mlx-lm-0.31.3`·`mlx-metal-0.32.2`·`protobuf-7.36.1`·`sentencepiece-0.2.2`)이고 기존 패키지 변경은 0이다(transformers 5.14.1이 이미 충족). 다만 이후 버전에서는 달라질 수 있다(D-181 유형 → G-4). `uvx` 임시 환경은 Python 3.13에 34개 패키지를 설치했다 |

`ChatOpenAI`(langchain-openai 1.3.2) 필드도 실측했다: `base_url`→`openai_api_base`, `max_tokens`(alias `max_completion_tokens`), `timeout`→`request_timeout`, `extra_body`. 단위 테스트는 이 실제 필드명으로 단언한다.

---

## 3. 설계

### 3.1 배치 형태 — 서버 분리(채택)

| 안 | 내용 | 판정 |
|---|---|---|
| **A. 서버 분리** | `mlx_lm.server`를 루프백에 띄우고 앱은 `ChatOpenAI`로 HTTP 호출 | **채택.** 운영 vLLM 오케스트레이터와 같은 모양이다(`src/llm.py:129` `_create_orchestrator_vllm`). 앱에 새로 생기는 의존성이 없다. 다른 맥에서 띄워도 된다. 설정 리로드(`/admin/settings/reload`) 때 모델을 다시 적재하지 않는다. 프롬프트 캐시가 앱 재시작과 무관하게 유지된다 |
| B. 인프로세스 | 앱 프로세스에서 `mlx_lm.generate`나 `langchain_community`의 MLX 래퍼를 직접 호출 | 기각. FastAPI 이벤트 루프 안에서 GPU 연산이 돈다. 앱 메모리에 가중치와 수 GB의 캐시가 상주한다. 리로드 때마다 재적재한다. tool 파서가 없다. uvicorn 워커 수만큼 중복 적재된다 |
| C. 코드 무변경 | `ORCHESTRATOR_PROVIDER=vllm` + mlx URL, 워커는 `LLM_PROVIDER=fabrix`의 OpenAI 호환 모드로 위장 | 기각. ① 512토큰 절단 방지와 thinking off가 서버 기동 플래그에만 숨는다 — 플래그를 빠뜨리면 **응답이 잘리거나 빈다**(Phase 0 실측, 침묵적 강등). ② 산출물·벤치 메타(`scripts/eval_text2sql.py:1225-1227`)에 `vllm`/`fabrix`로 기록돼 **운영 기준선과 구별할 수 없다**(D-174 부기 위반 위험). ③ fabrix 위장은 더미 `FABRIX_API_KEY`가 필요하고 tool 정의가 few-shot 텍스트로 주입되는 등 의미가 오염된다 |
| D. Ollama `/v1` 재사용 | Ollama OpenAI 호환 엔드포인트를 오케스트레이터로 사용 | **대안으로 남긴다.** 이 장비에서 Ollama `/v1/models`가 응답하는 것은 확인했다. MLX를 고른 근거는 ① 9B 가중치를 이미 보유했다는 사실(S-4)과 ② Apple Silicon에서 더 빠를 것이라는 **가정**이다. MLX 쪽 수치(생성 47.6 tok/s · prefill 320~370 tok/s)는 Phase 0에서 확보했고, Ollama 쪽 같은 크기 모델 수치는 Phase 4에서 잰다. 우위가 없으면 G-1에서 재판단한다 |

**귀결**: 앱 코드는 HTTP 클라이언트일 뿐이므로 **앱 쪽 플랫폼 가드(`sys.platform`)를 두지 않는다.** 플랫폼 제약은 `mlx-lm` 설치 단계에만 있다.

### 3.2 provider 값 `mlx` — 평면별 매핑

| 평면 | 설정 | 생성 | 가용성 판정 |
|---|---|---|---|
| **워커**(데이터 평면) | `LLM_PROVIDER=mlx` | `create_llm` → 신규 `_create_mlx(config)` → **`MLXChatOpenAI`**(`ChatOpenAI` 서브클래스, 신규 `src/clients/mlx_client.py`) | 해당 없음(요청 시 연결 오류가 그대로 노출된다) |
| **오케스트레이터**(제어 평면) | `ORCHESTRATOR_PROVIDER=mlx` | `create_orchestrator_llm` → 기존 `_create_orchestrator_vllm` 재사용, mlx일 때만 kwargs 2개 추가(아래) | **코드 무변경.** `orchestrator_available`의 else 분기가 이미 `vllm_healthy(ORCHESTRATOR_BASE_URL)`다(`src/orchestration/deep_agent.py:66-72`). noise_gate의 `_select_backend`도 else 분기라 그대로 통한다(`noise_gate/application/nodes/agentic_enricher.py:162-170`) |

**워커 `_create_mlx` 생성 인자**

```python
MLXChatOpenAI(
    base_url=config.llm.mlx_base_url,
    api_key="EMPTY",
    model=config.llm.mlx_model or "default_model",   # 빈 값이면 서버 --model로 매핑
    temperature=0.0,                                   # ollama·gemini·vllm과 동일
    max_tokens=config.llm.mlx_max_tokens,              # 서버 기본 512 절단 방지(Phase 0 ①)
    timeout=config.llm.mlx_timeout,
    extra_body={"chat_template_kwargs": {"enable_thinking": config.llm.mlx_enable_thinking}},
)
```

- `langchain_openai`는 **함수 안에서 lazy import**한다. `langchain-openai`는 `deepagents` extra에만 속하기 때문이다(`pyproject.toml:52-57`). 미설치면 설치 명령을 담은 `ValueError`를 낸다.
- `src/clients/__init__.py`(`:6-8`, 3개 클라이언트를 eager import)에 **등록하지 않는다.** 등록하면 `src.clients`를 import하는 순간 `langchain_openai`가 필수가 된다.
- `chat_template_kwargs`는 **모델명과 무관하게 붙인다(Phase 0으로 확정).**
  - 필요한 이유: 미전송이면 Qwen3.5가 43초 동안 2048토큰을 reasoning에 쓰고 **`content`가 빈 문자열**이다(J-1 ②). 기존 vLLM 경로의 `qwen` 이름 가드(`src/llm.py:149`)는 `model="default_model"`일 때 **빗나가 정확히 이 상태**를 만든다.
  - 무해한 이유: 템플릿이 이 변수를 쓰지 않는 Qwen2.5-7B에 보내도 오류 없이 정상 응답했다(J-1 ⑥).

**왜 `ChatOpenAI`를 그대로 쓰지 않고 서브클래스를 두는가 — Phase 0 J-3으로 확정**

구조화 출력 어댑터는 **클래스명으로 모드를 고른다**. 클래스명이 `{"ChatOpenAI","AzureChatOpenAI"}`면 `TOOLS`, 그 밖이면 `MD_JSON`이다(`src/clients/instructor_adapter.py:41` · `:94`). 그런데 `TOOLS`를 받쳐야 할 `_lc_create`는 instructor가 넘기는 tools kwargs를 **전달하지 않고** 텍스트만 돌려준다(`src/clients/instructor_adapter.py:231-237`).

J-3 mock 재현 결과는 다음과 같다.
- `ChatOpenAI` 이름: 평문·코드펜스 JSON 모두 `StructuredOutputError`("No tool calls or function call found in response (mode: TOOLS)")
- `MLXChatOpenAI` 이름: `markdown_json_mode`로 둘 다 정상 파싱

서브클래스는 운영 워커 FabriX와 **같은 `MD_JSON` 모드**라 충실도도 오른다. 이 의존은 암묵적이니 **단위 테스트로 못 박는다**(T-5).

**오케스트레이터 mlx 분기 — `_create_orchestrator_vllm` 안의 변경은 이것뿐이다**

```python
is_mlx = config.orchestrator.provider == "mlx"
if is_mlx or "qwen" in config.orchestrator.model.lower():   # vllm 경로 조건은 그대로
    extra_body = {...}
...
if is_mlx:
    kwargs["max_tokens"] = config.llm.mlx_max_tokens         # vllm 경로는 미전송 유지
```

- `base_url`·`model`·`timeout`·`verify_ssl`·`enable_thinking`은 **`ORCHESTRATOR_*`를 그대로 쓴다.** 그래야 noise_gate의 health check(`orchestrator.base_url`)가 무변경으로 맞는다.
- `max_tokens`만 `LLM_MLX_MAX_TOKENS`를 공유한다. 평면 간 참조의 선례는 Gemini 오케스트레이터의 `llm.gemini_model` 폴백이다(`src/llm.py:189~`).
- `deep_agent.py:128` 로그가 "오케스트레이터=vLLM"으로 고정돼 있다. `config.orchestrator.provider`로 바꾼다(식별성 — 한 줄).

**건드리지 않는 것**

- `worker_provider_override`의 Literal(`src/config.py:1258`): `LLM_PROVIDER=mlx`로 충분하다(YAGNI).
- KBGenAI 클래스 기반 분기 24곳(`is_kbgenai(`·`isinstance(llm, KBGenAIChat)`·`type(llm) is KBGenAIChat` grep 2026-09-17, 정의 `src/utils/llm_compat.py:16`): mlx는 비-KBGenAI라 기존 OpenAI 스타일 메시지 경로를 탄다.
- `column_deriver`의 `bind_tools`(`src/nodes/column_deriver.py:274`): `ChatOpenAI` 네이티브로 동작한다. 게이트 `stepwise_derivation`은 기본 off다.
- 토큰 스트리밍: `ChatOpenAI`가 `_astream`을 구현하므로 `astream_text`의 SSE가 **실제로 토큰 단위로** 흐른다. Phase 0에서 45청크, 첫 토큰 0.37초였다(S-3 대비 개선, 부작용 아님).

### 3.3 설정 필드 — `LLMConfig`(env_prefix `LLM_`)에만 추가

| 키 | 기본값 | 근거 |
|---|---|---|
| `LLM_PROVIDER` | Literal에 `"mlx"` 추가 | `src/config.py:23` |
| `LLM_MLX_BASE_URL` | `http://127.0.0.1:8080/v1` | `mlx_lm.server` 기본 host·port(CLI 기본값, Phase 0 기동 로그 `Starting httpd at 127.0.0.1 on port 8080`) |
| `LLM_MLX_MODEL` | `""` → `"default_model"` 전송 | 서버 `--model`로 매핑돼 이름 불일치 재적재를 피한다(J-1 ⑦ 동작 확인). 단 응답 `model` 에코가 `default_model`이라 산출물에 실모델명이 남지 않으므로, **`.env` 예시에는 실 ID를 명시**한다 |
| `LLM_MLX_MAX_TOKENS` | `4096` | 서버 기본 512 절단 방지(J-1 ①: 미전송 512 `length` / 4096이면 1,492토큰 `stop`). 워커·오케스트레이터(mlx) 공용 |
| `LLM_MLX_TIMEOUT` | **`600`**(v1 잠정 300에서 변경) | 한 요청의 최악 경우 = 콜드 prefill + 최대 생성이다. 프롬프트 예산 `prompt_token_budget=90_000`(추정치 기준, `src/config.py:370`)은 추정이 10~13% 과소하므로 실 토큰 약 10만이다 → 콜드 prefill 320 tok/s 기준 약 310초. 여기에 생성 4,096토큰 ÷ 47.6 tok/s ≈ 86초를 더하면 **약 400초**다. 일반 규모(템플릿+스키마 16K토큰) 콜드는 44초, 접두 캐시 적중 시 1초 미만이다(J-2) |
| `LLM_MLX_ENABLE_THINKING` | `false` | 미전송·true면 reasoning이 별도 필드로 빠져 버려지고 `content`가 빌 수 있다(J-1 ②). D-042 no-think 방침과 정합 |

- `OrchestratorConfig`: Literal에 `"mlx"`만 추가한다(`src/config.py:103`). **새 필드는 없다.** 오케스트레이터 요청 타임아웃은 기존 `ORCHESTRATOR_TIMEOUT`(기본 120초, `src/config.py:107`)이다. 오케스트레이터 프롬프트가 콜드에서 120초를 넘는지는 Phase 4에서 재고, 넘으면 **로컬 `.env`에서만** 올린다(코드 기본값 변경 없음).
- 모든 신규 필드는 provider가 `mlx`가 아니면 읽히지 않는다 → **기본 off = 현행 비트 동일** 원칙(`plans/80` §5.4-③)을 충족한다.
- `.env` list/dict 이슈는 없다(전부 스칼라). 인라인 주석도 금지한다(Known Mistakes).

### 3.4 과금 게이트 — `mlx` 면제 + 오케스트레이터 평면 판정 (G-2 · G-3)

**실측된 빈틈.** 비과금 판정 집합이 세 곳에 따로 정의돼 있고, **모두 `llm.provider` 하나만 본다.**

| 위치 | 정의 | 판정 입력 |
|---|---|---|
| `scripts/scenario/preflight.py:37` | `INTERNAL_PROVIDERS = {"fabrix","ollama"}` | `cfg.llm.provider`(`:114`) |
| `scripts/scenario/__main__.py:41` | 동일 | `load_config().llm.provider`(`:49`) |
| `scripts/bench/__main__.py:42` | `_INTERNAL_PROVIDERS` 동일 | 에코 `llm.provider`(`:46`) |

S-1의 `.env`에서 **`LLM_PROVIDER`만 `mlx`(지금도 `ollama`면 동일)로 바꾸면**, `python -m scripts.scenario`를 인자 없이 실행했을 때 **승인 없이 전 시나리오를 실 실행**한다(`scripts/scenario/__main__.py:372-379`). 그 사이 오케스트레이터는 여전히 Gemini를 호출한다 → **D-127 우회.** 기존 `ollama`에도 있던 빈틈이지만, MLX 도입은 "워커만 로컬로 바꾸는" 조합을 흔한 설정으로 만든다.

**제안**

- 비과금 집합을 평면별로 둔다. 워커 `{"fabrix","ollama","mlx"}`, 오케스트레이터 `{"vllm","mlx"}`.
- 판정을 "**평가 대상 평면 중 하나라도 집합 밖이면 외부**"로 바꾼다.
- G-3 권장안은 오케스트레이터 평면을 `ENABLE_DEEPAGENTS_PACKAGE`와 무관하게 **항상** 판정하는 것이다. noise_gate agentic enricher 트랙 B도 같은 오케스트레이터를 쓰므로(`noise_gate/infrastructure/noise_signal_tools.py:244-247`) 플래그 조합 추론은 오판 여지만 남긴다.
- 세 곳의 중복 정의는 판정 규칙이 바뀌는 김에 **정의 1곳 + 소비 3곳**으로 모은다. 위치는 구현 시 import 관계를 실측해 정한다(후보는 이미 정본 주석을 단 `scripts/scenario/preflight.py`).
- bench는 서버 설정 에코에서 판정한다. **J-4 판독 결과 에코가 `AppConfig` 전체를 평탄화하므로 `orchestrator.provider`가 이미 실린다**(`scripts/bench/probe.py:36-71`) → 에코 쪽 변경은 필요 없다.
- 결정 개정: **D-211 ⑨**(`docs/02_decision.md:2037`, "판정은 코드가 `LLM_PROVIDER`로")와 **D-216 ①**(내부망 = fabrix·ollama)을 개정한다.

**행동 변화(명시)**: G-3을 채택하면 **`LLM_PROVIDER=ollama` + `ORCHESTRATOR_PROVIDER=gemini` 조합도 승인이 필요해진다.** 안전 쪽 변화지만 기존 동작 변경이므로 사용자 확정 대상이다.

### 3.5 preflight 보강 — MLX 서버 사전 점검

"사람이 판정하던 사전 점검을 코드가 한다"(`a0bce74`)는 흐름에 맞춰 `scripts/scenario/preflight.py`에 점검을 더한다. provider가 `mlx`인 평면마다 확인한다.

| 점검 | 판정 | 조치 문구(무엇을 하는가) |
|---|---|---|
| `GET {base_url}/models`가 200인가 | 아니면 **STOP** | 서버 기동 명령 한 줄(§3.6) |
| 설정 모델 ID가 `default_model`이거나 `/v1/models` 목록에 있는가 | 아니면 **WARN**(로컬 경로로 띄우면 목록에 없을 수 있어 STOP으로 두지 않는다. 틀린 ID는 요청 시 즉시 404로 드러난다 — J-1 ⑧) | "`--model`과 같은 값으로 맞춘다" |
| 워커·오케스트레이터가 같은 `base_url`인데 모델 ID가 다른가 | **WARN** | "요청마다 3.6~5.3초 교대 재적재(J-1 ⑥) — 한 모델로 맞추거나 포트를 나눈다" |
| `base_url` 호스트가 루프백인가 | 아니면 **WARN** | "MLX 서버는 무인증·CORS `*` — 원격이면 신뢰 구간인지 확인한다"(R-8) |

`/health`·`/v1/models`는 모델 적재 전에도 200이므로(Phase 0) 이 점검은 **도달성까지만** 판정한다. 적재·첫 요청 지연은 판정하지 않는다.

### 3.6 설치 · 기동

> **v3.1(사용자 지시 2026-09-17)**: 테스트 시 직접 띄우는 기동 스크립트 **`scripts/mlx_server.sh`**가 정본 기동 방법이다.
> - 스크립트는 앱과 같은 설정 키 `LLM_MLX_MODEL`·`LLM_MLX_BASE_URL`(포트 명시 필수)을 앱과 같은 순서(셸 환경변수 > `.env`, `.env`는 `source`하지 않음)로 읽어 아래 명령을 `127.0.0.1`에 포그라운드로 실행한다. PATH에 `mlx_lm.server`가 없으면 `uvx`로 실행한다.
> - v3.1 후속 교정(팀 리드 지적 2건 실측 반영): ①셸 환경변수를 무시하고 `.env`만 읽어 앱(pydantic — 셸 우선)과 포트·모델이 어긋날 수 있었다 ②포트 없는 URL을 8080으로 오판했다(앱은 80/443) → 셸 우선 판독 · 포트 미명시 시 중단으로 고쳤다. bash 3.2에서 7경로 드라이런 확인.
> - 기동 전 점검: Apple Silicon 확인, 포트 중복·기동 중 서버, HF 캐시 모델 존재(중단), `.env`의 워커·오케스트레이터 모델·포트 불일치(경고). `--dry-run`, `MLX_ALLOW_DOWNLOAD=1`을 지원한다.
> - 아래 원시 명령은 스크립트가 내부에서 실행하는 형태로 참고용이다(사용 안내는 `docs/03_setup_guide.md` §7.2).

G-4 권장안(격리 설치)을 기준으로 적는다. `uv`는 이 장비에 설치돼 있다(`~/.local/bin/uv`).

```bash
# 1회 설치 — 루트 venv와 격리 (D-181 유형 예방)
uv tool install "mlx-lm==0.31.3"
# (설치 없이 한 번만 돌려 보려면: uvx --from "mlx-lm==0.31.3" mlx_lm.server ... — Phase 0이 이 방식)

# 기동 — 반드시 127.0.0.1 바인딩 (서버는 무인증 · CORS 기본 "*")
HF_HUB_OFFLINE=1 mlx_lm.server \
  --model mlx-community/Qwen3.5-9B-OptiQ-4bit \
  --host 127.0.0.1 --port 8080 \
  --max-tokens 4096 \
  --chat-template-args '{"enable_thinking":false}' \
  --prompt-cache-bytes 6GB
```

- 서버 쪽 `--max-tokens`·`--chat-template-args`는 **이중 안전장치**다. 정본은 클라이언트가 보내는 값이다(§3.2). Phase 0은 이 두 플래그 **없이** 클라이언트 값만으로 정상 동작함을 확인했다.
- `HF_HUB_OFFLINE=1`은 모델 ID를 잘못 적었을 때 조용히 수 GB를 받지 않고 **즉시 404로 실패**시키기 위해 둔다(J-1 ⑧).
- `--prompt-cache-bytes 6GB`는 **잠정값**이다. 상한이 없으면 측정 중 캐시가 13.33GB까지 커졌다(J-2). 상한을 너무 낮추면 축출된 프롬프트가 다시 콜드 prefill(16K토큰 44초)을 치르므로, Phase 4에서 노드별 서로 다른 시스템 프롬프트 수에 맞춰 조정한다(R-10).
- **워밍업**: 서버 기동 직후 첫 질의는 노드마다 시스템 프롬프트가 콜드다. 개발 `.env`의 `API_QUERY_TIMEOUT=240`(코드 기본 60)에 걸릴 수 있으니, 첫 질의를 한 번 돌려 캐시를 채운 뒤 검증을 시작한다(R-11).

`.env`(한 서버·한 모델로 두 평면을 모두 태운다 — 32GB에서 재적재가 없는 구성):

```
LLM_PROVIDER=mlx
LLM_MLX_BASE_URL=http://127.0.0.1:8080/v1
LLM_MLX_MODEL=mlx-community/Qwen3.5-9B-OptiQ-4bit
ORCHESTRATOR_PROVIDER=mlx
ORCHESTRATOR_BASE_URL=http://127.0.0.1:8080/v1
ORCHESTRATOR_MODEL=mlx-community/Qwen3.5-9B-OptiQ-4bit
```

앱 설치는 기존과 같다. 워커 mlx만 쓸 때도 `langchain-openai`가 필요하므로 `pip install -e ".[dev,document,deepagents]"`를 쓴다(`requirements.txt:10`에도 평면 목록으로 들어 있다).

---

## 4. 수정 단위

| CU | 대상 | 변경 | 규모 |
|---|---|---|---|
| **CU-1** | `src/config.py:23` · `:103` | `LLMConfig` Literal에 `mlx` 추가 + 필드 5종(§3.3) · `OrchestratorConfig` Literal에 `mlx` 추가 | 소 |
| **CU-2** | `src/clients/mlx_client.py`(신규) | `MLXChatOpenAI(ChatOpenAI)` — `_llm_type`="mlx"만 재정의, 동작은 무변경. 모듈 docstring에 "클래스명이 instructor 모드 선택에 쓰인다(J-3)"를 명시 | 소 |
| **CU-3** | `src/llm.py:69-103` · `:106-126` · `:129~` | `create_llm`에 mlx 분기 + `_create_mlx` · `create_orchestrator_llm`의 mlx → `_create_orchestrator_vllm` · `is_mlx` kwargs 2종 · 모듈 docstring "지원 프로바이더" 갱신 · `src/orchestration/deep_agent.py:128` 로그 라벨 | 소 |
| **CU-4** | `scripts/scenario/preflight.py` · `scripts/scenario/__main__.py` · `scripts/bench/__main__.py` | 비과금 판정 단일화 + mlx 편입 + 오케스트레이터 평면 판정(G-2·G-3). bench 에코 변경 불요(J-4) | 중 |
| **CU-5** | `scripts/scenario/preflight.py` | MLX 서버 점검 4건(§3.5) | 소 |
| **CU-6** | `src/api/settings_catalog.py:274-293` · `.env.example` LLM 섹션(`:18-49`) · ORCHESTRATOR 섹션(`:439-440`) | `RELOADABLE_KEYS`에 `LLM_MLX_*` 5종(알람 워커 비대칭 주석 대상) · 예시 키·주석(주석이 웹 UI 도움말로 노출됨) | 소 |
| **CU-7** | `docs/03_setup_guide.md` §7(`:698` Ollama 절 다음) · `docs/21_orchestration_ladder.md:55-57` · `CLAUDE.md` Tech Stack provider 행·개발 명령 · `docs/02_decision.md` | MLX 절 신설(§3.6 명령·워밍업·캐시 상한) · 사다리 가용성 판정에 `provider=mlx` 줄 · 신규 D + D-211 ⑨·D-216 ① 개정 표기 | 소 |

**게이트 영향 없음(실측)**

- `arch_check`: `src.llm`·`src.clients`는 infrastructure 계층이고(`scripts/arch_check.py:55-56`) 외부 패키지만 새로 import한다.
- `overfit_check`: `src/llm.py`·`src/clients/`는 스캔 대상이 아니다(`scripts/overfit_check.py:53-69`).

---

## 5. 랜딩 순서

### Phase 0 — 조사 결과 (2026-09-17 완료 · 코드 0)

**실행 조건**

- 서버: `HF_HUB_OFFLINE=1 uvx --from "mlx-lm==0.31.3" mlx_lm.server --model mlx-community/Qwen3.5-9B-OptiQ-4bit --host 127.0.0.1 --port 8080`
  - `uvx` 임시 환경이다. 루트 venv에도 `uv tool`에도 설치하지 않았으므로 G-4를 선점하지 않는다.
  - **`--max-tokens`·`--chat-template-args` 플래그 없이** 띄웠다. 서버 기본값을 드러내고, 클라이언트 설계만으로 충분한지 보기 위해서다.
- 클라이언트: 루트 venv의 `langchain-openai` 1.3.2 · `openai` 2.26.0. `ChatOpenAI`/`OpenAI`를 **직접 생성**했다.
  - `load_config()`를 쓰지 않았고, 셸의 `LLM_PROVIDER`·`ORCHESTRATOR_PROVIDER`를 제거하고 실행했다. `.env`의 gemini가 재독돼 실 호출된 선례(`docs/18_known_mistakes.md:100`)를 막기 위해서다.
  - 호출은 전부 루프백이라 과금·D-127 대상이 아니다.
- 서버는 측정 후 종료했고 포트 8080이 해제된 것을 확인했다. 스크립트·로그는 세션 스크래치에만 두었다(저장소 미반입).

**J-1 — 서버 × `ChatOpenAI` 왕복**

| # | 확인 | 결과 | 설계 귀결 |
|---|---|---|---|
| 기동 | `/health` 200까지 | 8초(uvx 설치 포함). 첫 요청 3.2초(모델 적재 포함) | health 200 ≠ 적재 완료 → preflight는 도달성만(§3.5) |
| ① 절단 | 400줄 출력 요청, `max_tokens` **미전송** | `finish_reason=length` · **정확히 512토큰**(151번째 줄에서 끊김) · 11.3초 | **`max_tokens` 명시 확정**(§3.2) |
| ① | `max_tokens=4096` | `stop` · 1,492토큰 · 31.3초 · **생성 47.6 tok/s** | 4096 기본값 유지 |
| ② thinking | `chat_template_kwargs` **미전송** | **43.0초 · 2,048토큰 전부 reasoning(7,621자) · `content` 빈 문자열** | **무조건 부착 확정**(§3.2) |
| ② | `enable_thinking=false` | 0.4초 · 10토큰 · 정상 SQL | — |
| ③ tool calling | `ChatOpenAI.bind_tools`(도구 1개) × 5회 | **5/5** `AIMessage.tool_calls` 정상. 인자 타입 유지(`hostname: str`·`top_n: int`) · `invalid_tool_calls` 0 · 회당 1.1~2.1초. `ToolMessage` 왕복 후 한국어 최종 답변 정상 | G-1 (A) 유지. **단 도구 1개·단일 턴이다** — 다도구 deepagents 루프는 Phase 4(R-1) |
| ④ 스트리밍 | `astream` | 45청크 · 첫 토큰 0.37초 | 토큰 SSE 실동작(S-3 대비 개선) |
| ⑤ 동시성 | 동시 3건 `asyncio.gather` | 1.1초 · 전부 정답 | 병렬 서브에이전트 수용 |
| ⑥ 비-Qwen3.5 | Qwen2.5-7B에 `chat_template_kwargs` 전송 | 오류 없음·정상 응답. 교대 재적재 5.3초, 9B로 복귀 3.6초 | 무조건 부착 무해 확정 · R-6 수치화 |
| ⑦ 별칭 | `model="default_model"` | 정상. 응답 `model` 에코는 `default_model` | §3.3 빈 값 폴백 성립 · 예시는 실 ID 명시 |
| ⑧ 잘못된 ID | `model="Qwen3.5-9B"`(`HF_HUB_OFFLINE=1`) | **즉시 404 `NotFoundError`**("Cannot find an appropriate cached snapshot … outgoing traffic has been disabled") | 침묵 없음 · `HF_HUB_OFFLINE=1` 권장 확정 |

**J-2 — 실 프롬프트 prefill**

입력은 실 폴스타 조회 시스템 템플릿(`render_system_template()`)이다. B행부터는 스키마 대용으로 `config/db_profiles/polestar_cm_gp.yaml` 본문을 붙였고, `max_tokens=8`로 prefill만 쟀다.

| 입력 | 문자 수 | `estimate_prompt_tokens` | 실 `prompt_tokens` | 콜드 | 같은 프롬프트 재요청 |
|---|---:|---:|---:|---:|---:|
| A. 템플릿 | 13,851 | 4,456 | 5,029 | 21.5초 | 0.4초 |
| B. 템플릿+프로필 | 47,288 | 14,573 | 16,122 | 43.7초 | 0.4초 |
| C. B×2 | 94,576 | 29,147 | 32,202 | 89.9초 | 1.0초 |
| D. B×4 | 189,152 | 58,294 | 64,362 | 202.9초 | 2.1초 |

- 콜드 prefill은 **초당 320~370토큰**이다(A의 234는 적재 직후 첫 대형 요청 값).
- **질문만 바꾼 요청**(시스템 프롬프트 동일): `cached=16,087` · 0.8~0.9초. Qwen3.5 혼합 어텐션 구조에서도 **접두 캐시가 재사용된다.** Qwen2.5-7B도 같았다(콜드 38.0초 → 질문 변경 0.7초). "프롬프트 접두 고정(KV 캐시)" 원칙이 로컬에서도 효과를 낸다.
- 휴리스틱 `estimate_prompt_tokens`(`src/nodes/prompt_blocks.py:638`)는 Qwen3.5 토크나이저 실측보다 **10~13% 과소 추정**한다(5,029÷4,456=1.13 · 64,362÷58,294=1.10). `LLM_MLX_TIMEOUT` 산정에 반영했다(§3.3).
- 프롬프트 캐시가 측정 중 **9.1~13.33GB(10시퀀스)**까지 커졌다(서버 로그 `Prompt Cache:`) → R-10.
- **이상치 1건(원인 미확정)**: 같은 질문 재요청이 캐시 적중(`cached=16,118`, 실제 처리 4토큰)인데 33.9초 걸렸다. 서버 로그상 처리 시작 **전** 대기였고, 직전 캐시가 13.33GB였다. 메모리 압박 가설이며 Phase 4에서 `--prompt-cache-bytes` 적용 후 재현 여부를 본다.

**J-3 — instructor 모드(mock, 서버 불필요)**

| 워커 클래스명 | 선택 모드 | 평문 JSON | 코드펜스 JSON |
|---|---|---|---|
| `ChatOpenAI` | `tool_call`(TOOLS) | `StructuredOutputError` — "No tool calls or function call found in response (mode: TOOLS)" | 동일 실패 |
| `MLXChatOpenAI` | `markdown_json_mode` | 정상 파싱 | 정상 파싱 |

→ **§3.2 서브클래스 근거 확정.** 부수 발견으로, TOOLS 재질의 경로에서 `'_Msg' object has no attribute 'role'`이 났다(§10 — 기록만).

**J-4 — bench 에코(코드 판독)**

에코 스니펫이 `load_config()` 결과를 `model_dump()`로 전부 평탄화한다(`scripts/bench/probe.py:36-71`) → `orchestrator.provider` 키가 이미 실린다. **에코 추가 불필요.**

**Phase 0이 바꾼 설계**

- 변경 3건: ① `LLM_MLX_TIMEOUT` 300 → **600** ② 기동 권장에 `--prompt-cache-bytes`·워밍업 추가 ③ preflight 루프백 점검 추가 · R-10·R-11 신설
- 근거만 확정된 설계(변경 없음): `max_tokens` 명시 · `chat_template_kwargs` 무조건 부착 · 서브클래스 · 평면 매핑

### Phase 1 — 설정 · 팩토리 (CU-1 · CU-2 · CU-3)

단위 테스트는 네트워크 0이다(전역 소켓 가드는 루프백을 허용하지만 단위 테스트는 서버에 의존하지 않는다). §6 표의 T-1~T-7을 쓴다.

### Phase 2 — 과금 게이트 (CU-4) · **G-2·G-3 확정 후에만**

### Phase 3 — preflight · 카탈로그 · 예시 env (CU-5 · CU-6)

### Phase 4 — 로컬 종단 검증 (실 MLX 서버)

§3.6 기동 명령을 쓴다(플래그 포함). **워밍업 1회 후**에 측정하고, 콜드·웜 수치는 분리해 기록한다.

| 검증 | 방법 |
|---|---|
| 단일 DB 워커 경로 | `python -m src.main --query "…"`(로컬 샌드박스 `polestar`) — 응답 성공 |
| 정본 1단 사다리 | 기동 로그 `record_ladder_resolution`(`src/graph.py:720`)의 tier = `deep_agent` · 트레이스에 tool_calls 1건 이상 · **다도구 루프 완주**(Phase 0 ③은 도구 1개였다) |
| 오케스트레이터 콜드 지연 | 제어 평면 첫 요청이 `ORCHESTRATOR_TIMEOUT`(120초) 안인가 — 넘으면 로컬 `.env`만 상향 |
| 토큰 스트리밍 | SSE `user_response` 태그 청크 수 > 1 |
| noise_gate 트랙 선택 | `_select_backend` → `track_b`(서버 기동 시) / `track_a`(서버 중지 시) |
| 긴 응답 절단 | 표 합성 응답에서 `finish_reason="length"` 발생 여부 → `LLM_MLX_MAX_TOKENS` 확정 |
| 캐시 상한 | `--prompt-cache-bytes 6GB`에서 노드별 시스템 프롬프트 축출 빈도(서버 로그 `Prompt Cache:`) · J-2 이상치(캐시 적중인데 33.9초) 재현 여부 |
| 성능 **기록** | 첫 토큰 시간 · tok/s · 단순 질의 종단 시간(콜드/웜). 목표(<10s)는 **판정이 아니라 기록**이다(운영 기준선 아님) |
| (G-1 선택) Ollama 비교 | 같은 크기 모델로 prefill·생성 tok/s 비교 → MLX 채택 근거 ② 확인 |

#### Phase 4 결과 (2026-09-17 · v3)

**조건**: `uvx --from "mlx-lm==0.31.3"` 서버를 `127.0.0.1:8080`에 §3.6 플래그 그대로 기동(측정 후 종료·포트 해제 확인). 앱은 셸 환경변수로만 설정했다 — `LLM_PROVIDER=mlx` · `ORCHESTRATOR_PROVIDER=mlx` · `ORCHESTRATOR_BASE_URL`·`ORCHESTRATOR_MODEL`·`LLM_MLX_*` 명시, **`LLM_GEMINI_API_KEY`·`GOOGLE_API_KEY`·`ORCHESTRATOR_API_KEY`를 빈 값으로 덮어** 과금 경로가 인증 자체를 못 하게 했다(실행 전 `load_config()`로 두 provider=`mlx`·키 빈 값 실측). `ENABLE_STRUCTURE_APPROVAL=false` · `ALARM_ENABLED=false`(공유 알람 스트림 비소비) · API는 `127.0.0.1:8051`. DB는 기존 기동 중이던 MCP 서버(9099)·로컬 샌드박스 `polestar`(5434)·Redis(6380)를 읽기만 했다. CLI는 스레드 `cli-session`을 재사용해 체크포인트 이력(turn 22~23)이 쌓인 상태였다. **과금 호출 0.**

| 검증 | 결과 |
|---|---|
| 단일 DB 워커 경로(3단 `semantic_router`, CLI) | **성공** — CPU·메모리 상위 5 응답. CLI 종단 78.8초(서버 기동 후 첫 질의 · 프로세스 기동 포함) / 56.3초(다른 질문) |
| 정본 1단 사다리 | **`tier=deep_agent degraded_reason=none`** 2회 확정 · 조립 로그 `오케스트레이터=mlx(…), 도구 7개, 워커=mlx` · 도구 호출 관측: `data_query` → `alarm_query`(`input_from=['tool_data_query_1']` 선행 스코프 주입) → `alarm_query` 재호출 |
| 다도구 루프 완주 | **부분.** ①2도구 질의(CPU 상위 3 + 메모리 상위 3) **249초 완주** — SQL 2건 각 3행, 단 **최종 응답이 메모리 결과를 누락**("메모리 사용률 관련 컬럼이 없으므로"). 두 조회가 병렬 호출이었는지는 로그로 확정하지 못했다(선행 결과 없는 첫 호출은 `deepagents 도구 호출` 로그를 남기지 않는다) ②알람 결합 질의(CPU 상위 3 + 활성 알람)는 **미완** — 오케스트레이터가 존재하지 않는 `server_events` 테이블 SQL을 `sub_query`로 넘겼고, 워커 SQL 생성이 컬럼 나열로 퇴행해 **4096토큰에서 절단**(SQL 14,649자 · `missing FROM-clause entry for table "r"`) · 재시도 4회 각 약 108초 → 1,200초 제한에서 중단 |
| 오케스트레이터 콜드 지연 | 첫 요청(프롬프트 7,255토큰) **약 20초** — `ORCHESTRATOR_TIMEOUT` 120초 이내, 상향 불필요 |
| 토큰 스트리밍 | `/api/v1/query/stream` **`token` 이벤트 200건**(>1). 첫 토큰 62.6초 / 종단 66.9초(서버 기동 후 첫 질의) · 같은 질문 새 스레드 43.7초 / 48.1초 |
| noise_gate 트랙 선택 | 서버 기동 **`track_b`** · 서버 중지 **`track_a`**(fallback `semantic_routing`) |
| 긴 응답 절단 | 400행 표 요청 **`finish_reason=length`** · 출력 4,096토큰(89.7초). 파이프라인에서도 발생(위 ②). **`LLM_MLX_MAX_TOKENS` 4096 유지** — 실사례는 모델 퇴행이라 상향해도 풀리지 않고 시도당 지연만 는다(8192면 시도당 약 3.5분) |
| 캐시 상한 | `--prompt-cache-bytes 6GB`에서 로그 최대 **7.52GB**(일시 초과) · 대부분 1.2~5.9GB. **J-2 이상치(캐시 적중인데 30초대 대기) 재현 안 됨** — 소형 프롬프트 수신→처리 시작 1초 이내. 새 발견: schema_analyzer 프롬프트(약 12K토큰)는 **같은 질문을 다시 보내도** 매번 26~27초 재처리(접두 캐시 미적중 — 앞부분 가변 요소 추정, 원인 미확정) |
| 성능 기록(판정 아님) | 생성 **약 46 tok/s**(4,096토큰/88.9초) · 짧은 프롬프트 첫 토큰 0.86초 · prefill 약 360~450 tok/s(12,147토큰 26~32초 · 25,480토큰 70초) |
| 서버 미기동 preflight(수동) | `[중단] MLX 서버(워커)`·`(오케스트레이터)` + 기동 명령 · 사다리 `intent_orchestration (degraded_reason=orchestrator_unavailable)` |
| (G-1 선택) Ollama 비교 | **미실시** — 로컬 Ollama에 같은 크기 모델이 없다(`qwen3.5:2b`·`0.8b`만, S-4) |

**기동 스크립트 재확인(v3.1 지시 후 · 2026-09-17)**: 위 측정은 스크립트 지시 수신 전 원시 명령으로 했다 — `scripts/mlx_server.sh --dry-run` 출력 인자와 동일(모델·호스트·포트·`--max-tokens 4096`·thinking off·`--prompt-cache-bytes 6GB`·`HF_HUB_OFFLINE=1`). 이후 스크립트로 8080에 다시 띄워 확인했다: `/health` 200(약 2초) · `127.0.0.1` 바인딩 · 셸 덮어쓰기 없이 `load_config()` 두 평면 `mlx`(Gemini 키는 빈 값으로 덮어 실행) · 워커 응답 0.70초 `stop` · 오케스트레이터 `bind_tools` tool call 1건 1.59초 · preflight MLX 4행 `[OK]`·사다리 1단 · 기동 중 재실행은 `오류: 127.0.0.1:8080 에 이미 서버가 떠 있다`로 차단 · 종료 후 8080 해제.

**판독**: 기능 경로(설정·팩토리·사다리·도구 호출·SSE·트랙 선택·preflight)는 모두 동작했다. 무너진 곳은 **모델 품질**이다 — 오케스트레이터가 도구 인자에 SQL을 지어 넣고, 워커가 알람 SQL에서 퇴행했다. §7 G-1은 "다도구 루프가 무너지면 (B)로 강등"이었는데, ②의 직접 원인은 **워커** SQL 생성 퇴행이라 (B)(워커만 `mlx`)로 내려도 풀리지 않는다. 강등 여부는 사용자 판단으로 남긴다(R-1).

### Phase 5 — 문서 · 결정 (CU-7)

---

## 6. 테스트 계획

**원칙**: 테스트 config는 provider를 **명시**한다. 로컬 `.env`의 `ORCHESTRATOR_PROVIDER=gemini`가 새어 들어온 선례가 있다(`docs/18_known_mistakes.md:19`).

| # | 파일 | 단언 |
|---|---|---|
| T-1 | `tests/test_llm_mlx.py`(신규) | `LLMConfig(provider="mlx")`·`OrchestratorConfig(provider="mlx")` 수용 · 신규 필드 기본값(`timeout==600` 포함) |
| T-2 | 〃 | `create_llm(provider=mlx)` → `MLXChatOpenAI` · `openai_api_base`·`model_name`·`max_tokens`·`temperature==0.0`·`request_timeout`·`extra_body.chat_template_kwargs.enable_thinking is False` |
| T-3 | 〃 | `mlx_model=""` → `model_name=="default_model"` |
| T-4 | 〃 | `langchain_openai` import 실패(monkeypatch) → 설치 안내를 담은 `ValueError` |
| T-5 | 〃 | `select_mode(MLXChatOpenAI(...)) == MODE_MD_JSON`. J-3 재현(`ChatOpenAI` 이름이면 TOOLS 실패)이 근거이므로 **유지 확정** |
| T-6 | 〃 | `create_orchestrator_llm(provider=mlx)` → `max_tokens` 설정 · 모델명이 `default_model`이어도 `extra_body` 부착 · **`provider=vllm`은 `max_tokens is None`·비-qwen 모델명이면 `extra_body` 없음(회귀 방어)** |
| T-7 | `tests/test_orchestration/test_deep_agent.py` | `orchestrator_available(provider=mlx)` → `vllm_healthy`가 `ORCHESTRATOR_BASE_URL`로 호출됨(mock) |
| T-8 | `noise_gate/tests/test_agentic_enricher.py` | `provider="mlx"` → health check 경로(gemini 분기를 타지 않음) |
| T-9 | `tests/test_scenario/test_preflight.py:50-53` · `tests/test_scripts/test_bench_report.py:170-171` · `tests/test_scenario/test_cli_surface.py:65` · `tests/test_scenario/test_runner_cli.py` | `mlx` 비과금 · **`llm=mlx` + `orchestrator=gemini` → 외부(승인 필요)** · `ollama`+`gemini` 조합의 판정 변경 반영(G-3) |
| T-10 | `tests/test_scenario/test_preflight.py` | MLX 서버 점검 4건(requests mock) |

**함께 갱신해야 하는 기존 단언(repo grep 실측)**

- `tests/test_api/test_settings_catalog.py:205`: 필드 수 `335` → `340`(그룹 수 `24`는 불변 — `:204`). 병행 작업이 필드를 먼저 늘리면 그 값 +5로 맞춘다.
- `tests/test_api/test_settings_catalog.py:246`: `LLM_PROVIDER.enum_choices`에 `"mlx"` 추가
- `tests/test_api/test_settings_catalog.py:672-680`: `RELOADABLE_KEYS` 일치(CU-6과 동시 갱신)
- `tests/test_llm_gemini.py:42`: `"invalid_provider"` 거부 — 영향 없음(확인만)

`tests/test_api/test_settings_catalog.py:388` · `:450`이 `"openai"`를 무효값 예시로 쓰는데, `mlx` 추가 후에도 무효이므로 영향 없다.

**실 서버 라이브 테스트는 pytest에 넣지 않는다.** Phase 0 스크립트가 같은 확인을 수동으로 재현할 수 있고, 새 옵트인 게이트 개념(`RUN_E2E`와 다른 로컬 전용 플래그)을 늘리지 않는다. 회귀가 반복되면 그때 재검토한다.

---

## 7. 사용자 확정 게이트

> **확정(2026-09-17 · D-222)**: 사용자가 게이트별 답 없이 구현을 지시해 아래 **권장안 전건을 채택**했다(G-1 (A) · G-2 편입 · G-3 항상 포함 · G-4 (a) · G-5 (가) · G-6 Qwen3.5-9B-OptiQ-4bit).

| G | 질문 | 선택지 | 권장 · 근거 |
|---|---|---|---|
| **G-1** | 도입 범위 | (A) 워커 + 오케스트레이터 둘 다 `mlx` / (B) 워커만 / (C) 코드 무변경(§3.1 C안) | **(A).** L-1(1단 기능 검증)을 과금 없이 여는 것이 핵심 가치다. Phase 0 J-1 ③ tool calling 5/5로 전제가 섰다. 다도구 루프가 Phase 4에서 무너지면 (B)로 강등한다. (C)는 Phase 0에서 플래그 누락 시 **응답이 잘리거나 빈다**는 것이 실측돼 기각 근거가 강해졌다 |
| **G-2** | 비과금 집합에 `mlx` 편입(D-211 ⑨·D-216 ① 개정) | 편입 / 미편입(매번 승인) | **편입.** 루프백 로컬이라 과금·외부 송신이 0이다. D-127의 대상은 "과금 외부 API"다 |
| **G-3** | 과금 판정에 `ORCHESTRATOR_PROVIDER` 포함 | 항상 포함 / deepagents 플래그 on일 때만 / 현행 유지 | **항상 포함.** §3.4의 D-127 우회를 막는다. **대가**: `ollama`+`gemini` 오케스트레이터 조합도 승인이 필요해진다 |
| **G-4** | `mlx-lm` 설치 위치 | (a) 루트 venv 밖 격리(`uv tool`) / (b) 루트 `pyproject` extra `mlx`(플랫폼 마커) | **(a).** 서버가 별도 프로세스라 앱과 의존성을 공유할 이유가 없다. Phase 0도 격리 환경(`uvx`, Python 3.13)에서 문제없이 돌았다. 현재 dry-run 영향은 0이지만 공유 venv 파손 선례가 있다(D-181). (b)는 "한 명령 설치" 편의가 장점이다 |
| **G-5** | "맥북일 경우"의 해석 — 플랫폼 자동 감지로 provider를 바꿀 것인가 | (가) 명시 opt-in(`LLM_PROVIDER=mlx`) / (나) Apple Silicon이면 자동 `mlx` | **(가).** ① 플래그는 기동 시 1회 해석하고 설정으로 고정하는 것이 원칙이다. ② 자동 전환은 맥에서 gemini·ollama를 쓰던 개발자의 동작을 **말없이** 바꾼다. ③ 서버가 안 떠 있으면 조용한 강등이 된다(침묵 폴백 금지). ④ 자동 결정을 둔 tri-state 플래그(`ENABLE_SEMANTIC_ROUTING`)도 경고를 남기고 `.env` 명시를 권한다. **대신 CU-5 preflight가 맥에서 MLX 준비 상태를 알려준다** |
| **G-6** | 기본 로컬 모델 | Qwen3.5-9B-OptiQ-4bit / 더 작은 모델(2B) / 더 큰 모델 | **Qwen3.5-9B-OptiQ-4bit.** 캐시 보유(S-4) · 운영 오케스트레이터와 같은 계열 · 32GB에서 한 모델로 두 평면을 공유할 수 있다 · Phase 0 속도(생성 47.6 tok/s, 웜 응답 1초 미만) 실측. 2B는 tool-calling 신뢰성이 더 낮다(R-1) |

---

## 8. 리스크

| R | 내용 | 대응 |
|---|---|---|
| **R-1** | 로컬 소형 모델의 tool-calling 신뢰성. 오픈웨이트 1B~8B는 "tool-calling 신뢰성 부족"이 HolmesGPT로 실측됐다(`plans/64-automated-incident-investigation-and-response.md:542`). **Phase 0에서는 도구 1개·단일 턴 5/5였다** — 도구 수십 개·다단계 계획의 deepagents 루프는 아직 미확인이다 | Phase 4 다도구 루프 완주 검증. 실패 시 G-1 (B) 강등. 1단 강등은 기동 로그 1줄 + 사다리 강등 경고(D-221)로 보인다 |
| **R-2** | 콜드 prefill 지연. **실측 320~370 tok/s**로 16K토큰 44초, 32K 90초, 64K 203초다(J-2). 프롬프트 예산 `prompt_token_budget=90_000`(`src/config.py:370`)은 FabriX 기준이다 | 접두 캐시 적중 시 1초 미만(J-2, 질문만 바뀌어도 적중) · `LLM_MLX_TIMEOUT=600`(§3.3) · 워밍업(§3.6) |
| **R-3** | 품질 격차. 워커가 FabriX가 아니라 9B다 | 정확도·지연 수치는 **운영 기준선 인용 금지**(D-174 부기). 산출물 메타에 provider가 기록된다(`scripts/eval_text2sql.py:1225-1227`) → `mlx` 값으로 식별 |
| **R-4** | `max_tokens` 절단. 현 코드는 `finish_reason`을 읽는 곳이 0건이라 잘린 응답이 조용히 통과한다(Phase 0 ①에서 절단 자체는 재현) | 값을 설정으로 노출하고 Phase 4에서 긴 응답으로 확인한다. `finish_reason` 감지는 비범위(모든 provider 공통 갭) |
| **R-5** | 서버 미기동 | 워커: 요청 시 연결 오류가 노출된다. 오케스트레이터: 기동 시 semantic_router로 강등되고 로그가 남는다. CU-5 preflight가 선행 차단한다 |
| **R-6** | 모델 교대 재적재. 두 평면의 모델 ID가 다르면 요청마다 가중치를 다시 적재한다(`server.py:393`) — **실측 3.6~5.3초/회**(J-1 ⑥) | §3.6 구성 권장 + CU-5 WARN |
| **R-7** | 총상한 부재. D-198 부기 ②(`docs/02_decision.md:1893`)와 같은 갭이다 | `max_tokens`로 생성 길이가 유한하다(4,096토큰 ≈ 86초). 총상한 추가는 비범위 |
| **R-8** | 보안. 서버가 무인증이고 CORS 기본 `*`다 | **127.0.0.1 바인딩 필수**(문서) · CU-5 루프백 WARN. 폴스타 데이터는 루프백 안에서만 처리되므로 외부 송신 0(D-120 데이터 통제와 정합) |
| **R-9** | `langchain-openai`가 `deepagents` extra 소속 | lazy import + 명확한 오류(T-4) |
| **R-10** | **프롬프트 캐시 메모리.** 상한 없이 측정 중 9.1~13.33GB까지 커졌다(J-2). 32GB 장비에서 모델 5.6GB + 캐시 + 앱·Redis·Docker가 경합하고, 캐시 적중인데 33.9초가 걸린 이상치가 이 시점에 났다(원인 미확정) | `--prompt-cache-bytes 6GB` 잠정 권장(§3.6). 상한과 콜드 prefill 재발은 맞교환이므로 Phase 4에서 조정 |
| **R-11** | **콜드 스타트 vs API 타임아웃.** 서버 기동 직후 첫 질의는 노드별 시스템 프롬프트가 모두 콜드다. 한 노드에 수십 초씩 걸리면 요청 전체 상한 `API_QUERY_TIMEOUT`(개발 `.env` 240 · 코드 기본 60, `src/config.py:454`)에 걸릴 수 있다 | 워밍업 1회 후 검증(§3.6). 로컬에서 필요하면 `.env`만 상향. 코드 기본값은 바꾸지 않는다 |

---

## 9. 완료 판정

| # | 기준 | 측정 |
|---|---|---|
| 0 | Phase 0 설계 전제 확인(512 절단 · thinking 빈 응답 · tool call 5/5 · instructor 모드) | **완료(2026-09-17)** — §5 Phase 0 |
| 1 | provider 미변경 시 기존 테스트 전량 통과(fabrix·vllm·ollama·gemini 비트 동일) | `pytest` |
| 2 | T-1~T-10 통과, 단위 테스트 네트워크 0 | `pytest` |
| 3 | 품질 게이트 무위반 | `python scripts/arch_check.py --ci` · `python scripts/overfit_check.py --ci` · `ruff check src/ tests/` · `mypy src/` |
| 4 | 로컬 단일 질의 응답 성공(mlx 워커) | Phase 4 |
| 5 | 사다리 tier=`deep_agent` + **다도구 루프 완주**(mlx 오케스트레이터) — G-1 (A)일 때 | 기동 로그 · 트레이스 |
| 6 | SSE 토큰 청크 > 1 | Phase 4 |
| 7 | `llm=mlx` + `orchestrator=gemini` → 승인 요구 | T-9 |
| 8 | 서버 미기동 시 preflight STOP + 기동 명령 안내 | T-10 · 수동 |
| 9 | 성능 수치 **기록**(콜드/웜 분리 · 판정 아님) | Phase 4 표 |

**v3 판정(2026-09-17)**

| # | 결과 |
|---|---|
| 0 | 완료 |
| 1 | **충족** — 전체 `pytest` 7,048 passed · 26 failed · 5 errors. 실패 31건을 HEAD 기준선 worktree 2개(기준선 / 기준선+본 변경 파일만)에서 같은 `.env`로 재실행해 대조: 19건은 양쪽 동일 실패, 12건 중 7건은 기준선에서 **ERROR**(`.env`가 병행 편집으로 `ORCHESTRATOR_PROVIDER=mlx`가 되어 HEAD의 Literal이 설정 로드를 거부)이고 본 변경 후 설정이 로드되어 데이터 단언 **FAILED**로 바뀐 것(`test_e2e_polestar` 샌드박스 데이터 불일치 — 기존). 본 변경 기인 신규 실패 0. 관련 스위트 978 passed(실패 2 = `test_llm_gemini` 기본값 테스트 — **귀속 실측**: HEAD worktree + MLX 전환 전 `.env` 백업 + 현 `.encenv`에서도 같은 2건 실패, 두 파일이 없으면 통과. 원인은 전환 전후 `.env` 모두에 있는 `LLM_GEMINI_MODEL`과 `.encenv`의 `LLM_GEMINI_API_KEY` 누수이며 `.env` MLX 전환·본 변경과 무관) |
| 2 | **충족** — T-1~T-10 전건 통과, 단위 테스트 네트워크 0(`requests.get` mock) |
| 3 | **충족(신규 유입 기준)** — `arch_check --ci` error 0 · `overfit_check --ci` 신규 유입 없음 · `ruff`(변경 파일) 기준선 대비 E501·UP045 신규 0, **N802 +16**(한국어 테스트 함수명 — 해당 파일 기존 관례, 기준선 49건) · `mypy`(변경 5파일) 신규 0. 단 `mypy src/`는 기준선부터 `python_version=3.11`에서 numpy 스텁 구문 오류로 중단되어 `--python-version 3.12`로 대조했다 |
| 4 | **충족** — Phase 4 표 |
| 5 | **부분** — `tier=deep_agent` 확정 · 2도구 완주는 했으나 응답 누락 · 알람 결합 질의 미완(Phase 4 판독) |
| 6 | **충족** — `token` 200건 |
| 7 | **충족** — T-9(`mlx`+`gemini` → 외부) |
| 8 | **충족** — T-10 + Phase 4 수동 |
| 9 | **충족** — Phase 4 표 |

---

## 10. 하지 않는 것

- **임베딩의 MLX 전환.** `SentenceTransformer(..., device="cpu")` 고정 두 곳(`src/schema_cache/synonym_semantic.py:187` · `noise_gate/infrastructure/embedding_provider.py:170`)은 유지한다. 근접중복 임계 0.87이 모델·런타임 출력 분포에 맞춰져 있다(`src/config.py:852`).
- **sre_agent.** HolmesGPT는 litellm `MODEL`/`API_BASE`만으로 OpenAI 호환 엔드포인트에 붙을 수 있다(`sre_agent/sre_agent/settings.py:15` · `:51`). 그러나 미검증이고 R-1 위험이 가장 크다 → 별건.
- 인프로세스 MLX · 플랫폼 자동 감지(G-5) · 앱이 MLX 서버 프로세스를 대신 띄우는 기능.
- ~~`instructor_adapter`의 `TOOLS` 모드 잠복 결함 **수정**~~ → **v4에서 수정(B-3)**. `ChatOpenAI` 이름의 LLM이 `try_structured_call`에 들어오면 실패한다(J-3 재현). 재질의 경로의 `'_Msg' object has no attribute 'role'`도 여기에 속한다. 현재 워커는 `ChatOpenAI`가 아니라 발현하지 않으므로 **사실만 기록**하고, MLX는 서브클래스로 우회한다. 수정은 별건이다.
- `estimate_prompt_tokens`의 과소 추정(10~13%, J-2) 보정 — FabriX 토크나이저 기준 값이라 로컬 모델 실측만으로 바꾸지 않는다.
- `worker_provider_override`에 `mlx` 추가 · `finish_reason` 감지(**v4에서 MLX 경로 한정 WARNING 추가 — B-4**, 다른 provider는 여전히 비범위) · 총상한 가드.
- 폐쇄망·운영 배포. MLX는 macOS 전용이고 서버가 스스로 운영 부적합을 명시한다.

---

## 11. 변경 이력

| 버전 | 날짜 | 내용 |
|---|---|---|
| v1 | 2026-09-17 | 초안 — 코드 표면 전수 조사(src·noise_gate·scripts·tests·결정 이력) + `mlx-lm` 0.31.3 소스 실측 + 장비 실측 |
| v2 | 2026-09-17 | **Phase 0 조사 J-1~J-4 실측 완료.** 실 MLX 서버(`uvx` 임시 환경·플래그 없음)로 512 절단 · thinking 빈 응답 · tool call 5/5 · 스트리밍 · 동시성 · 재적재 · 잘못된 ID 404 확인, 실 폴스타 템플릿으로 prefill 320~370 tok/s · 접두 캐시 재사용 · 추정 10~13% 과소 · 캐시 13GB 확인, mock으로 instructor TOOLS 실패 재현, bench 에코 판독. **설계 변경**: `LLM_MLX_TIMEOUT` 300→600 · `--prompt-cache-bytes`·워밍업 권장 · preflight 루프백 점검 · R-10·R-11 신설. 나머지 설계는 근거만 확정 |
| v3 | 2026-09-17 | **구현 완료 · Phase 4 로컬 종단 검증.** §7 권장안 전건 채택(D-222 · D-211 ⑨·D-216 ① 개정). CU-1~CU-7 랜딩, 신규 테스트 `tests/test_llm_mlx.py` 9건 + 갱신 8파일(설정 필드 335→340). **계획서와 달라진 점**: ①CU-6에 없던 `config/settings_help/llm.yaml`·`orchestrator.yaml` 갱신이 필요했다 — 도움말 커버리지 게이트(`tests/test_api/test_settings_help.py` t2)가 신규 키 5종 누락으로 실패한다 ②`_create_mlx`는 `max_tokens=` 대신 alias `max_completion_tokens=`, `api_key`는 `SecretStr("EMPTY")`로 넘긴다 — `mypy` strict가 pydantic 생성자에서 필드명·str을 거부해 신규 오류 2건이 났다(런타임 동일 · T-2가 `max_tokens==4096` 단언) ③CU-4 판정 정의는 `scripts/scenario/preflight.py` `external_planes`에 두고 소비처 시그니처를 바꿨다 — `llm_provider()`→`llm_providers()`(튜플) · 벤치 `approval_policy(worker, orchestrator)` · `_provider_of`→`_providers_of`. 그래서 T-9 목록에 없던 `tests/test_scripts/test_bench_sweep.py`도 갱신했다 ④preflight는 `ORCHESTRATOR_PROVIDER`를 **별도 점검 행**으로 낸다(한 행에 합치면 어느 평면이 막았는지 사람이 다시 찾는다) ⑤`.env.example`의 `LLM_MLX_*`는 주석이 아닌 **활성 키**로 넣었다 — 도움말 파서(`parse_env_example_descriptions`)가 주석 블록을 다음 활성 키에 붙이므로 주석 키로 두면 뒤따르는 Gemini 키 도움말이 오염된다 ⑥`docs/03_setup_guide.md`는 MLX를 §7.2에 넣고 Gemini·FabriX를 §7.3·§7.4로 밀었다(외부 참조 0 확인). **실측 발견(비범위 · 기록만)**: preflight `_check_active_dbs`가 존재하지 않는 `cfg.active_db_ids`를 읽어 `ACTIVE_DB_IDS`가 늘 "(비어 있음)" 중단으로 나온다(실제 설정은 `multi_db.active_db_ids_csv` — 본 변경 이전부터) · schema_analyzer 12K 프롬프트 접두 캐시 미적중. **병행 변경 고지**: 구현 중 `.env`가 `mlx` 값으로 바뀌었고 미추적 `scripts/mlx_server.sh`가 생겼다 — 본 구현 작업은 둘 다 수정·반입하지 않았다(주체는 v3.1 참조) |
| v3.1 | 2026-09-17 | **사용자 지시 2건 반영(오케스트레이터 세션).** ①테스트 시 직접 띄우는 기동 스크립트 `scripts/mlx_server.sh` 신설 — `.env` 모델·포트 판독 · 127.0.0.1 포그라운드 · 캐시·포트·불일치 점검 · `--dry-run` · `uvx` 폴백. bash 3.2 문법·드라이런·오류 경로 5종·실기동(8081·0.8B)·중복 기동 차단 실측 후 종료. §3.6 · `docs/03_setup_guide.md` §7.2 · `CLAUDE.md` 개발 명령을 스크립트 기준으로 갱신 ②개발 `.env`를 MLX 권고값으로 전환(`LLM_PROVIDER`·`ORCHESTRATOR_PROVIDER`=`mlx`, `*_BASE_URL`=`http://127.0.0.1:8080/v1`, `*_MODEL`=`mlx-community/Qwen3.5-9B-OptiQ-4bit` — 이전 값은 `.env` 주석에 보존). v3 「병행 변경 고지」의 두 변경은 이 두 지시의 결과다. 부수 영향: `.env`를 상속하는 테스트가 gemini 대신 mlx를 보게 됐다(v3 전체 pytest의 `test_e2e_polestar` 7건 ERROR→FAILED가 이 전환에서 비롯) |
| v4 | 2026-09-17 | **발견 버그 4건 패치(사용자 지시 *"발견한 버그를 패치하라"*).** **B-1 preflight `ACTIVE_DB_IDS` 오판독**: 재현 = `.env ACTIVE_DB_IDS=polestar`인데 `--preflight`가 "(비어 있음)" 중단 · 원인 = `AppConfig`에 없는 `cfg.active_db_ids`를 `getattr(…, None)`로 읽어 늘 빈 목록(E-1 DB 조회 분기도 동일) · **테스트 픽스처 4곳이 같은 가짜 속성을 주입해 버그를 굳혔다** · 수정 = `_active_db_ids(cfg)` → `cfg.multi_db.get_active_db_ids()` 직접 접근(이름 오류가 예외로 드러나게), 픽스처를 실 접근 경로(`multi_db.get_active_db_ids()`)로 교정, 실 `load_config()` shape 1건 + `run_preflight` E-1 분기 1건 추가 · `scripts/` 전체 grep 동일 패턴 0건 · `docs/18` 1행. **B-2 `test_llm_gemini` 누수**: 기본값 테스트 2건에 `_env_file=None` + `LLM_GEMINI_API_KEY`·`LLM_GEMINI_MODEL`·`GOOGLE_API_KEY` delenv, `test_missing_api_key_raises_value_error`에도 `GOOGLE_API_KEY` delenv — 셸에 `GOOGLE_API_KEY`·`LLM_GEMINI_MODEL`을 심어도 40/40 통과. **B-3 instructor 어댑터**: ⓑ 재현 = MD_JSON(운영 FabriX 경로)은 무효 JSON 4유형(검증 오류·비JSON 문장·깨진 JSON·enum 위반) 모두 재질의 1회로 복구(`'_Msg' role` 오류 **없음** — 한국어 핸들러가 `content`만 씀) → **운영 영향 없음**, TOOLS 재질의만 `dump_message`가 `message.role`을 읽어 깨졌다 · ⓐ D-169 의도(평문=MD_JSON / vLLM=TOOLS)를 그대로 살리는 수정이라 **D-169 해석 변경 없음** · 수정 = `_Msg.role`·도구 호출 대역(`_ToolCall`), `_lc_create`가 `tools`를 `bind_tools(tools, tool_choice)`로 넘기고 `AIMessage.tool_calls`를 OpenAI 형식으로 되돌림, 도구 호출 없을 때 `tool_calls=[]`(재질의 None 순회 방지) — 어댑터 순증 약 45줄 · MD_JSON 경로는 `tools` 인자가 없어 비트 동일 · 테스트 = 실 `ChatOpenAI` + httpx `MockTransport`(소켓 0)로 S10 tools 전송·파싱 / S11 재질의 복구 / 도구 호출 없음 재시도 후 구조화 예외 / MD_JSON 재질의 불변 — **HEAD 코드에서 신규 3건 실패 · 수정 후 통과** 대조. **B-4 `finish_reason=length` 무음 통과**: `MLXChatOpenAI`가 `_create_chat_result`(일반)·`_convert_chunk_to_generation_chunk`(스트리밍 마지막 청크)에서 `length`면 WARNING(모델·max_tokens·응답 id) · 응답 형태 불변 · 오케스트레이터 `provider=mlx`도 서브클래스로 생성(오케스트레이터 LLM은 instructor 호출부 3곳(input_parser·intent_planner·semantic_router)에 들어가지 않음 — 전부 워커 `llm`, `src/graph.py:394·415·439` grep) · vllm 경로는 `type(llm) is ChatOpenAI` 단언으로 비트 동일 고정 · 테스트 = MockTransport 일반/스트리밍 × length/stop 4건. **검증**: 관련 스위트(`tests/test_llm_*`·`test_scenario/`·`test_clients/`·`test_structured_output/`·`test_semantic_routing/`·`test_middleware/test_identification.py`·`test_orchestration/`·`test_nodes/`·`test_api/test_settings_*`·`test_scripts/`·`noise_gate/tests/test_agentic_enricher.py`) **2,436 passed · 0 failed**(v3의 `test_llm_gemini` 2건 실패 해소) · `arch_check`·`overfit_check` 무위반 · ruff(변경 파일, HEAD 대비) E501·UP045·N806 신규 0 · N802 +2(B-1 한국어 테스트명 — 파일 관례) · mypy(변경 src 3파일, `--python-version 3.12`) 신규 0. MLX 서버 기동·실 LLM 호출 0 |
| v5 | 2026-09-17 | **27B 시험 → 9B 복귀 · 결함 A·B 수정(사용자 지시 *"9B로 바꾸고 결함 A, B 수정하라"*).** ①**모델**: *"qwen3.5나 3.8 중 사양을 판단하여 최신 모델"* 지시로 `Qwen3.8-27B-4bit`(16.1GB)를 로컬 `.env`에 올려 벤치 — 도구 2종 동시 호출 3/3 · 생성 17.5 tok/s · 콜드 prefill 107 tok/s. plans/93·94 로직 검증 실 실행에서 1턴 420~900초 · **Metal 메모리 부족 1회**(생성 스레드 사망) · **시스템 강제 종료 1회** → 9B 복귀(§7 G-6 값 무변경). Qwen3.8에는 27B 미만이 없다(HF 실측 · 중간 크기 공식 모델은 MoE `Qwen3.6-35B-A3B` 20.4GB뿐). ②**결함 A**(`src/clients/mlx_client.py` `stream_chunk_timeout_kwargs` · `src/llm.py` 두 평면): langchain-openai ≥1.2.0 `stream_chunk_timeout` 기본 120초가 비동기 스트리밍 본문에만 걸려 `LLM_MLX_TIMEOUT`과 무관했다 — 27B 12K토큰 prefill 123초에서 `schema_analyzer`·`semantic_compiler` 폴백 → 턴 900초 타임아웃. 각 평면 timeout으로 맞춤 · 1.1.13(필드 없음)에는 미전달 · vllm 무변경. ③**결함 B**(`scripts/scenario/preflight.py` `_probe_mlx_generation`): 좀비 서버(`/health` 200·생성 무응답)를 사전 점검이 통과시켰다 → `default_model` 별칭 1토큰 생성 30초 점검 · `[중단] MLX 생성` · `scripts/mlx_server.sh` 포트 사용 중 안내 보강. ④**검증**: 단위 신규 A 4건·B 3건(기존 MLX 점검 테스트 헬퍼에 POST 모의 추가) · 관련 777건 통과 · arch/overfit exit 0 · 재현(헤더 먼저·첫 청크 125초 지연 `astream`: 기본값 120.1초 실패 / 수정 125.1초 성공) · 좀비 흉내 서버 CLI 사전 점검 `[중단]` 2행 · 9B 실 사전 점검 생성 2행 OK · **9B 실 실행 SYN-A-02 정답(354.5초 · 청크 타임아웃 0 · OOM 0)**. ⑤**문서**: `docs/03` MLX 절 모델 표(32GB 권장 9B · 27B 비권장 근거) · `.env` 예시 · 증상표 2행 · `.env.example` 권장 주석 · D-222 부기. **잔여**: 하네스 측 실행 경로가 사전 점검을 호출하지 않음(`cmd_run`) · `expected_tier` 미전달 · 비스트리밍 턴 360초 read timeout · 9B SSE 최대 무이벤트 105.7초(hang 임계 120초 근접) · schema_analyzer 접두 캐시 미스 원인 후보(테이블 나열 순서 변동) |
| v6 | 2026-09-17 | **하네스 권고 반영(사용자 지시 *"권고에 맞게 수정하라"*).** v5 잔여 3건 + 점검 보고 권고 2건. ①**실 실행 직전 MLX 점검**: `scripts/scenario/__main__.py` `cmd_run`(인자 없는 기본 실행 포함) · `scripts/bench/__main__.py` `cmd_sweep --mode run` → `preflight.mlx_run_blockers`(도달·1토큰 생성) 중단 항목이 있으면 앱 서버 기동 전에 exit 1·2 · mlx 아니면 무호출. ②**가용성 강등 INVALID**: `scripts/scenario/server.py` `UNINTENDED_DEGRADATION`(`orchestrator_unavailable`·`package_missing`) — 러너가 `expected_tier`를 넘기지 않아도 기동 로그 사유로 판정 · `flag_off`는 유효(D-221 O-c 경고 유지). ③**비스트리밍 대기 상한**: 설정 에코의 `API_QUERY_TIMEOUT`·`API_FILE_QUERY_TIMEOUT`을 `ProfileStatus.server_timeouts` → `ClientConfig.server_timeouts`로 전달, `plain`·`file` 요청 read = max(`--timeout`, 서버 상한+30초) · 폼필 답변 턴은 파일 상한. ④기동 안내 `MLX_SERVER_COMMAND` = `scripts/mlx_server.sh`(모델 ID 제거). ⑤문서: `plans/94` ⑪ · `plans/93` 퀵 가이드 7 · `docs/03` §7.2 사전 점검 문단 · D-222 부기 2. **검증**: 신규 테스트 14건(가용성 강등 3 · 에코 상한 2 · 비스트리밍 대기 3 · 실행 전 점검 2 · CLI 4) + 기동 안내 단언 1건 수정 · `tests/test_scenario`+`tests/test_scripts`+`tests/test_llm_mlx.py` 765건 통과. **하지 않은 것**: 인자 없는 실행의 확인 프롬프트(D-216 사용자 확정과 충돌 — 결정 필요) · SSE 무이벤트 105.7초 원인 · schema_analyzer 접두 캐시 미스(테이블 나열 순서) |
