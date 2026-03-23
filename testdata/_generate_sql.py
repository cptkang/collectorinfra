#!/usr/bin/env python3
"""
POLESTAR 테스트 데이터 SQL 생성기.

이 스크립트는 02_insert_cmm_resource.sql 과 03_insert_core_config_prop.sql을 생성합니다.
직접 실행: python testdata/_generate_sql.py
"""

import os

# ==============================================================================
# 설정
# ==============================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CMM_OUTPUT = os.path.join(BASE_DIR, "02_insert_cmm_resource.sql")
CONFIG_OUTPUT = os.path.join(BASE_DIR, "03_insert_core_config_prop.sql")

# ID 시작 값
CMM_ID_START = 300001
CONFIG_ID_START = 500001

# 타임스탬프 범위 (epoch ms)
# 2022-01-01 ~ 2025-06-30
CTIME_BASE = 1640995200000   # 2022-01-01 00:00:00 UTC
MTIME_BASE = 1719705600000   # 2024-06-30 00:00:00 UTC

# ==============================================================================
# 서버 목록 (plans/17 문서 기반)
# ==============================================================================
SERVERS = [
    # (hostname, group, profile, platform, cpu_cores, ip)
    # WEB: svr-web-01~05는 2코어, svr-web-06~10은 4코어
    ("svr-web-01", "WEB", "A", "VMware",  2, "10.0.1.1"),
    ("svr-web-02", "WEB", "A", "VMware",  2, "10.0.1.2"),
    ("svr-web-03", "WEB", "A", "VMware",  2, "10.0.1.3"),
    ("svr-web-04", "WEB", "A", "VMware",  2, "10.0.1.4"),
    ("svr-web-05", "WEB", "A", "VMware",  2, "10.0.1.5"),   # AVAIL_STATUS=1
    ("svr-web-06", "WEB", "A", "VMware",  4, "10.0.1.6"),
    ("svr-web-07", "WEB", "A", "HPE",     4, "10.0.1.7"),
    ("svr-web-08", "WEB", "A", "HPE",     4, "10.0.1.8"),
    ("svr-web-09", "WEB", "A", "VMware",  4, "10.0.1.9"),
    ("svr-web-10", "WEB", "A", "VMware",  4, "10.0.1.10"),
    # WAS: svr-was-01~05는 4코어, svr-was-06~10은 8코어
    ("svr-was-01", "WAS", "B", "VMware",  4, "10.0.2.1"),
    ("svr-was-02", "WAS", "B", "VMware",  4, "10.0.2.2"),
    ("svr-was-03", "WAS", "B", "HPE",     4, "10.0.2.3"),
    ("svr-was-04", "WAS", "B", "HPE",     4, "10.0.2.4"),
    ("svr-was-05", "WAS", "B", "VMware",  4, "10.0.2.5"),
    ("svr-was-06", "WAS", "B", "VMware",  8, "10.0.2.6"),
    ("svr-was-07", "WAS", "B", "Dell",    8, "10.0.2.7"),   # AVAIL_STATUS=1
    ("svr-was-08", "WAS", "B", "Dell",    8, "10.0.2.8"),
    ("svr-was-09", "WAS", "B", "VMware",  8, "10.0.2.9"),
    ("svr-was-10", "WAS", "B", "HPE",     8, "10.0.2.10"),
    # DB: svr-db-01~05는 8코어, svr-db-06~10은 16코어
    ("svr-db-01",  "DB",  "C", "HPE",     8, "10.0.3.1"),
    ("svr-db-02",  "DB",  "C", "HPE",     8, "10.0.3.2"),
    ("svr-db-03",  "DB",  "C", "Dell",    8, "10.0.3.3"),
    ("svr-db-04",  "DB",  "C", "Dell",    8, "10.0.3.4"),
    ("svr-db-05",  "DB",  "C", "HPE",     8, "10.0.3.5"),
    ("svr-db-06",  "DB",  "C", "HPE",    16, "10.0.3.6"),
    ("svr-db-07",  "DB",  "C", "Dell",   16, "10.0.3.7"),
    ("svr-db-08",  "DB",  "C", "Dell",   16, "10.0.3.8"),
    ("svr-db-09",  "DB",  "C", "HPE",    16, "10.0.3.9"),
    ("svr-db-10",  "DB",  "C", "HPE",    16, "10.0.3.10"),
]

