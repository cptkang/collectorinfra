"""1-2 측정 — 도구 규약 블록 + few-shot 블록 비용 (plans/148 1단계 1-2).

녹화본(`testdata/consumer_requests/`)에서 도구 집합을 꺼내 프록시가 systemPrompt에 덧붙이는
블록을 실제 렌더 함수로 만들고 길이를 잰다. 실 LLM 호출 0건 — 순수 렌더만 한다.

집합:
- (a) `deep_agent_tier1` — deepagents 내장 도구(녹화 요청 tools에서 합성 도구 제외) + 본체 1단 도구
- (b) `noise_track_b` — 노이즈 게이트 트랙 B 도구
- (c) `deepagents_builtin_only` — 참고: deepagents 내장 도구만

렌더 조건은 녹화된 소비자 요청과 같다 — `tool_choice` 미전송(=auto) · `parallel_tool_calls`
미전송(=true) · few-shot static · system 배치. 합계는 프록시가 systemPrompt에서 두 블록을 잇는
방식(`"\\n\\n"` 결합)대로 센다. 추정 토큰은 `estimate_tokens`(ASCII/4 + 비ASCII/1, 보수 추정)다.

사용: `cd fabrix_proxy && python scripts/measure_protocol_cost.py [--out <JSON>]`
결과: stdout 표 + `testdata/consumer_requests/protocol_cost.json`(기본).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROXY_ROOT = Path(__file__).resolve().parents[1]  # fabrix_proxy/
if str(PROXY_ROOT) not in sys.path:
    sys.path.insert(0, str(PROXY_ROOT))

from fabrix_proxy import tool_protocol as tp  # noqa: E402

RECORDS = PROXY_ROOT / "testdata" / "consumer_requests"
DEFAULT_OUT = RECORDS / "protocol_cost.json"
INPUT_LIMIT_TOKENS = 95_232  # 운영 KBGenAI 입력 한도(plans/148 §1)
LANGS = ("en", "ko")
SEPARATOR = "\n\n"  # convert.prepare_request의 systemPrompt 결합자
# 녹화 스크립트(record_consumer_requests.py `_lc_tools`)가 넣은 합성 도구 — 내장 도구가 아니다
SYNTHETIC_TOOL_NAMES = frozenset({"lookup_weather", "get_order_status"})


def _load(name: str) -> Any:
    return json.loads((RECORDS / name).read_text(encoding="utf-8"))


def _content_text(content: Any) -> str:
    """메시지 content(문자열 또는 parts 배열)를 텍스트로 잇는다."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(p.get("text", ""))
            for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        )
    return ""


def deepagents_first_request() -> dict[str, Any]:
    """녹화된 deepagents 미니 에이전트의 첫 요청 바디."""
    record = _load("deepagents_mini_agent.json")
    return dict(record["requests"][0]["body"])


def tool_sets() -> dict[str, list[dict[str, Any]]]:
    """측정 대상 도구 집합 3종."""
    body = deepagents_first_request()
    builtin = [
        t for t in body["tools"] if tp.tool_function(t).get("name") not in SYNTHETIC_TOOL_NAMES
    ]
    tier1 = _load("body_tools_deep_agent.json")["tools"]
    track_b = _load("body_tools_noise_track_b.json")["tools"]
    return {
        "deep_agent_tier1": builtin + tier1,
        "noise_track_b": track_b,
        "deepagents_builtin_only": builtin,
    }


def measure(tools: list[dict[str, Any]], lang: str, fewshot_data: dict[str, Any]) -> dict[str, Any]:
    """도구 집합 1개 × 언어 1개의 블록 길이를 잰다."""
    protocol = tp.render_protocol(
        tools, "auto", True, template=tp.load_protocol_template(lang), lang=lang
    )
    fewshot = tp.select_fewshot(fewshot_data, "static", tools, True)
    fewshot_block = tp.render_fewshot_block(fewshot, lang) if fewshot is not None else ""
    combined = SEPARATOR.join(p for p in (protocol, fewshot_block) if p)
    tokens = tp.estimate_tokens(combined)
    return {
        "lang": lang,
        "tool_count": len(tools),
        "protocol_chars": len(protocol),
        "fewshot_chars": len(fewshot_block),
        "total_chars": len(combined),
        "protocol_est_tokens": tp.estimate_tokens(protocol),
        "fewshot_est_tokens": tp.estimate_tokens(fewshot_block),
        "total_est_tokens": tokens,
        "pct_of_limit": round(tokens / INPUT_LIMIT_TOKENS * 100, 2),
    }


def run() -> dict[str, Any]:
    """전 집합 × 언어를 측정해 결과 dict를 만든다."""
    fewshot_data = tp.load_fewshot()
    sets = tool_sets()
    body = deepagents_first_request()
    system_text = "".join(
        _content_text(m.get("content")) for m in body["messages"] if m.get("role") == "system"
    )
    return {
        "plan": "plans/148 1-2",
        "input_limit_tokens": INPUT_LIMIT_TOKENS,
        "token_estimator": "estimate_tokens = ceil(ASCII/4 + 비ASCII/1) — F3 실측 전 보수 추정",
        "render_conditions": {
            "tool_choice": "auto",
            "parallel_tool_calls": True,
            "fewshot": "static",
            "fewshot_placement": "system",
        },
        "tool_sets": {
            name: [tp.tool_function(t).get("name") for t in tools] for name, tools in sets.items()
        },
        "results": {
            name: [measure(tools, lang, fewshot_data) for lang in LANGS]
            for name, tools in sets.items()
        },
        "reference": {
            "deepagents_system_message_chars": len(system_text),
            "deepagents_system_message_est_tokens": tp.estimate_tokens(system_text),
        },
    }


def render_table(result: dict[str, Any]) -> str:
    """결과를 고정폭 표로 렌더한다."""
    header = (
        f"{'집합':<26}{'언어':<5}{'도구':>5}{'규약 문자':>10}{'few-shot 문자':>14}"
        f"{'합계 문자':>10}{'추정 토큰':>10}{'한도 %':>8}"
    )
    lines = [header, "-" * len(header)]
    for name, rows in result["results"].items():
        for row in rows:
            lines.append(
                f"{name:<26}{row['lang']:<5}{row['tool_count']:>5}{row['protocol_chars']:>10}"
                f"{row['fewshot_chars']:>14}{row['total_chars']:>10}"
                f"{row['total_est_tokens']:>10}{row['pct_of_limit']:>8.2f}"
            )
    ref = result["reference"]
    lines.append("")
    lines.append(
        f"참고: deepagents system 메시지 {ref['deepagents_system_message_chars']}자 "
        f"(추정 {ref['deepagents_system_message_est_tokens']} 토큰) · "
        f"한도 {INPUT_LIMIT_TOKENS} 토큰"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """측정하고 표를 출력한 뒤 JSON을 쓴다."""
    parser = argparse.ArgumentParser(description="도구 규약 블록 + few-shot 비용 측정(1-2)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="결과 JSON 경로")
    args = parser.parse_args(argv)
    result = run()
    print(render_table(result))
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n저장: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
