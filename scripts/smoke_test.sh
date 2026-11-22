#!/usr/bin/env bash
# Smoke test pós-deploy: cria uma transação, testa idempotência e espera o processamento.
# Uso: ./scripts/smoke_test.sh https://xxxx.execute-api.us-east-1.amazonaws.com
set -euo pipefail

API_URL="${1:?Informe a URL da API}"
API_URL="${API_URL%/}"
CHAVE="smoke-$(date +%s)-$RANDOM"
CORPO='{"tipo":"PAGAMENTO","valor":"12.34","conta_origem":"smoke-origem","conta_destino":"smoke-destino"}'

echo "==> GET /health"
curl -fsS "$API_URL/health"; echo

echo "==> POST /transacoes (Idempotency-Key: $CHAVE)"
RESP=$(curl -fsS -X POST "$API_URL/transacoes" \
  -H "Content-Type: application/json" -H "Idempotency-Key: $CHAVE" -d "$CORPO")
echo "$RESP"
ID=$(echo "$RESP" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')

echo "==> POST repetido deve devolver o mesmo id"
ID2=$(curl -fsS -X POST "$API_URL/transacoes" \
  -H "Content-Type: application/json" -H "Idempotency-Key: $CHAVE" -d "$CORPO" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
[ "$ID" = "$ID2" ] || { echo "FALHA: idempotência quebrada ($ID != $ID2)"; exit 1; }

echo "==> Aguardando processamento de $ID"
for _ in $(seq 1 20); do
  STATUS=$(curl -fsS "$API_URL/transacoes/$ID" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')
  echo "    status: $STATUS"
  case "$STATUS" in
    APROVADA|RECUSADA) echo "OK: smoke test passou"; exit 0 ;;
  esac
  sleep 2
done

echo "FALHA: transação não foi processada em 40s"
exit 1
