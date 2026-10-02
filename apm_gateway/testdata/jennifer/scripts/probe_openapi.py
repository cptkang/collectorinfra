"""로컬 제니퍼 Open API 실측 프로브 (plans/87 §0.8 J0-L · §5.2(e) 허용목록 · 로컬 전용).

허용목록 경로·필수 파라미터·민감 GET·쓰기 경로 존재 여부·인증 방식을 호출해
상태 코드와 응답 구조(키 이름)를 기록한다. 쓰기 경로는 실행하지 않고 메서드를
어긋나게(GET) 보내 존재와 허용 메서드만 확인한다.

사용:
    JENNIFER_API_TOKEN=... python probe_openapi.py [--base http://127.0.0.1:17900] [--out DIR]

응답 본문은 --out 아래에 저장한다(기본 recorded/raw/ — 커밋 제외). 민감 경로는
본문을 저장하지 않고 키 이름만 남긴다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DOMAIN = 1000


def _window_ms(minutes: int) -> tuple[int, int]:
    end = int(time.time() * 1000)
    return end - minutes * 60_000, end


def _hour_window_ms() -> tuple[int, int]:
    end = int(time.time() // 3600 * 3600 * 1000)
    return end - 3_600_000, end


def build_checks() -> list[dict]:
    s10, e10 = _window_ms(10)
    s1, e1 = _window_ms(1)
    hs, he = _hour_window_ms()
    rng10 = {"domain_id": DOMAIN, "start_time": s10, "end_time": e10}
    checks: list[dict] = [
        # (A) 인증
        {
            "group": "auth",
            "name": "auth-test 토큰 없음",
            "path": "/api-v2/auth-test",
            "auth": "none",
        },
        {"group": "auth", "name": "auth-test Bearer", "path": "/api-v2/auth-test"},
        {"group": "auth", "name": "domain 쿼리 token", "path": "/api/domain", "auth": "query"},
        # (B) §5.2(e) 허용목록 — 필수 파라미터 포함
        {"group": "allow", "name": "domain", "path": "/api/domain"},
        {
            "group": "allow",
            "name": "instance",
            "path": "/api/instance",
            "params": {"domain_id": DOMAIN},
        },
        {
            "group": "allow",
            "name": "realtime/instance",
            "path": "/api/realtime/instance",
            "params": {"domain_id": DOMAIN},
        },
        {
            "group": "allow",
            "name": "dbmetrics/instance",
            "path": "/api/dbmetrics/instance",
            "params": {**rng10, "instance_id": 10000, "interval_minute": 1, "metrics": "tps"},
        },
        {"group": "allow", "name": "metrics", "path": "/api/metrics"},
        {
            "group": "allow",
            "name": "activeService/list",
            "path": "/api/activeService/list",
            "params": {"domain_id": DOMAIN},
        },
        {
            "group": "allow",
            "name": "transaction/time (1분)",
            "path": "/api/transaction/time",
            "params": {"domain_id": DOMAIN, "start_time": s1, "end_time": e1},
        },
        {
            "group": "allow",
            "name": "transaction/txid",
            "path": "/api/transaction/txid",
            "params": {"domain_id": DOMAIN, "txid": 1, "time": e1},
        },
        {
            "group": "allow",
            "name": "transaction/profile.txt",
            "path": "/api/transaction/profile.txt",
            "params": {"domain_id": DOMAIN, "txid": 1, "time": e1},
            "accept": "text/plain",
        },
        {
            "group": "param",
            "name": "profile.txt Accept json",
            "path": "/api/transaction/profile.txt",
            "params": {"domain_id": DOMAIN, "txid": 1, "time": e1},
        },
        {
            "group": "allow",
            "name": "transaction/sql",
            "path": "/api/transaction/sql",
            "params": {"domain_id": DOMAIN, "txid": 1, "time": e1},
        },
        {
            "group": "allow",
            "name": "dbsearch/event",
            "path": "/api/dbsearch/event",
            "params": rng10,
        },
        {
            "group": "allow",
            "name": "dbsearch/error",
            "path": "/api/dbsearch/error",
            "params": rng10,
        },
        {
            "group": "allow",
            "name": "status/application (시 단위)",
            "path": "/api/status/application",
            "params": {"domain_id": DOMAIN, "start_time": hs, "end_time": he},
        },
        {
            "group": "allow",
            "name": "status/sql (시 단위)",
            "path": "/api/status/sql",
            "params": {"domain_id": DOMAIN, "start_time": hs, "end_time": he},
        },
        {
            "group": "allow",
            "name": "status/external_call (시 단위)",
            "path": "/api/status/external_call",
            "params": {"domain_id": DOMAIN, "start_time": hs, "end_time": he},
        },
        {
            "group": "allow",
            "name": "deploy/{domainId}",
            "path": f"/api-v2/deploy/{DOMAIN}",
            "params": {"startTime": s10, "endTime": e10},
        },
        # (C) 필수 파라미터 누락·단위 위반
        {"group": "param", "name": "dbsearch/event 파라미터 없음", "path": "/api/dbsearch/event"},
        {
            "group": "param",
            "name": "dbsearch/event start 이름",
            "path": "/api/dbsearch/event",
            "params": {"domain_id": DOMAIN, "start": s10, "end": e10},
        },
        {
            "group": "param",
            "name": "transaction/profile.txt txid만",
            "path": "/api/transaction/profile.txt",
            "params": {"txid": 1},
            "accept": "text/plain",
        },
        {
            "group": "param",
            "name": "status/application 분 단위",
            "path": "/api/status/application",
            "params": {"domain_id": DOMAIN, "start_time": s10, "end_time": e10},
        },
        {
            "group": "param",
            "name": "transaction/time 10분 창",
            "path": "/api/transaction/time",
            "params": {"domain_id": DOMAIN, "start_time": s10, "end_time": e10},
        },
        # (D) 민감 GET — 본문 미저장
        {
            "group": "sensitive",
            "name": "auth/userlist",
            "path": "/api/auth/userlist",
            "sensitive": True,
        },
        {
            "group": "sensitive",
            "name": "auth/userlist.xml",
            "path": "/api/auth/userlist.xml",
            "sensitive": True,
        },
        {
            "group": "sensitive",
            "name": "restapi/users",
            "path": "/restapi/users",
            "sensitive": True,
        },
        {
            "group": "sensitive",
            "name": "environment-variable",
            "path": f"/api-v2/environment-variable/{DOMAIN}",
            "sensitive": True,
        },
        {
            "group": "sensitive",
            "name": "system-property-config",
            "path": "/api-v2/manage/data-server/system-property-config",
            "sensitive": True,
        },
        {
            "group": "sensitive",
            "name": "rule/event/error",
            "path": f"/api-v2/manage/rule/event/error/{DOMAIN}",
            "sensitive": True,
        },
        # (E) 쓰기·제어 경로 — 실행하지 않고 GET으로 존재·허용 메서드만 확인
        {
            "group": "write",
            "name": "data-server/control",
            "path": "/api-v2/manage/data-server/control",
        },
        {
            "group": "write",
            "name": "db/property/1000/copy",
            "path": "/api-v2/manage/data-server/db/property/1000/copy",
        },
        {"group": "write", "name": "domain-group", "path": "/api-v2/manage/domain-group"},
        {"group": "write", "name": "domain/put", "path": "/api-v2/manage/domain/put"},
        {"group": "write", "name": "manual-rdb-export", "path": "/api-v2/manual-rdb-export"},
        {
            "group": "write",
            "name": "rdb-export-password-override",
            "path": "/api-v2/configuration/rdb-export-password-override",
        },
        # (F) 변형
        {"group": "variant", "name": "domain.xml", "path": "/api/domain.xml"},
        {
            "group": "variant",
            "name": "realtime/instance.xml",
            "path": "/api/realtime/instance.xml",
            "params": {"domain_id": DOMAIN},
        },
        {"group": "variant", "name": "POST /api/domain", "path": "/api/domain", "method": "POST"},
    ]
    return checks


def _shape(body: bytes) -> object:
    try:
        data = json.loads(body)
    except ValueError:
        return {"non_json": body[:120].decode("utf-8", "replace")}
    if isinstance(data, dict):
        out: dict = {"keys": sorted(data)}
        inner = data.get("result")
        if isinstance(inner, list):
            out["result_len"] = len(inner)
            if inner and isinstance(inner[0], dict):
                out["result_item_keys"] = sorted(inner[0])
        return out
    if isinstance(data, list):
        return {
            "list_len": len(data),
            "item_keys": sorted(data[0]) if data and isinstance(data[0], dict) else None,
        }
    return {"scalar": type(data).__name__}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # 3xx를 그대로 기록한다
        return None


def run(base: str, token: str, out: Path) -> list[dict]:
    opener = urllib.request.build_opener(_NoRedirect)
    out.mkdir(parents=True, exist_ok=True)
    results = []
    for i, c in enumerate(build_checks(), 1):
        params = dict(c.get("params") or {})
        headers = {"Accept": c.get("accept", "application/json")}
        auth = c.get("auth", "bearer")
        if auth == "bearer":
            headers["Authorization"] = f"Bearer {token}"
        elif auth == "query":
            params["token"] = token
        url = base + c["path"] + ("?" + urllib.parse.urlencode(params) if params else "")
        req = urllib.request.Request(url, method=c.get("method", "GET"), headers=headers)
        t0 = time.monotonic()
        try:
            with opener.open(req, timeout=15) as resp:
                status, body, ctype = resp.status, resp.read(), resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            status, body, ctype = e.code, e.read(), e.headers.get("Content-Type", "")
        except (urllib.error.URLError, TimeoutError) as e:
            status, body, ctype = -1, str(e).encode(), ""
        elapsed = round((time.monotonic() - t0) * 1000)
        rec = {
            "no": i,
            "group": c["group"],
            "name": c["name"],
            "method": c.get("method", "GET"),
            "path": c["path"],
            "params": sorted(params) if params else [],
            "status": status,
            "content_type": ctype,
            "bytes": len(body),
            "elapsed_ms": elapsed,
            "shape": _shape(body),
            "source": "local-docker",
        }
        if not c.get("sensitive"):
            rec["body_head"] = body[:2000].decode("utf-8", "replace")
        results.append(rec)
        (out / f"{i:02d}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    (out / "summary.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:17900")
    ap.add_argument(
        "--out",
        default=str(
            Path(__file__).resolve().parent.parent
            / "recorded"
            / "raw"
            / time.strftime("%Y%m%d-%H%M%S")
        ),
    )
    args = ap.parse_args()
    token = os.environ.get("JENNIFER_API_TOKEN", "")
    if not token:
        print("JENNIFER_API_TOKEN이 없다", file=sys.stderr)
        return 2
    results = run(args.base.rstrip("/"), token, Path(args.out))
    for r in results:
        shape = r["shape"]
        short = (
            shape.get("non_json", "")[:60]
            if isinstance(shape, dict) and "non_json" in shape
            else json.dumps(shape, ensure_ascii=False)[:110]
        )
        print(
            f"{r['no']:02d} [{r['group']:9}] {r['method']:4} {r['status']:>4} "
            f"{r['elapsed_ms']:>5}ms  {r['name']:<34} {short}"
        )
    print(f"\n저장: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
