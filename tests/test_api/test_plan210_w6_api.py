"""plans/210 W6 — 관리자 「APM·SMS 연계」 API(§3.10).

실 PG 대신 저장소 대역(`_FakeRepo` — 실 저장소와 같은 version·origin 규칙)과 실 사본
(`RedisApmHostMapCache` + `FakeAsyncRedis`)을 쓴다. 인가는 실 `require_admin_user` 경로를
JWT로 탄다.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noise_gate.application import host_candidates as hc
from noise_gate.application.apm_host_map_scan import ScanRunningError
from noise_gate.domain.apm_host_map import (
    ApmHostMapExistsError,
    ApmHostMapVersionError,
    cache_field,
    normalize_targets,
)
from noise_gate.infrastructure.apm_host_map_cache import RedisApmHostMapCache, map_key
from src.api.routes import admin_apm_host_map as routes
from tests.mocks.async_redis import FakeAsyncRedis

AUTH_SECRET = "auth-secret-210w6-0123456789abcdef0123"
ADMIN_SECRET = "admin-secret-210w6-0123456789abcdef012"
BASE = "/api/v1/admin/apm-host-map"


def _token(payload: dict[str, Any], secret: str) -> str:
    return jwt.encode(
        {**payload, "exp": datetime.now(UTC) + timedelta(hours=1)}, secret, algorithm="HS256"
    )


ADMIN_BG = {"Authorization": "Bearer " + _token({"sub": "ops", "type": "admin"}, ADMIN_SECRET)}
#: 일반 로그인(user_token)으로 들어온 관리자 — DB role이 admin(docs/18 2026-09-30)
ADMIN_USER = {"Authorization": "Bearer " + _token({"sub": "a", "type": "user"}, AUTH_SECRET)}
PLAIN_USER = {"Authorization": "Bearer " + _token({"sub": "u", "type": "user"}, AUTH_SECRET)}


class _Users:
    """`user_repo` 대역 — `a`만 관리자."""

    async def get_by_user_id(self, user_id: str):  # noqa: ANN201
        from src.domain.user import User, UserRole

        role = UserRole.ADMIN if user_id == "a" else UserRole.USER
        return User(user_id=user_id, username=user_id, hashed_password="x", role=role)


class _Audit:
    def __init__(self) -> None:
        self.entries: list[Any] = []

    async def log(self, entry: Any) -> None:
        self.entries.append(entry)

    def actions(self) -> list[dict[str, Any]]:
        return [e.extra for e in self.entries]


class _FakeRepo:
    """`PostgresApmHostMapRepository` 관리 메서드 대역 — version·origin·basis 규칙을 실물대로."""

    def __init__(self) -> None:
        self.rows: dict[int, dict[str, Any]] = {}
        self.runs: list[dict[str, Any]] = []
        self.snapshots: dict[str, tuple[str, list[dict[str, Any]]]] = {}
        self.applied: list[Any] = []
        self._seq = 0

    def seed(self, **row: Any) -> dict[str, Any]:
        self._seq += 1
        base = {
            "id": self._seq,
            "source_id": "jen1",
            "domain_id": "1001",
            "domain_name": "인터넷뱅킹",
            "scope": "instance",
            "match_key": f"was{self._seq}",
            "instance_example": f"was{self._seq}",
            "origin": "auto",
            "basis": "vm_exact",
            "state": "active",
            "verified": False,
            "targets": [],
            "evidence": {},
            "note": "",
            "version": 1,
            "updated_by": "system",
            "updated_at": "2026-11-01T00:00:00+00:00",
        }
        base.update(row)
        base["targets"] = normalize_targets(base["targets"])
        self.rows[base["id"]] = base
        return copy.deepcopy(base)

    def _check(self, map_id: int, version: int) -> dict[str, Any] | None:
        row = self.rows.get(map_id)
        if row is None:
            return None
        if row["version"] != version:
            raise ApmHostMapVersionError(map_id, row["version"])
        return row

    async def list_maps(
        self,
        *,
        source_id=None,
        state=None,
        origin=None,
        basis=None,  # noqa: ANN001
        query=None,
        review_only=False,
        limit=50,
        offset=0,
    ):  # noqa: ANN001, ANN201
        rows = list(self.rows.values())
        if state:
            rows = [r for r in rows if r["state"] == state]
        if review_only:
            rows = [r for r in rows if routes._review_reasons(r)]
        if query:
            rows = [r for r in rows if query in str(r["match_key"]) + str(r["domain_name"])]
        return [copy.deepcopy(r) for r in rows[offset : offset + limit]], len(rows)

    async def get(self, map_id: int):  # noqa: ANN201
        row = self.rows.get(map_id)
        return copy.deepcopy(row) if row else None

    async def all_rows(self):  # noqa: ANN201
        return [copy.deepcopy(r) for r in self.rows.values()]

    async def create_manual(
        self,
        *,
        source_id,
        domain_id,
        scope,
        match_key,
        targets,  # noqa: ANN001
        state,
        domain_name,
        instance_example,
        note,
        by,
    ):  # noqa: ANN001, ANN201
        for r in self.rows.values():
            if (r["source_id"], r["domain_id"], r["scope"], r["match_key"]) == (
                source_id,
                domain_id,
                scope,
                match_key,
            ):
                raise ApmHostMapExistsError(r["id"])
        return self.seed(
            source_id=source_id,
            domain_id=domain_id,
            scope=scope,
            match_key=match_key,
            targets=targets,
            state=state,
            domain_name=domain_name,
            instance_example=instance_example,
            note=note,
            origin="manual",
            basis="manual",
            updated_by=by,
        )

    async def update(self, map_id, *, version, targets=None, state=None, note=None, by=""):  # noqa: ANN001, ANN201
        if state is not None and state not in ("active", "disabled"):
            raise ValueError(state)
        row = self._check(map_id, version)
        if row is None:
            return None
        if targets is not None:
            row["targets"] = normalize_targets(list(targets))
            row["basis"] = "manual"
        row["state"] = state or row["state"]
        row["note"] = row["note"] if note is None else note
        row["origin"] = "manual"
        row["evidence"] = {
            k: v for k, v in row["evidence"].items() if k not in ("changed", "scan_suggests")
        }
        row.update(updated_by=by, version=row["version"] + 1)
        return copy.deepcopy(row)

    async def verify(self, map_id, *, version, by=""):  # noqa: ANN001, ANN201
        row = self._check(map_id, version)
        if row is None:
            return None
        row.update(verified=True, updated_by=by, version=row["version"] + 1)
        return copy.deepcopy(row)

    async def delete(self, map_id, *, version):  # noqa: ANN001, ANN201
        row = self._check(map_id, version)
        if row is None:
            return None
        return self.rows.pop(map_id)

    async def apply_auto(self, source_id, proposal, *, retry_hours=24):  # noqa: ANN001, ANN201
        self.applied.append(proposal)
        for r in self.rows.values():
            if (r["source_id"], r["scope"], r["match_key"]) == (
                source_id,
                proposal.scope,
                proposal.match_key,
            ):
                r.update(
                    targets=[dict(t) for t in proposal.targets],
                    basis=proposal.basis,
                    state=proposal.state,
                    version=r["version"] + 1,
                )
                return "update", copy.deepcopy(r)
        return "insert", None

    async def list_scan_runs(self, *, limit=20):  # noqa: ANN001, ANN201
        return copy.deepcopy(self.runs[:limit])

    async def get_scan_run(self, run_id):  # noqa: ANN001, ANN201
        return next((copy.deepcopy(r) for r in self.runs if r["run_id"] == run_id), None)

    async def current_snapshot_run(self, table):  # noqa: ANN001, ANN201
        snap = self.snapshots.get(table)
        return snap[0] if snap else None

    async def snapshot_rows(self, table, *, query=None, limit=None, offset=0):  # noqa: ANN001, ANN201
        snap = self.snapshots.get(table)
        rows = [dict(r) for r in (snap[1] if snap else [])]
        if query:
            rows = [r for r in rows if any(query in str(v) for v in r.values())]
        rows = rows[offset:]
        return rows if limit is None else rows[:limit]


class _Scan:
    def __init__(self, running: str | None = None) -> None:
        self.running = running
        self.calls: list[str] = []

    async def start_scan(self, *, requested_by: str) -> str:
        if self.running:
            raise ScanRunningError(self.running)
        self.calls.append(requested_by)
        self.running = "run-1"
        return "run-1"


def _config(*, auto: bool = True, max_targets: int = 10) -> SimpleNamespace:
    return SimpleNamespace(
        auth=SimpleNamespace(enabled=True, jwt_secret=AUTH_SECRET),
        admin=SimpleNamespace(jwt_secret=ADMIN_SECRET),
        noise_gate=SimpleNamespace(
            apm_host_map_enabled=True,
            apm_host_map_auto_enabled=auto,
            apm_host_map_max_targets=max_targets,
            apm_host_map_retry_hours=24,
        ),
    )


@pytest.fixture
def located(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """`locate_selected_server` 대역 — 서버명 `missing`은 없음 · `twin`은 동명 여럿."""
    calls: list[dict[str, Any]] = []

    async def _locate(config, *, db_ids, server_name, hostname, probe=None):  # noqa: ANN001, ANN202
        calls.append({"db_ids": list(db_ids), "server_name": server_name})
        name = server_name or hostname
        if name == "missing":
            return {"db_id": None, "row": None, "ambiguous": False, "unchecked": []}
        if name == "twin":
            return {"db_id": None, "row": None, "ambiguous": True, "unchecked": []}
        return {
            "db_id": db_ids[0],
            "ambiguous": False,
            "unchecked": [],
            "row": {"server_name": name, "hostname": name + ".corp", "ipaddress": "10.0.0.7"},
        }

    monkeypatch.setattr(hc, "locate_selected_server", _locate)
    monkeypatch.setattr(hc, "node_lookup_db_ids", lambda config: ["polestar_cm_gp", "polestar_b0"])
    return calls


def _app(
    *,
    repo: _FakeRepo | None = None,
    scan: _Scan | None = None,
    auto: bool = True,
    redis: FakeAsyncRedis | None = None,
) -> tuple[TestClient, FastAPI]:
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1")
    app.state.config = _config(auto=auto)
    app.state.user_repo = _Users()
    app.state.audit_service = _Audit()
    app.state.apm_host_map_repo = repo
    app.state.apm_host_map_cache = RedisApmHostMapCache(redis or FakeAsyncRedis())
    app.state.apm_host_map_scan_service = scan
    return TestClient(app), app


# ─── 인가 ──────────────────────────────────────────────────────────────


def test_admin_tokens_ok_both_login_paths() -> None:
    repo = _FakeRepo()
    repo.seed()
    client, _ = _app(repo=repo)
    for headers in (ADMIN_BG, ADMIN_USER):
        res = client.get(BASE, headers=headers)
        assert res.status_code == 200, res.text
        assert res.json()["total"] == 1 and res.json()["auto_enabled"] is True


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "", None),
        ("POST", "", {"source_id": "s", "domain_id": "1", "scope": "domain", "targets": []}),
        ("PUT", "/1", {"version": 1}),
        ("POST", "/1/verify", {"version": 1}),
        ("POST", "/1/preview", None),
        ("POST", "/1/resolve", {"apply": False}),
        ("DELETE", "/1", {"version": 1}),
        ("POST", "/scan", None),
        ("GET", "/scan/runs", None),
        ("GET", "/scan/runs/run-1", None),
        ("GET", "/inventory/apm", None),
    ],
)
def test_non_admin_is_403_on_every_endpoint(method: str, path: str, body: Any) -> None:
    repo = _FakeRepo()
    repo.seed()
    scan = _Scan()
    client, app = _app(repo=repo, scan=scan)
    res = client.request(method, BASE + path, headers=PLAIN_USER, json=body)
    assert res.status_code == 403
    assert scan.calls == [] and app.state.audit_service.entries == []


def test_forged_tokens_rejected() -> None:
    client, _ = _app(repo=_FakeRepo())
    forged = [
        # 사용자 시크릿으로 서명한 type=admin(break-glass 위조) · 운영자 시크릿의 type=user
        _token({"sub": "ops", "type": "admin"}, AUTH_SECRET),
        _token({"sub": "a", "type": "user"}, ADMIN_SECRET),
        # 서명은 맞지만 type 없음 · role 클레임만 admin으로 꾸민 일반 사용자
        _token({"sub": "ops"}, ADMIN_SECRET),
        _token({"sub": "u", "type": "user", "role": "admin"}, AUTH_SECRET),
    ]
    codes = [client.get(BASE, headers={"Authorization": f"Bearer {t}"}).status_code for t in forged]
    assert codes[:3] == [401, 401, 401] and codes[3] == 403
    assert client.get(BASE).status_code == 401


def test_repo_missing_is_503_structured() -> None:
    client, _ = _app(repo=None)
    res = client.get(BASE, headers=ADMIN_BG)
    assert res.status_code == 503 and res.json()["detail"]["code"] == "apm_host_map_unavailable"


def test_repo_failure_is_503_store_failed() -> None:
    repo = _FakeRepo()

    async def _boom(**_: Any) -> Any:
        raise ConnectionError("pg down")

    repo.list_maps = _boom  # type: ignore[method-assign]
    client, _ = _app(repo=repo)
    res = client.get(BASE, headers=ADMIN_BG)
    assert res.status_code == 503 and res.json()["detail"]["code"] == "apm_host_map_store_failed"
    assert "ConnectionError" in res.json()["detail"]["reason"]


# ─── 목록 · 검토 필요 ──────────────────────────────────────────────────


def test_list_review_filter_and_view_fields() -> None:
    repo = _FakeRepo()
    repo.seed()
    repo.seed(state="ambiguous")
    repo.seed(evidence={"changed": True})
    repo.seed(targets=[{"db_id": "polestar_cm_gp", "server_name": "a", "target_gone": True}])
    client, _ = _app(repo=repo)
    body = client.get(BASE, params={"review": "true"}, headers=ADMIN_BG).json()
    assert body["total"] == 3
    assert [r["review_reasons"] for r in body["rows"]] == [
        ["ambiguous"],
        ["changed"],
        ["target_gone"],
    ]
    assert all(r["evidence_text"] for r in body["rows"])
    assert client.get(BASE, params={"state": "bogus"}, headers=ADMIN_BG).status_code == 422


# ─── 수동 추가 ─────────────────────────────────────────────────────────


def _create_body(**over: Any) -> dict[str, Any]:
    body = {
        "source_id": "jen1",
        "domain_id": "1001",
        "scope": "instance",
        "instance_name": "ib-was01",
        "domain_name": "인터넷뱅킹",
        "targets": [{"db_id": "polestar_cm_gp", "server_name": "kbl-a"}],
        "state": "active",
        "note": "운영팀 확인",
    }
    body.update(over)
    return body


def test_create_manual_checks_targets_caches_and_audits(located: list[dict[str, Any]]) -> None:
    repo = _FakeRepo()
    redis = FakeAsyncRedis()
    client, app = _app(repo=repo, redis=redis)
    res = client.post(BASE, json=_create_body(), headers=ADMIN_BG)
    assert res.status_code == 201, res.text
    row = res.json()["map"]
    assert row["origin"] == "manual" and row["basis"] == "manual" and row["match_key"] == "ib-was01"
    assert row["targets"][0]["hostname"] == "kbl-a.corp" and res.json()["audit_logged"] is True
    assert located == [{"db_ids": ["polestar_cm_gp"], "server_name": "kbl-a"}]
    cached = _lookup(app, "jen1", "1001", "ib-was01")
    assert cached["instance"]["state"] == "active"
    audit = app.state.audit_service.actions()[-1]
    assert audit["action"] == "apm_host_map_create" and audit["outcome"] == "ok"
    assert audit["after"]["targets"] == [{"db_id": "polestar_cm_gp", "server_name": "kbl-a"}]
    # 같은 키 — 409 exists(+ 기존 id) · 거부도 감사
    dup = client.post(BASE, json=_create_body(), headers=ADMIN_BG)
    assert dup.status_code == 409 and dup.json()["detail"]["code"] == "exists"
    assert dup.json()["detail"]["existing_id"] == row["id"]
    assert app.state.audit_service.actions()[-1]["outcome"] == "denied"


@pytest.mark.parametrize(
    ("over", "status", "code"),
    [
        (
            {"targets": [{"db_id": "polestar_cm_gp", "server_name": "missing"}]},
            400,
            "server_not_found",
        ),
        ({"targets": [{"server_name": "twin"}]}, 409, "server_ambiguous"),
        ({"targets": [{"db_id": "other_db", "server_name": "kbl-a"}]}, 400, "invalid_target_db"),
        ({"targets": [{"db_id": "polestar_cm_gp", "server_name": "a b;"}]}, 400, "invalid_target"),
        ({"targets": []}, 400, "targets_required"),
        ({"targets": [{"server_name": f"s{i}"} for i in range(11)]}, 400, "too_many_targets"),
        ({"scope": "workload", "instance_name": "plain"}, 400, "invalid_key"),
    ],
)
def test_create_manual_rejected(
    located: list[dict[str, Any]], over: dict[str, Any], status: int, code: str
) -> None:
    repo = _FakeRepo()
    client, _ = _app(repo=repo)
    res = client.post(BASE, json=_create_body(**over), headers=ADMIN_BG)
    assert res.status_code == status, res.text
    assert res.json()["detail"]["code"] == code
    assert repo.rows == {}


@pytest.mark.parametrize(
    "over",
    [
        {"state": "ambiguous"},
        {"targets": [{"db_id": "polestar'; --", "server_name": "a"}]},
        {"targets": [{"server_name": "a"}] * 51},
        {"note": "x" * 1001},
        {"scope": "bogus"},
    ],
)
def test_create_body_validation_422(located: list[dict[str, Any]], over: dict[str, Any]) -> None:
    client, _ = _app(repo=_FakeRepo())
    assert client.post(BASE, json=_create_body(**over), headers=ADMIN_BG).status_code == 422


@pytest.mark.parametrize(
    ("over", "status", "code", "outcome"),
    [
        ({"scope": "workload", "instance_name": "plain"}, 400, "invalid_key", "denied"),
        (
            {"targets": [{"db_id": "polestar_cm_gp", "server_name": "missing"}]},
            400,
            "server_not_found",
            "denied",
        ),
        ({"targets": [{"server_name": "twin"}]}, 409, "server_ambiguous", "denied"),
        ({"targets": [{"server_name": f"s{i}"} for i in range(11)]}, 400, "too_many_targets",
         "denied"),
    ],
)
def test_create_rejections_are_audited(
    located: list[dict[str, Any]], over: dict[str, Any], status: int, code: str, outcome: str
) -> None:
    """L-1 — 수동 추가의 검증 거부도 감사에 남는다(`reason` = 응답 오류 코드)."""
    client, app = _app(repo=_FakeRepo())
    res = client.post(BASE, json=_create_body(**over), headers=ADMIN_BG)
    assert res.status_code == status and res.json()["detail"]["code"] == code
    audit = app.state.audit_service.actions()[-1]
    assert audit["action"] == "apm_host_map_create"
    assert (audit["outcome"], audit["reason"]) == (outcome, code)
    assert audit["after"]["source_id"] == "jen1"


def test_create_verify_failed_is_audited_as_failed(
    monkeypatch: pytest.MonkeyPatch, located: list[dict[str, Any]]
) -> None:
    async def _down(config, **_: Any):  # noqa: ANN001, ANN202
        raise TimeoutError("mcp")

    monkeypatch.setattr(hc, "locate_selected_server", _down)
    client, app = _app(repo=_FakeRepo())
    assert client.post(BASE, json=_create_body(), headers=ADMIN_BG).status_code == 502
    audit = app.state.audit_service.actions()[-1]
    assert (audit["outcome"], audit["reason"]) == ("failed", "verify_failed")


def test_create_repo_value_error_is_fixed_text_and_audited(
    located: list[dict[str, Any]],
) -> None:
    """L-2 — 저장소 `ValueError` 문구(입력값 포함)를 응답·감사에 싣지 않는다."""
    repo = _FakeRepo()

    async def _bad(**_: Any) -> Any:
        raise ValueError("SECRET-INPUT 'x'")

    repo.create_manual = _bad  # type: ignore[method-assign]
    client, app = _app(repo=repo)
    res = client.post(BASE, json=_create_body(), headers=ADMIN_BG)
    assert res.status_code == 400 and res.json()["detail"]["code"] == "invalid_key"
    assert "SECRET-INPUT" not in res.text
    audit = app.state.audit_service.actions()[-1]
    assert (audit["outcome"], audit["reason"]) == ("denied", "invalid_key")
    assert "SECRET-INPUT" not in repr(audit)


@pytest.mark.parametrize(
    "over",
    [
        {"source_id": "jen:1"},
        {"source_id": "Jen1"},
        {"source_id": "jen 1"},
        {"source_id": "a" * 17},
        {"domain_id": "10a"},
        {"domain_id": "-1"},
        {"domain_id": "1|2"},
        {"domain_id": "1" * 20},
    ],
)
def test_create_key_format_422(located: list[dict[str, Any]], over: dict[str, Any]) -> None:
    """L-4 — 소스 id는 게이트웨이 소문자 슬러그 · 도메인 id는 숫자 문자열만."""
    repo = _FakeRepo()
    client, _ = _app(repo=repo)
    assert client.post(BASE, json=_create_body(**over), headers=ADMIN_BG).status_code == 422
    assert repo.rows == {}


def test_source_id_pattern_matches_gateway_rule() -> None:
    """API 소스 id 패턴이 게이트웨이 설정 규칙(`source_id_error`의 형식 부분)과 같은 문자열이다."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    src = (root / "apm_gateway" / "apm_gateway" / "domain" / "sources.py").read_text(
        encoding="utf-8"
    )
    m = re.search(r'_SOURCE_ID = re\.compile\(r"([^"]+)"\)', src)
    assert m is not None and m.group(1) == routes._SOURCE_ID_PATTERN


