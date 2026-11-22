"""Exceções de domínio: regras de negócio violadas, independentes de infraestrutura."""


class ErroDeDominio(Exception):
    """Base para qualquer violação de regra de negócio."""


class TransacaoInvalida(ErroDeDominio):
    """Os dados informados não formam uma transação válida."""


class TransicaoDeStatusInvalida(ErroDeDominio):
    """Tentativa de mudar o status de uma transação para um estado não permitido."""

    def __init__(self, atual: str, destino: str) -> None:
        super().__init__(f"Transição de status inválida: {atual} -> {destino}")
        self.atual = atual
        self.destino = destino
