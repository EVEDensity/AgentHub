from app.cli.config import ConfigResolver


def test_config_resolver_has_explicit_over_config_over_environment_precedence():
    resolver = ConfigResolver({
        "AGENTHUB_CLI_PROVIDER": "deepseek",
        "AGENTHUB_CLI_MODEL": "env-model",
        "AGENTHUB_CLI_MODEL_BASE_URL": "https://env.invalid",
        "DEEPSEEK_API_KEY": "env-key",
    })
    resolved = resolver.resolve_model(
        provider="mock",
        model="flag-model",
        base_url="https://flag.invalid",
        config={"provider": "openai", "model": "config-model", "base_url": "https://config.invalid"},
        default_model="mock-model",
    )
    assert resolved == {
        "provider": "mock",
        "model": "flag-model",
        "base_url": "https://flag.invalid",
        "api_key": "",
    }


def test_config_resolver_uses_provider_key_without_persisting_other_environment_values():
    resolver = ConfigResolver({
        "AGENTHUB_CLI_PROVIDER": "deepseek",
        "DEEPSEEK_API_KEY": "secret",
        "AGENTHUB_CLI_MODEL": "v4-flash",
    })
    resolved = resolver.resolve_model(
        provider=None,
        model=None,
        base_url=None,
        config={},
        default_model="mock-model",
    )
    assert resolved["provider"] == "deepseek"
    assert resolved["api_key"] == "secret"
    assert "secret" not in repr(resolver)