@pytest.mark.parametrize(
    "source_id", ["", "  ", "jen:1", ":", "jen1", "default", "a|b"]
)
def test_valid_key_and_map_key_agree_on_empty_and_colon(source_id: str) -> None:
    """L-4 — 매핑 키 판정(`valid_key`)과 사본 키(`map_key`)가 빈 값·`:`에서 같은 판정(읽기만)."""
    from noise_gate.domain.apm_host_map import valid_key

    try:
        map_key(source_id)
        cache_ok = True
    except ValueError:
        cache_ok = False
    key_ok = valid_key(source_id, "1001", "domain", "")
    if "|" in source_id:  # 사본 키는 `|`를 막지 않는다 — 매핑 키가 더 좁다(저장 전 거부)
        assert key_ok is False
        return
    assert key_ok is cache_ok


@pytest.mark.parametrize("source_id", ["smsip", "smshost", "req", "inflight"])
def test_valid_key_rejects_map_key_reserved_words(source_id: str) -> None:
    from noise_gate.domain.apm_host_map import valid_key

    with pytest.raises(ValueError):
        map_key(source_id)
    assert valid_key(source_id, "1001", "domain", "") is False


def test_create_disabled_without_targets_is_link_absent(located: list[dict[str, Any]]) -> None:
    client, app = _app(repo=_FakeRepo())
    res = client.post(BASE, json=_create_body(targets=[], state="disabled"), headers=ADMIN_BG)
    assert res.status_code == 201, res.text
    assert _lookup(app, "jen1", "1001", "ib-was01")["instance"]["state"] == "disabled"


