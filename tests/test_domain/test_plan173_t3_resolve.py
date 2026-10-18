"""`time_spec.resolve` 해석기 특성화 — 명세 × 기준 시각 × 주체 골든 (plans/173 T3 · 166 I5).

plans/173 W1 T3 특성화 스냅샷 — Extract Function 전에 채록했다. 기대값은 「맞는 값」이 아니라
「지금 값」이다(D-337 ⑤). 리팩터 뒤 스냅샷을 갱신해야 한다면 그 변경은 리팩터가 아니다.

입력 매트릭스:

- 명세(`_specs()`) — 관계 10종(none·last·this·to_date·next·ago·absolute·between·since·until) ×
  단위·완결성·n · 시각(시·분 · 오늘/어제 오프셋) · 연도 명시/상대/미상 · 2월 29일 · 뒤집힌 범위 ·
  명시 기준(anchor=explicit) · 요청 입도(display_grain) · 출처(llm)
- 기준 시각(`_NOWS`) — 월 중간·윤년 월말 자정 직전·월초 자정·연말·반기 첫날 자정 직후·
  UTC 입력(월요일 정시) · naive 입력은 일부 명세(`_NAIVE_SPECS`)에만
- 주체 — metric · event

출력: `TimeResolution` 전 필드(start·end·grain·completeness·source·anchor_at·span·unbounded·notes·
anchor). 명세 생성이나 해석이 `TimeSpecError`를 내면 `["!", code]`. 시각은 KST면 오프셋을 뺀
ISO(`2026-10-13T10:37:20`), 아니면 전체 ISO로 적는다. `anchor_at`이 KST로 옮긴 기준 시각과 같으면
`"now"`로 적는다(무손실 축약). 스냅샷은 명세 id 한 줄에 `<기준 시각 id>|<주체>` 케이스를 모은다.

스냅샷 (재)채록: `UPDATE_PLAN173_T3_SNAPSHOT=1 python -m pytest <이 파일> -q -p no:cacheprovider`
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from src.domain.time_spec import KST, PartialDate, TimeSpec, TimeSpecError, resolve

SNAPSHOT_PATH = (
    Path(__file__).resolve().parent.parent / "snapshots" / "plan173_t3" / "resolve.json"
)
_UPDATE_ENV = "UPDATE_PLAN173_T3_SNAPSHOT"

_NOWS: dict[str, datetime] = {
    "mid": datetime(2026, 10, 13, 10, 37, 20, tzinfo=KST),       # 화 · 월 중간 · 시 중간
    "leap_eom": datetime(2024, 2, 29, 23, 59, 59, tzinfo=KST),   # 윤년 월말 자정 직전(목)
    "mar1_0": datetime(2024, 3, 1, 0, 0, 0, tzinfo=KST),         # 월초 자정 정각
    "eoy": datetime(2025, 12, 31, 23, 30, 15, tzinfo=KST),       # 연말 자정 직전
    "jul1": datetime(2026, 7, 1, 0, 0, 30, tzinfo=KST),          # 반기·분기 첫날 자정 직후(수)
    "utc_mon": datetime(2026, 10, 12, 8, 0, 0, tzinfo=UTC),      # = KST 월 17:00 정시
}
_NAIVE_NOW = datetime(2026, 10, 13, 10, 37, 20)  # naive → KST로 본다
_SUBJECTS: tuple[str, ...] = ("metric", "event")

_CAL_UNITS = ("hour", "day", "week", "month", "quarter", "half", "year")
_COMPLETENESS = (None, "complete", "rolling", "to_date")


def P(**kw: int) -> dict[str, int]:  # noqa: N802 — 부분값 선언 축약
    return kw


def _specs() -> dict[str, dict[str, Any]]:
    """명세 id → `TimeSpec` 키워드(부분값은 dict — 생성 오류도 채록하려고 지연 생성)."""
    s: dict[str, dict[str, Any]] = {}
    # ── none
    for dg in ("none", "hour", "day", "month"):
        s[f"none.dg={dg}"] = {"relation": "none", "display_grain": dg}
    for dg in ("none", "day"):
        s[f"none.unbounded.dg={dg}"] = {"relation": "none", "unbounded": True, "display_grain": dg}
    # ── last (달력 단위 × 완결성 × n)
    for unit in _CAL_UNITS:
        for comp in _COMPLETENESS:
            for n in (1, 3):
                s[f"last.{unit}.{comp}.n{n}"] = {
                    "relation": "last", "unit": unit, "completeness": comp, "n": n,
                }
    s["last.day.none.nNone"] = {"relation": "last", "unit": "day"}
    # ── last·ago 분 (D-328)
    for rel in ("last", "ago"):
        for n in (1, 30, 61):
            s[f"{rel}.minute.n{n}"] = {"relation": rel, "unit": "minute", "n": n}
    s["last.minute.rolling.n90"] = {
        "relation": "last", "unit": "minute", "n": 90, "completeness": "rolling",
    }
    s["this.minute"] = {"relation": "this", "unit": "minute"}  # 생성 오류(invalid_spec)
    # ── ago
    for unit in _CAL_UNITS:
        for n in (1, 2):
            s[f"ago.{unit}.n{n}"] = {"relation": "ago", "unit": unit, "n": n}
    s["ago.month.rolling.n1"] = {"relation": "ago", "unit": "month", "n": 1,
                                  "completeness": "rolling"}
    # ── this · to_date
    for unit in _CAL_UNITS:
        for comp in _COMPLETENESS:
            s[f"this.{unit}.{comp}"] = {"relation": "this", "unit": unit, "completeness": comp}
            s[f"to_date.{unit}.{comp}"] = {
                "relation": "to_date", "unit": unit, "completeness": comp,
            }
    # ── next
    for unit in _CAL_UNITS:
        for n in (1, 2):
            s[f"next.{unit}.n{n}"] = {"relation": "next", "unit": unit, "n": n}
    s["next.day.rolling"] = {"relation": "next", "unit": "day", "completeness": "rolling"}
    # ── absolute (달력 기간 하나)
    absolutes: dict[str, tuple[dict[str, int], str | None]] = {
        "y2026": (P(year=2026), None),
        "y2025": (P(year=2025), None),
        "y2024": (P(year=2024), None),
        "y2027": (P(year=2027), None),
        "m10": (P(month=10), None),
        "m12": (P(month=12), None),
        "m2": (P(month=2), None),
        "m7": (P(month=7), None),
        "m2d29": (P(month=2, day=29), None),
        "m12d31": (P(month=12, day=31), None),
        "m10d13": (P(month=10, day=13), None),
        "y2023m2d29": (P(year=2023, month=2, day=29), None),
        "yoff-1m6": (P(year_offset=-1, month=6), None),
        "yoff0m10": (P(year_offset=0, month=10), None),
        "yoff1m1": (P(year_offset=1, month=1), None),
        "y2026m10": (P(year=2026, month=10), None),
        "y2026m12": (P(year=2026, month=12), None),
        "y2024m2": (P(year=2024, month=2), None),
        "y2026m10d13": (P(year=2026, month=10, day=13), None),
        "y2024m2d29": (P(year=2024, month=2, day=29), None),
        "q.y2026m7": (P(year=2026, month=7), "quarter"),
        "q.m10": (P(month=10), "quarter"),
        "q.m1": (P(month=1), "quarter"),
        "q.m8": (P(month=8), "quarter"),  # 생성 오류(unit_misaligned)
        "h.y2026m7": (P(year=2026, month=7), "half"),
        "h.m1": (P(month=1), "half"),
        "h.m7": (P(month=7), "half"),
        "y.y2024": (P(year=2024), "year"),
        "y.y2025": (P(year=2025), "year"),
        "w.y2026m10d13": (P(year=2026, month=10, day=13), "week"),
        "w.m2d29": (P(month=2, day=29), "week"),
        "w.m10": (P(month=10), "week"),
        "d.m9d15h10": (P(month=9, day=15, hour=10), "day"),
        "m.m10d13": (P(month=10, day=13), "month"),
        "clock.m10d13h9": (P(month=10, day=13, hour=9), None),
        "clock.m10d13h14": (P(month=10, day=13, hour=14), None),
        "clock.y2026m10d13h10m30": (P(year=2026, month=10, day=13, hour=10, minute=30), None),
        "clock.off-1h10": (P(day_offset=-1, hour=10), None),
        "clock.off0h14m30": (P(day_offset=0, hour=14, minute=30), None),
        "clock.off0h10": (P(day_offset=0, hour=10), None),
        "clock.off0h23": (P(day_offset=0, hour=23), None),
        "clock.h.m2d29h23": (P(month=2, day=29, hour=23), "hour"),
    }
    for key, (part, unit) in absolutes.items():
        spec: dict[str, Any] = {"relation": "absolute", "abs_start": part}
        if unit is not None:
            spec["unit"] = unit
        s[f"absolute.{key}"] = spec
    s["absolute.y2026.comp=rolling"] = {
        "relation": "absolute", "abs_start": P(year=2026), "completeness": "rolling",
    }
    # ── between
    betweens: dict[str, tuple[dict[str, int], dict[str, int]]] = {
        "m11~m2": (P(month=11), P(month=2)),
        "y2025m6~m8": (P(year=2025, month=6), P(month=8)),
        "y2025m11~m2": (P(year=2025, month=11), P(month=2)),
        "m3~y2026m5": (P(month=3), P(year=2026, month=5)),
        "m8~y2026m5": (P(month=8), P(year=2026, month=5)),
        "m8d24~m9d22": (P(month=8, day=24), P(month=9, day=22)),
        "y2026m9~y2026m6": (P(year=2026, month=9), P(year=2026, month=6)),
        "y2027m1~y2027m3": (P(year=2027, month=1), P(year=2027, month=3)),
        "y2026m1~y2026m12": (P(year=2026, month=1), P(year=2026, month=12)),
        "m2d28~m2d29": (P(month=2, day=28), P(month=2, day=29)),
        "y2023m2d1~y2023m2d29": (P(year=2023, month=2, day=1), P(year=2023, month=2, day=29)),
        "y2024m1~y2024m2": (P(year=2024, month=1), P(year=2024, month=2)),
        "m10d1~m10d13": (P(month=10, day=1), P(month=10, day=13)),
        "clock.off0h9~off0h10": (P(day_offset=0, hour=9), P(day_offset=0, hour=10)),
        "clock.off0h10~off0h10m30": (P(day_offset=0, hour=10),
                                     P(day_offset=0, hour=10, minute=30)),
        "clock.off0h14~off0h9": (P(day_offset=0, hour=14), P(day_offset=0, hour=9)),
        "clock.off-1h22~off0h2": (P(day_offset=-1, hour=22), P(day_offset=0, hour=2)),
        "clock.off0h8~off1h0": (P(day_offset=0, hour=8), P(day_offset=1, hour=0)),
        "clock.m10d1h9m15~m10d13": (P(month=10, day=1, hour=9, minute=15),
                                    P(month=10, day=13)),
        "clock.m10d12~m10d13h8": (P(month=10, day=12), P(month=10, day=13, hour=8)),
    }
    for key, (a, b) in betweens.items():
        s[f"between.{key}"] = {"relation": "between", "abs_start": a, "abs_end": b}
    s["between.m11~m2.comp=to_date"] = {
        "relation": "between", "abs_start": P(month=11), "abs_end": P(month=2),
        "completeness": "to_date",
    }
    # ── since
    sinces: dict[str, dict[str, int]] = {
        "m9": P(month=9), "m12": P(month=12), "y2026m1": P(year=2026, month=1),
        "y2027": P(year=2027), "y2026m10": P(year=2026, month=10),
        "m10d1": P(month=10, day=1), "m10d13": P(month=10, day=13), "m3d1": P(month=3, day=1),
        "m2d29": P(month=2, day=29), "y2024": P(year=2024),
        "clock.off0h9": P(day_offset=0, hour=9),
        "clock.off0h10m30": P(day_offset=0, hour=10, minute=30),
        "clock.off0h10": P(day_offset=0, hour=10),
        "clock.off-1h23": P(day_offset=-1, hour=23),
        "clock.off0h0": P(day_offset=0, hour=0),
        "clock.m10d13h17": P(month=10, day=13, hour=17),
    }
    for key, part in sinces.items():
        s[f"since.{key}"] = {"relation": "since", "abs_start": part}
    s["since.m9.comp=complete"] = {
        "relation": "since", "abs_start": P(month=9), "completeness": "complete",
    }
    # ── until
    untils: dict[str, dict[str, int]] = {
        "m9": P(month=9), "y2026m12": P(year=2026, month=12), "m10d13": P(month=10, day=13),
        "y2025": P(year=2025), "m2d29": P(month=2, day=29),
        "clock.off0h12": P(day_offset=0, hour=12), "clock.off1h0": P(day_offset=1, hour=0),
        "clock.off0h9m45": P(day_offset=0, hour=9, minute=45),
    }
    for key, part in untils.items():
        s[f"until.{key}"] = {"relation": "until", "abs_end": part}
    s["until.m9.comp=to_date"] = {
        "relation": "until", "abs_end": P(month=9), "completeness": "to_date",
    }
    # ── 명시 기준(anchor=explicit)
    anchor = P(month=9, day=15)
    explicit: dict[str, dict[str, Any]] = {
        "last.day.n3": {"relation": "last", "unit": "day", "n": 3},
        "last.hour.n2": {"relation": "last", "unit": "hour", "n": 2},
        "last.month.rolling.n1": {"relation": "last", "unit": "month", "n": 1,
                                  "completeness": "rolling"},
        "this.month": {"relation": "this", "unit": "month"},
        "this.year": {"relation": "this", "unit": "year"},
        "to_date.week": {"relation": "to_date", "unit": "week"},
        "next.day.n2": {"relation": "next", "unit": "day", "n": 2},
        "ago.week.n1": {"relation": "ago", "unit": "week", "n": 1},
        "last.minute.n30": {"relation": "last", "unit": "minute", "n": 30},
        "since": {"relation": "since"},  # 생성 오류(anchor_explicit_unsupported_relation)
    }
    for key, kw in explicit.items():
        s[f"explicit.{key}"] = {**kw, "anchor": "explicit", "abs_start": anchor}
    s["explicit.last.hour.h10.n3"] = {
        "relation": "last", "unit": "hour", "n": 3, "anchor": "explicit",
        "abs_start": P(month=9, day=15, hour=10, minute=20),
    }
    s["explicit.this.day.y2027"] = {
        "relation": "this", "unit": "day", "anchor": "explicit",
        "abs_start": P(year=2027, month=1, day=5),
    }
    # ── 요청 입도(display_grain)
    for dg in ("hour", "day", "month"):
        s[f"dg.last.day.n30.{dg}"] = {
            "relation": "last", "unit": "day", "n": 30, "display_grain": dg,
        }
        s[f"dg.last.month.n3.{dg}"] = {
            "relation": "last", "unit": "month", "n": 3, "display_grain": dg,
        }
        s[f"dg.this.year.{dg}"] = {"relation": "this", "unit": "year", "display_grain": dg}
        s[f"dg.between.m8d24~m9d22.{dg}"] = {
            "relation": "between", "abs_start": P(month=8, day=24),
            "abs_end": P(month=9, day=22), "display_grain": dg,
        }
        s[f"dg.absolute.m10.{dg}"] = {
            "relation": "absolute", "abs_start": P(month=10), "display_grain": dg,
        }
        s[f"dg.since.clock.off0h9.{dg}"] = {
            "relation": "since", "abs_start": P(day_offset=0, hour=9), "display_grain": dg,
        }
    # ── 출처·스팬 전달
    s["src.llm.last.day.n7"] = {
        "relation": "last", "unit": "day", "n": 7, "source": "llm", "span": "최근 7일",
        "span_pos": (0, 5),
    }
    s["src.llm.absolute.m10"] = {
        "relation": "absolute", "abs_start": P(month=10), "source": "llm", "span": "10월",
    }
    s["src.llm.none"] = {"relation": "none", "source": "llm", "span": "전체"}
    s["src.llm.none.unbounded"] = {
        "relation": "none", "source": "llm", "span": "적이 있는", "unbounded": True,
    }
    return s


#: naive 기준 시각을 함께 넣는 명세(`_to_kst` naive 분기)
_NAIVE_SPECS: tuple[str, ...] = (
    "none.dg=none", "last.day.None.n3", "this.month.None", "absolute.m10",
    "since.clock.off0h9", "last.minute.n30",
)


def _build(kw: dict[str, Any]) -> TimeSpec:
    args = dict(kw)
    for key in ("abs_start", "abs_end"):
        if key in args:
            args[key] = PartialDate(**args[key])
    return TimeSpec(**args)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.utcoffset() == timedelta(hours=9):
        return dt.isoformat()[:-6]
    return dt.isoformat()


def _run(kw: dict[str, Any], now: datetime, subject: str) -> list[Any]:
    try:
        r = resolve(_build(kw), now, subject=subject)  # type: ignore[arg-type]
    except TimeSpecError as exc:
        return ["!", exc.code]
    kst_now = now.replace(tzinfo=KST) if now.tzinfo is None else now.astimezone(KST)
    anchor_at = "now" if r.anchor_at == kst_now else _iso(r.anchor_at)
    return [
        _iso(r.start), _iso(r.end), r.grain, r.completeness, r.source, anchor_at,
        r.span, r.unbounded, list(r.notes), r.anchor,
    ]


def _observe() -> dict[str, dict[str, Any]]:
    """명세 id → {`<기준 시각 id>|<주체>`: 출력}."""
    specs = _specs()
    out: dict[str, dict[str, Any]] = {}
    for sid, kw in specs.items():
        row: dict[str, Any] = {}
        for nid, now in _NOWS.items():
            for subject in _SUBJECTS:
                row[f"{nid}|{subject}"] = _run(kw, now, subject)
        if sid in _NAIVE_SPECS:
            for subject in _SUBJECTS:
                row[f"naive|{subject}"] = _run(kw, _NAIVE_NOW, subject)
        out[sid] = row
    return out


def test_resolve_matches_snapshot() -> None:
    """매트릭스 전 케이스의 해석이 채록 시점과 같다(필드 순서: start·end·grain·completeness·
    source·anchor_at·span·unbounded·notes·anchor · 오류는 `["!", code]`)."""
    observed = _observe()
    if os.environ.get(_UPDATE_ENV) == "1":
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        lines = ",\n".join(
            f" {json.dumps(k, ensure_ascii=False)}: {json.dumps(v, ensure_ascii=False)}"
            for k, v in observed.items()
        )
        SNAPSHOT_PATH.write_text("{\n" + lines + "\n}\n", encoding="utf-8")
    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert set(observed) == set(expected)
    diff = [
        (sid, case)
        for sid in expected
        for case in sorted(set(expected[sid]) | set(observed[sid]))
        if observed[sid].get(case) != expected[sid].get(case)
    ]
    assert not diff, "\n".join(
        f"{sid} {case}\n  기대 {expected[sid].get(case)}\n  실제 {observed[sid].get(case)}"
        for sid, case in diff[:10]
    )


def test_naive_now_equals_kst_now() -> None:
    """naive 기준 시각은 같은 벽시계의 KST와 같은 결과를 낸다(채록 매트릭스 내부 일관성)."""
    specs = _specs()
    aware = _NAIVE_NOW.replace(tzinfo=KST)
    for sid in _NAIVE_SPECS:
        for subject in _SUBJECTS:
            assert _run(specs[sid], _NAIVE_NOW, subject) == _run(specs[sid], aware, subject)
