"""자격증명 제거 — 원본 외부 응답을 받은 직후 한 번 지난다 (plans/134 N-17 · D-296 ③ ·
SPEC-apm-question-coverage §4.1·§4.2).

규칙(값만 가린다 · 키 이름은 남긴다 · 일반 설정값은 가리지 않는다):

1. 계정 비밀번호 필드(`password`·`passwd`·`pwd` — 대소문자·유니코드 변형 무시)는 **키째
   제거**한다.
2. 키-값 묶음의 키가 비밀 패턴이면 값을 `[가림]`으로 바꾼다 — dict · 이름/값 칸 묶음
   (이름 {key,name,k,id,label,propertyName,property} 중 **하나라도** 비밀 · 값
   {value,val,v,values,propertyValue}) · 2원소 리스트 `[이름, 값]` · 평행 배열
   `{keys|names: [...], values|vals: [...]}` · `KEY=VALUE`·`KEY: VALUE` 텍스트. 비밀 키 아래 중첩
   값은 문자열·숫자 잎을 전부 가린다. 깊이에 상관없이 같은 규칙이다(반복 순회 — 재귀 한도 없음).
3. 값 안의 자격증명(어느 키든): `Authorization`·`Proxy-Authorization`·`Cookie`·`Set-Cookie` 헤더는
   줄 끝까지 · URL·JDBC 사용자 정보(`scheme://user:pass@host` — 비밀번호에 `/ # @`가 섞여도 마지막
   `@`까지 · `@` 없는 `scheme://user:` 꼬리는 통째) · Oracle `jdbc:oracle:…:user/pass@` · Oracle
   명령(`sqlplus`·`expdp`… `user/pass@db`) · 비밀 키의 따옴표 없는 값은 `; & ,`·줄바꿈까지
   (SQL Server `{…}` 값 포함) · JVM 속성 `-D<비밀 키>=…` · `--password …` · 붙은 `-p<값>` ·
   `sshpass -p` · `-u`/`--user 이름:비밀번호` · `Bearer …` · JSON(일반·이스케이프·작은따옴표) 속
   `"<비밀 키>": 값` · XML 요소·속성 · JSON 문자열 값은 디코드해 같은 규칙을 다시 적용한다.
   명령줄 문맥(키에 COMMAND·SCRIPT·ARGS·EXEC…)에서는 띄어 쓴 `-p 값`·일반 `user/pass@db`도 가린다.
4. 키·텍스트는 NFKC + 서식(Cf)·결합 문자를 걷은 꼴로 판정한다(`ＰＡＳＳＷＯＲＤ`·`pass\\u200bword`).
5. 중첩 깊이 32 초과는 같은 규칙으로 끝까지 검사하고 그 사실을 메모로 남긴다(봉투 `limits`).
6. 실제로 가리거나 지운 칸의 **이름**(값이 바뀐 잎의 가장 가까운 dict 키 · 지운 비밀번호 키)을
   모은다(`scrub_detail` — 봉투 고지 `apm_masked_fields`용 · plans/134 W1 검증 L-5). 값은 모으지
   않는다.

비밀 패턴(키 — camelCase 경계와 `_ . - 공백 / :`로 나눈 대문자 토큰, 뒤 숫자 무시):
- 부분 문자열 PASSWORD·PASSWD·PASSPHRASE·SECRET·CREDENTIAL·APIKEY·ACCESSKEY·PRIVATEKEY·TOKEN·
  COOKIE·JSESSIONID·SESSID·JWT · 끝이 PASS·PWD·PW(`PGPASSWORD`·`dbpass`·`rootpw`)
- 토큰 AUTH·AUTHORIZATION·BEARER·PRIVATE · `KEY`가 API·ACCESS·SECRET·PRIVATE·PRIV·ENCRYPT·
  ENCRYPTION·SIGNING·HMAC·MASTER·SSH 뒤(또는 붙여 씀 — `sshKey`·`MASTERKEY`)
- **세션 식별자**(SESSION·SESSIONID·SID 토큰 — `ORACLE_SID` 제외)는 숫자가 아닌 값만 가린다:
  제니퍼 `ActiveServiceData.sessionId`(int32 에이전트 세션 ID)는 상세 조회 인자라 남는다.
일반 단어(`KEYBOARD`·`MONKEY`·`KEYSTORE_TYPE`·`PATH`·`JAVA_HOME`·`java.vendor`)는 아니다. 부분
문자열 규칙은 `passCount`·`tokenCount` 같은 일반 필드도 가린다(과잉 가림 — 제니퍼 필드 어휘에
영향 없음을 테스트로 고정). 자격증명 누락은 출시 결함이라 덜 가리는 쪽으로 틀리지 않는다.

정규식은 앞쪽 고정·길이 상한·소유 수량자로 선형 시간이다(100KB 공격 문자열 1초 이내 — 회귀
테스트). 64 KiB 넘는 텍스트는 줄 경계 조각으로 나눠 처리한다(호출자가 스레드에서 돌리면 GIL이
조각 사이에서 풀린다). 패턴 테스트만으로 모든 비밀을 보장한다고 선언하지 않는다 — 운영 마스킹
녹화본 대조는 W10이다. 표준 라이브러리만 쓰는 순수 함수다(입력을 바꾸지 않고 새 값을 돌려준다).
"""

