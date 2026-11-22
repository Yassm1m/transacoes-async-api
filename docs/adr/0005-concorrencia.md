# ADR 0005 — Consumidor idempotente e lock otimista

**Status:** aceito

## Contexto

SQS standard garante entrega **pelo menos uma vez**: a mesma mensagem pode chegar duas vezes, e
até para dois workers ao mesmo tempo. Além disso, o replay idempotente da API (ADR 0002) pode
enfileirar a mesma transação de novo de propósito.

## Decisão

- Cada transação tem um campo `versao`, incrementado a cada mudança de status pelo domínio.
- O repositório só grava se a versão no banco for a esperada
  (`ConditionExpression: versao = :v`). Caso contrário, `ConflitoDeConcorrencia`.
- O worker:
  1. ignora transações já finalizadas (`APROVADA`/`RECUSADA`);
  2. marca `PROCESSANDO` com escrita condicional — se perder a corrida, desiste (o outro worker
     está cuidando);
  3. grava o status final também com escrita condicional.
- Escolhemos lock **otimista** (não pessimista) porque conflito é raro: não vale pagar uma trava
  em toda transação para resolver um caso eventual.

## Consequências

- Mensagem duplicada vira um `GetItem` e um log "ignorada", sem efeito colateral.
- Não precisamos de fila FIFO (mais cara e com limite de vazão) para ter "processa uma vez".
- O teste ponta a ponta envia a mesma transação duas vezes para a fila e verifica que existe
  exatamente um comprovante e um evento publicado.
