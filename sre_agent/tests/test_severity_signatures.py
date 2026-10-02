"""severity_judge 도메인 규칙 검증 (Plan 02 §6) — 시그니처 표·escalate-only·원격 대체·증거 불충분.

순수 도메인 모듈(외부 의존 없음)이므로 원시 출력 픽스처만으로 결정적 검증한다.
"""

import pytest

from sre_agent.domain.severity_signatures import (
    LEVEL_CRITICAL,
    LEVEL_NAMES,
    ImportanceVerdict,
    Signal,
    clamp_level,
    judge,
    match_signatures,
)

# 레벨명 → 인덱스 역매핑(escalate-only 불변식 단언용).
_NAME_TO_LEVEL = {name: level for level, name in LEVEL_NAMES.items()}


# ── 시그니처 표(강 신호) — 도구 원시 출력 → 상향(강) ────────────────

OOM_LOG = "kernel: Out of memory: Killed process 12345 (java) total-vm:..."
FS_RO_LOG = "EXT4-fs error (device sda1): ... Remounting filesystem read-only"
RESTART_LOOP_LOG = "systemd[1]: java.service: start-limit-hit; not restarting"
SOFT_LOCKUP_LOG = "kernel: BUG: soft lockup - CPU#3 stuck for 22s!"
HUNG_TASK_LOG = "kernel: INFO: task nginx:9981 blocked for more than 120 seconds"
SEGFAULT_LOG = "kernel: java[12345]: segfault at 0 ip 00007f..."
CONNTRACK_LOG = "kernel: nf_conntrack: table full, dropping packet"


@pytest.mark.parametrize(
    "text,signame",
    [
        (OOM_LOG, "oom_kill"),
        (FS_RO_LOG, "fs_readonly"),
        (RESTART_LOOP_LOG, "service_restart_loop"),
        (SOFT_LOCKUP_LOG, "soft_lockup"),
        (HUNG_TASK_LOG, "hung_task"),
        (SEGFAULT_LOG, "segfault"),
        (CONNTRACK_LOG, "conntrack_full"),
    ],
)
def test_strong_signatures_escalate_to_critical(text, signame):
    v = judge(gate_severity=2, tool_outputs=[text])
    assert v.level == "심각"
    assert v.confidence == "high"
    assert v.escalate is True  # 경고(2) → 심각(3)
    assert signame in [s.name for s in v.signals]
    # 인용 근거(매칭 라인)가 보존된다.
    assert v.signals[0].evidence


# ── 중 신호 → 상향(중) ────────────────────────────────────────────


def test_medium_signature_escalates_by_one():
    fd_log = "app: java.io.IOException: Too many open files"
    v = judge(gate_severity=1, tool_outputs=[fd_log])  # 주의(1)
    assert v.confidence == "medium"
    assert v.escalate is True
    assert v.level == "경고"  # min(심각, 1+1)=경고(2)


def test_medium_disk_full():
    v = judge(gate_severity=2, tool_outputs=["cp: No space left on device"])
    assert v.confidence == "medium"
    assert v.level == "심각"  # min(3, 2+1)=3
    assert "inode_or_disk_full" in [s.name for s in v.signals]


# ── 상향 없음(단발 자기 복구) ─────────────────────────────────────


def test_self_recovery_no_escalation_local():
    text = "load spiked briefly then recovered; all services healthy"
    v = judge(gate_severity=2, tool_outputs=[text], remote=False)
    assert v.signals == []
    assert v.escalate is False
    assert v.confidence == "none"
    assert v.level == "경고"  # baseline 유지
    assert v.evidence_insufficient is False  # 로컬 로그 확보 → 불충분 아님


# ── escalate-only 불변식(하향·소급 변경 절대 불가) ────────────────


@pytest.mark.parametrize("gate", [0, 1, 2, 3])
@pytest.mark.parametrize(
    "text",
    ["all healthy", OOM_LOG, "Too many open files", "random noise", ""],
)
def test_never_downgrades_below_gate(gate, text):
    v = judge(gate_severity=gate, tool_outputs=[text])
    assert _NAME_TO_LEVEL[v.level] >= clamp_level(gate)
    # 상향 시에만 escalate=True.
    assert v.escalate == (_NAME_TO_LEVEL[v.level] > clamp_level(gate))


def test_gate_critical_stays_critical_with_strong_signal():
    # 이미 심각(3) — 강 신호가 있어도 더 올라갈 곳이 없다(escalate=False, 확정만).
    v = judge(gate_severity=3, tool_outputs=[OOM_LOG])
    assert v.level == "심각"
    assert v.escalate is False
    assert v.confidence == "high"
    assert v.signals  # 확정 근거는 남는다


def test_gate_out_of_range_clamped():
    assert clamp_level(-5) == 0
    assert clamp_level(99) == LEVEL_CRITICAL
    v = judge(gate_severity=99, tool_outputs=["all healthy"])
    assert v.level == "심각"  # 상한 클램프


# ── 원격 배치(Prometheus 카운터 대체 · 증거 불충분) ──────────────


def test_remote_metric_alternative_oom():
    # 로그 원문 대신 Prometheus 카운터(node_vmstat_oom_kill)로 대체 매칭.
    v = judge(gate_severity=2, tool_outputs=["node_vmstat_oom_kill 3"], remote=True)
    assert v.escalate is True
    assert v.level == "심각"
    assert v.evidence_insufficient is False
    assert "oom_kill_metric" in [s.name for s in v.signals]


def test_remote_metric_zero_not_matched_evidence_insufficient():
    # 카운터가 0이면 매칭하지 않고, 원격에서 대체 신호도 없으면 증거 불충분(상향 보류).
    v = judge(gate_severity=2, tool_outputs=["node_vmstat_oom_kill 0"], remote=True)
    assert v.signals == []
    assert v.escalate is False
    assert v.evidence_insufficient is True


def test_remote_no_evidence_insufficient():
    v = judge(gate_severity=3, tool_outputs=["<empty>"], remote=True)
    assert v.evidence_insufficient is True
    assert v.escalate is False  # 보류(상향 없음)


def test_local_no_signal_not_insufficient():
    v = judge(gate_severity=2, tool_outputs=["clean"], remote=False)
    assert v.evidence_insufficient is False


# ── match_signatures 중복 제거 ────────────────────────────────────


def test_match_dedups_same_signature_across_outputs():
    signals = match_signatures([OOM_LOG, OOM_LOG, FS_RO_LOG])
    names = [s.name for s in signals]
    assert names.count("oom_kill") == 1
    assert "fs_readonly" in names


def test_verdict_and_signal_are_frozen_dataclasses():
    v = judge(gate_severity=2, tool_outputs=[OOM_LOG])
    assert isinstance(v, ImportanceVerdict)
    assert isinstance(v.signals[0], Signal)
    with pytest.raises(Exception):
        v.escalate = False  # frozen
