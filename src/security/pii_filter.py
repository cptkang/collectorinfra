"""FabriX 개인정보(PII) 필터 판정 규칙 및 진단 유틸.

`docs/pii_filtering_rules.md` 의 정규식 규칙을 코드로 옮긴 것이다.

FabriX AI 커넥터는 프롬프트/응답에서 아래 PII를 탐지하면 응답 ``status`` 를
``FILTER_INVALID`` 로 돌려주고 ``filterBlockReason``(``ko``·``policy_id``·
``filter_log_id``)을 함께 싣는다. (프롬프트 차단 시 ``content`` 가
"The content was blocked by the filter" 로 치환되어 오기도 한다.)

두 가지 용도로 쓴다.

1. :func:`scan_pii` — 전송한 프롬프트를 로컬에서 스캔해 "어떤 텍스트가 어떤
   PII 유형으로 걸릴지"를 특정한다. 차단 상세(policy_id)가 응답에 실리지 않는
   경우 오탐 원인 문자열을 찾는 데 쓴다.
2. :func:`log_filter_block_if_any` — FabriX 응답이 필터로 차단됐으면
   ``filterBlockReason`` 을 사람이 읽을 수 있게 해석(policy_id → 유형명,
   filter_log_id 노출)하고, 로컬 스캔 결과와 함께 경고 로그를 1건 남긴다.

변경 방법
---------
* **ON/OFF**: ``.env`` 의 ``SECURITY_PII_FILTER_LOG_ENABLED=true|false`` (기본 ON).
  OFF면 감지·스캔·로깅을 전부 건너뛴다(호출부 무변경).
* **마스킹 해제**: ``SECURITY_PII_FILTER_LOG_UNMASK=true`` (기본 OFF=마스킹).
  ON이면 감지 문자열을 원문 그대로 로그에 남겨 오탐(타임스탬프 등) 판정을 돕는다.
  로그에 실 개인정보가 남을 수 있으니 진단 시에만 한시적으로 켠다.
* **샘플 스크럽**: ``SECURITY_PII_SCRUB_SAMPLES=true|false`` (기본 ON). ON이면
  프롬프트 주입 전 샘플 데이터의 PII를 :func:`scrub_pii` 로 마스킹해 FabriX 필터
  오탐 차단을 예방한다(형식 보존 → 컬럼 추론 신호 유지).
* **판정 규칙**: 아래 :data:`PII_RULES` 만 수정하면 된다(유형 추가/삭제/정규식 교체).
  근거 문서 ``docs/pii_filtering_rules.md`` 도 함께 갱신한다.
* **policy_id 해석표**: :data:`_POLICY_SETS` (FabriX가 돌려주는 정책 번호 → 유형명).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. 로컬 판정 규칙 (docs/pii_filtering_rules.md 의 정규식을 그대로 이식)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PiiRule:
    name: str          # PII 유형명
    rule_id: str       # docs/pii_filtering_rules.md 기준 룰 ID
    pattern: "re.Pattern[str]"
    whole_line: bool = False  # True면 값 전체가 한 필드인 규칙 → 긴 라인엔 미적용


PII_RULES: List[PiiRule] = [
    PiiRule(
        "주민등록번호", "848",
        re.compile(
            r"(?<!\d)(?:(?:[5-9]\d)(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])[- ]?[12]\d{6}"
            r"|(?:[0-4]\d)(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])[- ]?[34]\d{6})(?!\d)"
        ),
    ),
    PiiRule(
        "법인등록번호", "854",
        re.compile(
            r"(?<!\d)(?:(1[1-9]|2[0-8])(0[1-3]|1[1-5]|4[1-9]|5[0-9]|6[0-2])"
            r"(1[1-5]|2[1-2]|3[1-9]|4[0-9]|5[0-5]|6|7|8[1-6])(?:[-\s]\d{7}))(?!\d)"
        ),
    ),
    PiiRule(
        "외국인등록번호", "849",
        re.compile(r"(?<!\d)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])[- ]?[5678]\d{6}(?!\d)"),
    ),
    PiiRule(
        "운전면허번호", "850",
        re.compile(
            r"(?<!\d)(?:(?:서울|부산|경기|강원|충북|충남|전북|전남|경북|경남|제주|대구|인천|광주|대전|울산|1[1-9]|2[0-8])[- ]?\d{2}[- ]\d{6}[- ]\d{2}"
            r"|(?:서울|부산|경기|강원|충북|충남|전북|전남|경북|경남|제주|대구|인천|광주|대전|울산|1[1-9]|2[0-8])[- ]\d{10})(?!\d)"
        ),
    ),
    PiiRule(
        "여권번호", "842",
        re.compile(r"(?<!\d)(?:[MSROD]\d{8}|[MSROD]\d{3}[A-Z]\d{4})(?!\d)"),
    ),
    PiiRule(
        # 2026-08-05 가이드 개정 반영: 구분자(하이픈·점·공백) 형식(010-1234-5678,
        # 010.1234.5678, 010 1234 5678)을 백레퍼런스로 탐지. 종전 로컬 룰은 무구분/
        # 단일구분(01012345678)만 잡아 서버 개정 룰과 격차 — 샘플 연락처가 스크럽을
        # 통과해 차단되는 원인이었다. 혼합 형식(010-12345678)은 서버 룰과 동일하게 비탐지.
        "핸드폰번호", "855",
        re.compile(
            r"(?<!\d)(?:010([-.\s]?)\d{4}\1\d{4}"
            r"|01[16789]([-.\s]?)\d{3,4}\2\d{4})(?!\d)"
        ),
    ),
    PiiRule(
        # 2026-08-05 가이드 개정 반영: 라인 전체 판정 → 인라인 판정으로 전환.
        # 가이드 원문 정규식이 PDF에서 일부 절단되어 아래는 공식 예시·예외 주석
        # (2-2-3-5 미탐지, 총 10~14자리, 단일 알파벳 접두 접수번호 제외, 동일 구분자,
        # 핸드폰번호 형태 중복 탐지 허용)을 기준으로 한 재구성본 — 폐쇄망 실측으로 보정.
        "계좌번호", "851",
        re.compile(
            r"(?<![A-Za-z0-9])(?<!\d[- ])"
            r"(?!\d{2}(?P<a>[- ])\d{2}(?P=a)\d{3}(?P=a)\d{5}(?!\d))"  # 2-2-3-5 공식 예외
            # 날짜형(YYYY-MM-DD) 제외 — 단 뒤에 동일 구분자 그룹이 더 이어지면 계좌로 본다
            # (예: 6565-01-02-233123). 서버 원문에도 날짜·시각 제외 단편이 존재.
            r"(?!\d{4}(?P<d>[- ])\d{2}(?P=d)\d{2}(?!(?P=d)\d)(?!\d))"
            r"(?=(?:\d[- ]?){10,14}(?![- ]?\d))"                      # 총 숫자 10~14자리
            r"\d{2,6}(?P<s>[- ])\d{2,6}(?P=s)\d{1,8}(?:(?P=s)\d{1,8})*(?!\d)"
        ),
    ),
    PiiRule(
        "카드번호", "852",
        re.compile(r"(?<!\d)\d{4}([-\s])\d{4}\1\d{4}\1\d{4}(?!\d)"),
    ),
    PiiRule(
        # 2026-08-05 가이드 개정 반영: 로컬파트 말미 리터럴 "\n" 제외 룩비하인드 추가.
        # 제외 도메인(kbonecloud/kbfg)은 종전 가이드 유지분 — 개정본에서 해당 위치가
        # 절단되어 확인 불가, 폐쇄망 실측으로 검증 필요.
        "이메일 주소", "853",
        re.compile(
            r"[A-Za-z0-9]+(?:[._%+-][A-Za-z0-9]+)*(?<!\\n)@"
            r"(?!(?:kbonecloud\.com|kbfg\.com)\b)"
            r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*(?:\.[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)+"
        ),
    ),
]

_WHOLE_LINE_MAX = 120  # whole_line 규칙(계좌 등)은 짧은 라인에만 적용(백트래킹·오탐 방지)

# ── 계좌번호(851) 광폭 매칭 의심 형태 (D-155 후속3) ─────────────────────────────
# 폐쇄망 실측(2026-08-05): 계좌번호 정책 차단인데 덤프에서 계좌 유사 문자열을 찾지
# 못하는 사례. 서버 원문 단편의 날짜 제외는 2자리 연도(YY-MM-DD)형만 커버하고,
# 자릿수 창이 비숫자 관통(\D*)이라면 `"2026-06-17 02:30:45", "id": 123` 같은 라인이
# 날짜+뒷숫자로 10자리를 채워 계좌로 매칭될 수 있다(구룰이 실제 그 방식이었음).
# DB2 타임스탬프(2026-08-05-14.30.45.123456)는 하이픈 4그룹 10자리라 직접 걸린다.
# 이 형태는 기본 스크럽하지 않고(날짜 형식 신호 보존) 진단에 "의심" 라벨로 보고한다.
# 실측 확정 시 SECURITY_PII_SCRUB_SUSPECT_DATES=true 로 켜면 구분자 치환(-·공백→.)
# 으로 무해화한다 — 서버 계좌 룰의 구분자 클래스는 [- ] 뿐이라 점 치환으로 회피된다.
_SUSPECT_DATE_RE = re.compile(
    r"(?<!\d)\d{4}[- ]\d{1,2}[- ]\d{1,2}"
    r"(?:[- T]\d{1,2}[:.]\d{2}(?:[:.]\d{2})?(?:\.\d+)?)?(?!\d)"
)
_SUSPECT_MIN_LINE_DIGITS = 10  # 라인 총 숫자 자릿수가 이 이상일 때만 의심 보고


def is_scrub_suspect_dates_enabled() -> bool:
    """날짜·타임스탬프 무해화 스크럽 on/off (``SECURITY_PII_SCRUB_SUSPECT_DATES``, 기본 OFF).

    설정 로드 실패 시 OFF(현행 유지) — 의심 형태 확정 전에는 값 변형을 하지 않는다.
    """
    try:
        from src.config import load_config

        return bool(load_config().security.pii_scrub_suspect_dates)
    except Exception:
        return False


def _defang_date(matched: str) -> str:
    """날짜·타임스탬프의 서버 계좌 룰 구분자([- ])를 점으로 치환해 무해화한다.

    마스킹이 아니라 구분자 치환 — LLM의 날짜 형식 추론 신호(자릿수·값)는 보존된다.
    예: "2026-06-17 02:30:45" → "2026.06.17.02:30:45"
    """
    return matched.replace("-", ".").replace(" ", ".")


# ---------------------------------------------------------------------------
# 2. FabriX 응답 policy_id → 유형명 (운영/검증 6개 정책 슬롯 모두 수록)
#    순서: 검증분석질문·검증분석답변·검증서빙질문·검증서빙답변·운영분석질문·운영분석답변
#    운영 슬롯(850~867)은 유형별로 고유하고, 일부 검증 슬롯은 유형 간 겹쳐 "/"로 병기.
# ---------------------------------------------------------------------------
_POLICY_SETS = {
    "주민등록번호":   [635, 647, 649, 659, 850, 859],
    "법인등록번호":   [644, 646, 657, 658, 851, 860],
    "외국인등록번호": [636, 648, 650, 660, 852, 861],
    "운전면허번호":   [637, 649, 651, 661, 853, 862],
    "여권번호":       [638, 650, 652, 662, 854, 863],
    "핸드폰번호":     [645, 651, 653, 663, 855, 864],
    "계좌번호":       [640, 652, 654, 664, 856, 865],
    "카드번호":       [641, 653, 655, 665, 857, 866],
    "이메일주소":     [642, 654, 656, 666, 858, 867],
}
POLICY_ID_TYPE: dict[int, str] = {}
for _name, _ids in _POLICY_SETS.items():
    for _pid in _ids:
        if _pid in POLICY_ID_TYPE and _name not in POLICY_ID_TYPE[_pid]:
            POLICY_ID_TYPE[_pid] = f"{POLICY_ID_TYPE[_pid]}/{_name}"
        else:
            POLICY_ID_TYPE.setdefault(_pid, _name)

# 가이드 스크린샷 실측(2026-08-05): KB GenAI 포털 경유 시 별도 정책 슬롯(364·376,
# ko="개인정보(탐지)")이 반환된다 — 은행 테넌트 표 밖의 정책이라 유형 미세분.
POLICY_ID_TYPE.setdefault(364, "개인정보(포털 정책)")
POLICY_ID_TYPE.setdefault(376, "개인정보(포털 정책)")


def policy_type(policy_id: Any) -> str:
    """policy_id(정수/문자열)를 PII 유형명으로 해석한다. 미상이면 '미상'."""
    try:
        return POLICY_ID_TYPE.get(int(str(policy_id).strip()), "미상")
    except (TypeError, ValueError):
        return "미상"


def is_detection_enabled() -> bool:
    """PII 필터 감지 로깅 on/off (``SECURITY_PII_FILTER_LOG_ENABLED``, 기본 ON).

    설정 로드 실패 시 안전하게 ON으로 간주한다(진단 로깅 유지).
    """
    try:
        from src.config import load_config

        return bool(load_config().security.pii_filter_log_enabled)
    except Exception:
        return True


def is_unmask_enabled() -> bool:
    """감지 문자열을 원문 그대로 남길지 (``SECURITY_PII_FILTER_LOG_UNMASK``, 기본 OFF).

    설정 로드 실패 시 안전하게 OFF(마스킹)로 간주한다.
    """
    try:
        from src.config import load_config

        return bool(load_config().security.pii_filter_log_unmask)
    except Exception:
        return False


def is_scrub_samples_enabled() -> bool:
    """프롬프트 주입 전 샘플 데이터 PII 스크럽 on/off (``SECURITY_PII_SCRUB_SAMPLES``, 기본 ON).

    설정 로드 실패 시 안전하게 ON으로 간주한다(FabriX 필터 차단 예방 우선).
    """
    try:
        from src.config import load_config

        return bool(load_config().security.pii_scrub_samples)
    except Exception:
        return True


def is_block_dump_enabled() -> bool:
    """차단 시 프롬프트·응답 전문 파일 덤프 on/off (``SECURITY_PII_BLOCK_DUMP_ENABLED``, 기본 ON).

    설정 로드 실패 시 안전하게 ON으로 간주한다(원인 특정이 우선 — D-155 후속1).
    """
    try:
        from src.config import load_config

        return bool(load_config().security.pii_block_dump_enabled)
    except Exception:
        return True


# ---------------------------------------------------------------------------
# 로컬 스캔
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PiiMatch:
    name: str
    rule_id: str
    masked: str      # 원문을 가린 형태(형태만 노출)
    context: str     # 앞뒤 문맥(어느 필드/샘플에서 왔는지 식별용)


def _mask_value(name: str, s: str) -> str:
    """탐지 문자열을 로그에 그대로 남기지 않도록 가린다(형태만 노출)."""
    if name == "이메일 주소" and "@" in s:
        local, _, domain = s.partition("@")
        head = local[0] if local else ""
        return f"{head}{'*' * max(1, len(local) - 1)}@{domain}"
    if len(s) <= 2:
        return "*" * len(s)
    digits = re.sub(r"\D", "", s)
    return f"{s[0]}{'*' * (len(s) - 2)}{s[-1]} (숫자{len(digits)}자리)"


def _context(line: str, start: int, end: int, shown: str, window: int = 25) -> str:
    pre = line[max(0, start - window):start]
    post = line[end:end + window]
    return f"{pre}>>{shown}<<{post}".replace("\n", " ").strip()


def scan_pii(
    text: str,
    max_per_rule: int = 5,
    unmask: Optional[bool] = None,
    rules: Optional[List[PiiRule]] = None,
) -> List[PiiMatch]:
    """프롬프트/텍스트를 로컬 규칙으로 스캔해 PII 후보를 반환한다.

    라인 단위로 적용한다(PII 값은 개행을 넘지 않고, 값-전체 규칙의 ^/$ 앵커가
    라인 경계로 스코프되어 오탐·백트래킹을 줄인다).

    Args:
        unmask: True면 감지 문자열을 원문 그대로 담는다. None(기본)이면
            ``SECURITY_PII_FILTER_LOG_UNMASK`` 설정을 따른다(기본 마스킹).
        rules: 지정하면 해당 규칙들로만 스캔한다(서버 차단 유형별 표적 대조용,
            D-155 후속5). None이면 전체 규칙(9종).
    """
    if not text:
        return []
    if unmask is None:
        unmask = is_unmask_enabled()
    results: List[PiiMatch] = []
    lines = text.split("\n")
    for rule in (rules if rules is not None else PII_RULES):
        count = 0
        for line in lines:
            if rule.whole_line and len(line) > _WHOLE_LINE_MAX:
                continue
            try:
                for m in rule.pattern.finditer(line):
                    matched = m.group(0)
                    shown = matched if unmask else _mask_value(rule.name, matched)
                    results.append(
                        PiiMatch(rule.name, rule.rule_id, shown,
                                 _context(line, m.start(), m.end(), shown))
                    )
                    count += 1
                    if count >= max_per_rule:
                        break
            except Exception as exc:  # 진단 스캔이 본 흐름을 깨지 않도록
                logger.debug("PII 스캔 규칙 실패(%s): %r", rule.name, exc)
            if count >= max_per_rule:
                break
    return results


def scan_account_suspects(
    text: str, max_hits: int = 8, unmask: Optional[bool] = None
) -> List[PiiMatch]:
    """계좌번호(851) 광폭 매칭 **의심** 형태(날짜·타임스탬프)를 스캔한다 (D-155 후속3).

    라인 총 숫자 자릿수가 ``_SUSPECT_MIN_LINE_DIGITS`` 이상일 때만 보고한다 —
    서버 룰의 자릿수 창이 비숫자 관통(\\D*)이라는 가설에서, 짧은 날짜 단독 라인은
    10자리를 못 채워 차단 원인이 될 수 없기 때문이다.
    """
    if not text:
        return []
    if unmask is None:
        unmask = is_unmask_enabled()
    results: List[PiiMatch] = []
    for line in text.split("\n"):
        if sum(c.isdigit() for c in line) < _SUSPECT_MIN_LINE_DIGITS:
            continue
        for m in _SUSPECT_DATE_RE.finditer(line):
            matched = m.group(0)
            shown = matched if unmask else _mask_value("계좌의심", matched)
            results.append(
                PiiMatch(
                    "계좌의심(날짜·타임스탬프)", "851?", shown,
                    _context(line, m.start(), m.end(), shown),
                )
            )
            if len(results) >= max_hits:
                return results
    return results


def scrub_pii(text: str) -> str:
    """텍스트에서 PII 규칙에 걸리는 부분을 마스킹 형태로 치환한다(프롬프트 주입 전 위생).

    :func:`scan_pii` 와 동일한 라인 단위·``whole_line`` 가드를 적용한다. 형식 보존
    마스킹(``@도메인``·자릿수 유지)이라 LLM의 컬럼·형식 추론 신호는 남기면서
    FabriX 개인정보 필터의 오탐/차단만 예방한다. PII에 안 걸리는 값(OS명·호스트명·
    숫자·상태 등)은 바이트 무변경으로 통과한다.

    ``SECURITY_PII_SCRUB_SUSPECT_DATES=true`` 옵트인 시 날짜·타임스탬프의 구분자를
    점으로 치환해 서버 계좌번호(851) 광폭 매칭을 회피한다(D-155 후속3 — 값·자릿수
    보존, 코드 재배포 없이 env로 활성화).
    """
    if not text:
        return text
    scrub_dates = is_scrub_suspect_dates_enabled()
    out: List[str] = []
    for line in text.split("\n"):
        # 날짜 무해화를 먼저 적용 — 계좌 룰(정식)이 날짜형을 오탐 마스킹하기 전에
        # 구분자 치환으로 형식을 보존한다(마스킹보다 정보 손실이 적은 경로 우선).
        if scrub_dates:
            try:
                line = _SUSPECT_DATE_RE.sub(lambda m: _defang_date(m.group(0)), line)
            except Exception as exc:
                logger.debug("날짜 무해화 실패: %r", exc)
        for rule in PII_RULES:
            if rule.whole_line and len(line) > _WHOLE_LINE_MAX:
                continue
            try:
                line = rule.pattern.sub(
                    lambda m: _mask_value(rule.name, m.group(0)), line
                )
            except Exception as exc:  # 스크럽이 본 흐름을 깨지 않도록
                logger.debug("PII 스크럽 규칙 실패(%s): %r", rule.name, exc)
        out.append(line)
    return "\n".join(out)


def _format_scan(matches: List[PiiMatch]) -> str:
    if not matches:
        return "로컬 규칙 일치 없음(서버측 규칙이 더 넓거나, 응답 본문에서 탐지됐을 수 있음)"
    by_name: dict[str, List[PiiMatch]] = {}
    for mch in matches:
        by_name.setdefault(mch.name, []).append(mch)
    parts = []
    for name, items in by_name.items():
        rid = items[0].rule_id
        ctxs = "; ".join(i.context for i in items[:3])
        parts.append(f"{name}(룰{rid})×{len(items)} [{ctxs}]")
    return " | ".join(parts)


def diagnose_blocked_prompt(
    sections: dict[str, str], max_per_rule: int = 5
) -> str:
    """차단된 요청의 프롬프트를 **섹션별**로 스캔해 원인 후보를 특정한다 (D-155).

    "프롬프트에 PII성 텍스트 포함"만으로는 어느 재료(스키마 샘플/유사어/질의/매핑)가
    걸렸는지 알 수 없다(2026-08-05 폐쇄망 실측 — FabriX 필터 정책 강화 후 SQL 생성
    전면 차단인데 원인 특정 불가). 섹션 이름과 함께 유형·룰ID·앞뒤 문맥을 반환해
    "어느 블록의 어떤 값"인지까지 로그·에러 메시지에서 바로 읽히게 한다.

    Args:
        sections: {섹션명: 텍스트} — 예: {"시스템(스키마·샘플·유사어)": ..., "사용자(질의·매핑)": ...}
        max_per_rule: 섹션·규칙당 최대 보고 건수

    Returns:
        섹션별 진단 문자열. 전 섹션 일치 없음이면 서버측 규칙 확대 가능성 안내
        (이때는 filterBlockReason의 policy_id/ko가 유일한 단서 — [PII-FILTER] 로그 확인).
    """
    parts: List[str] = []
    suspect_parts: List[str] = []
    for name, text in sections.items():
        if not text:
            continue
        matches = scan_pii(text, max_per_rule=max_per_rule)
        if matches:
            parts.append(f"《{name}》 {_format_scan(matches)}")
        # 정식 규칙과 별개로 계좌(851) 광폭 매칭 의심 형태(날짜·타임스탬프)도 보고
        # (D-155 후속3 — "계좌번호 차단인데 계좌 유사 문자열 부재" 실측 대응)
        suspects = scan_account_suspects(text)
        if suspects:
            suspect_parts.append(f"《{name}》[의심] {_format_scan(suspects)}")
    if suspect_parts:
        parts.append(
            "계좌번호(851) 광폭 매칭 의심(숫자 많은 라인의 날짜·타임스탬프 — "
            "확정 시 SECURITY_PII_SCRUB_SUSPECT_DATES=true 로 무해화): "
            + " ‖ ".join(suspect_parts)
        )
    if parts:
        return " ‖ ".join(parts)
    return (
        "로컬 규칙(9종)·의심 형태 일치 없음 — 서버측 필터 정책이 로컬 규칙보다 넓어진 것으로 추정. "
        f"전송 원문·응답 전문은 {_DUMP_DIR}/ 덤프 파일에서 직접 확인하고, 트리거 미상이면 "
        "scripts/pii_probe.py <덤프파일>로 이등분 재현해 차단 구간을 특정. "
        "확정된 유형은 docs/pii_filtering_rules.md·PII_RULES에 반영"
    )


def _rules_for_type(type_name: str) -> List[PiiRule]:
    """서버 차단 유형명(policy_id 해석 결과)을 로컬 PiiRule로 매핑한다.

    정책표 표기("이메일주소")와 룰 표기("이메일 주소")의 공백 차이·복합 표기("A/B",
    검증 슬롯 정책 번호 중복)를 흡수한다. 매핑 불가(미상·포털 정책 등)면 빈 목록.
    """
    names = {t.strip().replace(" ", "") for t in (type_name or "").split("/") if t.strip()}
    return [r for r in PII_RULES if r.name.replace(" ", "") in names]


def _diagnose_reasons_vs_prompt(
    reasons: List[dict], prompt: Optional[str]
) -> tuple[str, bool]:
    """서버가 반환한 차단 유형별로 **그 유형의 로컬 정규식**을 프롬프트에 역적용한다.

    (D-155 후속5) "차단 사유(policy_id)"와 "프롬프트의 어떤 값" 사이를 직접 연결하는
    표적 대조. 셋 중 하나로 판정한다:
    ① 매칭 → 걸린 값(마스킹)·문맥을 그대로 보고 — 원인 값 특정 완료.
    ② 대응 룰이 있는데 매칭 0건 → **알려진 필터 기준으로는 차단될 수 없는 프롬프트가
       차단됨** — 서버 필터가 문서와 다르게 변경/추가된 것(정책 무단 변경 포함)으로
       판단하고 그렇게 명시한다.
    ③ 유형 자체가 미상(정책표 밖) → 로컬 대응 룰 없음으로 명시.

    Returns:
        (판정 문자열, 매칭 존재 여부)
    """
    if not prompt:
        return "프롬프트 미제공 — 원인 값 대조 불가", False
    parts: List[str] = []
    any_match = False
    seen: set = set()
    for r in reasons:
        type_name = r.get("type") or "미상"
        if type_name in seen:
            continue
        seen.add(type_name)
        rules = _rules_for_type(type_name)
        if not rules:
            parts.append(
                f"유형 '{type_name}': 로컬 대응 룰 없음(정책표 밖 신규/미상 필터) — "
                "filter_log_id 서버 조회로만 식별 가능"
            )
            continue
        matches = scan_pii(prompt, max_per_rule=5, rules=rules)
        if matches:
            any_match = True
            parts.append(
                f"{type_name}: 프롬프트 매칭 {len(matches)}건 → {_format_scan(matches)}"
            )
        else:
            rule_ids = ",".join(rule.rule_id for rule in rules)
            parts.append(
                f"⚠ {type_name}(룰{rule_ids}): 로컬 정규식 매칭 0건 — 알려진 필터 "
                "기준으로는 차단될 수 없는 프롬프트가 차단됨. 서버 필터가 문서"
                "(docs/pii_filtering_rules.md)와 다르게 변경/추가된 것으로 판단 — "
                "filter_log_id 서버 조회 또는 pii_probe 재현으로 실제 룰 확인 필요"
            )
    if not parts:
        return "차단 유형 정보 없음", False
    return " ‖ ".join(parts), any_match


# ---------------------------------------------------------------------------
# 차단 원문 덤프 (D-155 후속1)
# ---------------------------------------------------------------------------
_DUMP_DIR = "logs/pii_block"


def dump_blocked_payload(
    *,
    response_text: str = "",
    prompt_sections: Optional[dict[str, str]] = None,
    prompt: Optional[str] = None,
    detail: str = "",
    diagnosis: str = "",
    where: str = "",
    dump_dir: str = _DUMP_DIR,
) -> Optional[str]:
    """차단된 요청의 프롬프트·응답 **전문(무마스킹)**을 파일로 덤프하고 경로를 반환한다.

    서버측 필터 정책이 로컬 규칙보다 넓으면 로그 발췌·로컬 스캔으로는 트리거를 특정할
    수 없다(2026-08-05 폐쇄망 실측: "일치 없음"만 반복). FabriX로 이미 전송한 것과 동일한
    텍스트를 서버 로컬 파일로 남겨 운영자가 직접 대조하거나 scripts/pii_probe.py 로
    이등분 재현할 수 있게 한다. 반출 금지 주의를 파일 머리에 명기한다.

    Returns:
        덤프 파일 경로 (기능 OFF·실패 시 None — 덤프 실패가 본 흐름을 깨지 않는다)
    """
    if not is_block_dump_enabled():
        return None
    try:
        from datetime import datetime
        from pathlib import Path

        d = Path(dump_dir)
        d.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        safe_where = re.sub(r"[^A-Za-z0-9_.-]", "_", where or "block")
        path = d / f"{ts}_{safe_where}.log"
        parts: List[str] = [
            "# FabriX PII 필터 차단 덤프 (D-155 후속1)",
            f"# where: {where}",
            f"# 탐지 상세(policy/rule id): {detail}",
            f"# 원인 값 대조: {diagnosis}" if diagnosis else "# 원인 값 대조: (미수행)",
            "# 주의: 아래는 FabriX로 전송/수신된 원문 그대로다(마스킹 없음). 서버 밖 반출 금지.",
            "# 트리거 특정: scripts/pii_probe.py 로 이 파일의 프롬프트 구간을 이등분 재현.",
            "",
            "===== 응답(차단) 전문 =====",
            response_text or "(없음)",
        ]
        for name, text in (prompt_sections or {}).items():
            parts += ["", f"===== 프롬프트 섹션: {name} =====", text or ""]
        if prompt and not prompt_sections:
            parts += ["", "===== 프롬프트 전문 =====", prompt]
        path.write_text("\n".join(parts), encoding="utf-8")
        # 보존 상한 — 전면 차단 상황에서 호출마다 파일이 쌓이므로 최신 100개만 유지
        # (무상한 누적 금지 — 데몬류 sweep 원칙과 동형)
        files = sorted(d.glob("*.log"))
        for old in files[:-100]:
            try:
                old.unlink()
            except OSError:
                pass
        return str(path)
    except Exception as exc:  # 덤프는 진단 보조 — 실패해도 본 흐름 유지
        logger.warning("PII 차단 덤프 실패: %r", exc)
        return None


# ---------------------------------------------------------------------------
# FabriX 응답 해석
# ---------------------------------------------------------------------------
def _find_all(obj: Any, key: str) -> List[Any]:
    found: List[Any] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                found.append(v)
            else:
                found.extend(_find_all(v, key))
    elif isinstance(obj, list):
        for it in obj:
            found.extend(_find_all(it, key))
    return found


def _first_scalar(obj: Any) -> Any:
    """dict/list 안에서 첫 스칼라 값을 뽑는다(policy_id/ko가 중첩돼 올 때 대비)."""
    if isinstance(obj, dict):
        for v in obj.values():
            r = _first_scalar(v)
            if r is not None:
                return r
        return None
    if isinstance(obj, list):
        for v in obj:
            r = _first_scalar(v)
            if r is not None:
                return r
        return None
    return obj


def _normalize_reasons(result: Any) -> List[dict]:
    """응답 어디에 있든 filter 차단 사유를 {ko, policy_id, filter_log_id, type}로 정규화.

    가이드(2026-08-05) 실측 응답은 두 표기가 공존한다: 비스트림 `filterBlockReason`
    (camelCase)·스트림 `filter_block_reason`(snake_case). 스트림은 **통과 라인에도**
    result_code=FR-200("The content was passed")인 reason 객체가 실리므로, FR-400
    (차단)·policy_id 존재·"blocked" 문구인 것만 차단 사유로 인정한다 — 통과 라인을
    사유로 오인하면 전 청크가 차단 판정된다.
    """
    reasons: List[dict] = []
    for key in ("filterBlockReason", "filter_block_reason"):
        for block in _find_all(result, key):
            candidates = block if isinstance(block, list) else [block]
            for cand in candidates:
                if not isinstance(cand, dict):
                    cand = {"ko": str(cand)}
                result_code = str(cand.get("result_code") or "").upper()
                message = str(cand.get("message") or "")
                pids = cand.get("policy_id")
                pid_list = pids if isinstance(pids, list) else [pids]
                # 통과 표시(FR-200/passed·policy_id null)는 차단 사유가 아니다
                is_blocked_reason = (
                    result_code == "FR-400"
                    or "blocked" in message.lower()
                    or any(p not in (None, "", "null") for p in pid_list)
                )
                if not is_blocked_reason:
                    continue
                for pid in pid_list:
                    reasons.append({
                        "ko": cand.get("ko") or message or "",
                        "policy_id": pid,
                        "filter_log_id": cand.get("filter_log_id") or cand.get("filterLogId") or "",
                        "result_code": result_code,
                        "type": policy_type(pid),
                    })
    return reasons


# APIM 필터 차단 형태(가이드 2026-08-05): 400 에러 detail에
# "{'error': '민감정보 감지됨: ', 'rule_name': '핸드폰번호', 'catched_text': '...'}"
_APIM_BLOCK_MARK = "민감정보 감지됨"
_APIM_FIELD_RE = re.compile(r"'(rule_name|catched_text)'\s*:\s*'([^']*)'")


def _apim_reasons(blob: str) -> List[dict]:
    """APIM 필터 차단 문자열에서 rule_name·catched_text를 사유로 추출한다."""
    if _APIM_BLOCK_MARK not in (blob or ""):
        return []
    fields = dict(_APIM_FIELD_RE.findall(blob))
    return [{
        "ko": _APIM_BLOCK_MARK,
        "policy_id": None,
        "filter_log_id": "",
        "result_code": "APIM-400",
        "type": fields.get("rule_name", "미상"),
        "catched_text": fields.get("catched_text", ""),
    }]


def is_filter_blocked(result: Any = None, raw_text: Optional[str] = None) -> bool:
    """응답(dict) 또는 원문 문자열이 PII 필터 차단인지 판정.

    스트림 통과 라인(FR-200 reason 동봉)은 차단으로 오인하지 않는다(가이드 실측 shape).
    """
    if isinstance(result, (dict, list)):
        for st in _find_all(result, "status"):
            if str(st).upper() == "FILTER_INVALID":
                return True
        if _normalize_reasons(result):
            return True
    blob = raw_text or ""
    if isinstance(result, dict):
        blob += " " + str(result.get("content", ""))
    low = blob.lower()
    return (
        "blocked by the filter" in low
        or "filter_invalid" in low
        or "filterblockreason" in low
        or _APIM_BLOCK_MARK in blob
    )


def log_filter_block_if_any(
    log: Optional[logging.Logger] = None,
    *,
    result: Any = None,
    raw_text: Optional[str] = None,
    prompt: Optional[str] = None,
    prompt_sections: Optional[dict[str, str]] = None,
    where: str = "",
) -> bool:
    """응답이 PII 필터로 차단됐으면 사람이 읽을 수 있는 경고 1건을 남긴다.

    Args:
        log: 사용할 로거(없으면 이 모듈 로거).
        result: 파싱된 응답(dict) 또는 스트림 라인(dict).
        raw_text: 파싱 전 원문(스트림 라인 등).
        prompt: 전송한 프롬프트 전체. 차단 상세(policy_id)가 없을 때 어떤
            텍스트가 걸렸는지 로컬 스캔으로 특정하는 데 쓴다.
        prompt_sections: {섹션명: 텍스트}. 주면 전체 스캔 대신 섹션별 진단
            (:func:`diagnose_blocked_prompt`)으로 원인 블록까지 특정한다(D-155).
        where: 로그 위치 태그(예: "_agenerate").

    Returns:
        차단이면 True. (기능 OFF이거나 차단이 아니면 False)
    """
    log = log or logger
    if not is_detection_enabled():
        return False
    if not is_filter_blocked(result, raw_text):
        return False

    reasons = _normalize_reasons(result)
    if not reasons:
        # APIM 필터 변형(400 detail 문자열) — rule_name·catched_text가 유일한 상세
        _blob = (raw_text or "") + (
            " " + str(result.get("content", "")) if isinstance(result, dict) else ""
        )
        reasons = _apim_reasons(_blob)
    if reasons:
        detail = "; ".join(
            f"{r['type']}(policy_id={r['policy_id']}"
            + (f", result_code={r['result_code']}" if r.get("result_code") else "")
            + (f", log_id={r['filter_log_id']}" if r['filter_log_id'] else "")
            + (f", 사유='{r['ko']}'" if r['ko'] else "")
            + (f", 탐지문자열='{scrub_pii(r['catched_text'])}'" if r.get("catched_text") else "")
            + ")"
            for r in reasons
        )
    else:
        detail = "차단 상세(policy_id/filter_log_id) 미제공 — 아래 원문 응답에서 직접 확인"

    # 원문 응답 전체(D-155 후속1) — 차단 응답은 안내문뿐이라 짧다. 정규화 파서가 모르는
    # 키(정책 개편으로 필드명이 바뀐 경우 등)도 그대로 보이도록 JSON 전문을 남긴다.
    import json as _json

    if isinstance(result, (dict, list)):
        try:
            _raw = _json.dumps(result, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            _raw = str(result)
    else:
        _raw = raw_text or ""
    raw_repr = scrub_pii(" ".join(_raw.split()))
    if len(raw_repr) > 2000:
        raw_repr = raw_repr[:2000] + "…(절단 — 전문은 덤프 파일)"

    # ── 원인 값 대조 (D-155 후속5) ─────────────────────────────────────────────
    # 서버 사유(policy_id→유형)가 있으면 **그 유형의 로컬 정규식만** 프롬프트에
    # 역적용해 "정확히 어떤 값이 걸렸는지"를 특정한다. 표적 대조가 0건이면
    # "알려진 필터로는 차단 불가한 프롬프트가 차단됨(서버 필터 변경 의심)"을 명시하고
    # 전수+의심 스캔을 보조로 붙인다. 사유가 없는 변형은 전수 스캔으로 폴백.
    _joined_prompt = prompt or (
        "\n\n".join(f"[{k}]\n{v}" for k, v in (prompt_sections or {}).items()) or None
    )
    if reasons:
        cause, _matched = _diagnose_reasons_vs_prompt(reasons, _joined_prompt)
        if not _matched and _joined_prompt:
            _aux = (
                diagnose_blocked_prompt(prompt_sections)
                if prompt_sections else _format_scan(scan_pii(_joined_prompt))
            )
            cause += f" | 보조(전수+의심 스캔): {_aux}"
    elif prompt_sections:
        cause = diagnose_blocked_prompt(prompt_sections)
    elif prompt:
        _matches = scan_pii(prompt)
        _suspects = scan_account_suspects(prompt)
        if _matches or _suspects:
            cause = _format_scan(_matches + _suspects)
        else:
            cause = (
                "알려진 필터(9종)·의심 형태 어느 것에도 매칭되지 않는 프롬프트가 차단됨 — "
                "적용 필터 식별 불가(서버 필터 변경/신규 필터). filter_log_id 서버 조회 "
                "또는 pii_probe 재현 필요"
            )
    else:
        cause = "프롬프트 미제공(원인 값 대조 생략)"

    # filter_log_id = 서버측 필터 로그 레코드 ID — 클라이언트 응답에는 탐지 문자열이
    # 실리지 않으므로(APIM 경로만 catched_text 제공), 표적 대조 0건일 때의 무추정
    # 확인 경로는 FabriX 테넌트 관리 콘솔에서 이 ID로 조회하는 것뿐이다(D-155 후속4).
    _log_ids = sorted({r["filter_log_id"] for r in reasons if r.get("filter_log_id")})
    server_lookup = (
        f"서버측 탐지 내역 조회: FabriX 관리 콘솔 필터 로그에서 filter_log_id={','.join(_log_ids)} 조회"
        if _log_ids else
        "filter_log_id 미제공 — 서버측 조회 불가, scripts/pii_probe.py 이등분 재현으로 특정"
    )

    # 전송 원문·응답 전문 파일 덤프(D-155 후속1) — 사유·대조 결과를 함께 기록해
    # 파일 하나로 "프롬프트 전문 + policy/rule id + 원인 값" 식별이 닫히게 한다.
    dump_path = dump_blocked_payload(
        response_text=_raw,
        prompt_sections=prompt_sections,
        prompt=prompt,
        detail=detail,
        diagnosis=cause,
        where=where,
    )

    where_tag = f"[{where}] " if where else ""
    mask_tag = "원문" if is_unmask_enabled() else "마스킹"
    log.warning(
        "[PII-FILTER] %sFabriX 개인정보 필터 차단\n"
        "  ├ 사유(서버 응답): %s\n"
        "  ├ 원인 값 대조(%s): %s\n"
        "  ├ %s\n"
        "  ├ 원문 응답: %s\n"
        "  └ 전문 덤프: %s",
        where_tag, detail, mask_tag, cause, server_lookup, raw_repr,
        dump_path or "OFF(SECURITY_PII_BLOCK_DUMP_ENABLED=false) 또는 실패",
    )
    return True
