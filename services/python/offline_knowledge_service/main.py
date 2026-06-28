from fastapi import FastAPI

app = FastAPI(title="offline-knowledge-service")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "offline-knowledge-service"}


@app.get("/profile")
async def profile() -> dict:
    return {
        "service": "offline-knowledge-service",
        "responsibilities": [
            "knowledge ingestion",
            "chunking",
            "embedding generation",
            "vector upsert pipelines",
        ],
    }