from __future__ import annotations

import functools
import json
import re
import unicodedata
from collections.abc import Callable
from typing import Any

MASK = "[가림]"
MAX_DEPTH = 32
_NOTE_MAX = 5
_JSON_DECODE_MAX = 1 << 20
_JSON_NEST_MAX = 8
_TEXT_CHUNK = 64 * 1024
_CACHE_KEY_MAX = 128

# 키 분류 결과
NONE = 0
SESSION = 1
SECRET = 2

_PASSWORD_FIELDS = frozenset({"password", "passwd", "pwd"})
_STRONG_SUBSTR = (
    "PASSWORD",
    "PASSWD",
    "PASSPHRASE",
    "SECRET",
    "CREDENTIAL",
    "APIKEY",
    "ACCESSKEY",
    "PRIVATEKEY",
    "TOKEN",
    "COOKIE",
    "JSESSIONID",
    "SESSID",
    "JWT",
)
_STRONG_SUFFIX = ("PASS", "PWD", "PW")
_STRONG_EXACT = frozenset({"AUTH", "AUTHORIZATION", "BEARER", "PRIVATE"})
_SESSION_EXACT = frozenset({"SESSION", "SESSIONID", "SID"})
_SESSION_EXEMPT_AFTER = frozenset({"ORACLE"})  # ORACLE_SID = 인스턴스 이름(일반 설정값)
_KEY_QUALIFIERS = frozenset(
    {
        "API",
        "ACCESS",
        "SECRET",
        "PRIVATE",
        "PRIV",
        "ENCRYPT",
        "ENCRYPTION",
        "SIGNING",
        "HMAC",
        "MASTER",
        "SSH",
    }
)
_COMMAND_TOKENS = frozenset(
    {"COMMAND", "COMMANDLINE", "CMD", "CMDLINE", "SCRIPT", "ARGS", "ARGUMENTS", "EXEC", "EXECUTE"}
)
_NAME_FIELDS = frozenset({"key", "name", "k", "id", "label", "propertyname", "property"})
_VALUE_FIELDS = frozenset({"value", "val", "v", "values", "propertyvalue"})
_PARALLEL_NAMES = frozenset({"keys", "names"})
_PARALLEL_VALUES = frozenset({"values", "vals"})
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_SEP = re.compile(r"[_.\-\s/:]+")
_DIGITS = "0123456789"


# ── 정규화 · 키 분류 ─────────────────────────────────────────


