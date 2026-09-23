"""처분 제안기 — 리포트를 읽어 노브마다 처분 하나를 정한다 (plans/93 §6.5·§6.6).

**파일을 수정하지 않는다.** 산출은 제안 문서 셋뿐이다(§6.1).

    recommended.env.diff   기준선 대비 권고 설정 제안
    knob_disposition.md    노브별 처분 + 근거
    d161_evidence.md       삭제·상수화 제안의 4항 실측
    migration_pin.env      기본값을 옮기기 전에 현재 실효값을 박는 블록(§6.7.1)

자동 반영을 하지 않는 이유는 둘이다. 환경변수 삭제는 D-161이 정의한 *폐기 제안*이고
4항 실측 첨부 없이는 반려된다. 그리고 `.env`는 git 미추적이라 자동 수정은 복구 근거가 없다.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.bench import axes as axes_mod
from scripts.bench import catalog as cat_mod
from scripts.bench import compare as cmp_mod
from scripts.bench import validate as val_mod

#: 처분 어휘. 리포트·제안서는 이 밖의 말을 쓰지 않는다.
FIX_NOW = "즉시 수정"
PROPOSE_DELETE = "삭제 제안"
PROPOSE_CONSTANT = "상수화 제안"
CHANGE_DEFAULT = "기본값 변경"
KEEP_WITH_VALUE = "존치 + 권고값"
DOWNGRADE = "등급 강등(비노출)"
KEEP = "존치"
DEFER = "보류"

#: 처분 → 롤아웃 단계(§6.6)
ROLLOUT = {
    FIX_NOW: "R0", KEEP_WITH_VALUE: "R1", DOWNGRADE: "R1·R2",
    CHANGE_DEFAULT: "R3", PROPOSE_CONSTANT: "R4", PROPOSE_DELETE: "R4",
    KEEP: "—", DEFER: "—",
}


@dataclass(frozen=True)
class Disposition:
    """노브 1건의 처분과 **왜 그렇게 됐는지**."""

    env_key: str
    action: str
    rule: str          # 결정 절차에서 맞은 규칙 번호(§6.5.2)
    reason: str
    recommended_value: Optional[str] = None

    @property
    def stage(self) -> str:
        return ROLLOUT.get(self.action, "—")


@dataclass(frozen=True)
class Evidence:
    """D-161 4항 실측 — 하나라도 비면 폐기 제안을 보류한다(§6.4)."""

    env_key: str
    operational_value: str
    runtime_available: str
    last_change: str
    reverse_imports: str

    @property
    def complete(self) -> bool:
        return all(v.strip() for v in
                   (self.operational_value, self.runtime_available,
                    self.last_change, self.reverse_imports))


def decide(
    knob: cat_mod.KnobSpec,
    *,
    integrity_kinds: Sequence[str] = (),
    shadowed: bool = False,
    boot_failed: bool = False,
    consumption: Optional[str] = None,       # changed | unchanged | unchecked
    verdict: Optional[str] = None,           # compare 판정 5어휘
    recommended_value: Optional[str] = None,
    reference_count: Optional[int] = None,
    env_differs_from_default: bool = False,
) -> Disposition:
    """§6.5.2의 10순위 결정 절차. **위에서부터 먼저 맞는 것**을 적용한다.

    0단계(고아·가림·기동 실패)가 맨 앞인 이유는 그것이 간소화가 아니라 **결함 수정**이기
    때문이다. 깨진 설정을 안고 간소화를 논하면 "줄였더니 안 된다"가 된다.
    """
    key = knob.env_key

    if boot_failed:
        return Disposition(key, FIX_NOW, "0-a", "기본값·경계값으로 기동이 실패한다")
    if "orphan" in integrity_kinds:
        return Disposition(key, FIX_NOW, "0-b", "파일에만 있고 코드가 읽지 않는다 — 파일에서 제거")
    if "missing_example" in integrity_kinds:
        return Disposition(key, FIX_NOW, "0-c", ".env.example에 없다 — 신규 설치자가 존재를 모른다")
    if shadowed:
        return Disposition(key, FIX_NOW, "0-d", "주입이 상위 소스에 가려 무효 — 측정 자체가 불가")

    if consumption == "unchanged" and reference_count == 0:
        return Disposition(key, PROPOSE_DELETE, "1", "동작 불변 + 프로덕션 참조 0건")
    if consumption == "unchanged" and reference_count is not None and 1 <= reference_count <= 2 \
            and (knob.default or "").lower() in ("true", "1", "yes"):
        return Disposition(key, PROPOSE_CONSTANT, "2", "동작 불변 + 참조 1~2건 + 기본값 ON")

    if verdict == cmp_mod.ADOPT:
        return Disposition(key, CHANGE_DEFAULT, "3", "측정에서 기준선보다 낫다", recommended_value)
    if verdict == cmp_mod.CONDITIONAL:
        return Disposition(key, KEEP_WITH_VALUE, "4",
                           "한 지표가 좋아지고 다른 지표가 나빠진다 — 기본값은 두고 권고값만 병기",
                           recommended_value)
    if verdict == cmp_mod.REJECT:
        return Disposition(key, KEEP, "5", "측정에서 나빠진다 — 기본값 유지 + '권장하지 않음' 주석")
    if verdict == cmp_mod.NO_DIFFERENCE and (reference_count or 0) >= 3:
        return Disposition(key, DOWNGRADE, "6", "성능 차이 없음 + 참조 3건 이상 — 존치하되 비노출")
    if verdict == cmp_mod.UNDERPOWERED:
        return Disposition(key, DEFER, "7", "검정력 부족 — 정적 규칙으로 폴백, 재측정 대기")
    if verdict is None:
        return Disposition(key, DOWNGRADE, "8", "성능 미측정(축 밖) — C등급 기본 배정")

    if env_differs_from_default:
        return Disposition(key, KEEP, "9", ".env 실제값이 코드 기본값과 다르다 — 살아있는 레버")
    return Disposition(key, KEEP, "9", "위 어디에도 걸리지 않는다")


def count_references(env_key: str, *, roots: Sequence[str] = ("src", "noise_gate", "mcp_server")) -> int:
    """프로덕션 참조 수 — **워드 경계 grep · `.venv` 제외**.

    `.venv` 제외를 기본값으로 넣는 이유는 `docs/flag_audit.md`가 기록한 실사례 때문이다:
    벤더 venv 오염이 `trace_enabled`를 52건(실제 5건)으로 부풀렸다.
    """
    field = env_key.lower()
    total = 0
    for root in roots:
        base = _ROOT / root
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            parts = path.parts
            if ".venv" in parts or "tests" in parts or path.name.startswith("test_"):
                continue
            if path.name == "config.py" and root == "src":
                continue   # 정의 자체는 참조가 아니다
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            total += len(re.findall(rf"\b{re.escape(field)}\b", text))
    return total


def collect_evidence(env_key: str, knob: cat_mod.KnobSpec) -> Evidence:
    """D-161 4항을 코드로 채운다. 못 채운 항은 **빈 문자열로 남겨 미완을 드러낸다**."""
    return Evidence(
        env_key=env_key,
        operational_value=_operational_value(env_key),
        runtime_available=f"카탈로그 등재 · apply_mode={knob.apply_mode} · consumed={knob.consumed}",
        last_change=_last_change(env_key),
        reverse_imports=f"프로덕션 참조 {count_references(env_key)}건(워드 경계 · .venv 제외)",
    )


def _operational_value(env_key: str) -> str:
    """코드 기본값이 아니라 **운영 `.env`의 현재 값**(D-161 ①)."""
    for name in (".env", ".encenv"):
        path = _ROOT / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            stripped = line.strip()
            if stripped.startswith(f"{env_key}="):
                value = stripped.split("=", 1)[1]
                shown = "***" if name == ".encenv" else value
                return f"{name}에 등재: {shown or '(빈 값)'}"
    return "운영 파일에 미등재 — 코드 기본값으로 동작"


def _last_change(env_key: str) -> str:
    """현 브랜치 한정 최종 변경(D-161 ③ — `--all` 금지).

    커밋 메시지에 한글이 흔해서 인코딩을 못박지 않으면 Windows 콘솔 코드페이지와 어긋나
    디코딩이 깨진다. 그때 빈 문자열을 돌려주면 **증거가 조용히 사라져** D-161 4항이 미완이 되고,
    제안이 "보류"로 잘못 분류된다 — 침묵 폴백 금지 원칙이 정확히 겨냥하는 상황이다.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(_ROOT), "log", "-1", "--format=%ad|%h", "--date=short",
             "-S", env_key.lower(), "--", "src", "noise_gate"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
        )
    except Exception as exc:
        # 실패 사유를 실어 보낸다. 빈 문자열은 "미수집"으로 읽혀 제안을 보류시킨다.
        return f"(git 조회 실패: {type(exc).__name__})"
    out = (proc.stdout or "").strip()
    return out or "현 브랜치 이력에서 찾지 못함"


