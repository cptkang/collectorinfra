"""plans/134 M-5 — 모의 게이트웨이의 다건 대상 `targets` 배치(계약 A-2) 흉내.

모의 게이트웨이(각 테스트 파일의 `_Gateway`)는 단건 응답 함수를 갖고 있다. 본체가 `targets`로
부르면 항목마다 그 항목의 대상 인자(`hostname` · `instance_name` · `instance_id` · `source_id` →
`source_ids`)로 단건 응답을 만들고 계약 모양 봉투로 합친다:

- 행 = 하위 행 + `target_index` · `batch[i]` = `{index, target, status, row_count, ...하위 봉투의
  행 밖 칸}`
- 일부 실패 = `partial` + `[한계] 대상 k/N 조회 실패: …` · 전부 실패 = 오류 봉투(코드가 모두
  같으면 그 코드 · 다르면 `api_error`) + `batch`
- 고지 = 하위 고지 합집합(중복 제거) · `limits` = 하위 한계 합집합 + 실패 줄

모의라서 작업 승격은 흉내 내지 않는다(작업 접수 배치는 테스트가 봉투를 직접 준다). 이 모듈은 테스트
수집 대상이 아니다(`test_` 접두 없음).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

#: 하위 봉투에서 `batch[i]`로 옮기지 않는 칸(계약 A-2 — 행·봉투 공통 칸)
_ROW_FREE = frozenset({"rows", "queried_at", "source", "source_kind", "tool", "row_count"})


def item_args(arguments: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    """배치 인자 + 항목 → 그 항목의 단건 호출 인자(항목 `source_id`는 `source_ids`로)."""
    args = {k: v for k, v in arguments.items() if k != "targets"}
    if item.get("source_id"):
        args["source_ids"] = [item["source_id"]]
    args.update({k: v for k, v in item.items() if k != "source_id"})
    return args


def _shown(item: dict[str, Any]) -> str:
    return str(item.get("hostname") or item.get("instance_name") or item)


def batch_envelope(tool: str, arguments: dict[str, Any],
                   single: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
    """`targets` 배치 1호출 → 계약 A-2 봉투."""
    targets = arguments["targets"]
    rows: list[dict[str, Any]] = []
    batch: list[dict[str, Any]] = []
    limits: list[str] = []
    disclosures: list[dict[str, Any]] = []
    failed: list[tuple[dict[str, Any], str, str]] = []
    head: dict[str, Any] = {}
    for i, item in enumerate(targets):
        env = single(item_args(arguments, item))
        entry: dict[str, Any] = {"index": i, "target": dict(item)}
        if env.get("error"):
            entry.update(status="error", error=env["error"], reason=env.get("reason", ""),
                         row_count=0)
            failed.append((item, str(env["error"]), str(env.get("reason", ""))))
        else:
            head = head or env
            sub_rows = env.get("rows") or []
            rows += [{**r, "target_index": i} for r in sub_rows]
            entry.update({k: v for k, v in env.items() if k not in _ROW_FREE})
            entry.update(status="ok", row_count=env.get("row_count", len(sub_rows)))
            limits += [x for x in env.get("limits") or [] if x not in limits]
            disclosures += [d for d in env.get("disclosures") or [] if d not in disclosures]
        batch.append(entry)
    if failed and len(failed) == len(targets):
        codes = {code for _, code, _ in failed}
        reason = "; ".join(f"{_shown(t)}({c}: {r})" for t, c, r in failed[:3])
        return {"error": codes.pop() if len(codes) == 1 else "api_error",
                "reason": f"대상 {len(targets)}개 모두 실패 — {reason}", "tool": tool,
                "batch": batch}
    out: dict[str, Any] = {
        "tool": tool, "rows": rows, "row_count": len(rows),
        "queried_at": head.get("queried_at"), "source": head.get("source", "apm"),
        "source_kind": head.get("source_kind", "apm_api"), "batch": batch,
        "limits": limits, "disclosures": disclosures,
    }
    if failed:
        out["partial"] = True
        shown = ", ".join(f"{_shown(t)}({c})" for t, c, _ in failed[:3])
        out["limits"] = [*limits, f"[한계] 대상 {len(failed)}/{len(targets)} 조회 실패: {shown}"]
    elif any(e.get("partial") for e in batch):
        out["partial"] = True
    return out


def reply_for(tool: str, arguments: dict[str, Any],
              single: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
    """`targets`가 있으면 배치 봉투, 없으면 단건 응답 그대로."""
    if "targets" not in arguments:
        return single(arguments)
    return batch_envelope(tool, arguments, single)


def expanded(calls: list[tuple[Any, ...]], tool: str) -> list[dict[str, Any]]:
    """기록된 호출 중 `tool` 호출을 대상별 인자로 편다(배치는 항목별 · 단건은 그대로)."""
    out: list[dict[str, Any]] = []
    for name, args, *_ in calls:
        if name != tool:
            continue
        out += ([item_args(args, item) for item in args["targets"]] if "targets" in args
                else [args])
    return out
