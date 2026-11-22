"""Logs estruturados em JSON, correlation ID e métricas via CloudWatch EMF.

- Todo log sai em uma linha JSON, pronta para CloudWatch Logs Insights.
- O correlation_id fica em um ContextVar: é definido na borda (HTTP ou SQS) e
  aparece automaticamente em todos os logs daquela requisição/mensagem.
- Métricas usam Embedded Metric Format: um log com formato especial que o
  CloudWatch transforma em métrica, sem chamada de API extra.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)

NAMESPACE_METRICAS = "TransacoesAsync"
SERVICO = os.getenv("SERVICO", "transacoes-api")

_CAMPOS_PADRAO = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__.keys()) | {
    "message",
    "asctime",
}


def obter_correlation_id() -> str | None:
    return _correlation_id.get()


def definir_correlation_id(valor: str | None = None) -> str:
    valor = valor or str(uuid.uuid4())
    _correlation_id.set(valor)
    return valor


class FormatadorJSON(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "nivel": record.levelname,
            "mensagem": record.getMessage(),
            "logger": record.name,
            "servico": SERVICO,
            "correlation_id": obter_correlation_id(),
        }
        for chave, valor in record.__dict__.items():
            if chave not in _CAMPOS_PADRAO and not chave.startswith("_"):
                payload[chave] = valor
        if record.exc_info:
            payload["excecao"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def configurar_logs(nivel: str | None = None) -> None:
    nivel = (nivel or os.getenv("LOG_LEVEL", "INFO")).upper()
    raiz = logging.getLogger()
    raiz.setLevel(nivel)
    # A runtime do Lambda já instala um handler; trocamos o formatter de todos.
    if not raiz.handlers:
        raiz.addHandler(logging.StreamHandler(sys.stdout))
    for handler in raiz.handlers:
        handler.setFormatter(FormatadorJSON())
    for barulhento in ("botocore", "boto3", "urllib3"):
        logging.getLogger(barulhento).setLevel(logging.WARNING)


def registrar_metrica(
    nome: str, valor: float = 1, unidade: str = "Count", **dimensoes: str
) -> None:
    """Emite uma métrica no formato EMF (vira métrica no CloudWatch automaticamente)."""
    dimensoes = {"Servico": SERVICO, **dimensoes}
    documento = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": NAMESPACE_METRICAS,
                    "Dimensions": [list(dimensoes.keys())],
                    "Metrics": [{"Name": nome, "Unit": unidade}],
                }
            ],
        },
        nome: valor,
        **dimensoes,
    }
    print(json.dumps(documento), flush=True)
