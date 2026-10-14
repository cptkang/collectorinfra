"""반출 위생 — 토큰 단위 형식 보존 치환 · 민감 모양만 · 근거 묶음 · 누출 관문.

plans/152 §3.8·§3.10 · W4 · D-323 · D-321(형식 보존 치환) · D-301(ITAM 위생 재사용) ·
D-300 ④(계정 가림) · D-274(게이트웨이 import 0).

순수 로직이다 — 네트워크·LLM 0. 디스크 쓰기는 `write_outputs` 하나뿐이고, 관문을 통과할 때만
산출물을 쓴다(실패하면 `leak_check.json`만 쓰고 호출 측이 exit 1).

ITAM 벤치 부품을 **고치지 않고** 그대로 쓴다: `substitute.FakeValues`(run 스코프 가짜 값 ·
2단계 수집) ·
`redact.PiiVault`·`redact_text`·`scrub_tree`·`mask_identifier`·`LeakGate`·`write_gated` ·
`catalog.ColumnPolicy`(APM은 컬럼이 없어 빈 테이블 + 카나리아만).

**이름 토큰화 규칙**(`split_name`) — 이름(인스턴스·호스트·업무·서비스·URL)을 다음에서 자른다.
  - 구분자: 글자(유니코드 L*)·숫자(N*)가 아닌 글자 전부(`-` `_` `.` `/` `:` `@` 공백 …) —
    원문 그대로 둔다
  - 숫자 ↔ 숫자 아닌 글자 경계(`was01` → `was`|`01`)
  - 소문자 → 대문자 경계(`ordWas` → `ord`|`Was`) · 대문자 연쇄 뒤 대문자+소문자(`HTTPServer` →
    `HTTP`|`Server`)
  - 문자 체계 경계(한글 ↔ 라틴 ↔ 그 밖 글자 — `주문WAS` → `주문`|`WAS`)
  - IPv4는 토큰화하지 않고 `FakeValues.fake_ip`(계층 일관)로 바꾼다
  토큰마다 `FakeValues.fake`(형식 보존 · 메모 키가 ASCII 대소문자를 접으므로 `ORD`·`ord`는 같은 가짜
  토큰에 원 대소문자를 다시 입힌 값)로 바꿔 재조립한다. 허용 목록(`term_policy.yaml`) 토큰은 원문
  유지 — 대조는 NFKC + casefold 같음.

**토큰 사전**(`name_tokens`) — 치환으로 바뀌는 이름의 토큰 중 허용 목록·숫자만·1자를 뺀 전부(ASCII
  2자 토큰 포함 — 공통 2자 단어도 허용 목록으로만 살린다 · 기본 거부)와, 대소문자 경계로만 갈린
  라틴 토큰 연쇄의 결합 소문자형(`OrdPay01` → `ordpay`). 코드 어휘·레지스트리·안전 요약 어휘의
  낱말(`vocab_words` — 보기 id·소스 id·라벨·분류·키 이름 조각)과 같은 토큰은 뺀다(`protect_tokens`
  · 관문도 같은 낱말 · plans/164 W1 4라운드 — 원이름 전체 대조는 그대로). 자유 문장은 치환 전에
  NFKC + 제로폭 문자 제거(`_normalize_free` · 치환기만). 토큰 사전은 레코드·P층·run 메타의 **문자열
  값 전부**에 적용한다(키는 원이름·ID·계정만 — 코드 어휘 칸 `SKIP_KEYS`와 시나리오 파일 어휘
  `expect`는 제외). 문장 칸(`SENTENCE_KEYS` · 관측 자유 문장 포함)은 이어서 `redact_text`로, 그 밖
  값은 `scrub_tree`로 넘긴다. 관문은 같은 범위의 잎을 토큰 사전으로 다시 대조한다(`pii_value` ·
  `.md`는 원이름·ID·카나리아만).

**코드 어휘 칸 값 검증**(plans/164 W1) — `SKIP_KEYS`는 키 이름으로 치환·토큰 대조를 면제하므로,
  값을 칸별 닫힌 어휘(`CodeVocab` — 레지스트리 보기·인자·소스·영역·도구 · 판정기·본체 상수)와
  대조해 밖의 값은 버리고 `<키>_dropped`에 건수만 싣는다. 칸 아래 사전의 키도 같은 어휘로 본다.
  면제는 도구·계획 인자 묶음(`ARG_KEYS`) 아래에서 꺼진다(보기·소스 id 칸만 유지) — 그 아래 값은 일반
  치환·토큰 대조를 받는다. 벤치 소유 칸(`BENCH_KEYS` — 모양만)은 소유 위치(`BENCH_PATHS`)에서만
  면제이고 그 밖 깊이에서는 빈 어휘다. 소스 id 칸(`SOURCE_ID_KEYS`)과 소스 맥락
  (`SOURCE_CONTEXT_KEYS` — 소스 범위·소스 되묻기·자동 답) 안 소스 칸은 레지스트리 소스 id·라벨·
  「전체」 표지로 닫힌다.
  위치 판정은 치환기·관문이 `closed_field` 하나를 같이 쓴다. 관문은 2차 방어로 같은 어휘 밖
  잎·키를 `pii_value`로 막는다. 재선택 보기 후보는 `candidate_views`로 싣는다(`target.candidates`
  원이름과 구분). 레지스트리 밖 소스 id(E2 게이트웨이 `.env` 소스)는 호출 측이 원이름 대조 집합에
  합친다(`unregistered_sources`).

**사용자 정보 대칭 치환**(plans/164 W1 · D-301 ④) — 관문 `user_info`와 같은 값(3자 이상)을
  대소문자 무시로 관측 자유 문장 칸(`response`·`clarify_text`·`answer_lines`·`disclosures[].text`·
  `error`·`calls[].error`)에서 `USER_INFO_MARK`로 바꾼다 — 원이름·ID·토큰 치환 뒤 맨 마지막. 값이
  인벤토리 이름·토큰과 겹치거나(`user_info_conflicts`) 코드 어휘·산출물 키 이름의 조각이면
  (`user_info_vocab_conflicts`) 호출 측이 턴 송신 전에 멈춘다(E2 · E1 은 경고).

**GUID·txid 규칙** — 키 이름(`guid`·`txid`)으로 모은 값과 GUID 모양(8-4-4-4-12 16진 · 하이픈 없는
  16진 32자리)은 형식 보존 치환한다. GUID 모양은 위치마다 16진 숫자 난수(하이픈 유지 ·
  원값에 대문자만 있으면 대문자) — `FakeValues`의 라틴 군 치환은 16진 형식을 깨므로 별도
  생성기(`_HexIds`)를 쓴다.
  그 밖 모양(부호 있는 정수 txid · `guid-0000` 같은 합성 GUID)은 `FakeValues.fake`(숫자→숫자 · 글자→
  글자 · 구조 유지)다. 둘 다 run 메모 · 2단계 등록을 따른다.

**run 단위 2단계**(Known Mistake 2026-10-08) — `RunSanitizer.sanitize`가 ①원이름·ID·계정을 전부
  모아 `FakeValues.collecting()` 안에서 같은 치환 경로를 한 번 돌려 원값만 등록하고 ②그 뒤
  같은 경로로 발급한다. 「이미 가짜 값이면 통과」 단락은 없다 — 다시 바꾸지 않는 것은
  **이번 호출이 만든 구간**(사전 일치 구간)뿐이고, 그 밖 구간만 `redact_text`·`scrub_tree`로 넘긴다.

**근거 묶음 「없음」 규칙** — 관측상 없음(드라이버가 칸을 채웠는데 빈 목록·None·빈 문자열)은 표지
  `NONE`(`"none"`)으로, 관측 누락(드라이버 레코드에 칸 자체가 없음)은 `null`로 적는다.
  `check_evidence`는 실패 턴의 필수 칸이 `null`·빈 문자열·부재이면 누락으로 센다(`"none"`·False·0은
  값이다).
"""

from __future__ import annotations

import contextlib
import copy
import functools
import json
import re
import secrets
import unicodedata
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

import yaml

from scripts.itam_bench.catalog import ColumnPolicy
from scripts.itam_bench.redact import (
    _IPV4,
    ERROR_TEXT_MAX,
    MASK,
    RUN_USER_FIELDS,
    LeakGate,
    PiiVault,
    _pii_regex_hit,
    mask_identifier,
    redact_text,
    scrub_tree,
    write_gated,
)
from scripts.itam_bench.substitute import FakeValues

from . import REPO_ROOT
from .record import TurnRecord

TERM_POLICY_PATH = REPO_ROOT / "testdata" / "apm_bench" / "term_policy.yaml"

#: 근거 묶음 — 관측상 없음 표지(`null`은 관측 누락).
NONE = "none"
#: 민감 시나리오 응답 본문 자리 표지(모양만 — 길이는 `response_len`).
SENSITIVE_RESPONSE = "<민감:모양만>"
#: SQL 본문 가림 표지(첫 키워드는 남긴다).
SQL_MASK = "<가림:sql>"

