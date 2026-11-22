"""Regras de aprovação. Ficam no domínio porque são decisão de negócio, não de infra."""

from __future__ import annotations

from dataclasses import dataclass

from transacoes.domain.transacao import TipoTransacao, Transacao

LIMITE_PADRAO_CENTAVOS = 1_000_000  # R$ 10.000,00


@dataclass(frozen=True)
class Decisao:
    aprovada: bool
    motivo: str | None = None

    @classmethod
    def aprovar(cls) -> Decisao:
        return cls(aprovada=True)

    @classmethod
    def recusar(cls, motivo: str) -> Decisao:
        return cls(aprovada=False, motivo=motivo)


class PoliticaDeAprovacao:
    """Avalia se uma transação pode seguir para liquidação.

    Simples de propósito: um limite por tipo de transação. Em um sistema real,
    aqui entrariam antifraude, saldo, horário de corte, etc.
    """

    def __init__(
        self,
        limite_pagamento_centavos: int = LIMITE_PADRAO_CENTAVOS,
        limite_transferencia_centavos: int = LIMITE_PADRAO_CENTAVOS,
    ) -> None:
        self._limites = {
            TipoTransacao.PAGAMENTO: limite_pagamento_centavos,
            TipoTransacao.TRANSFERENCIA: limite_transferencia_centavos,
        }

    def avaliar(self, transacao: Transacao) -> Decisao:
        limite = self._limites[transacao.tipo]
        if transacao.valor_centavos > limite:
            return Decisao.recusar(
                f"Valor acima do limite de {_formatar_reais(limite)} para {transacao.tipo.value}"
            )
        return Decisao.aprovar()


def _formatar_reais(centavos: int) -> str:
    reais, cents = divmod(centavos, 100)
    inteiro = f"{reais:,}".replace(",", ".")
    return f"R$ {inteiro},{cents:02d}"
