"""녹화 응답 마스킹 (plans/87 §0.8 (4) 데이터 출처 표지 · U-6).

- 키 기반: 비밀·개인정보 키의 값을 가린다.
- 값 기반: 이메일·휴대폰·주민번호 형태를 가린다.
- SQL: 문자열·숫자 리터럴을 `?`로 바꾼다(바인드 값 노출 방지).
- URL: 쿼리 문자열 값만 가리고 키는 남긴다.
- ops-masked 출처는 호스트명·IP·인스턴스명을 **같은 입력 → 같은 가명**으로 바꿔
  조인 관계를 보존한다.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

SECRET_KEYS = {
    "password",
    "passwd",
    "token",
    "authorization",
    "cookie",
    "email",
    "phonenumber",
    "phone",
    "mobile",
    "allowip",
    "clientip",
    "remoteaddress",
    "userid",
    "username",
}
PSEUDONYM_HOST_KEYS = {"hostname", "host", "instancename", "instname", "domainname"}
PSEUDONYM_IP_KEYS = {"ipaddress", "ip", "serverip", "agentip"}
SQL_KEY_HINT = ("sql", "query")
URL_KEY_HINT = ("applicationname", "url", "servicename", "application")

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"\b01[016789][- ]?\d{3,4}[- ]?\d{4}\b")
_RRN = re.compile(r"\b\d{6}[- ]?[1-4]\d{6}\b")
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_SQL_STR = re.compile(r"'(?:[^']|'')*'")
_SQL_NUM = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w.])")
_URL_QUERY = re.compile(r"([?&])([^=&#\s]+)=([^&#\s]*)")


class Masker:
    def __init__(self, pseudonymize: bool = False, salt: str = "plans87") -> None:
        self.pseudonymize = pseudonymize
        self.salt = salt
        self.count = 0
        self.rules: set[str] = set()

    def _hit(self, rule: str) -> None:
        self.count += 1
        self.rules.add(rule)

    def _digest(self, value: str) -> str:
        return hashlib.sha256(f"{self.salt}:{value}".encode()).hexdigest()

    def pseudo_host(self, value: str) -> str:
        self._hit("pseudonym-host")
        return f"host-{self._digest(value)[:8]}"

    def pseudo_ip(self, value: str) -> str:
        self._hit("pseudonym-ip")
        d = self._digest(value)
        return f"10.{int(d[0:2], 16)}.{int(d[2:4], 16)}.{int(d[4:6], 16)}"

    def mask_sql(self, text: str) -> str:
        out = _SQL_NUM.sub("?", _SQL_STR.sub("?", text))
        if out != text:
            self._hit("sql-literal")
        return out

    def mask_url(self, text: str) -> str:
        out = _URL_QUERY.sub(lambda m: f"{m.group(1)}{m.group(2)}=<v>", text)
        if out != text:
            self._hit("url-query")
        return out

    def mask_text(self, text: str) -> str:
        out = text
        for rx, tag in ((_EMAIL, "<email>"), (_RRN, "<rrn>"), (_PHONE, "<phone>")):
            new = rx.sub(tag, out)
            if new != out:
                self._hit(f"value-{tag.strip('<>')}")
                out = new
        if self.pseudonymize:
            new = _IPV4.sub(lambda m: self.pseudo_ip(m.group(0)), out)
            out = new
        return out

    def mask(self, obj: Any, key: str = "") -> Any:
        k = key.lower()
        if isinstance(obj, dict):
            return {kk: self.mask(v, kk) for kk, v in obj.items()}
        if isinstance(obj, list):
            return [self.mask(v, key) for v in obj]
        if not isinstance(obj, str):
            return obj
        if k in SECRET_KEYS:
            if obj:
                self._hit(f"key-{k}")
                return "<masked>"
            return obj
        if self.pseudonymize and k in PSEUDONYM_HOST_KEYS and obj:
            return self.pseudo_host(obj)
        if self.pseudonymize and k in PSEUDONYM_IP_KEYS and obj:
            return self.pseudo_ip(obj)
        if any(h in k for h in SQL_KEY_HINT):
            obj = self.mask_sql(obj)
        if any(h in k for h in URL_KEY_HINT):
            obj = self.mask_url(obj)
        return self.mask_text(obj)

    def mask_profile_text(self, text: str) -> str:
        """profile.txt(text/plain) — 줄 단위로 가린다.

        숫자 리터럴은 SQL 문장 줄에서만 가린다(다른 줄의 시각·경과 ms는 분석에 쓰인다).
        """
        lines = []
        for line in text.splitlines(keepends=True):
            if _SQL_LINE.search(line):
                line = self.mask_sql(line)
            elif _SQL_STR.search(line):
                line = _SQL_STR.sub("?", line)
                self._hit("sql-literal")
            lines.append(self.mask_text(self.mask_url(line)))
        return "".join(lines)


_SQL_LINE = re.compile(r"\b(select|insert|update|delete|merge|where|values)\b", re.IGNORECASE)
