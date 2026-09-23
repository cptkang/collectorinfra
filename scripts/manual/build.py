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


def render_section(fid: str, title: str, text: str) -> str:
    out, lines, i = [], text.splitlines(), 0
    slots_seen = set()
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
        if kind == "case":
            cid, *fig = arg.split()
            inner = render_case(cid, body, fig[0] if fig else None)
            slot = "case"
        elif kind == "ui":
            inner = render_ui(arg, body)
            slot = "ui"
        elif kind in SLOT_LABEL:
            inner = md(body)
            slot = kind
        else:
            raise SystemExit(f"{fid}: 알 수 없는 칸 {kind}")
        if slot == "case" and "case" in slots_seen:
            out.append(inner)  # 사례가 여럿이면 같은 칸 제목 아래 이어 붙인다
            continue
        slots_seen.add(slot)
        out.append(
            f'<div class="slot slot-{slot}" data-slot="{slot}"><h4>{SLOT_LABEL[slot]}</h4>{inner}<!--/slot--></div>'
        )
    if "".join(loose).strip():
        raise SystemExit(f"{fid}: 칸 밖에 본문이 있다 — {''.join(loose)[:60]}")
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
