"""벤치 안전 가드 — 버전 · 호출 예산 · 자동 중단 · 일정 (plans/152 §3.7 · W2 · D-323).

① **뷰 서버 버전**: 5.6.2.18 미만이면 실행하지 않는다. 허용 GET 경로 중 뷰 서버 버전을 주는 것이
   없다(`/api/instance`의 버전은 에이전트 버전) → E2 는 운영자가 `--view-server-version`으로 넣는다
   (없거나 미달이면 exit 2). E1(목)은 해당 없음.
② **호출 예산**: 소스당 3,000 호출(게이트웨이 감사 줄 `api_calls`를 소스별로 합산). 넘으면 즉시
   중단한다 — 드라이버가 턴 사이와 턴 도중(감시 스레드) 모두 검사하고, 넘으면 게이트웨이를 멈춘다.
   승격 작업은 수명 끝 감사 줄(`audit_job_finished`)이 수명 호출 수를 다시 남기므로 이중으로 셀 수
   있다 — 예산은 넉넉한 쪽(과대)으로 세는 것이 안전하므로 그대로 둔다.
③ **자동 중단**: 429 1회 · 타임아웃 비율 5% 초과 · 지연 기준선 대비 3배.
   팬아웃·복합 도구(`apm_fleet`·`apm_app_health` 등)는 도메인/소스별 429·타임아웃을 봉투
   `limits`로만 남기고 감사 줄 `error`는 비운다 → 드라이버는 게이트웨이를 DEBUG 로 띄워 제니퍼
   응답 1건마다 찍히는 `apm http: … status=… elapsed_ms=…` 줄을 함께 읽는다(`gateway.AuditTail`
   이 다음 감사 항목의 `http` 칸으로 접는다). 429 = 응답 줄 `status=429`(또는 감사
   `error=apm_quota_exceeded`). 타임아웃 = 응답 줄 없이 끝난 호출(감사 `api_calls` − 응답 줄 수 ·
   httpx 타임아웃은 응답 줄 전에 끊긴다)이 있고 그 도구 경과가 호출 상한의 90% 이상인 것(빠른
   연결 실패는 제외). 2026-10-13 실측.
   타임아웃 비율 = **타임아웃 난 도구 호출 수 ÷ 제니퍼를 부른 도구 호출 수**(감사 줄 단위 · 승격
   작업의 수명 끝 줄은 같은 호출로 센다).
④ **일정**: 무거운 등급은 맨 마지막 · 최대 5개 · 앞 단계가 정상(중단·타임아웃 0)일 때만. E2 는
   `e2_skip` 시나리오를 건너뛰고 WAS 접촉 API(표 A 경로)는 경로당 1회만 돈다.

중단 사유는 run 메타(`Guard.meta()`)에 남고, 남은 시나리오는 `verdict.skipped="aborted:<사유>"`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .scenarios import HEAVY_MAX, Scenario

#: 뷰 서버 최소 버전(D-323 ① — 그 아래는 허용 API 응답 모양이 다르다).
VIEW_SERVER_MIN: tuple[int, ...] = (5, 6, 2, 18)
#: 소스당 제니퍼 호출 예산(run 1회 · D-323 ③).
BUDGET_PER_SOURCE = 3000
#: 타임아웃 비율 상한(초과 시 중단).
TIMEOUT_RATIO_MAX = 0.05
#: 타임아웃 비율 분모 하한(도구 호출 수) — 첫 몇 호출에서 1건만 나도 비율이 100%가 돼 중단되는
#: 것을 막는다. 20이면 처음 20호출 안에서 1건은 통과(5%), 2건이면 중단(10%)이다 — 「두 번 연속
#: 의심」 수준.
TIMEOUT_MIN_DENOM = 20
#: 지연 기준선 대비 배수(초과 시 중단).
LATENCY_FACTOR = 3.0
#: 지연 비교의 절대 하한(ms · **E1 전용**) — 목·근거리 응답은 수 ms 라 지터만으로 3배가 된다.
#: 기준선보다 이만큼도 느려지지 않았으면 배수를 넘어도 부하 신호로 보지 않는다. E2 는 §3.7 그대로
#: 3배만 본다(드라이버가 `latency_floor_ms=0`).
LATENCY_FLOOR_MS = 200.0
#: 지연 기준선 재측정 간격(시나리오 수).
BASELINE_EVERY = 10
#: 타임아웃으로 보는 감사 오류 코드 — 게이트웨이는 타임아웃을 `source_unavailable`로 싣고 원인
#: 문구는 감사 줄에 남기지 않는다 → 경과가 호출 상한의 90% 이상인 `source_unavailable`만 센다.
TIMEOUT_ERROR = "source_unavailable"
TIMEOUT_ELAPSED_RATIO = 0.9
QUOTA_ERROR = "apm_quota_exceeded"
QUOTA_STATUS = "429"
E1_VERSION_LABEL = "e1-mock(해당 없음)"

_VERSION_RE = re.compile(r"^\s*(\d+(?:\.\d+){0,3})\s*$")


def parse_version(text: str | None) -> tuple[int, ...] | None:
    """`5.6.2.18` → (5, 6, 2, 18) · 형식이 아니면 None."""
    if not text:
        return None
    match = _VERSION_RE.match(text)
    if match is None:
        return None
    return tuple(int(p) for p in match.group(1).split("."))


def check_view_server_version(env: str, version: str | None) -> tuple[bool, str]:
    """① 버전 확인 → (실행 가능, 메타 표기 또는 중단 사유).

    E1 은 목이라 해당 없음(값을 주면 그대로 표기). E2 는 운영자 입력이 필수이고 최소 버전 이상이어야
    한다.
    """
    if env != "e2":
        return True, version or E1_VERSION_LABEL
    parsed = parse_version(version)
    if parsed is None:
        return False, ("뷰 서버 버전을 도구로 얻을 수 없다 — --view-server-version 으로 넣는다"
                       if not version else  # 값 미출력 — 오입력이 다른 비밀일 수 있다
                       "뷰 서버 버전 형식이 아니다(숫자.숫자… 예 5.6.2.18) — 값은 출력하지 않는다")
    padded = parsed + (0,) * (len(VIEW_SERVER_MIN) - len(parsed))
    if padded < VIEW_SERVER_MIN:
        return False, (f"뷰 서버 {version} < {'.'.join(map(str, VIEW_SERVER_MIN))} — 실행하지 "
                       "않는다(D-323 ①)")
    return True, str(version)


# --- 일정 (④) -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Plan:
    """실행 일정.

    Attributes:
        light: 먼저 도는 시나리오(입력 순서)
        heavy: 맨 마지막 무거운 등급(최대 `HEAVY_MAX` · 앞 단계 정상일 때만)
        skipped: 돌지 않는 시나리오 id → 사유
    """

    light: tuple[Scenario, ...]
    heavy: tuple[Scenario, ...]
    skipped: dict[str, str] = field(default_factory=dict)


def _was_apis(scenario: Scenario) -> list[str]:
    """WAS 접촉 API 키 — 덮는 표 A 경로 전부(정렬 · 없으면 시나리오 id)."""
    return sorted(scenario.cov_paths) or [scenario.id]


def schedule(scenarios: Sequence[Scenario], *, env: str, heavy_max: int = HEAVY_MAX) -> Plan:
    """④ 일정 — 무거운 등급 맨 뒤·최대 `heavy_max` · E2 는 `e2_skip` 제외 · WAS API 당 1회."""
    light: list[Scenario] = []
    heavy: list[Scenario] = []
    skipped: dict[str, str] = {}
    was_seen: set[str] = set()
    for scenario in scenarios:
        if env == "e2" and scenario.e2_skip:
            skipped[scenario.id] = "e2_skip"  # 판정기 계약 값(사유는 시나리오 파일)
            continue
        if env == "e2" and scenario.was_touch:
            apis = _was_apis(scenario)
            seen = [a for a in apis if a in was_seen]
            if seen:  # 경로 하나라도 이미 접촉했으면 시나리오째 건너뛴다(첫 경로만 보던 우회 차단)
                skipped[scenario.id] = f"was_once:{seen[0]}"
                continue
            was_seen.update(apis)
        if scenario.heavy:
            if len(heavy) >= heavy_max:
                skipped[scenario.id] = f"heavy_cap:{heavy_max}"
                continue
            heavy.append(scenario)
        else:
            light.append(scenario)
    return Plan(tuple(light), tuple(heavy), skipped)


# --- 예산·자동 중단 (②·③) ------------------------------------------------------------------


def is_timeout(entry: Mapping[str, Any], timeout_sec: float) -> bool:
    """감사 항목이 제니퍼 호출 타임아웃으로 보이는가(휴리스틱 — `TIMEOUT_ERROR` 주석)."""
    return (entry.get("error") == TIMEOUT_ERROR
            and float(entry.get("elapsed_ms") or 0.0) >= TIMEOUT_ELAPSED_RATIO * timeout_sec * 1000)


def _quota_responses(entry: Mapping[str, Any]) -> int:
    """항목에 접힌 응답 줄 중 429 수."""
    statuses = (entry.get("http") or {}).get("statuses") or {}
    return int(statuses.get(QUOTA_STATUS) or 0)


class Guard:
    """호출 예산·자동 중단 상태(스레드 하나에서만 갱신한다 — 드라이버 감시 스레드와 본 스레드는
    `observe`를 번갈아 부르지 않게 드라이버가 잠근다).

    Args:
        timeout_sec: 게이트웨이 호출 1회 상한(타임아웃 판정)
        budget: 소스당 호출 예산
        http_lines: 감사 항목에 응답 줄 통계(`http`)가 빠짐없이 접혀 들어오는가(게이트웨이 DEBUG
            로그 · 드라이버). True 면 응답 줄 없이 끝난 호출로 팬아웃 부분 타임아웃을 센다. False 면
            감사 줄 `error`만 본다(응답 줄이 없다고 타임아웃으로 오인하지 않게).
        latency_floor_ms: 지연 비교 절대 하한(E1 `LATENCY_FLOOR_MS` · E2 0 = 3배만)
    """

    def __init__(self, *, timeout_sec: float, budget: int = BUDGET_PER_SOURCE,
                 http_lines: bool = False, latency_floor_ms: float = LATENCY_FLOOR_MS) -> None:
        self.timeout_sec = timeout_sec
        self.budget = budget
        self.http_lines = http_lines
        self.latency_floor_ms = latency_floor_ms
        self.calls_by_source: dict[str, int] = {}
        self.total_calls = 0
        self.tool_calls = 0
        self.timeouts = 0
        self.quota_hits = 0
        self.http_responses = 0
        self.unanswered_fast = 0
        self._carry = 0
        self._job_calls: dict[str, int] = {}
        self._timed_jobs: set[str] = set()
        self.baseline_ms: float | None = None
        self.latency_samples: list[float] = []
        self.aborted: str | None = None

    def abort(self, reason: str) -> str:
        """중단 사유를 남긴다(첫 사유만 유지)."""
        if self.aborted is None:
            self.aborted = reason
        return self.aborted

    def _unanswered(self, entry: Mapping[str, Any], attempts: int) -> int:
        """응답 줄 없이 끝난 호출 수 — 응답 줄은 전역으로 이월해 맞춘다(동시 도구의 응답 줄이
        먼저 끝난 도구 항목에 접혀도 뒤 항목에서 상쇄된다). 감사 줄이 없는 응답 줄(인스턴스 선적재
        등)은 이월분으로 남아 뒤 누락을 그만큼 덜 센다 — 타임아웃 판정이 늦어지는 쪽의 한계다."""
        self._carry += int((entry.get("http") or {}).get("responses") or 0)
        covered = min(attempts, self._carry)
        self._carry -= covered
        return attempts - covered

    def observe(self, entries: Iterable[Mapping[str, Any]]) -> str | None:
        """감사 항목을 누적하고 중단 조건이면 사유를 돌려준다(이미 중단이면 그 사유)."""
        for entry in entries:
            calls = int(entry.get("api_calls") or 0)
            sources = entry.get("sources") or {}
            if sources:
                for sid, n in sources.items():
                    self.calls_by_source[sid] = self.calls_by_source.get(sid, 0) + int(n)
            elif calls:
                self.calls_by_source["?"] = self.calls_by_source.get("?", 0) + calls
            self.total_calls += calls
            self.http_responses += int((entry.get("http") or {}).get("responses") or 0)
            quota = max(_quota_responses(entry), 1 if entry.get("error") == QUOTA_ERROR else 0)
            if quota:
                self.quota_hits += quota
                self.abort("quota_429")
            # 승격 작업: 넘김 줄과 수명 끝 줄이 같은 job_id 로 누적 호출 수를 남긴다 → 증분만
            job = entry.get("job_id")
            seen_job = bool(job) and job in self._job_calls
            attempts = calls
            if job:
                prev = self._job_calls.get(job, 0)
                attempts = max(calls - prev, 0)
                self._job_calls[job] = max(prev, calls)
            if entry.get("tool") is None:  # 응답 줄만 접힌 합성 항목(게이트웨이 tail)
                self._unanswered(entry, 0)
                continue
            if calls and not seen_job:
                self.tool_calls += 1
            timed = is_timeout(entry, self.timeout_sec)
            if self.http_lines:
                missing = self._unanswered(entry, attempts)
                slow = (float(entry.get("elapsed_ms") or 0.0)
                        >= TIMEOUT_ELAPSED_RATIO * self.timeout_sec * 1000)
                if missing and not slow:
                    self.unanswered_fast += missing
                timed = timed or bool(missing and slow)
            if timed and not (job and job in self._timed_jobs):
                self.timeouts += 1
                if job:
                    self._timed_jobs.add(job)
        if self.aborted is None:
            over = sorted(s for s, n in self.calls_by_source.items() if n > self.budget)
            if over:
                self.abort(f"budget:{over[0]}")
            elif self.timeouts / max(self.tool_calls, TIMEOUT_MIN_DENOM) > TIMEOUT_RATIO_MAX:
                self.abort("timeout_ratio")
        return self.aborted

    def observe_latency(self, elapsed_ms: float) -> str | None:
        """기준선 측정 1회 — 첫 값은 기준선, 이후는 배수·절대 하한 둘 다 넘으면 중단."""
        self.latency_samples.append(round(elapsed_ms, 1))
        if self.baseline_ms is None:
            self.baseline_ms = elapsed_ms
            return self.aborted
        if (elapsed_ms > LATENCY_FACTOR * self.baseline_ms
                and elapsed_ms - self.baseline_ms > self.latency_floor_ms):
            self.abort("latency_3x")
        return self.aborted

    @property
    def healthy(self) -> bool:
        """앞 단계 정상 — 중단 없음 · 타임아웃 0 · 429 0(무거운 등급 실행 조건)."""
        return self.aborted is None and self.timeouts == 0 and self.quota_hits == 0

    def meta(self) -> dict[str, Any]:
        return {
            "aborted": self.aborted,
            "calls_by_source": dict(sorted(self.calls_by_source.items())),
            "total_calls": self.total_calls,
            "tool_calls": self.tool_calls,
            "timeouts": self.timeouts,
            "quota_hits": self.quota_hits,
            "http_lines": self.http_lines,
            "http_responses": self.http_responses,
            "unanswered_fast": self.unanswered_fast,
            "baseline_ms": None if self.baseline_ms is None else round(self.baseline_ms, 1),
            "latency_samples": list(self.latency_samples),
            "limits": {
                "budget_per_source": self.budget,
                "timeout_ratio_max": TIMEOUT_RATIO_MAX,
                "timeout_ratio_unit": "tool_calls",
                "timeout_min_denom": TIMEOUT_MIN_DENOM,
                "latency_factor": LATENCY_FACTOR,
                "latency_floor_ms": self.latency_floor_ms,
                "baseline_every": BASELINE_EVERY,
                "heavy_max": HEAVY_MAX,
            },
        }


__all__ = [
    "VIEW_SERVER_MIN",
    "BUDGET_PER_SOURCE",
    "TIMEOUT_RATIO_MAX",
    "TIMEOUT_MIN_DENOM",
    "LATENCY_FACTOR",
    "LATENCY_FLOOR_MS",
    "BASELINE_EVERY",
    "E1_VERSION_LABEL",
    "parse_version",
    "check_view_server_version",
    "Plan",
    "schedule",
    "is_timeout",
    "Guard",
]
