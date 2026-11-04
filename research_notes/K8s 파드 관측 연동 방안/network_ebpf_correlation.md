# K8s 파드 IP → 노드/호스트 해석, 네트워크·eBPF 기반 app ↔ host ↔ DB 상관분석

## Q1. CNI/SNAT 설정별로 외부 DB가 보는 출발지 IP (파드 IP vs 노드 IP vs egress IP)

### Takeaway
기본값은 대부분 "클러스터 밖(비-네이티브-라우팅) 목적지 → 노드 IP로 SNAT"이다. 파드 IP가 DB(DPM)에 그대로 보이는 경우는 (a) 목적지 CIDR이 non-masquerade/네이티브 라우팅 대상일 때, (b) natOutgoing/masquerade를 끈 BGP·언더레이 라우팅일 때뿐이다. 고정 egress IP(Cilium Egress Gateway, OpenShift EgressIP, Calico egress gateway)를 쓰면 DB는 "노드도 파드도 아닌 egress IP"를 본다 — 이 경우 IP만으로는 파드 식별이 불가능하다.

### Cited Findings
- GKE: ip-masq-agent의 nonMasqueradeCIDRs에 있는 목적지로 가는 패킷은 파드 소스 IP 유지, 나열되지 않은 목적지는 노드 IP로 SNAT된다. DaemonSet을 배포하지 않으면 기본 SNAT 동작. (검색 요약상 기본 목록은 10/8, 172.16/12, 192.168/16이라 하나 영문 원문 재확인 필요) — [GKE ip-masquerade-agent](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/ip-masquerade-agent), [GKE pod→external](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/pod-to-external-endpoint-connectivity)
- GKE Cloud NAT 예: 파드 10.4.0.5, 노드 10.128.0.10, 목적지 8.8.8.8(리스트 밖) → 노드에서 먼저 SNAT되어 VPC에 노드 IP로 나감 — 위 GKE 문서
- Cilium: 기본적으로 클러스터를 떠나는 모든 트래픽의 소스를 노드 IPv4로 masquerade. `ipv4-native-routing-cidr` 내 목적지는 masquerade 제외(퍼블릭 클라우드에서는 VPC CIDR 자동 감지). — [Cilium masquerading](https://docs.cilium.io/en/stable/network/concepts/masquerading/)
- Cilium BPF masquerade(`bpf.masquerade=true`)는 가장 효율적인 구현이며, `ipMasqAgent.enabled=true` + `nonMasqueradeCIDRs`로 제외 대역 지정(빈 설정이면 RFC1918 등 내장 목록). BPF masquerade 시 "파드→노드 External IP" 트래픽은 masquerade 되지 않음(iptables 모드와 다름). iptables 모드는 모든 커널에서 동작. — 위 Cilium masquerading 문서
- Cilium Egress Gateway: 정책에 매칭된 트래픽이 파드/노드 IP가 아닌 지정 egress IP로 나간다. 게이트웨이 노드에 해당 IP가 인터페이스에 설정되어 있어야 하며 없으면 드롭. Cilium은 IP를 생성/할당/이동하지 않는다. 전제: `egressGateway.enabled`, `bpf.masquerade=true`, `kubeProxyReplacement=true`, identity mode=CRD. 새 파드는 정책 적용 지연이 있어 그 사이 파드/노드 IP로 나갈 수 있다. egressIP와 interface 동시 지정 불가. — [Cilium Egress Gateway](https://docs.cilium.io/en/stable/network/egress-gateway/egress-gateway/)
- Calico `natOutgoing`: IP pool 밖 목적지로 가는 트래픽을 masquerade(노드 로컬에서 수행). 특정 CIDR 제외는 해당 대역을 disabled pool로 추가. 온프렘에서 BGP로 물리망과 피어링하면 natOutgoing을 끄고 물리망이 NAT/라우팅하도록 권장. — [Calico IPPool](https://docs.tigera.io/calico/latest/reference/resources/ippool.md), [Configure outgoing NAT](https://docs.tigera.io/calico/latest/networking/configuring/workloads-outside-cluster)
- Calico egress gateway: 기본(natOutgoing:false)은 소스가 egress gateway 파드 IP, natOutgoing:true면 게이트웨이 호스트 노드 IP로 SNAT. (Enterprise/Cloud 문서) — [Calico egress gateway host ip](https://docs.tigera.io/calico-enterprise/latest/networking/egress/egress-gateway-host-ip)
- Flannel: `--ip-masq` 기본 false이나 upstream K8s 매니페스트는 명시적으로 켠다(→ `FLANNEL_IPMASQ=true`). CNI 플러그인은 delegate의 ipMasq를 그 반대로 설정해 flannel이 egress 규칙을 소유. (제3자 블로그 기반, 설치 매니페스트로 확인 필요) — [oneuptime Flannel ip-masq](https://oneuptime.com/blog/post/2026-08-21-flannel-ip-masq-source-ip/markdown)
- AWS VPC CNI: 기본값은 VPC CIDR 밖 IPv4 목적지 트래픽을 노드 primary ENI의 primary private IP로 SNAT. `AWS_VPC_K8S_CNI_EXTERNALSNAT=true`면 SNAT 규칙을 생략(외부 NAT 게이트웨이가 처리하는 경우용). VPC 내부 목적지(DB가 같은 VPC)는 SNAT 안 되므로 파드 IP(보조 ENI IP)가 그대로 보인다. — [EKS external SNAT](https://docs.aws.amazon.com/eks/latest/userguide/external-snat.md), [amazon-vpc-cni-k8s #1392](https://github.com/aws/amazon-vpc-cni-k8s/issues/1392)
- OpenShift OVN-Kubernetes EgressIP: `k8s.ovn.org/egress-assignable` 라벨 노드에 EgressIP 객체의 주소를 분배, namespaceSelector/podSelector로 대상 파드 선택, status에 노드↔IP 매핑 표시. 즉 외부에는 해당 EgressIP가 소스로 보인다. 라벨 전에 객체를 만들면 첫 노드에 몰릴 수 있음. — [OCP 4.20 EgressIP](https://docs.redhat.com/en/documentation/openshift_container_platform/4.20/html/ovn-kubernetes_network_plugin/configuring-egress-ips-ovn)

### Inferences
- DPM이 보는 client IP는 4종: 파드 IP(non-masq/네이티브 라우팅/VPC 내부), 노드 IP(기본 masquerade), egress IP(Egress Gateway/EgressIP/Calico EGW), NAT 장비 IP(외부 방화벽·NAT). 고객 환경 CNI별로 어느 쪽인지 먼저 판정(실측: DB 세션의 client_addr vs 노드/파드 CIDR)해야 한다.
- 노드 IP로 SNAT되는 경우 DB 세션은 "노드 IP + 임시 포트"만 알려주므로 노드까지는 특정되나 파드는 conntrack(Q3)이나 앱 측 커넥션 정보(JENNIFER의 DB 접속 정보/세션)가 필요하다. egress IP 방식은 노드조차 모호(HA 시 노드 이동)하여 파드 상관에는 불리하다.
- 폐쇄 금융망에서 파드 IP를 DB에 직접 노출하는(non-masq, BGP) 구성이면 상관이 가장 쉬우나, DB 방화벽 ACL이 파드 CIDR 전체 허용을 요구하게 되어 보안 정책과 충돌할 수 있다(추론).

### Gaps
- Calico 기본 natOutgoing 값(IP pool 생성 시 기본 true 여부)·AWS 문서 원문의 정확한 문구는 이번에 직접 확인 못함(검색 요약 기반).
- OpenShift EgressIP의 구체적 SNAT 처리 위치(egress 노드 경유 시 홉)·OVN 기본 masquerade 동작은 문서 본문 미확인.
- DB(Oracle/PostgreSQL/DB2 등) 별로 세션 뷰의 client 주소 필드 및 DPM 수집 방식은 조사하지 않음.

## Q2. K8s 메타데이터를 flow/process에 붙이는 eBPF·flow 도구 카탈로그

### Takeaway
공통 패턴은 "노드별 DaemonSet 에이전트가 커널(소켓/패킷)에서 5-tuple·PID를 수집 + K8s API watch로 IP/cgroup→pod 매핑 캐시 + conntrack 또는 CNI의 자체 매핑으로 NAT 보정"이다. DeepFlow는 IP 대신 정수 태그(SmartEncoding)로 저장 비용을 줄이고, Cilium/Hubble은 CNI 자체의 identity/ipcache를 쓰며, Coroot·Caretta 등은 K8s API 캐시 + conntrack을 쓴다. 이번 조사에서 Kindling/Odigos/Kepler/Parca/Groundcover(상용)는 근거 확보 실패.

### Cited Findings
- DeepFlow(SIGCOMM 2023 "Network-Centric Distributed Tracing with DeepFlow: Troubleshooting Your Microservices in Zero Code"): README가 논문 채택을 명시. SmartEncoding은 클라우드 리소스, K8s 컨테이너/라벨/어노테이션, CMDB 업무 속성 등의 메타 태그를 사전 인코딩해 주입하며 ClickHouse String/LowCard 대비 저장 오버헤드 10배 감소를 주장(벤더 주장). — [DeepFlow GitHub](https://github.com/deepflowio/deepflow), [논문](https://dl.acm.org/doi/pdf/10.1145/3603269.3604823)
- 논문(검색 발췌, 일부 단편): 태그 수집 단계에서 클러스터 내 에이전트가 K8s 리소스 정보를 수집하고 서버가 클라우드 리소스 태그를 직접 수집. 스마트 인코딩 단계에서 에이전트는 트레이스에 VPC·IP 태그만 정수 형태로 주입하고, 서버가 나머지 리소스 태그(node, service, pod 등)를 정수로 저장/조회 시점에 추가. — 위 논문 링크 (본문 fetch는 403으로 실패, 발췌 기반이라 잠정적)
- DeepFlow 메타데이터 파이프라인(자동 생성 인덱스 문서, 2차 출처): agent의 ResourceWatcher가 K8s API server를 list/watch, ActivePoller(SYS_ADMIN, setns) 또는 PassivePoller(AF_PACKET)로 파드·노드 MAC 동기화; controller.recorder가 MySQL에 메타 저장 후 tagrecorder가 태그 테이블 생성. 에이전트는 노드마다 배치. — [DeepFlow docsearch 인덱스](https://docsearch.algolia.com/mcp/docs/repo/deepflowio/deepflow)
- Cilium/Hubble: flow 레코드가 source/destination의 identity, namespace, labels, pod_name 및 최상위 node_name을 포함(제3자 가이드의 샘플 JSON 기반). 라벨 필터에 `reserved:host` 등 identity 라벨 사용. — [Netways observing-traffic](https://docs.nws.netways.de/en/kubernetes/troubleshooting/observing-traffic/), [Hubble README](https://github.com/cilium/hubble/blob/main/README.md)
- Microsoft Retina: CNI·클라우드 비종속, DaemonSet으로 노드마다 eBPF 로드, 원시 네트워크 데이터에 K8s 메타(Namespace, Owner, Resource)를 붙여 Prometheus/Grafana/Hubble UI로 내보냄. AKS Advanced Network Observability의 비-Cilium 노드 엔진. (공식 retina.sh 미확인, 2차 출처) — [Tigera 비교글](https://www.tigera.io/?p=66383), [MS Tech Community](https://techcommunity.microsoft.com/blog/linuxandopensourceblog/-/4403361)
- Coroot node-agent: eBPF로 컨테이너 TCP connect()/listen() 추적(최소 커널 5.1). K8s Service의 가상 ClusterIP 문제는 root netns(사이드카면 클라이언트 파드 netns)의 conntrack 테이블 조회로 실제 목적지 복원; kube-proxy 없는 Cilium 환경은 Cilium의 conntrack 테이블 사용; 에이전트 시작 전 연결은 각 netns의 /proc/net/tcp(6)로 보충; IP:PORT 조인으로 container↔container 엣지 구성, kube-state-metrics로 Deployment/StatefulSet 집계. — [Coroot 블로그](https://coroot.com/blog/building-a-service-map-using-ebpf/), [pkg.go.dev](https://pkg.go.dev/github.com/coroot/coroot-node-agent@v1.34.0)
- Caretta(Groundcover): DaemonSet eBPF 커넥션 추적, `K8sIPResolver`가 K8s 리소스 watch로 인메모리 스냅샷을 유지하며 IP를 파드 또는 최상위 컨트롤러로 귀속, 외부 주소는 선택적 DNS. 노드 IP 해석 여부는 확인 못함. — [DeepWiki Caretta IP resolution](https://deepwiki.com/groundcover-com/caretta/2.2-kubernetes-ip-resolution)
- Pixie: 공식 요구사항 커널 4.14+, PEM을 DaemonSet으로 배포하며 BPF 설치를 위해 vizier-pem 파드에 privileged 필요; EKS Fargate·GKE Autopilot 미지원. — [Pixie requirements](https://docs.px.dev/installing-pixie/requirements/)
- Grafana Beyla: `BEYLA_KUBE_METADATA_ENABLE=true`로 메타 장식, pods/ReplicaSets list·watch RBAC 필요; DaemonSet 모드는 `hostPID: true`; privileged 대신 CAP_SYS_ADMIN·CAP_SYS_PTRACE(커널 5.11 미만은 CAP_SYS_RESOURCE 추가) + perf_event 접근(CAP_PERFMON 또는 paranoid≤2). — [Beyla K8s setup](https://grafana.com/docs/beyla/latest/setup/kubernetes/)

### Inferences
- "앱↔호스트" 연결에는 앱 프로세스의 cgroup/PID→파드→노드 매핑(Beyla/DeepFlow/Pixie/Coroot 방식)이 핵심이며, 이는 IP가 필요 없다. IP 기반은 "app↔DB" 구간(SNAT 보정 필요)에만 필요하다.
- 외부 DB 구간은 에이전트가 파드 netns 쪽(NAT 전) 소켓에서 5-tuple을 잡으면 소스가 파드 IP로 보이고, 노드 NIC(NAT 후)에서 잡으면 노드 IP — 어느 지점에서 수집하는지에 따라 키가 달라진다. 소켓 레벨(connect/tcp_* kprobe) 수집이 NAT 전 5-tuple을 주므로 conntrack 조인에 유리(추론).
- 도입 우선순위 후보: Cilium 환경이면 Hubble, 비-Cilium이면 Coroot(커널 5.1+)/DeepFlow/Retina. 단 JENNIFER·SMS·DPM 같은 기존 도구와 병행하는 "상관용 매핑 소스"로만 쓰는 최소 구성이 폐쇄망에 현실적(추론).

### Gaps
- Kindling, Odigos, Kepler, Parca, Groundcover(상용)의 메타데이터 부착 방식은 근거 문서를 확보하지 못함(Odigos는 계측 자동화, Kepler는 전력 추정, Parca는 프로파일링 중심이라는 것은 일반 지식이며 미검증).
- DeepFlow 논문 본문(SmartEncoding 세부, NAT 처리) fetch 실패(403) — 정확한 인코딩 구조·저장 절감 수치 재확인 필요.
- Retina의 enrichment 필드 상세는 공식 문서 미확인.

## Q3. conntrack 기반 NAT 역해석 (DB가 본 노드 IP:포트 → 파드)

### Takeaway
Linux conntrack 엔트리는 original tuple(파드 IP:port → DB)과 reply tuple(DB → 노드 IP:translated port)을 모두 보관하므로, DB 세션의 (노드 IP, 소스 포트, DB IP:port)로 역조회하면 파드 IP:원 포트를 얻을 수 있다. 단 엔트리는 연결 종료 후 타임아웃으로 사라지므로 실시간/주기 수집이 필요하다.

### Cited Findings
- masquerade는 파드 IP를 노드 IP로 변환하며 conntrack이 변환 전후 양쪽 tuple을 보관해 응답에 역변환을 적용한다. `conntrack -L --any-nat`으로 SNAT/DNAT 엔트리 조회 가능. — [devopscube NAT in K8s](https://newsletter.devopscube.com/p/the-nat-behind-kubernetes-networking), [oneuptime NAT translation table](https://oneuptime.com/blog/post/2026-03-20-view-nat-translation-table-linux/markdown)
- Coroot는 위 방식(conntrack으로 변환 주소 확인)을 에이전트에서 자동화하고, kube-proxy 대체 Cilium에서는 Cilium conntrack(BPF CT 맵)을 사용한다. — [Coroot 블로그](https://coroot.com/blog/building-a-service-map-using-ebpf/)
- 노드 IP+포트 → 파드 역조회 레시피는 직접 문서를 못 찾음(아래 추론).

### Inferences
- 절차(미검증 설계): DPM의 세션 (client_ip=노드IP, client_port, server_ip:port) → 해당 노드에서 `conntrack -L -p tcp` 중 reply tuple의 dst=노드IP:client_port 로 매칭 → original src(파드 IP:port) → K8s API로 파드/노드 해석. conntrack 이벤트 구독(`conntrack -E`/netlink)으로 연결 시작 시점에 로그를 남기지 않으면 단기 세션은 놓친다.
- 포트 재사용(masquerade가 소스 포트를 재할당)과 장수명 DB 커넥션 풀(수시간 유지)이 있어, 커넥션 풀 기반 JDBC에서는 파드당 소수 커넥션이 장기 유지 → 폴링 주기를 느슨히 잡아도 포착 가능(추론).
- Cilium BPF masquerade·kube-proxy replacement 환경은 iptables conntrack 엔트리가 없을 수 있어 `cilium bpf nat list`/`cilium bpf ct list` 등 Cilium 자체 맵을 봐야 한다(Coroot 사례에서 유추; 명령어 정확성은 미검증).

### Gaps
- 노드 IP:포트→파드 역해석을 전용 도구로 제공하는 오픈소스 문서는 못 찾음. `cilium bpf nat list` 정확한 명령·출력 형식은 확인 못함.
- conntrack 테이블 크기/타임아웃 기본값, 금융망에서의 허용 여부는 미조사.

## Q4. 시간 인지(IP churn/재사용) IP→워크로드 해석

### Takeaway
IP는 파드 재생성 시 재사용되므로 "현재 매핑 캐시"만으로는 과거 장애 분석이 틀어진다. Cilium은 정책 판단을 IP가 아닌 numeric identity로 하고 ipcache(IP→identity)로 매핑하며, DeepFlow는 수집 시점에 에이전트가 정수 태그를 박는 설계다. 그러나 두 도구의 "과거 시점 재해석" 보장 방식은 이번 조사에서 근거 확보 실패.

### Cited Findings
- Cilium 정책 결정은 IP가 아닌 identity 비교이며, identity는 동일 보안 라벨 집합의 엔드포인트 그룹에 부여되는 숫자 라벨. IP→identity 매핑은 `cilium map get cilium_ipcache`로 조회. — [oneuptime Cilium identity](https://oneuptime.com/blog/post/2026-03-13-cilium-identity-management-mode/markdown), [Solo.io](https://solo.io/blog/could-network-cache-based-identity-be-mistaken)
- IP 재사용 시 새 파드가 이전 파드 IP를 받되 identity가 다를 수 있다는 우려를 Solo.io 글이 다루며, 같은 글 안에서 그 우려를 "구식/오해"로도 서술해 해석이 엇갈린다. — [Solo.io](https://solo.io/blog/could-network-cache-based-identity-be-mistaken)
- Caretta는 인메모리 스냅샷만 유지(현재 상태). — [DeepWiki Caretta](https://deepwiki.com/groundcover-com/caretta/2.2-kubernetes-ip-resolution)
- DeepFlow는 리소스 메타를 MySQL(controller)에 보관하고 에이전트가 수집 시점에 VPC/IP 정수 태그를 부여 — 위 DeepFlow 출처.

### Inferences
- 우리 플랫폼에서 안전한 방식: K8s API watch 이벤트(pod add/delete, IP, node, 시작/종료 시각)를 이력 테이블(ip, pod_uid, node, valid_from, valid_to)로 저장하고, 조회 시 "장애 시각 ∈ [valid_from, valid_to)"로 해석(SCD Type 2). 이것은 일반적 설계 제안이며 특정 도구의 구현을 인용한 것이 아님.
- 파드 UID와 컨테이너 ID를 키로 삼고 IP는 속성으로 취급한다(IP 재사용 회피).

### Gaps
- DeepFlow/Hubble이 과거 시점 IP 재사용을 어떻게 구분하는지(예: 태그에 시간 범위 포함 여부) 근거 없음 — 논문 본문 필요.
- Cilium 공식 문서의 ipcache 수명주기 확인 못함.

## Q5. 폐쇄망(금융) 적용 요건 및 제약: 커널, 권한

### Takeaway
eBPF 도구는 노드 커널 5.x(권장 5.10+)와 privileged(또는 CAP_SYS_ADMIN/BPF/PERFMON) DaemonSet이 필수라, RHEL 8(4.18) 계열 + 보안 심사가 걸림돌이다. 에이전트리스 대안은 K8s API watch + (필요 시) 노드의 conntrack 조회로 권한을 최소화하는 것이다.

### Cited Findings
- Cilium 공식 요구: 커널 5.10+ 권장(또는 RHEL 8.10의 4.18 등가 백포트). 기본 eBPF 설정 CONFIG_BPF, BPF_SYSCALL, BPF_JIT, DEBUG_INFO_BTF, CGROUP_BPF 등 필요. — [Cilium system requirements](https://docs.cilium.io/en/stable/operations/system_requirements/)
- Cilium Egress Gateway: 커널 5.2+; 5.10 미만은 인터페이스 리다이렉트 실패 시 기본 인터페이스로 폴백(strict 요구 시 5.10+ 권장). — [Cilium Egress Gateway](https://docs.cilium.io/en/stable/network/egress-gateway/egress-gateway/)
- Pixie: 커널 4.14+, PEM privileged DaemonSet, RHEL 8+/CentOS 7.3+/Debian 10+ 동작 사례. — [Pixie requirements](https://docs.px.dev/installing-pixie/requirements/)
- Coroot node-agent: 최소 커널 5.1. — [pkg.go.dev](https://pkg.go.dev/github.com/coroot/coroot-node-agent@v1.34.0)
- Beyla: privileged 대신 CAP_SYS_ADMIN+CAP_SYS_PTRACE(+<5.11 CAP_SYS_RESOURCE), hostPID 필요. — [Beyla K8s setup](https://grafana.com/docs/beyla/latest/setup/kubernetes/)
- DeepFlow 에이전트 ActivePoller는 SYS_ADMIN(setns) 필요. — [DeepFlow docsearch 인덱스](https://docsearch.algolia.com/mcp/docs/repo/deepflowio/deepflow)
- Fargate·GKE Autopilot은 eBPF 불가(Pixie 문서). — [Pixie requirements](https://docs.px.dev/installing-pixie/requirements/)

### Inferences
- 국내 금융권 폐쇄망은 RHEL/Rocky 8.x(4.18 백포트) 또는 OpenShift RHCOS가 많아 "커널 버전 문자열"만으로 판단 말고 BTF 지원과 백포트 여부를 실측해야 한다(추론).
- 이미지 반입(레지스트리 미러), privileged 파드 승인(PSA/SCC), 커널 모듈·BTF 요건, 에이전트 CPU 오버헤드 승인 절차가 도입 장벽이다. 가장 낮은 위험 순서: ① K8s API 읽기 전용 매핑(pod IP/UID/node) ② 노드 conntrack 조회 ③ eBPF DaemonSet(Hubble/Coroot/DeepFlow) (추론).

### Gaps
- 국내 금융감독 규정·망분리 환경에서의 eBPF 에이전트 도입 사례/승인 기준: 근거 없음.
- OpenShift의 SCC에서 privileged DaemonSet 허용 절차, RHEL 커널별 BTF 지원표는 조사하지 않음.
- 오버헤드(CPU %) 수치: 일부 벤더 주장만 있었고 확인된 독립 수치 없음.
