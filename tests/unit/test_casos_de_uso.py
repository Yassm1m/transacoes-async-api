from dataclasses import replace

import pytest

from tests.conftest import RETRY_RAPIDO, nao_dormir
from transacoes.adapters.outbound.memoria import FilaEmMemoria
from transacoes.application.casos_de_uso import (
    ConsultarTransacao,
    CriarTransacao,
    ProcessarTransacao,
    ResultadoProcessamento,
)
from transacoes.application.excecoes import (
    ConflitoDeIdempotencia,
    ErroTransitorio,
    TransacaoNaoEncontrada,
)
from transacoes.domain.excecoes import TransacaoInvalida
from transacoes.domain.politica import PoliticaDeAprovacao
from transacoes.domain.transacao import StatusTransacao
from transacoes.ports import (
    ConflitoDeConcorrencia,
    FalhaTemporariaNoProcessador,
    ProcessadorDePagamento,
)

# ============================================================ CriarTransacao


class TestCriarTransacao:
    def test_cria_pendente_e_enfileira(self, criar, fila, repositorio, comando):
        resultado = criar.executar(comando, "chave-0001", correlation_id="corr-1")

        assert not resultado.reaproveitada
        assert resultado.transacao.status is StatusTransacao.PENDENTE
        assert repositorio.buscar(resultado.transacao.id) is not None
        assert fila.mensagens == [
            {"transacao_id": resultado.transacao.id, "correlation_id": "corr-1"}
        ]

    def test_mesma_chave_e_mesmo_corpo_devolve_a_original(self, criar, comando, fila):
        primeira = criar.executar(comando, "chave-0001")
        segunda = criar.executar(comando, "chave-0001")

        assert segunda.reaproveitada
        assert segunda.transacao.id == primeira.transacao.id
        # Ainda PENDENTE -> reenfileira (autocura); o worker é idempotente.
        assert len(fila.mensagens) == 2

    def test_replay_de_transacao_finalizada_nao_reenfileira(self, criar, processar, comando, fila):
        primeira = criar.executar(comando, "chave-0001")
        processar.executar(primeira.transacao.id)

        segunda = criar.executar(comando, "chave-0001")

        assert segunda.transacao.status is StatusTransacao.APROVADA
        assert len(fila.mensagens) == 1

    def test_mesma_chave_com_corpo_diferente_e_conflito(self, criar, comando):
        criar.executar(comando, "chave-0001")
        with pytest.raises(ConflitoDeIdempotencia):
            criar.executar(replace(comando, valor_centavos=999), "chave-0001")

    def test_hash_ignora_espacos_nas_contas(self, comando):
        com_espacos = replace(comando, conta_origem=f"  {comando.conta_origem} ")
        assert com_espacos.hash() == comando.hash()

    def test_valida_regras_de_dominio_antes_de_gravar(self, criar, comando, repositorio):
        invalido = replace(comando, conta_destino=comando.conta_origem)
        with pytest.raises(TransacaoInvalida):
            criar.executar(invalido, "chave-0001")
        assert repositorio._transacoes == {}

    def test_retenta_envio_para_fila(self, repositorio, comando):
        class FilaInstavel(FilaEmMemoria):
            falhas = 2

            def enviar(self, transacao_id, correlation_id=None):
                if self.falhas:
                    self.falhas -= 1
                    raise ConnectionError("sqs fora")
                super().enviar(transacao_id, correlation_id)

        fila = FilaInstavel()
        criar = CriarTransacao(repositorio, fila, politica_retry=RETRY_RAPIDO, dormir=nao_dormir)
        criar.executar(comando, "chave-0001")
        assert len(fila.mensagens) == 1


# ======================================================== ConsultarTransacao


class TestConsultarTransacao:
    def test_encontra(self, criar, repositorio, comando):
        criada = criar.executar(comando, "chave-0001").transacao
        assert ConsultarTransacao(repositorio).executar(criada.id).id == criada.id

    def test_nao_encontrada(self, repositorio):
        with pytest.raises(TransacaoNaoEncontrada):
            ConsultarTransacao(repositorio).executar("nao-existe")


# ========================================================= ProcessarTransacao


class ProcessadorFalhando(ProcessadorDePagamento):
    def __init__(self, falhas: int) -> None:
        self.falhas = falhas
        self.chamadas = 0

    def liquidar(self, transacao):
        self.chamadas += 1
        if self.chamadas <= self.falhas:
            raise FalhaTemporariaNoProcessador("timeout")


def montar_processar(repositorio, comprovantes, publicador, **kw) -> ProcessarTransacao:
    return ProcessarTransacao(
        repositorio=repositorio,
        comprovantes=comprovantes,
        publicador=publicador,
        politica_retry=RETRY_RAPIDO,
        dormir=nao_dormir,
        **kw,
    )


