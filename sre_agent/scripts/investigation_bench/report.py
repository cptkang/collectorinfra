"""벤치 보고서 `report.md` (plans/154 §4.9 · §6.2 · §6.7).

입력은 run(또는 `.bundle`) 디렉터리의 `manifest.json`·`raw.jsonl`·`scores.jsonl`·
`gpu_samples.jsonl`(있을 때)뿐이다. 같은 입력이면 같은 바이트다 — 시각 같은 비결정 값은 manifest에
있는 것만 옮긴다.

- 머리: LLM 백엔드(크게) · 모델 · 사이트 · 커밋 · judge_version · 무효표 수/율(> 20%면 판정 불가
  고지 — D-219 ③ 준용).
- 실험군 × 지표 표(개발·홀드아웃 분리) · 실패 유형 분포 · A/A 잡음폭(`control`·`control_b`) ·
  처분 초안(홀드아웃만 · D-238 어휘) · 오답 검토표 · GPU 사용률.

경계(D-139 · §9.5): `sre_agent` 패키지를 import하지 않는다.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from investigation_bench.judge import FAILURE_CODES, read_jsonl

CONTROL_ARM = "control"
CONTROL_B_ARM = "control_b"
SPLIT_LABELS: dict[str, str] = {"dev": "개발", "holdout": "홀드아웃"}
HOLDOUT = "holdout"
# 무효표율이 이 값을 넘으면 처분 전체가 판정 불가다(§6.2 · D-219 ③ 준용).
INVALID_RATE_MAX = 0.20
# 지연 가드: 실험군 p90이 대조군의 이 배수 이내여야 한다(§6.2 「지연 p90 +50% 이내」).
LATENCY_P90_GUARD = 1.5
DISPOSITIONS = ("채택 권고", "기각", "판정 불가")
GPU_UTIL_KEYS: tuple[str, ...] = ("util", "utilization", "utilization_gpu", "gpu_util", "util_pct")

Row = dict[str, Any]


# ─────────────────────────────── 값 도우미 ───────────────────────────────


def _mget(manifest: Mapping[str, Any], *paths: str) -> Any:
    """manifest에서 첫 번째로 값이 있는 점 경로(`llm.backend` 등)."""
    for path in paths:
        node: Any = manifest
        for part in path.split("."):
            node = node.get(part) if isinstance(node, Mapping) else None
        if node not in (None, ""):
            return node
    return None


def _cell(value: Any) -> str:
    """표 칸 — 줄바꿈·파이프를 지운다."""
    return str(value).replace("\r", " ").replace("\n", " ").replace("|", "\\|")


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{x * 100:.1f}%"


def _num(x: float | None, unit: str = "") -> str:
    return "-" if x is None else f"{x:.1f}{unit}"


def _rate(values: Iterable[Any]) -> float | None:
    vs = [bool(v) for v in values if v is not None]
    return sum(vs) / len(vs) if vs else None


def _mean(values: Iterable[Any]) -> float | None:
    vs = [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return sum(vs) / len(vs) if vs else None


def percentile(values: Iterable[Any], p: float) -> float | None:
    """최근접 순위 백분위(보간 없음 · 결정적)."""
    vs = sorted(float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool))
    if not vs:
        return None
    idx = max(0, math.ceil(p / 100 * len(vs)) - 1)
    return vs[idx]


def _tokens(v: Any) -> Any:
    if isinstance(v, Mapping):
        return v.get("total") or v.get("total_tokens")
    return v


# ─────────────────────────────── 지표 ───────────────────────────────


def maj_at_k(rows: Sequence[Row]) -> float | None:
    """Maj@k — 세계마다 유효 반복 2회 이상에서 1위 (호스트, kind) 최빈 비율의 세계 평균."""
    by_world: dict[str, list[tuple[Any, Any]]] = {}
    for r in rows:
        top = r.get("top1") or {}
        by_world.setdefault(str(r.get("world_id")), []).append((top.get("host"), top.get("kind")))
    fracs = [Counter(v).most_common(1)[0][1] / len(v) for v in by_world.values() if len(v) >= 2]
    return sum(fracs) / len(fracs) if fracs else None


def metrics(rows: Sequence[Row]) -> dict[str, Any]:
    """유효 행 묶음의 지표(무효표는 분모에서 뺀다)."""
    valid = [r for r in rows if r.get("valid")]
    hallu_num = sum((r.get("hallu") or {}).get("num", 0) for r in valid)
    hallu_den = sum((r.get("hallu") or {}).get("den", 0) for r in valid)
    cost = [r.get("cost") or {} for r in valid]
    return {
        "n": len(rows),
        "valid": len(valid),
        "rc1": _rate(r.get("rc1") for r in valid),
        "rc3": _rate(r.get("rc3") for r in valid),
        "ecr": _mean((r.get("ecr") or {}).get("value") for r in valid),
        "hallu": hallu_num / hallu_den if hallu_den else (0.0 if valid else None),
        "dist": _rate(r.get("dist") for r in valid),
        "abst": _rate(r.get("abst_ok") for r in valid),
        "complete": _rate(r.get("complete") for r in valid),
        "p50": percentile((c.get("elapsed_s") for c in cost), 50),
        "p90": percentile((c.get("elapsed_s") for c in cost), 90),
        "tool_calls": _mean(c.get("tool_calls") for c in cost),
        "steps": _mean(c.get("steps") for c in cost),
        "tokens": _mean(_tokens(c.get("tokens")) for c in cost),
        "maj": maj_at_k(valid),
    }


_METRIC_COLUMNS: tuple[tuple[str, str, Callable[[Any], str]], ...] = (
    ("RC@1", "rc1", _pct), ("RC@3", "rc3", _pct), ("ECR", "ecr", _pct), ("HALLU", "hallu", _pct),
    ("DIST", "dist", _pct), ("ABST", "abst", _pct), ("완주율", "complete", _pct),
    ("지연 p50", "p50", lambda x: _num(x, "s")), ("지연 p90", "p90", lambda x: _num(x, "s")),
    ("도구 호출", "tool_calls", _num), ("step", "steps", _num), ("토큰", "tokens", _num),
    ("Maj@k", "maj", _pct),
)


def _order(values: Iterable[Any]) -> list[str]:
    return list(dict.fromkeys(str(v) for v in values))


def _splits(scores: Sequence[Row]) -> list[str]:
    present = _order(r.get("split") for r in scores)
    return [s for s in SPLIT_LABELS if s in present] + [s for s in present if s not in SPLIT_LABELS]


def _select(scores: Sequence[Row], arm: str, split: str) -> list[Row]:
    return [r for r in scores if str(r.get("arm")) == arm and str(r.get("split")) == split]


# ─────────────────────────────── 처분 ───────────────────────────────


def _pair_key(r: Row) -> tuple[str, str]:
    return str(r.get("world_id")), str(r.get("trial"))


def _paired(a: Sequence[Row], b: Sequence[Row]) -> tuple[list[Row], list[Row]]:
    """세계×반복이 양쪽 모두 유효한 쌍만(§6.2 ②)."""
    va = {_pair_key(r): r for r in a if r.get("valid")}
    vb = {_pair_key(r): r for r in b if r.get("valid")}
    keys = [k for k in va if k in vb]
    return [va[k] for k in keys], [vb[k] for k in keys]


def aa_noise(scores: Sequence[Row], split: str = HOLDOUT) -> dict[str, float] | None:
    """A/A 잡음폭 — `control`·`control_b` 쌍의 지표별 |Δ|. 둘 중 하나라도 없으면 None."""
    a, b = _paired(_select(scores, CONTROL_ARM, split), _select(scores, CONTROL_B_ARM, split))
    if not a:
        return None
    ma, mb = metrics(a), metrics(b)
    out: dict[str, float] = {}
    for key in ("rc1", "rc3", "ecr", "hallu", "dist", "abst", "complete", "p90"):
        if ma[key] is not None and mb[key] is not None:
            out[key] = abs(float(ma[key]) - float(mb[key]))
    return out


def disposition(scores: Sequence[Row], arm: str, *, invalid_rate: float,
                noise: Mapping[str, float] | None) -> tuple[str, str]:
    """실험군 1개의 처분 초안(홀드아웃 · 대조군 `control` 대비) → (처분, 사유)."""
    if invalid_rate > INVALID_RATE_MAX:
        return "판정 불가", f"무효표 {_pct(invalid_rate)} > {_pct(INVALID_RATE_MAX)}"
    exp_rows = _select(scores, arm, HOLDOUT)
    ctl_rows = _select(scores, CONTROL_ARM, HOLDOUT)
    if not any(r.get("complete") for r in exp_rows):
        return "판정 불가", "실험군 홀드아웃 완주 0건"
    exp, ctl = _paired(exp_rows, ctl_rows)
    if not exp:
        return "판정 불가", "대조군과 짝지을 유효 홀드아웃 실행 없음"
    me, mc = metrics(exp), metrics(ctl)
    if me["rc1"] is None or mc["rc1"] is None:
        return "판정 불가", "RC@1 계산 불가(정답 라벨 세계 없음)"
    delta = float(me["rc1"]) - float(mc["rc1"])
    noise = noise or {}
    worlds = len({str(r.get("world_id")) for r in exp})
    one_world = 1 / worlds
    threshold = max(noise.get("rc1", 0.0), one_world)
    noise_txt = "미측정" if "rc1" not in noise else _pct(noise["rc1"])
    basis = (f"ΔRC@1 {delta * 100:+.1f}%p · 기준 {threshold * 100:.1f}%p"
             f"(잡음폭 {noise_txt} · 1세계분 {_pct(one_world)})")
    if delta <= 0:
        return "기각", f"대조군이 같거나 낫다 — {basis}"
    if delta <= threshold:
        return "판정 불가", f"차이가 기준 안 — {basis}"
    worse: list[str] = []
    for key, label in (("complete", "완주율"),):
        if float(mc[key] or 0) - float(me[key] or 0) > noise.get(key, 0.0):
            worse.append(label)
    for key, label in (("hallu", "HALLU"), ("dist", "DIST")):
        if float(me[key] or 0) - float(mc[key] or 0) > noise.get(key, 0.0):
            worse.append(label)
    p90_e, p90_c = me["p90"], mc["p90"]
    if p90_e is not None and p90_c is not None and p90_e > p90_c * LATENCY_P90_GUARD:
        worse.append("지연 p90 +50% 초과")
    if worse:
        return "기각", f"가드 지표 악화({', '.join(worse)}) — {basis}"
    return "채택 권고", basis


# ─────────────────────────────── GPU ───────────────────────────────


def _gpu_utils(row: Mapping[str, Any]) -> list[float]:
    gpus = row.get("gpus")
    items: list[Any] = gpus if isinstance(gpus, list) else [row]
    out: list[float] = []
    for g in items:
        if not isinstance(g, Mapping):
            continue
        for key in GPU_UTIL_KEYS:
            v = g.get(key)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out.append(float(v))
                break
    return out


# ─────────────────────────────── 조립 ───────────────────────────────


def render(manifest: Mapping[str, Any], raw: Sequence[Row], scores: Sequence[Row],
           gpu_samples: Sequence[Row] | None, run_name: str) -> str:
    """보고서 본문(마크다운)."""
    lines: list[str] = []
    add = lines.append
    run_id = _mget(manifest, "run_id") or run_name
    backend = _mget(manifest, "backend", "llm.backend", "spec.llm.backend") or "미기록"
    total = len(scores)
    invalid = sum(1 for r in scores if not r.get("valid"))
    invalid_rate = invalid / total if total else 0.0
    versions = _order(r.get("judge_version") for r in scores) or ["-"]

    add(f"# 조사 품질 벤치 보고서 — {run_id}")
    add("")
    add(f"## LLM 백엔드: {str(backend).upper()}")
    add("")
    if invalid_rate > INVALID_RATE_MAX:
        add(f"> **판정 불가 — 무효표 {invalid}/{total} ({_pct(invalid_rate)}) > "
            f"{_pct(INVALID_RATE_MAX)} (D-219 ③ 준용). 처분 초안은 모두 판정 불가다.**")
        add("")
    if raw and len(raw) != total:
        add(f"> **주의 — raw {len(raw)}행 · scores {total}행: 재채점(`--rejudge`)이 필요하다.**")
        add("")
    add("| 항목 | 값 |")
    add("|---|---|")
    for label, value in (
        ("백엔드", backend),
        ("모델", _mget(manifest, "model", "llm.model", "spec.llm.model")),
        ("사이트", _mget(manifest, "site", "run.site", "spec.run.site")),
        ("커밋", _mget(manifest, "commit", "repo_commit", "git_commit")),
        ("judge_version", ", ".join(versions)),
        ("무효표", f"{invalid} / {total} ({_pct(invalid_rate)})"),
        ("시작", _mget(manifest, "started_at")),
        ("종료", _mget(manifest, "finished_at")),
    ):
        add(f"| {label} | {_cell(value if value is not None else '-')} |")
    add("")

    arms = _order(r.get("arm") for r in scores)
    splits = _splits(scores)

    add("## 실험군 × 지표")
    add("")
    add("무효표(F-INFRA)는 모든 지표의 분모에서 뺀다. "
        "HALLU = 근거 없는 수치 주장 / 수치 주장(전 실행 합).")
    add("")
    for split in splits:
        add(f"### {SPLIT_LABELS.get(split, split)} (`{split}`)")
        add("")
        add("| 실험군 | 유효/전체 | " + " | ".join(c[0] for c in _METRIC_COLUMNS) + " |")
        add("|---|---|" + "---|" * len(_METRIC_COLUMNS))
        for arm in arms:
            rows = _select(scores, arm, split)
            if not rows:
                continue
            m = metrics(rows)
            cells = [fmt(m[key]) for _, key, fmt in _METRIC_COLUMNS]
            add(f"| {_cell(arm)} | {m['valid']}/{m['n']} | " + " | ".join(cells) + " |")
        add("")

    add("## 실패 유형 분포")
    add("")
    add("| 실험군 | 분할 | " + " | ".join(FAILURE_CODES) + " |")
    add("|---|---|" + "---|" * len(FAILURE_CODES))
    for arm in arms:
        for split in splits:
            rows = _select(scores, arm, split)
            if not rows:
                continue
            counts = Counter(str(r.get("failure_code")) for r in rows)
            dist = " | ".join(str(counts.get(c, 0)) for c in FAILURE_CODES)
            add(f"| {_cell(arm)} | {split} | {dist} |")
    add("")

    noise = aa_noise(scores)
    add("## A/A 잡음폭 (홀드아웃 · `control` vs `control_b`)")
    add("")
    if noise is None:
        add("이 회차에 A/A 쌍이 없다 — 처분 기준은 1세계분만 적용한다"
            "(기준선 회차의 잡음폭과 대조할 것).")
    else:
        add("| 지표 | Δ 절댓값 |")
        add("|---|---|")
        for key, value in noise.items():
            add(f"| {key} | {_num(value, 's') if key == 'p90' else _pct(value)} |")
    add("")

    add("## 처분 초안 (홀드아웃만 · D-238)")
    add("")
    exp_arms = [a for a in arms if a not in (CONTROL_ARM, CONTROL_B_ARM)]
    has_holdout = any(str(r.get("split")) == HOLDOUT for r in scores)
    if not has_holdout:
        add("홀드아웃 실행이 없다 — 처분 없음(개발셋 점수는 보고만 한다).")
    elif CONTROL_ARM not in arms:
        add(f"대조군 `{CONTROL_ARM}`이 없다 — 처분 없음.")
    elif not exp_arms:
        add("실험군이 없다(기준선 회차) — 처분 없음.")
    else:
        add("| 실험군 | 처분 | 근거 |")
        add("|---|---|---|")
        for arm in exp_arms:
            verdict, why = disposition(scores, arm, invalid_rate=invalid_rate, noise=noise)
            add(f"| {_cell(arm)} | {verdict} | {_cell(why)} |")
    add("")

    add("## 오답 검토표")
    add("")
    wrong = [r for r in scores if r.get("valid") and r.get("failure_code") != "PASS"]
    if not wrong:
        add("오답 없음.")
    else:
        add("| 실험군 | 세계 | 분할 | 반복 | 실패 | 정답 라벨 | 1위 가설 원문 | 걸린 규칙 |")
        add("|---|---|---|---|---|---|---|---|")
        for r in wrong:
            truth_label: Mapping[str, Any] = r.get("label") or {}
            parts = [str(truth_label[k]) for k in ("host", "component", "kind")
                     if truth_label.get(k)]
            truth = " · ".join((["기권 기대"] if truth_label.get("abstain") else []) + parts)
            top = (r.get("top1") or {}).get("cause") or "(가설 없음)"
            rules = "; ".join(str(n) for n in r.get("notes") or []) or "-"
            add(f"| {_cell(r.get('arm'))} | {_cell(r.get('world_id'))} | {_cell(r.get('split'))} | "
                f"{_cell(r.get('trial'))} | {r.get('failure_code')} | {_cell(truth or '-')} | "
                f"{_cell(top)} | {_cell(rules)} |")
    add("")

    if gpu_samples:
        utils = [u for row in gpu_samples for u in _gpu_utils(row)]
        add("## GPU 사용률")
        add("")
        if utils:
            avg = sum(utils) / len(utils)
            add(f"- 샘플 {len(gpu_samples)}건 · 평균 {avg:.1f}% · 최대 {max(utils):.1f}%")
        else:
            add(f"- 샘플 {len(gpu_samples)}건 · 사용률 값 없음")
        add("")
    return "\n".join(lines).rstrip("\n") + "\n"


def write_report(run_dir: Path) -> Path:
    """run(또는 `.bundle`) 디렉터리의 산출 파일로 `report.md`를 쓴다. 반환 = 보고서 경로."""
    run_dir = Path(run_dir)
    manifest_path = run_dir / "manifest.json"
    manifest = (json.loads(manifest_path.read_text(encoding="utf-8"))
                if manifest_path.exists() else {})
    gpu_path = run_dir / "gpu_samples.jsonl"
    gpu = read_jsonl(gpu_path) if gpu_path.exists() else None
    name = run_dir.name.removesuffix(".bundle")
    text = render(manifest, read_jsonl(run_dir / "raw.jsonl"), read_jsonl(run_dir / "scores.jsonl"),
                  gpu, name)
    out = run_dir / "report.md"
    out.write_text(text, encoding="utf-8")
    return out