# 비정상 ServiceResource 서버 (5대 = 전체 30대의 ~17%)
ABNORMAL_SERVERS = {"svr-web-05", "svr-web-08", "svr-was-03", "svr-was-07", "svr-db-04"}

# 파일시스템 목록 (프로파일별)
FS_WEB_BASE = ["/", "/boot", "/fsutil", "/fsapp", "/fslog", "/fshome"]
FS_WEB_EXTRA = ["/fsdump", "/var/crash"]  # svr-web-06~10에 추가

FS_WAS_BASE = ["/", "/boot", "/fsutil", "/fsapp", "/fslog", "/fshome", "/fswas", "/fswaslog"]
FS_WAS_EXTRA = ["/fsdump", "/var/crash"]  # svr-was-06~10에 추가

FS_DB_BASE = ["/", "/boot", "/fsutil", "/fslog", "/fshome", "/fsudb",
              "/FSDB2INST", "/FSDB2LOG", "/FSDB2DAT", "/FSDB2MON"]
FS_DB_EXTRA = ["/FSDB2BAK", "/FSDB2ARC", "/var/crash", "/boot/efi"]  # svr-db-06~10에 추가

# 네트워크 인터페이스
NIC_WEB = ["ens192", "ens224"]
NIC_WAS_SMALL = ["bond0", "ens2f0"]
NIC_WAS_LARGE = ["bond0", "ens2f0", "ens5f0"]
NIC_DB_SMALL = ["ens192", "ens224"]
NIC_DB_LARGE = ["ens192", "ens224", "bond0"]


def sql_str(val):
    """SQL 문자열 이스케이프."""
    if val is None:
        return "NULL"
    return "'" + str(val).replace("'", "''") + "'"


def sql_int(val):
    """SQL 정수/NULL 변환."""
    if val is None:
        return "NULL"
    return str(val)


class IdGenerator:
    """순차 ID 생성기."""
    def __init__(self, start: int):
        self._next = start

    def next(self) -> int:
        val = self._next
        self._next += 1
        return val

    @property
    def current(self) -> int:
        return self._next - 1


def make_ctime(server_idx: int) -> int:
    """서버 인덱스별 생성 시각."""
    return CTIME_BASE + server_idx * 86400000 * 30  # 약 30일 간격


def make_mtime(server_idx: int) -> int:
    """서버 인덱스별 수정 시각."""
    return MTIME_BASE + server_idx * 86400000 * 7   # 약 7일 간격


def insert_cmm_resource(
    rid, dtype, acl_id, avail_status, ctime, mtime, description,
    haschildren, hostname, id_ancestry, importance_id, inheritstatus,
    invisible, ipaddress, name, parent_resource_id, platform_resource_id,
    resource_key, resource_type, service_resource_id, order_num=None
):
    """CMM_RESOURCE INSERT 문 생성."""
    cols = (
        "ID, DTYPE, ACL_ID, AVAIL_STATUS, CTIME, MTIME, DESCRIPTION, "
        "HASCHILDREN, HOSTNAME, ID_ANCESTRY, IMPORTANCE_ID, INHERITSTATUS, "
        "INVISIBLE, IPADDRESS, NAME, PARENT_RESOURCE_ID, PLATFORM_RESOURCE_ID, "
        "RESOURCE_KEY, RESOURCE_TYPE, SERVICE_RESOURCE_ID, ORDER_NUM"
    )
    vals = (
        f"{rid}, {sql_str(dtype)}, {acl_id}, {sql_int(avail_status)}, "
        f"{sql_int(ctime)}, {sql_int(mtime)}, {sql_str(description)}, "
        f"{sql_int(haschildren)}, {sql_str(hostname)}, {sql_str(id_ancestry)}, "
        f"{importance_id}, {inheritstatus}, {invisible}, {sql_str(ipaddress)}, "
        f"{sql_str(name)}, {sql_int(parent_resource_id)}, {sql_int(platform_resource_id)}, "
        f"{sql_str(resource_key)}, {sql_str(resource_type)}, {sql_int(service_resource_id)}, "
        f"{sql_int(order_num)}"
    )
    return f"INSERT INTO POLESTAR.CMM_RESOURCE ({cols}) VALUES ({vals});"


