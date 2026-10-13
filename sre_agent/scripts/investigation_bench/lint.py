"""세계 YAML 린트 (plans/154 §4.6 · §3 원칙 1·5 · §6.7 ⑤ · D-324 ①④).

세계 24건이 벤치의 정답이므로 세계 자체의 결함(라벨 누락 · 정답 누수 · 근거 없는 핵심 증거 ·
스냅샷 밖 도구 · 홀드아웃 유출)을 실행 전에 막는다. 오류 1건이라도 있으면 CLI `--lint`가 exit 1이다.

규칙(오류):
- `layout` — 파일 이름(stem) = `id` · 상위 디렉터리 이름 = `split`(`dev`·`holdout`) · id 중복 금지 ·
  YAML 읽기 실패.
- `label` — `label.root_entity.host` · `layer`(`LAYERS`) · `kind` · `change_related`(bool) ·
  `aliases.kind` 2개 이상 · `expect.abstain`(bool) · `script.model` 존재.
- `leak` — 조사에 주입되는 `event`(전 필드)·`meta`에 정답 kind 표면형(라벨 kind + 별칭 — 채점기
  `judge.kind_forms`와 같은 정규화)이 들어 있으면 실패. `title`·`script`는 조사에 주입되지 않아
  대상이 아니다(§4.6 — 대본은 가짜 LLM 전용).
- `evidence` — `key_evidence` 항목마다 `cite`가 비어 있지 않고, 그 도구의 응답 규칙 중 `match`와
  모순되지 않는 첫 규칙(=그 인자로 부르면 닿는 규칙)이 있으며, 그 응답이 오류 주입이 아니고
  `cite` 토큰을 전부 담는다(정규화 비교 · `gen` 지시는 펼치기 전 원문 기준).
- `hosts` — `hosts`가 알람 호스트(`event.hostname`·`serverName`)와 정답 호스트를 포함한다.
- `schema` — `schema.check_world`(도구·인자가 스냅샷에 있는가 — 대본 포함).
- `phrase_dup` — 홀드아웃과 개발셋이 같은 문구를 쓰면 실패(`phrase_units`·`duplicate_phrases`).
- `holdout_token` — 홀드아웃 고유 토큰(`holdout_tokens`)이 `sre_agent/sre_agent/` 소스(`*.py`)에
  있으면 실패(§3 원칙 5 — 벤치 과적합 방지).

규칙(경고 — exit 코드에 영향 없음):
- `pii` — 세계 문자열에 문서용 대역(`DOC_NETWORKS`) 밖 IPv4 · 주민번호 형태 · 휴대전화 · 이메일 ·
  카드번호 형태가 있으면 경고(FabriX PII 필터 대응 · §6.7 ⑤).

경계(D-139): `sre_agent` 패키지를 import하지 않는다 — 소스는 파일 텍스트로만 읽는다.
"""

from __future__ import annotations

import ipaddress
import json
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from investigation_bench import schema
from investigation_bench.judge import kind_forms, norm
from investigation_bench.worlds import World, load_world

TOP = Path(__file__).resolve().parents[2]  # sre_agent/
DEFAULT_WORLDS_ROOT = TOP / "testdata" / "investigation_bench" / "worlds"
DEFAULT_SOURCE_ROOT = TOP / "sre_agent"

SPLITS: tuple[str, ...] = ("dev", "holdout")
LAYERS = frozenset({"os", "app", "db", "network", "change", "none"})
MIN_KIND_ALIASES = 2

# 문구 중복 기준(`phrase_units`): 공백으로 나눈 토큰이 2개 이상이고 공백 제외 6자 이상인 문자열
# 값만 「문구」로 본다 — 한 토큰짜리 값(`/var/log`·`postgresql`·`DOWN`·시각)은 식별자·열거값이라
# 세계마다 겹쳐도 문구 재사용이 아니다. 같은 문구 전체가 양쪽에 있거나 연속 `SHINGLE_TOKENS`개
# 토큰이 겹치면 중복.
PHRASE_MIN_TOKENS = 2
PHRASE_MIN_CHARS = 6
SHINGLE_TOKENS = 4
# 홀드아웃 고유 토큰 최소 길이 — 짧은 구성요소(`/`)가 소스의 우연한 문자열에 걸리지 않게.
TOKEN_MIN_CHARS = 4

