"""plans/167 3차 교정 재확인 — 스킴 값 토큰 · Bearer lookahead · structlog 키 단위 격리."""

from __future__ import annotations

import time

import pytest

from src.utils import log_masking as lm
from src.utils.log_masking import mask_event_dict, mask_secrets


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("x-openapi-token: Bearer abc", "x-openapi-token: Bearer ***"),
        ("x-openapi-token:Bearer abc", "x-openapi-token:Bearer ***"),
        ("X-Api-Key: Bearer abc", "X-Api-Key: Bearer ***"),
        ("X-Auth-Token: token abc", "X-Auth-Token: token ***"),
        ("access_token=Bearer abc", "access_token=Bearer ***"),
        ("access_token = Bearer abc", "access_token = Bearer ***"),
        ("Token=Bearer abc", "Token=Bearer ***"),
        ("x-openapi-token: Basic abc==", "x-openapi-token: Basic ***"),
        ("secret=NTLM TlRMTVNT", "secret=NTLM ***"),
        ("token=Bearer\tabc", "token=Bearer\t***"),
        ("x-openapi-token: Bearer abc, other=1", "x-openapi-token: Bearer ***, other=1"),
        ("x-openapi-token: Bearer abc:def", "x-openapi-token: Bearer ***"),
        ("token=Bearer token=abc", "token=Bearer ***"),
        # 1차 순서 누출 3종
        ("token_type=bearer access_token=abc", "token_type=bearer access_token=***"),
        ("Bearer token=abc", "Bearer token=***"),
        ("auth bearer secret=abc", "auth bearer secret=***"),
        # Bearer lookahead
        ("Bearer abc123: rejected by upstream", "Bearer ***: rejected by upstream"),
        ("bearer abc: def", "bearer ***: def"),
        ("Bearer abc123:", "Bearer ***:"),
    ],
)
def test_scheme_and_bearer_masked(text: str, expected: str) -> None:
    masked = mask_secrets(text)
    assert masked == expected
    assert mask_secrets(masked) == masked  # 멱등


@pytest.mark.parametrize(
    "text",
    [
        "token: expired",
        "(using password: YES)",
        "max_tokens=512",
        "token_type=bearer",
        "url /auth/bearer abc",
    ],
)
def test_no_over_masking(text: str) -> None:
    assert mask_secrets(text) is text


@pytest.mark.parametrize(
    "text",
    [
        "token=bearer " * (262144 // 13),
        "x-a-token: Bearer " * (262144 // 18),
        "bearer " * (262144 // 7),
        "token=bearer" + "\t" * 262144 + "*",
        "Bearer a: " * (262144 // 10),
        "Bearer a" + "=" * 262144 + "*",
        "pwd=ntlm a," * 23000,
    ],
)
def test_adversarial_256kb_linear(text: str) -> None:
    started = time.perf_counter()
    mask_secrets(text)
    assert time.perf_counter() - started < 1.0


class _BadRepr:
    def __repr__(self) -> str:
        raise RuntimeError("boom")


class _SecretRepr:
    def __repr__(self) -> str:
        return "Obj(password=hunter2)"


def test_structlog_per_key_failure_isolated(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(lm, "_warned", False)
    bad = _BadRepr()
    event = {
        "event": "x",
        "bad": bad,
        "token": "abc",
        "msg": "password=hunter2",
        "o": _SecretRepr(),
    }
    out = mask_event_dict(None, "info", event)
    assert out["bad"] is bad
    assert out["token"] == "***"
    assert out["msg"] == "password=***"
    assert out["o"] == "Obj(password=***)"
    mask_event_dict(None, "info", {"bad": _BadRepr()})
    assert capsys.readouterr().err.count("log_masking") == 1


def test_structlog_cycle_terminates_and_keeps_caller_object() -> None:
    cyc: dict = {"password": "x"}
    cyc["self"] = cyc
    out = mask_event_dict(None, "info", {"d": cyc})
    assert cyc["password"] == "x"
    assert out["d"] is not cyc and out["d"]["password"] == "***"

    plain: dict = {"a": 1}
    plain["self"] = plain
    assert mask_event_dict(None, "info", {"d": plain})["d"] is plain


def test_structlog_json_renderer_no_leak() -> None:
    structlog = pytest.importorskip("structlog")
    cyc: dict = {"password": "hunter2"}
    cyc["self"] = cyc
    event = {
        "event": "login",
        "password": "hunter2",
        "headers": {"Authorization": "Bearer abc"},
        "url": "pg://u:pw@h/db",
        "obj": _SecretRepr(),
        "cyc": cyc,
    }
    masked = mask_event_dict(None, "info", event)
    rendered = structlog.processors.JSONRenderer()(None, "info", masked)
    assert "hunter2" not in rendered
    assert "Bearer abc" not in rendered
    assert "u:pw@" not in rendered
