"""실험 사전 점검 수용 기준 (plans/99 E-0·E-1 · `--preflight`).

이 점검의 값은 **사람이 표에서 행을 찾는 일을 없앤 것**이다. 따라서 테스트도
"판정이 나오는가"가 아니라 **"조치가 함께 나오는가"** 를 본다 - 판정만 있고
조치가 없으면 종전 체크리스트와 다를 게 없다.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts.scenario import preflight as pf


def _multi_db(ids: list[str]) -> SimpleNamespace:
    """`AppConfig.multi_db` 대역 - 실 설정과 같은 접근 경로(`get_active_db_ids()`)만 흉내 낸다.

    종전 픽스처는 `AppConfig` 에 없는 `active_db_ids` 속성을 주입해, 코드가 존재하지 않는
    속성을 읽는 버그(`ACTIVE_DB_IDS` 가 늘 비어 보임)를 통과로 굳혔다(2026-09-17).
    """
    return SimpleNamespace(get_active_db_ids=lambda: list(ids))


def _cfg(**kw):
    base = dict(
        llm=SimpleNamespace(provider="fabrix"),
        orchestrator=SimpleNamespace(provider="vllm"),
        auth=SimpleNamespace(enabled=True),
        multi_db=_multi_db(["polestar_cm_gp"]),
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
    ("mlx", pf.VERDICT_OK),
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


@pytest.mark.parametrize("orchestrator,verdict", [
    ("vllm", pf.VERDICT_OK),
    ("mlx", pf.VERDICT_OK),
    ("gemini", pf.VERDICT_STOP),
    ("", pf.VERDICT_STOP),
])
def test_오케스트레이터_평면도_항상_판정한다(orchestrator: str, verdict: str) -> None:
    """D-222(G-3) - 워커가 내부망이어도 제어 평면이 외부면 과금 경로다.

    `ENABLE_DEEPAGENTS_PACKAGE` 가 꺼져 있어도 본다
    (알람 enricher 트랙 B 가 같은 오케스트레이터를 쓴다).
    """
    report = pf.Report()
    cfg = _cfg(llm=SimpleNamespace(provider="mlx"),
               orchestrator=SimpleNamespace(provider=orchestrator), enable_deepagents_package=False)
    pf._check_provider(cfg, report)
    by_key = {c.key: c for c in report.checks}
    assert by_key["LLM_PROVIDER"].verdict == pf.VERDICT_OK
    assert by_key["ORCHESTRATOR_PROVIDER"].verdict == verdict
    if verdict == pf.VERDICT_STOP:
        assert "D-127" in by_key["ORCHESTRATOR_PROVIDER"].action


@pytest.mark.parametrize("worker,orchestrator,external", [
    ("fabrix", "vllm", []),
    ("mlx", "mlx", []),
    ("ollama", "mlx", []),
    ("mlx", "gemini", ["ORCHESTRATOR_PROVIDER=gemini"]),
    ("ollama", "gemini", ["ORCHESTRATOR_PROVIDER=gemini"]),
    ("gemini", "gemini", ["LLM_PROVIDER=gemini", "ORCHESTRATOR_PROVIDER=gemini"]),
    ("vllm", "vllm", ["LLM_PROVIDER=vllm"]),
    ("unknown(ValidationError)", "unknown(ValidationError)",
     ["LLM_PROVIDER=unknown(ValidationError)", "ORCHESTRATOR_PROVIDER=unknown(ValidationError)"]),
])
def test_과금_판정의_단일_정의(worker: str, orchestrator: str, external: list[str]) -> None:
    """평면별 비과금 집합 - 워커 {fabrix, ollama, mlx} / 오케스트레이터 {vllm, mlx}(D-222)."""
    assert pf.external_planes(worker, orchestrator) == external


def test_시나리오와_벤치가_같은_판정_정의를_쓴다() -> None:
    """정의가 세 곳에 따로 있다가 한 곳만 고쳐지는 것을 막는다(plans/100 CU-4)."""
    from scripts.bench import __main__ as bench_cli
    from scripts.scenario import __main__ as scenario_cli

    assert scenario_cli.external_planes is pf.external_planes
    assert bench_cli.external_planes is pf.external_planes
    for module in (pf, scenario_cli, bench_cli):
        assert not hasattr(module, "INTERNAL_PROVIDERS")
        assert not hasattr(module, "_INTERNAL_PROVIDERS")


# --- 환경 판정 --------------------------------------------------------------

def test_활성_DB가_비면_중단이다() -> None:
    report = pf.Report()
    pf._check_active_dbs(_cfg(multi_db=_multi_db([])), report)
    assert report.checks[0].verdict == pf.VERDICT_STOP


def test_샌드박스만_활성이면_폐쇄망_측정이_아님을_경고한다() -> None:
    from scripts.scenario.runner import SANDBOX_DB_ID

    report = pf.Report()
    pf._check_active_dbs(_cfg(multi_db=_multi_db([SANDBOX_DB_ID])), report)
    assert report.checks[0].verdict == pf.VERDICT_WARN
    assert "sandbox" in report.checks[0].action


def test_활성_DB는_실제_설정_경로로_읽는다(monkeypatch) -> None:
    """mock 통과 != 실동작 - 실 `load_config()` 가 만든 `AppConfig` 로 판정한다(B-1).

    종전 코드는 `AppConfig` 에 없는 `active_db_ids` 를 읽어 `ACTIVE_DB_IDS=polestar` 가
    설정돼 있어도 "(비어 있음)" 중단을 냈다.
    """
    from src.config import load_config

    monkeypatch.setenv("ACTIVE_DB_IDS", "polestar_cm_gp,polestar")
    load_config.cache_clear()
    try:
        cfg = load_config()
        assert pf._active_db_ids(cfg) == ["polestar_cm_gp", "polestar"]
        report = pf.Report()
        pf._check_active_dbs(cfg, report)
    finally:
        load_config.cache_clear()
    check = report.checks[0]
    assert check.verdict == pf.VERDICT_OK
    assert check.observed == "polestar_cm_gp, polestar"


def test_DB_조회_점검도_같은_활성_DB_목록을_쓴다(monkeypatch) -> None:
    """E-1 조회 분기(`run_preflight`)도 같은 경로로 읽어야 G-5/G-8 이 '비활성'으로 새지 않는다."""
    from src.config import AppConfig, MultiDBConfig

    cfg = AppConfig(multi_db=MultiDBConfig(_env_file=None, active_db_ids_csv="polestar_cm_gp"))
    monkeypatch.setattr("src.config.load_config", lambda: cfg)
    for name in ("_check_provider", "_check_mlx", "_check_auth", "_check_db_backend",
                 "_check_ladder", "_check_disk"):
        monkeypatch.setattr(pf, name, lambda *a, **k: None)
    seen: dict = {}

    async def fake_db_checks(_cfg, _report, active, b0, gp):
        seen["active"] = active

    monkeypatch.setattr(pf, "_run_db_checks", fake_db_checks)
    report = pf.run_preflight(with_db=True)

    assert seen["active"] == {"polestar_cm_gp"}
    assert [c.verdict for c in report.checks if c.key == "ACTIVE_DB_IDS"] == [pf.VERDICT_OK]


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
               multi_db=_multi_db([]), db_backend="dbhub")
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
    monkeypatch.delenv("ORCHESTRATOR_PROVIDER", raising=False)
    report = pf.Report()
    pf._check_provider(_cfg(llm=SimpleNamespace(provider="fabrix")), report)
    check = report.checks[0]
    assert check.source == ""
    assert "셸 환경변수" not in check.action
    assert "셸 환경변수" not in pf.format_report(report)


# --- MLX 로컬 서버 점검 (plans/100 CU-5 · T-10) ------------------------------

class _Resp:
    def __init__(self, status: int = 200, ids: tuple[str, ...] = ()):
        self.status_code = status
        self._ids = ids

    def json(self):
        return {"object": "list", "data": [{"id": i, "object": "model"} for i in self._ids]}


_QWEN = "mlx-community/Qwen3.5-9B-OptiQ-4bit"


def _mlx_cfg(*, worker_url="http://127.0.0.1:8080/v1", worker_model="",
             orchestrator="vllm", orch_url="", orch_model="Qwen3.5-9B"):
    return _cfg(
        llm=SimpleNamespace(provider="mlx", mlx_base_url=worker_url, mlx_model=worker_model),
        orchestrator=SimpleNamespace(provider=orchestrator, base_url=orch_url, model=orch_model),
    )


class _Calls(list):
    """GET URL 목록. 생성 점검 POST 는 `.posts`(URL)·`.bodies`(요청 본문)에 따로 담는다."""

    def __init__(self) -> None:
        super().__init__()
        self.posts: list[str] = []
        self.bodies: list[dict] = []


def _serve(monkeypatch, responses: dict, posts: dict | None = None):
    """URL -> _Resp 또는 예외. 호출된 URL 을 돌려준다(네트워크 0).

    생성 점검(`POST .../chat/completions`)은 `posts` 에 없으면 200 으로 응답한다.
    """
    import requests

    calls = _Calls()

    def fake_get(url, timeout=None, **kwargs):
        calls.append(url)
        result = responses[url]
        if isinstance(result, Exception):
            raise result
        return result

    def fake_post(url, json=None, timeout=None, **kwargs):
        calls.posts.append(url)
        calls.bodies.append(json)
        result = (posts or {}).get(url, _Resp())
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "post", fake_post)
    return calls


def test_mlx_가_아니면_점검을_싣지_않는다(monkeypatch) -> None:
    """운영 경로(fabrix·vllm)의 사전 점검 출력은 비트 동일이다."""
    calls = _serve(monkeypatch, {})
    report = pf.Report()
    pf._check_mlx(_cfg(), report)
    assert report.checks == [] and calls == [] and calls.posts == []


def test_mlx_서버에_도달하지_못하면_중단하고_기동_명령을_준다(monkeypatch) -> None:
    import requests

    calls = _serve(monkeypatch, {"http://127.0.0.1:8080/v1/models": requests.ConnectionError("refused")})
    report = pf.Report()
    pf._check_mlx(_mlx_cfg(), report)

    stop = report.checks[0]
    assert stop.key == "MLX 서버(워커)" and stop.verdict == pf.VERDICT_STOP
    assert "scripts/mlx_server.sh" in stop.action
    assert "Qwen" not in stop.action, "모델 ID 를 박으면 설정을 바꾼 뒤 안내가 낡는다"
    assert "ConnectionError" in stop.detail
    assert [c.key for c in report.checks] == ["MLX 서버(워커)"], \
        "도달 못 한 서버의 모델은 판정하지 않는다"
    assert calls.posts == [], "도달 못 한 서버에는 생성 요청을 보내지 않는다"


def test_mlx_서버가_200이_아니면_중단이다(monkeypatch) -> None:
    _serve(monkeypatch, {"http://127.0.0.1:8080/v1/models": _Resp(status=503)})
    report = pf.Report()
    pf._check_mlx(_mlx_cfg(), report)
    assert report.checks[0].verdict == pf.VERDICT_STOP
    assert "503" in report.checks[0].detail


def test_mlx_모델_ID_가_목록에_없으면_주의다(monkeypatch) -> None:
    """로컬 경로로 띄우면 목록에 없을 수 있어 중단으로 두지 않는다(틀린 ID 는 첫 요청이 404)."""
    _serve(monkeypatch, {"http://127.0.0.1:8080/v1/models": _Resp(ids=(_QWEN,))})
    report = pf.Report()
    pf._check_mlx(_mlx_cfg(worker_model="Qwen3.5-9B"), report)
    model = {c.key: c for c in report.checks}["MLX 모델(워커)"]
    assert model.verdict == pf.VERDICT_WARN
    assert "--model" in model.action


@pytest.mark.parametrize("worker_model", ["", _QWEN])
def test_mlx_모델_별칭이거나_목록에_있으면_통과다(monkeypatch, worker_model: str) -> None:
    _serve(monkeypatch, {"http://127.0.0.1:8080/v1/models": _Resp(ids=(_QWEN,))})
    report = pf.Report()
    pf._check_mlx(_mlx_cfg(worker_model=worker_model), report)
    assert [c.verdict for c in report.checks] == [pf.VERDICT_OK, pf.VERDICT_OK, pf.VERDICT_OK]


def test_mlx_같은_서버에_다른_모델이면_재적재를_경고한다(monkeypatch) -> None:
    url = "http://127.0.0.1:8080/v1"
    calls = _serve(monkeypatch, {url + "/models": _Resp(ids=("a/model-1", "b/model-2"))})
    report = pf.Report()
    cfg = _mlx_cfg(worker_model="a/model-1", orchestrator="mlx", orch_url=url,
                   orch_model="b/model-2")
    pf._check_mlx(cfg, report)

    reload_check = {c.key: c for c in report.checks}["MLX 재적재"]
    assert reload_check.verdict == pf.VERDICT_WARN
    assert "재적재" in reload_check.action
    assert calls == [url + "/models"], "같은 서버는 한 번만 조회한다"
    assert calls.posts == [url + "/chat/completions"], "같은 서버는 생성 점검도 한 번만 보낸다"


def test_mlx_같은_모델이면_재적재_경고가_없다(monkeypatch) -> None:
    url = "http://127.0.0.1:8080/v1"
    _serve(monkeypatch, {url + "/models": _Resp(ids=("a/model-1",))})
    report = pf.Report()
    cfg = _mlx_cfg(worker_model="a/model-1", orchestrator="mlx", orch_url=url,
                   orch_model="a/model-1")
    pf._check_mlx(cfg, report)
    assert "MLX 재적재" not in {c.key for c in report.checks}
    assert {c.key for c in report.checks} == {
        "MLX 서버(워커)", "MLX 모델(워커)", "MLX 생성(워커)",
        "MLX 서버(오케스트레이터)", "MLX 모델(오케스트레이터)", "MLX 생성(오케스트레이터)"}


def test_mlx_루프백이_아니면_주의다(monkeypatch) -> None:
    url = "http://192.168.0.20:8080/v1"
    _serve(monkeypatch, {url + "/models": _Resp(ids=())})
    report = pf.Report()
    pf._check_mlx(_mlx_cfg(worker_url=url), report)
    binding = {c.key: c for c in report.checks}["MLX 바인딩(워커)"]
    assert binding.verdict == pf.VERDICT_WARN
    assert "무인증" in binding.action


@pytest.mark.parametrize("url", ["http://127.0.0.1:8080/v1", "http://localhost:8080/v1", "http://[::1]:8080/v1"])
def test_mlx_루프백이면_바인딩_경고가_없다(monkeypatch, url: str) -> None:
    _serve(monkeypatch, {url + "/models": _Resp(ids=())})
    report = pf.Report()
    pf._check_mlx(_mlx_cfg(worker_url=url), report)
    assert "MLX 바인딩(워커)" not in {c.key for c in report.checks}


def test_mlx_오케스트레이터_base_url_이_비면_중단이다(monkeypatch) -> None:
    calls = _serve(monkeypatch, {})
    report = pf.Report()
    cfg = _cfg(orchestrator=SimpleNamespace(provider="mlx", base_url="", model="default_model"))
    pf._check_mlx(cfg, report)
    assert report.checks[0].key == "MLX 서버(오케스트레이터)"
    assert report.checks[0].verdict == pf.VERDICT_STOP
    assert calls == [] and calls.posts == []


def test_mlx_생성_스레드가_죽은_서버는_중단이다(monkeypatch) -> None:
    """목록 조회는 200 인데 생성은 무응답인 서버(2026-09-17 Qwen3.8-27B Metal OOM 실측)."""
    import requests

    url = "http://127.0.0.1:8080/v1"
    calls = _serve(monkeypatch, {url + "/models": _Resp(ids=(_QWEN,))},
                   posts={url + "/chat/completions": requests.ReadTimeout("read timed out")})
    report = pf.Report()
    pf._check_mlx(_mlx_cfg(worker_model=_QWEN), report)

    checks = {c.key: c for c in report.checks}
    assert checks["MLX 서버(워커)"].verdict == pf.VERDICT_OK, "목록 조회만으로는 통과로 보인다"
    probe = checks["MLX 생성(워커)"]
    assert probe.verdict == pf.VERDICT_STOP
    assert "ReadTimeout" in probe.detail
    assert "Insufficient Memory" in probe.action and pf.MLX_SERVER_COMMAND in probe.action
    assert pf.format_report(report).count("[중단]") == 1


def test_mlx_생성_점검은_서버_모델_별칭으로_1토큰만_보낸다(monkeypatch) -> None:
    """설정 모델 ID 를 보내면 캐시에 있는 다른 모델을 서버가 적재할 수 있다 - 별칭으로 보낸다."""
    url = "http://127.0.0.1:8080/v1"
    calls = _serve(monkeypatch, {url + "/models": _Resp(ids=("a/other",))})
    report = pf.Report()
    pf._check_mlx(_mlx_cfg(worker_model="a/other"), report)

    assert calls.bodies == [{
        "model": pf.MLX_DEFAULT_MODEL,
        "messages": [{"role": "user", "content": "1"}],
        "max_tokens": 1,
        "chat_template_kwargs": {"enable_thinking": False},
    }]


def test_mlx_생성_점검이_200이_아니면_중단이다(monkeypatch) -> None:
    url = "http://127.0.0.1:8080/v1"
    _serve(monkeypatch, {url + "/models": _Resp(ids=(_QWEN,))},
           posts={url + "/chat/completions": _Resp(status=500)})
    report = pf.Report()
    pf._check_mlx(_mlx_cfg(), report)
    probe = {c.key: c for c in report.checks}["MLX 생성(워커)"]
    assert probe.verdict == pf.VERDICT_STOP and "500" in probe.detail


def test_mlx_점검_문구도_조치를_동반하고_ASCII_구두점이다(monkeypatch) -> None:
    import requests

    url = "http://10.0.0.5:8080/v1"
    _serve(monkeypatch, {url + "/models": _Resp(ids=("x/other",))},
           posts={url + "/chat/completions": requests.ReadTimeout("read timed out")})
    report = pf.Report()
    cfg = _mlx_cfg(worker_url=url, worker_model="a/model-1", orchestrator="mlx", orch_url=url,
                   orch_model="b/model-2")
    pf._check_mlx(cfg, report)
    assert report.checks
    emitted = pf.format_report(report)
    for check in report.checks:
        assert check.action.strip()
    for ch in _NON_ASCII_PUNCT:
        assert ch not in emitted


def test_실행_전_MLX_점검은_mlx_가_아니면_아무것도_보내지_않는다(monkeypatch) -> None:
    calls = _serve(monkeypatch, {})
    assert pf.mlx_run_blockers(_cfg()) == []
    assert calls == [] and calls.posts == []


def test_실행_전_MLX_점검은_중단_항목만_돌려준다(monkeypatch) -> None:
    import requests

    url = "http://127.0.0.1:8080/v1"
    _serve(monkeypatch, {url + "/models": _Resp(ids=(_QWEN,))},
           posts={url + "/chat/completions": requests.ReadTimeout("read timed out")})
    blockers = pf.mlx_run_blockers(_mlx_cfg(worker_model=_QWEN))
    assert [c.key for c in blockers] == ["MLX 생성(워커)"]
