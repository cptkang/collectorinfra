"""작업 트리 스냅샷 + 캡처 전용 설정 (plans/116 §4.4 · §4.7).

왜 스냅샷인가: 관리자 설정 화면은 **프로젝트 루트의 `.env` 를 고정 경로로** 읽는다
(`src/api/routes/admin.py` `_ENV_FILE`). 저장소에서 그대로 띄우면 운영 설정값이 캡처에 찍히고,
설정은 cwd 기준 `.encenv`(외부 LLM 키 상존)까지 읽는다. 그래서 필요한 폴더만 복사한 스냅샷에
캡처 전용 `.env` 를 쓰고 `.encenv` 는 두지 않는다. 스냅샷은 작업 트리(미커밋 포함)를 복사하므로
화면은 지금 코드 그대로다.

프로필:
- ``record``  : 사례 녹화 — 실 그래프 · 로컬 MLX 두 평면 · 샌드박스 데이터 DB
- ``alarm``   : 알람 녹화 — 알람 워커 · 노이즈 게이트 · 로컬 MLX
- ``capture`` : 캡처 — 재생 그래프 · LLM 호출 0
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BUILD = REPO / "build" / "manual_capture"
# 프로필마다 폴더를 따로 둔다 — 녹화 서버가 도는 동안 캡처 스냅샷을 다시 만들어도 서로 지우지 않는다
APP_DIRS = {
    "record": BUILD / "app",
    "record3": BUILD / "app3",
    "alarm": BUILD / "app-alarm",
    "capture": BUILD / "app-capture",
    "capture-zones": BUILD / "app-capture-zones",
}
APP = APP_DIRS["record"]
FIXTURES = REPO / "scripts" / "manual" / "fixtures"

# 스냅샷에 담는 것 — 앱이 import·읽는 것만
_COPY_DIRS = ("src", "noise_gate", "config", "scripts/manual")
_EXCLUDES = ("__pycache__", "*.pyc", ".DS_Store", "fixtures")

PORT = 18981
PG = "postgresql://polestar_user:polestar_pass_2024@localhost:5434"  # testdata/pg 샌드박스 컨테이너
CAPTURE_DB = "manual_capture"
SANDBOX_DB = "infradb"
MLX_URL = "http://127.0.0.1:8080/v1"
MLX_MODEL = "mlx-community/Qwen3.5-9B-OptiQ-4bit"

# 캡처 전용 계정·시크릿 — 스냅샷 밖으로 나가지 않는 가짜 값이다(운영 시크릿 아님).
ADMIN_USER = "admin"
ADMIN_PASSWORD = "Manual-Admin-2026"
DEMO_USER = "demo_user"
DEMO_PASSWORD = "Manual-User-2026"

_COMMON = {
    "API_HOST": "127.0.0.1",
    "API_PORT": str(PORT),
    "LOG_LEVEL": "INFO",
    "AUTH_ENABLED": "true",
    # AuthConfig(env_prefix AUTH_) 필드 auth_db_url → AUTH_AUTH_DB_URL. 비면 DB_CONNECTION_STRING 으로 폴백한다
    "AUTH_AUTH_DB_URL": f"{PG}/{CAPTURE_DB}",
    "AUTH_JWT_SECRET": "0" * 30 + "manual-capture-user-secret-0000000",
    "ADMIN_USERNAME": ADMIN_USER,
    "ADMIN_PASSWORD": ADMIN_PASSWORD,
    "ADMIN_JWT_SECRET": "0" * 30 + "manual-capture-admin-secret-000000",
    "DB_BACKEND": "direct",
    "ACTIVE_DB_IDS": "polestar",
    "REDIS_HOST": "localhost",
    "REDIS_PORT": "6380",
    "REDIS_DB": "13",
    "SCHEMA_CACHE_BACKEND": "file",
    # 실행 경로: 기준 경로 2단(D-251) — 1단(deepagents)은 운영 off
    "ENABLE_DEEPAGENTS_PACKAGE": "false",
    "ENABLE_INTENT_ORCHESTRATION": "true",
    "ENABLE_SEMANTIC_ROUTING": "true",
    # 두 평면 모두 로컬 MLX(D-240 — 비과금 루프백). 재생 모드에서는 호출 자체가 없다.
    "LLM_PROVIDER": "mlx",
    "LLM_MLX_BASE_URL": MLX_URL,
    "LLM_MLX_MODEL": MLX_MODEL,
    "LLM_MLX_MAX_TOKENS": "4096",
    "LLM_MLX_TIMEOUT": "600",
    "LLM_MLX_ENABLE_THINKING": "false",
    "ORCHESTRATOR_PROVIDER": "mlx",
    "ORCHESTRATOR_BASE_URL": MLX_URL,
    "ORCHESTRATOR_MODEL": MLX_MODEL,
    # 알람·노이즈 — 채널·스트림은 캡처 전용 이름(pub/sub 은 Redis DB 번호와 무관하게 전역)
    "NOISE_ENABLE_NOISE_GATE": "true",
    "NOISE_SSE_BRIDGE_ENABLED": "true",
    "NOISE_SSE_BRIDGE_CHANNEL": "manual:alarm:sse",
    "NOISE_INCIDENT_TRACKING_ENABLED": "true",
    "NOISE_INCIDENT_EVENT_CHANNEL": "manual:alarm:incident",
    "ALARM_REDIS_STREAM_KEY": "manual:alarm:raw",
    "ALARM_ENABLED": "false",
    "API_QUERY_TIMEOUT": "600",
    "API_FILE_QUERY_TIMEOUT": "900",
    # 질의 동작 설정 — 루트 `.env`(운영 실측 기준) 값을 고정한다. 코드 기본값으로 두면 폴스타 어댑터(검증기·
    # 결정적 조립)·시맨틱 컴파일러가 꺼진 채 녹화돼 운영과 다른 답이 실린다(2026-09-23 실측 — docs/18)
    "POLESTAR_DB_IDS": "polestar_b0,polestar_cm_gp,polestar_cm_yd,polestar",
    "TEXT2SQL_SEMANTIC_COMPOSE": "true",
    "TEXT2SQL_SEMANTIC_FALLBACK": "candidate_then_human",
    "TEXT2SQL_FALLBACK_CONFIDENCE_MIN": "0.0",
    "TEXT2SQL_MULTI_CANDIDATE": "false",
    "TEXT2SQL_CANDIDATE_COUNT": "3",
    "TEXT2SQL_CANDIDATE_STRATEGIES": "multi_prompt",
    "TEXT2SQL_COMPLEXITY_GATE": "false",
    "TEXT2SQL_SELECTION": "hybrid",
    "QUERY_MAX_RETRY_COUNT": "3",
    "QUERY_DEFAULT_LIMIT": "1000",
    "QUERY_INTENT_LLM_ASSIST": "true",
    "ZONE_GROUP_EXCLUSIVE": "false",
    "ENABLE_SQL_APPROVAL": "false",
    "SYNONYM_FUZZY_MATCH": "true",
    "SYNONYM_VALUE_RETRIEVAL": "false",
    "SYNONYM_SEMANTIC_MATCH": "false",
    "SYNONYM_MATCH_CONFIDENCE_MIN": "0.85",
    "SYNONYM_GOVERNANCE": "false",
    "COMPOSITE_SEQUENTIAL_GATE_ENABLED": "true",
    "COMPOSITE_SCOPE_POSTCHECK_ENABLED": "true",
    "COMPOSITE_PRIOR_SCOPE_BY_DB_ENABLED": "true",
    "COMPOSITE_PLAN_DAG_VALIDATION_ENABLED": "true",
    "COMPOSITE_SEQUENTIAL_REPLAN_ENABLED": "true",
    "COMPOSITE_SEQUENTIAL_FALLBACK_TIERS_ENABLED": "true",
    "COMPOSITE_PRIOR_SCOPE_LATEST_ONLY": "true",
}

PROFILES = {
    # 녹화: 데이터 DB 는 샌드박스. 사건 추적은 DB_CONNECTION_STRING 에 테이블을 만들므로 끈다(샌드박스 오염 방지)
    # 스키마 캐시는 Redis(캡처 전용 DB 13) — 파일 백엔드면 유사어 등록이 「Redis 연결 불가」로 끝난다(2026-09-23 실측)
    "record": {
        **_COMMON,
        "DB_CONNECTION_STRING": f"{PG}/{SANDBOX_DB}",
        "NOISE_INCIDENT_TRACKING_ENABLED": "false",
        "SCHEMA_CACHE_BACKEND": "redis",
    },
    # 녹화(3단): 비교 arm(semantic_router)으로 녹화하는 사례용
    "record3": {
        **_COMMON,
        "DB_CONNECTION_STRING": f"{PG}/{SANDBOX_DB}",
        "NOISE_INCIDENT_TRACKING_ENABLED": "false",
        "ENABLE_INTENT_ORCHESTRATION": "false",
        "SCHEMA_CACHE_BACKEND": "redis",
    },
    # 알람 녹화: 사건 테이블은 캡처 DB 에 만든다. 노이즈 문맥(서버 중요도)은 DB 레지스트리 → MCP(DBHUB_SERVER_URL,
    # 로컬 9099 · 읽기 전용)로 샌드박스를 읽으므로 DB_CONNECTION_STRING 과 무관하다
    "alarm": {
        **_COMMON,
        "DB_CONNECTION_STRING": f"{PG}/{CAPTURE_DB}",
        "ALARM_ENABLED": "true",
        "NOISE_ENABLE_LLM_ACTIONABILITY": "true",
    },
    # 캡처: 침묵 규칙 화면을 활성 상태로 보이기 위해 침묵 기능을 켠다(기본 off)
    "capture": {
        **_COMMON,
        "DB_CONNECTION_STRING": f"{PG}/{CAPTURE_DB}",
        "NOISE_SILENCE_ENABLED": "true",
        # 알람 카드의 피드백 버튼(유효·노이즈)은 이 플래그가 켜져야 보인다 — 캡처 서버엔 워커가 없어 LLM 호출은 없다
        "NOISE_ENABLE_LLM_ACTIONABILITY": "true",
    },
}
# 존 장면 전용 — 존 역질문·조회 대상 선택지는 활성 DB 에 존 DB 가 있어야 나타난다(라우트 제어 화면 — LLM·녹화
# 불필요). 기본 캡처 서버에 넣으면 「전체 서버…」 같은 대량 조회마다 존 역질문이 먼저 발동해 녹화 재생이 가려진다
# (관리자 계정은 전 존 권한이다). direct 모드 헬스는 DB ID 와 무관하게 같은
# 연결을 보므로 상태 배지는 정상으로 나온다
PROFILES["capture-zones"] = {
    **PROFILES["capture"],
    "ACTIVE_DB_IDS": "polestar,polestar_cm_gp,polestar_cm_yd,polestar_b0",
}


def build(profile: str) -> Path:
    """스냅샷을 새로 만들고 프로필 `.env` 를 쓴다. 상태 폴더(logs·checkpoints)는 매번 비운다."""
    app = APP_DIRS[profile]
    if app.exists():
        shutil.rmtree(app)
    app.mkdir(parents=True)
    ignore = shutil.ignore_patterns(*_EXCLUDES)
    for rel in _COPY_DIRS:
        src = REPO / rel
        dst = app / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst, ignore=ignore)
        else:
            shutil.copy2(src, dst)
    env = PROFILES[profile]
    (app / ".env").write_text("".join(f"{k}={v}\n" for k, v in env.items()), encoding="utf-8")
    assert not (app / ".encenv").exists()
    verify(app)
    return app


_VERIFY = """
from src.config import AppConfig
c = AppConfig()
print(c.auth.auth_db_url.rsplit('/', 1)[-1], c.llm.provider, c.orchestrator.provider, c.server.port)
print(bool(c.polestar_db_ids), c.text2sql.semantic_compose)
"""


def verify(app: Path) -> None:
    """쓴 키가 실제 필드로 해석됐는지 스냅샷 안에서 확인한다(docs/18 2026-09-23 — 없는 키는 조용히 무시된다).

    인증 DB 가 캡처 DB 가 아니면 공유 샌드박스에 테이블이 생긴다. 두 평면이 mlx 가 아니면 과금 경로다.
    폴스타 어댑터·시맨틱 컴파일러가 꺼져 있으면 운영과 다른 답이 녹화된다.
    """
    out = subprocess.run(
        [sys.executable, "-c", _VERIFY], cwd=app, capture_output=True, text=True, check=True
    )
    auth_db, llm, orch, port, polestar, compose = out.stdout.split()
    got = (auth_db, llm, orch, port, polestar, compose)
    if got != (CAPTURE_DB, "mlx", "mlx", str(PORT), "True", "True"):
        raise SystemExit(
            f"스냅샷 설정 해석 불일치 — auth_db={auth_db} llm={llm} orch={orch} port={port} "
            f"polestar_adapter={polestar} semantic_compose={compose}"
        )


def reset_capture_db() -> None:
    """캡처 DB 를 지우고 새로 만든다(샌드박스 컨테이너 안의 전용 DB — 다른 DB 는 건드리지 않는다)."""
    for sql in (
        f"DROP DATABASE IF EXISTS {CAPTURE_DB} WITH (FORCE)",
        f"CREATE DATABASE {CAPTURE_DB}",
    ):
        subprocess.run(
            [
                "docker",
                "exec",
                "polestar_pg",
                "psql",
                "-U",
                "polestar_user",
                "-d",
                SANDBOX_DB,
                "-c",
                sql,
            ],
            check=True,
            capture_output=True,
        )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("profile", choices=sorted(PROFILES))
    ap.add_argument("--reset-db", action="store_true", help="캡처 DB 재생성")
    a = ap.parse_args()
    if a.reset_db:
        reset_capture_db()
    print(build(a.profile))


if __name__ == "__main__":
    sys.exit(main())
