"""실험 사전 점검 - 사람이 판정하던 것을 코드가 판정한다 (plans/99 E-0·E-1).

`plans/99` §3.5 는 처음에 *"이 값들을 grep 해서 표에서 찾아라"* 였다. 두 가지가 나빴다.

1. **`.env` grep 은 실효값이 아니다.** OS 환경변수가 `.env` 를 덮는데(함정 ③) grep 은
   파일만 본다 - 파일을 고치고 왜 안 먹는지 헤매는 것이 가장 흔한 함정이다.
   `load_config()` 는 `.env`·`.encenv`·OS env 우선순위를 그대로 거친 **실효값**을 준다.
2. **사람이 표에서 행을 찾는 일 자체가 불필요하다.** 판정 규칙이 결정적이면 코드가 한다.

그래서 이 모듈은 **읽기만 하고 판정과 조치를 출력한다.** 고치지는 않는다 - 설정 변경은
사람 결정이고(H-2), 바꾼 사실이 run 기록에 남아야 회귀 비교가 성립한다.

**읽기 전용이다**(D-003). SELECT 2건 외에 DB 를 건드리지 않고, LLM 을 호출하지 않는다.
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]

#: 판정 결과. `ok` 는 "넘어가도 된다", `warn` 은 "돌긴 하는데 알고 있어야 한다",
#: `stop` 은 "여기서 멈춘다", `unknown` 은 "판정하지 못했다"(추정하지 않는다).
VERDICT_OK = "ok"
VERDICT_WARN = "warn"
VERDICT_STOP = "stop"
VERDICT_UNKNOWN = "unknown"

_MARK = {VERDICT_OK: "[OK]", VERDICT_WARN: "[주의]", VERDICT_STOP: "[중단]", VERDICT_UNKNOWN: "[판정불가]"}

#: 내부망 프로바이더 - 승인·RUN_E2E 없이 실 실행한다(D-216 · D-211 (11)).
INTERNAL_PROVIDERS = frozenset({"fabrix", "ollama"})

#: E-3 이 쓰는 디스크 하한(GB). 지난 run 이 체크포인트 1.49GB + 산출물이었고
#: 반복 대상이 늘면 더 커진다(plans/99 E-0-6).
MIN_FREE_GB = 5.0


@dataclass
class Check:
    """점검 1건. `action` 은 **무엇을 해야 하는가**이지 설명이 아니다."""

    key: str
    observed: str
    verdict: str
    action: str
    detail: str = ""


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, *args: Any, **kwargs: Any) -> None:
        self.checks.append(Check(*args, **kwargs))

    @property
    def stops(self) -> list[Check]:
        return [c for c in self.checks if c.verdict == VERDICT_STOP]

    @property
    def unknowns(self) -> list[Check]:
        return [c for c in self.checks if c.verdict == VERDICT_UNKNOWN]


# --- E-0: 설정 실효값 --------------------------------------------------------

def _check_provider(cfg: Any, report: Report) -> None:
    provider = str(getattr(cfg.llm, "provider", "") or "").strip().lower()
    if provider in INTERNAL_PROVIDERS:
        report.add("LLM_PROVIDER", provider, VERDICT_OK,
                   "그대로 진행한다. 승인·RUN_E2E 불필요(D-216).")
    elif not provider:
        report.add("LLM_PROVIDER", "(미설정)", VERDICT_STOP,
                   ".env 에 명시한다. 러너가 외부로 간주해 --run 이 거부된다.")
    else:
        report.add("LLM_PROVIDER", provider, VERDICT_STOP,
                   "폐쇄망이면 환경을 잘못 잡은 것이다(다른 PC·다른 .env). "
                   "의도한 것이면 RUN_E2E=1 + 건별 사용자 승인을 먼저 받는다(D-127).")


def _check_active_dbs(cfg: Any, report: Report) -> None:
    from .runner import SANDBOX_DB_ID

    ids = list(getattr(cfg, "active_db_ids", None) or [])
    if not ids:
        report.add("ACTIVE_DB_IDS", "(비어 있음)", VERDICT_STOP,
                   "실행 전에 채운다. 환경 판정이 어긋나 시나리오가 통째로 보류된다.")
        return
    non_sandbox = [d for d in ids if d != SANDBOX_DB_ID]
    if non_sandbox:
        report.add("ACTIVE_DB_IDS", ", ".join(ids), VERDICT_OK,
                   "그대로 진행한다(환경=closed).")
    else:
        report.add("ACTIVE_DB_IDS", ", ".join(ids), VERDICT_WARN,
                   "로컬 도커 샌드박스다(환경=sandbox). 폐쇄망 측정이 목적이면 잘못된 환경이다.")


def _check_auth(cfg: Any, report: Report) -> None:
    enabled = bool(getattr(cfg.auth, "enabled", False))
    if not enabled:
        report.add("AUTH_ENABLED", "false", VERDICT_WARN,
                   "로그인하지 않으므로 토큰 만료 문제가 없다(T-a·T-b 무의미). "
                   "운영과 다른 조건이니 그 사실을 run 기록에 남긴다.")
        return
    report.add("AUTH_ENABLED", "true", VERDICT_OK,
               "0단계 --mock 으로 로그인이 실제로 되는지 확인한다. "
               "실패하면 --user/--password 를 넘긴다(안 넘기면 프로파일 INVALID 로 선다 - 의도된 차단).")


def _check_db_backend(cfg: Any, report: Report) -> None:
    backend = str(getattr(cfg, "db_backend", "") or "").strip().lower()
    if backend == "dbhub":
        report.add("DB_BACKEND", "dbhub", VERDICT_WARN,
                   "MCP 서버가 따로 떠 있어야 한다 - `cd mcp_server && python -m mcp_server`. "
                   "안 떠 있으면 전 시나리오가 조회 실패다. (아래 E-1 조회가 실제 도달 여부를 대신 확인한다)")
    elif backend == "direct":
        report.add("DB_BACKEND", "direct", VERDICT_WARN,
                   "asyncpg 직결이라 MCP 는 불필요하다. 운영과 다른 경로이므로 run 기록에 남긴다.")
    else:
        report.add("DB_BACKEND", backend or "(미설정)", VERDICT_UNKNOWN,
                   "설정을 확인하고 dbhub/direct 중 어느 쪽인지 정한다.")


def _check_ladder(cfg: Any, report: Report) -> None:
    """사다리 단 - 서버를 띄우지 않고 같은 판정 함수로 확정한다.

    `build_graph()` 와 같은 `resolve_ladder_tier` 를 쓰므로 기동 로그와 어긋나지 않는다.
    `select_orchestration_backend` 는 vLLM 헬스체크(HTTP GET)를 한다 - LLM 호출이 아니다.
    """
    try:
        from src.observability.ladder import resolve_ladder_tier
        from src.orchestration.deep_agent import select_orchestration_backend
    except Exception as exc:
        report.add("사다리 단", f"판정 실패({type(exc).__name__})", VERDICT_UNKNOWN,
                   "서버를 띄워 기동 로그의 `오케스트레이션 사다리 확정` 줄을 직접 읽는다.")
        return

    try:
        backend = select_orchestration_backend(cfg)
    except Exception as exc:
        backend = "semantic_router"
        report.add("오케스트레이터 가용성", f"확인 실패({type(exc).__name__})", VERDICT_WARN,
                   "vLLM 헬스체크가 실패했다. 1단을 원하면 ORCHESTRATOR_BASE_URL 서빙을 확인한다.")

    buildable = _deep_agent_buildable(cfg)
    tier, reason = resolve_ladder_tier(cfg, backend=backend, buildable=buildable)
    tier_value = getattr(tier, "value", str(tier))

    if tier_value == "deep_agent":
        report.add("사다리 단", "deep_agent (1단)", VERDICT_OK,
                   "그대로 진행한다. plans/99 목표 9 달성.")
        return

    actions = {
        "flag_off": "H-2 를 먼저 정한다. 1단을 재려면 ENABLE_DEEPAGENTS_PACKAGE=true 로 바꾸고 재기동한다. "
                    "안 바꿔도 run 은 돈다 - 대신 plans/99 §0 「답하지 못하는 것」에 1단이 남는다.",
        "orchestrator_unavailable": "ORCHESTRATOR_PROVIDER 를 본다. vllm 이면 서빙 여부와 "
                                    "ORCHESTRATOR_BASE_URL 의 /v1/models 를, gemini 면 api_key 와 D-127 승인을 확인한다.",
        "package_missing": "deepagents 조립이 실패했다. 폐쇄망 wheel 반입 여부를 확인한다.",
    }
    action = actions.get(reason, "지난 run(2단)과 조건이 다르면 회귀 비교가 성립하지 않는다 - 맞추거나 기록한다.")
    report.add("사다리 단", f"{tier_value} (degraded_reason={reason})", VERDICT_WARN, action,
               detail="어느 단이든 run 은 돈다. 단을 맞추는 것보다 **어느 단으로 돌았는지 아는 것**이 중요하다.")


def _deep_agent_buildable(cfg: Any) -> bool:
    """deepagents 조립이 되는가. 임포트만 보고 실제 조립은 하지 않는다."""
    try:
        import deepagents  # noqa: F401
    except Exception:
        return False
    return bool(getattr(cfg, "enable_deepagents_package", False))


def _check_disk(report: Report) -> None:
    try:
        free_gb = shutil.disk_usage(REPO_ROOT).free / (1024 ** 3)
    except OSError as exc:
        report.add("디스크 여유", f"확인 실패({type(exc).__name__})", VERDICT_UNKNOWN,
                   "수동으로 확인한다(E-3 은 체크포인트만 1.49GB 규모다).")
        return
    verdict = VERDICT_OK if free_gb >= MIN_FREE_GB else VERDICT_STOP
    action = ("그대로 진행한다." if verdict == VERDICT_OK
              else f"{MIN_FREE_GB:.0f}GB 이상 확보한다. 지난 run 은 체크포인트만 1.49GB 였다.")
    report.add("디스크 여유", f"{free_gb:.1f}GB", verdict, action)


# --- E-1: DB 조회 2건 (읽기 전용) --------------------------------------------

#: G-5 - LOB 속성의 stringvalue NULL 비율. 0% 면 stringvalue 단독이 안전하고,
#: 0% 가 아니면 COALESCE 가 필요하다(= B-09 단언이 과하다).
SQL_G5 = """
SELECT COUNT(*) AS total,
       SUM(CASE WHEN stringvalue IS NULL THEN 1 ELSE 0 END) AS null_cnt
