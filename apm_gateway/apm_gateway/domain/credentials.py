"""자격증명 제거 — 원본 외부 응답을 받은 직후 한 번 지난다 (plans/134 N-17 · D-296 ③ ·
SPEC-apm-question-coverage §4.1·§4.2).

규칙(값만 가린다 · 키 이름은 남긴다 · 일반 설정값은 가리지 않는다):

1. 계정 비밀번호 필드(`password`·`passwd`·`pwd` — 대소문자·유니코드 변형 무시)는 **키째
   제거**한다. 예외: POSIX 작업 디렉터리 환경변수 `PWD`·`OLDPWD`(대문자 정확 일치)는 값이 절대
   경로(`/…`)일 때 비밀로 보지 않는다(dict 키 · `KEY=VALUE` · JSON 쌍 텍스트 — 값이 경로가 아니면
   종전대로다: ODBC 연결 문자열 `PWD=…`는 비밀번호다 · plans/134 W7 I-1).
2. 키-값 묶음의 키가 비밀 패턴이면 값을 `[가림]`으로 바꾼다 — dict · 이름/값 칸 묶음
   (이름 {key,name,k,id,label,propertyName,property} 또는 끝이 name·key·field·param인 칸 중
   **하나라도** 비밀 · 값 {value,val,v,values,propertyValue} 또는 끝이 value·val인 칸 —
   `paramName`/`paramValue`·`headerName`/`headerValue`·`field`/`value` · 대소문자 무시) · 2원소
   리스트 `[이름, 값]` · 평행 배열 `{keys|names: [...], values|vals: [...]}` · `KEY=VALUE`·
   `KEY: VALUE` 텍스트. 비밀 키 아래 중첩 값은 문자열·숫자 잎을 전부 가린다. 깊이에 상관없이 같은
   규칙이다(반복 순회 — 재귀 한도 없음).
3. 값 안의 자격증명(어느 키든): `Authorization`·`Proxy-Authorization`·`Cookie`·
   `Set-Cookie` 헤더는 줄 끝까지 · URL·JDBC 사용자 정보(`scheme://user:pass@host` —
   비밀번호에 `/ # @`가 섞여도 마지막 `@`까지 · 숫자로 시작해도 `?`·`#` 앞에 `@`가 있으면
   포트가 아니다 · `@` 없는 `scheme://user:` 꼬리는 통째 · 콜론 없는 `scheme://<토큰>@`은
   사용자 정보 통째) · Oracle `jdbc:oracle:…:user/pass@` · Oracle 명령(`sqlplus`·`expdp`…
   `user/pass@db`) · 값 **전체**가 접속 문자열 `user/pass@db`인 값(경로 오탐을 막으려고 전체
   일치만) · 비밀 키의 따옴표 없는 값은 공백까지(줄 머리 키 — 설정 파일 줄 — 는 줄 끝까지 ·
   `k=v;k=v` 연결 문자열 문맥에서만 `;`까지 · SQL Server `{…}` 값 포함) · 따옴표 값은
   백슬래시 이스케이프(`\\"`)까지 한 값이다 · JVM 속성 `-D<비밀 키>=…`는 공백까지 ·
   `--password …` · 붙은 `-p<값>` · `sshpass -p` · `-u`/`--user 이름:비밀번호` · `Bearer …` ·
   도구별 표기(명령 키가 아닌 칸의 셸 명령에도 — REAUDIT-2): DB2 `connect|attach … user X
   using <pw>`(같은 줄) · `-P <pw>`·`-P<pw>` · 띄어 쓴 `-a <pw>`·`-w <pw>` · `-u user,<pw>` ·
   `-U user%<pw>`(`-a`·`-w`가 다른 뜻인 도구는 과잉 가림) · JSON(일반·이스케이프·작은따옴표)
   속 `"<비밀 키>": 값` · XML 요소·속성 · JSON 문자열 값은 디코드해 같은 규칙을 다시 적용한다.
   명령줄 문맥(키에 COMMAND·SCRIPT·ARGS·EXEC…)은 **첫 토큰(실행 파일 — 따옴표로 묶인 경로
   포함)만 남기고 나머지 인자를 통째로 가린다**(`"/opt/restart.sh [가림]"` · 인자가 없으면
   그대로 · 첫 토큰이 `NAME=값` 대입이면 값도 가린다). 비밀번호 표기는 도구마다 달라 명령 키
   에서는 패턴으로 쫓지 않는다(plans/134 W7 AUDIT-1 — 위 도구별 표기는 겹쳐 적용된다). 명령줄
   문맥의 배열은 첫 원소 뒤를 인자로 보고 가리고, 객체(맵)는 칸 이름으로 실행 파일 칸을 고를 수
   없어 잎을 전부 가린다(REAUDIT-1).
4. 키·텍스트는 NFKC + 서식(Cf)·결합 문자를 걷은 꼴로 판정한다(`ＰＡＳＳＷＯＲＤ`·`pass\\u200bword`).
5. 중첩 깊이 32 초과는 같은 규칙으로 끝까지 검사하고 그 사실을 메모로 남긴다(봉투 `limits`).
6. 실제로 가리거나 지운 칸의 **이름**(값이 바뀐 잎의 가장 가까운 dict 키 · 지운 비밀번호 키)을
   모은다(`scrub_detail` — 봉투 고지 `apm_masked_fields`용 · plans/134 W1 검증 L-5). 값은 모으지
   않는다.

비밀 패턴(키 — camelCase 경계와 `_ . - 공백 / :`로 나눈 대문자 토큰, 뒤 숫자 무시):
- 부분 문자열 PASSWORD·PASSWD·PASSPHRASE·SECRET·CREDENTIAL·APIKEY·ACCESSKEY·PRIVATEKEY·TOKEN·
  COOKIE·JSESSIONID·SESSID·JWT · 끝이 PASS·PWD·PW(`PGPASSWORD`·`dbpass`·`rootpw`)
- 토큰 AUTH·AUTHORIZATION·BEARER·PRIVATE·CRED(토큰 단위 — `INCREDIBLE`·`CREDIT` 아님) · `KEY`가
  API·ACCESS·SECRET·PRIVATE·PRIV·ENCRYPT·ENCRYPTION·SIGNING·HMAC·MASTER·SSH·X509·RSA·DSA·ECDSA·
  ED25519·PGP·GPG 뒤(또는 붙여 씀 — `sshKey`·`MASTERKEY`·`x509Key` · TLS·SSL은 제외 —
  `javax.net.ssl.keyStore`는 저장소 경로 설정)
- **구분자를 걷은 대문자 전체 키**(영숫자·한글만 — `googleAPIkey` → `GOOGLEAPIKEY`)에도 위 부분
  문자열·끝맺음(PASS·PWD·PW·ASSERTION — `clientAssertion` · 위 한정어 + KEY) 규칙과 CREDS·
  PASSCODE·비밀번호·암호·패스워드·시크릿을 적용한다 — camelCase 분할이 대문자 묶음+소문자
  (`APIkey` → `AP|Ikey`)를 쪼개도 빗나가지 않는다(plans/134 W7 AUDIT-2 · REAUDIT-4)
- **세션 식별자**(SESSION·SESSIONID·SID 토큰 — `ORACLE_SID` 제외)는 숫자가 아닌 값만 가린다:
  제니퍼 `ActiveServiceData.sessionId`(int32 에이전트 세션 ID)는 상세 조회 인자라 남는다.
일반 단어(`KEYBOARD`·`MONKEY`·`KEYSTORE_TYPE`·`PATH`·`JAVA_HOME`·`java.vendor`)는 아니다. 부분
문자열 규칙은 `passCount`·`tokenCount` 같은 일반 필드도 가린다(과잉 가림 — 제니퍼 필드 어휘에
영향 없음을 테스트로 고정). 자격증명 누락은 출시 결함이라 덜 가리는 쪽으로 틀리지 않는다.

정규식은 앞쪽 고정·길이 상한·소유 수량자로 선형 시간이다(100KB 공격 문자열 1초 이내 ·
2026-10-06 추가 규칙은 1MB 공격 문자열 — 회귀 테스트). 64 KiB 넘는 텍스트는 줄 경계 조각으로
나눠 처리한다(호출자가 스레드에서 돌리면 GIL이 조각 사이에서 풀린다). 패턴 테스트만으로 모든
비밀을 보장한다고 선언하지 않는다 — 운영 마스킹 녹화본 대조는 W10이다. 표준 라이브러리만 쓰는
순수 함수다(입력을 바꾸지 않고 새 값을 돌려준다).
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
# 구분자를 걷은 전체 키에만 보는 어휘(토큰 분할과 무관 — AUDIT-2)
_FLAT_SUBSTR = (*_STRONG_SUBSTR, "CREDS", "PASSCODE", "비밀번호", "암호", "패스워드", "시크릿")
# 끝맺음 — `clientAssertion`·`samlAssertion`(OAuth·SAML 클라이언트 비밀 · REAUDIT-4). 끝맺음만 본다
# (`assertionConsumerServiceUrl` 같은 설정 URL은 아니다)
_FLAT_SUFFIX = (*_STRONG_SUFFIX, "ASSERTION")
_FLAT_DROP = re.compile(r"[^0-9A-Z가-힣]+")
# 토큰 정확 일치 — `CRED`(약어 · REAUDIT-4)는 토큰 단위라 `INCREDIBLE`·`CREDIT`은 아니다
_STRONG_EXACT = frozenset({"AUTH", "AUTHORIZATION", "BEARER", "PRIVATE", "CRED"})
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
        # 인증서·암호 키 알고리즘(`x509Key`·`rsaKey`·`gpgKey` — REAUDIT-4). TLS·SSL은 넣지 않는다
        # (`javax.net.ssl.keyStore`는 키 저장소 경로 설정이다)
        "X509",
        "RSA",
        "DSA",
        "ECDSA",
        "ED25519",
        "PGP",
        "GPG",
    }
)
_COMMAND_TOKENS = frozenset(
    {"COMMAND", "COMMANDLINE", "CMD", "CMDLINE", "SCRIPT", "ARGS", "ARGUMENTS", "EXEC", "EXECUTE"}
)
_NAME_FIELDS = frozenset({"key", "name", "k", "id", "label", "propertyname", "property"})
_VALUE_FIELDS = frozenset({"value", "val", "v", "values", "propertyvalue"})
# 이름/값 칸 끝맺음(`paramName`·`headerValue` — AUDIT-5)
_NAME_SUFFIXES = ("name", "key", "field", "param")
_VALUE_SUFFIXES = ("value", "val")
# POSIX 작업 디렉터리 환경변수 — 값이 절대 경로면 비밀이 아니다(I-1)
_POSIX_DIR_KEYS = frozenset({"PWD", "OLDPWD"})
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
        prev_raw = toks[i - 1] if i > 0 else ""
        prev = prev_raw.rstrip(_DIGITS)
        if (
            base in _STRONG_EXACT
            or any(s in base for s in _STRONG_SUBSTR)
            or base.endswith(_STRONG_SUFFIX)
            or (base == "KEY" and (prev in _KEY_QUALIFIERS or prev_raw in _KEY_QUALIFIERS))
            or (base.endswith("KEY") and base[:-3] in _KEY_QUALIFIERS)
        ):
            kind = SECRET
            break
        if base in _SESSION_EXACT and prev not in _SESSION_EXEMPT_AFTER:
            kind = SESSION
    if kind != SECRET:
        flat = _FLAT_DROP.sub("", norm.upper())
        bare = flat.rstrip(_DIGITS)
        if (
            any(s in flat for s in _FLAT_SUBSTR)
            or bare.endswith(_FLAT_SUFFIX)
            or any(bare.endswith(q + "KEY") for q in _KEY_QUALIFIERS)
        ):
            kind = SECRET
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


def _posix_dir(key: Any, value: Any) -> bool:
    """POSIX 작업 디렉터리 환경변수(`PWD`·`OLDPWD` 대문자 정확 일치)이고 값이 절대 경로인가 — 이때는
    비밀이 아니다(I-1). 값이 경로가 아니면(ODBC `PWD=…`) 종전대로 비밀번호 필드다."""
    return key in _POSIX_DIR_KEYS and isinstance(value, str) and value.startswith("/")


def _secret_value(kind: int, value: str) -> bool:
    """분류에 따라 이 값을 가리는가 — 세션 식별자는 숫자가 아닌 값만."""
    if kind == SECRET:
        return True
    if kind == SESSION:
        bare = value.strip().strip("\"'{}")
        return bool(bare) and not bare.isdigit()
    return False


# ── 값 안의 자격증명(텍스트) ────────────────────────────────

# 따옴표 값 — 백슬래시 이스케이프(`\"`)까지 한 값이다(AUDIT-4 · 소유 수량자로 선형)
_Q = r"\"(?:[^\"\\\r\n]++|\\.)*+\"|'(?:[^'\\\r\n]++|\\.)*+'"
_HEADER_LINE = re.compile(
    r"(?im)\b(?:proxy-)?authori[sz]ation[ \t]*[:=][ \t]*(?P<pw>[^\r\n]+)"
    r"|\b(?:set-)?cookie[ \t]*[:=][ \t]*(?P<pw2>[^\r\n]+)"
)
_SCHEME = re.compile(r"(?<![A-Za-z0-9+.\-])[A-Za-z][A-Za-z0-9+.\-]{0,31}://")
_URL_TOKEN_END = re.compile(r"[\s\"'<>`\\]")
_URL_USER = re.compile(r"[^:/@?#]{0,4096}")
_PORT = re.compile(r"\d{1,5}(?:[/?#;,]|$)")
_URL_QUERY_START = re.compile(r"[?#]")
_ORACLE_JDBC = re.compile(
    r"(?i)\bjdbc:oracle:[a-z]{1,16}:[^\s/@:'\"]{1,128}/(?P<pw>[^\s@'\"]{1,1024})(?=@)"
)
_ORACLE_CLI = re.compile(
    r"(?i)\b(?:sqlplus|expdp|impdp|exp|imp|sqlldr|rman|dgmgrl)(?:[ \t]+-{1,2}\w{1,32})*[ \t]+"
    r"[A-Za-z_][\w$#]{0,63}/(?P<pw>[^\s@/'\"]{1,256})(?=@|\s|$)"
)
# 값 전체가 접속 문자열 `user/pass@db`(전체 일치만 — 경로 오탐 방지 · AUDIT-3) —
# 비밀번호는 마지막 `@`까지
_CONNECT_STRING = re.compile(
    r"[ \t]*[A-Za-z_][\w$#]{0,63}/(?P<pw>[^\s/]{1,1024})@[^\s@]{1,4096}[ \t]*"
)
_BEARER = re.compile(r"(?i)\bbearer[ \t]+(?P<pw>[A-Za-z0-9._~+/\-]{1,4096}=*)")
# JVM 속성 값은 공백까지(`,` `;`가 섞인 비밀번호 — AUDIT-4)
_JVM_PROP = re.compile(rf"(?<![\w\-])-D(?P<key>[\w.\-]{{1,256}})=(?P<pw>{_Q}|\S+)")
_CLI_FLAG = re.compile(
    rf"(?<![\w\-])--?(?P<key>[A-Za-z][\w.\-]{{1,63}})(?:=|[ \t]+)"
    rf"(?P<pw>{_Q}|[^\s\"'\-]\S*)"
)
_ATTACHED_P = re.compile(r"(?<![\w\-])-p(?P<pw>[^\s\-\"']\S*)")
# 명령줄 문맥의 첫 토큰(실행 파일) — 따옴표로 묶인 경로 포함 · 길이 상한(AUDIT-1)
_COMMAND_HEAD = re.compile(r"\s*(?:\"[^\"\r\n]{1,4096}\"|'[^'\r\n]{1,4096}'|[^\s\"']{1,4096})")
_SSHPASS = re.compile(rf"(?i)\bsshpass[ \t]+-p[ \t]*(?P<pw>{_Q}|\S+)")
# 도구별 비밀번호 표기 — 명령 키가 아닌 칸에 담긴 셸 명령(`STARTUP_HOOK` 등)에도 적용한다
# (명령 키의 「첫 토큰만」과 겹쳐 둔다 · REAUDIT-2): DB2 `connect|attach … user X using <pw>`
# (같은 줄) · sqlcmd·isql `-P <pw>`·`-P<pw>` · redis-cli `-a <pw>` · ldapsearch `-w <pw>`
# (`-a`·`-w`는 띄어 쓴 꼴만 — `-agentlib`은 아니고, 다른 뜻인 도구는 과잉 가림 방향) ·
# lftp `-u user,<pw>` · smbclient `-U user%<pw>`
_DB2_USING = re.compile(
    rf"(?i)\b(?:connect|attach)\b[^\r\n]{{0,256}}?\buser[ \t]+[^\s'\"]{{1,128}}[ \t]+using[ \t]+"
    rf"(?P<pw>{_Q}|\S+)"
)
_SHORT_SECRET_FLAG = re.compile(
    rf"(?<![\w\-])-(?:P[ \t]*|[aw][ \t]+)(?P<pw>{_Q}|[^\s\"'\-]\S*)"
)
_USER_SEP_PASS = re.compile(
    r"(?<![\w\-])-[uU][ \t]*[^\s,%:'\"]{1,256}[,%](?P<pw>[^\s'\"]{1,1024})"
)
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
# 비밀 키 값 — SQL Server `{…}` · 따옴표(이스케이프 포함) · 그 밖은 문맥별(`_kv_value_rx` · AUDIT-4)
_KV_QUOTED_VALUE = re.compile(rf"\{{[^}}\r\n]{{0,1024}}\}}(?:\}}[^}}\r\n]{{0,1024}}\}})*|{_Q}")
_KV_WORD_VALUE = re.compile(r"\S+")
_KV_LINE_VALUE = re.compile(r"[^\r\n]+")
_KV_CONN_VALUE = re.compile(r"[^;\r\n]+")
_KV_CONN_NEXT = re.compile(r"[^;\r\n]{0,512};[ \t]*[A-Za-z_][\w .\-]{0,63}=")
_TRIGGER = re.compile(
    r"[:=@<\"'\-]|(?i:bearer|sqlplus|expdp|impdp|sqlldr|rman|dgmgrl|\bexp\b|\bimp\b|\busing\b)"
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
        key = m.group("key")
        if _posix_dir(key, pw) or not _secret_value(key_kind(key), value):
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
    포트·경로가 아닌 꼬리를 통째로 가린다(잘린 사유 등). 숫자로 시작해도 `?`·`#` 앞에 `@`가 있으면
    포트가 아니라 비밀번호다(`app:12/…@db`) · 콜론 없는 `scheme://<토큰>@`은 사용자 정보를 통째로
    가린다(AUDIT-3).

    토큰 끝·다음 `@`·다음 `?#` 위치는 앞으로만 움직이는 캐시로 찾는다 — 스킴이 촘촘한 긴 문자열에서
    스킴마다 4 KiB 창을 다시 훑지 않는다(1MB 공격 문자열 선형 — 2026-10-06)."""
    out: list[str] = []
    pos = 0
    n = len(text)
    token_end = next_at = next_query = -1
    for m in _SCHEME.finditer(text):
        start = m.end()
        if start < pos:
            continue
        if token_end < start:
            stop = _URL_TOKEN_END.search(text, start)
            token_end = stop.start() if stop else n
        end = min(token_end, start + 4096)
        user = _URL_USER.match(text, start, end)
        colon = user.end() if user else start
        if colon >= end:
            continue
        if text[colon] == "@":
            if colon > start and not text.startswith(MASK, start):
                out.append(text[pos:start])
                out.append(MASK)
                pos = colon
            continue
        if text[colon] != ":":
            continue
        rest_start = colon + 1
        if rest_start >= end or text.startswith(MASK, rest_start):
            continue
        if next_at < rest_start:
            found = text.find("@", rest_start)
            next_at = found if found >= 0 else n
        if next_query < rest_start:
            query = _URL_QUERY_START.search(text, rest_start)
            next_query = query.start() if query else n
        if _PORT.match(text, rest_start, end) and next_at >= min(next_query, end):
            continue
        at = text.rfind("@", rest_start, end) if next_at < end else -1
        if at == rest_start:
            continue
        if at < 0:
            if text[rest_start] == "/":
                continue
            cut = end
        else:
            cut = at
        out.append(text[pos:rest_start])
        out.append(MASK)
        pos = cut
    if not out:
        return text
    out.append(text[pos:])
    return "".join(out)


