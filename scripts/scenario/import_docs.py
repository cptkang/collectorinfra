"""문서 145건 -> 시나리오 YAML 초안 (plans/94 §3.4 · 1회성).

파서가 채우는 것: id · title · turns[].send.query · plans(군->계획서 매핑표) · notes(원문)
**사람이 채우는 것: expect 단언.** 초안은 `expect: {manual_review: "<원문>"}` 으로 두고,
단언으로 옮긴 만큼만 자동 판정 대상이 된다. 옮기지 않은 것은 리포트 9절에 계속 남는다 -
침묵 누락을 만들지 않는 것이 요점이다.

자동 변환만으로는 `expect` 가 산문이라 기계 단언이 되지 않는다. 이 스크립트는 이관의
기계적인 절반만 한다.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any, Optional

from . import REPO_ROOT, utf8_open

DOC29 = REPO_ROOT / "docs" / "29_query_performance_test_plan.md"
DOC_SYNONYM = REPO_ROOT / "docs" / "synonym_test_cases.md"
OUT_DIR = REPO_ROOT / "testdata" / "scenarios"
# 폼필 초안의 자리표 양식. 케이스별 실제 양식은 사람이 채운다.
PLACEHOLDER_FORM = "testdata/scenarios/fixtures/form_sample.xlsx"

# 군 -> (파일명, 이름, 목표 ms, 계획서, 엔드포인트) — §1.5 클러스터 표의 기계 판독본.
GROUP_SPEC: dict[str, tuple[str, str, int, list[int], str]] = {
    "A": ("a_routing.yaml", "의도 분류/라우팅", 5_000, [9, 79], "stream"),
    "B": ("b_resource.yaml", "단순 조회 - 서버 구성(EAV)", 10_000, [20, 21, 25, 32, 33, 37, 42, 43], "stream"),
    "C": ("c_metric.yaml", "성능 통계 - 기간 파싱/집계", 30_000, [61, 69], "stream"),
    "D": ("d_alarm.yaml", "알람 조회", 10_000, [44, 45], "stream"),
    "E": ("e_composite.yaml", "복합/멀티인텐트", 30_000, [88, 82, 80, 78], "stream"),
    "F": ("f_zone_hitl.yaml", "존 선택 HITL/멀티DB", 30_000, [82, 90, 75], "stream"),
    "G": ("g_multiturn.yaml", "멀티턴 - 지시어/승계", 30_000, [50], "stream"),
    "H": ("h_formfill.yaml", "폼필 (Excel/Word)", 60_000, [10, 19, 72, 73, 58, 35], "file_stream"),
    "I": ("i_formfill_hitl.yaml", "폼필 HITL/기억", 60_000, [73, 58], "file_stream"),
    "J": ("j_guard.yaml", "가드/안전성", 30_000, [7, 41], "stream"),
    "K": ("k_load.yaml", "부하/성능", 30_000, [67, 84], "stream"),
    "L": ("l_synonym.yaml", "유사어/용어 (SYN A~I)", 30_000, [37, 61], "stream"),
}

# G군은 G-01-1 처럼 멀티턴 행이라 세 번째 조각이 턴 번호다.
_DOC29_ID = re.compile(r"^([A-K])-(\d+)(?:-(\d+))?$")
_SYN_ID = re.compile(r"^SYN-([A-I])-(\d+[a-z]?)$")


def _table_rows(text: str) -> list[list[str]]:
    """마크다운 표의 데이터 행만 뽑는다. 구분선과 헤더는 버린다."""
    rows: list[list[str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if not cells or set("".join(cells)) <= set("-: "):
            continue
        rows.append(cells)
    return rows


def _clean(text: str) -> str:
    """표 셀에서 프롬프트를 꺼낸다. 백틱/굵게 표기를 벗긴다."""
    text = text.strip()
    text = re.sub(r"^`+|`+$", "", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    return text.strip()


def parse_doc29(path: Path = DOC29) -> dict[str, list[dict[str, Any]]]:
    text = path.read_text(encoding="utf-8")
    out: dict[str, list[dict[str, Any]]] = {}
    for cells in _table_rows(text):
        match = _DOC29_ID.match(cells[0])
        if not match or len(cells) < 2:
            continue
        group = match.group(1)
        prompt = _clean(cells[1])
        if not prompt:
            continue
        scenario_id = f"{group}-{match.group(2)}"
        turn_no = match.group(3)
        notes = " / ".join(c for c in cells[2:] if c) or None
        bucket = out.setdefault(group, [])
        existing = next((b for b in bucket if b["id"] == scenario_id), None)
        if existing is None:
            bucket.append({"id": scenario_id, "turns": [{"query": prompt, "notes": notes}]})
        elif turn_no:
            # 멀티턴(G-01-1, G-01-2 ...) - 같은 시나리오의 뒤 턴이다.
            existing["turns"].append({"query": prompt, "notes": notes})
    return out


def parse_synonym(path: Path = DOC_SYNONYM) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    out: list[dict[str, Any]] = []
    for cells in _table_rows(text):
        match = _SYN_ID.match(cells[0])
        if not match or len(cells) < 2:
            continue
        prompt = _clean(cells[1])
        if not prompt:
            continue
        out.append({
            "id": cells[0],
            "turns": [{
                "query": prompt,
                "notes": " / ".join(c for c in cells[2:] if c and c != "☐") or None,
            }],
        })
    return out


def _yaml_quote(text: str) -> str:
    """YAML 겹따옴표 스칼라로 감싼다. 프롬프트에는 콜론/따옴표가 흔하다."""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    escaped = escaped.replace("\n", " ")
    return f'"{escaped}"'


def render_group(group: str, items: list[dict[str, Any]], env: str) -> str:
    filename, name, target, plans, endpoint = GROUP_SPEC[group]
    lines = [
        "# 자동 생성 초안 (scripts/scenario/import_docs.py) - plans/94 §3.4",
        "#",
        "# expect 는 비어 있고 manual_review 만 들어 있다. 단언으로 옮긴 만큼만 자동 판정",
        "# 대상이 되며, 옮기지 않은 것은 리포트 9절 '수동 검토'에 계속 남는다.",
        "# plans 필드는 군 단위 기본값이다 - 시나리오별로 정확히 좁히는 것이 사람의 일이다.",
        "version: 1",
        "group:",
        f"  id: {group}",
        f"  name: {_yaml_quote(name)}",
        f"  latency_target_ms: {target}",
        "  policy_confirmed: false",
        f"  source: {_yaml_quote('docs/29' if group != 'L' else 'docs/synonym_test_cases.md')}",
        "scenarios:",
    ]
    for item in items:
        turns = item["turns"]
        lines.append(f"  - id: {item['id']}")
        lines.append(f"    plans: [{', '.join(str(p) for p in plans)}]")
        lines.append(f"    title: {_yaml_quote(turns[0]['query'][:70])}")
        lines.append("    kind: normal")
        lines.append(f"    env: {env}")
        lines.append("    profile: baseline")
        lines.append(f"    endpoint: {endpoint}")
        if endpoint in ("file", "file_stream"):
            # 자리표 양식이다. 케이스별 실제 양식으로 바꾸는 것이 사람의 일이다(§3.4).
            lines.append(f"    upload: {_yaml_quote(PLACEHOLDER_FORM)}")
        lines.append("    turns:")
        for turn in turns:
            lines.append("      - send:")
            lines.append(f"          query: {_yaml_quote(turn['query'])}")
            lines.append("        expect:")
            lines.append(
                "          manual_review: "
                + _yaml_quote((turn.get("notes") or "원문 기대 결과 미기재")[:300])
            )
        if len(turns) > 1:
            lines.append("    teardown: [drop_thread]")
    return "\n".join(lines) + "\n"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="문서 -> 시나리오 YAML 초안")
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    parser.add_argument("--prefix", default="",
                        help="파일 접두어 (재실행 시 정본과 나란히 두고 비교할 때)")
    parser.add_argument("--force", action="store_true",
                        help="이미 있는 파일을 덮어쓴다 (사람이 채운 expect 가 사라진다)")
    args = parser.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    doc29 = parse_doc29()
    synonym = parse_synonym()

    def emit(group: str, items: list[dict[str, Any]], env: str) -> int:
        path = args.out / f"{args.prefix}{GROUP_SPEC[group][0]}"
        if path.exists() and not args.force:
            # 사람이 채운 expect 를 덮어쓰지 않는다 - 이관의 절반은 사람 작업이다(§3.4).
            print(f"  {path.name}: 건너뜀 (이미 있음 - 덮어쓰려면 --force)")
            return 0
        with utf8_open(path, "w") as handle:
            handle.write(render_group(group, items, env=env))
        print(f"  {path.name}: {len(items)}건")
        return len(items)

    total = 0
    for group, items in sorted(doc29.items()):
        total += emit(group, items, env="closed")
    if synonym:
        total += emit("L", synonym, env="sandbox")

    print(f"초안 {total}건 생성. expect 단언은 사람이 채운다 (§3.4).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