#: 자리표 종류(`{{kind_N}}`의 kind) → 처리. `pid`·`domain_id`는 사람·조직을 식별하지 않아
#: 그대로 둔다.
NAME_PLACEHOLDER_KINDS: frozenset[str] = frozenset({"inst", "host", "biz", "svc", "url"})
ID_PLACEHOLDER_KINDS: frozenset[str] = frozenset({"guid"})
ACCOUNT_PLACEHOLDER_KINDS: frozenset[str] = frozenset({"user"})
#: 레코드 어디서든 이 키의 값은 원이름으로 모은다(게이트웨이 도구 인자 이름 · 처리기 provenance).
NAME_KEYS: frozenset[str] = frozenset(
    {"hostname", "instance_name", "business", "business_name", "service", "service_name",
     "application_name", "url"}
)
#: 이 키의 값은 GUID·txid 원값으로 모은다.
ID_KEYS: frozenset[str] = frozenset({"guid", "txid"})
#: 이 키의 값은 계정(D-300 ④ `mask_identifier`)으로 모은다.
ACCOUNT_KEYS: frozenset[str] = frozenset({"user_id", "userId", "login", "login_id", "os_user"})
#: 치환하지 않는 코드 어휘 칸(시나리오·보기·도구·분류 id — 이름 사전이 우연히 겹쳐도 깨지 않게).
SKIP_KEYS: frozenset[str] = frozenset(
    {"v", "scenario_id", "split", "covers", "sent_at", "tool", "view", "views", "agent", "agents",
     "task_id", "categories", "locus", "axes", "clarify_kind", "result_views", "parsed_views",
     "kind", "field", "sources", "areas", "tools", "kinds", "view_rows", "candidate_views"}
)
#: 보기 id 를 담는 코드 어휘 칸(plans/164 W1) — 키 이름만으로 면제하지 않고, 값을 레지스트리
#: apm 보기 id 와 교집합만 남긴다(밖의 값은 버리고 `<키>_dropped`에 건수만). 재선택 LLM 원출력
#: (`parsed_views`)이 보기 id 가 아닌 문자열(호스트 이름 등)을 내도 치환 없이 반출되지 않게 한다.
VIEW_KEYS: frozenset[str] = frozenset(
    {"view", "views", "result_views", "parsed_views", "view_rows", "candidate_views"}
)
#: 버린 코드 어휘 값 건수 칸 접미사(`parsed_views_dropped` 등 — 값 없이 건수만).
DROPPED_SUFFIX = "_dropped"
#: 코드 어휘 칸(`SKIP_KEYS`) 문자열 값의 모양 — 칸별 어휘(`CodeVocab`) 대조의 앞 조건이다
#: (plans/164 W1). 공백·비 ASCII 는 맞지 않는다.
CODE_WORD = re.compile(r"[A-Za-z0-9_.:#+\[\]\-]*")
#: 도구·계획 인자 묶음 칸 — 이 아래에서는 `SKIP_KEYS` 이름 면제를 끈다(보기 id 칸만 유지 ·
#: plans/164 W1 교정 M-3). 인자 값·키는 LLM·사용자 값이라 일반 치환·토큰 대조를 받는다.
ARG_KEYS: frozenset[str] = frozenset({"args", "view_args", "parsed_view_args", "tool_args"})
#: 벤치·시나리오 파일·시계에서 오는 코드 어휘 칸(런타임 출력이 아니다) — 모양(`CODE_WORD`)만 본다.
#: 면제는 벤치가 직접 만드는 위치(`BENCH_PATHS` — 레코드·P층 행 최상위와 근거 묶음 사본)에서만
#: 듣고, 그 밖 깊이(LLM 원출력 사전 등)에서는 빈 어휘(`BENCH_NESTED`)로 본다(plans/164 W1 N-3).
BENCH_KEYS: frozenset[str] = frozenset({"v", "scenario_id", "split", "covers", "sent_at"})
#: 벤치 소유 칸 면제 위치(목록 단계는 `[]` · 레코드·P층 행·run 메타 최상위 + P층 대조 행 +
#: 근거 묶음 사본 — `build_evidence`가 치환된 최상위 값을 옮긴 것).
BENCH_PATHS: frozenset[str] = (
    BENCH_KEYS
    | frozenset(f"{base}[].{k}" for base in ("probe", "evidence.execution.probe")
                for k in BENCH_KEYS)
    | frozenset({"evidence.question.covers", "evidence.question.split",
                 "evidence.window.sent_at"})
)
#: 벤치 소유 칸 이름이 면제 위치 밖에 올 때의 어휘 칸(빈 어휘 — 값은 버린다).
BENCH_NESTED = "bench_nested"
#: 레코드의 P층 대조 행 칸(행 최상위가 벤치 소유 칸 면제 위치).
PROBE_KEY = "probe"
#: 소스 id 칸(어느 위치든 · 인자 묶음 아래 포함) — 레지스트리 소스 id·「전체」 표지만 남긴다
#: (plans/164 W1 N-1 — E2 소스 id 는 게이트웨이 `.env`에서 와 인벤토리 밖일 수 있다).
SOURCE_ID_KEYS: frozenset[str] = frozenset(
    {"source_ids", "source_id", "observed_sources", "expected_sources"}
)
#: 소스 맥락 뿌리(소스 범위·소스 되묻기 · 자동 답 기록) — 아래의 일반 이름 칸
#: (`SOURCE_CONTEXT_FIELDS`)을 소스 어휘로 본다.
SOURCE_CONTEXT_KEYS: frozenset[str] = frozenset(
    {"apm_source_scope", "apm_source_clarification", "source_auto_answer"}
)
#: 소스 맥락 안 칸 → 어휘 칸(`source_ref` = 소스 id · `source_label` = 레지스트리 라벨).
SOURCE_CONTEXT_FIELDS: dict[str, str] = {
    "ids": "source_ref", "last_target_sources": "source_ref", "key": "source_ref",
    "id": "source_ref", "source": "source_ref", "label": "source_label",
}
#: 모양 검사 없이 닫힌 집합 정확 일치만 보는 어휘 칸(「전체」 표지 `*` · 한글 라벨).
_EXACT_VOCAB: frozenset[str] = frozenset({"source_ref", "source_label"})
#: 정제 변경 칸 이름(`driver.sanitize_changes`의 `field` — `record.SanitizeChange` 주석).
SANITIZE_FIELDS: frozenset[str] = frozenset({"agent", "views", "view_args", "targets", "sources"})
#: 드라이버 되묻기 종류(`driver._clarify`가 직접 붙이는 값 · 라우트 사전 되묻기 기본값).
CLARIFY_WORDS: frozenset[str] = frozenset({"source", "time", "required_arg", "pre_gate"})
#: P층 행 종류(`probe.py`의 `kind` — 빈 결과 대조 · 허용값 탐침).
PROBE_KINDS: frozenset[str] = frozenset({"empty_check", "allowed_value"})
#: 판정기 보기 근거(`judge.effective_views`의 `basis` 사본 — 판정기에 상수가 없다).
VIEW_BASIS: frozenset[str] = frozenset({"plan_final", "reselect.result_views"})
#: 판정 상세 사전(`axis_detail.views` · `axis_detail.categories[분류]`)의 키 → 값 어휘 칸.
#: `flag`는 문자열을 받지 않는다(bool·None 만).
_DETAIL_KEYS: dict[str, str] = {
    "expected": "views", "observed": "views", "missing": "views", "extra": "views",
    "planned": "views", "basis": "basis", "reselected": "flag", "fewshot_fixable": "flag",
    "locus": "locus",
}
#: 분해 task id(`intent_planner` — `t{순번}` · 분해 프롬프트 예시도 같다).
_TASK_ID = re.compile(r"t\d+")
#: few-shot 위치 — `F1:<보기>` · `F1:<보기>:args.<인자>` · `F1:<보기>:example#N`(`judge._loci`).
_F1_LOCUS = re.compile(r"F1:(?P<view>[^:]+)(?::(?:args\.(?P<arg>.+)|example#\d+))?")
#: 사용자 정보 치환 표지(자유 문장 칸 · 값 없이 — plans/164 W1 · D-301 ④).
USER_INFO_MARK = "<사용자정보>"
#: 시나리오 파일 어휘 칸(자리표 미충전 정답 — 저장소 공개 값) — 원이름·ID·계정만 바꾸고 토큰 사전은
#: 적용하지 않는다(관문 토큰 대조도 제외).
SCENARIO_KEYS: frozenset[str] = frozenset({"expect"})
#: 자유 문장 칸(관측) — 이름 토큰까지 사전 치환 + `redact_text` + 사용자 정보 치환(`error`는
#: 턴 실패 사유 — plans/164 L-3).
OBSERVED_FREE_TEXT: frozenset[str] = frozenset({"response", "clarify_text", "error"})
#: 구조 칸 중 문장 값 칸(어느 깊이든) — 자유 문장과 같이 사전 치환 + `redact_text`.
SENTENCE_KEYS: frozenset[str] = frozenset(
    {"prompt", "original_query", "question", "clarification", "degraded_reason", "message",
     "reason", "text", "summary"}
)

#: 한↔영 약어 관계 경고(`report.md`) — 운영 이름이 새지 않게 예시 단어를 넣지 않는다.
TERM_RISK_NOTE = (
    "이름 토큰은 일반 단어 허용 목록(term_policy.yaml)에 없으면 형식 보존 가짜 값으로 바뀐다. "
    "한글 업무어와 그 영문 약어 토큰이 한쪽만 허용 목록에 있으면 둘의 대응 관계가 반출본에서 "
    "사라진다 — 업무어↔약어 대조가 필요한 실패는 내부망 원본으로 확인한다."
)

_UUID = re.compile(
    r"(?<![0-9A-Fa-f-])[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{12}(?![0-9A-Fa-f-])|(?<![0-9A-Fa-f])[0-9A-Fa-f]{32}(?![0-9A-Fa-f])"
)
_JWT = r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*"
_BEARER = r"\bbearer[ \t]+[A-Za-z0-9._~+/=-]{8,}"
_BEARER_RE = re.compile(_BEARER, re.IGNORECASE)
_SQL = (
    r"\b(?:select|insert|update|delete|merge)\b[^\n]{0,4000}?\b(?:from|into|set)\b[^\n]*"
)
_ASCII_ALNUM = "A-Za-z0-9"
#: 민감 응답 줄 — `키 = 값`·`키: 값`이면 키만 남긴다.
_KEY_LINE = re.compile(r"^\s*([^\s=:]{1,64})\s*[=:]\s*\S")
_MAX_DRAWS = 32


# --- 허용 목록 · 토큰화 -------------------------------------------------------------------


#: 제로폭 문자(U+200B~U+200D · U+2060 · U+FEFF) — 자유 문장 치환 전에 지운다(plans/164 N-4).
_ZERO_WIDTH = re.compile("[\u200b-\u200d\u2060\ufeff]")


def _normalize_free(text: str) -> str:
    """자유 문장 → NFKC + 제로폭 문자 제거(전각·제로폭 변형이 원이름·사용자 정보 치환을 비켜 가지
    않게 · 치환기만 — 관문은 그대로)."""
    return _ZERO_WIDTH.sub("", unicodedata.normalize("NFKC", text))


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _char_kind(ch: str) -> str:
    """토큰 글자 종류 — `d` 숫자 · `U`/`l` 대·소문자 · `h` 한글 · `o` 그 밖 글자 · `""` 구분자."""
    category = unicodedata.category(ch)
    if category[0] == "N":
        return "d"
    if category[0] != "L":
        return ""
    code = ord(ch)
    if (
        0xAC00 <= code <= 0xD7A3
        or 0x1100 <= code <= 0x11FF
        or 0x3130 <= code <= 0x318F
        or 0xA960 <= code <= 0xA97F
        or 0xD7B0 <= code <= 0xD7FF
    ):
        return "h"
    if ch.isupper():
        return "U"
    if ch.islower():
        return "l"
    return "o"


def _boundary(prev: str, kind: str, text: str, i: int) -> bool:
    if prev in ("U", "l") and kind in ("U", "l"):
        if prev == "l" and kind == "U":
            return True
        if prev == "U" and kind == "U" and i + 1 < len(text):
            return _char_kind(text[i + 1]) == "l"
        return False
    return prev != kind


def split_name(name: str) -> list[tuple[str, bool]]:
    """이름 → 조각 ``(글, 토큰인가)`` — 구분자 조각은 False(원문 유지). 규칙은 모듈 독스트링."""
    pieces: list[tuple[str, bool]] = []
    buf, prev = "", ""
    for i, ch in enumerate(name):
        kind = _char_kind(ch)
        if not kind:
            if buf:
                pieces.append((buf, True))
                buf = ""
            if pieces and not pieces[-1][1]:
                pieces[-1] = (pieces[-1][0] + ch, False)
            else:
                pieces.append((ch, False))
            prev = ""
            continue
        if buf and _boundary(prev, kind, name, i):
            pieces.append((buf, True))
            buf = ""
        buf += ch
        prev = kind
    if buf:
        pieces.append((buf, True))
    return pieces