def test_verify_failure_is_502_and_nothing_saved(
    monkeypatch: pytest.MonkeyPatch, located: list[dict[str, Any]]
) -> None:
    async def _down(config, **_: Any):  # noqa: ANN001, ANN202
        raise TimeoutError("mcp")

    monkeypatch.setattr(hc, "locate_selected_server", _down)
    repo = _FakeRepo()
    client, _ = _app(repo=repo)
    res = client.post(BASE, json=_create_body(), headers=ADMIN_BG)
    assert res.status_code == 502 and res.json()["detail"]["code"] == "verify_failed"
    assert repo.rows == {}


# ─── 수정 · 확인 · 삭제 ────────────────────────────────────────────────


def _lookup(app: FastAPI, source: str, domain: str, instance: str) -> dict[str, Any]:
    """사본의 instance 범위 값 → `{"instance": 값}`(없으면 `{}`)."""
    import asyncio
    import json

    raw = asyncio.run(
        app.state.apm_host_map_cache.redis.hget(
            map_key(source), cache_field("instance", domain, instance)
        )
    )
    return {"instance": json.loads(raw)} if raw else {}


def test_edit_auto_row_becomes_manual_and_disabled_reaches_cache(
    located: list[dict[str, Any]],
) -> None:
    repo = _FakeRepo()
    seeded = repo.seed(
        match_key="ib-was01",
        instance_example="ib-was01",
        targets=[{"db_id": "polestar_cm_gp", "server_name": "kbl-a"}],
        evidence={"changed": True},
    )
    client, app = _app(repo=repo)
    res = client.put(
        f"{BASE}/{seeded['id']}",
        json={"version": 1, "state": "disabled", "note": "안 씀"},
        headers=ADMIN_USER,
    )
    assert res.status_code == 200, res.text
    row = res.json()["map"]
    assert row["origin"] == "manual" and row["state"] == "disabled" and row["version"] == 2
    assert row["basis"] == "vm_exact"  # 대상을 안 바꾸면 근거 유지
    assert row["review_reasons"] == []
    assert located == []  # 대상 안 바꿈 → 실재 확인 없음
    assert _lookup(app, "jen1", "1001", "ib-was01")["instance"]["state"] == "disabled"
    audit = app.state.audit_service.actions()[-1]
    assert audit["action"] == "apm_host_map_update" and audit["before"]["origin"] == "auto"
    assert audit["after"]["origin"] == "manual" and audit["after"]["state"] == "disabled"


