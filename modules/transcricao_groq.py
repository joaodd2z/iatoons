"""Explicitly authorized audio-only Groq Whisper adapter; no editorial provider changes."""
from fractions import Fraction
from pathlib import Path
import tempfile
from time import perf_counter

from modules.performance import medir_operacao, anotar

MODELO_GROQ = "whisper-large-v3-turbo"
PARAMETROS_GROQ = {"backend": "groq_whisper", "modo": "groq_whisper", "modelo": MODELO_GROQ,
                   "temperature": 0, "timestamp_granularities": ["word", "segment"],
                   "response_format": "verbose_json", "audio": "flac/16000/mono", "versao": 1}


def _cliente_groq():
    from groq import Groq
    from modules.credenciais import obter_chave
    chave = obter_chave("groq")
    if not chave:
        raise RuntimeError("Configure uma credencial Groq antes de transcrever na nuvem.")
    try:
        return Groq(api_key=chave, timeout=90, max_retries=0)
    except Exception as erro:
        from modules.ai.errors import erro_seguro
        raise erro_seguro(erro, "groq") from None


@medir_operacao("audio_preparacao_groq", backend="pyav/flac")
def preparar_audio(caminho, destino, cancelada_ref=None):
    import av
    import numpy as np
    from faster_whisper.audio import decode_audio
    from modules.transcricao import _cancelar_se_necessario
    _cancelar_se_necessario(cancelada_ref)
    audio = decode_audio(str(caminho), sampling_rate=16000)
    _cancelar_se_necessario(cancelada_ref)
    with av.open(str(destino), mode="w") as container:
        stream = container.add_stream("flac", rate=16000)
        stream.layout = "mono"
        for offset in range(0, len(audio), 4096):
            _cancelar_se_necessario(cancelada_ref)
            samples = np.clip(audio[offset:offset+4096]*32767, -32768, 32767).astype(np.int16)[None, :]
            frame = av.AudioFrame.from_ndarray(samples, format="s16", layout="mono")
            frame.sample_rate, frame.pts, frame.time_base = 16000, offset, Fraction(1, 16000)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return len(audio)/16000


def converter_resposta(dados):
    import math
    from modules.transcricao import PalavraTranscrita, SegmentoTranscricao
    palavras = [PalavraTranscrita(float(p["start"]), float(p["end"]), p["word"])
                for p in dados.get("words", [])]
    segmentos = []
    for s in dados.get("segments", []):
        inicio, fim = float(s["start"]), float(s["end"])
        segmento = SegmentoTranscricao(inicio, fim, s["text"].strip(),
                         [p for p in palavras if inicio <= (p.inicio+p.fim)/2 < fim])
        if segmento.texto and not segmento.palavras:
            raise ValueError("Groq retornou fala sem timestamps de palavras.")
        segmentos.append(segmento)
    if dados.get("text", "").strip() and not segmentos:
        raise ValueError("Groq retornou texto sem segmentos temporizados.")
    duracao = float(dados.get("duration", 0))
    if not math.isfinite(duracao) or duracao <= 0:
        raise ValueError("Duracao invalida na resposta Groq.")
    for s in segmentos:
        if not (math.isfinite(s.inicio) and math.isfinite(s.fim) and 0 <= s.inicio < s.fim <= duracao+.25):
            raise ValueError("Timestamp de segmento Groq invalido.")
        for p in s.palavras:
            if not (math.isfinite(p.inicio) and math.isfinite(p.fim)
                    and s.inicio-.25 <= p.inicio < p.fim <= s.fim+.25):
                raise ValueError("Timestamp de palavra Groq invalido.")
    atribuidas = [p for s in segmentos for p in s.palavras]
    if len(atribuidas) != len(palavras) or len({id(p) for p in atribuidas}) != len(palavras):
        raise ValueError("Palavras Groq fora dos segmentos correspondentes.")
    if any(a.inicio > b.inicio for a,b in zip(palavras,palavras[1:])):
        raise ValueError("Palavras Groq fora de ordem temporal.")
    if any(a.inicio > b.inicio for a,b in zip(segmentos,segmentos[1:])):
        raise ValueError("Segmentos Groq fora de ordem temporal.")
    return segmentos, duracao


