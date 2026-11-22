"""Processador externo simulado (no lugar de um banco/PSP de verdade).

`percentual_falha` permite injetar falhas transitórias para ver retry, backoff e
DLQ funcionando em ambiente real (variável FALHA_SIMULADA_PERCENTUAL).
"""

from __future__ import annotations

import logging
import random
from collections.abc import Callable

from transacoes.domain.transacao import Transacao
from transacoes.ports import FalhaTemporariaNoProcessador, ProcessadorDePagamento

logger = logging.getLogger(__name__)


class ProcessadorSimulado(ProcessadorDePagamento):
    def __init__(
        self,
        percentual_falha: float = 0.0,
        sortear: Callable[[], float] = random.random,
    ) -> None:
        if not 0 <= percentual_falha <= 100:
            raise ValueError("percentual_falha deve estar entre 0 e 100")
        self._probabilidade = percentual_falha / 100
        self._sortear = sortear

    def liquidar(self, transacao: Transacao) -> None:
        if self._sortear() < self._probabilidade:
            raise FalhaTemporariaNoProcessador("Processador externo indisponível (simulado)")
        logger.info("liquidacao confirmada pelo processador", extra={"transacao_id": transacao.id})
