FROM python:3.11-slim
WORKDIR /app
COPY services/python/pyproject.toml /app/pyproject.toml
RUN pip install --no-cache-dir fastapi uvicorn[standard] pydantic pydantic-settings httpx prometheus_client nats-py
COPY services/python/summarization_service /app/summarization_service
COPY services/python/shared /app/shared
EXPOSE 8093
CMD ["uvicorn", "summarization_service.main:app", "--host", "0.0.0.0", "--port", "8093"]