def _norm(text: str) -> str:
    """NFKD → 서식(Cf)·결합(Mn) 문자 제거 → NFC. 전각·영폭 문자·악센트 변형을 걷는다."""
    decomposed = unicodedata.normalize("NFKD", text)
    kept = "".join(ch for ch in decomposed if unicodedata.category(ch) not in ("Cf", "Mn"))
    return unicodedata.normalize("NFC", kept)


def _tokens(key: str) -> list[str]:
    return [t for t in _SEP.split(_CAMEL.sub("_", key).upper()) if t]


def _classify_raw(key: str) -> tuple[bool, int, bool]:
    """(비밀번호 필드, 분류 NONE/SESSION/SECRET, 명령줄 키)."""
    norm = key if key.isascii() else _norm(key)
    toks = _tokens(norm)
    kind = NONE
    for i, tok in enumerate(toks):
        base = tok.rstrip(_DIGITS) or tok
        prev = toks[i - 1].rstrip(_DIGITS) if i > 0 else ""
        if (
            base in _STRONG_EXACT
            or any(s in base for s in _STRONG_SUBSTR)
            or base.endswith(_STRONG_SUFFIX)
            or (base == "KEY" and prev in _KEY_QUALIFIERS)
            or (base.endswith("KEY") and base[:-3] in _KEY_QUALIFIERS)
        ):
            kind = SECRET
            break
        if base in _SESSION_EXACT and prev not in _SESSION_EXEMPT_AFTER:
            kind = SESSION
    return (
        norm.strip().lower() in _PASSWORD_FIELDS,
        kind,
        any(tok in _COMMAND_TOKENS for tok in toks),
    )


_classify_cached = functools.lru_cache(maxsize=8192)(_classify_raw)


def _classify(key: str) -> tuple[bool, int, bool]:
    """대량 결과는 같은 키가 행마다 반복된다 — 짧은 키만 캐시한다(응답이 정한 긴 문자열로 캐시를
    불리지 않게)."""
    return _classify_cached(key) if len(key) <= _CACHE_KEY_MAX else _classify_raw(key)


def key_kind(key: Any) -> int:
    """키 분류 — 비밀번호 필드는 SECRET으로 본다(키-값 묶음·텍스트에서는 지울 수 없어 가린다)."""
    password, kind, _ = _classify(str(key))
    return SECRET if password else kind


def is_secret_key(key: Any) -> bool:
    """키 이름이 비밀 패턴인가(모듈 설명의 규칙 — 세션 식별자 분류는 제외)."""
    return _classify(str(key))[1] == SECRET


def is_password_field(key: Any) -> bool:
    """키째 제거하는 계정 비밀번호 필드인가."""
    return _classify(str(key))[0]


def _is_command_key(key: Any) -> bool:
    return _classify(str(key))[2]


def _secret_value(kind: int, value: str) -> bool:
    """분류에 따라 이 값을 가리는가 — 세션 식별자는 숫자가 아닌 값만."""
    if kind == SECRET:
        return True
    if kind == SESSION:
        bare = value.strip().strip("\"'{}")
        return bool(bare) and not bare.isdigit()
    return False


# ── 값 안의 자격증명(텍스트) ────────────────────────────────

