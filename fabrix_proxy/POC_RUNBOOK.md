# fabrix_proxy 1단계 PoC 실행 런북 (내부망)

> plans/148 1단계 — OpenAI Chat Completions(+`tools`)를 받아 FabriX KBGenAI로 변환하는 프록시가 **실 FabriX에서 도구 호출을 해내는지** 재는 절차다.
> 위에서 아래로 명령 블록을 그대로 복사해 붙여 넣으면 된다. 고칠 곳은 **`REPO=` 한 줄**과 **설정 파일의 `<...>` 자리**뿐이다.
> 정본: `plans/148` §5 1단계 · 부록 D. 모든 명령과 출력 예는 개발 맥에서 가짜 KBGenAI로 실제로 돌려 확인한 것이다(10절).

- 실 FabriX 호출량: 약 445회(순차 · `--concurrency 1`) · 전체 소요 약 1~2.5시간
- 시나리오의 도구·도구 결과는 전부 합성값이다 — 실 DB·실 알람 데이터를 FabriX에 보내지 않는다.
- 반출물은 `summary.md` · `results.jsonl` · `env_check.md` **세 개뿐**이다. `failures/`·로그는 내부망에 남긴다.

## 0. 한눈에 보기

| 완료 | 단계 | 명령(요지) | 소요 |
|:---:|---|---|---|
| [ ] | 1 변수 | `REPO=… ; PY=… ; RUN_DIR=…` (2절) | 1분 |
| [ ] | 2 설정 파일 | `fabrix_proxy/.env` · `fabrix_proxy/.encenv` 작성 (3절) | 5분 |
| [ ] | 3 프록시 기동 | `nohup "$PY" -m fabrix_proxy > "$RUN_DIR/proxy.log" 2>&1 &` (4절) | 1분 |
| [ ] | ① 환경 점검 | `"$PY" scripts/poc_run.py env-check --out "$RUN_DIR"` | 2분 |
| [ ] | ② 오프라인 테스트 | `"$PY" -m pytest tests -q` · (루트) `"$PY" -m pytest tests/test_fabrix_proxy_poc.py -rs -q` | 3분 |
| [ ] | ③ F1 네이티브 탐침 | `"$PY" scripts/probe_fabrix.py native --out "$RUN_DIR" [--url <후보>]` | 1분 |
| [ ] | ④ F2 contents 탐침 | `"$PY" scripts/probe_fabrix.py contents --out "$RUN_DIR"` → `CONTENTS_MODE` | 5분 |
| [ ] | ⑤a few-shot 비교 | `run --only S1,S4,S5 --repeat S1=10,S4=10,S5=5 --fewshot none` → `static` → `dynamic` → `FEWSHOT`·`FEWSHOT_PLACEMENT` | 10~35분 |
| [ ] | ⑤b 문구 언어 비교 | `run --only S1,S5 --repeat S1=10,S5=5 --fewshot "$FEWSHOT" --fewshot-placement "$FEWSHOT_PLACEMENT" --protocol-lang en` → `ko` → `PROTOCOL_LANG` | 5~20분 |
| [ ] | ⑥ 본 측정 | `run --contents-mode "$CONTENTS_MODE" --fewshot "$FEWSHOT" --fewshot-placement "$FEWSHOT_PLACEMENT" --protocol-lang "$PROTOCOL_LANG"` | 20~55분 |
| [ ] | ⑥+ passthrough (조건부) | ③에서 `ADVISE PASSTHROUGH`가 나왔을 때만 — 6.8절 | 20~55분 |
| [ ] | ⑦ 반출 점검 | `export/`에 3종 복사 · grep 0줄 확인 | 5분 |
| [ ] | 정리 | `kill "$(cat "$RUN_DIR/proxy.pid")"` (9절) | 1분 |

모든 러너 명령은 `cd "$REPO/fabrix_proxy"`에서 돈다. 결과는 전부 같은 `--out "$RUN_DIR"`에 쌓인다(같은 디렉터리를 주면 덧붙이고 `summary.md`·`recommend.env`를 디렉터리 전체로 다시 만든다).

종료 코드(러너·탐침 공통): `0` 정상 · `1` 실행 실패(프록시 미기동·POC_MODE 아님 등) · `2` 사용법·설정 오류 · `3` 반출물 자기 검사 실패(그 파일을 쓰지 않았다).

## 1. 반입 묶음

| 넣는 것 | 넣지 않는 것 |
|---|---|
| `fabrix_proxy/` 전체(`fabrix_proxy/` 코드 · `scripts/` · `tests/` · `testdata/` · `.env.example` · `POC_RUNBOOK.md` · `POC_VERSION` · `pyproject.toml`) | `fabrix_proxy/.env` · `fabrix_proxy/.encenv` |
| 루트 `tests/test_fabrix_proxy_poc.py` | `logs/` (개발 맥 리허설 산출물 포함) |
| | 자격증명 일체 · 실데이터 · `__pycache__/`·`.pytest_cache/` |

반입한 파일은 내부망 저장소 작업 트리의 **같은 경로**에 둔다. 루트 venv(`$REPO/.venv`)를 그대로 쓴다 — 프록시 전용 venv는 만들지 않는다.

**`POC_VERSION`.** 반입 시점 개발 쪽 커밋(`git rev-parse HEAD`) 1줄이다. 러너가 `env_check.md`·`summary.md` 머리에 옮겨 적으므로, 반출물만 보고 어느 코드로 잰 결과인지 개발 쪽이 맞춰 볼 수 있다. 내부망에서 고치지 않는다(재반입하면 새 값으로 바뀐다).

## 2. 변수 블록

**맨 처음 한 번** — 어느 터미널에서든. `REPO=` 줄만 내부망 저장소 경로로 고친다.

```bash
REPO=/path/to/collectorinfra
PY="$REPO/.venv/bin/python"
RUN_DIR="$REPO/logs/fabrix_proxy_poc/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$RUN_DIR"
echo "$RUN_DIR" > "$REPO/logs/fabrix_proxy_poc/CURRENT"
echo "RUN_DIR=$RUN_DIR"
```

**터미널을 새로 열 때마다**(터미널 B · 끊긴 뒤 재접속 · 다음 날 재개) — 새 디렉터리를 만들지 않고 위에서 정한 `RUN_DIR`을 이어 쓴다. `REPO=` 줄은 위와 같게 고친다.

