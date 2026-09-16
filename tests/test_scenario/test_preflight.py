"""실험 사전 점검 수용 기준 (plans/99 E-0·E-1 · `--preflight`).

이 점검의 값은 **사람이 표에서 행을 찾는 일을 없앤 것**이다. 따라서 테스트도
"판정이 나오는가"가 아니라 **"조치가 함께 나오는가"** 를 본다 - 판정만 있고
조치가 없으면 종전 체크리스트와 다를 게 없다.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts.scenario import preflight as pf


def _cfg(**kw):
    base = dict(
        llm=SimpleNamespace(provider="fabrix"),
        auth=SimpleNamespace(enabled=True),
        active_db_ids=["polestar_cm_gp"],
        db_backend="dbhub",
        enable_deepagents_package=False,
    )
    base.update(kw)
    return SimpleNamespace(**base)


# --- 모든 점검은 조치를 동반한다 -------------------------------------------

def test_모든_점검은_조치_문구를_동반한다() -> None:
    """판정만 있고 조치가 없으면 읽는 사람이 무엇을 할지 모른다(사용자 지적 2026-09-16)."""
    report = pf.Report()
    cfg = _cfg()
    pf._check_provider(cfg, report)
    pf._check_active_dbs(cfg, report)
    pf._check_auth(cfg, report)
    pf._check_db_backend(cfg, report)
    pf._check_disk(report)

    assert report.checks
    for check in report.checks:
        assert check.action.strip(), f"{check.key} 에 조치가 없다"
        assert check.verdict in (pf.VERDICT_OK, pf.VERDICT_WARN,
                                 pf.VERDICT_STOP, pf.VERDICT_UNKNOWN)


# --- 프로바이더: 과금 게이트가 판정에 실린다 --------------------------------

@pytest.mark.parametrize("provider,verdict", [
    ("fabrix", pf.VERDICT_OK),
    ("ollama", pf.VERDICT_OK),
    ("gemini", pf.VERDICT_STOP),
    ("", pf.VERDICT_STOP),
])
def test_프로바이더별_판정(provider: str, verdict: str) -> None:
    report = pf.Report()
    pf._check_provider(_cfg(llm=SimpleNamespace(provider=provider)), report)
    assert report.checks[0].verdict == verdict


def test_외부_프로바이더는_승인_요구를_조치에_적는다() -> None:
    """D-127 은 건별 승인이다 - 조치에서 그 사실이 빠지면 안 된다."""
    report = pf.Report()
    pf._check_provider(_cfg(llm=SimpleNamespace(provider="gemini")), report)
    assert "D-127" in report.checks[0].action


# --- 환경 판정 --------------------------------------------------------------

def test_활성_DB가_비면_중단이다() -> None:
    report = pf.Report()
    pf._check_active_dbs(_cfg(active_db_ids=[]), report)
    assert report.checks[0].verdict == pf.VERDICT_STOP


def test_샌드박스만_활성이면_폐쇄망_측정이_아님을_경고한다() -> None:
    from scripts.scenario.runner import SANDBOX_DB_ID

    report = pf.Report()
    pf._check_active_dbs(_cfg(active_db_ids=[SANDBOX_DB_ID]), report)
    assert report.checks[0].verdict == pf.VERDICT_WARN
    assert "sandbox" in report.checks[0].action


# --- 인증: 꺼져 있으면 T-a·T-b 가 무의미해진다 ------------------------------

def test_인증이_꺼져_있으면_토큰_경로가_무의미함을_밝힌다() -> None:
    report = pf.Report()
    pf._check_auth(_cfg(auth=SimpleNamespace(enabled=False)), report)
    check = report.checks[0]
    assert check.verdict == pf.VERDICT_WARN
    assert "T-a" in check.action and "T-b" in check.action


# --- 읽기 전용 계약 ---------------------------------------------------------

def test_E1_질의는_SELECT_뿐이다() -> None:
    """D-003 읽기 전용. 점검이 DB 를 건드리면 그 자체가 측정 오염이다."""
    for sql in (pf.SQL_G5, pf.SQL_G8):
        head = sql.strip().split()[0].upper()
        assert head == "SELECT", sql[:60]
        lowered = sql.lower()
        for forbidden in ("insert", "update", "delete", "drop", "alter", "truncate", "create"):
            assert forbidden not in lowered, f"{forbidden} 가 점검 SQL 에 있다"


def test_스키마_접두사는_단일_출처를_쓴다() -> None:
    """레지스트리를 직접 읽으면 DB 편입 때 여기가 뒤처진다(CLAUDE.md 체크리스트 (4))."""
    import inspect

    source = inspect.getsource(pf._schema_prefix)
    assert "get_schema_prefix" in source


# --- 판정 요약 --------------------------------------------------------------

def test_중단이_하나라도_있으면_요약이_중단이다() -> None:
    report = pf.Report()
    report.add("K", "v", pf.VERDICT_OK, "진행")
    report.add("S", "v", pf.VERDICT_STOP, "멈춤")
    assert "중단" in pf.format_report(report)


def test_판정불가는_추정하지_않고_그대로_노출한다() -> None:
    """모르는 것을 OK 로 넘기면 그 게이트가 조용히 미결로 남는다."""
    report = pf.Report()
    report.add("K", "v", pf.VERDICT_OK, "진행")
    report.add("U", "조회 실패", pf.VERDICT_UNKNOWN, "DB 도달을 먼저 푼다")
    text = pf.format_report(report)
    assert "판정하지 못했습니다" in text and "추정하지 않습니다" in text


_NON_ASCII_PUNCT = ("\u2014", "\u2013", "\u2018", "\u2019", "\u201c", "\u201d")


def test_출력은_ASCII_구두점만_쓴다() -> None:
    """cp949 콘솔에서 em-dash 하나가 런을 죽인다(W5).

    **테스트가 만든 문자열이 아니라 모듈이 실제로 내보내는 조치 문구를 본다.**
    처음에는 테스트용 Report 하나만 검사해서 실제 조치 문구의 em-dash 를 놓쳤다
    (2026-09-16) - 통과하는데 출력은 깨지는 테스트였다.
    """
    report = pf.Report()
    cfg = _cfg(llm=SimpleNamespace(provider="gemini"), auth=SimpleNamespace(enabled=False),
               active_db_ids=[], db_backend="dbhub")
    pf._check_provider(cfg, report)
    pf._check_active_dbs(cfg, report)
    pf._check_auth(cfg, report)
    pf._check_db_backend(cfg, report)
    pf._check_disk(report)
    pf._check_ladder(cfg, report)

    emitted = pf.format_report(report) + "".join(c.action + c.observed for c in report.checks)
    for ch in _NON_ASCII_PUNCT:
        assert ch not in emitted, f"ASCII 아닌 구두점 {ch!r} 가 실제 출력에 있다"


def test_모든_조치_문구가_ASCII_구두점이다() -> None:
    """분기마다 조치 문구가 다르다 - 한 경로만 보면 다른 경로의 깨짐을 놓친다."""
    import re

    source = open(pf.__file__, encoding="utf-8").read()
    # report.add(...) 안의 문자열 리터럴만 본다(독스트링·주석은 콘솔로 나가지 않는다).
    for call in re.findall(r"report\.add\((.*?)\)\n", source, re.S):
        for ch in _NON_ASCII_PUNCT:
            assert ch not in call, f"report.add 문구에 {ch!r} 가 있다: {call[:80]}"


# --- 출처 판정: 셸 오염을 사람이 따로 확인하지 않는다 -----------------------

def test_셸_환경변수가_덮으면_출처를_밝힌다(monkeypatch) -> None:
    """종전에는 `env | grep` 을 따로 돌려 머릿속에서 대조했다(사용자 지적 2026-09-16).

    `.env` 로딩은 os.environ 에 주입되지 않으므로(Known Mistakes 2026-06-10)
    os.environ 에 있다는 것은 **셸에서 왔다**는 뜻이고, 그러면 .env 를 고쳐도 안 먹는다.
    """
    monkeypatch.setenv("LLM_PROVIDER", "fabrix")
    report = pf.Report()
    pf._check_provider(_cfg(llm=SimpleNamespace(provider="fabrix")), report)
    check = report.checks[0]
    assert check.source == "os"
    assert ".env 를 고쳐도 먹지 않는다" in check.action
    assert "unset LLM_PROVIDER" in check.action
    assert "셸 환경변수" in pf.format_report(report)


def test_셸이_깨끗하면_출처를_붙이지_않는다(monkeypatch) -> None:
    """정상 경로에 잡음을 더하지 않는다 - 상시 경고는 진짜 경고를 덮는다."""
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    report = pf.Report()
    pf._check_provider(_cfg(llm=SimpleNamespace(provider="fabrix")), report)
    check = report.checks[0]
    assert check.source == ""
    assert "셸 환경변수" not in check.action
    assert "셸 환경변수" not in pf.format_report(report)
