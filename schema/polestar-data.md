# POLESTAR DB Sample Data

> Source: `sample/CMM_RESOURCE(873.xlsx`, `sample/CORE_CONFIG_PROP(110.xlsx`
> 조회 기준: CMM_RESOURCE ID=873 하위 리소스, CORE_CONFIG_PROP CONFIGURATION_ID=110,176

---

## 1. CMM_RESOURCE (124행)

### 1.1 데이터 요약

| 항목 | 값 |
|------|-----|
| 총 행 수 | 124 |
| 호스트 | hostname1 (45행), hotname2 (79행) |
| DTYPE 분포 | ServiceResource(2), Resource(122) |
| RESOURCE_TYPE 종류 | 19종 |

### 1.2 RESOURCE_TYPE별 분포

| RESOURCE_TYPE | 수량 | 설명 |
|---------------|------|------|
| server.FileSystem | 47 | 개별 파일시스템 마운트포인트 |
| server.Cpu | 26 | 개별 CPU 코어 |
| server.LogMonitor | 9 | 로그 모니터 |
| server.ProcessMonitor | 7 | 프로세스 모니터 |
| server.NetworkInterface | 6 | 네트워크 인터페이스 |
| server.HbaPort | 4 | HBA 포트 |
| management.MonitorGroup | 2 | 모니터 그룹 |
| server.Cpus | 2 | CPU 컨테이너 |
| server.Disks | 2 | 디스크 컨테이너 |
| server.FileSystems | 2 | 파일시스템 컨테이너 |
| server.Memory | 2 | 메모리 |
| server.Netstat | 2 | 네트워크 세션 |
| server.NetworkInterfaces | 2 | 네트워크 인터페이스 컨테이너 |
| server.Other | 2 | 기타 정보 |
| server.Process | 2 | 프로세스 |
| server.OtherMemory | 2 | 기타 메모리 |
| server.VirtualMemory | 2 | 가상 메모리 |
| server.Hba | 2 | HBA 어댑터 |
| server.Hbas | 1 | HBA 컨테이너 |

### 1.3 전체 데이터

주요 컬럼만 표시. 전체 59컬럼 중 핵심 컬럼을 선별.

#### hostname1 (서버 ID=873)

