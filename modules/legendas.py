"""Gera, recorta e exporta legendas a partir da transcrição Whisper."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from modules.transcricao import Transcricao


@dataclass
class BlocoLegenda:
    inicio: float
    fim: float
    texto: str

    def como_dict(self) -> dict:
        return asdict(self)


def gerar_blocos_legenda(transcricao: Transcricao, max_palavras: int = 5) -> list[BlocoLegenda]:
    """Agrupa palavras consecutivas sem perder os timestamps do Whisper."""
    if not 1 <= max_palavras <= 12:
        raise ValueError("A quantidade de palavras por bloco deve estar entre 1 e 12.")
    blocos: list[BlocoLegenda] = []

    for segmento in transcricao.segmentos:
        if segmento.palavras:
            grupo = []
            for palavra in segmento.palavras:
                grupo.append(palavra)
                encerra_frase = palavra.texto.rstrip().endswith((".", "!", "?", ":", ";"))
                if len(grupo) >= max_palavras or encerra_frase:
                    blocos.append(BlocoLegenda(
                        grupo[0].inicio, grupo[-1].fim,
                        "".join(p.texto for p in grupo).strip(),
                    ))
                    grupo = []
            if grupo:
                blocos.append(BlocoLegenda(
                    grupo[0].inicio, grupo[-1].fim,
                    "".join(p.texto for p in grupo).strip(),
                ))
        elif segmento.texto.strip():
            blocos.append(BlocoLegenda(segmento.inicio, segmento.fim, segmento.texto.strip()))
    return blocos


def recortar_legendas(blocos: list[BlocoLegenda], inicio_corte: float,
                      fim_corte: float) -> list[BlocoLegenda]:
    """Seleciona legendas do corte e desloca seu início para 00:00:00."""
    if fim_corte <= inicio_corte:
        raise ValueError("Intervalo de corte inválido.")
    resultado = []
    duracao = fim_corte - inicio_corte
    for bloco in blocos:
        if bloco.fim <= inicio_corte or bloco.inicio >= fim_corte:
            continue
        inicio = max(0.0, bloco.inicio - inicio_corte)
        fim = min(duracao, bloco.fim - inicio_corte)
        if fim > inicio:
            resultado.append(BlocoLegenda(inicio, fim, bloco.texto))
    return resultado


def _tempo_srt(segundos: float) -> str:
    total_ms = max(0, round(segundos * 1000))
    horas, resto = divmod(total_ms, 3_600_000)
    minutos, resto = divmod(resto, 60_000)
    secs, ms = divmod(resto, 1000)
    return f"{horas:02d}:{minutos:02d}:{secs:02d},{ms:03d}"


def salvar_srt(blocos: list[BlocoLegenda], caminho: str | Path) -> Path:
    destino = Path(caminho)
    destino.parent.mkdir(parents=True, exist_ok=True)
    partes = []
    for indice, bloco in enumerate(blocos, start=1):
        partes.append(
            f"{indice}\n{_tempo_srt(bloco.inicio)} --> {_tempo_srt(bloco.fim)}\n{bloco.texto}\n"
        )
    destino.write_text("\n".join(partes), encoding="utf-8-sig")
    return destino
