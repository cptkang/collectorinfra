# Codex 프로젝트 구성

2026-10-02 Claude Code 개발 지원 구성을 Codex로 이식했다. 기존 Claude 구성과
`agents/run.py`는 보존한다. 앱의 LangGraph·HolmesGPT·LLM provider를 바꾸는 작업은 아니다.

## 구성 대응

| 기존 구성 | Codex 구성 |
|---|---|
| `CLAUDE.md` | 루트 `AGENTS.md` |
| `.claude/agents/*.md` 5개 | `.codex/agents/*.toml` 5개 |
| `.claude/skills/arch-check.md` | `.agents/skills/arch-check/SKILL.md` |
| `.claude/skills/overfit-check.md` | `.agents/skills/overfit-check/SKILL.md` |
| `/arch-check`, `/overfit-check` | `$arch-check`, `$overfit-check` |
| `python -m agents.run --phase N` | Codex 세션에서 team-lead 절차와 Phase 범위를 요청 |

에이전트는 `name`, `description`, `developer_instructions`를 가진 독립 TOML이다.
모델·추론 수준·승인 정책·샌드박스는 고정하지 않고 실행 세션에서 상속한다.
Claude의 `tools` 목록은 Codex 권한 설정으로 복사하지 않는다. 역할별 편집 범위는 지침이며
도구 권한을 강제하는 샌드박스는 아니다.

`AGENTS.md`는 원문의 프로젝트 규칙·보안·Known Mistakes를 보존하면서 오래된 통계와
명령 예시를 압축했다. 기본 프로젝트 지침 한도인 32KiB 이하를 유지한다.
아키텍처 설명에서 계층 나열 순서와 실제 import 방향을 구분하고 검사 코드에 맞췄다.
스킬의 검사 범위는 스크립트를 정본으로 삼으며 기준선 전면 재생성을 금지한다.

## 사용

저장소 루트에서 Codex를 시작한다. 기존 세션에서 새 구성이 보이지 않으면 재시작한다.
프로젝트 신뢰 여부는 사용자가 확인하며 이 구성은 신뢰·권한 설정을 자동 변경하지 않는다.

```bash
codex
codex 'team-lead 역할로 Phase 1 요구사항 분석만 수행하라. .codex/agents/team-lead.toml의 지침을 따르라.'
codex 'team-lead 역할로 Phase 1~2만 수행하라. .codex/agents/team-lead.toml의 지침을 따르라.'
codex 'team-lead 역할로 지정한 계획의 Phase 1~4를 수행하라. .codex/agents/team-lead.toml의 지침을 따르라.'
```

Phase 1=요구사항, 2=조사·계획, 3=구현, 4=검증이다. 기존 구현을 새로 만드는 것이 아니라
현재 요청에 해당하는 범위만 수행한다. 직접 스킬 실행은 세션에서 다음과 같이 요청한다.

```text
$arch-check
$overfit-check
verifier 에이전트로 현재 변경을 검증하고 결과를 보고하라.
```

서브에이전트 지원과 동시 실행 한도는 호스트에 따른다. team-lead가 최상위 세션의 역할을
맡을 수 있으며, 역할 선택이 없는 호스트에서는 해당 TOML의 지침을 위임 메시지에 전달한다.
서브에이전트 자체가 없으면 역할별 순서를 직접 수행하고 그 제한을 알린다.
Codex의 병렬 실행이 worktree를 자동 격리한다고 가정하지 않는다.

## Claude 전용 기능의 대체

| 기존 호출 | Codex에서 수행할 작업 |
|---|---|
| `Agent`, `SendMessage`, `run_in_background` | 현재 호스트의 서브에이전트 생성·메시지·완료 대기 도구 |
| `isolation: "worktree"` | 담당 파일 분리 또는 명시적으로 준비한 worktree |
| `/code-review`, `/simplify` | 변경 코드 직접 리뷰·관련 테스트·중복/복잡도 확인 |
| `/revise-claude-md` | `AGENTS.md` 갱신 |
| `/loop` | 실행 세션의 진행 상태 확인; 무한 재시도하지 않음 |
| `xlsx`, `docx`, `mcp-builder`, `frontend-design`, `webapp-testing` | 설치된 동등 스킬이 있으면 사용, 없으면 프로젝트 라이브러리·테스트·직접 리뷰 |

외부 플러그인 자체는 이 저장소에 없으므로 자동 설치하거나 동작한다고 가정하지 않는다.
Claude SDK 실행기와 wheel은 기존 사용자를 위해 남겨 두며 Codex 사용에 필요하지 않다.
두 도구를 함께 사용하면 공통 정책 변경은 `AGENTS.md`와 `CLAUDE.md` 양쪽에 반영한다.
이력 문서의 Claude 명칭과 과거 경로는 변경하지 않는다.

## 검증

TOML 구문과 필수 필드, 스킬 YAML frontmatter, 경로와 프로젝트 지침 크기를 확인한다.
코드 변경 시에는 기존 `scripts/arch_check.py --ci`, `scripts/overfit_check.py --ci`를 사용한다.
파일 형식 검증과 실제 LLM을 사용하는 멀티에이전트 실행은 별도다.

공식 형식 근거:
- [AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
- [Custom agents](https://learn.chatgpt.com/docs/agent-configuration/subagents)
- [Skills](https://learn.chatgpt.com/docs/build-skills)
