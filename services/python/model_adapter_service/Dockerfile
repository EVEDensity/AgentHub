FROM python:3.11-slim
WORKDIR /app
COPY services/python/pyproject.toml /app/pyproject.toml
RUN pip install --no-cache-dir fastapi uvicorn[standard] pydantic pydantic-settings httpx prometheus_client
COPY services/python/model_adapter_service /app/model_adapter_service
COPY services/python/shared /app/shared
EXPOSE 8091
CMD ["uvicorn", "model_adapter_service.main:app", "--host", "0.0.0.0", "--port", "8091"]
