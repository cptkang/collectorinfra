# Pod → Node → Host 매핑 메커니즘 카탈로그 (K8s 파드 텔레메트리 ↔ 서버 모니터링 조인)

> 작성일 2026-11-03. 페이지 일부는 fetch 요약 도구가 잘라낸 부분이 있어, 해당 항목은 "미확인"으로 표시했다. 제니퍼(JENNIFER) 에이전트 고유 동작, ServiceNow의 node→Linux server CI 연결 방식은 신뢰할 출처를 찾지 못했다(Gaps 참조).

## 1. Downward API로 APM 에이전트/앱에 노드 정체성 주입

### Takeaway
Downward API는 `spec.nodeName`, `status.hostIP(s)`, `status.podIP(s)`, `metadata.name/namespace/uid`를 env var로 노출한다. 에이전트(또는 JVM 시스템 프로퍼티·OTel resource attribute)가 이를 읽어 트랜잭션에 node 태그를 붙이는 것이 가장 결정적(deterministic)이고 시간 이력 문제가 없는 방법이다.

### Cited Findings
- env var·volume 파일 양쪽 가능: `metadata.name`, `metadata.namespace`, `metadata.uid`, `metadata.annotations['<KEY>']`, `metadata.labels['<KEY>']` — [K8s Downward API](https://kubernetes.io/docs/concepts/workloads/pods/downward-api/)
- env var 전용: `spec.serviceAccountName`, `spec.nodeName`(파드가 실행 중인 노드), `status.hostIP`(노드의 primary IP), `status.hostIPs`(dual-stack, 첫 항목 = hostIP), `status.podIP`, `status.podIPs` — [K8s Downward API](https://kubernetes.io/docs/concepts/workloads/pods/downward-api/)
- volume 파일 전용: `metadata.labels`, `metadata.annotations` 전체 — 위와 동일
- 공식 예제의 fieldRef 경로: `spec.nodeName`, `metadata.name`, `metadata.namespace`, `status.podIP` — [Expose Pod Information as Env Vars](https://kubernetes.io/docs/tasks/inject-data-application/environment-variable-expose-pod-information/)
- 설정 예(공식 문서 예제 발췌·확장):
```yaml
env:
- name: NODE_NAME   # 호스트 조인 키 후보 1
  valueFrom: { fieldRef: { fieldPath: spec.nodeName } }
- name: HOST_IP     # 호스트 조인 키 후보 2 (SMS가 IP 키일 때)
  valueFrom: { fieldRef: { fieldPath: status.hostIP } }
- name: POD_IP
  valueFrom: { fieldRef: { fieldPath: status.podIP } }
- name: POD_NAME
  valueFrom: { fieldRef: { fieldPath: metadata.name } }
- name: POD_NAMESPACE
  valueFrom: { fieldRef: { fieldPath: metadata.namespace } }
- name: POD_UID
  valueFrom: { fieldRef: { fieldPath: metadata.uid } }
- name: OTEL_RESOURCE_ATTRIBUTES   # OTel SDK/에이전트 사용 시 (env 참조는 $(VAR) 문법, VAR가 먼저 정의돼 있어야 함)
  value: k8s.node.name=$(NODE_NAME),k8s.pod.name=$(POD_NAME),k8s.pod.uid=$(POD_UID),k8s.namespace.name=$(POD_NAMESPACE)
```
- OTel k8sattributes README도 sidecar 상황(같은 파드 내 컨테이너 구분 불가)에서 downward API 사용을 권고하고, DaemonSet 에이전트의 `filter.node_from_env_var`용 노드명 주입에 downward API를 쓴다 — [k8sattributesprocessor README](https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector-contrib/main/processor/k8sattributesprocessor/README.md)
- hostNetwork: k8sattributes README는 "host networking 파드는 IP로 식별 불가, 비-IP 연관 규칙만 가능"이라 명시 — 위 README. (kube_pod_info에도 `host_network` 라벨 존재 — [KSM pod metrics](https://raw.githubusercontent.com/kubernetes/kube-state-metrics/main/docs/metrics/workload/pod-metrics.md))

### Inferences
- hostNetwork: true이면 podIP == hostIP가 되어(일반 K8s 동작, 이번 조사에서 공식 문서로 직접 확인하지는 못함) 파드 IP 기반 조인은 곧 호스트 IP 조인이 된다. 오히려 SMS 조인은 쉬워지지만 같은 노드의 파드끼리 구분 불가.
- 제니퍼 에이전트가 env var/시스템 프로퍼티로 임의 태그를 트랜잭션에 붙일 수 있는지에 따라 이 방법 가능 여부가 갈린다(미확인). 불가하면 에이전트의 host 이름 설정(hostname override 류)에 `NODE_NAME`을 넣는 우회가 후보.

### Gaps
- `status.hostIPs` 등의 도입 버전(공식 문서에 버전 표기 없음, 기억상 v1.28 부근이나 미확인).
- 제니퍼 에이전트의 K8s/Downward API 지원 여부 — 공개 자료 검색에서 못 찾음(검색 결과가 Elastic/Datadog만 반환).

## 2. Kubernetes API/Watch 기반 매핑 (kube-state-metrics, Prometheus 조인)

### Takeaway
kube-state-metrics(KSM)의 `kube_pod_info`가 pod_ip·host_ip·node·uid를 한 시계열에 담고 있어, Prometheus에서 현재 및 과거(보존 기간 내) pod→node 매핑 테이블로 쓸 수 있다.

### Cited Findings
- `kube_pod_info` (STABLE) 라벨: `pod, namespace, host_ip, pod_ip, node, created_by_kind, created_by_name, uid, priority_class, host_network` — [KSM pod-metrics](https://raw.githubusercontent.com/kubernetes/kube-state-metrics/main/docs/metrics/workload/pod-metrics.md)
- `kube_pod_container_info` (STABLE) 라벨: `container, pod, namespace, image, image_id, image_spec, container_id, uid` — 위와 동일 (container_id 포맷은 문서에 미기재; 보통 `containerd://<64hex>`)
- `kube_pod_status_phase/ready/scheduled` 등에도 `uid` 라벨 → uid로 재사용 IP 구분 가능 — 위와 동일
- `kube_node_info` (STABLE) 라벨: `node, kernel_version, os_image, container_runtime_version, kubelet_version, pod_cidr, provider_id, system_uuid, internal_ip` — [KSM node-metrics](https://raw.githubusercontent.com/kubernetes/kube-state-metrics/main/docs/metrics/cluster/node-metrics.md)
- PromQL 조인 예(일반 패턴, 라벨 이름은 위 문서 기준):
```promql
# 파드 메트릭에 node/host_ip 부여
container_memory_working_set_bytes{container!=""}
  * on(namespace,pod) group_left(node,host_ip,pod_ip) kube_pod_info
# 노드 → system_uuid/provider_id/internal_ip
kube_pod_info * on(node) group_left(system_uuid,provider_id,internal_ip) kube_node_info
```
- 조인 키로 `pod`+`namespace` 대신 `uid`를 쓰면 같은 이름 재생성(StatefulSet) 파드를 구분 가능 — (KSM 라벨 목록에서 uid 존재 사실에 근거한 추론)

### Inferences
- `kube_pod_info{pod_ip=...}`를 시간 범위 쿼리하면 "그 시각에 해당 IP를 가진 파드" 이력이 된다(Prometheus 보존기간/스크레이프 간격만큼의 해상도). 스크레이프 간격보다 짧게 산 파드는 누락될 수 있다.
- 장기 보존이 필요하면 Prometheus 외부(RDB)로 주기 스냅샷 적재 필요.

### Gaps
- metrics-server, cAdvisor/kubelet 라벨(`container_*`의 `id`, `pod`, `namespace`)에 대한 1차 문서는 이번에 fetch하지 못함. (cAdvisor 메트릭은 `instance`=kubelet 주소가 사실상 노드 식별자라는 점은 일반 지식이며 미검증)

## 3. OpenTelemetry (k8sattributes, resourcedetection, semconv)

### Takeaway
k8sattributes 프로세서가 파드 IP/컨테이너 ID/리소스 속성으로 파드를 찾아 `k8s.node.name`, `k8s.pod.uid` 등을 텔레메트리에 붙이는 표준 방식이다. resourcedetection(system 등)은 `host.name`·`host.id`를 붙인다. 앱 텔레메트리의 `k8s.node.name`과 호스트 메트릭의 `host.name`/`host.id` 사이의 최종 조인은 별도 설계가 필요하다.

### Cited Findings
- 연관(association) 규칙: 순서대로 평가, 첫 매칭 승리, 규칙당 최대 4 source 모두 일치. source 유형 `connection`(수신 연결 IP; batch/tail sampling 앞에 위치해야 함), `resource_attribute`(name 지정). 규칙 미설정 시 connection IP만 사용 — [k8sattributes README](https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector-contrib/main/processor/k8sattributesprocessor/README.md)
- 기본 추출 메타데이터: `k8s.namespace.name, k8s.pod.name, k8s.pod.uid, k8s.pod.start_time, k8s.deployment.name, k8s.node.name`. 선택: `k8s.pod.ip, k8s.pod.hostname, k8s.node.uid, k8s.cluster.uid`, 워크로드 UID 등. `container.id`는 기본 미포함, metadata에 명시해야 함. **노드 IP 속성은 목록에 없음** — 위와 동일
- RBAC: pods/namespaces get·watch·list 필요, `k8s.node.uid`에는 nodes 권한 필요. namespace 스코프 Role만 쓰면 node 객체 접근 불가 — 위와 동일
- 배포: Agent(DaemonSet)는 `filter.node_from_env_var`(downward API로 노드명 주입)로 로컬 노드 파드만 watch. Gateway 구성에서는 에이전트가 `passthrough: true`로 pod IP만 붙이고 gateway가 `k8s.pod.ip`를 resource_attribute source로 연관 — 위와 동일
- 한계: hostNetwork 파드는 IP 식별 불가; IP 재사용 문제는 README에서 다루지 않음; 비필터 사용 시 메모리 증가 — 위와 동일
- 설정 예:
```yaml
processors:
  k8s_attributes:
    filter: { node_from_env_var: KUBE_NODE_NAME }
    extract: { metadata: [k8s.namespace.name,k8s.pod.name,k8s.pod.uid,k8s.node.name,container.id] }
    pod_association:
      - sources: [{ from: resource_attribute, name: container.id }]
      - sources: [{ from: resource_attribute, name: k8s.pod.ip }]
      - sources: [{ from: connection }]
```
(위는 README 예제들을 조합한 것으로, 조합 자체의 동작은 직접 검증하지 않음)
- semconv: `k8s.node.name`(예 node-1), `k8s.node.uid`, `k8s.pod.uid`는 Recommended; `k8s.pod.ip`는 Opt-In(PodStatus.podIP에 대응) — [OTel semconv K8s](https://opentelemetry.io/docs/specs/semconv/resource/k8s/)
- `container.id`: Stable·Recommended, "보통 UUID, Docker 컨테이너 식별에 사용", 축약 가능(예 `a3bf90e006b2`). 획득 방법은 해당 페이지에 없음 — [OTel semconv container](https://opentelemetry.io/docs/specs/semconv/resource/container/)
- `host.id`: 클라우드 호스트는 인스턴스 ID, 비컨테이너 시스템은 machine ID. SDK 감지기는 Linux `/etc/machine-id`(폴백 `/var/lib/dbus/machine-id`)를 사용해야 하며, `dmidecode`·`/sys/devices/virtual/dmi/id/` 같은 권한 필요 소스는 SDK에서 금지, 필요 시 `OTEL_RESOURCE_ATTRIBUTES`로 주입. `host.name`은 hostname/FQDN. K8s 전용 지침은 없음 — [OTel semconv host](https://opentelemetry.io/docs/specs/semconv/resource/host/)
- resourcedetection: 기본 detectors `[env]`; `system`(host.name = FQDN 우선, 폴백 OS hostname, `hostname_sources` 기본 `["dns","os"]`), `k8s_api`(구 `k8snode`, deprecated; API 서버에서 노드·클러스터 속성 조회), `kubeadm`, `docker`, 클라우드별 `ec2/eks/gcp/azure/...`, `openshift` 등 — [resourcedetection README](https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector-contrib/main/processor/resourcedetectionprocessor/README.md)
- Datadog 문서(벤더 사례): OTel Collector DaemonSet에서 resourcedetection(cloud+system) + k8sattributes 조합으로 hostname/host tags를 구성, `host.name`/`host.id`는 resourcedetection의 system/클라우드 감지기에서, `k8s.node.name`은 k8sattributes에서 옴 — [Datadog hostname tagging](https://docs.datadoghq.com/opentelemetry/config/hostname_tagging/?tab=host)

### Inferences
- 파드 안 앱의 `host.name`은 파드 hostname(=파드명)이 되므로 호스트 조인에는 쓸 수 없다. 조인은 `k8s.node.name`(앱) ↔ 노드의 호스트 식별자(별도 매핑)로 해야 한다.
- 호스트 쪽(SMS)이 IP 키라면 K8s Node의 `status.addresses[InternalIP]`/KSM `internal_ip`를 매핑 테이블에 포함시켜야 한다 (k8sattributes는 노드 IP를 붙이지 않음).
- 노드 호스트(OS)에서 도는 OTel 호스트 에이전트의 `host.id`는 /etc/machine-id, K8s `nodeInfo.machineID`도 /etc/machine-id에서 유래(후자는 일반 지식; 아래 5절 참조) → 둘을 조인 키로 쓸 수 있다는 가설.

### Gaps
- "OTel이 infra·app 신호 상관을 어떻게 권고하는가"를 직접 서술한 공식 문서는 못 찾음(검색 결과가 Datadog 문서뿐). 위 설명은 컴포넌트 README에서 도출한 것.
- semconv의 `k8s.pod.ip` 외 `host.ip`, `cloud.*` 세부는 이번에 확인하지 않음.

## 4. 이력(시간 버전) 매핑: IP 재사용 대응

### Takeaway
공식적으로 "시점별 IP→파드" 이력을 제공하는 K8s 기능은 찾지 못했다. 직접 Watch/KSM 시계열로 이벤트소싱 테이블(ip, pod_uid, node, valid_from, valid_to)을 만들어야 한다.

### Cited Findings
- 파드 삭제 시 IP는 미사용 풀로 반환되어 재사용된다 — [Source Allies](https://www.sourceallies.com/2024/03/hands-on-kubernetes-part-3) (튜토리얼 블로그, 2차 자료)
- 실제 이슈 사례: 실패(failed) 상태 파드의 IP가 새 파드에 재사용되었는데 트래픽이 옛 파드로 가는 문제(1.22부터 evicted 파드 status에 IP가 남음) — [kubernetes/kubernetes#109414](https://issue.k8s.io/109414)
- GKE persistent pod IP 기능은 특정 파드에 안정 IP를 부여(기본은 재시작·이동 시 IP 변경) — [GKE docs](https://cloud.google.com/kubernetes-engine/docs/concepts/about-persistent-ip-addresses-for-gke-pods)
- 검색에서 K8s 내장 IP→파드 이력 기능은 확인되지 않았고 로그/CNI/관측 도구에 의존해야 한다는 정황만 확인됨 (위 검색 결과 요약 기반)
- KSM `kube_pod_info`의 `pod_ip`+`uid` 시계열을 이력 소스로 사용 가능 (2절)

### Inferences
- 설계안: K8s API Watch(pods, nodes)를 구독하는 소형 수집기가 `(pod_ip, pod_uid, pod_name, ns, node_name, host_ip, container_ids, start/end 시각)`을 RDB에 append. 조인은 `event_time BETWEEN valid_from AND COALESCE(valid_to, now)`. 삭제 이벤트/`deletionTimestamp`/phase 전이(Succeeded/Failed)를 종료 시각으로 사용.
- 스크레이프 기반(KSM)은 간격보다 짧은 수명 파드(Job)를 놓친다 → Watch 이벤트 소싱이 더 정확.
- APM 알람에 pod_uid/node가 직접 태깅되어 있으면(1절) 이력 테이블 자체가 불필요해진다.

### Gaps
- K8s audit log/Events를 이력 소스로 쓰는 문서화된 사례, Device42·ServiceNow Discovery의 시점 이력 방식: 신뢰할 출처 못 찾음. ServiceNow 검색에서는 Service Mapping의 K8s discovery와 Pacemaker/VCS 클러스터 노드→`cmdb_ci_linux_server` "Hosted on" 관계만 확인되었으며(예: [ServiceNow Pacemaker discovery](https://www.servicenow.com/docs/r/u7nZu9GEUxWr9UkuDmbe6w/XgCYwo4YuyrAKc8kqjSrdA)), K8s node→서버 CI 연결은 문서로 확인 불가.

## 5. Node ↔ 물리/VM 호스트 매핑

### Takeaway
노드 객체의 `status.addresses`(Hostname/InternalIP/ExternalIP), `status.nodeInfo`(machineID, systemUUID), `spec.providerID`가 CMDB/하이퍼바이저와 맞출 식별자들이다. SMS가 IP/hostname 키이면 InternalIP·Hostname 일치가 가장 간단하다.

### Cited Findings
- 노드 addresses: `HostName`(커널 보고값; kubelet `--hostname-override`로 변경 가능), `InternalIP`, `ExternalIP`. 사용 방식은 클라우드/베어메탈에 따라 다름 — [Node Status](https://kubernetes.io/docs/reference/node/node-status/)
- KSM `kube_node_info`에 `provider_id`, `system_uuid`, `internal_ip` 노출 — [KSM node-metrics](https://raw.githubusercontent.com/kubernetes/kube-state-metrics/main/docs/metrics/cluster/node-metrics.md)
- OTel `host.id`는 비컨테이너 시스템에서 machine ID(/etc/machine-id) — [semconv host](https://opentelemetry.io/docs/specs/semconv/resource/host/)
- resourcedetection `k8s_api` 감지기로 API 서버에서 노드 속성 조회 가능 — [resourcedetection README](https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector-contrib/main/processor/resourcedetectionprocessor/README.md)

### Inferences (일반 지식, 이번 조사에서 1차 출처로 확인 못 함)
- `nodeInfo.machineID` = /etc/machine-id, `nodeInfo.systemUUID` = SMBIOS/DMI UUID (VMware라면 VM의 BIOS UUID와 대응해 vCenter/CMDB 매칭에 유용), `spec.providerID` = `<provider>://<id>`(클라우드 CCM이 설정; 온프렘 kubeadm은 비어 있는 경우가 많음).
- 온프렘 베어메탈/VM에서는 providerID가 비는 경우가 흔하므로 실무 키는 (a) node name = OS hostname 일치, (b) InternalIP = SMS 등록 IP, (c) systemUUID = VMware UUID/CMDB.

### Gaps
- nodeInfo 필드 정의와 providerID에 대한 1차 문서 확인 실패(fetch가 해당 부분을 반환하지 않음). 확인하려면 https://kubernetes.io/docs/reference/kubernetes-api/cluster-resources/node-v1/ 를 직접 열어볼 것.

## 6. 컨테이너 ID / cgroup 기반 호스트 측 매핑

### Takeaway
호스트 에이전트는 `/proc/<pid>/cgroup`에서 컨테이너 ID(와 pod UID)를 추출한 뒤 CRI(`crictl`)/kubelet 또는 K8s API로 파드명을 해석한다. cgroup 드라이버(cgroupfs vs systemd)에 따라 경로 형식이 다르다.

### Cited Findings
- cgroupfs 드라이버: `/kubepods/burstable/pod<uid>/<container-id>`; systemd 드라이버: `kubepods-...slice:cri-containerd:<container_id>` 형태. kubelet과 런타임의 cgroup 드라이버가 일치해야 한다 — [웹 검색 결과 요약](https://dev.to/aws-builders/from-which-kubernetes-pod-and-namespace-is-this-process-that-i-see-on-my-host-5c0n), [icloudnative](https://icloudnative.io/posts/find-kubernetes-pod-info-from-process-id/) (블로그·커뮤니티 자료; 이 두 페이지 전문은 직접 fetch하지 않고 검색 스니펫 요약에 의존)
- pod UID는 /proc/<pid>/mountinfo의 `/etc/hosts` 바인드마운트 경로, 또는 systemd slice 이름(대시가 밑줄로 바뀜)에서 얻을 수 있다 — 위 출처
- 컨테이너 ID → `crictl inspect`의 라벨 `io.kubernetes.pod.name` 등으로 파드명 해석 — 위 출처
- 컨테이너 내부의 `/proc/self/cgroup`은 호스트와 다른 계층을 보여준다 — 위 출처(Gurubase 요약 포함)
- KSM `kube_pod_container_info.container_id`와 OTel k8sattributes의 `container.id` 연관(`from: resource_attribute, name: container.id`)이 같은 키로 서로 맞물린다 — [KSM](https://raw.githubusercontent.com/kubernetes/kube-state-metrics/main/docs/metrics/workload/pod-metrics.md), [k8sattributes](https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector-contrib/main/processor/k8sattributesprocessor/README.md)

### Inferences
- SMS 에이전트가 호스트에서 프로세스(java)를 관측하면 cgroup→container_id→(KSM container_info)→pod/ns 로 조인할 수 있다. 호스트 PID ↔ APM 에이전트 PID(제니퍼가 PID를 보낸다면)로도 연결 가능(미확인).
- 컨테이너 ID 형식 접두사(`containerd://`, `cri-o://`, `docker://`)와 64자/12자 축약 차이를 정규화해야 한다.

### Gaps
- 위 cgroup 파싱 규칙은 비공식 블로그 기반이므로, 실제 노드(cgroup v1/v2, 런타임)에서 검증 필요.

## 7. 종합 권고 (참고용 추론)
1. 1순위: Downward API로 `NODE_NAME`/`HOST_IP`/`POD_UID`를 제니퍼 에이전트 태그 또는 JVM 프로퍼티로 주입 → APM 알람에 직접 노드 키 부착(이력 불필요).
2. 2순위(태깅 불가한 구간, 예: DPM의 클라이언트 IP): K8s Watch 기반 시간버전 `pod_ip→pod_uid→node` 테이블 + `node→SMS 호스트` 정적 매핑(InternalIP/Hostname/systemUUID).
3. hostNetwork 파드는 IP가 노드 IP와 같아 파드 구분 불가 — 포트·프로세스 등 별도 키 필요.
4. DB 클라이언트 IP가 NAT/SNAT(노드 IP로 변환)되는 환경인지 CNI 설정 확인이 선행 필요(이번 조사 범위 밖, 미검증).
