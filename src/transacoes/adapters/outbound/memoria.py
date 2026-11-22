"""Adapters em memória: usados em testes unitários e para rodar a API sem AWS."""

from __future__ import annotations

import copy
import threading
from collections.abc import Callable

from transacoes.domain.eventos import TransacaoProcessada
from transacoes.domain.transacao import Transacao
from transacoes.ports import (
    ArmazenamentoDeComprovantes,
    ChaveDeIdempotenciaJaUtilizada,
    ConflitoDeConcorrencia,
    FilaDeProcessamento,
    PublicadorDeEventos,
    RegistroDeIdempotencia,
    RepositorioDeTransacoes,
)


class RepositorioEmMemoria(RepositorioDeTransacoes):
    def __init__(self) -> None:
        self._transacoes: dict[str, Transacao] = {}
        self._idempotencia: dict[str, RegistroDeIdempotencia] = {}
        self._lock = threading.Lock()

    def criar(self, transacao: Transacao, idempotencia: RegistroDeIdempotencia) -> None:
        with self._lock:
            existente = self._idempotencia.get(idempotencia.chave)
            if existente is not None:
                raise ChaveDeIdempotenciaJaUtilizada(existente)
            self._idempotencia[idempotencia.chave] = idempotencia
            self._transacoes[transacao.id] = copy.deepcopy(transacao)

    def buscar(self, transacao_id: str) -> Transacao | None:
        t = self._transacoes.get(transacao_id)
        return copy.deepcopy(t) if t else None

    def atualizar(self, transacao: Transacao, versao_esperada: int) -> None:
        with self._lock:
            atual = self._transacoes.get(transacao.id)
            if atual is None or atual.versao != versao_esperada:
                raise ConflitoDeConcorrencia(transacao.id)
            self._transacoes[transacao.id] = copy.deepcopy(transacao)


class FilaEmMemoria(FilaDeProcessamento):
    """Guarda as mensagens. Se receber um `consumidor`, processa na hora (modo demo)."""

    def __init__(self, consumidor: Callable[[str], object] | None = None) -> None:
        self.mensagens: list[dict] = []
        self.consumidor = consumidor

    def enviar(self, transacao_id: str, correlation_id: str | None = None) -> None:
        self.mensagens.append({"transacao_id": transacao_id, "correlation_id": correlation_id})
        if self.consumidor:
            self.consumidor(transacao_id)


class PublicadorEmMemoria(PublicadorDeEventos):
    def __init__(self) -> None:
        self.eventos: list[TransacaoProcessada] = []

    def publicar(self, evento: TransacaoProcessada) -> None:
        self.eventos.append(evento)


class ComprovantesEmMemoria(ArmazenamentoDeComprovantes):
    def __init__(self) -> None:
        self.comprovantes: dict[str, dict] = {}

    def salvar(self, transacao: Transacao) -> str:
        self.comprovantes[transacao.id] = {"id": transacao.id, "status": transacao.status.value}
        return f"memoria://comprovantes/{transacao.id}.json"
