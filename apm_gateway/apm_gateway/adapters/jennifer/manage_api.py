"""제니퍼 관리·민감 조회 함수 (plans/134 W7 · N-15·N-16 · D-296 ①).

`JenniferApi`(성능·거래 조회)와 같은 클라이언트를 쓰는 별도 묶음이다 — 허용목록 GET만 부르고, 응답은
클라이언트의 두 출구(`get_json`)에서 **자격증명 경계(`domain/credentials.py`)를 지난 값**이다(새
경로도 이 경계를 빠져나갈 수 없다). Open API 경로·필드명은 이 하위 패키지 밖으로 나가지 않는다.

- v2 응답은 맨 배열·객체·불리언이다(COV E-18) — 모양이 다르면 `apm_api_error`(빈 결과로
  강등하지 않는다).
- 인스턴스 개별 설정의 404 = 「개별 설정 없음」(COV E-19 · None을 돌려준다).
- 비교 룰은 경로 표기가 매뉴얼 안에서 갈린다(COV E-01 — 형식 `compare` · 예제 `comparing`).
  `compare`를 먼저 묻고 **404일 때만** `comparing`으로 다시 물어(둘 다 GET 읽기) 답한 표기를
  돌려준다.
- 그 밖의 404·405는 그대로 올린다(호출자가 「버전 미지원 가능」 — COV E-28로 적는다).
"""

from __future__ import annotations

import re
from typing import Any

from apm_gateway.adapters.jennifer import manage_fields as mf
from apm_gateway.adapters.jennifer.allowlist import PATH_VAR_FORMATS
from apm_gateway.adapters.jennifer.client import JenniferClient
from apm_gateway.domain.call_context import expect_calls
from apm_gateway.domain.errors import API_ERROR, ApmError

EXTRA = mf.EXTRA
ENV_SCOPES = mf.ENV_SCOPES
# 룰 종류 → 대상 종류(경로 변수 `{targetType}` 열거 — 허용목록 선언과 같다)
RULE_TARGETS: dict[str, tuple[str, ...]] = {
    "metric": ("domain", "instance", "business"),
    "compare": ("domain", "instance"),
}
# 비교 룰 경로 표기(먼저 · 404일 때) — COV E-01
COMPARE_SPELLINGS: tuple[str, str] = ("compare", "comparing")
_COMPARE = {
    "compare": "/api-v2/manage/rule/event/compare/{domainId}/{targetType}",
    "comparing": "/api-v2/manage/rule/event/comparing/{domainId}/{targetType}",
}
_INDIVIDUAL = (
    "/api-v2/manage/rule/event/error/{domainId}/{errorType}/individual-setting/{instanceId}"
)


def _shape(what: str, expect: str) -> ApmError:
    return ApmError(API_ERROR, f"{what} 응답 모양이 예상과 다르다({expect})")