# PII 경고(§6.7 ⑤): 네트워크 세계는 문서용 대역(RFC 5737)만 쓴다.
DOC_NETWORKS: tuple[ipaddress.IPv4Network, ...] = tuple(
    ipaddress.IPv4Network(n) for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24"))
_PHRASE_SEP_RE = re.compile(r"[^\w.%/:+-]+")  # 문구 토큰 경계 — 단어·수치·경로 문자 외 전부
_IPV4_RE = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(?![\d.])")
_PII_RES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("주민번호 형태", re.compile(r"(?<!\d)\d{6}\s?-\s?[1-4]\d{6}(?!\d)")),
    ("휴대전화 형태", re.compile(r"(?<!\d)01[016789]-?\d{3,4}-?\d{4}(?!\d)")),
    ("이메일 형태", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    ("카드번호 형태", re.compile(r"(?<!\d)\d{4}-\d{4}-\d{4}-\d{4}(?!\d)")),
)

ERROR = "error"
WARN = "warn"


@dataclass(frozen=True)
class Finding:
    """린트 결과 1건."""

    level: str
    rule: str
    world: str
    message: str

    def __str__(self) -> str:
        return f"[{self.level}] {self.rule} · {self.world}: {self.message}"


@dataclass
class LintReport:
    """린트 결과 전체."""

    findings: list[Finding] = field(default_factory=list)
    worlds: dict[str, World] = field(default_factory=dict)

    @property
    def errors(self) -> list[Finding]:
        """오류만."""
        return [f for f in self.findings if f.level == ERROR]

    @property
    def warnings(self) -> list[Finding]:
        """경고만."""
        return [f for f in self.findings if f.level == WARN]

    @property
    def ok(self) -> bool:
        """오류가 없으면 True(경고는 통과)."""
        return not self.errors


# ─────────────────────────────── 문자열 수집 ───────────────────────────────


def _strings(node: Any, out: list[str]) -> None:
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, Mapping):
        for v in node.values():
            _strings(v, out)
    elif isinstance(node, (list, tuple)):
        for v in node:
            _strings(v, out)


def reply_strings(world: World) -> list[str]:
    """도구 응답(`tools.*[].reply` · `tools.default`)의 문자열 값."""
    out: list[str] = []
    for name, rules in world.tools.items():
        if name == "default":
            _strings(rules, out)
            continue
        for rule in rules if isinstance(rules, list) else []:
            if isinstance(rule, Mapping):
                _strings(rule.get("reply"), out)
    return out


def injected_strings(world: World) -> list[str]:
    """조사에 주입되는 문자열(`event` 전 필드 + `meta`) — 누수 검사 대상."""
    out: list[str] = []
    _strings(world.event, out)
    _strings(world.data.get("meta"), out)
    return out


# ─────────────────────────────── 규칙 ───────────────────────────────


def check_layout(world: World) -> list[str]:
    """파일 위치·split·id 일치."""
    problems: list[str] = []
    if world.split not in SPLITS:
        problems.append(f"split {world.split!r}는 {SPLITS} 중 하나여야 한다")
    if world.path is not None:
        if world.path.stem != world.id:
            problems.append(f"파일 이름 {world.path.name}이 id {world.id}와 다르다")
        if world.path.parent.name != world.split:
            problems.append(f"디렉터리 {world.path.parent.name}/가 split {world.split}과 다르다")
    return problems


def check_label(world: World) -> list[str]:
    """라벨 완전성 · 별칭 수 · 기대 판정 · 모범 대본."""
    label = world.label
    problems: list[str] = []
    entity = label.get("root_entity")
    if not isinstance(entity, Mapping) or not entity.get("host"):
        problems.append("label.root_entity.host가 없다")
    if label.get("layer") not in LAYERS:
        problems.append(f"label.layer {label.get('layer')!r}는 {sorted(LAYERS)} 중 하나여야 한다")
    if not label.get("kind"):
        problems.append("label.kind가 없다")
    if not isinstance(label.get("change_related"), bool):
        problems.append("label.change_related(bool)가 없다")
    aliases = label.get("aliases")
    kinds = aliases.get("kind") if isinstance(aliases, Mapping) else None
    if not isinstance(kinds, list) or len([a for a in kinds if a]) < MIN_KIND_ALIASES:
        problems.append(f"label.aliases.kind가 {MIN_KIND_ALIASES}개 미만이다")
    if not isinstance(world.expect.get("abstain"), bool):
        problems.append("expect.abstain(bool)이 없다")
    model = (world.data.get("script") or {}).get("model")
    if not isinstance(model, list) or not model:
        problems.append("script.model(모범 대본)이 없다")
    return problems


def check_leak(world: World) -> list[str]:
    """`event`·`meta`에 정답 kind 표면형이 있는가."""
    forms = kind_forms(world.label)
    problems: list[str] = []
    for text in injected_strings(world):
        n = norm(text)
        hits = [f for f in forms if f and f in n]
        if hits:
            problems.append(f"알람 문구 {text!r}에 정답 표면형 {hits}")
    return problems


