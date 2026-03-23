.PHONY: install dev test lint run server format

install:
	pip install -e .

dev:
	pip install -e ".[dev,document]"

test:
	pytest tests/ -v --cov=src --cov-report=term-missing

lint:
	ruff check src/ tests/
	mypy src/

run:
	python -m src.main --query "$(Q)"

server:
	python -m src.main --server

format:
	ruff format src/ tests/
