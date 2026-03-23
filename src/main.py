"""서버 및 CLI 진입점.

API 서버를 시작하거나 CLI 모드로 직접 질의를 처리한다.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가 (IDE 직접 실행 지원)
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import uvicorn

from src.config import load_config
from src.graph import build_graph
from src.security.audit_logger import setup_logging
from src.state import create_initial_state

logger = logging.getLogger(__name__)


async def run_query(query: str) -> str:
    """CLI 모드에서 단일 질의를 처리한다.

    Args:
        query: 사용자 자연어 질의

    Returns:
        최종 응답 텍스트
    """
    config = load_config()
    graph = build_graph(config)

    initial_state = create_initial_state(user_query=query)

    thread_config = {
        "configurable": {
            "thread_id": "cli-session",
        }
    }

    result = await graph.ainvoke(initial_state, thread_config)
    return result.get("final_response", "응답을 생성할 수 없습니다.")


def run_server() -> None:
    """API 서버를 실행한다."""
    config = load_config()
    uvicorn.run(
        "src.api.server:app",
        host=config.server.host,
        port=config.server.port,
        reload=True,  # 개발 시에만
    )


def main() -> None:
    """메인 진입점.

    --server 옵션: API 서버 모드
    --query 옵션: CLI 질의 모드
    옵션 없이 실행: 대화형 CLI 모드
    """
    parser = argparse.ArgumentParser(
        description="인프라 데이터 조회 에이전트",
    )
    parser.add_argument(
        "--server",
        action="store_true",
        help="API 서버 모드로 실행",
    )
    parser.add_argument(
        "--query", "-q",
        type=str,
        help="CLI 모드에서 실행할 질의",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="로그 레벨 (기본: INFO)",
    )

    args = parser.parse_args()

    # 로깅 설정
    setup_logging(args.log_level)

    if args.server:
        # API 서버 모드
        print("API 서버를 시작합니다...")
        run_server()
    elif args.query:
        # 단일 질의 모드
        try:
            response = asyncio.run(run_query(args.query))
            print(response)
        except ValueError as e:
            print(f"설정 오류: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        # 대화형 CLI 모드
        print("인프라 데이터 조회 에이전트 (종료: quit/exit)")
        print("-" * 50)
        while True:
            try:
                query = input("\n질의> ").strip()
                if not query:
                    continue
                if query.lower() in ("quit", "exit", "q"):
                    print("종료합니다.")
                    break

                response = asyncio.run(run_query(query))
                print(f"\n{response}")
            except KeyboardInterrupt:
                print("\n종료합니다.")
                break
            except Exception as e:
                print(f"\n에러 발생: {e}")


if __name__ == "__main__":
    main()
