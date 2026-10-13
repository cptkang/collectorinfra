"""도구 스키마 스냅샷 · 드리프트 가드 (plans/154 §4.3 · D-324 ①).

실 `mcp_server`(조사 프로파일)·`apm_gateway`의 도구 표면을 **MCP 프로토콜 `list_tools`로** 받아
`testdata/investigation_bench/tool_schemas.json`에 서버별 묶음으로 남긴다. import가 아니라
프로토콜로 받으므로 패키지 경계(D-139 — 양방향 import 0)를 건드리지 않는다.

- 파일 모양: `{"polestar": [도구…], "apm": [도구…]}` — 도구는 이름순, 도구 안의 키 순서는 서버가 준
  그대로(LLM에 넘어가는 인자 순서를 보존한다).
- `snapshot_hash`는 키 순서와 무관한 정규 직렬화의 sha256이다(manifest 기록용 — W3).
- `compare_with_live`는 실 서버가 떠 있으면 다시 받아 대조하고, 안 떠 있으면 「미대조」를 돌려준다.
- `check_world`는 세계 YAML이 스냅샷에 없는 도구·인자를 쓰는지 본다(W4 린트가 재사용).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

SERVERS: tuple[str, ...] = ("polestar", "apm")
DEFAULT_SNAPSHOT_PATH = (
    Path(__file__).resolve().parents[2] / "testdata" / "investigation_bench" / "tool_schemas.json"
)
# 스냅샷에 남기는 도구 필드 — mcp 판마다 늘어나는 부가 필드(_meta·icons 등)로
# 해시가 흔들리지 않게 고정한다.
SNAPSHOT_FIELDS: tuple[str, ...] = (
    "name", "title", "description", "inputSchema", "outputSchema", "annotations",
)

MATCH = "일치"
DRIFT = "불일치"
UNVERIFIED = "미대조"

Snapshot = dict[str, list[dict[str, Any]]]


async def fetch_tools(url: str, timeout: float = 10.0) -> list[dict[str, Any]]:
    """SSE MCP 서버 하나의 `list_tools`를 받아 스냅샷 모양(이름순 · 고정 필드)으로 돌려준다."""
    from mcp import ClientSession
    from mcp.client.sse import sse_client

    async def _fetch() -> list[dict[str, Any]]:
        async with sse_client(url, timeout=timeout) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
        tools = []
        for tool in result.tools:
            dumped = tool.model_dump(mode="json", by_alias=True, exclude_none=True)
            tools.append({k: dumped[k] for k in SNAPSHOT_FIELDS if k in dumped})
        return sorted(tools, key=lambda t: t["name"])

    return await asyncio.wait_for(_fetch(), timeout=timeout)


def fetch_snapshot(urls: Mapping[str, str | None], timeout: float = 10.0) -> Snapshot:
    """서버별 URL로 스냅샷을 만든다. URL이 None인 서버는 빈 묶음이다. 접속 실패는 예외로 올린다."""

    async def _all() -> Snapshot:
        out: Snapshot = {}
        for server in SERVERS:
            url = urls.get(server)
            out[server] = await fetch_tools(url, timeout) if url else []
        return out

    return asyncio.run(_all())


def dump_snapshot(snapshot: Snapshot) -> str:
    """파일에 쓸 결정적 직렬화(서버 고정 순서 · 들여쓰기 2 · 끝 줄바꿈)."""
    ordered = {server: snapshot.get(server, []) for server in SERVERS}
    return json.dumps(ordered, ensure_ascii=False, indent=2) + "\n"


def write_snapshot(snapshot: Snapshot, path: Path = DEFAULT_SNAPSHOT_PATH) -> Path:
    """스냅샷을 파일로 쓴다(상위 디렉터리 생성)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_snapshot(snapshot), encoding="utf-8")
    return path