FROM   {prefix}core_config_prop
WHERE  name = 'OSParameter'
""".strip()

#: G-8 - 은행존 장비명에 LIKE 와일드카드(_ %)가 있는가.
#: 있으면 CU-1 의 OR 3분기에 ESCAPE 절이 필요하다.
SQL_G8 = """
SELECT COUNT(*) AS wildcard_names
FROM   {prefix}cmm_resource
WHERE  resource_type = 'server.Server' AND dtime IS NULL
  AND  (name LIKE '%\\_%' ESCAPE '\\' OR name LIKE '%\\%%' ESCAPE '\\')
""".strip()


async def _run_sql(cfg: Any, db_id: str, sql: str) -> tuple[Optional[list], Optional[str]]:
    """SELECT 1건을 돌린다. (행, 실패 사유)."""
    from src.db import get_db_client

    try:
        async for client in get_db_client(cfg, db_id=db_id):
            result = await client.execute_sql(sql)
            return list(getattr(result, "rows", None) or []), None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"
    return None, "클라이언트를 얻지 못했다"


def _schema_prefix(db_id: str) -> str:
    """테이블 참조 접두사(`"schema."` 또는 `""`). DB2 는 대문자 `POLESTAR.` 다.

    레지스트리를 직접 읽지 않고 `get_schema_prefix` 를 쓴다 - 스키마 한정 규칙이
    한 곳에만 있어야 DB 편입 때 여기가 뒤처지지 않는다(CLAUDE.md 새 DB 체크리스트 (4)).
    """
    try:
        from src.routing.db_schema import get_schema_prefix

        return get_schema_prefix(db_id)
    except Exception:
        return ""


def _first_number(rows: Optional[list], key: str, index: int) -> Optional[int]:
    if not rows:
        return None
    row = rows[0]
    value = row.get(key) if isinstance(row, dict) else (row[index] if len(row) > index else None)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def _check_g5(cfg: Any, db_id: str, report: Report) -> None:
    rows, error = await _run_sql(cfg, db_id, SQL_G5.format(prefix=_schema_prefix(db_id)))
    if error:
        report.add("G-5 LOB stringvalue", f"조회 실패 ({db_id})", VERDICT_UNKNOWN,
                   "DB 도달을 먼저 푼다. G-5 는 미결로 남고 CU-13 도 막힌다.", detail=error)
        return
    total = _first_number(rows, "total", 0)
    nulls = _first_number(rows, "null_cnt", 1)
    if total is None or nulls is None:
        report.add("G-5 LOB stringvalue", "결과 해석 실패", VERDICT_UNKNOWN,
                   "반환 형태를 확인한다.", detail=str(rows)[:200])
        return
    if total == 0:
        report.add("G-5 LOB stringvalue", "OSParameter 행 0건", VERDICT_UNKNOWN,
                   "표본이 없어 판정할 수 없다. 다른 LOB 속성으로 다시 잰다.")
        return
    ratio = nulls / total * 100.0
    if nulls == 0:
        report.add("G-5 LOB stringvalue", f"NULL {nulls}/{total} (0.0%)", VERDICT_OK,
                   "stringvalue 단독이 안전하다 → G-5 를 (b)로 확정하고 "
                   "B-09 단언(stringvalue_short 금지)을 그대로 둔다. CU-13 착수 가능.")
    else:
        report.add("G-5 LOB stringvalue", f"NULL {nulls}/{total} ({ratio:.1f}%)", VERDICT_OK,
                   "COALESCE 가 필요하다 → G-5 를 (a)로 확정하고 "
                   "B-09 단언이 과하다고 보아 plans/94 로 이관한다(코드 수정 0).")


async def _check_g8(cfg: Any, db_id: str, report: Report) -> None:
    rows, error = await _run_sql(cfg, db_id, SQL_G8.format(prefix=_schema_prefix(db_id)))
    if error:
        report.add("G-8 LIKE 와일드카드", f"조회 실패 ({db_id})", VERDICT_UNKNOWN,
                   "DB 도달을 먼저 푼다. G-8 은 미결로 남는다.", detail=error)
        return
    hits = _first_number(rows, "wildcard_names", 0)
    if hits is None:
        report.add("G-8 LIKE 와일드카드", "결과 해석 실패", VERDICT_UNKNOWN,
                   "반환 형태를 확인한다.", detail=str(rows)[:200])
        return
    if hits == 0:
        report.add("G-8 LIKE 와일드카드", "0건", VERDICT_OK,
                   "ESCAPE 절이 불필요하다 → G-8 종결. CU-1 의 OR 3분기를 그대로 둔다.")
    else:
        report.add("G-8 LIKE 와일드카드", f"{hits}건", VERDICT_WARN,
                   "장비명에 _ 또는 % 가 있다 → CU-1 의 OR 3분기에 ESCAPE 절을 추가한다. "
                   "지금은 그 장비명으로 조회하면 매칭이 넓어진다.")


# --- 실행 -------------------------------------------------------------------

def run_preflight(*, with_db: bool = True, b0_db_id: str = "polestar_b0",
                  gp_db_id: str = "polestar_cm_gp") -> Report:
    """E-0 설정 점검 + (선택) E-1 DB 조회 2건. 읽기만 한다."""
    report = Report()
    try:
        from src.config import load_config

        cfg = load_config()
    except Exception as exc:
        report.add("설정 로드", f"실패({type(exc).__name__})", VERDICT_STOP,
                   ".env/.encenv 를 읽지 못했다. 이것부터 푼다.", detail=str(exc)[:200])
        return report

    _check_provider(cfg, report)
    _check_active_dbs(cfg, report)
    _check_auth(cfg, report)
    _check_db_backend(cfg, report)
    _check_ladder(cfg, report)
    _check_disk(report)

    if with_db:
        active = set(getattr(cfg, "active_db_ids", None) or [])
        asyncio.run(_run_db_checks(cfg, report, active, b0_db_id, gp_db_id))
    return report


async def _run_db_checks(cfg: Any, report: Report, active: set[str],
                         b0_db_id: str, gp_db_id: str) -> None:
    if gp_db_id in active:
        await _check_g5(cfg, gp_db_id, report)
    else:
        report.add("G-5 LOB stringvalue", f"{gp_db_id} 비활성", VERDICT_UNKNOWN,
                   f"ACTIVE_DB_IDS 에 {gp_db_id} 가 없어 조회하지 않았다.")
    if b0_db_id in active:
        await _check_g8(cfg, b0_db_id, report)
    else:
        report.add("G-8 LIKE 와일드카드", f"{b0_db_id} 비활성", VERDICT_UNKNOWN,
                   f"ACTIVE_DB_IDS 에 {b0_db_id} 가 없어 조회하지 않았다.")


def format_report(report: Report) -> str:
    """콘솔 출력. ASCII 구두점만 쓴다(cp949 콘솔 보호 - W5)."""
    lines = ["[사전 점검] plans/99 E-0 + E-1 - 읽기만 합니다(설정 변경 0 - LLM 호출 0)", ""]
    width = max((len(c.key) for c in report.checks), default=10)
    for check in report.checks:
        lines.append(f"  {_MARK[check.verdict]:<9} {check.key:<{width}}  {check.observed}")
        lines.append(f"  {'':<9} {'':<{width}}  -> {check.action}")
        if check.detail:
            lines.append(f"  {'':<9} {'':<{width}}     ({check.detail[:160]})")
        lines.append("")

    if report.stops:
        lines.append("판정: 중단. 아래를 먼저 해결하십시오 -")
        lines += [f"  - {c.key}: {c.action}" for c in report.stops]
    elif report.unknowns:
        lines.append("판정: 조건부 진행. 아래는 판정하지 못했습니다(추정하지 않습니다) -")
        lines += [f"  - {c.key}: {c.action}" for c in report.unknowns]
        lines.append("  이 항목들이 막는 게이트가 있으면 그 게이트는 미결로 남습니다.")
    else:
        lines.append("판정: 진행 가능. 0단계(무과금 점검)로 넘어가십시오.")
    return "\n".join(lines)