| ID | DTYPE | NAME | RESOURCE_TYPE | AVAIL_STATUS | PARENT_ID | DESCRIPTION |
|----|-------|------|---------------|-------------|-----------|-------------|
| 874 | ServiceResource | monitor group | management.MonitorGroup | 0 | 873 | |
| 875 | Resource | Log Monitor | server.LogMonitor | 0 | 874 | |
| 919 | Resource | CPU | server.Cpus | 0 | 873 | CPU 관리 |
| 920 | Resource | 디스크 | server.Disks | 0 | 873 | 전체 디스크 |
| 921 | Resource | 파일시스템 | server.FileSystems | 0 | 873 | 파일시스템 관리 |
| 922 | Resource | 메모리 | server.Memory | 0 | 873 | 물리적 메모리 관리 |
| 923 | Resource | 네트워크 세션 | server.Netstat | 0 | 873 | 네트워크 연결 정보에 대한 관리 |
| 924 | Resource | Network Interfaces | server.NetworkInterfaces | 0 | 873 | Network Interface 관리 |
| 925 | Resource | 기타정보 | server.Other | 0 | 873 | IPCS, OS Table 등에 대한 정보 |
| 926 | Resource | 프로세스 | server.Process | 0 | 873 | 실행중인 프로세스 관제 |
| 1014 | Resource | / | server.FileSystem | 0 | 921 | |
| 1015 | Resource | /fsutil | server.FileSystem | 0 | 921 | |
| 1016 | Resource | 기타 메모리 | server.OtherMemory | 0 | 922 | 페이지, 문맥교환 횟수 등에 대한 정보 |
| 1017 | Resource | /fskmd | server.FileSystem | 0 | 921 | |
| 1018 | Resource | /fscm | server.FileSystem | 0 | 921 | |
| 1019 | Resource | 가상메모리 | server.VirtualMemory | 0 | 922 | |
| 1020 | Resource | /fsmf1 | server.FileSystem | 0 | 921 | |
| 1021 | Resource | /boot | server.FileSystem | 0 | 921 | |
| 1022 | Resource | /fsbackup | server.FileSystem | 0 | 921 | |
| 1023 | Resource | /fswas | server.FileSystem | 0 | 921 | |
| 1024 | Resource | /fsscp | server.FileSystem | 0 | 921 | |
| 1025 | Resource | /fsimg1 | server.FileSystem | 0 | 921 | |
| 1026 | Resource | /fsapp | server.FileSystem | 0 | 921 | |
| 1027 | Resource | /fsimg | server.FileSystem | 0 | 921 | |
| 1028 | Resource | /fsimg2 | server.FileSystem | 0 | 921 | |
| 1029 | Resource | /fswaslog | server.FileSystem | 0 | 921 | |
| 1030 | Resource | /fslog | server.FileSystem | 0 | 921 | |
| 1031 | Resource | /fsconfig | server.FileSystem | 0 | 921 | |
| 1032 | Resource | /fshome | server.FileSystem | 0 | 921 | |
| 1033 | Resource | /fsdump | server.FileSystem | 0 | 921 | |
| 1034 | Resource | /var/crash | server.FileSystem | 0 | 921 | |
| 1046 | Resource | Core1 | server.Cpu | 0 | 919 | |
| 1047 | Resource | ens224 | server.NetworkInterface | 0 | 924 | |
| 1048 | Resource | Core2 | server.Cpu | 0 | 919 | |
| 1049 | Resource | ens192 | server.NetworkInterface | 0 | 924 | |
| 21521 | Resource | ntpd | server.ProcessMonitor | 0 | 874 | ntpd |
| 23179 | Resource | Syslog Monitor | server.LogMonitor | 0 | 874 | |
| 32601 | Resource | /fsfile | server.FileSystem | 0 | 921 | |
| 32973 | Resource | /fsbatch | server.FileSystem | 0 | 921 | |
| 69268 | Resource | NC_NODEMANAGER | server.ProcessMonitor | 0 | 874 | [프로세스개수=1] |
| 69272 | Resource | NC_CMDEPLOY | server.ProcessMonitor | 0 | 874 | [프로세스개수=1] |
| 68796 | Resource | ntpd | server.ProcessMonitor | 1 | 874 | ntpd |
| 69545 | Resource | /fsmf | server.FileSystem | 0 | 921 | |
| 158216 | Resource | Secuve_TOS_Fail | server.LogMonitor | 0 | 874 | |
| 159779 | Resource | Deep_Security_Fail | server.LogMonitor | 0 | 874 | 트랜드 마이크로 백신 이상 로그 |

#### hotname2 (서버 ID=1092)