def _kv_value_rx(text: str, key_start: int, value_start: int) -> re.Pattern[str]:
    """따옴표 없는 비밀 값의 끝 — `k=v;k=v` 연결 문자열 문맥(키 앞이 `;`이거나 값 뒤에 `;키=`)은
    `;`까지 · 줄 머리 키(설정 파일 줄 `db.password=…`)는 줄 끝까지 · 그 밖은 공백까지(AUDIT-4 —
    `,` `;` `&`가 섞인 비밀번호의 꼬리를 남기지 않는다 · 과잉 가림은 허용 방향)."""
    before = key_start
    while before > 0 and text[before - 1] in " \t":
        before -= 1
    if (before > 0 and text[before - 1] == ";") or _KV_CONN_NEXT.match(text, value_start):
        return _KV_CONN_VALUE
    if before == 0 or text[before - 1] in "\r\n":
        return _KV_LINE_VALUE
    return _KV_WORD_VALUE


def _mask_key_values(text: str) -> str:
    """`KEY=VALUE`·`KEY: VALUE` — 비밀 키의 값을 가린다(키는 겹쳐 찾는다 — `OPTS=password=x`의
    안쪽 키도 본다). 따옴표 없는 값의 끝은 `_kv_value_rx`."""
    out: list[str] = []
    pos = 0
    for m in _KV_KEY.finditer(text):
        if m.start() < pos:
            continue
        kind = key_kind(m.group("key"))
        if kind == NONE:
            continue
        value = _KV_QUOTED_VALUE.match(text, m.end()) or _kv_value_rx(
            text, m.start(), m.end()
        ).match(text, m.end())
        if value is None or value.end() == m.end() or value.group(0).startswith(MASK):
            continue
        if _posix_dir(m.group("key"), value.group(0)) or not _secret_value(kind, value.group(0)):
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


