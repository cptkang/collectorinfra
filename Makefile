.PHONY: install dev test regress lint run server format

install:
	pip install -e .

dev:
	pip install -e ".[dev,document]"

# D-303: 기본은 모듈 단위 회귀. 전체(test)는 사용자가 요청할 때만 돌린다
test:
	python scripts/regress.py --full

regress:
	python scripts/regress.py

lint:
	ruff check src/ tests/
	mypy src/

run:
	python -m src.main --query "$(Q)"

server:
	python -m src.main --server

format:
	ruff format src/ tests/
