# K8s 파드 APM ↔ 서버(SMS) ↔ DB(DPM) 상관 연동 — 벤더별 접근 비교

(조사일 2026-11-03. 검색 도구 한계로 일부 문서는 검색 요약만 확인했다. 확인 못 한 항목은 Gaps에 명시.)

## 1. JENNIFER APM의 Kubernetes/컨테이너 지원 (agent env, 인스턴스 명명, Open API 노드 정보)

### Takeaway
JENNIFER는 5.6.1.1부터 APM-Kubernetes 모니터링(노드/컨테이너 리소스 + 앱 성능 동시 수집)을 공식 제공한다고 밝히지만, 공개 자료로는 파드→노드 매핑 식별자, 에이전트 환경변수, Open API의 노드/호스트 필드를 확인하지 못했다.

### Cited Findings
- JENNIFER 5.6.1.1에서 APM-Kubernetes 모니터링 기능 추가. 컨테이너에서 앱이 운영되면 K8s 인프라 정보와 앱 성능 정보를 함께 수집해 실시간 대시보드에 표시 — [JENNIFER Kubernetes](https://jennifersoft.com/en/blog/kubernetes/jennifer-kubenetes/)
- System Admin 대시보드: 트랜잭션을 처리하는 서비스의 컨테이너 리소스 상태 및 replica/deployment/service 등 워크로드 변경 표시. System Resource 대시보드: Node CPU, Container CPU, 메모리, 디스크, 네트워크 비교(컨테이너가 할당 자원을 쓰는지 확인) — 같은 페이지
- Node/Container Performance Metrics 뷰에서 노드·컨테이너 선택 시 상세, 이벤트, 메타데이터(YAML), 로그 이력 조회 — 같은 페이지
- 향후 "JENNIFER-K8s"(메인 대시보드, 워크로드 그룹별 CPU/메모리/네트워크/스토리지)가 예고됨 — 같은 페이지 (출시 여부 미확인)
- Docker 환경 JENNIFER 에이전트 설치는 jennifer.conf + setenv.sh JVM 인자 방식 (Tomcat 예시), PDF 본문은 직접 파싱 실패 — [Docker_JENNIFER.pdf](https://cdn.jennifersoft.com/wp-content/uploads/Documents/ko/Docker_JENNIFER.pdf) (검색 요약 기준)

### Inferences
- 대시보드가 "서비스의 컨테이너 ↔ 노드"를 보여주므로 JENNIFER 내부에는 인스턴스→컨테이너→노드 매핑이 존재할 가능성이 높다. 다만 이것이 Pod IP/노드명 필드로 외부(Open API)에 노출되는지는 별개 문제다.
- 사내 통합은 JENNIFER Open API에서 얻는 instance/agent 정보에 pod 이름(HOSTNAME)이 들어가도록 obj_name(인스턴스명) 규칙을 파드명 기반으로 두는 방식이 현실적 후보이나 미검증.

### Gaps
- 에이전트 옵션(obj_name 등)·환경변수·Downward API 연동 방법: docs.jennifersoft.com 본문을 확인하지 못함(검색에 걸리지 않음, 로그인/내부 문서 가능성).
- Open API 응답에 host/node/container 필드가 있는지 미확인. 운영 JENNIFER 매뉴얼 또는 벤더 확인 필요.
- JENNIFER 쪽 DB 연계(별도 DB 제품) 공개 자료 못 찾음.

## 2. 한국 벤더 (WhaTap, Exem, SMS 벤더)

### Takeaway
WhaTap은 Operator가 노드 에이전트(DaemonSet)와 APM 자동 주입을 모두 관리해 K8s 메타데이터로 앱↔노드를 묶는 구조다. Exem은 MaxGauge(DB)·InterMax(APM)·싸이옵스(통합 AIOps)·DataSaker(K8s 포함 풀스택)로 제품이 분리돼 있고 E2E 연계를 주장하나, 세션↔트랜잭션 연계 메커니즘과 K8s 연동 세부는 확인하지 못했다.

### Cited Findings
- WhaTap: Helm으로 whatap-operator 설치 시 whatap-master-agent, whatap-node-agent가 Pod로 추가 설치됨 — [WhaTap operator 설치](https://docs.whatap.io/kubernetes/install-whatap-operator)
- WhaTap APM 자동 계측: `WhatapAgent` CR의 `features.apm.instrumentation.targets`에서 language(java/python/nodejs), 에이전트 버전, `namespaceSelector`, `podSelector.matchLabels`로 대상 지정, Operator가 앱 Pod에 APM 에이전트를 자동 주입. K8s 에이전트 없이 APM 자동 설치 단독 사용 가능. Operator 방식은 Istio 사이드카 주입 미지원 — 같은 문서
- WhaTap 대상 매칭은 namespace·pod label 기준이며 pod/node 이름 기준이 아님. 주입 환경변수·노드 링크 상세는 「APM 자동 설치 구성」 페이지(/kubernetes/install-auto-application-agent)에 있다고 안내됨(미열람) — 같은 문서
- Exem: InterMax는 사용자 단말→WEB→WAS→TP→DB E2E 트랜잭션 추적 APM, 5.4에서 저장소 ClickHouse 전환 — [byline.network](https://byline.network/2024/02/240221_003/)
- Exem: 싸이옵스는 맥스게이지·인터맥스 기술 집대성 AIOps(2019 출시), 인터맥스와 연계하여 WEB/WAS/TP/DB 연계 추적·RCA 주장 — [newswire](https://www.newswire.co.kr/newsRead.php?no=975939), [newswire 974496](https://www.newswire.co.kr/newsRead.php?no=974496)
- Exem DataSaker(엑셈원): 인프라·DB·애플리케이션·쿠버네티스/도커/컨테이너 로그까지 통합 모니터링, 오라클·PostgreSQL 등 DB 지원 — [byline.network 엑셈원](https://byline.network/2024/04/240401_003/)

### Inferences
- WhaTap식 "에이전트를 Operator가 주입하고 같은 Operator가 노드 에이전트를 배포" 구조는 노드명·파드명이 동일 플랫폼 내 K8s API 메타로 결합되므로 파드↔노드 문제가 구조적으로 해소된다. 이종 제품 조합(JENNIFER+Polestar)에서는 불가능하므로 결합 키를 직접 만들어야 한다.

### Gaps
- Exem: MaxGauge 세션과 InterMax 트랜잭션의 실제 연계 키(GUID/TXID, client_info 등) 미확인. DataSaker의 K8s 노드-파드-DB 토폴로지 구현도 미확인.
- 브레인즈컴퍼니(Polestar) 등 국내 SMS의 K8s 지원: 검색하지 못함.
- WhaTap APM↔K8s 링크용 주입 env/label 상세 미열람.

## 3. 글로벌 벤더 토폴로지/식별자

### Takeaway
모든 벤더가 결국 "앱 telemetry에 pod/container/node/host 식별자를 리소스 속성으로 싣고, 인프라 에이전트가 같은 키의 엔티티를 보고해 조인"한다. 키는 host.id/host.name, container.id, k8s.pod.name/uid, k8s.node.name, cluster 명 등이다.

### Cited Findings
- Dynatrace: Smartscape는 `is_part_of`, `runs_on`, `monitors` 등 타입 있는 엣지 모델. 예) pod is_part_of deployment, OneAgent monitors host — [Smartscape semantic dictionary](https://docs.dynatrace.com/docs/semantic-dictionary/model/smartscape)
- Dynatrace classic full-stack: OneAgent를 DaemonSet(노드당 1 파드)으로 배포, 새 컨테이너를 감지해 code module 주입, 계측된 파드가 호스트 및 호스트 메트릭과의 관계 유지. Application-only 모드는 노드 호스트 메트릭 수집 안 됨 — [Classic Full-Stack](https://docs.dynatrace.com/managed/ingest-from/setup-on-k8s/how-it-works/other-deployment-modes/classic-fullstack), [how it works](https://docs.dynatrace.com/docs/shortlink/how-it-works-k8s)
- Datadog Unified Service Tagging: env/service/version 3 태그를 pod label(`tags.datadoghq.com/`) 또는 `DD_SERVICE` 등 env로 부여해 trace·metric·log 연결. `kube_cluster_name`은 호스트 태그로 부여(출처 서술이 문서별 상이) — [Datadog K8s tag](https://docs.datadoghq.com/containers/kubernetes/tag/?tab=containerizedagent)
- New Relic(OpenTelemetry 서비스): 호스트·컨테이너 관계 지원. K8s에서는 k8s.cluster.name, k8s.namespace.name, k8s.pod.name, k8s.container.name 리소스 속성 필요(Collector k8sattributes로 주입). 비 K8s 컨테이너는 container.id 일치, 호스트는 host.id가 infra agent의 호스트 식별과 일치해야 함 — [New Relic OTel best practices](https://docs.newrelic.com/docs/opentelemetry/best-practices/opentelemetry-best-practices-resources/)
- Elastic APM: 에이전트가 kubernetes.namespace, kubernetes.node.name, kubernetes.pod.name 메타데이터를 보고, host.name은 ECS 필드 — [APM span fields](https://www.elastic.co/guide/en/apm/server/current/exported-fields-apm-span.html). K8s 정보가 있으면 단순 hostname은 무시됨(Downward API로 pod 이름 공급) — [metadata api](https://www.elastic.co/guide/en/apm/server/7.9/metadata-api.html)
- OTel k8sattributes processor: 기본은 수신 연결 IP를 Pod IP에 연관, 프록시 시 k8s.pod.ip/host.name/k8s.pod.uid 등 resource_attribute 규칙으로 pod_association 지정 가능 — [processor docs](https://pkg.go.dev/github.com/open-telemetry/opentelemetry-collector-contrib/processor/k8sattributesprocessor), [Splunk](https://help.splunk.com/splunk-observability-cloud/manage-data/splunk-distribution-of-the-opentelemetry-collector/get-started-with-the-splunk-distribution-of-the-opentelemetry-collector/collector-components/processors/kubernetes-attributes-processor)

### Inferences
- 재사용 패턴: (a) 파드 기동 시 Downward API로 `spec.nodeName`, `status.hostIP`, `metadata.name`, `status.podIP`를 env로 주입; (b) 앱 에이전트 측 인스턴스 태그에 node/host 정보를 포함; (c) 중앙에서 pod_ip/pod_name→node→host(SMS 호스트 ID)로 조인하는 매핑 테이블(K8s API 기반) 유지.
- 노드명(k8s.node.name)과 SMS 호스트명/IP가 다르면 별도 alias 매핑(예: node InternalIP ↔ SMS 등록 IP)이 필요하다. 벤더들은 이를 host.id 일치 규칙으로 해결한다.

### Gaps
- Instana, Grafana(Beyla/Alloy), Splunk 고유 토폴로지 모델은 미조사.
- Dynatrace process group↔host↔pod 정확한 엣지 정의 미확인(요약 수준).

## 4. APM ↔ DB 상관 (SQL 코멘트, 세션 메타, NAT)

### Takeaway
DB 쪽에서 APM 트랜잭션과 연결하는 표준 기법은 (1) SQL 코멘트 주입(Datadog DBM, sqlcommenter traceparent), (2) 세션 메타 필드(Oracle MODULE/ACTION/CLIENT_IDENTIFIER, Db2 client info, PG application_name)이며, 소스 IP(파드/노드)는 NAT 때문에 신뢰 키가 아니다.

### Cited Findings
- Datadog `DD_DBM_PROPAGATION_MODE`: `full`(트레이스 정보 전송, 개별 트레이스 조사), `service`(서비스명만), `disabled`, `dynamic_service`(Oracle·Java 한정, SQL 코멘트 대신 V$SESSION.ACTION에 서비스 해시 기록, 기존 ACTION 값 덮어씀, Java tracer 1.67.0+ 및 `DD_DBM_PROPAGATION_ORACLE_ACTION_ONLY_ENABLED=true`) — [Datadog connect DBM and APM](https://docs.datadoghq.com/database_monitoring/connect_dbm_and_apm/)
- 지원 DB: Postgres, MySQL, SQL Server, Oracle, MongoDB. DB2는 언급 없음. SQL Server full은 `SET context_info`(기존 값 덮어씀, 왕복 추가). Oracle은 SQL Plan Management가 SQL 텍스트에 의존하므로 코멘트 회피 옵션 제공 — 같은 문서
- Datadog 연계 UI: Query Samples에 APM 배지, 샘플→트레이스 미리보기, 호스트 active connection을 호출 APM 서비스별로 분해, DB 호스트를 호출 서비스로 필터 — 같은 문서
- 코멘트로 서비스명 등이 DB에 저장되는 점이 기밀 노출 주의 사항 — 같은 문서
- sqlcommenter 계열 구현(Rust es-entity, Elixir 등)은 샘플링된 span의 W3C `traceparent`를 SQL 코멘트로 덧붙이며 DB 쿼리 로그에서 trace ID로 검색 — [es-entity](https://docs.rs/es-entity/latest/es_entity/sql_commenter/), [opentelemetry_sqlcommenter](https://hex.pm/packages/opentelemetry_sqlcommenter/0.1.1/files/lib/opentelemetry_sqlcommenter.ex) (제3자 블로그 수준 자료 포함, 공식 sqlcommenter 문서는 미열람)
- Oracle: MODULE/ACTION/CLIENT_INFO는 V$SESSION, 모듈·액션은 V$SQLAREA에도 노출; CLIENT_IDENTIFIER는 DBMS_SESSION으로 별도 설정; JDBC에서 PL/SQL 호출 없이 세팅 가능 — [Oracle DBMS_APPLICATION_INFO](https://docs.oracle.com/en/database/oracle/oracle-database/23/arpls/DBMS_APPLICATION_INFO.html), [oracle-base](https://oracle-base.com/articles/8i/dbms_application_info)
- Db2: JDBC `Connection.setClientInfo`로 clientUser/clientWorkstation 등 설정 후 SQL 실행 시 서버로 전송; 이 값은 WLM 평가, audit 레코드, application snapshot에 표시. 서버측 대안 `WLM_SET_CLIENT_INFO`(COMMIT/ROLLBACK 필요, 권한 필요) — [IBM JDBC client info](https://ibm.com/docs/en/SSEPEK_13.0.0/java/src/tpc/imjcc_t0052428.html), [WLM_SET_CLIENT_INFO](https://ibm.com/docs/SSEPGG_11.5.0/com.ibm.db2.luw.sql.rtn.doc/doc/r0053116.html)

### Inferences
- 은행형 환경(Db2 포함 가능): 코멘트 주입은 SQL 텍스트 변경(Plan/통계 영향, 승인 SQL 화이트리스트와 충돌 가능)이 있어 Db2에서는 clientWorkstation에 `노드명/파드명`, clientApplicationInformation에 서비스명/인스턴스명을 싣는 세션 메타 방식이 더 안전해 보인다(미검증).
- DB 세션 관점에서 소스 IP가 노드(SNAT)인지 파드인지는 CNI 설정에 좌우된다 — 이 부분 벤더 문서는 찾지 못했고 일반 지식 수준이다.

### Gaps
- Exem MaxGauge/InterMax 및 JENNIFER의 DB 세션 연계 방식, Dynatrace·New Relic의 DB 쪽 연계는 확인하지 못함.
- Oracle JDBC end-to-end metrics, 공식 google sqlcommenter 문서 미열람.
- PostgreSQL application_name 사례 문서 미확인.
