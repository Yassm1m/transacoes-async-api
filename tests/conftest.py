import pytest

from transacoes.adapters.outbound.memoria import (
    ComprovantesEmMemoria,
    FilaEmMemoria,
    PublicadorEmMemoria,
    RepositorioEmMemoria,
)
from transacoes.adapters.outbound.processador_simulado import ProcessadorSimulado
from transacoes.application.casos_de_uso import (
    ComandoCriarTransacao,
    ConsultarTransacao,
    CriarTransacao,
    ProcessarTransacao,
)
from transacoes.config import Container
from transacoes.domain.transacao import TipoTransacao
from transacoes.shared.resiliencia import PoliticaDeRetry

RETRY_RAPIDO = PoliticaDeRetry(tentativas=3, espera_base_s=0, espera_maxima_s=0)


def nao_dormir(_segundos: float) -> None:
    return None


@pytest.fixture
def comando() -> ComandoCriarTransacao:
    return ComandoCriarTransacao(
        tipo=TipoTransacao.TRANSFERENCIA,
        valor_centavos=15_075,
        conta_origem="0001-11111-1",
        conta_destino="0001-22222-2",
        descricao="Aluguel",
    )


@pytest.fixture
def repositorio() -> RepositorioEmMemoria:
    return RepositorioEmMemoria()


@pytest.fixture
def fila() -> FilaEmMemoria:
    return FilaEmMemoria()


@pytest.fixture
def publicador() -> PublicadorEmMemoria:
    return PublicadorEmMemoria()


@pytest.fixture
def comprovantes() -> ComprovantesEmMemoria:
    return ComprovantesEmMemoria()


@pytest.fixture
def criar(repositorio, fila) -> CriarTransacao:
    return CriarTransacao(repositorio, fila, politica_retry=RETRY_RAPIDO, dormir=nao_dormir)


@pytest.fixture
def processar(repositorio, comprovantes, publicador) -> ProcessarTransacao:
    return ProcessarTransacao(
        repositorio=repositorio,
        processador=ProcessadorSimulado(),
        comprovantes=comprovantes,
        publicador=publicador,
        politica_retry=RETRY_RAPIDO,
        dormir=nao_dormir,
    )


@pytest.fixture
def container(criar, repositorio, processar) -> Container:
    return Container(
        criar_transacao=criar,
        consultar_transacao=ConsultarTransacao(repositorio),
        processar_transacao=processar,
    )
