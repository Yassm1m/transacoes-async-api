import pytest
from fastapi.testclient import TestClient

from transacoes.adapters.inbound.http.app import create_app

CORPO = {
    "tipo": "TRANSFERENCIA",
    "valor": "150.75",
    "conta_origem": "0001-11111-1",
    "conta_destino": "0001-22222-2",
}


@pytest.fixture
def client(container) -> TestClient:
    return TestClient(create_app(container), raise_server_exceptions=False)


def post(client, corpo=None, chave="chave-teste-001", **headers):
    if chave:
        headers["Idempotency-Key"] = chave
    return client.post("/transacoes", json=corpo or CORPO, headers=headers)


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_post_responde_202_pendente_com_location(client):
    resp = post(client)

    assert resp.status_code == 202
    corpo = resp.json()
    assert corpo["status"] == "PENDENTE"
    assert corpo["valor"] == "150.75"
    assert resp.headers["Location"] == f"/transacoes/{corpo['id']}"
    assert "Idempotent-Replayed" not in resp.headers


def test_get_mostra_resultado_apos_processamento(client, container, fila):
    transacao_id = post(client).json()["id"]
    assert client.get(f"/transacoes/{transacao_id}").json()["status"] == "PENDENTE"

    container.processar_transacao.executar(fila.mensagens[0]["transacao_id"])

    resp = client.get(f"/transacoes/{transacao_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "APROVADA"


def test_reenvio_com_mesma_chave_devolve_mesma_transacao(client):
    primeira = post(client)
    segunda = post(client)

    assert segunda.status_code == 202
    assert segunda.json()["id"] == primeira.json()["id"]
    assert segunda.headers["Idempotent-Replayed"] == "true"


def test_mesma_chave_com_corpo_diferente_retorna_422(client):
    post(client)
    resp = post(client, corpo={**CORPO, "valor": "999.00"})

    assert resp.status_code == 422
    assert resp.json()["erro"]["codigo"] == "IDEMPOTENCY_KEY_REUSED"


def test_sem_idempotency_key_retorna_400(client):
    resp = post(client, chave=None)
    assert resp.status_code == 400
    assert resp.json()["erro"]["codigo"] == "IDEMPOTENCY_KEY_AUSENTE"


@pytest.mark.parametrize("chave", ["curta", "tem espaco no meio", "x" * 129])
def test_idempotency_key_mal_formada_retorna_400(client, chave):
    resp = post(client, chave=chave)
    assert resp.status_code == 400
    assert resp.json()["erro"]["codigo"] == "IDEMPOTENCY_KEY_INVALIDA"


@pytest.mark.parametrize(
    "alteracao",
    [
        {"valor": "0"},
        {"valor": "-10.00"},
        {"valor": "10.001"},
        {"tipo": "BOLETO"},
        {"conta_origem": ""},
        {"campo_extra": "nao permitido"},
    ],
)
def test_corpo_invalido_retorna_422_padronizado(client, alteracao):
    resp = post(client, corpo={**CORPO, **alteracao})
    assert resp.status_code == 422
    assert resp.json()["erro"]["codigo"] == "REQUISICAO_INVALIDA"


def test_regra_de_dominio_violada_retorna_422(client):
    resp = post(client, corpo={**CORPO, "conta_destino": CORPO["conta_origem"]})
    assert resp.status_code == 422
    assert resp.json()["erro"]["codigo"] == "TRANSACAO_INVALIDA"


def test_get_inexistente_retorna_404(client):
    resp = client.get("/transacoes/nao-existe")
    assert resp.status_code == 404
    assert resp.json()["erro"]["codigo"] == "TRANSACAO_NAO_ENCONTRADA"


def test_correlation_id_e_propagado_para_resposta_e_fila(client, fila):
    resp = post(client, **{"X-Correlation-ID": "corr-abc-123"})

    assert resp.headers["X-Correlation-ID"] == "corr-abc-123"
    assert fila.mensagens[0]["correlation_id"] == "corr-abc-123"


def test_correlation_id_gerado_quando_ausente(client):
    assert client.get("/health").headers["X-Correlation-ID"]


def test_erro_inesperado_retorna_500_sem_vazar_detalhes(client, container, monkeypatch):
    def explode(*_a, **_k):
        raise RuntimeError("segredo interno")

    monkeypatch.setattr(container.consultar_transacao, "executar", explode)
    resp = client.get("/transacoes/qualquer")

    assert resp.status_code == 500
    assert resp.json()["erro"]["codigo"] == "ERRO_INTERNO"
    assert "segredo" not in resp.text