@dataclass(frozen=True)
class TermPolicy:
    """일반 단어 허용 목록(기본 거부) — 대조는 NFKC + casefold 같음."""

    terms: frozenset[str] = frozenset()

    def allows(self, token: str) -> bool:
        return _fold(token) in self.terms

    def changes(self, name: str) -> bool:
        """치환하면 이름이 바뀌는가(허용 밖 토큰이나 IP가 하나라도 있으면 True)."""
        if _IPV4.search(name):
            return True
        return any(is_token and not self.allows(p) for p, is_token in split_name(name))


def parse_term_policy(doc: Any) -> TermPolicy:
    """`term_policy.yaml` 문서 → `TermPolicy`. 형식이 틀리면 `ValueError`(닫힌 쪽)."""
    if not isinstance(doc, Mapping) or not isinstance(doc.get("terms"), list):
        raise ValueError("term_policy: 최상위 `terms` 목록이 필요하다")
    terms: set[str] = set()
    for index, term in enumerate(doc["terms"]):
        if not isinstance(term, str) or not term.strip():
            raise ValueError(f"term_policy: terms[{index}] 빈 값·문자열 아님")
        pieces = split_name(term)
        if len(pieces) != 1 or not pieces[0][1]:
            raise ValueError(f"term_policy: terms[{index}] 토큰 하나가 아니다")
        if term.isdigit():
            raise ValueError(f"term_policy: terms[{index}] 숫자만인 항목은 받지 않는다")
        terms.add(_fold(term))
    return TermPolicy(frozenset(terms))


