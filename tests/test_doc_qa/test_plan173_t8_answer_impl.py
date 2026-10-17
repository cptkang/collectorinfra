"""`doc_qa.service._answer_impl` 특성화 — 분기별 반환·검색·캐시·LLM 메시지·로그.

plans/173 T8 · 166 I5.

plans/173 W2 T8 특성화 스냅샷 — Extract Function 전에 채록했다. 기대값은 「맞는 값」이 아니라
「지금 값」이다(D-337 ⑤). 리팩터 뒤 스냅샷을 갱신해야 한다면 그 변경은 리팩터가 아니다.

시나리오(`_SCENARIOS`)마다 `_answer_impl`을 한 번 돌려 다음을 기록한다.

- 반환 `DocAnswer` 전 필드(answer·citations·status·reason·diagnostics·raw)
- 가짜 `retrieve_many` 호출(컬렉션 id·질의·키워드 인자) — 재시도 회차 포함
- 가짜 캐시 `get`/`set` 호출(키·값·ttl)
- 가짜 LLM에 넘긴 메시지(클래스명·내용 바이트 그대로)와 `tags`
- `src.*` 로거 레코드(로거명·수준·메시지) — 순서 그대로

실 LLM·외부 호출 0. 시계는 모듈 전역 `time`을 고정값 가짜로 바꿔 ms 값을 결정적으로 만든다.

스냅샷 (재)채록: `UPDATE_PLAN173_T8_SNAPSHOT=1 python -m pytest <이 파일> -q -p no:cacheprovider`
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os
import textwrap
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.clients.fabrix_retrieval import (
    STATUS_BLOCKED_PII,
    STATUS_DISABLED,
    STATUS_EMPTY,
    STATUS_ERROR,
    STATUS_OK,
    STATUS_STALE_ID,
    STATUS_TIMEOUT,
    DocHit,
    RetrievalOutcome,
)
from src.doc_qa import service as svc
from src.infrastructure import doc_sources as ds

SNAPSHOT_PATH = (
    Path(__file__).resolve().parent.parent / "snapshots" / "plan173_t8" / "answer_impl.json"
)
_UPDATE_ENV = "UPDATE_PLAN173_T8_SNAPSHOT"

MANIFEST = """
    version: 1
    collections:
      - id: hq_manual
        title: 본부 전산관리매뉴얼
        description: 규정
        surface_terms: [전산관리매뉴얼, 본부매뉴얼]
      - id: arch_docs
        title: 아키텍처 설계문서
        description: 설계
        surface_terms: [아키텍처, 설계문서]
