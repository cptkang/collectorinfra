"""alarm_server: 외부 알람 소켓 수신 전용 서버.

폴스타 모니터링 시스템 등으로부터 TCP 소켓 통신으로 알람 이벤트를 수신하여
Redis Stream('alarm:raw')에 발행한다.

에이전트 서버(src/)와 독립된 별도 프로세스로 실행된다.
기동 방법: python -m alarm_server
"""
