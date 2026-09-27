"""매뉴얼 HTML 생성 (plans/116 §4.2~§4.3 · §4.9).

원천:
- ``content/user.md`` · ``content/admin.md`` — 본문(마크다운 + 칸 지시어)
- ``features.yaml`` — 절 제목·순서의 정본
- ``fixtures/samples/*.json`` — 사례 입력·결과(실 실행 녹화 · 여기서만 가져온다 — 손으로 옮기지 않는다)
- ``src/static/manual/img/*.png`` — capture.py 산출

산출: ``src/static/manual/user.html`` · ``admin.html`` (커밋한다 — 배포 서버에 빌드 도구가 없어도 된다)

본문 형식::

    ## 3. 질의하기                       ← 장
    장 도입 문단
    ### U-11                             ← 절(제목은 features.yaml)
    ::: what                             ← 칸: what · case · how · ui · caution · related
    ...
    :::
    ::: case Q-SERVER-LIST [캡처ID]      ← 사례 — 입력·결과는 샘플에서, 포인트·바꿔 보기는 본문에서
    포인트: ...
    바꿔 보기: 문장1 | 문장2
    :::
    ::: ui 캡처ID                        ← 그림 + 번호 설명(번호 = captures.yaml 콜아웃)
    1. ...
    :::
    ::: detail                           ← 상세 설명(「주의·제약」 뒤·「관련」 앞 — plans/116 §11)
    #### 소제목                          ← 본문 제목(접지 않음) · 칸 머리 바로가기에 나온다
    ##### 항목                           ← 제목 + 일반 설명(첫 문단·첫 글머리표)이 보이고 나머지는 「자세히」로 접힌다
    ...
    :::

``markdown-it-py`` 는 ``rich`` 의 의존성으로 들어와 있다 — 이 빌드에서만 쓴다(앱 런타임 무관).
"""

from __future__ import annotations

import datetime as dt
import html
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml
from markdown_it import MarkdownIt

REPO = Path(__file__).resolve().parents[2]
HERE = REPO / "scripts" / "manual"
OUT = REPO / "src" / "static" / "manual"
SAMPLES = HERE / "fixtures" / "samples"
# 본문(우리가 쓴 원천)은 HTML 을 허용해 강조를 미리 <strong> 으로 바꾼다 — CommonMark 는 「**×**로」처럼
# 강조 끝이 기호이고 바로 한글이 오면 강조로 보지 않는다. 사례 응답(LLM 출력)은 HTML 을 막은 렌더러로만 그린다.
MD = MarkdownIt("commonmark", {"html": True}).enable("table")
MD_SAFE = MarkdownIt("commonmark", {"html": False})
_BOLD = re.compile(r"\*\*([^*\n]+?)\*\*")
DRAFT = False  # --draft: 없는 캡처·샘플을 자리 표시로 둔다(작성 중 확인용 — 가드 테스트가 최종본에서 막는다)

SLOT_LABEL = {
    "what": "무엇을 하나",
    "case": "사례",
    "how": "사용 방법",
    "ui": "화면 요소",
    "caution": "주의·제약",
    "related": "관련 항목",
    "detail": "상세 설명",
}
TITLES = {"user": "사용자 매뉴얼", "admin": "관리자 매뉴얼"}
OTHER = {"user": ("admin", "관리자 매뉴얼"), "admin": ("user", "사용자 매뉴얼")}


def esc(s: object) -> str:
    return html.escape(str(s), quote=True)


def _bold(text: str) -> str:
    return _BOLD.sub(lambda m: f"<strong>{m.group(1)}</strong>", text)


def md(text: str) -> str:
    return MD.render(_bold(text.strip()))


def _captured() -> str:
    """마지막 캡처 일자·커밋(capture.py 가 남긴 img/captured.json) — 없으면 「미기록」."""
    path = OUT / "img" / "captured.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return f"{esc(doc['date'])} (커밋 <code>{esc(doc['commit'])}</code>)"
    except (OSError, ValueError, KeyError):
        return "미기록"


SCREENS = {
    "user": '<a href="/" target="_blank" rel="noopener">질의 화면</a>',
    "admin": '<a href="/admin" target="_blank" rel="noopener">관리자 화면</a>'
    '<a href="/" target="_blank" rel="noopener">질의 화면</a>',
}


