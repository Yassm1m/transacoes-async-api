"""Entrada do Lambda worker (SQS -> ProcessarTransacao).

Usa "partial batch response": só as mensagens que falharam voltam para a fila.
Sem isso, uma mensagem ruim faria o lote inteiro ser reprocessado.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from transacoes.application.casos_de_uso import ProcessarTransacao, ResultadoProcessamento
from transacoes.application.excecoes import ErroTransitorio
from transacoes.shared.observabilidade import (
    configurar_logs,
    definir_correlation_id,
    registrar_metrica,
)

logger = logging.getLogger(__name__)

_processar: ProcessarTransacao | None = None


def _obter_caso_de_uso() -> ProcessarTransacao:
    global _processar
    if _processar is None:
        from transacoes.config import construir_container

        configurar_logs()
        _processar = construir_container().processar_transacao
    return _processar


def processar_lote(event: dict[str, Any], caso_de_uso: ProcessarTransacao) -> dict[str, list]:
    falhas: list[dict[str, str]] = []

    for record in event.get("Records", []):
        message_id = record["messageId"]
        atributos = record.get("messageAttributes") or {}
        correlation_id = (atributos.get("correlation_id") or {}).get("stringValue")
        definir_correlation_id(correlation_id or message_id)
        tentativa = int((record.get("attributes") or {}).get("ApproximateReceiveCount", 1))

        try:
            corpo = json.loads(record["body"])
            transacao_id = corpo["transacao_id"]
        except (json.JSONDecodeError, KeyError, TypeError):
            # Mensagem malformada nunca vai dar certo: não adianta retentar.
            # Registramos e descartamos (vai para análise via log/métrica).
            logger.error("mensagem malformada descartada", extra={"message_id": message_id})
            registrar_metrica("MensagensMalformadas")
            continue

        try:
            resultado = caso_de_uso.executar(transacao_id)
            registrar_metrica("MensagensProcessadas", Resultado=resultado)
            if resultado == ResultadoProcessamento.PROCESSADA:
                registrar_metrica("TransacoesFinalizadas")
        except ErroTransitorio as exc:
            logger.warning(
                "falha transitoria, mensagem volta para a fila",
                extra={"transacao_id": transacao_id, "tentativa": tentativa, "erro": str(exc)},
            )
            registrar_metrica("FalhasTransitorias")
            falhas.append({"itemIdentifier": message_id})
        except Exception:
            logger.exception(
                "erro inesperado no processamento",
                extra={"transacao_id": transacao_id, "tentativa": tentativa},
            )
            registrar_metrica("FalhasInesperadas")
            falhas.append({"itemIdentifier": message_id})

    return {"batchItemFailures": falhas}


def handler(event: dict[str, Any], context: Any = None) -> dict[str, list]:
    return processar_lote(event, _obter_caso_de_uso())
