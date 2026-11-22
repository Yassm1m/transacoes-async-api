"""Casos de uso: orquestram domínio + portas. Não importam boto3 nem FastAPI."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from transacoes.application.excecoes import (
    ConflitoDeIdempotencia,
    ErroTransitorio,
    TransacaoNaoEncontrada,
)
from transacoes.domain.eventos import TransacaoProcessada
from transacoes.domain.politica import PoliticaDeAprovacao
from transacoes.domain.transacao import StatusTransacao, TipoTransacao, Transacao
from transacoes.ports import (
    ArmazenamentoDeComprovantes,
    ChaveDeIdempotenciaJaUtilizada,
    ConflitoDeConcorrencia,
    FalhaTemporariaNoProcessador,
    FilaDeProcessamento,
    ProcessadorDePagamento,
    PublicadorDeEventos,
    RegistroDeIdempotencia,
    RepositorioDeTransacoes,
)
from transacoes.shared.resiliencia import PoliticaDeRetry, executar_com_retry

logger = logging.getLogger(__name__)


# =============================================================== criar


@dataclass(frozen=True)
class ComandoCriarTransacao:
    tipo: TipoTransacao
    valor_centavos: int
    conta_origem: str
    conta_destino: str
    descricao: str | None = None

    def hash(self) -> str:
        """Impressão digital do pedido, usada para detectar reuso indevido da chave."""
        canonico = json.dumps(
            {
                "tipo": self.tipo.value,
                "valor_centavos": self.valor_centavos,
                "conta_origem": self.conta_origem.strip(),
                "conta_destino": self.conta_destino.strip(),
                "descricao": self.descricao,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonico.encode()).hexdigest()


@dataclass(frozen=True)
class ResultadoCriacao:
    transacao: Transacao
    reaproveitada: bool


class CriarTransacao:
    """Registra a transação como PENDENTE e enfileira para processamento.

    Idempotência: a mesma `chave_idempotencia` com o mesmo corpo devolve a
    transação original em vez de criar outra. Com corpo diferente é conflito.
    """

    def __init__(
        self,
        repositorio: RepositorioDeTransacoes,
        fila: FilaDeProcessamento,
        politica_retry: PoliticaDeRetry | None = None,
        dormir: Callable[[float], None] = time.sleep,
    ) -> None:
        self._repositorio = repositorio
        self._fila = fila
        self._retry = politica_retry or PoliticaDeRetry()
        self._dormir = dormir

    def executar(
        self,
        comando: ComandoCriarTransacao,
        chave_idempotencia: str,
        correlation_id: str | None = None,
    ) -> ResultadoCriacao:
        transacao = Transacao.criar(
            tipo=comando.tipo,
            valor_centavos=comando.valor_centavos,
            conta_origem=comando.conta_origem,
            conta_destino=comando.conta_destino,
            descricao=comando.descricao,
        )
        registro = RegistroDeIdempotencia(
            chave=chave_idempotencia,
            hash_requisicao=comando.hash(),
            transacao_id=transacao.id,
        )

        try:
            self._repositorio.criar(transacao, registro)
        except ChaveDeIdempotenciaJaUtilizada as exc:
            return self._tratar_replay(exc.registro, registro, correlation_id)

        logger.info(
            "transacao criada",
            extra={"transacao_id": transacao.id, "tipo": transacao.tipo.value},
        )
        self._enfileirar(transacao.id, correlation_id)
        return ResultadoCriacao(transacao=transacao, reaproveitada=False)

    def _tratar_replay(
        self,
        existente: RegistroDeIdempotencia,
        novo: RegistroDeIdempotencia,
        correlation_id: str | None,
    ) -> ResultadoCriacao:
        if existente.hash_requisicao != novo.hash_requisicao:
            logger.warning("conflito de idempotencia", extra={"chave": novo.chave})
            raise ConflitoDeIdempotencia(novo.chave)

        original = self._repositorio.buscar(existente.transacao_id)
        if original is None:  # pragma: no cover - só acontece com dado corrompido
            raise TransacaoNaoEncontrada(existente.transacao_id)

        logger.info(
            "requisicao repetida, devolvendo transacao original",
            extra={"transacao_id": original.id, "chave": novo.chave},
        )
        # Autocura: se a primeira chamada gravou mas falhou ao enfileirar, o
        # cliente vai repetir com a mesma chave. Reenfileiramos com segurança,
        # porque o worker também é idempotente.
        if original.status is StatusTransacao.PENDENTE:
            self._enfileirar(original.id, correlation_id)
        return ResultadoCriacao(transacao=original, reaproveitada=True)

    def _enfileirar(self, transacao_id: str, correlation_id: str | None) -> None:
        executar_com_retry(
            lambda: self._fila.enviar(transacao_id, correlation_id),
            politica=self._retry,
            erros_retentaveis=(Exception,),
            nome="fila.enviar",
            dormir=self._dormir,
        )


# =============================================================== consultar


class ConsultarTransacao:
    def __init__(self, repositorio: RepositorioDeTransacoes) -> None:
        self._repositorio = repositorio

    def executar(self, transacao_id: str) -> Transacao:
        transacao = self._repositorio.buscar(transacao_id)
        if transacao is None:
            raise TransacaoNaoEncontrada(transacao_id)
        return transacao


# =============================================================== processar


class ResultadoProcessamento:
    PROCESSADA = "PROCESSADA"
    IGNORADA_JA_FINALIZADA = "IGNORADA_JA_FINALIZADA"
    IGNORADA_NAO_ENCONTRADA = "IGNORADA_NAO_ENCONTRADA"
    IGNORADA_CONCORRENCIA = "IGNORADA_CONCORRENCIA"


class ProcessarTransacao:
    """Executado pelo worker a cada mensagem do SQS.

    Garantias:
    - Idempotente: SQS entrega "pelo menos uma vez"; uma transação já finalizada
      é ignorada.
    - Concorrência: atualização condicional por versão impede que duas execuções
      simultâneas processem a mesma transação.
    - Resiliência: falhas transitórias no processador externo têm retry com
      backoff local; se persistirem, viram ErroTransitorio e a mensagem volta
      para a fila (e, após N tentativas, vai para a DLQ).
    """

    def __init__(
        self,
        repositorio: RepositorioDeTransacoes,
        processador: ProcessadorDePagamento,
        comprovantes: ArmazenamentoDeComprovantes,
        publicador: PublicadorDeEventos,
        politica: PoliticaDeAprovacao | None = None,
        politica_retry: PoliticaDeRetry | None = None,
        dormir: Callable[[float], None] = time.sleep,
    ) -> None:
        self._repositorio = repositorio
        self._processador = processador
        self._comprovantes = comprovantes
        self._publicador = publicador
        self._politica = politica or PoliticaDeAprovacao()
        self._retry = politica_retry or PoliticaDeRetry()
        self._dormir = dormir

    def executar(self, transacao_id: str) -> str:
        transacao = self._repositorio.buscar(transacao_id)
        if transacao is None:
            logger.warning(
                "transacao inexistente, descartando", extra={"transacao_id": transacao_id}
            )
            return ResultadoProcessamento.IGNORADA_NAO_ENCONTRADA

        if transacao.finalizada:
            logger.info(
                "transacao ja finalizada, mensagem duplicada ignorada",
                extra={"transacao_id": transacao_id, "status": transacao.status.value},
            )
            return ResultadoProcessamento.IGNORADA_JA_FINALIZADA

        # 1) Marca PROCESSANDO (trava otimista: só uma execução vence).
        versao = transacao.versao
        transacao.iniciar_processamento()
        try:
            self._repositorio.atualizar(transacao, versao_esperada=versao)
        except ConflitoDeConcorrencia:
            logger.info("outra execucao assumiu a transacao", extra={"transacao_id": transacao_id})
            return ResultadoProcessamento.IGNORADA_CONCORRENCIA

        # 2) Regra de negócio + liquidação externa.
        versao = transacao.versao
        decisao = self._politica.avaliar(transacao)
        if decisao.aprovada:
            self._liquidar(transacao)
            transacao.aprovar()
        else:
            transacao.recusar(decisao.motivo or "Recusada pela política")

        # 3) Efeitos colaterais idempotentes ANTES de gravar o status final: se algo
        #    falhar aqui, a mensagem volta e tudo é refeito. Comprovante é
        #    sobrescrito na mesma chave; o evento tem id determinístico.
        try:
            uri = self._comprovantes.salvar(transacao)
            self._publicador.publicar(TransacaoProcessada.de(transacao, uri))
        except Exception as exc:
            raise ErroTransitorio(f"Falha ao registrar resultado: {exc}") from exc

        try:
            self._repositorio.atualizar(transacao, versao_esperada=versao)
        except ConflitoDeConcorrencia:
            logger.warning(
                "status final ja gravado por outra execucao", extra={"transacao_id": transacao_id}
            )
            return ResultadoProcessamento.IGNORADA_CONCORRENCIA

        logger.info(
            "transacao processada",
            extra={
                "transacao_id": transacao_id,
                "status": transacao.status.value,
                "motivo_recusa": transacao.motivo_recusa,
            },
        )
        return ResultadoProcessamento.PROCESSADA

    def _liquidar(self, transacao: Transacao) -> None:
        try:
            executar_com_retry(
                lambda: self._processador.liquidar(transacao),
                politica=self._retry,
                erros_retentaveis=(FalhaTemporariaNoProcessador,),
                nome="processador.liquidar",
                dormir=self._dormir,
            )
        except FalhaTemporariaNoProcessador as exc:
            raise ErroTransitorio(str(exc)) from exc
