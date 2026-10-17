"""APM 처리기 공통 상수(처리기·시스템 이름 · 고지 문구 · 결과 키)와 활성·어휘 순수 함수.

plans/180 W1 — `src/orchestration/apm_query.py`에서 순수 이동(본문·주석·평가 순서 불변).
옛 경로 `src.orchestration.apm_query`가 같은 이름으로 재노출한다(D-337 ① shim).
"""

from __future__ import annotations

from typing import Any

from src.domain import disclosure as disc

#: 처리기 이름(분해 어휘 — 활성일 때만 렌더) · 시스템 코드(레지스트리 `solutions[apm]`).
APM_QUERY_AGENT = "apm_query"
APM_SYSTEM = "apm"
#: 보기를 고르지 않았고 **이번 턴 대상(식별자·선행 결과)이 있을 때**의 기본 보기(영역 기본 —
#: was_performance). 대상이 없으면 목록 보기(`INSTANCES_VIEW`)다(plans/132 N-4 · G-3 · D-293).
DEFAULT_VIEW = "apm.app_health"
#: 대상 없이 부를 수 있는 목록 보기 — 대상 미지정이면 코드가 먼저 부른다.
INSTANCES_VIEW = "apm.instances"
#: 결과 메타(감사·계획 요약·후속 조합이 읽는다).
META_KEY = "apm_query"
#: 대상 미지정 첫 홉의 부하 순위 보기(plans/134 W6 ④ — 현재 부하 상위 N개만 대상).
RANKING_VIEW = "apm.ranking"
#: 기간 순위 전용 지표(게이트웨이 `PERIOD_RANKING_METRICS` 중 실시간 이름이 아닌 것 · W6 A-4) —
#: 기간 없이 보내면 게이트웨이가 `invalid_argument`로 거부하므로 조회하지 않고 되묻는다(기본 지표로
#: 바꿔 조회하지 않는다).
PERIOD_ONLY_RANKING_METRICS = ("calls", "failures", "failure_rate", "max_response_time_ms")
PERIOD_ONLY_ASK_NOTE = ("실패 수·실패율·최대 응답시간·호출 수 순위는 기간 통계로만 냅니다 — 기간을"
                        " 함께 알려 주세요(예 「어제 실패율 높은 WAS 5개」)")
#: 비교 구간을 정하지 못한 기간 비교 — 조회하지 않고 되묻는다(plans/134 W6 A-1).
COMPARE_ASK_NOTE = ("비교할 두 기간을 정하지 못해 조회하지 않았습니다 — 「지난주 대비」·"
                    "「어제와 오늘」처럼 기간을 말해 주세요")
#: 비교할 기간의 경과 시간이 0(정시 직후 「지난주 대비」·「이번 주」) — 조회하지 않고
#: 되묻는다(V6-5).
COMPARE_ELAPSED_NOTE = ("이번 기간은 아직 지난 시간이 없어 비교할 수 없습니다 — "
                        "「지난주와 지지난주」처럼 비교할 기간을 지정해 주세요")
#: 분 단위 기간을 시 단위 통계 구간으로 넓히자 두 기간이 겹칠 때의 되묻기 머리(plans/159 · D-328 ③)
#: — 뒤에 넓힌 두 구간을 붙인다.
COMPARE_OVERLAP_NOTE = ("두 기간이 겹쳐 비교하지 않았습니다 — 비교는 시 단위 통계라 분 단위 기간을 "
                        "직전 정시까지의 시간 구간으로 넓힙니다")
#: 기간을 줬는데 행이 0건일 때의 고지 — 보존 기간 만료로 단정하지 않는다(plans/134 W6 M-7 · W10).
EMPTY_PERIOD_NOTE = ("요청 기간에 행이 없습니다 — 원천 보존 기간은 확인 전(W10)이라 기간 밖인지는"
                     " 단정하지 않습니다")
CURRENT_ONLY_NOTE = "현재값 기준입니다 — 현재값만 있는 보기라 요청 기간의 값이 아닙니다"
HOURLY_NOTE = "시 단위 통계라 요청 구간보다 넓은 정시 경계로 집계한 값입니다"
#: 결과에 싣는 결정적 줄(판정·집계)의 키 — 집계기가 최종 답에 그대로 붙인다(plans/134 M-1).
ANSWER_LINES_KEY = "answer_lines"
#: 보기 id 없이 낸 평면 조건 묶음을 처리기까지 옮기는 `view_args` 예약 키(W1 검증 L-1).
FLAT_VIEW_ARGS_KEY = "*"
#: 보기의 고정 고지 문구(plans/134 W2 — `ViewSpec.notices`의 kind → 문구).
_NOTICE_TEXT = {
    disc.APM_CHANGE_DETECTION: ("변경 감지 시각(데이터 서버가 소스 변경을 알아챈 시각)이며"
                                " 배포 시각으로 확정할 수 없습니다"),
    disc.APM_HOURLY_RESOLUTION: HOURLY_NOTE,
}
#: 채팅 표·LLM 입력에 싣는 셀 문자열 상한(자) — 전문은 CSV(`query_results`)·결과 파일에 남는다.
DISPLAY_CELL_MAX = 300
#: 일반 현황 영역 — 보기를 고르지 않아도 기본 보기로 답한다(D-293 · SPEC §6.4 ③).
GENERAL_AREAS = frozenset({"was_performance", "was_instance"})
#: 선택 재시도 미해결 시 되물을 후보 보기 수(SPEC §6.4 ②).
MAX_SELECTION_CANDIDATES = 3
#: 봉투에서 (보기, 대상)별로 옮기는 집계 키(SPEC §7.1 · 전 대상 순위 `provisional`·이벤트
#: `coverage` — plans/134 W3).
_AGGREGATE_KEYS = ("summary", "hourly", "errors_by_type", "window", "sources", "partial",
                   "artifact", "job", "hour_start", "hour_end", "provisional", "coverage")


# ── 활성 · 어휘 ──────────────────────────────────────────────────────────────

def apm_endpoint(app_config: Any) -> tuple[str, str | None] | None:
    """(URL, 토큰) 또는 None(비활성). 설정 대역(MagicMock)은 비활성으로 읽는다."""
    getter = getattr(getattr(app_config, "dbhub", None), "source_endpoint", None)
    if not callable(getter):
        return None
    try:
        endpoint = getter(APM_SYSTEM)
    except Exception:  # noqa: BLE001 — 설정 대역·깨진 설정은 비활성
        return None
    if isinstance(endpoint, tuple) and endpoint and isinstance(endpoint[0], str) and endpoint[0]:
        return endpoint
    return None


def sanitize_sources(raw: Any) -> list[str]:
    """분해 `sources` → 소스 id 목록 — 형태만 정제한다(검증은 처리기 · plans/134 M-5).

    앞뒤 공백을 지우고 소문자로 맞춘다(레지스트리 소스 id는 소문자 슬러그). 순서를 지키고 중복을
    지운다. 문자열이 아닌 항목도 버리지 않는다 — 버리면 「무효한 소스만 지목」이 「지목 없음」
    (전 소스)으로 넓어진다. 처리기가 모르는 id로 세어 알린다. 다시 걸러도 결과가 같다.
    """
    items = [raw] if isinstance(raw, str) else (raw if isinstance(raw, (list, tuple)) else [])
    kept: list[str] = []
    for item in items:
        if item is None:  # 미지정
            continue
        text = (item if isinstance(item, str) else str(item)[:40]).strip().lower()
        if text and text not in kept:
            kept.append(text)
    return kept