def _commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except Exception:
        return "unknown"


# ── 사례 블록 ──────────────────────────────────────────────────────────────


def _summary(response: str) -> tuple[str, list[str]]:
    """응답에서 요약 문장(첫 문단의 산문)과 나머지 산문 줄을 뽑는다 — 표·제목·목록·소제목 줄은 뺀다."""
    lines = [ln.strip() for ln in (response or "").splitlines()]
    prose = [
        ln
        for ln in lines
        if ln
        and not ln.startswith(("|", "#", "---", "```", "- ", "* ", "> "))
        and not re.fullmatch(r"\*\*[^*]+\*\*:?|조회 결과|참고사항|상세 내용", ln)
    ]
    return " ".join(prose[:2]), prose


def _rows_table(rows: list[dict], total: int | None, limit: int = 5) -> str:
    if not rows:
        return ""
    cols = list(rows[0].keys())[:6]
    head = "".join(f"<th>{esc(c)}</th>" for c in cols)

    def cell(v: object) -> str:
        return "—" if v is None or v == "" else esc(v)

    body = "".join(
        "<tr>" + "".join(f"<td>{cell(r.get(c))}</td>" for c in cols) + "</tr>" for r in rows[:limit]
    )
    more = ""
    if total and total > min(limit, len(rows)):
        more = f'<p class="case-more">… 외 {total - min(limit, len(rows))}행 (전체 {total}행)</p>'
    return f'<div class="case-table"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>{more}'


def _turn_result(turn: dict) -> str:
    done = turn.get("done") or {}
    if done.get("type") == "error":
        return f'<p class="case-error">{esc(done.get("message") or "오류")}</p>'
    clar = done.get("clarification")
    if clar:
        opts = clar.get("options") or []
        labels = " · ".join(esc(o.get("label") or o.get("db_id")) for o in opts)
        return (
            f"<p>{esc(clar.get('question') or clar.get('message') or '조회할 존을 골라 주세요.')}</p>"
            f'<p class="case-note">선택지: {labels}</p>'
        )
    parts = []
    summary, prose = _summary(done.get("response") or "")
    if summary:
        parts.append(f"<p>{MD_SAFE.renderInline(summary)}</p>")
    table = _rows_table(turn.get("rows_head") or [], done.get("row_count"))
    if table:
        parts.append(table)
    elif len(prose) > 2:
        parts.append(
            "<ul>" + "".join(f"<li>{MD_SAFE.renderInline(p)}</li>" for p in prose[2:6]) + "</ul>"
        )
    if done.get("has_file"):
        parts.append(
            f'<p class="case-file">📄 결과 파일: <code>{esc(done.get("file_name"))}</code></p>'
        )
    ff = done.get("form_fill_clarification")
    if ff:
        fields = ff.get("fields") or ff.get("unresolved") or []
        names = ", ".join(
            esc(f.get("name") or f.get("field") or f) if isinstance(f, dict) else esc(f)
            for f in fields
        )
        parts.append(f'<p class="case-note">채우지 못한 항목을 되묻습니다: {names}</p>')
    return "".join(parts) or '<p class="case-note">(응답 없음)</p>'


