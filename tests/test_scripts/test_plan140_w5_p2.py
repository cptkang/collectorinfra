"""plans/140 W5 — 외부망 자산 빌더 P2 경로(`--build-assets --p2`) · D-311 ②③ · D-127.

가짜 LLM(정해진 문자열 응답)과 가짜 DB 클라이언트로 P2 조각(`run_asset_llm`과 같은 요약 → 예시·섹션
초안 → 결정적 검증·실행)이 실행 성공분만 남기는지, 치환 코드값 리터럴이 들어간 예시는 빼고 섹션은
통째로 버리는지, 근거 0이면 키·파일을 만들지 않는지, 과금 평면이면 거부하는지, 출력에 값이 나오지
않는지, `p2=False`면 W3 산출과 바이트 동일한지 본다. 1회차 실반출이 있으면 끝까지 돌려 본다.

MLX·과금 API·실 DB·Redis 0. 저장소 루트는 tmp(실반출은 읽기 전용으로만 읽는다).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from scripts.itam_bench import REPO_ROOT
from scripts.itam_bench import __main__ as cli
from scripts.itam_bench import build_assets as ba
from src.db_adapters.generated import GeneratedTemplateAdapter

_REAL_RUN = REPO_ROOT / "results" / "itam_bench" / "20261006-152938"
_SUBSTITUTE = "QZ7K"
_LABEL = "갸뇨"
_ORIGINAL_ENUM = "A=사용,B=중지"  # DB 주석의 원 코드 열거(요약에만 · 출력 금지)
_STAMP = "2026-11-01T00:00:00+0900"

_OK_EXAMPLE = {"question": "서버 호스트 목록", "sql": "SELECT host_name FROM t_srv LIMIT 10"}
_SUB_EXAMPLE = {
    "question": "상태별 앱",
    "sql": f"SELECT app_id FROM t_app WHERE app_st = '{_SUBSTITUTE}' LIMIT 10",
}
_FAIL_EXAMPLE = {"question": "깨진 질의", "sql": "SELECT broken_col FROM t_app LIMIT 10"}
_SECTION = (
    "- 서버는 `t_srv`의 `host_name`으로 찾는다.\n"
    "- 앱과 서버는 `srv_id`로 잇는다.\n\n"
    "```sql\nSELECT host_name FROM t_srv LIMIT 10\n```\n"
)
_SECTION_SUB = _SECTION + f"\n- 사용 중 상태는 `app_st` = '{_SUBSTITUTE}' 이다.\n"


# ──────────────────────────────────────────────
# 합성 반출 · 저장소
# ──────────────────────────────────────────────


def _col(name: str, comment: str | None = None) -> dict[str, Any]:
    return {
        "name": name, "type": "varchar", "nullable": True,
        "meaning": comment, "meaning_source": "db_comment" if comment else "none",
    }


def _catalog() -> dict[str, Any]:
    return {
        "db_id": "itam", "source": "structure_store",
        "approved_profile": {
            "source": "manual", "allowed_tables": ["t_srv", "t_app", "tcdmsif81"],
        },
        "tables": {
            "t_srv": {"key": ["srv_id"], "rows_estimate": 100, "relations": [],
                      "meaning": "서버 원장", "meaning_source": "db_comment",
                      "columns": [_col("srv_id"), _col("host_name", "서버 호스트명")]},
            "t_app": {"key": [], "rows_estimate": 50, "relations": [],
                      "columns": [_col("app_id"), _col("srv_id"),
                                  _col("app_st", f"앱 상태({_ORIGINAL_ENUM})")]},
            "tcdmsif81": {"key": [], "rows_estimate": None, "relations": [],
                          "columns": [_col("usr_id"), _col("pwd")]},
        },
    }


_SAMPLES = {"columns": {"t_app.app_st": {
    "distinct": 2, "substitution": "ok", "values": [_SUBSTITUTE, "WX2P"],
    "labels": [[_SUBSTITUTE, _LABEL]],
}}}

_SEED = {"db_id": "itam", "tables": {
    "t_srv": {"kind": "현행", "manages": "서버 원장(시드)"},
    "t_app": {"kind": "현행", "manages": "앱 원장(시드)"},
    "tcdmsif81": {"kind": "설정", "manages": "계정(시드)"},
}}


def _repo(tmp_path: Path, seed: Any = None) -> Path:
    repo = tmp_path / "repo"
    path = repo / ba.SEED_DEFINITIONS_REL
    path.parent.mkdir(parents=True)
    if isinstance(seed, Path):
        path.write_bytes(seed.read_bytes())
    else:
        path.write_text(yaml.safe_dump(seed or _SEED, allow_unicode=True), encoding="utf-8")
    return repo


def _run_dir(tmp_path: Path, samples: dict[str, Any] | None = _SAMPLES) -> Path:
    run = tmp_path / "results" / "20261101-000000"
    run.mkdir(parents=True)
    (run / ba.CATALOG_FILE).write_text(yaml.safe_dump(_catalog(), allow_unicode=True),
                                       encoding="utf-8")
    if samples is not None:
        (run / ba.CODE_SAMPLES_FILE).write_text(yaml.safe_dump(samples, allow_unicode=True),
                                                encoding="utf-8")
    return run


# ──────────────────────────────────────────────
# 가짜 LLM · DB
# ──────────────────────────────────────────────


class FakeLLM:
    """호출 순서대로 정해진 문자열을 돌려준다(예시 → 섹션 — `build_p2` 호출 순서)."""

    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    async def ainvoke(self, messages: Any) -> Any:
        self.prompts.append(str(messages[0].content))
        return SimpleNamespace(content=self.responses[len(self.prompts) - 1])


class FakeClient:
    """`broken_col`이 들어간 SQL만 실패한다."""

    def __init__(self, healthy: bool = True) -> None:
        self.healthy = healthy
        self.executed: list[str] = []

    async def health_check(self) -> bool:
        return self.healthy

    async def execute_sql(self, sql: str) -> Any:
        self.executed.append(sql)
        if "broken_col" in sql:
            raise RuntimeError("Unknown column")
        return SimpleNamespace(rows=[{"x": 1}])


class RecordingChecker:
    """가짜 SQL 검증기 — 받은 스키마를 기록하고 스키마 밖 테이블을 거절한다."""

    def __init__(self) -> None:
        self.schemas: list[Mapping[str, Any]] = []

    def __call__(self, sql: str, schema: Mapping[str, Any], engine: str, db_id: str) -> list[str]:
        self.schemas.append(schema)
        tables = set(schema.get("tables") or {})
        word = sql.split(" FROM ", 1)[1].split()[0] if " FROM " in sql else ""
        return [] if word in tables else [f"없는 테이블: {word}"]


def _examples(*items: Mapping[str, str]) -> str:
    return json.dumps(list(items), ensure_ascii=False)


def _deps(llm: FakeLLM, client: FakeClient | None = None,
          checker: RecordingChecker | None = None) -> ba.P2Deps:
    target = client or FakeClient()

    @asynccontextmanager
    async def factory() -> AsyncIterator[FakeClient]:
        yield target

    return ba.P2Deps(llm_factory=lambda: llm, client_factory=factory,
                     sql_checker=checker or RecordingChecker())


def _p2(llm: FakeLLM, *, code_values: set[str] | None = None,
        checker: RecordingChecker | None = None) -> dict[str, Any]:
    return asyncio.run(ba.build_p2(
        _catalog(), llm=llm, client=FakeClient(), sql_checker=checker or RecordingChecker(),
        code_values=ba.substituted_values(_SAMPLES) if code_values is None else code_values,
    ))


def _stable(files: Mapping[str, str]) -> dict[str, str]:
    """스키마 캐시의 저장 시각 키만 지운 산출물(나머지는 바이트 그대로)."""
    out = dict(files)
    rel = str(ba.SCHEMA_SEED_REL)
    if rel in out:
        data = json.loads(out[rel])
        for key in ("_cached_at", "_cached_at_iso"):
            data.pop(key, None)
        out[rel] = json.dumps(data, ensure_ascii=False, sort_keys=True)
    return out


@pytest.fixture
def fixed_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ba.time, "strftime", lambda fmt, *a: _STAMP)


# ──────────────────────────────────────────────
# build_p2 (순수에 가까운 조각)
# ──────────────────────────────────────────────


class TestBuildP2:
    def test_keeps_only_clean_executed_example(self):
        llm = FakeLLM(_examples(_OK_EXAMPLE, _SUB_EXAMPLE, _FAIL_EXAMPLE), _SECTION)
        result = _p2(llm)
        assert result["query_examples"] == [_OK_EXAMPLE]
        assert result["excluded_substituted"] == 1
        assert result["section"] == _SECTION.strip()
        assert result["section_substituted"] is False
        checks = result["checks"]["query_examples"]
        assert [c["error"] is None for c in checks] == [True, True, False]

    def test_section_with_substituted_literal_dropped(self):
        result = _p2(FakeLLM(_examples(_OK_EXAMPLE), _SECTION_SUB))
        assert result["checks"]["prompt_template"]["passed"] is True
        assert result["section"] is None and result["section_substituted"] is True

    def test_first_export_has_no_block_set(self):
        """1회차(`code_samples.yaml` 없음) — 차단 집합이 비어 실행 성공분이 다 남는다."""
        result = _p2(FakeLLM(_examples(_OK_EXAMPLE, _SUB_EXAMPLE), _SECTION_SUB), code_values=set())
        assert result["query_examples"] == [_OK_EXAMPLE, _SUB_EXAMPLE]
        assert result["excluded_substituted"] == 0 and result["section"]

    def test_prompts_use_evidence_and_comments_without_substitutes(self):
        llm = FakeLLM(_examples(_OK_EXAMPLE), _SECTION)
        _p2(llm)
        assert len(llm.prompts) == 2
        for prompt in llm.prompts:
            assert "`t_srv` — 서버 원장" in prompt and "서버 호스트명" in prompt
            assert "LIMIT 50" in prompt
            assert _SUBSTITUTE not in prompt and _LABEL not in prompt
            assert "tcdmsif81" not in prompt  # 기본 제외 테이블은 요약에 없다

    def test_excluded_table_not_in_checker_schema(self):
        checker = RecordingChecker()
        bad = {"question": "계정", "sql": "SELECT usr_id FROM tcdmsif81 LIMIT 10"}
        result = _p2(FakeLLM(_examples(bad, _OK_EXAMPLE), _SECTION), checker=checker)
        assert result["query_examples"] == [_OK_EXAMPLE]
        assert all("tcdmsif81" not in (s.get("tables") or {}) for s in checker.schemas)

    def test_garbage_llm_gives_no_evidence(self):
        result = _p2(FakeLLM("설명뿐", ""))
        assert result["query_examples"] == [] and result["section"] is None


# ──────────────────────────────────────────────
# run_build(p2=True) 쓰기
# ──────────────────────────────────────────────


class TestRunBuildP2:
    def test_writes_examples_section_and_header(self, tmp_path, fixed_time, capsys):
        repo = _repo(tmp_path)
        llm = FakeLLM(_examples(_OK_EXAMPLE, _SUB_EXAMPLE, _FAIL_EXAMPLE), _SECTION)
        code = ba.run_build(_run_dir(tmp_path), repo_root=repo, p2=True, p2_deps=_deps(llm))
        assert code == ba.EXIT_OK
        text = (repo / ba.PROFILE_REL).read_text(encoding="utf-8")
        assert yaml.safe_load(text)["query_examples"] == [_OK_EXAMPLE]
        assert (
            "#   query_examples     P2 LLM 초안(모의 DB 실행 성공분 · 치환 코드 리터럴 제외 1건)"
            in text
        )
        assert (
            "#   prompt_template    P2 LLM 초안(구조 검사·섹션 SQL 모의 DB 실행 통과) → "
            f"{ba.SECTION_REL}" in text
        )
        section_text = (repo / ba.SECTION_REL).read_text(encoding="utf-8")
        assert section_text.startswith("# plans/140 W5")
        data = yaml.safe_load(section_text)
        assert data["db_id"] == "itam" and data["section"] == _SECTION.strip()
        assert "environment" not in data
        # 질의 경로 생성 템플릿 어댑터가 그대로 읽는다(같은 경로·형식)
        assert GeneratedTemplateAdapter(root=repo).section("itam") == _SECTION.strip()
        out = capsys.readouterr().out
        assert "후보 3 · 실행 성공 2 · 치환 코드 리터럴 제외 1 · 씀 1" in out

    def test_zero_evidence_no_key_no_file_and_bytes_equal_w3(self, tmp_path, fixed_time):
        run = _run_dir(tmp_path)
        repo_a, repo_b = _repo(tmp_path / "a"), _repo(tmp_path / "b")
        assert ba.run_build(run, repo_root=repo_a) == ba.EXIT_OK
        llm = FakeLLM(_examples(_FAIL_EXAMPLE, _SUB_EXAMPLE), _SECTION_SUB)
        assert ba.run_build(run, repo_root=repo_b, p2=True, p2_deps=_deps(llm)) == ba.EXIT_OK
        profile_b = (repo_b / ba.PROFILE_REL).read_text(encoding="utf-8")
        assert "query_examples" not in profile_b and "prompt_template" not in profile_b
        assert not (repo_b / ba.SECTION_REL).exists()
        assert profile_b == (repo_a / ba.PROFILE_REL).read_text(encoding="utf-8")

    def test_section_substituted_not_written(self, tmp_path, fixed_time, capsys):
        repo = _repo(tmp_path)
        llm = FakeLLM(_examples(_OK_EXAMPLE), _SECTION_SUB)
        assert ba.run_build(_run_dir(tmp_path), repo_root=repo, p2=True,
                            p2_deps=_deps(llm)) == ba.EXIT_OK
        assert not (repo / ba.SECTION_REL).exists()
        assert "통째로 버림" in capsys.readouterr().out

    def test_stdout_has_no_values(self, tmp_path, fixed_time, capsys):
        repo = _repo(tmp_path)
        llm = FakeLLM(_examples(_OK_EXAMPLE, _SUB_EXAMPLE, _FAIL_EXAMPLE), _SECTION_SUB)
        ba.run_build(_run_dir(tmp_path), repo_root=repo, p2=True, p2_deps=_deps(llm))
        out = capsys.readouterr().out
        for secret in (_SUBSTITUTE, "WX2P", _LABEL, _ORIGINAL_ENUM, "서버 호스트명",
                       _OK_EXAMPLE["sql"], _OK_EXAMPLE["question"], _SUB_EXAMPLE["question"],
                       "broken_col", "Unknown column", "host_name"):
            assert secret not in out

    def test_final_guard_covers_section_file(self, tmp_path, fixed_time):
        """build_p2를 건너뛴 결과가 와도 쓰기 직전 검사가 섹션 파일 리터럴을 막는다."""
        run = _run_dir(tmp_path)
        p2_result = {"query_examples": [], "section": _SECTION_SUB.strip(),
                     "excluded_substituted": 0}
        with pytest.raises(ba.BuildError) as err:
            ba.build(run, repo_root=_repo(tmp_path), p2_result=p2_result)
        assert str(ba.SECTION_REL) in str(err.value) and _SUBSTITUTE not in str(err.value)

    def test_p2_false_same_as_build(self, tmp_path, fixed_time):
        run = _run_dir(tmp_path)
        repo = _repo(tmp_path)
        expected = _stable(ba.build(run, repo_root=repo, generated_at=_STAMP)["files"])
        assert _stable(
            ba.build(run, repo_root=repo, generated_at=_STAMP, p2_result=None)["files"]
        ) == expected
        assert ba.run_build(run, repo_root=repo) == ba.EXIT_OK
        written = {rel: (repo / rel).read_text(encoding="utf-8") for rel in expected}
        assert _stable(written) == expected
        assert not (repo / ba.SECTION_REL).exists()


class TestConnection:
    def test_connect_failure_exit_2(self, tmp_path, capsys):
        repo = _repo(tmp_path)

        @asynccontextmanager
        async def broken() -> AsyncIterator[Any]:
            raise ConnectionRefusedError("127.0.0.1:3308")
            yield  # pragma: no cover

        llm = FakeLLM("[]", "")
        deps = ba.P2Deps(llm_factory=lambda: llm, client_factory=broken, sql_checker=None)
        assert ba.run_build(_run_dir(tmp_path), repo_root=repo, p2=True,
                            p2_deps=deps) == ba.EXIT_INPUT
        out = capsys.readouterr().out
        assert "연결 실패" in out and "ConnectionRefusedError" in out
        assert llm.prompts == []
        assert not (repo / ba.PROFILE_REL).exists()

    def test_unhealthy_exit_2(self, tmp_path):
        repo = _repo(tmp_path)
        llm = FakeLLM("[]", "")
        code = ba.run_build(_run_dir(tmp_path), repo_root=repo, p2=True,
                            p2_deps=_deps(llm, FakeClient(healthy=False)))
        assert code == ba.EXIT_INPUT and llm.prompts == []
        assert not (repo / ba.PROFILE_REL).exists()

    def test_w3_refusal_precedes_llm(self, tmp_path):
        """정의 검증 오류면 LLM을 부르기 전에 거부한다."""
        seed = {"db_id": "itam", "tables": {"t_srv": {"kind": "없는종류", "manages": "x"}}}
        repo = _repo(tmp_path, seed)
        llm = FakeLLM("[]", "")
        assert ba.run_build(_run_dir(tmp_path), repo_root=repo, p2=True,
                            p2_deps=_deps(llm)) == ba.EXIT_REFUSED
        assert llm.prompts == []


# ──────────────────────────────────────────────
# CLI · 과금 평면 거부
# ──────────────────────────────────────────────


def _cfg(worker: str, orchestrator: str) -> Any:
    return SimpleNamespace(llm=SimpleNamespace(provider=worker),
                           orchestrator=SimpleNamespace(provider=orchestrator))


class TestBillingGate:
    @pytest.mark.parametrize("planes", [("gemini", "mlx"), ("mlx", "gemini"), ("", "mlx")])
    def test_billable_plane_refused(self, tmp_path, monkeypatch, capsys, planes):
        def no_deps(cfg: Any) -> Any:
            raise AssertionError("과금 평면에서 의존성(LLM)을 만들면 안 된다")

        monkeypatch.setattr(ba, "default_p2_deps", no_deps)
        monkeypatch.setattr(ba, "run_build", lambda *a, **k: pytest.fail("빌드가 돌면 안 된다"))
        args = cli.build_parser().parse_args(["--build-assets", str(tmp_path), "--p2"])
        assert cli.cmd_build_assets(args, cfg=_cfg(*planes)) == 1
        assert "과금 평면" in capsys.readouterr().out

    def test_local_planes_pass(self):
        assert ba.p2_billing_refusal(_cfg("mlx", "mlx")) is None
        assert ba.p2_billing_refusal(_cfg("fabrix", "vllm")) is None

    def test_p2_without_build_assets(self, capsys):
        assert cli.main(["--p2"]) == 1
        assert "--build-assets" in capsys.readouterr().out

    def test_p2_flag_passed_to_run_build(self, tmp_path, monkeypatch):
        seen: dict[str, Any] = {}
        sentinel = object()
        monkeypatch.setattr(ba, "default_p2_deps", lambda cfg: sentinel)
        monkeypatch.setattr(ba, "run_build", lambda run_dir, **kw: seen.update(kw) or 0)
        args = cli.build_parser().parse_args(["--build-assets", str(tmp_path), "--p2"])
        assert cli.cmd_build_assets(args, cfg=_cfg("mlx", "mlx")) == 0
        assert seen["p2"] is True and seen["p2_deps"] is sentinel


# ──────────────────────────────────────────────
# 1회차 실반출 — 가짜 LLM으로 끝까지
# ──────────────────────────────────────────────


@pytest.mark.skipif(
    not (_REAL_RUN / ba.CATALOG_FILE).is_file(), reason="1회차 반출물 없음(외부망 로컬 전용)"
)
def test_first_export_p2_end_to_end(tmp_path, fixed_time, capsys):
    catalog, samples = ba.load_export(_REAL_RUN)
    assert samples is None  # 1회차 — 치환 코드값 파일 없음
    allowed, _ = ba.allowed_tables(catalog)
    table = allowed[0]
    example = {"question": "목록", "sql": f"SELECT * FROM {table} LIMIT 10"}
    section = f"- 원장은 `{table}`에 있다.\n\n```sql\nSELECT * FROM {table} LIMIT 10\n```"
    repo = _repo(tmp_path, REPO_ROOT / ba.SEED_DEFINITIONS_REL)
    llm = FakeLLM(_examples(example, _FAIL_EXAMPLE), section)
    client = FakeClient()
    code = ba.run_build(_REAL_RUN, repo_root=repo, p2=True, p2_deps=_deps(llm, client))
    assert code == ba.EXIT_OK
    profile = yaml.safe_load((repo / ba.PROFILE_REL).read_text(encoding="utf-8"))
    assert profile["query_examples"] == [example]
    assert len(profile["allowed_tables"]) == 98
    assert yaml.safe_load((repo / ba.SECTION_REL).read_text(encoding="utf-8"))["section"] == section
    assert len(client.executed) == 2  # 예시 1 + 섹션 블록 1(broken_col은 검증기에서 거절)
    assert "tcdmsif81" not in llm.prompts[0]