| ID | DTYPE | NAME | RESOURCE_TYPE | AVAIL_STATUS | PARENT_ID | DESCRIPTION |
|----|-------|------|---------------|-------------|-----------|-------------|
| 1116 | ServiceResource | monitor group | management.MonitorGroup | 0 | 1092 | |
| 1117 | Resource | Log Monitor | server.LogMonitor | 0 | 1116 | |
| 1209 | Resource | CPU | server.Cpus | 0 | 1092 | CPU 관리 |
| 1210 | Resource | 디스크 | server.Disks | 0 | 1092 | 전체 디스크 |
| 1211 | Resource | 파일시스템 | server.FileSystems | 0 | 1092 | 파일시스템 관리 |
| 1212 | Resource | 메모리 | server.Memory | 0 | 1092 | 물리적 메모리 관리 |
| 1213 | Resource | 네트워크 세션 | server.Netstat | 0 | 1092 | 네트워크 연결 정보에 대한 관리 |
| 1214 | Resource | Network Interfaces | server.NetworkInterfaces | 0 | 1092 | Network Interface 관리 |
| 1215 | Resource | 기타정보 | server.Other | 0 | 1092 | IPCS, OS Table 등에 대한 정보 |
| 1216 | Resource | 프로세스 | server.Process | 0 | 1092 | 실행중인 프로세스 관제 |
| 1324 | Resource | /FSDB2MON | server.FileSystem | 0 | 1211 | |
| 1325 | Resource | /fsutil | server.FileSystem | 0 | 1211 | |
| 1326 | Resource | 기타 메모리 | server.OtherMemory | 0 | 1212 | 페이지, 문맥교환 횟수 등에 대한 정보 |
| 1327 | Resource | /FSDB2INST1SHRD | server.FileSystem | 0 | 1211 | |
| 1328 | Resource | /FSSUUADAT0001 | server.FileSystem | 0 | 1211 | |
| 1329 | Resource | 가상메모리 | server.VirtualMemory | 0 | 1212 | |
| 1330 | Resource | /FSSUUATMP0001 | server.FileSystem | 0 | 1211 | |
| 1331 | Resource | /FSSUUADAT0002 | server.FileSystem | 0 | 1211 | |
| 1332 | Resource | / | server.FileSystem | 0 | 1211 | |
| 1333 | Resource | /FSSUUALOG0001 | server.FileSystem | 0 | 1211 | |
| 1334 | Resource | /FSSUUADAT0004 | server.FileSystem | 0 | 1211 | |
| 1335 | Resource | /FSSUUABAK0001 | server.FileSystem | 0 | 1211 | |
| 1336 | Resource | /opt/IBM/db2 | server.FileSystem | 0 | 1211 | |
| 1337 | Resource | /boot | server.FileSystem | 0 | 1211 | |
| 1338 | Resource | /fsudb | server.FileSystem | 0 | 1211 | |
| 1339 | Resource | /fshome | server.FileSystem | 0 | 1211 | |
| 1340 | Resource | /FSSUUAARC0001 | server.FileSystem | 0 | 1211 | |
| 1341 | Resource | /fslog | server.FileSystem | 0 | 1211 | |
| 1342 | Resource | /FSSUUA | server.FileSystem | 0 | 1211 | |
| 1343 | Resource | /FSSUUADAT0003 | server.FileSystem | 0 | 1211 | |
| 1344 | Resource | /boot/efi | server.FileSystem | 0 | 1211 | |
| 1345 | Resource | /FSSUUALOG0002 | server.FileSystem | 0 | 1211 | |
| 1346 | Resource | /FSDB2INST1DIAG | server.FileSystem | 0 | 1211 | |
| 1347 | Resource | /FSSUUATMP0002 | server.FileSystem | 0 | 1211 | |
| 1348 | Resource | /fsmig | server.FileSystem | 0 | 1211 | |
| 1349 | Resource | /var/crash | server.FileSystem | 0 | 1211 | |
| 1350 | Resource | Core1 | server.Cpu | 0 | 1209 | |
| 1351 | Resource | Core9 | server.Cpu | 0 | 1209 | |
| 1352 | Resource | Core14 | server.Cpu | 0 | 1209 | |
| 1353 | Resource | Core15 | server.Cpu | 0 | 1209 | |
| 1354 | Resource | Core3 | server.Cpu | 0 | 1209 | |
| 1355 | Resource | Core12 | server.Cpu | 0 | 1209 | |
| 1356 | Resource | Core17 | server.Cpu | 0 | 1209 | |
| 1357 | Resource | Core7 | server.Cpu | 0 | 1209 | |
| 1358 | Resource | Core4 | server.Cpu | 0 | 1209 | |
| 1359 | Resource | Core24 | server.Cpu | 0 | 1209 | |
| 1360 | Resource | Core22 | server.Cpu | 0 | 1209 | |
| 1362 | Resource | Core23 | server.Cpu | 0 | 1209 | |
| 1363 | Resource | Core5 | server.Cpu | 0 | 1209 | |
| 1365 | Resource | Core8 | server.Cpu | 0 | 1209 | |
| 1367 | Resource | Core11 | server.Cpu | 0 | 1209 | |
| 1369 | Resource | Core10 | server.Cpu | 0 | 1209 | |
| 1370 | Resource | Core20 | server.Cpu | 0 | 1209 | |
| 1372 | Resource | Core13 | server.Cpu | 0 | 1209 | |
| 1374 | Resource | Core21 | server.Cpu | 0 | 1209 | |
| 1376 | Resource | Core16 | server.Cpu | 0 | 1209 | |
| 1378 | Resource | Core6 | server.Cpu | 0 | 1209 | |
| 1379 | Resource | Core2 | server.Cpu | 0 | 1209 | |
| 1380 | Resource | Core18 | server.Cpu | 0 | 1209 | |
| 1381 | Resource | Core19 | server.Cpu | 0 | 1209 | |
| 1385 | Resource | ens5f0 | server.NetworkInterface | 0 | 1214 | |
| 1386 | Resource | bond0 | server.NetworkInterface | 0 | 1214 | |
| 1387 | Resource | ens2f0 | server.NetworkInterface | 0 | 1214 | |
| 21528 | Resource | ntpd | server.ProcessMonitor | 0 | 1116 | ntpd |
| 23136 | Resource | DB2진단로그 | server.LogMonitor | 0 | 1116 | |
| 23186 | Resource | Syslog Monitor | server.LogMonitor | 0 | 1116 | |
| 32088 | Resource | DB2 엔진 | server.ProcessMonitor | 0 | 1116 | |
| 68573 | Resource | eno1 | server.NetworkInterface | 0 | 1214 | |
| 68750 | Resource | ntpd | server.ProcessMonitor | 1 | 1116 | ntpd |
| 158623 | Resource | Secuve_TOS_Fail | server.LogMonitor | 0 | 1116 | |
| 159800 | Resource | Deep_Security_Fail | server.LogMonitor | 0 | 1116 | 트랜드 마이크로 백신 이상 로그 |
| 168646 | Resource | /fssli | server.FileSystem | 0 | 1211 | |
| 264253 | Resource | HBA | server.Hbas | 0 | 1092 | HBA |
| 264254 | Resource | SN1200E2P.MYT9222M0L | server.Hba | 0 | 264253 | |
| 264255 | Resource | host9 | server.HbaPort | 0 | 264254 | |
| 264256 | Resource | host10 | server.HbaPort | 1 | 264254 | |
| 264257 | Resource | SN1200E2P.MYT9222M0H | server.Hba | 0 | 264253 | |
| 264258 | Resource | host7 | server.HbaPort | 0 | 264257 | |
| 264259 | Resource | host8 | server.HbaPort | 1 | 264257 | |

