"""조치 권고 — human-gated 후보 제시 (Plan 02 §9 · D-011 예약분 · D-035 계승).

**순수 도메인 모듈**(stdlib만·외부 의존 없음). severity_judge가 도구 원시 출력에서 매칭한
시그니처(`Signal`)를 입력으로, 시그니처별 **조치 후보**를 결정적 표에서 조회해 근거·신뢰도·
위험도와 함께 제시한다. LLM 서술은 입력이 아니다 — 권고 목록에 환각이 개입할 수 없다.

**실행 경로 없음(D-003·D-011)**: 이 모듈은 문자열만 만든다. 명령을 실행하거나 실행 API에
전달하는 코드 경로가 존재하지 않으며, 그 부재를 테스트로 고정한다(`test_remediation.py`).
자동 실행은 읽기전용 원칙의 예외 결정 + 이중 승인 + 롤백·blast radius 설계가 선행돼야 하고
본 계획 범위 밖이다.

위험도 등급(§9):
    low    — renice·로그 로테이션 등 되돌리기 쉬운 조치
    medium — 프로세스 종료(TERM 시그널) 등 단일 프로세스 영향
    high   — 서비스 재기동·설정 변경 등 서비스 영향

**고위험 × 저신뢰는 권고하지 않는다** — "검토 필요"로만 표기한다(§9). 신뢰도는 시그니처
category에서 온다(strong=high · medium=medium).

WAS 후보(plans/87 §5.4(c) · J3): 게이트웨이 `was_signals` kind별 후보를 가역성 순으로 둔다. WAS 항목은
**검증 방법**·**롤백**을 함께 담는다(L2 §5.7의 입력). OS 항목은 두 칸이 비어 렌더가 종전과 바이트 동일하다.
"""

from __future__ import annotations

from dataclasses import dataclass

from sre_agent.domain.severity_signatures import Signal

# 위험도 등급.
RISK_LOW = "low"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"

# 시그니처 category → 권고 신뢰도.
_CONFIDENCE_BY_CATEGORY: dict[str, str] = {"strong": "high", "medium": "medium"}

# 고위험 조치를 정식 권고로 낼 수 있는 최소 신뢰도(그 미만이면 "검토 필요" 강등).
_HIGH_RISK_MIN_CONFIDENCE = "high"


@dataclass(frozen=True)
class Remediation:
    """단일 조치 후보(제시 전용 — 실행 대상이 아니다).

    action:      운영자가 검토할 조치 문구(명령 그 자체가 아니라 조치 서술).
    risk:        RISK_LOW | RISK_MEDIUM | RISK_HIGH.
    confidence:  "high" | "medium" — 근거 시그니처 category에서 결정적으로 도출.
    rationale:   근거(시그니처 라벨 + 매칭 발췌) — 인용 없는 권고를 만들지 않는다.
    review_only: True면 정식 권고가 아니라 "검토 필요" 표기(고위험×저신뢰).
    verification: 조치 뒤 회복을 확인하는 방법(서술 · 비면 렌더하지 않는다 — WAS 항목만).
    rollback:    조치를 되돌리는 방법 또는 되돌릴 수 없다는 사실(서술 · 비면 렌더하지 않는다).
    """

    action: str
    risk: str
    confidence: str
    rationale: str
    review_only: bool = False
    verification: str = ""
    rollback: str = ""

    def to_line(self) -> str:
        """브리핑 권고 항목 한 줄로 렌더한다(build_briefing의 remediation 인자 형식)."""
        head = "[검토 필요] " if self.review_only else ""
        line = (
            f"{head}{self.action} "
            f"(위험도 {self.risk}·신뢰도 {self.confidence}) — 근거: {self.rationale}"
        )
        if self.verification:
            line += f" · 검증: {self.verification}"
        if self.rollback:
            line += f" · 롤백: {self.rollback}"
        return line


