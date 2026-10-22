"""실행 기록 가림 — `mask_secrets` + 명령행 비밀값 꼴 (plans/187 W13 · G-36).

실행 기록(`domain.activity_log`)의 한 줄·발췌·AI 메모는 사용자에게도 보인다(G-36 — 발췌 제외 목록
없음). 그래서 버퍼에 넣기 전에 공용 로그 가림(`log_masking.mask_secrets` · D-339)을 거치고, 그것이
가리지 않는 **명령행 꼴**을 여기서 더 가린다.

`log_masking.py`에 직접 더하지 않은 이유: 그 파일은 `src/utils/log_masking.py` 정본의
바이트 사본이고, 루트 테스트(`tests/test_utils/test_log_masking_copies.py`)가 사본 4개의
바이트 일치를 단언한다 — sre_agent 사본만 고치면 깨지고, 정본을 고치면 본체·다른 패키지까지
바뀐다(W13 범위 밖). 정본 편입은 후속 결정 사항이다.

더 가리는 꼴(치환 표지 `***` · 두 번 적용해도 결과가 같다):
1. 띄어 쓴 긴 옵션 — `--password <값>`·`--token <값>`·`--client-secret <값>`·`--api-key <값>`
   (옵션 이름 판정은 `log_masking`과 같은 민감 이름 규칙 · 값이 `-`로 시작하면 다음 옵션이라 둔다)
2. MySQL·MariaDB 클라이언트의 붙여 쓴 `-p<값>`(`mysql -uroot -pSecret`
   — `-p` 단독은 프롬프트라 둔다)
3. DB2 CLP `connect to <db> user <id> using <값>`
4. `redis-cli … -a <값>`
5. 키-값 — `<민감 이름>: <값>`·`<민감 이름> = <값>`(YAML·설정·로그 출력 · 공백 허용)과
   붙여 쓴 `=`(`export DB_PASS=<값>`). `mask_secrets`는 띄어 쓴 맨값을 오탐 우려로 두지만,
   실행 기록은 사용자에게도 보이므로 가린다. 이름 판정은 민감 이름 규칙 + `pass`·`*_pass`
   (`bypass`·`compass`처럼 낱말 안의 `pass`는 제외). 오탐 억제:
   - 수량 접미 키(`secret_count`·`token_total` 등)는 민감 이름이 아니다.
   - 앞에 다른 낱말이 붙은 `pass`(`test pass: ok`)는 판정 결과라 둔다.
   - 따옴표 없는 값이 판정·수량 꼴(불리언·ok/fail·분수·백분율·소수·세 자리 이하 정수)이면 둔다
     — 네 자리 이상 숫자는 PIN·숫자 비밀번호일 수 있어 가린다.
6. `curl -u|--user <id>:<값>`(스킴 없는 사용자:비밀번호)
7. Oracle `sqlplus <id>/<값>@…`(`sqlplus / as sysdba`는 둔다)
8. SQL `IDENTIFIED BY <값>`

포트·pid 옵션(`psql -p 5432`·`mysql -P 3306 -p`·`--port 8080`·`ps -p`·`{"port": 5432}`)은
그대로 둔다.
"""

from __future__ import annotations

import re

from sre_agent.infrastructure.log_masking import MASK, _is_sensitive_name, mask_secrets

#: 5. 민감 이름 판정 보강 — `pass` 단독과 구분자 뒤 `pass`.
_PASS_SUFFIXES = ("_pass", "-pass", ".pass")
#: 5. 수량 접미 — 민감 이름이어도 값이 개수·합계다(`secret_count: 0`).
_COUNT_SUFFIXES = tuple(
    f"{sep}{word}" for sep in "_-." for word in ("count", "cnt", "total", "num", "length", "len")
)
#: 5. 판정·수량 꼴 값(따옴표 없는 값만) — 가리지 않는다.
_VERDICT_VALUE_RE = re.compile(
    r"(?i)(?:true|false|yes|no|on|off|ok|ng|fail|failed|pass|passed|none|null"
    r"|\d+/\d+|\d+(?:\.\d+)?%|\d+\.\d+|\d{1,3})"
)
#: 5. 키 앞에 붙은 다른 낱말(`test pass:`) — `pass` 단독 키에만 본다.
_WORD_BEFORE_RE = re.compile(r"[A-Za-z0-9][ \t]+$")


def _is_activity_sensitive(name: str) -> bool:
    lowered = name.lower()
    if lowered.endswith(_COUNT_SUFFIXES):
        return False
    return _is_sensitive_name(name) or lowered == "pass" or lowered.endswith(_PASS_SUFFIXES)


