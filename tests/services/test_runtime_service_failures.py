from unittest.mock import AsyncMock, patch

import pytest

from services.python.model_adapter_service.main import (
    ChatCompletionRequest,
    MockProvider,
)
from services.python.offline_knowledge_service import (
    multimodal_embedding_client as embeddings,
)


def test_mock_provider_projects_only_text_parts():
    request = ChatCompletionRequest(model="mock", messages=[{
        "role": "user", "content": [
            {"type": "text", "text": "describe this"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,private"}},
        ],
    }])
    response = MockProvider().chat(request)
    assert "describe this" in response.choices[0]["message"]["content"]
    assert "private" not in response.choices[0]["message"]["content"]


@pytest.mark.asyncio
async def test_failed_image_batch_cannot_be_reported_as_zero_vectors():
    with patch.object(embeddings, "embed_image", new=AsyncMock(side_effect=[
        [0.1, 0.2], embeddings.MultimodalEmbeddingError("service unavailable"),
    ])), pytest.raises(embeddings.MultimodalEmbeddingError, match="batch index 1"):
        await embeddings.embed_images([b"first", b"second"])
