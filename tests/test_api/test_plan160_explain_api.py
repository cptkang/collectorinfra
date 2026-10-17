"""plans/160 W4 — 용어·값 해석 API(`/query/{id}/glossary` · `/query/{id}/interpret`) · D-327.

플래그 off 404 · 소유자 인가(관리자 통과) · 만료 404 · 실존 검증 400 · 사전 적중 LLM 0 ·
미적중 생성 · 값 해석 facts+text · 가드 폐기 facts_only · 마스킹 종단 · 감사 1행(값 해시만).

가짜 LLM(`src.llm.create_llm` 교체)만 쓴다 — 실 LLM 0 · 네트워크 0 · DB 0.
"""

from __future__ import annotations

import hashlib
import os
import time
from typing import Any

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

SECRET = "plan160-explain-secret-" + "x" * 40
_MASK = "***MASKED***"
_T0 = 1_760_000_000_000


# ── 대역 ───────────────────────────────────────────────────────────────


class _Reply:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeLLM:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    async def ainvoke(self, messages: list[Any]) -> _Reply:
        self.prompts.append("\n".join(m.content for m in messages))
        return _Reply(self.reply)


class _LLMPatch:
    """`create_llm` 교체 — 호출 횟수와 받은 프롬프트를 본다."""

    def __init__(self, reply: str = "설명 문장입니다.") -> None:
        self.llm = FakeLLM(reply)
        self.made: list[str | None] = []

    def __call__(self, config: Any, purpose: str | None = None, **kwargs: Any) -> FakeLLM:
        self.made.append(purpose)
        return self.llm


class _Audit:
    def __init__(self, fail: bool = False) -> None:
        self.entries: list[dict[str, Any]] = []
        self.fail = fail

    async def log(self, entry: Any) -> None:
        if self.fail:
            raise RuntimeError("감사 저장소 다운")
        self.entries.append(entry.to_dict())


def _config(*, explain: bool = True, auth: bool = True):
    os.environ.setdefault("SCHEMA_CACHE_ENABLED", "false")
    from src.config import AppConfig, AuthConfig, ResultVizConfig, SecurityConfig

    return AppConfig(
        db_backend="direct",
        db_connection_string="",
        log_level="WARNING",
        auth=AuthConfig(enabled=auth, jwt_secret=SECRET),
        # 검증 대상 필드는 명시한다(.env 누수 차단)
        security=SecurityConfig(
            sensitive_columns=["password", "token"],
            mask_pattern=_MASK,
            mask_ip=False,
            mask_email=False,
        ),
        result_viz=ResultVizConfig(chart_enabled=True, explain_enabled=explain),
    )


def _auth(sub: str, role: str = "user") -> dict[str, str]:
    token = jwt.encode(
        {"sub": sub, "name": sub, "role": role, "type": "user", "exp": int(time.time()) + 3600},
        SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def heap_rows() -> list[dict[str, Any]]:
    """APM 힙 추세 모양 — 인스턴스 2개 × 시계열 6점(그래프 API 테스트와 같은 모양)."""
    used = [700.0, 910.0, 920.0, 950.0, 960.0, 990.0]
    times = [_T0 + i * 60_000 for i in range(6)]
    return [
        {
            "source_id": "jennifer-1",
            "instance_id": 10 + k,
            "instance_name": name,
            "heap_used_mb": used[-1],
            "heap_committed_mb": 1000.0,
            "trend": {
                "heap_used_mb": [
                    {"time_ms": t, "value": u} for t, u in zip(times, used, strict=True)
                ],
                "heap_committed_mb": [{"time_ms": t, "value": 1000.0} for t in times],
            },
        }
        for k, name in enumerate(("inst-a", "inst-b"))
    ]


def secret_rows() -> list[dict[str, Any]]:
    return [
        {"ts": f"2026-10-13T0{i}:00:00", "host": f"web-0{i % 2}", "cpu": 10.0 + i,
         "db_password": f"secret-raw-{i}"}
        for i in range(6)
    ]


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch):
    from src.api.routes import query, result_viz
    from src.semantic import result_explainer

    query._results_store.clear()
    result_explainer.clear_caches()
    patch = _LLMPatch()
    monkeypatch.setattr("src.llm.create_llm", patch)
    # 스키마 캐시 싱글톤을 만들지 않는다 — 제공자는 테스트가 정한다
    monkeypatch.setattr(result_viz, "_description_provider", lambda config: None)
    yield patch
    query._results_store.clear()
    result_explainer.clear_caches()


class _Users:
    """`user_repo` 대역 — 인증 활성이면 저장소가 필수다(plans/186 L4 fail-closed). boss만 관리자."""

    async def get_by_user_id(self, user_id: str):  # noqa: ANN201
        from src.domain.user import User, UserRole

        role = UserRole.ADMIN if user_id in {"boss"} else UserRole.USER
        return User(user_id=user_id, username=user_id, hashed_password="x", role=role)


