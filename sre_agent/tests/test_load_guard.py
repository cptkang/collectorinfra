"""대상 호스트 부하 가드 (Plan 78 W2-6·W3-6 · `docs/25_host_investigation_load_guard.md`).

## 왜 allowlist로 강제하는가

본 조사의 대표 시나리오가 **이미 포화된 서버를 조사하는 것**이다 — 조사가 장애를 악화시키면
계획의 목적 자체가 무너진다(78 §4.4 갭 ③).

`system_prompt_additions`(지침 주입)를 넘기는 **프로덕션 호출부가 0건**이었으므로(2026-08-27 실측)
지침만으로는 아무것도 강제되지 않았다. allow/deny가 **prefix 매칭**이라는 성질을 이용해
무거운 명령을 *가드 형태로만* 등록하면, 가드 없는 형태가 자동으로 거부된다.

    L-1 nice 우선순위 하향   L-2 명령 자체 timeout   L-3 반복 샘플링 1회 고정
"""

from __future__ import annotations

import pytest

from sre_agent.diagnosis import _with_load_guard_note
from sre_agent.toolset_profiles import (
    HEAVY_DIAG_COMMANDS,
    LIGHT_DIAG_COMMANDS,
    LOAD_GUARD_NICE,
    LOAD_GUARD_NOTE,
    LOAD_GUARD_PREFIX,
    LOAD_GUARD_TIMEOUT_SECONDS,
    MIDDLEWARE_FOCUS_NOTE,
    VM_DIAG_ALLOW,
    VM_DIAG_DENY,
    guarded,
    middleware_profile,
    vm_profile,
)


def _allow() -> list[str]:
    return vm_profile()["bash"]["config"]["allow"]


def _permitted(command: str, allow: list[str] | None = None) -> bool:
    """prefix 매칭 규약대로 명령이 허용되는지 판정한다(bash toolset과 동일 규약)."""
    return any(command.startswith(p) for p in (allow if allow is not None else _allow()))


def _denied(command: str) -> bool:
    return any(command.startswith(p) for p in VM_DIAG_DENY)


# ──────────────────────────────────────────────
# L-1 nice · L-2 timeout
# ──────────────────────────────────────────────

def test_guard_prefix_carries_both_nice_and_timeout():
    """★ L-1 + L-2를 한 접두에 담는다 — 순서도 고정한다(`timeout`이 바깥이어야 실효)."""
    assert LOAD_GUARD_PREFIX == f"timeout {LOAD_GUARD_TIMEOUT_SECONDS} nice -n {LOAD_GUARD_NICE} "
    assert LOAD_GUARD_PREFIX.startswith("timeout ")
    assert " nice -n " in LOAD_GUARD_PREFIX


@pytest.mark.parametrize("cmd", HEAVY_DIAG_COMMANDS)
def test_heavy_command_requires_the_guard(cmd):
    """★ 무거운 명령은 **가드 없이는 거부**된다 — 이것이 유일한 실효 강제다."""
    assert not _permitted(cmd), f"{cmd!r}가 가드 없이 허용된다(L-1·L-2 미강제)"
    assert _permitted(guarded(cmd)), f"{cmd!r}의 가드 형태가 거부된다(조사 무력화)"


@pytest.mark.parametrize("cmd", LIGHT_DIAG_COMMANDS)
def test_light_command_stays_unwrapped(cmd):
    """가벼운 명령까지 래핑하면 조사 마찰만 커진다 — 즉시 끝나고 출력이 유계다."""
    assert _permitted(cmd)


def test_guard_values_are_constants_not_literals():
    """상한 값이 명령 문자열에 흩어져 있으면 조정이 불가능해진다."""
    assert isinstance(LOAD_GUARD_TIMEOUT_SECONDS, int) and LOAD_GUARD_TIMEOUT_SECONDS > 0
    assert isinstance(LOAD_GUARD_NICE, int) and LOAD_GUARD_NICE > 0


# ──────────────────────────────────────────────
# L-3 반복 샘플링 1회 고정
# ──────────────────────────────────────────────

def test_unbounded_top_is_no_longer_allowed():
    """★ L-3 — 종전 allow는 `"top -b"` 였다. `-n`이 없으면 **무한 실행**된다.

    포화된 호스트를 조사하다 조사 자체가 호스트를 붙드는 형태였다.
    """
    assert not _permitted("top -b")
    assert not _permitted("top -b -d 1")
    assert _permitted(guarded("top -b -n 1"))