# ── 축 최적 레벨 → 처분 입력 (D-237) ────────────────────────────────
#
# `decide()` 는 **arm 5어휘**(`compare.ADOPT` 등)만 안다. 그 어휘는 "이 arm 이 기준선보다
# 나은가"를 답하는 말이라, 축 판정(`compare.AxisOptimum` — "이 축의 최적 레벨은 무엇인가")을
# 그대로 넣을 수 없다. 여기서 번역한다. **`decide()` 는 건드리지 않는다** — 처분 규칙
# (§6.5.2)은 정본이고, 바뀐 것은 입력을 만드는 방법뿐이다.


def axis_verdict_word(optimum: "cmp_mod.AxisOptimum") -> tuple[str, Optional[str], str]:
    """축 판정 1건을 `(arm 5어휘, 권고값, 사람이 읽을 사유)` 로 옮긴다.

    핵심은 **최적 레벨이 대조군일 때**다. 대조군은 기준선과 실효 설정이 같은 레벨이므로,
    그것이 이겼다는 말은 *"현행이 최적이고 다른 레벨이 더 나쁘다"* 는 뜻이다 — 기본값을
    바꾸는 `채택 권고`가 아니라 **`기각`**(기본값 유지 + '권장하지 않음')으로 가야 한다.
    이 구분이 없으면 현행 유지가 기본값 변경 제안으로 둔갑한다.
    """
    controls = set(optimum.control_levels)
    if optimum.verdict == cmp_mod.BEST_LEVEL and optimum.best_level:
        if optimum.best_level in controls:
            others = [lv for lv in optimum.levels if lv != optimum.best_level]
            return (cmp_mod.REJECT, None,
                    f"레벨 간 비교에서 **현행값 `{optimum.best_level}`(대조군)이 우세**하다 — "
                    f"{'·'.join(others)} 는 더 나쁘다")
        return (cmp_mod.ADOPT, optimum.best_level,
                f"레벨 간 비교에서 `{optimum.best_level}` 가 우세하다")
    if optimum.verdict == cmp_mod.LEVELS_TIED:
        return (cmp_mod.NO_DIFFERENCE, None, "레벨 간 유의차 없음 — 기본값을 바꿀 근거가 없다")
    return (cmp_mod.UNDERPOWERED, None, f"축 판정 불가 — {optimum.sentence[:120]}")


