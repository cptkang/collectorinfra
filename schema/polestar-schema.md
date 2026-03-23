# POLESTAR DB Schema Definition

> Source: `sample/Table Schema.xlsx`
> DB Engine: IBM DB2
> Schema: POLESTAR

---

## 1. CMM_RESOURCE

인프라 리소스(서버, CPU, 메모리, 디스크, 파일시스템, 네트워크 등)를 계층 구조로 관리하는 테이블.

| # | Column | Type | Nullable | Default | Description |
|---|--------|------|----------|---------|-------------|
| 1 | DTYPE | VARCHAR(31) | NOT NULL | | 리소스 구분 (ServiceResource, Resource) |
| 2 | ID | BIGINT | NOT NULL | IDENTITY | PK, 자동증가 |
| 3 | ACL_ID | BIGINT | NOT NULL | | 접근 제어 목록 ID |
| 4 | ACL_MANAGER_GROUP_ID | BIGINT | NULL | | ACL 관리 그룹 ID |
| 5 | ACL_MANAGER_ID | BIGINT | NULL | | ACL 관리자 ID |
| 6 | AVAIL_STATUS | INTEGER | NULL | | 가용 상태 (0=정상, 1=비정상) |
| 7 | CTIME | BIGINT | NULL | | 생성 시각 (epoch ms) |
| 8 | DTIME | BIGINT | NULL | | 삭제 시각 (epoch ms) |
| 9 | DESCRIPTION | VARCHAR(4000) | NULL | | 리소스 설명 (한국어) |
| 10 | GROUP_PATH | VARCHAR(4000) | NULL | | 그룹 경로 |
| 11 | HASCHILDREN | SMALLINT | NULL | | 하위 리소스 존재 여부 (0/1) |
| 12 | HOSTNAME | VARCHAR(255) | NULL | | 호스트명 |
| 13 | ID_ANCESTRY | VARCHAR(2000) | NULL | | 계층 경로 (예: 1>10812>150577>10807>873>) |
| 14 | IDENTIFIER | VARCHAR(4000) | NULL | | 식별자 |
| 15 | IMPORTANCE_ID | INTEGER | NOT NULL | 1 | 중요도 ID |
| 16 | IS_INHERIT_AVAIL_DEPEND | SMALLINT | NULL | | 가용성 의존 상속 여부 |
| 17 | IS_INHERIT_CUSTOM_CONF | SMALLINT | NULL | | 사용자 설정 상속 여부 |
| 18 | IS_INHERIT_MANAGER_ZONE | SMALLINT | NULL | | 관리 존 상속 여부 |
| 19 | INHERITSTATUS | SMALLINT | NOT NULL | | 상속 상태 |
| 20 | INVENTORYPOLLINGINTERVAL | INTEGER | NULL | | 인벤토리 폴링 간격 (초) |
| 21 | INVISIBLE | SMALLINT | NOT NULL | | 숨김 여부 |
| 22 | IPADDRESS | VARCHAR(255) | NULL | | IP 주소 |
| 23 | LC | INTEGER | NULL | | 라이프사이클 |
| 24 | LOCATION | VARCHAR(255) | NULL | | 물리적 위치 |
| 25 | LONGPOLLINGINTERVAL | INTEGER | NULL | | 장기 폴링 간격 (초) |
| 26 | MTIME | BIGINT | NULL | | 수정 시각 (epoch ms) |
| 27 | IS_MAINTENANCE | SMALLINT | NULL | | 유지보수 모드 여부 |
| 28 | MANAGER_ZONE | VARCHAR(255) | NULL | | 관리 존 |
| 29 | MESUREMENTPOLLINGINTERVAL | INTEGER | NULL | | 측정 폴링 간격 (초) |
| 30 | MODIFIEDBY | VARCHAR(255) | NULL | | 최종 수정자 |
| 31 | NAME | VARCHAR(900) | NOT NULL | | 리소스명 (예: CPU, 디스크, /fsutil 등) |
| 32 | OPTLOCK | INTEGER | NULL | | 낙관적 락 버전 |
| 33 | ORDER_NUM | INTEGER | NULL | | 정렬 순서 |
| 34 | PARENT_RESOURCE_ID | BIGINT | NULL | | 부모 리소스 ID (계층 구조) |
| 35 | PLATFORM_RESOURCE_ID | BIGINT | NULL | | 플랫폼 리소스 ID |
| 36 | POLLINGPOLICY | INTEGER | NULL | | 폴링 정책 |
| 37 | PRIORITY | INTEGER | NULL | | 우선순위 |
| 38 | RESOURCEICON | VARCHAR(255) | NULL | | 리소스 아이콘 |
| 39 | RESOURCE_KEY | VARCHAR(255) | NOT NULL | | 리소스 고유 키 (UUID 등) |
| 40 | RESOURCESTATUS | INTEGER | NULL | | 리소스 상태값 |
| 41 | RESOURCE_TYPE | VARCHAR(255) | NOT NULL | | 리소스 유형 (server.Cpu, server.FileSystem 등) |
| 42 | RESOURCETYPEVERSION | VARCHAR(255) | NULL | | 리소스 유형 버전 |
| 43 | SERVICE_RESOURCE_ID | BIGINT | NULL | | 서비스 리소스 ID |
| 44 | IS_SYNC_DESC | SMALLINT | NULL | | 설명 동기화 여부 |
| 45 | IS_SYNC_NAME | SMALLINT | NULL | | 이름 동기화 여부 |
| 46 | UUID | VARCHAR(255) | NULL | | UUID |
| 47 | VERSION | VARCHAR(255) | NULL | | 버전 |
| 48 | SYSTEM | SMALLINT | NULL | | 시스템 리소스 여부 |
| 49 | AVAIL_DEPEND_RESOURCE_ID | BIGINT | NULL | | 가용성 의존 리소스 ID |
| 50 | AVAIL_DEPEND_RESOURCE_ID_2 | BIGINT | NULL | | 가용성 의존 리소스 ID (보조) |
| 51 | CONNECTION_CONF_ID | BIGINT | NULL | | 연결 설정 ID |
| 52 | CUSTOM_CONF_ID | BIGINT | NULL | | 사용자 정의 설정 ID |
| 53 | REALTIME_INFO_ID | BIGINT | NULL | | 실시간 정보 ID |
| 54 | RESOURCE_CONF_ID | BIGINT | NULL | | 리소스 설정 ID |
| 55 | RESOURCE_PATH_ID | BIGINT | NULL | | 리소스 경로 ID |
| 56 | SCHEDULE_ID | BIGINT | NULL | | 스케줄 ID |
| 57 | RESOURCE_SYSTEM_ID | BIGINT | NULL | | 리소스 시스템 ID |
| 58 | GROUP_RESOURCE_ID | BIGINT | NULL | | 그룹 리소스 ID |
| 59 | BUSINESS_GROUP_RESOURCE_ID | BIGINT | NULL | | 비즈니스 그룹 리소스 ID |

