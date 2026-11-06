"""plans/201 W1 — 담당 업무 지정(D-354 + 부기 1~4).

고정하는 계약:
  1. 어휘 정본 `config/duty_areas.yaml` 하나 — 대소문자 무시 매핑 · 다중 영역 ·
     부재·손상 = 빈 어휘 + 경고.
  2. 저장 값 검증 — 어휘 밖 422 · 중복 제거 · 선언 순서 · 빈 목록 = 미지정 ·
     폐지 코드는 읽을 때 무시 + 경고.
  3. 저장 — DDL 멱등(ALTER IF NOT EXISTS) · 저장소 위치 인자 왕복.
  4. 지정 경로 — 가입 선택 · 관리자 수정(감사 changes 전후) · **본인 변경 API 0건**.
  5. 플래그 off = 응답 JSON에 담당 키 부재 · 받아도 저장 안 함 · 라우트 미등록(404).
  6. 권한이 아니다 — 인가 모듈에 `duty_areas` 문자열 0건.
  7. 매핑 점검 — 최근 7일 판정 레코드만(`type` 레코드·창 밖 제외) · 미매핑 상위 5 + 건수 ·
     배지 건수.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.api import duty_area_support
from src.api.routes import duty_areas as duty_routes
from src.api.routes.admin import list_users, update_user
from src.api.schemas import UpdateUserRequest, UserInfoResponse, UserRegisterRequest
from src.domain.audit import AuditEvent
from src.domain.duty_areas import (
    DutyAreaVocabulary,
    UnknownDutyAreaError,
    parse_vocabulary,
    unmapped_resource_types,
)
from src.domain.user import User, UserRole, UserStatus
from src.infrastructure.duty_area_vocabulary import (
    DUTY_AREAS_FILE,
    load_duty_area_vocabulary,
    read_duty_area_vocabulary,
)
from src.infrastructure.user_repository import PostgresUserRepository
from src.security.audit_service import AuditService

_ROOT = Path(__file__).resolve().parents[1]
_STATIC = _ROOT / "src" / "static"

# 테스트 전용 어휘 — 정본 파일과 독립(정본 내용이 바뀌어도 계약 테스트가 흔들리지 않게).
_VOCAB_RAW = {
    "areas": [
        {"code": "alpha", "label": "알파",
         "resource_types": ["kind.One", "표시 이름", "kind.Shared"]},
        {"code": "beta", "label": "베타", "resource_types": ["kind.Two", "kind.Shared"]},
        {"code": "gamma", "label": "감마", "resource_types": []},
    ],
}


@pytest.fixture
def vocab(monkeypatch) -> DutyAreaVocabulary:
    """라우트 헬퍼가 테스트 어휘를 쓰게 한다."""
    v = parse_vocabulary(_VOCAB_RAW)
    monkeypatch.setattr(duty_area_support, "load_duty_area_vocabulary", lambda: v)
    return v


# ─── 1. 어휘 ─────────────────────────────────────────────────────────────────


def test_vocabulary_maps_case_insensitive_and_multi_area() -> None:
    v = parse_vocabulary(_VOCAB_RAW)
    assert v.codes() == ("alpha", "beta", "gamma")
    assert v.areas_for_resource_type("KIND.ONE") == ["alpha"]
    assert v.areas_for_resource_type("  표시 이름 ") == ["alpha"]
    assert v.areas_for_resource_type("kind.shared") == ["alpha", "beta"]   # 여러 영역 · 선언 순서
    assert v.areas_for_resource_type("kind.none") == []
    assert v.areas_for_resource_type("") == [] and v.areas_for_resource_type(None) == []
    assert v.options() == [
        {"code": "alpha", "label": "알파"}, {"code": "beta", "label": "베타"},
        {"code": "gamma", "label": "감마"},
    ]


def test_vocabulary_skips_bad_entries_with_warning(caplog) -> None:
    raw = {"areas": [{"code": "a", "label": "A"}, {"code": "a", "label": "dup"},
                     {"label": "no code"}, "x"],
           "cause_hints": {"k": "v"}}
    with caplog.at_level(logging.WARNING):
        v = parse_vocabulary(raw)
    assert v.codes() == ("a",)
    assert v.sections == {"cause_hints": {"k": "v"}}   # 뒤 Wave 섹션은 원문 그대로 보존
    assert len([r for r in caplog.records if "담당 업무 어휘" in r.getMessage()]) == 3


def test_canonical_file_loads_three_areas() -> None:
    """정본 파일 — W1 영역 3종과 대표 매핑(코드값·표시 이름 · 대소문자 무시)."""
    load_duty_area_vocabulary.cache_clear()
    v = load_duty_area_vocabulary()
    assert v.codes() == ("server_os", "was_mw", "db")
    assert v.areas_for_resource_type("server.cpus") == ["server_os"]
    assert v.areas_for_resource_type("개별 파일시스템") == ["server_os"]
    assert v.areas_for_resource_type("server.WebURLMonitor") == ["was_mw"]
    assert v.areas_for_resource_type("Mariadb.Memory") == ["db"]
    assert v.areas_for_resource_type("icmp.ICMPNode") == []          # 3영역 밖 = 미매핑
    assert DUTY_AREAS_FILE.name == "duty_areas.yaml"


def test_missing_file_is_empty_vocabulary_with_warning(tmp_path, caplog) -> None:
    with caplog.at_level(logging.WARNING):
        v = read_duty_area_vocabulary(tmp_path / "nope.yaml")
    assert v.codes() == ()
    assert any("어휘 파일 없음" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("content", ["areas: [unclosed", "- just\n- a list\n"])
def test_broken_file_is_empty_vocabulary_with_warning(tmp_path, caplog, content) -> None:
    path = tmp_path / "duty_areas.yaml"
    path.write_text(content, encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        v = read_duty_area_vocabulary(path)
    assert v.codes() == ()
    assert any("빈 어휘로 동작" in r.getMessage() for r in caplog.records)


# ─── 2. 검증·정규화 ──────────────────────────────────────────────────────────


def test_normalize_dedups_and_orders_by_declaration() -> None:
    v = parse_vocabulary(_VOCAB_RAW)
    assert v.normalize_codes(["gamma", "alpha", " gamma ", "alpha"]) == ["alpha", "gamma"]
    assert v.normalize_codes([]) is None          # 빈 목록 = 미지정
    assert v.normalize_codes(None) is None


def test_normalize_rejects_unknown_codes() -> None:
    v = parse_vocabulary(_VOCAB_RAW)
    with pytest.raises(UnknownDutyAreaError) as exc:
        v.normalize_codes(["alpha", "zeta", "zeta"])
    assert exc.value.unknown == ("zeta",)


def test_validate_raises_422(vocab) -> None:
    with pytest.raises(HTTPException) as exc:
        duty_area_support.validate_duty_areas(["alpha", "nope"])
    assert exc.value.status_code == 422
    assert "nope" in exc.value.detail


def test_retired_codes_are_ignored_with_warning(vocab, caplog) -> None:
    with caplog.at_level(logging.WARNING):
        got = duty_area_support.effective_duty_areas("u1", ["beta", "retired", "alpha"])
    assert got == ["alpha", "beta"]
    msgs = [r.getMessage() for r in caplog.records if "폐지 코드" in r.getMessage()]
    assert len(msgs) == 1 and "retired" in msgs[0]
    assert duty_area_support.effective_duty_areas("u1", ["retired"]) is None


# ─── 3. 저장 — DDL · 저장소 왕복 ───────────────────────────────────────────


def test_ddl_declares_column_everywhere() -> None:
    from src.api.server import _AUTH_DDL

    assert re.search(r"duty_areas\s+TEXT\[\]", _AUTH_DDL)
    for rel in ("ddl/auth_tables.sql", "db/init/03_auth_tables.sql"):
        assert re.search(r"duty_areas\s+TEXT\[\]", (_ROOT / rel).read_text(encoding="utf-8")), rel


class _Conn:
    def __init__(self, row: dict | None = None) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self.row = row

    async def execute(self, sql: str, *args) -> None:
        self.calls.append((sql, args))

    async def fetchrow(self, sql: str, *args):
        self.calls.append((sql, args))
        return self.row


class _Pool:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn

    def acquire(self):
        conn = self.conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


async def test_ensure_auth_tables_adds_column_idempotently() -> None:
    from src.api.server import _ensure_auth_tables

    conn = _Conn()
    await _ensure_auth_tables(_Pool(conn))
    sqls = [sql for sql, _ in conn.calls]
    assert "ALTER TABLE auth_users ADD COLUMN IF NOT EXISTS duty_areas TEXT[]" in sqls


def _user(**overrides) -> User:
    fields = dict(
        user_id="u1", username="이름", hashed_password="h", role=UserRole.USER,
        status=UserStatus.ACTIVE, department="IT", allowed_db_ids=["db_a"],
        allowed_sources=["apm"], alarm_zones=["z1"], is_protected=False,
        duty_areas=["alpha", "gamma"], login_fail_count=2,
    )
    fields.update(overrides)
    return User(**fields)


async def test_repository_insert_binds_duty_areas_at_its_column_position() -> None:
    conn = _Conn()
    user = _user()
    await PostgresUserRepository(_Pool(conn)).create(user)
    sql, args = conn.calls[0]
    cols = [c.strip() for c in re.search(r"\(([^)]*)\)\s*VALUES", sql, re.S).group(1).split(",")]
    placeholders = re.findall(r"\$(\d+)", sql.split("VALUES", 1)[1])
    assert len(cols) == len(placeholders) == len(args)
    bound = {col: args[int(n) - 1] for col, n in zip(cols, placeholders)}
    assert bound["duty_areas"] == ["alpha", "gamma"]
    # 기존 열도 자기 자리 그대로(위치 인자 밀림 방지)
    assert bound["allowed_sources"] == ["apm"]
    assert bound["alarm_zones"] == ["z1"]
    assert bound["auth_method"] == "local"


async def test_repository_update_binds_duty_areas_at_its_column_position() -> None:
    conn = _Conn()
    await PostgresUserRepository(_Pool(conn)).update(_user())
    sql, args = conn.calls[0]
    pairs = dict(re.findall(r"(\w+)\s*=\s*\$(\d+)", sql))
    assert len(set(pairs.values())) == len(args)
    bound = {col: args[int(n) - 1] for col, n in pairs.items()}
    assert bound["duty_areas"] == ["alpha", "gamma"]
    assert bound["user_id"] == "u1"
    assert bound["allowed_sources"] == ["apm"]
    assert bound["alarm_zones"] == ["z1"]
    assert bound["login_fail_count"] == 2


async def test_repository_row_mapping_reads_duty_areas() -> None:
    row = {
        "user_id": "u1", "username": "n", "hashed_password": "h", "role": "user",
        "status": "active", "department": None, "allowed_db_ids": None,
        "allowed_sources": None, "alarm_zones": None, "is_protected": False,
        "duty_areas": ["beta"], "auth_method": "local", "login_fail_count": 0,
        "last_login_at": None, "created_at": None, "updated_at": None,
    }
    user = await PostgresUserRepository(_Pool(_Conn(row))).get_by_user_id("u1")
    assert user.duty_areas == ["beta"]
    legacy = {k: v for k, v in row.items() if k != "duty_areas"}   # ALTER 전 행
    legacy_user = await PostgresUserRepository(_Pool(_Conn(legacy))).get_by_user_id("u1")
    assert legacy_user.duty_areas is None


# ─── 4. 지정 경로 ────────────────────────────────────────────────────────────


class _AuditRepo:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def log_event(self, event: dict) -> None:
        self.events.append(event)


def _request(*, enabled: bool, user_repo=None) -> tuple[SimpleNamespace, _AuditRepo]:
    captured = _AuditRepo()
    audit_cfg = SimpleNamespace(jsonl_enabled=False, db_enabled=True,
                                alert_on_failed_login=5, alert_on_large_result=5000)
    config = SimpleNamespace(
        auth=SimpleNamespace(password_min_length=8, default_allowed_db_ids="",
                             default_allowed_sources="", enabled=True),
        audit=SimpleNamespace(retention_days=90),
    )
    state = SimpleNamespace(user_repo=user_repo, audit_service=AuditService(audit_cfg, captured),
                            config=config, duty_area_enabled=enabled)
    req = SimpleNamespace(
        app=SimpleNamespace(state=state),
        state=SimpleNamespace(client_ip="10.0.0.9", request_id="rid"),
        client=SimpleNamespace(host="10.0.0.9"),
        headers={},
    )
    return req, captured


def _repo(target: User | None = None) -> SimpleNamespace:
    created: dict = {}
    return SimpleNamespace(
        created=created,
        exists=AsyncMock(return_value=False),
        create=AsyncMock(side_effect=lambda u: created.setdefault("user", u)),
        get_by_user_id=AsyncMock(return_value=target),
        update=AsyncMock(),
        list_all=AsyncMock(return_value=[target] if target else []),
    )


def _register_body(**extra) -> UserRegisterRequest:
    return UserRegisterRequest(
        user_id="new_user", username="새 사용자", password="password123", **extra
    )


def _json(model) -> dict:
    return json.loads(model.model_dump_json())


async def test_register_on_stores_normalized_and_audits(vocab) -> None:
    from src.api.routes.user_auth import register

    repo = _repo()
    req, captured = _request(enabled=True, user_repo=repo)
    resp = await register(req, _register_body(duty_areas=["beta", "alpha", "beta"]))
    assert repo.created["user"].duty_areas == ["alpha", "beta"]
    assert _json(resp)["duty_areas"] == ["alpha", "beta"]
    detail = captured.events[0]["detail"]["extra"]
    assert captured.events[0]["event_type"] == AuditEvent.REGISTER.value
    assert detail["duty_areas"] == ["alpha", "beta"]


async def test_register_on_without_choice_is_unassigned(vocab) -> None:
    from src.api.routes.user_auth import register

    repo = _repo()
    req, _ = _request(enabled=True, user_repo=repo)
    await register(req, _register_body(duty_areas=[]))
    assert repo.created["user"].duty_areas is None


async def test_register_on_unknown_code_is_422_and_not_created(vocab) -> None:
    from src.api.routes.user_auth import register

    repo = _repo()
    req, _ = _request(enabled=True, user_repo=repo)
    with pytest.raises(HTTPException) as exc:
        await register(req, _register_body(duty_areas=["zeta"]))
    assert exc.value.status_code == 422
    repo.create.assert_not_called()


async def test_register_off_ignores_value_and_keeps_response_shape(vocab) -> None:
    from src.api.routes.user_auth import register

    repo = _repo()
    req, captured = _request(enabled=False, user_repo=repo)
    resp = await register(req, _register_body(duty_areas=["zeta"]))   # off면 검증도 저장도 없다
    assert repo.created["user"].duty_areas is None
    body = _json(resp)
    assert "duty_areas" not in body and "duty_area_options" not in body
    assert "duty_areas" not in captured.events[0]["detail"]["extra"]


async def test_admin_update_records_duty_before_after(vocab) -> None:
    target = _user(duty_areas=None)
    req, captured = _request(enabled=True, user_repo=_repo(target))
    body = UpdateUserRequest(duty_areas=["gamma", "alpha"])
    resp = await update_user(req, "u1", body, {"sub": "root"})
    assert target.duty_areas == ["alpha", "gamma"]
    extra = captured.events[0]["detail"]["extra"]
    assert captured.events[0]["user_id"] == "root"            # 누가
    assert extra["action"] == "user_update"
    assert extra["target_user_id"] == "u1"                    # 누구의
    assert extra["changes"] == {"duty_areas": {"before": None, "after": ["alpha", "gamma"]}}
    assert _json(resp)["duty_areas"] == ["alpha", "gamma"]


async def test_admin_update_clear_and_protected_account_allowed(vocab) -> None:
    target = _user(is_protected=True, duty_areas=["alpha"])
    req, captured = _request(enabled=True, user_repo=_repo(target))
    await update_user(req, "u1", UpdateUserRequest(duty_areas=[]), {"sub": "root"})
    assert target.duty_areas is None                          # 빈 목록 = 미지정
    assert captured.events[0]["detail"]["extra"]["changes"] == {
        "duty_areas": {"before": ["alpha"], "after": None}
    }


async def test_admin_update_unknown_code_is_422_and_not_saved(vocab) -> None:
    repo = _repo(_user())
    req, captured = _request(enabled=True, user_repo=repo)
    with pytest.raises(HTTPException) as exc:
        await update_user(req, "u1", UpdateUserRequest(duty_areas=["zeta"]), {"sub": "root"})
    assert exc.value.status_code == 422
    repo.update.assert_not_called()
    assert captured.events == []


async def test_admin_update_off_ignores_value_and_shape(vocab) -> None:
    target = _user(duty_areas=["alpha"])
    repo = _repo(target)
    req, captured = _request(enabled=False, user_repo=repo)
    resp = await update_user(req, "u1", UpdateUserRequest(duty_areas=["beta"]), {"sub": "root"})
    assert target.duty_areas == ["alpha"]
    assert captured.events[0]["detail"]["extra"]["changes"] == {}
    assert "duty_areas" not in _json(resp)


async def test_list_users_on_off(vocab) -> None:
    target = _user(duty_areas=["beta", "retired"])
    req_on, _ = _request(enabled=True, user_repo=_repo(target))
    on = [_json(m) for m in await list_users(req_on, {"sub": "root"})]
    assert on[0]["duty_areas"] == ["beta"]                     # 폐지 코드는 읽을 때 무시
    req_off, _ = _request(enabled=False, user_repo=_repo(target))
    off = [_json(m) for m in await list_users(req_off, {"sub": "root"})]
    assert "duty_areas" not in off[0]
    assert list(off[0]) == [k for k in on[0] if k != "duty_areas"]   # 나머지 키·순서 동일


# ─── 5. /auth/me · 응답 키 ───────────────────────────────────────────────────


async def test_auth_me_on_reads_repository_and_returns_options(vocab) -> None:
    from src.api.routes.user_auth import get_me

    repo = _repo(_user(duty_areas=["gamma"]))
    req, _ = _request(enabled=True, user_repo=repo)
    me = _json(await get_me(req, {"sub": "u1", "name": "n", "role": "user"}))
    assert me["duty_areas"] == ["gamma"]
    assert me["duty_area_options"] == vocab.options()
    repo.get_by_user_id.assert_awaited_once_with("u1")


async def test_auth_me_on_unassigned_keeps_keys(vocab) -> None:
    from src.api.routes.user_auth import get_me

    req, _ = _request(enabled=True, user_repo=_repo(_user(duty_areas=None)))
    me = _json(await get_me(req, {"sub": "u1", "name": "n", "role": "user"}))
    assert me["duty_areas"] is None and "duty_area_options" in me


async def test_auth_me_on_repository_failure_is_unassigned(vocab, caplog) -> None:
    """저장소 조회 예외는 500이 아니라 미지정 + 경고 1줄."""
    from src.api.routes.user_auth import get_me

    repo = _repo()
    repo.get_by_user_id = AsyncMock(side_effect=RuntimeError("db down"))
    req, _ = _request(enabled=True, user_repo=repo)
    with caplog.at_level(logging.WARNING):
        me = _json(await get_me(req, {"sub": "u1", "name": "n", "role": "user"}))
    assert me["duty_areas"] is None and me["duty_area_options"] == vocab.options()
    assert len([r for r in caplog.records if "담당 업무 조회 실패" in r.getMessage()]) == 1


@pytest.mark.parametrize(("values", "needle"), [
    ([f"x{i}" for i in range(33)], "최대 32개"),
    (["a" * 65], "64자 이하"),
])
def test_validate_caps_length(vocab, values, needle) -> None:
    with pytest.raises(HTTPException) as exc:
        duty_area_support.validate_duty_areas(values)
    assert exc.value.status_code == 422 and needle in exc.value.detail
    assert len(exc.value.detail) < 120


def test_validate_unknown_detail_is_truncated(vocab) -> None:
    with pytest.raises(HTTPException) as exc:
        duty_area_support.validate_duty_areas([f"x{i}" for i in range(20)])
    assert "x0, x1, x2, x3, x4 외 15개(총 20개)" in exc.value.detail
    assert "x5" not in exc.value.detail


async def test_auth_me_off_has_no_duty_keys_and_no_lookup(vocab) -> None:
    from src.api.routes.user_auth import get_me

    repo = _repo(_user())
    req, _ = _request(enabled=False, user_repo=repo)
    me = _json(await get_me(req, {"sub": "u1", "name": "n", "role": "user"}))
    assert "duty_areas" not in me and "duty_area_options" not in me
    repo.get_by_user_id.assert_not_called()


def test_user_info_response_default_shape_unchanged() -> None:
    """명시로 채우지 않으면 응답 키 집합이 종전과 같다(로그인·상태 응답 포함)."""
    body = _json(UserInfoResponse(user_id="u", username="n", role="user"))
    assert list(body) == ["user_id", "username", "role", "department", "allowed_db_ids",
                          "alarm_zones", "is_protected", "status", "last_login_at"]


def test_auth_dict_does_not_carry_duty_areas() -> None:
    """인가 dict(`to_auth_dict`)에는 담당 업무를 싣지 않는다 — 권한이 아니다."""
    assert "duty_areas" not in _user().to_auth_dict()


# ─── 4'. 본인 변경 API 0건 ──────────────────────────────────────────────────


def test_no_self_service_duty_change_endpoint() -> None:
    """`/auth/*`의 PUT·PATCH 중 담당 업무를 받는 경로가 없다(D-354 부기 4 ①)."""
    from src.api.routes import user_auth

    app = FastAPI()
    app.include_router(user_auth.router, prefix="/api/v1")
    app.include_router(duty_routes.router, prefix="/api/v1")
    offenders = []
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        if "/auth/" not in path or not (methods & {"PUT", "PATCH"}):
            continue
        for param in route.dependant.body_params:
            fields = getattr(param.field_info.annotation, "model_fields", {}) or {}
            if "duty_areas" in fields or param.name == "duty_areas":
                offenders.append(path)
    assert offenders == []
    # 담당 업무 라우터의 `/auth/*`는 GET(메타 읽기)뿐이다
    for route in duty_routes.router.routes:
        if "/auth/" in route.path:
            assert route.methods == {"GET"}


# ─── 6. 권한 비참조 ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("rel", [
    "src/routing/db_authz.py",
    "src/routing/zones.py",
    "src/domain/host_authz.py",
    "src/domain/auth.py",
    "src/api/dependencies.py",
    "src/orchestration/db_access.py",
])
def test_authz_modules_never_read_duty_areas(rel) -> None:
    text = (_ROOT / rel).read_text(encoding="utf-8")
    assert "duty_area" not in text, (
        f"{rel}가 담당 업무를 참조한다 — 담당 업무는 권한이 아니다(D-354)"
    )


# ─── 7. 매핑 점검 · 관리자 현황 · 라우트 등록 ───────────────────────────────


def _write_decisions(path: Path) -> None:
    now = datetime.now(UTC)
    recent = (now - timedelta(days=1)).isoformat()
    old = (now - timedelta(days=8)).isoformat()
    rows = (
        [{"ts": recent, "resource_type": "kind.One"}] * 3              # 매핑됨
        + [{"ts": recent, "resource_type": "kind.Unknown"}] * 4
        + [{"ts": recent, "resource_type": "KIND.unknown"}]             # 다른 철자 = 별개 집계
        + [{"ts": recent, "resource_type": f"u.{i}"} for i in range(6)]  # 1건짜리 6종
        + [{"ts": recent, "resource_type": ""}, {"ts": recent}]          # 빈 유형 → 별도 수
        + [{"ts": old, "resource_type": "kind.Old"}]                    # 7일 창 밖
        + [{"ts": recent, "type": "recurrence", "resource_type": "kind.Typed"},
           {"ts": recent, "type": "annotation", "resource_type": "kind.Typed"}]   # type 레코드 제외
    )
    lines = [json.dumps(r, ensure_ascii=False) for r in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_unmapped_resource_types_pure() -> None:
    v = parse_vocabulary(_VOCAB_RAW)
    got = unmapped_resource_types(v, {"kind.One": 1, "x": 2, "y": 1}, empty_count=1, top_n=1)
    assert got == {"checked": 4, "empty_count": 1, "unmapped_total": 3, "unmapped_distinct": 2,
                   "top": [{"resource_type": "x", "count": 2}]}


def test_decision_store_counts_values_streaming(tmp_path) -> None:
    """레코드 목록을 쌓지 않고 값별 건수만 센다 — type 레코드·창 밖 제외 · 빈 값 별도."""
    from noise_gate.infrastructure.decision_store import DecisionStore

    path = tmp_path / "d.jsonl"
    _write_decisions(path)
    store = DecisionStore(str(path), True, 10)
    counts, empty = store.count_recent_values(7 * 86400, "resource_type")
    assert counts["kind.Unknown"] == 4 and counts["KIND.unknown"] == 1
    assert "kind.Old" not in counts and "kind.Typed" not in counts
    assert empty == 2
    missing = DecisionStore(str(tmp_path / "none.jsonl"), True, 10)
    assert missing.count_recent_values(60, "x") == ({}, 0)


def test_status_scan_runs_in_thread(tmp_path, vocab) -> None:
    """매핑 점검의 동기 파일 스캔은 `asyncio.to_thread`로 돈다(이벤트 루프 정지 방지)."""
    import asyncio as _asyncio

    calls: list = []
    real = _asyncio.to_thread

    async def _spy(func, *args, **kwargs):
        calls.append(getattr(func, "__name__", ""))
        return await real(func, *args, **kwargs)

    with patch.object(duty_routes.asyncio, "to_thread", _spy):
        TestClient(_status_app(tmp_path, users=[])).get("/api/v1/admin/duty-areas/status")
    assert calls == ["count_recent_values_many"]


def test_status_mapping_false_skips_scan(tmp_path, vocab) -> None:
    app = _status_app(tmp_path, users=[_user(duty_areas=["alpha"])])
    with patch(
        "noise_gate.infrastructure.decision_store.DecisionStore.count_recent_values_many",
        side_effect=AssertionError("매핑 점검을 다시 계산하면 안 된다"),
    ):
        body = TestClient(app).get("/api/v1/admin/duty-areas/status?mapping=false").json()
    assert body["sections"] is None and body["badge_count"] is None
    assert body["assignment"]["assigned_users"] == 1


def _status_app(tmp_path: Path, *, users: list[User], auth_enabled: bool = False) -> FastAPI:
    store = tmp_path / "decisions.jsonl"
    _write_decisions(store)
    app = FastAPI()
    app.include_router(duty_routes.router, prefix="/api/v1")
    app.state.config = SimpleNamespace(
        auth=SimpleNamespace(enabled=auth_enabled, jwt_secret="s" * 32),
        admin=SimpleNamespace(jwt_secret="a" * 32),
        noise_gate=SimpleNamespace(decision_store_path=str(store), decision_store_enabled=True,
                                   decision_store_max_lines=20000),
    )
    app.state.user_repo = SimpleNamespace(list_all=AsyncMock(return_value=users),
                                          get_by_user_id=AsyncMock(return_value=None))
    app.state.duty_area_enabled = True
    return app


def test_status_counts_unmapped_types_and_assignment(tmp_path, vocab) -> None:
    users = [
        _user(user_id="a", duty_areas=["alpha", "beta"]),
        _user(user_id="b", duty_areas=["beta"]),
        _user(user_id="c", duty_areas=None),
        _user(user_id="d", duty_areas=["retired"]),
    ]
    client = TestClient(_status_app(tmp_path, users=users))
    body = client.get("/api/v1/admin/duty-areas/status").json()

    assert body["options"] == vocab.options()
    assert body["window_days"] == 7
    assert body["assignment"] == {
        "total_users": 4, "assigned_users": 2, "unassigned_users": 2,   # 폐지 코드뿐 = 미지정
        "by_area": [{"code": "alpha", "label": "알파", "users": 1},
                    {"code": "beta", "label": "베타", "users": 2},
                    {"code": "gamma", "label": "감마", "users": 0}],
    }
    (sec,) = body["sections"]
    assert sec["key"] == "resource_type"
    assert sec["action_hint"] == "config/duty_areas.yaml의 resource_types에 추가하세요"
    assert sec["empty_count"] == 2
    assert sec["checked"] == 3 + 4 + 1 + 6               # 창 밖·type 레코드·빈 유형 제외
    assert sec["unmapped_total"] == 11
    assert sec["unmapped_distinct"] == 8
    assert [t["resource_type"] for t in sec["top"]] == [
        "kind.Unknown", "KIND.unknown", "u.0", "u.1", "u.2",
    ]
    assert sec["top"][0]["count"] == 4
    assert len(sec["top"]) == 5
    assert body["badge_count"] == 8
    names = {t["resource_type"] for t in sec["top"]}
    assert "kind.Old" not in names and "kind.Typed" not in names


def test_status_requires_admin(tmp_path, vocab) -> None:
    client = TestClient(_status_app(tmp_path, users=[], auth_enabled=True))
    assert client.get("/api/v1/admin/duty-areas/status").status_code == 401
    route = next(r for r in duty_routes.router.routes if r.path == "/admin/duty-areas/status")
    from src.api.dependencies import require_admin_user

    assert any(d.call is require_admin_user for d in route.dependant.dependencies)


def test_status_rejects_non_admin_user(tmp_path, vocab) -> None:
    app = _status_app(tmp_path, users=[], auth_enabled=True)
    plain = AsyncMock(return_value={"sub": "u1", "role": "user"})
    with patch("src.api.dependencies._try_break_glass_admin", return_value=None), \
         patch("src.api.dependencies.require_user", plain):
        resp = TestClient(app).get("/api/v1/admin/duty-areas/status",
                                   headers={"Authorization": "Bearer x"})
    assert resp.status_code == 403


def test_public_options_endpoint(tmp_path, vocab) -> None:
    client = TestClient(_status_app(tmp_path, users=[]))
    assert client.get("/api/v1/auth/duty-areas").json() == {"options": vocab.options()}


@pytest.mark.parametrize("enabled", [True, False])
def test_router_registered_only_when_enabled(enabled) -> None:
    from src.api.server import create_app
    from src.config import AppConfig, DutyAreaConfig

    app = create_app(AppConfig(duty_area=DutyAreaConfig(enabled=enabled)))
    # 등록 여부는 실제 요청으로 본다 — FastAPI 0.14x는 `app.routes`에 포함 라우터를 펼치지 않는다
    # (`_IncludedRouter`). 등록돼 있으면 인증·처리 결과(401·200 등), 없으면 404다.
    client = TestClient(app, raise_server_exceptions=False)
    for path in ("/api/v1/admin/duty-areas/status", "/api/v1/auth/duty-areas"):
        assert (client.get(path).status_code != 404) is enabled, path
    assert app.state.duty_area_enabled is enabled


def test_flag_default_off_and_declared() -> None:
    from src.api.settings_catalog import GROUP_ORDER, field_index
    from src.config import DutyAreaConfig

    assert DutyAreaConfig.model_fields["enabled"].default is False
    assert field_index()["DUTY_AREA_ENABLED"].group_key == "duty_area"
    assert "duty_area" in GROUP_ORDER
    example = (_ROOT / ".env.example").read_text(encoding="utf-8")
    assert len(re.findall(r"^#?\s*DUTY_AREA_ENABLED=false$", example, re.M)) == 1


# ─── 정적 자산 ───────────────────────────────────────────────────────────────


def test_static_cache_versions_bumped() -> None:
    assert 'src="/static/js/app.js?v=56"' in (_STATIC / "index.html").read_text(encoding="utf-8")
    dashboard = (_STATIC / "admin" / "dashboard.html").read_text(encoding="utf-8")
    assert 'src="/static/js/admin.js?v=19"' in dashboard


def test_app_js_notice_is_gated_on_server_key() -> None:
    js = (_STATIC / "js" / "app.js").read_text(encoding="utf-8")
    assert ("담당 업무를 지정하면 예시 질문·알람·조사 결과가 담당 영역 중심으로 정리됩니다. " in js
            and "담당 업무는 관리자가 지정합니다 — 관리자에게 요청하세요." in js)
    assert 'hasOwnProperty.call(me, "duty_area_options")' in js     # 키 부재 = off → 안 그림
    assert "duty_area_notice_dismissed:" in js                       # 닫기 기억
    assert "지정하기" not in js                                       # 본인 지정 버튼 없음


def test_admin_js_gated_on_status_endpoint() -> None:
    js = (_STATIC / "js" / "admin.js").read_text(encoding="utf-8")
    assert '"/api/v1/admin/duty-areas/status"' in js
    assert "dutyState = response.ok ? await response.json() : null;" in js
    assert "if (dutyState) ensureDutyHeader();" in js
    assert "if (dutyState) decorateDutyRow(tr, u, permTd);" in js
    assert "미지정만 보기" in js and "일괄 지정" in js
    # 일괄 지정은 새 API 없이 사용자별 PUT 반복
    assert ('"PUT", "/api/v1/admin/users/" + encodeURIComponent(targets[k]), '
            '{duty_areas: picked}') in js
    html = (_STATIC / "admin" / "dashboard.html").read_text(encoding="utf-8")
    assert '<div id="dutyAreaStatusCard" hidden' in html


def test_register_html_reads_server_meta() -> None:
    html = (_STATIC / "register.html").read_text(encoding="utf-8")
    assert 'fetch("/api/v1/auth/duty-areas")' in html
    assert 'id="dutyAreaGroup" hidden' in html
    assert "if (dutyEnabled)" in html


def test_static_assets_have_no_area_code_copies() -> None:
    """영역 코드·라벨은 서버 메타로만 온다 — 프론트에 사본이 없다."""
    load_duty_area_vocabulary.cache_clear()
    canonical = load_duty_area_vocabulary()
    # 짧은 일반어 코드(예: 두 글자)는 무관한 문자열과 겹치므로 밑줄 있는 코드만 본다
    codes = [c for c in canonical.codes() if "_" in c]
    assert codes
    for rel in ("js/app.js", "js/admin.js", "register.html", "admin/dashboard.html"):
        text = (_STATIC / rel).read_text(encoding="utf-8")
        for code in codes:
            assert f'"{code}"' not in text and f"'{code}'" not in text, (rel, code)