#: 1. 띄어 쓴 긴 옵션(이름 판정은 콜백).
_LONG_OPTION_RE = re.compile(
    r"(?<![\w-])--(?P<name>[A-Za-z0-9][A-Za-z0-9_.\-]{0,63})(?P<sp>[ \t]+)"
    r"(?P<value>\"[^\"\n]*\"|'[^'\n]*'|[^\s'\"]+)"
)
#: 2. MySQL·MariaDB 클라이언트의 `-p<값>` — 같은 명령(줄·파이프·`;`·`&` 전) 안에서만.
_MYSQL_P_RE = re.compile(
    r"(?P<head>(?<![\w-])(?:mysql[\w-]*|mariadb[\w-]*)(?=\s)[^\n|;&]{0,512}?(?<=\s)-p)"
    r"(?P<value>[^\s'\"]+)"
)
#: 3. DB2 CLP 접속 비밀번호.
_DB2_USING_RE = re.compile(
    r"(?i)(?P<head>\bconnect\s+to\s+\S+\s+user\s+\S+\s+using\s+)(?P<value>[^\s'\";]+)"
)
#: 4. `redis-cli -a <값>`.
_REDIS_A_RE = re.compile(
    r"(?P<head>(?<![\w-])redis-cli(?=\s)[^\n|;&]{0,512}?(?<=\s)-a[ \t]+)(?P<value>[^\s'\"]+)"
)
#: 5. 키-값(이름 판정은 콜백 · `=`는 붙여 쓴 꼴만).
_KEY_VALUE_RE = re.compile(
    r"(?<![A-Za-z0-9_.\-])(?P<qk>[\"']?)(?P<key>[A-Za-z0-9_.\-]{1,64})(?P=qk)"
    r"(?P<pre>[ \t]*)(?P<op>[:=])(?P<post>[ \t]*)"
    r"(?P<value>\"[^\"\n]*\"|'[^'\n]*'|[^\s'\",;)\]}]+)"
)
#: 6. `curl -u|--user <id>:<값>`.
_CURL_USER_RE = re.compile(
    r"(?P<head>(?<![\w-])curl(?=\s)[^\n|;&]{0,512}?(?<=\s)(?:-u|--user)(?:[ \t]+|=)"
    r"[\"']?[^\s:'\"]+:)(?P<value>[^\s'\"]+)"
)
#: 7. `sqlplus <id>/<값>@`.
_SQLPLUS_RE = re.compile(
    r"(?P<head>(?<![\w-])sqlplus(?:[ \t]+-\S+)*[ \t]+[\"']?[^\s/@'\"]+/)(?P<value>[^\s@'\"]+)"
)
#: 8. `IDENTIFIED BY <값>`.
_IDENTIFIED_BY_RE = re.compile(
    r"(?i)(?P<head>\bidentified\s+by\s+)(?P<value>'[^'\n]*'|\"[^\"\n]*\"|[^\s;'\"]+)"
)


def _mask_long_option(match: re.Match[str]) -> str:
    value = match.group("value")
    if not _is_activity_sensitive(match.group("name")) or value.startswith("-") or value == MASK:
        return match.group(0)
    return f"--{match.group('name')}{match.group('sp')}{MASK}"


def _mask_head(match: re.Match[str]) -> str:
    return match.group("head") + MASK


def _mask_key_value(match: re.Match[str]) -> str:
    value = match.group("value")
    key = match.group("key")
    if (
        not _is_activity_sensitive(key)
        or MASK in value
        or value in ('""', "''")
        or _VERDICT_VALUE_RE.fullmatch(value)
        or (key.lower() == "pass"
            and _WORD_BEFORE_RE.search(match.string, 0, match.start()) is not None)
    ):
        return match.group(0)
    if value[0] in "\"'":
        masked = value[0] + MASK + value[0]
    else:
        masked = MASK
    return match.group(0)[: match.start("value") - match.start()] + masked


def _mask_quoted_head(match: re.Match[str]) -> str:
    value = match.group("value")
    if value[0] in "\"'":
        return match.group("head") + value[0] + MASK + value[0]
    return match.group("head") + MASK


def mask_activity_text(text: str) -> str:
    """실행 기록 텍스트의 비밀값 꼴을 가린다 — `mask_secrets` 뒤 명령행 꼴."""
    masked = mask_secrets(text)
    masked = _LONG_OPTION_RE.sub(_mask_long_option, masked)
    masked = _MYSQL_P_RE.sub(_mask_head, masked)
    masked = _DB2_USING_RE.sub(_mask_head, masked)
    masked = _REDIS_A_RE.sub(_mask_head, masked)
    masked = _KEY_VALUE_RE.sub(_mask_key_value, masked)
    masked = _CURL_USER_RE.sub(_mask_head, masked)
    masked = _SQLPLUS_RE.sub(_mask_head, masked)
    masked = _IDENTIFIED_BY_RE.sub(_mask_quoted_head, masked)
    return masked


__all__ = ["mask_activity_text"]
