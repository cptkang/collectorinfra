"""호스트 조사·실시간 프로세스 조회의 본체 감사 이벤트 (plans/121 TP-1.13 · N-14 · D-027·D-261).

`host_inspect`(mcp_server 고수준 도구)와 `process_query`(폴스타 프로세스 REST)는 본체 감사 로그에
남지 않았다. 세 단(1단 도구 · 2단 디스패치 · 3단 계획 루프)이 모두 `SUBAGENT_REGISTRY` 핸들러를
부르므로 레지스트리의 핸들러를 감싸 **task당 1건**을 남긴다(호출부 한 곳 · 사본 없음).

- 기록은 `log_investigation`(78 W6 스키마 · `backend` 계약)이다. `log_query_execution`은 쓰지
  않는다 — 하네스의 `row_counts_by_db`·`sql_must_match`와 관리자 「쿼리 실행」 필터·경보
  (D-261 ④)가 오염된다.
- `COMPOSITE_AUDIT_ENABLED`(`composite.audit_enabled` · 기본 on)가 끄는 스위치다 — 종전에는 기동
  로그 보고(`investigation_metrics`)뿐인 죽은 설정이었다. 설정 대역(MagicMock)은 `is True`로만 켠다.
- 감사 실패는 조회를 막지 않는다(경고 로그 · 예외 원문 대신 클래스명).
- `process_query`는 서버명 → hostname 해소 SELECT(고정 SQL · `noise_gate` 리졸버)를 `commands`에
  싣는다 — 조회가 끝까지 간 결과(단일 성공·다대상)만이다. 다른 실패 경로는 SQL 실행 여부를 결과로
  구분할 수 없어 사유(`degraded`)만 남긴다.

계층: orchestration — handler 규약(task, isolated, *, llm, app_config)을 그대로 감싼다.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from functools import wraps
from typing import Any

from src.orchestration.db_access import is_access_denied_result
from src.orchestration.host_inspect import DEGRADED_KEY
from src.orchestration.process_query import REASON_HOST_UNAVAILABLE
from src.security.audit_logger import (
    INVESTIGATION_DENIED,
    INVESTIGATION_FAILED,
    INVESTIGATION_OK,
    INVESTIGATION_PARTIAL,
    log_investigation,
)

logger = logging.getLogger(__name__)

BACKEND_MCP = "mcp_server"
BACKEND_PROCESS_API = "process_api"
#: 관측 소스 게이트웨이(APM · plans/125 A-3) — 두 번째 MCP 엔드포인트.
BACKEND_APM = "apm_gateway"


def _audit_enabled(app_config: Any) -> bool:
    return getattr(getattr(app_config, "composite", None), "audit_enabled", False) is True


def _host_inspect_fields(result: dict[str, Any]) -> dict[str, Any]:
    target = result.get("inspect_target") or result.get("target")
    fields: dict[str, Any] = {
        "targets": [target] if isinstance(target, dict) else None,
        "profile": result.get("profile"),
    }
    if result.get("error"):
        fields["outcome"] = INVESTIGATION_FAILED
        fields["degraded"] = [{"reason": result.get(DEGRADED_KEY) or "error"}]
    elif result.get("truncated_targets"):
        fields["outcome"] = INVESTIGATION_PARTIAL
        fields["truncation"] = {"truncated": True,
                                "truncated_count": result["truncated_targets"]}
    else:
        fields["outcome"] = INVESTIGATION_OK
    return fields


def _apm_command(provenance: dict[str, Any]) -> str:
    """도구 호출 1건 — 대상 + 고정 인자·조건(plans/134 W1 검증 L-6 · 조건이 없으면 종전 모양)."""
    parts = [f"hostname={provenance.get('hostname') or '*'}"]
    args = provenance.get("args")
    if isinstance(args, dict):
        parts += [f"{k}={v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)}"
                  for k, v in args.items()]
    return f"{provenance.get('tool')}({', '.join(parts)})"


def _apm_query_fields(result: dict[str, Any]) -> dict[str, Any]:
    """APM 보기 호출 감사(plans/125 A-3) — 대상 hostname · 도구 호출 요약 · 실패 사유."""
    meta = result.get("apm_query") or {}
    hosts = [h for h in meta.get("hostnames") or [] if h]
    fields: dict[str, Any] = {
        "targets": [{"hostname": h} for h in hosts] or None,
        "profile": ",".join(meta.get("views") or []) or None,
        # 첫 홉 삽입 호출(부하 순위 · 인스턴스 목록 — V6-8 ①) 뒤에 대상 조회 호출
        "commands": [_apm_command({"tool": s["tool"], "args": s.get("args")})
                     for s in meta.get("inserted_steps") or []
                     if isinstance(s, dict) and s.get("tool")]
                    + [_apm_command(p) for p in meta.get("provenance") or []] or None,
    }
    failures = meta.get("failures") or []
    accepted = meta.get("accepted_jobs") or []
    partial = [a for a in meta.get("aggregates") or [] if isinstance(a, dict) and a.get("partial")]
    if result.get("error"):
        fields["outcome"] = INVESTIGATION_FAILED
        fields["degraded"] = [{"reason": result.get(DEGRADED_KEY) or "error"}]
    elif failures or accepted or partial:
        # 작업 접수 · 게이트웨이 부분 결과(plans/134 W0-B)는 완료(ok)로 세지 않는다
        fields["outcome"] = INVESTIGATION_PARTIAL
        fields["degraded"] = [{"reason": str(f.get("reason"))[:120]} for f in failures[:5]] + [
            {"reason": "apm_job_accepted", "detail": str(j)[:8]} for j in accepted[:5]] + [
            {"reason": "apm_partial_sources", "detail": f"{a.get('view')}:{a.get('hostname')}"}
            for a in partial[:5]]
    else:
        fields["outcome"] = INVESTIGATION_OK
    return fields


def _resolver_sql(db_id: str, values: list[str]) -> list[str]:
    """해소 SELECT 원문 — 리졸버와 같은 결정적 조립 함수로 만든다(실행 SQL과 같은 문자열)."""
    from noise_gate.infrastructure.polestar_hostname_resolver import (
        build_host_status_sql,
        build_hostname_sql,
    )
    from src.routing.domain_config import get_domain_by_id

    domain = get_domain_by_id(db_id)
    engine = domain.db_engine if domain else "postgresql"
    if len(values) == 1:
        return [build_hostname_sql(db_id, values[0], engine)]
    return [build_host_status_sql(db_id, values, engine)]


def _process_query_fields(result: dict[str, Any]) -> dict[str, Any]:
    meta = result.get("process_query") or {}
    db_id = meta.get("db_id")
    fields: dict[str, Any] = {}
    if "targets" in meta:  # 다대상(W2 fan-out)
        fields["targets"] = meta.get("targets") or None
        succeeded, failed = meta.get("succeeded_count", 0), meta.get("failed_count", 0)
        fields["outcome"] = (
            INVESTIGATION_FAILED if not succeeded
            else INVESTIGATION_PARTIAL if failed or meta.get("truncated")
            else INVESTIGATION_OK
        )
        values = [
            str(t.get("hostname") or t.get("server_name") or t.get("ip"))
            for t in meta.get("targets") or []
            if t.get("hostname") or t.get("server_name") or t.get("ip")
        ]
        if db_id and values:
            fields["commands"] = _resolver_sql(db_id, values)
        return fields
    fields["targets"] = [{
        "server_name": meta.get("server_name"), "hostname": meta.get("hostname"), "db_id": db_id,
    }] if (meta.get("server_name") or meta.get("hostname")) else None
    rows = (result.get("organized_data") or {}).get("rows")
    if rows or meta.get("captured_at"):
        fields["outcome"] = INVESTIGATION_OK
        identifier = meta.get("server_name")
        if db_id and identifier:
            fields["commands"] = _resolver_sql(db_id, [str(identifier)])
    else:
        reason = meta.get("reason")
        fields["outcome"] = (
            INVESTIGATION_DENIED if reason == REASON_HOST_UNAVAILABLE else INVESTIGATION_FAILED
        )
        fields["degraded"] = [{"reason": reason or "no_result"}]
    return fields


async def _record(
    backend: str,
    task: dict[str, Any],
    isolated: dict[str, Any],
    result: Any,
    duration_ms: float,
    exc: BaseException | None,
) -> None:
    fields: dict[str, Any]
    if exc is not None:
        fields = {"outcome": INVESTIGATION_FAILED,
                  "degraded": [{"reason": "exception", "detail": type(exc).__name__}]}
    elif isinstance(result, dict) and is_access_denied_result(result):
        fields = {"outcome": INVESTIGATION_DENIED, "degraded": [{"reason": "access_denied"}]}
    elif isinstance(result, dict) and backend == BACKEND_APM:
        fields = _apm_query_fields(result)
    elif isinstance(result, dict):
        fields = (
            _host_inspect_fields(result) if backend == BACKEND_MCP
            else _process_query_fields(result)
        )
    else:
        fields = {"outcome": INVESTIGATION_FAILED, "degraded": [{"reason": "no_result"}]}
    await log_investigation(
        request_id=isolated.get("request_id"),
        entry_point="chat",
        user_id=isolated.get("user_id"),
        thread_id=isolated.get("thread_id"),
        backend=backend,
        duration_ms=round(duration_ms, 1),
        task_id=task.get("task_id"),
        **fields,
    )


def audited_investigation(handler: Callable[..., Any], backend: str) -> Callable[..., Any]:
    """조사 계열 handler를 감싸 task당 감사 1건을 남긴다(반환값·예외는 그대로 통과)."""

    @wraps(handler)
    async def _run(
        task: dict[str, Any], isolated: dict[str, Any], *, llm: Any, app_config: Any,
    ) -> Any:
        started = time.monotonic()
        result: Any = None
        error: BaseException | None = None
        try:
            result = await handler(task, isolated, llm=llm, app_config=app_config)
            return result
        except Exception as exc:
            error = exc
            raise
        finally:
            if _audit_enabled(app_config):
                try:
                    await _record(
                        backend, task, isolated, result,
                        (time.monotonic() - started) * 1000, error,
                    )
                except Exception as audit_exc:  # noqa: BLE001 — 감사 실패가 조회를 막지 않는다
                    logger.warning(
                        "조사 감사 기록 실패(%s): %s", backend, type(audit_exc).__name__
                    )

    return _run
