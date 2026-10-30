"""제니퍼 웹 세션 내부망 실측 — `plans/202` §7 2~6 · D-355.

뷰서버 웹 세션(`/login/page` → `JSESSIONID<포트>`)으로 `/agent/upgrade/list?sid=`가
어떻게 동작하는지 한 번에 점검한다. 출력에는 **비밀번호·쿠키 값·IP·호스트명을 남기지 않는다**
(쿠키는 이름만, 목록은 칸 이름·건수·분포만).

호출하는 경로: GET `/login`(TLS 확인·로그인 전 세션) · POST `/login/page` · GET `/agent/upgrade/list` ·
(T7b) Open API GET `/api/instance`. 리다이렉트는 따라가지 않는다. 로그인은 브라우저와 같은 순서
(`/login` 먼저 → `Origin`·`Referer` 헤더)로 하고, 첫 로그인이 실패하면 더 시도하지 않는다.

사용 예(게이트웨이 서버, `apm_gateway/`에서):
    ../.venv/bin/python scripts/probe_web_session.py --source cm_prd --sid 1001 --sid 1002
    ../.venv/bin/python scripts/probe_web_session.py --source b0 --url https://h:7901 --insecure \
        --sid 2001
    ../.venv/bin/python scripts/probe_web_session.py --source cm_stg --sid 3001 --bad-password
    ../.venv/bin/python scripts/probe_web_session.py --source cm_prd --sid 1001 --idle 10,30,60
    ../.venv/bin/python scripts/probe_web_session.py --source cm_prd --sid 1001 --dump-nodes /tmp/nodes.txt

접속값은 `apm_gateway/.env`의 `JENNIFER_<ID>_WEB_URL`(없으면 `_API_URL`)·`_WEB_USER`·
`_WEB_PASSWORD`에서 읽고, 없으면 `--url`·`--user`·프롬프트 입력을 쓴다.
"""

from __future__ import annotations

import argparse
import getpass
import json
import re
import ssl
import sys
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
LIST_PATH = "/agent/upgrade/list"
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
KEEP_FIELDS = (
    "instId", "sid", "hostName", "ip", "hostAddress", "status", "statusMsg", "lastLiveTime",
)


# ── 입력 ────────────────────────────────────────────────────────────────


def read_env(path: Path) -> dict[str, str]:
    """`.env`의 KEY=VALUE만 읽는다(os.environ에 넣지 않는다)."""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        # 감싼 따옴표 한 쌍만 벗긴다 — 비밀번호 끝의 따옴표 문자를 지우지 않게
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key.strip()] = value
    return out


def resolve_inputs(
    args: argparse.Namespace, *, need_password: bool = True
) -> tuple[str, str, str, str, str]:
    env = read_env(Path(args.env_file))
    prefix = f"JENNIFER_{args.source.upper()}_" if args.source else "JENNIFER_"
    url = args.url or env.get(prefix + "WEB_URL") or env.get(prefix + "API_URL") or ""
    user = args.user or env.get(prefix + "WEB_USER") or ""
    password = env.get(prefix + "WEB_PASSWORD") or ""
    args.pw_source = ".env" if password else "프롬프트"
    api_url = (env.get(prefix + "API_URL") or "").rstrip("/")
    token = env.get(prefix + "API_TOKEN") or ""
    if not url:
        sys.exit(f"접속 URL 없음 — --url 또는 .env {prefix}WEB_URL/{prefix}API_URL")
    if not need_password:
        return url.rstrip("/"), user, password, api_url, token
    if not user:
        user = input("웹 계정 ID: ").strip()
    if not password:
        password = getpass.getpass("웹 계정 비밀번호(화면에 표시 안 됨): ")
    return url.rstrip("/"), user, password, api_url, token


# ── 공통 ────────────────────────────────────────────────────────────────