### RESOURCE_TYPE 분류

| RESOURCE_TYPE | 설명 |
|--------------|------|
| management.MonitorGroup | 모니터 그룹 (ServiceResource) |
| server.Cpus | CPU 관리 (컨테이너) |
| server.Cpu | 개별 CPU 코어 |
| server.Memory | 물리적 메모리 |
| server.VirtualMemory | 가상 메모리 |
| server.OtherMemory | 기타 메모리 (페이지, 문맥교환) |
| server.Disks | 디스크 관리 (컨테이너) |
| server.FileSystems | 파일시스템 관리 (컨테이너) |
| server.FileSystem | 개별 파일시스템 마운트포인트 |
| server.NetworkInterfaces | 네트워크 인터페이스 (컨테이너) |
| server.NetworkInterface | 개별 네트워크 인터페이스 |
| server.Netstat | 네트워크 세션/연결 정보 |
| server.Process | 프로세스 관제 |
| server.ProcessMonitor | 프로세스 모니터 |
| server.LogMonitor | 로그 모니터 |
| server.Other | 기타 정보 (IPCS, OS Table 등) |
| server.Hbas | HBA 관리 (컨테이너) |
| server.Hba | 개별 HBA 어댑터 |
| server.HbaPort | HBA 포트 |

### 계층 구조 (PARENT_RESOURCE_ID)

```
Server (ID=873, hostname1 / ID=1092, hotname2)
├── monitor group (management.MonitorGroup)
│   ├── Log Monitor (server.LogMonitor)
│   ├── Syslog Monitor (server.LogMonitor)
│   ├── ntpd (server.ProcessMonitor)
│   └── ...
├── CPU (server.Cpus)
│   ├── Core1 (server.Cpu)
│   ├── Core2 (server.Cpu)
│   └── ...
├── 디스크 (server.Disks)
├── 파일시스템 (server.FileSystems)
│   ├── / (server.FileSystem)
│   ├── /fsutil (server.FileSystem)
│   ├── /boot (server.FileSystem)
│   └── ...
├── 메모리 (server.Memory)
│   ├── 기타 메모리 (server.OtherMemory)
│   └── 가상메모리 (server.VirtualMemory)
├── 네트워크 세션 (server.Netstat)
├── Network Interfaces (server.NetworkInterfaces)
│   ├── ens192 (server.NetworkInterface)
│   ├── ens224 (server.NetworkInterface)
│   └── ...
├── 기타정보 (server.Other)
├── 프로세스 (server.Process)
└── HBA (server.Hbas) [hotname2 only]
    ├── SN1200E2P.MYT9222M0L (server.Hba)
    │   ├── host9 (server.HbaPort)
    │   └── host10 (server.HbaPort)
    └── SN1200E2P.MYT9222M0H (server.Hba)
        ├── host7 (server.HbaPort)
        └── host8 (server.HbaPort)
```

