# ADR 0003 — Resiliência em camadas: retry, backoff, DLQ

**Status:** aceito

## Contexto

O processador externo (banco, PSP) pode ficar lento ou fora do ar. Falhas têm naturezas
diferentes: um soluço de rede de 200 ms, uma indisponibilidade de 10 minutos, ou um dado que
nunca vai ser processado.

## Decisão

Três camadas, cada uma para uma escala de tempo:

| Camada | Onde | Escala | Para quê |
|---|---|---|---|
| Retry local com backoff exponencial + *full jitter* | `shared/resiliencia.py` | milissegundos | soluços de rede, throttling |
| Retry do SQS (visibility timeout de 180 s) | `template.yaml` | minutos | indisponibilidade curta do externo |
| DLQ após `maxReceiveCount: 3` + alarme | `template.yaml` | humano | falha que não se resolve sozinha |

Detalhes:

- **Full jitter** (`espera = aleatório(0, min(teto, base·2ⁿ))`) evita que vários workers
  tentem de novo no mesmo instante (*thundering herd*).
- Erros são classificados: `FalhaTemporariaNoProcessador` → retry; regra de negócio violada →
  `RECUSADA` (sem retry); mensagem malformada → descarte com log e métrica (retentar não ajuda).
- **Partial batch response** (`ReportBatchItemFailures`): o worker devolve só os `messageId`
  que falharam; as outras 9 mensagens do lote não são reprocessadas.
- `ScalingConfig.MaximumConcurrency: 5` limita quantos workers batem no externo ao mesmo tempo.
- O SDK (boto3) usa retry `adaptive` para chamadas à própria AWS.
- `FALHA_SIMULADA_PERCENTUAL` injeta falhas no processador simulado para demonstrar o fluxo em
  ambiente real.

## Consequências

- Teste de integração prova o caminho completo: 3 recebimentos com falha → mensagem na DLQ →
  transação parada em `PROCESSANDO`, pronta para *redrive* (o domínio permite retomar
  `PROCESSANDO → PROCESSANDO`).
- Comprovante (S3) e evento (SNS) são gravados **antes** do status final. Se falharem, a mensagem
  volta e tudo é refeito: o comprovante é sobrescrito na mesma chave e o evento tem id
  determinístico (`<transacao_id>:<status>`) para os consumidores descartarem duplicatas.
  Trocamos "evento duplicado possível" por "evento perdido impossível".