class Probe:
    def __init__(self, base: str, verify: Any, timeout: float) -> None:
        self.base = base
        self.verify = verify
        self.timeout = timeout

    def client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.base, verify=self.verify, timeout=self.timeout, follow_redirects=False
        )

    def login(
        self, client: httpx.Client, user: str, password: str, *, plain: bool = False
    ) -> dict[str, Any]:
        """브라우저와 같은 순서로 로그인한다 — `/login`으로 세션을 먼저 받고 `Origin`·`Referer`를
        붙여 POST한다(b0 https는 단순 POST를 `/login`으로 되돌림 · 2026-10-29)."""
        info: dict[str, Any] = {}
        headers: dict[str, str] = {}
        if not plain:
            pre = client.get("/login", headers={"User-Agent": BROWSER_UA})
            jar = sorted(c.name for c in client.cookies.jar)
            info["사전 GET /login"] = f"{pre.status_code} 쿠키={jar}"
            parts = urlsplit(self.base)
            origin = f"{parts.scheme}://{parts.netloc}"
            headers = {"Origin": origin, "Referer": origin + "/login", "User-Agent": BROWSER_UA}
        r = client.post(
            "/login/page",
            data={"id": user, "password": password, "autoLogin": "false", "redirect": ""},
            headers=headers,
        )
        info["status"] = r.status_code
        info["location"] = _path_only(r.headers.get("location"))
        set_cookie = r.headers.get_list("set-cookie")
        info["set_cookie"] = sorted({_cookie_label(c) for c in set_cookie})
        info["body_hint"] = _text_hint(r.text) if r.status_code == 200 else ""
        return info

    def fetch_list(
        self, client: httpx.Client, sid: str, cookie_header: str | None = None
    ) -> dict[str, Any]:
        headers = {"Accept": "application/json, text/plain, */*"}
        if cookie_header is not None:
            headers["Cookie"] = cookie_header
        started = time.perf_counter()
        r = client.get(LIST_PATH, params={"sid": sid}, headers=headers)
        elapsed = int((time.perf_counter() - started) * 1000)
        out: dict[str, Any] = {
            "status": r.status_code,
            "location": _path_only(r.headers.get("location")),
            "ctype": r.headers.get("content-type", ""),
            "bytes": len(r.content),
            "ms": elapsed,
            "rows": None,
        }
        if r.status_code == 200:
            try:
                data = r.json()
            except (json.JSONDecodeError, ValueError):
                out["verdict"] = "200이지만 JSON 아님(로그인 화면일 가능성) — " + _text_hint(r.text)
                return out
            if isinstance(data, list):
                out["rows"] = data
                out["verdict"] = f"성공 · {len(data)}건"
            else:
                shape = sorted(data)[:8] if isinstance(data, dict) else type(data).__name__
                out["verdict"] = f"JSON이지만 배열 아님(최상위: {shape})"
        elif 300 <= r.status_code < 400:
            out["verdict"] = f"리다이렉트 → {out['location']}"
        else:
            out["verdict"] = f"실패 {r.status_code} — {_text_hint(r.text)}"
        return out


def _path_only(location: str | None) -> str:
    if not location:
        return "-"
    parts = urlsplit(location)
    # 호스트는 가리고 스킴·포트만 남긴다 — http → https 전환 리다이렉트를 구분하기 위해
    port = f":{parts.port}" if parts.port else ""
    head = f"{parts.scheme}://…{port}" if parts.scheme else ""
    if not parts.query:
        return head + parts.path
    query = "&".join(k.split("=")[0] + "=…" for k in parts.query.split("&"))
    return f"{head}{parts.path}?{query}"


def _cookie_label(set_cookie: str) -> str:
    """쿠키 이름만 남긴다. 삭제 지시(Max-Age=0·과거 Expires)면 「(삭제)」를 붙인다."""
    pair, _, attrs = set_cookie.partition(";")
    name, _, value = pair.partition("=")
    low = attrs.lower().replace(" ", "")
    deleted = not value.strip() or "max-age=0" in low or "1970" in low
    return name.strip() + ("(삭제)" if deleted else "")


def _short(text: str, n: int) -> str:
    t = " ".join(text.split())
    return t[:n] + ("…" if len(t) > n else "")