```bash
REPO=/path/to/collectorinfra
PY="$REPO/.venv/bin/python"
RUN_DIR="$(cat "$REPO/logs/fabrix_proxy_poc/CURRENT")"
[ -f "$RUN_DIR/recommend.env" ] && . "$RUN_DIR/recommend.env"
echo "RUN_DIR=$RUN_DIR"
```

- 마지막 `.` 줄은 이미 나온 권장값(`CONTENTS_MODE` 등)을 셸 변수로 다시 싣는다 — 중간에 터미널을 바꿔도 ⑤b·⑥ 명령을 그대로 쓸 수 있다.
- 터미널 A = 프록시 기동·종료(4절 · 9절), 터미널 B = ①~⑦. 프록시를 `nohup`으로 띄우면 터미널 하나로도 된다.

## 3. 설정 파일

프록시는 패키지 루트의 `fabrix_proxy/.env`와 `fabrix_proxy/.encenv`를 읽는다(키 이름 정본: `fabrix_proxy/.env.example`). **비밀 3종(`FABRIX_PROXY_TOKEN`·`FABRIX_API_KEY`·`FABRIX_CLIENT_KEY`)은 `.encenv`에, 나머지는 `.env`에** 둔다.

- 인라인 주석 금지(`KEY=value # 설명` 금지 — 값에 섞인다). list/dict 값은 JSON(`["fabrix-tools"]` · `{}`).
- 셸에 `FABRIX_`로 시작하는 환경변수가 export돼 있으면 파일보다 **우선한다**. 먼저 `env | grep '^FABRIX_'`가 0줄인지 본다(있으면 `unset`).

### 3.1 값을 어디서 가져오는가

본체는 같은 값을 `LLM_` 접두사로 쓴다(`src/config.py` `LLMConfig` · `env_prefix="LLM_"`). 프록시는 접두사 없이 쓴다. 「본체 키」 칸의 값을 내부망 본체 `$REPO/.env`·`$REPO/.encenv`에서 그대로 옮긴다.

| 프록시 키 | 파일 | 값 | 출처 |
|---|---|---|---|
| `FABRIX_PROXY_TOKEN` | `.encenv` | 64자 임의 값 | **새로 만든다** — `"$PY" -c "import secrets;print(secrets.token_hex(32))"` (3.3 블록이 자동 생성) |
| `FABRIX_API_KEY` | `.encenv` | 본체와 같은 값 | 본체 `.encenv`의 `LLM_FABRIX_API_KEY` (본체가 셸 환경변수 `FABRIX_API_KEY`로 주고 있으면 그 값) |
| `FABRIX_CLIENT_KEY` | `.encenv` | 본체와 같은 값 | 본체 `.encenv`의 `LLM_FABRIX_CLIENT_KEY` (셸 환경변수 `FABRIX_CLIENT_KEY`면 그 값) |
| `FABRIX_BASE_URL` | `.env` | 본체와 같은 값 | 본체 `.env`의 `LLM_FABRIX_BASE_URL` |
| `FABRIX_MODEL` | `.env` | 본체와 같은 값 | 본체 `.env`의 `LLM_FABRIX_CHAT_MODEL` — 비어 있으면 본체는 `LLM_MODEL`을 쓰므로 그 값 |
| `FABRIX_TOTAL_TIMEOUT` | `.env` | `300` | 본체 `.env`의 `LLM_FABRIX_TOTAL_TIMEOUT`(기본 300) |
| `FABRIX_VERIFY_SSL` | `.env` | `false` | 기본값 유지 — 본체 KBGenAI 클라이언트가 `verify=False`로 부른다 |
| `FABRIX_TIMEOUT` | `.env` | `300` | 기본값 유지(read 간격 · 벽시계 상한은 `FABRIX_TOTAL_TIMEOUT`) |
| `FABRIX_LLM_CONFIG` | `.env` | `{}` | 기본값 유지(`llmConfig` 미전송 = FabriX 서버 기본값). 빈 값(`FABRIX_LLM_CONFIG=`)으로 두지 않는다 |
| `FABRIX_NATIVE_URL` · `FABRIX_NATIVE_MODEL` | `.env` | 빈 값 | 기본값 유지. ⑥+ passthrough 때만 셸에서 준다(6.8절) |
| `FABRIX_PROXY_HOST` · `FABRIX_PROXY_PORT` | `.env` | `127.0.0.1` · `9095` | 기본값 유지(포트가 쓰이고 있으면 8절) |
| `FABRIX_PROXY_LOG_LEVEL` | `.env` | `INFO` | 기본값 유지 |
| `FABRIX_PROXY_MODEL_ALIASES` | `.env` | `["fabrix-tools"]` | 기본값 유지 |
| `FABRIX_PROXY_CONTENTS_MODE` · `_PROTOCOL_LANG` · `_PROTOCOL_FILE` · `_FEWSHOT` · `_FEWSHOT_PLACEMENT` · `_FEWSHOT_FILE` · `_REPAIR_MAX` · `_PASSTHROUGH` | `.env` | `turns` · `en` · 빈 값 · `static` · `system` · 빈 값 · `1` · `false` | 기본값 유지 — 측정 중에는 러너가 요청마다 덮어쓴다 |
| `FABRIX_PROXY_POC_MODE` | `.env` | **`true`** | **PoC 필수** — `false`면 러너가 「POC_MODE가 아니다」로 멈춘다 |

러너가 보낸 요청 선택지는 프록시 설정보다 **항상 우선한다** — `_PROTOCOL_FILE`·`_FEWSHOT_FILE`·`_PASSTHROUGH=true`를 `.env`에 적어도 측정에는 쓰이지 않으므로(라벨 = 실제 동작), 파일 옵션은 러너 `--protocol-file`·`--fewshot-file`로, passthrough는 `--passthrough`로 준다.

### 3.2 `fabrix_proxy/.env` 만들기

```bash
umask 077
cd "$REPO/fabrix_proxy"
cat > .env <<'EOF'
FABRIX_PROXY_HOST=127.0.0.1
FABRIX_PROXY_PORT=9095
FABRIX_PROXY_LOG_LEVEL=INFO
FABRIX_PROXY_MODEL_ALIASES=["fabrix-tools"]
FABRIX_BASE_URL=<본체 .env의 LLM_FABRIX_BASE_URL 값>
FABRIX_MODEL=<본체 .env의 LLM_FABRIX_CHAT_MODEL 값>
FABRIX_VERIFY_SSL=false
FABRIX_TIMEOUT=300
FABRIX_TOTAL_TIMEOUT=300
FABRIX_LLM_CONFIG={}
FABRIX_NATIVE_URL=
FABRIX_NATIVE_MODEL=
FABRIX_PROXY_CONTENTS_MODE=turns
FABRIX_PROXY_PROTOCOL_LANG=en
FABRIX_PROXY_PROTOCOL_FILE=
FABRIX_PROXY_FEWSHOT=static
FABRIX_PROXY_FEWSHOT_PLACEMENT=system
FABRIX_PROXY_FEWSHOT_FILE=
FABRIX_PROXY_REPAIR_MAX=1
FABRIX_PROXY_PASSTHROUGH=false
FABRIX_PROXY_POC_MODE=true
EOF
chmod 600 .env
vi .env
```

