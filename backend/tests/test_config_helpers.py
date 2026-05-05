from app.config import uses_openai_compatible_client


def test_uses_openai_compatible_client():
    assert uses_openai_compatible_client("ollama") is True
    assert uses_openai_compatible_client("openai_compatible") is True
    assert uses_openai_compatible_client("openai") is False
    assert uses_openai_compatible_client("gemini") is False
