"""Configuração via variáveis de ambiente e composição das dependências (composition root).

É o único lugar que sabe quais adapters concretos ligar em quais portas.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from transacoes.application.casos_de_uso import (
    ConsultarTransacao,
    CriarTransacao,
    ProcessarTransacao,
)
from transacoes.domain.politica import LIMITE_PADRAO_CENTAVOS, PoliticaDeAprovacao


@dataclass(frozen=True)
class Configuracoes:
    tabela: str | None = None
    url_fila: str | None = None
    arn_topico: str | None = None
    bucket: str | None = None
    falha_simulada_percentual: float = 0.0
    limite_pagamento_centavos: int = LIMITE_PADRAO_CENTAVOS
    limite_transferencia_centavos: int = LIMITE_PADRAO_CENTAVOS
    extras: dict = field(default_factory=dict)

    @property
    def usa_aws(self) -> bool:
        return bool(self.tabela)

    @classmethod
    def do_ambiente(cls) -> Configuracoes:
        return cls(
            tabela=os.getenv("TABLE_NAME"),
            url_fila=os.getenv("QUEUE_URL"),
            arn_topico=os.getenv("TOPIC_ARN"),
            bucket=os.getenv("BUCKET_NAME"),
            falha_simulada_percentual=float(os.getenv("FALHA_SIMULADA_PERCENTUAL", "0")),
            limite_pagamento_centavos=int(
                os.getenv("LIMITE_PAGAMENTO_CENTAVOS", LIMITE_PADRAO_CENTAVOS)
            ),
            limite_transferencia_centavos=int(
                os.getenv("LIMITE_TRANSFERENCIA_CENTAVOS", LIMITE_PADRAO_CENTAVOS)
            ),
        )


@dataclass
class Container:
    criar_transacao: CriarTransacao
    consultar_transacao: ConsultarTransacao
    processar_transacao: ProcessarTransacao


def construir_container(cfg: Configuracoes | None = None) -> Container:
    cfg = cfg or Configuracoes.do_ambiente()

    # Imports locais: o modo memória não precisa carregar boto3.
    from transacoes.adapters.outbound.processador_simulado import ProcessadorSimulado

    politica = PoliticaDeAprovacao(
        limite_pagamento_centavos=cfg.limite_pagamento_centavos,
        limite_transferencia_centavos=cfg.limite_transferencia_centavos,
    )
    processador = ProcessadorSimulado(percentual_falha=cfg.falha_simulada_percentual)

    if cfg.usa_aws:
        from transacoes.adapters.outbound.dynamodb import RepositorioDynamoDB
        from transacoes.adapters.outbound.mensageria import (
            ComprovantesS3,
            FilaSQS,
            PublicadorSNS,
        )

        _exigir(cfg, "url_fila", "arn_topico", "bucket")
        repositorio = RepositorioDynamoDB(cfg.tabela)
        fila = FilaSQS(cfg.url_fila)
        processar = ProcessarTransacao(
            repositorio=repositorio,
            processador=processador,
            comprovantes=ComprovantesS3(cfg.bucket),
            publicador=PublicadorSNS(cfg.arn_topico),
            politica=politica,
        )
    else:
        # Modo local sem AWS: a "fila" processa na mesma hora, para facilitar a demo.
        from transacoes.adapters.outbound.memoria import (
            ComprovantesEmMemoria,
            FilaEmMemoria,
            PublicadorEmMemoria,
            RepositorioEmMemoria,
        )

        repositorio = RepositorioEmMemoria()
        fila = FilaEmMemoria()
        processar = ProcessarTransacao(
            repositorio=repositorio,
            processador=processador,
            comprovantes=ComprovantesEmMemoria(),
            publicador=PublicadorEmMemoria(),
            politica=politica,
        )
        fila.consumidor = processar.executar

    return Container(
        criar_transacao=CriarTransacao(repositorio, fila),
        consultar_transacao=ConsultarTransacao(repositorio),
        processar_transacao=processar,
    )


def _exigir(cfg: Configuracoes, *campos: str) -> None:
    faltando = [c for c in campos if not getattr(cfg, c)]
    if faltando:
        raise RuntimeError(f"Configuração ausente: {', '.join(faltando)}")