`vi`에서 `<...>` 두 곳을 3.1 표의 값으로 바꾼다(꺾쇠까지 지운다). 첫 줄 `umask 077`은 이 셸에서 새로 만드는 파일을 본인만 읽게 한다(파일이 생기는 순간부터 600 · `chmod 600`은 이미 있던 파일을 덮어쓴 경우까지 맞춘다). 3.3도 같은 명령으로 시작한다.

### 3.3 `fabrix_proxy/.encenv` 만들기

```bash
umask 077
cd "$REPO/fabrix_proxy"
PROXY_TOKEN="$("$PY" -c "import secrets;print(secrets.token_hex(32))")"
cat > .encenv <<EOF
FABRIX_PROXY_TOKEN=$PROXY_TOKEN
FABRIX_API_KEY=<본체 .encenv의 LLM_FABRIX_API_KEY 값>
FABRIX_CLIENT_KEY=<본체 .encenv의 LLM_FABRIX_CLIENT_KEY 값>
EOF
chmod 600 .encenv
unset PROXY_TOKEN
vi .encenv
```

`vi`에서 `<...>` 두 곳을 바꾼다. 토큰 줄은 이미 채워져 있다. 프록시 토큰은 러너가 `.encenv`에서 직접 읽는다 — 따로 적어 둘 필요가 없다.

### 3.4 확인 (값은 출력하지 않는다)

```bash
cd "$REPO/fabrix_proxy"
grep -n '<' .env .encenv
env | grep '^FABRIX_'
ls -l .env .encenv | grep -v '^-rw-------'
"$PY" -c "from fabrix_proxy.config import ProxySettings as S; s=S(); [print(k.upper(), '설정됨' if getattr(s, k) else '비어 있음 <-- 채울 것') for k in ('fabrix_proxy_token','fabrix_base_url','fabrix_api_key','fabrix_client_key','fabrix_model')]; print('FABRIX_PROXY_POC_MODE', s.fabrix_proxy_poc_mode)"
```

성공 시: 앞 세 줄(`grep` · `env` · `ls`)은 **아무것도 출력하지 않고**(`ls` 줄이 나오면 그 파일에 `chmod 600`), 마지막 명령은 다음과 같다.

```
FABRIX_PROXY_TOKEN 설정됨
FABRIX_BASE_URL 설정됨
FABRIX_API_KEY 설정됨
FABRIX_CLIENT_KEY 설정됨
FABRIX_MODEL 설정됨
FABRIX_PROXY_POC_MODE True
```

## 4. 프록시 기동 (터미널 A)

```bash
cd "$REPO/fabrix_proxy"
nohup "$PY" -m fabrix_proxy > "$RUN_DIR/proxy.log" 2>&1 &
echo $! > "$RUN_DIR/proxy.pid"
sleep 5
cat "$RUN_DIR/proxy.log"
curl -s --noproxy '*' http://127.0.0.1:9095/health; echo
curl -s --noproxy '*' http://127.0.0.1:9095/v1/models; echo
```

- 로그: `$RUN_DIR/proxy.log` · PID: `$RUN_DIR/proxy.pid` (둘 다 반출하지 않는다)
- 대안 — 터미널 A를 프록시 전용으로 쓰려면 포그라운드로 띄운다(Ctrl+C로 종료): `cd "$REPO/fabrix_proxy" && "$PY" -m fabrix_proxy`

성공 시 출력 예:

```
2026-10-08 10:46:02,814 INFO fabrix_proxy fabrix_proxy start backend=fabrix host=127.0.0.1 port=9095 aliases=fabrix-tools contents_mode=turns fewshot=static poc_mode=True
INFO:     Started server process [76726]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:9095 (Press CTRL+C to quit)
{"status":"ok"}
{"object":"list","data":[{"id":"fabrix-tools","object":"model","created":0,"owned_by":"fabrix_proxy","backend":"fabrix","capabilities":{"tools":"emulated","stream":true}}]}
```

기동 로그 1줄에 `backend=fabrix`와 `poc_mode=True`가 보여야 한다. `/v1/models`는 인증 없이 200이다. `fabrix_proxy 기동 거부: FABRIX_PROXY_TOKEN이 비어 있다`가 나오면 3.3을 다시 한다.

## 5. 결과 디렉터리

```
$RUN_DIR/
  env_check.md        ① — 반출
  results.jsonl       ⑤a·⑤b·⑥ 판정값(본문 없음) — 반출
  summary.md          지표·판정 초안·RECOMMEND/ADVISE 줄 — 반출
  recommend.env       권장값(source 가능) — 내부망 보관
  probe_native.json   ③ — 내부망 보관(요지는 summary.md에 들어간다)
  probe_contents.json ④ — 내부망 보관
  failures/           실패 건 원출력 앞 500자 — 반출 금지
  proxy.log · proxy.pid
  export/             ⑦에서 만든다 — 이 안의 3개만 반출
```

## 6. 실행 ①~⑦ (터미널 B)

터미널 B를 새로 열었으면 2절 두 번째 블록부터 실행한다.

### 6.1 ① 환경 점검 — 2분

```bash
cd "$REPO/fabrix_proxy"
"$PY" scripts/poc_run.py env-check --out "$RUN_DIR"
```

FabriX 짧은 호출 1회 · 프록시 `/health`·`/v1/models` · POC_MODE 확인 · 녹화 하네스 재실행(외부 패키지 소비자 요청 필드 대조 · LLM 호출 없음)을 한다. 성공 시 출력 예(`$REPO`는 실제 경로로 찍힌다):

```
[env-check] FabriX=ok health=200 models=200 poc_mode=on 녹화 하네스 rc=0
[env-check] $REPO/logs/fabrix_proxy_poc/20261008-104547/env_check.md
```

확인:

```bash
grep -n -E '누락|결과:|/health|/v1/models|POC_MODE|하네스 종료 코드|차이' "$RUN_DIR/env_check.md"
```

