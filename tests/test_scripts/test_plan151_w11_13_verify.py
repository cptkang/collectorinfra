"""plans/151 W11·W12 독립 검증(verifier · 제품 코드 무수정).

- W11: 실 카탈로그 경로(`build_schema_catalog` → 관문 `gate_table_definitions`)에서 판정이
  살아 있는지 ·
  `cmd_run` 배선(콘솔 `[주의]` 1줄 · run 계속) · 경계 · 리포트 값 0 · 기준 SHA 리포트와 비트 비교.
- W12: 정의 시드 `key_columns` 정리와 빌드 산출(`config/db_profiles/itam.yaml`)의 정합(정적 대조).

반출 원본(`results/`)·DB·LLM 을 쓰지 않는다.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from scripts.itam_bench import POLICY_PATH
from scripts.itam_bench import __main__ as cli
from scripts.itam_bench import catalog as cat
from scripts.itam_bench import report as rp

REPO = Path(__file__).resolve().parents[2]
BASE_SHA = "c87df37"
_APPROVED = {"draft_id": "d1", "status": "approved", "created_at": "t", "engine": "mariadb"}
_CANARY = "카나리아코드값Q7Z"


@pytest.fixture(scope="module")
def policy() -> cat.ColumnPolicy:
    return cat.load_policy(POLICY_PATH)


def _schema(p1: dict[str, Any] | None = None) -> dict[str, Any]:
    schema = cat.load_schema_source("transcript")
    if p1 is not None:
        schema["p1"] = dict(p1)
    return schema


def _profile(*, rels: int = 0, codes: bool = False) -> dict[str, Any]:
    profile: dict[str, Any] = {
        "source": "manual",
        "allowed_tables": ["TCDMSIF80"],
        "query_rules": ["규칙 1", "규칙 2", "규칙 3"],
        "relationships": [{"from": "a.x", "to": "b.x", "origin": "p1"}] * rels,
        "table_definitions": {"TCDMSIF80": {"manages": "서버 통합", "origin": "import"}},
    }
    if codes:
        profile["code_values"] = {"TCDMSIF80.자산상태구분": {_CANARY: "라벨", "B": "비"}}
        profile["code_labels"] = {"TCDMSIF80.자산상태구분": {_CANARY: _CANARY}}
    return profile


def _section0(text: str) -> str:
    return text.split("## 0.")[1].split("## 1.")[0]


# --- W11: 실 카탈로그 경로 ---------------------------------------------------------------


class TestRealCatalogPath:
    def test_build_schema_catalog_carries_judgment(self, policy: cat.ColumnPolicy) -> None:
        doc = cat.build_schema_catalog(
            _schema(_APPROVED), policy, assets={}, profile=_profile(), repo_root=REPO
        )
        assert doc["approved_profile"] is not None
        assert rp.p1_assets_missing(doc)
        assert rp.p1_asset_counts(doc) == {
            "relationships": 0,
            "code_value_columns": 0,
            "query_rules": 3,
            "entity_table": None,
        }

    def test_build_schema_catalog_with_assets_no_warning(self, policy: cat.ColumnPolicy) -> None:
        doc = cat.build_schema_catalog(
            _schema(_APPROVED),
            policy,
            assets={},
            profile=_profile(rels=2, codes=True),
            repo_root=REPO,
        )
        assert not rp.p1_assets_missing(doc)
        assert rp.p1_asset_counts(doc)["code_value_columns"] == 1  # type: ignore[index]

    def test_gate_keeps_relationships_and_counts(self, policy: cat.ColumnPolicy) -> None:
        """관문이 정의 행을 전부 빼도 관계·counts 는 남아 판정이 같다.

        리포트는 관문 뒤 카탈로그로 쓴다(`stage_gated`).
        """
        for prof, missing in ((_profile(), True), (_profile(rels=1, codes=True), False)):
            doc = cat.build_schema_catalog(
                _schema(_APPROVED), policy, assets={}, profile=prof, repo_root=REPO
            )
            gated = cat.gate_table_definitions(doc, lambda _text: True)
            assert gated["approved_profile"]["table_definitions"] == {}
            assert rp.p1_asset_counts(gated) == rp.p1_asset_counts(doc)
            assert rp.p1_assets_missing(gated) is missing
            head = _section0(rp.render_report({"run_id": "r"}, gated, []))
            assert "승인 프로필 P1 자산" in head
            assert (rp.P1_ASSETS_MISSING_WARNING in head) is missing

    def test_report_has_counts_only_no_values(self, policy: cat.ColumnPolicy) -> None:
        doc = cat.build_schema_catalog(
            _schema(_APPROVED),
            policy,
            assets={},
            profile=_profile(rels=1, codes=True),
            repo_root=REPO,
        )
        text = rp.render_report({"run_id": "r"}, doc, [])
        assert _CANARY not in text
        assert "| 승인 프로필 P1 자산 | 관계 1 · 코드값 칸 1 · 쿼리 규칙 3 |" in text


# --- W11: cmd_run 배선 ---------------------------------------------------------------------


def _drive_run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    p1: dict[str, Any] | None,
    profile: dict[str, Any] | None,
) -> tuple[int, list[str], dict[str, Any]]:
    said: list[str] = []
    seen: dict[str, Any] = {}
    monkeypatch.setattr(cli, "say", lambda text="": said.append(str(text)))
    monkeypatch.setattr(cli, "preflight", lambda cfg, check_mlx=True: [])
    monkeypatch.setattr("src.config.load_config", lambda: SimpleNamespace())
    schema = _schema(p1)  # 패치 전에 전사본을 읽어 둔다
    monkeypatch.setattr(cat, "load_schema_source", lambda kind, **_kw: schema)
    monkeypatch.setattr(cat, "load_profile", lambda *a, **k: profile)

    def fake_server(
        _args: Any, _cfg: Any, _policy: Any, _sc: Any, catalog_doc: Any, *a: Any, **k: Any
    ) -> int:
        seen["catalog"] = catalog_doc
        seen["said_before_server"] = list(said)
        return 0

    monkeypatch.setattr(cli, "_run_with_server", fake_server)
    code = cli.main(["--run", "--only", "ITAM-02", "--schema-source", "transcript"])
    return code, said, seen


class TestCmdRunWiring:
    def test_console_warning_once_and_run_continues(self, monkeypatch: pytest.MonkeyPatch) -> None:
        code, said, seen = _drive_run(monkeypatch, p1=_APPROVED, profile=_profile(codes=False))
        line = f"[주의] {rp.P1_ASSETS_MISSING_WARNING}"
        assert code == 0
        assert said.count(line) == 1
        # 서버 기동(30분 run) 전에 이미 나왔다
        assert line in seen["said_before_server"]
        assert rp.p1_assets_missing(seen["catalog"])
        assert all(_CANARY not in s for s in said)

    def test_no_console_warning_when_assets_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        code, said, _ = _drive_run(monkeypatch, p1=_APPROVED, profile=_profile(rels=3, codes=True))
        assert code == 0
        assert not any(rp.P1_ASSETS_MISSING_WARNING in s for s in said)
        assert all(_CANARY not in s for s in said)

    @pytest.mark.parametrize(
        "p1,profile",
        [
            (None, _profile()),  # P1 없음
            ({**_APPROVED, "status": "pending"}, _profile()),  # 미승인
            (_APPROVED, None),  # 승인 프로필 없음
        ],
    )
    def test_no_console_warning_boundaries(
        self,
        monkeypatch: pytest.MonkeyPatch,
        p1: dict[str, Any] | None,
        profile: dict[str, Any] | None,
    ) -> None:
        code, said, _ = _drive_run(monkeypatch, p1=p1, profile=profile)
        assert code == 0
        assert not any(rp.P1_ASSETS_MISSING_WARNING in s for s in said)


# --- W11: 경계 ------------------------------------------------------------------------------


def _cat(approved: Any, p1: Any = _APPROVED) -> dict[str, Any]:
    return {"source": "structure_store", "assets": {}, "p1": p1, "approved_profile": approved}


@pytest.mark.parametrize(
    "approved,expected",
    [
        ({"relationships": [], "counts": {}}, True),  # counts 에 code_values 없음
        ({"relationships": []}, True),  # counts 없음
        ({"relationships": None, "counts": None}, True),
        ({"relationships": [], "counts": {"code_values": ["a", "b"]}}, True),  # dict 아님 → 0칸
        ({"relationships": [], "counts": {"code_values": {}}}, True),
        ({"relationships": [{"from": "a"}], "counts": {}}, False),  # 관계만 있음
        ({"relationships": [], "counts": {"code_values": {"t.c": 0}}}, False),  # 코드값만
    ],
)
def test_boundary_shapes(approved: dict[str, Any], expected: bool) -> None:
    assert rp.p1_assets_missing(_cat(approved)) is expected


@pytest.mark.parametrize("status", ["pending", "draft", "rejected", None, "APPROVED"])
def test_boundary_status_other_values(status: Any) -> None:
    assert not rp.p1_assets_missing(_cat({"relationships": []}, {**_APPROVED, "status": status}))


# --- W11: 기준 SHA 리포트와 비트 비교 -----------------------------------------------------------


def _load_base_report(tmp_path: Path) -> Any:
    try:
        src = subprocess.run(
            ["git", "-C", str(REPO), "show", f"{BASE_SHA}:scripts/itam_bench/report.py"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("기준 SHA 를 읽을 수 없다")
    path = tmp_path / "report_base.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("scripts.itam_bench._report_base_verify", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_NEW_ROW = re.compile(r"^\| 승인 프로필 P1 자산 \|.*$\n?", re.M)


@pytest.mark.parametrize("seed", [None, frozenset({"tcdmsif72"})])
def test_report_bit_identical_except_new_lines(tmp_path: Path, seed: Any) -> None:
    base = _load_base_report(tmp_path)
    defs = {"tcdmsif72": {"manages": "가", "origin": "comment"}}
    cases = [
        # P1 없음(정의만) — 새 줄 0 · 완전 동일
        {"source": "x", "assets": {}, "approved_profile": {"table_definitions": defs}},
        # P1 있음 · 자산 있음 — 새 줄 1개만 다르다
        {
            "source": "structure_store",
            "assets": {},
            "p1": _APPROVED,
            "approved_profile": {
                "table_definitions": defs,
                "relationships": [{"from": "a"}],
                "counts": {"code_values": {"t.c": 1}, "query_rules": 2},
                "entity_keys": {"table": "tcdmsif76"},
            },
        },
        # P1 있음 · 자산 0 — 새 줄 + 경고 줄(하향 경고 뒤)
        {
            "source": "structure_store",
            "assets": {},
            "p1": _APPROVED,
            "approved_profile": {"table_definitions": defs, "relationships": [], "counts": {}},
        },
    ]
    for doc in cases:
        old = base.render_report({"run_id": "r"}, doc, [], seed_tables=seed)
        new = rp.render_report({"run_id": "r"}, doc, [], seed_tables=seed)
        warn = f"> 경고 — {rp.P1_ASSETS_MISSING_WARNING}.\n"
        stripped = _NEW_ROW.sub("", new).replace(warn, "")
        if "p1" in doc and rp.p1_assets_missing(doc) and seed is None:
            # 경고 블록이 새로 생기면 앞의 빈 줄 하나도 새로 생긴다
            stripped = stripped.replace("\n\n\n", "\n\n", 1)
        assert stripped == old
        if rp.p1_assets_missing(doc) and seed is not None:
            head = _section0(new)
            assert head.index("> 경고 — closed 시드에") < head.index(warn.strip())


# --- W12: 원천 ↔ 산출 정합(정적) ---------------------------------------------------------------

_TARGET = ["tcdmsif72", "tcdmsif73", "tcdmsif80", *[f"tcdmsif{n}" for n in range(85, 94)]]
_USAGE = re.compile(r"사용[률율량]")


@pytest.fixture(scope="module")
def seed_defs() -> dict[str, Any]:
    path = REPO / "testdata/itam_bench/closed/table_definitions.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))["tables"]


@pytest.fixture(scope="module")
def built_profile() -> dict[str, Any]:
    return yaml.safe_load((REPO / "config/db_profiles/itam.yaml").read_text(encoding="utf-8"))


def test_w12_seed_key_columns(seed_defs: dict[str, Any]) -> None:
    for name in _TARGET:
        cols = seed_defs[name]["key_columns"]
        assert not [c for c in cols if _USAGE.search(c)], name
        assert "사용률 정본 아님" in str(seed_defs[name].get("notes")), name
    assert "담당부점코드" not in seed_defs["tcdmsif72"]["key_columns"]
    assert "담당부점명" in seed_defs["tcdmsif80"]["key_columns"]
    assert "CPU용량" in seed_defs["tcdmsif86"]["key_columns"]
    for n in range(51, 57):
        cols = seed_defs[f"tcdmsif{n}"]["key_columns"]
        assert "장비할당량" in cols and "장비사용량" in cols, n


def test_w12_built_profile_mirrors_seed(
    seed_defs: dict[str, Any], built_profile: dict[str, Any]
) -> None:
    built = built_profile["table_definitions"]
    for name, row in seed_defs.items():
        if name not in built:
            continue
        for field in ("key_columns", "manages", "notes"):
            assert built[name].get(field) == row.get(field), (name, field)


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def test_w12_guide_sentences_in_built_query_guide(built_profile: dict[str, Any]) -> None:
    guide = yaml.safe_load(
        (REPO / "testdata/itam_bench/closed/knowledge/guide.yaml").read_text(encoding="utf-8")
    )
    items = {i["id"]: i for i in guide["items"]}
    qg = _flat(built_profile["query_guide"])
    for gid in ("g07-org-person", "g09-utilization"):
        assert _flat(items[gid]["text"]) in qg, gid
    assert "tcdmsif80`의 `담당부점명`" in items["g07-org-person"]["text"]
    assert "사용량·사용률 칸" not in items["g09-utilization"]["text"]
    assert items["g09-utilization"]["evidence"] == "20261012-092859"


def test_w12_built_profile_scope_unchanged_vs_base(built_profile: dict[str, Any]) -> None:
    try:
        old_src = subprocess.run(
            ["git", "-C", str(REPO), "show", f"{BASE_SHA}:config/db_profiles/itam.yaml"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("기준 SHA 를 읽을 수 없다")
    old = yaml.safe_load(old_src)
    assert set(old) == set(built_profile)
    changed = sorted(k for k in old if old[k] != built_profile[k])
    assert changed == ["query_guide", "table_definitions"]
    changed_tables = sorted(
        t
        for t in old["table_definitions"]
        if old["table_definitions"][t] != built_profile["table_definitions"][t]
    )
    assert changed_tables == sorted(_TARGET)
    for t in changed_tables:
        o, n = old["table_definitions"][t], built_profile["table_definitions"][t]
        assert {k for k in o if o[k] != n.get(k)} == {"key_columns"}, t
