# ADR 0001 — Arquitetura hexagonal (ports and adapters)

**Status:** aceito

## Contexto

A regra de negócio (criar, validar, aprovar ou recusar transações) precisa ser testável em
milissegundos e não pode ficar presa a uma tecnologia. Hoje o deploy é em Lambda com DynamoDB e
SQS; amanhã pode ser ECS com Postgres e Kafka.

## Decisão

Separar o código em quatro camadas com dependências apontando para dentro:

- `domain/`: entidade `Transacao` (máquina de estados), `PoliticaDeAprovacao` e eventos. Só Python puro.
- `application/`: casos de uso que orquestram o domínio por meio de portas.
- `ports/`: classes abstratas (`ABC`) que descrevem o que a aplicação precisa (repositório, fila,
  publicador, armazenamento, processador externo).
- `adapters/`: implementações concretas. *Inbound* (FastAPI, worker SQS) chama os casos de uso;
  *outbound* (DynamoDB, SQS, SNS, S3, memória) implementa as portas.

`config.py` é o *composition root*: o único arquivo que escolhe qual adapter vai em cada porta.

## Consequências

- 79 dos 91 testes usam adapters em memória e rodam em menos de 1 segundo.
- A API roda em três ambientes sem mudar uma linha da regra de negócio: Lambda (Mangum),
  container (Dockerfile/uvicorn) e memória (`make run`).
- Custo: mais arquivos e uma camada de indireção. Para um CRUD simples seria exagero; aqui se
  paga porque a parte difícil (idempotência, concorrência, retry) fica na aplicação, testada
  sem AWS.
