"""로컬 제니퍼 초기 설정 — 최고관리자 생성 · 로그인 · Open API 토큰 발급 (로컬 전용).

5.7.0.1 뷰 서버는 첫 기동 때 관리자 부트스트랩 토큰 파일을 만든다
(`server.view/db_view/security/admin-bootstrap.token`). 이 스크립트는 그 토큰으로
`POST /login/user/setup`을 호출해 관리자를 만들고, 로그인 세션으로
`POST /auth/token/create`(tokenType=0 = OPEN_API)를 호출해 토큰을 발급한다.
관리자가 이미 있으면 생성은 건너뛰고 로그인부터 한다.

사용:
    JENNIFER_LOCAL_ADMIN_ID=jadmin JENNIFER_LOCAL_ADMIN_PW=... \\
    python bootstrap_local.py [--base http://127.0.0.1:17900] \\
        [--container jennifer-local-jennifer-server-1]

발급한 토큰은 표준출력에 한 줄(`JENNIFER_API_TOKEN=...`)로 낸다 — 저장소 밖 파일로 받는다.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request

TOKEN_FILE = "/opt/jennifer/server.view/db_view/security/admin-bootstrap.token"
OPEN_API = 0  # /auth/token/create의 tokenType(int) — 목록 응답에는 "OPEN_API"로 보인다
VALID_MS = 30 * 24 * 3600 * 1000


def _post(opener, url: str, fields: dict) -> str:
    data = urllib.parse.urlencode(fields).encode()
    with opener.open(urllib.request.Request(url, data=data, method="POST"), timeout=15) as resp:
        return resp.read().decode("utf-8", "replace")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:17900")
    ap.add_argument("--container", default="jennifer-local-jennifer-server-1")
    ap.add_argument("--memo", default="plans/87 J0-L local")
    args = ap.parse_args()
    uid = os.environ.get("JENNIFER_LOCAL_ADMIN_ID", "")
    pw = os.environ.get("JENNIFER_LOCAL_ADMIN_PW", "")
    if not uid or not pw:
        print("JENNIFER_LOCAL_ADMIN_ID·JENNIFER_LOCAL_ADMIN_PW가 필요하다", file=sys.stderr)
        return 2
    base = args.base.rstrip("/")
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )

    boot = subprocess.run(
        ["docker", "exec", args.container, "cat", TOKEN_FILE], capture_output=True, text=True
    )
    if boot.returncode == 0 and boot.stdout.strip():
        res = _post(
            opener,
            f"{base}/login/user/setup",
            {"id": uid, "password": pw, "name": "Local Admin", "token": boot.stdout.strip()},
        )
        print(f"관리자 생성 응답: {res.strip()} (M0478 = 성공)", file=sys.stderr)
    else:
        print("부트스트랩 토큰 파일 없음 — 관리자가 이미 있다고 보고 로그인한다", file=sys.stderr)

    opener.open(f"{base}/login", timeout=15).read()
    _post(opener, f"{base}/login/page", {"id": uid, "password": pw})
    _post(
        opener,
        f"{base}/auth/token/create",
        {"tokenType": OPEN_API, "validTime": VALID_MS, "tokenCount": 1_000_000, "memo": args.memo},
    )
    with opener.open(f"{base}/auth/token/list", timeout=15) as resp:
        tokens = json.loads(resp.read())
    mine = [t for t in tokens if t.get("memo") == args.memo and t.get("type") == "OPEN_API"]
    if not mine:
        print("토큰 발급 확인 실패 — 로그인 실패일 수 있다", file=sys.stderr)
        return 1
    newest = max(mine, key=lambda t: t.get("createdDate", 0))
    print(f"JENNIFER_API_TOKEN={newest['key']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