def _consistent(rule_match: Mapping[str, Any], ke_match: Mapping[str, Any]) -> bool:
    """규칙 `match`가 핵심 증거 `match`와 모순되지 않는가(같은 키는 같은 값)."""
    return all(k not in ke_match or ke_match[k] == v for k, v in rule_match.items())


def evidence_rule(world: World, ke: Mapping[str, Any]) -> tuple[str, Any] | None:
    """핵심 증거 인자로 부르면 닿는 첫 응답 규칙 `(matcher id, reply)` — 없으면 None."""
    name = str(ke.get("tool"))
    rules = world.tools.get(name)
    match = ke.get("match") or {}
    for i, rule in enumerate(rules if isinstance(rules, list) else []):
        if isinstance(rule, Mapping) and _consistent(rule.get("match") or {}, match):
            return f"{name}#{i}", rule.get("reply")
    return None


def check_evidence(world: World) -> list[str]:
    """`key_evidence`가 `tools`에 실제로 있는가(도구 · 규칙 · 응답 속 cite 토큰)."""
    problems: list[str] = []
    for i, ke in enumerate(world.key_evidence):
        where = f"key_evidence[{i}] {ke.get('tool')}"
        cites = [c for c in ke.get("cite") or [] if str(c).strip()]
        if not cites:
            problems.append(f"{where}: cite가 비었다")
        found = evidence_rule(world, ke)
        if found is None:
            problems.append(f"{where}: match {ke.get('match') or {}}에 닿는 응답 규칙이 없다")
            continue
        matcher, reply = found
        if isinstance(reply, Mapping) and reply.get("inject") is True:
            problems.append(f"{where}: 닿는 규칙 {matcher}가 오류 주입이다")
            continue
        body = norm(json.dumps(reply, ensure_ascii=False, default=str))
        missing = [c for c in cites if norm(c) not in body]
        if missing:
            problems.append(f"{where}: 규칙 {matcher} 응답에 cite {missing}가 없다")
    return problems


def check_hosts(world: World) -> list[str]:
    """`hosts`가 알람 호스트·정답 호스트를 포함하는가."""
    ev = world.event
    entity = world.label.get("root_entity") or {}
    wanted = [ev.get("hostname"), ev.get("serverName"),
              entity.get("host") if isinstance(entity, Mapping) else None]
    missing = sorted({str(h) for h in wanted if h and str(h).strip().casefold() not in world.hosts})
    return [f"hosts에 {missing}가 없다"] if missing else []


def check_pii(world: World) -> list[str]:
    """PII 형태 경고 — 세계 문자열 전부(대본 포함)."""
    texts: list[str] = []
    _strings(world.data, texts)
    problems: list[str] = []
    for text in texts:
        for m in _IPV4_RE.finditer(text):
            try:
                ip = ipaddress.IPv4Address(m.group(1))
            except ValueError:
                continue
            if not any(ip in net for net in DOC_NETWORKS):
                problems.append(f"문서용 대역 밖 IPv4 {m.group(1)} — {text[:60]!r}")
        for label, pattern in _PII_RES:
            if pattern.search(text):
                problems.append(f"{label} — {text[:60]!r}")
    return problems


# ─────────────────────────────── 홀드아웃 · 문구 ───────────────────────────────


def phrase_norm(text: str) -> str:
    """문구 비교용 — NFKC · 대소문자 무시 · 괄호·기호를 공백으로(`[CPU` = `cpu`) · 공백 하나로."""
    return " ".join(_PHRASE_SEP_RE.sub(" ", unicodedata.normalize("NFKC", text).casefold()).split())


def phrase_units(world: World) -> set[str]:
    """세계의 「문구」 단위 — `event`·`meta`·도구 응답의 문자열 값 중 토큰 2개 이상 · 6자 이상."""
    out: set[str] = set()
    for text in injected_strings(world) + reply_strings(world):
        p = phrase_norm(text)
        tokens = p.split()
        if len(tokens) >= PHRASE_MIN_TOKENS and len("".join(tokens)) >= PHRASE_MIN_CHARS:
            out.add(p)
    return out


def _shingles(phrase: str) -> set[tuple[str, ...]]:
    tokens = phrase.split()
    return {tuple(tokens[i:i + SHINGLE_TOKENS]) for i in range(len(tokens) - SHINGLE_TOKENS + 1)}


