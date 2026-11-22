"""Infra AWS simulada com moto, espelhando o template.yaml."""

import json
import os

import boto3
import pytest
from moto import mock_aws

from transacoes.adapters.outbound import aws

REGIAO = "us-east-1"


@pytest.fixture(autouse=True)
def _credenciais_falsas(monkeypatch):
    for var, valor in {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SESSION_TOKEN": "testing",
        "AWS_DEFAULT_REGION": REGIAO,
    }.items():
        monkeypatch.setenv(var, valor)
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    monkeypatch.delenv("AWS_PROFILE", raising=False)


@pytest.fixture
def infra():
    """Cria tabela, fila + DLQ, tópico (com fila assinante para inspecionar eventos) e bucket."""
    with mock_aws():
        aws.cliente.cache_clear()

        ddb = boto3.client("dynamodb", region_name=REGIAO)
        ddb.create_table(
            TableName="transacoes",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        sqs = boto3.client("sqs", region_name=REGIAO)
        dlq_url = sqs.create_queue(QueueName="processamento-dlq")["QueueUrl"]
        dlq_arn = sqs.get_queue_attributes(QueueUrl=dlq_url, AttributeNames=["QueueArn"])[
            "Attributes"
        ]["QueueArn"]
        fila_url = sqs.create_queue(
            QueueName="processamento",
            Attributes={
                "VisibilityTimeout": "0",  # em teste a mensagem volta na hora
                "RedrivePolicy": json.dumps(
                    {"deadLetterTargetArn": dlq_arn, "maxReceiveCount": "3"}
                ),
            },
        )["QueueUrl"]

        sns = boto3.client("sns", region_name=REGIAO)
        topico_arn = sns.create_topic(Name="eventos")["TopicArn"]
        assinante_url = sqs.create_queue(QueueName="assinante")["QueueUrl"]
        assinante_arn = sqs.get_queue_attributes(
            QueueUrl=assinante_url, AttributeNames=["QueueArn"]
        )["Attributes"]["QueueArn"]
        sns.subscribe(
            TopicArn=topico_arn,
            Protocol="sqs",
            Endpoint=assinante_arn,
            Attributes={"RawMessageDelivery": "true"},
        )

        s3 = boto3.client("s3", region_name=REGIAO)
        s3.create_bucket(Bucket="comprovantes")

        yield {
            "tabela": "transacoes",
            "fila_url": fila_url,
            "dlq_url": dlq_url,
            "topico_arn": topico_arn,
            "assinante_url": assinante_url,
            "bucket": "comprovantes",
            "sqs": sqs,
            "s3": s3,
            "ddb": ddb,
        }
        aws.cliente.cache_clear()


@pytest.fixture
def env_aws(infra, monkeypatch):
    monkeypatch.setenv("TABLE_NAME", infra["tabela"])
    monkeypatch.setenv("QUEUE_URL", infra["fila_url"])
    monkeypatch.setenv("TOPIC_ARN", infra["topico_arn"])
    monkeypatch.setenv("BUCKET_NAME", infra["bucket"])
    os.environ.pop("FALHA_SIMULADA_PERCENTUAL", None)
    return infra
