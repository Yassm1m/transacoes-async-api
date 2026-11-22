# Imagem para rodar a API em container (docker-compose local ou ECS/Fargate).
# Na AWS, o deploy padrão deste projeto é Lambda; esta imagem mostra que o mesmo
# código roda em container sem mudanças, graças à arquitetura hexagonal.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY src/requirements.txt ./src/requirements.txt
RUN pip install --no-cache-dir -r src/requirements.txt "uvicorn>=0.30"

COPY src ./src
COPY scripts ./scripts

RUN useradd --create-home --uid 10001 app
USER app

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"

CMD ["uvicorn", "transacoes.adapters.inbound.http.lambda_handler:app", "--host", "0.0.0.0", "--port", "8080"]