def duplicate_phrases(holdout: World, dev: World) -> list[str]:
    """홀드아웃 세계와 개발 세계가 함께 쓰는 문구(같은 문구 전체 또는 연속 4토큰)."""
    h_units, d_units = phrase_units(holdout), phrase_units(dev)
    found = sorted(h_units & d_units)
    d_shingles: set[tuple[str, ...]] = set()
    for p in d_units:
        d_shingles |= _shingles(p)
    for p in sorted(h_units - set(found)):
        shared = sorted(_shingles(p) & d_shingles)
        if shared:
            found.append(f"{p} (겹친 연속 토큰: {' '.join(shared[0])})")
    return found


def holdout_tokens(world: World) -> set[str]:
    """홀드아웃 고유 토큰 — 호스트명 전부 · 정답 호스트 · 정답 구성요소(프로세스명·경로 등)."""
    entity = world.label.get("root_entity") or {}
    raw: list[Any] = list(world.data.get("hosts") or [])
    raw += [world.event.get("hostname"), world.event.get("serverName")]
    if isinstance(entity, Mapping):
        raw += [entity.get("host"), entity.get("component")]
    return {str(t).strip() for t in raw if t and len(str(t).strip()) >= TOKEN_MIN_CHARS}


def source_token_hits(tokens: Iterable[str], source_root: Path) -> dict[str, list[str]]:
    """토큰 → 그 토큰이 들어 있는 소스 파일(`source_root` 아래 `*.py` · 대소문자 무시)."""
    wanted = {t: t.casefold() for t in tokens}
    hits: dict[str, list[str]] = {}
    root = Path(source_root)
    for path in sorted(root.rglob("*.py")) if root.is_dir() else []:
        text = path.read_text(encoding="utf-8", errors="replace").casefold()
        for token, folded in wanted.items():
            if folded in text:
                hits.setdefault(token, []).append(str(path.relative_to(root)))
    return hits


# ─────────────────────────────── 전체 ───────────────────────────────


def world_files(root: Path) -> list[Path]:
    """세계 YAML 목록(`<root>/<split>/*.yaml` — 하위 전부)."""
    return sorted(Path(root).rglob("*.yaml"))


def lint(worlds_root: Path = DEFAULT_WORLDS_ROOT, *,
         snapshot_path: Path = schema.DEFAULT_SNAPSHOT_PATH,
         source_root: Path = DEFAULT_SOURCE_ROOT) -> LintReport:
    """세계 디렉터리 전건을 검사한다."""
    report = LintReport()
    add = report.findings.append
    snapshot = schema.load_snapshot(snapshot_path)
    for path in world_files(worlds_root):
        try:
            world = load_world(path)
        except Exception as e:  # YAML 문법·id 누락
            add(Finding(ERROR, "layout", path.name, f"읽기 실패: {type(e).__name__}: {e}"))
            continue
        if world.id in report.worlds:
            add(Finding(ERROR, "layout", world.id,
                        f"id 중복 — {report.worlds[world.id].path} · {path}"))
            continue
        report.worlds[world.id] = world
        checks: Sequence[tuple[str, list[str]]] = (
            ("layout", check_layout(world)),
            ("label", check_label(world)),
            ("leak", check_leak(world)),
            ("evidence", check_evidence(world)),
            ("hosts", check_hosts(world)),
            ("schema", [p.split(": ", 1)[-1] for p in schema.check_world(world.data, snapshot)]),
        )
        for rule, problems in checks:
            for p in problems:
                add(Finding(ERROR, rule, world.id, p))
        for p in check_pii(world):
            add(Finding(WARN, "pii", world.id, p))

    dev = [w for w in report.worlds.values() if w.split == "dev"]
    holdout = [w for w in report.worlds.values() if w.split == "holdout"]
    for h in holdout:
        for d in dev:
            for p in duplicate_phrases(h, d):
                add(Finding(ERROR, "phrase_dup", h.id, f"개발 세계 {d.id}와 같은 문구: {p!r}"))
        for token, files in sorted(source_token_hits(holdout_tokens(h), source_root).items()):
            add(Finding(ERROR, "holdout_token", h.id,
                        f"고유 토큰 {token!r}이 sre_agent 소스에 있다: {', '.join(files)}"))
    return report


def main(worlds_root: Path = DEFAULT_WORLDS_ROOT) -> int:
    """CLI `--lint` — 위반 목록 출력 · 오류가 있으면 1."""
    report = lint(worlds_root)
    for f in report.findings:
        print(f)
    splits = {s: sum(1 for w in report.worlds.values() if w.split == s) for s in SPLITS}
    print(f"세계 {len(report.worlds)}건({', '.join(f'{k} {v}' for k, v in splits.items())}) · "
          f"오류 {len(report.errors)} · 경고 {len(report.warnings)}")
    return 0 if report.ok else 1