def test_edit_targets_reuses_kept_and_checks_new(located: list[dict[str, Any]]) -> None:
    repo = _FakeRepo()
    seeded = repo.seed(targets=[{"db_id": "polestar_cm_gp", "server_name": "kbl-a"}])
    client, _ = _app(repo=repo)
    res = client.put(
        f"{BASE}/{seeded['id']}",
        json={
            "version": 1,
            "targets": [
                {"db_id": "polestar_cm_gp", "server_name": "kbl-a"},
                {"db_id": "polestar_b0", "server_name": "kbl-b"},
            ],
        },
        headers=ADMIN_BG,
    )
    assert res.status_code == 200, res.text
    assert [t["server_name"] for t in res.json()["map"]["targets"]] == ["kbl-a", "kbl-b"]
    assert res.json()["map"]["basis"] == "manual"
    assert located == [{"db_ids": ["polestar_b0"], "server_name": "kbl-b"}]


def test_version_conflict_409_on_update_verify_delete(located: list[dict[str, Any]]) -> None:
    repo = _FakeRepo()
    seeded = repo.seed(version=3)
    client, app = _app(repo=repo)
    for method, path, body in (
        ("PUT", "", {"version": 2, "note": "x"}),
        ("POST", "/verify", {"version": 2}),
        ("DELETE", "", {"version": 2}),
    ):
        res = client.request(method, f"{BASE}/{seeded['id']}{path}", json=body, headers=ADMIN_BG)
        assert res.status_code == 409, (method, path, res.text)
        assert res.json()["detail"] == {
            **res.json()["detail"],
            "code": "version_conflict",
            "current_version": 3,
        }
    assert repo.rows[seeded["id"]]["version"] == 3
    assert [a["outcome"] for a in app.state.audit_service.actions()] == ["denied"] * 3


