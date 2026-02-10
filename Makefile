.PHONY: setup setup-env dev dev-infra backend worker frontend test lint format help clean logs backup

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

setup-env: ## Copy example env files if actual env files don't exist
	@test -f envs/dev.env || (cp envs/dev.env.example envs/dev.env && echo "Created envs/dev.env — edit it with your local settings")
	@test -f envs/production.env || (cp envs/production.env.example envs/production.env && echo "Created envs/production.env — edit it with your production settings")

setup: setup-env ## Install all dependencies (backend + frontend)
	cd backend && uv sync
	cd frontend && npm ci

dev-infra: ## Start development infrastructure (PostgreSQL + Redis)
	docker compose -f compose-dev.yaml up -d

dev: dev-infra ## Start development infrastructure and print instructions
	@echo ""
	@echo "Infrastructure is running (PostgreSQL + Redis)."
	@echo ""
	@echo "Start services in separate terminals:"
	@echo "  make backend   - Django/Daphne on :8000"
	@echo "  make worker    - Celery worker"
	@echo "  make frontend  - Vite dev server on :5173"
	@echo ""

backend: ## Start backend server only
	cd backend/recotem && uv run daphne recotem.asgi:application -b 0.0.0.0 -p 8000

worker: ## Start Celery worker
	cd backend/recotem && uv run celery -A recotem worker --loglevel=INFO

frontend: ## Start frontend dev server
	cd frontend && npm run dev

migrate: ## Run database migrations
	cd backend/recotem && uv run python manage.py migrate

test: ## Run all tests
	cd backend && uv run pytest recotem/tests/ -v --cov --cov-fail-under=80
	cd frontend && npm run type-check && npm run lint && npm run test:unit

test-backend: ## Run backend tests only
	cd backend && uv run pytest recotem/tests/ -v --cov --cov-fail-under=80

test-frontend: ## Run frontend tests only
	cd frontend && npm run type-check && npm run lint && npm run test:unit

test-e2e: ## Run E2E tests (requires running services)
	cd frontend && npx playwright test

lint: ## Lint all code
	cd backend && uv run ruff check --fix .
	cd frontend && npm run lint

format: ## Format all code
	cd backend && uv run ruff format .

docker-build: ## Build production Docker images
	docker compose build

docker-up: ## Start production stack
	docker compose up -d

docker-down: ## Stop production stack
	docker compose down

helm-lint: ## Lint Helm chart
	helm lint helm/recotem/

helm-template: ## Render Helm templates
	helm template recotem helm/recotem/ -f helm/recotem/values.yaml

clean: ## Remove build artifacts and caches
	find backend -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find backend -type f -name '*.pyc' -delete 2>/dev/null || true
	rm -rf frontend/node_modules/.cache
	rm -rf frontend/dist
	rm -rf backend/recotem/coverage.xml
	rm -rf frontend/coverage

logs: ## Follow Docker Compose logs
	docker compose logs -f

backup: ## Dump PostgreSQL database to a timestamped file
	@mkdir -p backups
	docker compose exec -T db pg_dump -U recotem_user recotem > backups/recotem_$$(date +%Y%m%d_%H%M%S).sql
	@echo "Backup saved to backups/recotem_$$(date +%Y%m%d_%H%M%S).sql"