def _mask_connect_string(text: str) -> str:
    """값 전체가 접속 문자열 `user/pass@db`면 비밀번호를 가린다(전체 일치만 — AUDIT-3)."""
    m = _CONNECT_STRING.fullmatch(text)
    if m is None or _masked(text, *m.span("pw")):
        return text
    return text[: m.start("pw")] + MASK + text[m.end("pw") :]


def _rules(text: str) -> str:
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
    if "/" in out and "@" in out:
        out = _mask_connect_string(out)
    if "bearer" in low:
        out = _sub_pw(_BEARER, out)
    if "using" in low and "user" in low and ("connect" in low or "attach" in low):
        out = _sub_pw(_DB2_USING, out)
    if "-" in out:
        if "-D" in out:
            out = _sub_pw(_JVM_PROP, out, check=_key_check)
        out = _sub_pw(_CLI_FLAG, out, check=_key_check)
        if "sshpass" in low:
            out = _sub_pw(_SSHPASS, out)
        if "-u" in out or "--user" in out:
            out = _sub_pw(_USER_PASS, out)
        if "-u" in out or "-U" in out:
            out = _sub_pw(_USER_SEP_PASS, out)
        if "-P" in out or "-a" in out or "-w" in out:
            out = _sub_pw(_SHORT_SECRET_FLAG, out)
        if "-p" in out:
            out = _sub_pw(_ATTACHED_P, out)
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