```
25:- 결과: ok
29:- /health: 200
30:- /v1/models: 200
31:- POC_MODE: on
35:- 하네스 종료 코드: 0
36:- 차이: 0건 (없음)
```

- `누락`이 한 줄이라도 있으면 **여기서 멈추고** `env_check.md`만 반출한다(개발 쪽이 wheel을 보완해 재반입).
- `결과: ok`가 아니면 8절 「① FabriX 도달 실패」. `POC_MODE: on`이 아니면 `.env`의 `FABRIX_PROXY_POC_MODE=true`를 확인하고 프록시를 재기동한다(9절 → 4절).
- `차이: N건`(녹화 필드 대조)은 기록만 하고 계속한다 — 개발 쪽이 반출물로 판단한다.

### 6.2 ② 오프라인 테스트 — 3분

```bash
cd "$REPO/fabrix_proxy"
"$PY" -m pytest tests -q
cd "$REPO"
"$PY" -m pytest tests/test_fabrix_proxy_poc.py -rs -q
```

성공 시 출력 예(각 명령의 마지막 줄 · 경고 줄은 무시한다):

```
199 passed in 15.65s
```

```
SKIPPED [1] tests/test_fabrix_proxy_poc.py:292: litellm 미설치 — sre_agent/.venv 전용 의존이라 루트 venv에서는 생략한다
3 passed, 1 skipped, 2 warnings in 2.24s
```

- 실 FabriX를 부르지 않는다(가짜 KBGenAI 대본). 위 litellm skip 1건은 정상이다. 그 밖의 `skipped`가 있으면 `-rs`가 찍은 사유를 메모해 반출 때 함께 알린다.
- 통과 개수는 반입 판에 따라 다를 수 있다 — `failed`·`error`가 0이면 된다.
- `failed`가 있으면 멈추고, 실패 목록(`"$PY" -m pytest tests -q 2>&1 | tail -30`)과 `env_check.md`를 반출한다.

### 6.3 ③ F1 네이티브 엔드포인트 탐침 — 1분

FabriX에 OpenAI 호환(네이티브 도구 호출) 엔드포인트가 있는지 1회씩 찔러 본다. 후보 URL을 알면 `--url`로 준다(여러 번 줄 수 있다). 후보를 모르면 `--url` 없이 돌린다(후보 0개로 끝나며 이상이 아니다).

```bash
cd "$REPO/fabrix_proxy"
"$PY" scripts/probe_fabrix.py native --out "$RUN_DIR" --url '<OpenAI 호환 엔드포인트 후보 URL>'
```

후보가 없을 때:

```bash
cd "$REPO/fabrix_proxy"
"$PY" scripts/probe_fabrix.py native --out "$RUN_DIR"
```

성공 시 출력 예(후보 1개 · tool_calls 있음):

```
[probe native] 후보 1개 — tool_calls 있음 1개
[probe] $REPO/logs/fabrix_proxy_poc/20261008-104547
```

후보 없이 돌리면 `[probe native] 후보 0개 — tool_calls 있음 0개`가 정상 출력이다.

확인: `grep -n '^ADVISE PASSTHROUGH' "$RUN_DIR/summary.md"` — 한 줄 나오면 ⑥ 뒤에 6.8절(⑥+)을 한다. tool_calls가 나온 후보 URL을 메모해 둔다(`probe_native.json`에는 URL을 적지 않고 `--url#1`처럼 순번만 적는다).

### 6.4 ④ F2 contents 규칙 탐침 — 5분

```bash
cd "$REPO/fabrix_proxy"
"$PY" scripts/probe_fabrix.py contents --out "$RUN_DIR"
grep '^RECOMMEND' "$RUN_DIR/summary.md"
```

성공 시 출력 예(점수는 측정마다 다르다):

```
[probe contents] turns 3/3 · transcript 0/3 → CONTENTS_MODE=turns
[probe] $REPO/logs/fabrix_proxy_poc/20261008-104547
RECOMMEND CONTENTS_MODE=turns
```

넘기는 값: `RECOMMEND CONTENTS_MODE=…` → `recommend.env`의 `CONTENTS_MODE`(⑥에서 쓴다).

### 6.5 ⑤a few-shot 모드 비교 — 10~35분

```bash
cd "$REPO/fabrix_proxy"
"$PY" scripts/poc_run.py run --only S1,S4,S5 --repeat S1=10,S4=10,S5=5 --fewshot none --out "$RUN_DIR"
"$PY" scripts/poc_run.py run --only S1,S4,S5 --repeat S1=10,S4=10,S5=5 --fewshot static --out "$RUN_DIR"
"$PY" scripts/poc_run.py run --only S1,S4,S5 --repeat S1=10,S4=10,S5=5 --fewshot dynamic --out "$RUN_DIR"
grep -E '^(RECOMMEND|ADVISE)' "$RUN_DIR/summary.md"
```

성공 시 출력 예(명령마다 시작·완료 두 줄 · 마지막 `grep`):

```
[run] contents=turns,fewshot=none,placement=system,lang=en,repair=1,passthrough=false — S1,S4,S5
[run] 완료 0.2s — $REPO/logs/fabrix_proxy_poc/20261008-104547
[run] contents=turns,fewshot=static,placement=system,lang=en,repair=1,passthrough=false — S1,S4,S5
[run] 완료 0.2s — $REPO/logs/fabrix_proxy_poc/20261008-104547
[run] contents=turns,fewshot=dynamic,placement=system,lang=en,repair=1,passthrough=false — S1,S4,S5
[run] 완료 0.2s — $REPO/logs/fabrix_proxy_poc/20261008-104547
RECOMMEND CONTENTS_MODE=turns
RECOMMEND FEWSHOT=static
RECOMMEND FEWSHOT_PLACEMENT=system
ADVISE PASSTHROUGH=⑥ 뒤 --passthrough 측정 추가 (F1 네이티브 응답에 tool_calls 있음)
```

(소요 `0.2s`는 가짜 업스트림 기준이다. 실 FabriX는 호출당 5~15초.) 근거는 `summary.md` 끝 「근거:」의 `⑤a fewshot=… 형식 유효율(S1·S4·S5) … · 예시 도구 오호출 N건` 줄이다.

**`ADVISE FEWSHOT_RERUN=…` 줄이 보일 때만**(세 모드 모두 형식 유효율 <90%) 1회 더 돌린다:

```bash
cd "$REPO/fabrix_proxy"
"$PY" scripts/poc_run.py run --only S1,S4,S5 --repeat S1=10,S4=10,S5=5 --fewshot static --fewshot-placement contents --out "$RUN_DIR"
grep -E '^(RECOMMEND|ADVISE)' "$RUN_DIR/summary.md"
```

