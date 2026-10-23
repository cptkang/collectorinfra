"""알람 소켓 수신 서버 전용 설정.

에이전트 서버의 AppConfig와 독립된 전용 설정 클래스.
env_prefix: ALARM_SERVER_
env_file: CWD 기준 .env · alarm_server.env — pydantic-settings는 목록 뒤 파일이 이기므로
같은 키가 둘 다에 있으면 **전용 파일(alarm_server.env) 값이 쓰인다**(2026-10-21 교정 — 종전 순서는
.env가 이겼다 · D-345 부기). 전용 파일에 없는 키는 공용 .env로 보충된다.
OS 환경변수는 둘 다보다 우선한다.
"""

from pydantic_settings import BaseSettings


class AlarmServerConfig(BaseSettings):
    """알람 소켓 수신 서버 전용 설정."""

    socket_host: str = "0.0.0.0"
    socket_port: int = 9100
    # Redis — 에이전트 서버의 동일 Redis를 가리켜야 함
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str = ""
    stream_key: str = "alarm:raw"
    log_level: str = "INFO"
    # 설정 관리 리스너(plans/191 · D-345) — 포트·토큰이 모두 있을 때만 기동한다(미설정 = 현행 동일).
    admin_port: int | None = None
    admin_host: str = "127.0.0.1"
    admin_token: str = ""

    model_config = {
        "env_prefix": "ALARM_SERVER_",
        "env_file": [".env", "alarm_server.env"],  # 뒤 파일 우선 — 같은 키면 전용 파일이 이긴다
        "extra": "ignore",
    }
