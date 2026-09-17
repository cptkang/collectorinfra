#!/usr/bin/env bash
# MLX 로컬 LLM 서버 기동 — Apple Silicon 로컬 테스트 전용 (plans/100)
#
# 앱 설정(셸 환경변수 > .env)이 요청할 모델·포트로 mlx_lm.server를 127.0.0.1에 띄운다.
# 포그라운드로 실행되며 Ctrl+C로 종료한다. 앱(python -m src.main --server)은 다른 터미널에서 띄운다.
#
# 사용법:
#   scripts/mlx_server.sh                          # .env 기준으로 기동
#   scripts/mlx_server.sh --dry-run                # 점검만 하고 실행할 명령을 출력
#   scripts/mlx_server.sh -- --log-level DEBUG     # -- 뒤 인자는 mlx_lm.server에 그대로 전달
#
# 값의 우선순위: MLX_* > 앱 설정 키(LLM_MLX_* · ORCHESTRATOR_* — 셸 환경변수 > .env, 앱과 같은 순서) > 기본값
#   MLX_MODEL               LLM_MLX_MODEL               · 기본 mlx-community/Qwen3.5-9B-OptiQ-4bit
#   MLX_PORT                LLM_MLX_BASE_URL의 포트(필수 명시) · 기본 URL http://127.0.0.1:8080/v1
#   MLX_MAX_TOKENS          LLM_MLX_MAX_TOKENS          · 기본 4096
#   MLX_PROMPT_CACHE_BYTES  기본 6GB (상한이 없으면 캐시가 13GB까지 컸다 — plans/100 J-2)
#   MLX_LM_VERSION          기본 0.31.3 (mlx_lm.server가 PATH에 없을 때 uvx로 받을 버전)
#   MLX_ALLOW_DOWNLOAD=1    로컬 캐시에 없는 모델을 Hugging Face에서 받도록 허용 (기본은 오프라인)
#
# 서버 쪽 --max-tokens · enable_thinking=false는 이중 안전장치다. 정본은 앱이 요청마다 보내는 값이다
# (미전송 시 512토큰 절단 · Qwen3.5 thinking으로 빈 응답 — plans/100 Phase 0 실측).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"
DEFAULT_MODEL="mlx-community/Qwen3.5-9B-OptiQ-4bit"

die() { echo "[mlx_server] 오류: $*" >&2; exit 1; }
warn() { echo "[mlx_server] 주의: $*" >&2; }
info() { echo "[mlx_server] $*"; }

# 앱 설정 키를 앱(pydantic-settings)과 같은 순서로 읽는다: 셸 환경변수(빈 값 포함) > .env.
# .env는 KEY=VALUE 한 줄만 읽고 source 하지 않는다 — 다른 설정(JSON 값·시크릿)이 셸로 해석되지 않게.
env_value() {
    if [ -n "${!1+x}" ]; then
        echo "${!1}"
        return 0
    fi
    [ -f "$ENV_FILE" ] || return 0
    { grep -E "^$1=" "$ENV_FILE" || true; } | tail -n 1 | cut -d= -f2- \
        | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"
}

# URL의 호스트·포트를 뽑는다 (http://127.0.0.1:8080/v1 → 127.0.0.1 / 8080).
url_host() { echo "$1" | sed -E 's#^[A-Za-z]+://([^/:]+).*#\1#'; }
url_port() { echo "$1" | sed -E -n 's#^[A-Za-z]+://[^/:]+:([0-9]+).*#\1#p'; }

DRY_RUN=0
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        --) shift; break ;;
        -h|--help) sed -n '2,21p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "알 수 없는 인자: $1 (mlx_lm.server 인자는 -- 뒤에 넘긴다)" ;;
    esac
done
EXTRA_ARGS=("$@")

# --- 플랫폼 -------------------------------------------------------------------
if [ "$(uname -s)" != "Darwin" ] || [ "$(uname -m)" != "arm64" ]; then
    die "MLX는 Apple Silicon macOS 전용이다 (현재: $(uname -s)/$(uname -m))."
fi

# --- 값 해석 ------------------------------------------------------------------
BASE_URL="$(env_value LLM_MLX_BASE_URL)"
BASE_URL="${BASE_URL:-http://127.0.0.1:8080/v1}"
URL_PORT="$(url_port "$BASE_URL")"
if [ -z "$URL_PORT" ]; then
    die "LLM_MLX_BASE_URL($BASE_URL)에 포트가 없다 — 앱은 기본 포트(80/443)로 요청한다. 포트를 명시한다(예: http://127.0.0.1:8080/v1)."
fi
case "$(url_host "$BASE_URL")" in
    127.0.0.1|localhost) ;;
    *) warn "LLM_MLX_BASE_URL($BASE_URL)이 루프백이 아니다 — 이 스크립트는 127.0.0.1에만 띄우므로 앱이 이 서버를 보지 않는다." ;;
esac

MODEL="${MLX_MODEL:-$(env_value LLM_MLX_MODEL)}"
MODEL="${MODEL:-$DEFAULT_MODEL}"
PORT="${MLX_PORT:-$URL_PORT}"
MAX_TOKENS="${MLX_MAX_TOKENS:-$(env_value LLM_MLX_MAX_TOKENS)}"
MAX_TOKENS="${MAX_TOKENS:-4096}"
CACHE_BYTES="${MLX_PROMPT_CACHE_BYTES:-6GB}"
VERSION="${MLX_LM_VERSION:-0.31.3}"

# --- 앱 설정 정합성 점검 (앱이 이 서버를 실제로 쓰는가) ---------------------------------
if [ "$PORT" != "$URL_PORT" ]; then
    warn "포트 $PORT 로 띄우지만 앱 설정 LLM_MLX_BASE_URL은 포트 $URL_PORT 를 본다."