def _text_hint(html: str) -> str:
    title = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    plain = re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", html, flags=re.I | re.S)
    hint = f"title={_short(title.group(1), 40)!r} " if title else ""
    return hint + f"text={_short(plain, 120)!r}"


def cookie_header(client: httpx.Client, *, prefixes: tuple[str, ...]) -> tuple[str, list[str]]:
    picked = [(c.name, c.value) for c in client.cookies.jar if c.name.upper().startswith(prefixes)]
    return "; ".join(f"{n}={v}" for n, v in picked), [n for n, _ in picked]


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def line(key: str, value: Any) -> None:
    print(f"  {key:<22} {value}")


# ── 점검 ────────────────────────────────────────────────────────────────


def describe_login_page(html: str) -> None:
    """로그인 화면의 폼 구조만 보여 준다(값 없음) — 입력 칸 이름·암호화 스크립트 여부."""
    for form in re.finditer(r"<form\b([^>]*)>(.*?)</form>", html, re.I | re.S):
        attrs, inner = form.group(1), form.group(2)
        fields = [
            f"{_attr(tag, 'name') or '?'}:{_attr(tag, 'type') or 'text'}"
            for tag in re.findall(r"<(?:input|select|textarea)\b[^>]*>", inner, re.I)
        ]
        method = (_attr(attrs, "method") or "GET").upper()
        line("  로그인 폼", f"{method} {_path_only(_attr(attrs, 'action'))} 칸={fields}")
    srcs = [_path_only(x) for x in re.findall(r"<script[^>]+src\s*=\s*[\"']([^\"']+)", html, re.I)]
    line("  스크립트", srcs[:15] or "없음")
    words = ("rsa", "encrypt", "cryptojs", "sha256", "sha512", "publickey", "modulus", "otp")
    hits = sorted({w for w in words if w in html.lower()})
    line("  암호화·OTP 단서", hits or "없음")


_ATTR_VALUE = r"""\s*=\s*["']?([^"'\s>]+)"""


def _attr(tag: str, name: str) -> str:
    m = re.search(r"\b" + name + _ATTR_VALUE, tag, re.I)
    return m.group(1) if m else ""


def check_tls(p: Probe) -> bool:
    section("T0 접속·TLS")
    line("스킴", urlsplit(p.base).scheme)
    try:
        with p.client() as c:
            r = c.get("/login")
        line("GET /login", f"{r.status_code} {r.headers.get('content-type', '')}")
        if 300 <= r.status_code < 400:
            line("  → location", _path_only(r.headers.get("location")))
        elif r.status_code == 200:
            describe_login_page(r.text)
        return True
    except httpx.ConnectError as exc:
        cause = exc.__context__ or exc.__cause__
        cert = isinstance(cause, ssl.SSLCertVerificationError)
        if cert or "CERTIFICATE_VERIFY_FAILED" in str(exc):
            line("결과", "인증서 검증 실패 → 사설 CA. --ca <PEM>로 재실행(게이트웨이에도 CA 설정 필요)")
        else:
            line("결과", f"연결 실패: {type(exc).__name__}: {_short(str(exc), 120)}")
        return False
    except httpx.HTTPError as exc:
        line("결과", f"{type(exc).__name__}: {_short(str(exc), 120)}")
        return False


