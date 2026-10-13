"""포탈 기능 질의응답 색인 가드 (plans/142 §3.1 · D-131 · D-252).

``src/static/manual/help_index.json`` 은 매뉴얼 원천의 빌드 산출물이다.
매뉴얼을 고치고 빌드를 안 돌리면 ①이 실패한다 — ``python -m scripts.manual.build`` 로 다시 만든다.
LLM·서버 없이 파일만 본다.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "src" / "static" / "manual"
INDEX = OUT / "help_index.json"

sys.path.insert(0, str(REPO))
from scripts.manual import build  # noqa: E402


def _chunks() -> list[dict]:
    return json.loads(INDEX.read_text(encoding="utf-8"))["chunks"]


@pytest.mark.repo_guard
def test_index_matches_sources() -> None:
    """① 커밋된 색인 = 현재 원천으로 다시 만든 결과(바이트 동일)."""
    assert INDEX.read_text(encoding="utf-8") == build.help_index_json(), (
        "help_index.json 이 매뉴얼 원천과 다르다 — python -m scripts.manual.build 로 다시 만든다"
    )


def test_synonym_example_verbatim() -> None:
    """② U-37 절 청크가 사례 입력 문장을 원문 그대로 싣는다."""
    sec = next(c for c in _chunks() if c["id"] == "user:U-37")
    assert sec["kind"] == "section" and sec["anchor"] == "u-37"
    assert "hostname에 '장비이름' 유사 단어를 추가해줘" in sec["examples"]
    assert "(항목)에 '(낱말)' 유사 단어를 추가해줘" in sec["input_forms"]


def test_noise_item_chunk() -> None:
    """③ U-44 아래 항목 「수신 알람」 청크가 있다."""
    hits = [
        c
        for c in _chunks()
        if c["kind"] == "item" and c["section_id"] == "U-44" and c["item"] == "수신 알람"
    ]
    assert len(hits) == 1
    assert hits[0]["section_title"] == "노이즈 관제 화면 읽기" and hits[0]["text"]


def _examples(chunks: list[dict]) -> set[str]:
    return {q for c in chunks for q in c["examples"]}


def test_unpublished_cases_not_indexed() -> None:
    """④ wrong 판정·미게재 사례 문장은 어떤 청크에도 없다(커밋본 전수)."""
    published = _examples(_chunks())
    for path in build.SAMPLES.glob("*.json"):
        doc = json.loads(path.read_text(encoding="utf-8"))
        verdict = (doc.get("review") or {}).get("verdict")
        if doc.get("reviewed") is True and verdict in ("ok", "partly"):
            continue
        queries = {t["query"] for t in doc["turns"]}
        assert not queries & published, f"{path.stem}: 미게재 사례가 색인에 실렸다"


@pytest.mark.parametrize("change", ["wrong", "unreviewed"])
def test_verdict_change_drops_examples(
    change: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """④ 사례 판정을 wrong·미판정으로 바꿔 다시 만들면 그 사례 문장이 빠진다."""
    samples = tmp_path / "samples"
    shutil.copytree(build.SAMPLES, samples)
    path = samples / "Q-SYNONYM.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    if change == "wrong":
        doc["review"]["verdict"] = "wrong"
    else:
        doc["reviewed"] = False
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(build, "SAMPLES", samples)
    chunks = build._index_chunks("user")
    examples = _examples(chunks)
    for turn in doc["turns"]:
        assert turn["query"] not in examples
    assert next(c for c in chunks if c["id"] == "user:U-37")["examples"] == []
    assert "전체 서버의 호스트명, OS종류, 벤더를 조회해줘" in examples  # 다른 게재 사례는 그대로


def test_anchors_exist_in_html() -> None:
    """⑤ 모든 청크의 앵커가 해당 권의 HTML 에 실존한다."""
    ids = {
        m: set(re.findall(r'id="([^"]+)"', (OUT / f"{m}.html").read_text(encoding="utf-8")))
        for m in ("user", "admin")
    }
    missing = [c["id"] for c in _chunks() if c["anchor"] not in ids[c["manual"]]]
    assert not missing, f"HTML 에 없는 앵커: {missing[:10]}"


def test_chunk_ids_unique() -> None:
    ids = [c["id"] for c in _chunks()]
    assert len(ids) == len(set(ids))
