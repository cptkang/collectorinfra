# Offline Wheel Files

Python 3.11 대상으로 다운로드된 의존성 wheel 파일입니다.

## 디렉토리 구조

```
wheels/
├── requirements_all.txt   # 통합 의존성 목록
├── mac/                   # macOS ARM64 (Apple Silicon)
├── linux/                 # Linux x86_64
└── windows/               # Windows AMD64
```

## 오프라인 설치 방법

```bash
# 해당 플랫폼 폴더에서 설치
pip install --no-index --find-links=wheels/mac/ -r wheels/requirements_all.txt      # macOS
pip install --no-index --find-links=wheels/linux/ -r wheels/requirements_all.txt     # Linux
pip install --no-index --find-links=wheels/windows/ -r wheels/requirements_all.txt   # Windows
```

## 다운로드 정보

- Python 버전: 3.11
- 다운로드 일시: 2026-04-16
- 패키지 수: 101개 (각 플랫폼)
- 소스: `pyproject.toml` + `mcp_server/pyproject.toml` + `requirements.txt` 통합