def axis_disposition_inputs(
    optima: Sequence["cmp_mod.AxisOptimum"],
) -> tuple[dict[str, str], dict[str, str]]:
    """`build_from_validation(verdicts=…, recommended=…)` 에 그대로 넣을 두 매핑.

    **축 id 가 곧 env_key 다**(`axes.expand_ofat` 이 `env_key` 를 축 id 로 쓴다) — 별도
    매핑표가 필요 없다.
    """
    verdicts: dict[str, str] = {}
    recommended: dict[str, str] = {}
    for opt in optima:
        word, value, _ = axis_verdict_word(opt)
        verdicts[opt.axis] = word
        if value is not None:
            recommended[opt.axis] = value
    return verdicts, recommended


def _structural_axis(axis_id: str) -> Optional[axes_mod.AxisCandidate]:
    """구조 축이면 그 정의, 아니면 None. 정의를 못 읽으면 None(단일 키로 취급한다)."""
    try:
        return axes_mod.find_axis(axis_id)
    except Exception:
        return None


def _is_structural(axis_id: str) -> bool:
    return _structural_axis(axis_id) is not None


def _axis_env_pairs(axis_id: str, level: str) -> list[tuple[str, str]]:
    """축 id·레벨 → **실제로 `.env` 에 적을 키·값**.

    단일 키 축이면 `[(축 id, 레벨)]` 그대로다(종전 동작). 다중 키 축(`109·CS-31` X1)이면 그
    레벨이 정하는 키 전부다 — 축 id 를 env 키로 적으면 존재하지 않는 설정이 제안서에 실린다.
    """
    axis = _structural_axis(axis_id)
    if axis is None:
        return [(axis_id, level)]
    try:
        return sorted(axis.env_for(level).items())
    except KeyError:
        return [(axis_id, level)]