# 시그니처 name → 조치 후보(action, risk[, verification, rollback]). OS 카탈로그는
# severity_signatures.SIGNATURES와 1:1로 대응하고, WAS 카탈로그는 게이트웨이 was_signals kind
# (SPEC-apm-gateway §4)와 대응한다. 미등재 시그니처는 권고를 만들지 않는다(추측 금지 — 근거 있는 것만).
# action은 **조치 서술**이며 실행 가능한 명령 문자열이 아니다 — 프로세스 종료·서비스 재기동을
# 셸 명령 형태로 적지 말 것(복사·실행으로 이어진다). 경계 테스트가 명령 리터럴을 차단하며,
# 주석에 예시로 적는 것조차 그 검사에 걸린다(의도된 엄격함).
_CANDIDATES_BY_SIGNATURE: dict[str, tuple[tuple[str, ...], ...]] = {
    "oom_kill": (
        ("OOM 종료된 프로세스의 힙·워커 수 설정 검토 후 재기동", RISK_HIGH),
        ("메모리 상위 프로세스 누수 점검(추이가 지속 증가인지 확인)", RISK_LOW),
    ),
    "oom_kill_metric": (
        ("OOM 카운터 증가 구간의 메모리 상위 프로세스 점검", RISK_LOW),
        ("해당 서비스 메모리 상한·힙 설정 검토 후 재기동", RISK_HIGH),
    ),
    "fs_readonly": (
        ("파일시스템 오류 원인 확인(dmesg·스토리지 이벤트) 후 fsck·재마운트 검토", RISK_HIGH),
    ),
    "fs_readonly_metric": (
        ("read-only 리마운트된 마운트포인트 확인 후 스토리지 점검", RISK_HIGH),
    ),
    "service_restart_loop": (
        ("재시작 루프 서비스의 기동 실패 원인 확인(journalctl 최근 로그)", RISK_LOW),
        ("start-limit 해제 후 재기동(원인 제거 선행)", RISK_HIGH),
    ),
    "soft_lockup": (
        ("CPU 점유 상위 프로세스 우선순위 하향(renice)", RISK_LOW),
        ("커널·드라이버 이슈 여부 확인 후 노드 격리 검토", RISK_HIGH),
    ),
    "hung_task": (
        ("D-state 프로세스의 대기 위치 확인(/proc/PID/wchan) 후 IO 경로 점검", RISK_LOW),
        ("응답 없는 프로세스 종료(TERM 시그널 — 강제 종료 아님)", RISK_MEDIUM),
    ),
    "segfault": (
        ("segfault 발생 프로세스의 코어덤프·버전 확인", RISK_LOW),
        ("해당 서비스 재기동", RISK_HIGH),
    ),
    "conntrack_full": (
        ("conntrack 사용량·타임아웃 설정 검토(nf_conntrack_max 상향)", RISK_HIGH),
        ("비정상 연결 발생원 확인(연결 상위 소스 점검)", RISK_LOW),
    ),
    "fd_exhaustion": (
        ("FD 사용 상위 프로세스 확인(/proc/PID/fd) 후 누수 점검", RISK_LOW),
        ("해당 서비스 ulimit 조정 후 재기동", RISK_HIGH),
    ),
    "inode_or_disk_full": (
        ("증가 상위 디렉터리 확인 후 로그 로테이션·정리", RISK_LOW),
        ("파티션 확장 검토", RISK_HIGH),
    ),
    # ── WAS(APM) — plans/87 §5.4(c) 표 · 가역성 순 · (조치, 위험도, 검증, 롤백) ──
    "was_service_queuing": (
        ("정체 지점(DB·외부 호출) 확인 후 해당 트랜잭션을 제니퍼 콘솔에서 인터럽트", RISK_MEDIUM,
         "apm_active_services 정체 건수와 apm_app_health reject_rate가 줄어드는지 확인",
         "인터럽트는 되돌릴 수 없음 — 대상 트랜잭션을 기록하고 재처리 여부를 업무 담당과 확인"),
        ("PLC 상한 임시 하향으로 유입 차단(리다이렉트 페이지)", RISK_MEDIUM,
         "apm_app_health 응답시간·active_services 회복 확인",
         "PLC 상한을 변경 전 값으로 원복"),
        ("인스턴스 재기동(원인 미제거 시 재발)", RISK_HIGH,
         "재기동 뒤 apm_app_health p95·error_rate 회복과 apm_events 재발 여부 확인",
         "재기동은 되돌릴 수 없음 — 다중 인스턴스면 트래픽 배제 후 1개씩 진행"),
    ),
    "was_db_pool_exhaustion": (
        ("누수 의심 트랜잭션(DB_CONN_UNCLOSED 이벤트·X-View) 식별 후 개발팀 전달", RISK_LOW,
         "apm_resource_pool db_pool_usage_ratio 추이 확인",
         "해당 없음(조회만)"),
        ("커넥션 풀 상한·미반납 회수(removeAbandoned) 설정 조정 후 재기동", RISK_HIGH,
         "apm_resource_pool 사용률 하락과 JDBC 연결 실패 이벤트 소멸 확인",
         "변경 전 풀 설정으로 원복 후 재기동"),
    ),
    "was_heap_pressure": (
        ("힙 덤프 채취(서비스 덤프) 후 누수 객체 분석", RISK_LOW,
         "덤프 생성 확인(채취 중 일시 지연 가능) 후 분석 결과로 누수 객체 특정",
         "해당 없음(채취만)"),
        ("강제 GC로 일시 완화(STW 정지 발생)", RISK_MEDIUM,
         "apm_runtime_health heap_usage_ratio·gc_time_usage_pct 하락 확인",
         "되돌릴 수 없음 — 다시 오르면 다음 후보로"),
        ("힙 옵션 조정 후 재기동", RISK_HIGH,
         "재기동 뒤 apm_runtime_health 힙 추세가 계단식 상승을 반복하지 않는지 확인",
         "변경 전 힙 옵션으로 원복 후 재기동"),
    ),
    "was_error_burst": (
        ("최근 배포 여부 확인 후 롤백 검토", RISK_HIGH,
         "롤백 뒤 apm_app_health error_rate와 apm_events 오류 이벤트 감소 확인",
         "직전 배포본으로 재배포"),
    ),
    "was_slow_sql": (
        ("상위 SQL을 DBA 검토로 전달(apm_transaction_profile 상위 SQL)", RISK_LOW,
         "apm_slow_transactions sql_fetch_share 하락 확인",
         "해당 없음(전달만)"),
    ),
    "was_external_call_delay": (
        ("의존(외부) 서비스 조사로 전이하고 호출 타임아웃 설정 확인", RISK_LOW,
         "apm_slow_transactions external_share 하락 확인",
         "해당 없음(조회만)"),
    ),
    # 계획서 §5.4(c) 표에 없는 두 kind — 보수적 후보(조회·채취)만 둔다.
    "was_thread_pool_exhaustion": (
        ("정체 스레드의 실행 모드·대기 지점 확인(apm_active_services) 후 스레드 덤프 채취", RISK_LOW,
         "정체 건수와 elapsed_ms가 줄어드는지 apm_active_services로 확인",
         "해당 없음(조회·채취만)"),
    ),
    "was_gc_stall": (
        ("GC 시간 비중 추이 확인 후 힙·GC 설정 검토 요청", RISK_LOW,
         "apm_runtime_health gc_time_usage_pct 추이 확인",
         "해당 없음(검토 요청만)"),
    ),
}


