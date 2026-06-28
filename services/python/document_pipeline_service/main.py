from fastapi import FastAPI

app = FastAPI(title="document-pipeline-service")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "document-pipeline-service"}


@app.get("/profile")
async def profile() -> dict:
    return {
        "service": "document-pipeline-service",
        "responsibilities": [
            "pdf/docx/ppt/image extraction",
            "preview artifact generation",
            "content sanitization",
        ],
    }
