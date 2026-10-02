"""미들웨어 조사 프로파일 (Plan 78 W7-1 · D-168).

**핵심 불변식**: 미들웨어 조사는 **allowlist를 확장하지 않는다**.
필요한 신호(프로세스·포트·자원·로그)가 전부 `VM_DIAG_ALLOW`로 이미 얻어지기 때문이다.
프로파일의 실체는 **새 명령이 아니라 기존 명령의 조합·해석**이다 —
명령을 새로 열면 그만큼 공격 표면이 늘어난다.

검증 대상(SPEC-middleware-os-identification):
    S10 `middleware_profile()`의 allowlist가 `vm_profile()`과 **동일**(확장 0)
    S11 조사 초점 지침이 수집 순서·부하 가드·판정 위임을 담는다
"""

from sre_agent.toolset_profiles import (
    MIDDLEWARE_FOCUS_NOTE,
    VM_DIAG_ALLOW,
    guarded,
    VM_DIAG_DENY,
    middleware_profile,
    vm_profile,
)


def _allow(profile: dict) -> list[str]:
    return profile["bash"]["config"]["allow"]


class TestNoAllowlistExpansion:
    """S10 — 확장 0. 이것이 W7-1이 '선행조건 0'인 이유다."""

    def test_allowlist_identical_to_vm_profile(self):
        assert _allow(middleware_profile()) == _allow(vm_profile()), (
            "미들웨어 프로파일이 allowlist를 바꿨다 — W7-1은 확장 없이 성립해야 한다."
        )

    def test_no_command_outside_vm_diag_allow(self):
        extra = set(_allow(middleware_profile())) - set(VM_DIAG_ALLOW)
        assert not extra, f"VM_DIAG_ALLOW 밖의 명령이 열렸다: {sorted(extra)}"

    def test_required_signals_are_available(self):
        """W7-1이 요구하는 6종 신호가 실제로 허용돼 있어야 한다.

        **형태는 두 가지다**(2026-08-27 부하 가드 도입 — Plan 78 W2-6): 가벼운 명령은
        그대로, 무거운 명령은 `timeout N nice -n P …` **가드 형태로만** 허용된다.
        신호의 가용성은 두 형태를 합쳐서 판정한다 — 가드가 걸렸다고 신호가 사라진 것은 아니다.
        """
        allow = _allow(middleware_profile())
        for cmd in ("ps", "ss", "top -b", "pidstat", "journalctl", "systemctl status"):
            available = any(
                entry == cmd or entry.startswith(cmd) or guarded(cmd) in entry
                for entry in allow
            )
            assert available, f"필수 신호 명령이 없다: {cmd!r} (allow={allow})"

    def test_write_forms_still_denied(self):
        """읽기 전용 규약을 승계한다 — 조사가 대상을 바꿔서는 안 된다."""
        assert middleware_profile()["bash"]["config"]["deny"] == VM_DIAG_DENY

    def test_extra_allow_is_opt_in_only(self):
        """확장은 호출자가 명시할 때만 — 기본값이 넓어지면 안 된다."""
        p = middleware_profile(extra_allow=["ssh"])
        assert "ssh" in _allow(p)
        assert "ssh" not in _allow(middleware_profile())


class TestProfileShape:
    def test_k8s_disabled(self):
        """VM 대상이다 — k8s toolset을 켜면 LLM이 대상을 오인한다(D-004)."""
        assert middleware_profile()["kubernetes/logs"]["enabled"] is False

    def test_bash_enabled(self):
        assert middleware_profile()["bash"]["enabled"] is True


class TestFocusNote:
    """S11 — 프로파일의 실체는 지침이다. 지침이 비면 vm_profile과 다를 게 없다."""

    def test_note_defines_collection_order(self):
        """ps로 먼저 좁힌 뒤 pid 단위로 파고든다 — 전체 로그를 긁으면 컨텍스트가 폭증한다."""
        assert "ps" in MIDDLEWARE_FOCUS_NOTE
        assert "pidstat" in MIDDLEWARE_FOCUS_NOTE or "ss" in MIDDLEWARE_FOCUS_NOTE
        assert "journalctl" in MIDDLEWARE_FOCUS_NOTE

    def test_note_carries_load_guard(self):
        """조사 대상이 이미 포화 상태일 수 있다(78 §4.4 갭 ③ · D-117)."""
        assert "nice" in MIDDLEWARE_FOCUS_NOTE
        assert "timeout" in MIDDLEWARE_FOCUS_NOTE

    def test_note_delegates_identification(self):
        """종류 판정은 본체의 결정적 매처 몫이다 — LLM이 추정하면 안 된다(D-035)."""
        assert "추정" in MIDDLEWARE_FOCUS_NOTE or "판정" in MIDDLEWARE_FOCUS_NOTE

    def test_note_is_not_empty(self):
        assert len(MIDDLEWARE_FOCUS_NOTE.strip()) > 100
