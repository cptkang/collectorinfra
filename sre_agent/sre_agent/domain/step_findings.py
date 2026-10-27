"""「조사 흐름」 결정적 문장 — 결과 한 줄 · 왜 · 조회 · 건너뜀 (plans/187 W20 §5.6.4 ⓒ1~4 · D-035).

실행 기록 이벤트(`step_end.finding`·`step_start.because`·`tool_*.phrase`·`step_skip.because`)와
부분 브리핑 `sections[].finding`에 싣는 문장을 만든다. **LLM을 부르지 않는다** — 「왜」·「건너뜀」을
AI로 쓰면 실제 선택 근거와 다른 이유를 지어낼 수 있다(사후 합리화 · §5.6.4 ⓕ).

- 결과 한 줄(`summarize`): 액션별 문형. 사용자 예시 사슬 6종과 `apm.slow_tx_breakdown`·
  `apm.golden_signals`만 정교 문형, 나머지는 일반 문형(「판정 · 표 n행 · 첫 행 핵심 열」).
  **수치는 그 단계 표(또는 data)에 있는 값만** 쓴다(반올림만 허용 — 새 수치 계산 0). 개수·순위
  (「n건」·「n행」·「1위」)는 행 수·행 순서다. 결손·실패 단계는 사유 문장. 현재값 단계는
  「현재값」을 밝히고, 배지의 알람 대비 차이가 10분을 넘으면 「알람 시점과 다를 수 있음」을
  붙인다.
- 조회(`tool_phrase`): 도구명 → 「{출처} · {대상} {무엇}({조건})」(`TOOL_PHRASES`). 인자에서
  대상·시간창·개수만 꺼낸다. 사전에 없는 도구는 None(화면은 현행 한 줄).
- 왜(`when_because`·`default_because`)·건너뜀(`skip_because`): `when` 조건식 원소(비교·`in`) 단위
  문형 사전. 조건식 원문은 넣지 않는다(원문은 기존 `reason`에만).

판독 enum의 한국어는 이 모듈의 `READING_LABELS`·`ENUM_LABELS`가 정본이다(판독 모듈
`playbook_readings`에는 enum 한국어 표기가 없어 그 독스트링의 표현 — 「WAS 밖 프로세스」·「부하
증가」 — 을 옮겼다).

표준 라이브러리와 같은 domain의 `playbook_expr`만 쓴다(domain 순수). 적용 단계 제목(액션 제목)은
application이 인자로 넘긴다.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sre_agent.domain.playbook_expr import (
    READING_NAMES,
    Expr,
    Tri,
    _And,
    _Cmp,
    _In,
    _Lit,
    _Node,
    _Not,
    _Or,
    _Path,
    _truth,
)

#: 현재값 단계 — 알람 시각과 조사 시각 차이가 이 분을 넘으면 결과 한 줄에 주의를 붙인다([제안]).
REALTIME_DRIFT_MINUTES = 10
REALTIME_MARK = "현재값"
REALTIME_DRIFT_NOTE = "알람 시점과 다를 수 있음"
#: 건너뜀 사유 — 시간 예산 소진.
BUDGET_SKIP = "시간 예산 소진으로 생략"
#: 건너뜀 사유 — 원소 단위로 문장화할 수 없는 조건.
WHEN_SKIP_FALLBACK = "실행 조건을 충족하지 않아 생략"
#: 왜 — 실행 조건을 판정할 수 없어(판정 불가 원소를 문장화하지 못한 경우) 실행한 단계.
WHEN_UNKNOWN = "실행 조건을 판정할 수 없어 확인을 위해 실행"

#: 결과 한 줄에 싣는 사유·SQL·문자열 칸의 길이 상한(글자).
_REASON_CHARS = 160
_CELL_CHARS = 40
_SQL_CHARS = 80
_QUOTE_CHARS = 80

VERDICT_LABELS: dict[str, str] = {"degraded": "이상", "normal": "정상", "no_was": "WAS 없음"}
#: `S.verdict != 값`이 참일 때의 긍정 문장(「≠」 기호 대신).
VERDICT_NOT_SENTENCES: dict[str, str] = {"no_was": "이 호스트에 WAS 인스턴스가 있음"}
STATUS_LABELS: dict[str, str] = {
    "ok": "있음",
    "missing": "결손",
    "skipped": "생략",
    "pending_synthesis": "종합 대기",
}
KIND_LABELS: dict[str, str] = {
    "cpu": "CPU",
    "memory": "메모리",
    "filesystem": "파일시스템",
    "disk_io": "디스크 I/O",
}
#: 판독 이름 → 한국어(§4.2 R-A~R-H).
#: 지표 종류의 단위 — 사용률 지표(절대 임계 90%로 판정하는 종류)만. 데이터에 `unit`이 있으면 그 값.
KIND_UNITS: dict[str, str] = {"cpu": "%", "memory": "%", "filesystem": "%"}

READING_LABELS: dict[str, str] = {
    "scope": "영향 범위",
    "service_spread": "서비스 분포",
    "load_mode": "부하 유형",
    "little_check": "액티브 과다 점검",
    "dominant": "지배 구간",
    "db_split": "DB 구간 분리",
    "cpu_owner": "CPU 주인",
    "heap_shape": "힙 모양",
    "host_saturation": "호스트 포화",
    "fs_eta": "파일시스템 포화 예상",
    "change_candidates": "변경 후보",
}
#: 판독 enum → 한국어(없는 값은 원래 값 그대로).
ENUM_LABELS: dict[str, dict[str, str]] = {
    "cpu_owner": {
        "was_load": "WAS 처리(부하 증가)",
        "was_logic": "WAS 처리(부하 증가 없음)",
        "other_process": "WAS 밖 프로세스",
        "none": "해당 없음",
    },
    "dominant": {
        "sql": "SQL",
        "fetch": "페치",
        "external": "외부 호출",
        "cpu": "CPU",
        "network": "네트워크",
        "other": "기타",
    },
}
#: 스텝 결과의 보조 사실 이름(`S2.pool` 등) → 한국어 · 값 → 한국어.
FACT_LABELS: dict[str, str] = {"pool": "DB 풀", "gc": "GC", "change": "변경"}
FACT_VALUE_LABELS: dict[str, str] = {
    "exhausted": "고갈",
    "pressure": "압박",
    "near": "알람 직전",
    "far": "먼 시점",
    "none": "없음",
    "ok": "정상",
}
#: 게이트웨이 WAS 신호 kind → 한국어.
SIGNAL_LABELS: dict[str, str] = {
    "was_db_pool_exhaustion": "DB 풀 고갈 신호",
    "was_gc_stall": "GC 정지 신호",
    "was_heap_pressure": "힙 압박 신호",
}
_CMP_LABELS: dict[str, str] = {
    "==": "=",
    "<": "미만",
    "<=": "이하",
    ">": "초과",
    ">=": "이상",
}
_DRIFT = re.compile(r"알람 (?:후 )?(\d+)분")


# ── 공용 도우미 ─────────────────────────────────────────────────────────────


def _num(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return float(value)


def fmt_number(value: object) -> str | None:
    """표 값의 표기 — 반올림만 한다(1 이상은 소수 1자리 · 1 미만은 3자리 · 끝 0 제거)."""
    v = _num(value)
    if v is None:
        return None
    if isinstance(value, int):
        return str(value)
    digits = 1 if abs(v) >= 1 else 3
    text = f"{round(v, digits):.{digits}f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _clip(text: object, limit: int) -> str:
    s = " ".join(str(text).split())
    return s if len(s) <= limit else s[: max(0, limit - 1)] + "…"


def _cell(value: object) -> str | None:
    if value is None or value == "":
        return None
    n = fmt_number(value)
    if n is not None:
        return n
    if isinstance(value, bool):
        return "예" if value else "아니오"
    if isinstance(value, (dict, list)):
        return None
    return _clip(value, _CELL_CHARS)


def _batchim(word: str) -> bool | None:
    """마지막 글자의 받침 유무(한글·숫자·일부 영문) — 판단 불가면 None."""
    stripped = word.rstrip(" )」』'\"")
    if not stripped:
        return None
    ch = stripped[-1]
    if "가" <= ch <= "힣":
        return (ord(ch) - 0xAC00) % 28 != 0
    if ch.isdigit():
        return ch in "013678"
    if ch.isalpha() and ch.isascii():
        return ch.lower() in "lmnr"
    return None


def josa(word: str, with_batchim: str, without: str) -> str:
    """조사 — 받침이 있으면 `with_batchim`, 없으면 `without`, 판단 불가면 「을(를)」 꼴."""
    b = _batchim(word)
    if b is None:
        return f"{word}{with_batchim}({without})"
    return f"{word}{with_batchim if b else without}"


def _rows(section: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    data = section.get("data")
    rows = data.get("rows") if isinstance(data, Mapping) else None
    return [r for r in rows or [] if isinstance(r, Mapping)]


def _map(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _data(section: Mapping[str, Any]) -> Mapping[str, Any]:
    data = section.get("data")
    return data if isinstance(data, Mapping) else {}


def _verdict(section: Mapping[str, Any]) -> str:
    v = section.get("verdict")
    return VERDICT_LABELS.get(str(v), str(v)) if v else "판정 없음"


def _enum(name: str, value: object) -> str:
    return ENUM_LABELS.get(name, {}).get(str(value), str(value))


def _realtime_tail(section: Mapping[str, Any]) -> str:
    """현재값 배지가 있으면 「현재값」(+ 알람 대비 10분 초과면 주의) 꼬리."""
    badges = [str(b) for b in section.get("badges") or [] if str(b).startswith(REALTIME_MARK)]
    if not badges:
        return ""
    tail = f" · {REALTIME_MARK}"
    m = _DRIFT.search(badges[0])
    if m and int(m.group(1)) > REALTIME_DRIFT_MINUTES:
        tail += f" · {REALTIME_DRIFT_NOTE}"
    return tail


# ── 결과 한 줄 — 정교 문형 ───────────────────────────────────────────────────


def _usage_trend(section: Mapping[str, Any], readings: Mapping[str, Any]) -> str | None:
    findings = _data(section).get("findings")
    if not isinstance(findings, Mapping) or not findings:
        return None
    parts: list[tuple[int, str]] = []
    for kind, f in findings.items():
        if not isinstance(f, Mapping):
            continue
        anomalous = bool(f.get("is_anomalous"))
        unit = str(f.get("unit") or KIND_UNITS.get(str(kind), ""))
        peak = fmt_number(f.get("peak_value"))
        base = fmt_number(f.get("baseline_mean"))
        peak = f"{peak}{unit}" if peak else peak
        base = f"{base}{unit}" if base else base
        detail = [f"최대 {peak}"] if peak else []
        if base:
            detail.append(f"평소 평균 {base}")
        name = KIND_LABELS.get(str(kind), str(kind))
        text = f"{name} {'이상' if anomalous else '평소 범위'}"
        if detail:
            text += f"({' · '.join(detail)})"
        parts.append((0 if anomalous else 1, text))
    parts.sort(key=lambda x: x[0])
    head = "이상 지표 있음" if section.get("verdict") == "degraded" else "모든 지표 평소 범위"
    out = f"{head} — " + " · ".join(t for _, t in parts[:4])
    missing = [KIND_LABELS.get(str(k), str(k)) for k in section.get("missing") or []]
    if missing:
        out += f" · 결손 {', '.join(missing)}"
    return out


def _process_top(section: Mapping[str, Any], readings: Mapping[str, Any]) -> str | None:
    rows = _rows(section)
    if not rows:
        return "프로세스 0건"
    picks = []
    for i, r in enumerate(rows[:3], start=1):
        cpu = fmt_number(r.get("pcpu"))
        name = _clip(r.get("name") or "?", _CELL_CHARS)
        picks.append(f"{i}위 {name}" + (f" {cpu}%" if cpu else ""))
    return f"상위 {len(rows)}개 중 " + " · ".join(picks)


def _host_to_was(section: Mapping[str, Any], readings: Mapping[str, Any]) -> str | None:
    if section.get("verdict") == "no_was":
        return "이 호스트에 WAS 인스턴스 없음"
    insts = [i for i in _data(section).get("instances") or [] if isinstance(i, Mapping)]
    if not insts:
        return None
    deg = [i for i in insts if i.get("verdict") == "degraded"]
    unknown = [i for i in insts if i.get("verdict") not in ("degraded", "normal")]
    if not deg:
        out = f"WAS 인스턴스 {len(insts)}개 응답·오류 정상"
    else:
        names = ", ".join(str(i.get("instance")) for i in deg[:3])
        out = f"WAS 인스턴스 {len(insts)}개 중 이상 {len(deg)}개({names})"
        p95 = fmt_number((deg[0].get("stats") or {}).get("p95_ms"))
        if p95:
            out += f" — {deg[0].get('instance')} p95 {p95}ms"
    if unknown:
        out += f" · 판정 불가 {len(unknown)}개"
    return out


def _signal_names(signals: Iterable[object]) -> list[str]:
    out: list[str] = []
    for s in signals:
        kind = s.get("kind") if isinstance(s, Mapping) else s
        if kind:
            label = SIGNAL_LABELS.get(str(kind), f"신호 {kind}")
            if label not in out:
                out.append(label)
    return out


def _runtime_and_pool(section: Mapping[str, Any], readings: Mapping[str, Any]) -> str | None:
    targets = _data(section).get("targets")
    parts: list[str] = []
    if isinstance(targets, Mapping):
        for name, t in list(targets.items())[:3]:
            if not isinstance(t, Mapping):
                continue
            summ = _map(t.get("trend_summary"))
            bits = []
            proc = summ.get("process_cpu_pct")
            if isinstance(proc, Mapping) and (v := fmt_number(proc.get("max"))):
                bits.append(f"프로세스 CPU 최대 {v}%")
            heap = summ.get("heap_used_mb")
            if isinstance(heap, Mapping) and (v := fmt_number(heap.get("max"))):
                bits.append(f"힙 최대 {v}MB")
            pool = t.get("pool")
            if isinstance(pool, Mapping) and (v := fmt_number(pool.get("db_pool_usage_ratio"))):
                bits.append(f"DB 풀 사용률 {v}")
            if bits:
                parts.append(f"{name} " + " · ".join(bits))
    signals = _signal_names(_data(section).get("was_signals") or [])
    if signals:
        parts.append(", ".join(signals))
    owner = readings.get("cpu_owner")
    if owner is not None:
        parts.append(f"CPU 주인 = {_enum('cpu_owner', owner)}(R-E)")
    return " · ".join(parts) if parts else None


def _top_by(rows: Sequence[Mapping[str, Any]], key: str) -> Mapping[str, Any] | None:
    scored = [r for r in rows if _num(r.get(key)) is not None]
    return max(scored, key=lambda r: _num(r.get(key)) or 0.0) if scored else None


def _who(row: Mapping[str, Any]) -> str:
    inst = row.get("instance") or row.get("instance_name")
    app = row.get("application")
    return " ".join(_clip(x, _CELL_CHARS) for x in (inst, app) if x) or "?"


def _active_services(section: Mapping[str, Any], readings: Mapping[str, Any]) -> str | None:
    rows = _rows(section)
    if not rows:
        return "실행 중 요청 0건"
    parts = []
    cpu = _top_by(rows, "cpu_ms")
    if cpu is not None:
        parts.append(f"CPU 시간 1위 {_who(cpu)} {fmt_number(cpu.get('cpu_ms'))}ms")
    el = _top_by(rows, "elapsed_ms")
    if el is not None:
        parts.append(f"경과 1위 {_who(el)} {fmt_number(el.get('elapsed_ms'))}ms")
    return f"실행 중 요청 {len(rows)}건" + (" — " + " · ".join(parts) if parts else "")


def _active_sql(section: Mapping[str, Any], readings: Mapping[str, Any]) -> str | None:
    rows = _rows(section)
    if not rows:
        return "실행 중 요청 없음 — 상세 조회 대상 없음"
    top = _top_by(rows, "cpu_ms") or rows[0]
    out = f"실행 중 SQL {len(rows)}건 — 1위 {_who(top)}"
    if (cpu := fmt_number(top.get("cpu_ms"))) is not None:
        out += f" CPU {cpu}ms"
    if (el := fmt_number(top.get("elapsed_ms"))) is not None:
        out += f" · 경과 {el}ms"
    if top.get("sql"):
        out += f": {_clip(top.get('sql'), _SQL_CHARS)}"
    return out


_TX_PARTS: tuple[tuple[str, str], ...] = (
    ("sql_ms", "SQL"),
    ("fetch_ms", "페치"),
    ("external_ms", "외부 호출"),
    ("cpu_ms", "CPU"),
    ("network_ms", "네트워크"),
)


def _slow_tx(section: Mapping[str, Any], readings: Mapping[str, Any]) -> str | None:
    rows = _rows(section)
    if not rows:
        return None
    top = rows[0]
    out = f"느린 거래 {len(rows)}건 중 1위 {_clip(top.get('application') or '?', _CELL_CHARS)}"
    if (rt := fmt_number(top.get("response_time_ms"))) is not None:
        out += f" 응답 {rt}ms"
    comps = [(label, top.get(key)) for key, label in _TX_PARTS if _num(top.get(key)) is not None]
    if comps:
        label, value = max(comps, key=lambda c: _num(c[1]) or 0.0)
        out += f" — 최대 구간 {label} {fmt_number(value)}ms"
    dom = readings.get("dominant")
    if dom and dom != "n/a":
        out += f" · 지배 구간 = {_enum('dominant', dom)}(R-C)"
    return out


def _golden_signals(section: Mapping[str, Any], readings: Mapping[str, Any]) -> str | None:
    targets = _data(section).get("targets")
    if not isinstance(targets, Mapping) or not targets:
        return None
    items = [(n, t) for n, t in targets.items() if isinstance(t, Mapping)]
    items.sort(key=lambda x: 0 if x[1].get("verdict") == "degraded" else 1)
    parts = []
    for name, t in items[:3]:
        inc = _map(t.get("incident"))
        verdict = VERDICT_LABELS.get(str(t.get("verdict")), "판정 없음")
        bits = []
        if (p95 := fmt_number(inc.get("p95_ms"))) is not None:
            base = fmt_number(t.get("baseline_p95_ms"))
            bits.append(f"p95 {p95}ms" + (f"(기준선 {base}ms)" if base is not None else ""))
        if (tps := fmt_number(inc.get("tps"))) is not None:
            bits.append(f"TPS {tps}")
        if (err := fmt_number(inc.get("error_rate"))) is not None:
            bits.append(f"오류율 {err}")
        parts.append(f"{name} {verdict}" + (" — " + " · ".join(bits) if bits else ""))
    return " / ".join(parts)


def _generic(section: Mapping[str, Any], readings: Mapping[str, Any]) -> str | None:
    """일반 문형 — 「판정 · 표 n행 · 첫 행 핵심 열」."""
    table = _map(section.get("table"))
    cols = [str(c) for c in table.get("columns") or []]
    rows = [r for r in table.get("rows") or [] if isinstance(r, (list, tuple))]
    if not rows:
        return f"{_verdict(section)} · 결과 0건"
    cells = []
    for col, value in zip(cols, rows[0]):
        text = _cell(value)
        if text is not None:
            cells.append(f"{col} {text}")
        if len(cells) == 3:
            break
    out = f"{_verdict(section)} · 표 {len(rows)}행"
    if cells:
        out += " · 첫 행 " + ", ".join(cells)
    return out


#: 액션 → 정교 문형(그 밖은 일반 문형).
PRECISE_ACTIONS: dict[str, Callable[[Mapping[str, Any], Mapping[str, Any]], str | None]] = {
    "host.usage_trend": _usage_trend,
    "host.process_top": _process_top,
    "scope.host_to_was": _host_to_was,
    "apm.runtime_and_pool": _runtime_and_pool,
    "apm.active_services": _active_services,
    "apm.active_sql": _active_sql,
    "apm.slow_tx_breakdown": _slow_tx,
    "apm.golden_signals": _golden_signals,
}


def _failure(section: Mapping[str, Any]) -> str:
    status = str(section.get("status") or "missing")
    head = "생략" if status == "skipped" else "수집 결손"
    reason = section.get("reason")
    if reason:
        return f"{head} — {_clip(reason, _REASON_CHARS)}"
    missing = [str(m) for m in section.get("missing") or []]
    if missing:
        return f"{head} — {', '.join(missing)} 없음"
    return head


def summarize(
    action: str, section: Mapping[str, Any], readings: Mapping[str, Any] | None = None
) -> str | None:
    """단계 결과 한 줄(결정적 · LLM 0).

    section:  `StepResult.to_dict()` 모양(`status`·`verdict`·`table`·`data`·`badges`·`missing`·
              `reason`).
    readings: 그 시점까지의 판독 값(이름 → enum · `Readings.values`).
    종합 자리(`synthesize`·`pending_synthesis`)는 None. 결손·생략은 사유 문장.
    """
    status = section.get("status")
    if action == "synthesize" or status == "pending_synthesis":
        return None
    if status != "ok":
        return _failure(section)
    fn = PRECISE_ACTIONS.get(action, _generic)
    try:
        text = fn(section, readings or {})
    except Exception:  # noqa: BLE001 — 문형 실패는 일반 문형으로(사실 문장은 빈칸 0)
        text = None
    if text is None:
        text = _generic(section, readings or {}) or _verdict(section)
    if section.get("missing"):
        text += " · 일부 결손"
    tail = _realtime_tail(section)
    if tail and REALTIME_MARK in text:
        tail = tail.replace(f" · {REALTIME_MARK}", "", 1)
    return text + tail


# ── 조회 문장 ───────────────────────────────────────────────────────────────


def _hm(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip()).strftime("%H:%M")
    except ValueError:
        return None


def _window(args: Mapping[str, Any]) -> str | None:
    """`reference_time`·`lookback_minutes` → 「hh:mm~hh:mm」."""
    ref = args.get("reference_time")
    lb = _num(args.get("lookback_minutes"))
    if not isinstance(ref, str) or lb is None or lb <= 0:
        return None
    try:
        end = datetime.fromisoformat(ref.strip())
    except ValueError:
        return None
    return f"{(end - timedelta(minutes=lb)).strftime('%H:%M')}~{end.strftime('%H:%M')}"


def _span(start: object, end: object, *, with_date: bool = False) -> str | None:
    a, b = _hm(start), _hm(end)
    if not (a and b):
        return None
    if with_date and isinstance(start, str):
        try:
            a = f"{datetime.fromisoformat(start.strip()).strftime('%m-%d')} {a}"
        except ValueError:
            pass
    return f"{a}~{b}"


def _cond(*parts: str | None) -> str:
    kept = [p for p in parts if p]
    return f"({' · '.join(kept)})" if kept else ""


def _inst(args: Mapping[str, Any]) -> str:
    return str(args.get("instance_name") or args.get("instance") or "").strip()


def _server(args: Mapping[str, Any]) -> str:
    return str(args.get("server_name") or args.get("hostname") or "").strip()


def _top(args: Mapping[str, Any], key: str = "n") -> str | None:
    n = args.get(key)
    return f"상위 {n}" if isinstance(n, int) and not isinstance(n, bool) else None


def _apm(subject: str, what: str, cond: str = "", realtime: bool = False) -> str:
    text = f"제니퍼 · {subject} {what}{cond}".replace("  ", " ")
    return text + (f" · {REALTIME_MARK}" if realtime else "")


def _polestar(subject: str, what: str, cond: str = "", realtime: bool = False) -> str:
    text = f"폴스타 · {subject} {what}{cond}".replace("  ", " ")
    return text + (f" · {REALTIME_MARK}" if realtime else "")


def _instance_map(a: Mapping[str, Any]) -> str:
    if a.get("hostname"):
        return _apm(str(a["hostname"]), "호스트의 WAS 인스턴스 목록")
    if a.get("business"):
        return _apm(f"업무 {a['business']}", "인스턴스 목록")
    return _apm(str(a.get("query") or ""), "인스턴스 찾기")


def _runtime(a: Mapping[str, Any]) -> str:
    what = "프로세스 CPU·액티브 서비스 추세" if a.get("metrics") else "JVM 런타임(힙·GC)"
    return _apm(_inst(a), what, _cond(_window(a)))


def _metric(a: Mapping[str, Any]) -> str:
    kind = KIND_LABELS.get(str(a.get("kind")), str(a.get("kind") or "지표"))
    return _polestar(_server(a), f"{kind} 시간대별 추이", _cond(_window(a), "전일·전주 비교"))


def _process(a: Mapping[str, Any]) -> str:
    sort = str(a.get("sort") or "cpu")
    order = f"{'CPU' if sort == 'cpu' else sort} 순"
    return _polestar(_server(a), "프로세스 목록", _cond(order, _top(a, "top_n")), realtime=True)


def _period_compare(a: Mapping[str, Any]) -> str:
    base = _span(a.get("baseline_start"), a.get("baseline_end"), with_date=True)
    return _apm(
        _inst(a),
        "기간 비교",
        _cond(_span(a.get("current_start"), a.get("current_end")),
              f"기준선 {base}" if base else None),
    )


def _alarm_history(a: Mapping[str, Any]) -> str:
    hours = _num(a.get("hours"))
    days = f"최근 {int(hours // 24)}일" if hours is not None and hours >= 24 else None
    name = f"「{a['alarm_name']}」 " if a.get("alarm_name") else ""
    return _polestar(_server(a), f"{name}재발 이력", _cond(days))


#: 도구명 → 조회 문장 함수(인자 → 문장). 사전 밖 도구는 문장 없음(현행 한 줄).
TOOL_PHRASES: dict[str, Callable[[Mapping[str, Any]], str]] = {
    "apm_instance_map": _instance_map,
    "apm_app_health": lambda a: _apm(_inst(a), "응답·처리량·오류", _cond(_window(a))),
    "apm_period_compare": _period_compare,
    "apm_runtime_health": _runtime,
    "apm_resource_pool": lambda a: _apm(_inst(a), "DB 커넥션 풀", realtime=True),
    "apm_slow_transactions": lambda a: _apm(
        _inst(a), "느린 거래", _cond(_top(a), _window(a))
    ),
    "apm_transaction_profile": lambda a: _apm(_inst(a), "거래 프로파일(1위 건)"),
    "apm_events": lambda a: _apm(
        _inst(a), "오류 이벤트" if a.get("record") == "error" else "이벤트",
        _cond(_top(a), _window(a)),
    ),
    "apm_active_services": lambda a: _apm(
        _inst(a), "실행 중 요청", _cond(_top(a)), realtime=True
    ),
    "apm_active_detail": lambda a: _apm(_inst(a), "실행 중 요청의 SQL 문", realtime=True),
    "apm_source_changes": lambda a: _apm(_inst(a), "소스 변경 이력"),
    "apm_change_impact": lambda a: _apm(_inst(a), "변경 영향"),
    "polestar_metric_trend": _metric,
    "polestar_incident_alarms": lambda a: _polestar(_server(a), "전후 알람", _cond(_window(a))),
    "polestar_change_history": lambda a: _polestar(_server(a), "변경 이력", _cond(_window(a))),
    "polestar_process_snapshot": _process,
    "polestar_resource_status": lambda a: _polestar(_server(a), "자원 가용 상태"),
    "polestar_topology": lambda a: _polestar(
        _server(a), "토폴로지",
        _cond(f"{a['max_hops']}홉" if isinstance(a.get("max_hops"), int) else None),
    ),
    "polestar_alarm_history": _alarm_history,
}


def tool_phrase(tool: str, args: Mapping[str, Any] | None) -> str | None:
    """도구 호출 1건의 조회 문장. 사전 밖 도구·문장화 실패는 None(화면은 현행 한 줄)."""
    fn = TOOL_PHRASES.get(tool)
    if fn is None:
        return None
    try:
        return " ".join(fn(args or {}).split())
    except Exception:  # noqa: BLE001 — 문장화 실패는 문장 없음(현행 한 줄)
        return None


# ── 왜 · 건너뜀 (`when` 원소 문형) ─────────────────────────────────────────


@dataclass(frozen=True)
class _Atom:
    node: _Node
    path: tuple[str, ...]
    op: str  # == != < <= > >= in
    values: tuple[object, ...]
    negated: bool


def _atoms(node: _Node, negated: bool = False) -> list[_Atom]:
    if isinstance(node, (_And, _Or)):
        return [a for item in node.items for a in _atoms(item, negated)]
    if isinstance(node, _Not):
        return _atoms(node.item, not negated)
    if isinstance(node, _In) and isinstance(node.left, _Path):
        return [_Atom(node, node.left.parts, "in", tuple(node.items), negated)]
    if isinstance(node, _Cmp):
        if isinstance(node.left, _Path) and isinstance(node.right, _Lit):
            return [_Atom(node, node.left.parts, node.op, (node.right.value,), negated)]
        if isinstance(node.right, _Path) and isinstance(node.left, _Lit):
            flip = {"<": ">", "<=": ">=", ">": "<", ">=": "<="}.get(node.op, node.op)
            return [_Atom(node, node.right.parts, flip, (node.left.value,), negated)]
    return []


def _is_step(root: str) -> bool:
    return root not in READING_NAMES


def _value_label(atom: _Atom, value: object) -> str:
    root = atom.path[0]
    field = atom.path[-1] if len(atom.path) > 1 else ""
    if not _is_step(root):
        return _enum(root, value)
    if field == "verdict":
        return VERDICT_LABELS.get(str(value), str(value))
    if field == "status":
        return STATUS_LABELS.get(str(value), str(value))
    return FACT_VALUE_LABELS.get(str(value), str(value))


def _subject(atom: _Atom, titles: Mapping[str, str]) -> str:
    root = atom.path[0]
    if not _is_step(root):
        return READING_LABELS.get(root, root)
    title = titles.get(root, root)
    rest = atom.path[1:]
    if rest in (("verdict",), ("status",)):
        return f"{title} {'판정' if rest[0] == 'verdict' else '결과'}"
    label = ".".join(FACT_LABELS.get(p, p) for p in rest)
    return f"{title}의 {label}" if label else title


def atom_sentence(atom: _Atom, titles: Mapping[str, str]) -> str:
    """조건 원소 1개의 긍정 문장(조건식 원문 없음)."""
    root = atom.path[0]
    rest = atom.path[1:]
    vals = [_value_label(atom, v) for v in atom.values]
    if _is_step(root) and rest == ("verdict",) and len(atom.values) == 1:
        title = titles.get(root, root)
        if atom.op == "==" and atom.values[0] == "degraded":
            return f"{title}에서 이상 판정"
        if atom.op == "!=" and atom.values[0] == "normal":
            return f"{title} 판정이 정상이 아님"
        if atom.op == "!=" and str(atom.values[0]) in VERDICT_NOT_SENTENCES:
            return VERDICT_NOT_SENTENCES[str(atom.values[0])]
    if _is_step(root) and rest == ("status",) and len(atom.values) == 1:
        title = titles.get(root, root)
        if atom.values[0] == "ok":
            return f"{title} 결과 있음" if atom.op == "==" else f"{title} 결과 없음"
    subject = _subject(atom, titles)
    if atom.op == "in":
        return f"{subject} = {' 또는 '.join(vals)}"
    op = _CMP_LABELS.get(atom.op, atom.op)
    if atom.op == "!=":
        return f"{josa(subject, '이', '가')} {josa(vals[0], '이', '가')} 아님"
    if atom.op == "==":
        return f"{subject} {op} {vals[0]}"
    return f"{subject} {vals[0]} {op}"


def _state_sentence(atom: _Atom, context: Mapping[str, object],
                    titles: Mapping[str, str]) -> str | None:
    """거짓이 된 원소의 실제 상태 문장(건너뜀 사유) — 값이 없으면 None."""
    root = atom.path[0]
    cur: object = context.get(root)
    for p in atom.path[1:]:
        cur = cur.get(p) if isinstance(cur, Mapping) else None
    if cur is None or isinstance(cur, (Mapping, list)):
        return None
    rest = atom.path[1:]
    if _is_step(root) and rest == ("verdict",):
        return f"{titles.get(root, root)} 결과가 {VERDICT_LABELS.get(str(cur), str(cur))}"
    if _is_step(root) and rest == ("status",):
        if str(cur) == "skipped":
            title = titles.get(root, root)
            return f"{josa(title, '을', '를')} 건너뛰어"
        return f"{titles.get(root, root)} 결과가 {STATUS_LABELS.get(str(cur), str(cur))}"
    return f"{_subject(atom, titles)} = {_value_label(atom, cur)}"


def _effective(atom: _Atom, context: Mapping[str, object]) -> Tri:
    t = _truth(atom.node, context)
    if t is Tri.UNKNOWN or not atom.negated:
        return t
    return Tri.FALSE if t is Tri.TRUE else Tri.TRUE


def skip_because(expr: Expr, context: Mapping[str, object], titles: Mapping[str, str]) -> str:
    """`when` 거짓으로 건너뛴 사유 문장 — 「{근거 단계 제목} 결과가 정상이라 생략」 꼴.

    거짓이 된 원소마다 실제 상태를 문장화한다(최대 2개). 문장화할 수 없으면 「실행 조건을 충족하지
    않아 생략」. 조건식 원문은 넣지 않는다.
    """
    try:
        states: list[str] = []
        for atom in _atoms(expr._root):
            if _effective(atom, context) is not Tri.FALSE:
                continue
            text = _state_sentence(atom, context, titles)
            if text and text not in states:
                states.append(text)
        if not states:
            return WHEN_SKIP_FALLBACK
        states = states[:2]
        skipped = [t for t in states if t.endswith(" 건너뛰어")]
        others = [t for t in states if t not in skipped]
        clauses = list(skipped)
        if others:
            joined = ", ".join(others)
            clauses.append(josa(joined, "이라", "라"))
        return f"{', '.join(clauses)} 생략"
    except Exception:  # noqa: BLE001 — 사유 문장화 실패는 고정 문장
        return WHEN_SKIP_FALLBACK


def default_because(why: str | None, focus: str | None, title: str) -> str:
    """조건 없는 단계의 왜 — 작성자 한 문장(`narrate.why`) → 「{focus}를 확인」 → 「{제목}을 확인」.

    LLM을 쓰지 않는다(사후 합리화 방지).
    """
    if why:
        return why
    return f"{josa(focus or title, '을', '를')} 확인"


def when_because(
    expr: Expr,
    context: Mapping[str, object],
    *,
    titles: Mapping[str, str],
    findings: Mapping[str, str],
    tail: str,
    reading_steps: Mapping[str, Sequence[str]] | None = None,
    reading_reasons: Mapping[str, str] | None = None,
) -> str:
    """`when`으로 실행된 단계의 왜 — 참이 된 조건 원소 문장 + 근거 단계 결과 한 줄 인용 → tail.

    조건이 판정 불가로 실행됐으면 「「{조건}」인지 판정할 수 없어(사유: …) 확인을 위해 실행」 —
    작성자 문장(tail)은 조건이 참일 때만 붙인다(확인되지 않은 주장 단정 금지). 사유는 판독
    원소면 `reading_reasons`(판독 이름 → 판독 사유), 스텝 원소면 그 스텝 상태(결손·건너뜀)다.
    판독 원소(`cpu_owner` 등)의 근거 단계는 `reading_steps`(판독 이름 → 스텝 ID)로 찾는다.
    """
    try:
        if expr.evaluate(context) is Tri.UNKNOWN:
            return _unknown_because(expr, context, titles, reading_reasons or {})
        atoms = [a for a in _atoms(expr._root) if _effective(a, context) is Tri.TRUE]
        # 같은 단계의 판정 원소가 참이면 「결과 있음」 원소는 중복이라 뺀다.
        judged = {a.path[0] for a in atoms if a.path[1:] != ("status",)}
        conds: list[str] = []
        refs: list[str] = []
        for atom in atoms:
            if atom.path[1:] == ("status",) and atom.path[0] in judged and not atom.negated:
                continue
            text = atom_sentence(atom, titles)
            if atom.op == "in" and not atom.negated:
                # 목록 조건은 실제 값으로 적는다(「CPU 주인 = WAS 처리(부하 증가)」).
                text = _state_sentence(atom, context, titles) or text
            if atom.negated:
                text = f"「{text}」 아님"
            if text not in conds:
                conds.append(text)
            root = atom.path[0]
            steps = [root] if _is_step(root) else list((reading_steps or {}).get(root) or ())
            refs.extend(s for s in steps if s not in refs)
        if not conds:
            return tail
        quotes = [
            f"{titles.get(s, s)}: {_clip(findings[s], _QUOTE_CHARS)}"
            for s in refs
            if findings.get(s)
        ][:2]
        head = " · ".join(conds)
        if quotes:
            head += f"({' / '.join(quotes)})"
        return f"{head} → {tail}"
    except Exception:  # noqa: BLE001 — 문장화 실패는 tail만
        return tail


def _unknown_reason(atom: _Atom, context: Mapping[str, object], titles: Mapping[str, str],
                    reading_reasons: Mapping[str, str]) -> str | None:
    """판정 불가 원소의 사유 — 판독 사유 · 스텝 결손/건너뜀(의도된 생략과 결손을 구분)."""
    root = atom.path[0]
    if not _is_step(root):
        reason = reading_reasons.get(root)
        return _clip(reason, _REASON_CHARS) if reason else None
    title = titles.get(root, root)
    entry = context.get(root)
    status = entry.get("status") if isinstance(entry, Mapping) else None
    if status == "skipped" or entry is None:
        return f"{josa(title, '을', '를')} 건너뜀"
    if status == "missing":
        return f"{title} 결과 결손"
    return None


def _unknown_because(expr: Expr, context: Mapping[str, object], titles: Mapping[str, str],
                     reading_reasons: Mapping[str, str]) -> str:
    conds: list[str] = []
    reasons: list[str] = []
    for atom in _atoms(expr._root):
        if _effective(atom, context) is not Tri.UNKNOWN:
            continue
        text = atom_sentence(atom, titles)
        if atom.negated:
            text = f"{text} 아님"
        if text not in conds:
            conds.append(text)
        reason = _unknown_reason(atom, context, titles, reading_reasons)
        if reason and reason not in reasons:
            reasons.append(reason)
    if not conds:
        return WHEN_UNKNOWN
    head = f"「{' · '.join(conds[:2])}」인지 판정할 수 없어"
    if reasons:
        head += f"(사유: {' · '.join(reasons[:2])})"
    return f"{head} 확인을 위해 실행"


__all__ = [
    "BUDGET_SKIP",
    "ENUM_LABELS",
    "PRECISE_ACTIONS",
    "READING_LABELS",
    "REALTIME_DRIFT_MINUTES",
    "REALTIME_DRIFT_NOTE",
    "TOOL_PHRASES",
    "WHEN_SKIP_FALLBACK",
    "WHEN_UNKNOWN",
    "atom_sentence",
    "default_because",
    "fmt_number",
    "josa",
    "skip_because",
    "summarize",
    "tool_phrase",
    "when_because",
]
