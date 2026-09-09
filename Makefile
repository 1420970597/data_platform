.PHONY: install test lint up down smoke
install:
	pip install -r requirements.txt
test:
	python -m pytest -q
lint:
	python -m compileall -q app
up:
	docker compose up -d --build
down:
	docker compose down
smoke:
	APP_PORT=$${APP_PORT:-18000} docker compose up -d --build
	curl --fail --retry 20 --retry-delay 2 http://localhost:$${APP_PORT:-18000}/api/v1/health
	APP_PORT=$${APP_PORT:-18000} docker compose down -v