def summarize_rows(sid: str, res: dict[str, Any], keys_seen: Counter[str]) -> None:
    rows = res["rows"] or []
    line(f"sid={sid}", f"{res['verdict']} · {res['bytes']}B · {res['ms']}ms")
    if not rows:
        return
    for row in rows:
        keys_seen.update(row.keys() if isinstance(row, dict) else [])
    dicts = [r for r in rows if isinstance(r, dict)]
    inst = Counter(str(r.get("instId")) for r in dicts)
    dup = {k: v for k, v in inst.items() if v > 1}
    status = Counter(f"{r.get('status')}:{r.get('statusMsg')}" for r in dicts)
    pod = sum(
        1 for r in dicts if r.get("hostAddress") and r.get("ip") and r["hostAddress"] != r["ip"]
    )
    no_addr = sum(1 for r in dicts if not r.get("hostAddress"))
    missing = {f: sum(1 for r in dicts if f not in r) for f in KEEP_FIELDS}
    line("  instId 중복", f"{len(dup)}개 instId가 2회 이상(최대 {max(dup.values())}회)" if dup else "없음")
    line("  status:statusMsg", dict(status.most_common()))
    line("  hostAddress≠ip", f"{pod}/{len(dicts)}  (hostAddress 비어 있음 {no_addr})")
    gaps = {k: v for k, v in missing.items() if v}
    line("  허용 칸 누락", gaps or "없음")
    if dup:
        same_dup_diff = 0
        for k in dup:
            group = [r for r in dicts if str(r.get("instId")) == k]
            if len({(r.get("hostAddress"), r.get("ip")) for r in group}) > 1:
                same_dup_diff += 1
        line("  중복 중 주소 상이", f"{same_dup_diff}개 instId")


def compare_open_api(api: httpx.Client, sid: str, rows: list[Any]) -> None:
    try:
        r = api.get("/api/instance", params={"domain_id": sid})
        body = r.json() if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError) as exc:
        line(f"sid={sid}", f"Open API 실패: {type(exc).__name__}")
        return
    result = body.get("result") if isinstance(body, dict) else None
    if not isinstance(result, list):
        line(f"sid={sid}", f"Open API 응답 모양 다름(status {r.status_code})")
        return
    open_ids = {str(x.get("instanceId")) for x in result if isinstance(x, dict)}
    web_ids = {str(x.get("instId")) for x in rows if isinstance(x, dict)}
    line(
        f"sid={sid}",
        f"Open API {len(open_ids)} · 웹 {len(web_ids)} · 공통 {len(open_ids & web_ids)}"
        f" · Open API에만 {len(open_ids - web_ids)} · 웹에만 {len(web_ids - open_ids)}",
    )
    if open_ids - web_ids:
        line("  판정", "웹 목록이 일부만 반환 — 페이지·상한 파라미터 확인 필요(개발자도구 요청 URL)")


def dump_nodes(path: Path, web_rows: dict[str, list[Any]]) -> None:
    """파드 원소의 고유 노드 IP를 서버 안 로컬 파일로만 남긴다(화면에는 건수만) — 폴스타 등록률
    측정용(plans/202 검토 §1). 같은 이름 `.sql`에 존별로 돌릴 조회문을 함께 쓴다."""
    nodes = sorted(
        {
            str(r["hostAddress"])
            for rows in web_rows.values()
            for r in rows
            if isinstance(r, dict)
            and r.get("hostAddress")
            and r.get("ip")
            and r["hostAddress"] != r["ip"]
        }
    )
    path.write_text("\n".join(nodes) + "\n", encoding="utf-8")
    in_list = ", ".join(f"'{ip}'" for ip in nodes if re.fullmatch(r"[0-9.]+", ip))
    sql = (
        "-- 존마다(polestar_b0 = POLESTAR 스키마 · CM-GP/YD = polestar 스키마) 실행해 건수만 공유\n"
        "SELECT COUNT(DISTINCT r.ipaddress) AS matched_nodes\n"
        "FROM <스키마>.cmm_resource r\n"
        "WHERE r.resource_type = 'server.Server' AND r.dtime IS NULL\n"
        f"  AND r.ipaddress IN ({in_list or chr(39) * 2});\n"
    )
    sql_path = path.with_suffix(".sql")
    sql_path.write_text(sql, encoding="utf-8")
    for f in (path, sql_path):
        try:
            f.chmod(0o600)
        except OSError:
            pass
    line("노드 IP 파일", f"{path} · 고유 노드 {len(nodes)}개(값은 파일에만)")
    line("존별 조회문", f"{sql_path} — 존마다 실행해 matched_nodes 건수만 공유")