def render_case(case_id: str, body: str, fig: str | None) -> str:
    path = SAMPLES / f"{case_id}.json"
    if not path.exists():
        if DRAFT:
            return f'<div class="case draft-missing" data-draft="{esc(case_id)}">[초안] 사례 샘플 없음: {esc(case_id)}</div>'
        raise SystemExit(f"사례 샘플 없음: {case_id} — python -m scripts.manual.samples --run")
    doc = json.loads(path.read_text(encoding="utf-8"))
    point, vary, rest = "", [], []
    for ln in body.strip().splitlines():
        if ln.startswith("포인트:"):
            point = ln.split(":", 1)[1].strip()
        elif ln.startswith("바꿔 보기:"):
            vary = [v.strip() for v in ln.split(":", 1)[1].split("|") if v.strip()]
        else:
            rest.append(ln)
    turns_html = []
    for i, turn in enumerate(doc["turns"]):
        label = "입력" if len(doc["turns"]) == 1 else f"입력 {i + 1}"
        file_note = (
            f' <span class="case-attach">📎 {esc(turn["file"])}</span>' if turn.get("file") else ""
        )
        turns_html.append(
            f'<div class="case-row"><div class="case-k">{label}</div><div class="case-v">'
            f'<div class="case-prompt"><code>{esc(turn["query"])}</code>'
            f'<button type="button" class="copy-btn" data-copy="{esc(turn["query"])}">복사</button></div>{file_note}</div></div>'
            f'<div class="case-row"><div class="case-k">결과</div><div class="case-v case-result">{_turn_result(turn)}</div></div>'
        )
    fig_html = _figure(fig, "") if fig else ""
    extra = md("\n".join(rest)) if "".join(rest).strip() else ""
    vary_html = (
        (
            '<div class="case-row"><div class="case-k">바꿔 보기</div><div class="case-v">'
            + " ".join(f'<code class="case-vary">{esc(v)}</code>' for v in vary)
            + "</div></div>"
        )
        if vary
        else ""
    )
    point_html = (
        f'<div class="case-row"><div class="case-k">포인트</div><div class="case-v">{md(point)}</div></div>'
        if point
        else ""
    )
    when = esc(doc.get("recorded_at", "")[:10])
    return (
        f'<div class="case" data-case="{esc(case_id)}"><div class="case-head">사례 · <span>{esc(case_id)}</span>'
        f'<span class="case-when">실행 {when} · 샌드박스</span></div>'
        + "".join(turns_html)
        + fig_html
        + point_html
        + vary_html
        + extra
        + "</div>"
    )


# ── 그림 ─────────────────────────────────────────────────────────────────


def _figure(cap_id: str, caption: str) -> str:
    img = OUT / "img" / f"{cap_id}.png"
    if not img.exists():
        if DRAFT:
            return f'<figure class="shot draft-missing" data-draft="{esc(cap_id)}">[초안] 캡처 없음: {esc(cap_id)}</figure>'
        raise SystemExit(
            f"캡처 없음: {img.name} — python scripts/manual/capture.py --only {cap_id}"
        )
    cap = f"<figcaption>{esc(caption)}</figcaption>" if caption else ""
    return (
        f'<figure class="shot"><a href="/static/manual/img/{cap_id}.png" class="zoom" target="_blank" rel="noopener">'
        f'<img src="/static/manual/img/{cap_id}.png" alt="{esc(caption or cap_id)}" loading="lazy"></a>{cap}</figure>'
    )


def render_ui(arg: str, body: str) -> str:
    parts = arg.split(None, 1)
    cap_id = parts[0] if parts else "-"
    caption = parts[1] if len(parts) > 1 else ""
    fig = _figure(cap_id, caption) if cap_id != "-" else ""
    items = []
    other = []
    for ln in body.strip().splitlines():
        m = re.match(r"^\s*(\d+)\.\s+(.*)$", ln)
        if m:
            items.append(
                f'<li><span class="badge-n">{m.group(1)}</span><span>{MD.renderInline(_bold(m.group(2)))}</span></li>'
            )
        elif ln.strip():
            other.append(ln)
    lst = f'<ol class="callouts">{"".join(items)}</ol>' if items else ""
    return fig + lst + (md("\n".join(other)) if other else "")


# ── 본문 파싱 ─────────────────────────────────────────────────────────────


_DIRECTIVE = re.compile(r"^:::\s*(\w+)\s*(.*)$")


_LEAD_LABEL = re.compile(r"^\s*(?:-\s*)?\*\*무엇인가요?\*\*\s*[—:]\s*")


def _lead_and_rest(text: str) -> tuple[str, str]:
    """본문에 보일 일반 설명(첫 글머리표 또는 첫 문단)과 「자세히」로 접을 나머지로 나눈다.

    「**무엇인가(요)** —」·「- **무엇인가(요)**:」 머리말은 항목 제목이 이미 말하므로 뗀다."""
    text = text.strip("\n")
    lines = text.splitlines()
    if lines and re.match(r"^\s*-\s", lines[0]):  # 글머리표 목록 — 첫 항목(이어지는 들여쓴 줄 포함)
        k = 1
        while k < len(lines) and lines[k].startswith(" ") and not re.match(r"^\s*-\s", lines[k]):
            k += 1
        lead, rest = "\n".join(lines[:k]), "\n".join(lines[k:])
        lead = re.sub(r"^\s*-\s*", "", lead)
    elif lines and lines[0].lstrip().startswith("|"):  # 표로 시작 — 일반 설명 없이 전부 접는다
        return "", text
    else:
        parts = re.split(r"\n\s*\n", text, maxsplit=1)
        lead, rest = parts[0], parts[1] if len(parts) > 1 else ""
    return _LEAD_LABEL.sub("", lead).strip(), rest.strip()