---

## 2. CORE_CONFIG_PROP

에이전트/리소스 설정 정보를 키-값(EAV) 구조로 저장하는 테이블.

| # | Column | Type | Nullable | Default | Description |
|---|--------|------|----------|---------|-------------|
| 1 | DTYPE | VARCHAR(31) | NOT NULL | | 속성 유형 (SIMPLE) |
| 2 | ID | BIGINT | NOT NULL | IDENTITY | PK, 자동증가 |
| 3 | ERRORMESSAGE | VARCHAR(255) | NULL | | 에러 메시지 |
| 4 | NAME | VARCHAR(255) | NULL | | 설정 항목명 (EAV의 Attribute) |
| 5 | TIME_STAMP | BIGINT | NULL | | 타임스탬프 (epoch ms) |
| 6 | STRINGVALUE | CLOB | NULL | | 대용량 설정 값 (LOB) |
| 7 | IS_LOB | SMALLINT | NULL | | LOB 사용 여부 (0=SHORT 사용, 1=CLOB 사용) |
| 8 | STRINGVALUE_SHORT | VARCHAR(4000) | NULL | | 설정 값 (EAV의 Value) |
| 9 | CONFIGURATION_ID | BIGINT | NULL | | 소속 설정 그룹 ID (EAV의 Entity) |
| 10 | PARENT_LIST_ID | BIGINT | NULL | | 부모 리스트 ID |
| 11 | PARENT_MAP_ID | BIGINT | NULL | | 부모 맵 ID |
| 12 | PROPERTYDEFINITION_ID | BIGINT | NOT NULL | | 속성 정의 ID |

### EAV 패턴 설명

이 테이블은 **Entity-Attribute-Value** 패턴을 사용한다:

| EAV 요소 | 컬럼 | 설명 |
|----------|------|------|
| Entity | CONFIGURATION_ID | 설정 그룹 식별자 (리소스 ID에 매핑) |
| Attribute | NAME | 설정 항목명 (AgentID, Hostname, OSType 등) |
| Value | STRINGVALUE_SHORT / STRINGVALUE | 설정 값 (IS_LOB=0이면 SHORT, 1이면 CLOB) |

### NAME별 설정 항목

| NAME | 설명 | 값 예시 |
|------|------|---------|
| AgentID | 에이전트 식별자 | MA_hostname1_20191017131141 |
| AgentVersion | 에이전트 버전 | 7.6.26_6 |
| GMT | 타임존 | GMT+09:00 |
| Hostname | 호스트명 | hostname1 |
| IPaddress | IP 주소 | 10.0.0.11 |
| InstallPath | 에이전트 설치 경로 | /fsutil/polestar/agent/NNPAgent/MAgent/ |
| Model | 서버 모델 | VMware Virtual Platform, ProLiant DL380 Gen10 |
| OSParameter | OS 커널 파라미터 | sysctl 출력 (IS_LOB=1일 수 있음) |
| OSType | 운영체제 종류 | LINUX |
| OSVerson | OS 버전 | 3.10.0-957.el7.x86_64 |
| SerialNumber | 시리얼 번호 | VMware-abcd, HOST123456AB |
| Vendor | 제조사 | VMware, Inc. / HPE |

---

## 3. DDL (원본)

### CMM_RESOURCE

