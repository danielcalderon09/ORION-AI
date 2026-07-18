.PHONY: install install-gpu dev test lint format clean backend frontend docs

install:
	pip install -r backend/requirements/base.txt

install-gpu:
	pip install -r backend/requirements/base.txt
	pip install -r backend/requirements/gpu.txt

dev:
	pip install -r backend/requirements/dev.txt
	cd frontend && npm install

test:
	cd backend && pytest -v

lint:
	cd backend && ruff check src
	cd backend && mypy src

format:
	cd backend && black src tests
	cd backend && ruff check --fix src

backend:
	cd backend && python -m src.main

frontend:
	cd frontend && npm run dev

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true

# Development with docker
docker-up:
	docker-compose up -d

docker-down:
	docker-compose down
