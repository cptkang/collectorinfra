"""조사 결과 영역별 판정 — 브리핑 선택 키 `duty_view` (plans/201 W4 §6.1 · D-354).

담당자가 조사 결과에서 먼저 알고 싶은 것은 「원인이 내 영역인가, 넘겨야 하나」다. 이 모듈은
플레이북이 **이미 만든** 판정을 담당 영역(서버/OS · WAS/미들웨어 · DB)으로 옮겨 쓴다 — 새 판정 규칙·
LLM 호출이 없다. 읽는 것은 브리핑 `playbook` 블록(`_playbook_block` 산출)뿐이다:

- 가설 순위(`synthesis.hypotheses` — 규칙표 `HYPOTHESIS_RULES`의 상태 지지·판단 보류·반박)
- 판독 값(`readings` — R-C~R-G enum)과 그 근거(`readings_detail.evidence` — 근거 단계·수치·사유)
- 단계 결과(`sections` — 상태·도구)와 조사 범위(`scope.polestar_source`)

순서(§6.1): ① 커넥션 계열 가설을 계층표보다 먼저 본다 — 1순위 지지면 그 영역 하나, 판단 보류면
was_mw·db 둘 다 의심 + 사유. ② 그 밖 가설은 지지일 때만 그 영역을 「이상」으로. ③ 판독 계층
(`engine_agreement.READING_LAYERS`)이 가리키는 영역은 「의심」(커넥션 계열이 있으면 db 계층은 건너뛴다).
④ 남은 영역은 판독이 돌았고 그 값이 영역을 가리키지 않으며 **도구 인용이 있을 때만** 「정상」, 그 밖은
「확인 안 됨」 + 사유(근거 없는 정상 0건 — 원칙 7).

근거 문자열은 가설 제목·판독 이름·enum·단계 ID·도구 이름·고정 사유로만 만든다 — 서버명·IP·SQL 원문을
넣지 않는다(단계 사유 원문·표 셀·소견을 옮기지 않는다).

순수 도메인 — 표준 라이브러리와 같은 domain 패키지만 쓴다. 매핑은 `sre_agent/config/duty_area_layers.yaml`
(로더 `infrastructure.duty_area_loader`).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sre_agent.domain.engine_agreement import LAYER_DB, LAYER_EXTERNAL, READING_LAYERS

ABNORMAL = "abnormal"
SUSPECT = "suspect"
NORMAL = "normal"
UNVERIFIED = "unverified"
STATUSES: tuple[str, ...] = (ABNORMAL, SUSPECT, NORMAL, UNVERIFIED)
_RANK = {ABNORMAL: 3, SUSPECT: 2, NORMAL: 1, UNVERIFIED: 0}

#: 가설 상태(playbook_hypotheses) — 문자열만 읽는다.
_SUPPORTED = "supported"
_INCONCLUSIVE = "inconclusive"
_REFUTED = "refuted"
_NA = "n/a"
#: R-E `other_process`(WAS 밖 프로세스) · 그 상위 프로세스 근거 경로.
_CPU_OWNER = "cpu_owner"
_OTHER_PROCESS = "other_process"
#: 가설 → 영역 표의 「R-E 상위 프로세스 종류로 정함」 표지.
PROCESS_AREA = "process"
#: 영역 행에 싣는 근거 줄 상한.
SIGNALS_MAX = 3
#: 사유 문자열 상한(판독 실패 예외 문구 등이 길게 새지 않게).
_REASON_MAX = 80
_SKIP_WORDS = {"missing": "수집 결손", "skipped": "생략"}


@dataclass(frozen=True)
class DutyArea:
    """담당 영역 1개(선언 순서 = 표 순서)."""

    code: str
    label: str
    no_step_reason: str


@dataclass(frozen=True)
class DutyAreaMap:
    """`duty_area_layers.yaml` 파싱 결과."""

    areas: tuple[DutyArea, ...]
    status_labels: Mapping[str, str]
    layers: Mapping[str, str]
    reading_areas: Mapping[str, tuple[str, ...]]
    reading_labels: Mapping[str, str]
    connection_hypotheses: Mapping[str, str]
    connection_inconclusive_areas: tuple[str, ...]
    connection_inconclusive_reason: str
    connection_inconclusive_general_reason: str
    connection_handled_reason: str
    cpu_owner_anomalous_area: str
    cpu_owner_anomalous_reason: str
    hypothesis_areas: Mapping[str, str]
    external_label: str
    external_tag: str
    external_hypotheses: frozenset[str]
    process_default_area: str
    process_unidentified_note: str
    process_missing_note: str
    process_kind_areas: Mapping[str, str]
    process_signatures: tuple[tuple[str, re.Pattern[str]], ...]
    polestar_unlinked_area: str
    polestar_unlinked_reason: str
    uncited_reason: str
    handoff: Mapping[str, str]
    signal_words: Mapping[str, str] = field(default_factory=dict)

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(a.code for a in self.areas)

    def label(self, code: str) -> str:
        return next((a.label for a in self.areas if a.code == code), code)


# ── 파싱(검증) ───────────────────────────────────────────────────────────────


def _req_str(data: Mapping[str, Any], key: str, where: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where}.{key}: 빈 문자열이 아니어야 한다")
    return value


def _req_map(data: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = data.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"{key}: 매핑이어야 한다")
    return value


def _str_map(data: Mapping[str, Any], key: str) -> dict[str, str]:
    raw = _req_map(data, key)
    out = {str(k): v for k, v in raw.items()}
    if not all(isinstance(v, str) and v for v in out.values()):
        raise ValueError(f"{key}: 값은 빈 문자열이 아니어야 한다")
    return out


def _codes_ok(codes: Sequence[str], known: set[str], where: str, *extra: str) -> None:
    bad = [c for c in codes if c not in known and c not in extra]
    if bad:
        raise ValueError(f"{where}: 어휘 밖 영역 코드 {bad}")


def parse_duty_area_map(data: Mapping[str, Any]) -> DutyAreaMap:
    """YAML 매핑을 검증해 `DutyAreaMap`으로 만든다(형식 오류·어휘 밖 영역 코드는 `ValueError`)."""
    raw_areas = data.get("areas")
    if not isinstance(raw_areas, list) or not raw_areas:
        raise ValueError("areas: 비지 않은 목록이어야 한다")
    areas = tuple(
        DutyArea(_req_str(a, "code", "areas"), _req_str(a, "label", "areas"),
                 _req_str(a, "no_step_reason", "areas"))
        for a in raw_areas
        if isinstance(a, Mapping)
    )
    if len(areas) != len(raw_areas) or len({a.code for a in areas}) != len(areas):
        raise ValueError("areas: 항목은 매핑이고 code가 겹치지 않아야 한다")
    known = {a.code for a in areas}
    status_labels = _str_map(data, "status_labels")
    if set(status_labels) != set(STATUSES):
        raise ValueError(f"status_labels: 키는 {STATUSES}여야 한다")
    layers = _str_map(data, "layers")
    _codes_ok(list(layers.values()), known, "layers")
    reading_raw = _req_map(data, "reading_areas")
    reading_areas: dict[str, tuple[str, ...]] = {}
    for name, codes in reading_raw.items():
        if not isinstance(codes, list):
            raise ValueError(f"reading_areas.{name}: 목록이어야 한다")
        _codes_ok([str(c) for c in codes], known, f"reading_areas.{name}")
        reading_areas[str(name)] = tuple(str(c) for c in codes)
    conn = _str_map(data, "connection_hypotheses")
    _codes_ok(list(conn.values()), known, "connection_hypotheses")
    inc = _req_map(data, "connection_inconclusive")
    inc_areas = [str(c) for c in inc.get("areas") or []]
    _codes_ok(inc_areas, known, "connection_inconclusive.areas")
    hyp = _str_map(data, "hypothesis_areas")
    _codes_ok(list(hyp.values()), known, "hypothesis_areas", PROCESS_AREA)
    ext = _req_map(data, "external")
    proc = _req_map(data, "process")
    kind_areas: dict[str, str] = {}
    for code, kinds in _req_map(proc, "kind_areas").items():
        _codes_ok([str(code)], known, "process.kind_areas")
        for kind in kinds or []:
            kind_areas[str(kind)] = str(code)
    sigs: list[tuple[str, re.Pattern[str]]] = []
    for item in data.get("process_signatures") or []:
        if not isinstance(item, Mapping):
            raise ValueError("process_signatures: 항목은 매핑이어야 한다")
        kind = _req_str(item, "kind", "process_signatures")
        try:
            sigs.append((kind, re.compile(_req_str(item, "match_regex", "process_signatures"))))
        except re.error as exc:
            raise ValueError(f"process_signatures.{kind}: 정규식 오류 {exc}") from exc
    unlinked = _req_map(data, "polestar_unlinked")
    handoff = _req_map(data, "handoff")
    default_area = _req_str(proc, "default_area", "process")
    unlinked_area = _req_str(unlinked, "area", "polestar_unlinked")
    anomalous = _req_map(data, "cpu_owner_anomalous")
    anomalous_area = _req_str(anomalous, "area", "cpu_owner_anomalous")
    _codes_ok([default_area, unlinked_area, anomalous_area], known,
              "process·polestar_unlinked·cpu_owner_anomalous")
    return DutyAreaMap(
        areas=areas,
        status_labels=status_labels,
        layers=layers,
        reading_areas=reading_areas,
        reading_labels=_str_map(data, "reading_labels"),
        connection_hypotheses=conn,
        connection_inconclusive_areas=tuple(inc_areas),
        connection_inconclusive_reason=_req_str(inc, "reason", "connection_inconclusive"),
        connection_inconclusive_general_reason=_req_str(
            inc, "general_reason", "connection_inconclusive"
        ),
        connection_handled_reason=_req_str(data, "connection_handled_reason", "(최상위)"),
        cpu_owner_anomalous_area=anomalous_area,
        cpu_owner_anomalous_reason=_req_str(anomalous, "reason", "cpu_owner_anomalous"),
        hypothesis_areas=hyp,
        external_label=_req_str(ext, "label", "external"),
        external_tag=_req_str(ext, "tag", "external"),
        external_hypotheses=frozenset(str(h) for h in ext.get("hypotheses") or []),
        process_default_area=default_area,
        process_unidentified_note=_req_str(proc, "unidentified_note", "process"),
        process_missing_note=_req_str(proc, "missing_note", "process"),
        process_kind_areas=kind_areas,
        process_signatures=tuple(sigs),
        polestar_unlinked_area=unlinked_area,
        polestar_unlinked_reason=_req_str(unlinked, "reason", "polestar_unlinked"),
        uncited_reason=_req_str(data, "uncited_reason", "(최상위)"),
        handoff={k: str(v) for k, v in handoff.items() if isinstance(v, str)},
        signal_words={str(k): str(v) for k, v in (handoff.get("signal_words") or {}).items()},
    )


# ── 판정 ─────────────────────────────────────────────────────────────────────


@dataclass
class _Acc:
    """영역 1개의 누적 — 가장 강한 상태 · 근거 줄 · 근거 단계 · 사유 · 꼬리표."""

    status: str = UNVERIFIED
    signals: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    reason: str | None = None
    tag: str | None = None

    def raise_to(self, status: str, signal: str, steps: Sequence[str]) -> None:
        if _RANK[status] > _RANK[self.status]:
            self.status = status
        if signal and signal not in self.signals:
            self.signals.append(signal)
        for s in steps:
            if s not in self.steps:
                self.steps.append(s)


def _as_map(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _sections(block: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """수집 단계(종합 S9 제외) — 스텝 ID → 섹션."""
    out: dict[str, Mapping[str, Any]] = {}
    for sec in _as_list(block.get("sections")):
        if not isinstance(sec, Mapping) or sec.get("action") == "synthesize":
            continue
        if sec.get("status") == "pending_synthesis" or not sec.get("step"):
            continue
        out[str(sec["step"])] = sec
    return out


def _tools(steps: Sequence[str], sections: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """근거 단계 중 성공(`ok`)한 단계의 도구 이름(등장 순 · 중복 제거) — 인용의 기준."""
    out: list[str] = []
    for sid in steps:
        sec = sections.get(sid)
        if sec is None or sec.get("status") != "ok":
            continue
        for tool in _as_list(sec.get("tools")):
            name = str(tool or "")
            if name and name not in out:
                out.append(name)
    return out


def _cite(text: str, steps: Sequence[str], tools: Sequence[str]) -> str:
    """근거 1줄 — `본문 (S3·S2 ← apm_a, apm_b)`. 도구가 없으면 단계만."""
    where = "·".join(steps)
    if tools:
        cited = ", ".join(tools)
        return f"{text} ({where} ← {cited})" if where else f"{text} (← {cited})"
    return f"{text} ({where})" if where else text


def _evidence(block: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    detail = _as_map(block.get("readings_detail"))
    return _as_map(_as_map(detail.get("evidence")).get(name))


def _ev_steps(ev: Mapping[str, Any]) -> list[str]:
    return [str(s) for s in _as_list(ev.get("steps")) if s]


def _short(reason: str) -> str:
    """사유를 짧게 — 판독 실패 예외 문구(`판독 실패 — Type: msg`)는 앞머리만 남긴다."""
    text = reason.split(":", 1)[0].strip()
    return text if len(text) <= _REASON_MAX else text[:_REASON_MAX] + "…"


def process_kind(name: object, mapping: DutyAreaMap) -> str | None:
    """프로세스 이름 → 미들웨어 kind(DB 엔진 시그니처 사본만 · 못 잡으면 None)."""
    text = str(name or "").strip()
    if not text:
        return None
    return next((kind for kind, rx in mapping.process_signatures if rx.search(text)), None)


def _process_area(block: Mapping[str, Any], mapping: DutyAreaMap) -> tuple[str, str]:
    """R-E 상위 프로세스 → (영역, 근거 꼬리말). 증거에는 프로세스 이름만 있다(`top_process.name`)."""
    values = _as_map(_evidence(block, _CPU_OWNER).get("values"))
    top = _as_map(values.get("top_process"))
    if not top.get("name"):
        return mapping.process_default_area, mapping.process_missing_note
    kind = process_kind(top.get("name"), mapping)
    if kind is None:
        return mapping.process_default_area, mapping.process_unidentified_note
    area = mapping.process_kind_areas.get(kind, mapping.process_default_area)
    return area, f"상위 프로세스 종류 {kind}"


def _hypotheses(block: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    syn = _as_map(block.get("synthesis"))
    hyps = [h for h in _as_list(syn.get("hypotheses")) if isinstance(h, Mapping)]
    return sorted(hyps, key=lambda h: h.get("rank") if isinstance(h.get("rank"), int) else 1_000)


def _apply_hypotheses(
    block: Mapping[str, Any],
    mapping: DutyAreaMap,
    sections: Mapping[str, Mapping[str, Any]],
    acc: dict[str, _Acc],
) -> bool:
    """① 커넥션 계열 · ② 그 밖 지지 가설을 영역에 옮긴다. 커넥션 계열이 판정에 쓰였으면 True."""
    hyps = _hypotheses(block)
    conn = [h for h in hyps if h.get("rule_id") in mapping.connection_hypotheses
            and h.get("status") != _REFUTED]
    if conn:
        top = conn[0]
        steps = [str(s) for s in _as_list(top.get("evidence_steps"))]
        tools = _tools(steps, sections)
        line = _cite(f"{top.get('title')} — {top.get('status_label') or top.get('status')}",
                     steps, tools)
        if top.get("status") == _SUPPORTED:
            area = mapping.connection_hypotheses[str(top.get("rule_id"))]
            acc[area].raise_to(ABNORMAL if tools else SUSPECT, line, steps)
        else:
            # 「획득·실행 구분 정보 없음」은 db_split 판독이 n/a·미결정일 때만 — 그 밖은 일반 보류 사유
            split = _as_map(block.get("readings")).get("db_split")
            reason = (mapping.connection_inconclusive_reason
                      if not isinstance(split, str) or split == _NA
                      else mapping.connection_inconclusive_general_reason)
            for area in mapping.connection_inconclusive_areas:
                acc[area].raise_to(SUSPECT, line, steps)
                acc[area].reason = reason
    for h in hyps:
        rid = str(h.get("rule_id") or "")
        if h.get("status") != _SUPPORTED or rid not in mapping.hypothesis_areas:
            continue
        steps = [str(s) for s in _as_list(h.get("evidence_steps"))]
        tools = _tools(steps, sections)
        text = f"{h.get('title')} — {h.get('status_label') or h.get('status')}"
        area = mapping.hypothesis_areas[rid]
        if area == PROCESS_AREA:
            area, note = _process_area(block, mapping)
            text = f"{text} · {note}"
        if rid in mapping.external_hypotheses:
            text = f"{mapping.external_label} · {text}"
            acc[area].tag = mapping.external_tag
        acc[area].raise_to(ABNORMAL if tools else SUSPECT, _cite(text, steps, tools), steps)
    return bool(conn)


def _reading_area(
    block: Mapping[str, Any], mapping: DutyAreaMap, name: str, value: str, layer: str
) -> tuple[str, str]:
    """판독 계층 → (영역, 근거 본문)."""
    label = mapping.reading_labels.get(name, name)
    text = f"{label} {value}"
    if name == _CPU_OWNER and value == _OTHER_PROCESS:
        area, note = _process_area(block, mapping)
        return area, f"{text} · {note}"
    return mapping.layers.get(layer, ""), text


def _apply_layers(
    block: Mapping[str, Any],
    mapping: DutyAreaMap,
    sections: Mapping[str, Mapping[str, Any]],
    acc: dict[str, _Acc],
    skip_db: bool,
) -> tuple[dict[str, str], set[str]]:
    """③ 판독 계층이 가리키는 영역을 「의심」으로.

    (판독 이름 → 가리킨 영역, 커넥션 가설에 넘겨 옮기지 않은 판독 이름)을 돌려준다(정상·사유 판정용).
    """
    readings = _as_map(block.get("readings"))
    pointed: dict[str, str] = {}
    handed: set[str] = set()
    for name, table in READING_LAYERS:
        value = readings.get(name)
        if not isinstance(value, str) or value not in table:
            continue
        layer = table[value]
        area, text = _reading_area(block, mapping, name, value, layer)
        if not area:
            continue
        pointed[name] = area
        if skip_db and layer == LAYER_DB:
            handed.add(name)
            continue
        steps = _ev_steps(_evidence(block, name))
        tools = _tools(steps, sections)
        if layer == LAYER_EXTERNAL:
            text = f"{mapping.external_label} · {text}"
            acc[area].tag = mapping.external_tag
        acc[area].raise_to(SUSPECT, _cite(text, steps, tools), steps)
    return pointed, handed


def _step_failures(steps: Sequence[str], sections: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """근거 단계 중 결손·생략 — `S3 느린 트랜잭션 분해: 수집 결손`(단계 제목은 고정 어휘)."""
    out: list[str] = []
    for sid in steps:
        sec = sections.get(sid)
        word = _SKIP_WORDS.get(str((sec or {}).get("status")))
        if sec is not None and word:
            title = sec.get("title") or sec.get("action") or ""
            out.append(f"{sid} {title}: {word}".replace(" :", ":"))
    return out


def _settle(
    block: Mapping[str, Any],
    mapping: DutyAreaMap,
    sections: Mapping[str, Mapping[str, Any]],
    acc: dict[str, _Acc],
    pointed: Mapping[str, str],
    handed: set[str],
) -> None:
    """④ 이상·의심이 아닌 영역 — 인용 있는 정상 근거가 있으면 정상, 없으면 확인 안 됨 + 사유.

    호스트 CPU 이상(R-E 증거 `host_cpu_anomalous`)이면 cpu_owner는 어느 영역의 정상 근거도 아니고
    서버/OS는 정상이 될 수 없다. 커넥션 가설에 넘긴 판독은 그 영역의 정상 근거로 쓰지 않는다.
    """
    readings = _as_map(block.get("readings"))
    polestar_linked = bool(_as_map(block.get("scope")).get("polestar_source"))
    cpu_values = _as_map(_evidence(block, _CPU_OWNER).get("values"))
    host_cpu_anomalous = cpu_values.get("host_cpu_anomalous") is True
    for area in mapping.areas:
        a = acc[area.code]
        if a.status in (ABNORMAL, SUSPECT):
            continue
        reasons: list[str] = []
        ran = False
        for name, codes in mapping.reading_areas.items():
            if area.code not in codes:
                continue
            ev = _evidence(block, name)
            steps = _ev_steps(ev)
            if not steps:
                continue  # 이 조사에 해당 판독(근거 단계)이 없다
            ran = True
            value = readings.get(name)
            determined = isinstance(value, str) and value != _NA
            tools = _tools(steps, sections)
            if name in handed and pointed.get(name) == area.code:
                reasons.append(mapping.connection_handled_reason)
                continue
            if name == _CPU_OWNER and host_cpu_anomalous:
                reasons.append(mapping.cpu_owner_anomalous_reason)
                continue
            if determined and pointed.get(name) != area.code and tools:
                label = mapping.reading_labels.get(name, name)
                a.raise_to(NORMAL, _cite(f"{label} {value}", steps, tools), steps)
                continue
            failed = _step_failures(steps, sections)
            if failed:
                reasons.extend(failed)
            elif determined:
                reasons.append(mapping.uncited_reason)
            elif isinstance(ev.get("reason"), str) and ev["reason"]:
                reasons.append(_short(ev["reason"]))
        if (host_cpu_anomalous and area.code == mapping.cpu_owner_anomalous_area
                and a.status == NORMAL):
            a.status = UNVERIFIED  # 호스트 CPU 이상 — 서버/OS 정상 근거가 있어도 정상이 아니다
            reasons.append(mapping.cpu_owner_anomalous_reason)
        if a.status == NORMAL:
            continue
        if area.code == mapping.polestar_unlinked_area and not polestar_linked:
            a.reason = mapping.polestar_unlinked_reason
        elif reasons:
            a.reason = " · ".join(dict.fromkeys(reasons))
        elif not ran:
            a.reason = area.no_step_reason
        else:
            a.reason = mapping.uncited_reason


def _handoff(rows: Sequence[Mapping[str, Any]], mapping: DutyAreaMap) -> str:
    """인계 문구 — 이상·의심 영역 목록으로 템플릿을 고른다(조직·사람 이름 없음)."""
    flagged = [r for r in rows if r["status"] in (ABNORMAL, SUSPECT)]
    if not flagged:
        if all(r["status"] == NORMAL for r in rows):
            return mapping.handoff.get("none_all_checked", "")
        return mapping.handoff.get("none_with_unverified", "")
    names = "·".join(
        f"{r['label']}({r['tag']})" if r.get("tag") else str(r["label"]) for r in flagged
    )
    word_key = ABNORMAL if any(r["status"] == ABNORMAL for r in flagged) else SUSPECT
    template = mapping.handoff.get("single" if len(flagged) == 1 else "multi", "")
    return template.format(signal=mapping.signal_words.get(word_key, ""), areas=names,
                           count=len(flagged))


def build_duty_view(block: Mapping[str, Any] | None, mapping: DutyAreaMap) -> dict[str, Any] | None:
    """플레이북 블록 → `duty_view`(`{areas: [...], handoff}`). 수집 단계가 하나도 없으면 None(키 없음).

    영역 행: `code`·`label`·`status`(4값)·`status_label`·`signals`(근거 줄 ≤ 3 · 인용 표기)·`steps`
    (근거 단계 ID) · 선택 `reason`(확인 안 됨이면 필수 · 커넥션 판단 보류의 의심에도) · 선택 `tag`
    (외부 연계 꼬리표). 행 순서는 YAML 선언 순서다(담당별 재정렬은 화면이 한다).
    """
    if not isinstance(block, Mapping):
        return None
    sections = _sections(block)
    if not sections:
        return None
    acc = {code: _Acc() for code in mapping.codes}
    conn_used = _apply_hypotheses(block, mapping, sections, acc)
    pointed, handed = _apply_layers(block, mapping, sections, acc, skip_db=conn_used)
    _settle(block, mapping, sections, acc, pointed, handed)
    rows: list[dict[str, Any]] = []
    for area in mapping.areas:
        a = acc[area.code]
        row: dict[str, Any] = {
            "code": area.code,
            "label": area.label,
            "status": a.status,
            "status_label": mapping.status_labels[a.status],
            "signals": a.signals[:SIGNALS_MAX],
            "steps": list(a.steps),
        }
        if a.reason:
            row["reason"] = a.reason
        if a.tag and a.status in (ABNORMAL, SUSPECT):
            row["tag"] = a.tag
        rows.append(row)
    return {"areas": rows, "handoff": _handoff(rows, mapping)}


__all__ = [
    "ABNORMAL",
    "NORMAL",
    "PROCESS_AREA",
    "SIGNALS_MAX",
    "STATUSES",
    "SUSPECT",
    "UNVERIFIED",
    "DutyArea",
    "DutyAreaMap",
    "build_duty_view",
    "parse_duty_area_map",
    "process_kind",
]
