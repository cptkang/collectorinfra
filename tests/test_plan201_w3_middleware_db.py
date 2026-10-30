"""plans/201 W3 — DB 엔진 미들웨어 시그니처(정탐·오탐 픽스처) · `classify_process_kind`.

DB 엔진 규칙은 **실행 파일 이름 경계 정규식**이다 — cmdline 맨 앞 토큰(경로 접두 허용)이 엔진 실행 파일일
때만 잡는다. WAS 인자의 JDBC 드라이버 jar · 수집기(`*_exporter`) · 래퍼(`mysqld_safe`)는 걸리지 않는다.
기존 13종 규칙은 고치지 않았다(추가만).
"""

from __future__ import annotations

import pytest

from src.domain import middleware as mw

_EXISTING_KINDS = [
    "tomcat", "jboss-wildfly", "weblogic", "websphere", "jeus", "nginx", "apache-httpd", "webtob",
    "redis", "kafka", "rabbitmq", "zookeeper", "tmax",
]
_DB_KINDS = ["db2", "mysql", "mariadb", "postgresql", "oracle", "tibero"]


def _kind(cmdline: str) -> str | None:
    scan = mw.identify([{"pid": 1, "cmdline": cmdline}])
    return scan.matched[0].kind if scan.matched else None


def test_existing_rules_untouched_and_db_rules_appended() -> None:
    kinds = [s.kind for s in mw.load_signatures()]
    assert kinds[: len(_EXISTING_KINDS)] == _EXISTING_KINDS
    assert kinds[len(_EXISTING_KINDS):] == _DB_KINDS
    for sig in mw.load_signatures():
        if sig.kind in _DB_KINDS:
            assert sig.match_regex and not sig.match_any, sig.kind   # 경계 정규식만


@pytest.mark.parametrize(("cmdline", "kind"), [
    ("db2sysc 0", "db2"),
    ("/opt/ibm/db2/V11.5/adm/db2sysc 0", "db2"),
    ("db2sysc", "db2"),
    ("/usr/sbin/mysqld --basedir=/usr --datadir=/var/lib/mysql", "mysql"),
    ("mysqld", "mysql"),
    ("/usr/sbin/mariadbd --user=mysql", "mariadb"),
    ("/usr/pgsql-15/bin/postgres -D /var/lib/pgsql/15/data", "postgresql"),
    ("postgres: checkpointer", "postgresql"),
    ("postgres: 15/main: walwriter", "postgresql"),
    ("/usr/lib/postgresql/9.6/bin/postmaster -D /data", "postgresql"),
    ("ora_pmon_ORCL", "oracle"),
    ("oracleORCL (LOCAL=NO)", "oracle"),
    ("/u01/app/oracle/product/19c/bin/oracle", "oracle"),
    ("tbsvr -t NORMAL -SVR_SID tibero", "tibero"),
    ("/home/tibero/tibero6/bin/tbsvr", "tibero"),
    ("tbsvr_FGWP000 -t NORMAL", "tibero"),
])
def test_db_engine_true_positive(cmdline: str, kind: str) -> None:
    assert _kind(cmdline) == kind


@pytest.mark.parametrize("cmdline", [
    # WAS가 JDBC 드라이버를 싣는 경우 — 엔진이 아니다(tomcat이 잡힌다)
    "/usr/bin/java -cp /opt/app/lib/postgresql-42.7.3.jar:/opt/app/lib/mysql-connector.jar com.example.App",
    "/usr/bin/java -Dspring.datasource.url=jdbc:postgresql://h/db -jar app.jar",
    "/usr/local/bin/postgres_exporter --web.listen-address=:9187",
    "/usr/local/bin/mysqld_exporter --config.my-cnf=/etc/.my.cnf",
    "/bin/sh /usr/bin/mysqld_safe --datadir=/var/lib/mysql",
    "/opt/exporters/oracledb_exporter --default.metrics",
    "oracle_exporter",
    "/home/oracle/scripts/backup.sh",
    "/usr/bin/python3 /opt/db2sysc_monitor.py",
    "psql -U postgres -c select",
    "grep postgres",                 # 공통 제외(조사 명령 자신)
    "/usr/bin/tbsvr_exporter",
])
def test_db_engine_false_positive(cmdline: str) -> None:
    assert _kind(cmdline) not in _DB_KINDS


def test_tomcat_with_jdbc_jar_stays_tomcat() -> None:
    tomcat = ("/usr/lib/jvm/java-17/bin/java -Dcatalina.home=/opt/tomcat -cp /opt/tomcat/lib/postgresql-42.jar "
              "org.apache.catalina.startup.Bootstrap start")
    assert _kind(tomcat) == "tomcat"


def test_oracle_instance_from_pmon() -> None:
    scan = mw.identify([{"pid": 1, "cmdline": "ora_pmon_PRD01"}])
    assert scan.matched[0].instance == "PRD01"


def test_classify_process_kind_prefers_args_then_name() -> None:
    assert mw.classify_process_kind("java", "/usr/bin/java -Dcatalina.home=/x "
                                    "org.apache.catalina.startup.Bootstrap") == "tomcat"
    # 인자가 절단·비어 있으면 이름으로 판정
    assert mw.classify_process_kind("db2sysc", "") == "db2"
    assert mw.classify_process_kind("postgres", "-D /var/lib/pgsql/data") == "postgresql"
    assert mw.classify_process_kind("sshd", "/usr/sbin/sshd -D") is None
    assert mw.classify_process_kind("", "") is None
