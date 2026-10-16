"""단일 DB 파이프라인 노드가 공유하는 순수 헬퍼 패키지 · application 계층(D-331 ①).

그래프 노드가 아닌 헬퍼 모듈을 ``src/nodes/``에서 옮겨 왔다(plans/166 W2 N3). 옛 경로
``src.nodes.<모듈>``은 ``sys.modules`` 별칭 shim으로 1사이클 남는다(D-337 ①).
"""
