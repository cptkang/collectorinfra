"""plans/166 W1 A5·A6 — 인가 판정 단일 술어 · 토큰 종류 검증 공통화 · SSE type 상수.

동작 불변 리팩터(D-337)라 기대값은 「치환 전 사본의 현재 값」이다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from fastapi import HTTPException

from src.api import sse_events
from src.api.dependencies import _verify_user_token, decode_typed_token, is_admin
from src.api.routes.admin_auth import verify_admin_token

SECRET = "s" * 32


def _token(claims: dict[str, Any], *, expired: bool = False) -> str:
    exp = datetime.now(UTC) + timedelta(hours=-1 if expired else 1)
    return jwt.encode({**claims, "exp": exp}, SECRET, algorithm="HS256")


# --- A5 is_admin: 치환 전 사본 두 형태와 결과가 같다 ---

_USERS: list[Any] = [
    None,
    {},
    {"sub": "u1"},
    {"role": None},
    {"role": "user"},
    {"role": "admin"},
    {"role": "ADMIN"},
    {"role": "admin", "sub": "u1", "break_glass": True},
]


@pytest.mark.parametrize("user", _USERS)
def test_is_admin_matches_alarm_copy(user: Any) -> None:
    """alarm.py 사본 `bool(user) and user.get("role") == "admin"`과 같다."""
    assert is_admin(user) is (bool(user) and user.get("role") == "admin")


@pytest.mark.parametrize("user", [u for u in _USERS if u is not None])
def test_is_admin_matches_dict_copy(user: dict) -> None:
    """dependencies·query·noise_dashboard 사본 `user.get("role") == "admin"`과 같다(dict 입력)."""
    assert is_admin(user) is (user.get("role") == "admin")


# --- A5 decode_typed_token: 문구·상태 코드 바이트 불변 ---


@pytest.mark.parametrize(
    ("expected_type", "claims", "detail"),
    [
        ("user", {"sub": "u", "type": "admin"}, "사용자 토큰이 아닙니다."),
        ("user", {"sub": "u"}, "사용자 토큰이 아닙니다."),
        ("admin", {"sub": "a", "type": "user"}, "관리자 토큰이 아닙니다."),
        ("admin", {"sub": "a"}, "관리자 토큰이 아닙니다."),
    ],
)
def test_type_mismatch_detail(expected_type: str, claims: dict, detail: str) -> None:
    with pytest.raises(HTTPException) as ei:
        decode_typed_token(_token(claims), SECRET, expected_type)
    assert (ei.value.status_code, ei.value.detail) == (401, detail)


@pytest.mark.parametrize("expected_type", ["user", "admin"])
def test_expired_and_bad_signature_detail(expected_type: str) -> None:
    claims = {"sub": "x", "type": expected_type}
    with pytest.raises(HTTPException) as ei:
        decode_typed_token(_token(claims, expired=True), SECRET, expected_type)
    assert (ei.value.status_code, ei.value.detail) == (401, "토큰이 만료되었습니다.")
    with pytest.raises(HTTPException) as ei:
        decode_typed_token(_token(claims), "t" * 32, expected_type)
    assert (ei.value.status_code, ei.value.detail) == (401, "유효하지 않은 토큰입니다.")


def test_sub_rules_stay_per_kind() -> None:
    """`sub` 기준 — 사용자·운영자 모두 falsy 거부(plans/184 S2 — 운영자도 빈 문자열 거부)."""
    with pytest.raises(HTTPException) as ei:
        _verify_user_token(_token({"sub": "", "type": "user"}), SECRET)
    assert ei.value.detail == "유효하지 않은 토큰입니다."
    with pytest.raises(HTTPException) as ei:
        verify_admin_token(_token({"sub": "", "type": "admin"}), SECRET)
    assert ei.value.detail == "유효하지 않은 토큰입니다."
    with pytest.raises(HTTPException) as ei:
        verify_admin_token(_token({"type": "admin"}), SECRET)
    assert ei.value.detail == "유효하지 않은 토큰입니다."


# --- A6 SSE type 상수: 와이어 값 불변 ---


def test_sse_type_values_are_wire_literals() -> None:
    assert {
        name: getattr(sse_events, name) for name in dir(sse_events) if name.startswith("SSE_")
    } == {
        "SSE_DONE": "done",
        "SSE_TOKEN": "token",
        "SSE_META": "meta",
        "SSE_ERROR": "error",
        "SSE_HEARTBEAT": "heartbeat",
        "SSE_PROGRESS": "progress",
        "SSE_NODE_START": "node_start",
        "SSE_NODE_COMPLETE": "node_complete",
    }


def test_thread_history_still_detects_done_chunk() -> None:
    from src.api.thread_history import _done_payload

    assert _done_payload('data: {"type": "done", "response": "x"}\n\n') == {
        "type": "done", "response": "x",
    }
    assert _done_payload('data: {"type": "token", "content": "done"}\n\n') is None