권장값을 셸 변수로 싣는다:

```bash
. "$RUN_DIR/recommend.env"
echo "CONTENTS_MODE=$CONTENTS_MODE FEWSHOT=$FEWSHOT FEWSHOT_PLACEMENT=$FEWSHOT_PLACEMENT"
```

```
CONTENTS_MODE=turns FEWSHOT=static FEWSHOT_PLACEMENT=system
```

선택 규칙(러너가 계산한다 — 사용자는 `summary.md`의 근거 줄과 맞는지만 본다):
1. 예시 도구 오호출이 1건 이상인 모드는 뺀다.
2. 남은 모드 중 형식 유효율(S1·S4·S5)이 가장 높은 쪽. 차이가 2%p 이내면 `static`.
3. 세 모드 모두 90% 미만이면 위의 `--fewshot-placement contents` 1회 뒤 같은 규칙.

`FEWSHOT`이 비어 있으면(`ADVISE FEWSHOT=undecided` — 모든 모드에 예시 도구 오호출) 기본값으로 계속하고, 반출 때 이 사실을 알린다: `FEWSHOT=static; FEWSHOT_PLACEMENT=system`

### 6.6 ⑤b 규약 문구 언어 비교 — 5~20분

```bash
cd "$REPO/fabrix_proxy"
"$PY" scripts/poc_run.py run --only S1,S5 --repeat S1=10,S5=5 --fewshot "$FEWSHOT" --fewshot-placement "$FEWSHOT_PLACEMENT" --protocol-lang en --out "$RUN_DIR"
"$PY" scripts/poc_run.py run --only S1,S5 --repeat S1=10,S5=5 --fewshot "$FEWSHOT" --fewshot-placement "$FEWSHOT_PLACEMENT" --protocol-lang ko --out "$RUN_DIR"
grep '^RECOMMEND' "$RUN_DIR/summary.md"
. "$RUN_DIR/recommend.env"
echo "CONTENTS_MODE=$CONTENTS_MODE FEWSHOT=$FEWSHOT FEWSHOT_PLACEMENT=$FEWSHOT_PLACEMENT PROTOCOL_LANG=$PROTOCOL_LANG"
```

성공 시 출력 예:

```
[run] contents=turns,fewshot=static,placement=system,lang=en,repair=1,passthrough=false — S1,S5
[run] 완료 0.1s — $REPO/logs/fabrix_proxy_poc/20261008-104547
[run] contents=turns,fewshot=static,placement=system,lang=ko,repair=1,passthrough=false — S1,S5
[run] 완료 0.1s — $REPO/logs/fabrix_proxy_poc/20261008-104547
RECOMMEND CONTENTS_MODE=turns
RECOMMEND FEWSHOT=static
RECOMMEND FEWSHOT_PLACEMENT=system
RECOMMEND PROTOCOL_LANG=en
CONTENTS_MODE=turns FEWSHOT=static FEWSHOT_PLACEMENT=system PROTOCOL_LANG=en
```

선택 규칙: 형식 유효율(S1·S5)이 높은 쪽 · 2%p 이내면 S5 완주율 · 그래도 같으면 `en`.

### 6.7 ⑥ 본 측정 — 20~55분

네 값이 모두 채워져 있어야 한다(위 `echo` 출력). 기본 반복 수(S1 20 · S2 20 · S3 10+10 · S4 20 · S5 10 · S6 10 · F5 3)로 S1~S6·F5를 전부 돈다.

```bash
cd "$REPO/fabrix_proxy"
"$PY" scripts/poc_run.py run --contents-mode "$CONTENTS_MODE" --fewshot "$FEWSHOT" --fewshot-placement "$FEWSHOT_PLACEMENT" --protocol-lang "$PROTOCOL_LANG" --out "$RUN_DIR"
grep -E '^(RECOMMEND|ADVISE)' "$RUN_DIR/summary.md"
```

성공 시 출력 예:

```
[run] contents=turns,fewshot=static,placement=system,lang=en,repair=1,passthrough=false — S1,S2,S3,S4,S5,S6,F5
[run] 완료 4.2s — $REPO/logs/fabrix_proxy_poc/20261008-104547
RECOMMEND CONTENTS_MODE=turns
RECOMMEND FEWSHOT=static
RECOMMEND FEWSHOT_PLACEMENT=system
RECOMMEND PROTOCOL_LANG=en
ADVISE PASSTHROUGH=⑥ 뒤 --passthrough 측정 추가 (F1 네이티브 응답에 tool_calls 있음)
```

- `summary.md`는 선택지 조합(라벨)별로 지표를 묶는다. ⑥이 ⑤a·⑤b의 어떤 조합과 선택지가 같으면 한 라벨로 합산된다 — 정상이다.
- 개별 호출의 400 `content_filter`·502 `tool_call_invalid`는 **측정 결과**다 — 멈추지 않는다(8절).
- 400 `invalid_request`(선택지·설정 오류)는 러너가 즉시 멈춘다(종료 코드 1 · 8절).
- FabriX 쪽이 느리거나 502 `upstream_error`·504가 이어지면 `--sleep 2`처럼 호출 간 대기를 넣는다.

### 6.8 ⑥+ passthrough 측정 (조건부) — 20~55분

③에서 `ADVISE PASSTHROUGH`가 나왔을 때만 한다. 네이티브 엔드포인트로 그대로 보내는(에뮬레이션 끈) 같은 측정이다. 프록시를 네이티브 URL을 주고 재기동한다 — `.env`는 고치지 않고 셸 환경변수로 준다(환경변수가 `.env`보다 우선).

터미널 A:

```bash
kill "$(cat "$RUN_DIR/proxy.pid")"
sleep 2
cd "$REPO/fabrix_proxy"
FABRIX_NATIVE_URL='<③에서 tool_calls가 나온 후보 URL>' nohup "$PY" -m fabrix_proxy > "$RUN_DIR/proxy_passthrough.log" 2>&1 &
echo $! > "$RUN_DIR/proxy.pid"
sleep 5
cat "$RUN_DIR/proxy_passthrough.log"
```

네이티브 엔드포인트의 모델 이름이 KBGenAI `FABRIX_MODEL`과 다르면 같은 줄 앞에 `FABRIX_NATIVE_MODEL='<모델 이름>'`을 붙인다.

터미널 B:

