"""Erros da camada de aplicação (casos de uso)."""


class ErroDeAplicacao(Exception):
    pass


class TransacaoNaoEncontrada(ErroDeAplicacao):
    def __init__(self, transacao_id: str) -> None:
        super().__init__(f"Transação {transacao_id} não encontrada")
        self.transacao_id = transacao_id


class ConflitoDeIdempotencia(ErroDeAplicacao):
    """Mesma Idempotency-Key reutilizada com um corpo de requisição diferente."""

    def __init__(self, chave: str) -> None:
        super().__init__(
            f"A chave de idempotência '{chave}' já foi usada com uma requisição diferente"
        )
        self.chave = chave


class ErroTransitorio(ErroDeAplicacao):
    """Falha que deve ser tentada de novo mais tarde (o worker devolve a mensagem à fila)."""