@medir_operacao("transcricao_groq", backend="groq_whisper")
def transcrever_groq(caminho, idioma, usar_cache, cancelada_ref, progresso_cb, *, autorizar_nuvem=False):
    from datetime import datetime, timezone
    from modules.transcricao import (Transcricao, buscar_transcricao_cache, salvar_transcricao,
                                    _identidade_video, _cancelar_se_necessario)
    if not autorizar_nuvem:
        raise ValueError("Envio de audio externo exige autorizacao explicita.")
    if idioma not in (None, "pt"):
        raise ValueError("Idioma nao suportado.")
    inicio = perf_counter()
    tempos = {}
    _cancelar_se_necessario(cancelada_ref)
    if usar_cache:
        cache = buscar_transcricao_cache(caminho, MODELO_GROQ, idioma, configuracao=PARAMETROS_GROQ)
        if cache:
            cache.cache_reutilizado = True
            if progresso_cb:
                progresso_cb(100, "Transcricao Groq reutilizada do cache, sem envio de audio.")
            return cache
    cliente = _cliente_groq()
    try:
        with tempfile.TemporaryDirectory(prefix="yatuns_audio_") as directory:
            audio = Path(directory) / "audio.flac"
            if progresso_cb:
                progresso_cb(3, "Preparando audio para Groq Whisper (envio autorizado).")
            fase = perf_counter()
            preparar_audio(caminho, audio, cancelada_ref)
            tempos["preparacao_s"] = perf_counter() - fase
            if audio.stat().st_size > 25*1024*1024:
                raise ValueError("Audio excede o limite conservador de upload Groq de 25 MB; use CPU.")
            _cancelar_se_necessario(cancelada_ref)
            opcoes = {"model": MODELO_GROQ, "temperature": 0, "response_format": "verbose_json",
                      "timestamp_granularities": ["word", "segment"]}
            if idioma:
                opcoes["language"] = idioma
            anotar(provider="groq", modelo=MODELO_GROQ, entrada_bytes=audio.stat().st_size)
            if progresso_cb:
                progresso_cb(15, "Transcrevendo via Groq Whisper; cancelamento respeitado ao retornar a chamada.")
            with audio.open("rb") as arquivo:
                try:
                    fase = perf_counter()
                    resposta = cliente.audio.transcriptions.create(file=arquivo, **opcoes)
                    tempos["upload_inferencia_transporte_s"] = perf_counter() - fase
                except Exception as erro:
                    from modules.ai.errors import erro_seguro
                    raise erro_seguro(erro, "groq") from None
            _cancelar_se_necessario(cancelada_ref)
            dados = resposta.model_dump() if hasattr(resposta, "model_dump") else resposta
    finally:
        cliente.close()
    segmentos, duracao = converter_resposta(dados)
    resultado = Transcricao(str(Path(caminho).resolve()), _identidade_video(caminho, MODELO_GROQ, idioma),
        MODELO_GROQ, idioma, dados.get("language") or idioma or "", 0, duracao,
        datetime.now(timezone.utc).isoformat(), segmentos, backend="groq_whisper", modo="groq_whisper",
        parametros=PARAMETROS_GROQ.copy(), tempo_transcricao_s=perf_counter()-inicio,
        tempos_backend={**tempos, "total_s": perf_counter()-inicio, "upload_s": None,
            "inferencia_s": None, "custo": None, "cota": None,
            "observacao": "SDK nao separa upload/inferencia; custo e cota nao estimados."})
    _cancelar_se_necessario(cancelada_ref)
    salvar_transcricao(resultado)
    if progresso_cb:
        progresso_cb(100, "Transcricao Groq concluida e salva no cache local.")
    return resultado
