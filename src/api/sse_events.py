"""질의 SSE 스트림의 이벤트 `type` 값 (plans/166 A6 · 단일 출처).

`/query/stream`·`/query/file/stream`이 내보내는 `data: {"type": …}` 이벤트 종류다. 값은 화면
(`src/static/js/`)·하네스·턴 기록(`thread_history`)이 그대로 읽는 외부 계약이라 바꾸지 않는다.
알림 스트림(`alarm_notification`·`ping`)은 다른 채널이라 여기 두지 않는다.
"""

from __future__ import annotations

SSE_DONE = "done"
SSE_TOKEN = "token"
SSE_META = "meta"
SSE_ERROR = "error"
SSE_HEARTBEAT = "heartbeat"
SSE_PROGRESS = "progress"
SSE_NODE_START = "node_start"
SSE_NODE_COMPLETE = "node_complete"