def recommend(signals: list[Signal] | tuple[Signal, ...]) -> list[Remediation]:
    """매칭된 시그니처에서 조치 후보를 결정적으로 도출한다(제시 전용).

    입력이 비었거나 카탈로그 미등재 시그니처뿐이면 빈 목록을 반환한다 — 근거 없는 권고를
    지어내지 않는다. 같은 조치가 여러 시그니처에서 나오면 처음 것만 남긴다(중복 제거).

    Args:
        signals: severity_judge가 매칭한 `Signal` 목록(도구 원시 출력 기반).

    Returns:
        위험도 낮은 순(low→medium→high) 정렬된 `Remediation` 목록. 고위험×저신뢰는
        review_only=True로 표기된다(§9 — 정식 권고 아님).
    """
    out: list[Remediation] = []
    seen: set[str] = set()
    for signal in signals or ():
        candidates = _CANDIDATES_BY_SIGNATURE.get(signal.name)
        if not candidates:
            continue
        confidence = _CONFIDENCE_BY_CATEGORY.get(signal.category, "medium")
        rationale = f"{signal.label} — {signal.evidence}"
        for action, risk, *extra in candidates:
            if action in seen:
                continue
            seen.add(action)
            verification, rollback = (*extra, "", "")[:2]
            out.append(
                Remediation(
                    action=action,
                    risk=risk,
                    confidence=confidence,
                    rationale=rationale,
                    # 고위험은 강 시그니처(high 신뢰도) 근거가 있을 때만 정식 권고.
                    review_only=(
                        risk == RISK_HIGH and confidence != _HIGH_RISK_MIN_CONFIDENCE
                    ),
                    verification=verification,
                    rollback=rollback,
                )
            )
    order = {RISK_LOW: 0, RISK_MEDIUM: 1, RISK_HIGH: 2}
    out.sort(key=lambda r: order.get(r.risk, 99))
    return out


def recommend_lines(signals: list[Signal] | tuple[Signal, ...]) -> list[str]:
    """`recommend` 결과를 브리핑 권고 항목 문자열 목록으로 렌더한다."""
    return [r.to_line() for r in recommend(signals)]


__all__ = [
    "RISK_HIGH",
    "RISK_LOW",
    "RISK_MEDIUM",
    "Remediation",
    "recommend",
    "recommend_lines",
]
