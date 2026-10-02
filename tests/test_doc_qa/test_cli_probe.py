"""CLI 프로브·접속 반입 도구 계약 (plans/126 §4.17 T-1 · W3).

실 LLM 0 · 외부 호출 0 · `.env` 미접촉(모두 tmp_path).
"""

from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _load(name: str):
    """`scripts/`는 패키지가 아니므로 파일 경로로 적재한다(저장소 관례)."""
    path = PROJECT_ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


probe = _load("rag_probe")
conn = _load("rag_conn")

from src.clients.fabrix_retrieval import (  # noqa: E402
    STATUS_DISABLED,
    STATUS_EMPTY,
    STATUS_OK,
    STATUS_STALE_ID,
    STATUS_TIMEOUT,
)
from src.doc_qa.service import DocAnswer  # noqa: E402
from src.infrastructure import doc_sources as ds  # noqa: E402

MANIFEST = """
    version: 1
    collections:
      - id: hq_manual
        title: 본부 전산관리매뉴얼
        description: 규정
      - id: arch_docs
        title: 아키텍처 설계문서
        description: 설계
"""


@pytest.fixture()
def manifest(tmp_path):
    path = tmp_path / "rag_collections.yaml"
    path.write_text(textwrap.dedent(MANIFEST), encoding="utf-8")
    ds.load_collection_meta.cache_clear()
    yield str(path)
    ds.load_collection_meta.cache_clear()


def _config(manifest_path, **kw):
    rag = SimpleNamespace(
        enabled=True, collections_file=manifest_path,
        hq_manual_endpoint="http://kb/a", hq_manual_token="tok1234",
        hq_manual_client_key="cli5678", hq_manual_retrieval_id="20260928181958_a",
        arch_docs_endpoint="", arch_docs_token="", arch_docs_client_key="",
        arch_docs_retrieval_id="",
        timeout=12, total_timeout=20, max_collections_per_turn=2,
        max_doc_chars=4000, max_context_chars=24000, answer_max_chars=1200,
        cache_ttl=0, doc_url_base="", chat_routing_enabled=False,
    )
    for k, v in kw.items():
        setattr(rag, k, v)
    return SimpleNamespace(rag=rag)


# ── 종료 코드 계약 (docs/32 §6 판독표와 1:1) ─────────────────────────

@pytest.mark.parametrize("status,code", [
    (STATUS_OK, 0), (STATUS_EMPTY, 2), (STATUS_STALE_ID, 3),
    (STATUS_TIMEOUT, 4), (STATUS_DISABLED, 5),
])
def test_exit_codes_map_to_status(monkeypatch, manifest, status, code, capsys):
    monkeypatch.setattr(probe, "load_config", lambda: _config(manifest))

    async def fake(*a, **kw):
        return DocAnswer("답", status=status, diagnostics={"llm_calls": 0})

    monkeypatch.setattr(probe, "answer_from_documents", fake)
    rc = probe.main(["-c", "hq_manual", "-q", "질문", "--search-only"])
    assert rc == code
    assert status in capsys.readouterr().out


def test_missing_collection_is_usage_error(monkeypatch, manifest, capsys):
    """미지정은 전체 검색이 아니라 오류 — 암묵적 라우팅 금지(§4.6)."""
    monkeypatch.setattr(probe, "load_config", lambda: _config(manifest))
    rc = probe.main(["-q", "질문"])
    assert rc == 6
    assert "collection" in capsys.readouterr().err


def test_missing_query_is_usage_error(monkeypatch, manifest):
    monkeypatch.setattr(probe, "load_config", lambda: _config(manifest))
    assert probe.main(["-c", "hq_manual"]) == 6


def test_search_only_does_not_build_llm(monkeypatch, manifest):
    """`--search-only` 는 LLM 을 만들지도 않는다(과금·의존 0)."""
    monkeypatch.setattr(probe, "load_config", lambda: _config(manifest))
    seen = {}

    async def fake(query, collections, **kw):
        seen["llm"] = kw.get("llm")
        seen["search_only"] = kw.get("search_only")
        return DocAnswer("답", status=STATUS_OK, diagnostics={"llm_calls": 0})

    monkeypatch.setattr(probe, "answer_from_documents", fake)
    assert probe.main(["-c", "hq_manual", "-q", "q", "--search-only"]) == 0
    assert seen["llm"] is None and seen["search_only"] is True


def test_list_shows_state_and_masks_secrets(monkeypatch, manifest, capsys):
    monkeypatch.setattr(probe, "load_config", lambda: _config(manifest))
    assert probe.main(["--list"]) == 0
    out = capsys.readouterr().out
    assert "본부 전산관리매뉴얼" in out and "사용 가능" in out
    assert "아키텍처 설계문서" in out and "비활성" in out
    assert "RAG_ARCH_DOCS_ENDPOINT" in out        # 무엇이 비었는지 말한다
    assert "tok1234" not in out and "…1234" in out  # 토큰 원문 미출력


