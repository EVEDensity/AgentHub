FROM python:3.11-slim
WORKDIR /app
COPY services/python/pyproject.toml /app/pyproject.toml
RUN pip install --no-cache-dir fastapi uvicorn[standard] pydantic pydantic-settings httpx qdrant-client opensearch-py minio redis tenacity prometheus_client nats-py
COPY services/python/offline_knowledge_service /app/offline_knowledge_service
COPY services/python/shared /app/shared
EXPOSE 8092
CMD ["uvicorn", "offline_knowledge_service.main:app", "--host", "0.0.0.0", "--port", "8092"]
