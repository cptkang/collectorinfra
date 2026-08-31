#!/bin/zsh
# 서지 갱신 — bibliography.csv의 DOI로 OpenAlex를 재조회해 인용수 컬럼을 새 날짜로 추가한다.
#
# 사용법:  ./docs/literature/refresh.sh            # 표준출력에 비교표
#          ./docs/literature/refresh.sh --write    # CSV에 cited_by_<YYYY_MM_DD> 컬럼 추가
#
# 설계 원칙: **기존 컬럼을 덮어쓰지 않는다.** 인용수는 시점 값이므로 이력이 남아야
# "이 계획을 세울 때 이 문헌은 얼마나 인용됐나"를 나중에 확인할 수 있다.
# 스킬 경로가 바뀌면 OPENALEX만 수정한다(플러그인 캐시 경로는 버전 해시를 포함한다).
set -e
HERE="${0:A:h}"
CSV="$HERE/bibliography.csv"
OPENALEX="${OPENALEX:-$HOME/.claude/plugins/cache/claude-scholar/claude-scholar/19da3429782c/skills/openalex/scripts/openalex.py}"

if [[ ! -f "$OPENALEX" ]]; then
  echo "openalex.py를 찾지 못했습니다: $OPENALEX" >&2
  echo "  → claude-scholar 플러그인 경로를 확인하고 OPENALEX 환경변수로 지정하세요." >&2
  echo "  예: OPENALEX=\$(find ~/.claude/plugins -name openalex.py | head -1) $0" >&2
  exit 1
fi

DOIS=$(python3 -c "
import csv,sys
print(' '.join(r['doi'] for r in csv.DictReader(open('$CSV',encoding='utf-8')) if r['doi'] and r['doi']!='-'))
")

uv run --script "$OPENALEX" batch-lookup works ${=DOIS} --id-field doi 2>/dev/null \
  | python3 "$HERE/_apply_refresh.py" "$CSV" "$@"