```bash
cd "$REPO/fabrix_proxy"
"$PY" scripts/poc_run.py run --passthrough --contents-mode "$CONTENTS_MODE" --fewshot "$FEWSHOT" --fewshot-placement "$FEWSHOT_PLACEMENT" --protocol-lang "$PROTOCOL_LANG" --out "$RUN_DIR"
```

성공 시 출력 예 — 시작 줄이 `passthrough=true`여야 한다:

```
[run] contents=turns,fewshot=static,placement=system,lang=en,repair=1,passthrough=true — S1,S2,S3,S4,S5,S6,F5
[run] 완료 7.1s — $REPO/logs/fabrix_proxy_poc/20261008-104547
```

`summary.md` 라벨 표에 `passthrough=true` 행이 따로 생긴다.

같은 세션에서 시간이 모자라면 다음 세션에 2절 두 번째 블록 → 이 절만 한다.

### 6.9 ⑦ 반출 점검 — 5분

러너가 반출물을 쓸 때마다 자기 검사(URL · `Bearer` · 키 값 · 업스트림 호스트 · 시나리오 본문 표지 · 응답 원문)를 하지만, 사용자가 한 번 더 본다.

```bash
mkdir -p "$RUN_DIR/export"
cp "$RUN_DIR/summary.md" "$RUN_DIR/results.jsonl" "$RUN_DIR/env_check.md" "$RUN_DIR/export/"
cd "$RUN_DIR/export"
API6="$(grep '^FABRIX_API_KEY=' "$REPO/fabrix_proxy/.encenv" | cut -d= -f2- | tr -d "\"'" | cut -c1-6)"
CLI6="$(grep '^FABRIX_CLIENT_KEY=' "$REPO/fabrix_proxy/.encenv" | cut -d= -f2- | tr -d "\"'" | cut -c1-6)"
TOK6="$(grep '^FABRIX_PROXY_TOKEN=' "$REPO/fabrix_proxy/.encenv" | cut -d= -f2- | tr -d "\"'" | cut -c1-6)"
echo "앞 6자 길이: ${#API6} ${#CLI6} ${#TOK6}"
grep -n -E 'https?://|Bearer' summary.md results.jsonl env_check.md
grep -n -F -e "$API6" -e "$CLI6" -e "$TOK6" summary.md results.jsonl env_check.md
unset API6 CLI6 TOK6
ls -l
```

성공 시 출력 예 — 두 `grep`은 아무것도 찍지 않는다:

```
앞 6자 길이: 6 6 6
total 1312
-rw-r--r--@ 1 user  staff     871 Oct  8 10:48 env_check.md
-rw-r--r--@ 1 user  staff  652087 Oct  8 10:48 results.jsonl
-rw-r--r--@ 1 user  staff    9824 Oct  8 10:48 summary.md
```

통과 조건:
- `앞 6자 길이: 6 6 6` (6이 아니면 `.encenv` 키 이름을 확인 — 빈 패턴은 모든 줄에 걸린다)
- 두 `grep`이 **아무것도 출력하지 않는다**(0줄). 한 줄이라도 나오면 반출하지 않고 줄 번호·파일 이름만 알린다(내용은 옮기지 않는다)
- `ls -l`에 세 파일만 있다

반출: `$RUN_DIR/export/`의 세 파일만 내부망 반출 절차로 내보낸다. `failures/`·`proxy*.log`·`recommend.env`·`probe_*.json`은 내부망에 남긴다. (`summary.md` 머리의 「반출:」 줄도 같은 3종을 적는다 — 탐침 결과는 `summary.md` 「탐침」 절에 이미 들어 있다.)

## 7. 중단 후 재개

같은 `RUN_DIR`에 덧붙인다 — 끝나지 않은 시나리오만 `--only`로 다시 돈다. 앞 단계 결과·권장값은 그대로 남는다.

```bash
REPO=/path/to/collectorinfra
PY="$REPO/.venv/bin/python"
RUN_DIR="$(cat "$REPO/logs/fabrix_proxy_poc/CURRENT")"
[ -f "$RUN_DIR/recommend.env" ] && . "$RUN_DIR/recommend.env"
curl -s --noproxy '*' http://127.0.0.1:9095/health; echo
cd "$REPO/fabrix_proxy"
"$PY" scripts/poc_run.py run --only S5,S6 --contents-mode "$CONTENTS_MODE" --fewshot "$FEWSHOT" --fewshot-placement "$FEWSHOT_PLACEMENT" --protocol-lang "$PROTOCOL_LANG" --out "$RUN_DIR"
```

- `/health`가 응답하지 않으면 프록시부터 다시 띄운다(4절 — 같은 `RUN_DIR`이면 `proxy.log`를 덮어쓴다).
- 위 예는 ⑥ 도중 S5·S6이 남은 경우다. ⑤a·⑤b 도중이면 그 단계 명령에서 남은 줄만 다시 돌린다.
- 어디까지 됐는지 — 라벨·시나리오별 결과 줄 수(없는 시나리오가 남은 것이다). 러너가 `[run] 완료 …` 줄을 찍지 못하고 끊긴 명령이 다시 돌릴 대상이다.

```bash
cd "$REPO/fabrix_proxy"
"$PY" -c "import json,sys,collections; c=collections.Counter((r['label'],r['scenario']) for r in map(json.loads,open(sys.argv[1]))); [print(n,s,l) for (l,s),n in sorted(c.items())]" "$RUN_DIR/results.jsonl"
```

```
10 S1 contents=turns,fewshot=dynamic,placement=system,lang=en,repair=1,passthrough=false
10 S4 contents=turns,fewshot=dynamic,placement=system,lang=en,repair=1,passthrough=false
26 S5 contents=turns,fewshot=dynamic,placement=system,lang=en,repair=1,passthrough=false
…
```

## 8. 실패 시 조치

