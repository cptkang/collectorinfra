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
# 프롬프트 칼럼은 군마다 위치가 다르다(실측 2026-09-11).
#   A~E·J: | ID | 입력 질의 | ...        -> index 1
#   F     : | ID | 입력(1턴) | ... | 입력(2턴) | ...  -> index 1 과 3 (2턴)
#   H     : | ID | 양식 칼럼 구성 | 입력 질의 | ...   -> index 2
#   I     : | ID | 시나리오 | 예상 |      -> 산문. 프롬프트가 아니다
#   K     : | ID | 방법 | 측정 | 합격 기준 | -> 다른 군을 반복하는 실행 방법. 프롬프트가 아니다
# 고정 인덱스로 집으면 H군은 양식 파일 경로가, K군은 "B-01~B-06 각 5회 반복"이 프롬프트가 된다.
_PROMPT_HEADERS = ("입력 질의", "프롬프트", "입력(1턴)", "입력")
# F군의 "입력(2턴)"은 자연어가 아니라 **구조화 필드 값**이다
# (`selected_db_ids=[polestar_cm_gp]` · `[gp, yd]` · `b0 선택`). 자동으로 query 에 넣으면
# 거짓 프롬프트가 되므로, 2턴이 있는 군은 사람이 작성하도록 표시만 하고 원문은 notes 로 남긴다.
_STRUCTURED_SECOND_TURN_HEADERS = ("입력(2턴)",)
# 이 헤더가 프롬프트 칼럼 자리에 오면 그 군은 **프롬프트 미작성**이다(사람이 써야 한다).
_NOT_A_PROMPT_HEADERS = ("시나리오", "방법", "양식", "양식 칼럼 구성", "템플릿")

_DOC29_ID = re.compile(r"^([A-K])-(\d+)(?:-(\d+))?$")
_SYN_ID = re.compile(r"^SYN-([A-I])-(\d+[a-z]?)$")


def _table_rows(text: str) -> list[tuple[list[str], list[str]]]:
    """마크다운 표의 (데이터 행, 그 행이 속한 헤더)를 함께 돌려준다.

    헤더를 버리면 프롬프트가 몇 번째 칼럼인지 알 수 없다 - 고정 인덱스로 집는 순간
    H군은 양식 경로를, K군은 실행 방법을 프롬프트로 담게 된다(실측 2026-09-11).
    """
    rows: list[tuple[list[str], list[str]]] = []
    header: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            header = []          # 표가 끝나면 헤더도 끝난다
            continue
        # 셀 안의 이스케이프 `\|` 는 구분자가 아니다. 보호하지 않으면 H군의
        # "호스트명 \| IP주소 \| ..." 가 여섯 칼럼으로 쪼개져 이후 칼럼이 전부 밀린다.
        guarded = stripped.replace("\\|", "\x00")
        cells = [c.strip().replace("\x00", "|") for c in guarded.strip("|").split("|")]
        if not cells or set("".join(cells)) <= set("-: "):
            continue             # 구분선
        if not header:
            header = cells       # 표의 첫 행이 헤더다
            continue
        rows.append((cells, header))
    return rows


def _prompt_columns(header: list[str]) -> tuple[Optional[int], Optional[int]]:
    """헤더에서 (1턴 프롬프트, 2턴 프롬프트) 칼럼 위치를 찾는다. 없으면 None."""
    first = second = None
    for index, name in enumerate(header):
        if index == 0:
            continue             # ID 칼럼
        if first is None and name in _PROMPT_HEADERS:
            first = index
        elif name in _STRUCTURED_SECOND_TURN_HEADERS:
            second = index
    return first, second


def _clean(text: str) -> str:
    """표 셀에서 프롬프트를 꺼낸다. 백틱/굵게 표기를 벗긴다."""
    text = text.strip()
    text = re.sub(r"^`+|`+$", "", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    return text.strip()


def parse_doc29(path: Path = DOC29) -> dict[str, list[dict[str, Any]]]:
    text = path.read_text(encoding="utf-8")
    out: dict[str, list[dict[str, Any]]] = {}
    for cells, header in _table_rows(text):
        match = _DOC29_ID.match(cells[0])
        if not match or len(cells) < 2:
            continue
        group = match.group(1)
        first, second = _prompt_columns(header)

        # 프롬프트 칼럼이 없는 군(I: 시나리오 · K: 방법)은 **프롬프트로 단정하지 않는다.**
        # 원문을 그대로 두되 prompt_authored=false 로 표시해 러너가 사유와 함께 건너뛴다 -
        # 산문을 LLM 에 보내면 무의미한 결과에 돈만 나간다.
        authored = first is not None
        if first is None:
            first = 1

        prompt = _clean(cells[first]) if first < len(cells) else ""
        if not prompt:
            continue
        turns = [{"query": prompt, "notes": None}]
        if second is not None and second < len(cells):
            follow = _clean(cells[second])
            if follow and follow not in ("—", "-", "–"):
                # 2턴은 구조화 필드다. query 로 만들지 않고 사람 작성 대상으로 표시한다.
                authored = False

        used = {0, first}
        notes = " / ".join(c for i, c in enumerate(cells) if i not in used and c) or None
        turns[0]["notes"] = notes

        scenario_id = f"{group}-{match.group(2)}"
        turn_no = match.group(3)
        bucket = out.setdefault(group, [])
        existing = next((b for b in bucket if b["id"] == scenario_id), None)
        if existing is None:
            bucket.append({"id": scenario_id, "turns": turns, "authored": authored})
        elif turn_no:
            # 멀티턴(G-01-1, G-01-2 ...) - 같은 시나리오의 뒤 턴이다.
            existing["turns"].extend(turns)
    return out


def parse_synonym(path: Path = DOC_SYNONYM) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    out: list[dict[str, Any]] = []
    for cells, _header in _table_rows(text):
        match = _SYN_ID.match(cells[0])
        if not match or len(cells) < 2:
            continue
        prompt = _clean(cells[1])
        if not prompt:
            continue
        out.append({
            "id": cells[0],
            "authored": True,
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
        if not item.get("authored", True):
            lines.append("    # 원문이 프롬프트가 아니라 산문(시나리오/방법 서술)이다.")
            lines.append("    # 사람이 실제 프롬프트로 고쳐 쓰고 이 줄을 지울 때까지 러너가 건너뛴다.")
            lines.append("    prompt_authored: false")
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
