"""Implementa??o Gemini existente, com pol?ticas preservadas por modo."""
from __future__ import annotations
import json
import re
from time import perf_counter
from pydantic import BaseModel, Field
from .base import AIProvider
from .errors import ErroProvider as ErroGemini
from .errors import erro_seguro
from .response import com_metadados
from .diagnostico_gemini import erro_conexao_seguro


class _CandidatoResposta(BaseModel):
    inicio: float
    fim: float
    descricao: str
    motivo: str = ""
    titulos: list[str] = Field(default_factory=list)
    inicio_original: float | None = None
    gancho: str = ""
    excepcional_curto: bool = False


class _RespostaGemini(BaseModel):
    candidatos: list[_CandidatoResposta] = Field(default_factory=list)


def _erro_seguro(erro: Exception) -> ErroGemini:
    seguro = erro_seguro(erro, "gemini")
    if seguro.tipo in ("rede", "timeout"):
        return seguro
    codigo = getattr(erro, "code", None) or getattr(erro, "status_code", None)
    texto = str(erro)
    if codigo == 429 or "429" in texto or "RESOURCE_EXHAUSTED" in texto:
        achado = re.search(r"(?:retry(?:Delay| in)?[^0-9]{0,12})([0-9]+(?:\.[0-9]+)?)", texto, re.I)
        espera = seguro.aguardar_segundos
        if espera is None:
            espera = float(achado.group(1)) if achado else None
        detalhe = f" Aguarde {espera:g} segundos." if espera is not None else " Aguarde o prazo informado pelo serviço."
        return ErroGemini("Limite gratuito temporariamente atingido (HTTP 429)." + detalhe,
                          codigo=429, aguardar_segundos=espera)
    if codigo in (401, 403) or "API_KEY" in texto.upper():
        return ErroGemini("A chave foi recusada ou não tem acesso ao modelo selecionado.", codigo=codigo)
    if codigo == 400 or "INVALID_ARGUMENT" in texto:
        return ErroGemini("A Gemini recusou o formato da solicitação. Selecione outro modelo compatível e tente novamente.",
                          codigo=400)
    if codigo == 404 or "NOT_FOUND" in texto:
        return ErroGemini(
            "O modelo selecionado aparece na listagem, mas não aceita geração neste projeto. "
            "Abra Configurações de IA, escolha outro modelo e salve as preferências.", codigo=404
        )
    if codigo == 503 or "UNAVAILABLE" in texto:
        return ErroGemini("A Gemini está temporariamente indisponível. Tente novamente mais tarde.", codigo=503)
    if "TIMEOUT" in texto.upper() or "CONNECT" in texto.upper():
        return ErroGemini("A conexão com a Gemini expirou ou foi interrompida.")
    return ErroGemini("Não foi possível comunicar com a Gemini. Verifique a conexão e o modelo selecionado.",
                      codigo=codigo)