---

## 2. CORE_CONFIG_PROP (24행)

### 2.1 데이터 요약

| 항목 | 값 |
|------|-----|
| 총 행 수 | 24 |
| CONFIGURATION_ID 수 | 2 (110=hostname1, 176=hostname2) |
| NAME 종류 | 12종 (각 CONFIGURATION_ID마다 동일 항목 12개) |
| DTYPE | 모두 SIMPLE |

### 2.2 CONFIGURATION_ID=110 (hostname1)

| ID | NAME | STRINGVALUE_SHORT | IS_LOB | PROPERTYDEFINITION_ID |
|----|------|-------------------|--------|----------------------|
| 2196 | AgentID | MA_hostname1_20191017131141 | 0 | 464 |
| 2194 | AgentVersion | 7.6.26_6 | 0 | 462 |
| 2190 | GMT | GMT+09:00 | 0 | 458 |
| 2192 | Hostname | hostname1 | 0 | 460 |
| 2193 | IPaddress | 10.0.0.11 | 0 | 461 |
| 2195 | InstallPath | /fsutil/polestar/agent/NNPAgent/MAgent/ | 0 | 463 |
| 2187 | Model | VMware Virtual Platform | 0 | 455 |
| 2197 | OSParameter | *(sysctl 커널 파라미터 출력, 약 40행)* | 0 | 465 |
| 2188 | OSType | LINUX | 0 | 456 |
| 2189 | OSVerson | 3.10.0-957.el7.x86_64 | 0 | 457 |
| 2191 | SerialNumber | VMware-abcd | 0 | 459 |
| 2186 | Vendor | VMware, Inc. | 0 | 454 |

