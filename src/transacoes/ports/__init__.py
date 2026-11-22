"""Portas (interfaces) que a aplicação usa para falar com o mundo externo.

Os adapters em `transacoes.adapters.outbound` implementam estas classes abstratas.
Trocar DynamoDB por Postgres, ou SQS por Kafka, é escrever um novo adapter: o
domínio e os casos de uso não mudam.
"""

from transacoes.ports.saida import (
    ArmazenamentoDeComprovantes,
    ChaveDeIdempotenciaJaUtilizada,
    ConflitoDeConcorrencia,
    FalhaTemporariaNoProcessador,
    FilaDeProcessamento,
    ProcessadorDePagamento,
    PublicadorDeEventos,
    RegistroDeIdempotencia,
    RepositorioDeTransacoes,
)

__all__ = [
    "ArmazenamentoDeComprovantes",
    "ChaveDeIdempotenciaJaUtilizada",
    "ConflitoDeConcorrencia",
    "FalhaTemporariaNoProcessador",
    "FilaDeProcessamento",
    "ProcessadorDePagamento",
    "PublicadorDeEventos",
    "RegistroDeIdempotencia",
    "RepositorioDeTransacoes",
]