"""


def _rag(**kw: Any) -> SimpleNamespace:
    rag = SimpleNamespace(
        enabled=True, collections_file="",
        hq_manual_endpoint="http://kb/a", hq_manual_token="tok1234",
        hq_manual_client_key="cli5678", hq_manual_retrieval_id="20260928181958_a",
        arch_docs_endpoint="http://kb/b", arch_docs_token="tok9876",
        arch_docs_client_key="cli4321", arch_docs_retrieval_id="20260929160345_b",
        timeout=12, total_timeout=20, max_collections_per_turn=2,
        max_doc_chars=4000, max_context_chars=24000, answer_max_chars=1200,
        cache_ttl=0, empty_retries=0, doc_url_base="", chat_routing_enabled=False,
    )
    for k, v in kw.items():
        setattr(rag, k, v)
    return rag


def _hit(collection: str = "hq_manual", *, score: float = 0.8,
         content: str = "계정 신청은 부서장이 승인한다.", doc_id: str = "D-1",
         title: str = "본부 전산관리매뉴얼", subtitle: str = "제5장 3절", rank: int = 1) -> DocHit:
    return DocHit(
        collection_id=collection, doc_id=doc_id, title=title, filename="m.pdf",
        subtitle=subtitle, content=content, rank=rank, rank_score=score,
        url="/docs/m.pdf", content_type="CT01", catalog_id="C-1", result_id="r1",
    )


def _ok(cid: str, *hits: DocHit, executed: str = "", raw: dict | None = None) -> RetrievalOutcome:
    return RetrievalOutcome(cid, STATUS_OK, hits=list(hits), executed_query=executed, raw=raw)


class _LLM:
    def __init__(self, text: str = "규정에 따르면 부서장이 승인합니다.",
                 fail: bool = False) -> None:
        self.text, self.fail = text, fail


class _Cache:
    """`get`/`set` 호출을 기록하는 가짜 캐시. `fail_get`/`fail_set`이면 예외를 낸다."""

    def __init__(self, blobs: dict[str, str] | None = None, *, fail_get: bool = False,
                 fail_set: bool = False) -> None:
        self.blobs = dict(blobs or {})
        self.fail_get, self.fail_set = fail_get, fail_set
        self.calls: list[list[Any]] = []

    async def get(self, key: str) -> str | None:
        self.calls.append(["get", key])
        if self.fail_get:
            raise RuntimeError("cache down")
        return self.blobs.get(key)

    async def set(self, key: str, value: str, ttl: int) -> None:
        self.calls.append(["set", key, value, ttl])
        if self.fail_set:
            raise RuntimeError("cache down")


def _blob(*hits: DocHit) -> str:
    return json.dumps([h._asdict() for h in hits], ensure_ascii=False)


def _key(cid: str, query: str) -> str:
    rid = {"hq_manual": "20260928181958_a", "arch_docs": "20260929160345_b"}[cid]
    return svc.cache_key(cid, rid, query)


# 시나리오: (id, 질의, 컬렉션 id 목록, rag 덮어쓰기, 검색 회차별 결과, 호출 키워드, 캐시)
#   검색 결과는 회차마다 `{collection_id: RetrievalOutcome}` — 없는 id는 empty.
def _scenarios() -> list[dict[str, Any]]:
    q = "계정 신청 절차"
    stale = RetrievalOutcome("arch_docs", STATUS_STALE_ID, reason="자산 ID 폐기")
    return [
        {"id": "rag_none", "app_config": SimpleNamespace(rag=None)},
        {"id": "rag_disabled", "rag": {"enabled": False}},
        {"id": "empty_query", "query": "   "},
        {"id": "prefix_only_query_kept", "query": "본부매뉴얼에서", "rounds": [
            {"hq_manual": _ok("hq_manual", _hit())}]},
        {"id": "no_collection_ids", "ids": []},
        {"id": "no_collection_ids_none_usable", "ids": [],
         "rag": {"hq_manual_endpoint": "", "arch_docs_token": ""}},
        {"id": "all_unknown", "ids": ["nope", "zzz"]},
        {"id": "all_blocked", "ids": ["hq_manual", "arch_docs"], "kw": {
            "allowed_collection_ids": ["other"]}},
        {"id": "partly_blocked", "ids": ["hq_manual", "arch_docs"], "kw": {
            "allowed_collection_ids": ["hq_manual"]},
         "rounds": [{"hq_manual": _ok("hq_manual", _hit())}]},
        {"id": "only_unusable", "ids": ["arch_docs"], "rag": {"arch_docs_endpoint": ""}},
        {"id": "mixed_unusable", "ids": ["arch_docs", "hq_manual"],
         "rag": {"arch_docs_endpoint": ""},
         "rounds": [{"hq_manual": _ok("hq_manual", _hit())}]},
        {"id": "limit_truncate", "ids": ["hq_manual", "arch_docs"],
         "rag": {"max_collections_per_turn": 1},
         "rounds": [{"hq_manual": _ok("hq_manual", _hit())}]},
        {"id": "limit_zero_falls_back_2", "ids": ["hq_manual", "arch_docs", "nope"],
         "rag": {"max_collections_per_turn": 0},
         "rounds": [{"hq_manual": _ok("hq_manual", _hit()),
                     "arch_docs": _ok("arch_docs", _hit("arch_docs", score=0.5, doc_id="A-1",
                                                        title="설계서", subtitle=""))}]},
        {"id": "empty_all", "rounds": [{}]},
        {"id": "empty_two_names", "ids": ["hq_manual", "arch_docs"], "rounds": [{}]},
        {"id": "stale_all", "ids": ["arch_docs"], "rounds": [{"arch_docs": stale}]},
        {"id": "blocked_pii", "rounds": [{"hq_manual": RetrievalOutcome(
            "hq_manual", STATUS_BLOCKED_PII, reason="PII 차단")}]},
        {"id": "timeout", "rounds": [{"hq_manual": RetrievalOutcome(
            "hq_manual", STATUS_TIMEOUT, reason="read timeout 12s")}]},
        {"id": "error_no_reason", "rounds": [{"hq_manual": RetrievalOutcome(
            "hq_manual", STATUS_ERROR)}]},
        {"id": "disabled_outcome", "rounds": [{"hq_manual": RetrievalOutcome(
            "hq_manual", STATUS_DISABLED, reason="꺼짐")}]},
        {"id": "priority_stale_over_timeout", "ids": ["hq_manual", "arch_docs"], "rounds": [{
            "hq_manual": RetrievalOutcome("hq_manual", STATUS_TIMEOUT, reason="t"),
            "arch_docs": stale}]},
        {"id": "search_only", "kw": {"search_only": True}, "rounds": [{
            "hq_manual": _ok("hq_manual", _hit(), _hit(doc_id="D-2", subtitle="", rank=2,
                                                       score=0.6))}]},
        {"id": "llm_none", "llm": None, "rounds": [{"hq_manual": _ok("hq_manual", _hit())}]},
        {"id": "llm_fail", "llm": _LLM(fail=True), "rounds": [{
            "hq_manual": _ok("hq_manual", _hit())}]},
        {"id": "ok_basic", "rounds": [{"hq_manual": _ok("hq_manual", _hit())}]},
        {"id": "ok_prefix_stale_hyde_raw", "query": "본부매뉴얼에서 계정 신청 절차",
         "ids": ["hq_manual", "arch_docs"], "kw": {"include_raw": True},
         "rag": {"doc_url_base": "https://docs.example/", "answer_max_chars": 0},
         "rounds": [{"hq_manual": _ok("hq_manual", _hit(), executed="가상 답변 문서",
                                      raw={"r": 1}),
                     "arch_docs": RetrievalOutcome("arch_docs", STATUS_STALE_ID, reason="폐기",
                                                   raw={"r": 2})}]},
        {"id": "ok_truncated_and_dropped",
         "rag": {"max_doc_chars": 10, "max_context_chars": 25},
         "rounds": [{"hq_manual": _ok(
             "hq_manual", _hit(content="가" * 30, score=0.9),
             _hit(content="나" * 30, doc_id="D-2", score=0.7, rank=2),
             _hit(content="다" * 30, doc_id="D-3", score=0.5, rank=3))}]},
        {"id": "retry_recovers", "rag": {"empty_retries": 2},
         "rounds": [{}, {"hq_manual": _ok("hq_manual", _hit())}]},
        {"id": "retry_exhausted", "rag": {"empty_retries": 2}, "rounds": [{}, {}, {}]},
        {"id": "retry_budget_short", "rag": {"empty_retries": 2, "total_timeout": 1,
                                             "timeout": 1}, "rounds": [{}]},
        {"id": "cache_hit_all", "rag": {"cache_ttl": 60},
         "cache": lambda: _Cache({_key("hq_manual", q): _blob(_hit(content="캐시 본문"))})},
        {"id": "cache_partial_and_store", "ids": ["hq_manual", "arch_docs"],
         "rag": {"cache_ttl": 60},
         "cache": lambda: _Cache({_key("hq_manual", q): _blob(_hit(content="캐시 본문"))}),
         "rounds": [{"arch_docs": _ok("arch_docs", _hit("arch_docs", doc_id="A-1",
                                                        title="설계서"))}]},
        {"id": "cache_bad_blob", "rag": {"cache_ttl": 60},
         "cache": lambda: _Cache({_key("hq_manual", q): "not-json"}),
         "rounds": [{"hq_manual": _ok("hq_manual", _hit())}]},
        {"id": "cache_get_set_fail", "rag": {"cache_ttl": 60},
         "cache": lambda: _Cache(fail_get=True, fail_set=True),
         "rounds": [{"hq_manual": _ok("hq_manual", _hit())}]},
        {"id": "cache_ttl_zero_unused",
         "cache": lambda: _Cache({_key("hq_manual", q): _blob(_hit(content="캐시 본문"))}),
         "rounds": [{"hq_manual": _ok("hq_manual", _hit())}]},
        {"id": "audit_context_ignored", "kw": {"audit_context": {"user_id": "u1"}},
         "rounds": [{"hq_manual": _ok("hq_manual", _hit())}]},
    ]


def _jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _jsonable(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _run_one(sc: dict[str, Any], manifest: str, monkeypatch: pytest.MonkeyPatch,
             caplog: pytest.LogCaptureFixture) -> dict[str, Any]:
    searches: list[dict[str, Any]] = []
    rounds: list[dict[str, RetrievalOutcome]] = list(sc.get("rounds", []))
    llm_calls: list[dict[str, Any]] = []

    async def fake_retrieve(collections, query, **kw):
        n = len(searches)
        searches.append({"ids": [c.id for c in collections], "query": query, "kw": kw})
        by_id = rounds[n] if n < len(rounds) else {}
        return [
            dataclasses.replace(by_id[c.id]) if c.id in by_id
            else RetrievalOutcome(c.id, STATUS_EMPTY)
            for c in collections
        ]

    async def fake_astream(llm, messages, **kw):
        llm_calls.append({
            "messages": [[type(m).__name__, m.content] for m in messages],
            "kw": _jsonable(kw),
        })
        if llm.fail:
            raise RuntimeError("boom")
        return llm.text

    monkeypatch.setattr(svc, "retrieve_many", fake_retrieve)
    monkeypatch.setattr(svc, "astream_text", fake_astream)
    monkeypatch.setattr(svc, "time", SimpleNamespace(monotonic=lambda: 1000.0))

    app_config = sc.get("app_config") or SimpleNamespace(rag=_rag(**sc.get("rag", {})))
    cache = sc["cache"]() if "cache" in sc else None
    llm = sc["llm"] if "llm" in sc else _LLM()
    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        out = asyncio.run(svc._answer_impl(
            sc.get("query", "계정 신청 절차"), sc.get("ids", ["hq_manual"]),
            llm=llm, app_config=app_config, cache=cache, collections_path=manifest,
            **sc.get("kw", {}),
        ))
    logs = [
        [r.name, r.levelname, r.getMessage()]
        for r in caplog.records if r.name.startswith("src.")
    ]
    return {
        "result": _jsonable(out),
        "searches": _jsonable(searches),
        "cache": cache.calls if cache is not None else None,
        "llm": llm_calls,
        "logs": logs,
    }


@pytest.fixture()
def manifest(tmp_path):
    path = tmp_path / "rag_collections.yaml"
    path.write_text(textwrap.dedent(MANIFEST), encoding="utf-8")
    ds.load_collection_meta.cache_clear()
    yield str(path)
    ds.load_collection_meta.cache_clear()


def _collect(manifest: str, monkeypatch: pytest.MonkeyPatch,
             caplog: pytest.LogCaptureFixture) -> dict[str, Any]:
    return {sc["id"]: _run_one(sc, manifest, monkeypatch, caplog) for sc in _scenarios()}


def test_answer_impl_matches_snapshot(manifest, monkeypatch, caplog):
    actual = json.loads(json.dumps(_collect(manifest, monkeypatch, caplog), ensure_ascii=False))
    if os.environ.get(_UPDATE_ENV) == "1":
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_text(
            json.dumps(actual, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert sorted(actual) == sorted(expected)
    for sid in expected:
        assert actual[sid] == expected[sid], sid


def test_snapshot_covers_every_status_branch():
    """스냅샷이 7-b 결정적 안내 5종 + 7-a 3종(검색 전용·서술 실패·정상)을 모두 담는다."""
    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    statuses = {v["result"]["status"] for v in expected.values()}
    assert {STATUS_OK, STATUS_EMPTY, STATUS_STALE_ID, STATUS_BLOCKED_PII, STATUS_TIMEOUT,
            STATUS_ERROR, STATUS_DISABLED} <= statuses
    assert any(v["llm"] and v["result"]["status"] == STATUS_ERROR for v in expected.values())
