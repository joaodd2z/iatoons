"""Planos A/B de abertura, derivados localmente da transcrição já armazenada."""

from __future__ import annotations

from dataclasses import dataclass
import re

from modules.cortes import Corte
from modules.legendas import BlocoLegenda, recortar_legendas
from modules.transcricao import Transcricao


@dataclass(frozen=True)
class TrechoFonte:
    inicio: float
    fim: float

    @property
    def duracao(self) -> float:
        return self.fim - self.inicio


def _pontuar(texto: str) -> int:
    t = texto.strip().casefold()
    pontos = 0
    if "?" in t:
        pontos += 5
    if "!" in t:
        pontos += 4
    if re.search(r"\b(como|por que|quem|onde|quê|o que|você|não|nunca|espera|olha)\b", t):
        pontos += 2
    if re.search(r"\b(absurdo|louco|problema|socorro|mentira|impossível|adeus)\b", t):
        pontos += 3
    return pontos


def identificar_gancho_b(corte: Corte, transcricao: Transcricao) -> tuple[TrechoFonte | None, str]:
    """Escolhe bloco completo de 2–4 s no começo da cena, sem inferir conteúdo ausente."""
    limite = min(corte.inicio + 20.0, corte.fim - 3.0)
    candidatos: list[tuple[int, float, TrechoFonte, str]] = []
    for segmento in transcricao.segmentos:
        if segmento.inicio < corte.inicio or segmento.fim > limite:
            continue
        # Evita uma versão B que apenas repita a abertura já vista imediatamente.
        if segmento.inicio < corte.inicio + 3.0:
            continue
        if segmento.palavras:
            palavras = segmento.palavras
            for i, primeira in enumerate(palavras):
                texto = ""
                for ultima in palavras[i:]:
                    texto += ultima.texto
                    duracao = ultima.fim - primeira.inicio
                    if duracao > 4.0:
                        break
                    termina = ultima.texto.rstrip().endswith((".", "!", "?", ":", ";"))
                    if 2.0 <= duracao <= 4.0 and termina:
                        trecho = TrechoFonte(float(primeira.inicio), float(ultima.fim))
                        candidatos.append((_pontuar(texto), -trecho.inicio, trecho, texto.strip()))
        else:
            duracao = segmento.fim - segmento.inicio
            if 2.0 <= duracao <= 4.0 and segmento.texto.strip():
                trecho = TrechoFonte(segmento.inicio, segmento.fim)
                candidatos.append((_pontuar(segmento.texto), -trecho.inicio, trecho,
                                   segmento.texto.strip()))
    if not candidatos:
        return None, "Nenhum bloco completo de 2 a 4 segundos foi encontrado nos primeiros 20 segundos."
    pontos, _ordem, trecho, texto = max(candidatos, key=lambda item: (item[0], item[1]))
    if pontos <= 0:
        return None, "Não há fala intrigante suficiente para justificar uma abertura antecipada."
    resumo = " ".join(texto.split())
    if len(resumo) > 70:
        resumo = resumo[:67].rstrip() + "…"
    return trecho, f"Gancho antecipado: “{resumo}”"


def segmentos_da_versao(corte: Corte, versao: str) -> list[TrechoFonte]:
    if versao == "B" and corte.gancho_b_inicio is not None and corte.gancho_b_fim is not None:
        return [TrechoFonte(corte.gancho_b_inicio, corte.gancho_b_fim),
                TrechoFonte(corte.inicio, corte.fim)]
    return [TrechoFonte(corte.inicio, corte.fim)]


def remapear_legendas(blocos: list[BlocoLegenda], trechos: list[TrechoFonte]) -> list[BlocoLegenda]:
    resultado: list[BlocoLegenda] = []
    deslocamento = 0.0
    for trecho in trechos:
        recortados = recortar_legendas(blocos, trecho.inicio, trecho.fim)
        for bloco in sorted(recortados, key=lambda item: (item.inicio, item.fim)):
            resultado.append(BlocoLegenda(bloco.inicio + deslocamento,
                                          bloco.fim + deslocamento, bloco.texto))
        deslocamento += trecho.duracao
    return resultado

