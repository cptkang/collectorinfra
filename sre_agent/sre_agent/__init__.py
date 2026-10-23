"""sre_agent — HolmesGPT 기반 장애 진단 에이전트 (collectorinfra 독립 패키지, D-118)."""

import os

__version__ = "0.1.0"

# 무거운 import 전 프로세스 환경 키(대문자) — 셸·systemd가 준 키만 담긴다. litellm은 import 시
# python-dotenv로 CWD `.env`를 os.environ에 넣으므로, 설정 관리 API의 override_os 판정은 이 집합만
# 본다(plans/191 · D-345).
PROCESS_ENV_KEYS_AT_IMPORT = frozenset(key.upper() for key in os.environ)
