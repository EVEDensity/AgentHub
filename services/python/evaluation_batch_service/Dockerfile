FROM python:3.11-slim
WORKDIR /app
COPY services/python/pyproject.toml /app/pyproject.toml
RUN pip install --no-cache-dir fastapi uvicorn[standard] pydantic pydantic-settings httpx psycopg2-binary prometheus_client nats-py \
    -i https://mirrors.aliyun.com/pypi/simple \
    --trusted-host mirrors.aliyun.com
COPY services/python/evaluation_batch_service /app/evaluation_batch_service
COPY services/python/shared /app/shared
EXPOSE 8094
CMD ["uvicorn", "evaluation_batch_service.main:app", "--host", "0.0.0.0", "--port", "8094"]