def load_term_policy(path: Path = TERM_POLICY_PATH) -> TermPolicy:
    """허용 목록 파일을 읽는다(없거나 형식이 틀리면 예외 — 닫힌 쪽)."""
    return parse_term_policy(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


def term_risk_lines(policy: TermPolicy) -> list[str]:
    """`report.md`에 넣을 위험 표시 줄(허용 단어는 수만 — 단어 자체는 쓰지 않는다)."""
    return [
        TERM_RISK_NOTE,
        f"일반 단어 허용 목록 {len(policy.terms)}개 — 그 밖 이름 토큰은 전부 치환(기본 거부).",
    ]


def protect_tokens(tokens: Iterable[str], protected: frozenset[str]) -> set[str]:
    """토큰 사전에서 보호 낱말(`vocab_words`)과 같은 토큰을 뺀다(치환기·관문 공통)."""
    return {t for t in tokens if _fold(t) not in protected}


def name_tokens(names: Iterable[str], policy: TermPolicy) -> set[str]:
    """치환 대상 이름들의 토큰 사전(소문자) — 모듈 독스트링 「토큰 사전」."""
    tokens: set[str] = set()
    for name in names:
        if len(name.strip()) < 2 or not policy.changes(name):
            continue
        run: list[str] = []  # 구분자 없이 대소문자 경계로만 이어진 라틴 토큰 연쇄
        for piece, is_token in [*split_name(name), ("", False)]:
            latin = is_token and piece.isascii() and piece.isalpha()
            if latin:
                run.append(piece)
            else:
                if len(run) >= 2:
                    tokens.add("".join(run).lower())
                run = []
            if not is_token or policy.allows(piece) or piece.isdigit() or len(piece) < 2:
                continue
            tokens.add(piece.lower())
    return tokens


def vocab_words(strings: Iterable[str]) -> frozenset[str]:
    """코드 어휘·레지스트리 문자열 → 낱말(문자열 전체와 `split_name` 토큰 · NFKC + casefold).

    원이름 토큰이 이 낱말과 같으면 토큰 사전에서 뺀다(plans/164 W1 4라운드) — 레지스트리 밖 소스
    id(`zetaledger-prd`)의 토큰 `prd`가 레지스트리 id `cm_prd`·`budget:cm_prd`를 깨거나 관문에
    걸리게 하지 않는다. 원이름 전체 문자열 대조는 그대로다.
    """
    out: set[str] = set()
    for text in strings:
        out.add(_fold(str(text)))
        out.update(_fold(piece) for piece, is_token in split_name(str(text)) if is_token)
    return frozenset(out)


def placeholder_kind(name: str) -> str:
    """자리표 이름(`inst_1`·`domain_id_1`) → 종류(`inst`·`domain_id`)."""
    head, _, tail = name.rpartition("_")
    return head if head and (tail.isdigit() or len(tail) == 1) else name


# --- 사전 정규식 ------------------------------------------------------------------------


def _trie_pattern(words: Iterable[str]) -> str:
    """낱말 목록 → 접두 트리 정규식(긴 쪽 우선 · 백트래킹으로 짧은 쪽도 시도)."""
    trie: dict[str, Any] = {}
    for word in words:
        node = trie
        for ch in word:
            node = node.setdefault(ch, {})
        node[""] = {}

    def build(node: dict[str, Any]) -> str:
        branches = [re.escape(ch) + build(child) for ch, child in sorted(node.items()) if ch]
        if not branches:
            return ""
        body = branches[0] if len(branches) == 1 else "(?:" + "|".join(branches) + ")"
        return f"(?:{body})?" if "" in node else body

    return build(trie)


def _dictionary_pattern(words: Iterable[str]) -> str | None:
    """사전 낱말(소문자) → 정규식 조각 — ASCII 낱말은 영숫자 경계, 그 밖(한글 등)은 경계 없음."""
    ascii_words = sorted({w for w in words if w.isascii()})
    other_words = sorted({w for w in words if not w.isascii()})
    parts = []
    if ascii_words:
        parts.append(
            rf"(?<![{_ASCII_ALNUM}])(?:{_trie_pattern(ascii_words)})(?![{_ASCII_ALNUM}])"
        )
    if other_words:
        parts.append(f"(?:{_trie_pattern(other_words)})")
    return "|".join(parts) if parts else None


def _names_regex(words: Iterable[str]) -> re.Pattern[str] | None:
    pattern = _dictionary_pattern(w.lower() for w in words if w)
    return re.compile(pattern, re.IGNORECASE) if pattern else None


# --- 코드 어휘 -----------------------------------------------------------------------------


@dataclass(frozen=True)
class CodeVocab:
    """코드 어휘 칸(`SKIP_KEYS`)별 닫힌 어휘 — 밖의 값은 치환기가 버리고 관문이 막는다
    (plans/164 W1).

    Attributes:
        views: 레지스트리 apm 보기 id(`VIEW_KEYS` 칸)
        view_args: 보기 id → 그 보기의 인자 이름(locus `F1:<보기>:args.<인자>`)
        sources: 레지스트리 apm 소스 id(locus `sources:<소스>`)
        words: 칸 이름 → 닫힌 어휘(`axes`의 값은 `axis_status` · 소스 칸은 `source_ref`(소스
            id · 「전체」 `*`)·`source_label`(레지스트리 라벨 · 「전체」) — 둘은 모양 검사 없이 정확
            일치)
        loci: 고정 locus(판정기 분류·분해 전 되묻기 기본 위치)
    """

    views: frozenset[str]
    view_args: Mapping[str, frozenset[str]]
    sources: frozenset[str]
    words: Mapping[str, frozenset[str]]
    loci: frozenset[str]

    def allows(self, key: str, value: str) -> bool:
        """`key` 칸 문자열 값이 어휘 안인가(`NONE` 표지는 어느 칸이든 값이다)."""
        if key in _EXACT_VOCAB:
            return value == NONE or value in self.words.get(key, frozenset())
        if not CODE_WORD.fullmatch(value):
            return False
        if value == NONE or key in BENCH_KEYS:
            return True
        if key in VIEW_KEYS:
            return value in self.views
        if key == "task_id":
            return _TASK_ID.fullmatch(value) is not None
        if key == "locus":
            return self._locus(value)
        return value in self.words.get(key, frozenset())

    def _locus(self, value: str) -> bool:
        if value in self.loci:
            return True
        if value.startswith("sources:"):
            return value.removeprefix("sources:") in self.sources
        match = _F1_LOCUS.fullmatch(value)
        if match is None or match.group("view") not in self.views:
            return False
        arg = match.group("arg")
        return arg is None or arg in self.view_args.get(match.group("view"), frozenset())

    def strings(self) -> frozenset[str]:
        """어휘 문자열 전부(보기·인자·소스·칸별 어휘·locus) — 토큰 사전 보호 낱말의 재료."""
        return (self.views | self.sources | self.loci
                | frozenset(a for args in self.view_args.values() for a in args)
                | frozenset(w for group in self.words.values() for w in group))

    def child(self, key: str, sub: Any) -> str | None:
        """`key` 칸 사전의 키 `sub` → 그 값의 어휘 칸(어휘 밖 키면 None — 항목째 버린다)."""
        if not isinstance(sub, str) or not CODE_WORD.fullmatch(sub):
            return None
        if key == "axes":
            return "axis_status" if sub in self.words["axes"] else None
        if key == "categories":
            return "category_detail" if sub in self.words["categories"] else None
        if key in VIEW_KEYS or key == "category_detail":
            return _DETAIL_KEYS.get(sub)
        return None


def build_code_vocab(registry: Any) -> CodeVocab:
    """레지스트리(`scenarios.RegistryInfo`)·판정기·본체 상수로 코드 어휘를 만든다."""
    from src.domain.disclosure import KIND_TABLE
    from src.domain.scope_select import KIND as SCOPE_SELECT_KIND
    from src.orchestration.apm_query import APM_QUERY_AGENT, TARGET_KINDS
    from src.orchestration.conditional_agents import SOURCE_SELECT_KIND
    from src.orchestration.doc_query import DOC_QUERY_AGENT
    from src.orchestration.portal_help import PORTAL_HELP_AGENT
    from src.orchestration.schemas import allowed_agents
    from src.routing.apm_source_select import ALL_LABEL, ALL_SOURCES

    from . import judge as jd
    from .driver import INVENTORY_CALLS, PRE_GATE_KINDS

    views = registry.views
    clarify = (CLARIFY_WORDS | set(PRE_GATE_KINDS.values()) | set(jd.PRE_GRAPH_CLARIFY)
               | jd.POST_PLAN_CLARIFY | {SOURCE_SELECT_KIND, SCOPE_SELECT_KIND})
    # 대상 종류(분해 · 이름 보기 `target_arg`) · 참조 종류 · 고지 · P층 행 · 되묻기
    kinds = (set(TARGET_KINDS) | {v.target_arg for v in views.values() if v.target_arg}
             | {v.reference for v in views.values() if v.reference}
             | set(KIND_TABLE) | PROBE_KINDS | clarify)
    tools = {v.tool for v in views.values()} | {tool for tool, _, _ in INVENTORY_CALLS}
    agents = allowed_agents() | {APM_QUERY_AGENT, DOC_QUERY_AGENT, PORTAL_HELP_AGENT}
    pre_gate = [*jd.PRE_GRAPH_CLARIFY.values(), jd.PRE_GATE_DEFAULT]
    words = {
        "agent": agents, "agents": agents,
        "sources": registry.source_ids,
        "areas": {v.capability for v in views.values()},
        "tool": tools, "tools": tools,
        "kind": kinds, "kinds": kinds,
        "clarify_kind": clarify,
        "field": SANITIZE_FIELDS,
        "categories": set(jd.CATEGORY_BY_NAME),
        "axes": set(jd.AXES),
        "axis_status": {jd.PASS, jd.FAIL, jd.NA},
        "basis": VIEW_BASIS,
        "source_ref": set(registry.source_ids) | {ALL_SOURCES},
        "source_label": set(getattr(registry, "source_labels", ())) | {ALL_LABEL},
    }
    return CodeVocab(
        views=frozenset(views),
        view_args={vid: frozenset(n for a in v.args for n in (a.name, a.tool_arg) if n)
                   for vid, v in views.items()},
        sources=frozenset(registry.source_ids),
        words={k: frozenset(v) for k, v in words.items()},
        loci=frozenset(loc for c in jd.CATEGORIES for loc in c.locus)
        | frozenset(loc for p in pre_gate for loc in p.locus),
    )


@functools.lru_cache(maxsize=1)
def load_code_vocab() -> CodeVocab:
    """저장소 레지스트리로 만든 코드 어휘(프로세스 안 1회)."""
    from .scenarios import load_registry_info

    return build_code_vocab(load_registry_info())


def _vocab_only(vocab: CodeVocab, key: str, value: Any) -> tuple[Any, int]:
    """코드 어휘 칸 값 → (어휘 안 값만 남긴 값, 버린 건수).

    문자열 스칼라는 어휘 밖이면 None · 목록은 어휘 밖 문자열 원소만 뺀다 · 사전은 키를
    `CodeVocab.child`로 보고 밖의 키는 항목째 버린다(키는 그대로 · 값은 그 키의 어휘로 재귀) ·
    그 밖(None·수·bool)은 그대로. 관문 `_code_leaves`는 이 처리를 건너뛴 산출물의 2차 방어다.
    """
    if isinstance(value, str):
        return (value, 0) if vocab.allows(key, value) else (None, 1)
    if isinstance(value, (list, tuple)):
        items: list[Any] = []
        dropped = 0
        for item in value:
            if isinstance(item, str) and not vocab.allows(key, item):
                dropped += 1
                continue
            kept, n = _vocab_only(vocab, key, item)
            items.append(kept)
            dropped += n
        return items, dropped
    if isinstance(value, Mapping):
        out: dict[Any, Any] = {}
        dropped = 0
        for sub, item in value.items():
            child = vocab.child(key, sub)
            if child is None:
                dropped += 1
                continue
            out[sub], n = _vocab_only(vocab, child, item)
            dropped += n
        return out, dropped
    return copy.deepcopy(value), 0


def closed_field(key: Any, *, args: bool = False, source: bool = False,
                 bench: bool = False) -> str | None:
    """이 위치의 칸 `key`가 닫힌 어휘 칸이면 그 어휘 칸 이름, 아니면 None — 치환기 `tree`와
    관문 `_token_leaves`가 같이 쓴다(대칭).

    소스 id 칸(`SOURCE_ID_KEYS`)·소스 맥락 안 칸(`source` · `SOURCE_CONTEXT_FIELDS`)·보기 id 칸은
    인자 묶음(`args`) 아래에서도 닫힌다. 그 밖 `SKIP_KEYS`는 인자 묶음 아래에서 면제가 없고(None —
    일반 치환·토큰 대조), 벤치 소유 칸은 면제 위치(`bench`)가 아니면 빈 어휘(`BENCH_NESTED`)다.
    """
    if not isinstance(key, str):
        return None
    if key in SOURCE_ID_KEYS:
        return "source_ref"
    if source and key in SOURCE_CONTEXT_FIELDS:
        return SOURCE_CONTEXT_FIELDS[key]
    if key not in SKIP_KEYS:
        return None
    if key in VIEW_KEYS:
        return key
    if args:
        return None
    if key in BENCH_KEYS and not bench:
        return BENCH_NESTED
    return key


# --- GUID 모양 생성기 ---------------------------------------------------------------------


class _HexIds:
    """GUID 모양 값의 16진 형식 보존 가짜 값(run 메모리 전용 · 2단계)."""

    def __init__(self) -> None:
        self.collecting = False
        self._originals: set[str] = set()
        self._memo: dict[str, str | None] = {}
        self._issued: set[str] = set()

    def __repr__(self) -> str:
        return f"<_HexIds 가짜 {len(self._issued)}건>"

    def fake(self, text: str) -> str:
        key = text.lower()
        if self.collecting:
            self._originals.add(key)
            return MASK
        if key not in self._memo:
            self._memo[key] = self._draw(key)
        out = self._memo[key]
        if out is None:
            return MASK
        return out.upper() if text.isupper() else out

    def _draw(self, key: str) -> str | None:
        self._originals.add(key)
        for _ in range(_MAX_DRAWS):
            candidate = "".join(
                ch if ch == "-" else secrets.choice("0123456789abcdef") for ch in key
            )
            if (
                candidate not in self._originals
                and candidate not in self._issued
                and not _pii_regex_hit(candidate)  # 관문 `pii_regex`에 걸릴 모양은 재추첨
            ):
                self._issued.add(candidate)
                return candidate
        return None

    def is_fake(self, text: str) -> bool:
        return text.lower() in self._issued


# --- 치환 ---------------------------------------------------------------------------------


class SanitizedRun(NamedTuple):
    """치환된 run — 원값은 없다(가짜 값 · 가림 표지만)."""

    records: list[TurnRecord]
    probe_rows: list[dict[str, Any]]
    run_meta: dict[str, Any]


@dataclass
class _Originals:
    names: set[str]
    ids: set[str]
    accounts: set[str]


def _strings(value: Any) -> list[str]:
    if isinstance(value, bool) or value is None:
        return []
    if isinstance(value, (str, int)):
        text = str(value)
        return [text] if text.strip() and "{{" not in text and text != MASK else []
    if isinstance(value, (list, tuple)):
        return [s for item in value for s in _strings(item)]
    return []


def _target_names(value: Any) -> list[str]:
    """`targets`(문자열 목록 또는 `{text, candidates, selected}` 목록) → 이름."""
    out: list[str] = []
    for item in value if isinstance(value, list) else [value]:
        if isinstance(item, Mapping):
            for key in ("text", "name", "candidates", "selected"):
                out += _strings(item.get(key))
        else:
            out += _strings(item)
    return out


def _scan(node: Any, found: _Originals) -> None:
    """키 이름으로 원값을 모은다(재귀)."""
    if isinstance(node, Mapping):
        if node.get("field") == "targets":  # 정제 변경(SanitizeChange)의 대상 칸
            for side in ("before", "after"):
                found.names.update(_target_names(node.get(side)))
        for key, value in node.items():
            if key == "targets":
                found.names.update(_target_names(value))
            elif key in NAME_KEYS:
                found.names.update(_strings(value))
            elif key in ID_KEYS:
                found.ids.update(_strings(value))
            elif key in ACCOUNT_KEYS:
                found.accounts.update(_strings(value))
            _scan(value, found)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _scan(item, found)


def _placeholder_originals(placeholders: Mapping[str, Any], found: _Originals) -> None:
    for name, value in placeholders.items():
        kind = placeholder_kind(str(name))
        if kind in NAME_PLACEHOLDER_KINDS:
            found.names.update(_strings(value))
        elif kind in ID_PLACEHOLDER_KINDS:
            found.ids.update(_strings(value))
        elif kind in ACCOUNT_PLACEHOLDER_KINDS:
            found.accounts.update(_strings(value))


def collect_originals(
    records: Sequence[Mapping[str, Any]],
    *,
    inventory_names: Iterable[str],
    probe_rows: Sequence[Mapping[str, Any]] = (),
    placeholders: Mapping[str, Any] | None = None,
) -> tuple[set[str], set[str], set[str]]:
    """run 원값 전수 — (이름, GUID·txid, 계정).

    이름: 자리표(이름 종류) 값 · `observed.targets[].text/candidates/selected` ·
    `calls[].hostname` ·
    인자 이름 칸(`NAME_KEYS`) · `inventory_names`(드라이버가 넘기는 run 인벤토리 이름 전부).
    `placeholders`(자리표 파일 전체 — 관문과 같은 원값)를 주면 이번 run 레코드가 쓰지 않은 자리표
    값도 등록한다 — 다른 턴 값이 응답에만 나와도(예 목 프로파일 행의 URL) 치환된다.
    """
    found = _Originals(set(_strings(list(inventory_names))), set(), set())
    _placeholder_originals(placeholders or {}, found)
    for record in records:
        _placeholder_originals(record.get("placeholders") or {}, found)
        _scan({k: v for k, v in record.items() if k != "placeholders"}, found)
    for row in probe_rows:
        _scan(row, found)
    return found.names, found.ids, found.accounts


class RunSanitizer:
    """run 하나의 치환기 — 가짜 값 대응은 이 객체 메모리에만 있다(run 이 끝나면 버린다).

    Args:
        term_policy: 일반 단어 허용 목록(없으면 빈 목록 — 전부 치환)
        canaries: 카나리아 문자열 — 산출물에 나타나면 가짜 값으로 바꾸고(이중 방어) 생성기가 피한다
        user_values: 사용자 정보(종류 → 값 · `run_user_values()`) — 관문 `user_info`와 같은 값
            (3자 이상)을 대소문자 무시로 자유 문장 칸에서 `USER_INFO_MARK`로 바꾼다. 원이름·ID·
            토큰 치환이 끝난 뒤 맨 마지막에 적용한다
        vocab: 코드 어휘 칸 어휘(없으면 저장소 레지스트리로 만든다 — `load_code_vocab`)
        protected_words: 토큰 사전에서 뺄 낱말의 재료(코드 어휘 `vocab`에 더해 — 호출 측은 안전
            요약 어휘 `literals | fields`를 넘긴다 · `vocab_words`). 관문(`make_gate`)도 이 치환기의
            보호 낱말로 같은 토큰을 뺀다(대칭)
    """

    def __init__(
        self, *, term_policy: TermPolicy | None = None, canaries: Iterable[str] = (),
        user_values: Mapping[str, str | None] | None = None,
        vocab: CodeVocab | None = None,
        protected_words: Iterable[str] = (),
    ) -> None:
        self.vocab = vocab or load_code_vocab()
        self.protected: frozenset[str] = vocab_words([*self.vocab.strings(), *protected_words])
        # 긴 값 먼저(홈 경로가 OS 사용자를 품는다) · 같은 값(대소문자 무시)은 앞 종류 하나만
        pairs: dict[str, tuple[str, str]] = {}
        for kind, value in (user_values or {}).items():
            if value and len(str(value)) >= 3:
                pairs.setdefault(str(value).casefold(), (kind, str(value)))
        self._user_values: list[tuple[str, re.Pattern[str]]] = [
            (kind, re.compile(re.escape(value), re.IGNORECASE))
            for kind, value in sorted(pairs.values(), key=lambda kv: -len(kv[1]))
        ]
        self.user_info_counts: dict[str, int] = {kind: 0 for kind, _ in self._user_values}
        self.policy = term_policy or TermPolicy()
        # 관문 `pii_regex`(연락처·계좌 모양 등)에 걸릴 가짜 값은 재추첨한다
        self.fakes = FakeValues(reject=_pii_regex_hit)
        self.vault = PiiVault(tuple(canaries), fakes=self.fakes)
        self.hex_ids = _HexIds()
        self.names: frozenset[str] = frozenset()
        self.ids: frozenset[str] = frozenset()
        self.accounts: frozenset[str] = frozenset()
        self.tokens: frozenset[str] = frozenset()
        self._kinds: dict[str, str] = {}
        self._struct_re: re.Pattern[str] = re.compile(r"(?!)")
        self._free_re: re.Pattern[str] = re.compile(r"(?!)")
        self._used = False

    def __repr__(self) -> str:
        return f"<RunSanitizer 이름 {len(self.names)}건 · ID {len(self.ids)}건>"

    # --- 진입 ---------------------------------------------------------------------------

    def sanitize(
        self,
        records: Sequence[TurnRecord],
        *,
        inventory_names: Iterable[str],
        probe_rows: Sequence[Mapping[str, Any]] = (),
        run_meta: Mapping[str, Any] | None = None,
        placeholders: Mapping[str, Any] | None = None,
    ) -> SanitizedRun:
        """run 전체를 2단계로 치환한다(입력은 바꾸지 않는다 · run 하나에 한 번만).

        ①원값 전수 수집 → ②`collecting()` 안에서 같은 경로를 돌려 원값만 등록 → ③발급.
        """
        if self._used:
            raise RuntimeError("RunSanitizer 는 run 하나에 한 번만 쓴다(2단계 등록 일관)")
        self._used = True
        names, ids, accounts = collect_originals(
            records, inventory_names=inventory_names, probe_rows=probe_rows,
            placeholders=placeholders,
        )
        self._index(names, ids, accounts)
        meta = dict(run_meta or {})
        with self._collecting():
            for name in sorted(names):
                self.fakes.fake(name)  # 이름 전체도 원값으로 등록(부분 문자열 대조)
                self._fake_name(name)
            for token in sorted(self.tokens):
                self.fakes.fake(token)  # 결합 소문자형(`ordpay`)도 원값 — 가짜 값이 피한다
            for value in sorted(ids):
                self._fake_id(value)
            self.fakes.register(sorted(accounts))
            self._run(records, probe_rows, meta)
        return SanitizedRun(*self._run(records, probe_rows, meta))

    @contextlib.contextmanager
    def _collecting(self) -> Iterator[None]:
        self.hex_ids.collecting = True
        try:
            with self.fakes.collecting():
                yield
        finally:
            self.hex_ids.collecting = False

    def _index(self, names: set[str], ids: set[str], accounts: set[str]) -> None:
        kinds: dict[str, str] = {}
        changed = {n for n in names if len(n.strip()) >= 2 and self.policy.changes(n)}
        tokens = protect_tokens(name_tokens(changed, self.policy), self.protected)
        self.tokens = frozenset(tokens)
        for word in tokens:
            kinds[word] = "token"
        for word in changed:
            kinds[word.lower()] = "name"
        for word in ids:
            kinds[word.lower()] = "id"
        for word in accounts:
            kinds[word.lower()] = "account"
        self._kinds = kinds
        self.names = frozenset(changed)
        self.ids = frozenset(ids)
        self.accounts = frozenset(accounts)
        struct = [w for w, k in kinds.items() if k != "token" and len(w) >= 2]
        self._struct_re = self._compile(struct)
        self._free_re = self._compile([w for w in kinds if len(w) >= 2])

    @staticmethod
    def _compile(words: list[str]) -> re.Pattern[str]:
        dictionary = _dictionary_pattern(words)
        parts = [
            f"(?P<secret>{_JWT}|{_BEARER})",
            f"(?P<sql>{_SQL})",
            f"(?P<uuid>{_UUID.pattern})",
        ]
        if dictionary:
            parts.append(f"(?P<word>{dictionary})")
        return re.compile("|".join(parts), re.IGNORECASE)

    # --- 값 단위 ------------------------------------------------------------------------

    def _fake_tokens(self, text: str) -> str:
        out = []
        for piece, is_token in split_name(text):
            if not is_token or self.policy.allows(piece):
                if is_token and self.hex_ids.collecting:
                    self.fakes.register([piece])  # 허용 단어도 원값 — 가짜 토큰이 피한다
                out.append(piece)
            else:
                out.append(self.fakes.fake(piece))
        return "".join(out)

    def _fake_name(self, text: str) -> str:
        """이름 → 토큰 단위 형식 보존 가짜 이름(IPv4는 계층 일관 가짜 IP)."""
        out, cursor = [], 0
        for match in _IPV4.finditer(text):
            out.append(self._fake_tokens(text[cursor : match.start()]))
            out.append(self.fakes.fake_ip(match.group(0)))
            cursor = match.end()
        out.append(self._fake_tokens(text[cursor:]))
        return "".join(out)

    def _fake_id(self, text: str) -> str:
        if _UUID.fullmatch(text):
            return self.hex_ids.fake(text)
        return self.fakes.fake(text)

    def _replace(self, match: re.Match[str]) -> str:
        text = match.group(0)
        if match.group("secret") is not None:
            return text.split()[0] + " " + MASK if text[:1] in "bB" else MASK
        if match.group("sql") is not None:
            return text.split()[0] + " " + SQL_MASK
        if match.group("uuid") is not None:
            return self.hex_ids.fake(text)
        kind = self._kinds.get(text.lower(), "name")
        if kind == "id":
            return self._fake_id(text)
        if kind == "account":
            return mask_identifier(text)
        return self._fake_name(text)

    def text(self, text: str, *, free: bool = False, prompt: str = "",
             tokens: bool | None = None) -> str:
        """문자열 하나 — 사전·비밀·SQL·GUID 구간은 바꾸고, 그 밖 구간만 ITAM 가림으로 넘긴다.

        `free`(자유 문장)면 나머지를 `redact_text`로, 아니면 `scrub_tree`로 넘긴다. `tokens`(기본 =
        `free`)면 이름 토큰까지 사전에 넣는다(아니면 원이름·ID·계정만). 이번 호출이 바꾼 구간은
        다시 처리하지 않는다(출처 기준).
        """
        if free:
            text = _normalize_free(text)
        regex = self._free_re if (free if tokens is None else tokens) else self._struct_re
        pieces, cursor = [], 0
        for match in regex.finditer(text):
            pieces.append(self._rest(text[cursor : match.start()], free, prompt))
            pieces.append(self._replace(match))
            cursor = match.end()
        pieces.append(self._rest(text[cursor:], free, prompt))
        return "".join(pieces)

    def _rest(self, segment: str, free: bool, prompt: str) -> str:
        if not segment:
            return segment
        if free:
            limit = len(segment) * 2 + ERROR_TEXT_MAX
            return redact_text(segment, vault=self.vault, prompt=prompt, limit=limit)
        return str(scrub_tree(segment, self.vault))

    def _prompt_basis(self, prompt: Any) -> str:
        """`redact_text` 프롬프트 근거 — 사전 구간을 지운 원 프롬프트(두 단계에서 같은 값)."""
        return self._free_re.sub(" ", _normalize_free(str(prompt or "")))

    def tree(self, node: Any, *, prompt: str = "", tokens: bool = True,
             sentence: bool = False, args: bool = False, source: bool = False,
             bench: bool = False) -> Any:
        """중첩 구조 — 키는 원이름·ID·계정만, 문자열 값은 토큰 사전까지(`tokens`) 바꾼다. 문장 칸
        (`SENTENCE_KEYS`) 값은 `redact_text`까지 · 닫힌 어휘 칸(`closed_field`)은 어휘 대조
        (`_skip`) · 시나리오 어휘 칸(`SCENARIO_KEYS`) 아래는 토큰 사전 없이. 인자 묶음(`ARG_KEYS` —
        `args`) 아래에서는 보기·소스 id 칸 말고는 이름 면제가 없다. 소스 맥락(`SOURCE_CONTEXT_KEYS`
        — `source`) 아래 소스 칸은 소스 어휘로 닫힌다. `bench`는 이 사전의 키(한 단계)만 벤치 소유
        칸 면제 위치다."""
        if isinstance(node, Mapping):
            out: dict[Any, Any] = {}
            for key, value in node.items():
                vkey = closed_field(key, args=args, source=source, bench=bench)
                if vkey is not None:
                    out.update(self._skip(key, value, vkey))
                    continue
                new_key = self.text(key) if isinstance(key, str) else key
                out[new_key] = self.tree(value, prompt=prompt,
                                         tokens=tokens and key not in SCENARIO_KEYS,
                                         sentence=key in SENTENCE_KEYS,
                                         args=args or key in ARG_KEYS,
                                         source=source or key in SOURCE_CONTEXT_KEYS)
            return out
        if isinstance(node, (list, tuple)):
            return [self.tree(item, prompt=prompt, tokens=tokens, sentence=sentence, args=args,
                              source=source, bench=bench)
                    for item in node]
        if isinstance(node, str):
            return self.text(node, free=sentence and tokens, prompt=prompt, tokens=tokens)
        return copy.deepcopy(node)

    def _skip(self, key: str, value: Any, vkey: str | None = None) -> dict[str, Any]:
        """코드 어휘 칸 — 칸별 어휘(`CodeVocab` · 어휘 칸 `vkey` — 기본 `key`) 안 값만 남기고 버린
        건수를 `<키>_dropped`에 싣는다(없으면 칸을 더하지 않는다)."""
        kept, dropped = _vocab_only(self.vocab, vkey or key, value)
        return {key: kept, **({f"{key}{DROPPED_SUFFIX}": dropped} if dropped else {})}

    def _user_info(self, text: str) -> str:
        """자유 문장 하나의 사용자 정보 → `USER_INFO_MARK`(대소문자 무시 · 발급 단계만 센다).

        원이름·ID·토큰 치환(`text`)이 끝난 결과에 맨 마지막으로 적용한다 — 먼저 돌면 운영 이름
        조각을 표지로 바꿔 원이름 치환·토큰 대조 신호를 가린다.
        """
        for kind, pattern in self._user_values:
            text, hits = pattern.subn(USER_INFO_MARK, text)
            if hits and not self.hex_ids.collecting:
                self.user_info_counts[kind] += hits
        return text

    def _free(self, text: str, prompt: str) -> str:
        """관측 자유 문장 칸 — 사전·비밀·SQL·GUID 치환 + `redact_text` 뒤 사용자 정보."""
        return self._user_info(self.text(text, free=True, prompt=prompt))

    # --- 레코드 -------------------------------------------------------------------------

    def _run(
        self,
        records: Sequence[TurnRecord],
        probe_rows: Sequence[Mapping[str, Any]],
        run_meta: Mapping[str, Any],
    ) -> tuple[list[TurnRecord], list[dict[str, Any]], dict[str, Any]]:
        out_records = [self._record(r) for r in records]
        out_probe = [self.tree(row, bench=True) for row in probe_rows]
        meta: dict[str, Any] = {}
        for key, value in run_meta.items():
            if key in RUN_USER_FIELDS and value is not None:
                meta[key] = self.fakes.fake(str(value))  # 관문 `substitution`: 값 전체 가짜 값
            else:
                meta[key] = self.tree(value)
        return out_records, out_probe, meta

    def _record(self, record: TurnRecord) -> TurnRecord:
        prompt = self._prompt_basis(record.get("prompt"))
        out: dict[str, Any] = {}
        for key, value in record.items():
            if key in ("prompt", "prompt_template") and isinstance(value, str):
                out[key] = self.text(value, free=True, prompt=prompt)
            elif key == "observed" and isinstance(value, Mapping):
                out[key] = self._observed(value, bool(record.get("sensitive")), prompt)
            elif (vkey := closed_field(key, bench=True)) is not None:
                out.update(self._skip(key, value, vkey))
            else:
                out[key] = self.tree(value, prompt=prompt, tokens=key not in SCENARIO_KEYS,
                                     bench=key == PROBE_KEY)
        return out  # type: ignore[return-value]

    def _observed(
        self, observed: Mapping[str, Any], sensitive: bool, prompt: str
    ) -> dict[str, Any]:
        source = dict(observed)
        if sensitive:
            source.update(sensitive_shape(source))
        out: dict[str, Any] = {}
        for key, value in source.items():
            if key in OBSERVED_FREE_TEXT and isinstance(value, str):
                out[key] = self._free(value, prompt)
            elif key == "calls" and isinstance(value, list):
                out[key] = [self._call(item, prompt) for item in value]
            elif key == "answer_lines" and isinstance(value, list):
                out[key] = [self._free(str(line), prompt) for line in value]
            elif key == "disclosures" and isinstance(value, list):
                out[key] = [self._disclosure(item, prompt) for item in value]
            elif key == "reselect" and isinstance(value, Mapping):
                out[key] = self.tree(reselect_keys(value), prompt=prompt)
            elif (vkey := closed_field(key)) is not None:
                out.update(self._skip(key, value, vkey))
            else:
                out[key] = self.tree(value, prompt=prompt, source=key in SOURCE_CONTEXT_KEYS)
        return out

    def _disclosure(self, item: Any, prompt: str) -> Any:
        return self._with_free(item, "text", prompt)

    def _call(self, item: Any, prompt: str) -> Any:
        """호출 하나 — 오류 문장(`error`)은 자유 문장 처리(사용자 정보 치환 포함 ·
        plans/164 L-3)."""
        return self._with_free(item, "error", prompt)

    def _with_free(self, item: Any, free_key: str, prompt: str) -> Any:
        """사전 하나 — `free_key` 문자열 값만 `_free`, 나머지 칸은 `tree`(코드 어휘 칸 포함)."""
        if not isinstance(item, Mapping):
            return self.tree(item, prompt=prompt)
        out: dict[Any, Any] = {}
        for key, value in item.items():
            if key == free_key and isinstance(value, str):
                out[key] = self._free(value, prompt)
            else:
                out.update(self.tree({key: value}, prompt=prompt))
        return out


def reselect_keys(reselect: Mapping[str, Any]) -> dict[str, Any]:
    """재선택 관측 — 보기 후보 키를 보기 id 칸 이름으로(`candidates` → `candidate_views`).

    `target.candidates`(인스턴스 원이름)는 바꾸지 않는다 — `candidates`를 `SKIP_KEYS`에 넣으면
    원이름이 치환·토큰 대조를 건너뛴다(plans/164 W1).
    """
    return {("candidate_views" if key == "candidates" else key): value
            for key, value in reselect.items()}


#: 사용자 정보 충돌 대상 표시(`UserInfoConflictError` — 값 없이 무엇과 겹쳤는지만).
CONFLICT_INVENTORY = "인벤토리 이름·토큰"
CONFLICT_VOCAB = "코드 어휘·산출물 키 이름"


class UserInfoConflictError(Exception):
    """사용자 정보 값이 인벤토리 이름·토큰 또는 코드 어휘·산출물 키 이름과 겹친다 — 종류와 겹친
    대상만 싣는다(값 없음 · plans/164 W1)."""

    def __init__(self, kinds: Sequence[str],
                 against: Sequence[str] = (CONFLICT_INVENTORY,)) -> None:
        self.kinds = tuple(kinds)
        self.against = tuple(against)
        super().__init__(
            "사용자 정보가 " + " · ".join(self.against) + "과 겹친다 — 종류 "
            + "·".join(self.kinds)
            + " · 해결: 운영 이름·코드 어휘와 겹치지 않는 전용 실행 계정·호스트에서 다시 실행한다"
            "(로그인 계정·OS 사용자·호스트 짧은 이름·홈 디렉터리 이름 확인)")


def _user_probe(kind: str, value: str) -> str:
    """충돌 대조 값 — 호스트는 첫 라벨(짧은 이름 · DNS 접미 제외), 홈은 마지막 경로 성분, 그 밖은
    값 전체(사용자명)."""
    if kind == "host":
        return value.split(".", 1)[0]
    if kind == "home":
        return re.split(r"[\\/]", value.rstrip("/\\"))[-1]
    return value


def user_info_conflicts(
    user_values: Mapping[str, str | None],
    inventory_names: Iterable[str],
    policy: TermPolicy | None = None,
) -> list[str]:
    """사용자 정보와 인벤토리 이름·토큰이 겹치는 종류(정렬 · 값 없음).

    대조 값(`_user_probe`)은 사용자명 전체 · 호스트 첫 라벨 · 홈 마지막 성분이다 — 점으로 나뉜 DNS
    접미 라벨(`corp.example.co.kr`)은 운영 이름과 공유해도 충돌이 아니다(같은 대조 값은 앞 종류
    하나로). 3자 미만은 건너뛴다. 겹침 = 대조 값이 인벤토리 이름의 부분 문자열(대소문자 무시 —
    토큰은 이름의 조각이라 이름 대조가 토큰 대조를 품는다)이거나, 그 반대로 인벤토리 이름·토큰이
    대조 값 안에서 치환기와 같은 의미(ASCII 는 영숫자 경계 · 대소문자 무시)로 잡히는 경우다. 겹치면
    자유 문장의 사용자 정보 치환과 원이름·토큰 치환이 서로의 신호를 가린다.
    """
    names = sorted({n.strip() for n in inventory_names if len(n.strip()) >= 2})
    folded = [n.casefold() for n in names]
    regex = _names_regex([*names, *name_tokens(names, policy or TermPolicy())])
    kinds: set[str] = set()
    seen: set[str] = set()
    for kind, value in user_values.items():
        text = _user_probe(kind, str(value or ""))
        if len(text) < 3 or text.casefold() in seen:
            continue
        seen.add(text.casefold())
        if any(text.casefold() in name for name in folded) or (
            regex is not None and regex.search(text)
        ):
            kinds.add(kind)
    return sorted(kinds)


def user_info_vocab_conflicts(user_values: Mapping[str, str | None],
                              words: Iterable[str]) -> list[str]:
    """사용자 정보 대조 값(`_user_probe`)이 코드 어휘·산출물 키 이름(`words`) 안에 부분 문자열로
    들어 있는 종류(정렬 · 값 없음 · plans/164 W1 N-2).

    관문 `user_info`는 산출물 잎 전부(키 포함)를 사용자 정보 값의 부분 문자열로 본다 — 대조 값이
    보기 id·분류·키 이름(`was_instance`·`split` 값 등)의 조각이면 정상 run 도 끝에서 관문이 막는다.
    대소문자 무시로 대조한다(관문보다 넓다 — 턴 송신 전에 닫힌 쪽으로 멈춘다). 3자 미만은 건너뛴다.
    """
    folded = sorted({str(w).casefold() for w in words if w})
    kinds: set[str] = set()
    for kind, value in user_values.items():
        text = _user_probe(kind, str(value or "")).casefold()
        if len(text) >= 3 and any(text in word for word in folded):
            kinds.add(kind)
    return sorted(kinds)


def unregistered_sources(records: Iterable[Mapping[str, Any]], meta: Mapping[str, Any],
                         source_ids: Iterable[str]) -> list[str]:
    """감사 줄 소스(`calls[].sources` · 가드 `calls_by_source` 키) 중 레지스트리 밖 소스 id(정렬).

    E2 게이트웨이 `.env`(`JENNIFER_SOURCES`) 소스는 레지스트리 밖일 수 있다 — 호출 측이 관문 원이름
    대조 집합(`inventory_names`)에 합쳐 치환기·관문이 원이름처럼 다루게 한다(plans/164 W1 N-1).
    """
    known = set(source_ids)
    found: set[str] = set()
    for record in records:
        observed = record.get("observed")
        calls = observed.get("calls") if isinstance(observed, Mapping) else None
        for call in calls if isinstance(calls, list) else []:
            if isinstance(call, Mapping):
                found |= {s for s in call.get("sources") or [] if isinstance(s, str)}
    guard = meta.get("guard") or {}
    if isinstance(guard, Mapping):
        found |= {s for s in (guard.get("calls_by_source") or {}) if isinstance(s, str)}
    return sorted(s for s in found - known if len(s.strip()) >= 2 and s != "?")


def sensitive_shape(observed: Mapping[str, Any]) -> dict[str, Any]:
    """민감 시나리오(환경변수·로드된 클래스·사용자 목록 계열) — 응답은 모양만(키 이름·건수).

    `response` → 표지(길이는 `response_len`) · `answer_lines` → 줄 수 유지, `키=값`·`키: 값`
    줄은 키만(`키=<가림>`), 그 밖 줄은 `<가림>`. 호출 결과는 원래 행 수·오류 유형만 싣는다.
    """
    out: dict[str, Any] = {}
    if "response" in observed:
        response = str(observed.get("response") or "")
        out["response"] = SENSITIVE_RESPONSE
        if "response_len" not in observed:
            out["response_len"] = len(response)
    if isinstance(observed.get("answer_lines"), list):
        lines = []
        for line in observed["answer_lines"]:
            match = _KEY_LINE.match(str(line))
            lines.append(f"{match.group(1)}={MASK}" if match else MASK)
        out["answer_lines"] = lines
    return out


# --- 근거 묶음 ----------------------------------------------------------------------------


def _v(container: Any, key: str) -> Any:
    """관측 칸 → 근거 값(칸 없음 = None · 비었음 = `NONE`)."""
    if not isinstance(container, Mapping) or key not in container:
        return None
    value = container[key]
    if value is None or value == "" or value == [] or value == {}:
        return NONE
    return value


def _or_none(value: Any) -> Any:
    return NONE if value in (None, "", [], {}) else value


def _per_call(calls: Any, pick: Any) -> Any:
    if calls is None:
        return None
    if calls == NONE:
        return NONE
    return _or_none([pick(i, c) for i, c in enumerate(calls) if isinstance(c, Mapping)])


def _llm_raw(plan_raw: Any) -> Any:
    if plan_raw is None or plan_raw == NONE:
        return plan_raw
    keys = ("agent", "views", "view_args", "targets", "sources")
    return [{k: t.get(k) for k in keys} for t in plan_raw if isinstance(t, Mapping)]


def _fewshot_copy(value: Any) -> Any:
    """`fewshot_copy` — 보기·예문 번호·인자만(예문 원문은 반출하지 않는다)."""
    if not isinstance(value, Mapping):
        return value
    return {k: value.get(k) for k in ("view", "example_index", "args")}


#: 실패 턴 근거 묶음 필수 칸(점 경로).
REQUIRED_EVIDENCE: tuple[str, ...] = (
    "question.prompt",
    "question.expect",
    "question.covers",
    "question.split",
    "decompose.apm_section.fingerprint",
    "decompose.apm_section.chars",
    "decompose.view_rows",
    "decompose.llm_raw",
    "sanitize.changes",
    "reselect",
    "target.texts",
    "target.candidates",
    "target.selected",
    "target.not_found",
    "window.sent_at",
    "window.resolved",
    "window.tool_args",
    "execution.tools",
    "execution.rows",
    "execution.empty",
    "execution.errors",
    "execution.probe",
    "response.answer_lines",
    "response.disclosures",
    "response.clarify_text",
    "response.length",
    "classification.categories",
    "classification.fewshot_fixable",
    "classification.locus",
    "classification.fewshot_copy",
    "versions.code_sha",
    "versions.F1",
    "versions.F2",
    "versions.F3",
    "versions.F4",
    "versions.registry",
)
#: 판정 불가 턴(턴 오류·관측 0)은 관측 칸이 원래 없다 — 질문·분류 사유·버전만 본다.
REQUIRED_EVIDENCE_UNJUDGED: tuple[str, ...] = (
    *(p for p in REQUIRED_EVIDENCE if p.startswith(("question.", "versions."))),
    "classification.unjudged",
)


def build_evidence(record: Mapping[str, Any], *, versions: Mapping[str, Any]) -> dict[str, Any]:
    """턴 하나의 근거 묶음(§3.8) — **치환을 거친 레코드**로 만든다.

    실패 턴은 전체(`level: full`), 통과 턴은 요약(`summary`), 건너뛴 턴은 사유만(`skipped`).

    Args:
        record: 치환된 `TurnRecord`(판정 `verdict`가 붙은 것)
        versions: `{"code_sha": SHA, "fingerprints": fingerprints.fewshot_fingerprints() 결과,
            "apm_section": {"fingerprint", "chars", "view_rows"}}`
    """
    verdict = record.get("verdict") or {}
    observed = record.get("observed") or {}
    prints = versions.get("fingerprints") or {}
    if verdict.get("skipped"):
        return {"level": "skipped", "reason": verdict.get("skipped")}
    calls = _v(observed, "calls")
    if verdict.get("turn_pass") is True:
        return {
            "level": "summary",
            "axes": verdict.get("axes"),
            "tools": _per_call(calls, lambda _i, c: c.get("tool")),
            "response_len": observed.get("response_len"),
            "elapsed_ms": observed.get("elapsed_ms"),
            "code_sha": versions.get("code_sha"),
        }
    expect = record.get("expect") or {}
    targets = _v(observed, "targets")
    target_list = targets if isinstance(targets, list) else []

    def per_target(key: str) -> Any:
        if targets is None or targets == NONE:
            return targets
        return [t.get(key) for t in target_list if isinstance(t, Mapping)]

    reselect = _v(observed, "reselect")
    if isinstance(reselect, Mapping):
        # 보기 id 칸은 코드 어휘 키 이름 그대로 싣는다(plans/164 W1 — 일반 이름 `output`은 토큰
        # 대조를 받아 보기 id 낱말이 운영 이름 토큰과 겹치면 거짓 양성)
        source = reselect_keys(reselect)
        dropped = {f"{k}{DROPPED_SUFFIX}": source[f"{k}{DROPPED_SUFFIX}"]
                   for k in ("candidate_views", "parsed_views", "result_views")
                   if source.get(f"{k}{DROPPED_SUFFIX}")}
        reselect = {
            "happened": source.get("happened"),
            "candidate_views": _or_none(source.get("candidate_views")),
            "parsed_views": _or_none(source.get("parsed_views")),
            "parsed_view_args": _or_none(source.get("parsed_view_args")),
            "result_views": _or_none(source.get("result_views")),
            "reason": _or_none(source.get("reason")),
            **dropped,
        }
    disclosures = _v(observed, "disclosures")
    section = versions.get("apm_section") or {}
    answer_lines = observed.get("answer_lines")
    return {
        "level": "full",
        "question": {
            "prompt": record.get("prompt") or None,
            "expect": {
                "source": {"agents": expect.get("agents"),
                           "apm_sources": expect.get("apm_sources")},
                "views": expect.get("views"),
                "target": expect.get("target"),
                "window": expect.get("window"),
                "args": expect.get("args"),
                "clarify": expect.get("clarify"),
                "refusal": expect.get("refusal"),
            } if expect else None,
            "covers": _v(record, "covers"),
            "split": record.get("split") or None,
        },
        "decompose": {
            "apm_section": {"fingerprint": _v(section, "fingerprint"),
                            "chars": _v(section, "chars")},
            "view_rows": _v(section, "view_rows"),
            "llm_raw": _llm_raw(_v(observed, "plan_raw")),
            "agents": _v(observed, "agents"),
        },
        "sanitize": {"changes": _v(observed, "sanitize_changes")},
        "reselect": reselect,
        "target": {
            "texts": per_target("text"),
            "kinds": per_target("kind"),
            "candidates": per_target("candidates"),
            "selected": per_target("selected"),
            "not_found": None if targets is None else _or_none(
                [t.get("text") for t in target_list
                 if isinstance(t, Mapping) and t.get("resolved") is False]
            ),
            "untargeted": observed.get("untargeted"),
        },
        "window": {
            "sent_at": record.get("sent_at") or None,
            "resolved": _v(observed, "time_resolution"),
            "call_windows": _per_call(calls, lambda _i, c: c.get("window")),
            "tool_args": _per_call(calls, lambda _i, c: c.get("args")),
        },
        "execution": {
            "tools": _per_call(calls, lambda _i, c: c.get("tool")),
            "rows": _per_call(calls, lambda _i, c: c.get("rows")),
            "empty": None if calls is None else _or_none(
                [i for i, c in enumerate(calls if isinstance(calls, list) else [])
                 if isinstance(c, Mapping) and c.get("rows") == 0 and not c.get("error")]
            ),
            "errors": _per_call(calls, lambda _i, c: c.get("error") or NONE),
            "probe": _v(record, "probe"),
            "apm_called": observed.get("apm_called"),
            "turn_error": observed.get("error"),
        },
        "response": {
            "answer_lines": None if "answer_lines" not in observed else bool(answer_lines),
            "answer_line_count": len(answer_lines) if isinstance(answer_lines, list) else None,
            "disclosures": disclosures if not isinstance(disclosures, list) else [
                d.get("text") if isinstance(d, Mapping) else d for d in disclosures
            ],
            "clarify_kind": _v(observed, "clarify_kind"),
            "clarify_text": _v(observed, "clarify_text"),
            "refused": observed.get("refused"),
            "length": observed.get("response_len"),
            "degraded_reason": _v(observed, "degraded_reason"),
        },
        "classification": {
            "categories": _v(verdict, "categories"),
            "fewshot_fixable": _v(verdict, "fewshot_fixable"),
            "locus": _v(verdict, "locus"),
            "fewshot_copy": _fewshot_copy(_v(verdict, "fewshot_copy")),
            "axes": verdict.get("axes"),
            "unjudged": _v(verdict, "unjudged"),
        },
        "versions": {
            "code_sha": versions.get("code_sha") or None,
            **{key: _v(prints, key) for key in ("F1", "F2", "F3", "F4", "registry")},
        },
    }


def _at(node: Any, path: str) -> Any:
    for part in path.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return None
        node = node[part]
    return node


def check_evidence(records: Iterable[Mapping[str, Any]]) -> list[tuple[str, int, str]]:
    """실패 턴(통과·건너뜀 제외) 근거 묶음의 필수 칸 누락 — ``(scenario_id, turn, 칸)`` 목록.

    누락 = 칸 없음 · `null` · 빈 문자열(`"none"`·False·0은 값). 묶음이 `full`이 아니면 `level` 누락.
    """
    out: list[tuple[str, int, str]] = []
    for record in records:
        verdict = record.get("verdict") or {}
        if verdict.get("skipped") or verdict.get("turn_pass") is True:
            continue
        sid, turn = str(record.get("scenario_id")), int(record.get("turn") or 0)
        evidence = record.get("evidence") or {}
        if evidence.get("level") != "full":
            out.append((sid, turn, "level"))
            continue
        required = REQUIRED_EVIDENCE_UNJUDGED if verdict.get("unjudged") else REQUIRED_EVIDENCE
        for path in required:
            value = _at(evidence, path)
            if value is None or value == "":
                out.append((sid, turn, path))
    return out


# --- 관문 · 산출 --------------------------------------------------------------------------


class ApmLeakGate(LeakGate):
    """ITAM `LeakGate` + APM 규칙 — 같은 규칙 이름으로 보고한다(`GATE_RULES` 그대로).

    - `pii_value`: run 원이름(인벤토리·자리표 · 치환으로 바뀌는 것)·GUID/txid 원값(대소문자 무시 ·
      ASCII 는 영숫자 경계)
    - `user_info`: Bearer 토큰(JWT·계정은 기존 규칙)
    - `pii_regex`: 이 run 가짜 값이 아닌 GUID 모양(16진 8-4-4-4-12 · 32자리)
    - `pii_value`(토큰): `trace.jsonl`·`probe.jsonl`·`run.json`의 문자열 **값** 잎에 남은 이름 토큰
      (`name_tokens` — ASCII 는 영숫자 경계). 치환기와 같은 범위만 본다 — 키 · 닫힌 어휘 칸
      (`closed_field` — 인자 묶음 `ARG_KEYS` 아래는 보기·소스 id 칸만) · 시나리오 어휘 칸
      (`SCENARIO_KEYS`) 아래는 제외. `.md`는 원이름·ID·카나리아만 대조한다(저장소 어휘 — 보기
      id·경로 — 가 일반 토큰과 겹친다 · 본문은 치환된 레코드에서 만든다)
    - `pii_value`(코드 어휘): 같은 구조 파일에서 코드 어휘 칸 아래 문자열 잎·사전 키가
      칸별 어휘(`CodeVocab`) 밖이면 위반 — 이름 면제 칸이 자유 문자열·이름 조각을 실어
      나르지 못하게(plans/164 W1). 토큰 대조는 하지 않는다 — 어휘가 닫혀 있어 더 엄격하고,
      저장소 어휘(`was_instance`·`cm_prd` 등)가 운영 이름 토큰과 겹쳐 거짓 양성을 낸다
    """

    #: 토큰 대조 대상 파일(구조 파일 — 값 잎 단위).
    TOKEN_FILES: tuple[str, ...] = ("trace.jsonl", "probe.jsonl", "run.json")

    def __init__(
        self,
        *,
        policy: ColumnPolicy,
        vault: PiiVault,
        user_values: Mapping[str, str | None],
        fakes: FakeValues | None,
        originals: Iterable[str],
        hex_ids: _HexIds | None,
        tokens: Iterable[str] = (),
        vocab: CodeVocab | None = None,
    ) -> None:
        super().__init__(policy=policy, vault=vault, user_values=user_values, fakes=fakes)
        self._originals = _names_regex(o for o in originals if len(o.strip()) >= 2)
        self._hex_ids = hex_ids
        self._tokens = _names_regex(tokens)
        self._vocab = vocab or load_code_vocab()

    def _token_leaves(self, node: Any, path: str = "", parent: str = "", args: bool = False,
                      source: bool = False, norm: str = "") -> Iterable[str]:
        """토큰 대조 대상 값 잎의 경로(값은 돌려주지 않는다) — 닫힌 어휘 칸(`closed_field` —
        치환기 `tree`와 같은 판정) 아래는 토큰 대조 대신 어휘 검사(`_code_leaves`). 인자 묶음
        (`ARG_KEYS` — `args`) 아래는 보기·소스 id 칸만 어휘 검사이고 그 밖 칸 이름은 면제가 없다.
        벤치 소유 칸은 `norm`(목록 단계를 `[]`로 접은 경로)이 `BENCH_PATHS`일 때만 면제다."""
        if isinstance(node, Mapping):
            for position, (key, value) in enumerate(node.items()):
                if key in SCENARIO_KEYS:
                    continue
                label = self._label(str(key), position, parent)
                child = f"{path}.{label}" if path else label
                npath = f"{norm}.{key}" if norm else str(key)
                vkey = closed_field(key, args=args, source=source, bench=npath in BENCH_PATHS)
                if vkey is not None:
                    yield from self._code_leaves(value, child, str(key), vkey)
                    continue
                yield from self._token_leaves(value, child, str(key), args or key in ARG_KEYS,
                                              source or key in SOURCE_CONTEXT_KEYS, npath)
        elif isinstance(node, (list, tuple)):
            for position, value in enumerate(node):
                yield from self._token_leaves(value, f"{path}[{position}]", parent, args,
                                              source, f"{norm}[]")
        elif isinstance(node, str) and self._tokens is not None and self._tokens.search(node):
            yield path

    def _code_leaves(self, node: Any, path: str, parent: str, vkey: str) -> Iterable[str]:
        """코드 어휘 칸(`vkey` 어휘) 아래 문자열 잎·사전 키 중 어휘 밖인 것의 경로(plans/164 W1 —
        키 이름 면제가 자유 문자열·이름 조각을 실어 나르면 막는다 · 경로에 값은 없다)."""
        if isinstance(node, Mapping):
            for position, (key, value) in enumerate(node.items()):
                sub = self._vocab.child(vkey, key)
                if sub is None:
                    yield f"{path}.#{position}"  # 어휘 밖 키는 경로에도 쓰지 않는다
                    continue
                label = self._label(str(key), position, parent)
                yield from self._code_leaves(value, f"{path}.{label}", str(key), sub)
        elif isinstance(node, (list, tuple)):
            for position, value in enumerate(node):
                yield from self._code_leaves(value, f"{path}[{position}]", parent, vkey)
        elif isinstance(node, str) and not self._vocab.allows(vkey, node):
            yield path

    def check(self, files: Mapping[str, str]) -> list[dict[str, Any]]:
        violations = super().check(files)
        for name in self.TOKEN_FILES:
            text = files.get(name)
            if not text:
                continue
            if name.endswith(".jsonl"):
                units = [(n, line) for n, line in enumerate(text.splitlines(), start=1)
                         if line.strip()]
            else:
                units = [(None, text)]
            for number, body in units:
                try:
                    parsed = json.loads(body)
                except json.JSONDecodeError:
                    continue  # 형식 위반은 기존 규칙이 본다
                violations += [{"file": name, "record": number, "field": field or None,
                                "rule": "pii_value"} for field in self._token_leaves(parsed)]
        return violations

    def rules(self, text: str, *, schema_section: bool) -> list[str]:
        found = super().rules(text, schema_section=schema_section)
        if "pii_value" not in found and self._originals is not None and self._originals.search(
            text
        ):
            found.append("pii_value")
        if "user_info" not in found and _BEARER_RE.search(text):
            found.append("user_info")
        return found

    def leaf_rules(self, text: str, *, schema_section: bool) -> list[str]:
        found = super().leaf_rules(text, schema_section=schema_section)
        hex_ids = self._hex_ids
        if "pii_regex" not in found and any(
            hex_ids is None or not hex_ids.is_fake(m.group(0)) for m in _UUID.finditer(text)
        ):
            found.append("pii_regex")
        return found


def make_gate(
    *,
    inventory_names: Iterable[str],
    placeholders: Mapping[str, Any],
    user_values: Mapping[str, str | None],
    canaries: Iterable[str],
    sanitizer: RunSanitizer | None = None,
) -> ApmLeakGate:
    """누출 관문 — 카나리아 · run 인벤토리 원이름 전부·그 토큰 · 정규식 · 사용자 정보 대조.

    Args:
        inventory_names: run 인벤토리 이름 전부(드라이버가 게이트웨이 목록에서 모은 것)
        placeholders: 자리표 이름 → 원값(이름 종류 → 원이름 · `guid` → ID · `user` → 계정)
        user_values: 사용자 정보(로그인 계정·OS 사용자 등 — 3자 이상 부분 문자열 대조)
        canaries: 카나리아 문자열
        sanitizer: 이 run 치환기 — 가짜 값 등록부(IP·GUID 통과 판정 · 충돌 검사)와 치환기가 모은
            원값·허용 목록을 쓴다. 없으면 남은 IP·GUID 모양은 전부 위반(닫힌 쪽)
    """
    term_policy = sanitizer.policy if sanitizer is not None else TermPolicy()
    found = _Originals(set(_strings(list(inventory_names))), set(), set())
    _placeholder_originals(placeholders, found)
    if sanitizer is not None:
        found.names |= sanitizer.names
        found.ids |= sanitizer.ids
        found.accounts |= sanitizer.accounts
    tokens = name_tokens(found.names, term_policy)
    if sanitizer is not None:  # 치환기와 같은 보호 낱말을 뺀다(없으면 닫힌 쪽 — 전부 대조)
        tokens = protect_tokens(tokens, sanitizer.protected) | sanitizer.tokens
    users: dict[str, str | None] = dict(user_values)
    for index, account in enumerate(sorted(found.accounts)):
        users[f"account#{index}"] = account
    canary_list = tuple(str(c) for c in canaries if str(c))
    return ApmLeakGate(
        policy=ColumnPolicy(db_id="apm", scope="apm_bench", tables={}, canary_literals=canary_list),
        vault=sanitizer.vault if sanitizer is not None else PiiVault(canary_list),
        user_values=users,
        fakes=sanitizer.fakes if sanitizer is not None else None,
        originals=[n for n in found.names if term_policy.changes(n)] + sorted(found.ids),
        hex_ids=sanitizer.hex_ids if sanitizer is not None else None,
        tokens=sorted(tokens),
        vocab=sanitizer.vocab if sanitizer is not None else None,
    )


#: 산출 파일(관문 대상) — `leak_check.json`은 `write_outputs`가 쓴다.
OUTPUT_FILES: tuple[str, ...] = (
    "trace.jsonl", "run.json", "probe.jsonl", "coverage.md", "report.md",
)


def stage_outputs(
    records: Sequence[Mapping[str, Any]],
    run_meta: Mapping[str, Any],
    probe_rows: Sequence[Mapping[str, Any]],
    coverage_md: str,
    report_md: str,
) -> dict[str, str]:
    """치환된 산출물 → 파일 이름 → 본문(메모리 — 디스크에 닿기 전 관문이 본다)."""

    def jsonl(rows: Sequence[Mapping[str, Any]]) -> str:
        return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)

    return {
        "trace.jsonl": jsonl(records),
        "run.json": json.dumps(run_meta, ensure_ascii=False, indent=2) + "\n",
        "probe.jsonl": jsonl(probe_rows),
        "coverage.md": coverage_md,
        "report.md": report_md,
    }


