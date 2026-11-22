"""Adapter de entrada HTTP (FastAPI). Traduz HTTP <-> casos de uso."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable

from fastapi import FastAPI, Header, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from transacoes.adapters.inbound.http.schemas import (
    CriarTransacaoRequest,
    ErroResponse,
    TransacaoResponse,
)
from transacoes.application.casos_de_uso import ComandoCriarTransacao
from transacoes.application.excecoes import ConflitoDeIdempotencia, TransacaoNaoEncontrada
from transacoes.config import Container, construir_container
from transacoes.domain.excecoes import ErroDeDominio
from transacoes.shared.observabilidade import definir_correlation_id, obter_correlation_id

logger = logging.getLogger(__name__)

CABECALHO_CORRELATION = "X-Correlation-ID"
CHAVE_IDEMPOTENCIA_VALIDA = re.compile(r"^[A-Za-z0-9_\-:.]{8,128}$")


class RequisicaoInvalida(Exception):
    def __init__(self, codigo: str, mensagem: str) -> None:
        super().__init__(mensagem)
        self.codigo = codigo
        self.mensagem = mensagem


def _erro(status_code: int, codigo: str, mensagem: str) -> JSONResponse:
    corpo = ErroResponse(
        erro={"codigo": codigo, "mensagem": mensagem},
        correlation_id=obter_correlation_id(),
    )
    return JSONResponse(status_code=status_code, content=corpo.model_dump())


def create_app(container: Container | None = None) -> FastAPI:
    app = FastAPI(
        title="API de Transações Assíncronas",
        version="1.0.0",
        description=(
            "Recebe pagamentos e transferências, responde 202 na hora e processa em "
            "segundo plano via fila. Use o cabeçalho `Idempotency-Key` para reenviar "
            "com segurança."
        ),
    )
    app.state.container = container or construir_container()

    # ---------------------------------------------------------- middleware
    @app.middleware("http")
    async def correlation_e_log_de_acesso(request: Request, call_next: Callable):
        correlation_id = definir_correlation_id(request.headers.get(CABECALHO_CORRELATION))
        inicio = time.perf_counter()
        response: Response = await call_next(request)
        response.headers[CABECALHO_CORRELATION] = correlation_id
        logger.info(
            "requisicao http",
            extra={
                "metodo": request.method,
                "caminho": request.url.path,
                "status_http": response.status_code,
                "duracao_ms": round((time.perf_counter() - inicio) * 1000, 2),
            },
        )
        return response

    # ------------------------------------------------------ tratamento de erros
    @app.exception_handler(RequisicaoInvalida)
    async def _requisicao_invalida(_: Request, exc: RequisicaoInvalida):
        return _erro(status.HTTP_400_BAD_REQUEST, exc.codigo, exc.mensagem)

    @app.exception_handler(RequestValidationError)
    async def _validacao(_: Request, exc: RequestValidationError):
        detalhes = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'][1:]) or 'corpo'}: {e['msg']}" for e in exc.errors()
        )
        return _erro(422, "REQUISICAO_INVALIDA", detalhes)

    @app.exception_handler(ErroDeDominio)
    async def _dominio(_: Request, exc: ErroDeDominio):
        return _erro(422, "TRANSACAO_INVALIDA", str(exc))

    @app.exception_handler(ConflitoDeIdempotencia)
    async def _idempotencia(_: Request, exc: ConflitoDeIdempotencia):
        return _erro(422, "IDEMPOTENCY_KEY_REUSED", str(exc))

    @app.exception_handler(TransacaoNaoEncontrada)
    async def _nao_encontrada(_: Request, exc: TransacaoNaoEncontrada):
        return _erro(status.HTTP_404_NOT_FOUND, "TRANSACAO_NAO_ENCONTRADA", str(exc))

    @app.exception_handler(Exception)
    async def _inesperado(_: Request, exc: Exception):
        logger.exception("erro inesperado")
        return _erro(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "ERRO_INTERNO", "Erro interno inesperado"
        )

    # --------------------------------------------------------------- rotas
    respostas_erro = {
        400: {"model": ErroResponse},
        404: {"model": ErroResponse},
        422: {"model": ErroResponse},
    }

    @app.get("/health", tags=["infra"])
    def health() -> dict:
        return {"status": "ok"}

    @app.post(
        "/transacoes",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=TransacaoResponse,
        responses=respostas_erro,
        tags=["transacoes"],
        summary="Recebe uma transação para processamento assíncrono",
    )
    def criar_transacao(
        corpo: CriarTransacaoRequest,
        request: Request,
        response: Response,
        idempotency_key: str | None = Header(
            default=None,
            alias="Idempotency-Key",
            description="Identificador único gerado pelo cliente (8 a 128 caracteres)",
        ),
    ) -> TransacaoResponse:
        if not idempotency_key:
            raise RequisicaoInvalida(
                "IDEMPOTENCY_KEY_AUSENTE", "O cabeçalho Idempotency-Key é obrigatório"
            )
        if not CHAVE_IDEMPOTENCIA_VALIDA.match(idempotency_key):
            raise RequisicaoInvalida(
                "IDEMPOTENCY_KEY_INVALIDA",
                "Idempotency-Key deve ter de 8 a 128 caracteres [A-Za-z0-9_-:.]",
            )

        comando = ComandoCriarTransacao(
            tipo=corpo.tipo,
            valor_centavos=corpo.valor_centavos,
            conta_origem=corpo.conta_origem,
            conta_destino=corpo.conta_destino,
            descricao=corpo.descricao,
        )
        resultado = request.app.state.container.criar_transacao.executar(
            comando, chave_idempotencia=idempotency_key, correlation_id=obter_correlation_id()
        )
        response.headers["Location"] = f"/transacoes/{resultado.transacao.id}"
        if resultado.reaproveitada:
            response.headers["Idempotent-Replayed"] = "true"
        return TransacaoResponse.de(resultado.transacao)

    @app.get(
        "/transacoes/{transacao_id}",
        response_model=TransacaoResponse,
        responses=respostas_erro,
        tags=["transacoes"],
        summary="Consulta o status de uma transação",
    )
    def consultar_transacao(transacao_id: str, request: Request) -> TransacaoResponse:
        transacao = request.app.state.container.consultar_transacao.executar(transacao_id)
        return TransacaoResponse.de(transacao)

    return app