| 증상 | 원인 후보 | 조치 |
|---|---|---|
| `fabrix_proxy 기동 거부: FABRIX_PROXY_TOKEN이 비어 있다` | `.encenv` 토큰 줄 누락 | 3.3 다시 → 4절 |
| `fabrix_proxy 설정 오류: <키 이름>` | 값 형식 오류(인라인 주석 · list/dict가 JSON이 아님 · `FABRIX_LLM_CONFIG=` 빈 값) | 그 키 줄을 3.2 템플릿대로 고친다 |
| `proxy.log`에 `address already in use` | 9095 사용 중 | 기동에 `--port 9195`를 붙이고(`nohup "$PY" -m fabrix_proxy --port 9195 …`) 러너·탐침 명령마다 `--proxy-url http://127.0.0.1:9195`를 붙인다 |
| `/v1/models`가 401 | 인증을 잘못 붙인 판(`/v1/models`는 무인증이어야 한다) | **코드 결함** — `proxy.log` 마지막 20줄 요지와 함께 알리고 멈춘다 |
| 러너 `실행 실패: 프록시 /v1/models 확인 실패` | 프록시가 떠 있지 않음 · 다른 포트 | 4절 `curl` 확인 · `--proxy-url` 확인 |
| 러너 `실행 실패: 프록시가 POC_MODE가 아니다` | `.env`에 `FABRIX_PROXY_POC_MODE=true` 없음 | 고친 뒤 프록시 재기동(9절 → 4절) |
| ① `결과:`가 `connect_error`·`timeout`·`http_4xx` | 주소 · 망 · TLS · 키 | 본체 `.env`/`.encenv` 값과 다시 대조 · TLS 오류면 `FABRIX_VERIFY_SSL=false` 확인 · 셸 `HTTPS_PROXY`가 FabriX 주소를 가로채는지 확인 |
| ① `결과: not_configured` | `FABRIX_BASE_URL` 빈 값 | 3.2 |
| ① `누락` 패키지 | 내부망 venv에 wheel 없음 | `env_check.md`만 반출 → 개발 쪽 보완 후 재반입 |
| 400 `content_filter` | FabriX PII 필터 | F5와 같은 현상 — 기록되고 계속 진행한다(조치 없음) |
| 러너 `실행 실패: 프록시가 요청을 거부했다(400 invalid_request · 관련 옵션: …)` | 측정 선택지·프록시 설정 오류(예: `FABRIX_NATIVE_URL` 없이 `--passthrough` · `--repair-max` 4 이상) — 반복해도 같아 러너가 즉시 멈춘다(종료 코드 1) | 「관련 옵션」의 러너 인자나 프록시 설정을 고치고 같은 명령을 다시 돈다 |
| 502 `tool_call_invalid` 다발 | 모델이 규약을 못 따름 | **측정 결과 자체다** — 멈추지 않는다 |
| 502 `upstream_error` · 504가 연속 | FabriX 장애 · 과부하 | 5분 쉬고 해당 시나리오만 `--only`로 재실행(7절) · `--sleep 2`~`5` 추가 |
| ② 테스트 실패 | 내부망 패키지 버전 차이 | 실패 목록과 `env_check.md` 반출 → 개발 쪽 보완 후 재반입 |
| 러너 종료 코드 3 `반출물 자기 검사 실패 — <파일>: 규칙 …` | 반출 파일에 URL·키·본문 표지가 들어가려 함 | 그 파일은 쓰이지 않았다. 출력 한 줄(규칙 이름만)을 그대로 알리고 멈춘다 — **코드 결함**으로 다룬다 |
| ⑤a·⑤b 뒤 `echo`에서 값이 빈다 | 권장값 근거 부족(`ADVISE …=undecided`) | `grep -E '^(RECOMMEND|ADVISE)' "$RUN_DIR/summary.md"`로 사유 확인 · 6.5 끝 기본값으로 계속 |

## 9. 프록시 종료·정리

```bash
kill "$(cat "$RUN_DIR/proxy.pid")"
sleep 2
lsof -nP -iTCP:9095 -sTCP:LISTEN
```

- 자기가 띄운 PID만 종료한다(`pkill` 금지). 마지막 `lsof`가 아무것도 출력하지 않으면 끝.
- 포그라운드로 띄웠으면 터미널 A에서 Ctrl+C.
- `fabrix_proxy/.env`·`.encenv`는 재측정에 다시 쓰므로 내부망에 둔다(반출 금지). `$RUN_DIR`도 판정이 끝날 때까지 보관한다.

## 10. 개발 맥 리허설

반입 전에 개발 맥에서 **가짜 KBGenAI를 업스트림으로** 이 런북을 그대로 돌려 본다. 실 LLM 호출 0건이다. 위 절의 명령을 그대로 쓰고, 다른 것은 아래뿐이다.

| 항목 | 내부망 | 리허설 |
|---|---|---|
| `PY` | `"$REPO/.venv/bin/python"` | `python` (루트 venv가 없으면 패키지가 깔린 파이썬) |
| 업스트림 | 실 FabriX | 터미널 C의 가짜 KBGenAI(127.0.0.1:9096 · 대본 `testdata/scenarios/poc_dryrun_script.json`) |
| 3.2·3.3 설정 파일 | 템플릿 + `vi` | 아래 10.2 블록(가짜 값 · `vi` 없음) |
| ③ | `--url <후보>` | `--url http://127.0.0.1:9096/native/v1/chat/completions` |
| ⑥+ | `FABRIX_NATIVE_URL='<후보>'` | `FABRIX_NATIVE_URL='http://127.0.0.1:9096/native/v1/chat/completions'` |
| `--repeat` | 위 값 그대로 | 줄여도 된다 — 명령 끝에 `--repeat 1`을 붙이면 앞의 `--repeat`를 덮어쓴다 |
| 끝나면 | 설정 파일 보관 | 가짜 서버도 종료 · `fabrix_proxy/.env`·`.encenv` 삭제 |

zsh에서 블록을 붙여 넣으면 `#` 주석 줄이 명령으로 읽힌다 — 먼저 `setopt interactivecomments`.

### 10.1 변수와 가짜 KBGenAI (터미널 C)

```bash
REPO=/path/to/collectorinfra
PY=python
lsof -nP -iTCP:9095 -sTCP:LISTEN
lsof -nP -iTCP:9096 -sTCP:LISTEN
RUN_DIR="$REPO/logs/fabrix_proxy_poc/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$RUN_DIR"
echo "$RUN_DIR" > "$REPO/logs/fabrix_proxy_poc/CURRENT"
cd "$REPO/fabrix_proxy"
nohup "$PY" scripts/fake_kbgenai.py --host 127.0.0.1 --port 9096 --script testdata/scenarios/poc_dryrun_script.json > "$RUN_DIR/fake.log" 2>&1 &
echo $! > "$RUN_DIR/fake.pid"
sleep 2
head -1 "$RUN_DIR/fake.log"
```

두 `lsof`는 아무것도 출력하지 않아야 한다(포트가 비어 있음). 마지막 줄은 `FAKE_KBGENAI_READY port=9096`.

다른 터미널에서는 2절 두 번째 블록을 `PY=python`으로 바꿔 쓴다.

### 10.2 리허설 설정 파일

