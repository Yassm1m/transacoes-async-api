import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from transacoes.adapters.outbound.dynamodb import RepositorioDynamoDB
from transacoes.adapters.outbound.mensageria import ComprovantesS3, FilaSQS, PublicadorSNS
from transacoes.domain.eventos import TransacaoProcessada
from transacoes.domain.transacao import StatusTransacao, Transacao
from transacoes.ports import (
    ChaveDeIdempotenciaJaUtilizada,
    ConflitoDeConcorrencia,
    RegistroDeIdempotencia,
)

pytestmark = pytest.mark.integration


def nova_transacao() -> Transacao:
    return Transacao.criar("TRANSFERENCIA", 15_075, "A", "B", descricao="teste")


def registro(t: Transacao, chave="chave-0001", hash_="h1") -> RegistroDeIdempotencia:
    return RegistroDeIdempotencia(chave=chave, hash_requisicao=hash_, transacao_id=t.id)


class TestRepositorioDynamoDB:
    def test_cria_e_busca_preservando_todos_os_campos(self, infra):
        repo = RepositorioDynamoDB(infra["tabela"])
        t = nova_transacao()

        repo.criar(t, registro(t))
        lida = repo.buscar(t.id)

        assert lida == t

    def test_buscar_inexistente(self, infra):
        assert RepositorioDynamoDB(infra["tabela"]).buscar("nada") is None

    def test_chave_repetida_e_atomica_nao_cria_segunda_transacao(self, infra):
        repo = RepositorioDynamoDB(infra["tabela"])
        t1, t2 = nova_transacao(), nova_transacao()
        repo.criar(t1, registro(t1))

        with pytest.raises(ChaveDeIdempotenciaJaUtilizada) as exc:
            repo.criar(t2, registro(t2))

        assert exc.value.registro.transacao_id == t1.id
        assert repo.buscar(t2.id) is None  # a transação também não foi gravada

    def test_grava_ttl_na_chave_de_idempotencia(self, infra):
        repo = RepositorioDynamoDB(infra["tabela"])
        t = nova_transacao()
        repo.criar(t, registro(t))
        item = infra["ddb"].get_item(
            TableName=infra["tabela"], Key={"pk": {"S": "IDEMPOTENCIA#chave-0001"}}
        )["Item"]
        assert int(item["expira_em"]["N"]) > 0

    def test_lock_otimista(self, infra):
        repo = RepositorioDynamoDB(infra["tabela"])
        t = nova_transacao()
        repo.criar(t, registro(t))

        t.iniciar_processamento()
        repo.atualizar(t, versao_esperada=1)
        assert repo.buscar(t.id).status is StatusTransacao.PROCESSANDO

        with pytest.raises(ConflitoDeConcorrencia):
            repo.atualizar(t, versao_esperada=1)

    def test_requisicoes_simultaneas_com_mesma_chave_criam_uma_so(self, infra):
        repo = RepositorioDynamoDB(infra["tabela"])

        def tentar(_):
            t = nova_transacao()
            try:
                repo.criar(t, registro(t))
                return "criou"
            except ChaveDeIdempotenciaJaUtilizada:
                return "replay"

        with ThreadPoolExecutor(max_workers=8) as pool:
            resultados = list(pool.map(tentar, range(8)))

        assert resultados.count("criou") == 1
        assert resultados.count("replay") == 7


class TestMensageria:
    def test_fila_envia_com_correlation_id(self, infra):
        FilaSQS(infra["fila_url"]).enviar("t-1", correlation_id="corr-1")
        msg = infra["sqs"].receive_message(
            QueueUrl=infra["fila_url"], MessageAttributeNames=["All"]
        )["Messages"][0]

        assert json.loads(msg["Body"]) == {"transacao_id": "t-1"}
        assert msg["MessageAttributes"]["correlation_id"]["StringValue"] == "corr-1"

    def test_publicador_entrega_evento_para_assinantes(self, infra):
        t = nova_transacao()
        t.iniciar_processamento()
        t.aprovar()

        PublicadorSNS(infra["topico_arn"]).publicar(TransacaoProcessada.de(t, "s3://x"))

        msg = infra["sqs"].receive_message(QueueUrl=infra["assinante_url"])["Messages"][0]
        evento = json.loads(msg["Body"])
        assert evento["transacao_id"] == t.id
        assert evento["status"] == "APROVADA"

    def test_comprovante_salvo_no_s3_criptografado(self, infra):
        t = nova_transacao()
        uri = ComprovantesS3(infra["bucket"]).salvar(t)

        chave = uri.removeprefix(f"s3://{infra['bucket']}/")
        obj = infra["s3"].get_object(Bucket=infra["bucket"], Key=chave)
        assert chave.startswith("comprovantes/") and chave.endswith(f"{t.id}.json")
        assert obj["ServerSideEncryption"] == "AES256"
        assert json.loads(obj["Body"].read())["transacao_id"] == t.id
