"""Normalização de transporte JSON; a validação editorial continua no aplicativo."""
import json
from time import perf_counter

from .errors import ErroProvider


def _constante_invalida(_valor):
    raise ValueError("Constante numérica inválida em JSON.")


def com_metadados(dados: dict, provider: str, modelo: str, inicio: float) -> dict:
    latencia_ms = round(max(0.0, perf_counter() - inicio) * 1000, 3)
    return {**dados, "provider": provider, "model": modelo,
            "latency_ms": latencia_ms,
            "provider_solicitado": provider,
            "provider_utilizado": provider,
            "modelo_utilizado": modelo,
            "numero_de_tentativas": 1,
            "fallback_ocorreu": False,
            "motivo_fallback": None,
            "latencia_total_ms": latencia_ms}


def normalizar_resposta(conteudo, chave: str) -> dict:
    if isinstance(conteudo, list):
        # Mistral pode devolver blocos de conteúdo em vez de uma string única.
        conteudo = "".join(bloco.get("text", "") if isinstance(bloco, dict)
                           else getattr(bloco, "text", "") for bloco in conteudo)
    if isinstance(conteudo, str):
        texto = conteudo.strip()
        if texto.startswith("```") and texto.endswith("```"):
            texto = texto.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            conteudo = json.loads(texto, parse_constant=_constante_invalida)
        except (ValueError, TypeError):
            raise ErroProvider("A IA devolveu JSON inválido; nenhum conteúdo parcial foi aceito.") from None
    if not isinstance(conteudo, dict):
        raise ErroProvider("A resposta da IA deve ser um objeto JSON.")
    alias = {"candidatos": "candidates", "historias": "stories"}[chave]
    itens = conteudo.get(chave, conteudo.get(alias))
    if not isinstance(itens, list) or any(not isinstance(item, dict) for item in itens):
        raise ErroProvider(f"A resposta da IA não contém uma lista válida de {chave}.")
    aliases = {"start": "inicio", "end": "fim", "description": "descricao",
               "titles": "titulos", "reason": "motivo"} if chave == "candidatos" else {}
    normalizados = []
    for item in itens:
        copia = dict(item)
        for origem, destino in aliases.items():
            if origem in copia:
                if destino not in copia:
                    copia[destino] = copia[origem]
                del copia[origem]
        normalizados.append(copia)
    # Metadados vindos do modelo não são confiáveis; são calculados pelo provider.
    return {chave: normalizados}
