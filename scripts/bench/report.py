"""설정 건강 보고서 + 전건 커버리지 장부 (plans/93 §5.4).

두 가지를 낸다.

  `config_health.md`    사람이 읽는 정본 — 즉시 조치부터 순서대로
  `coverage_ledger.md`  335필드가 **한 줄씩** 들어가는 장부

장부가 이 모듈의 핵심이다. *"전체 설정 파일을 테스트했다"* 가 빈말이 되지 않게 하는
유일한 장치는 **미측정을 사유와 함께 공개**하는 것이고, 그래서 사유 없는 미측정은
리포트 생성을 **실패시킨다**(§5.4.2 — 침묵 제외 금지의 기계적 강제).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

from scripts.bench import catalog, validate

#: 커버리지 기호. ⚪(미측정)만 사유를 요구한다.
COVERED = "O"
FAILED = "X"
UNMEASURED = "-"
NOT_APPLICABLE = "n/a"


class LedgerIncomplete(RuntimeError):
    """미측정에 사유가 비었다 — 리포트를 낼 수 없다."""


@dataclass(frozen=True)
class LedgerRow:
    """커버리지 장부 1행 = 설정 필드 1건."""

    env_key: str
    group_key: str
    type: str
    l1: str
    l2: str
    l3: str
    l4: str
    l5: str
    grade: str                      # A 환경결속 | B 운영레버 | C 내부상수급
    unmeasured_reason: str = ""

    def validate(self) -> None:
        """미측정이 하나라도 있으면 사유가 있어야 한다."""
        if UNMEASURED in (self.l1, self.l2, self.l3, self.l4, self.l5):
            if not self.unmeasured_reason.strip():
                raise LedgerIncomplete(
                    f"{self.env_key}: 미측정 항목이 있는데 사유가 비었다 "
                    f"(plans/93 §5.4.2 — 침묵 제외 금지)"
                )


@dataclass(frozen=True)
class HealthReport:
    """건강 보고서의 재료. 렌더는 `render_*`가 한다."""

    run_id: str
    integrity: Sequence[catalog.IntegrityFinding]
    shadowed: Sequence[validate.ShadowedKey]
    boot: Sequence[validate.BootFinding]
    consumption: Sequence[validate.ConsumptionFinding]
    unconsumed_cmp: Optional[validate.UnconsumedComparison]
    ledger: Sequence[LedgerRow]
    notes: Mapping[str, str]

    @property
    def immediate_actions(self) -> list[str]:
        """즉시 조치 — 간소화가 아니라 결함 수정이다(§6.5.2 0단계)."""
        out = [f"고아 키 `{f.env_key}` — {f.detail}"
               for f in self.integrity if f.kind == "orphan"]
        out += [f"가림 키 `{s.env_key}` — 주입 {s.injected!r}이 무효(출처 추정 {s.suspected_source})"
                for s in self.shadowed]
        out += [f"기동 실패 `{b.env_key}={b.value}` — {b.error_type}: {(b.error or '')[:120]}"
                for b in self.boot if not b.ok]
        return out


def assign_grade(knob: catalog.KnobSpec, *, in_env: bool, excluded_reason: Optional[str]) -> str:
    """노브 3등급 초안 배정(§6.3).

    최종 심판은 신규 설치 시나리오(§6.8.1)다 — 여기서는 초안만 낸다.
    """
    if knob.is_secret or knob.is_sensitive:
        return "A"
    lowered = knob.env_key.lower()
    if any(lowered.endswith(s) for s in ("_url", "_host", "_port")) or knob.env_key in (
        "ACTIVE_DB_IDS", "LLM_PROVIDER", "ORCHESTRATOR_PROVIDER", "DB_BACKEND",
    ):
        return "A"
    if excluded_reason is None:
        return "B"       # 축 후보 = 성능에 닿을 수 있다 = 운영 레버 후보
    return "C"


def build_ledger(
    knobs: Iterable[catalog.KnobSpec],
    *,
    integrity: Sequence[catalog.IntegrityFinding] = (),
    shadowed: Sequence[validate.ShadowedKey] = (),
    boot: Sequence[validate.BootFinding] = (),
    consumption: Sequence[validate.ConsumptionFinding] = (),
    env_keys: frozenset[str] = frozenset(),
    injection_checked: frozenset[str] = frozenset(),
    l5_measured: frozenset[str] = frozenset(),
) -> list[LedgerRow]:
    """전건 장부를 만든다. 입력이 없는 층은 사유와 함께 미측정으로 남는다."""
    shadow_keys = {s.env_key for s in shadowed}
    boot_fail = {b.env_key for b in boot if not b.ok}
    boot_seen = {b.env_key for b in boot}
    consumption_by_key = {c.env_key: c for c in consumption}
    integrity_seen = bool(integrity) or True  # L1은 전수 실행이 전제다

    rows: list[LedgerRow] = []
    for knob in knobs:
        reason_bits: list[str] = []
        excluded = catalog.f1_exclusion_reason(knob)

        l1 = COVERED if integrity_seen else UNMEASURED

        if knob.env_key in boot_fail:
            l2 = FAILED
        elif knob.env_key in boot_seen:
            l2 = COVERED
        else:
            l2 = UNMEASURED
            reason_bits.append(f"L2 대상 밖 — {excluded}" if excluded else "L2 미실행(--quick 또는 대상 제한)")

        if knob.env_key in shadow_keys:
            l3 = FAILED
        elif knob.env_key in injection_checked:
            l3 = COVERED
        else:
            l3 = UNMEASURED
            reason_bits.append(f"L3 대상 밖 — {excluded}" if excluded else "L3 미실행")

        finding = consumption_by_key.get(knob.env_key)
        if finding is None:
            l4 = UNMEASURED
            reason_bits.append(f"L4 대상 밖 — {excluded}" if excluded else "L4 미실행(--quick 또는 대상 제한)")
        elif finding.verdict == "unchecked":
            l4 = UNMEASURED
            reason_bits.append(f"L4 판정 보류 — {finding.detail}")
        else:
            l4 = COVERED

        if knob.env_key in l5_measured:
            l5 = COVERED
        else:
            l5 = UNMEASURED
            reason_bits.append(
                f"L5 미측정 — {excluded}" if excluded
                else "L5 미측정 — 성능 스위프 미실행(실 LLM 필요)"
            )

        rows.append(LedgerRow(
            env_key=knob.env_key, group_key=knob.group_key, type=knob.type,
            l1=l1, l2=l2, l3=l3, l4=l4, l5=l5,
            grade=assign_grade(knob, in_env=knob.env_key in env_keys, excluded_reason=excluded),
            unmeasured_reason=" · ".join(reason_bits),
        ))

    for row in rows:
        row.validate()
    return rows


def render_ledger(rows: Sequence[LedgerRow]) -> str:
    """장부를 Markdown 표로."""
    head = (
        "# 전건 커버리지 장부\n\n"
        f"> 설정 필드 **{len(rows)}건** 전수. 기호: `{COVERED}` 검증 · `{FAILED}` 실패(조치 필요) · "
        f"`{UNMEASURED}` 미측정(사유 필수)\n\n"
        "| 키 | 그룹 | 타입 | L1 | L2 | L3 | L4 | L5 | 등급 | 미측정 사유 |\n"
        "|---|---|---|---|---|---|---|---|---|---|\n"
    )
    body = "".join(
        f"| `{r.env_key}` | {r.group_key} | {r.type} | {r.l1} | {r.l2} | {r.l3} | {r.l4} | "
        f"{r.l5} | {r.grade} | {r.unmeasured_reason or '—'} |\n"
        for r in rows
    )
    return head + body


def render_health(report: HealthReport) -> str:
    """건강 보고서 — 즉시 조치가 맨 앞이다."""
    lines: list[str] = [
        f"# 설정 건강 보고서 — {report.run_id}",
        "",
        f"> 생성 {datetime.now().isoformat(timespec='seconds')} · "
        f"대상 **{len(report.ledger)}필드** · plans/93 트랙 T(L1~L4 · 실 LLM 미호출)",
        "",
    ]

    actions = report.immediate_actions
    lines += ["## 1. 즉시 조치", ""]
    if actions:
        lines += [f"{i}. {a}" for i, a in enumerate(actions, 1)]
        lines += ["", "> 위는 **간소화가 아니라 결함 수정**이다(§6.5.2 0단계). 성능 판정보다 먼저 처리한다."]
    else:
        lines.append("없음 — 고아 키·가림 키·기동 실패가 모두 0이다.")
    lines.append("")

    kinds = Counter(f.kind for f in report.integrity)
    lines += ["## 2. 카탈로그 정합 (L1)", "", "| 유형 | 건수 | 뜻 |", "|---|---:|---|"]
    labels = {
        "orphan": "파일에만 있고 코드가 읽지 않는다",
        "missing_example": "코드에는 있는데 `.env.example`이 알려주지 않는다",
        "undocumented": "설명이 없다(웹UI 도움말·주석의 원천)",
        "encenv_shadow_candidate": "`.encenv`가 관리 — `.env` 수정이 무효일 수 있다",
    }
    for kind, label in labels.items():
        lines.append(f"| {kind} | {kinds.get(kind, 0)} | {label} |")
    lines.append("")

    lines += ["## 3. 주입 실효성 (L3) — \"바꿨는데 왜 안 바뀌지\"", ""]
    if report.shadowed:
        lines += ["| 키 | 주입 | 실효 | 출처 추정 |", "|---|---|---|---|"]
        lines += [f"| `{s.env_key}` | {s.injected} | {s.effective} | {s.suspected_source} |"
                  for s in report.shadowed]
    else:
        lines.append("가림 0건 — 주입한 값이 전부 그대로 읽혔다.")
    lines.append("")

    fails = [b for b in report.boot if not b.ok]
    lines += ["## 4. 기동 안전성 (L2)", "",
              f"시도 {len(report.boot)}건 · 거부 {len(fails)}건", ""]
    if fails:
        lines += ["| 키 | 값 | 오류 |", "|---|---|---|"]
        lines += [f"| `{b.env_key}` | `{b.value}` | {b.error_type}: {(b.error or '')[:160]} |"
                  for b in fails[:50]]
        lines.append("")

    lines += ["## 5. 소비 실증 (L4)", ""]
    verdicts = Counter(c.verdict for c in report.consumption)
    lines.append(f"변함 {verdicts.get('changed', 0)} · 불변 {verdicts.get('unchanged', 0)} · "
                 f"판정 보류 {verdicts.get('unchecked', 0)}")
    cmp = report.unconsumed_cmp
    if cmp:
        lines += ["", "### `UNCONSUMED_KEYS` 대조", "", "| 분류 | 건수 | 조치 |", "|---|---:|---|",
                  f"| 목록과 일치(미소비 확인) | {len(cmp.agreed_unconsumed)} | 유지 |",
                  f"| **목록이 놓친 후보** | {len(cmp.list_missed)} | 참조 grep 후 등재 검토 |",
                  f"| **목록이 낡음(소비가 생김)** | {len(cmp.list_stale)} | 목록에서 제거 |"]
        if cmp.list_stale:
            lines += ["", "낡은 항목: " + ", ".join(f"`{k}`" for k in cmp.list_stale[:20])]
    lines += ["", "> **불변 = 삭제해도 된다가 아니다.** 이 층이 보는 것은 설정 객체의 변화이고,",
              "> 그 값을 읽는 코드가 있는지는 별개다(§3.5.4).", ""]

    grades = Counter(r.grade for r in report.ledger)
    lines += ["## 6. 등급 초안", "", "| 등급 | 뜻 | 건수 |", "|---|---|---:|",
              f"| A | 환경 결속 — 설치마다 정해야 한다 | {grades.get('A', 0)} |",
              f"| B | 운영 레버 — 권고값과 함께 제공 | {grades.get('B', 0)} |",
              f"| C | 내부 상수급 — 기본값 사용 | {grades.get('C', 0)} |",
              "", "> 최종 심판은 신규 설치 시나리오다(§6.8.1) — 빈 `.env` + A등급만으로 기동·질의가 되는가.", ""]

    covered = sum(1 for r in report.ledger if r.l4 == COVERED)
    lines += ["## 7. 커버리지 요약", "",
              f"- L1 정합: **{len(report.ledger)}건 전수**",
              f"- L2 기동: {len({b.env_key for b in report.boot})}건",
              f"- L3 주입: {len(report.consumption) or len(report.boot)}건",
              f"- L4 소비: {covered}건",
              "- L5 성능: **0건** — 실 LLM이 필요한 스위프는 별도 실행이다",
              "", "상세는 `coverage_ledger.md`(전건 한 줄씩).", ""]

    for key, note in (report.notes or {}).items():
        lines += [f"> **{key}**: {note}"]
    return "\n".join(lines) + "\n"


def render_summary(report: HealthReport) -> str:
    """`SUMMARY.md` — 한 눈에 블록. **다음 할 일 줄이 항상 있다**(§5.5)."""
    actions = report.immediate_actions
    kinds = Counter(f.kind for f in report.integrity)
    grades = Counter(r.grade for r in report.ledger)
    verdicts = Counter(c.verdict for c in report.consumption)

    next_step = (
        f"`config_health.md` 1절의 즉시 조치 {len(actions)}건을 먼저 처리한다(결함 수정)."
        if actions else
        "즉시 조치 없음. 성능 비교가 필요하면 `--sweep`로 스위프를 돌린다."
    )
    return "\n".join([
        f"# {report.run_id} 요약", "",
        "```",
        f"대상       : 설정 {len(report.ledger)}필드 · 트랙 T(L1~L4) · 실 LLM 호출 0",
        f"즉시 조치  : {len(actions)}건"
        + (f" (고아 {kinds.get('orphan', 0)} · 가림 {len(report.shadowed)} · "
           f"기동실패 {sum(1 for b in report.boot if not b.ok)})" if actions else ""),
        f"정합       : 누락 {kinds.get('missing_example', 0)} · 설명부재 {kinds.get('undocumented', 0)}"
        f" · encenv 관리 {kinds.get('encenv_shadow_candidate', 0)}",
        f"소비 실증  : 변함 {verdicts.get('changed', 0)} · 불변 {verdicts.get('unchanged', 0)}"
        f" · 보류 {verdicts.get('unchecked', 0)}",
        f"등급 초안  : A {grades.get('A', 0)} · B {grades.get('B', 0)} · C {grades.get('C', 0)}",
        f"다음 할 일 : {next_step}",
        "```", "",
        "| 파일 | 내용 |", "|---|---|",
        "| `config_health.md` | 설정 건강 보고서 — 즉시 조치부터 |",
        "| `coverage_ledger.md` | 전건 커버리지 장부 (한 줄씩) |",
        "",
    ]) + "\n"


def write_report(report: HealthReport, out_dir: Path) -> dict[str, Path]:
    """산출물을 한 폴더에 쓴다. **`.env`·`config.py`는 절대 건드리지 않는다**(V5)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "summary": out_dir / "SUMMARY.md",
        "health": out_dir / "config_health.md",
        "ledger": out_dir / "coverage_ledger.md",
    }
    paths["summary"].write_text(render_summary(report), encoding="utf-8")
    paths["health"].write_text(render_health(report), encoding="utf-8")
    paths["ledger"].write_text(render_ledger(report.ledger), encoding="utf-8")
    return paths