_Q = r"\"[^\"\r\n]*\"|'[^'\r\n]*'"
_HEADER_LINE = re.compile(
    r"(?im)\b(?:proxy-)?authori[sz]ation[ \t]*[:=][ \t]*(?P<pw>[^\r\n]+)"
    r"|\b(?:set-)?cookie[ \t]*[:=][ \t]*(?P<pw2>[^\r\n]+)"
)
_SCHEME = re.compile(r"(?<![A-Za-z0-9+.\-])[A-Za-z][A-Za-z0-9+.\-]{0,31}://")
_URL_TOKEN_END = re.compile(r"[\s\"'<>`\\]")
_URL_USER = re.compile(r"[^:/@?#]{0,256}")
_PORT = re.compile(r"\d{1,5}(?:[/?#;,]|$)")
_ORACLE_JDBC = re.compile(
    r"(?i)\bjdbc:oracle:[a-z]{1,16}:[^\s/@:'\"]{1,128}/(?P<pw>[^\s@'\"]{1,1024})(?=@)"
)
_ORACLE_CLI = re.compile(
    r"(?i)\b(?:sqlplus|expdp|impdp|exp|imp|sqlldr|rman|dgmgrl)(?:[ \t]+-{1,2}\w{1,32})*[ \t]+"
    r"[A-Za-z_][\w$#]{0,63}/(?P<pw>[^\s@/'\"]{1,256})(?=@|\s|$)"
)
_CMD_USER_PASS = re.compile(r"(?<![\w/:.@\-])[A-Za-z_][\w$#]{0,63}/(?P<pw>[^\s@/'\"]{1,256})(?=@)")
_BEARER = re.compile(r"(?i)\bbearer[ \t]+(?P<pw>[A-Za-z0-9._~+/\-]{1,4096}=*)")
_JVM_PROP = re.compile(rf"(?<![\w\-])-D(?P<key>[\w.\-]{{1,256}})=(?P<pw>{_Q}|[^\s;,'\"]+)")
_CLI_FLAG = re.compile(
    rf"(?<![\w\-])--?(?P<key>[A-Za-z][\w.\-]{{1,63}})(?:=|[ \t]+)"
    rf"(?P<pw>{_Q}|[^\s\"'\-][^\s\"']*)"
)
_ATTACHED_P = re.compile(r"(?<![\w\-])-p(?P<pw>[^\s\-\"'][^\s\"']*)")
_SPACED_P = re.compile(rf"(?<![\w\-])-p[ \t]+(?P<pw>{_Q}|[^\s\"'\-][^\s\"']*)")
_SSHPASS = re.compile(rf"(?i)\bsshpass[ \t]+-p[ \t]*(?P<pw>{_Q}|\S+)")
_USER_PASS = re.compile(
    r"(?<![\w\-])(?:-u[ \t]*|--user(?:[ \t]+|=))(?P<q>['\"]?)[^\s:'\"]{1,256}:"
    r"(?P<pw>[^\s'\"]{1,1024})(?P=q)"
)
_JSON_PAIR = re.compile(
    r'"(?P<key>[^"\\\r\n]{1,256})"[ \t]*:[ \t]*'
    r'(?:"(?P<pw>[^"\\\r\n]*+(?:\\.[^"\\\r\n]*+)*+)"|(?P<num>-?\d{1,64}(?:\.\d{1,64})?))'
)
_JSON_PAIR_ESC = re.compile(
    r'\\"(?P<key>[^"\\\r\n]{1,256})\\"[ \t]*:[ \t]*'
    r'(?:\\"(?P<pw>(?:[^"\\\r\n]|\\(?!")[^\r\n])*+)\\"|(?P<num>-?\d{1,64}(?:\.\d{1,64})?))'
)
_SQ_PAIR = re.compile(
    r"'(?P<key>[^'\\\r\n]{1,256})'[ \t]*:[ \t]*(?:'(?P<pw>[^'\\\r\n]*+)'|(?P<num>-?\d{1,64}))"
)
_XML_ELEM = re.compile(
    r"<(?P<key>[A-Za-z_][\w:.\-]{0,63})(?:[ \t][^<>]{0,2048})?>(?P<pw>[^<]{1,8192})"
    r"</(?P=key)[ \t]*>"
)
_XML_TAG = re.compile(r"<[A-Za-z_][^<>]{0,4096}>")
_XML_NAME_ATTR = re.compile(
    r"(?i)\b(?:name|key|id|property)[ \t]*=[ \t]*(?P<q>[\"'])(?P<n>[^\"'<>]{1,256})(?P=q)"
)
_XML_VALUE_ATTR = re.compile(r"(?i)\b(?:value|val)[ \t]*=[ \t]*(?P<q>[\"'])(?P<pw>[^\"'<>]*)(?P=q)")
_KV_KEY = re.compile(r"(?<![\w.\-])(?P<key>[A-Za-z_][\w.\-]{0,255})[ \t]*(?:=(?!=)|:(?!//))[ \t]*")
_KV_SECRET_VALUE = re.compile(
    r"\{[^}\r\n]{0,1024}\}(?:\}[^}\r\n]{0,1024}\})*|\"[^\"\r\n]*\"|'[^'\r\n]*'|[^;&,\r\n]+"
)
_TRIGGER = re.compile(
    r"[:=@<\"'\-]|(?i:bearer|sqlplus|expdp|impdp|sqlldr|rman|dgmgrl|\bexp\b|\bimp\b)"
)