def render_recommended_env(
    optima: Sequence["cmp_mod.AxisOptimum"],
    *,
    baseline_effective: Optional[Mapping[str, str]] = None,
) -> str:
    """`recommended.env.diff` — **기준선 대비 권고 설정만** 적는다(모듈 독스트링의 산출 ①).

    종전에는 독스트링이 이 파일을 약속해 놓고 `write_proposals` 가 만들지 않았다
    (실측 2026-09-21). 축 최적 레벨이 나오면 여기로 이어진다.
    """
    lines = [
        "# 기준선 대비 권고 설정 (plans/93 §6.5 · D-237 레벨 간 비교)",
        "#",
        "# **제안일 뿐이다** — 이 블록을 `.env` 에 반영하는 것은 §6.6 R3 이고, 그 전에",
        "# `migration_pin.env` 로 현재 실효값을 먼저 박는다(§6.7.1).",
        "#",
    ]
    changes = 0
    # **구조 축(사다리 단)이 1순위다**(plans/114 M-0 · D-250 ②) — 서버 `.env` 의 단과 이긴 단이
    # 다르면 그것이 가장 큰 설정 차이이고, 나머지 축 권고는 전부 그 단 위에서 잰 값이다.
    for opt in sorted(optima, key=lambda o: (not _is_structural(o.axis), o.axis)):
        word, value, reason = axis_verdict_word(opt)
        if word != cmp_mod.ADOPT or value is None:
            lines.append(f"# {opt.axis}: 권고 없음 — {reason}")
            continue
        # 다중 키 축은 축 id 가 env 키가 아니다 — 레벨이 주입하는 **실제 키들**로 펼친다.
        pairs = _axis_env_pairs(opt.axis, value)
        stale = [(key, val) for key, val in pairs
                 if str((baseline_effective or {}).get(key, "")).strip().lower()
                 != val.strip().lower()]
        single = len(pairs) == 1
        if not stale:
            shown = value if single else ", ".join(f"{k}={v}" for k, v in pairs)
            lines.append(f"# {opt.axis}: 이미 권고값({shown})이다 — 변경 없음")
            continue
        if single:
            current = (baseline_effective or {}).get(opt.axis)
            note = f" (현행 {current})" if current is not None else ""
        else:
            note = " (현행: " + ", ".join(
                f"{k}={(baseline_effective or {}).get(k, '?')}" for k, _ in pairs) + ")"
        lines.append(f"# {opt.axis}: {reason}{note}")
        if len(pairs) > 1:
            lines.append(f"#   `{opt.axis}` 은 **다중 키 축**이다 — 레벨 `{value}` 가 아래 "
                         f"{len(pairs)}키를 함께 정한다(따로 바꾸면 그 레벨이 아니다)")
        lines += [f"{key}={val}" for key, val in pairs]
        changes += 1
    if not changes:
        lines += ["#", "# **권고 변경 0건.** 측정에서 기본값을 바꿀 근거가 나오지 않았다."]
    return "\n".join(lines) + "\n"


def render_dispositions(dispositions: Sequence[Disposition]) -> str:
    lines = [
        "# 노브 처분 제안",
        "",
        "> **제안일 뿐이다** — 이 문서는 파일을 고치지 않는다. 반영은 §6.6의 R0~R4 순서로 사람이 한다.",
        "",
        "| 키 | 처분 | 규칙 | 단계 | 권고값 | 근거 |",
        "|---|---|---|---|---|---|",
    ]
    order = {FIX_NOW: 0, CHANGE_DEFAULT: 1, KEEP_WITH_VALUE: 2, DOWNGRADE: 3,
             PROPOSE_CONSTANT: 4, PROPOSE_DELETE: 5, DEFER: 6, KEEP: 7}
    for d in sorted(dispositions, key=lambda x: (order.get(x.action, 9), x.env_key)):
        lines.append(
            f"| `{d.env_key}` | {d.action} | {d.rule} | {d.stage} | "
            f"{d.recommended_value or '—'} | {d.reason} |"
        )
    return "\n".join(lines) + "\n"


