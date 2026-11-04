"""Provider Groq; reserva de modelo somente quando o escolhido não existe."""
from modules.credenciais import obter_chave, normalizar_chave_groq

from .chat_json import ChatJSONProvider
from .errors import ErroProvider, erro_seguro


def _criar_cliente(**opcoes):
    try:
        from groq import Groq
    except ImportError:
        raise ErroProvider("Instale o SDK oficial groq na .venv do projeto.") from None
    return Groq(**opcoes)


class GroqProvider(ChatJSONProvider):
    provider = "groq"
    MODELO_PADRAO = "openai/gpt-oss-120b"
    MODELOS_RESERVA = (MODELO_PADRAO, "openai/gpt-oss-20b",
                      "llama-3.3-70b-versatile", "llama-3.1-8b-instant")
    MODELOS_JSON_SCHEMA = frozenset((MODELO_PADRAO, "openai/gpt-oss-20b",
                                    "openai/gpt-oss-safeguard-20b", "qwen/qwen3.8-27b"))

    def __init__(self, chave: str | None = None, *, schema=None):
        chave = chave if chave is not None else obter_chave(self.provider)
        chave = normalizar_chave_groq(chave)
        if not chave or not chave.strip():
            raise ErroProvider("Configure GROQ_API_KEY ou salve a chave Groq no cofre do Windows.")
        self._configurar(schema)
        try:
            self._cliente = _criar_cliente(api_key=chave, timeout=self.TIMEOUT_SEGUNDOS, max_retries=0)
        except ErroProvider:
            raise
        except Exception as erro:
            raise erro_seguro(erro, self.provider) from None

    def _listar_modelos(self) -> list[str]:
        return [item.id for item in self._cliente.models.list().data]

    def selecionar_modelo(self, modelo: str, disponiveis: list[str]) -> str:
        if modelo in disponiveis:
            return modelo
        for reserva in self.MODELOS_RESERVA:
            if reserva in disponiveis:
                return reserva
        raise ErroProvider("O modelo Groq selecionado não existe e não há modelo de reserva disponível.", codigo=404)

    def _gerar(self, modelo: str, prompt: str):
        estruturado = modelo in self.MODELOS_JSON_SCHEMA
        return self._cliente.chat.completions.create(
            model=modelo, messages=self._mensagens(prompt, incluir_schema=not estruturado),
            response_format=self._formato_schema() if estruturado else {"type": "json_object"},
            temperature=0.5 if self._chave_resposta == "historias" else 0.55,
            max_completion_tokens=16384 if self._chave_resposta == "historias" else 8192,
            stream=False)

    def _teste_minimo(self, modelo: str):
        # Testa exatamente o modelo selecionado: sem listagem, reserva ou retentativa.
        opcoes = {"reasoning_effort": "low"} if modelo in (self.MODELO_PADRAO, "openai/gpt-oss-20b") else {}
        return self._cliente.chat.completions.create(
            model=modelo, messages=[{"role": "user", "content": "Responda apenas OK"}],
            max_completion_tokens=256, temperature=0, stream=False, timeout=30, **opcoes)
