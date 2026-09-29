"""러너 생성 입력 · 요구 소스 선택 제외 (plans/122 H-6 · C-4b · D-276 ②) — 관측 수집 담당.

고정하는 계약:
  - H-6 `auth: none`: 그 턴만 Authorization 헤더 없이 보내고 401 에 재로그인 재시도를 하지 않는다.
  - H-6 `upload_generate`: run 디렉터리 `generated/` 에 선언한 확장자·크기의 파일을 만들어
    올리고, 시나리오가 끝나면 지운다(run 이 끝나면 폴더도 없다). 내용이 채움 바이트여도 되는
    근거 - 서버 업로드 검사에서 크기가 파싱보다 앞선다 - 를 서버 소스 순서로 고정한다.
  - C-4b: 요구 소스가 활성 소스의 부분집합이 아니면 선택하지 않고 `skipped` 에 사유 +
    `reason_code` 를 남긴다. 선언 없는 시나리오 · 모의 실행 · 판독 불가는 제외하지 않는다.
    예상치·1단·4단 출력이 건수를 보인다.
전부 무과금이다(LLM·DB 0 · 서버는 모의 서버 자식 프로세스 1회).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest

from scripts.scenario import REPO_ROOT
from scripts.scenario import __main__ as cli
from scripts.scenario import runner as runner_mod
from scripts.scenario.assertions import Observation
from scripts.scenario.catalog import Catalog, Group, Scenario, Turn, load_catalog
from scripts.scenario.client import ClientConfig, ScenarioClient
from scripts.scenario.runner import (
    SOURCE_SKIP_CODE,
    RawLog,
    RunConfig,
    estimate,
    execute,
    generate_upload,
    iter_executions,
    resolve_active_sources,
    source_exclusions,
)

META = {"run_id": "r", "env": "closed", "mode": "run"}


class FakeClient:
    def __init__(self, responses: list[Observation]) -> None:
        self.responses = list(responses)
        self.sent: list[dict[str, Any]] = []

    def send(self, endpoint: str, payload: dict[str, Any], upload: Path | None = None,
             **kwargs: Any) -> Observation:
        size = upload.stat().st_size if upload is not None and upload.exists() else None
        self.sent.append({"endpoint": endpoint, "upload": upload, "size": size, **kwargs})
        return self.responses.pop(0)

    def download(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _scenario(expect: dict[str, Any], **kwargs: Any) -> Scenario:
    base: dict[str, Any] = dict(id="T-01", group="T", plans=[122], title="t",
                                turns=[Turn({"query": "q"}, expect)])
    base.update(kwargs)
    return Scenario(**base)


def _catalog(*scenarios: Scenario) -> Catalog:
    return Catalog(groups={"T": Group("T", "T", 60000)}, scenarios=list(scenarios),
                   profiles={"baseline": {}})


def _run(tmp_path: Path, scenario: Scenario, client: FakeClient) -> list[dict[str, Any]]:
    raw = tmp_path / "raw.jsonl"
    runner_mod._run_once(_catalog(scenario), RunConfig(mode="run"), dict(META), "baseline",
                         scenario, 0, client, RawLog(raw), tmp_path, [],  # type: ignore[arg-type]
                         preference=[])
    return [json.loads(line) for line in raw.read_text(encoding="utf-8").splitlines() if line]


# --- H-6 auth: none --------------------------------------------------------------

def test_auth_none_턴만_익명으로_보낸다(tmp_path: Path) -> None:
    scenario = _scenario({}, turns=[
        Turn({"query": "q1"}, {"http_status": 401}, auth="none"),
        Turn({"query": "q2"}, {"status": "completed"}),
    ])
    client = FakeClient([Observation(http_status=401, status="error", error="http 401"),
                         Observation(http_status=200, status="completed")])
    rows = _run(tmp_path, scenario, client)
    assert client.sent[0].get("anonymous") is True
    assert "anonymous" not in client.sent[1], "인증 턴은 종전 호출 모양 그대로다"
    assert rows[0]["func_verdict"] == "pass", "401 이 기대값이면 러너 인증 실패(무효)가 아니다"


class _Refresh:
    def __init__(self) -> None:
        self.calls = 0

    @property
    def token(self) -> str | None:
        return "tok"

    def refresh(self) -> str | None:
        self.calls += 1
        return "tok2"


def test_익명_송신은_헤더가_없고_401_에_재로그인하지_않는다() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(401, json={"detail": "Not authenticated"})

    source = _Refresh()
    client = ScenarioClient(ClientConfig(port=1, token_source=source))
    client._client.close()
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    obs = client.send("stream", {"query": "q"}, anonymous=True)
    assert len(seen) == 1 and "authorization" not in seen[0].headers
    assert obs.http_status == 401 and obs.auth_retried is False
    assert source.calls == 0, "401 이 기대값이다 - 재로그인 재시도 금지"

    seen.clear()
    obs = client.send("plain", {"query": "q"})
    assert [r.headers.get("authorization") for r in seen] == ["Bearer tok", "Bearer tok"]
    assert source.calls == 1 and obs.auth_retried is True, "인증 턴의 T-a 재시도는 종전 그대로다"


# --- H-6 upload_generate ---------------------------------------------------------

def test_생성_업로드는_선언한_확장자와_크기로_만든다(tmp_path: Path) -> None:
    size = 3 * 1024 * 1024 + 5   # 조각(1 MiB) 경계를 넘는 크기
    scenario = _scenario({}, endpoint="file", upload_generate={"ext": ".xlsx", "size_bytes": size})
    path = generate_upload(tmp_path, scenario, 2)
    assert path == tmp_path / "generated" / "T-01-2.xlsx"
    assert path.stat().st_size == size


def test_생성_업로드를_올리고_시나리오가_끝나면_지운다(tmp_path: Path) -> None:
    scenario = _scenario({"http_status": 400}, endpoint="file",
                         upload_generate={"ext": ".csv", "size_bytes": 2048})
    client = FakeClient([Observation(http_status=400, status="error", error="http 400")])
    (row,) = _run(tmp_path, scenario, client)
    sent = client.sent[0]
    assert sent["upload"] == tmp_path / "generated" / "T-01-0.csv"
    assert sent["size"] == 2048, "업로드 시점에 파일이 선언 크기로 있었다"
    assert not sent["upload"].exists(), "시나리오가 끝나면 지운다"
    assert row["func_verdict"] == "pass"


def test_생성_업로드는_업로드_엔드포인트_턴에만_싣는다(tmp_path: Path) -> None:
    scenario = _scenario({}, endpoint="file", upload_generate={"ext": ".xls", "size_bytes": 10},
                         turns=[Turn({"query": "q1"}, {}),
                                Turn({"query": "q2"}, {}, endpoint="stream")])
    client = FakeClient([Observation(http_status=200, status="completed"),
                         Observation(http_status=200, status="completed")])
    _run(tmp_path, scenario, client)
    assert client.sent[0]["upload"] is not None and client.sent[1]["upload"] is None


def _function_body(source: str, name: str) -> str:
    start = source.index(f"async def {name}(")
    end = source.find("\n@router.", start)
    return source[start:end if end > 0 else len(source)]


@pytest.mark.parametrize("route", ["process_file_query", "process_file_query_stream"])
def test_서버_업로드_검사는_확장자와_크기가_파싱보다_앞선다(route: str) -> None:
    """생성 업로드를 채움 바이트로 만드는 근거(runner.generate_upload).

    이 순서가 바뀌면 생성 방식을 다시 본다.
    """
    source = (REPO_ROOT / "src" / "api" / "routes" / "query.py").read_text(encoding="utf-8")
    body = _function_body(source, route)
    ext = body.index('if file_ext not in ("xlsx", "docx")')
    size = body.index("len(file_bytes) > 10 * 1024 * 1024")
    drm = body.index("_resolve_uploaded_bytes(")
    assert ext < size < drm, "확장자 -> 크기 -> DRM 해제(그 뒤 파싱) 순서여야 한다"
    assert "excel_to_csv" not in body[:size] and "parse_excel" not in body[:size]


def test_모의_실행에서_생성_업로드가_올라가고_run_이_끝나면_폴더가_없다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner_mod, "RESULTS_ROOT", tmp_path)
    scenario = _scenario({"http_status": 200}, env="closed", endpoint="file",
                         upload_generate={"ext": ".xlsx", "size_bytes": 4096},
                         turns=[Turn({"query": "h-run 생성 업로드 모의 질의"},
                                     {"http_status": 200})])
    summary = execute(_catalog(scenario), RunConfig(mode="mock", env="closed", only=["T-01"]))
    run_dir = Path(summary["out_dir"])
    (row,) = [json.loads(line) for line in
              (run_dir / "raw.jsonl").read_text(encoding="utf-8").splitlines() if line]
    assert row["func_verdict"] == "pass" and row["error"] is None
    assert not (run_dir / "generated").exists()


# --- C-4b 요구 소스 선택 제외 -------------------------------------------------------

M_IDS = [f"M-0{i}" for i in range(1, 10)]
CLOSED = ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd"]


def test_활성_소스는_ACTIVE_DB_IDS_이고_모의는_판정하지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.config

    class _Multi:
        @staticmethod
        def get_active_db_ids() -> list[str]:
            return ["polestar_cm_gp", "polestar_b0", "polestar_cm_gp"]

    class _Cfg:
        multi_db = _Multi()

    monkeypatch.setattr(src.config, "load_config", lambda: _Cfg())
    assert resolve_active_sources(RunConfig(mode="run")) == (
        ["polestar_b0", "polestar_cm_gp"], "ACTIVE_DB_IDS")
    assert resolve_active_sources(RunConfig(mode="mock")) == (None, "mock")

    def boom() -> None:
        raise RuntimeError("설정 없음")

    monkeypatch.setattr(src.config, "load_config", boom)
    active, origin = resolve_active_sources(RunConfig(mode="run"))
    assert active is None and origin.startswith("판독 불가")


def test_itam_이_없는_run_에서_M군을_고르지_않고_사유_9건을_남긴다() -> None:
    catalog = load_catalog()
    closed = RunConfig(mode="run", active_sources=list(CLOSED))
    picked = [s.id for _p, group in iter_executions(catalog, closed) for s in group]
    assert not [sid for sid in picked if sid.startswith("M-")]
    skipped = source_exclusions(catalog, closed)
    # plans/125 M-1 4소스 골드 초안(FS군)도 요구 소스(apm·itam)를 선언한다 — M군만 떼어 본다.
    assert sorted(i["scenario_id"] for i in skipped if i["scenario_id"].startswith("M-")) == M_IDS
    assert {i["scenario_id"][:3] for i in skipped} <= {"M-0", "FS-"}
    first = next(item for item in skipped if item["scenario_id"] == "M-01")
    assert first["reason_code"] == SOURCE_SKIP_CODE == "requires_sources"
    assert first["reason"] == ("요구 소스 비활성 - requires_sources=[polestar, itam] · "
                               "활성=[polestar_b0, polestar_cm_gp, polestar_cm_yd]")


def test_선택_제외는_선언한_시나리오에만_걸린다() -> None:
    catalog = load_catalog()
    undecided = [s.id for _p, g in iter_executions(catalog, RunConfig(mode="run")) for s in g]
    closed = RunConfig(mode="run", active_sources=list(CLOSED))
    picked = [s.id for _p, g in iter_executions(catalog, closed) for s in g]
    # 선언한 군(M · plans/125 FS 초안)만 빠진다 — FS 중 폐쇄망 DB 만 요구하는 기권 2건은 선택된다.
    fs_unmet = sorted(s.id for s in catalog.scenarios
                      if s.group == "FS" and not set(s.requires_sources) <= set(CLOSED))
    assert sorted(set(undecided) - set(picked)) == sorted(M_IDS + fs_unmet), \
        "선언 없는 L군 등은 그대로 선택된다"
    assert "FS-21" in picked and "FS-01" not in picked
    local = RunConfig(mode="run", active_sources=["itam", "polestar"])
    assert "M-01" in [s.id for _p, g in iter_executions(catalog, local) for s in g]
    assert not [i for i in source_exclusions(catalog, local) if i["scenario_id"].startswith("M-")]


def test_모의_실행과_판독_불가는_선택_제외를_하지_않는다() -> None:
    catalog = load_catalog()
    for config in (RunConfig(mode="mock", active_sources=["polestar_b0"]), RunConfig(mode="run")):
        assert source_exclusions(catalog, config) == []
        assert "M-01" in [s.id for _p, g in iter_executions(catalog, config) for s in g]


def test_예상치는_선택_제외_예정을_따로_센다() -> None:
    catalog = load_catalog()
    closed = RunConfig(mode="run", groups=["M"], active_sources=["polestar_b0"])
    result = estimate(catalog, closed)
    assert result["scenarios"] == 0 and sorted(result["source_excluded"]) == M_IDS
    assert estimate(catalog, RunConfig(mode="run", groups=["M"]))["source_excluded"] == []


def test_실행은_서버를_띄우기_전에_제외하고_run_json_에_사유를_적재한다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner_mod, "RESULTS_ROOT", tmp_path)
    monkeypatch.setattr(runner_mod, "ServerHandle",
                        lambda **_kw: pytest.fail("전부 제외된 run 에서 서버를 띄웠다"))
    scenario = _scenario({"status": "completed"}, requires_sources=["itam"])
    summary = execute(_catalog(scenario), RunConfig(mode="run", env="closed",
                                                    active_sources=["polestar_cm_gp"]))
    assert summary["executed_turns"] == 0 and summary["profiles"] == []
    run = json.loads((Path(summary["out_dir"]) / "run.json").read_text(encoding="utf-8"))
    assert run["skipped"] == [{
        "scenario_id": "T-01",
        "reason": "요구 소스 비활성 - requires_sources=[itam] · 활성=[polestar_cm_gp]",
        "reason_code": "requires_sources",
    }]


def test_모의_실행은_선언이_있어도_돈다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner_mod, "RESULTS_ROOT", tmp_path)
    scenario = _scenario({"status": "completed"}, env="closed", requires_sources=["itam"],
                         turns=[Turn({"query": "h-run 요구 소스 모의 질의"},
                                     {"status": "completed"})])
    summary = execute(_catalog(scenario), RunConfig(mode="mock", env="closed"))
    assert summary["executed_turns"] == 1
    assert not [s for s in summary["skipped"] if s.get("reason_code") == SOURCE_SKIP_CODE]


def _fixed_sources(monkeypatch: pytest.MonkeyPatch, active: list[str] | None) -> None:
    origin = "ACTIVE_DB_IDS" if active else "판독 불가: 테스트"
    monkeypatch.setattr(cli, "resolve_active_sources", lambda _config: (active, origin))


def test_1단_출력이_선택_제외_예정_건수를_보인다(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    _fixed_sources(monkeypatch, ["polestar_b0"])
    assert cli.main(["--dry-run", "--group", "M"]) == 0
    out = capsys.readouterr().out
    assert re.search(
        r"선택 제외 예정\(requires_sources\): 9건 - 활성 소스 \[polestar_b0\]: M-01", out)

    _fixed_sources(monkeypatch, None)
    assert cli.main(["--dry-run", "--group", "M"]) == 0
    assert "선택 제외(requires_sources): 판정 안 함 - 판독 불가: 테스트" in capsys.readouterr().out


def test_3단_예상치가_선택_제외_예정_건수를_보인다(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    _fixed_sources(monkeypatch, ["polestar_b0"])
    assert cli.main(["--estimate", "--group", "M"]) == 0
    out = capsys.readouterr().out
    assert "시나리오      : 0건" in out and "선택 제외 예정(requires_sources): 9건" in out


def test_4단_실행은_판독한_활성_소스를_그대로_넘긴다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "llm_providers", lambda: ("fabrix", "vllm"))
    monkeypatch.delenv("RUN_E2E", raising=False)
    _fixed_sources(monkeypatch, ["polestar_b0"])
    executed: list[RunConfig] = []

    def fake_execute(_catalog: Catalog, config: RunConfig) -> dict[str, Any]:
        executed.append(config)
        return {"out_dir": str(tmp_path), "executed_turns": 0, "skipped": []}

    monkeypatch.setattr(cli, "execute", fake_execute)
    monkeypatch.setattr(cli, "write_report", lambda *_: {"report": tmp_path / "report.md"})
    monkeypatch.setattr(cli, "analyze", lambda *_: [])
    assert cli.main(["--run", "--group", "M"]) == 0
    assert executed[0].active_sources == ["polestar_b0"], "예상치와 실행이 같은 판독을 본다"
    assert "선택 제외 예정(requires_sources): 9건" in capsys.readouterr().out
