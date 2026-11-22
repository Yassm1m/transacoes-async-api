## O que muda

<!-- Resumo em 1-3 linhas -->

## Por quê

## Como testar

- [ ] `make lint`
- [ ] `make test`

## Checklist de review

- [ ] Regra de negócio ficou no `domain/` ou `application/`, sem importar boto3/FastAPI
- [ ] Novos adapters implementam uma porta existente (ou a porta foi criada)
- [ ] Operações que podem ser repetidas continuam idempotentes
- [ ] Logs novos usam `extra={...}` (campos estruturados) e não expõem dados sensíveis
- [ ] Testes cobrem o caminho feliz e as falhas
- [ ] ADR criado/atualizado se houve decisão de arquitetura
