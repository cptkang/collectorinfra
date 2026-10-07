"""plans/144 W5 독립 검증 2회차 — 사건 피드백 API 입력 경계.

    - 줄바꿈 값은 거부되고, 위조 JSON 조각은 JSONL에 레코드를 하나 더 만들지 못한다(저장소 무결성)
    - 위조 라벨 레코드가 few-shot·유효/노이즈 집계에 섞이지 않는다
    - episode_id 형식·문자열 필드 길이·제어문자를 입력 경계에서 422로 거부한다(교정 반영)
"""

from __future__ import annotations

from noise_gate.infrastructure.feedback_store import FeedbackStore
from tests.test_api.test_plan144_w5_episode_feedback import PATH, _body, _client, _config, _rows


def test_newline_payload_cannot_forge_extra_jsonl_record(tmp_path):
    # 줄바꿈이 든 값은 입력 경계(제어문자 금지)에서 422로 거부돼 파일에 아무것도 쓰이지 않는다.
    config = _config(tmp_path)
    forged = '"}\n{"ts":"2026-01-01T00:00:00+00:00","label":"noise","alarm_name":"X"}\n'
    resp = _client(config).post(PATH, json=_body(note=forged[:200], alarm_name=forged))
    assert resp.status_code == 422
    assert not (tmp_path / "feedback.jsonl").exists()


def test_quote_payload_stays_one_jsonl_record(tmp_path):
    # 줄바꿈 없는 위조 JSON 조각(따옴표·중괄호)은 받아도 JSON 이스케이프로 레코드 1줄에 갇힌다.
    config = _config(tmp_path)
    forged = '"},{"ts":"2026-01-01T00:00:00+00:00","label":"noise","alarm_name":"X"}'
    resp = _client(config).post(PATH, json=_body(note=forged[:200], alarm_name=forged))
    assert resp.status_code == 200
    rows = _rows(config)
    assert len(rows) == 1 and rows[0]["label"] == "demotion_needed"
    assert rows[0]["alarm_name"] == forged
    store = FeedbackStore(config.noise_gate.feedback_store_path, True, 20000)
    assert store.summarize(limit=50) == []
    assert store.summarize_episode_feedback() == {"episode_split": 0, "demotion_needed": 1}


def test_oversized_or_control_char_episode_id_is_rejected(tmp_path):
    config = _config(tmp_path)
    client = _client(config)
    assert client.post(PATH, json=_body(episode_id="ep-" + "a" * 5000)).status_code == 422
    assert client.post(PATH, json=_body(episode_id="ep-1\r\nINJECTED")).status_code == 422
    assert client.post(PATH, json=_body(alarm_name="x" * 100_000)).status_code == 422
    assert client.post(PATH, json=_body(server_name="a\x1bb")).status_code == 422
    assert not (tmp_path / "feedback.jsonl").exists()