def load_snapshot(path: Path = DEFAULT_SNAPSHOT_PATH) -> Snapshot:
    """스냅샷 파일을 읽는다."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {server: list(data.get(server) or []) for server in SERVERS}


def _canonical(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def snapshot_hash(snapshot: Snapshot) -> str:
    """스냅샷 내용 해시(sha256 hex) — 키 순서·들여쓰기와 무관하다."""
    ordered = {server: snapshot.get(server, []) for server in SERVERS}
    return hashlib.sha256(_canonical(ordered).encode("utf-8")).hexdigest()


def _diff(saved: list[dict[str, Any]], live: list[dict[str, Any]]) -> dict[str, list[str]]:
    a = {t["name"]: _canonical(t) for t in saved}
    b = {t["name"]: _canonical(t) for t in live}
    return {
        "added": sorted(set(b) - set(a)),
        "removed": sorted(set(a) - set(b)),
        "changed": sorted(n for n in set(a) & set(b) if a[n] != b[n]),
    }


def compare_with_live(
    snapshot: Snapshot, urls: Mapping[str, str | None], timeout: float = 5.0
) -> dict[str, Any]:
    """저장된 스냅샷을 실 서버 `list_tools`와 대조한다.

    서버별로 `일치`·`불일치`·`미대조`(URL 미지정·접속 실패)를 매기고, 전체 상태는
    불일치 > 미대조 > 일치 순으로 정한다. 스냅샷에 도구가 없는 서버는 대조 대상이 아니다.

    Returns:
        `{"status", "snapshot_hash", "servers": {서버: {"status", "reason"?, "diff"?}}}`
    """
    servers: dict[str, dict[str, Any]] = {}
    for server in SERVERS:
        saved = snapshot.get(server) or []
        url = urls.get(server)
        if not saved and not url:
            continue
        if not url:
            servers[server] = {"status": UNVERIFIED, "reason": "URL 미지정"}
            continue
        try:
            live = asyncio.run(fetch_tools(url, timeout))
        except Exception as e:  # 접속 실패·시간 초과 — 실 서버가 안 떠 있다
            servers[server] = {"status": UNVERIFIED, "reason": f"{type(e).__name__}: {e}"[:300]}
            continue
        diff = _diff(saved, live)
        servers[server] = {
            "status": DRIFT if any(diff.values()) else MATCH,
            "diff": diff,
        }
    statuses = {s["status"] for s in servers.values()}
    if DRIFT in statuses:
        status = DRIFT
    elif UNVERIFIED in statuses or not servers:
        status = UNVERIFIED
    else:
        status = MATCH
    return {"status": status, "snapshot_hash": snapshot_hash(snapshot), "servers": servers}


def tool_index(snapshot: Snapshot) -> dict[str, dict[str, Any]]:
    """도구 이름 → 도구 정의(전 서버 합집합)."""
    return {t["name"]: t for server in SERVERS for t in snapshot.get(server) or []}


def tool_properties(tool: Mapping[str, Any]) -> dict[str, Any]:
    """도구 `inputSchema.properties`."""
    return dict((tool.get("inputSchema") or {}).get("properties") or {})


def check_world(world: Mapping[str, Any], snapshot: Snapshot) -> list[str]:
    """세계 YAML(원문 dict)이 스냅샷에 없는 도구·인자를 쓰는지 검사한다. 문제 목록(빈 목록 = 통과).

    대상: `tools:` 키와 규칙의 `match` 인자 · `key_evidence`의 `tool`·`match` ·
    `script`(모범·오답 대본)의 `call`·`args`.
    """
    index = tool_index(snapshot)
    wid = world.get("id", "?")
    problems: list[str] = []

    def _check(where: str, name: Any, args: Any) -> None:
        tool = index.get(str(name))
        if tool is None:
            problems.append(f"{wid}: {where}: 스냅샷에 없는 도구 {name!r}")
            return
        props = tool_properties(tool)
        for key in sorted((args or {}).keys()):
            if key not in props:
                problems.append(f"{wid}: {where}: 도구 {name!r}에 없는 인자 {key!r}")

    for name, rules in (world.get("tools") or {}).items():
        if name == "default":
            continue
        if not isinstance(rules, list):
            problems.append(f"{wid}: tools.{name}: 규칙은 목록이어야 한다")
            continue
        if not rules:
            _check(f"tools.{name}", name, {})
        for i, rule in enumerate(rules):
            _check(f"tools.{name}[{i}].match", name, (rule or {}).get("match"))

    for i, ev in enumerate(world.get("key_evidence") or []):
        _check(f"key_evidence[{i}]", ev.get("tool"), ev.get("match"))

    script = world.get("script") or {}
    scripts: list[tuple[str, Any]] = [("script.model", script.get("model"))]
    scripts += [(f"script.neg.{code}", steps) for code, steps in (script.get("neg") or {}).items()]
    for where, steps in scripts:
        for j, step in enumerate(steps or []):
            if isinstance(step, Mapping) and "call" in step:
                _check(f"{where}[{j}]", step["call"], step.get("args"))
    return problems