def _more(rest: str, label: str = "자세히") -> str:
    if not rest:
        return ""
    return f'<details class="detail-more"><summary>{label}</summary><div class="detail-more-body">{md(rest)}</div></details>'


def _split_heads(text: str, mark: str) -> tuple[str, list[tuple[str, str]]]:
    """``mark`` 제목 줄로 나눈다 — (첫 제목 앞 글, [(제목, 본문)…])."""
    parts = re.split(rf"^{re.escape(mark)} (.+)$", text, flags=re.M)
    return parts[0], [(parts[k].strip(), parts[k + 1]) for k in range(1, len(parts), 2)]


def render_detail(fid: str, body: str, linked: frozenset[str] = frozenset()) -> str:
    """상세 설명 — 본문에 연결되지 않은 항목만 여기 보인다(사용자 지시 2026-09-27).

    - 소제목(``####``)은 접지 않는 제목이다. 칸 머리에 소제목별 항목 바로가기 링크를 둔다.
    - 항목(``#####``)은 제목 + 일반 설명(첫 문단 또는 첫 글머리표)이 보이고, 나머지는 「자세히」에 접힌다.
    - 항목이 없는 소제목은 첫 문단만 보이고 나머지(표 등)는 「자세히」에 접힌다.
    - ``linked`` (본문 링크·표 채움이 가리키는 항목 id)는 목록에서 빼고 보이지 않는 저장소(``.detail-store``)에만
      둔다 — 본문 링크를 누르면 manual.js 가 그 자리 아래에 펼친다. 항목이 모두 빠진 소제목은 목록에서 없앤다."""
    lead, topics = _split_heads(body, "####")
    if lead.strip() or not topics:
        raise SystemExit(f"{fid}: 상세 설명은 소제목(####)으로 시작해야 한다")
    base = fid.lower()
    out, index, store = [], [], []
    for t, (title, text) in enumerate(topics, 1):
        intro, items = _split_heads(text, "#####")
        if items:
            inner = md(intro) if intro.strip() else ""
        else:
            gen, rest = _lead_and_rest(intro)
            inner = (md(gen) if gen else "") + _more(rest)
        links, shown, hidden = [], 0, []
        for k, (name, desc) in enumerate(items, 1):
            gen, rest = _lead_and_rest(desc)
            if not gen and not rest:
                raise SystemExit(f"{fid}: 항목 「{name}」 설명이 비었다")
            iid = f"{base}-d-{t}-{k}"
            title_html = MD.renderInline(_bold(name))
            block = (
                f'<div class="detail-item" id="{iid}"><h6 class="detail-item-title">{title_html}</h6>'
                f'<div class="detail-item-lead">{md(gen) if gen else ""}</div>{_more(rest)}</div>'
            )
            if iid in linked:
                hidden.append(block)
                continue
            shown += 1
            links.append(f'<a href="#{iid}">{title_html}</a>')
            inner += block
        if hidden:
            store.append(f'<h5 class="detail-topic-title">{esc(title)}</h5>' + "".join(hidden))
        if items and not shown:
            continue  # 항목이 모두 본문에 연결된 소제목 — 목록에서 뺀다
        index.append(
            f'<li><a href="#{base}-d-{t}">{esc(title)}</a>'
            + (f'<span class="detail-index-items">{"".join(links)}</span>' if links else "")
            + "</li>"
        )
        out.append(
            f'<div class="detail-topic" id="{base}-d-{t}"><h5 class="detail-topic-title">{esc(title)}</h5>'
            f"{inner}</div>"
        )
    head = (
        f'<h4>{SLOT_LABEL["detail"]}</h4><ul class="detail-index">{"".join(index)}</ul>'
        if out
        else ""
    )
    return (
        f'<div class="slot slot-detail{"" if out else " detail-empty"}" data-slot="detail" id="{base}-detail">'
        f'{head}{"".join(out)}'
        f'<div class="detail-store" hidden>{"".join(store)}</div><!--/slot--></div>'
    )


