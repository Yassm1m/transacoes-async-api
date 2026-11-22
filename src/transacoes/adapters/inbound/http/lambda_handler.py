"""Entrada do Lambda da API (API Gateway HTTP API -> Mangum -> FastAPI).

O app é criado fora do handler para ser reaproveitado entre invocações (warm start).
"""

from mangum import Mangum

from transacoes.adapters.inbound.http.app import create_app
from transacoes.shared.observabilidade import configurar_logs

configurar_logs()
app = create_app()
handler = Mangum(app, lifespan="off")
