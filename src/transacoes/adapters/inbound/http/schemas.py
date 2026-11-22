"""Contratos HTTP (DTOs). Ficam no adapter: o domínio não depende do Pydantic."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from transacoes.domain.transacao import StatusTransacao, TipoTransacao, Transacao


class CriarTransacaoRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "tipo": "TRANSFERENCIA",
                "valor": "150.75",
                "conta_origem": "0001-12345-6",
                "conta_destino": "0001-65432-1",
                "descricao": "Aluguel de novembro",
            }
        },
    )

    tipo: TipoTransacao
    valor: Decimal = Field(gt=0, max_digits=12, decimal_places=2, description="Em reais")
    conta_origem: str = Field(min_length=1, max_length=64)
    conta_destino: str = Field(min_length=1, max_length=64)
    descricao: str | None = Field(default=None, max_length=140)

    @property
    def valor_centavos(self) -> int:
        return int(self.valor * 100)


class TransacaoResponse(BaseModel):
    id: str
    tipo: TipoTransacao
    valor: str = Field(description="Valor em reais, como string para não perder precisão")
    conta_origem: str
    conta_destino: str
    descricao: str | None
    status: StatusTransacao
    motivo_recusa: str | None
    criada_em: datetime
    atualizada_em: datetime
    links: dict[str, str]

    @classmethod
    def de(cls, t: Transacao) -> TransacaoResponse:
        reais, centavos = divmod(t.valor_centavos, 100)
        return cls(
            id=t.id,
            tipo=t.tipo,
            valor=f"{reais}.{centavos:02d}",
            conta_origem=t.conta_origem,
            conta_destino=t.conta_destino,
            descricao=t.descricao,
            status=t.status,
            motivo_recusa=t.motivo_recusa,
            criada_em=t.criada_em,
            atualizada_em=t.atualizada_em,
            links={"self": f"/transacoes/{t.id}"},
        )


class DetalheErro(BaseModel):
    codigo: str
    mensagem: str


class ErroResponse(BaseModel):
    erro: DetalheErro
    correlation_id: str | None = None