def _masked(text: str, start: int, end: int) -> bool:
    return text.startswith(MASK, start) or start >= end


def _sub_pw(
    rx: re.Pattern[str],
    text: str,
    *,
    check: Callable[[re.Match[str], str], bool] | None = None,
    groups: tuple[str, ...] = ("pw",),
) -> str:
    """`groups` 중 잡힌 칸을 `[가림]`으로 바꾼다(`check`가 False면 그대로)."""

    def repl(m: re.Match[str]) -> str:
        name = next((g for g in groups if m.group(g) is not None), None)
        if name is None:
            return m.group(0)
        start, end = m.span(name)
        if _masked(text, start, end):
            return m.group(0)
        if check is not None and not check(m, m.group(name)):
            return m.group(0)
        base = m.start()
        whole = m.group(0)
        return whole[: start - base] + MASK + whole[end - base :]

    return rx.sub(repl, text)


def _key_check(m: re.Match[str], value: str) -> bool:
    return _secret_value(key_kind(m.group("key")), value)


def _json_pair_sub(rx: re.Pattern[str], text: str, quote: str) -> str:
    """JSON 쌍 — 문자열 값은 따옴표 안을, 숫자 값은 따옴표로 감싼 `[가림]`으로 바꾼다."""

    def repl(m: re.Match[str]) -> str:
        pw, num = m.group("pw"), m.group("num")
        value = pw if pw is not None else num
        if value is None or value.startswith(MASK):
            return m.group(0)
        if not _secret_value(key_kind(m.group("key")), value):
            return m.group(0)
        whole, base = m.group(0), m.start()
        if pw is not None:
            start, end = m.span("pw")
            return whole[: start - base] + MASK + whole[end - base :]
        start, end = m.span("num")
        return whole[: start - base] + quote + MASK + quote + whole[end - base :]

    return rx.sub(repl, text)


def _mask_userinfo(text: str) -> str:
    """`scheme://user:pass@host` — 비밀번호는 마지막 `@`까지(`/ # @` 섞임 허용) · `@`가 없으면
    포트·경로가 아닌 꼬리를 통째로 가린다(잘린 사유 등)."""
    out: list[str] = []
    pos = 0
    for m in _SCHEME.finditer(text):
        start = m.end()
        if start < pos:
            continue
        limit = min(len(text), start + 4096)
        stop = _URL_TOKEN_END.search(text, start, limit)
        end = stop.start() if stop else limit
        user = _URL_USER.match(text, start, end)
        colon = user.end() if user else start
        if colon >= end or text[colon] != ":":
            continue
        rest_start = colon + 1
        rest = text[rest_start:end]
        if not rest or _PORT.match(rest) or rest.startswith(MASK):
            continue
        at = rest.rfind("@")
        if at == 0:
            continue
        if at < 0:
            if rest.startswith("/"):
                continue
            cut = rest_start + len(rest)
        else:
            cut = rest_start + at
        out.append(text[pos:rest_start])
        out.append(MASK)
        pos = cut
    if not out:
        return text
    out.append(text[pos:])
    return "".join(out)


