# ADR 0004 — Lambda + SQS (e não ECS) para API e processamento

**Status:** aceito

## Contexto

Opções avaliadas para rodar a API e o worker: Lambda, ECS Fargate ou EC2.

## Decisão

Lambda atrás de API Gateway (HTTP API) para a API, e Lambda acionado por SQS para o worker.

- Carga de transações é irregular (picos em horário comercial, quase zero de madrugada): Lambda
  escala de zero e não cobra parado.
- O *event source mapping* do SQS já entrega lote, retry, DLQ e controle de concorrência sem
  código de *polling*.
- HTTP API custa menos e tem menos latência que REST API; os recursos extras do REST API
  (API keys, validação de request no gateway) não são necessários aqui.
- `arm64` (Graviton): mais barato por ms que x86.

## Consequências

- *Cold start* na primeira chamada (centenas de ms com FastAPI). Aceitável porque a resposta é
  só um `202`; se virar problema, *provisioned concurrency* ou SnapStart.
- O código não depende de Lambda: o `Dockerfile` roda a mesma API com uvicorn, pronta para ECS
  Fargate. Migrar seria trocar a infraestrutura, não a aplicação.
- Limite de 15 minutos por execução não é problema para processar uma transação.