def write_outputs(
    out_dir: Path, staged: Mapping[str, str], gate: LeakGate
) -> tuple[bool, list[dict[str, Any]]]:
    """관문 통과면 전부 + `leak_check.json`(통과 기록), 실패면 `leak_check.json`(위치만)만 쓴다.

    실패(False)면 호출 측이 exit 1 한다.
    """
    if "leak_check.json" in staged:
        raise ValueError("leak_check.json 은 관문이 쓴다")
    return write_gated(Path(out_dir), staged, gate)


__all__ = [
    "ACCOUNT_KEYS",
    "ARG_KEYS",
    "ApmLeakGate",
    "BENCH_KEYS",
    "BENCH_NESTED",
    "BENCH_PATHS",
    "CONFLICT_INVENTORY",
    "CONFLICT_VOCAB",
    "CODE_WORD",
    "CodeVocab",
    "DROPPED_SUFFIX",
    "ID_KEYS",
    "NAME_KEYS",
    "NONE",
    "OUTPUT_FILES",
    "REQUIRED_EVIDENCE",
    "REQUIRED_EVIDENCE_UNJUDGED",
    "RunSanitizer",
    "SCENARIO_KEYS",
    "SENTENCE_KEYS",
    "SOURCE_CONTEXT_FIELDS",
    "SOURCE_CONTEXT_KEYS",
    "SOURCE_ID_KEYS",
    "SENSITIVE_RESPONSE",
    "SQL_MASK",
    "SanitizedRun",
    "TERM_POLICY_PATH",
    "TERM_RISK_NOTE",
    "TermPolicy",
    "USER_INFO_MARK",
    "UserInfoConflictError",
    "VIEW_KEYS",
    "build_code_vocab",
    "build_evidence",
    "check_evidence",
    "closed_field",
    "collect_originals",
    "load_code_vocab",
    "load_term_policy",
    "make_gate",
    "name_tokens",
    "parse_term_policy",
    "placeholder_kind",
    "protect_tokens",
    "reselect_keys",
    "sensitive_shape",
    "split_name",
    "stage_outputs",
    "term_risk_lines",
    "unregistered_sources",
    "user_info_conflicts",
    "user_info_vocab_conflicts",
    "vocab_words",
    "write_outputs",
]
