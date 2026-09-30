"""관리자 「문서 검색 시험」 탭 토큰 (plans/126 T-3).

2026-09-30 폐쇄망 실측 결함을 고정한다 — 탭 스크립트가 `admin_token`만 읽어, 일반 로그인
(`/login` → `user_token`)한 admin 계정이 토큰 없이 요청해 401을 받았다(화면에는 「권한이
없습니다」로 보였다).

같은 날 함께 고친 `/문서` 접두 스트림 진입 테스트는 T-4 접두 제거(D-286 ⑫)와 병합하면서 뺐다.

실 LLM 0 · 네트워크 0.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


class TestAdminTabToken:
    """관리자 화면 스크립트는 로그인 경로와 무관하게 같은 토큰을 싣는다."""

    def test_rag_tab_reads_user_token_like_admin_js(self):
        rag = (ROOT / "src/static/js/admin-rag-docs.js").read_text(encoding="utf-8")
        admin = (ROOT / "src/static/js/admin.js").read_text(encoding="utf-8")
        order = 'var TOKEN_KEYS = ["admin_token", "user_token"];'
        assert order in admin
        assert order in rag

    def test_every_dashboard_script_that_reads_admin_token_also_reads_user_token(self):
        """대시보드에 실리는 스크립트 중 `admin_token`만 읽는 것이 없어야 한다.

        같은 결함의 재발 차단.
        """
        html = (ROOT / "src/static/admin/dashboard.html").read_text(encoding="utf-8")
        scripts = re.findall(r'<script src="/static/js/([\w\-]+\.js)', html)
        assert "admin-rag-docs.js" in scripts
        offenders = []
        for name in scripts:
            text = (ROOT / "src/static/js" / name).read_text(encoding="utf-8")
            if '"admin_token"' in text and '"user_token"' not in text:
                offenders.append(name)
        assert not offenders, offenders

    def test_auth_failures_are_told_apart(self):
        """401(토큰 없음·만료)과 403(관리자 아님)을 한 문구로 뭉치지 않는다 — 이번 오진의 원인."""
        rag = (ROOT / "src/static/js/admin-rag-docs.js").read_text(encoding="utf-8")
        assert "status === 401" in rag and "status === 403" in rag
        assert "status === 401 || r.status === 403" not in rag