```sql
CREATE TABLE "POLESTAR"."CMM_RESOURCE"  (
    "DTYPE" VARCHAR(31 OCTETS) NOT NULL ,
    "ID" BIGINT NOT NULL GENERATED BY DEFAULT AS IDENTITY (
      START WITH +1
      INCREMENT BY +1
      MINVALUE +1
      MAXVALUE +9223372036854775807
      NO CYCLE
      CACHE 20
      NO ORDER ) ,
    "ACL_ID" BIGINT NOT NULL ,
    "ACL_MANAGER_GROUP_ID" BIGINT ,
    "ACL_MANAGER_ID" BIGINT ,
    "AVAIL_STATUS" INTEGER ,
    "CTIME" BIGINT ,
    "DTIME" BIGINT ,
    "DESCRIPTION" VARCHAR(4000 OCTETS) ,
    "GROUP_PATH" VARCHAR(4000 OCTETS) ,
    "HASCHILDREN" SMALLINT ,
    "HOSTNAME" VARCHAR(255 OCTETS) ,
    "ID_ANCESTRY" VARCHAR(2000 OCTETS) ,
    "IDENTIFIER" VARCHAR(4000 OCTETS) ,
    "IMPORTANCE_ID" INTEGER NOT NULL WITH DEFAULT 1 ,
    "IS_INHERIT_AVAIL_DEPEND" SMALLINT ,
    "IS_INHERIT_CUSTOM_CONF" SMALLINT ,
    "IS_INHERIT_MANAGER_ZONE" SMALLINT ,
    "INHERITSTATUS" SMALLINT NOT NULL ,
    "INVENTORYPOLLINGINTERVAL" INTEGER ,
    "INVISIBLE" SMALLINT NOT NULL ,
    "IPADDRESS" VARCHAR(255 OCTETS) ,
    "LC" INTEGER ,
    "LOCATION" VARCHAR(255 OCTETS) ,
    "LONGPOLLINGINTERVAL" INTEGER ,
    "MTIME" BIGINT ,
    "IS_MAINTENANCE" SMALLINT ,
    "MANAGER_ZONE" VARCHAR(255 OCTETS) ,
    "MESUREMENTPOLLINGINTERVAL" INTEGER ,
    "MODIFIEDBY" VARCHAR(255 OCTETS) ,
    "NAME" VARCHAR(900 OCTETS) NOT NULL ,
    "OPTLOCK" INTEGER ,
    "ORDER_NUM" INTEGER ,
    "PARENT_RESOURCE_ID" BIGINT ,
    "PLATFORM_RESOURCE_ID" BIGINT ,
    "POLLINGPOLICY" INTEGER ,
    "PRIORITY" INTEGER ,
    "RESOURCEICON" VARCHAR(255 OCTETS) ,
    "RESOURCE_KEY" VARCHAR(255 OCTETS) NOT NULL ,
    "RESOURCESTATUS" INTEGER ,
    "RESOURCE_TYPE" VARCHAR(255 OCTETS) NOT NULL ,
    "RESOURCETYPEVERSION" VARCHAR(255 OCTETS) ,
    "SERVICE_RESOURCE_ID" BIGINT ,
    "IS_SYNC_DESC" SMALLINT ,
    "IS_SYNC_NAME" SMALLINT ,
    "UUID" VARCHAR(255 OCTETS) ,
    "VERSION" VARCHAR(255 OCTETS) ,
    "SYSTEM" SMALLINT ,
    "AVAIL_DEPEND_RESOURCE_ID" BIGINT ,
    "AVAIL_DEPEND_RESOURCE_ID_2" BIGINT ,
    "CONNECTION_CONF_ID" BIGINT ,
    "CUSTOM_CONF_ID" BIGINT ,
    "REALTIME_INFO_ID" BIGINT ,
    "RESOURCE_CONF_ID" BIGINT ,
    "RESOURCE_PATH_ID" BIGINT ,
    "SCHEDULE_ID" BIGINT ,
    "RESOURCE_SYSTEM_ID" BIGINT ,
    "GROUP_RESOURCE_ID" BIGINT ,
    "BUSINESS_GROUP_RESOURCE_ID" BIGINT )
   COMPRESS YES ADAPTIVE
   IN "SSNISND01R" INDEX IN "SSNISND01X"
   ORGANIZE BY ROW;
```

### CORE_CONFIG_PROP

```sql
CREATE TABLE "POLESTAR"."CORE_CONFIG_PROP"  (
    "DTYPE" VARCHAR(31 OCTETS) NOT NULL ,
    "ID" BIGINT NOT NULL GENERATED BY DEFAULT AS IDENTITY (
      START WITH +1
      INCREMENT BY +1
      MINVALUE +1
      MAXVALUE +9223372036854775807
      NO CYCLE
      CACHE 20
      NO ORDER ) ,
    "ERRORMESSAGE" VARCHAR(255 OCTETS) ,
    "NAME" VARCHAR(255 OCTETS) ,
    "TIME_STAMP" BIGINT ,
    "STRINGVALUE" CLOB(1073741824 OCTETS) LOGGED NOT COMPACT ,
    "IS_LOB" SMALLINT ,
    "STRINGVALUE_SHORT" VARCHAR(4000 OCTETS) ,
    "CONFIGURATION_ID" BIGINT ,
    "PARENT_LIST_ID" BIGINT ,
    "PARENT_MAP_ID" BIGINT ,
    "PROPERTYDEFINITION_ID" BIGINT NOT NULL )
   COMPRESS YES ADAPTIVE
   IN "SSNISND01R" INDEX IN "SSNISND01X"
   ORGANIZE BY ROW;
```
