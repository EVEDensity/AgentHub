FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/srv/agenthub

WORKDIR /srv/agenthub

RUN pip install --no-cache-dir \
    "asyncpg>=0.31,<1" \
    "fastapi>=0.115,<1" \
    "httpx>=0.28,<1" \
    "pydantic>=2.10,<3" \
    "pydantic-settings>=2.7,<3" \
    "python-dotenv>=1.0,<2" \
    "uvicorn[standard]>=0.34,<1"

COPY app /srv/agenthub/app
COPY services/python/decision_expiry_service \
    /srv/agenthub/services/python/decision_expiry_service

RUN useradd --create-home --uid 10003 decision-expiry
USER 10003

EXPOSE 8099

CMD ["python", "-m", "services.python.decision_expiry_service"]