def test_update_active_without_targets_rejected(located: list[dict[str, Any]]) -> None:
    repo = _FakeRepo()
    seeded = repo.seed(state="disabled", origin="manual", basis="manual")
    client, _ = _app(repo=repo)
    res = client.put(
        f"{BASE}/{seeded['id']}", json={"version": 1, "state": "active"}, headers=ADMIN_BG
    )
    assert res.status_code == 400 and res.json()["detail"]["code"] == "targets_required"
    assert client.put(f"{BASE}/999", json={"version": 1}, headers=ADMIN_BG).status_code == 404


@pytest.mark.parametrize(
    ("targets", "status", "code"),
    [
        ([{"db_id": "polestar_cm_gp", "server_name": "missing"}], 400, "server_not_found"),
        ([{"server_name": "twin"}], 409, "server_ambiguous"),
        ([{"server_name": f"s{i}"} for i in range(11)], 400, "too_many_targets"),
    ],
)
def test_update_rejections_are_audited(
    located: list[dict[str, Any]], targets: list[dict[str, Any]], status: int, code: str
) -> None:
    """L-1 — 수정의 대상 검증 거부도 감사(`denied` · before 포함)."""
    repo = _FakeRepo()
    seeded = repo.seed()
    client, app = _app(repo=repo)
    res = client.put(
        f"{BASE}/{seeded['id']}", json={"version": 1, "targets": targets}, headers=ADMIN_BG
    )
    assert res.status_code == status and res.json()["detail"]["code"] == code
    audit = app.state.audit_service.actions()[-1]
    assert audit["action"] == "apm_host_map_update"
    assert (audit["outcome"], audit["reason"]) == ("denied", code)
    assert audit["before"]["id"] == seeded["id"]
    assert repo.rows[seeded["id"]]["version"] == 1