```bash
umask 077
cd "$REPO/fabrix_proxy"
cat > .env <<'EOF'
FABRIX_PROXY_HOST=127.0.0.1
FABRIX_PROXY_PORT=9095
FABRIX_PROXY_LOG_LEVEL=INFO
FABRIX_PROXY_MODEL_ALIASES=["fabrix-tools"]
FABRIX_BASE_URL=http://127.0.0.1:9096/kbgenai/v1/chat
FABRIX_MODEL=fake-asset-001
FABRIX_VERIFY_SSL=false
FABRIX_TIMEOUT=300
FABRIX_TOTAL_TIMEOUT=300
FABRIX_LLM_CONFIG={}
FABRIX_NATIVE_URL=
FABRIX_NATIVE_MODEL=
FABRIX_PROXY_CONTENTS_MODE=turns
FABRIX_PROXY_PROTOCOL_LANG=en
FABRIX_PROXY_PROTOCOL_FILE=
FABRIX_PROXY_FEWSHOT=static
FABRIX_PROXY_FEWSHOT_PLACEMENT=system
FABRIX_PROXY_FEWSHOT_FILE=
FABRIX_PROXY_REPAIR_MAX=1
FABRIX_PROXY_PASSTHROUGH=false
FABRIX_PROXY_POC_MODE=true
EOF
chmod 600 .env
PROXY_TOKEN="$("$PY" -c "import secrets;print(secrets.token_hex(32))")"
cat > .encenv <<EOF
FABRIX_PROXY_TOKEN=$PROXY_TOKEN
FABRIX_API_KEY=fakeapikey-7Q3X9
FABRIX_CLIENT_KEY=fakeclient-5M1Z2
EOF
chmod 600 .encenv
unset PROXY_TOKEN
```

그다음 3.4 → 4절 → 6.1~6.9 → 9절을 위 표의 차이만 바꿔 그대로 돈다.

### 10.3 리허설 정리

```bash
kill "$(cat "$RUN_DIR/proxy.pid")"
kill "$(cat "$RUN_DIR/fake.pid")"
sleep 2
lsof -nP -iTCP:9095 -sTCP:LISTEN
lsof -nP -iTCP:9096 -sTCP:LISTEN
rm -f "$REPO/fabrix_proxy/.env" "$REPO/fabrix_proxy/.encenv"
ls -a "$REPO/fabrix_proxy" | grep -E '^\.(env|encenv)$'
```

마지막 세 명령이 아무것도 출력하지 않으면 끝. 리허설 산출물은 `logs/`(git 제외) 아래에만 남는다 — 반입 묶음에 넣지 않는다.

### 10.4 간이 확인 — `--dry-run`

설정 파일·터미널 없이 러너가 가짜 KBGenAI와 프록시(POC_MODE · 임시 토큰 · 빈 포트)를 하위 프로세스로 직접 띄워 env-check → F1 → F2 → ⑤a → ⑤b → ⑥ 전체 순서를 한 디렉터리에 돈다. 명령이 맞는지가 아니라 코드가 도는지만 빠르게 볼 때 쓴다.

```bash
cd "$REPO/fabrix_proxy"
"$PY" scripts/poc_run.py run --dry-run --out "$REPO/logs/fabrix_proxy_poc/dryrun-$(date +%Y%m%d-%H%M%S)"
```

```
[env-check] FabriX=ok health=200 models=200 poc_mode=on 녹화 하네스 rc=0
[probe native] 후보 2개 — tool_calls 있음 1개
[probe contents] turns 3/3 · transcript 0/3 → CONTENTS_MODE=turns
[run] contents=turns,fewshot=none,placement=system,lang=en,repair=1,passthrough=false — S1,S4,S5
[run] contents=turns,fewshot=static,placement=system,lang=en,repair=1,passthrough=false — S1,S4,S5
[run] contents=turns,fewshot=dynamic,placement=system,lang=en,repair=1,passthrough=false — S1,S4,S5
[run] contents=turns,fewshot=static,placement=system,lang=en,repair=1,passthrough=false — S1,S5
[run] contents=turns,fewshot=static,placement=system,lang=ko,repair=1,passthrough=false — S1,S5
[run] contents=turns,fewshot=static,placement=system,lang=en,repair=1,passthrough=false — S1,S2,S3,S4,S5,S6,F5
[run] 완료 10.1s — $REPO/logs/fabrix_proxy_poc/dryrun-20261008-104846
```

### 10.5 리허설 기록 (2026-10-08 · 개발 맥 · pyenv Python 3.13.1 · 실 LLM 0건)

이 런북 명령을 위에서부터 그대로 붙여 넣어 돌린 결과다. `--repeat`는 줄이지 않았다(내부망과 같은 반복 수).

| 단계 | 결과 | 소요 |
|---|---|---|
| 10.1 가짜 KBGenAI · 10.2 설정 · 3.4 확인 | `FAKE_KBGENAI_READY port=9096` · 다섯 키 「설정됨」 · `POC_MODE True` | 1분 |
| 4 프록시 기동 | `backend=fabrix … poc_mode=True` · `/health`·`/v1/models` 200 | 5초 |
| ① env-check | `FabriX=ok … poc_mode=on 녹화 하네스 rc=0` · 누락 0 · 차이 0건 | 5초 |
| ② 오프라인 테스트 | `199 passed` · 루트 `3 passed, 1 skipped`(litellm) | 23초 |
| ③ native (`--url` 1개 · 후보 0개도 확인) | 후보 1개 tool_calls 있음 → `ADVISE PASSTHROUGH` | 1초 |
| ④ contents | `CONTENTS_MODE=turns` | 1초 |
| ⑤a (3모드 · 재실행 블록은 별도 디렉터리에서 명령만 확인) | `FEWSHOT=static` · `FEWSHOT_PLACEMENT=system` | 1초 |
| ⑤b | `PROTOCOL_LANG=en` | 1초 |
| ⑥ | S1~S6·F5 완료 · rc 0 | 4초 |
| ⑥+ passthrough(`FABRIX_NATIVE_URL` 셸 주입 재기동) | `passthrough=true` 라벨 생성 · rc 0 | 7초 |
| 7절 재개(`--only S5,S6` 같은 `--out`) | 덧붙임 · rc 0 | 4초 |
| ⑦ 반출 점검 | `앞 6자 길이: 6 6 6` · 두 `grep` 0줄 · 세 파일 | 1초 |
| 10.3 정리 | `lsof` 0줄 · `.env`/`.encenv` 삭제 · 남은 프로세스 0 | 2초 |
| 10.4 `--dry-run` | 전체 순서 완주 · rc 0 | 10초 |
