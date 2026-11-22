"""Faz o papel do "event source mapping" do Lambda em ambiente local.

Lê mensagens do SQS (LocalStack ou AWS real), monta o evento no formato do Lambda,
chama o mesmo handler usado em produção e apaga só as mensagens processadas com
sucesso (as que falharam voltam para a fila e, após 3 tentativas, vão para a DLQ).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import boto3

from transacoes.adapters.inbound.sqs_worker import handler
from transacoes.shared.observabilidade import configurar_logs


def para_evento_lambda(mensagens: list[dict]) -> dict:
    return {
        "Records": [
            {
                "messageId": m["MessageId"],
                "receiptHandle": m["ReceiptHandle"],
                "body": m["Body"],
                "attributes": m.get("Attributes", {}),
                "messageAttributes": {
                    k: {"stringValue": v.get("StringValue"), "dataType": v["DataType"]}
                    for k, v in m.get("MessageAttributes", {}).items()
                },
            }
            for m in mensagens
        ]
    }


def main() -> None:
    configurar_logs()
    sqs = boto3.client("sqs")
    url = os.environ["QUEUE_URL"]
    print(f"worker local ouvindo {url}", flush=True)

    while True:
        resp = sqs.receive_message(
            QueueUrl=url,
            MaxNumberOfMessages=10,
            WaitTimeSeconds=10,  # long polling
            AttributeNames=["All"],
            MessageAttributeNames=["All"],
        )
        mensagens = resp.get("Messages", [])
        if not mensagens:
            continue

        evento = para_evento_lambda(mensagens)
        resultado = handler(evento)
        falhas = {f["itemIdentifier"] for f in resultado["batchItemFailures"]}
        for r in evento["Records"]:
            if r["messageId"] not in falhas:
                sqs.delete_message(QueueUrl=url, ReceiptHandle=r["receiptHandle"])


if __name__ == "__main__":
    main()
