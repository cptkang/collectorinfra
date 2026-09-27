"""매뉴얼 "모든 기능" 가드 (plans/116 §4.6 · D-252).

playwright·LLM·서버 없이 파일만 본다. 실패는 대개 둘 중 하나다.
- UI 가 바뀌었는데 매뉴얼이 따라오지 않았다 → 매뉴얼 절·캡처를 고친다
  (재생성: ``python -m scripts.manual.capture`` → ``python -m scripts.manual.build``)
- 새 버튼·탭이 생겼는데 manifest 에 없다 → ``scripts/manual/features.yaml`` 에 항목을 더한다
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
MANUAL = REPO / "scripts" / "manual"
STATIC = REPO / "src" / "static"
OUT = STATIC / "manual"

FEATURES = yaml.safe_load((MANUAL / "features.yaml").read_text(encoding="utf-8"))
CASES = {
    c["id"]: c for c in yaml.safe_load((MANUAL / "cases.yaml").read_text(encoding="utf-8"))["cases"]
}
CAPTURES = {
    c["id"]: c
    for c in yaml.safe_load((MANUAL / "captures.yaml").read_text(encoding="utf-8"))["captures"]
}

# 역방향 감시 대상 화면 — 버튼 id 와 탭 값
SCREENS = [
    "index.html",
    "login.html",
    "register.html",
    "noise.html",
    "admin/login.html",
    "admin/dashboard.html",
    "admin/noise.html",
]
_SLOTS_COMMON = ("what", "how", "ui", "caution", "related")


def _items(manual: str) -> list[dict]:
    return FEATURES[manual]


def _all_items() -> list[tuple[str, dict]]:
    return [("user", i) for i in _items("user")] + [("admin", i) for i in _items("admin")]


def _html(manual: str) -> str:
    return (OUT / f"{manual}.html").read_text(encoding="utf-8")


def _section(html: str, fid: str) -> str:
    m = re.search(rf'<section class="feature" id="{fid.lower()}">(.*?)</section>', html, re.S)
    assert m, f"{fid}: 매뉴얼 절 없음"
    return m.group(1)


def _anchor_present(anchor: str, texts: list[str]) -> bool:
    if anchor.startswith("#"):
        ident = anchor[1:]
        pat = re.compile(
            rf'id=["\']{re.escape(ident)}["\']|getElementById\(["\']{re.escape(ident)}["\']\)'
        )
        return any(pat.search(t) for t in texts)
    return any(anchor in t for t in texts)


@pytest.mark.parametrize(
    "manual,item", _all_items(), ids=lambda v: v["id"] if isinstance(v, dict) else v
)
def test_ui_anchor_exists(manual: str, item: dict) -> None:
    """③ 항목이 가리키는 화면 요소가 코드에 실존한다."""
    texts = [(REPO / f).read_text(encoding="utf-8") for f in item["files"]]
    missing = [a for a in item["anchors"] if not _anchor_present(a, texts)]
    assert not missing, f"{item['id']}: 화면 요소가 사라졌다 {missing} — 매뉴얼을 갱신하라"


@pytest.mark.parametrize(
    "manual,item", _all_items(), ids=lambda v: v["id"] if isinstance(v, dict) else v
)
def test_section_has_all_slots(manual: str, item: dict) -> None:
    """① 항목마다 매뉴얼 절이 있고 설명 칸이 비어 있지 않다."""
    sec = _section(_html(manual), item["id"])
    slots = _SLOTS_COMMON
    for slot in slots:
        m = re.search(rf'data-slot="{slot}"[^>]*>(.*?)<!--/slot-->', sec, re.S)
        assert m and re.sub(r"<[^>]+>|\s", "", m.group(1)), f"{item['id']}: '{slot}' 칸이 비었다"


# ── 상세 설명(plans/116 §11 · R8 — 옛 이름 「자세히 보기」) ──────────────────
# 대상은 manifest 에 detail_src(근거 파일)가 있는 항목이다. 전개 중이라 대상 목록으로 한정하고,
# D2 끝에 「전 항목 detail_src 필수」로 바꾼다(§11.5 — 가드를 끄는 대신 대상을 좁혔다).
_DETAIL_TOPICS = {
    "user": {
        "이렇게 동작합니다",
        "단계별로 자세히",
        "상황별 결과",
        "입력 규칙과 한도",
        "이럴 땐 이렇게",
        "자주 묻는 질문",
        "더 알아보기",
        "항목별 설명",
    },
    "admin": {
        "동작 원리",
        "항목 전수",
        "반영 시점과 영향",
        "권한과 보안",
        "문제 해결",
        "근거와 참고 문서",
    },
}
# 사용자 매뉴얼에 쓰지 않는 내부 어휘(§4.3 문체) — 결정 번호·계획서·소스 경로·설정 키·코드 식별자
_INTERNAL_WORDS = re.compile(
    r"\bD-\d{3}\b|plans/|\bsrc/|\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b|\b[a-z]+_[a-z_]+\b"
)
_DETAIL_RE = re.compile(r'<div class="slot slot-detail"[^>]*>(.*?)<!--/slot--></div>', re.S)
_TOPIC_RE = re.compile(r'<h5 class="detail-topic-title">(.*?)</h5>')
_TERM_LEAD_RE = re.compile(r'<span class="term-lead"[^>]*>.*?</span>', re.S)
_TERM_LINK_RE = re.compile(r'<a class="term" href="#([^"]+)" data-term="([^"]+)">')
_ITEM_SPLIT = re.compile(r'(?=<div class="detail-item" id=)')
_ITEM_HEAD = re.compile(
    r'<h6 class="detail-item-title">(.*?)</h6><div class="detail-item-lead">(.*?)</div>', re.S
)


def _detail_item_parts(body: str) -> list[tuple[str, str, str]]:
    """상세 칸의 항목마다 (제목, 본문에 보이는 일반 설명, 항목 전체 글)."""
    out = []
    for chunk in _ITEM_SPLIT.split(body)[1:]:
        m = _ITEM_HEAD.search(chunk)
        if m:
            out.append((_text(m.group(1)).strip(), _text(m.group(2)).strip(), _text(chunk)))
    return out


def _detail_items() -> list[tuple[str, dict]]:
    return [(m, i) for m, i in _all_items() if i.get("detail_src")]


def _text(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", fragment))


def _sentences(fragment: str) -> list[str]:
    """블록 요소·문장 끝으로 나눈 문장(공백·문장부호를 뺀 정규형) — 짧은 조각은 뺀다."""
    out = []
    for block in re.split(r"</(?:p|li|td|th|summary)>", fragment):
        for sent in re.split(r"(?<=[.!?])\s+", _text(block)):
            norm = re.sub(r"[\s.,·:;!?()「」『』\"'`—-]", "", sent)
            if len(norm) >= 20:
                out.append(norm)
    return out


def test_details_start_closed() -> None:
    """V-12 — 처음 열면 자세히 보기가 전부 접혀 있다(open 속성 0)."""
    for manual in ("user", "admin"):
        opened = re.findall(r"<details\b[^>]*\bopen\b", _html(manual))
        assert not opened, f"{manual}.html 에 펼쳐진 채 시작하는 상세가 있다: {opened[:3]}"


@pytest.mark.parametrize(
    "manual,item", _all_items(), ids=lambda v: v["id"] if isinstance(v, dict) else v
)
def test_detail_matches_manifest(manual: str, item: dict) -> None:
    """자세히 보기가 있는 절과 manifest detail_src 가 있는 항목이 같다(근거 없는 상세 금지)."""
    has_detail = 'data-slot="detail"' in _section(_html(manual), item["id"])
    assert has_detail == bool(item.get("detail_src")), (
        f"{item['id']}: 자세히 보기 {'있음' if has_detail else '없음'} · detail_src "
        f"{'있음' if item.get('detail_src') else '없음'} — 둘을 함께 쓴다"
    )


@pytest.mark.parametrize(
    "manual,item", _detail_items(), ids=lambda v: v["id"] if isinstance(v, dict) else v
)
def test_detail_content(manual: str, item: dict) -> None:
    """V-11·V-13 — 소제목 2개 이상(정해진 어휘) · 본문 300자 이상 · 항목마다 본문에 보이는
    일반 설명이 있고 설명 30자 이상 · 근거 파일 실존 · 보이는 칸과 문장이 겹치지 않는다 ·
    사용자 매뉴얼은 내부 어휘를 쓰지 않는다."""
    sec = _section(_html(manual), item["id"])
    m = _DETAIL_RE.search(sec)
    assert m, f"{item['id']}: 자세히 보기 없음"
    body = m.group(1)
    topics = [_text(t).strip() for t in _TOPIC_RE.findall(body)]
    assert len(topics) >= 2, f"{item['id']}: 소제목 {len(topics)}개 — 2개 이상"
    odd = [t for t in topics if t not in _DETAIL_TOPICS[manual]]
    assert not odd, f"{item['id']}: 정해진 소제목이 아니다 {odd} — plans/116 §11.3"
    size = len(re.sub(r"\s", "", _text(body)))
    assert size >= 300, f"{item['id']}: 상세 본문 {size}자 — 300자 이상"
    parts = _detail_item_parts(body)
    no_lead = [name for name, lead, _ in parts if not lead]
    assert not no_lead, f"{item['id']}: 본문에 보일 일반 설명이 없는 항목 {no_lead}"
    thin = [name for name, _, full in parts if len(re.sub(r"\s", "", full)) - len(name) < 30]
    assert not thin, f"{item['id']}: 설명이 30자 미만인 항목 {thin}"
    missing = [p for p in item["detail_src"] if not (REPO / p).exists()]
    assert not missing, f"{item['id']}: detail_src 근거 파일 없음 {missing}"
    # 표에 {{항목}}으로 채운 일반 설명(span.term-lead)은 항목 문장을 가져온 것이라
    # 중복 대조에서 뺀다
    shown = _TERM_LEAD_RE.sub("", _DETAIL_RE.sub("", sec))
    visible = re.sub(r"[\s.,·:;!?()「」『』\"'`—-]", "", _text(shown))
    dup = [s for s in _sentences(body) if s in visible]
    assert not dup, f"{item['id']}: 보이는 칸과 겹치는 문장 {dup[:2]}"
    if manual == "user":
        words = sorted(set(_INTERNAL_WORDS.findall(_text(body))))
        assert not words, f"{item['id']}: 사용자 상세에 내부 어휘 {words}"


@pytest.mark.parametrize(
    "manual,item", _detail_items(), ids=lambda v: v["id"] if isinstance(v, dict) else v
)
def test_term_links_resolve(manual: str, item: dict) -> None:
    """본문 속 항목 이름 링크(a.term)·채운 일반 설명(span.term-lead)이
    같은 절의 실제 항목을 가리킨다."""
    sec = _section(_html(manual), item["id"])
    ids = set(re.findall(r'<div class="detail-item" id="([^"]+)"', sec))
    links = _TERM_LINK_RE.findall(sec)
    bad = [h for h, d in links if h != d or h not in ids]
    leads = re.findall(r'<span class="term-lead" data-term="([^"]+)"', sec)
    bad += [d for d in leads if d not in ids]
    assert not bad, f"{item['id']}: 없는 항목을 가리키는 본문 링크 {bad}"
    # 본문에 연결된 항목은 아래 상세 설명 목록에 다시 나오지 않는다
    # (숨은 저장소에만 둔다 — 2026-09-27 지시)
    listed = _DETAIL_RE.search(sec).group(1).split('<div class="detail-store"')[0]
    shown = re.findall(r'<div class="detail-item" id="([^"]+)"', listed)
    linked = {d for _, d in links}
    again = sorted(set(shown) & linked)
    assert not again, f"{item['id']}: 본문에 연결됐는데 상세 설명 목록에도 나오는 항목 {again}"


@pytest.mark.parametrize(
    "manual,item", _all_items(), ids=lambda v: v["id"] if isinstance(v, dict) else v
)
def test_captures_exist(manual: str, item: dict) -> None:
    """② 캡처가 정의돼 있고, 파일이 있고, 절이 그 이미지를 싣는다."""
    sec = _section(_html(manual), item["id"])
    for cid in item.get("captures") or []:
        assert cid in CAPTURES, f"{item['id']}: captures.yaml 에 {cid} 없음"
        img = OUT / "img" / f"{cid}.png"
        assert img.exists(), f"{item['id']}: 캡처 파일 없음 {img.name}"
        assert f"img/{cid}.png" in sec, f"{item['id']}: 절이 캡처 {cid} 를 싣지 않는다"


def test_every_user_item_has_case_or_reason() -> None:
    """R7 — 사용자 항목은 사례가 있거나 사례가 성립하지 않는 사유가 있다."""
    bad = [i["id"] for i in _items("user") if not i.get("cases") and not i.get("case_na")]
    assert not bad, f"사례도 사유도 없는 항목: {bad}"
    unknown = [c for i in _items("user") for c in i.get("cases") or [] if c not in CASES]
    assert not unknown, f"cases.yaml 에 없는 사례: {unknown}"


@pytest.mark.parametrize(
    "item", [i for i in FEATURES["user"] if i.get("cases")], ids=lambda i: i["id"]
)
def test_case_blocks_match_samples(item: dict) -> None:
    """⑥ 사례 블록의 입력·결과 표가 샘플과 같고, 샘플은 게재 판정(ok·partly)을 받았다.

    사람 확인(``human_review``)은 여기서 강제하지 않는다 — ``python -m scripts.manual.samples`` 가
    「사람 확인 대기」로 보고한다(plans/116 V-10 잔여)."""
    sec = _section(_html("user"), item["id"])
    for cid in item["cases"]:
        path = MANUAL / "fixtures" / "samples" / f"{cid}.json"
        assert path.exists(), f"{cid}: 샘플 없음"
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert doc.get("reviewed") is True, f"{cid}: 샘플 게재 판정 전(reviewed=false)"
        m = re.search(
            rf'<div class="case" data-case="{cid}">.*?(?=<div class="case" data-case=|$)', sec, re.S
        )
        assert m, f"{item['id']}: 사례 {cid} 블록 없음"
        block = m.group(0)
        for turn in doc["turns"]:
            assert _html_escape(turn["query"]) in block, f"{cid}: 입력 문구가 샘플과 다르다"
            table = _build()._rows_table(
                turn.get("rows_head") or [], (turn.get("done") or {}).get("row_count")
            )
            assert table in block, f"{cid}: 결과 표가 샘플 결과 행 앞부분과 다르다 — 재빌드 필요"


def _build():
    import sys

    sys.path.insert(0, str(REPO))
    from scripts.manual import build

    return build


def _html_escape(s: str) -> str:
    return html.escape(s, quote=True)  # build.py 의 esc() 와 같은 규칙


def test_reverse_every_button_and_tab_is_documented() -> None:
    """④ 화면의 버튼 id·탭 값 중 manifest 에 없는 것이 없다(새 기능 누락 감시)."""
    anchors = {a for _, i in _all_items() for a in i["anchors"]}
    ignore = set((FEATURES.get("ignore") or {}).keys())
    undocumented = []
    for rel in SCREENS:
        html = (STATIC / rel).read_text(encoding="utf-8")
        for ident in re.findall(r'<button[^>]*\bid="([^"]+)"', html):
            if f"#{ident}" not in anchors and ident not in ignore:
                undocumented.append(f"{rel}#{ident}")
        for attr, val in re.findall(r'\b(data-(?:tab|view|pane))="([^"]+)"', html):
            if f'{attr}="{val}"' not in anchors:
                undocumented.append(f'{rel} {attr}="{val}"')
    assert not undocumented, f"매뉴얼에 없는 화면 요소: {sorted(set(undocumented))}"


def test_ignore_entries_have_reasons() -> None:
    """④ 역방향 감시 예외는 사유가 있어야 한다(§4.6)."""
    ignore = FEATURES.get("ignore") or {}
    blank = [k for k, v in ignore.items() if not str(v or "").strip()]
    assert not blank, f"사유 없는 ignore: {blank}"


def test_images_within_budget_and_referenced() -> None:
    """G-1 — 캡처 총량 ≤15MB, 그리고 captures.yaml 에 없는 고아 이미지가 없다(저장소 비대 방지)."""
    pngs = list((OUT / "img").glob("*.png"))
    total = sum(p.stat().st_size for p in pngs)
    assert total <= 15 * 1024 * 1024, f"캡처 총량 {total / 1048576:.1f}MB > 15MB"
    orphans = sorted(p.stem for p in pngs if p.stem not in CAPTURES)
    assert not orphans, f"captures.yaml 에 없는 이미지: {orphans}"


def test_not_a_draft_build() -> None:
    """초안 빌드(--draft)의 자리 표시가 최종본에 남지 않았다."""
    for manual in ("user", "admin"):
        assert "data-draft=" not in _html(manual), (
            f"{manual}.html 이 초안 빌드다 — python -m scripts.manual.build"
        )


def test_no_external_resources() -> None:
    """⑤ 폐쇄망 — 매뉴얼이 외부 URL 리소스를 불러오지 않는다."""
    for manual in ("user", "admin"):
        html = _html(manual)
        ext = re.findall(r'(?:src|href)="(https?://[^"]+)"', html)
        assert not ext, f"{manual}.html 외부 리소스: {ext}"


def test_samples_have_no_secrets() -> None:
    """§4.7 — 샘플·재생 파일에 토큰·연결 문자열·내부 경로가 없다."""
    pat = re.compile(r"eyJ[A-Za-z0-9_-]{20,}|postgresql://|/Users/|password", re.I)

    def texts(
        obj,
    ):  # 파일 바이트(base64 · "__bytes__")는 우연히 패턴과 겹치므로 뺀다 — 텍스트 값만 본다
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k != "__bytes__":
                    yield from texts(v)
        elif isinstance(obj, list):
            for v in obj:
                yield from texts(v)
        elif isinstance(obj, str):
            yield obj

    hits = []
    for path in (MANUAL / "fixtures").rglob("*.json"):
        for text in texts(json.loads(path.read_text(encoding="utf-8"))):
            m = pat.search(text)
            if m:
                hits.append(f"{path.relative_to(REPO)}: {m.group(0)[:20]}")
                break
    assert not hits, f"민감 문자열: {hits}"


def test_capture_queries_have_recordings() -> None:
    """캡처 장면이 보내는 질문마다 유지된 녹화가 있다.

    없으면 재생 서버가 「녹화 없음」을 찍는다."""
    import sys

    sys.path.insert(0, str(REPO))
    from scripts.manual.replay import record_key

    keys = {
        t["key"]
        for p in (MANUAL / "fixtures" / "samples").glob("*.json")
        for t in json.loads(p.read_text(encoding="utf-8"))["turns"]
    }
    missing = []
    for cap in CAPTURES.values():
        upload = None
        for step in cap.get("steps") or []:
            ((kind, arg),) = step.items()
            if kind == "upload":
                upload = (MANUAL / "fixtures" / "forms" / arg[1]).read_bytes()
            elif kind in ("send", "send_nowait"):
                # 「ㅇㅇ존」은 파이프라인 전 라우트 제어 화면(존 역질문)이라 녹화가 없다
                if "ㅇㅇ존" not in arg and record_key(arg, upload, False) not in keys:
                    missing.append(f"{cap['id']}: {arg}")
                upload = None
    assert not missing, f"녹화 없는 캡처 질문: {missing}"