def run(args: argparse.Namespace) -> int:
    base, user, password, api_url, token = resolve_inputs(args, need_password=not args.inspect)
    verify: Any = False if args.insecure else (args.ca or True)
    p = Probe(base, verify, args.timeout)
    sids = args.sid
    first = sids[0]

    if not check_tls(p):
        return 2
    if args.inspect:
        print("\n--inspect: 로그인 없이 종료(로그인 실패 횟수를 늘리지 않음)")
        return 0

    # T1: 로그인 → 세션 A. 실패하면 더 시도하지 않는다(실패 누적 = 계정 잠금 위험)
    plain = args.plain_login
    section("T1 로그인 — " + ("단순 POST(--plain-login)" if plain else "브라우저 방식"))
    spaces = "있음 ⚠" if password != password.strip() else "없음"
    line("비밀번호 출처", f"{getattr(args, 'pw_source', '')} · 앞뒤 공백 {spaces}")
    a = p.client()
    info = p.login(a, user, password, plain=plain)
    for k, v in info.items():
        line(k, v)
    res_a = p.fetch_list(a, first)
    line("목록 조회", res_a["verdict"])
    if res_a["rows"] is None:
        print("\n로그인 실패 — 더 시도하지 않고 멈춘다. 결과를 공유하세요.")
        a.close()
        return 3

    # T2·T3: 같은 계정 두 번째 로그인 → 세션 B · 첫 세션 생존
    section("T2·T3 중복 로그인 — 두 번째 로그인 뒤 첫 세션(A) 유지?")
    b = p.client()
    p.login(b, user, password, plain=plain)
    res_b = p.fetch_list(b, first)
    line("세션 B 조회", res_b["verdict"])
    line("세션 A 재조회", p.fetch_list(a, first)["verdict"])

    live = b if res_b["rows"] is not None else a
    # T4: JSESSIONID만
    section("T4 JSESSIONID 쿠키만으로 조회 (§7-3)")
    header, names = cookie_header(live, prefixes=("JSESSIONID",))
    line("보유 쿠키 이름", sorted(c.name for c in live.cookies.jar))
    with p.client() as fresh:
        line(f"{names} 만", p.fetch_list(fresh, first, header)["verdict"])

    # T5: ID/PASSWORD 쿠키만(자동 로그인 가능성 — 보안 확인)
    section("T5 ID<포트>·PASSWORD<포트> 쿠키만으로 조회 (보안 확인)")
    header, names = cookie_header(live, prefixes=("ID", "PASSWORD"))
    if names:
        with p.client() as fresh:
            line(f"{names} 만", p.fetch_list(fresh, first, header)["verdict"])
    else:
        line("결과", "ID·PASSWORD 쿠키를 받지 않음(autoLogin=false) — 해당 없음")

    # T6: 만료·무효 세션 모양
    section("T6 무효 세션으로 조회 — 만료 시 모양 (§7-5)")
    jnames = cookie_header(live, prefixes=("JSESSIONID",))[1]
    jname = jnames[0] if jnames else "JSESSIONID"
    with p.client() as fresh:
        fake = f"{jname}=probe-invalid-session"
        line("가짜 JSESSIONID", p.fetch_list(fresh, first, fake)["verdict"])
    with p.client() as fresh:
        line("쿠키 없음", p.fetch_list(fresh, first, "")["verdict"])

    # T7: 목록 내용 집계
    section("T7 목록 내용 (§7-6) — 값 없이 건수·분포만")
    keys_seen: Counter[str] = Counter()
    web_rows: dict[str, list[Any]] = {}
    for sid in sids:
        res = p.fetch_list(live, sid)
        web_rows[sid] = res["rows"] or []
        summarize_rows(sid, res, keys_seen)
    if keys_seen:
        line("응답 칸 이름 전체", sorted(keys_seen))
    if args.dump_nodes:
        dump_nodes(Path(args.dump_nodes), web_rows)

    # T7b: Open API 인스턴스 목록과 대조 — 웹 목록 건수 상한(페이지) 여부
    section("T7b Open API /api/instance 대조 — 웹 목록이 잘리는지")
    if not (api_url and token):
        line("결과", "건너뜀(.env에 API_URL·API_TOKEN 없음)")
    else:
        with httpx.Client(
            base_url=api_url, verify=p.verify, timeout=p.timeout, follow_redirects=False,
            headers={"Authorization": f"Bearer {token}"},
        ) as api:
            for sid in sids:
                compare_open_api(api, sid, web_rows[sid])

    # T8: 잘못된 비밀번호 1회 (옵트인)
    if args.bad_password:
        section("T8 잘못된 비밀번호 1회 (§7-4) — 직후 정상 로그인으로 실패 횟수 초기화")
        with p.client() as bad:
            info = p.login(bad, user, password + "#probe-wrong", plain=plain)
            for k, v in info.items():
                line(k, v)
            line("실패 세션으로 조회", p.fetch_list(bad, first)["verdict"])
        with p.client() as reset:
            p.login(reset, user, password, plain=plain)
            line("정상 재로그인 후 조회", p.fetch_list(reset, first)["verdict"])

    # T9: 유휴 만료 관찰 (옵트인 · 오래 걸림)
    if args.idle:
        minutes = sorted({int(x) for x in args.idle.split(",") if x.strip()})
        section(f"T9 유휴 만료 관찰 — {minutes}분 (§7-5) · 최대 {max(minutes)}분 대기")
        sessions = []
        for m in minutes:
            c = p.client()
            p.login(c, user, password, plain=plain)
            sessions.append((m, c))
        print(f"  세션 {len(sessions)}개 로그인 완료 — 각자 처음이자 마지막 조회를 지정 시각에 1회 한다.")
        results: dict[int, str] = {}

        def wait_and_check(m: int, c: httpx.Client) -> None:
            time.sleep(m * 60)
            results[m] = p.fetch_list(c, first)["verdict"]
            print(f"  {m:>4}분 유휴 후: {results[m]}", flush=True)

        threads = [threading.Thread(target=wait_and_check, args=s, daemon=True) for s in sessions]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        for _, c in sessions:
            c.close()

    a.close()
    b.close()
    print("\n완료 — 위 출력에는 비밀값·쿠키 값·IP·호스트명이 없습니다. 그대로 공유해도 됩니다.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="제니퍼 웹 세션 내부망 실측(plans/202 §7)")
    ap.add_argument("--source", help="소스 id(b0·cm_prd·cm_stg) — .env JENNIFER_<ID>_* 사용")
    ap.add_argument("--url", help="뷰서버 기준 URL(예: http://host:7900) — .env보다 우선")
    ap.add_argument("--user", help="웹 계정 ID — .env보다 우선(비밀번호는 .env 또는 프롬프트만)")
    ap.add_argument(
        "--sid", action="append", required=True, help="도메인 id(반복 가능 · 첫 값으로 T1~T6)"
    )
    ap.add_argument("--env-file", default=str(PACKAGE_ROOT / ".env"))
    ap.add_argument("--ca", help="https 사설 CA PEM 경로")
    ap.add_argument("--insecure", action="store_true", help="인증서 검증 끔(진단 전용)")
    ap.add_argument("--timeout", type=float, default=10.0)
    ap.add_argument(
        "--dump-nodes", help="파드 노드 IP를 이 로컬 파일에 저장 + 존별 조회문(.sql) — 등록률 측정"
    )
    ap.add_argument(
        "--plain-login", action="store_true", help="사전 GET·Origin·Referer 없이 로그인(비교용)"
    )
    ap.add_argument("--inspect", action="store_true", help="T0(로그인 화면 구조)만 — 로그인하지 않음")
    ap.add_argument("--bad-password", action="store_true", help="T8 잘못된 비밀번호 1회 시도(잠금 정책 확인 후)")
    ap.add_argument("--idle", help="T9 유휴 만료 관찰 분 목록(예: 10,30,60)")
    return run(ap.parse_args())


if __name__ == "__main__":
    sys.exit(main())
