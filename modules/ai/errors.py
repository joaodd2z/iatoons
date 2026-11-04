"""Erros seguros e comuns, compatíveis com os handlers legados de Gemini."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math


class ErroProvider(RuntimeError):
    def __init__(self, mensagem: str, *, codigo: int | None = None,
                 aguardar_segundos: float | None = None, tipo: str | None = None):
        super().__init__(mensagem)
        self.codigo = codigo
        self.aguardar_segundos = aguardar_segundos
        self.tipo = tipo


def erro_seguro(erro: Exception, provider: str) -> ErroProvider:
    """Nunca incorpora o corpo, a requisição ou o texto da exceção do SDK."""
    codigo = getattr(erro, "status_code", None) or getattr(erro, "code", None)
    resposta = getattr(erro, "response", None) or getattr(erro, "raw_response", None)
    espera = None
    if codigo == 429:
        headers = getattr(resposta, "headers", {}) or {}
        valor = headers.get("retry-after") or headers.get("Retry-After")
        try:
            espera = float(valor)
            if not math.isfinite(espera):
                espera = None
            elif espera < 0:
                espera = 0.0
        except (ValueError, TypeError):
            try:
                espera = max(0.0, (parsedate_to_datetime(valor) - datetime.now(timezone.utc)).total_seconds())
            except (ValueError, TypeError, OverflowError):
                pass
        return ErroProvider(f"Limite de chamadas de {provider} atingido (HTTP 429).",
                            codigo=429, aguardar_segundos=espera)
    if codigo in (401, 403):
        return ErroProvider(f"A chave de {provider} foi recusada ou não tem acesso ao recurso.", codigo=codigo)
    if codigo == 404:
        return ErroProvider(f"O modelo selecionado não está disponível em {provider}.", codigo=404)
    # Inclui TimeoutException/httpx2 e APITimeoutError/Groq, sem ler mensagens privadas.
    if isinstance(erro, TimeoutError) or any("timeout" in cls.__name__.lower() for cls in type(erro).__mro__):
        return ErroProvider(f"A conexão com {provider} excedeu o tempo limite.", codigo=408, tipo="timeout")
    if isinstance(erro, ConnectionError) or any(cls.__name__ in (
            "ConnectError", "NetworkError", "APIConnectionError", "TransportError") for cls in type(erro).__mro__):
        return ErroProvider(f"Falha temporária de rede em {provider}.", tipo="rede")
    if codigo in (400, 422):
        return ErroProvider(f"{provider} recusou o formato da solicitação ou o modelo selecionado.", codigo=codigo)
    return ErroProvider(f"Não foi possível comunicar com {provider}.", codigo=codigo)
