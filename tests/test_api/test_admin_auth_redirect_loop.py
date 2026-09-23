"""관리자 화면 401 → 로그인 무한 리다이렉트 회귀 (plans/116 §10.3).

사용자 토큰(user_token)이 만료된 채 `/admin`을 열면, admin.js가 401에서 `admin_token`만
지우고 `/login?next=/admin`으로 보냈다. login.html은 `user_token`이 남아 있으면 곧장
`next`로 되돌리므로 `/admin` ↔ `/login`을 계속 오갔다(헤드리스 Chromium 재현: 3초에 34회 이동).
noise.js(D-247 · D-245 결함 수정)와 같이 **실제로 보낸 토큰의 키**를 지워야 끊긴다.

브라우저 도구가 없으므로 JS를 정적으로 점검한다(test_plan104_admin_ui.py 관례).
"""

from __future__ import annotations

import re
from pathlib import Path

_ADMIN_JS = Path(__file__).resolve().parents[2] / "src" / "static" / "js" / "admin.js"


def _function_body(src: str, name: str) -> str:
    start = src.index(f"function {name}(")
    depth = 0
    for i in range(src.index("{", start), len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start : i + 1]
    raise AssertionError(f"{name} 본문을 찾지 못했다")


def test_admin_js_remembers_which_token_key_was_sent() -> None:
    src = _ADMIN_JS.read_text(encoding="utf-8")
    # 두 키를 우선순위대로 보고, 고른 키 이름을 기억한다.
    assert re.search(r'\[\s*"admin_token"\s*,\s*"user_token"\s*\]', src)
    assert re.search(r"tokenKey\s*=\s*TOKEN_KEYS\[i\]", src)


def test_redirect_unauthenticated_clears_the_sent_token_key() -> None:
    body = _function_body(_ADMIN_JS.read_text(encoding="utf-8"), "redirectUnauthenticated")
    assert "localStorage.removeItem(tokenKey)" in body
    # 고정 키(admin_token)만 지우면 user_token이 남아 로그인 화면이 즉시 되돌린다.
    assert 'localStorage.removeItem("admin_token")' not in body
    assert "/login?next=/admin" in body
