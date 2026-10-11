"""Source policy for controlled model execution projections."""


def supports_model_source(source: str, source_types: tuple[str, ...], adapter: str) -> bool:
    if source not in source_types:
        return False
    return source != "chat" or adapter == "function-calling"