def test_next_step_text_is_actionable(monkeypatch, manifest, capsys):
    monkeypatch.setattr(probe, "load_config", lambda: _config(manifest))

    async def fake(*a, **kw):
        return DocAnswer("문서 색인이 갱신", status=STATUS_STALE_ID,
                         diagnostics={"llm_calls": 0})

    monkeypatch.setattr(probe, "answer_from_documents", fake)
    probe.main(["-c", "hq_manual", "-q", "q", "--search-only"])
    out = capsys.readouterr().out
    assert "rag_conn.py" in out and "docs/32" in out


def test_json_output_is_machine_readable(monkeypatch, manifest, capsys):
    import json
    monkeypatch.setattr(probe, "load_config", lambda: _config(manifest))

    async def fake(*a, **kw):
        return DocAnswer("답", status=STATUS_OK, diagnostics={"llm_calls": 0, "counts": {}})

    monkeypatch.setattr(probe, "answer_from_documents", fake)
    probe.main(["-c", "hq_manual", "-q", "q", "--search-only", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == STATUS_OK and "diagnostics" in payload


# ── 접속 정보 반입 도구 ──────────────────────────────────────────────

class TestParseBlob:
    def test_four_lines_in_order(self):
        got = conn.parse_blob("http://kb/x/retrieval\nTOKENVAL\nCLIENTVAL\n20260928181958_a")
        assert got == {"endpoint": "http://kb/x/retrieval", "token": "TOKENVAL",
                       "client_key": "CLIENTVAL", "retrieval_id": "20260928181958_a"}

    def test_key_value_form(self):
        blob = """
        endpoint = http://kb/x/retrieval
        token: TOKENVAL
        client_key=CLIENTVAL
        retrieval_id=20260928181958_a
        """
        assert conn.parse_blob(textwrap.dedent(blob))["token"] == "TOKENVAL"

    def test_env_key_form_is_accepted(self):
        blob = ("RAG_HQ_MANUAL_ENDPOINT=http://kb/x\nRAG_HQ_MANUAL_TOKEN=T\n"
                "RAG_HQ_MANUAL_CLIENT_KEY=C\nRAG_HQ_MANUAL_RETRIEVAL_ID=20260928181958_a")
        got = conn.parse_blob(blob)
        assert got["client_key"] == "C" and got["retrieval_id"].startswith("2026")

    def test_out_of_order_url_and_asset_id_are_placed_by_shape(self):
        """사람이 순서를 헷갈려도 형태로 알 수 있는 둘은 자리를 바로잡는다."""
        got = conn.parse_blob("20260928181958_a\nTOKENVAL\nhttp://kb/x\nCLIENTVAL")
        assert got["endpoint"] == "http://kb/x"
        assert got["retrieval_id"] == "20260928181958_a"
        assert {got["token"], got["client_key"]} == {"TOKENVAL", "CLIENTVAL"}

    def test_quotes_and_stray_whitespace_are_stripped(self):
        got = conn.parse_blob('  "http://kb/x"  \n  TOKENVAL \n CLIENTVAL \n 20260928181958_a ')
        assert got["endpoint"] == "http://kb/x" and got["token"] == "TOKENVAL"


class TestSetCommand:
    def _env(self, tmp_path, monkeypatch, initial=""):
        env = tmp_path / ".env"
        if initial:
            env.write_text(textwrap.dedent(initial), encoding="utf-8")
        monkeypatch.setattr(conn, "ENV_FILE", env)
        return env

    def _blob(self, tmp_path, text):
        f = tmp_path / "vals.txt"
        f.write_text(textwrap.dedent(text), encoding="utf-8")
        return str(f)

    FULL = """
        http://kb/x/retrieval
        TOKENVAL
        CLIENTVAL
        20260928181958_a
    """

    def test_writes_four_keys_and_masks_output(self, tmp_path, monkeypatch, capsys):
        env = self._env(tmp_path, monkeypatch)
        rc = conn.main(["set", "hq_manual", "--from-file", self._blob(tmp_path, self.FULL)])
        assert rc == 0
        body = env.read_text(encoding="utf-8")
        assert "RAG_HQ_MANUAL_ENDPOINT=http://kb/x/retrieval" in body
        assert "RAG_HQ_MANUAL_TOKEN=TOKENVAL" in body
        assert "RAG_HQ_MANUAL_RETRIEVAL_ID=20260928181958_a" in body
        out = capsys.readouterr().out
        assert "TOKENVAL" not in out and "…NVAL" in out      # 화면에는 마스킹

    def test_partial_values_are_refused(self, tmp_path, monkeypatch, capsys):
        """4종을 함께 교체해야 한다 — 부분 교체는 «다른 자산을 가리키는 조합»이 된다."""
        self._env(tmp_path, monkeypatch)
        rc = conn.main(["set", "hq_manual", "--from-file",
                        self._blob(tmp_path, "http://kb/x\nTOKENVAL")])
        assert rc == 2
        assert "누락" in capsys.readouterr().err

    def test_existing_values_need_force(self, tmp_path, monkeypatch, capsys):
        env = self._env(tmp_path, monkeypatch, """
            RAG_HQ_MANUAL_ENDPOINT=http://old
            RAG_HQ_MANUAL_TOKEN=OLDTOKEN
            RAG_HQ_MANUAL_CLIENT_KEY=OLDCLIENT
            RAG_HQ_MANUAL_RETRIEVAL_ID=20260901000000_old
        """)
        rc = conn.main(["set", "hq_manual", "--from-file", self._blob(tmp_path, self.FULL)])
        assert rc == 3 and "OLDTOKEN" not in capsys.readouterr().out
        assert "OLDTOKEN" in env.read_text(encoding="utf-8")   # 쓰지 않았다

    def test_force_replaces_in_place_and_backs_up(self, tmp_path, monkeypatch):
        env = self._env(tmp_path, monkeypatch, """
            OTHER_KEY=keep-me
            RAG_HQ_MANUAL_ENDPOINT=http://old
            RAG_HQ_MANUAL_TOKEN=OLDTOKEN
            RAG_HQ_MANUAL_CLIENT_KEY=OLDCLIENT
            RAG_HQ_MANUAL_RETRIEVAL_ID=20260901000000_old
        """)
        rc = conn.main(["set", "hq_manual", "--force",
                        "--from-file", self._blob(tmp_path, self.FULL)])
        assert rc == 0
        body = env.read_text(encoding="utf-8")
        assert "OTHER_KEY=keep-me" in body          # 다른 키 불변
        assert "OLDTOKEN" not in body and "TOKENVAL" in body
        assert body.count("RAG_HQ_MANUAL_TOKEN=") == 1   # 제자리 갱신(중복 생성 없음)
        assert list(tmp_path.glob(".env.bak-rag-*"))     # 백업 존재

    def test_commented_key_is_activated(self, tmp_path, monkeypatch):
        env = self._env(tmp_path, monkeypatch, """
            # RAG_HQ_MANUAL_ENDPOINT=
            # RAG_HQ_MANUAL_TOKEN=
            # RAG_HQ_MANUAL_CLIENT_KEY=
            # RAG_HQ_MANUAL_RETRIEVAL_ID=
        """)
        assert conn.main(["set", "hq_manual", "--from-file",
                          self._blob(tmp_path, self.FULL)]) == 0
        body = env.read_text(encoding="utf-8")
        assert "\nRAG_HQ_MANUAL_TOKEN=TOKENVAL" in body
        assert "# RAG_HQ_MANUAL_TOKEN=" not in body

    def test_dry_run_writes_nothing(self, tmp_path, monkeypatch):
        env = self._env(tmp_path, monkeypatch)
        assert conn.main(["set", "hq_manual", "--dry-run",
                          "--from-file", self._blob(tmp_path, self.FULL)]) == 0
        assert not env.exists()

    def test_unknown_collection_is_refused_with_guidance(self, tmp_path, monkeypatch, capsys):
        self._env(tmp_path, monkeypatch)
        rc = conn.main(["set", "nope", "--from-file", self._blob(tmp_path, self.FULL)])
        assert rc == 2
        err = capsys.readouterr().err
        assert "hq_manual" in err and "docs/32" in err

    def test_no_inline_comment_is_produced(self, tmp_path, monkeypatch):
        """`.env` 관례 — 값 뒤 인라인 주석 금지(파싱이 깨진 이력)."""
        env = self._env(tmp_path, monkeypatch)
        conn.main(["set", "hq_manual", "--from-file", self._blob(tmp_path, self.FULL)])
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("RAG_"):
                assert "#" not in line

    def test_show_reports_fill_state(self, tmp_path, monkeypatch, capsys):
        self._env(tmp_path, monkeypatch, """
            RAG_HQ_MANUAL_ENDPOINT=http://kb/x
            RAG_HQ_MANUAL_TOKEN=TOKENVAL
        """)
        assert conn.main(["show"]) == 0
        out = capsys.readouterr().out
        assert "2/4" in out and "TOKENVAL" not in out