# ── 본문 ↔ 항목 연결 (사용자 지시 2026-09-27) ──────────────────────────────
# 보이는 칸(무엇·사용 방법·화면 요소·주의)의 화면 이름을 그 절 상세 설명의 항목에 잇는다.
#   자동: 굵은 글씨 전체(「·」로 이은 나열은 하나씩) · 표 칸 전체 · 화면 요소 번호 설명의 첫 이름이 항목 이름과 같을 때
#   수동: [[항목명]] · [[보일 글자|항목명]]  → 링크 / {{항목명}} → 그 항목의 일반 설명(첫 문단)을 그 자리에 채운다
# 누르면 manual.js 가 그 줄(표면 그 행) 바로 아래에 항목 설명을 펼친다. JS 가 없으면 상세 설명 항목으로 이동한다.
_LINKED_SLOTS = ("what", "how", "ui", "caution")
_DETAIL_BLOCK = re.compile(r"^::: detail\n(.*?)^:::\s*$", re.M | re.S)


def _plain(name: str) -> str:
    return re.sub(r"[*`]", "", name).strip()


def _detail_terms(fid: str, text: str) -> dict[str, tuple[str, str]]:
    """절의 상세 설명 항목 — {항목명: (앵커 id, 일반 설명 마크다운)}. 번호는 render_detail 과 같다."""
    m = _DETAIL_BLOCK.search(text)
    if not m:
        return {}
    terms: dict[str, tuple[str, str]] = {}
    _, topics = _split_heads(m.group(1), "####")
    for t, (_, ttext) in enumerate(topics, 1):
        _, items = _split_heads(ttext, "#####")
        for k, (name, desc) in enumerate(items, 1):
            terms.setdefault(_plain(name), (f"{fid.lower()}-d-{t}-{k}", _lead_and_rest(desc)[0]))
    return terms


# 이 소제목의 항목은 「화면 요소」 칸에 링크 줄로 모두 올린다 — 본문에서 이어지므로 아래 목록에서는 빠진다
_INDEX_TOPICS = ("항목별 설명", "항목 전수")


def _term_list(fid: str, text: str) -> str:
    """「항목별 설명」·「항목 전수」 소제목의 항목 전부를 본문 링크 줄로 만든다(누르면 줄 아래에 펼친다)."""
    m = _DETAIL_BLOCK.search(text)
    if not m:
        return ""
    rows = []
    _, topics = _split_heads(m.group(1), "####")
    for t, (title, ttext) in enumerate(topics, 1):
        _, items = _split_heads(ttext, "#####")
        if title in _INDEX_TOPICS and items:
            links = " ".join(
                _term_a(MD.renderInline(_bold(name)), f"{fid.lower()}-d-{t}-{k}")
                for k, (name, _) in enumerate(items, 1)
            )
            rows.append(f'<p class="term-list"><span class="term-list-label">{esc(title)}</span> {links}</p>')
    return "".join(rows)


def _term_a(label: str, iid: str) -> str:
    return f'<a class="term" href="#{iid}" data-term="{iid}">{label}</a>'


def _expand_terms(fid: str, body: str, terms: dict[str, tuple[str, str]]) -> str:
    """원천의 [[…]]·{{…}} 를 링크·일반 설명으로 바꾼다 — 없는 항목명이면 빌드 실패."""

    def need(name: str) -> tuple[str, str]:
        if _plain(name) not in terms:
            raise SystemExit(f"{fid}: 상세 설명에 없는 항목 「{name}」")
        return terms[_plain(name)]

    def link(m: re.Match) -> str:
        label, _, name = m.group(1).partition("|")
        return _term_a(esc(label), need(name or label)[0])

    def lead(m: re.Match) -> str:
        iid, text = need(m.group(1))
        return f'<span class="term-lead" data-term="{iid}">{MD.renderInline(_bold(text))}</span>'

    # 코드 표기(`{{placeholder}}` 같은 Word 양식 설명)는 건드리지 않는다
    body = re.sub(r"(?<!`)\[\[([^\]`]+)\]\](?!`)", link, body)
    return re.sub(r"(?<!`)\{\{([^}`]+)\}\}(?!`)", lead, body)