def test_top_snapshot_form_is_the_only_one_registered():
    tops = [a for a in VM_DIAG_ALLOW if "top" in a]
    assert tops == [guarded("top -b -n 1")]


# ──────────────────────────────────────────────
# ★ deny 우회 차단 (가드 접두가 만든 신규 위험)
# ──────────────────────────────────────────────

@pytest.mark.parametrize(
    "destructive",
    ["dmesg -C", "dmesg --clear", "journalctl --vacuum-time=1s",
     "journalctl --rotate", "journalctl --flush"],
)
def test_destructive_forms_are_denied_bare(destructive):
    """읽기 전용 규약 — 조사가 대상을 바꿔서는 안 된다(D-003)."""
    assert _denied(destructive)


@pytest.mark.parametrize(
    "destructive",
    ["dmesg -C", "dmesg --clear", "journalctl --vacuum-time=1s",
     "journalctl --rotate", "journalctl --flush"],
)
def test_destructive_forms_are_denied_when_guarded(destructive):
    """★ 가드 접두가 deny를 **비껴가지 않는다**.

    `journalctl --vacuum`을 bare deny로만 두면 `timeout … nice … journalctl --vacuum`이
    그 prefix로 시작하지 않아 **차단이 우회된다** — 가드 도입이 만든 신규 위험이며,
    allow와 deny에 **같은 `guarded()`를 쓰는 것**이 그 답이다.
    """
    assert _denied(guarded(destructive)), f"가드 형태로 우회된다: {destructive!r}"


def test_deny_covers_both_forms_for_every_destructive_entry():
    """deny 목록이 bare/guarded 쌍으로 유지된다 — 한쪽만 추가하면 구멍이 생긴다."""
    bare = [d for d in VM_DIAG_DENY if not d.startswith("timeout ")]
    assert bare, "bare deny가 비었다"
    for d in bare:
        assert guarded(d) in VM_DIAG_DENY, f"가드 형태 deny 누락: {d!r}"


# ──────────────────────────────────────────────
# 강제와 안내는 한 세트 (조사 무력화 방지)
# ──────────────────────────────────────────────

def test_note_states_the_exact_enforced_form():
    """★ 강제만 있고 안내가 없으면 무거운 명령이 전부 거부되어 **조사가 무력화**된다."""
    assert LOAD_GUARD_PREFIX in LOAD_GUARD_NOTE
    assert "-n 1" in LOAD_GUARD_NOTE
    for cmd in ("journalctl", "top -b -n 1"):
        assert cmd in LOAD_GUARD_NOTE


def test_note_is_injected_by_default():
    """★ 배선 확인 — 지침을 넘기는 프로덕션 호출부가 0건이었다(정의만으로는 무효)."""
    assert LOAD_GUARD_NOTE in _with_load_guard_note(None)


def test_caller_additions_are_preserved():
    """호출자 지침을 덮어쓰지 않는다."""
    out = _with_load_guard_note("호출자 고유 지침")
    assert "호출자 고유 지침" in out
    assert LOAD_GUARD_NOTE in out


def test_no_duplicate_injection():
    """이미 포함된 지침(MIDDLEWARE_FOCUS_NOTE)에 두 번 붙이지 않는다."""
    assert _with_load_guard_note(MIDDLEWARE_FOCUS_NOTE) == MIDDLEWARE_FOCUS_NOTE


def test_middleware_note_references_single_source():
    """부하 가드 문구가 두 벌이 되면 지침과 강제가 조용히 어긋난다(D-053)."""
    assert LOAD_GUARD_NOTE in MIDDLEWARE_FOCUS_NOTE


# ──────────────────────────────────────────────
# 프로파일 불변식 유지
# ──────────────────────────────────────────────

def test_middleware_profile_still_matches_vm_profile():
    """W7-1 불변식 — 미들웨어 조사는 allowlist를 확장하지 않는다(확장 0)."""
    assert middleware_profile()["bash"]["config"]["allow"] == _allow()


def test_allowlist_size_is_unchanged():
    """가드 도입이 **명령 종류를 늘리거나 줄이지 않았다** — 형태만 바뀌었다."""
    assert len(VM_DIAG_ALLOW) == len(LIGHT_DIAG_COMMANDS) + len(HEAVY_DIAG_COMMANDS)
    assert len(VM_DIAG_ALLOW) == 31


def test_extra_allow_is_not_guarded():
    """옵트인 확장(ssh 등)은 호출자 책임이다 — 임의로 가드를 씌우지 않는다."""
    allow = vm_profile(extra_allow=["ssh"])["bash"]["config"]["allow"]
    assert "ssh" in allow