def _mask_key_values(text: str) -> str:
    """`KEY=VALUE`·`KEY: VALUE` — 비밀 키의 값을 가린다(키는 겹쳐 찾는다 — `OPTS=password=x`의
    안쪽 키도 본다). 따옴표 없는 값은 `; & ,`·줄바꿈까지."""
    out: list[str] = []
    pos = 0
    for m in _KV_KEY.finditer(text):
        if m.start() < pos:
            continue
        kind = key_kind(m.group("key"))
        if kind == NONE:
            continue
        value = _KV_SECRET_VALUE.match(text, m.end())
        if value is None or value.end() == m.end() or value.group(0).startswith(MASK):
            continue
        if not _secret_value(kind, value.group(0)):
            continue
        out.append(text[pos : m.end()])
        out.append(MASK)
        pos = value.end()
    if not out:
        return text
    out.append(text[pos:])
    return "".join(out)


def _mask_xml_attrs(text: str) -> str:
    """`<property name="password" value="…"/>` — 이름 속성이 비밀이면 값 속성을 가린다."""

    def repl(m: re.Match[str]) -> str:
        tag = m.group(0)
        names = [n.group("n") for n in _XML_NAME_ATTR.finditer(tag)]
        if not any(key_kind(n) != NONE for n in names):
            return tag
        return _sub_pw(_XML_VALUE_ATTR, tag)

    return _XML_TAG.sub(repl, text)


def _rules(text: str, command: bool) -> str:
    if not _TRIGGER.search(text):
        return text
    low = text.lower()
    out = text
    if "authori" in low or "cookie" in low:
        out = _sub_pw(_HEADER_LINE, out, groups=("pw", "pw2"))
    if "://" in out:
        out = _mask_userinfo(out)
    if "jdbc:oracle:" in low:
        out = _sub_pw(_ORACLE_JDBC, out)
    if "/" in out and any(
        t in low for t in ("sqlplus", "expdp", "impdp", "exp", "imp", "sqlldr", "rman", "dgmgrl")
    ):
        out = _sub_pw(_ORACLE_CLI, out)
    if command and "/" in out and "@" in out:
        out = _sub_pw(_CMD_USER_PASS, out)
    if "bearer" in low:
        out = _sub_pw(_BEARER, out)
    if "-" in out:
        if "-D" in out:
            out = _sub_pw(_JVM_PROP, out, check=_key_check)
        out = _sub_pw(_CLI_FLAG, out, check=_key_check)
        if "sshpass" in low:
            out = _sub_pw(_SSHPASS, out)
        if "-u" in out or "--user" in out:
            out = _sub_pw(_USER_PASS, out)
        if "-p" in out:
            out = _sub_pw(_ATTACHED_P, out)
            if command:
                out = _sub_pw(_SPACED_P, out)
    if '"' in out:
        out = _json_pair_sub(_JSON_PAIR, out, '"')
        if '\\"' in out:
            out = _json_pair_sub(_JSON_PAIR_ESC, out, '\\"')
    if "'" in out:
        out = _json_pair_sub(_SQ_PAIR, out, "'")
    if "<" in out:
        out = _sub_pw(_XML_ELEM, out, check=_key_check)
        out = _mask_xml_attrs(out)
    if "=" in out or ":" in out:
        out = _mask_key_values(out)
    return out


def _scrub_piece(text: str, command: bool) -> str:
    if not text.isascii():
        norm = _norm(text)
        if norm != text:
            masked = _rules(norm, command)
            if masked != norm:
                return masked  # 정규화한 꼴에서만 드러나는 자격증명 — 정규화본을 낸다
    return _rules(text, command)


