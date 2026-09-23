"""사용자·관리자 매뉴얼 캡처 도구 (plans/116 · D-252).

- ``replay``   — 실 파이프라인 이벤트 녹화(RecordingGraph) · 재생(ReplayGraph)
- ``snapshot`` — 작업 트리 스냅샷 + 캡처 전용 ``.env`` (운영 ``.env``/``.encenv`` 격리)
- ``_serve``   — 스냅샷 cwd 에서 본체 앱을 녹화/재생 그래프로 기동
- ``samples``  — 사례 프롬프트 실 실행(샌드박스 + 로컬 MLX) → 녹화 고정
- ``seed``     — 캡처 데이터 시드(사용자·감사·사건·알람 재생)
- ``capture``  — playwright 캡처(uv 로 별도 실행 — 루트 venv 에 playwright 없음)
"""
