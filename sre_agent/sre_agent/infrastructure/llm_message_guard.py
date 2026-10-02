"""holmes 메시지 목록 정규화 — 비선두 `system` 메시지를 `user`로 강등한다 (D-213 후속).

**왜 필요한가** (폐쇄망 실측 2026-09-11):
Qwen 채팅 템플릿(vLLM)은 `system` 메시지가 **목록 맨 앞에만** 오도록 강제하고, 아니면
`BadRequestError: System message must be at the beginning.`으로 요청을 거부한다.
그런데 holmes의 컨텍스트 압축(`holmes/core/truncation/compaction.py::
compact_conversation_history`)은 압축 결과 **맨 끝에 `role="system"` 안내를 붙인다**:

    [0] system     원본 시스템 프롬프트
    [1] user       마지막 사용자 질문
    [2] assistant  압축 요약
    [3] system     "The conversation history has been compacted ... Continue."   ← 거부 유발

따라서 **컨텍스트가 차서 압축이 발동하는 모든 조사가 실패**한다(실측: 32K 컨텍스트에서
반복 재현). holmes ↔ Qwen 비호환이며 우리 코드의 결함이 아니다.

**무엇을 하는가**: 0번이 아닌 `system`의 role만 `user`로 바꾸고 **content는 보존**한다.
"압축됐으니 계속하라"는 지시가 사용자 턴으로 읽혀 ReAct 루프가 그대로 이어지고,
`[..., assistant, user]`는 어느 템플릿에서나 정상 형태다. 요약을 버리지 않으므로
정보 손실이 없다.

**어디에 거는가**: `DefaultLLM.completion` — holmes의 모든 요청이 반드시 통과하는 단일
관문이다. 압축 함수만 감싸면 다른 경로가 같은 모양을 만들 때 놓친다(한쪽만 고치는
비대칭이 재발 원인이라는 반복 교훈).

**안전성**: 고칠 것이 없으면 **원본 리스트 객체를 그대로 반환**한다 — 정상 경로의 요청
바이트가 바뀌지 않는다(no-op). 병적인 모양일 때만 동작하며, 그 모양은 현재 100% 실패한다.

계층: infrastructure(외부 패키지 어댑터). 상위(application)는 `install_system_message_guard()`만 부른다.
"""

from __future__ import annotations

import functools
import logging
from typing import Any

logger = logging.getLogger(__name__)

# 몽키패치 멱등성 표식 — 재설치·중복 호출을 무해하게 만든다.
_GUARD_FLAG = "_sre_system_message_guarded"


def normalize_system_messages(messages: Any) -> Any:
    """비선두 `system` 메시지를 `user`로 강등한다.

    Args:
        messages: holmes가 조립한 메시지 목록(list[dict] 기대). 다른 타입이면 그대로 통과.

    Returns:
        교정이 필요 없으면 **입력 객체 그대로**(동일성 보장 — no-op), 필요하면 교정 사본.
    """
    if not isinstance(messages, list) or len(messages) < 2:
        return messages

    misplaced = [
        i
        for i, m in enumerate(messages)
        if i > 0 and isinstance(m, dict) and m.get("role") == "system"
    ]
    if not misplaced:
        return messages  # ★ 정상 경로 — 원본 그대로(바이트 동일)

    fixed = list(messages)
    for i in misplaced:
        fixed[i] = {**fixed[i], "role": "user"}
    logger.info(
        "비선두 system 메시지 %d건을 user로 강등(인덱스=%s) — holmes 압축 산출물 교정(D-213)",
        len(misplaced),
        misplaced,
    )
    return fixed


def install_system_message_guard() -> bool:
    """holmes LLM 경계(`DefaultLLM.completion`)에 정규화를 설치한다.

    Returns:
        True면 이번 호출에서 설치했고, False면 설치하지 않았다(이미 설치됨 · holmes 부재 ·
        시그니처 불일치). **어느 경우에도 예외를 올리지 않는다** — 가드 설치 실패가 조사
        자체를 막아서는 안 된다(사유는 로그로 가시화, 침묵 금지).
    """
    try:
        from holmes.core.llm import DefaultLLM
    except Exception as exc:  # noqa: BLE001 — holmes 부재/구조 변경은 무해하게 건너뛴다
        logger.warning("system 메시지 가드 미설치 — holmes.core.llm 임포트 실패: %s", exc)
        return False

    original = getattr(DefaultLLM, "completion", None)
    if original is None:
        logger.warning("system 메시지 가드 미설치 — DefaultLLM.completion 부재(상위 버전 변경?)")
        return False
    if getattr(original, _GUARD_FLAG, False):
        return False  # 이미 설치됨(멱등)

    @functools.wraps(original)
    def guarded(self, messages, *args, **kwargs):  # noqa: ANN001, ANN202
        return original(self, normalize_system_messages(messages), *args, **kwargs)

    setattr(guarded, _GUARD_FLAG, True)
    DefaultLLM.completion = guarded  # type: ignore[method-assign]
    logger.info("system 메시지 가드 설치 완료 — 비선두 system → user (D-213)")
    return True
