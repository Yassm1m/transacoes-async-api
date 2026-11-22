"""Retry com backoff exponencial e jitter (estratégia "full jitter" da AWS).

Implementado à mão, sem bibliotecas, para deixar explícito o que acontece.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PoliticaDeRetry:
    tentativas: int = 3
    espera_base_s: float = 0.2
    espera_maxima_s: float = 2.0

    def calcular_espera(self, tentativa: int, sortear: Callable[[float, float], float]) -> float:
        """tentativa começa em 1. Espera = aleatório entre 0 e min(max, base * 2^(n-1))."""
        teto = min(self.espera_maxima_s, self.espera_base_s * (2 ** (tentativa - 1)))
        return sortear(0, teto)


def executar_com_retry[T](
    operacao: Callable[[], T],
    *,
    politica: PoliticaDeRetry,
    erros_retentaveis: tuple[type[BaseException], ...],
    dormir: Callable[[float], None] = time.sleep,
    sortear: Callable[[float, float], float] = random.uniform,
    nome: str = "operacao",
) -> T:
    """Executa `operacao`; em erro retentável espera e tenta de novo.

    Na última tentativa o erro é propagado para quem chamou (no worker, isso faz
    a mensagem voltar para o SQS e, depois de N recebimentos, ir para a DLQ).
    """
    if politica.tentativas < 1:
        raise ValueError("tentativas deve ser >= 1")

    for tentativa in range(1, politica.tentativas + 1):
        try:
            return operacao()
        except erros_retentaveis as exc:
            if tentativa == politica.tentativas:
                logger.error(
                    "retry esgotado",
                    extra={"operacao": nome, "tentativas": tentativa, "erro": str(exc)},
                )
                raise
            espera = politica.calcular_espera(tentativa, sortear)
            logger.warning(
                "falha transitória, tentando novamente",
                extra={
                    "operacao": nome,
                    "tentativa": tentativa,
                    "espera_s": round(espera, 3),
                    "erro": str(exc),
                },
            )
            dormir(espera)

    raise AssertionError("inalcançável")  # pragma: no cover
