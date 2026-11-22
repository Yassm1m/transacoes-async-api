"""Adapters de mensageria e armazenamento: SQS, SNS e S3."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from transacoes.adapters.outbound.aws import cliente
from transacoes.domain.eventos import TransacaoProcessada
from transacoes.domain.transacao import Transacao
from transacoes.ports import (
    ArmazenamentoDeComprovantes,
    FilaDeProcessamento,
    PublicadorDeEventos,
)


class FilaSQS(FilaDeProcessamento):
    def __init__(self, url_fila: str, client=None) -> None:
        self._url = url_fila
        self._client = client or cliente("sqs")

    def enviar(self, transacao_id: str, correlation_id: str | None = None) -> None:
        atributos = {}
        if correlation_id:
            atributos["correlation_id"] = {"DataType": "String", "StringValue": correlation_id}
        self._client.send_message(
            QueueUrl=self._url,
            MessageBody=json.dumps({"transacao_id": transacao_id}),
            MessageAttributes=atributos,
        )


class PublicadorSNS(PublicadorDeEventos):
    def __init__(self, arn_topico: str, client=None) -> None:
        self._arn = arn_topico
        self._client = client or cliente("sns")

    def publicar(self, evento: TransacaoProcessada) -> None:
        # Atributos permitem que assinantes filtrem (ex.: só RECUSADA).
        self._client.publish(
            TopicArn=self._arn,
            Message=json.dumps(evento.para_dict()),
            MessageAttributes={
                "evento": {"DataType": "String", "StringValue": evento.nome},
                "status": {"DataType": "String", "StringValue": evento.status},
                "tipo": {"DataType": "String", "StringValue": evento.tipo},
            },
        )


class ComprovantesS3(ArmazenamentoDeComprovantes):
    def __init__(self, bucket: str, client=None) -> None:
        self._bucket = bucket
        self._client = client or cliente("s3")

    def salvar(self, transacao: Transacao) -> str:
        data = transacao.criada_em.astimezone(UTC)
        chave = f"comprovantes/{data:%Y/%m/%d}/{transacao.id}.json"
        corpo = {
            "transacao_id": transacao.id,
            "tipo": transacao.tipo.value,
            "valor_centavos": transacao.valor_centavos,
            "conta_origem": transacao.conta_origem,
            "conta_destino": transacao.conta_destino,
            "status": transacao.status.value,
            "motivo_recusa": transacao.motivo_recusa,
            "criada_em": transacao.criada_em.isoformat(),
            "emitido_em": datetime.now(UTC).isoformat(),
        }
        self._client.put_object(
            Bucket=self._bucket,
            Key=chave,
            Body=json.dumps(corpo, ensure_ascii=False).encode(),
            ContentType="application/json",
            ServerSideEncryption="AES256",
        )
        return f"s3://{self._bucket}/{chave}"