def _link_terms(h: str, terms: dict[str, tuple[str, str]]) -> str:
    """렌더된 보이는 칸에서 화면 이름을 항목 링크로 바꾼다(자동 규칙 — 모듈 주석)."""

    def find(s: str) -> str | None:
        hit = terms.get(_plain(html.unescape(s)))
        return hit[0] if hit else None

    def strong(m: re.Match) -> str:
        parts = m.group(1).split("·")
        if not any(find(p) for p in parts):
            return m.group(0)
        return "<strong>" + "·".join(_term_a(p, find(p)) if find(p) else p for p in parts) + "</strong>"

    def cell(m: re.Match) -> str:
        iid = find(m.group(2))
        return f"{m.group(1)}{_term_a(m.group(2), iid)}</td>" if iid else m.group(0)

    def callout(m: re.Match) -> str:
        iid = find(m.group(2))
        return f"{m.group(1)}{_term_a(m.group(2), iid)}{m.group(3)}" if iid else m.group(0)

    h = re.sub(r"<strong>([^<]+)</strong>", strong, h)
    h = re.sub(r"(<td[^>]*>)([^<]+)</td>", cell, h)
    return re.sub(r'(<span class="badge-n">\d+</span><span>)([^<]+?)( — |\(|</span>)', callout, h)


def render_section(fid: str, title: str, text: str) -> str:
    terms = _detail_terms(fid, text)
    term_list = _term_list(fid, text)
    detail_body: str | None = None
    out, lines, i = [], text.splitlines(), 0
    slots_seen = set()
    order: list[str] = []
    loose: list[str] = []
    while i < len(lines):
        m = _DIRECTIVE.match(lines[i])
        if not m or m.group(1) == "":
            loose.append(lines[i])
            i += 1
            continue
        kind, arg = m.group(1), m.group(2).strip()
        j = i + 1
        while j < len(lines) and lines[j].strip() != ":::":
            j += 1
        body = "\n".join(lines[i + 1 : j])
        i = j + 1
        if kind in _LINKED_SLOTS:
            body = _expand_terms(fid, body, terms)
        if kind == "case":
            cid, *fig = arg.split()
            inner = render_case(cid, body, fig[0] if fig else None)
            slot = "case"
        elif kind == "ui":
            inner = render_ui(arg, body)
            slot = "ui"
        elif kind == "detail":
            detail_body = body
            out.append("@@DETAIL@@")  # 본문 링크를 다 모은 뒤 그린다(연결된 항목을 목록에서 빼려고)
            slots_seen.add("detail")
            order.append("detail")
            continue
        elif kind in SLOT_LABEL:
            inner = md(body)
            slot = kind
        else:
            raise SystemExit(f"{fid}: 알 수 없는 칸 {kind}")
        if slot == "case" and "case" in slots_seen:
            out.append(inner)  # 사례가 여럿이면 같은 칸 제목 아래 이어 붙인다
            continue
        if slot in _LINKED_SLOTS and terms:
            inner = _link_terms(inner, terms)
        if slot == "ui" and term_list:
            inner += term_list
            term_list = ""
        slots_seen.add(slot)
        order.append(slot)
        out.append(
            f'<div class="slot slot-{slot}" data-slot="{slot}"><h4>{SLOT_LABEL[slot]}</h4>{inner}<!--/slot--></div>'
        )
    if "".join(loose).strip():
        raise SystemExit(f"{fid}: 칸 밖에 본문이 있다 — {''.join(loose)[:60]}")
    if term_list:
        raise SystemExit(f"{fid}: 「항목별 설명」 링크 줄을 둘 「화면 요소」 칸이 없다")
    if detail_body is not None:
        linked = frozenset(re.findall(r'data-term="([^"]+)"', "".join(out)))
        out[out.index("@@DETAIL@@")] = render_detail(fid, detail_body, linked)
    if "detail" in order:
        k = order.index("detail")
        if "caution" not in order[:k] or "related" not in order[k:]:
            raise SystemExit(f"{fid}: 자세히 보기는 「주의·제약」 뒤, 「관련 항목」 앞에 둔다")
    return (
        f'<section class="feature" id="{fid.lower()}"><h3><span class="fid">{fid}</span>{esc(title)}</h3>'
        + "".join(out)
        + "</section>"
    )


