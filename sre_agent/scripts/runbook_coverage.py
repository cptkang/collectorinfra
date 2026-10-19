#!/usr/bin/env python3
"""런북 커버리지 점검 (plans/187 W8 §5.7.2 · 수용 기준 8 · D-341 ⑩).

- 플레이북 디렉토리의 `*.yaml`마다 런북 필수 칸(meaning·impact·owner·last_reviewed — 스키마가
  강제)이 있는지 센다. 로더가 거부한 파일은 「런북 없음/무효」로 사유와 함께 낸다.
- 검토 기한(`last_reviewed + review_every_days`)이 지난 런북 목록.
- (선택) 조사 저장소 JSONL(본체 `investigations.jsonl` — 본체 코드를 import하지 않고 JSONL만
  읽는다)을 주면 최근 N일 자동 조사(kind alarm·episode · 종결 done)의 플레이북 분포와
  「런북 없는 유형」(fallback 처리 건수 — 알람 이름별)을 낸다.

**읽기 전용** — 어떤 파일에도 쓰지 않는다.

사용법:
    python scripts/runbook_coverage.py                       # 기본 플레이북 디렉토리
    python scripts/runbook_coverage.py --investigations path/to/investigations.jsonl --days 30
    python scripts/runbook_coverage.py --json                # JSON 출력
    python scripts/runbook_coverage.py --ci                  # 커버리지 100% 미만이면 exit 1
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sre_agent.domain.playbook import FALLBACK_ID  # noqa: E402
from sre_agent.infrastructure.playbook_loader import (  # noqa: E402
    DEFAULT_PLAYBOOK_DIR,
    load_playbooks,
)

#: 자동 조사(PAGE 트리거) 레코드 kind — 사용자 요청(diagnosis·manual)은 분포에서 뺀다.
AUTO_KINDS = frozenset({"alarm", "episode"})
NO_ALARM_NAME = "(알람 이름 없음)"


def coverage_report(playbook_dir: Path | str, today: date) -> dict[str, Any]:
    """플레이북 디렉토리의 런북 커버리지 · 검토 기한 경과 목록."""
    root = Path(playbook_dir)
    files = sorted(p.name for p in root.glob("*.yaml")) if root.is_dir() else []
    loaded = load_playbooks(root)
    covered = sorted(pb.id for pb in loaded.playbooks)
    stale = [
        {
            "id": pb.id,
            "owner": pb.runbook.owner,
            "last_reviewed": pb.runbook.last_reviewed.isoformat(),
            "due": (
                pb.runbook.last_reviewed + timedelta(days=pb.runbook.review_every_days)
            ).isoformat(),
        }
        for pb in loaded.playbooks
        if pb.runbook.is_stale(today)
    ]
    pct = round(100.0 * len(loaded.playbooks) / len(files), 1) if files else 0.0
    return {
        "playbook_dir": str(root),
        "files": len(files),
        "covered": covered,
        "uncovered": [{"file": name, "reason": reason} for name, reason in loaded.rejected],
        "coverage_pct": pct,
        "stale": stale,
        "error": loaded.error,
    }


def _ts(value: object) -> datetime | None:
    try:
        ts = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)


def fallback_distribution(
    jsonl_path: Path | str, days: int, now: datetime | None = None
) -> dict[str, Any]:
    """최근 `days`일 자동 조사의 플레이북 분포와 fallback(런북 없는 유형) 알람 이름별 건수.

    조사 저장소 규칙대로 같은 `investigation_id`는 최신 행이 이긴다. 깨진 줄은 센 뒤 건너뛴다.
    """
    now = now or datetime.now(UTC)
    since = now - timedelta(days=days)
    latest: dict[str, dict[str, Any]] = {}
    bad = 0
    with Path(jsonl_path).open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                bad += 1
                continue
            if isinstance(rec, dict) and rec.get("investigation_id"):
                latest[str(rec["investigation_id"])] = rec
    by_playbook: Counter[str] = Counter()
    fallback_by_alarm: Counter[str] = Counter()
    no_playbook = 0
    total = 0
    for rec in latest.values():
        if (rec.get("kind") or "alarm") not in AUTO_KINDS or rec.get("status") != "done":
            continue
        ts = _ts(rec.get("ts"))
        if ts is None or ts < since:
            continue
        total += 1
        briefing = rec.get("briefing")
        pb = briefing.get("playbook") if isinstance(briefing, dict) else None
        if not isinstance(pb, dict) or not pb.get("id"):
            no_playbook += 1
            continue
        by_playbook[str(pb["id"])] += 1
        if pb["id"] == FALLBACK_ID:
            scope = pb.get("scope") if isinstance(pb.get("scope"), dict) else {}
            fallback_by_alarm[str(scope.get("alarm_name") or NO_ALARM_NAME)] += 1
    return {
        "window_days": days,
        "total": total,
        "by_playbook": dict(by_playbook.most_common()),
        "no_playbook": no_playbook,
        "fallback_total": sum(fallback_by_alarm.values()),
        "fallback_by_alarm": dict(fallback_by_alarm.most_common()),
        "bad_lines": bad,
    }


def _print_text(report: dict[str, Any]) -> None:
    cov = report["coverage"]
    print(f"런북 커버리지: {cov['coverage_pct']}% ({len(cov['covered'])}/{cov['files']}) — "
          f"{cov['playbook_dir']}")
    for item in cov["uncovered"]:
        print(f"  [런북 없음/무효] {item['file']}: {item['reason']}")
    if cov["error"]:
        print(f"  [세트 오류] {cov['error']}")
    print(f"검토 기한 경과: {len(cov['stale'])}건")
    for item in cov["stale"]:
        print(f"  {item['id']} — 담당 {item['owner']} · 검토 {item['last_reviewed']} · "
              f"기한 {item['due']}")
    dist = report.get("distribution")
    if dist:
        print(f"최근 {dist['window_days']}일 자동 조사 {dist['total']}건 "
              f"(플레이북 미적용 {dist['no_playbook']}건)")
        for pid, n in dist["by_playbook"].items():
            print(f"  {pid}: {n}")
        print(f"런북 없는 유형(→ fallback) {dist['fallback_total']}건")
        for name, n in dist["fallback_by_alarm"].items():
            print(f"  {name}: {n}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="런북 커버리지 점검(읽기 전용)")
    parser.add_argument("--playbook-dir", default=str(DEFAULT_PLAYBOOK_DIR))
    parser.add_argument("--investigations", help="조사 저장소 JSONL 경로(선택)")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--today", help="검토 기한 판정일 YYYY-MM-DD(기본 오늘)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--ci", action="store_true", help="커버리지 100%% 미만이면 exit 1")
    args = parser.parse_args(argv)

    today = date.fromisoformat(args.today) if args.today else date.today()
    report: dict[str, Any] = {"coverage": coverage_report(args.playbook_dir, today)}
    if args.investigations:
        report["distribution"] = fallback_distribution(args.investigations, args.days)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_text(report)
    if args.ci and report["coverage"]["coverage_pct"] < 100.0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
