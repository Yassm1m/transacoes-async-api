.PHONY: help install lint format test test-unit test-integration run up down logs build deploy smoke oidc

PY ?= python3
API_URL ?=

help: ## Lista os comandos
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install: ## Instala dependências de desenvolvimento
	$(PY) -m pip install -r requirements-dev.txt

lint: ## Roda ruff (lint + checagem de formatação)
	ruff check .
	ruff format --check .

format: ## Formata o código
	ruff check . --fix
	ruff format .

test: ## Roda todos os testes com cobertura
	$(PY) -m pytest --cov

test-unit: ## Só testes unitários (rápidos, sem AWS)
	$(PY) -m pytest tests/unit

test-integration: ## Só testes de integração (moto)
	$(PY) -m pytest tests/integration

run: ## API local em memória, sem AWS (http://localhost:8000/docs)
	PYTHONPATH=src uvicorn transacoes.adapters.inbound.http.lambda_handler:app --reload --port 8000

up: ## Sobe LocalStack + API + worker (precisa de LOCALSTACK_AUTH_TOKEN no .env)
	docker compose up --build -d

down: ## Derruba o ambiente local
	docker compose down -v

logs: ## Acompanha logs da API e do worker
	docker compose logs -f api worker

build: ## sam build
	sam build

deploy: ## Deploy guiado na sua conta AWS
	sam build && sam deploy --guided

smoke: ## Smoke test contra uma API publicada: make smoke API_URL=https://...
	./scripts/smoke_test.sh $(API_URL)

oidc: ## Cria a role do GitHub Actions: make oidc GITHUB_ORG=seu-usuario
	aws cloudformation deploy --template-file infra/github-oidc-role.yaml \
	  --stack-name transacoes-async-api-github-oidc --capabilities CAPABILITY_NAMED_IAM \
	  --parameter-overrides GitHubOrg=$(or $(GITHUB_ORG),Yassm1m)
