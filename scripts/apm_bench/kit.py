"""실행 전 키트 검사 · 실행 위생 (plans/164 §4.2 (d) · §4.4 W4 · G-3 (a) · G-4 (a)).

- **자리표 실재**(T-5): 인벤토리 수집 직후 · 턴 송신 전에 시나리오가 쓰는 이름 자리표(`inst`·
  `host`·`biz`·`svc` — 인벤토리 이름 종류) 값을 인벤토리 이름과 대조해 자리표마다 「있음 · 없음 ·
  여럿(부분 일치 N)」만 낸다. **값은 출력하지 않는다**(자리표 이름만). 「없음」 자리표를 쓰는 일반
  턴은 판정 불가(`placeholder_missing:<자리표>` — 기존 경로)로 분리하고, 「여럿」은 경고만 한다
  (G-4 (a) — D-323 ① 분모 불변). 인벤토리 도구 일부가 실패했으면 그 도구가 내는 종류에서 못
  찾은 자리표는 「없음」이 아니라 「검사 불가」다(분리하지 않는다 — 게이트웨이 장애가 분모를
  줄이지 않게).
  단건 식별자(`url`·`pid`·`guid`·`user`·`domain_id`)는 인벤토리에 없는 종류라 검사 제외다.
- **소스 대응 범위**(T-1): 대상 자리표 중 `sources.by_placeholder`에 없는 것(→ 소스 자동 답이
  `default`로 보낸다)을 목록으로 경고하고, 인벤토리 행의 소스 표지(`source_id`)로 본 그 값의 소스에
  `default`가 없으면 경고한다. 코드로 교정하지 않는다(경고만).
- **`apm_gateway/var`**(W4-1): run 전 파일 목록(상대 경로 · 크기 · mtime)을 떠 두고, 끝에 새로
  생기거나 바뀐 파일이 있을 때만 경고한다 — 파일 이름은 출력하지 않고 건수만(운영 이름이 파일
  이름에 들어갈 수 있다). 시작 전에 있었으면 「이전부터 있음」 정보 줄.
- **실행 계정**(W4-2 · G-3 (a)): E2 에서 실행 계정이 root 면 사전 점검에서 exit 2.

결과는 run 메타(`run_meta.json` `kit`)에 닫힌 어휘 · 정수 · 허용 자리표 이름으로만 싣는다.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .driver import (
    INVENTORY_CALLS,
    Placeholders,
    inherited_target,
    target_placeholder,
    turn_placeholders,
)
from .gateway import GW_ROOT
from .record import TurnRecord
from .scenarios import Scenario, is_allowed_placeholder

#: 인벤토리 이름과 대조하는 자리표 종류(인스턴스 · 호스트 · 업무 · 서비스 = 도메인 이름).
CHECKED_KINDS: frozenset[str] = frozenset({"inst", "host", "biz", "svc"})
PRESENT, ABSENT, PARTIAL, UNCHECKED = "present", "absent", "partial", "unchecked"
STATUS_LABEL = {PRESENT: "있음", ABSENT: "없음", PARTIAL: "여럿", UNCHECKED: "검사 불가"}
#: 자리표 종류 → 인벤토리 행 칸(`driver.INVENTORY_CALLS`의 키). 그 칸을 내는 도구가 실패했는데 값을
#: 못 찾았으면 「없음」이 아니라 「검사 불가」다(게이트웨이 장애가 분모를 줄이지 않게 · V-1).
KIND_FIELDS: dict[str, str] = {"inst": "instance_name", "host": "host_name",
                               "biz": "business_name", "svc": "domain_name"}
#: 값 안에 든 인벤토리 이름을 부분 일치로 볼 최소 길이(FQDN 값 ↔ 짧은 호스트 이름).
MIN_REVERSE_LEN = 3
#: 레코드 `extra`에 싣는 「없음」 자리표 이름 목록 키(판정기가 읽는다).
KIT_ABSENT_KEY = "kit_absent"
#: 게이트웨이 기본 스풀 위치(스풀 경로 주입이 새면 여기 쌓인다).
VAR_DIR = GW_ROOT / "var"
#: E2 root 실행 거부 고정 문구(G-3 (a) — 계정 이름·홈은 출력하지 않는다).
ROOT_REFUSAL = ("E2 는 root 로 실행하지 않는다 — 전용 비root 계정(예 apmbench · 홈 경로에 "
                "운영 서버 이름 없음)으로 다시 실행한다(plans/164 G-3)")


def placeholder_kind(name: str) -> str:
    """자리표 이름 → 종류(`inst_1` → `inst` · `domain_id_1` → `domain`)."""
    return name.split("_", 1)[0]


@dataclass
class KitReport:
    """키트 검사 결과(값 없이 자리표 이름 · 판정 · 건수).

    Attributes:
        status: 검사한 자리표 이름 → (판정 `present`·`absent`·`partial`, 부분 일치 수)
        excluded: 검사 제외 자리표 수(인벤토리에 없는 종류)
        unmapped: `by_placeholder`에 없는 대상 자리표(자동 답이 `default`로 보낸다)
        mismatch: 그중 인벤토리 소스 표지에 `default`가 없는 자리표
    """

    status: dict[str, tuple[str, int]] = field(default_factory=dict)
    excluded: int = 0
    unmapped: list[str] = field(default_factory=list)
    mismatch: list[str] = field(default_factory=list)

    def names(self, kind: str) -> list[str]:
        return [n for n, (s, _) in self.status.items() if s == kind]

    def counts(self) -> dict[str, int]:
        return {k: len(self.names(k)) for k in (PRESENT, ABSENT, PARTIAL, UNCHECKED)} | {
            "excluded": self.excluded}


def used_placeholders(scenarios: Sequence[Scenario], values: Mapping[str, str]
                      ) -> tuple[list[str], list[str]]:
    """돌릴 시나리오가 쓰는 자리표(값 있는 것) → (전부, 대상 자리표) — 출현 순 · 중복 없음."""
    used: dict[str, None] = {}
    targets: dict[str, None] = {}
    for scenario in scenarios:
        for idx, turn in enumerate(scenario.turns):
            used.update(dict.fromkeys(turn_placeholders(
                turn, values, inherited=inherited_target(scenario, idx))))
            target = target_placeholder(turn.expect.target)
            if target and target in values:
                targets[target] = None
    return list(used), list(targets)


def presence(value: str, inventory: Iterable[str]) -> tuple[str, int]:
    """자리표 값 하나 × 인벤토리 이름 → (판정, 부분 일치 수) — 대소문자 무시.

    정확 일치만 하나면 「있음」, 정확·부분 일치가 모두 0 이면 「없음」, 그 밖(정확 일치 둘 이상 ·
    값을 품은 이름 · 값에 든 이름 `MIN_REVERSE_LEN`자 이상)은 「여럿」이고 수는 걸린 이름 수다.
    """
    v = value.strip().casefold()
    if not v:
        return ABSENT, 0
    exact = 0
    partial = 0
    for name in inventory:
        n = name.strip().casefold()
        if not n:
            continue
        if n == v:
            exact += 1
        elif v in n or (len(n) >= MIN_REVERSE_LEN and n in v):
            partial += 1
    if exact == 1 and partial == 0:
        return PRESENT, 1
    if exact == 0 and partial == 0:
        return ABSENT, 0
    return PARTIAL, exact + partial


def failed_tools(errors: Iterable[str]) -> set[str]:
    """인벤토리 실패 사유(`<도구>:<사유>`) → 실패한 도구 이름."""
    return {str(e).split(":", 1)[0] for e in errors}


def unchecked_kinds(errors: Iterable[str]) -> set[str]:
    """실패한 인벤토리 도구가 내는 칸의 자리표 종류(그 종류의 「없음」은 「검사 불가」로 둔다)."""
    failed = failed_tools(errors)
    return {kind for kind, fld in KIND_FIELDS.items()
            for tool, _, keys in INVENTORY_CALLS if tool in failed and fld in keys}


def check_kit(scenarios: Sequence[Scenario], placeholders: Placeholders,
              inventory: Sequence[str],
              name_sources: Mapping[str, Iterable[str]] | None = None,
              errors: Iterable[str] = ()) -> KitReport:
    """자리표 실재 · 소스 대응 범위 검사.

    Args:
        scenarios: 돌릴 시나리오
        placeholders: 자리표 파일
        inventory: 인벤토리 이름(레지스트리 밖 소스 id 를 섞지 않은 수집 이름)
        name_sources: 인벤토리 이름(casefold) → 그 이름이 나온 행의 소스 id(소스 표지가 있을 때만)
        errors: 인벤토리 수집 실패 사유(`<도구>:<사유>`) — 실패한 도구가 내는 종류에서 값을 못
            찾으면 「없음」이 아니라 「검사 불가」(분리하지 않는다 · 찾았으면 판정 그대로)
    """
    values = placeholders.values
    used, targets = used_placeholders(scenarios, values)
    blind = unchecked_kinds(errors)
    report = KitReport()
    for name in used:
        kind = placeholder_kind(name)
        if kind not in CHECKED_KINDS or not is_allowed_placeholder(name):
            report.excluded += 1
            continue
        status = presence(values[name], inventory)
        report.status[name] = (UNCHECKED, 0) if status[0] == ABSENT and kind in blind else status
    default = placeholders.source_default
    for name in targets:
        if not is_allowed_placeholder(name) or name in placeholders.source_by_placeholder:
            continue
        report.unmapped.append(name)
        found = set((name_sources or {}).get(values[name].strip().casefold()) or ())
        if default and found and default not in found:
            report.mismatch.append(name)
    return report


def kit_lines(report: KitReport) -> list[str]:
    """키트 검사 출력 줄(자리표 이름 · 판정 · 건수만 — 값·소스 id 미출력)."""
    c = report.counts()
    lines = [f"      키트 검사: 자리표 있음 {c[PRESENT]} · 없음 {c[ABSENT]} · 여럿 {c[PARTIAL]}"
             f" · 검사 불가 {c[UNCHECKED]}"
             f" · 검사 제외 {c['excluded']} · 소스 대응 없음 {len(report.unmapped)} · 소스 불일치 "
             f"{len(report.mismatch)}"]
    if report.status:
        lines.append("      자리표 실재: " + ", ".join(
            f"{n} {STATUS_LABEL[s]}" + (f"(부분 일치 {k})" if s == PARTIAL else "")
            for n, (s, k) in report.status.items()))
    if c[ABSENT]:
        lines.append(f"      [경고] 자리표 없음 {', '.join(report.names(ABSENT))} — 이 자리표를 "
                     "쓰는 일반 턴은 판정 불가(placeholder_missing)로 분리한다")
    if c[UNCHECKED]:
        lines.append(f"      [경고] 자리표 검사 불가 {', '.join(report.names(UNCHECKED))} — "
                     "인벤토리 도구 일부 실패 · 판정 불가로 분리하지 않는다(분모에 남긴다)")
    if c[PARTIAL]:
        lines.append(f"      [경고] 자리표 여럿 {', '.join(report.names(PARTIAL))} — 경고만(분모에 "
                     "남긴다)")
    if report.unmapped:
        lines.append(f"      [경고] 소스 대응 없음 {', '.join(report.unmapped)} — 소스 되묻기 "
                     "자동 답이 default 소스로 보낸다(sources.by_placeholder)")
    if report.mismatch:
        lines.append(f"      [경고] 소스 불일치 {', '.join(report.mismatch)} — 인벤토리에서 그 "
                     "값이 있는 소스에 default 가 없다")
    return lines


def mark_absent(records: Sequence[TurnRecord], absent: Iterable[str]) -> int:
    """「없음」 자리표를 쓰는 레코드(건너뜀 제외)의 `extra.kit_absent`에 그 자리표 이름을 싣는다
    → 표시한 레코드 수. 판정기가 일반 턴을 판정 불가로 분리한다."""
    gone = set(absent)
    if not gone:
        return 0
    marked = 0
    for record in records:
        if (record.get("verdict") or {}).get("skipped"):
            continue
        hit = [n for n in (record.get("placeholders") or {}) if n in gone]
        if hit:
            extra = record.get("extra")
            if not isinstance(extra, dict):
                extra = record["extra"] = {}
            extra[KIT_ABSENT_KEY] = hit
            marked += 1
    return marked


# --- apm_gateway/var ---------------------------------------------------------------------


def var_snapshot(root: Path | None = None) -> dict[str, tuple[int, int]] | None:
    """`root`(기본 `VAR_DIR`) 아래 파일 → 상대 경로 → (크기, mtime ns). 디렉터리가 없으면 None.

    심볼릭 링크는 따라가지 않는다(링크 자체를 파일 하나로 센다).
    """
    base = VAR_DIR if root is None else root
    if not base.is_dir():
        return None
    out: dict[str, tuple[int, int]] = {}
    for top, dirs, files in os.walk(base, followlinks=False):
        for name in [*files, *(d for d in dirs if os.path.islink(os.path.join(top, d)))]:
            path = os.path.join(top, name)
            try:
                st = os.lstat(path)
            except OSError:
                continue
            out[os.path.relpath(path, base)] = (st.st_size, st.st_mtime_ns)
    return out


def var_changes(before: Mapping[str, tuple[int, int]] | None,
                after: Mapping[str, tuple[int, int]] | None) -> dict[str, Any]:
    """run 전후 파일 목록 → `{preexisting, created, new, changed}`(건수 · 이름 없음)."""
    old = before or {}
    now = after or {}
    return {"preexisting": before is not None,
            "created": before is None and after is not None,
            "new": sum(1 for k in now if k not in old),
            "changed": sum(1 for k, v in now.items() if k in old and old[k] != v)}


def var_lines(diff: Mapping[str, Any]) -> list[str]:
    """var 점검 줄 — 새로 생기거나 바뀐 파일이 있을 때만 경고 · 이전부터 있었으면 정보 줄."""
    if diff["new"] or diff["changed"] or diff["created"]:
        return [f"[경고] apm_gateway/var — 이번 run 에서 새 파일 {diff['new']} · 바뀐 파일 "
                f"{diff['changed']}" + (" · 디렉터리 새로 생김" if diff["created"] else "")
                + " — 스풀 경로 주입을 확인한다"]
    if diff["preexisting"]:
        return ["[정보] apm_gateway/var 이전부터 있음 — 이번 run 에서 새로 생기거나 바뀐 파일 없음"]
    return []


# --- 실행 계정 ---------------------------------------------------------------------------


def is_root() -> bool:
    """실행 계정이 root(euid 0)인가 — `geteuid`가 없는 플랫폼은 False."""
    geteuid = getattr(os, "geteuid", None)
    return bool(geteuid is not None and geteuid() == 0)


def kit_meta(report: KitReport | None, var: Mapping[str, Any] | None, *,
             root: bool) -> dict[str, Any]:
    """run 메타 `kit` 칸(원시 — `safe_summary.run_meta_doc`가 다시 거른다)."""
    return {
        "checked": report is not None,
        "placeholders": report.counts() if report is not None else None,
        "absent": report.names(ABSENT) if report is not None else [],
        "partial": report.names(PARTIAL) if report is not None else [],
        "source_unmapped": len(report.unmapped) if report is not None else None,
        "source_mismatch": len(report.mismatch) if report is not None else None,
        "var": dict(var) if var is not None else None,
        "root": root,
    }


__all__ = [
    "ABSENT",
    "CHECKED_KINDS",
    "KIT_ABSENT_KEY",
    "KitReport",
    "PARTIAL",
    "PRESENT",
    "UNCHECKED",
    "ROOT_REFUSAL",
    "VAR_DIR",
    "check_kit",
    "unchecked_kinds",
    "is_root",
    "kit_lines",
    "kit_meta",
    "mark_absent",
    "presence",
    "used_placeholders",
    "var_changes",
    "var_lines",
    "var_snapshot",
]
