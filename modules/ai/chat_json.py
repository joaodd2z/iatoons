"""Fluxo compartilhado pelos SDKs de chat, sem regras editoriais ou retentativas."""
from time import perf_counter

from .base import AIProvider
from .errors import ErroProvider, erro_seguro
from .gemini import _RespostaGemini
from .response import com_metadados, normalizar_resposta


class ChatJSONProvider(AIProvider):
    provider = ""
    TIMEOUT_SEGUNDOS = 360

    def _configurar(self, schema=None):
        self._schema = schema or _RespostaGemini
        self._chave_resposta = "historias" if "historias" in self._schema.model_fields else "candidatos"
        self._modelos = None

    def testar_conexao(self, modelo: str | None = None) -> bool | dict:
        if modelo is None:
            # Compatibilidade com o teste de catálogo das etapas anteriores.
            self.listar_modelos()
            return True
        inicio = perf_counter()
        try:
            resposta = self._teste_minimo(modelo)
            escolhas = resposta.choices
            if not escolhas or str(escolhas[0].finish_reason).lower() not in ("stop", "eos"):
                raise ErroProvider("O teste retornou uma resposta incompleta ou recusada.")
            conteudo = escolhas[0].message.content
            if isinstance(conteudo, list):
                conteudo = "".join(getattr(bloco, "text", "") if not isinstance(bloco, dict)
                                   else bloco.get("text", "") for bloco in conteudo)
            if not isinstance(conteudo, str) or conteudo.strip().upper() != "OK":
                raise ErroProvider("O modelo não confirmou o teste com OK.")
            return com_metadados({"ok": True}, self.provider, modelo, inicio)
        except ErroProvider:
            raise
        except Exception as erro:
            raise erro_seguro(erro, self.provider) from None

    def listar_modelos(self) -> list[str]:
        try:
            modelos = self._listar_modelos()
            self._modelos = sorted(set(modelos))
            return list(self._modelos)
        except ErroProvider:
            raise
        except Exception as erro:
            raise erro_seguro(erro, self.provider) from None

    def selecionar_modelo(self, modelo: str, disponiveis: list[str]) -> str:
        if modelo not in disponiveis:
            raise ErroProvider("O modelo selecionado não está disponível para esta conta.", codigo=404)
        return modelo

    def analisar(self, modelo: str, prompt: str) -> dict:
        disponiveis = self._modelos if self._modelos is not None else self.listar_modelos()
        modelo = self.selecionar_modelo(modelo, disponiveis)
        return self.analisar_uma_vez(modelo, prompt)

    def analisar_uma_vez(self, modelo: str, prompt: str) -> dict:
        """Uma geração no modelo exato, sem catálogo, retry ou reserva."""
        inicio = perf_counter()
        try:
            resposta = self._gerar(modelo, prompt)
            escolhas = resposta.choices
            if not escolhas:
                raise ErroProvider("A IA devolveu uma resposta vazia.")
            escolha = escolhas[0]
            motivo = str(escolha.finish_reason or "").lower()
            if motivo not in ("stop", "eos"):
                raise ErroProvider("A resposta da IA está incompleta ou foi recusada; nada foi aceito.")
            if getattr(escolha.message, "refusal", None):
                raise ErroProvider("A IA recusou a solicitação.")
            dados = normalizar_resposta(escolha.message.content, self._chave_resposta)
            return com_metadados(dados, self.provider, modelo, inicio)
        except ErroProvider:
            raise
        except Exception as erro:
            raise erro_seguro(erro, self.provider) from None

    def _formato_schema(self) -> dict:
        # Best effort preserva os campos opcionais dos schemas Gemini existentes.
        return {"type": "json_schema", "json_schema": {
            "name": self._schema.__name__, "schema": self._schema.model_json_schema(), "strict": False}}

    def _mensagens(self, prompt: str, *, incluir_schema: bool = False) -> list[dict]:
        mensagens = []
        if incluir_schema:
            import json
            mensagens.append({"role": "system", "content":
                "Responda somente com JSON seguindo este schema: " +
                json.dumps(self._schema.model_json_schema(), ensure_ascii=False)})
        mensagens.append({"role": "user", "content": prompt})
        return mensagens


def selecionar_modelo_disponivel(provedor, modelo: str, disponiveis: list[str]) -> str:
    """Mantém compatibilidade com providers simulados antigos sem esse método."""
    if modelo in disponiveis:
        return modelo
    selecionar = getattr(provedor, "selecionar_modelo", None)
    if callable(selecionar):
        return selecionar(modelo, disponiveis)
    raise ErroProvider("O modelo selecionado não está disponível para esta chave/projeto.", codigo=404)
