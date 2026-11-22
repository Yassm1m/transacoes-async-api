"""Fluxo ponta a ponta: HTTP -> DynamoDB -> SQS -> worker -> S3/SNS -> GET.

Também prova que uma falha persistente leva a mensagem para a DLQ.
"""

import json

import pytest
from fastapi.testclient import TestClient

from transacoes.adapters.inbound.http.app import create_app
from transacoes.adapters.inbound.sqs_worker import processar_lote
from transacoes.config import construir_container

pytestmark = pytest.mark.integration

CORPO = {
    "tipo": "PAGAMENTO",
    "valor": "89.90",
    "conta_origem": "0001-11111-1",
    "conta_destino": "boleto-123",
}


def receber_como_evento_lambda(sqs, fila_url: str) -> dict:
    """Lê do SQS e monta o evento no formato que o Lambda recebe."""
    resp = sqs.receive_message(
        QueueUrl=fila_url,
        MaxNumberOfMessages=10,
        MessageAttributeNames=["All"],
        AttributeNames=["All"],
    )
    records = []
    for m in resp.get("Messages", []):
        records.append(
            {
                "messageId": m["MessageId"],
                "receiptHandle": m["ReceiptHandle"],
                "body": m["Body"],
                "attributes": m.get("Attributes", {}),
                "messageAttributes": {
                    k: {"stringValue": v["StringValue"], "dataType": v["DataType"]}
                    for k, v in m.get("MessageAttributes", {}).items()
                },
            }
        )
    return {"Records": records}


def confirmar_sucessos(sqs, fila_url, evento, resultado):
    """Simula o Lambda: apaga da fila o que não foi reportado como falha."""
    falhas = {f["itemIdentifier"] for f in resultado["batchItemFailures"]}
    for r in evento["Records"]:
        if r["messageId"] not in falhas:
            sqs.delete_message(QueueUrl=fila_url, ReceiptHandle=r["receiptHandle"])


def test_fluxo_completo_aprovado(env_aws):
    container = construir_container()
    client = TestClient(create_app(container))
    sqs = env_aws["sqs"]

    # 1) Cliente envia (duas vezes, simulando retry de rede)
    headers = {"Idempotency-Key": "pedido-2026-0001", "X-Correlation-ID": "corr-e2e"}
    r1 = client.post("/transacoes", json=CORPO, headers=headers)
    r2 = client.post("/transacoes", json=CORPO, headers=headers)
    assert r1.status_code == r2.status_code == 202
    assert r1.json()["id"] == r2.json()["id"]
    transacao_id = r1.json()["id"]
    assert client.get(f"/transacoes/{transacao_id}").json()["status"] == "PENDENTE"

    # 2) Worker consome (2 mensagens: a original e o reenfileiramento do replay)
    evento = receber_como_evento_lambda(sqs, env_aws["fila_url"])
    assert len(evento["Records"]) == 2
    assert evento["Records"][0]["messageAttributes"]["correlation_id"]["stringValue"] == "corr-e2e"
    resultado = processar_lote(evento, container.processar_transacao)
    assert resultado == {"batchItemFailures": []}
    confirmar_sucessos(sqs, env_aws["fila_url"], evento, resultado)

    # 3) Cliente consulta o resultado
    corpo = client.get(f"/transacoes/{transacao_id}").json()
    assert corpo["status"] == "APROVADA"

    # 4) Comprovante no S3 e exatamente um evento no SNS (a duplicata foi ignorada)
    objetos = env_aws["s3"].list_objects_v2(Bucket=env_aws["bucket"])["Contents"]
    assert [o["Key"].endswith(f"{transacao_id}.json") for o in objetos] == [True]
    eventos = sqs.receive_message(QueueUrl=env_aws["assinante_url"], MaxNumberOfMessages=10)
    assert len(eventos["Messages"]) == 1
    assert json.loads(eventos["Messages"][0]["Body"])["status"] == "APROVADA"


def test_valor_acima_do_limite_e_recusado(env_aws, monkeypatch):
    monkeypatch.setenv("LIMITE_PAGAMENTO_CENTAVOS", "5000")
    container = construir_container()
    client = TestClient(create_app(container))

    transacao_id = client.post(
        "/transacoes", json=CORPO, headers={"Idempotency-Key": "pedido-2026-0002"}
    ).json()["id"]
    evento = receber_como_evento_lambda(env_aws["sqs"], env_aws["fila_url"])
    processar_lote(evento, container.processar_transacao)

    corpo = client.get(f"/transacoes/{transacao_id}").json()
    assert corpo["status"] == "RECUSADA"
    assert "R$ 50,00" in corpo["motivo_recusa"]


def test_falha_persistente_vai_para_dlq(env_aws, monkeypatch):
    monkeypatch.setenv("FALHA_SIMULADA_PERCENTUAL", "100")
    container = construir_container()
    container.processar_transacao._dormir = lambda _s: None
    client = TestClient(create_app(container))
    sqs = env_aws["sqs"]

    transacao_id = client.post(
        "/transacoes", json=CORPO, headers={"Idempotency-Key": "pedido-2026-0003"}
    ).json()["id"]

    # maxReceiveCount = 3: três recebimentos com falha e a mensagem vai para a DLQ.
    for _ in range(3):
        evento = receber_como_evento_lambda(sqs, env_aws["fila_url"])
        assert len(evento["Records"]) == 1
        resultado = processar_lote(evento, container.processar_transacao)
        assert len(resultado["batchItemFailures"]) == 1
        confirmar_sucessos(sqs, env_aws["fila_url"], evento, resultado)

    assert receber_como_evento_lambda(sqs, env_aws["fila_url"])["Records"] == []
    dlq = sqs.receive_message(QueueUrl=env_aws["dlq_url"])["Messages"]
    assert json.loads(dlq[0]["Body"]) == {"transacao_id": transacao_id}
    # Fica PROCESSANDO: um redrive da DLQ retoma de onde parou.
    assert client.get(f"/transacoes/{transacao_id}").json()["status"] == "PROCESSANDO"