def test_update_invalid_state_is_fixed_text_and_audited(located: list[dict[str, Any]]) -> None:
    """L-2 — 저장소 `ValueError`(입력값 포함 문구)를 고정 코드·문구로 바꾸고 감사에 남긴다."""
    repo = _FakeRepo()
    seeded = repo.seed()

    async def _bad(map_id, **_: Any):  # noqa: ANN001, ANN202
        raise ValueError("SECRET-STATE 'zzz'")

    repo.update = _bad  # type: ignore[method-assign]
    client, app = _app(repo=repo)
    res = client.put(f"{BASE}/{seeded['id']}", json={"version": 1, "note": "x"}, headers=ADMIN_BG)
    assert res.status_code == 400 and res.json()["detail"]["code"] == "invalid_state"
    assert "SECRET-STATE" not in res.text
    audit = app.state.audit_service.actions()[-1]
    assert (audit["outcome"], audit["reason"]) == ("denied", "invalid_state")
    assert "SECRET-STATE" not in repr(audit)


def test_verify_auto_only_and_ok() -> None:
    repo = _FakeRepo()
    auto = repo.seed()
    manual = repo.seed(origin="manual", basis="manual")
    client, app = _app(repo=repo)
    res = client.post(f"{BASE}/{auto['id']}/verify", json={"version": 1}, headers=ADMIN_BG)
    assert res.status_code == 200 and res.json()["map"]["verified"] is True
    assert app.state.audit_service.actions()[-1]["action"] == "apm_host_map_verify"
    res = client.post(f"{BASE}/{manual['id']}/verify", json={"version": 1}, headers=ADMIN_BG)
    assert res.status_code == 400 and res.json()["detail"]["code"] == "verify_auto_only"


def test_delete_removes_cache_and_audits(located: list[dict[str, Any]]) -> None:
    repo = _FakeRepo()
    client, app = _app(repo=repo)
    created = client.post(BASE, json=_create_body(), headers=ADMIN_BG).json()["map"]
    assert _lookup(app, "jen1", "1001", "ib-was01")
    res = client.request("DELETE", f"{BASE}/{created['id']}", json={"version": 1}, headers=ADMIN_BG)
    assert res.status_code == 200 and res.json()["audit_logged"] is True
    assert repo.rows == {} and _lookup(app, "jen1", "1001", "ib-was01") == {}
    audit = app.state.audit_service.actions()[-1]
    assert audit["action"] == "apm_host_map_delete" and audit["before"]["id"] == created["id"]
    assert audit["after"] is None


# ─── 다시 찾기 ─────────────────────────────────────────────────────────