class GeminiProvider(AIProvider):
    provider = "gemini"

    def __init__(self, chave: str):
        from google import genai
        self._cliente = genai.Client(api_key=chave)

    def testar_conexao(self, modelo: str | None = None) -> bool | dict:
        if modelo is None:
            self.listar_modelos()
            return True
        from google.genai import types
        inicio = perf_counter()
        try:
            resposta = self._cliente.models.generate_content(
                model=modelo, contents="Responda apenas OK",
                config=types.GenerateContentConfig(
                    max_output_tokens=128, temperature=0,
                    http_options=types.HttpOptions(
                        timeout=30000, retry_options=types.HttpRetryOptions(attempts=1))))
            if not resposta.text or resposta.text.strip().upper() != "OK":
                raise ErroGemini("O modelo não confirmou o teste com OK.")
            return com_metadados({"ok": True}, self.provider, modelo, inicio)
        except Exception as erro:
            raise erro_conexao_seguro(erro) from None

    def listar_modelos(self) -> list[str]:
        try:
            modelos = []
            for item in self._cliente.models.list():
                acoes = list(getattr(item, "supported_actions", None) or [])
                if acoes and "generateContent" not in acoes:
                    continue
                nome = str(getattr(item, "name", "") or "").removeprefix("models/")
                if nome.startswith("gemini-"):
                    modelos.append(nome)
            return sorted(set(modelos))
        except Exception as erro:
            raise _erro_seguro(erro) from None

    def analisar(self, modelo: str, prompt: str) -> dict:
        return self._analisar_uma_vez(modelo, prompt)

    def analisar_uma_vez(self, modelo: str, prompt: str) -> dict:
        from google.genai import types
        return self._analisar_uma_vez(modelo, prompt, http_options=types.HttpOptions(
            timeout=360000, retry_options=types.HttpRetryOptions(attempts=1)))

    def _analisar_uma_vez(self, modelo: str, prompt: str, *, http_options=None) -> dict:
        inicio = perf_counter()
        from google.genai import types
        try:
            resposta = self._cliente.models.generate_content(
                model=modelo,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=_RespostaGemini,
                    temperature=0.55,
                    max_output_tokens=8192,
                    http_options=http_options,
                ),
            )
            candidatos_resposta = list(getattr(resposta, "candidates", None) or [])
            if candidatos_resposta:
                motivo_fim = str(getattr(candidatos_resposta[0], "finish_reason", "") or "")
                if "MAX_TOKENS" in motivo_fim.upper():
                    raise ErroGemini(
                        "A resposta atingiu o limite de saída antes de terminar. Nenhuma lista parcial foi salva."
                    )
            if resposta.parsed is not None:
                dados = resposta.parsed.model_dump()
            elif resposta.text:
                dados = json.loads(resposta.text)
            else:
                raise ErroGemini("A Gemini devolveu uma resposta vazia, sem candidatos nem justificativa.")
            if not isinstance(dados, dict) or not isinstance(dados.get("candidatos"), list):
                raise ErroGemini("A resposta da Gemini não contém uma lista de candidatos válida.")
            return com_metadados(dados, self.provider, modelo, inicio)
        except ErroGemini:
            raise
        except Exception as erro:
            raise _erro_seguro(erro) from None


class GeminiAgencyProvider(GeminiProvider):
    TIMEOUT_MS = 6 * 60 * 1000   # uma chamada travada não pode prender a tela para sempre

    def __init__(self, chave: str, schema):
        self._schema = schema
        from google import genai
        from google.genai import types
        self._cliente = genai.Client(api_key=chave,
                                     http_options=types.HttpOptions(timeout=self.TIMEOUT_MS))

    # Na conta gratuita cada tentativa recusada (503) também consome a cota diária
    # (modelos Flash: só 20 pedidos/dia). Por isso, no máximo UMA nova tentativa.
    ESPERAS_RETENTATIVA = (20,)

    def analisar(self, modelo: str, prompt: str) -> dict:
        """Sobrecarga momentânea (503) é repetida; timeout, 429, chave e formato não."""
        import time

        for tentativa, espera in enumerate((*self.ESPERAS_RETENTATIVA, None), 1):
            try:
                return self._analisar_uma_vez(modelo, prompt)
            except ErroGemini as erro:
                if erro.codigo != 503 or espera is None:
                    raise
                time.sleep(espera)
        raise ErroGemini("A Gemini continua indisponível.")

    def _analisar_uma_vez(self, modelo: str, prompt: str, *, http_options=None) -> dict:
        inicio = perf_counter()
        from google.genai import types
        try:
            resposta = self._cliente.models.generate_content(
                model=modelo, contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=self._schema,
                    temperature=0.5,
                    max_output_tokens=16384,
                    http_options=http_options,
                ),
            )
            candidatos = list(getattr(resposta, "candidates", None) or [])
            if candidatos and "MAX_TOKENS" in str(getattr(candidatos[0], "finish_reason", "")).upper():
                raise ErroGemini("A resposta atingiu o limite de saída; nada foi salvo.")
            if resposta.parsed is not None:
                return com_metadados(resposta.parsed.model_dump(), self.provider, modelo, inicio)
            if resposta.text:
                return com_metadados(json.loads(resposta.text), self.provider, modelo, inicio)
            raise ErroGemini("A Gemini devolveu uma resposta vazia para o Modo Agência.")
        except ErroGemini:
            raise
        except Exception as erro:
            raise _erro_seguro(erro) from None


