"""처리기 결과(`task_results[*]`) 공용 키 — 정본 1곳 (plans/166 X3).

처리기(`apm_query`·`host_inspect`·`doc_query`·`portal_help`·`subagents`)가 결과 dict에 싣고,
감사·재계획·집계·사용자 오류 판별기가 같은 키로 읽는다. 사본이 어긋나면 거부 문구가
일반 오류로 덮이거나 병합 표의 CSV 원천이 바뀌므로 값은 여기 한 곳에만 둔다.

계층: utils — 소비처에 utils(`user_error`)가 있어 domain이 아니라 여기에 둔다(utils → domain 금지).
"""

from __future__ import annotations

#: 거부·실패 사유 키. 침묵 폴백 금지 — 호출자가 이 키를 보고 다음 행동을 고른다.
DEGRADED_KEY = "degraded_reason"
#: 처리기가 표시용 행(`organized_data.rows`)의 긴 셀을 줄였다는 표지(plans/134 W2).
#: 집계기는 이 표지가 있으면 병합 표의 CSV 원천을 그 task의 `query_results`(전문)로 만든다.
DISPLAY_CUT_KEY = "display_rows_cut"

__all__ = ["DEGRADED_KEY", "DISPLAY_CUT_KEY"]
