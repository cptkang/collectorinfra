"""큰 응답 — 메모리 임계 초과 본문의 스풀 · 원소 단위 점진 디코드 · 임시 파일 정리 (plans/134 W0-B
N-18 · SPEC-apm-question-coverage §3.5 · D-296 ④ — 오류로 끊지 않는다).
"""

from __future__ import annotations

import json

import httpx
import pytest
from apm_gateway.adapters.jennifer.client import JenniferClient
from apm_gateway.adapters.json_stream import load_json_file
from apm_gateway.domain.errors import ApmError

from apm_gateway.config import JenniferApiConfig

MIB = 1024 * 1024


def _rows(n: int) -> list[dict]:
    return [
        {
            "instanceId": i,
            "name": f"인스턴스-{i:06d}",
            "hostName": f"was-{i:06d}.example.local",
            "description": "x" * 40,
            "ratio": i / 7,
            "flags": [True, None, {"k": i}],
        }
        for i in range(n)
    ]


def _client(handler, spool) -> JenniferClient:
    return JenniferClient(
        JenniferApiConfig(url="http://apm.test", token="t", rate_limit_per_sec=0),
        transport=httpx.MockTransport(handler),
        spool_dir=spool,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("streamed", [False, True])
async def test_over_10mib_result_is_parsed_whole_and_temp_removed(tmp_path, streamed):
    rows = _rows(60_000)
    raw = json.dumps({"result": rows}, ensure_ascii=False).encode("utf-8")
    assert len(raw) > 10 * MIB  # 기본 메모리 임계 4 MiB의 두 배 넘게

    def handler(request):
        if streamed:  # Content-Length 없이 흘려 보낸다
            pieces = [raw[i : i + 65536] for i in range(0, len(raw), 65536)]
            return httpx.Response(200, stream=httpx.ByteStream(b"".join(pieces)))
        return httpx.Response(200, content=raw, headers={"content-type": "application/json"})

    spool = tmp_path / "tmp"
    client = _client(handler, spool)
    body = await client.get_json("/api/instance", {"domain_id": 1000})
    await client.aclose()
    assert body == {"result": rows}
    assert list(spool.iterdir()) == []  # 임시 파일은 파싱 뒤 지운다


@pytest.mark.asyncio
async def test_big_error_body_is_classified_from_head_and_removed(tmp_path):
    message = "1000 Domain is not connected " + "y" * (5 * MIB)
    raw = json.dumps({"exception": {"message": message}}).encode()
    spool = tmp_path / "tmp"
    client = _client(lambda r: httpx.Response(500, content=raw), spool)
    with pytest.raises(ApmError) as exc:
        await client.get_json("/api/instance", {"domain_id": 1000})
    await client.aclose()
    assert exc.value.code == "apm_api_error" or exc.value.code == "source_unavailable"
    assert len(exc.value.reason) <= 240 and list(spool.iterdir()) == []


@pytest.mark.asyncio
async def test_big_text_response_is_read_from_file(tmp_path):
    text = "\n".join(f"line {i} --password hunter{i}" for i in range(200_000))
    spool = tmp_path / "tmp"
    client = JenniferClient(
        JenniferApiConfig(url="http://apm.test", token="t", rate_limit_per_sec=0),
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, text=text, headers={"content-type": "text/plain"})
        ),
        spool_dir=spool,
    )
    out = await client.get_text(
        "/api/transaction/profile.txt", {"domain_id": 1, "txid": 2, "time": 3}
    )
    await client.aclose()
    assert out.count("\n") == 199_999 and "hunter" not in out  # 자격증명 제거도 지난다
    assert list(spool.iterdir()) == []


# ── 점진 디코드 단위 ─────────────────────────────────────────


@pytest.mark.parametrize(
    "value",
    [
        {"result": [1, 22, 333, -4.5e10, 'a\\"b]', {"k": [1, {"x": "}"}]}, True, None], "n": 3},
        {"result": []},
        {},
        [1, 2, {"a": "가나다라마바사" * 50}],
        [],
        "문자열 하나",
        123456789,
        {"meta": {"total": 2}, "result": [{"한글": "값" * 1000}, 9999999999999]},
    ],
)
def test_incremental_decoder_matches_json_loads_with_tiny_chunks(tmp_path, value):
    path = tmp_path / "body.json"
    path.write_text(" \n" + json.dumps(value, ensure_ascii=False) + "\n ", encoding="utf-8")
    for chunk in (1, 3, 7, 64, 1 << 20):  # 숫자·멀티바이트 문자가 조각 경계에서 끊기는 경우 포함
        assert load_json_file(path, chunk_bytes=chunk) == value, chunk


@pytest.mark.parametrize(
    "text",
    ['{"result": [1, 2', '{"result": [1 2]}', '[1, 2] x', '{"a" 1}', "", '{"result": [1,]}'],
)
def test_incremental_decoder_rejects_malformed(tmp_path, text):
    path = tmp_path / "bad.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        load_json_file(path, chunk_bytes=4)
