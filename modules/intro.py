"""Detecção conservadora e persistência do intervalo de abertura."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path

from modules.ganchos_ab import TrechoFonte
from modules.preset import DADOS_DIR
from modules.transcricao import Transcricao


INTROS_DIR = DADOS_DIR / "intros"
MARCADORES = (
    "violência em filmes", "sexo na tv", "velhos costumes", "costumávamos ver",
    "uma delícia de família", "família da pesada", "versão brasileira",
    "distribuição fox", "opening theme song",
)


@dataclass
class IntervaloIntro:
    inicio: float
    fim: float
    ativo: bool = True
    automatico: bool = True
    confianca: float = 0.0
    motivo: str = ""

    @property
    def duracao(self):
        return self.fim - self.inicio

    def validar(self, duracao_video: float) -> "IntervaloIntro":
        if self.inicio < 0 or self.fim <= self.inicio or self.fim > duracao_video + 0.001:
            raise ValueError("Intervalo da intro inválido para a duração do vídeo.")
        if self.duracao > 180:
            raise ValueError("A intro não pode ultrapassar três minutos.")
        return self


def detectar_intro(transcricao: Transcricao) -> IntervaloIntro | None:
    """Localiza um bloco de abertura; conteúdo anterior ao bloco nunca é marcado."""
    evidencias = []
    for indice, segmento in enumerate(transcricao.segmentos):
        if segmento.inicio > min(180.0, transcricao.duracao):
            break
        texto = segmento.texto.casefold()
        hits = [m for m in MARCADORES if m in texto]
        if hits:
            evidencias.append((indice, segmento, hits))
    if len(evidencias) < 2:
        return None
    # Exige evidências agrupadas; isso evita classificar uma cena narrativa isolada.
    melhor = []
    for evidencia in evidencias:
        grupo = [e for e in evidencias if 0 <= e[1].inicio - evidencia[1].inicio <= 75]
        if len(grupo) > len(melhor):
            melhor = grupo
    if len(melhor) < 2:
        return None
    primeiro_indice = melhor[0][0]
    ultimo_indice = melhor[-1][0]
    inicio = transcricao.segmentos[primeiro_indice].inicio
    fim = transcricao.segmentos[ultimo_indice].fim
    fim_ultima_evidencia = fim
    # Inclui versos/créditos contíguos, mas nunca recua para uma cena anterior.
    i = primeiro_indice - 1
    while i >= 0 and inicio - transcricao.segmentos[i].fim <= 1.5:
        if inicio - transcricao.segmentos[i].inicio > 20:
            break
        inicio = transcricao.segmentos[i].inicio
        i -= 1
    i = ultimo_indice + 1
    while i < len(transcricao.segmentos) and transcricao.segmentos[i].inicio - fim <= 1.5:
        if transcricao.segmentos[i].fim - fim_ultima_evidencia > 5.0:
            break
        fim = transcricao.segmentos[i].fim
        i += 1
    confianca = min(1.0, 0.55 + 0.12 * len(melhor))
    return IntervaloIntro(inicio, fim, True, True, confianca,
                          f"{len(melhor)} marcadores de abertura agrupados")


def caminho_intro(transcricao: Transcricao) -> Path:
    return INTROS_DIR / f"{transcricao.video_id}.json"


def salvar_intro(transcricao: Transcricao, intervalo: IntervaloIntro) -> Path:
    intervalo.validar(transcricao.duracao)
    destino = caminho_intro(transcricao)
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(intervalo), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(destino)
    return destino


def carregar_intro(transcricao: Transcricao) -> IntervaloIntro | None:
    origem = caminho_intro(transcricao)
    if not origem.exists():
        return None
    try:
        return IntervaloIntro(**json.loads(origem.read_text(encoding="utf-8"))).validar(transcricao.duracao)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def remover_intro(trechos: list[TrechoFonte], intervalo: IntervaloIntro | None) -> list[TrechoFonte]:
    if not intervalo or not intervalo.ativo:
        return list(trechos)
    resultado = []
    for trecho in trechos:
        if trecho.fim <= intervalo.inicio or trecho.inicio >= intervalo.fim:
            resultado.append(trecho)
            continue
        if trecho.inicio < intervalo.inicio:
            resultado.append(TrechoFonte(trecho.inicio, intervalo.inicio))
        if trecho.fim > intervalo.fim:
            resultado.append(TrechoFonte(intervalo.fim, trecho.fim))
    return [t for t in resultado if t.duracao > 0.05]