def build_server_resources(server_idx, hostname, group, profile, platform, cpu_cores, ip, id_gen):
    """한 서버의 모든 리소스 INSERT 문 생성."""
    lines = []
    ctime = make_ctime(server_idx)
    mtime = make_mtime(server_idx)

    is_abnormal = hostname in ABNORMAL_SERVERS
    svc_avail = 1 if is_abnormal else 0

    # 1. ServiceResource (최상위)
    svc_id = id_gen.next()
    key_prefix = f"test-{svc_id:06d}"
    # root ancestry: '1>{svc_id}>'
    ancestry_root = f"1>{svc_id}>"

    lines.append(insert_cmm_resource(
        rid=svc_id, dtype="ServiceResource", acl_id=1, avail_status=svc_avail,
        ctime=ctime, mtime=mtime, description=f"{hostname} 서버",
        haschildren=1, hostname=hostname, id_ancestry=f"1>",
        importance_id=1, inheritstatus=0, invisible=0, ipaddress=ip,
        name=hostname, parent_resource_id=None, platform_resource_id=None,
        resource_key=key_prefix, resource_type="platform.server",
        service_resource_id=None
    ))

    # 2. monitor group
    mg_id = id_gen.next()
    mg_ancestry = f"1>{svc_id}>"
    lines.append(insert_cmm_resource(
        rid=mg_id, dtype="ServiceResource", acl_id=1, avail_status=0,
        ctime=ctime, mtime=mtime, description=None,
        haschildren=1, hostname=hostname, id_ancestry=mg_ancestry,
        importance_id=1, inheritstatus=0, invisible=0, ipaddress=ip,
        name="monitor group", parent_resource_id=svc_id, platform_resource_id=svc_id,
        resource_key=f"test-{mg_id:06d}", resource_type="management.MonitorGroup",
        service_resource_id=svc_id
    ))

    def add_resource(name, rtype, parent_id, desc=None, avail=0, hostname_val=None,
                     ip_val=None, haschildren_val=0, order=None):
        rid = id_gen.next()
        anc = f"1>{svc_id}>{parent_id}>"
        lines.append(insert_cmm_resource(
            rid=rid, dtype="Resource", acl_id=1, avail_status=avail,
            ctime=ctime, mtime=mtime, description=desc,
            haschildren=haschildren_val, hostname=hostname_val, id_ancestry=anc,
            importance_id=1, inheritstatus=0, invisible=0, ipaddress=ip_val,
            name=name, parent_resource_id=parent_id, platform_resource_id=svc_id,
            resource_key=f"test-{rid:06d}", resource_type=rtype,
            service_resource_id=svc_id, order_num=order
        ))
        return rid

    # 3. CPU 컨테이너 + 코어
    cpus_id = add_resource("CPU", "server.Cpus", svc_id, "CPU 관리",
                           hostname_val=hostname, ip_val=ip, haschildren_val=1)
    for c in range(1, cpu_cores + 1):
        add_resource(f"Core{c}", "server.Cpu", cpus_id, order=c)

    # 4. 디스크 컨테이너
    add_resource("디스크", "server.Disks", svc_id, "전체 디스크",
                 hostname_val=hostname, ip_val=ip)

    # 5. 파일시스템 컨테이너 + 개별 파일시스템
    fs_id = add_resource("파일시스템", "server.FileSystems", svc_id, "파일시스템 관리",
                         hostname_val=hostname, ip_val=ip, haschildren_val=1)

    # 파일시스템 목록 결정
    suffix_num = int(hostname.split("-")[-1])
    if profile == "A":
        fs_list = list(FS_WEB_BASE)
        if suffix_num >= 6:
            fs_list.extend(FS_WEB_EXTRA)
    elif profile == "B":
        fs_list = list(FS_WAS_BASE)
        if suffix_num >= 6:
            fs_list.extend(FS_WAS_EXTRA)
    else:  # C
        fs_list = list(FS_DB_BASE)
        if suffix_num >= 6:
            fs_list.extend(FS_DB_EXTRA)

    for fs_name in fs_list:
        add_resource(fs_name, "server.FileSystem", fs_id)

    # 6. 메모리 + 하위
    mem_id = add_resource("메모리", "server.Memory", svc_id, "물리적 메모리 관리",
                          hostname_val=hostname, ip_val=ip, haschildren_val=1)
    add_resource("기타 메모리", "server.OtherMemory", mem_id, "페이지, 문맥교환 횟수 등에 대한 정보")
    add_resource("가상메모리", "server.VirtualMemory", mem_id)

    # 7. 네트워크 세션
    add_resource("네트워크 세션", "server.Netstat", svc_id, "네트워크 연결 정보에 대한 관리",
                 hostname_val=hostname, ip_val=ip)

    # 8. 네트워크 인터페이스 컨테이너 + 개별 인터페이스
    ni_id = add_resource("Network Interfaces", "server.NetworkInterfaces", svc_id,
                         "Network Interface 관리", hostname_val=hostname, ip_val=ip,
                         haschildren_val=1)

    if profile == "A":
        nics = NIC_WEB
    elif profile == "B":
        nics = NIC_WAS_LARGE if suffix_num >= 6 else NIC_WAS_SMALL
    else:
        nics = NIC_DB_LARGE if suffix_num >= 6 else NIC_DB_SMALL

    for nic_name in nics:
        add_resource(nic_name, "server.NetworkInterface", ni_id)

    # 9. 기타정보
    add_resource("기타정보", "server.Other", svc_id, "IPCS, OS Table 등에 대한 정보",
                 hostname_val=hostname, ip_val=ip)

    # 10. 프로세스
    add_resource("프로세스", "server.Process", svc_id, "실행중인 프로세스 관제",
                 hostname_val=hostname, ip_val=ip)

    # 11. LogMonitor (monitor group 하위) - 일부 비정상
    if profile == "A":
        log_monitors = [
            ("Log Monitor", None, 1 if (suffix_num in {3, 6, 9}) else 0),
            ("Syslog Monitor", None, 1 if (suffix_num in {2, 5, 8}) else 0),
        ]
    elif profile == "B":
        log_monitors = [
            ("Log Monitor", None, 1 if (suffix_num in {4, 7, 10}) else 0),
            ("Syslog Monitor", None, 1 if (suffix_num in {2, 6}) else 0),
            ("App Log", "애플리케이션 로그", 1 if (suffix_num in {3, 8}) else 0),
        ]
    else:
        log_monitors = [
            ("DB2진단로그", None, 1 if (suffix_num in {4, 7, 10}) else 0),
            ("Syslog Monitor", None, 1 if (suffix_num in {2, 5, 8}) else 0),
        ]

    for lm_name, lm_desc, lm_avail in log_monitors:
        add_resource(lm_name, "server.LogMonitor", mg_id, lm_desc, avail=lm_avail)

    # 12. ProcessMonitor (monitor group 하위)
    # 각 프로세스별 약 30%를 비정상으로 설정
    if profile == "A":
        proc_monitors = [
            ("httpd", "httpd", 1 if (suffix_num in {1, 5, 9}) else 0),      # 3개/10
            ("nginx", "nginx", 1 if (suffix_num in {2, 4, 6, 8, 10}) else 0),  # 5개/10
        ]
    elif profile == "B":
        proc_monitors = [
            ("java", "java", 1 if (suffix_num in {3, 5, 7}) else 0),        # 3개/10
            ("tomcat", "tomcat", 1 if (suffix_num in {2, 4, 6, 8}) else 0),  # 4개/10
            ("node", "node", 1 if (suffix_num in {1, 5, 9}) else 0),        # 3개/10
        ]
    else:
        proc_monitors = [
            ("db2sysc", "db2sysc", 1 if (suffix_num in {2, 4, 6, 8, 10}) else 0),  # 5개/10
            ("db2agent", "db2agent", 1 if (suffix_num in {1, 3, 5, 7, 9}) else 0),  # 5개/10
        ]

    for pm_name, pm_desc, pm_avail in proc_monitors:
        add_resource(pm_name, "server.ProcessMonitor", mg_id, pm_desc, avail=pm_avail)

    # 13. HBA (DB 서버만)
    if profile == "C":
        hbas_id = add_resource("HBA", "server.Hbas", svc_id, "HBA",
                               hostname_val=hostname, ip_val=ip, haschildren_val=1)
        # HBA 어댑터 수: 1~2개
        hba_count = 2 if suffix_num >= 6 else 1
        for h in range(hba_count):
            hba_sn = f"SN1200E2P.TEST{svc_id:06d}{chr(65+h)}"
            hba_id = add_resource(hba_sn, "server.Hba", hbas_id, haschildren_val=1)
            # HBA 포트: 각 어댑터당 2개 (약 25% 비정상)
            port_base = h * 2 + 1
            # 비정상 포트 설정
            port1_avail = 1 if (suffix_num in {5, 10} and h == 0) else 0
            port2_avail = 1 if (suffix_num in {1, 3, 5, 7, 9} and h == 0) else (
                1 if (suffix_num in {6, 8, 10} and h == 1) else 0
            )
            add_resource(f"host{port_base}", "server.HbaPort", hba_id, avail=port1_avail)
            add_resource(f"host{port_base+1}", "server.HbaPort", hba_id, avail=port2_avail)

    return lines