def _snapshots(repo: _FakeRepo) -> None:
    repo.snapshots = {
        "apm_inventory": (
            "run-0",
            [
                {
                    "source_id": "jen1",
                    "domain_id": "1001",
                    "domain_name": "인터넷뱅킹",
                    "instance_id": "1",
                    "instance_name": "was1",
                    "ip": "10.0.0.7",
                    "host_name": "kbl-a",
                    "workload_key": "",
                    "host_address": "",
                    "host_address_status": "",
                }
            ],
        ),
        "sms_inventory": (
            "run-0",
            [
                {
                    "db_id": "polestar_cm_gp",
                    "resource_id": "r1",
                    "server_name": "kbl-a",
                    "hostname": "kbl-a",
                    "ip": "10.0.0.7",
                    "nic_ips": [],
                }
            ],
        ),
    }


def test_preview_shows_proposal_without_saving() -> None:
    repo = _FakeRepo()
    _snapshots(repo)
    seeded = repo.seed(state="failed", match_key="was1")
    client, app = _app(repo=repo)
    res = client.post(f"{BASE}/{seeded['id']}/preview", headers=ADMIN_BG)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["proposal"]["state"] == "active" and body["applied"] is False
    assert body["proposal"]["targets"][0]["server_name"] == "kbl-a"
    assert body["preview_only"] is False and repo.applied == []
    assert app.state.audit_service.entries == []


def test_preview_without_snapshot_gives_reason() -> None:
    repo = _FakeRepo()
    seeded = repo.seed()
    client, _ = _app(repo=repo)
    body = client.post(f"{BASE}/{seeded['id']}/preview", headers=ADMIN_BG).json()
    assert body["proposal"] is None and body["reason"] == "apm_map_no_inventory"


def test_resolve_apply_writes_and_audits_admin_rows_preview_only() -> None:
    repo = _FakeRepo()
    _snapshots(repo)
    auto = repo.seed(state="failed", match_key="was1")
    manual = repo.seed(
        origin="manual", basis="manual", match_key="was1", domain_id="1001", source_id="jen2"
    )
    client, app = _app(repo=repo)
    res = client.post(
        f"{BASE}/{auto['id']}/resolve", json={"apply": True, "version": 1}, headers=ADMIN_BG
    )
    assert res.status_code == 200, res.text
    assert res.json()["applied"] is True and res.json()["map"]["state"] == "active"
    assert app.state.audit_service.actions()[-1]["action"] == "apm_host_map_resolve"
    res = client.post(
        f"{BASE}/{manual['id']}/resolve", json={"apply": True, "version": 1}, headers=ADMIN_BG
    )
    assert res.status_code == 409 and res.json()["detail"]["code"] == "preview_only"


def test_resolve_apply_refused_when_auto_off() -> None:
    repo = _FakeRepo()
    _snapshots(repo)
    seeded = repo.seed(state="failed", match_key="was1")
    client, _ = _app(repo=repo, auto=False)
    res = client.post(
        f"{BASE}/{seeded['id']}/resolve", json={"apply": True, "version": 1}, headers=ADMIN_BG
    )
    assert res.status_code == 409 and res.json()["detail"]["code"] == "apm_host_map_auto_disabled"
    assert repo.applied == []


def test_preview_rate_limit_10_per_admin_per_minute() -> None:
    repo = _FakeRepo()
    seeded = repo.seed()
    client, _ = _app(repo=repo)
    codes = [
        client.post(f"{BASE}/{seeded['id']}/preview", headers=ADMIN_BG).status_code
        for _ in range(10)
    ]
    assert codes == [200] * 10
    res = client.post(f"{BASE}/{seeded['id']}/resolve", json={"apply": False}, headers=ADMIN_BG)
    assert res.status_code == 429
    assert res.json()["detail"]["code"] == "apm_host_map_rate_limited"
    assert res.json()["detail"]["retry_after_seconds"] >= 1
    # 다른 관리자는 따로 센다
    assert client.post(f"{BASE}/{seeded['id']}/preview", headers=ADMIN_USER).status_code == 200


def test_rate_limit_falls_back_in_process_when_redis_fails() -> None:
    repo = _FakeRepo()
    seeded = repo.seed()
    client, app = _app(repo=repo)
    app.state.apm_host_map_cache = SimpleNamespace(redis=SimpleNamespace(pipeline=None))
    codes = [
        client.post(f"{BASE}/{seeded['id']}/preview", headers=ADMIN_BG).status_code
        for _ in range(11)
    ]
    assert codes == [200] * 10 + [429]
    assert app.state.apm_host_map_rate


# ─── 스캔 ──────────────────────────────────────────────────────────────


def test_scan_202_then_409_running_with_audit() -> None:
    repo = _FakeRepo()
    repo.runs = [{"run_id": "run-0", "status": "ok"}]
    scan = _Scan()
    client, app = _app(repo=repo, scan=scan)
    res = client.post(f"{BASE}/scan", headers=ADMIN_USER)
    assert res.status_code == 202 and res.json() == {
        "run_id": "run-1",
        "status": "running",
        "audit_logged": True,
    }
    assert scan.calls == ["a"]
    audit = app.state.audit_service.actions()[-1]
    assert audit["action"] == "apm_host_map_scan_start" and audit["outcome"] == "ok"
    assert audit["before"] == {"run_id": "run-0", "status": "ok"}
    assert audit["after"] == {"run_id": "run-1", "status": "running"}
    res = client.post(f"{BASE}/scan", headers=ADMIN_BG)
    assert res.status_code == 409
    assert (
        res.json()["detail"]["code"] == "scan_running" and res.json()["detail"]["run_id"] == "run-1"
    )
    assert app.state.audit_service.actions()[-1]["reason"] == "scan_running"


