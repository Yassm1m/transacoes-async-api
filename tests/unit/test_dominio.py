import pytest

from transacoes.domain.eventos import TransacaoProcessada
from transacoes.domain.excecoes import TransacaoInvalida, TransicaoDeStatusInvalida
from transacoes.domain.politica import PoliticaDeAprovacao
from transacoes.domain.transacao import StatusTransacao, TipoTransacao, Transacao


def nova(**kw) -> Transacao:
    dados = {
        "tipo": TipoTransacao.PAGAMENTO,
        "valor_centavos": 1000,
        "conta_origem": "A",
        "conta_destino": "B",
    }
    dados.update(kw)
    return Transacao.criar(**dados)


class TestCriacao:
    def test_cria_pendente_com_versao_1(self):
        t = nova()
        assert t.status is StatusTransacao.PENDENTE
        assert t.versao == 1
        assert t.id
        assert not t.finalizada

    def test_aceita_tipo_como_string_e_remove_espacos(self):
        t = nova(tipo="TRANSFERENCIA", conta_origem="  A ", conta_destino=" B")
        assert t.tipo is TipoTransacao.TRANSFERENCIA
        assert (t.conta_origem, t.conta_destino) == ("A", "B")

    @pytest.mark.parametrize(
        ("campos", "mensagem"),
        [
            ({"valor_centavos": 0}, "maior que zero"),
            ({"valor_centavos": -5}, "maior que zero"),
            ({"valor_centavos": 10.5}, "inteiro em centavos"),
            ({"valor_centavos": True}, "inteiro em centavos"),
            ({"conta_origem": " "}, "origem"),
            ({"conta_destino": ""}, "destino"),
            ({"tipo": "PIX_AGENDADO"}, "desconhecido"),
            (
                {"tipo": TipoTransacao.TRANSFERENCIA, "conta_origem": "X", "conta_destino": "X"},
                "mesma conta",
            ),
        ],
    )
    def test_rejeita_dados_invalidos(self, campos, mensagem):
        with pytest.raises(TransacaoInvalida, match=mensagem):
            nova(**campos)

    def test_pagamento_pode_ter_mesma_conta(self):
        # Ex.: pagamento de boleto da própria conta; a regra de "mesma conta" é só de transferência.
        assert nova(conta_origem="X", conta_destino="X").status is StatusTransacao.PENDENTE


class TestMaquinaDeEstados:
    def test_fluxo_feliz_incrementa_versao(self):
        t = nova()
        t.iniciar_processamento()
        t.aprovar()
        assert t.status is StatusTransacao.APROVADA
        assert t.versao == 3
        assert t.finalizada

    def test_recusa_guarda_motivo(self):
        t = nova()
        t.iniciar_processamento()
        t.recusar("limite")
        assert t.status is StatusTransacao.RECUSADA
        assert t.motivo_recusa == "limite"

    def test_recusa_exige_motivo(self):
        t = nova()
        t.iniciar_processamento()
        with pytest.raises(TransacaoInvalida):
            t.recusar("")

    def test_processamento_pode_ser_retomado(self):
        t = nova()
        t.iniciar_processamento()
        t.iniciar_processamento()
        assert t.status is StatusTransacao.PROCESSANDO

    def test_nao_aprova_sem_processar(self):
        with pytest.raises(TransicaoDeStatusInvalida):
            nova().aprovar()

    @pytest.mark.parametrize("final", ["aprovar", "recusar"])
    def test_estado_final_e_imutavel(self, final):
        t = nova()
        t.iniciar_processamento()
        t.aprovar() if final == "aprovar" else t.recusar("x")
        with pytest.raises(TransicaoDeStatusInvalida):
            t.iniciar_processamento()


class TestPolitica:
    def test_aprova_dentro_do_limite(self):
        assert PoliticaDeAprovacao(limite_pagamento_centavos=1000).avaliar(nova()).aprovada

    def test_recusa_acima_do_limite_com_valor_formatado(self):
        politica = PoliticaDeAprovacao(limite_transferencia_centavos=123_456_78)
        t = nova(tipo=TipoTransacao.TRANSFERENCIA, valor_centavos=123_456_79)
        decisao = politica.avaliar(t)
        assert not decisao.aprovada
        assert "R$ 123.456,78" in decisao.motivo


class TestEventos:
    def test_evento_tem_id_deterministico(self):
        t = nova()
        t.iniciar_processamento()
        t.aprovar()
        e1 = TransacaoProcessada.de(t, "s3://b/k")
        e2 = TransacaoProcessada.de(t, "s3://b/k")
        assert e1.id_evento == e2.id_evento == f"{t.id}:APROVADA"
        corpo = e1.para_dict()
        assert corpo["nome"] == "transacao.processada"
        assert corpo["comprovante_uri"] == "s3://b/k"