def render_evidence(evidences: Sequence[Evidence]) -> str:
    lines = [
        "# D-161 4항 실측 (삭제·상수화 제안 첨부)",
        "",
        "> 하나라도 비면 **그 제안은 보류**다 — 4항 누락 폐기 제안은 반려된다(D-161 ②).",
        "",
    ]
    for e in evidences:
        lines += [
            f"## `{e.env_key}` — {'완비' if e.complete else '★ 미완(보류)'}",
            "",
            f"1. **운영 설정 실제값**: {e.operational_value or '_(미수집)_'}",
            f"2. **런타임 가용성**: {e.runtime_available or '_(미수집)_'}",
            f"3. **최근 개발 활동**: {e.last_change or '_(미수집)_'}",
            f"4. **역방향 의존**: {e.reverse_imports or '_(미수집)_'}",
            "",
        ]
    return "\n".join(lines)


def render_migration_pin(values: Mapping[str, str]) -> str:
    """R3 선행 블록 — 기본값을 옮기기 전에 현재 실효값을 박는다(§6.7.1)."""
    lines = [
        "# 기본값 변경(R3) 선행 고정 블록",
        "#",
        "# 이 블록을 먼저 배포해 **값 변화 0**인 상태를 만든 뒤에만 코드 기본값을 옮긴다.",
        "# 그러지 않으면 `.env`에 명시되지 않은 설치의 동작이 말없이 바뀐다.",
        "",
    ]
    lines += [f"{key}={value}" for key, value in sorted(values.items())]
    return "\n".join(lines) + "\n"


def write_proposals(
    dispositions: Sequence[Disposition],
    evidences: Sequence[Evidence],
    pin_values: Mapping[str, str],
    out_dir: Path,
    *,
    optima: Sequence["cmp_mod.AxisOptimum"] = (),
    baseline_effective: Optional[Mapping[str, str]] = None,
) -> dict[str, Path]:
    """제안 문서를 쓴다. **`.env`·`config.py`는 건드리지 않는다**(V5).

    `optima` 가 있으면 `recommended.env.diff` 도 쓴다 — 모듈 독스트링이 약속한 산출 ①이고,
    축 최적 레벨(D-237)이 실제로 설정 제안으로 이어지는 지점이다.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "disposition": out_dir / "knob_disposition.md",
        "evidence": out_dir / "d161_evidence.md",
        "pin": out_dir / "migration_pin.env",
    }
    paths["disposition"].write_text(render_dispositions(dispositions), encoding="utf-8")
    paths["evidence"].write_text(render_evidence(evidences), encoding="utf-8")
    paths["pin"].write_text(render_migration_pin(pin_values), encoding="utf-8")
    if optima:
        paths["recommended"] = out_dir / "recommended.env.diff"
        paths["recommended"].write_text(
            render_recommended_env(optima, baseline_effective=baseline_effective),
            encoding="utf-8")
    return paths


def build_from_validation(
    knobs: Iterable[cat_mod.KnobSpec],
    *,
    integrity: Sequence[cat_mod.IntegrityFinding] = (),
    shadowed: Sequence[val_mod.ShadowedKey] = (),
    boot: Sequence[val_mod.BootFinding] = (),
    consumption: Sequence[val_mod.ConsumptionFinding] = (),
    verdicts: Optional[Mapping[str, str]] = None,
    recommended: Optional[Mapping[str, str]] = None,
    with_references: bool = True,
) -> list[Disposition]:
    """트랙 T 산출(+선택적으로 성능 판정)로 처분을 만든다."""
    kinds_by_key: dict[str, list[str]] = {}
    for finding in integrity:
        kinds_by_key.setdefault(finding.env_key, []).append(finding.kind)
    shadow_keys = {s.env_key for s in shadowed}
    boot_fail = {b.env_key for b in boot if not b.ok}
    consumption_by_key = {c.env_key: c.verdict for c in consumption}
    verdicts = verdicts or {}
    recommended = recommended or {}

    out: list[Disposition] = []
    for knob in knobs:
        key = knob.env_key
        refs = count_references(key) if with_references else None
        out.append(decide(
            knob,
            integrity_kinds=kinds_by_key.get(key, []),
            shadowed=key in shadow_keys,
            boot_failed=key in boot_fail,
            consumption=consumption_by_key.get(key),
            verdict=verdicts.get(key),
            recommended_value=recommended.get(key),
            reference_count=refs,
        ))
    return out
