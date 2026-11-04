"""Provider Mistral com o SDK oficial, JSON schema e timeout explícito."""
import logging
from modules.credenciais import obter_chave

from .chat_json import ChatJSONProvider
from .errors import ErroProvider, erro_seguro


def _criar_cliente(**opcoes):
    try:
        from mistralai.client import Mistral
    except ImportError:
        raise ErroProvider("Instale o SDK oficial mistralai na .venv do projeto.") from None
    # O logger de debug do SDK pode registrar cabeçalhos HTTP; fica desativado.
    logger = logging.Logger("ia.mistral.sdk")
    logger.disabled = True
    return Mistral(**opcoes, debug_logger=logger)


class MistralProvider(ChatJSONProvider):
    provider = "mistral"

    def __init__(self, chave: str | None = None, *, schema=None):
        chave = chave if chave is not None else obter_chave(self.provider)
        if not chave or not chave.strip():
            raise ErroProvider("Configure MISTRAL_API_KEY ou salve a chave Mistral no cofre do Windows.")
        self._configurar(schema)
        try:
            self._cliente = _criar_cliente(api_key=chave, timeout_ms=self.TIMEOUT_SEGUNDOS * 1000,
                                          retry_config=None)
        except ErroProvider:
            raise
        except Exception as erro:
            raise erro_seguro(erro, self.provider) from None

    def _listar_modelos(self) -> list[str]:
        resposta = self._cliente.models.list(retries=None)
        return [item.id for item in resposta.data
                if getattr(getattr(item, "capabilities", None), "completion_chat", True)]

    def _gerar(self, modelo: str, prompt: str):
        return self._cliente.chat.complete(
            model=modelo, messages=self._mensagens(prompt), response_format=self._formato_schema(),
            temperature=0.5 if self._chave_resposta == "historias" else 0.55,
            max_tokens=16384 if self._chave_resposta == "historias" else 8192,
            stream=False, retries=None)

    def _teste_minimo(self, modelo: str):
        return self._cliente.chat.complete(
            model=modelo, messages=[{"role": "user", "content": "Responda apenas OK"}],
            max_tokens=32, temperature=0, stream=False, timeout_ms=30000, retries=None)