class JenniferManageApi:
    """관리·민감 조회(W7). 인스턴스는 요청마다 만들어도 된다(상태 없음 — 클라이언트만 공유)."""

    def __init__(self, client: JenniferClient) -> None:
        self.client = client

    # ── 인자 형식(경로 변수 형식 — 허용목록 정본과 같은 정규식) ───────────

    @staticmethod
    def account_id_error(user_id: str) -> str | None:
        """계정 ID 형식 오류 사유(정상이면 None) — 점만으로 된 값·`.xml` 꼬리는 경로 변형이라
        거부한다."""
        if not re.fullmatch(PATH_VAR_FORMATS["account"], user_id):
            return "user_id는 영문·숫자·._@- 64자 이하여야 한다"
        if set(user_id) <= {"."} or user_id.lower().endswith(".xml"):
            return "user_id가 경로 변형 표기다"
        return None

    @staticmethod
    def is_error_type(error_type: str) -> bool:
        """ERROR 유형 이름 형식(대문자·숫자·밑줄 64자 이하 — 경로 변수 `token`)."""
        return re.fullmatch(PATH_VAR_FORMATS["token"], error_type) is not None

    @staticmethod
    def is_txid(txid: str) -> bool:
        """실행 중 요청 txid 형식(부호 있는 정수 — 경로 변수 `sint`)."""
        return re.fullmatch(PATH_VAR_FORMATS["sint"], txid) is not None

    # ── 사용자 · 계정 ─────────────────────────────────────────

    async def user_list(self) -> list[dict[str, Any]]:
        """사용자 목록(id·이름·이메일·휴대폰)."""
        rows = mf.parse_user_list(await self.client.get_json("/api/auth/userlist"))
        if rows is None:
            raise _shape("사용자 목록", "result가 객체 배열이 아님")
        return rows

    async def accounts(self) -> list[dict[str, Any]]:
        """계정 목록(id·이름·그룹·허용 IP — 비밀번호는 경계가 지웠다)."""
        rows = mf.parse_accounts(await self.client.get_json("/restapi/users"))
        if rows is None:
            raise _shape("계정 목록", "객체 배열이 아님")
        return rows

    async def account(self, user_id: str) -> dict[str, Any] | None:
        """계정 1건(빈 본문·빈 객체 = 없음 → None). 404는 그대로 올린다(호출자가 판단)."""
        body = await self.client.get_json("/restapi/user/{id}", path_vars={"id": user_id})
        if body is None or body == {}:
            return None
        if not isinstance(body, dict):
            raise _shape("계정", "객체가 아님")
        return mf.parse_account(body)

    # ── 데이터 서버 · 경로 · RDB Export ──────────────────────────

    async def data_server_domains(self) -> tuple[int | None, list[dict[str, Any]]]:
        parsed = mf.parse_data_server_domains(
            await self.client.get_json("/api-v2/manage/data-server/domains")
        )
        if parsed is None:
            raise _shape("데이터 서버 도메인 배치", "count·list[{address, domain[]}] 객체가 아님")
        return parsed

    async def data_server_resource(self) -> list[dict[str, Any]]:
        rows = mf.parse_data_server_resource(
            await self.client.get_json("/api-v2/manage/data-server/resource")
        )
        if rows is None:
            raise _shape("데이터 서버 자원", "데이터 서버 주소별 객체가 아님")
        return rows

    async def data_server_properties(self) -> list[dict[str, Any]]:
        rows = mf.parse_data_server_properties(
            await self.client.get_json("/api-v2/manage/data-server/system-property-config")
        )
        if rows is None:
            raise _shape("데이터 서버 시스템 속성", "데이터 서버 주소별 객체가 아님")
        return rows

    async def db_path(self, domain_id: int) -> dict[str, Any]:
        rec = mf.parse_db_path(
            await self.client.get_json(
                "/api-v2/manage/db/path/{domainId}", path_vars={"domainId": domain_id}
            )
        )
        if rec is None:
            raise _shape("DB 경로", "객체가 아님")
        return rec

    async def rdb_exports(self) -> list[dict[str, Any]]:
        rows = mf.parse_rdb_exports(await self.client.get_json("/api-v2/manual-rdb-export"))
        if rows is None:
            raise _shape("수동 RDB Export 작업", "객체 배열이 아님")
        return rows

    # ── 룰 · 색상 경계 ────────────────────────────────────────

    async def color_boundaries(self) -> list[int | float]:
        values = mf.parse_color_boundaries(
            await self.client.get_json("/api-v2/manage/rule/active-service-color-range-boundary")
        )
        if values is None:
            raise _shape("액티브 서비스 색상 경계", "숫자 3개 배열이 아님")
        return values

    async def error_rules(self, domain_id: int) -> list[dict[str, Any]]:
        rows = mf.parse_error_rules(
            await self.client.get_json(
                "/api-v2/manage/rule/event/error/{domainId}", path_vars={"domainId": domain_id}
            )
        )
        if rows is None:
            raise _shape("ERROR 이벤트 룰", "객체 배열이 아님")
        return rows

    async def metric_rules(self, domain_id: int, target: str) -> list[dict[str, Any]]:
        rows = mf.parse_metric_rules(
            await self.client.get_json(
                "/api-v2/manage/rule/event/metric/{domainId}/{targetType}",
                path_vars={"domainId": domain_id, "targetType": target},
            )
        )
        if rows is None:
            raise _shape("지표 이벤트 룰", "객체 배열이 아님")
        return rows

    async def compare_rules(self, domain_id: int, target: str) -> tuple[list[dict[str, Any]], str]:
        """비교 룰 → (행, 답한 경로 표기). `compare`가 404면 `comparing`으로 다시 묻는다(E-01)."""
        path_vars = {"domainId": domain_id, "targetType": target}
        spelled = COMPARE_SPELLINGS[0]
        try:
            body = await self.client.get_json(_COMPARE[spelled], path_vars=path_vars)
        except ApmError as e:
            if e.status != 404:
                raise
            spelled = COMPARE_SPELLINGS[1]
            expect_calls(1)
            try:
                body = await self.client.get_json(_COMPARE[spelled], path_vars=path_vars)
            except ApmError as again:
                if again.status != 404:
                    raise
                raise ApmError(
                    API_ERROR,
                    f"비교 룰 경로 {'·'.join(COMPARE_SPELLINGS)} 표기 모두 HTTP 404",
                    status=404,
                ) from again
        rows = mf.parse_compare_rules(body)
        if rows is None:
            raise _shape("비교 이벤트 룰", "객체 배열이 아님")
        return rows, spelled

    async def error_rule_applied(self, domain_id: int, error_type: str) -> bool:
        flag = mf.parse_flag(
            await self.client.get_json(
                "/api-v2/manage/rule/event/error/{domainId}/{errorType}/applied",
                path_vars={"domainId": domain_id, "errorType": error_type},
            )
        )
        if flag is None:
            raise _shape("ERROR 유형 룰 적용 여부", "불리언이 아님")
        return flag

    async def error_rule_individual(
        self, domain_id: int, error_type: str, instance_id: int
    ) -> bool | None:
        """인스턴스 개별 설정(불리언) — **404 = 개별 설정 없음**(None · 오류 아님 · COV E-19)."""
        try:
            body = await self.client.get_json(
                _INDIVIDUAL,
                path_vars={
                    "domainId": domain_id,
                    "errorType": error_type,
                    "instanceId": instance_id,
                },
            )
        except ApmError as e:
            if e.status == 404:
                return None
            raise
        flag = mf.parse_flag(body)
        if flag is None:
            raise _shape("ERROR 유형 인스턴스 개별 설정", "불리언이 아님")
        return flag

    # ── 실행 중 요청 · 환경 · 프로세스 · 클래스 ───────────────────

    async def active_detail(
        self, domain_id: int, txid: str, session_id: int | None, thread_hash: int | None
    ) -> dict[str, Any]:
        """실행 중 요청 상세 — 세션 ID·스레드 해시는 받은 값을 그대로 싣는다(필수 여부
        미기재 · E-15)."""
        params: dict[str, Any] = {}
        if session_id is not None:
            params["sessionId"] = session_id
        if thread_hash is not None:
            params["threadHash"] = thread_hash
        rec = mf.parse_active_detail(
            await self.client.get_json(
                "/api-v2/active-service/detail/{domainId}/{txid}",
                params,
                {"domainId": domain_id, "txid": txid},
            )
        )
        if rec is None:
            raise _shape("실행 중 요청 상세", "객체가 아님")
        return rec

    async def environment(self, domain_id: int) -> list[dict[str, Any]]:
        """환경변수·JVM 시스템 속성(인스턴스별) — 긴 형식 `{instance_id, scope, name, value}`."""
        rows = mf.parse_environment(
            await self.client.get_json(
                "/api-v2/environment-variable/{domainId}", path_vars={"domainId": domain_id}
            )
        )
        if rows is None:
            raise _shape("환경변수", "인스턴스 ID별 객체가 아님")
        return rows

    async def instances_by_process(
        self, process_id: int, hostname: str | None
    ) -> list[dict[str, Any]]:
        """프로세스 ID(+ 호스트) → 인스턴스(`{domain_id, instance_id, hostname, extra}`)."""
        rows = mf.parse_instances_by_process(
            await self.client.get_json(
                "/api-v2/manage/instance",
                {"processId": process_id, **({"hostname": hostname} if hostname else {})},
            )
        )
        if rows is None:
            raise _shape("프로세스 → 인스턴스", "도메인 ID → 인스턴스 ID → 객체가 아님")
        return rows

    async def loaded_classes(
        self, domain_id: int, instance_id: int, search: str | None
    ) -> list[dict[str, Any]]:
        rows = mf.parse_loaded_classes(
            await self.client.get_json(
                "/api-v2/loaded-class/{domainId}/{instanceId}",
                {"search": search} if search else {},
                {"domainId": domain_id, "instanceId": instance_id},
            )
        )
        if rows is None:
            raise _shape("로드된 클래스", "객체 배열이 아님")
        return rows
