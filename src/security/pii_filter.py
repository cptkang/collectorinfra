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
        "핸드폰번호", "855",
        re.compile(r"(?<!\d)(01[016789])(?:[-\s]*\d{7,8})(?!\d)"),
    ),
    PiiRule(
        "계좌번호", "851",
        re.compile(
            r"^(?!.*\b\d{2}[-\s]\d{2}[-\s]\d{3}[-\s]\d{5}\b)(?!.*[:~])"
            r"(?=(?:\D*\d){10,14}\D*$).*?(?<!\d)(?:\d+(?:-\d+)+|\d+(?: \d+)+)(?!\d)[^\d]*$"
        ),
        whole_line=True,
    ),
    PiiRule(
        "카드번호", "852",
        re.compile(r"(?<!\d)\d{4}([-\s])\d{4}\1\d{4}\1\d{4}(?!\d)"),
    ),
    PiiRule(
        "이메일 주소", "853",
        re.compile(
            r"[A-Za-z0-9]+(?:[._%+-][A-Za-z0-9]+)*@"
            r"(?!(?:kbonecloud\.com|kbfg\.com)\b)"
            r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*(?:\.[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)+"
        ),
    ),
]

_WHOLE_LINE_MAX = 120  # whole_line 규칙(계좌 등)은 짧은 라인에만 적용(백트래킹·오탐 방지)


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


def scan_pii(text: str, max_per_rule: int = 5, unmask: Optional[bool] = None) -> List[PiiMatch]:
    """프롬프트/텍스트를 로컬 규칙으로 스캔해 PII 후보를 반환한다.

    라인 단위로 적용한다(PII 값은 개행을 넘지 않고, 값-전체 규칙의 ^/$ 앵커가
    라인 경계로 스코프되어 오탐·백트래킹을 줄인다).

    Args:
        unmask: True면 감지 문자열을 원문 그대로 담는다. None(기본)이면
            ``SECURITY_PII_FILTER_LOG_UNMASK`` 설정을 따른다(기본 마스킹).
    """
    if not text:
        return []
    if unmask is None:
        unmask = is_unmask_enabled()
    results: List[PiiMatch] = []
    lines = text.split("\n")
    for rule in PII_RULES:
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


def scrub_pii(text: str) -> str:
    """텍스트에서 PII 규칙에 걸리는 부분을 마스킹 형태로 치환한다(프롬프트 주입 전 위생).

    :func:`scan_pii` 와 동일한 라인 단위·``whole_line`` 가드를 적용한다. 형식 보존
    마스킹(``@도메인``·자릿수 유지)이라 LLM의 컬럼·형식 추론 신호는 남기면서
    FabriX 개인정보 필터의 오탐/차단만 예방한다. PII에 안 걸리는 값(OS명·호스트명·
    숫자·상태 등)은 바이트 무변경으로 통과한다.
    """
    if not text:
        return text
    out: List[str] = []
    for line in text.split("\n"):
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
    """응답 어디에 있든 filterBlockReason 들을 {ko, policy_id, filter_log_id, type}로 정규화."""
    reasons: List[dict] = []
    for block in _find_all(result, "filterBlockReason"):
        candidates = block if isinstance(block, list) else [block]
        for cand in candidates:
            if not isinstance(cand, dict):
                cand = {"ko": str(cand)}
            pids = cand.get("policy_id")
            pid_list = pids if isinstance(pids, list) else [pids]
            for pid in pid_list:
                reasons.append({
                    "ko": cand.get("ko") or cand.get("message") or "",
                    "policy_id": pid,
                    "filter_log_id": cand.get("filter_log_id") or cand.get("filterLogId") or "",
                    "type": policy_type(pid),
                })
    return reasons


def is_filter_blocked(result: Any = None, raw_text: Optional[str] = None) -> bool:
    """응답(dict) 또는 원문 문자열이 PII 필터 차단인지 판정."""
    if isinstance(result, (dict, list)):
        for st in _find_all(result, "status"):
            if str(st).upper() == "FILTER_INVALID":
                return True
        if _find_all(result, "filterBlockReason"):
            return True
    blob = raw_text or ""
    if isinstance(result, dict):
        blob += " " + str(result.get("content", ""))
    low = blob.lower()
    return "blocked by the filter" in low or "filter_invalid" in low or "filterblockreason" in low


def log_filter_block_if_any(
    log: Optional[logging.Logger] = None,
    *,
    result: Any = None,
    raw_text: Optional[str] = None,
    prompt: Optional[str] = None,
    where: str = "",
) -> bool:
    """응답이 PII 필터로 차단됐으면 사람이 읽을 수 있는 경고 1건을 남긴다.

    Args:
        log: 사용할 로거(없으면 이 모듈 로거).
        result: 파싱된 응답(dict) 또는 스트림 라인(dict).
        raw_text: 파싱 전 원문(스트림 라인 등).
        prompt: 전송한 프롬프트 전체. 차단 상세(policy_id)가 없을 때 어떤
            텍스트가 걸렸는지 로컬 스캔으로 특정하는 데 쓴다.
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
    if reasons:
        detail = "; ".join(
            f"{r['type']}(policy_id={r['policy_id']}"
            + (f", log_id={r['filter_log_id']}" if r['filter_log_id'] else "")
            + (f", 사유='{r['ko']}'" if r['ko'] else "")
            + ")"
            for r in reasons
        )
    else:
        detail = "차단 상세(policy_id/filter_log_id) 미제공 — 응답 status/content만 차단 표시"

    scan = _format_scan(scan_pii(prompt)) if prompt else "프롬프트 미제공(로컬 스캔 생략)"
    where_tag = f"[{where}] " if where else ""
    mask_tag = "원문" if is_unmask_enabled() else "마스킹"
    log.warning(
        "[PII-FILTER] %sFabriX 개인정보 필터 차단 — 탐지=%s | 로컬 프롬프트 스캔(%s)=%s",
        where_tag, detail, mask_tag, scan,
    )
    return True
