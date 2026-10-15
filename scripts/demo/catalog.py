"""시연 세션 카탈로그와 환경 값 파일 로더.

- 세션 카탈로그(`testdata/demo/sessions.yaml`): 군 → 세션 → 턴. 질의 문구의 환경 의존 값은
  `{{이름}}` 자리표로 쓴다.
- 환경 값 파일(`testdata/demo/env.sandbox.yaml` 형식): 이 환경에 연동된 소스 목록과 자리표 값.
  폐쇄망 값은 같은 형식의 파일을 **저장소 밖에** 두고 `--env-file` 로 넘긴다(반출 금지 ·
  apm_bench `--placeholders` 와 같은 규칙).

로더는 문제를 모아서 돌려준다. 자리표가 안 채워진 채 질의가 나가면 시연 화면에 `{{...}}` 가
그대로 찍히므로, 실행 전에 전부 드러낸다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from . import DEMO_DIR, REPO_ROOT

SESSIONS_PATH = DEMO_DIR / "sessions.yaml"
DEFAULT_ENV_PATH = DEMO_DIR / "env.sandbox.yaml"

#: 세션이 요구할 수 있는 소스 어휘. 환경 값 파일의 `sources` 도 이 안에서만 고른다.
SOURCES: dict[str, str] = {
    "polestar": "폴스타 DB(서버 구성·성능·알람·프로세스)",
    "multi_zone": "폴스타 다중 존(은행존·공동존 김포/여의도 - 폐쇄망)",
    "itam": "자산관리(ITAM)",
    "prometheus": "Prometheus 메트릭 시계열",
    "apm": "제니퍼 APM(WAS)",
    "docs": "사내 문서(RAG)",
    "sre_agent": "장애 조사(sre_agent · 별도 프로세스)",
}

#: 턴에 쓸 수 있는 키. 오타 키가 조용히 무시되면 답을 보낸 줄 알고 안 보낸 시연이 된다.
TURN_KEYS: frozenset[str] = frozenset({
    "query", "upload", "selected_db_ids", "selected_sources", "selected_apm_source_ids",
    "form_fill_answers", "form_fill_remember", "expect_clarify", "note",
})
UPLOAD_SUFFIXES: frozenset[str] = frozenset({".xlsx", ".docx", ".csv", ".xls"})

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")


@dataclass(frozen=True)
class Turn:
    """질의 1회. `query` 가 비면 실행기가 직전 역질문의 원 질의로 채운다(웹 UI 와 같다)."""

    query: str = ""
    upload: Optional[str] = None
    selected_db_ids: tuple[str, ...] = ()
    selected_sources: tuple[str, ...] = ()
    selected_apm_source_ids: tuple[str, ...] = ()
    form_fill_answers: Optional[dict[str, Any]] = None
    form_fill_remember: bool = False
    expect_clarify: bool = False
    note: str = ""

    @property
    def upload_path(self) -> Optional[Path]:
        return (REPO_ROOT / self.upload) if self.upload else None


@dataclass(frozen=True)
class Session:
    id: str
    group_id: str
    group_name: str
    title: str
    shows: str
    core: bool
    requires: tuple[str, ...]
    turns: tuple[Turn, ...]


@dataclass(frozen=True)
class Environment:
    name: str
    sources: tuple[str, ...]
    values: dict[str, str]
    zone_preference: tuple[str, ...] = ()
    apm_source_preference: tuple[str, ...] = ()
    path: Optional[Path] = None


@dataclass
class Catalog:
    sessions: list[Session] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


class CatalogError(ValueError):
    """카탈로그·환경 파일을 읽을 수 없다(형식 오류)."""


def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(v) for v in value)


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise CatalogError(f"파일이 없습니다: {path}")
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise CatalogError(f"최상위가 매핑이 아닙니다: {path}")
    return data


def _parse_turn(raw: Any, where: str, problems: list[str]) -> Turn:
    if isinstance(raw, str):
        return Turn(query=raw)
    if not isinstance(raw, dict):
        problems.append(f"{where}: 턴은 문자열 또는 매핑이어야 합니다")
        return Turn()
    unknown = sorted(set(raw) - TURN_KEYS)
    if unknown:
        problems.append(f"{where}: 알 수 없는 턴 키 {unknown}")
    answers = raw.get("form_fill_answers")
    if answers is not None and not isinstance(answers, dict):
        problems.append(f"{where}: form_fill_answers 는 매핑이어야 합니다")
        answers = None
    turn = Turn(
        query=str(raw.get("query") or ""),
        upload=str(raw["upload"]) if raw.get("upload") else None,
        selected_db_ids=_as_tuple(raw.get("selected_db_ids")),
        selected_sources=_as_tuple(raw.get("selected_sources")),
        selected_apm_source_ids=_as_tuple(raw.get("selected_apm_source_ids")),
        form_fill_answers=answers,
        form_fill_remember=bool(raw.get("form_fill_remember", False)),
        expect_clarify=bool(raw.get("expect_clarify", False)),
        note=str(raw.get("note") or ""),
    )
    answers_clarify = (turn.selected_db_ids or turn.selected_sources
                       or turn.selected_apm_source_ids or turn.form_fill_answers)
    if not turn.query and not answers_clarify:
        problems.append(f"{where}: query 가 없고 역질문 답(selected_*·form_fill_answers)도 없습니다")
    return turn


def load_catalog(path: Path = SESSIONS_PATH) -> Catalog:
    data = _load_yaml(path)
    catalog = Catalog()
    seen: set[str] = set()
    for group in data.get("groups") or []:
        gid, gname = str(group.get("id") or ""), str(group.get("name") or "")
        if not gid or not gname:
            catalog.problems.append(f"군 id·name 이 비었습니다: {group!r:.80}")
            continue
        for raw in group.get("sessions") or []:
            sid = str(raw.get("id") or "")
            where = f"{sid or '(id 없음)'}"
            if not sid:
                catalog.problems.append(f"{gid}: 세션 id 가 비었습니다")
                continue
            if sid in seen:
                catalog.problems.append(f"{sid}: 세션 id 중복")
            seen.add(sid)
            requires = _as_tuple(raw.get("requires"))
            for src in requires:
                if src not in SOURCES:
                    catalog.problems.append(f"{where}: 모르는 소스 '{src}' (허용: {sorted(SOURCES)})")
            turns = tuple(_parse_turn(t, f"{where} 턴{i}", catalog.problems)
                          for i, t in enumerate(raw.get("turns") or [], start=1))
            if not turns:
                catalog.problems.append(f"{where}: 턴이 없습니다")
            catalog.sessions.append(Session(
                id=sid, group_id=gid, group_name=gname,
                title=str(raw.get("title") or sid), shows=str(raw.get("shows") or ""),
                core=bool(raw.get("core", False)), requires=requires, turns=turns,
            ))
    if not catalog.sessions:
        catalog.problems.append(
            f"{path}: 세션이 하나도 없습니다 - 최상위 `groups:` 와 반입 파일이 잘리지 않았는지 확인하세요")
    return catalog


def load_environment(path: Path = DEFAULT_ENV_PATH) -> Environment:
    data = _load_yaml(path)
    sources = _as_tuple(data.get("sources"))
    unknown = [s for s in sources if s not in SOURCES]
    if unknown:
        raise CatalogError(f"{path}: 모르는 소스 {unknown} (허용: {sorted(SOURCES)})")
    values = data.get("values") or {}
    if not isinstance(values, dict):
        raise CatalogError(f"{path}: values 는 매핑이어야 합니다")
    return Environment(
        name=str(data.get("environment") or path.stem),
        sources=sources,
        values={str(k): "" if v is None else str(v) for k, v in values.items()},
        zone_preference=_as_tuple(data.get("zone_preference")),
        apm_source_preference=_as_tuple(data.get("apm_source_preference")),
        path=path,
    )


def placeholders_in(value: Any) -> set[str]:
    """값 안의 자리표 이름 전부(문자열·리스트·매핑 재귀)."""
    if isinstance(value, str):
        return set(_PLACEHOLDER_RE.findall(value))
    if isinstance(value, (list, tuple)):
        return set().union(*(placeholders_in(v) for v in value)) if value else set()
    if isinstance(value, dict):
        found: set[str] = set()
        for k, v in value.items():
            found |= placeholders_in(k) | placeholders_in(v)
        return found
    return set()


def _fill(value: Any, values: dict[str, str]) -> Any:
    if isinstance(value, str):
        return _PLACEHOLDER_RE.sub(lambda m: values.get(m.group(1), m.group(0)), value)
    if isinstance(value, tuple):
        return tuple(_fill(v, values) for v in value)
    if isinstance(value, list):
        return [_fill(v, values) for v in value]
    if isinstance(value, dict):
        return {_fill(k, values): _fill(v, values) for k, v in value.items()}
    return value


def render_session(session: Session, env: Environment) -> tuple[Session, list[str]]:
    """자리표를 채운 세션과 문제 목록(빈 값·미정의 자리표·없는 업로드 파일)."""
    problems: list[str] = []
    turns: list[Turn] = []
    for i, turn in enumerate(session.turns, start=1):
        names = placeholders_in([turn.query, turn.upload, turn.selected_db_ids,
                                 turn.selected_sources, turn.selected_apm_source_ids,
                                 turn.form_fill_answers])
        missing = sorted(n for n in names if not env.values.get(n, "").strip())
        if missing:
            problems.append(f"{session.id} 턴{i}: 값이 비었거나 없는 자리표 {missing}")
        filled = Turn(
            query=_fill(turn.query, env.values),
            upload=_fill(turn.upload, env.values) if turn.upload else None,
            selected_db_ids=_fill(turn.selected_db_ids, env.values),
            selected_sources=_fill(turn.selected_sources, env.values),
            selected_apm_source_ids=_fill(turn.selected_apm_source_ids, env.values),
            form_fill_answers=_fill(turn.form_fill_answers, env.values),
            form_fill_remember=turn.form_fill_remember,
            expect_clarify=turn.expect_clarify,
            note=turn.note,
        )
        if filled.upload:
            path = filled.upload_path
            if path is None or not path.exists():
                problems.append(f"{session.id} 턴{i}: 업로드 파일이 없습니다 - {filled.upload}")
            elif path.suffix.lower() not in UPLOAD_SUFFIXES:
                problems.append(f"{session.id} 턴{i}: 업로드 확장자 {path.suffix} 는 시연 대상이 아닙니다")
        turns.append(filled)
    rendered = Session(
        id=session.id, group_id=session.group_id, group_name=session.group_name,
        title=session.title, shows=session.shows, core=session.core,
        requires=session.requires, turns=tuple(turns),
    )
    return rendered, problems


def select_sessions(
    catalog: Catalog,
    env: Environment,
    *,
    only: Optional[list[str]] = None,
    groups: Optional[list[str]] = None,
    core_only: bool = False,
) -> tuple[list[Session], list[tuple[Session, str]]]:
    """(실행할 세션, [(건너뛴 세션, 사유)]). 소스 미연동은 사유와 함께 건너뛴다."""
    wanted_ids = {s.strip() for s in (only or []) if s.strip()}
    wanted_groups = {g.strip().upper() for g in (groups or []) if g.strip()}
    chosen: list[Session] = []
    skipped: list[tuple[Session, str]] = []
    for session in catalog.sessions:
        if wanted_ids and session.id not in wanted_ids:
            continue
        if wanted_groups and session.group_id.upper() not in wanted_groups:
            continue
        if core_only and not wanted_ids and not session.core:
            continue
        missing = [s for s in session.requires if s not in env.sources]
        if missing:
            labels = ", ".join(f"{s}({SOURCES[s]})" for s in missing if s in SOURCES)
            skipped.append((session, f"이 환경에 연동되지 않은 소스: {labels}"))
            continue
        chosen.append(session)
    unknown = wanted_ids - {s.id for s in catalog.sessions}
    for sid in sorted(unknown):
        skipped.append((Session(sid, "?", "?", sid, "", False, (), ()), "카탈로그에 없는 세션 id"))
    for gid in sorted(wanted_groups - {s.group_id.upper() for s in catalog.sessions}):
        skipped.append((Session(gid, gid, "?", gid, "", False, (), ()), "카탈로그에 없는 군 id"))
    return chosen, skipped
