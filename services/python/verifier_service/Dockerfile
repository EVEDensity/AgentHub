FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/srv/agenthub

WORKDIR /srv/agenthub

RUN pip install --no-cache-dir \
    "fastapi>=0.115,<1" \
    "httpx>=0.28,<1" \
    "pydantic>=2.10,<3" \
    "pydantic-settings>=2.7,<3" \
    "uvicorn[standard]>=0.34,<1"

COPY app /srv/agenthub/app
COPY services/python/verifier_service /srv/agenthub/services/python/verifier_service

RUN useradd --create-home --uid 10002 verifier
USER 10002

EXPOSE 8098

CMD ["python", "-m", "services.python.verifier_service"]