def _scrub_piece(text: str) -> str:
    if not text.isascii():
        norm = _norm(text)
        if norm != text:
            masked = _rules(norm)
            if masked != norm:
                return masked  # 정규화한 꼴에서만 드러나는 자격증명 — 정규화본을 낸다
    return _rules(text)


def _command_text(text: str) -> str:
    """명령줄 문맥 — 첫 토큰(실행 파일)만 남기고 나머지 인자는 통째로 가린다(AUDIT-1). 첫 토큰도
    일반 규칙을 지나고, `NAME=값` 대입(환경변수 접두)이면 값을 가린다."""
    head = _COMMAND_HEAD.match(text)
    if head is None:
        return MASK if text.strip() else text
    first = _scrub_piece(head.group(0))
    if "=" in first and first.lstrip()[:1] not in ('"', "'"):
        first = first.split("=", 1)[0] + "=" + MASK
    rest = text[head.end() :]
    return first + rest if not rest.strip() else f"{first} {MASK}"


def scrub_text(text: str, *, command: bool = False) -> str:
    """텍스트 안의 자격증명 값을 가린다. `command`(명령줄 문맥)면 첫 토큰(실행 파일)만 남기고 나머지
    인자를 통째로 가린다.

    64 KiB 넘는 텍스트는 줄 경계 조각으로 나눠 처리한다(규칙은 줄을 넘지 않는다).
    """
    if not text:
        return text
    if command:
        return _command_text(text)
    if len(text) <= _TEXT_CHUNK or "\n" not in text:
        return _scrub_piece(text)
    pieces: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + _TEXT_CHUNK)
        if end < len(text):
            newline = text.rfind("\n", start, end)
            end = newline + 1 if newline > start else end
        pieces.append(_scrub_piece(text[start:end]))
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
        if lk in _NAME_FIELDS or lk.endswith(_NAME_SUFFIXES):
            if isinstance(v, str):
                names.append(v)
            elif isinstance(v, list):
                names += [x for x in v if isinstance(x, str)]
        elif lk in _VALUE_FIELDS or lk.endswith(_VALUE_SUFFIXES):
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
        if command:
            # 명령줄 문맥의 객체 — 칸 이름으로 실행 파일 칸을 고를 수 없어(모양 미확인) 잎을 전부
            # 가린다(배열은 첫 원소만 남긴다 · REAUDIT-1)
            force = SECRET
        pair = _pair_info(src)
        parallel = _parallel_info(src)
        for key, value in src.items():
            if _posix_dir(key, value):  # 작업 디렉터리 환경변수 — 비밀 아님(I-1)
                dst[key] = self._emit(value, force, command, depth, (path, key))
                continue
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
            if command and i > 0:
                kind = SECRET  # 명령줄 문맥 배열 — 첫 원소(실행 파일) 뒤는 인자다(AUDIT-1)
            dst.append(self._emit(item, kind, cmd, depth, (path, i)))

    def _leaf(self, value: Any, kind: int, command: bool) -> Any:
        if kind != NONE:
            return _mask_leaf(value, kind)
        if isinstance(value, str):
            return self._text(value, command)
        return value

    def _text(self, value: str, command: bool) -> str:
        if command:  # JSON으로 디코드하지 않는다 — 배열 원소 하나하나가 인자다(통째로 첫 토큰 규칙)
            return scrub_text(value, command=True)
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
