"""Portas de saída (driven ports)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from transacoes.domain.eventos import TransacaoProcessada
from transacoes.domain.transacao import Transacao

# ----------------------------------------------------------------- contratos


@dataclass(frozen=True)
class RegistroDeIdempotencia:
    chave: str
    hash_requisicao: str
    transacao_id: str


class ChaveDeIdempotenciaJaUtilizada(Exception):
    """A chave já existe. O caso de uso decide se é replay legítimo ou conflito."""

    def __init__(self, registro: RegistroDeIdempotencia) -> None:
        super().__init__(f"Chave de idempotência já utilizada: {registro.chave}")
        self.registro = registro


class ConflitoDeConcorrencia(Exception):
    """Outra execução alterou a transação antes (controle otimista por versão)."""


class FalhaTemporariaNoProcessador(Exception):
    """Falha que vale a pena tentar de novo (timeout, 5xx, throttling)."""


# -------------------------------------------------------------------- portas


class RepositorioDeTransacoes(ABC):
    @abstractmethod
    def criar(self, transacao: Transacao, idempotencia: RegistroDeIdempotencia) -> None:
        """Grava transação + chave de idempotência de forma atômica.

        Levanta ChaveDeIdempotenciaJaUtilizada se a chave já existir.
        """

    @abstractmethod
    def buscar(self, transacao_id: str) -> Transacao | None: ...

    @abstractmethod
    def atualizar(self, transacao: Transacao, versao_esperada: int) -> None:
        """Salva a transação só se a versão gravada for `versao_esperada`.

        Levanta ConflitoDeConcorrencia caso contrário.
        """


class FilaDeProcessamento(ABC):
    @abstractmethod
    def enviar(self, transacao_id: str, correlation_id: str | None = None) -> None: ...


class PublicadorDeEventos(ABC):
    @abstractmethod
    def publicar(self, evento: TransacaoProcessada) -> None: ...


class ArmazenamentoDeComprovantes(ABC):
    @abstractmethod
    def salvar(self, transacao: Transacao) -> str:
        """Persiste o comprovante e devolve a URI (ex.: s3://bucket/chave)."""


class ProcessadorDePagamento(ABC):
    """Representa o sistema externo que efetivamente liquida (banco, PSP, SPI)."""

    @abstractmethod
    def liquidar(self, transacao: Transacao) -> None:
        """Levanta FalhaTemporariaNoProcessador em falhas transitórias."""
