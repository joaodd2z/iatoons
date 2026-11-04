"""Factory de providers; aceita o ConfigIA legado ou o nome explícito."""
from modules.config_ia import ConfigIA, carregar_config_ia
from modules.credenciais import obter_chave, obter_chave_gemini
from .base import AIProvider
from .errors import ErroProvider
from .gemini import GeminiProvider, GeminiAgencyProvider


def obter_provider(config: ConfigIA | str | None = None, *, modo: str = "analise",
                   chave: str | None = None, schema=None) -> AIProvider:
    config = config if config is not None else carregar_config_ia()
    provider = config if isinstance(config, str) else config.provider_ativo
    if provider not in ("gemini", "mistral", "groq"):
        raise ValueError(f"Provider não suportado: {provider}")
    if modo not in ("analise", "agencia"):
        raise ValueError(f"Modo de IA não suportado: {modo}")
    if modo == "agencia" and schema is None:
        raise ValueError("O Modo Agência exige seu schema de resposta.")
    if isinstance(config, ConfigIA) and config.fallback_automatico:
        from .fallback import ProviderFallback
        return ProviderFallback(config, modo=modo, schema=schema)
    if chave is None:
        chave = obter_chave_gemini() if provider == "gemini" else obter_chave(provider)
    if not chave or not chave.strip():
        raise ErroProvider(f"Configure a chave de {provider} no cofre do Windows ou em {provider.upper()}_API_KEY.")
    if provider == "gemini":
        return GeminiAgencyProvider(chave, schema) if modo == "agencia" else GeminiProvider(chave)
    if provider == "mistral":
        from .mistral import MistralProvider
        return MistralProvider(chave, schema=schema if modo == "agencia" else None)
    from .groq import GroqProvider
    return GroqProvider(chave, schema=schema if modo == "agencia" else None)
