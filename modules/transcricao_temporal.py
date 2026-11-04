"""Timestamp contracts and local confidence, independent of editorial validators."""
from copy import deepcopy
import math


def validar_timestamps(segmentos, duracao, tolerancia=.025):
    problemas = []
    if not math.isfinite(duracao) or duracao <= 0:
        return [{"codigo": "duracao_invalida", "segmento": None, "critico": True}]
    anterior = None
    for i, s in enumerate(segmentos):
        def erro(codigo, critico=True):
            problemas.append({"codigo": codigo, "segmento": i, "critico": critico})
        if not (math.isfinite(s.inicio) and math.isfinite(s.fim)
                and 0 <= s.inicio < s.fim <= duracao + tolerancia):
            erro("segmento_fora_audio")
        if anterior:
            if s.inicio < anterior.inicio - tolerancia:
                erro("segmento_regressivo")
            if s.inicio < anterior.fim - .08:
                erro("sobreposicao_segmentos")
        anterior = s
        palavra_anterior = None
        for p in s.palavras:
            if not (math.isfinite(p.inicio) and math.isfinite(p.fim)
                    and 0 <= p.inicio <= p.fim <= duracao + tolerancia
                    and s.inicio - tolerancia <= p.inicio <= p.fim <= s.fim + tolerancia):
                erro("palavra_fora_segmento")
            if p.inicio == p.fim:
                erro("palavra_sem_duracao", False)
            if palavra_anterior:
                if p.inicio < palavra_anterior.inicio - tolerancia:
                    erro("palavra_regressiva")
                if p.inicio < palavra_anterior.fim - .08:
                    erro("sobreposicao_palavras")
                if p.inicio - palavra_anterior.fim > 1.2:
                    erro("gap_interno_suspeito", False)
            palavra_anterior = p
        if s.texto.strip() and not s.palavras:
            erro("fala_sem_palavras", not s.word_timestamps_invalidated)
    return problemas


def confianca_temporal(segmento, duracao, anterior=None, vad_intervalos=None):
    problemas = validar_timestamps([segmento], duracao)
    sinais = [p["codigo"] for p in problemas]
    score = 100 - sum(40 if p["critico"] else 15 for p in problemas)
    if anterior and segmento.inicio < anterior.fim - .08:
        score -= 40
        sinais.append("overlap_anterior")
    palavras = segmento.palavras
    if palavras:
        curtas = sum(p.fim - p.inicio < .04 for p in palavras)
        longas = sum(p.fim - p.inicio > 1.5 for p in palavras)
        if curtas:
            score -= min(35, curtas * 8)
            sinais.append("palavras_comprimidas")
        if longas:
            score -= min(30, longas * 10)
            sinais.append("palavras_longas")
        probabilidades = [p.probabilidade for p in palavras if p.probabilidade > 0]
        if probabilidades and sum(probabilidades) / len(probabilidades) < .6:
            score -= 25
            sinais.append("baixa_probabilidade_modelo")
        if (abs(segmento.inicio - palavras[0].inicio) > .2
                or abs(segmento.fim - palavras[-1].fim) > .2):
            score -= 25
            sinais.append("limites_inconsistentes")
    if vad_intervalos is not None:
        coberto = sum(max(0, min(segmento.fim, b) - max(segmento.inicio, a)) for a, b in vad_intervalos)
        if coberto < (segmento.fim - segmento.inicio) * .5:
            score -= 25
            sinais.append("conflito_vad")
    return {"timestamp_confidence": max(0, min(100, score)), "sinais": list(dict.fromkeys(sinais)),
            "precisao_absoluta_comprovada": False}


def recompor_chunk(segmentos, offset, duracao_audio, *, relativos=False, duracao_chunk=None):
    """Only adapters declaring relative times add offsets. Official Whisper is absolute."""
    if not math.isfinite(offset) or offset < 0:
        raise ValueError("Offset de chunk invalido.")
    resultado = deepcopy(segmentos)
    if relativos:
        if duracao_chunk is None:
            raise ValueError("Tempos relativos exigem duracao do chunk.")
        if any(p["critico"] for p in validar_timestamps(resultado, duracao_chunk)):
            raise ValueError("Timestamp relativo fora do chunk.")
        for s in resultado:
            s.inicio += offset
            s.fim += offset
            for p in s.palavras:
                p.inicio += offset
                p.fim += offset
    if any(p["critico"] for p in validar_timestamps(resultado, duracao_audio)):
        raise ValueError("Timestamp absoluto invalido apos recomposicao.")
    if duracao_chunk is not None and any(s.inicio < offset - .025 or s.fim > offset + duracao_chunk + .025
                                          for s in resultado):
        raise ValueError("Offset perdido ou timestamp fora das fronteiras do chunk.")
    return resultado


def chunks_sem_vad(audio, tamanho=30, sampling_rate=16000):
    """Explicit sample-grid clips; batched cannot disable VAD on long audio otherwise."""
    if not 1 <= tamanho <= 30:
        raise ValueError("Chunk deve ter de 1 a 30 segundos.")
    return [{"start": a / sampling_rate, "end": min(a + int(tamanho * sampling_rate), len(audio)) / sampling_rate}
            for a in range(0, len(audio), int(tamanho * sampling_rate))]
