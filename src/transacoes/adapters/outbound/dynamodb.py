"""Repositório DynamoDB (single-table).

Itens:
  pk = "TRANSACAO#<id>"     -> dados da transação (+ versao para lock otimista)
  pk = "IDEMPOTENCIA#<key>" -> hash da requisição + id da transação (com TTL)

A criação usa TransactWriteItems: transação e chave de idempotência são gravadas
juntas, ou nenhuma das duas é gravada. Isso fecha a corrida de duas requisições
iguais chegando ao mesmo tempo.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

from transacoes.adapters.outbound.aws import cliente
from transacoes.domain.transacao import StatusTransacao, TipoTransacao, Transacao
from transacoes.ports import (
    ChaveDeIdempotenciaJaUtilizada,
    ConflitoDeConcorrencia,
    RegistroDeIdempotencia,
    RepositorioDeTransacoes,
)

_ser = TypeSerializer()
_des = TypeDeserializer()

TTL_IDEMPOTENCIA_S = 24 * 60 * 60


def _pk_transacao(transacao_id: str) -> str:
    return f"TRANSACAO#{transacao_id}"


def _pk_idempotencia(chave: str) -> str:
    return f"IDEMPOTENCIA#{chave}"


def _serializar(item: dict[str, Any]) -> dict[str, Any]:
    return {k: _ser.serialize(v) for k, v in item.items() if v is not None}


def _desserializar(item: dict[str, Any]) -> dict[str, Any]:
    return {k: _des.deserialize(v) for k, v in item.items()}


class RepositorioDynamoDB(RepositorioDeTransacoes):
    def __init__(self, nome_tabela: str, client=None) -> None:
        self._tabela = nome_tabela
        self._client = client or cliente("dynamodb")

    # ------------------------------------------------------------- mapeamento
    @staticmethod
    def _para_item(t: Transacao) -> dict[str, Any]:
        return {
            "pk": _pk_transacao(t.id),
            "id": t.id,
            "tipo": t.tipo.value,
            "valor_centavos": t.valor_centavos,
            "conta_origem": t.conta_origem,
            "conta_destino": t.conta_destino,
            "descricao": t.descricao,
            "status": t.status.value,
            "motivo_recusa": t.motivo_recusa,
            "criada_em": t.criada_em.isoformat(),
            "atualizada_em": t.atualizada_em.isoformat(),
            "versao": t.versao,
        }

    @staticmethod
    def _de_item(item: dict[str, Any]) -> Transacao:
        return Transacao(
            id=item["id"],
            tipo=TipoTransacao(item["tipo"]),
            valor_centavos=int(item["valor_centavos"]),
            conta_origem=item["conta_origem"],
            conta_destino=item["conta_destino"],
            descricao=item.get("descricao"),
            status=StatusTransacao(item["status"]),
            motivo_recusa=item.get("motivo_recusa"),
            criada_em=datetime.fromisoformat(item["criada_em"]),
            atualizada_em=datetime.fromisoformat(item["atualizada_em"]),
            versao=int(item["versao"]),
        )

    # ------------------------------------------------------------------ porta
    def criar(self, transacao: Transacao, idempotencia: RegistroDeIdempotencia) -> None:
        item_idem = {
            "pk": _pk_idempotencia(idempotencia.chave),
            "hash_requisicao": idempotencia.hash_requisicao,
            "transacao_id": idempotencia.transacao_id,
            "expira_em": int(time.time()) + TTL_IDEMPOTENCIA_S,
        }
        try:
            self._client.transact_write_items(
                TransactItems=[
                    {
                        "Put": {
                            "TableName": self._tabela,
                            "Item": _serializar(item_idem),
                            "ConditionExpression": "attribute_not_exists(pk)",
                        }
                    },
                    {
                        "Put": {
                            "TableName": self._tabela,
                            "Item": _serializar(self._para_item(transacao)),
                            "ConditionExpression": "attribute_not_exists(pk)",
                        }
                    },
                ]
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "TransactionCanceledException":
                raise
            existente = self._buscar_idempotencia(idempotencia.chave)
            if existente is None:
                raise
            raise ChaveDeIdempotenciaJaUtilizada(existente) from exc

    def buscar(self, transacao_id: str) -> Transacao | None:
        resp = self._client.get_item(
            TableName=self._tabela,
            Key={"pk": {"S": _pk_transacao(transacao_id)}},
            ConsistentRead=True,
        )
        item = resp.get("Item")
        return self._de_item(_desserializar(item)) if item else None

    def atualizar(self, transacao: Transacao, versao_esperada: int) -> None:
        try:
            self._client.put_item(
                TableName=self._tabela,
                Item=_serializar(self._para_item(transacao)),
                ConditionExpression="versao = :v",
                ExpressionAttributeValues={":v": {"N": str(versao_esperada)}},
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ConflitoDeConcorrencia(transacao.id) from exc
            raise

    # --------------------------------------------------------------- privados
    def _buscar_idempotencia(self, chave: str) -> RegistroDeIdempotencia | None:
        resp = self._client.get_item(
            TableName=self._tabela,
            Key={"pk": {"S": _pk_idempotencia(chave)}},
            ConsistentRead=True,
        )
        item = resp.get("Item")
        if not item:
            return None
        dados = _desserializar(item)
        return RegistroDeIdempotencia(
            chave=chave,
            hash_requisicao=dados["hash_requisicao"],
            transacao_id=dados["transacao_id"],
        )
