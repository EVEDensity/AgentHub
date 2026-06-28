from fastapi import FastAPI

app = FastAPI(title="model-adapter-service")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "model-adapter-service"}


@app.get("/profile")
async def profile() -> dict:
    return {
        "service": "model-adapter-service",
        "responsibilities": [
            "provider adapters",
            "offline model benchmarking",
            "batch embedding and rerank inference",
        ],
    }
