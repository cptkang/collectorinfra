"""Plan 52 §6.1 — 심각도3 재통보 간격 분리(sev3_repeat_interval_seconds) 단위 테스트.

`AlarmWorker._is_duplicate_fingerprint(fingerprint, now, severity)`가
  - severity==3 → sev3_repeat_interval_seconds 를,
  - 그 외      → repeat_interval_seconds 를 TTL로 사용함을 검증한다.
sev3 간격을 단축하면 동일 핑거프린트의 미해결 sev3가 더 빨리 재통보(중복 해제)된다.
기본값(둘 다 동일)이면 무변경(회귀 0).

Plan 60 E1: 반환 타입이 `bool` → `tuple[bool, Optional[dict]]`(is_dup, 재발생 메타)로
확장되었으나 **억제 판정(is_dup)은 현행과 비트 동일**해야 한다(집계만 추가). 아래 단언은
tuple을 언팩해 is_dup만 검증한다 — 이 테스트가 그대로 통과하는 것이 회귀 0의 근거다.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.alarm.application.alarm_worker import AlarmWorker


def _worker(repeat: int = 14400, sev3: int = 14400) -> AlarmWorker:
    cfg = SimpleNamespace(
        noise_gate=SimpleNamespace(
            repeat_interval_seconds=repeat,
            sev3_repeat_interval_seconds=sev3,
        )
    )
    return AlarmWorker(cfg)


class TestSev3RepeatInterval:
    def test_sev3_uses_shorter_interval(self):
        # sev3 간격 60s, 공통 14400s. sev3 핑거프린트는 61s 후 재통보 허용.
        w = _worker(repeat=14400, sev3=60)
        fp = "fp-sev3"
        is_dup, _meta = w._is_duplicate_fingerprint(fp, now=1000.0, severity=3)
        assert is_dup is False  # 최초
        is_dup, _meta = w._is_duplicate_fingerprint(fp, now=1030.0, severity=3)
        assert is_dup is True   # 30s<60 중복
        # 61s 후 → sev3 TTL(60) 만료 → 재통보 허용(중복 아님)
        is_dup, _meta = w._is_duplicate_fingerprint(fp, now=1061.0, severity=3)
        assert is_dup is False

    def test_non_sev3_uses_common_interval(self):
        # 동일 시점(61s 후)이라도 sev2는 공통 14400s TTL → 여전히 중복
        w = _worker(repeat=14400, sev3=60)
        fp = "fp-sev2"
        is_dup, _meta = w._is_duplicate_fingerprint(fp, now=1000.0, severity=2)
        assert is_dup is False
        is_dup, _meta = w._is_duplicate_fingerprint(fp, now=1061.0, severity=2)
        assert is_dup is True

    def test_sev3_within_interval_still_duplicate(self):
        w = _worker(repeat=14400, sev3=60)
        fp = "fp-sev3-dup"
        is_dup, _meta = w._is_duplicate_fingerprint(fp, now=1000.0, severity=3)
        assert is_dup is False
        # 59s 후 → sev3 TTL(60) 이내 → 중복
        is_dup, _meta = w._is_duplicate_fingerprint(fp, now=1059.0, severity=3)
        assert is_dup is True

    def test_default_equal_intervals_no_change_regression(self):
        # 기본값(둘 다 14400)에서 sev3와 sev2 거동이 동일(무변경)
        w = _worker(repeat=14400, sev3=14400)
        fp3, fp2 = "fp-eq3", "fp-eq2"
        is_dup, _meta = w._is_duplicate_fingerprint(fp3, now=1000.0, severity=3)
        assert is_dup is False
        is_dup, _meta = w._is_duplicate_fingerprint(fp3, now=1000.0 + 100, severity=3)
        assert is_dup is True
        is_dup, _meta = w._is_duplicate_fingerprint(fp2, now=1000.0, severity=2)
        assert is_dup is False
        is_dup, _meta = w._is_duplicate_fingerprint(fp2, now=1000.0 + 100, severity=2)
        assert is_dup is True
