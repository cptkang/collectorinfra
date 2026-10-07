# 보관한 Claude Code 구성요소 (D-312 · 2026-10-07)

이 폴더는 Claude Code가 읽지 않는다. 그래서 세션·서브에이전트 컨텍스트에 실리지 않는다.

| 파일 | 보관 사유 | 복원 |
|---|---|---|
| `agents/requirements-analyst.md` | 그린필드 Phase 1(spec.md → `docs/01_requirements.md`)용. 2026-09-16 이후 호출 2건 | `.claude/agents/`로 옮긴다 |
| `agents/research-planner.md` | 그린필드 Phase 2(spec.md → `plans/01~07`)용. 서브에이전트 기록 0건 | `.claude/agents/`로 옮긴다 |
| `skills/arch-check.md` · `skills/overfit-check.md` | 평면 `.md`라 Claude Code가 스킬로 로드하지 않았다(호출 0건). 계층 표가 낡았고 overfit 기준선 재생성 안내가 CLAUDE.md와 충돌한다. 현행 요지는 `docs/34` §12 | `.claude/skills/<이름>/SKILL.md` 형식으로 다시 써야 로드된다 |

`agents/run.py`(D-298 보존)의 Phase 1·2는 위 두 에이전트를 부른다. 실행하려면 먼저 복원한다.