def build_config_props(server_idx, hostname, group, profile, platform, cpu_cores, ip, config_id_gen):
    """한 서버의 CORE_CONFIG_PROP INSERT 문 12개 생성."""
    lines = []

    # CONFIGURATION_ID: 301~330
    conf_id = 301 + server_idx

    # 타임스탬프
    ts = make_ctime(server_idx) + 3600000  # ctime + 1시간

    # 플랫폼별 설정
    if platform == "VMware":
        model = "VMware Virtual Platform"
        vendor = "VMware, Inc."
        serial_prefix = "VMware-"
    elif platform == "HPE":
        model = "ProLiant DL380 Gen10 Plus"
        vendor = "HPE"
        serial_prefix = "HOST-"
    else:  # Dell
        model = "PowerEdge R750"
        vendor = "Dell Inc."
        serial_prefix = "SVCTAG-"

    serial = f"{serial_prefix}{hostname.replace('-', '').upper()}"

    # AgentVersion: 혼합
    versions = ["7.6.26_6", "7.6.28_1", "7.6.30_2"]
    agent_version = versions[server_idx % 3]

    # OS 버전: 혼합
    os_versions = ["3.10.0-957.el7.x86_64", "4.18.0-305.el8.x86_64", "5.14.0-70.el9.x86_64"]
    os_version = os_versions[server_idx % 3]

    # AgentID 타임스탬프 부분
    agent_ts_parts = [
        "20220315091000", "20220410143000", "20220505160000",
        "20220601100000", "20220715120000", "20220820093000",
        "20220915140000", "20221010110000", "20221105150000",
        "20221201080000", "20210120090000", "20210315110000",
        "20210501130000", "20210620100000", "20210715140000",
        "20210820160000", "20210915090000", "20211010120000",
        "20211105100000", "20211201080000", "20200601100000",
        "20200715120000", "20200901140000", "20201015110000",
        "20201120130000", "20201215090000", "20210115100000",
        "20210220110000", "20210315080000", "20210420090000",
    ]
    agent_id = f"MA_{hostname}_{agent_ts_parts[server_idx]}"

    os_param = "kernel.shmmax = 68719476736"

    # 12종 설정 (PROPERTYDEFINITION_ID: 454~465)
    props = [
        ("Vendor",       vendor,                                 454),
        ("Model",        model,                                  455),
        ("OSType",       "LINUX",                                456),
        ("OSVerson",     os_version,                             457),
        ("GMT",          "GMT+09:00",                            458),
        ("SerialNumber", serial,                                 459),
        ("Hostname",     hostname,                               460),
        ("IPaddress",    ip,                                     461),
        ("AgentVersion", agent_version,                          462),
        ("InstallPath",  "/fsutil/polestar/agent/NNPAgent/MAgent/", 463),
        ("AgentID",      agent_id,                               464),
        ("OSParameter",  os_param,                               465),
    ]

    for prop_name, prop_value, prop_def_id in props:
        pid = config_id_gen.next()
        line = (
            f"INSERT INTO POLESTAR.CORE_CONFIG_PROP "
            f"(ID, DTYPE, NAME, TIME_STAMP, IS_LOB, STRINGVALUE_SHORT, "
            f"CONFIGURATION_ID, PROPERTYDEFINITION_ID) VALUES "
            f"({pid}, 'SIMPLE', {sql_str(prop_name)}, {ts}, 0, {sql_str(prop_value)}, "
            f"{conf_id}, {prop_def_id});"
        )
        lines.append(line)

    return lines


