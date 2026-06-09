"""알람 소켓 수신 서버 전용 설정.

에이전트 서버의 AppConfig와 독립된 전용 설정 클래스.
env_prefix: ALARM_SERVER_
env_file 우선순위: alarm_server.env > .env
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

    model_config = {
        "env_prefix": "ALARM_SERVER_",
        "env_file": ["alarm_server.env", ".env"],  # 전용 env 우선, 없으면 공통 .env
        "extra": "ignore",
    }
