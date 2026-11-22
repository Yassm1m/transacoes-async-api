"""Testes do worker SQS, observabilidade, configuração e processador simulado."""

import json
import logging

import pytest

from transacoes.adapters.inbound import sqs_worker
from transacoes.adapters.inbound.sqs_worker import processar_lote
from transacoes.adapters.outbound.processador_simulado import ProcessadorSimulado
from transacoes.application.excecoes import ErroTransitorio
from transacoes.config import Configuracoes, construir_container
from transacoes.domain.transacao import StatusTransacao, Transacao
from transacoes.ports import FalhaTemporariaNoProcessador
from transacoes.shared.observabilidade import (
    FormatadorJSON,
    configurar_logs,
    definir_correlation_id,
    registrar_metrica,
)


def record(message_id: str, body, correlation_id: str | None = None, receive_count: int = 1):
    r = {
        "messageId": message_id,
        "body": body if isinstance(body, str) else json.dumps(body),
        "attributes": {"ApproximateReceiveCount": str(receive_count)},
        "messageAttributes": {},
    }
    if correlation_id:
        r["messageAttributes"]["correlation_id"] = {"stringValue": correlation_id}
    return r


# ==================================================================== worker


class TestWorker:
    def test_processa_lote_com_sucesso(self, criar, processar, repositorio, comando):
        ids = [criar.executar(comando, f"chave-{i:04d}").transacao.id for i in range(3)]
        evento = {"Records": [record(f"m{i}", {"transacao_id": t}) for i, t in enumerate(ids)]}

        assert processar_lote(evento, processar) == {"batchItemFailures": []}
        assert all(repositorio.buscar(t).status is StatusTransacao.APROVADA for t in ids)

    def test_mensagem_malformada_e_descartada_sem_travar_o_lote(self, processar):
        evento = {"Records": [record("m1", "isso nao e json"), record("m2", {"outro": 1})]}
        assert processar_lote(evento, processar) == {"batchItemFailures": []}

    def test_so_as_mensagens_com_falha_voltam_para_a_fila(self, criar, comando):
        ok = criar.executar(comando, "chave-0001").transacao.id

        class CasoDeUsoFalso:
            def executar(self, transacao_id):
                if transacao_id == "transitoria":
                    raise ErroTransitorio("timeout")
                if transacao_id == "bug":
                    raise KeyError("inesperado")
                return "PROCESSADA"

        evento = {
            "Records": [
                record("m-ok", {"transacao_id": ok}, correlation_id="c1"),
                record("m-trans", {"transacao_id": "transitoria"}, receive_count=2),
                record("m-bug", {"transacao_id": "bug"}),
            ]
        }
        resultado = processar_lote(evento, CasoDeUsoFalso())
        assert resultado == {
            "batchItemFailures": [{"itemIdentifier": "m-trans"}, {"itemIdentifier": "m-bug"}]
        }

    def test_handler_constroi_container_do_ambiente(self, monkeypatch):
        monkeypatch.delenv("TABLE_NAME", raising=False)
        monkeypatch.setattr(sqs_worker, "_processar", None)
        assert sqs_worker.handler({"Records": []}) == {"batchItemFailures": []}
        assert sqs_worker._processar is not None


# ============================================================ observabilidade


class TestObservabilidade:
    def test_log_json_com_correlation_id_e_campos_extras(self):
        definir_correlation_id("corr-xyz")
        registro = logging.LogRecord("teste", logging.INFO, __file__, 1, "olá %s", ("mundo",), None)
        registro.transacao_id = "t-1"

        saida = json.loads(FormatadorJSON().format(registro))

        assert saida["mensagem"] == "olá mundo"
        assert saida["nivel"] == "INFO"
        assert saida["correlation_id"] == "corr-xyz"
        assert saida["transacao_id"] == "t-1"

    def test_log_json_inclui_excecao(self):
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            registro = logging.LogRecord("t", logging.ERROR, __file__, 1, "x", (), sys.exc_info())
        assert "ValueError: boom" in json.loads(FormatadorJSON().format(registro))["excecao"]

    def test_configurar_logs_aplica_formatador(self):
        configurar_logs("DEBUG")
        raiz = logging.getLogger()
        assert raiz.level == logging.DEBUG
        assert all(isinstance(h.formatter, FormatadorJSON) for h in raiz.handlers)

    def test_metrica_emf(self, capsys):
        registrar_metrica("TransacoesFinalizadas", 2, Resultado="PROCESSADA")
        doc = json.loads(capsys.readouterr().out)
        metrica = doc["_aws"]["CloudWatchMetrics"][0]
        assert metrica["Namespace"] == "TransacoesAsync"
        assert metrica["Metrics"] == [{"Name": "TransacoesFinalizadas", "Unit": "Count"}]
        assert doc["TransacoesFinalizadas"] == 2
        assert doc["Resultado"] == "PROCESSADA"


# ============================================================== configuração


class TestConfiguracao:
    def test_modo_memoria_processa_na_hora(self, comando):
        container = construir_container(Configuracoes())
        t = container.criar_transacao.executar(comando, "chave-0001").transacao
        assert container.consultar_transacao.executar(t.id).status is StatusTransacao.APROVADA

    def test_le_variaveis_de_ambiente(self, monkeypatch):
        monkeypatch.setenv("TABLE_NAME", "tabela")
        monkeypatch.setenv("FALHA_SIMULADA_PERCENTUAL", "12.5")
        monkeypatch.setenv("LIMITE_PAGAMENTO_CENTAVOS", "500")
        cfg = Configuracoes.do_ambiente()
        assert cfg.usa_aws
        assert cfg.falha_simulada_percentual == 12.5
        assert cfg.limite_pagamento_centavos == 500

    def test_modo_aws_exige_todas_as_configuracoes(self):
        with pytest.raises(RuntimeError, match="url_fila"):
            construir_container(Configuracoes(tabela="t"))


# ====================================================== processador simulado


class TestProcessadorSimulado:
    def t(self):
        return Transacao.criar("PAGAMENTO", 100, "A", "B")

    def test_sem_falha(self):
        ProcessadorSimulado(0).liquidar(self.t())

    def test_falha_conforme_percentual(self):
        with pytest.raises(FalhaTemporariaNoProcessador):
            ProcessadorSimulado(50, sortear=lambda: 0.1).liquidar(self.t())
        ProcessadorSimulado(50, sortear=lambda: 0.9).liquidar(self.t())

    def test_percentual_invalido(self):
        with pytest.raises(ValueError):
            ProcessadorSimulado(150)