fi

APP_MODEL="$(env_value LLM_MLX_MODEL)"
if [ -n "$APP_MODEL" ] && [ "$APP_MODEL" != "$MODEL" ]; then
    warn "앱 설정 LLM_MLX_MODEL($APP_MODEL) ≠ 기동 모델($MODEL) — 워커 요청마다 모델 교대 재적재(3.6~5.3초)가 일어난다."
fi

LLM_PROVIDER_VALUE="$(env_value LLM_PROVIDER)"
if [ "$LLM_PROVIDER_VALUE" != "mlx" ]; then
    info "참고: 앱 설정 LLM_PROVIDER=${LLM_PROVIDER_VALUE:-(미설정)} — 워커는 이 서버를 쓰지 않는다."
fi

if [ "$(env_value ORCHESTRATOR_PROVIDER)" = "mlx" ]; then
    ORCH_URL="$(env_value ORCHESTRATOR_BASE_URL)"
    ORCH_MODEL="$(env_value ORCHESTRATOR_MODEL)"
    ORCH_PORT="$(url_port "$ORCH_URL")"
    if [ -z "$ORCH_URL" ]; then
        warn "앱 설정 ORCHESTRATOR_BASE_URL이 비어 있다 — 오케스트레이터가 미가용으로 판정돼 하위 경로로 강등된다."
    elif [ "${ORCH_PORT:-}" != "$PORT" ]; then
        warn "앱 설정 ORCHESTRATOR_BASE_URL($ORCH_URL)이 포트 $PORT 가 아니다."
    fi
    if [ -n "$ORCH_MODEL" ] && [ "$ORCH_MODEL" != "$MODEL" ] && [ "$ORCH_MODEL" != "default_model" ]; then
        warn "앱 설정 ORCHESTRATOR_MODEL($ORCH_MODEL) ≠ 기동 모델($MODEL) — 요청마다 모델 교대 재적재가 일어나거나, 캐시에 없는 ID면 404가 난다."
    fi
fi

# --- 모델 캐시 ----------------------------------------------------------------
if [ "${MLX_ALLOW_DOWNLOAD:-0}" = "1" ]; then
    OFFLINE=0
else
    OFFLINE=1
fi
if [ ! -d "$MODEL" ]; then
    HUB_DIR="${HF_HUB_CACHE:-${HF_HOME:-$HOME/.cache/huggingface}/hub}"
    CACHE_DIR="$HUB_DIR/models--$(echo "$MODEL" | sed 's#/#--#g')"
    if [ ! -d "$CACHE_DIR/snapshots" ] && [ "$OFFLINE" = "1" ]; then
        die "모델이 로컬 캐시에 없다: $MODEL ($CACHE_DIR)
       받으려면: MLX_ALLOW_DOWNLOAD=1 scripts/mlx_server.sh
       캐시에 있는 MLX 모델: $(ls "$HUB_DIR" 2>/dev/null | sed -n 's/^models--mlx-community--/mlx-community\//p' | tr '\n' ' ')"
    fi
fi

# --- 실행기 -------------------------------------------------------------------
if command -v mlx_lm.server >/dev/null 2>&1; then
    LAUNCH=(mlx_lm.server)
elif command -v uvx >/dev/null 2>&1; then
    LAUNCH=(uvx --from "mlx-lm==$VERSION" mlx_lm.server)
else
    die "mlx_lm.server도 uvx도 없다. 설치: uv tool install \"mlx-lm==$VERSION\" (루트 venv에는 설치하지 않는다 — plans/100 G-4)"
fi

CMD=("${LAUNCH[@]}"
    --model "$MODEL"
    --host 127.0.0.1
    --port "$PORT"
    --max-tokens "$MAX_TOKENS"
    --chat-template-args '{"enable_thinking":false}'
    --prompt-cache-bytes "$CACHE_BYTES")

info "모델=$MODEL · 주소=http://127.0.0.1:$PORT/v1 · 오프라인=$OFFLINE · 실행기=${LAUNCH[0]}"

if [ "$DRY_RUN" = "1" ]; then
    printf '[mlx_server] 실행할 명령: '
    printf 'HF_HUB_OFFLINE=%s ' "$OFFLINE"
    printf '%q ' "${CMD[@]}" ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}
    printf '\n'
    exit 0
fi

# --- 포트 ---------------------------------------------------------------------
if curl -s -m 2 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
    die "127.0.0.1:$PORT 에 이미 서버가 떠 있다(/health 응답). 그대로 쓰거나 먼저 종료한다 — 확인: lsof -nP -iTCP:$PORT -sTCP:LISTEN
  /health 는 생성 스레드가 죽어도(서버 로그 Insufficient Memory) 200이다 — 생성되는지는 python -m scripts.scenario --preflight --no-db 로 본다"
fi
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    die "포트 $PORT 를 다른 프로세스가 쓰고 있다: $(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN | tail -n +2 | head -1)"
fi

info "준비 확인: curl -s http://127.0.0.1:$PORT/health  (200이어도 모델은 첫 요청 때 적재된다)"
info "서버 기동 직후 첫 질의는 콜드 prefill로 수십 초 걸린다 — 한 번 돌려 캐시를 채운 뒤 검증한다. 종료: Ctrl+C"

# 오프라인 여부를 항상 명시한다 — 셸 프로필이 HF_HUB_OFFLINE=1을 전역으로 켜 두면
# MLX_ALLOW_DOWNLOAD=1이어도 그 값이 상속돼 다운로드가 막힌다(2026-09-17 실측).
if [ "$OFFLINE" = "1" ]; then
    export HF_HUB_OFFLINE=1
else
    export HF_HUB_OFFLINE=0 TRANSFORMERS_OFFLINE=0
fi
exec "${CMD[@]}" ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}
