"""Entidade Transacao e sua máquina de estados.

O domínio não conhece FastAPI, boto3 nem AWS. Ele só sabe o que é uma transação
válida e quais mudanças de status são permitidas.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from transacoes.domain.excecoes import TransacaoInvalida, TransicaoDeStatusInvalida


class TipoTransacao(StrEnum):
    PAGAMENTO = "PAGAMENTO"
    TRANSFERENCIA = "TRANSFERENCIA"


class StatusTransacao(StrEnum):
    PENDENTE = "PENDENTE"
    PROCESSANDO = "PROCESSANDO"
    APROVADA = "APROVADA"
    RECUSADA = "RECUSADA"


# PROCESSANDO -> PROCESSANDO é permitido de propósito: se o worker cair no meio do
# processamento, a mensagem volta para a fila e outra execução precisa retomar.
TRANSICOES_PERMITIDAS: dict[StatusTransacao, frozenset[StatusTransacao]] = {
    StatusTransacao.PENDENTE: frozenset({StatusTransacao.PROCESSANDO}),
    StatusTransacao.PROCESSANDO: frozenset(
        {StatusTransacao.PROCESSANDO, StatusTransacao.APROVADA, StatusTransacao.RECUSADA}
    ),
    StatusTransacao.APROVADA: frozenset(),
    StatusTransacao.RECUSADA: frozenset(),
}

STATUS_FINAIS = frozenset({StatusTransacao.APROVADA, StatusTransacao.RECUSADA})


def _agora() -> datetime:
    return datetime.now(UTC)


@dataclass
class Transacao:
    tipo: TipoTransacao
    valor_centavos: int
    conta_origem: str
    conta_destino: str
    descricao: str | None = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: StatusTransacao = StatusTransacao.PENDENTE
    motivo_recusa: str | None = None
    criada_em: datetime = field(default_factory=_agora)
    atualizada_em: datetime = field(default_factory=_agora)
    versao: int = 1

    # ------------------------------------------------------------------ fábrica
    @classmethod
    def criar(
        cls,
        tipo: TipoTransacao | str,
        valor_centavos: int,
        conta_origem: str,
        conta_destino: str,
        descricao: str | None = None,
    ) -> Transacao:
        try:
            tipo = TipoTransacao(tipo)
        except ValueError as exc:
            raise TransacaoInvalida(f"Tipo de transação desconhecido: {tipo}") from exc

        conta_origem = (conta_origem or "").strip()
        conta_destino = (conta_destino or "").strip()

        if not isinstance(valor_centavos, int) or isinstance(valor_centavos, bool):
            raise TransacaoInvalida("O valor deve ser um inteiro em centavos")
        if valor_centavos <= 0:
            raise TransacaoInvalida("O valor deve ser maior que zero")
        if not conta_origem:
            raise TransacaoInvalida("A conta de origem é obrigatória")
        if not conta_destino:
            raise TransacaoInvalida("A conta de destino é obrigatória")
        if tipo is TipoTransacao.TRANSFERENCIA and conta_origem == conta_destino:
            raise TransacaoInvalida("Não é possível transferir para a mesma conta")

        return cls(
            tipo=tipo,
            valor_centavos=valor_centavos,
            conta_origem=conta_origem,
            conta_destino=conta_destino,
            descricao=descricao,
        )

    # --------------------------------------------------------- comportamentos
    @property
    def finalizada(self) -> bool:
        return self.status in STATUS_FINAIS

    def iniciar_processamento(self) -> None:
        self._transicionar(StatusTransacao.PROCESSANDO)

    def aprovar(self) -> None:
        self._transicionar(StatusTransacao.APROVADA)

    def recusar(self, motivo: str) -> None:
        if not motivo:
            raise TransacaoInvalida("Uma recusa precisa de motivo")
        self._transicionar(StatusTransacao.RECUSADA)
        self.motivo_recusa = motivo

    def _transicionar(self, destino: StatusTransacao) -> None:
        if destino not in TRANSICOES_PERMITIDAS[self.status]:
            raise TransicaoDeStatusInvalida(self.status, destino)
        self.status = destino
        self.atualizada_em = _agora()
        self.versao += 1
