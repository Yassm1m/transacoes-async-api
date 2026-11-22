#!/usr/bin/env bash
# Executado pelo LocalStack quando fica pronto. Espelha os recursos do template.yaml.
set -euo pipefail
REGIAO=us-east-1

awslocal dynamodb create-table \
  --table-name transacoes \
  --attribute-definitions AttributeName=pk,AttributeType=S \
  --key-schema AttributeName=pk,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST --region "$REGIAO"
awslocal dynamodb update-time-to-live --table-name transacoes \
  --time-to-live-specification Enabled=true,AttributeName=expira_em --region "$REGIAO"

awslocal sqs create-queue --queue-name transacoes-processamento-dlq --region "$REGIAO"
DLQ_ARN=$(awslocal sqs get-queue-attributes \
  --queue-url "http://localhost:4566/000000000000/transacoes-processamento-dlq" \
  --attribute-names QueueArn --query Attributes.QueueArn --output text --region "$REGIAO")
awslocal sqs create-queue --queue-name transacoes-processamento --region "$REGIAO" \
  --attributes "{\"VisibilityTimeout\":\"30\",\"RedrivePolicy\":\"{\\\"deadLetterTargetArn\\\":\\\"$DLQ_ARN\\\",\\\"maxReceiveCount\\\":\\\"3\\\"}\"}"

awslocal sns create-topic --name transacoes-eventos --region "$REGIAO"
awslocal s3 mb s3://transacoes-comprovantes --region "$REGIAO"

echo "Recursos LocalStack criados."
