"""사례 프롬프트 실 실행·고정 (plans/116 §4.9.3 ①).

``--run``  : 녹화 서버(스냅샷 ``record`` 프로필 · 로컬 MLX 두 평면)에 사례를 **순차로** 보내고
             결과 요약을 ``fixtures/samples/<case>.json`` 에, 재생용 이벤트를
             ``fixtures/queries/<key>.json`` 에 남긴다.
기본(``--check``) : 사례 ↔ 샘플 ↔ 재생 파일 대조만 한다(LLM·서버 불필요).
``--sheet``  : 사람 확인용 검토표(HTML)를 ``build/manual_capture/review_sheet.html`` 에 만든다 —
             사례마다 입력·응답 전문·결과 행·생성 SQL·대리 판정. 서버·LLM 불필요.
``--confirm CASE --by 이름`` : 사람이 확인한 사례에 ``human_review`` 를 남긴다.

검토 두 층(plans/116 §4.9.3 · V-10):
- ``reviewed`` / ``review`` — 게재 판정. ``--review`` 로 남긴다
  (대리 검토 가능 — 샌드박스 독립 SQL 대조).
  ``ok``·``partly`` 만 매뉴얼에 싣는다(가드 테스트가 막는다).
- ``human_review`` — **사람**의 확인. 에이전트는 채우지 않는다. ``--check`` 는 이것이 없는 사례를
  「사람 확인 전」으로 보고하고 exit 1 로 끝낸다(V-10 미충족을 숨기지 않는다).

실행 전제(D-240): 두 평면이 ``mlx`` 루프백이어야 한다. ``--run`` 은 서버 ``.env`` 를 읽어
확인하고, 아니면 실행하지 않는다. 공유 MLX 서버 보호를 위해 동시 요청은 보내지 않는다.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import yaml

from scripts.manual.replay import record_key
from scripts.manual.snapshot import (
    ADMIN_PASSWORD,
    ADMIN_USER,
    APP_DIRS,
    BUILD,
    DEMO_PASSWORD,
    DEMO_USER,
    FIXTURES,
    PORT,
)

HERE = Path(__file__).resolve().parent
CASES = HERE / "cases.yaml"
SAMPLES = FIXTURES / "samples"
QUERIES = FIXTURES / "queries"
FORMS = FIXTURES / "forms"
BASE = f"http://127.0.0.1:{PORT}"


def load_cases() -> list[dict]:
    return yaml.safe_load(CASES.read_text(encoding="utf-8"))["cases"]


def _assert_local_llm(profile: str) -> None:
    app = APP_DIRS[profile]
    env = dict(
        line.split("=", 1)
        for line in (app / ".env").read_text(encoding="utf-8").splitlines()
        if "=" in line
    )
    planes = (env.get("LLM_PROVIDER"), env.get("ORCHESTRATOR_PROVIDER"))
    urls = (env.get("LLM_MLX_BASE_URL", ""), env.get("ORCHESTRATOR_BASE_URL", ""))
    if planes != ("mlx", "mlx") or not all(u.startswith("http://127.0.0.1") for u in urls):
        sys.exit(f"실행 거부 — 두 평면이 로컬 MLX 가 아니다: {planes} {urls}")
    if (app / ".encenv").exists():
        sys.exit("실행 거부 — 스냅샷에 .encenv 가 있다")


def _login(client: httpx.Client) -> str:
    # 사례 계정이 없으면 가입(첫 실행). 이미 있으면 409 — 무시하고 로그인한다.
    client.post(
        f"{BASE}/api/v1/auth/register",
        json={
            "user_id": DEMO_USER,
            "username": "홍길동",
            "password": DEMO_PASSWORD,
            "department": "인프라운영팀",
        },
    )
    # 캡처 서버와 같은 권한(샌드박스 DB)을 준다 — 권한 없는 계정으로 녹화하면 인가가 걸린 경로(사용법·DB 목록)는
    # 거절 안내만 녹화된다(2026-09-23 · 종전 녹화는 빈 권한 계정이었다)
    admin = client.post(
        f"{BASE}/api/v1/auth/login", json={"user_id": ADMIN_USER, "password": ADMIN_PASSWORD}
    )
    admin.raise_for_status()
    client.put(
        f"{BASE}/api/v1/admin/users/{DEMO_USER}/permissions",
        headers={"Authorization": f"Bearer {admin.json()['access_token']}"},
        json={"allowed_db_ids": ["polestar"]},
    ).raise_for_status()
    r = client.post(
        f"{BASE}/api/v1/auth/login", json={"user_id": DEMO_USER, "password": DEMO_PASSWORD}
    )
    r.raise_for_status()
    return r.json()["access_token"]


def _sse(resp: httpx.Response) -> dict:
    """SSE 를 읽어 done/error 이벤트와 진행 요약을 돌려준다."""
    done: dict | None = None
    nodes: list[str] = []
    for line in resp.iter_lines():
        if not line.startswith("data:"):
            continue
        try:
            ev = json.loads(line[5:].strip())
        except json.JSONDecodeError:
            continue
        t = ev.get("type")
        if t == "node_start":
            nodes.append(ev.get("node"))
        elif t in ("done", "error"):
            done = ev
    return {"done": done, "nodes": nodes}


def _turn(client: httpx.Client, token: str, turn: dict, thread_id: str) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    query = turn["query"]
    file_bytes: bytes | None = None
    t0 = time.time()
    if turn.get("file"):
        path = FORMS / turn["file"]
        file_bytes = path.read_bytes()
        data = {"query": query, "thread_id": thread_id}
        if turn.get("selected_db_ids"):
            data["selected_db_ids"] = json.dumps(turn["selected_db_ids"])
        with client.stream(
            "POST",
            f"{BASE}/api/v1/query/file/stream",
            headers=headers,
            data=data,
            files={"file": (path.name, file_bytes)},
            timeout=1200,
        ) as r:
            out = _sse(r)
    else:
        body: dict[str, Any] = {"query": query, "thread_id": thread_id}
        for k in (
            "selected_db_ids",
            "form_fill_answers",
            "form_fill_remember",
            "form_memory_delete",
        ):
            if k in turn:
                body[k] = turn[k]
        with client.stream(
            "POST", f"{BASE}/api/v1/query/stream", headers=headers, json=body, timeout=1200
        ) as r:
            out = _sse(r)
    done = out["done"] or {}
    key = record_key(query, file_bytes, bool(turn.get("form_fill_answers")))
    return {
        "query": query,
        "file": turn.get("file"),
        "key": key,
        "elapsed_s": round(time.time() - t0, 1),
        "nodes": out["nodes"],
        "done": {
            k: done.get(k)
            for k in (
                "type",
                "response",
                "row_count",
                "executed_sql",
                "has_file",
                "file_name",
                "awaiting_approval",
                "clarification",
                "form_fill_clarification",
                "form_memory_panel",
                "message",
                "code",
            )
        },
        "rows_head": _rows_head(key),
    }


def _rows_head(key: str) -> list:
    """녹화 파일의 최종 출력에서 결과 행 앞 10건(결과 조회 API 는 행을 돌려주지 않는다)."""
    path = QUERIES / f"{key}.json"
    if not path.exists():
        return []
    for rec in reversed(json.loads(path.read_text(encoding="utf-8"))["events"]):
        out = rec.get("output")
        if isinstance(out, dict) and "final_response" in out:
            rows = out.get("query_results") or []
            return rows[:10] if isinstance(rows, list) else []
    return []


def run(only: list[str] | None, profile: str) -> None:
    _assert_local_llm(profile)
    SAMPLES.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=60) as client:
        client.get(f"{BASE}/api/v1/health").raise_for_status()
        token = _login(client)
        for case in load_cases():
            if only and case["id"] not in only:
                continue
            if case.get("record") is False or case.get("profile", "record") != profile:
                continue
            thread_id = str(uuid.uuid4())
            turns = []
            for turn in case["turns"]:
                print(f"[{case['id']}] {turn['query'][:60]} …", flush=True)
                res = _turn(client, token, turn, thread_id)
                print(
                    f"   → {res['elapsed_s']}s rows={res['done'].get('row_count')} "
                    f"{(res['done'].get('response') or res['done'].get('message') or '')[:80]!r}",
                    flush=True,
                )
                turns.append(res)
            prev = SAMPLES / f"{case['id']}.json"
            doc = {
                "id": case["id"],
                "turns": turns,
                "reviewed": False,
                "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            prev.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")


def check() -> int:
    """사례 ↔ 샘플 ↔ 재생 파일 대조. 문제 목록을 출력하고 개수를 돌려준다(사람 확인 대기 포함)."""
    problems = []
    pending = []
    for case in load_cases():
        if case.get("record") is False:
            continue
        path = SAMPLES / f"{case['id']}.json"
        if not path.exists():
            problems.append(f"{case['id']}: 샘플 없음")
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        for i, (turn, got) in enumerate(zip(case["turns"], doc["turns"])):
            if turn["query"] != got["query"]:
                problems.append(f"{case['id']}#{i}: 질의 불일치")
            if not turn.get("pregate") and not (QUERIES / f"{got['key']}.json").exists():
                problems.append(f"{case['id']}#{i}: 재생 파일 없음 {got['key']}")
        if not doc.get("reviewed"):
            problems.append(f"{case['id']}: 게재 판정 전(reviewed=false)")
        elif not doc.get("human_review"):
            verdict = (doc.get("review") or {}).get("verdict", "?")
            pending.append(f"{case['id']}: 사람 확인 전(human_review 없음 · 대리 판정 {verdict})")
    for p in problems:
        print(p)
    for p in pending:
        print(p)
    print(
        f"— 구조 문제 {len(problems)}건 · 사람 확인 대기 {len(pending)}건"
        + (" (검토표: python -m scripts.manual.samples --sheet)" if pending else "")
    )
    return len(problems) + len(pending)


def review(case_id: str, verdict: str, notes: str) -> None:
    """사람(또는 대리 검토자)의 판정을 샘플에 남긴다. verdict: ok(표·요약 모두 맞음) · partly(표는 맞고 문장에 흠 —
    본문 포인트가 흠을 밝혀야 한다) · wrong(싣지 않는다 — 문장을 바꿔 다시 녹화). ok·partly 만 reviewed=true."""
    path = SAMPLES / f"{case_id}.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["review"] = {"verdict": verdict, "notes": notes, "at": time.strftime("%Y-%m-%d")}
    doc["reviewed"] = verdict in ("ok", "partly")
    if not doc["reviewed"]:
        doc.pop("human_review", None)  # 싣지 않는 샘플에 사람 확인 표지를 남기지 않는다
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")


def confirm(case_id: str, by: str, notes: str) -> None:
    """사람이 검토표로 확인한 사례에 표지를 남긴다.

    틀렸으면 이 명령 대신 ``--review CASE wrong 메모``."""
    if not by.strip():
        sys.exit("--by 에 확인한 사람 이름을 적는다")
    path = SAMPLES / f"{case_id}.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not doc.get("reviewed"):
        sys.exit(f"{case_id}: 게재 판정(reviewed)이 없는 샘플은 확인 표지를 달 수 없다")
    doc["human_review"] = {"by": by.strip(), "at": time.strftime("%Y-%m-%d"), "notes": notes}
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")


# ── 사람 확인용 검토표 ─────────────────────────────────────────────────────


def _recorded_sql(key: str) -> list[str]:
    """녹화 파일 안의 생성 SQL(중복 제거 · 등장 순서)."""
    path = QUERIES / f"{key}.json"
    if not path.exists():
        return []
    found: list[str] = []

    def walk(o: Any) -> None:
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "generated_sql" and isinstance(v, str) and v.strip() and v not in found:
                    found.append(v)
                else:
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(json.loads(path.read_text(encoding="utf-8"))["events"])
    return found


def sheet(out: Path) -> Path:
    """사례별 입력·응답·결과 행·SQL·대리 판정을 한 장에 모은 HTML.

    사람이 원본 데이터와 대조하는 데 쓴다."""
    e = lambda v: html.escape("" if v is None else str(v))  # noqa: E731
    cases = {c["id"]: c for c in load_cases()}
    parts = []
    for path in sorted(SAMPLES.glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        cid = doc["id"]
        case = cases.get(cid, {})
        rv = doc.get("review") or {}
        hr = doc.get("human_review")
        status = (
            f'<b class="ok">사람 확인됨 — {e(hr["by"])} {e(hr["at"])}</b>'
            if hr
            else '<b class="todo">사람 확인 전</b>'
        )
        turns = []
        for i, t in enumerate(doc["turns"], 1):
            done = t.get("done") or {}
            rows = t.get("rows_head") or []
            cols = list(rows[0].keys()) if rows else []
            table = (
                "<table><tr>"
                + "".join(f"<th>{e(c)}</th>" for c in cols)
                + "</tr>"
                + "".join(
                    "<tr>" + "".join(f"<td>{e(r.get(c))}</td>" for c in cols) + "</tr>"
                    for r in rows
                )
                + "</table>"
                if rows
                else "<p>(결과 행 없음)</p>"
            )
            sqls = (
                "".join(f"<pre>{e(q)}</pre>" for q in _recorded_sql(t["key"]))
                or "<p>(SQL 없음)</p>"
            )
            answer = e(done.get("response") or done.get("message"))
            attach = f" · 첨부 {e(t['file'])}" if t.get("file") else ""
            turns.append(
                f"<h4>입력 {i}{attach}</h4>"
                f"<pre class='q'>{e(t['query'])}</pre>"
                f"<p>row_count={e(done.get('row_count'))}"
                f" · 파일={e(done.get('file_name') or '-')}"
                f" · 소요 {e(t.get('elapsed_s'))}s</p>"
                f"<details open><summary>응답 전문</summary><pre>{answer}</pre></details>"
                f"<details><summary>결과 행(앞 10)</summary>{table}</details>"
                f"<details><summary>생성 SQL</summary>{sqls}</details>"
            )
        cmd = "python -m scripts.manual.samples"
        head = f"{e(case.get('feature'))} · 원천 {e(case.get('scenario_ref') or '-')}"
        parts.append(
            f"<section id='{e(cid)}'><h3>{e(cid)} <small>{head}</small></h3>"
            f"<p>{status} · 대리 판정 <b>{e(rv.get('verdict'))}</b> — {e(rv.get('notes'))}</p>"
            + (f"<p class='note'>사례 메모: {e(case.get('note'))}</p>" if case.get("note") else "")
            + "".join(turns)
            + f"<p class='cmd'>확인: <code>{cmd} --confirm {e(cid)} --by 이름</code>"
            f' · 틀림: <code>{cmd} --review {e(cid)} wrong "메모"</code></p></section>'
        )
    style = (
        "body{font:14px/1.6 system-ui,sans-serif;max-width:1100px;margin:24px auto;padding:0 16px}"
        "section{border:1px solid #ccc;border-radius:8px;padding:12px 16px;margin:16px 0}"
        "pre{background:#f4f4f4;padding:8px;white-space:pre-wrap;font-size:12.5px}"
        "pre.q{background:#e8f5ef}"
        "table{border-collapse:collapse;font-size:12px}td,th{border:1px solid #ccc;padding:3px 6px}"
        ".ok{color:#0a7a3f}.todo{color:#b45309}.cmd{font-size:12.5px;color:#555}.note{color:#555}"
    )
    page = (
        "<!DOCTYPE html><html lang='ko'><head><meta charset='utf-8'>"
        f"<title>매뉴얼 사례 샘플 검토표</title><style>{style}</style></head><body>"
        "<h1>매뉴얼 사례 샘플 검토표</h1>"
        "<p>사례마다 응답·결과 행이 샌드박스 원본 데이터(polestar_pg · 스키마 polestar)와 맞는지 "
        "확인한다. 대리 판정은 에이전트가 독립 SQL 로 대조한 결과이며 사람 확인을 대신하지 않는다. "
        "판정 기준: ok(표·요약 모두 맞음) · partly(표는 맞고 문장에 흠 — 매뉴얼 본문 포인트가 "
        "흠을 밝혔는지 확인) · wrong(싣지 않는다).</p>" + "".join(parts) + "</body></html>"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", action="store_true", help="녹화 서버에 실 실행(로컬 MLX)")
    ap.add_argument("--only", nargs="*", help="사례 ID 한정")
    ap.add_argument(
        "--profile",
        default="record",
        help="녹화 서버 프로필(record|record3) — 사례의 profile 과 같은 것만 보낸다",
    )
    ap.add_argument(
        "--review",
        nargs=3,
        metavar=("CASE", "VERDICT", "NOTES"),
        help="검토 판정 기록(ok|partly|wrong)",
    )
    ap.add_argument("--sheet", action="store_true", help="사람 확인용 검토표(HTML) 생성")
    ap.add_argument("--confirm", metavar="CASE", help="사람 확인 표지 기록(--by 필수)")
    ap.add_argument("--by", default="", help="--confirm: 확인한 사람")
    ap.add_argument("--notes", default="", help="--confirm: 메모")
    a = ap.parse_args()
    if a.sheet:
        print(sheet(BUILD / "review_sheet.html"))  # build/ 는 .gitignore — 커밋하지 않는다
    elif a.confirm:
        confirm(a.confirm, a.by, a.notes)
    elif a.review:
        review(*a.review)
    elif a.run:
        run(a.only, a.profile)
    else:
        sys.exit(1 if check() else 0)


if __name__ == "__main__":
    main()
