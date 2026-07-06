FROM python:3.11-slim
WORKDIR /app
COPY services/python/pyproject.toml /app/pyproject.toml
RUN pip install --no-cache-dir fastapi uvicorn[standard] pydantic pydantic-settings httpx opensearch-py minio prometheus_client nats-py pypdf python-docx python-pptx Pillow python-multipart \
    -i https://mirrors.aliyun.com/pypi/simple \
    --trusted-host mirrors.aliyun.com
COPY services/python/document_pipeline_service /app/document_pipeline_service
COPY services/python/shared /app/shared
EXPOSE 8095
CMD ["uvicorn", "document_pipeline_service.main:app", "--host", "0.0.0.0", "--port", "8095"]
