"""plans/194 W5 독립 검증 — Q-1 수동 전용 안내 링크의 HTML 이스케이프(D-347 ⑥)."""

from __future__ import annotations

from noise_gate.application.nodes.alarm_notifier import (
    MANUAL_ONLY_NOTICE_TEXT,
    manual_only_notice_html,
)


def test_notice_link_base_with_quotes_and_tags_is_escaped() -> None:
    html = manual_only_notice_html('https://portal.example/"><script>alert(1)</script>')
    assert "<script>" not in html
    assert 'href="https://portal.example/&quot;&gt;&lt;script&gt;' in html
    assert html.count("<a ") == 1 and html.endswith("에서 [조사 시작]")


def test_notice_text_has_no_markup_besides_link() -> None:
    assert "<" not in MANUAL_ONLY_NOTICE_TEXT and "&" not in MANUAL_ONLY_NOTICE_TEXT
    assert manual_only_notice_html("JAVASCRIPT:alert(1)") == f"<br><br>{MANUAL_ONLY_NOTICE_TEXT}"
