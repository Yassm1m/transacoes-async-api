# ADR 0002 — Idempotência com escrita transacional no DynamoDB

**Status:** aceito

## Contexto

Em pagamentos, o cliente vai reenviar requisições: timeout de rede, app que fecha, usuário que
clica duas vezes. Cada reenvio não pode virar uma cobrança nova. É o mesmo problema do
*overbooking* em reservas de hotel: duas requisições disputando o mesmo recurso ao mesmo tempo.

A abordagem ingênua — "busca a chave; se não existe, grava" — tem condição de corrida: duas
requisições leem "não existe" ao mesmo tempo e as duas gravam.

## Decisão

1. O cliente envia o cabeçalho `Idempotency-Key` (obrigatório), seguindo o
   [draft da IETF](https://datatracker.ietf.org/doc/draft-ietf-httpapi-idempotency-key-header/).
2. A API grava **dois itens em uma única `TransactWriteItems`**, ambos com
   `attribute_not_exists(pk)`:
   - `IDEMPOTENCIA#<chave>` → `hash_requisicao`, `transacao_id`, `expira_em` (TTL de 24h)
   - `TRANSACAO#<id>` → dados da transação
   Ou os dois são gravados, ou nenhum. O DynamoDB serializa a disputa.
3. Se a chave já existir:
   - **mesmo hash do corpo** → é retry legítimo: devolve a transação original com `202` e
     `Idempotent-Replayed: true`;
   - **hash diferente** → o cliente reutilizou a chave para outra operação: `422 IDEMPOTENCY_KEY_REUSED`.
4. O hash é calculado na camada de aplicação sobre o comando normalizado (valor em centavos,
   contas sem espaços), então `"10.0"` e `"10.00"` são o mesmo pedido.

## Consequências

- Teste de integração dispara 8 threads com a mesma chave contra o DynamoDB (moto): exatamente
  uma cria, sete recebem replay.
- **Autocura:** se a transação foi gravada mas o envio ao SQS falhou, o cliente vai repetir com a
  mesma chave; como o status ainda é `PENDENTE`, a API reenfileira. O worker é idempotente
  (ADR 0005), então mensagens duplicadas não causam dano.
- O TTL limpa as chaves sozinho; depois de 24h a mesma chave pode ser reutilizada.
- Alternativa futura: padrão *outbox* com DynamoDB Streams para eliminar a janela entre gravar e
  enfileirar.
