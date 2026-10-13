"""few-shot 개선 루프 도구 — 과적합 검사 · 회차 비교 · 오프라인 재현 · 수정 이력.

plans/152 §3.9 · W5 · D-323.

외부망에서 F1~F4(보기 예문·분해 프롬프트 APM 절·교차 예시·재선택 프롬프트)를 고치고 다음 내부망
회차로 효과를 재는 루프의 로직이다. CLI 연결은 `__main__`이 한다(통합 Wave).

- `fewshot_check` — 예문이 벤치 프롬프트를 베꼈는지(문자 n-gram 자카드) + APM 절 크기 상한
- `compare` — 반출된 두 run(`run.json`·`trace.jsonl`) 대조: **holdout 턴 정확도 상승**이 판정 기준 ·
  대상 실패 ID 해소 · 다른 ID 악화 · 예문 수정 2회 뒤 잔존 = 결정적 처리 승격
- `replay` — dev 실패 턴(기본)의 분해·보기 재선택 단계만 현재 프롬프트로 다시 돌린다(LLM 주입 ·
  방향 확인용)
- `load_log`·`append_entry`·`fix_counts` — `testdata/apm_bench/fewshot_log.yaml`
- `fix_candidates` — few-shot 수정 후보(**개발셋만** — 권고 ③ · holdout 은 수정 대상으로
  내놓지 않는다)

E1(외부망 목)·MLX run 은 판정 근거에서 뺀다(D-323 주의 ①) — 결과에 「방향 확인용」을 단다.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from . import REPO_ROOT
from .fingerprints import planner_module, size_gate
from .judge import (
    APM_AGENT,
    CATEGORY_BY_NAME,
    ExampleParseError,
    ParsedExample,
    _plan_views_args,
    effective_views,
    example_table,
    judge_args,
    judge_views,
    parse_example,
    summarize,
)
from .scenarios import PLACEHOLDER_RE, Expect, RegistryInfo, Scenario

FEWSHOT_LOG_PATH = REPO_ROOT / "testdata" / "apm_bench" / "fewshot_log.yaml"

#: 과적합 검사 — 문자 n-gram 길이와 차단 임계(자카드).
SIMILARITY_N = 3
SIMILARITY_THRESHOLD = 0.8
#: 같은 실패 ID가 예문 수정 이만큼(로그 기준) 뒤에도 남으면 결정적 처리로 올린다(§3.5 승격 규칙).
PROMOTE_AFTER = 2
#: E1·MLX 결과 표지(D-323 주의 ① · D-240).
DIRECTIONAL_LABEL = "방향 확인용 — FabriX와 다를 수 있음"
#: 판정 근거로 쓰는 환경(내부망 게이트웨이 · 본체 LLM 설정).
DECISIVE_ENV = "e2"

#: 수정 위치 칸(`locus[].slot`).
LOCUS_SLOTS: frozenset[str] = frozenset({"F1", "F2", "F3", "F4"})
_TARGET_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*#[0-9]+$")
_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

#: 영문 이름 모양(숫자나 하이픈이 섞인 토큰 — `web01`·`abc-was`·`0a1b2c3d`). 자리표 제거본과 비교할
#: 때 예문 쪽에서도 지운다 — 「{{inst_1}} …」을 「web01 …」로 바꿔 넣은 예문을 잡기 위해서다.
_NAME_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:[A-Za-z][A-Za-z0-9_]*[0-9][A-Za-z0-9_-]*"
    r"|[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+)(?![A-Za-z0-9_])"
)
_SUB_QUERY_RE = re.compile(r'"sub_query":\s*"([^"]+)"')
_INPUT_RE = re.compile(r'입력:\s*"([^"]+)"')
_QUOTE_RE = re.compile(r"「([^」]+)」")


class ReplayBlockedError(RuntimeError):
    """실 MLX 재현 전제(두 평면 `mlx` · 루프백 · MLX 점검 통과)가 깨졌다."""


# --- n-gram 유사도 ------------------------------------------------------------------------------


def _normalize(text: str) -> str:
    """비교용 글 — casefold 후 글자·숫자만 남긴다(공백·문장부호·밑줄 제거)."""
    return re.sub(r"[\W_]+", "", text.casefold())


def char_ngrams(text: str, n: int = SIMILARITY_N) -> frozenset[str]:
    """정규화한 글의 문자 n-gram 집합 — n보다 짧으면 글 전체 1개."""
    s = _normalize(text)
    if not s:
        return frozenset()
    return frozenset(s[i:i + n] for i in range(max(len(s) - n + 1, 1)))


def similarity(a: str, b: str, n: int = SIMILARITY_N) -> float:
    """문자 n-gram 자카드 유사도 |A∩B| / |A∪B| (0~1 · 둘 다 비면 0)."""
    ga, gb = char_ngrams(a, n), char_ngrams(b, n)
    union = ga | gb
    return len(ga & gb) / len(union) if union else 0.0


# --- 과적합 검사 (§3.9 ②) ------------------------------------------------------------------------


def _example_question(text: str, view: str, index: int) -> str:
    """F1 예문의 「…」 질문 — 형식 밖이면 예문 전체(보수적으로 전체를 비교한다)."""
    try:
        return parse_example(text, view, index).question
    except ExampleParseError:
        return text


def fewshot_texts(registry: RegistryInfo) -> list[tuple[str, str]]:
    """비교할 예시 문장 `(위치, 글)` — F1 예문 질문 · F2 `sub_query`·「…」 · F3 입력·`sub_query`.

    F4(재선택 프롬프트)는 자체 예문이 없다(보기 표 = F1 이 다시 실린다).
    """
    from .fingerprints import f2_text, f3_text

    out: list[tuple[str, str]] = []
    for view_id, view in registry.views.items():
        for i, text in enumerate(view.examples, 1):
            out.append((f"F1:{view_id}:example#{i}", _example_question(text, view_id, i)))
    f2 = f2_text()
    for i, text in enumerate(dict.fromkeys(_SUB_QUERY_RE.findall(f2)), 1):
        out.append((f"F2:sub_query#{i}", text))
    for i, text in enumerate(dict.fromkeys(_QUOTE_RE.findall(f2)), 1):
        out.append((f"F2:quote#{i}", text))
    f3 = f3_text()
    for i, text in enumerate(dict.fromkeys(_INPUT_RE.findall(f3)), 1):
        out.append((f"F3:input#{i}", text))
    for i, text in enumerate(dict.fromkeys(_SUB_QUERY_RE.findall(f3)), 1):
        out.append((f"F3:sub_query#{i}", text))
    return out


def fewshot_check(scenarios: Sequence[Scenario], registry: RegistryInfo, *,
                  threshold: float = SIMILARITY_THRESHOLD, n: int = SIMILARITY_N,
                  sizes: dict[str, int] | None = None) -> list[dict[str, Any]]:
    """예문 과적합 검사 + APM 절 크기 상한 — 위반 목록(빈 목록 = 통과).

    방식: 문자 n-gram(기본 n=3) 자카드. 글은 casefold 후 글자·숫자만 남긴다. 비교 쌍은
    ① 예문 ↔ 시나리오 프롬프트 자리표 원문 ② 예문(영문 이름 모양 토큰 제거) ↔ 자리표 제거본
    (자리표가 있는 턴만). 쌍마다 높은 쪽 점수가 `threshold` 이상이면 차단이다. 생성 시
    (2026-10-12 · `ee13b47`) 최고 점수 0.778 · 위반 0건.

    Args:
        scenarios: 시나리오 전체(dev·holdout 모두 — 어느 쪽을 베껴도 측정이 오염된다)
        registry: F1 예문 원천(`RegistryInfo`)
        threshold: 차단 임계
        n: n-gram 길이
        sizes: 크기 측정값(None이면 현재 렌더를 잰다 — `fingerprints.apm_section_size`)

    Returns:
        `{"kind": "similarity", "locus", "example", "scenario", "variant", "score"}` ·
        `{"kind": "size", "message"}`
    """
    prompts: list[tuple[str, str, str]] = []
    for sc in scenarios:
        for t, turn in enumerate(sc.turns):
            ref = f"{sc.id}#{t}"
            prompts.append((ref, "raw", turn.prompt))
            if PLACEHOLDER_RE.search(turn.prompt):
                prompts.append((ref, "placeholder_removed", PLACEHOLDER_RE.sub(" ", turn.prompt)))
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for locus, text in fewshot_texts(registry):
        stripped = _NAME_RE.sub(" ", text)
        for ref, variant, prompt in prompts:
            score = similarity(stripped if variant != "raw" else text, prompt, n)
            if score < threshold:
                continue
            key = (locus, ref)
            if key not in best or score > best[key]["score"]:
                best[key] = {"kind": "similarity", "locus": locus, "example": text,
                             "scenario": ref, "variant": variant, "score": round(score, 3)}
    out: list[dict[str, Any]] = sorted(best.values(), key=lambda v: (-v["score"], v["locus"]))
    out += [{"kind": "size", "message": m} for m in size_gate(sizes)]
    return out


def next_gate_commands() -> list[str]:
    """`--fewshot-check` 통과 뒤 남은 게이트(이 도구는 실행하지 않는다 — §3.9 ④)."""
    return [
        "python scripts/prompt_render_diff.py --ci",
        "python -m scripts.bench --preflight   # 두 평면 mlx·127.0.0.1 확인(아래 둘의 전제)",
        "python scripts/eval_routing.py --decomposition"
        "   # 두 평면 mlx면 승인 불요 · 아니면 RUN_E2E=1 건별 승인",
        "python -m scripts.scenario --mock --group FS   # fs_four_source 골드(무과금 모의)",
        "python -m scripts.scenario --run --group FS --profile tier2_intent"
        "   # 두 평면 mlx 확인 후",
    ]


def render_fewshot_check(violations: Sequence[Mapping[str, Any]]) -> str:
    """검사 결과 문자열 — 위반 목록 + 다음에 돌릴 명령."""
    lines = ["## few-shot 검사"]
    sims = [v for v in violations if v.get("kind") == "similarity"]
    sizes = [v for v in violations if v.get("kind") == "size"]
    if not violations:
        lines.append(f"- 통과 — 유사도 {SIMILARITY_THRESHOLD} 이상 0건 · 크기 상한 이내")
    for v in sims:
        lines.append(f"- [차단] {v['locus']} 「{v['example']}」 ≈ {v['scenario']}"
                     f" ({v['variant']}) 자카드 {v['score']}")
    for v in sizes:
        lines.append(f"- [차단] {v['message']}")
    lines += ["", "### 다음에 돌릴 명령", *[f"- `{c}`" for c in next_gate_commands()]]
    return "\n".join(lines) + "\n"


# --- 수정 이력 (fewshot_log.yaml) -----------------------------------------------------------------


def load_log(path: Path = FEWSHOT_LOG_PATH, *,
             holdout_ids: Iterable[str] = ()) -> list[dict[str, Any]]:
    """이력 항목 목록(파일이 없거나 비었으면 빈 목록). 항목마다 검증한다 — `holdout_ids`에 든
    대상을 담은 항목도 형식 위반이다(권고 ③ · 손으로 쓴 항목 포함).

    Raises:
        ValueError: 형식 위반 항목이 있을 때(번호·사유)
    """
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: 최상위는 매핑(entries: [...])이어야 한다")
    entries = data.get("entries") or []
    if not isinstance(entries, list):
        raise ValueError(f"{path}: entries 는 목록이어야 한다")
    held = list(holdout_ids)
    problems = [f"#{i}: {p}" for i, e in enumerate(entries)
                for p in validate_entry(e, holdout_ids=held)]
    if problems:
        raise ValueError(f"{path}: " + " · ".join(problems))
    return [dict(e) for e in entries]


def validate_entry(entry: Any, *, holdout_ids: Iterable[str] = ()) -> list[str]:
    """이력 항목 형식 위반 문구(빈 목록 = 통과). `holdout_ids`에 든 대상은 거부한다(권고 ③)."""
    if not isinstance(entry, Mapping):
        return ["항목은 매핑이어야 한다"]
    out: list[str] = []
    rnd = entry.get("round")
    if not isinstance(rnd, int) or isinstance(rnd, bool) or rnd < 1:
        out.append("round 는 1 이상 정수")
    if not isinstance(entry.get("date"), str) or not _DATE_RE.match(entry["date"]):
        out.append("date 는 YYYY-MM-DD 문자열")
    targets = entry.get("targets")
    if not isinstance(targets, list) or not targets:
        out.append("targets 는 비지 않은 목록(scenario_id#turn)")
    else:
        bad = [t for t in targets if not isinstance(t, str) or not _TARGET_ID_RE.match(t)]
        if bad:
            out.append(f"targets 형식 밖: {bad}")
        held = sorted(set(holdout_ids) & {t for t in targets if isinstance(t, str)})
        if held:
            out.append(f"holdout 턴은 수정 대상이 아니다(권고 ③): {held}")
    locus = entry.get("locus")
    if not isinstance(locus, list) or not locus:
        out.append("locus 는 비지 않은 목록")
    else:
        for i, loc in enumerate(locus):
            if not isinstance(loc, Mapping) or loc.get("slot") not in LOCUS_SLOTS:
                out.append(f"locus[{i}].slot 은 F1~F4")
                continue
            example = loc.get("example")
            if example is not None and (not isinstance(example, int) or example < 1):
                out.append(f"locus[{i}].example 은 1 이상 정수")
    fps = entry.get("fingerprints")
    if not isinstance(fps, Mapping) or not all(isinstance(fps.get(k), Mapping)
                                                for k in ("before", "after")):
        out.append("fingerprints 는 {before: {...}, after: {...}}")
    if not isinstance(entry.get("run_id"), str) or not entry["run_id"]:
        out.append("run_id(근거 run) 는 비지 않은 문자열")
    if not isinstance(entry.get("memo", ""), str):
        out.append("memo 는 문자열")
    return out


def _log_header(path: Path) -> str:
    """파일 머리 주석(첫 비주석 줄 앞까지) — 추가할 때 칸 정의를 보존한다."""
    if not path.exists():
        return ""
    head: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            head.append(line)
        else:
            break
    return "\n".join(head).rstrip() + "\n\n" if head else ""


def append_entry(entry: Mapping[str, Any], path: Path = FEWSHOT_LOG_PATH, *,
                 holdout_ids: Iterable[str] = ()) -> dict[str, Any]:
    """검증한 항목을 이력 끝에 덧붙인다(머리 주석 보존). `date`가 없으면 오늘.

    Raises:
        ValueError: 형식 위반
    """
    item = {"date": date.today().isoformat(), "memo": "", **dict(entry)}
    problems = validate_entry(item, holdout_ids=holdout_ids)
    if problems:
        raise ValueError(" · ".join(problems))
    entries = [*load_log(path), item]
    body = yaml.safe_dump({"entries": entries}, allow_unicode=True, sort_keys=False)
    path.write_text(_log_header(path) + body, encoding="utf-8")
    return item


def fix_counts(entries: Sequence[Mapping[str, Any]]) -> Counter[str]:
    """실패 ID별 예문 수정 횟수 — 그 ID를 `targets`에 담은 항목 수."""
    return Counter(t for e in entries for t in dict.fromkeys(e.get("targets") or []))


# --- 수정 후보 (개발셋만) -----------------------------------------------------------------------


def _ref(record: Mapping[str, Any]) -> str:
    return f"{record.get('scenario_id')}#{record.get('turn')}"


def _status(record: Mapping[str, Any]) -> str:
    """턴 상태 — `pass` · `fail` · `na`(건너뜀·판정 불가)."""
    verdict = record.get("verdict") or {}
    if verdict.get("skipped") or (verdict.get("axis_detail") or {}).get("unjudged"):
        return "na"
    return "pass" if verdict.get("turn_pass") else "fail"


def fix_candidates(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """few-shot 수정 후보 — **dev 턴만**(holdout 은 수정 대상으로 내놓지 않는다 · 권고 ③).

    실패 턴의 분류 중 few-shot 으로 고칠 수 있는 것(`fewshot_fixable`가 False 가 아닌 분류 — 턴별
    값 `axis_detail.categories[분류]`가 있으면 그것 · 분해 전 되묻기는 턴별로 False)과 그
    지목 위치(F1~F4·소스 용어)만 싣는다. 코드 교정 분류만 있는 턴은 빠진다.
    """
    out: list[dict[str, Any]] = []
    for r in records:
        if str(r.get("split") or "") != "dev" or _status(r) != "fail":
            continue
        verdict = r.get("verdict") or {}
        per = (verdict.get("axis_detail") or {}).get("categories") or {}
        cats = [c for c in verdict.get("categories") or []
                if c in CATEGORY_BY_NAME and (per.get(c) or {}).get(
                    "fewshot_fixable", CATEGORY_BY_NAME[c].fewshot_fixable) is not False]
        if not cats:
            continue
        locus = [loc for c in cats for loc in (per.get(c) or {}).get("locus") or []
                 if not str(loc).startswith("code:")]
        out.append({"id": _ref(r), "categories": cats, "locus": list(dict.fromkeys(locus))})
    return out


# --- 회차 비교 (--compare) ----------------------------------------------------------------------


def load_run(path: Path) -> dict[str, Any]:
    """반출 산출물 디렉터리 → `{"meta": run.json, "records": trace.jsonl 행 목록, "path"}`."""
    meta = json.loads((path / "run.json").read_text(encoding="utf-8"))
    records = [json.loads(line) for line in
               (path / "trace.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    return {"meta": meta, "records": records, "path": str(path)}


def directional_reasons(meta: Mapping[str, Any]) -> list[str]:
    """이 run 을 판정 근거에서 빼야 하는 사유(빈 목록 = 판정 근거로 쓴다 · D-323 주의 ①).

    `run.json` 칸 가정(통합 Wave 가 맞춘다): `env`(`e1`·`e2`) · `planes{worker, orchestrator}`
    (LLM provider 이름). `env`가 `e2`가 아니거나(없음 포함) 평면 하나라도 `mlx`이거나 평면 칸이
    없으면 뺀다 — 모르면 판정에 쓰지 않는 쪽이 기본이다.
    """
    reasons: list[str] = []
    env = str(meta.get("env") or "").strip().lower()
    if env != DECISIVE_ENV:
        reasons.append("E1(외부망 목 · 가짜 LLM)" if env == "e1"
                       else f"env 칸 불명({env or '없음'})")
    planes = meta.get("planes")
    if not isinstance(planes, Mapping):
        reasons.append("planes 칸 없음")
    else:
        mlx = [k for k in ("worker", "orchestrator")
               if str(planes.get(k) or "").strip().lower() == "mlx"]
        if mlx:
            reasons.append(f"MLX 평면({'·'.join(mlx)})")
    return reasons


def _split_turn(records: Sequence[Mapping[str, Any]], split: str) -> dict[str, Any]:
    table = (summarize(records)["by_split"].get(split) or {}).get("turn") or {}
    return {"accuracy": table.get("accuracy"), "pass": table.get("pass", 0),
            "total": table.get("total", 0)}


def _direction(a: float | None, b: float | None) -> str:
    if a is None or b is None:
        return "판정 불가"
    if b > a + 1e-9:
        return "상승"
    if b < a - 1e-9:
        return "하락"
    return "불변"


def _fp_changes(meta_a: Mapping[str, Any], meta_b: Mapping[str, Any]) -> dict[str, Any] | None:
    """SHA 가 다르면 F1~F4 지문 변화(`run.json` `fingerprints` 칸 가정) — 같으면 None."""
    if meta_a.get("sha") == meta_b.get("sha"):
        return None
    fa, fb = meta_a.get("fingerprints"), meta_b.get("fingerprints")
    if not isinstance(fa, Mapping) or not isinstance(fb, Mapping):
        return {"note": "fingerprints 칸 없음 — 지문 대조 불가", "changed": {}}
    keys = list(dict.fromkeys([*fa, *fb]))
    return {"note": "", "changed": {k: {"a": fa.get(k), "b": fb.get(k)} for k in keys
                                    if fa.get(k) != fb.get(k)}}


def compare(run_a: Path, run_b: Path, *, log: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """두 run(A = 수정 전 · B = 수정 후) 대조.

    판정 규칙:
      - **판정 기준 = holdout 턴 정확도**(`judge.summarize` `by_split.holdout.turn` — 일반 턴) A→B
        상승·하락·불변. dev 정확도는 참고로만 싣는다.
      - 둘 중 하나라도 `directional_reasons`가 있으면(E1·MLX) `decisive=False` — 판정 근거에서
        빼고 「방향 확인용」을 단다.
      - 대상 실패 ID: `log` 중 근거 run(`run_id`)이 A 인 항목의 `targets`(없으면 마지막 회차 항목)
        → B 에서 통과면 해소 · 실패면 잔존 · 건너뜀/판정 불가면 판정 불가.
      - 악화: A 통과 → B 실패. dev 는 ID 목록, holdout 은 **건수만**(수정 대상으로 내놓지 않는다).
      - 승격: 로그상 수정 `PROMOTE_AFTER`(2)회 이상인 ID 가 B 에서도 실패 → 결정적 처리 승격 목록.
      - SHA 가 다르면 F1~F4 지문 변화를 함께 싣는다.
      - 두 run 의 턴 집합(`scenario_id#turn`)이 다르면 **공통 턴만으로** 정확도·악화를 매기고
        `turn_sets.same=False`로 표시한다(전체 run 대 일부 턴 run 이 「상승」으로 읽히지 않게).
        공통 턴이 없으면 방향은 판정 불가다.
    """
    a, b = load_run(run_a), load_run(run_b)
    meta_a, meta_b = a["meta"], b["meta"]
    reasons = {"a": directional_reasons(meta_a), "b": directional_reasons(meta_b)}
    decisive = not (reasons["a"] or reasons["b"])
    refs_a, refs_b = {_ref(r) for r in a["records"]}, {_ref(r) for r in b["records"]}
    common = refs_a & refs_b
    turn_sets = {"same": refs_a == refs_b, "common": len(common),
                 "only_a": len(refs_a - common), "only_b": len(refs_b - common)}
    if not turn_sets["same"]:
        a["records"] = [r for r in a["records"] if _ref(r) in common]
        b["records"] = [r for r in b["records"] if _ref(r) in common]
    status_a = {_ref(r): _status(r) for r in a["records"]}
    status_b = {_ref(r): _status(r) for r in b["records"]}
    split_of = {_ref(r): str(r.get("split") or "") for r in [*a["records"], *b["records"]]}

    hold_a, hold_b = _split_turn(a["records"], "holdout"), _split_turn(b["records"], "holdout")
    dev_a, dev_b = _split_turn(a["records"], "dev"), _split_turn(b["records"], "dev")
    direction = _direction(hold_a["accuracy"], hold_b["accuracy"])

    run_id_a = str(meta_a.get("run_id") or "")
    picked = [e for e in log if run_id_a and e.get("run_id") == run_id_a]
    target_basis = f"run_id={run_id_a}"
    if not picked and log:
        last = max(int(e.get("round") or 0) for e in log)
        picked = [e for e in log if int(e.get("round") or 0) == last]
        target_basis = f"마지막 회차 round={last}(근거 run 불일치)"
    counts = fix_counts(log)
    targets: list[dict[str, Any]] = []
    for tid in dict.fromkeys(t for e in picked for t in e.get("targets") or []):
        sb = status_b.get(tid, "na")
        targets.append({"id": tid, "a": status_a.get(tid, "na"), "b": sb,
                        "result": {"pass": "해소", "fail": "잔존"}.get(sb, "판정 불가"),
                        "fixes": counts.get(tid, 0)})

    worse = [tid for tid, s in status_b.items() if s == "fail" and status_a.get(tid) == "pass"]
    promote = [{"id": tid, "fixes": n} for tid, n in sorted(counts.items())
               if n >= PROMOTE_AFTER and status_b.get(tid) == "fail"]
    return {
        "runs": {
            "a": {"run_id": run_id_a, "sha": meta_a.get("sha"), "env": meta_a.get("env"),
                  "planes": meta_a.get("planes"), "directional": reasons["a"]},
            "b": {"run_id": str(meta_b.get("run_id") or ""), "sha": meta_b.get("sha"),
                  "env": meta_b.get("env"), "planes": meta_b.get("planes"),
                  "directional": reasons["b"]},
        },
        "decisive": decisive,
        "label": "" if decisive else DIRECTIONAL_LABEL,
        "turn_sets": turn_sets,
        "holdout": {"a": hold_a, "b": hold_b, "direction": direction},
        "dev": {"a": dev_a, "b": dev_b, "direction": _direction(dev_a["accuracy"],
                                                                 dev_b["accuracy"])},
        "target_basis": target_basis if picked else "로그 항목 없음",
        "targets": targets,
        "worsened": {"dev": sorted(t for t in worse if split_of.get(t) == "dev"),
                     "holdout": sum(1 for t in worse if split_of.get(t) == "holdout")},
        "promote": promote,
        "fingerprints": _fp_changes(meta_a, meta_b),
    }


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def render_compare(result: Mapping[str, Any]) -> str:
    """`compare` 결과 → markdown."""
    ra, rb = result["runs"]["a"], result["runs"]["b"]
    hold, dev = result["holdout"], result["dev"]
    lines = ["# few-shot 회차 비교", ""]
    if not result["decisive"]:
        why = " · ".join([*(f"A: {r}" for r in ra["directional"]),
                          *(f"B: {r}" for r in rb["directional"])])
        lines += [f"> **{DIRECTIONAL_LABEL}** — 판정 근거에서 제외({why})", ""]
    lines += [
        f"- A `{ra['run_id']}` (SHA `{ra['sha']}` · env {ra['env']})",
        f"- B `{rb['run_id']}` (SHA `{rb['sha']}` · env {rb['env']})",
        "",
        "## 판정 — holdout 턴 정확도",
        *([] if result.get("turn_sets", {}).get("same", True) else [
            f"- **턴 집합 다름 — 공통 턴 {result['turn_sets']['common']}개만으로 판정** "
            f"(A 에만 {result['turn_sets']['only_a']} · B 에만 {result['turn_sets']['only_b']})"]),
        f"- {_pct(hold['a']['accuracy'])} ({hold['a']['pass']}/{hold['a']['total']}) → "
        f"{_pct(hold['b']['accuracy'])} ({hold['b']['pass']}/{hold['b']['total']}) · "
        f"**{hold['direction']}**" + ("" if result["decisive"] else " (방향 확인용)"),
        f"- 참고 dev: {_pct(dev['a']['accuracy'])} → {_pct(dev['b']['accuracy'])} · "
        f"{dev['direction']}",
        "",
        f"## 대상 실패 ID ({result['target_basis']})",
    ]
    lines += ([f"- {t['id']}: {t['a']} → {t['b']} · {t['result']} (수정 {t['fixes']}회)"
               for t in result["targets"]] or ["- 없음"])
    worse = result["worsened"]
    lines += ["", "## 악화 (A 통과 → B 실패)",
              f"- dev: {', '.join(worse['dev']) if worse['dev'] else '없음'}",
              f"- holdout: {worse['holdout']}건(상세는 수정 대상이 아니라 싣지 않는다)",
              "", f"## 결정적 처리 승격 (예문 수정 {PROMOTE_AFTER}회 뒤 잔존)"]
    lines += ([f"- {p['id']} (수정 {p['fixes']}회)" for p in result["promote"]] or ["- 없음"])
    fps = result.get("fingerprints")
    if fps is not None:
        lines += ["", "## 지문 변화 (SHA 다름)"]
        if fps.get("note"):
            lines.append(f"- {fps['note']}")
        lines += ([f"- {k}: `{v['a']}` → `{v['b']}`" for k, v in fps["changed"].items()]
                  or ([] if fps.get("note") else ["- 변화 없음"]))
    return "\n".join(lines) + "\n"


# --- 오프라인 재현 (--replay) -------------------------------------------------------------------


class _ReplayDbhub:
    """`dbhub` 대역 — APM 엔드포인트만 가짜 루프백으로 답한다(재현은 게이트웨이를 부르지 않는다)."""

    def __init__(self, base: Any) -> None:
        self._base = base

    def source_endpoint(self, system: str) -> Any:
        if system == "apm":
            return ("http://127.0.0.1:9/replay", None)
        getter = getattr(self._base, "source_endpoint", None)
        return getter(system) if callable(getter) else None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)


class _ReplayConfig:
    """앱 설정 위임 대역 — `dbhub.source_endpoint("apm")`만 바꿔 APM 을 활성으로 본다."""

    def __init__(self, base: Any) -> None:
        self._base = base
        self.dbhub = _ReplayDbhub(getattr(base, "dbhub", None))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)


def replay_config(base: Any = None) -> Any:
    """재현용 설정 — 본체 설정(`load_config()`)의 플래그 그대로 + APM 활성."""
    if base is None:
        from src.config import load_config

        base = load_config()
    return _ReplayConfig(base)


def _loopback(url: str) -> bool:
    host = urlparse(url).hostname or ""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def make_mlx_llm(cfg: Any = None) -> Any:
    """실 MLX 재현용 LLM — 두 평면 `mlx` · 루프백 · MLX 점검 통과일 때만 만든다(D-240).

    Raises:
        ReplayBlockedError: 과금 평면이 있거나 `mlx`가 아니거나 루프백이 아니거나 MLX 점검 중단
            항목이 있을 때
    """
    from scripts.scenario.preflight import external_planes, mlx_run_blockers

    if cfg is None:
        from src.config import load_config

        cfg = load_config()
    worker = str(getattr(getattr(cfg, "llm", None), "provider", "") or "").strip().lower()
    orchestrator = str(getattr(getattr(cfg, "orchestrator", None), "provider", "")
                       or "").strip().lower()
    external = external_planes(worker, orchestrator)
    if external:
        raise ReplayBlockedError(f"과금 평면이 있어 재현하지 않는다: {external}")
    if worker != "mlx" or orchestrator != "mlx":
        raise ReplayBlockedError(f"두 평면이 모두 mlx 여야 한다(worker={worker} · "
                            f"orchestrator={orchestrator})")
    urls = [str(getattr(cfg.llm, "mlx_base_url", "") or ""),
            str(getattr(cfg.orchestrator, "base_url", "") or "")]
    remote = [u for u in urls if not _loopback(u)]
    if remote:
        raise ReplayBlockedError(f"MLX 주소가 루프백이 아니다: {remote}")
    blockers = mlx_run_blockers(cfg)
    if blockers:
        raise ReplayBlockedError("MLX 점검 중단: " + " · ".join(
            f"{c.key}={c.observed}" for c in blockers))
    from src.llm import create_llm

    return create_llm(cfg)


def _expect_of(record: Mapping[str, Any]) -> Expect:
    """레코드 `expect` dict → `Expect`(판정 함수 입력)."""
    raw = record.get("expect") or {}
    return Expect(
        agents=tuple(raw.get("agents") or ()), views=tuple(raw.get("views") or ()),
        target=str(raw.get("target") or "none"), window=str(raw.get("window") or "default"),
        args=dict(raw.get("args") or {}), apm_sources=tuple(raw.get("apm_sources") or ()),
        clarify=raw.get("clarify"), refusal=raw.get("refusal"),
    )


def _views_args_axes(record: Mapping[str, Any], observed: Mapping[str, Any],
                     registry: RegistryInfo,
                     examples: Mapping[str, Sequence[ParsedExample]]) -> dict[str, Any]:
    """judge 의 ②보기·⑤인자 축만 다시 매긴다."""
    expect = _expect_of(record)
    eff = effective_views(observed, registry)
    _, raw_args = _plan_views_args(observed.get("plan_raw"), registry.views)
    view_axis, view_detail, view_cats = judge_views(expect, eff)
    arg_axis, arg_detail, arg_cats, copy = judge_args(
        expect, eff, raw_args, str(record.get("prompt") or ""),
        record.get("placeholders") or {}, registry, examples)
    return {"views": view_axis, "args": arg_axis, "observed_views": list(eff["views"]),
            "observed_args": eff["args"], "categories": [*view_cats, *arg_cats],
            "views_detail": view_detail, "args_detail": arg_detail, "fewshot_copy": copy}


def _replay_context(record: Mapping[str, Any],
                    by_ref: Mapping[str, Mapping[str, Any]]) -> dict[str, Any] | None:
    """앞 턴 맥락(근사) — 본체 `context_resolver`의 압축 신호 중 턴 수·직전 질의만 채운다."""
    turn = int(record.get("turn") or 0)
    if turn <= 0:
        return None
    prev = by_ref.get(f"{record.get('scenario_id')}#{turn - 1}") or {}
    summary = f"직전 질의: {prev.get('prompt')}" if prev.get("prompt") else ""
    return {"turn_count": turn + 1, "previous_results_summary": summary}


async def _replay_turn(record: Mapping[str, Any], by_ref: Mapping[str, Mapping[str, Any]],
                       llm: Any, app_config: Any) -> dict[str, Any]:
    """턴 하나 — 분해(`intent_planner._llm_decompose`) → APM task 마다 보기 재선택
    (`apm_query._select_views`) → 관측 모양으로 정리."""
    from src.orchestration import apm_query

    prompt = str(record.get("prompt") or "")
    result = await planner_module()._llm_decompose(
        llm, prompt, app_config, conversation_context=_replay_context(record, by_ref))
    tasks = [t for t in result.get("tasks") or [] if isinstance(t, dict)]
    apm_tasks = [t for t in tasks if t.get("agent") == APM_AGENT]
    by_id = {v.id: v for v in apm_query.apm_views()}
    isolated = {"original_user_query": prompt, "user_query": prompt,
                "thread_id": f"replay:{_ref(record)}"}
    happened = False
    reselect: dict[str, Any] = {"candidates": [], "parsed_views": [], "parsed_view_args": {},
                                "result_views": [], "reason": None}
    for task in apm_tasks:
        selection = await apm_query._select_views(task, isolated, llm, by_id)
        info = selection.info
        happened = happened or bool(info.get("retried"))
        reselect["candidates"] += list(info.get("candidates") or [])
        reselect["parsed_views"] += list(info.get("retried_views") or [])
        reselect["parsed_view_args"].update(selection.view_args)
        reselect["result_views"] += list(selection.views)
        reselect["reason"] = info.get("error") or info.get("result")
    observed = {"agents": [str(t.get("agent")) for t in tasks], "plan_raw": [],
                "plan_final": apm_tasks,
                "reselect": {**reselect, "happened": happened} if happened else None}
    return {"observed": observed, "degraded": result.get("degraded") or []}


async def areplay(records: Sequence[Mapping[str, Any]], *, llm: Any, registry: RegistryInfo,
                  turn_ids: Iterable[str] | None = None,
                  app_config: Any = None) -> list[dict[str, Any]]:
    """`replay`의 비동기 본체."""
    by_ref = {_ref(r): r for r in records}
    wanted = set(turn_ids) if turn_ids is not None else None
    picked = [r for r in records
              if (_ref(r) in wanted if wanted is not None
                  else _status(r) == "fail" and str(r.get("split") or "") == "dev")]
    cfg = app_config if app_config is not None else replay_config()
    examples = example_table(registry)
    out: list[dict[str, Any]] = []
    for record in picked:
        row: dict[str, Any] = {"id": _ref(record), "split": record.get("split"),
                               "label": DIRECTIONAL_LABEL,
                               "before": _views_args_axes(record, record.get("observed") or {},
                                                          registry, examples)}
        try:
            got = await _replay_turn(record, by_ref, llm, cfg)
        except Exception as e:  # noqa: BLE001 — 턴마다 독립 · 사유를 행에 남긴다
            row.update(after=None, error=f"{type(e).__name__}: {str(e)[:200]}")
            out.append(row)
            continue
        after = _views_args_axes(record, got["observed"], registry, examples)
        after.update(plan=got["observed"]["plan_final"], reselect=got["observed"]["reselect"],
                     degraded=got["degraded"])
        row.update(after=after, error=None,
                   changed={axis: [row["before"][axis], after[axis]]
                            for axis in ("views", "args")
                            if row["before"][axis] != after[axis]})
        out.append(row)
    return out


def replay(records: Sequence[Mapping[str, Any]], *, llm: Any, registry: RegistryInfo,
           turn_ids: Iterable[str] | None = None, app_config: Any = None) -> list[dict[str, Any]]:
    """반출된 턴의 분해·보기 재선택 단계만 현재 프롬프트로 다시 돌려 ②보기·⑤인자 전/후를 낸다.

    같은 SHA 에서 고친 F1~F4 가 그대로 반영된다(프롬프트는 호출 시점에 렌더). 본체 진입점은
    `src/orchestration/intent_planner.py:_llm_decompose`(분해 + 정제 — 사전 처리 계층 A 우회)와
    `src/orchestration/apm_query.py:_select_views`(필요 시 `_retry_selection` LLM 1회)다.
    게이트웨이·대상 해석·창은 부르지 않는다.

    Args:
        records: 반출 `trace.jsonl` 행(치환본 질문 · 판정 포함)
        llm: 주입 LLM(테스트는 가짜 · 실 MLX 는 `make_mlx_llm()`) — 분해·재선택에 같이 쓴다
        registry: 판정용 `RegistryInfo`
        turn_ids: 재현할 `scenario_id#turn`(None 이면 **dev** 실패 턴 전부 — holdout 은 수정
            피드백으로 쓰지 않는다 · 권고 ③. 지정하면 split 과 무관하게 그 턴을 돈다 — CLI 가
            holdout 지정에 경고한다)
        app_config: 설정(None 이면 `replay_config()` — 본체 설정 + APM 활성)

    Returns:
        턴마다 `{"id", "split", "label"(방향 확인용), "before", "after", "changed", "error"}`.
        앞 턴 맥락은 턴 수·직전 질의만 채운 근사다(본체 `context_resolver` 미경유).
    """
    return asyncio.run(areplay(records, llm=llm, registry=registry, turn_ids=turn_ids,
                               app_config=app_config))


__all__ = [
    "FEWSHOT_LOG_PATH",
    "SIMILARITY_N",
    "SIMILARITY_THRESHOLD",
    "PROMOTE_AFTER",
    "DIRECTIONAL_LABEL",
    "ReplayBlockedError",
    "char_ngrams",
    "similarity",
    "fewshot_texts",
    "fewshot_check",
    "next_gate_commands",
    "render_fewshot_check",
    "load_log",
    "validate_entry",
    "append_entry",
    "fix_counts",
    "fix_candidates",
    "load_run",
    "directional_reasons",
    "compare",
    "render_compare",
    "replay_config",
    "make_mlx_llm",
    "areplay",
    "replay",
]
