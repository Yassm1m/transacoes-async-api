"""Eventos de domínio publicados quando algo relevante acontece."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from transacoes.domain.transacao import Transacao


@dataclass(frozen=True)
class TransacaoProcessada:
    transacao_id: str
    status: str
    tipo: str
    valor_centavos: int
    motivo_recusa: str | None
    comprovante_uri: str | None
    ocorrido_em: datetime = field(default_factory=lambda: datetime.now(UTC))
    nome: str = "transacao.processada"

    @property
    def id_evento(self) -> str:
        # Determinístico: o mesmo processamento gera o mesmo id, o que permite
        # aos consumidores descartarem duplicatas (entrega at-least-once).
        return f"{self.transacao_id}:{self.status}"

    @classmethod
    def de(cls, transacao: Transacao, comprovante_uri: str | None) -> TransacaoProcessada:
        return cls(
            transacao_id=transacao.id,
            status=transacao.status.value,
            tipo=transacao.tipo.value,
            valor_centavos=transacao.valor_centavos,
            motivo_recusa=transacao.motivo_recusa,
            comprovante_uri=comprovante_uri,
        )

    def para_dict(self) -> dict:
        return {
            "id_evento": self.id_evento,
            "nome": self.nome,
            "transacao_id": self.transacao_id,
            "status": self.status,
            "tipo": self.tipo,
            "valor_centavos": self.valor_centavos,
            "motivo_recusa": self.motivo_recusa,
            "comprovante_uri": self.comprovante_uri,
            "ocorrido_em": self.ocorrido_em.isoformat(),
        }
