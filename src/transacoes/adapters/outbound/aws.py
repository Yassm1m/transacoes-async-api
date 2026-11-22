"""Fábrica de clientes boto3 com retry adaptativo do próprio SDK.

O endpoint é lido de AWS_ENDPOINT_URL (suporte nativo do boto3), o que permite
apontar para o LocalStack sem mudar código.
"""

from __future__ import annotations

from functools import cache

import boto3
from botocore.config import Config

CONFIG_PADRAO = Config(
    retries={"mode": "adaptive", "max_attempts": 5},
    connect_timeout=2,
    read_timeout=5,
)


@cache
def cliente(servico: str):
    return boto3.client(servico, config=CONFIG_PADRAO)