def main():
    cmm_id_gen = IdGenerator(CMM_ID_START)
    config_id_gen = IdGenerator(CONFIG_ID_START)

    cmm_lines = []
    config_lines = []

    # 헤더 주석
    cmm_lines.append("-- =============================================================================")
    cmm_lines.append("-- POLESTAR 테스트 데이터: CMM_RESOURCE")
    cmm_lines.append("-- 파일: testdata/02_insert_cmm_resource.sql")
    cmm_lines.append("-- 설명: 서버 30대의 리소스 데이터 INSERT (약 780행)")
    cmm_lines.append("-- DB 엔진: IBM DB2 11.5+")
    cmm_lines.append("-- 인코딩: UTF-8")
    cmm_lines.append("-- =============================================================================")
    cmm_lines.append("")

    config_lines.append("-- =============================================================================")
    config_lines.append("-- POLESTAR 테스트 데이터: CORE_CONFIG_PROP")
    config_lines.append("-- 파일: testdata/03_insert_core_config_prop.sql")
    config_lines.append("-- 설명: 서버 30대 x 12설정 = 360행 INSERT")
    config_lines.append("-- DB 엔진: IBM DB2 11.5+")
    config_lines.append("-- 인코딩: UTF-8")
    config_lines.append("-- =============================================================================")
    config_lines.append("")

    total_cmm_rows = 0
    total_config_rows = 0

    for idx, (hostname, group, profile, platform, cpu_cores, ip) in enumerate(SERVERS):
        # CMM_RESOURCE
        cmm_lines.append(f"-- ---------------------------------------------------------------------------")
        cmm_lines.append(f"-- {hostname} ({group}, Profile {profile}, {platform}, {cpu_cores}코어, {ip})")
        cmm_lines.append(f"-- ---------------------------------------------------------------------------")
        server_inserts = build_server_resources(idx, hostname, group, profile, platform, cpu_cores, ip, cmm_id_gen)
        cmm_lines.extend(server_inserts)
        cmm_lines.append("")
        total_cmm_rows += len(server_inserts)

        # CORE_CONFIG_PROP
        config_lines.append(f"-- {hostname} (CONFIGURATION_ID={301 + idx})")
        config_inserts = build_config_props(idx, hostname, group, profile, platform, cpu_cores, ip, config_id_gen)
        config_lines.extend(config_inserts)
        config_lines.append("")
        total_config_rows += len(config_inserts)

    # COMMIT
    cmm_lines.append("-- COMMIT")
    cmm_lines.append("COMMIT;")
    cmm_lines.append("")
    cmm_lines.append(f"-- 총 INSERT 행 수: {total_cmm_rows}")

    config_lines.append("-- COMMIT")
    config_lines.append("COMMIT;")
    config_lines.append("")
    config_lines.append(f"-- 총 INSERT 행 수: {total_config_rows}")

    # 파일 쓰기
    with open(CMM_OUTPUT, "w", encoding="utf-8") as f:
        f.write("\n".join(cmm_lines) + "\n")

    with open(CONFIG_OUTPUT, "w", encoding="utf-8") as f:
        f.write("\n".join(config_lines) + "\n")

    print(f"생성 완료:")
    print(f"  {CMM_OUTPUT}: {total_cmm_rows}행")
    print(f"  {CONFIG_OUTPUT}: {total_config_rows}행")
    print(f"  CMM_RESOURCE ID 범위: {CMM_ID_START} ~ {cmm_id_gen.current}")
    print(f"  CORE_CONFIG_PROP ID 범위: {CONFIG_ID_START} ~ {config_id_gen.current}")

    # 행 수 검증
    assert total_cmm_rows >= 600, f"CMM_RESOURCE 행 수 부족: {total_cmm_rows} < 600"
    assert total_config_rows == 360, f"CORE_CONFIG_PROP 행 수 불일치: {total_config_rows} != 360"

    # AVAIL_STATUS 통계
    abnormal_count = 0
    for line in cmm_lines:
        if "AVAIL_STATUS" not in line:
            continue
        # INSERT 문에서 AVAIL_STATUS 값 추출
        if "VALUES (" in line:
            vals_part = line.split("VALUES (")[1]
            fields = vals_part.split(",")
            # AVAIL_STATUS는 4번째 값 (0-indexed: 3)
            avail = fields[3].strip()
            if avail == "1":
                abnormal_count += 1
    abnormal_pct = (abnormal_count / total_cmm_rows) * 100 if total_cmm_rows > 0 else 0
    print(f"  AVAIL_STATUS=1 비정상: {abnormal_count}행 ({abnormal_pct:.1f}%)")
    print(f"  AVAIL_STATUS=0 정상: {total_cmm_rows - abnormal_count}행 ({100 - abnormal_pct:.1f}%)")


if __name__ == "__main__":
    main()