def build(manual: str) -> Path:
    feats = {
        f["id"]: f
        for f in yaml.safe_load((HERE / "features.yaml").read_text(encoding="utf-8"))[manual]
    }
    text = (HERE / "content" / f"{manual}.md").read_text(encoding="utf-8")
    chapters = re.split(r"^## ", text, flags=re.M)
    preface, chapters = chapters[0], chapters[1:]
    toc, body, seen = [], [], []
    for ch in chapters:
        head, _, rest = ch.partition("\n")
        m = re.match(r"(\d+|부록)\.?\s*(.*)$", head.strip())
        ch_id = f"ch-{m.group(1)}" if m else f"ch-{len(toc)}"
        parts = re.split(r"^### ", rest, flags=re.M)
        intro, secs = parts[0], parts[1:]
        sub_toc, sec_html = [], []
        for sec in secs:
            sid, _, stext = sec.partition("\n")
            sid = sid.strip()
            if sid not in feats:
                raise SystemExit(f"{manual}: features.yaml 에 없는 절 {sid}")
            seen.append(sid)
            title = feats[sid]["title"]
            sub_toc.append(
                f'<li><a href="#{sid.lower()}"><span class="fid">{sid}</span>{esc(title)}</a></li>'
            )
            sec_html.append(render_section(sid, title, stext))
        toc.append(
            f'<li><a href="#{ch_id}">{esc(head.strip())}</a><ol>{"".join(sub_toc)}</ol></li>'
        )
        body.append(
            f'<section class="chapter" id="{ch_id}"><h2>{esc(head.strip())}</h2>{md(intro) if intro.strip() else ""}'
            + "".join(sec_html)
            + "</section>"
        )
    missing = [f for f in feats if f not in seen]
    if missing:
        raise SystemExit(f"{manual}: 본문에 없는 절 {missing}")
    other, other_title = OTHER[manual]
    page = TEMPLATE.format(
        title=TITLES[manual],
        manual=manual,
        other=other,
        other_title=other_title,
        commit=_commit(),
        date=dt.date.today().isoformat(),
        captured=_captured(),
        screens=SCREENS[manual],
        count=len(feats),
        preface=md(preface) if preface.strip() else "",
        toc="".join(toc),
        body="".join(body),
    )
    path = OUT / f"{manual}.html"
    path.write_text(page, encoding="utf-8")
    return path


TEMPLATE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} — Infra Query Agent</title>
<script src="/static/js/theme.js"></script>
<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="/static/manual/manual.css">
</head>
<body data-manual="{manual}">
<header class="m-header">
  <button type="button" class="toc-toggle" aria-label="목차 열기">☰</button>
  <div class="m-title"><span class="dot"></span><span class="brand">INFRA QUERY AGENT</span> <b>{title}</b></div>
  <input type="search" class="toc-filter" placeholder="기능 찾기 (예: CSV, 존, 침묵)" aria-label="기능 찾기">
  <nav class="m-links">
    <button type="button" class="detail-all" aria-pressed="false">모두 펼치기</button>
    <a href="/manual/{other}">{other_title}</a>
    {screens}
    <button type="button" class="theme-btn" aria-label="테마 전환">◐</button>
  </nav>
</header>
<div class="m-wrap">
  <aside class="toc"><ol>{toc}</ol></aside>
  <main class="m-main">
    <div class="edition">기준 커밋 <code>{commit}</code> · 빌드 {date} · 캡처 {captured} · 기능 {count}개 · 화면은 모의 백엔드 위에서 자동 캡처했고, 사례 결과는 로컬 샌드박스에서 실제로 실행한 결과입니다.</div>
    {preface}
    {body}
  </main>
</div>
<script src="/static/manual/manual.js"></script>
</body>
</html>
"""


def main() -> None:
    global DRAFT
    args = sys.argv[1:]
    DRAFT = "--draft" in args
    manuals = [a for a in args if not a.startswith("--")] or ["user", "admin"]
    for m in manuals:
        print(build(m))


if __name__ == "__main__":
    main()