class TestProcessarTransacao:
    def test_aprova_gera_comprovante_e_publica_evento(
        self, criar, processar, repositorio, comprovantes, publicador, comando
    ):
        t = criar.executar(comando, "chave-0001").transacao

        assert processar.executar(t.id) == ResultadoProcessamento.PROCESSADA

        salva = repositorio.buscar(t.id)
        assert salva.status is StatusTransacao.APROVADA
        assert t.id in comprovantes.comprovantes
        assert [e.status for e in publicador.eventos] == ["APROVADA"]
        assert publicador.eventos[0].comprovante_uri.endswith(f"{t.id}.json")

    def test_recusa_acima_do_limite(self, criar, repositorio, comprovantes, publicador, comando):
        t = criar.executar(comando, "chave-0001").transacao
        processar = montar_processar(
            repositorio,
            comprovantes,
            publicador,
            processador=ProcessadorFalhando(0),
            politica=PoliticaDeAprovacao(limite_transferencia_centavos=100),
        )

        processar.executar(t.id)

        salva = repositorio.buscar(t.id)
        assert salva.status is StatusTransacao.RECUSADA
        assert "limite" in salva.motivo_recusa
        assert publicador.eventos[0].status == "RECUSADA"

    def test_mensagem_duplicada_e_ignorada(self, criar, processar, publicador, comando):
        t = criar.executar(comando, "chave-0001").transacao
        processar.executar(t.id)

        assert processar.executar(t.id) == ResultadoProcessamento.IGNORADA_JA_FINALIZADA
        assert len(publicador.eventos) == 1

    def test_transacao_inexistente_e_descartada(self, processar):
        assert processar.executar("fantasma") == ResultadoProcessamento.IGNORADA_NAO_ENCONTRADA

    def test_falha_transitoria_recupera_com_retry_local(
        self, criar, repositorio, comprovantes, publicador, comando
    ):
        t = criar.executar(comando, "chave-0001").transacao
        externo = ProcessadorFalhando(falhas=2)
        processar = montar_processar(repositorio, comprovantes, publicador, processador=externo)

        processar.executar(t.id)

        assert externo.chamadas == 3
        assert repositorio.buscar(t.id).status is StatusTransacao.APROVADA

    def test_falha_persistente_vira_erro_transitorio_e_permite_retomar(
        self, criar, repositorio, comprovantes, publicador, comando
    ):
        t = criar.executar(comando, "chave-0001").transacao
        externo = ProcessadorFalhando(falhas=3)
        processar = montar_processar(repositorio, comprovantes, publicador, processador=externo)

        with pytest.raises(ErroTransitorio):
            processar.executar(t.id)
        assert repositorio.buscar(t.id).status is StatusTransacao.PROCESSANDO
        assert publicador.eventos == []

        # A mensagem volta do SQS; na nova tentativa o externo já respondeu.
        assert processar.executar(t.id) == ResultadoProcessamento.PROCESSADA
        assert repositorio.buscar(t.id).status is StatusTransacao.APROVADA

    def test_falha_ao_gravar_comprovante_nao_finaliza_status(
        self, criar, repositorio, publicador, comando
    ):
        class ComprovantesQuebrados:
            def salvar(self, transacao):
                raise RuntimeError("s3 fora")

        t = criar.executar(comando, "chave-0001").transacao
        processar = montar_processar(
            repositorio,
            ComprovantesQuebrados(),
            publicador,
            processador=ProcessadorFalhando(0),
        )

        with pytest.raises(ErroTransitorio):
            processar.executar(t.id)
        assert repositorio.buscar(t.id).status is StatusTransacao.PROCESSANDO

    def test_conflito_ao_iniciar_significa_que_outra_execucao_assumiu(
        self, criar, repositorio, comprovantes, publicador, comando, monkeypatch
    ):
        t = criar.executar(comando, "chave-0001").transacao
        processar = montar_processar(
            repositorio, comprovantes, publicador, processador=ProcessadorFalhando(0)
        )

        def conflito(*_a, **_k):
            raise ConflitoDeConcorrencia(t.id)

        monkeypatch.setattr(repositorio, "atualizar", conflito)
        assert processar.executar(t.id) == ResultadoProcessamento.IGNORADA_CONCORRENCIA
        assert publicador.eventos == []

    def test_conflito_ao_finalizar(self, criar, repositorio, comprovantes, publicador, comando):
        t = criar.executar(comando, "chave-0001").transacao
        original = repositorio.atualizar
        chamadas = {"n": 0}

        def segunda_chamada_conflita(transacao, versao_esperada):
            chamadas["n"] += 1
            if chamadas["n"] == 2:
                raise ConflitoDeConcorrencia(transacao.id)
            original(transacao, versao_esperada)

        repositorio.atualizar = segunda_chamada_conflita
        processar = montar_processar(
            repositorio, comprovantes, publicador, processador=ProcessadorFalhando(0)
        )
        assert processar.executar(t.id) == ResultadoProcessamento.IGNORADA_CONCORRENCIA


def test_repositorio_em_memoria_detecta_versao_desatualizada(repositorio, criar, comando):
    t = criar.executar(comando, "chave-0001").transacao
    t.iniciar_processamento()
    with pytest.raises(ConflitoDeConcorrencia):
        repositorio.atualizar(t, versao_esperada=99)
