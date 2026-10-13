"""포탈 기능 도움말 — 매뉴얼 색인 검색 · 답변 서비스 · 되돌림 판정 (plans/142 §3.2·§4 · W2).

**그래프 노드가 아니다.** `(질의, 사용자)`를 받아 매뉴얼 근거로 답하는 서비스 함수이고, 누가
부르는지(2단 조건부 처리기 · 1단 도구 · `general_inference` 되돌림)는 모른다 — 배선은
`src/orchestration/portal_help.py`(W3) 몫이다.

근거는 빌드 산출물 `src/static/manual/help_index.json` 하나다(G-1 · G-7 — 매뉴얼 HTML 과 같은
원천·같은 빌드). 검색은 어휘 BM25(G-3 — 외부 패키지 0)이고 단계는 LLM 이 정하지 않는다:

    1 질의 정리 → 2 역할 확정(G-2) → 3 검색
      → 4a 근거 0건: 결정적 안내(LLM 0)
      → 4b 일반 사용자인데 근거가 관리자 매뉴얼에만: 결정적 「관리자 기능」 안내(LLM 0)
      → 4c 근거 있음: 서술 1회
      → 5 코드 부착: 입력 예(사례 원문 · 바이트 그대로 — G-9) · 매뉴얼에서 보기 링크

`portal_redirect_match` 는 분해 LLM 이 `general_inference` 로 보낸 질문을 매뉴얼로 되돌릴지 정하는
**좁은** 결정적 판정이다(G-5 (b) · D-004 부기 변경 — 사용자 승인). 매뉴얼 항목명의 낱말 전부 포함 ·
설명 의문 표지 · 데이터 요청 표지 부재 · 검색 1위·임계를 **모두** 만족할 때만 성립한다. 두 표지
판정은 `src/utils/help_question.py`(W4 `field_mapper` 가드와 공유)에 있다.
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from src.doc_qa.authz import _is_admin
from src.llm import USER_RESPONSE_TAG, astream_text
from src.prompts.portal_help import MANUAL_CLOSE, MANUAL_OPEN, render_system, render_user
from src.utils.help_question import (
    compact_text,
    has_data_request_cue,
    is_method_or_concept_question,
)

logger = logging.getLogger(__name__)

# ── 모듈 상수 (설정 노출 0 — W5 골드셋으로 정한다 · plans/142 §3.2) ─────────────────────────

#: 근거 상위 건수.
TOP_K = 4
#: 근거 본문 문자 예산(전 근거 합).
CHAR_BUDGET = 6000
#: 일반 검색의 최소 점수 — 이 미만이면 0건으로 본다. 점수는 질의 충족도(0~1)다(`_score_all` 참조).
MIN_SCORE = 0.3
#: 되돌림 판정의 최소 점수 — 일반 임계보다 높게 둔다(오탐이 정상 라우팅을 깨지 않게).
REDIRECT_MIN_SCORE = 0.5
#: 되돌림 판정에서 질의에 포함돼야 하는 용어의 최소 길이(공백 제외).
REDIRECT_MIN_TERM_CHARS = 3
#: 서술 길이 상한(프롬프트 지시).
ANSWER_MAX_CHARS = 1200

#: BM25 매개변수 · 필드 가중(항목명·절 제목 ×3 · 장 제목 ×2 · 본문 ×1).
BM25_K1 = 1.2
BM25_B = 0.75
TITLE_WEIGHT = 3
CHAPTER_WEIGHT = 2


#: 질의 정리 — 낱말 끝에서 한 번 떼는 어미(긴 것 먼저 · 남는 길이 1자 이상)와 조사(남는 길이 2자
#: 이상 — 「차이」「길이」 같은 두 글자 낱말을 깎지 않게). 한 글자로 남은 낱말은
#: `tokenize`가 버린다.
QUERY_VERB_ENDINGS: tuple[str, ...] = (
    "하려면", "하려고", "이에요", "해야", "해줘", "하는", "하라", "한다", "해요",
    "이야", "이냐", "인가", "인지", "예요",
)
QUERY_JOSA: tuple[str, ...] = (
    "에서", "으로", "까지", "부터",
    "은", "는", "이", "가", "을", "를", "의", "에", "로", "와", "과", "도",
)
#: 질의 정리 — 검색 점수에서 빼는 의문·요청 낱말(정리 뒤 낱말 단위 정확 일치). 매뉴얼 본문과 무관한
#: 말이 질의 충족도의 분모를 키워 근거를 0건으로 떨어뜨리지 않게 한다.
QUERY_STOPWORDS = frozenset({
    "뭐", "뭐야", "뭔가", "무엇", "무엇인지", "무엇인가", "무슨", "뜻", "의미", "설명", "설명해",
    "어떻게", "방법", "사용법", "알려줘", "알려", "인지", "되냐", "되나", "돼",
    "이야", "거야", "좀",
    "있어", "있나", "해야", "한다",
})

#: 색인 기본 위치 — 패키지 상대(`src/static/manual/help_index.json`).
DEFAULT_INDEX_PATH = Path(__file__).resolve().parents[1] / "static" / "manual" / "help_index.json"
INDEX_VERSION = 1

STATUS_OK = "ok"
STATUS_EMPTY = "empty"
STATUS_ADMIN_ONLY = "admin_only"
STATUS_ERROR = "error"

MANUALS = ("user", "admin")
MANUAL_LABELS = {"user": "사용자 매뉴얼", "admin": "관리자 매뉴얼"}
MANUAL_URL = "/manual/{manual}"

MSG_EMPTY = "매뉴얼에서 찾지 못했습니다."
MSG_HELP_BUTTON = (
    "지금 조회할 수 있는 데이터가 궁금하면 첫 화면의 「❓ 사용법」 버튼을 누르세요. "
    "매뉴얼에 쓰인 화면·항목 이름을 그대로 넣어 다시 물어보셔도 됩니다."
)
MSG_ADMIN_ONLY = (
    "관리자 기능입니다(관리자 매뉴얼 {section_id} 「{title}」) — 관리자에게 요청하세요."
)
MSG_UNAVAILABLE = "포탈 매뉴얼 색인을 쓸 수 없어 답하지 못했습니다: {reason}"
MSG_LLM_FAILED = "답변 생성에 실패했습니다({error}). 관련 매뉴얼 절은 다음과 같습니다."

TRUNCATION_MARK = " …(이하 생략)"

_HANGUL = re.compile(r"[가-힣]")
_TOKEN_RE = re.compile(r"[가-힣]+|[a-z0-9]+")
_SPACE_RE = re.compile(r"\s+")
_QUOTED_RE = re.compile(r"'([^']+)'|\"([^\"]+)\"|「([^」]+)」|‘([^’]+)’|“([^”]+)”|『([^』]+)』")


# ── 색인 ────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class HelpChunk:
    """색인 청크 1건(절 또는 항목). 필드는 `help_index.json` 스키마 그대로."""

    id: str
    manual: str
    kind: str
    section_id: str
    section_title: str
    chapter: str
    item: str
    anchor: str
    text: str
    aliases: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()
    input_forms: tuple[str, ...] = ()

    @property
    def title_key(self) -> str:
        """정확 일치 비교 대상 — 항목 청크는 항목명, 절 청크는 절 제목."""
        return self.item if self.kind == "item" else self.section_title


_compact = compact_text


def tokenize(text: str) -> list[str]:
    """한글은 2글자 단위(bigram), 영문·숫자는 연속 토큰.

    한 글자 한글 덩어리(조사·어미 「는」「해」 등)는 버린다 — bigram 색인에서 변별력이 없다.
    """
    out: list[str] = []
    for m in _TOKEN_RE.finditer((text or "").lower()):
        s = m.group()
        if _HANGUL.match(s):
            out.extend(s[i:i + 2] for i in range(len(s) - 1))
        else:
            out.append(s)
    return out


def _strip_word(word: str) -> str:
    for suffixes, keep in ((QUERY_VERB_ENDINGS, 1), (QUERY_JOSA, 2)):
        for suf in suffixes:
            if word.endswith(suf) and len(word) - len(suf) >= keep:
                return word[: -len(suf)]
    return word


def query_tokens(query: str) -> list[str]:
    """질의 정리(어미·조사 1회 떼기 · 의문 낱말 제거) 뒤 `tokenize`.

    색인 쪽은 그대로 두고 질의만 정리한다 — 「알람이」의 「람이」처럼 조사가 붙어 생긴 bigram 이
    질의 충족도의 분모만 키우는 것을 막는다.
    """
    words: list[str] = []
    for m in _TOKEN_RE.finditer((query or "").lower()):
        w = m.group()
        if _HANGUL.match(w):
            w = _strip_word(w)
        if w not in QUERY_STOPWORDS:
            words.append(w)
    return tokenize(" ".join(words))


class HelpIndex:
    """적재된 매뉴얼 색인 + BM25 통계(프로세스 1회 계산)."""

    def __init__(self, chunks: Sequence[HelpChunk], *, source_hash: str = "") -> None:
        self.chunks: tuple[HelpChunk, ...] = tuple(chunks)
        self.source_hash = source_hash
        self._tf: list[Counter[str]] = []
        self._len: list[int] = []
        df: Counter[str] = Counter()
        for c in self.chunks:
            tf: Counter[str] = Counter()
            title = f"{c.item} {c.section_title}" if c.kind == "item" else c.section_title
            for tok in tokenize(title):
                tf[tok] += TITLE_WEIGHT
            for tok in tokenize(c.chapter):
                tf[tok] += CHAPTER_WEIGHT
            for tok in tokenize(c.text):
                tf[tok] += 1
            self._tf.append(tf)
            self._len.append(sum(tf.values()))
            df.update(tf.keys())
        self._n = len(self.chunks)
        self._avgdl = (sum(self._len) / self._n) if self._n else 1.0
        self._df = df
        self._sections: dict[tuple[str, str], HelpChunk] = {
            (c.manual, c.section_id): c for c in self.chunks if c.kind == "section"
        }

    def idf(self, term: str) -> float:
        df = self._df.get(term, 0)
        return math.log(1.0 + (self._n - df + 0.5) / (df + 0.5))

    def section_of(self, chunk: HelpChunk) -> HelpChunk:
        """항목 청크의 부모 절 청크(없으면 자기 자신)."""
        return self._sections.get((chunk.manual, chunk.section_id), chunk)

    def term_score(self, i: int, term: str) -> float:
        tf = self._tf[i].get(term, 0)
        if not tf:
            return 0.0
        norm = BM25_K1 * (1 - BM25_B + BM25_B * self._len[i] / (self._avgdl or 1.0))
        return self.idf(term) * tf * (BM25_K1 + 1) / (tf + norm)


def _str_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(v for v in value if isinstance(v, str) and v.strip())


def _parse_chunk(row: Any, pos: int) -> HelpChunk:
    if not isinstance(row, dict):
        raise ValueError(f"chunks[{pos}]가 객체가 아닙니다")
    manual = row.get("manual")
    if manual not in MANUALS:
        raise ValueError(f"chunks[{pos}].manual 값이 잘못됐습니다: {manual!r}")
    kind = row.get("kind")
    if kind not in ("section", "item"):
        raise ValueError(f"chunks[{pos}].kind 값이 잘못됐습니다: {kind!r}")
    for key in ("id", "section_id", "text"):
        if not isinstance(row.get(key), str) or not row[key].strip():
            raise ValueError(f"chunks[{pos}].{key} 가 비었습니다")
    return HelpChunk(
        id=row["id"], manual=manual, kind=kind, section_id=row["section_id"],
        section_title=str(row.get("section_title") or ""),
        chapter=str(row.get("chapter") or ""), item=str(row.get("item") or ""),
        anchor=str(row.get("anchor") or ""), text=row["text"],
        aliases=_str_tuple(row.get("aliases")),
        examples=_str_tuple(row.get("examples")),
        input_forms=_str_tuple(row.get("input_forms")),
    )


def parse_help_index(data: Any) -> HelpIndex:
    """색인 JSON 객체 → `HelpIndex`. 형태가 어긋나면 `ValueError`(사유 문구)."""
    if not isinstance(data, dict):
        raise ValueError("최상위가 객체가 아닙니다")
    if data.get("version") != INDEX_VERSION:
        raise ValueError(f"지원하지 않는 색인 버전입니다: {data.get('version')!r}")
    rows = data.get("chunks")
    if not isinstance(rows, list) or not rows:
        raise ValueError("chunks 가 비었습니다")
    chunks = [_parse_chunk(row, i) for i, row in enumerate(rows)]
    return HelpIndex(chunks, source_hash=str(data.get("source_hash") or ""))


_CACHE: dict[Path, tuple[HelpIndex | None, str]] = {}


def _load(path: Path) -> tuple[HelpIndex | None, str]:
    if not path.is_file():
        return None, f"색인 파일이 없습니다({path.name} — 매뉴얼 빌드 필요)"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        index = parse_help_index(data)
    except (OSError, ValueError) as exc:  # JSONDecodeError 는 ValueError 하위
        return None, f"색인 파일이 손상됐습니다({path.name}: {exc})"
    return index, f"청크 {len(index.chunks)}건 적재(source_hash={index.source_hash[:12] or '-'})"


def _cached(path: Path | str | None) -> tuple[HelpIndex | None, str]:
    key = Path(path) if path is not None else DEFAULT_INDEX_PATH
    if key not in _CACHE:
        _CACHE[key] = _load(key)
    return _CACHE[key]


def load_help_index(path: Path | str | None = None) -> HelpIndex | None:
    """색인을 적재한다(경로별 프로세스 1회 캐시).

    부재·손상이면 None — 사유는 `help_index_status`.
    """
    return _cached(path)[0]


def help_index_status(path: Path | str | None = None) -> tuple[bool, str]:
    """`(사용 가능, 사유)` — 호출부가 기동 로그 1줄과 비활성 판정에 쓴다(침묵 폴백 금지)."""
    index, reason = _cached(path)
    return index is not None, reason


def reset_help_index_cache() -> None:
    """적재 캐시를 비운다(테스트·재빌드 뒤 재적재용)."""
    _CACHE.clear()


# ── 검색 ────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class HelpHit:
    """검색 결과 1건 — `text`는 문자 예산 적용 뒤 본문."""

    chunk: HelpChunk
    score: float
    exact: bool
    text: str
    truncated: bool = False


def quoted_terms(query: str) -> list[str]:
    """따옴표(' ' · " " · 「 」 · 『 』 · ‘ ’ · “ ”)로 묶은 화면 용어."""
    out: list[str] = []
    for m in _QUOTED_RE.finditer(query or ""):
        term = next((g for g in m.groups() if g), "").strip()
        if term:
            out.append(term)
    return out


def _allowed_manuals(is_admin: bool) -> frozenset[str]:
    return frozenset(MANUALS) if is_admin else frozenset({"user"})


def _score_all(query: str, index: HelpIndex, manuals: frozenset[str],
               ) -> list[tuple[int, float, bool]]:
    """`(청크 위치, 점수, 따옴표 정확 일치)` — 점수 내림차순(정확 일치 우선).

    점수는 **질의 충족도**다: BM25 합을 «질의 토큰이 모두 만점으로 맞았을 때의 합»으로 나눈 값
    (0~1 근방). 원점수는 색인 크기·문서 길이에 따라 척도가 바뀌어 임계를 상수로 둘 수 없다.
    질의에 든 `aliases`(「노이즈 캔슬링」)는 그 별칭을 가진 절의 이름으로 읽는다 — 그 절의 청크(항목
    청크 포함)는 별칭 토큰을 만점으로 친다. 다른 청크는 별칭 토큰을 보통대로 센다(검색 확장 전용 —
    라우팅 판단에는 쓰지 않는다).
    """
    base = set(query_tokens(query))
    ideal = sum(index.idf(t) * (BM25_K1 + 1) for t in base)
    compact_q = _compact(query)
    alias_tokens: set[str] = set()
    alias_sections: set[tuple[str, str]] = set()
    for c in index.chunks:
        hit = [a for a in c.aliases if _compact(a) and _compact(a) in compact_q]
        if hit and c.manual in manuals:
            alias_sections.add((c.manual, c.section_id))
            for a in hit:
                alias_tokens.update(t for t in tokenize(a) if t in base)
    alias_credit = sum(index.idf(t) * (BM25_K1 + 1) for t in alias_tokens)
    quoted = {_compact(t) for t in quoted_terms(query)}

    scored: list[tuple[int, float, bool]] = []
    for i, c in enumerate(index.chunks):
        if c.manual not in manuals:
            continue
        if (c.manual, c.section_id) in alias_sections:
            raw = alias_credit + sum(index.term_score(i, t) for t in base - alias_tokens)
        else:
            raw = sum(index.term_score(i, t) for t in base)
        score = raw / ideal if ideal > 0 else 0.0
        exact = bool(quoted) and _compact(c.title_key) in quoted
        if score > 0 or exact:
            scored.append((i, score, exact))
    scored.sort(key=lambda r: (not r[2], -r[1], r[0]))
    return scored


def search_help(query: str, *, index: HelpIndex, is_admin: bool, k: int = TOP_K,
                char_budget: int = CHAR_BUDGET, min_score: float = MIN_SCORE,
                manuals: frozenset[str] | None = None) -> list[HelpHit]:
    """역할 필터(사용자 = 사용자 매뉴얼만 · 관리자 = 두 권) 안에서 상위 k건 · 문자 예산 적용.

    따옴표 정확 일치 청크는 최우선이고 최소 점수와 무관하게 싣는다. 그 밖은 `min_score` 미만이면
    버린다. 예산은 앞 근거부터 채우고, 넘치는 근거는 잘라 싣고 그 뒤는 싣지 않는다.
    """
    allowed = manuals if manuals is not None else _allowed_manuals(is_admin)
    hits: list[HelpHit] = []
    remaining = char_budget
    for i, score, exact in _score_all(query, index, allowed):
        if len(hits) >= k or remaining <= 0:
            break
        if not exact and score < min_score:
            continue
        chunk = index.chunks[i]
        text, truncated = chunk.text, False
        if len(text) > remaining:
            text, truncated = text[:remaining] + TRUNCATION_MARK, True
        remaining -= len(chunk.text)
        hits.append(HelpHit(chunk, round(score, 4), exact, text, truncated))
    return hits


# ── 되돌림 판정 (G-5 (b)) ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PortalRedirectMatch:
    """되돌림 성립 근거 — 어떤 용어가 어느 청크를 1위로 끌어올렸나."""

    term: str
    chunk_id: str
    manual: str
    section_id: str
    score: float


def _index_terms(index: HelpIndex, manuals: frozenset[str],
                 ) -> dict[tuple[str, ...], set[tuple[str, str]]]:
    """판정 용어(공백 구분 낱말 묶음 · 소문자) → 그 용어를 가진 절 `(권, 절 id)` 집합."""
    terms: dict[tuple[str, ...], set[tuple[str, str]]] = {}
    for c in index.chunks:
        if c.manual not in manuals:
            continue
        for raw in (c.title_key, *c.aliases):
            if len(_compact(raw)) < REDIRECT_MIN_TERM_CHARS:
                continue
            words = tuple(w.lower() for w in raw.split())
            terms.setdefault(words, set()).add((c.manual, c.section_id))
    return terms


def _matched_terms(compact_q: str, index: HelpIndex, manuals: frozenset[str],
                   ) -> dict[tuple[str, ...], set[tuple[str, str]]]:
    """판정 용어 중 낱말이 전부 질의(공백 제거)에 든 것 → 그 용어를 가진 절 집합."""
    return {
        words: secs for words, secs in _index_terms(index, manuals).items()
        if all(w in compact_q for w in words)
    }


def _drop_term_words(compact_q: str, matched: Mapping[tuple[str, ...], Any]) -> str:
    """일치한 용어의 낱말을 긴 것부터 지운 나머지 — 항목명 자체에 든 낱말이 표지로 읽히지 않게."""
    rest = compact_q
    for word in sorted({w for words in matched for w in words}, key=len, reverse=True):
        rest = rest.replace(word, " ")
    return rest


def strip_screen_terms(query: str, *, is_admin: bool = True, index: HelpIndex | None = None,
                       quoted: bool = True) -> str:
    """표지 판정 재료 — 따옴표 구간(`quoted_terms`)과 일치한 색인 용어(항목명·절 제목·aliases)를
    지운 나머지(공백 제거 · 소문자).

    화면 용어 안의 낱말(「사용자 목록」·「상위 억제 알람 유형」·「최근 결정」)이 데이터 요청
    표지로 읽혀 포탈 질문이 뒤집히지 않게 한다(plans/142 V-3). 명시 명령 ③.9 · 분해 출구 데이터
    요청 교정 · 되돌림 판정이 함께 쓴다(되돌림은 `quoted=False` — 범위를 넓히지 않는다). 색인이
    없으면 따옴표 구간만 지운다.
    """
    text = query or ""
    if quoted:
        text = _QUOTED_RE.sub(" ", text)
    compact_q = _compact(text)
    if index is None:
        index = load_help_index()
    if index is None or not compact_q:
        return compact_q
    return _drop_term_words(compact_q, _matched_terms(compact_q, index,
                                                      _allowed_manuals(is_admin)))


def portal_redirect_match(query: str, *, is_admin: bool,
                          index: HelpIndex | None = None) -> PortalRedirectMatch | None:
    """`general_inference`로 간 질문을 매뉴얼로 되돌릴지 — 아래 넷을 **모두** 만족할 때만 성립.

    (a) 색인의 항목명·절 제목·aliases 중 하나(공백 제외 `REDIRECT_MIN_TERM_CHARS`자 이상)에 대해
        **그 용어의 공백 구분 낱말이 전부** 질의(공백 무시)에 부분 문자열로 들어 있다
        (「유사어 등록」 → 「유사어를 등록하려고」도 성립 · 한 낱말 용어는 그 낱말 포함)
    (b) 설명·방법 의문 표지가 있다(`help_question.is_method_or_concept_question`)
    (c) 데이터 요청 표지가 없다(`help_question.has_data_request_cue`)
    (d) 그 용어를 가진 절의 청크가 검색 1위이고 점수가 `REDIRECT_MIN_SCORE` 이상

    (b)·(c)는 질의에서 일치한 용어의 낱말을 지운 나머지(`strip_screen_terms(quoted=False)`)로
    본다 — 항목명 자체에 든 낱말(「최근 …」)이 표지로 읽히지 않게. 역할 필터는 검색과 같다
    (사용자 = 사용자 매뉴얼만 · 호출자가 역할을 넘긴다 — V-2). 색인이 없으면 None.
    """
    if index is None:
        index = load_help_index()
    compact_q = _compact(query)
    if index is None or not compact_q:
        return None
    manuals = _allowed_manuals(is_admin)
    matched = _matched_terms(compact_q, index, manuals)
    if not matched:
        return None
    rest = strip_screen_terms(query, is_admin=is_admin, index=index, quoted=False)
    if not is_method_or_concept_question(rest) or has_data_request_cue(rest):
        return None
    ranked = _score_all(query, index, manuals)
    if not ranked:
        return None
    i, score, _exact = ranked[0]
    top = index.chunks[i]
    if score < REDIRECT_MIN_SCORE:
        return None
    for words in sorted(matched, key=lambda w: len("".join(w)), reverse=True):
        if (top.manual, top.section_id) in matched[words]:
            return PortalRedirectMatch(" ".join(words), top.id, top.manual, top.section_id,
                                       round(score, 4))
    return None


# ── 답변 ────────────────────────────────────────────────────────────────────────────────


@dataclass
class PortalHelpResult:
    """답변 산출물 — 실패도 사유를 들고 돌아온다(침묵 폴백 금지)."""

    answer: str
    status: str = STATUS_OK
    reason: str = ""
    section_ids: list[str] = field(default_factory=list)
    chunk_ids: list[str] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK


def _manual_links(is_admin: bool) -> list[str]:
    manuals = MANUALS if is_admin else ("user",)
    return [f"- [{MANUAL_LABELS[m]}]({MANUAL_URL.format(manual=m)})" for m in manuals]


def _link_lines(hits: Sequence[HelpHit], *, show_manual: bool) -> list[str]:
    """근거 절마다 한 줄 — 절 단위 중복 제거, 첫 근거의 앵커를 쓴다."""
    lines: list[str] = []
    seen: set[tuple[str, str]] = set()
    for h in hits:
        c = h.chunk
        key = (c.manual, c.section_id)
        if key in seen:
            continue
        seen.add(key)
        url = MANUAL_URL.format(manual=c.manual) + (f"#{c.anchor}" if c.anchor else "")
        label = f"{c.section_id} {c.section_title}".strip()
        if c.kind == "item" and c.item:
            label += f" — {c.item}"
        prefix = f"{MANUAL_LABELS[c.manual]} · " if show_manual else ""
        lines.append(f"- {prefix}[{label}]({url})")
    return lines


def _input_example_block(index: HelpIndex, top: HelpChunk) -> str:
    """상위 근거 절의 사례 원문·형식 문장 — **바이트 그대로** 코드블록으로(G-9)."""
    section = index.section_of(top)
    examples = list(dict.fromkeys(section.examples + top.examples))
    forms = list(dict.fromkeys(section.input_forms + top.input_forms))
    if not examples and not forms:
        return ""
    parts = ["**입력 예**"]
    if examples:
        parts.append("```text\n" + "\n".join(examples) + "\n```")
    if forms:
        parts.append("형식:")
        parts.append("```text\n" + "\n".join(forms) + "\n```")
    return "\n".join(parts)


def _render_evidence(hits: Sequence[HelpHit]) -> str:
    blocks: list[str] = []
    for n, h in enumerate(hits, 1):
        c = h.chunk
        head = f"[{MANUAL_LABELS[c.manual]} {c.section_id} 「{c.section_title}」"
        if c.chapter:
            head += f" · 장 「{c.chapter}」"
        if c.kind == "item" and c.item:
            head += f" · 항목 「{c.item}」"
        head += "]"
        blocks.append(f"{MANUAL_OPEN.format(n=n)}\n{head}\n{h.text}\n{MANUAL_CLOSE.format(n=n)}")
    return "\n\n".join(blocks)


def _section_ids(hits: Sequence[HelpHit]) -> list[str]:
    return list(dict.fromkeys(f"{h.chunk.manual}:{h.chunk.section_id}" for h in hits))


async def answer_portal_help(
    query: str,
    *,
    user: Mapping[str, Any] | None,
    llm: Any,
    index: HelpIndex | None = None,
) -> PortalHelpResult:
    """매뉴얼 근거로 포탈 기능 질문에 답한다(고정 단계 — 모듈 독스트링).

    Args:
        query: 사용자 질문(명령 접두 제거는 호출부 몫 — 여기서는 공백만 정리한다).
        user: 인증 사용자 정보(`role`·`type`·`roles`). 관리자 판정은 `authz._is_admin` 재사용.
        llm: 서술 LLM(answer 프로파일은 호출부가 고른다 — D-194). 근거 0건·관리자 전용이면
            부르지 않는다.
        index: 색인(None 이면 기본 경로 적재본).
    """
    text = _SPACE_RE.sub(" ", query or "").strip()
    is_admin = _is_admin(user)
    diagnostics: dict[str, Any] = {"llm_calls": 0, "is_admin": is_admin}

    if index is None:
        index = load_help_index()
        if index is None:
            _ok, reason = help_index_status()
            return PortalHelpResult(MSG_UNAVAILABLE.format(reason=reason), status=STATUS_ERROR,
                                    reason=reason, diagnostics=diagnostics)
    if not text:
        return PortalHelpResult(
            "\n\n".join([MSG_EMPTY, "\n".join(_manual_links(is_admin)), MSG_HELP_BUTTON]),
            status=STATUS_EMPTY, reason="빈 질의", diagnostics=diagnostics,
        )

    hits = search_help(text, index=index, is_admin=is_admin)
    diagnostics["hits"] = [(h.chunk.id, h.score, h.exact) for h in hits]

    if not hits:
        # 4b) 일반 사용자 — 관리자 매뉴얼에만 근거가 있으면 내용 없이 결정적 안내(G-2)
        if not is_admin:
            admin_hits = search_help(text, index=index, is_admin=False,
                                     manuals=frozenset({"admin"}), k=1)
            if admin_hits:
                top = admin_hits[0].chunk
                logger.info("포탈 도움말 관리자 전용 안내 %s · LLM 호출 0회", top.section_id)
                return PortalHelpResult(
                    MSG_ADMIN_ONLY.format(section_id=top.section_id, title=top.section_title),
                    status=STATUS_ADMIN_ONLY, reason="근거가 관리자 매뉴얼에만 있음",
                    section_ids=[f"admin:{top.section_id}"], chunk_ids=[top.id],
                    diagnostics=diagnostics,
                )
        # 4a) 근거 0건 — LLM 없이 결정적 안내
        logger.info("포탈 도움말 근거 0건 · LLM 호출 0회")
        return PortalHelpResult(
            "\n\n".join([MSG_EMPTY, "\n".join(_manual_links(is_admin)), MSG_HELP_BUTTON]),
            status=STATUS_EMPTY, reason="매뉴얼 근거 0건", diagnostics=diagnostics,
        )

    show_manual = len({h.chunk.manual for h in hits}) > 1
    links = "**매뉴얼에서 보기**\n" + "\n".join(_link_lines(hits, show_manual=show_manual))
    section_ids = _section_ids(hits)
    chunk_ids = [h.chunk.id for h in hits]

    # 4c) 서술 1회
    if llm is None:
        return PortalHelpResult(
            "\n\n".join([MSG_LLM_FAILED.format(error="서술 LLM 미지정"), links]),
            status=STATUS_ERROR, reason="서술 LLM 미지정", section_ids=section_ids,
            chunk_ids=chunk_ids, diagnostics=diagnostics,
        )
    # 입력 예 블록은 서술 전에 정한다 — 붙일 블록이 있을 때만 서술이 그것을 가리키게 한다
    example_block = _input_example_block(index, hits[0].chunk)
    messages = [
        SystemMessage(content=render_system(max_chars=ANSWER_MAX_CHARS,
                                            has_examples=bool(example_block))),
        HumanMessage(content=render_user(query=text, evidence=_render_evidence(hits))),
    ]
    try:
        diagnostics["llm_calls"] = 1
        narrative = await astream_text(llm, messages, tags=[USER_RESPONSE_TAG])
    except Exception as exc:  # noqa: BLE001 — 실패 사유를 구조화해 노출(일반 LLM 답으로 강등 금지)
        logger.exception("포탈 도움말 서술 실패 — 근거 링크만 반환")
        diagnostics["llm_error"] = type(exc).__name__
        return PortalHelpResult(
            "\n\n".join([MSG_LLM_FAILED.format(error=type(exc).__name__), links]),
            status=STATUS_ERROR, reason=f"서술 LLM 실패: {type(exc).__name__}",
            section_ids=section_ids, chunk_ids=chunk_ids, diagnostics=diagnostics,
        )

    # 5) 코드 부착 — 입력 예(사례 원문) · 매뉴얼에서 보기
    parts = [narrative.strip()]
    if example_block:
        parts.append(example_block)
    parts.append(links)
    logger.info("포탈 도움말 완료 근거=%s 서술=%d자", section_ids, len(narrative))
    return PortalHelpResult(
        "\n\n".join(parts), status=STATUS_OK, section_ids=section_ids,
        chunk_ids=chunk_ids, diagnostics=diagnostics,
    )


__all__ = [
    "HelpChunk",
    "HelpHit",
    "HelpIndex",
    "PortalHelpResult",
    "PortalRedirectMatch",
    "answer_portal_help",
    "help_index_status",
    "load_help_index",
    "parse_help_index",
    "portal_redirect_match",
    "query_tokens",
    "reset_help_index_cache",
    "search_help",
    "strip_screen_terms",
    "tokenize",
]