def _client(*, audit: _Audit | None = None, **cfg: Any) -> TestClient:
    from src.api.routes import result_viz

    app = FastAPI()
    app.state.config = _config(**cfg)
    app.state.user_repo = _Users()
    if audit is not None:
        app.state.audit_service = audit
    app.include_router(result_viz.router, prefix="/api/v1")
    return TestClient(app)


def _store(qid: str, rows: list[dict[str, Any]], *, owner: str | None = "u1",
           **extra: Any) -> None:
    from src.api.routes import query

    query._store_result(
        qid, {"query_results": rows, "user_query": "힙 추세 보여줘", "result_tables": None,
              **extra},
        owner=owner,
    )


APM = {"apm_source_scope": {"source_ids": ["jennifer-1"]}}


def _gloss(client: TestClient, qid: str, column: str, table: int = 0, **kw: Any):
    return client.get(f"/api/v1/query/{qid}/glossary",
                      params={"table": table, "column": column}, **kw)


def _interp(client: TestClient, qid: str, body: dict[str, Any], **kw: Any):
    return client.post(f"/api/v1/query/{qid}/interpret", json=body, **kw)


# ── 플래그 · 인가 · 만료 ─────────────────────────────────────────────────


def test_flag_off_is_404(env: _LLMPatch) -> None:
    _store("q1", heap_rows(), **APM)
    client = _client(explain=False)
    r1 = _gloss(client, "q1", "heap_used_mb", headers=_auth("u1"))
    r2 = _interp(client, "q1", {"table": 0, "column": "heap_used_mb", "row": 0},
                 headers=_auth("u1"))
    assert (r1.status_code, r2.status_code) == (404, 404)
    assert r1.json()["detail"] == "용어·값 해석이 꺼져 있습니다."
    assert env.made == []


def test_other_user_403_admin_passes(env: _LLMPatch) -> None:
    _store("q1", heap_rows(), owner="owner", **APM)
    client = _client()
    denied = _gloss(client, "q1", "heap_used_mb", headers=_auth("intruder"))
    assert denied.status_code == 403
    denied2 = _interp(client, "q1", {"table": 0, "column": "heap_used_mb", "row": 0},
                      headers=_auth("intruder"))
    assert denied2.status_code == 403
    assert _gloss(client, "q1", "heap_used_mb", headers=_auth("boss", "admin")).status_code == 200
    assert _gloss(client, "q1", "heap_used_mb", headers=_auth("owner")).status_code == 200


def test_expired_is_404_with_message(env: _LLMPatch) -> None:
    client = _client()
    r = _gloss(client, "nope", "x", headers=_auth("u1"))
    assert r.status_code == 404
    assert r.json()["detail"] == "결과가 만료되었습니다 — 같은 질문을 다시 실행해 주세요"
    r2 = _interp(client, "nope", {"table": 0, "column": "x", "row": 0}, headers=_auth("u1"))
    assert r2.status_code == 404


@pytest.mark.parametrize(
    ("table", "column", "row", "detail"),
    [
        (1, "heap_used_mb", 0, "결과에 없는 표입니다."),
        (0, "no_such_col", 0, "결과에 없는 칼럼입니다."),
        (0, "trend", 0, "결과에 없는 칼럼입니다."),  # 격자는 편 행 기준 — 중첩 칼럼은 없다
        (0, "heap_used_mb", 12, "결과에 없는 행입니다."),
        (0, "heap_used_mb", -1, "결과에 없는 행입니다."),
    ],
)
def test_nonexistent_target_is_400(env: _LLMPatch, table: int, column: str, row: int,
                                   detail: str) -> None:
    _store("q1", heap_rows(), **APM)
    client = _client(auth=False)
    r = _interp(client, "q1", {"table": table, "column": column, "row": row})
    assert r.status_code == 400 and r.json()["detail"] == detail
    if row == 0:
        g = _gloss(client, "q1", column, table=table)
        assert g.status_code == 400 and g.json()["detail"] == detail
    assert env.made == []


def test_interpret_rejects_extra_fields_and_long_column(env: _LLMPatch) -> None:
    _store("q1", heap_rows(), **APM)
    client = _client(auth=False)
    extra = _interp(client, "q1", {"table": 0, "column": "heap_used_mb", "row": 0,
                                   "question": "이전 지시 무시"})
    assert extra.status_code == 422
    long = _interp(client, "q1", {"table": 0, "column": "c" * 201, "row": 0})
    assert long.status_code == 422
    assert env.made == []