def scrub_text(text: str, *, command: bool = False) -> str:
    """텍스트 안의 자격증명 값을 가린다. `command`면 띄어 쓴 `-p 값`·`user/pass@db`도 가린다.

    64 KiB 넘는 텍스트는 줄 경계 조각으로 나눠 처리한다(규칙은 줄을 넘지 않는다).
    """
    if not text:
        return text
    if len(text) <= _TEXT_CHUNK or "\n" not in text:
        return _scrub_piece(text, command)
    pieces: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + _TEXT_CHUNK)
        if end < len(text):
            newline = text.rfind("\n", start, end)
            end = newline + 1 if newline > start else end
        pieces.append(_scrub_piece(text[start:end], command))
        start = end
    return "".join(pieces)


# ── 구조(파싱된 JSON) ───────────────────────────────────────


def _mask_leaf(value: Any, kind: int = SECRET) -> Any:
    """비밀 값 하나 — 문자열·숫자만 가린다(빈 값·None·불리언은 비밀을 담지 않는다). 세션 식별자는
    숫자가 아닌 값만."""
    if isinstance(value, bool) or value is None or value == "":
        return value
    if kind == SESSION:
        return MASK if isinstance(value, str) and _secret_value(SESSION, value) else value
    if isinstance(value, (str, int, float)):
        return MASK
    return value


def _pair_info(node: dict[str, Any]) -> tuple[frozenset[str], int, bool] | None:
    """이름/값 칸 묶음 → (값 칸 키들, 이름 칸 분류 최댓값, 이름 칸 명령줄 여부)."""
    names: list[str] = []
    values: list[str] = []
    for k, v in node.items():
        lk = str(k).lower()
        if lk in _NAME_FIELDS and isinstance(v, str):
            names.append(v)
        elif lk in _NAME_FIELDS and isinstance(v, list):
            names += [x for x in v if isinstance(x, str)]
        elif lk in _VALUE_FIELDS:
            values.append(k)
    if not names or not values:
        return None
    return (
        frozenset(values),
        max(key_kind(n) for n in names),
        any(_is_command_key(n) for n in names),
    )


def _parallel_info(node: dict[str, Any]) -> tuple[str, list[int]] | None:
    """평행 배열 `{keys: [...], values: [...]}` → (값 배열 키, 원소별 분류)."""
    names_key = next((k for k in node if str(k).lower() in _PARALLEL_NAMES), None)
    values_key = next((k for k in node if str(k).lower() in _PARALLEL_VALUES), None)
    if names_key is None or values_key is None:
        return None
    names, values = node[names_key], node[values_key]
    if not isinstance(names, list) or not isinstance(values, list) or len(names) != len(values):
        return None
    return values_key, [key_kind(n) if isinstance(n, str) else NONE for n in names]


_Path = tuple[Any, Any] | None


# 최상위가 dict 키 밑이 아닌 값(맨 문자열 · 맨 배열의 잎)을 가렸을 때의 칸 이름
ROOT_FIELD = "(본문)"


