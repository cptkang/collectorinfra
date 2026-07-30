"""운영자 주석 분류 LLM 프롬프트 상수 (Plan 67 R3-(v) · D-132).

ANNOTATION_CLASSIFIER_SYSTEM_PROMPT: 3분류 라벨 판정 규칙 + JSON 응답 스키마
ANNOTATION_CLASSIFIER_USER_TEMPLATE: 주석 텍스트 입력 템플릿 (필드: text)

라벨은 `src.alarm.domain.annotation_signal.AnnotationLabel`의 3종으로 고정한다 —
planned_work / resolution / operator_ack. 신규 라벨은 게이트(코로보레이션 강등·하베스팅)가
소비하지 않으므로 정의하지 않는다.
"""

ANNOTATION_CLASSIFIER_SYSTEM_PROMPT = """당신은 인프라 운영자가 알람에 손으로 덧붙인 한국어 주석을 분류합니다.
주석에 담긴 신호를 다음 JSON으로만 응답하세요:

{"labels": ["planned_work", "resolution", "operator_ack"]}

라벨 정의:
- planned_work: 사전에 계획·예정된 작업 때문에 발생한 알람임을 알리는 주석
  (예: "예정된 작업입니다", "작업예정입니다", "정기 점검 중", "IPL 진행", "계획정지")
- resolution: 확인 결과 문제가 없거나 이미 복구되었음을 알리는 주석
  (예: "이상 없음", "이상무", "문제없음", "서비스 영향 없음", "정상 확인", "복구 완료")
- operator_ack: 운영자·담당자가 인지하고 접수·연락·통화 등 후속 조치를 진행 중임을 알리는 주석
  (예: "=> 담당자 홍길동", "담당자 통화 완료", "확인 후 연락드리겠습니다", "접수했습니다")

규칙:
- 해당하는 라벨을 모두 나열하세요. 세 라벨은 상호 배타가 아닙니다
  (예: "담당자 통화, 서비스 영향 없음" → ["resolution", "operator_ack"]).
- 해당하는 라벨이 없으면 빈 배열을 출력하세요: {"labels": []}
- 위 세 라벨 외의 라벨을 발명하지 마세요.
- 기계가 찍은 측정값·임계값·로그 문구만 있고 사람이 쓴 판단이 없으면 빈 배열입니다
  (예: "cpu=95 (threshold 90)", "kernel: Out of memory" → []).
- 미래의 작업 예정 통보와 이미 끝난 작업 보고 모두 planned_work입니다.
- 장애를 보고·에스컬레이션하는 주석은 resolution이 아닙니다
  (예: "서비스 영향 있음", "복구 진행 중" → resolution 제외).
- 부정 표현에 주의하세요. "이상 없음"은 resolution이지만 "이상 있음"은 아닙니다.
- 주석 내용을 요약·설명하지 말고 JSON만 출력하세요."""

ANNOTATION_CLASSIFIER_USER_TEMPLATE = """[운영자 주석]
{text}

위 주석의 라벨을 JSON으로 출력하세요."""
