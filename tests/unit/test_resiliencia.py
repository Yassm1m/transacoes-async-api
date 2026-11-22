import pytest

from transacoes.shared.resiliencia import PoliticaDeRetry, executar_com_retry


class Instavel:
    def __init__(self, falhas: int, erro: type[Exception] = ConnectionError) -> None:
        self.falhas = falhas
        self.erro = erro
        self.chamadas = 0

    def __call__(self) -> str:
        self.chamadas += 1
        if self.chamadas <= self.falhas:
            raise self.erro("falhou")
        return "ok"


def test_sucesso_apos_falhas_transitorias_com_backoff_exponencial():
    esperas: list[float] = []
    op = Instavel(falhas=2)
    politica = PoliticaDeRetry(tentativas=3, espera_base_s=1, espera_maxima_s=10)

    resultado = executar_com_retry(
        op,
        politica=politica,
        erros_retentaveis=(ConnectionError,),
        dormir=esperas.append,
        sortear=lambda _a, b: b,  # sem jitter, para verificar o teto
    )

    assert resultado == "ok"
    assert op.chamadas == 3
    assert esperas == [1, 2]


def test_espera_respeita_teto_maximo():
    politica = PoliticaDeRetry(tentativas=10, espera_base_s=1, espera_maxima_s=3)
    assert politica.calcular_espera(5, lambda _a, b: b) == 3


def test_propaga_erro_quando_esgota_tentativas():
    op = Instavel(falhas=99)
    with pytest.raises(ConnectionError):
        executar_com_retry(
            op,
            politica=PoliticaDeRetry(tentativas=3),
            erros_retentaveis=(ConnectionError,),
            dormir=lambda _s: None,
        )
    assert op.chamadas == 3


def test_nao_retenta_erro_nao_retentavel():
    op = Instavel(falhas=1, erro=ValueError)
    with pytest.raises(ValueError):
        executar_com_retry(
            op,
            politica=PoliticaDeRetry(tentativas=5),
            erros_retentaveis=(ConnectionError,),
            dormir=lambda _s: None,
        )
    assert op.chamadas == 1


def test_tentativas_invalidas():
    with pytest.raises(ValueError):
        executar_com_retry(lambda: 1, politica=PoliticaDeRetry(tentativas=0), erros_retentaveis=())
