"""refresh.sh 보조 — OpenAlex 응답으로 인용수를 비교하고, --write면 새 시점 컬럼을 추가한다."""
from __future__ import annotations

import csv
import datetime
import json
import sys


def main() -> int:
    csv_path = sys.argv[1]
    write = "--write" in sys.argv[2:]
    payload = json.load(sys.stdin)
    rows = payload["results"] if isinstance(payload, dict) else payload
    live = {
        (w.get("doi") or "").replace("https://doi.org/", ""): w.get("cited_by_count")
        for w in rows
    }

    with open(csv_path, encoding="utf-8") as f:
        recs = list(csv.DictReader(f))
    fields = list(recs[0].keys())
    # 가장 최근 시점 컬럼(정렬상 마지막 cited_by_*)을 비교 기준으로 삼는다
    prev_cols = sorted(c for c in fields if c.startswith("cited_by_"))
    prev_col = prev_cols[-1] if prev_cols else None
    today = datetime.date.today().strftime("%Y_%m_%d")
    new_col = f"cited_by_{today}"

    print(f"{'entry_id':16s} {'doi':32s} {'이전':>8s} {'현재':>8s}  변화")
    for r in recs:
        now = live.get(r["doi"])
        was = r.get(prev_col) if prev_col else None
        delta = ""
        if now is not None and was not in (None, "", "-"):
            try:
                delta = f"{int(now) - int(was):+d}"
            except ValueError:
                delta = "?"
        print(f"{r['entry_id']:16s} {r['doi']:32s} {str(was or '-'):>8s} {str(now if now is not None else '-'):>8s}  {delta}")
        if write and now is not None:
            r[new_col] = now

    if write:
        if new_col == prev_col:
            print(f"\n오늘 시점 컬럼({new_col})이 이미 있어 값만 갱신했습니다.")
        elif new_col not in fields:
            fields.append(new_col)
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            wr = csv.DictWriter(f, fieldnames=fields, restval="")
            wr.writeheader()
            wr.writerows(recs)
        print(f"\n{csv_path} 갱신 — 컬럼 '{new_col}' 기록(기존 시점 컬럼은 보존)")
    else:
        print("\n(읽기 전용. CSV에 반영하려면 --write)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
