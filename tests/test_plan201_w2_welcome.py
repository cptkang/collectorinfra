"""plans/201 W2 — 첫 화면 담당별 예시 질의 · 상황 안내: 문구 정본 · 로더 검증 · 선별 상한 · `/auth/me` · 설정.

  1. 로더(`parse_welcome_queries`) — `verified_by` 없음·빈 값·규약 밖·`pending` · 어휘 밖 영역 · label/query 빈 값은
     제외 + 경고(문구 원문은 로그에 싣지 않는다).
  2. 선별(`select_welcome_queries`) — 어휘 선언 순서 · 영역당 3 · 전체 6.
  3. 정본 `config/duty_areas.yaml` `welcome_queries` — 초안 전부가 남아 있고(로드 또는 검증 대기), `gold:` 참조는
     실제 골드셋 항목으로, `assembler:` 이름은 코드의 함수로 해석된다(로더는 testdata를 읽지 않는다 — 테스트가 본다).
  4. `/auth/me` — 플래그 off면 응답 키 집합 그대로 · on이면 `duty_welcome_queries`·`welcome_guide` shape(실 라우트).
  5. 설정 — env 이름 하나씩 · 실효 AND · 카탈로그 · 도움말 · `.env.example` · 기동 시 1회 해석.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jwt
import pytest
import yaml
from fastapi.testclient import TestClient

from src.api import duty_area_support
from src.domain.duty_areas import is_valid_verified_by, parse_vocabulary, parse_welcome_queries
from src.domain.user import User, UserRole, UserStatus
from src.infrastructure.duty_area_vocabulary import read_duty_area_vocabulary

_ROOT = Path(__file__).resolve().parents[1]
_USER_SECRET = "u" * 40
_ADMIN_SECRET = "a" * 40
_KNOWN = ("alpha", "beta", "gamma")


def _q(label: str, query: str | None = None, verified_by: object = "gold:gp/gp-001", **kw) -> dict:
    item = {"label": label, "query": query if query is not None else f"{label} 질의"}
    if verified_by is not None:
        item["verified_by"] = verified_by
    item.update(kw)
    return item


# ─── 1. 로더 ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", [
    "gold:gp/gp-015", "gold:scenarios/APM-HEALTH-untargeted", "gold:syn/syn-e01",
    "assembler:try_deterministic_alarm_sql", "sandbox:polestar", "mlx:local", "closed_net:2026-01-15",
    "  gold:gp/gp-015  ",
])
def test_verified_by_convention_accepts(value) -> None:
    assert is_valid_verified_by(value) is True


@pytest.mark.parametrize("value", [
    None, "", "   ", 3, ["gold:gp/gp-001"], "gold:gp", "gold:/gp-001", "golden:gp/gp-001", "assembler:",
    "assembler:1bad", "sandbox:", "mlx:remote", "closed_net:2026-13-01", "closed_net:2026-02-30",
    "closed_net:yesterday", "manual", "gold:gp/gp-001 extra",
])
def test_verified_by_convention_rejects(value) -> None:
    assert is_valid_verified_by(value) is False


def test_verified_by_closed_net_not_after_reference_day() -> None:
    """`closed_net:` 확인일은 기준일보다 뒤일 수 없다 — 기준일 당일은 받는다(순수 · 기준일 주입)."""
    ref = date(2026, 10, 30)
    assert is_valid_verified_by("closed_net:2026-10-30", today=ref) is True
    assert is_valid_verified_by("closed_net:2026-10-31", today=ref) is False


def test_parse_excludes_future_closed_net(caplog) -> None:
    raw = {"server_os": [{"label": "미래 확인", "query": "ㅇㅇ존 질의", "verified_by": "closed_net:2026-11-01"},
                         {"label": "당일 확인", "query": "ㅇㅇ존 다른 질의", "verified_by": "closed_net:2026-10-30"}]}
    with caplog.at_level(logging.WARNING):
        out = parse_welcome_queries(raw, ("server_os",), today=date(2026, 10, 30))
    assert [q.label for q in out["server_os"]] == ["당일 확인"]
    assert "미래 확인" in caplog.text and "ㅇㅇ존 질의" not in caplog.text


def test_parse_excludes_unverified_and_warns_without_query_text(caplog) -> None:
    raw = {
        "alpha": [
            _q("통과"),
            _q("근거 없음", "비밀 질의 원문 A", verified_by=None),
            _q("빈 근거", "비밀 질의 원문 B", verified_by="  "),
            _q("규약 밖", "비밀 질의 원문 C", verified_by="손으로 확인함"),
            _q("대기", "비밀 질의 원문 D", verified_by=None, pending="closed_net"),
            _q("대기 근거 둘 다", "비밀 질의 원문 E", pending="closed_net"),
            {"label": "", "query": "비밀 질의 원문 F", "verified_by": "mlx:local"},
            {"label": "질의 없음", "query": "  ", "verified_by": "mlx:local"},
            "문자열 항목",
            _q("중복", "통과 질의"),
        ],
        "outsider": [_q("어휘 밖")],
        "beta": "목록 아님",
    }
    with caplog.at_level(logging.WARNING, logger="src.domain.duty_areas"):
        out = parse_welcome_queries(raw, _KNOWN)
    assert list(out) == ["alpha"]
    assert [(q.label, q.query, q.verified_by) for q in out["alpha"]] == [("통과", "통과 질의", "gold:gp/gp-001")]
    text = caplog.text
    assert "비밀 질의 원문" not in text                      # 문구 원문은 로그에 없다(label만)
    for label in ("근거 없음", "빈 근거", "규약 밖", "질의 없음", "중복"):
        assert label in text
    assert "outsider" in text and "목록이 아님" in text
    pending_lines = [r for r in caplog.records if "검증 대기" in r.getMessage()]
    assert len(pending_lines) == 1                          # 검증 대기는 모아서 1줄
    assert "alpha:대기" in pending_lines[0].getMessage() and "alpha:대기 근거 둘 다" in pending_lines[0].getMessage()


def test_parse_non_mapping_and_absent() -> None:
    assert parse_welcome_queries(None, _KNOWN) == {}
    assert parse_welcome_queries(["x"], _KNOWN) == {}


def test_parse_keeps_vocabulary_area_order() -> None:
    out = parse_welcome_queries({"gamma": [_q("감")], "alpha": [_q("가")]}, _KNOWN)
    assert list(out) == ["alpha", "gamma"]


# ─── 2. 선별 ─────────────────────────────────────────────────────────────────


def _vocab_with(per_area: int = 4):
    return parse_vocabulary({
        "areas": [{"code": c, "label": c.upper()} for c in _KNOWN],
        "welcome_queries": {c: [_q(f"{c}{i}") for i in range(1, per_area + 1)] for c in _KNOWN},
    })


def test_select_caps_per_area_and_total_in_declared_order() -> None:
    vocab = _vocab_with()
    picked = vocab.select_welcome_queries(["gamma", "beta", "alpha"])     # 입력 순서 무관 — 선언 순서
    assert [p["label"] for p in picked] == ["alpha1", "alpha2", "alpha3", "beta1", "beta2", "beta3"]
    assert picked[0] == {"area": "alpha", "area_label": "ALPHA", "label": "alpha1", "query": "alpha1 질의"}
    assert [p["label"] for p in vocab.select_welcome_queries(["gamma"])] == ["gamma1", "gamma2", "gamma3"]
    assert vocab.select_welcome_queries(["beta", "beta", "nope"]) == vocab.select_welcome_queries(["beta"])
    assert vocab.select_welcome_queries(None) == [] and vocab.select_welcome_queries([]) == []


def test_select_fewer_than_cap_and_area_without_queries() -> None:
    vocab = parse_vocabulary({
        "areas": [{"code": c, "label": c} for c in _KNOWN],
        "welcome_queries": {"alpha": [_q("a1")], "gamma": [_q(f"g{i}") for i in range(5)]},
    })
    assert [p["label"] for p in vocab.select_welcome_queries(["alpha", "beta", "gamma"])] == [
        "a1", "g0", "g1", "g2"]
    assert vocab.select_welcome_queries(["beta"]) == []


def test_support_helper_uses_plan_caps(monkeypatch) -> None:
    monkeypatch.setattr(duty_area_support, "load_duty_area_vocabulary", lambda: _vocab_with(5))
    assert duty_area_support.WELCOME_QUERIES_PER_AREA == 3 and duty_area_support.WELCOME_QUERIES_TOTAL == 6
    assert len(duty_area_support.duty_welcome_queries(list(_KNOWN))) == 6


# ─── 3. 정본 파일 ─────────────────────────────────────────────────────────────

_GOLD_DIRS = ("testdata/text2sql_gold", "testdata/text2sql_gold_sandbox", "testdata/routing_gold",
              "testdata/apm_bench")


def _raw_welcome() -> dict:
    body = yaml.safe_load((_ROOT / "config" / "duty_areas.yaml").read_text(encoding="utf-8"))
    return body["welcome_queries"]


def _ids(node: object) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        if isinstance(node.get("id"), str):
            found.add(node["id"])
        for v in node.values():
            found |= _ids(v)
    elif isinstance(node, list):
        for v in node:
            found |= _ids(v)
    return found


def _gold_index() -> dict[str, set[str]]:
    index: dict[str, set[str]] = {}
    for rel in _GOLD_DIRS:
        for path in sorted((_ROOT / rel).glob("*.yaml")):
            assert path.stem not in index, f"골드셋 stem 중복 — gold: 참조가 모호해진다: {path.stem}"
            index[path.stem] = _ids(yaml.safe_load(path.read_text(encoding="utf-8")))
    return index


def test_real_file_every_draft_is_loaded_or_pending() -> None:
    """초안은 전부 파일에 남는다 — 로드되거나(근거 유효) `pending`(검증 대기). 조용히 빠지는 항목이 없다."""
    raw = _raw_welcome()
    assert set(raw) == {"server_os", "was_mw", "db"}
    vocab = read_duty_area_vocabulary(_ROOT / "config" / "duty_areas.yaml")
    for area, entries in raw.items():
        loaded = [e for e in entries if not e.get("pending")]
        assert all(is_valid_verified_by(e.get("verified_by")) for e in loaded), area
        assert [q.query for q in vocab.welcome_queries.get(area, ())] == [e["query"] for e in loaded]
        assert all(e["pending"] == "closed_net" and "verified_by" not in e for e in entries if e.get("pending"))
    # 부록 A 초안 12개(서버/OS 5 · WAS 5 · DB 2) — 로드 4 · 검증 대기 8
    assert sum(len(v) for v in raw.values()) == 12
    assert sum(len(v) for v in vocab.welcome_queries.values()) == 4
    assert "db" not in vocab.welcome_queries                   # DB 문구는 전부 폐쇄망 검증 대기


def test_real_file_gold_refs_resolve_and_assemblers_exist() -> None:
    index = _gold_index()
    src_text = "\n".join(p.read_text(encoding="utf-8") for p in (_ROOT / "src").rglob("*.py"))
    refs = [e["verified_by"] for entries in _raw_welcome().values() for e in entries if e.get("verified_by")]
    assert refs, "검증된 문구가 하나도 없다"
    for ref in refs:
        kind, _, value = ref.partition(":")
        if kind == "gold":
            stem, _, item_id = value.partition("/")
            assert stem in index, f"골드셋 파일 없음: {ref}"
            assert item_id in index[stem], f"골드셋 항목 없음: {ref}"
        elif kind == "assembler":
            assert re.search(rf"^\s*def {re.escape(value)}\(", src_text, re.M), f"조립기 없음: {ref}"


def test_assembler_existence_check_is_live() -> None:
    """`assembler:` 존재 단언이 실제로 걸리는지(현재 정본엔 조립기 근거 문구가 없다 — 검사기 자체를 고정)."""
    src_text = (_ROOT / "src/db_adapters/polestar/assembler.py").read_text(encoding="utf-8")
    assert re.search(r"^\s*def try_deterministic_alarm_sql\(", src_text, re.M)
    assert not re.search(r"^\s*def no_such_assembler_xyz\(", src_text, re.M)


def test_real_file_has_no_server_names_or_sql() -> None:
    text = (_ROOT / "config" / "duty_areas.yaml").read_text(encoding="utf-8")
    section = text[text.index("welcome_queries:"):]
    assert not re.search(r"\b(SELECT|FROM|WHERE)\b", section)
    assert not re.search(r"\b\d{1,3}(\.\d{1,3}){3}\b", section)          # IP
    assert not re.search(r"\b(cocm|cob0|sbhdbo|hostapo)[\w-]*", section)  # 골드셋의 서버명 표면


def test_real_file_zone_placeholder_kept() -> None:
    for entries in _raw_welcome().values():
        for e in entries:
            if "존" in e["query"]:
                assert "ㅇㅇ존" in e["query"], e["label"]


# ─── 4. /auth/me ─────────────────────────────────────────────────────────────

_VOCAB = parse_vocabulary({
    "areas": [{"code": "alpha", "label": "알파"}, {"code": "beta", "label": "베타"}],
    "welcome_queries": {
        "alpha": [_q("a1"), _q("a2")],
        "beta": [_q("b1"), _q("b2"), _q("b3"), _q("b4"), _q("b-pending", verified_by=None, pending="closed_net")],
    },
})
_BASE_USER_KEYS = ["user_id", "username", "role", "department", "allowed_db_ids",
                   "alarm_zones", "is_protected", "status", "last_login_at"]


@pytest.fixture()
def vocab(monkeypatch):
    monkeypatch.setattr(duty_area_support, "load_duty_area_vocabulary", lambda: _VOCAB)


def _user(user_id: str, **kw) -> User:
    return User(user_id=user_id, username=user_id, hashed_password="x", role=UserRole.USER,
                status=UserStatus.ACTIVE, **kw)


def _hdr(sub: str) -> dict[str, str]:
    tok = jwt.encode({"sub": sub, "type": "user", "exp": datetime.now(UTC) + timedelta(hours=1)},
                     _USER_SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {tok}"}


def _app(*, duty: bool = False, hints: bool = False, focus: bool = False, guide: bool = False):
    from src.api.server import create_app
    from src.config import AdminConfig, AppConfig, AuthConfig, DutyAreaConfig, WelcomeGuideConfig

    cfg = AppConfig(
        auth=AuthConfig(enabled=True, jwt_secret=_USER_SECRET),
        admin=AdminConfig(jwt_secret=_ADMIN_SECRET),
        duty_area=DutyAreaConfig(enabled=duty, alarm_focus_enabled=focus, welcome_hints_enabled=hints),
        welcome_guide=WelcomeGuideConfig(enabled=guide),
    )
    app = create_app(cfg)
    users = {"both": _user("both", duty_areas=["beta", "alpha"]), "none": _user("none")}
    app.state.user_repo = SimpleNamespace(get_by_user_id=AsyncMock(side_effect=lambda uid: users.get(uid)))
    return app


def _me(app, sub: str = "both") -> dict:
    resp = TestClient(app).get("/api/v1/auth/me", headers=_hdr(sub))
    assert resp.status_code == 200
    return resp.json()


def test_create_app_resolves_flags_once(vocab) -> None:
    assert _app(duty=True, hints=True).state.duty_welcome_hints_enabled is True
    assert _app(duty=True, hints=False).state.duty_welcome_hints_enabled is False
    assert _app(duty=False, hints=True).state.duty_welcome_hints_enabled is False
    assert _app(guide=True).state.welcome_guide_enabled is True
    assert _app().state.welcome_guide_enabled is False


def test_me_off_keeps_key_set(vocab) -> None:
    assert list(_me(_app())) == _BASE_USER_KEYS
    # 예시 질의 플래그만 켜도(담당 지정 off) 키 없음 — 실효 AND
    assert list(_me(_app(hints=True))) == _BASE_USER_KEYS
    # 담당 지정만 켜면 W1 키 그대로(예시 질의 키 없음)
    assert list(_me(_app(duty=True))) == _BASE_USER_KEYS + ["duty_areas", "duty_area_options"]
    assert list(_me(_app(duty=True, focus=True))) == _BASE_USER_KEYS + [
        "duty_areas", "duty_area_options", "duty_alarm_focus"]


def test_me_duty_welcome_queries_shape(vocab) -> None:
    body = _me(_app(duty=True, hints=True))
    assert list(body) == _BASE_USER_KEYS + ["duty_areas", "duty_area_options", "duty_welcome_queries"]
    picked = body["duty_welcome_queries"]
    assert [(p["area"], p["label"]) for p in picked] == [
        ("alpha", "a1"), ("alpha", "a2"), ("beta", "b1"), ("beta", "b2"), ("beta", "b3")]
    assert all(set(p) == {"area", "area_label", "label", "query"} for p in picked)
    assert picked[0]["area_label"] == "알파" and picked[2]["area_label"] == "베타"
    assert "verified_by" not in str(body) and "b-pending" not in str(body)
    # 미지정 사용자 = 빈 목록(화면은 현행 버튼)
    assert _me(_app(duty=True, hints=True), "none")["duty_welcome_queries"] == []
    # 세 플래그 모두 켜면 키 순서는 W1 → W3 → W2
    assert list(_me(_app(duty=True, focus=True, hints=True)))[-2:] == ["duty_alarm_focus", "duty_welcome_queries"]


def test_me_welcome_guide_is_independent_of_duty(vocab) -> None:
    assert list(_me(_app(guide=True))) == _BASE_USER_KEYS + ["welcome_guide"]
    assert _me(_app(guide=True))["welcome_guide"] is True
    both = _me(_app(guide=True, duty=True, hints=True))
    assert list(both) == _BASE_USER_KEYS + [
        "duty_areas", "duty_area_options", "duty_welcome_queries", "welcome_guide"]


def test_login_response_never_carries_new_keys() -> None:
    from src.api.schemas import UserInfoResponse

    info = UserInfoResponse(user_id="u", username="u", role="user")
    assert "welcome_guide" not in info.model_dump() and "duty_welcome_queries" not in info.model_dump()


# ─── 5. 설정 ──────────────────────────────────────────────────────────────────


def test_env_names_one_each(monkeypatch) -> None:
    from src.config import DutyAreaConfig, WelcomeGuideConfig

    for name in ("DUTY_WELCOME_HINTS_ENABLED", "WELCOME_HINTS_ENABLED", "DUTY_AREA_WELCOME_HINTS_ENABLED",
                 "DUTY_AREA_ENABLED", "WELCOME_GUIDE_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    assert DutyAreaConfig(_env_file=None).welcome_hints_enabled is False
    assert WelcomeGuideConfig(_env_file=None).enabled is False
    monkeypatch.setenv("WELCOME_HINTS_ENABLED", "true")
    monkeypatch.setenv("DUTY_AREA_WELCOME_HINTS_ENABLED", "true")
    assert DutyAreaConfig(_env_file=None).welcome_hints_enabled is False
    monkeypatch.setenv("DUTY_WELCOME_HINTS_ENABLED", "true")
    cfg = DutyAreaConfig(_env_file=None)
    assert cfg.welcome_hints_enabled is True and cfg.welcome_hints_effective is False
    monkeypatch.setenv("DUTY_AREA_ENABLED", "true")
    assert DutyAreaConfig(_env_file=None).welcome_hints_effective is True
    monkeypatch.setenv("WELCOME_GUIDE_ENABLED", "true")
    assert WelcomeGuideConfig(_env_file=None).enabled is True


def test_constructor_field_names_and_effective_and() -> None:
    from src.config import DutyAreaConfig

    assert DutyAreaConfig(_env_file=None, enabled=True, welcome_hints_enabled=True).welcome_hints_effective is True
    assert DutyAreaConfig(_env_file=None, enabled=False, welcome_hints_enabled=True).welcome_hints_effective is False
    assert DutyAreaConfig(_env_file=None, enabled=True, welcome_hints_enabled=False).welcome_hints_effective is False
    # W3 필드명 인자 이관은 그대로
    both = DutyAreaConfig(_env_file=None, enabled=True, alarm_focus_enabled=True, welcome_hints_enabled=True)
    assert both.alarm_focus_effective is True and both.welcome_hints_effective is True


def test_settings_catalog_help_and_env_example() -> None:
    from src.api.settings_catalog import field_index

    index = field_index()
    assert index["DUTY_WELCOME_HINTS_ENABLED"].group_key == "duty_area"
    assert index["WELCOME_GUIDE_ENABLED"].group_key == "welcome_guide"
    duty_help = (_ROOT / "config/settings_help/duty_area.yaml").read_text(encoding="utf-8")
    assert "\n  DUTY_WELCOME_HINTS_ENABLED:\n" in duty_help
    guide_help = yaml.safe_load((_ROOT / "config/settings_help/welcome_guide.yaml").read_text(encoding="utf-8"))
    assert guide_help["group"] == "welcome_guide" and "WELCOME_GUIDE_ENABLED" in guide_help["settings"]
    example = (_ROOT / ".env.example").read_text(encoding="utf-8")
    for key in ("DUTY_WELCOME_HINTS_ENABLED", "WELCOME_GUIDE_ENABLED"):
        assert len(re.findall(rf"^#?\s*{key}=false$", example, re.M)) == 1, key


def test_authz_modules_do_not_read_welcome_values() -> None:
    for rel in ("src/api/dependencies.py", "src/api/routes/alarm.py"):
        text = (_ROOT / rel).read_text(encoding="utf-8")
        assert "welcome_queries" not in text and "welcome_guide" not in text, rel