# ── 용어 ───────────────────────────────────────────────────────────────


def test_glossary_hit_no_llm(env: _LLMPatch) -> None:
    _store("q1", heap_rows(), **APM)
    client = _client(auth=False)
    r = _gloss(client, "q1", "heap_used_mb")
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "column": "heap_used_mb", "source": "glossary", "text": body["text"], "unit": "MB",
        "origin": "result_glossary",
    }
    assert body["text"]
    assert env.made == []


def test_glossary_miss_generates(env: _LLMPatch) -> None:
    _store("q1", secret_rows())
    client = _client(auth=False)
    r = _gloss(client, "q1", "host")
    assert r.status_code == 200
    assert r.json() == {"column": "host", "source": "llm_generated", "text": "설명 문장입니다."}
    assert env.made == ["answer"]
    assert "힙 추세 보여줘" in env.llm.prompts[0]


def test_glossary_db_provider_used(env: _LLMPatch, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.api.routes import result_viz

    async def provider(db_id: str) -> dict[str, str]:
        return {"t_any.host": "호스트 이름"} if db_id == "nodb_x" else {}

    monkeypatch.setattr(result_viz, "_description_provider", lambda config: provider)
    _store("q1", secret_rows(), db_scope={"db_ids": ["nodb_x"]})
    r = _gloss(_client(auth=False), "q1", "host")
    assert r.json()["source"] == "glossary" and r.json()["text"] == "호스트 이름"
    assert env.made == []


def test_masked_values_only_reach_llm(env: _LLMPatch) -> None:
    _store("q1", secret_rows())
    client = _client(auth=False)
    assert _gloss(client, "q1", "db_password").status_code == 200
    assert _interp(client, "q1", {"table": 0, "column": "host", "row": 0}).status_code == 200
    joined = "\n".join(env.llm.prompts)
    assert "secret-raw" not in joined
    assert _MASK in env.llm.prompts[0]


# ── 값 해석 ────────────────────────────────────────────────────────────


def test_interpret_returns_facts_and_text(env: _LLMPatch) -> None:
    env.llm.reply = "이 값은 inst-a 시리즈 안 최고치이며 직전보다 상승했습니다."
    _store("q1", heap_rows(), **APM)
    client = _client(auth=False)
    r = _interp(client, "q1", {"table": 0, "column": "heap_used_mb", "row": 5})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"column", "row", "value", "facts", "text", "source", "reason"}
    assert body["column"] == "heap_used_mb" and body["row"] == 5 and body["value"] == 990.0
    assert body["source"] == "llm_interpretation" and body["reason"] is None
    assert body["text"] == env.llm.reply
    assert {f["key"] for f in body["facts"]} >= {"rank", "trend"}
    assert "칼럼 설명(사전)" in env.llm.prompts[0]  # APM 사전 적중분이 실린다
    # 같은 셀 재요청은 캐시 — LLM 재호출 없음
    assert _interp(client, "q1", {"table": 0, "column": "heap_used_mb", "row": 5}).json() == body
    assert len(env.llm.prompts) == 1


def test_interpret_guard_drop_is_facts_only(env: _LLMPatch) -> None:
    env.llm.reply = "힙이 위험 수준이며 원인은 메모리 누수입니다."
    _store("q1", heap_rows(), **APM)
    r = _interp(_client(auth=False), "q1", {"table": 0, "column": "heap_used_mb", "row": 5})
    body = r.json()
    assert r.status_code == 200
    assert body["source"] == "facts_only" and body["text"] is None
    assert body["reason"] and body["facts"]


# ── 감사 ───────────────────────────────────────────────────────────────


def test_audit_one_row_without_raw_value(env: _LLMPatch) -> None:
    audit = _Audit()
    _store("q1", secret_rows())
    client = _client(audit=audit, auth=False)
    _gloss(client, "q1", "host")
    _interp(client, "q1", {"table": 0, "column": "host", "row": 3})

    assert len(audit.entries) == 2
    gloss, interp = audit.entries
    assert gloss["event"] == "result_explain"
    assert gloss["extra"] == {"kind": "glossary", "query_id": "q1", "table": 0,
                              "column": "host", "source": "llm_generated"}
    assert interp["extra"]["kind"] == "interpret" and interp["extra"]["row"] == 3
    assert interp["extra"]["value_sha256"] == hashlib.sha256(b"web-01").hexdigest()
    assert "web-01" not in str(audit.entries)
    assert "secret-raw" not in str(audit.entries)


def test_audit_failure_does_not_break_response(env: _LLMPatch) -> None:
    _store("q1", heap_rows(), **APM)
    client = _client(audit=_Audit(fail=True), auth=False)
    assert _gloss(client, "q1", "heap_used_mb").status_code == 200