def test_scan_refused_when_auto_off() -> None:
    client, app = _app(repo=_FakeRepo(), scan=None, auto=False)
    res = client.post(f"{BASE}/scan", headers=ADMIN_BG)
    assert res.status_code == 409 and res.json()["detail"]["code"] == "apm_host_map_auto_disabled"
    assert app.state.audit_service.actions()[-1]["outcome"] == "denied"
    runs = client.get(f"{BASE}/scan/runs", headers=ADMIN_BG).json()
    assert runs["scan_available"] is False and runs["auto_enabled"] is False


def test_scan_disabled_error_uses_fixed_code_not_exception_text() -> None:
    """L-2 — `ScanDisabledError` 문구를 코드·감사 사유로 쓰지 않는다(고정 코드)."""
    from noise_gate.application.apm_host_map_scan import ScanDisabledError

    class _Off(_Scan):
        async def start_scan(self, *, requested_by: str) -> str:
            raise ScanDisabledError("SECRET-REASON")

    client, app = _app(repo=_FakeRepo(), scan=_Off())
    res = client.post(f"{BASE}/scan", headers=ADMIN_BG)
    assert res.status_code == 409 and res.json()["detail"]["code"] == "apm_host_map_auto_disabled"
    assert "SECRET-REASON" not in res.text
    audit = app.state.audit_service.actions()[-1]
    assert (audit["outcome"], audit["reason"]) == ("denied", "apm_host_map_auto_disabled")


def test_scan_runs_list_and_detail() -> None:
    repo = _FakeRepo()
    repo.runs = [
        {
            "run_id": "run-2",
            "status": "partial",
            "scanners": {
                "dpm": {"status": "not_integrated"},
                "node_address": {
                    "status": "partial",
                    "sources": [
                        {"source_id": "jen1", "status": "failed", "reason": "login_failed"}
                    ],
                },
            },
        }
    ]
    client, _ = _app(repo=repo, scan=_Scan())
    assert client.get(f"{BASE}/scan/runs", headers=ADMIN_BG).json()["runs"][0]["run_id"] == "run-2"
    run = client.get(f"{BASE}/scan/runs/run-2", headers=ADMIN_BG).json()["run"]
    assert run["scanners"]["dpm"]["status"] == "not_integrated"
    assert client.get(f"{BASE}/scan/runs/nope", headers=ADMIN_BG).status_code == 404
    assert client.get(f"{BASE}/scan/runs/a%20b", headers=ADMIN_BG).status_code == 400


# ─── 인벤토리 ──────────────────────────────────────────────────────────


def test_inventory_unmapped_filter() -> None:
    repo = _FakeRepo()
    _snapshots(repo)
    repo.snapshots["apm_inventory"][1].append(
        {"source_id": "jen1", "domain_id": "1001", "instance_name": "was2", "ip": "10.0.0.8"}
    )
    repo.snapshots["sms_inventory"][1].append(
        {"db_id": "polestar_cm_gp", "server_name": "kbl-z", "hostname": "kbl-z"}
    )
    repo.seed(match_key="was1", targets=[{"db_id": "polestar_cm_gp", "server_name": "kbl-a"}])
    client, _ = _app(repo=repo)
    apm = client.get(f"{BASE}/inventory/apm", params={"unmapped": "true"}, headers=ADMIN_BG).json()
    assert [r["instance_name"] for r in apm["rows"]] == ["was2"] and apm["run_id"] == "run-0"
    sms = client.get(f"{BASE}/inventory/sms", params={"unmapped": "true"}, headers=ADMIN_BG).json()
    assert [r["server_name"] for r in sms["rows"]] == ["kbl-z"]
    every = client.get(f"{BASE}/inventory/sms", params={"limit": 1}, headers=ADMIN_BG).json()
    assert len(every["rows"]) == 1 and every["has_more"] is True
    itam = client.get(f"{BASE}/inventory/itam", headers=ADMIN_BG).json()
    assert itam == {
        "kind": "itam",
        "run_id": None,
        "rows": [],
        "has_more": False,
        "limit": 50,
        "offset": 0,
    }
    assert client.get(f"{BASE}/inventory/dpm", headers=ADMIN_BG).status_code == 422


# ─── 플래그 off = 라우트 0건 ───────────────────────────────────────────


@pytest.mark.parametrize("enabled", [False, True])
def test_create_app_registers_routes_only_when_enabled(enabled: bool) -> None:
    from src.api.server import create_app
    from src.config import AppConfig, AuthConfig, NoiseGateConfig

    app = create_app(
        AppConfig(
            db_backend="direct",
            db_connection_string="",
            log_level="WARNING",
            auth=AuthConfig(enabled=False),
            noise_gate=NoiseGateConfig(apm_host_map_enabled=enabled),
        )
    )
    paths = [p for p in app.openapi()["paths"] if p.startswith(BASE)]
    if not enabled:
        assert paths == []
        return
    assert set(paths) == {
        BASE,
        f"{BASE}/{{map_id}}",
        f"{BASE}/{{map_id}}/verify",
        f"{BASE}/{{map_id}}/preview",
        f"{BASE}/{{map_id}}/resolve",
        f"{BASE}/scan",
        f"{BASE}/scan/runs",
        f"{BASE}/scan/runs/{{run_id}}",
        f"{BASE}/inventory/{{kind}}",
    }
