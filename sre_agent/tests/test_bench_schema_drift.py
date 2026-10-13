"""plans/151 W2 — 도구 스키마 스냅샷 · 드리프트 가드(§4.3).

실 LLM 0 · 네트워크는 127.0.0.1만. 「실 서버」 자리에는 소형 스냅샷을 그대로 내는 세계 서버를 띄운다
(MCP `list_tools` 왕복이 같은 경로다).
"""

from __future__ import annotations

import copy
import json
import socket
from pathlib import Path

import pytest
from investigation_bench import schema
from investigation_bench.__main__ import main as cli_main
from investigation_bench.world_server import WorldServer
from investigation_bench.worlds import load_world, load_worlds

_FIX = Path(__file__).resolve().parent / "fixtures" / "investigation_bench"


def _snapshot() -> schema.Snapshot:
    return schema.load_snapshot(_FIX / "tool_schemas.json")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live(tmp_path: Path):
    server = WorldServer(_snapshot(), {}, tmp_path / "calls.jsonl")
    ports = server.start({"polestar": 0, "apm": 0})
    yield {k: f"http://127.0.0.1:{v}/sse" for k, v in ports.items()}
    server.stop()


# ── 세계 YAML ↔ 스냅샷 ──


def test_fixture_worlds_pass_check() -> None:
    snap = _snapshot()
    for world in load_worlds([_FIX / "worlds"]).values():
        assert schema.check_world(world.data, snap) == []


def test_unknown_tool_or_arg_fails_check() -> None:
    """스냅샷에 없는 도구(tools 키·script call)·인자(match·args·key_evidence)를 잡는다."""
    problems = schema.check_world(load_world(_FIX / "invalid" / "X01.yaml").data, _snapshot())
    text = "\n".join(problems)
    assert "tools.polestar_disk_usage" in text
    assert "스냅샷에 없는 도구 'polestar_disk_usage'" in text
    assert "tools.polestar_metric_trend[0].match" in text and "없는 인자 'metric'" in text
    assert "key_evidence[0]" in text
    assert "script.model[0]" in text and "'hostname_typo'" in text
    assert "script.model[1]" in text and "'polestar_unknown_tool'" in text
    assert len(problems) == 5


def test_real_snapshot_shape() -> None:
    """저장소의 실 스냅샷이 서버별 묶음 · 이름순이고, 조사 프로파일(execute_sql 비노출)이다."""
    snap = schema.load_snapshot()
    names = {s: [t["name"] for t in snap[s]] for s in schema.SERVERS}
    for server in schema.SERVERS:
        assert names[server] == sorted(names[server]) and names[server]
    assert "list_sources" in names["polestar"] and "gateway_health" in names["apm"]
    assert "execute_sql" not in names["polestar"]
    assert not any(n.startswith("apm_") for n in names["polestar"])


# ── 해시 ──


def test_snapshot_hash_deterministic(tmp_path: Path) -> None:
    snap = _snapshot()
    h = schema.snapshot_hash(snap)
    assert h == schema.snapshot_hash(copy.deepcopy(snap))
    # 키 순서가 달라도 같은 해시
    reordered = {
        s: [dict(reversed(list(t.items()))) for t in snap[s]] for s in reversed(schema.SERVERS)
    }
    assert schema.snapshot_hash(reordered) == h
    # 쓰고 다시 읽어도 같은 바이트 · 같은 해시
    p1 = schema.write_snapshot(snap, tmp_path / "a.json")
    p2 = schema.write_snapshot(schema.load_snapshot(p1), tmp_path / "b.json")
    assert p1.read_bytes() == p2.read_bytes()
    assert schema.snapshot_hash(schema.load_snapshot(p2)) == h
    # 내용이 바뀌면 다른 해시
    changed = copy.deepcopy(snap)
    changed["polestar"][0]["description"] += " "
    assert schema.snapshot_hash(changed) != h


# ── 실 서버 대조 ──


def test_compare_without_live_server_is_unverified() -> None:
    port = _free_port()  # 아무도 듣지 않는 포트
    url = f"http://127.0.0.1:{port}/sse"
    out = schema.compare_with_live(_snapshot(), {"polestar": url, "apm": None}, timeout=3)
    assert out["status"] == schema.UNVERIFIED
    assert out["servers"]["polestar"]["status"] == schema.UNVERIFIED
    assert out["servers"]["apm"] == {"status": schema.UNVERIFIED, "reason": "URL 미지정"}
    assert out["snapshot_hash"] == schema.snapshot_hash(_snapshot())


def test_compare_with_live_match_and_drift(live: dict[str, str]) -> None:
    snap = _snapshot()
    assert schema.compare_with_live(snap, live)["status"] == schema.MATCH
    drifted = copy.deepcopy(snap)
    drifted["polestar"][0]["inputSchema"]["properties"]["new_arg"] = {"type": "string"}
    drifted["apm"] = drifted["apm"][1:]
    out = schema.compare_with_live(drifted, live)
    assert out["status"] == schema.DRIFT
    assert out["servers"]["polestar"]["diff"]["changed"] == [snap["polestar"][0]["name"]]
    assert out["servers"]["apm"]["diff"]["added"] == [snap["apm"][0]["name"]]


# ── CLI ──


def test_cli_schema_snapshot_roundtrip(live: dict[str, str], tmp_path: Path, capsys) -> None:
    """`--schema-snapshot`이 MCP로 받은 도구를 원본과 같은 바이트로 쓴다."""
    out = tmp_path / "tool_schemas.json"
    rc = cli_main(["--schema-snapshot", "--polestar-url", live["polestar"],
                   "--apm-url", live["apm"], "--out", str(out)])
    assert rc == 0
    assert out.read_text(encoding="utf-8") == schema.dump_snapshot(_snapshot())
    assert "polestar: 도구 5종" in capsys.readouterr().out


def test_cli_schema_snapshot_requires_polestar_url() -> None:
    with pytest.raises(SystemExit) as exc:
        cli_main(["--schema-snapshot"])
    assert exc.value.code == 2


def test_cli_schema_snapshot_server_down(tmp_path: Path) -> None:
    out = tmp_path / "s.json"
    rc = cli_main(["--schema-snapshot", "--polestar-url",
                   f"http://127.0.0.1:{_free_port()}/sse", "--out", str(out)])
    assert rc == 1 and not out.exists()


def test_snapshot_file_is_plain_json() -> None:
    data = json.loads((_FIX / "tool_schemas.json").read_text(encoding="utf-8"))
    assert list(data) == list(schema.SERVERS)
