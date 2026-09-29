"""제니퍼 Open API 녹화 하네스 (plans/87 §0.8 J0-L-a ④ · J0-L-b D1~D2).

§5.2(e) 허용목록의 GET 경로만 호출한다. 도메인 → 인스턴스 → 트랜잭션 순으로 식별자를
찾아가며 녹화하고, 응답을 마스킹해 출처 표지와 함께 fixture로 저장한다. 오류 응답
(예: 라이선스 없음 → "Domain is not connected")도 오류 모델 fixture로 저장한다.

사용:
    JENNIFER_API_TOKEN=... python record_openapi.py --source local-docker
    JENNIFER_API_TOKEN=... python record_openapi.py --source ops-masked --base https://<뷰 서버>

출력: ../recorded/<source>/*.json + index.json. 마스킹 전 원본은 --keep-raw일 때만
../recorded/raw/<시각>/(커밋 제외)에 남긴다. ops-masked는 호스트명·IP를 가명으로 바꾼다.
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
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from jennifer_catalog import ALLOWED, fixture_stem  # noqa: E402
from masking import Masker  # noqa: E402

HERE = Path(__file__).resolve().parent.parent
DEFAULT_METRICS = (
    "heap_used",
    "gc_time_usage",
    "service_rate",
    "service_time",
    "active_service",
    "error_count",
)


class NotAllowedError(ValueError):
    """허용목록 밖 호출 시도 — 네트워크 호출 전에 거부한다."""


@dataclass
class Response:
    status: int
    content_type: str
    body: bytes

    def json(self) -> Any:
        try:
            return json.loads(self.body)
        except ValueError:
            return None


def _first(d: dict, keys: tuple[str, ...]) -> Any:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def _result_list(payload: Any) -> list:
    if isinstance(payload, dict) and isinstance(payload.get("result"), list):
        return payload["result"]
    return []


def variant_of(resp: Response) -> str:
    if resp.status == 200:
        return "ok"
    text = resp.body.decode("utf-8", "replace")
    if "Domain is not connected" in text:
        return "domain_not_connected"
    if "Required request parameter" in text:
        return "missing_param"
    return f"status_{resp.status}"


class Recorder:
    def __init__(
        self,
        base: str,
        token: str,
        source: str,
        out_root: Path,
        jennifer_version: str = "unknown",
        keep_raw: bool = False,
        opener: urllib.request.OpenerDirector | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.base = base.rstrip("/")
        self.token = token
        self.source = source
        self.out = out_root / source
        self.raw = out_root / "raw" / time.strftime("%Y%m%d-%H%M%S") if keep_raw else None
        self.version = jennifer_version
        self.masker = Masker(pseudonymize=(source == "ops-masked"))
        self.opener = opener or urllib.request.build_opener()
        self.clock = clock
        self.index: list[dict] = []

    # --- 안전장치: 허용목록 정확 일치 · GET만 · 허용 키만 · token 쿼리 금지 ---
    def build_request(
        self, template: str, params: dict | None = None, path_vars: dict | None = None
    ) -> urllib.request.Request:
        ep = ALLOWED.get(template)
        if ep is None:
            raise NotAllowedError(f"허용목록 밖 경로: {template}")
        params = {k: v for k, v in (params or {}).items() if v is not None}
        if "token" in params:
            raise NotAllowedError("쿼리 token 금지")
        extra = set(params) - set(ep.required) - set(ep.optional)
        if extra:
            raise NotAllowedError(f"{template}에 허용되지 않은 쿼리 키: {sorted(extra)}")
        path = template
        for k, v in (path_vars or {}).items():
            path = path.replace("{" + k + "}", urllib.parse.quote(str(v), safe=""))
        if "{" in path:
            raise NotAllowedError(f"경로 변수 누락: {path}")
        url = self.base + path + ("?" + urllib.parse.urlencode(params) if params else "")
        return urllib.request.Request(
            url,
            method="GET",
            headers={"Authorization": f"Bearer {self.token}", "Accept": ep.accept},
        )

    def request(
        self, template: str, params: dict | None = None, path_vars: dict | None = None
    ) -> tuple[urllib.request.Request, Response]:
        req = self.build_request(template, params, path_vars)
        try:
            with self.opener.open(req, timeout=20) as r:
                return req, Response(r.status, r.headers.get("Content-Type", ""), r.read())
        except urllib.error.HTTPError as e:
            return req, Response(e.code, e.headers.get("Content-Type", ""), e.read())

    def get(
        self,
        template: str,
        params: dict | None = None,
        path_vars: dict | None = None,
        label: str = "",
    ) -> Response:
        req, resp = self.request(template, params, path_vars)
        self._save(template, req, params or {}, resp, label)
        return resp

    def _save(
        self, template: str, req: urllib.request.Request, params: dict, resp: Response, label: str
    ) -> None:
        variant = variant_of(resp)
        name = f"{fixture_stem(template)}__{variant}" + (f"__{label}" if label else "")
        before = self.masker.count
        payload = resp.json()
        response: dict[str, Any] = {"status": resp.status, "content_type": resp.content_type}
        if payload is not None:
            response["body_json"] = self.masker.mask(payload)
        elif template.endswith("profile.txt"):
            response["body_text"] = self.masker.mask_profile_text(
                resp.body.decode("utf-8", "replace")
            )
        else:
            response["body_text"] = self.masker.mask_text(resp.body.decode("utf-8", "replace"))
        fixture = {
            "fixture_version": 1,
            "source": self.source,
            "jennifer_version": self.version,
            "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "request": {
                "method": "GET",
                "template": template,
                "path": urllib.parse.urlsplit(req.full_url).path,
                "query": {k: str(v) for k, v in params.items()},
                "accept": req.get_header("Accept"),
            },
            "response": response,
            "variant": variant,
            "label": label,
            "masking": {"masked_values": self.masker.count - before},
        }
        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / f"{name}.json").write_text(
            json.dumps(fixture, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if self.raw is not None:
            self.raw.mkdir(parents=True, exist_ok=True)
            (self.raw / f"{name}.raw").write_bytes(resp.body)
        self.index.append(
            {
                "file": f"{name}.json",
                "template": template,
                "variant": variant,
                "label": label,
                "status": resp.status,
            }
        )

    # --- 발견 순서 ---
    def run(
        self,
        domain_fallback: int = 1000,
        window_minutes: int = 10,
        metrics: tuple[str, ...] = DEFAULT_METRICS,
        max_tx: int = 2,
    ) -> list[dict]:
        if self.out.exists():
            for f in self.out.glob("*.json"):
                f.unlink()
        now = int(self.clock() * 1000)
        start = now - window_minutes * 60_000
        hour_end = now // 3_600_000 * 3_600_000
        hour_start = hour_end - 3_600_000

        domains = [
            _first(d, ("domainId", "sid", "id"))
            for d in _result_list(self.get("/api/domain").json())
        ]
        domain = next((d for d in domains if d is not None), domain_fallback)
        rng = {"domain_id": domain, "start_time": start, "end_time": now}

        instances = [
            _first(i, ("instanceId", "instId", "id"))
            for i in _result_list(self.get("/api/instance", {"domain_id": domain}).json())
        ]
        instance = next((i for i in instances if i is not None), 10000)
        self.get("/api/realtime/instance", {"domain_id": domain})
        self.get("/api/activeService/list", {"domain_id": domain})

        catalog = (self.get("/api/metrics").json() or {}).get("result") or {}
        known = set(catalog.get("instance", [])) if isinstance(catalog, dict) else set()
        for m in metrics:
            if known and m not in known:
                continue
            self.get(
                "/api/dbmetrics/instance",
                {
                    "domain_id": domain,
                    "instance_id": instance,
                    "interval_minute": 1,
                    "metrics": m,
                    "start_time": start,
                    "end_time": now,
                },
                label=m,
            )

        txs: list[tuple[Any, Any]] = []
        # X-View는 1분 창 — 최근 창부터 거슬러 첫 비지 않은 창을 찾는다(저장은 첫 창과 찾은 창만)
        for k in range(window_minutes):
            w_end = now - k * 60_000
            q = {"domain_id": domain, "start_time": w_end - 60_000, "end_time": w_end}
            req, resp = self.request("/api/transaction/time", q)
            txs = [
                (t.get("txid"), _first(t, ("endTime", "collectTime", "startTime")))
                for t in _result_list(resp.json())
                if t.get("txid") is not None
            ]
            if k == 0 or txs:
                self._save("/api/transaction/time", req, q, resp, f"w{k}")
            if txs:
                break
        for i, (txid, t) in enumerate(txs[:max_tx] or [(1, now)]):
            q = {"domain_id": domain, "txid": txid, "time": t}
            for tpl in (
                "/api/transaction/txid",
                "/api/transaction/profile.txt",
                "/api/transaction/sql",
            ):
                self.get(tpl, q, label=f"tx{i}")

        self.get("/api/dbsearch/event", rng)
        self.get("/api/dbsearch/error", rng)
        hr = {"domain_id": domain, "start_time": hour_start, "end_time": hour_end}
        for tpl in ("/api/status/application", "/api/status/sql", "/api/status/external_call"):
            self.get(tpl, hr)
        self.get(
            "/api-v2/deploy/{domainId}", {"startTime": start, "endTime": now}, {"domainId": domain}
        )

        manifest = {
            "source": self.source,
            "jennifer_version": self.version,
            "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "masking_rules": sorted(self.masker.rules),
            "fixtures": self.index,
        }
        (self.out / "index.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return self.index


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:17900")
    ap.add_argument("--source", choices=("local-docker", "ops-masked"), default="local-docker")
    ap.add_argument("--out-root", default=str(HERE / "recorded"))
    ap.add_argument("--jennifer-version", default=os.environ.get("JENNIFER_VERSION", "unknown"))
    ap.add_argument("--domain-id", type=int, default=1000)
    ap.add_argument("--window-minutes", type=int, default=10)
    ap.add_argument("--keep-raw", action="store_true")
    args = ap.parse_args()
    token = os.environ.get("JENNIFER_API_TOKEN", "")
    if not token:
        print("JENNIFER_API_TOKEN이 없다", file=sys.stderr)
        return 2
    rec = Recorder(
        args.base, token, args.source, Path(args.out_root), args.jennifer_version, args.keep_raw
    )
    index = rec.run(domain_fallback=args.domain_id, window_minutes=args.window_minutes)
    for row in index:
        print(f"{row['status']:>4} {row['variant']:<22} {row['file']}")
    print(f"\n{len(index)}건 · 마스킹 규칙 {sorted(rec.masker.rules) or '없음'} · 저장 {rec.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