class _Walker:
    """반복 순회(명시 스택) — 깊이에 상관없이 같은 규칙 · 입력은 바꾸지 않는다."""

    def __init__(self, json_depth: int = 0) -> None:
        self.notes: list[str] = []
        self.masked: set[str] = set()
        self._json_depth = json_depth
        self._noted = False
        self._stack: list[tuple[Any, Any, int, int, bool, _Path]] = []

    def run(self, value: Any) -> Any:
        out = self._emit(value, NONE, False, 0, None)
        while self._stack:
            src, dst, depth, force, command, path = self._stack.pop()
            if depth > MAX_DEPTH and not self._noted:
                self._noted = True
                if len(self.notes) < _NOTE_MAX:
                    self.notes.append(
                        f"[한계] 자격증명 검사: 예상 밖 응답 모양({_format_path(path)}) — "
                        f"깊이 {MAX_DEPTH} 넘는 중첩도 같은 규칙으로 검사했다(응답 모양 확인 필요)"
                    )
            if isinstance(src, dict):
                self._dict(src, dst, depth, force, command, path)
            else:
                self._list(src, dst, depth, force, command, path)
        return out

    def _emit(self, value: Any, kind: int, command: bool, depth: int, path: _Path) -> Any:
        if isinstance(value, dict):
            child: Any = {}
        elif isinstance(value, list):
            child = []
        else:
            out = self._leaf(value, kind, command)
            if out is not value and out != value:
                self.masked.add(_field_of(path))
            return out
        self._stack.append((value, child, depth + 1, kind, command, path))
        return child

    def _dict(
        self,
        src: dict[str, Any],
        dst: dict[str, Any],
        depth: int,
        force: int,
        command: bool,
        path: _Path,
    ) -> None:
        pair = _pair_info(src)
        parallel = _parallel_info(src)
        for key, value in src.items():
            password, kkind, kcommand = _classify(str(key))
            if password:
                self.masked.add(str(key))
                continue  # 계정 비밀번호 필드는 키째 제거(D-296 ③)
            kind = max(force, kkind)
            cmd = command or kcommand
            if pair is not None and key in pair[0]:
                kind = max(kind, pair[1])
                cmd = cmd or pair[2]
            if parallel is not None and key == parallel[0] and isinstance(value, list):
                items: list[Any] = []
                dst[key] = items
                for i, item in enumerate(value):
                    k = max(kind, parallel[1][i])
                    items.append(self._emit(item, k, cmd, depth, ((path, key), i)))
                continue
            dst[key] = self._emit(value, kind, cmd, depth, (path, key))

    def _list(
        self, src: list[Any], dst: list[Any], depth: int, force: int, command: bool, path: _Path
    ) -> None:
        pair_kind = NONE
        pair_cmd = False
        if len(src) == 2 and isinstance(src[0], str):
            pair_kind = key_kind(src[0])
            pair_cmd = _is_command_key(src[0])
        for i, item in enumerate(src):
            kind, cmd = force, command
            if i == 1:
                kind, cmd = max(kind, pair_kind), cmd or pair_cmd
            dst.append(self._emit(item, kind, cmd, depth, (path, i)))

    def _leaf(self, value: Any, kind: int, command: bool) -> Any:
        if kind != NONE:
            return _mask_leaf(value, kind)
        if isinstance(value, str):
            return self._text(value, command)
        return value

    def _text(self, value: str, command: bool) -> str:
        head = value.lstrip()[:1]
        if (
            head in ("{", "[")
            and len(value) <= _JSON_DECODE_MAX
            and self._json_depth < _JSON_NEST_MAX
        ):
            try:
                parsed = json.loads(value)
            except (ValueError, RecursionError):
                parsed = None
            if isinstance(parsed, (dict, list)):
                inner = _Walker(self._json_depth + 1)
                cleaned = inner.run(parsed)
                self.notes.extend(n for n in inner.notes if n not in self.notes)
                if cleaned != parsed:
                    return json.dumps(cleaned, ensure_ascii=False)
                return value
        return scrub_text(value, command=command)


def _field_of(path: _Path) -> str:
    """경로에서 가장 가까운 dict 키(리스트 첨자는 건너뛴다)."""
    while path is not None:
        path, step = path
        if isinstance(step, str):
            return step
    return ROOT_FIELD


def _format_path(path: _Path) -> str:
    steps: list[Any] = []
    while path is not None:
        path, step = path
        steps.append(step)
    text = "$" + "".join(
        f"[{s}]" if isinstance(s, int) else f".{str(s)[:40]}" for s in reversed(steps)
    )
    return text if len(text) <= 160 else text[:157] + "…"


def scrub_detail(value: Any) -> tuple[Any, list[str], set[str]]:
    """`scrub` + 실제로 가리거나 지운 칸 이름(값 없음)."""
    walker = _Walker()
    cleaned = walker.run(value)
    return cleaned, walker.notes, walker.masked


def scrub(value: Any) -> tuple[Any, list[str]]:
    """JSON 값(파싱 결과)에서 자격증명을 제거한 새 값과 `[한계]` 메모를 돌려준다."""
    cleaned, notes, _ = scrub_detail(value)
    return cleaned, notes
