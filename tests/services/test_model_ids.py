from app.services.model_ids import canonical_model_id

def test_deepseek_aliases_are_canonical():
    assert canonical_model_id("v4-flash", provider="deepseek") == "deepseek-v4-flash"
    assert canonical_model_id("deepseek-chat", provider="deepseek") == "deepseek-chat"