### 2.3 CONFIGURATION_ID=176 (hostname2)

| ID | NAME | STRINGVALUE_SHORT | IS_LOB | PROPERTYDEFINITION_ID |
|----|------|-------------------|--------|----------------------|
| 2866 | AgentID | MA_hostname2_20191106101140 | 0 | 464 |
| 2864 | AgentVersion | 7.6.26_6 | 0 | 462 |
| 2860 | GMT | GMT+09:00 | 0 | 458 |
| 2862 | Hostname | hostname2 | 0 | 460 |
| 2863 | IPaddress | 10.0.0.12 | 0 | 461 |
| 2865 | InstallPath | /fsutil/polestar/agent/NNPAgent/MAgent/ | 0 | 463 |
| 2857 | Model | ProLiant DL380 Gen10 | 0 | 455 |
| 2867 | OSParameter | *(sysctl 커널 파라미터 출력, 약 60행)* | 1 | 465 |
| 2858 | OSType | LINUX | 0 | 456 |
| 2859 | OSVerson | 3.10.0-957.el7.x86_64 | 0 | 457 |
| 2861 | SerialNumber | HOST123456AB | 0 | 459 |
| 2856 | Vendor | HPE | 0 | 454 |

### 2.4 서버별 비교

| 속성 | hostname1 (CONF_ID=110) | hostname2 (CONF_ID=176) |
|------|------------------------|------------------------|
| AgentID | MA_hostname1_20191017131141 | MA_hostname2_20191106101140 |
| AgentVersion | 7.6.26_6 | 7.6.26_6 |
| GMT | GMT+09:00 | GMT+09:00 |
| IPaddress | 10.0.0.11 | 10.0.0.12 |
| Model | VMware Virtual Platform | ProLiant DL380 Gen10 |
| OSType | LINUX | LINUX |
| OSVerson | 3.10.0-957.el7.x86_64 | 3.10.0-957.el7.x86_64 |
| Vendor | VMware, Inc. | HPE |
| SerialNumber | VMware-abcd | HOST123456AB |
| CPU 코어 수 | 2 (Core1~2) | 24 (Core1~24) |
| 파일시스템 수 | 21 | 26 |
| 네트워크 I/F | 2 (ens192, ens224) | 4 (ens5f0, bond0, ens2f0, eno1) |
| HBA | 없음 | 2 어댑터, 4 포트 |

---

## 3. 테이블 간 관계

```
CMM_RESOURCE (ID=873, hostname1)
    ├── CORE_CONFIG_PROP (CONFIGURATION_ID=110)
    │   └── 12개 설정 항목 (AgentID, Hostname, IPaddress, OSType, ...)
    └── 하위 리소스 45개 (CPU, 메모리, 디스크, 파일시스템, 네트워크, ...)

CMM_RESOURCE (ID=1092, hotname2)
    ├── CORE_CONFIG_PROP (CONFIGURATION_ID=176)
    │   └── 12개 설정 항목
    └── 하위 리소스 79개
```

> **참고**: CMM_RESOURCE.ID와 CORE_CONFIG_PROP.CONFIGURATION_ID의 직접적 FK 관계는 DDL에 명시되어 있지 않으나, 데이터상 서버 리소스 ID(873, 1092)에 가까운 CONFIGURATION_ID(110, 176)가 매핑되는 것으로 보아, 별도의 매핑 테이블 또는 RESOURCE_CONF_ID를 통한 간접 참조 관계로 추정된다.
