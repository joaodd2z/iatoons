"""Whisper forced alignment of supplied text, never decoding a second transcript."""
from copy import deepcopy
from time import perf_counter

from modules.transcricao_temporal import confianca_temporal, recompor_chunk, validar_timestamps


def alinhar_texto(audio, texto, modelo, idioma="pt", cancelada_ref=None):
    import numpy as np
    from faster_whisper.tokenizer import Tokenizer
    from faster_whisper.transcribe import merge_punctuations
    from modules.transcricao import PalavraTranscrita, SegmentoTranscricao, _cancelar_se_necessario
    from modules.transcricao_benchmark import normalizar
    _cancelar_se_necessario(cancelada_ref)
    if not 0 < len(audio) <= 30 * 16000:
        raise ValueError("Alinhamento aceita somente trechos de ate 30 segundos.")
    tokenizer = Tokenizer(modelo.hf_tokenizer, modelo.model.is_multilingual, task="transcribe", language=idioma)
    tokens = tokenizer.encode(texto)
    if not tokens or len(tokens) > 440:
        raise ValueError("Texto do trecho excede a janela de alinhamento.")
    features = modelo.feature_extractor(audio)
    frames = min(features.shape[-1] - 1, round(len(audio) / 160))
    encoded = modelo.encode(features[:, :3000][np.newaxis, :])
    _cancelar_se_necessario(cancelada_ref)
    alinhamento = modelo.find_alignment(tokenizer, [tokens], encoded, [frames])[0]
    merge_punctuations(alinhamento, "\"'([{-", "\"'.。,，!！?？:：)]}、")
    palavras = [PalavraTranscrita(float(w["start"]), float(w["end"]), w["word"], float(w["probability"]))
                for w in alinhamento if w["word"].strip()]
    _cancelar_se_necessario(cancelada_ref)
    if normalizar(" ".join(p.texto for p in palavras)) != normalizar(texto) or not palavras:
        raise ValueError("Alinhamento nao preservou o texto fornecido.")
    s = SegmentoTranscricao(palavras[0].inicio, palavras[-1].fim, texto, palavras)
    if any(p["critico"] for p in validar_timestamps([s], len(audio) / 16000)):
        raise ValueError("Alinhamento retornou tempos invalidos.")
    if any(p.fim <= p.inicio for p in palavras):
        raise ValueError("Alinhamento retornou palavra sem duracao.")
    return s


def _alinhar_indice(transcricao, indice, audio, modelo, cancelada_ref=None):
    from modules.transcricao import _cancelar_se_necessario
    _cancelar_se_necessario(cancelada_ref)
    s = transcricao.segmentos[indice]
    if not 0 <= s.inicio < s.fim <= len(audio) / 16000 + .025:
        raise ValueError("Limites do trecho invalidos para alinhamento.")
    a, b = max(0, s.inicio - .35), min(len(audio) / 16000, s.fim + .35)
    if b - a > 30:
        raise ValueError("Trecho muito longo; nenhuma retranscricao foi feita.")
    ia, ib = round(a * 16000), round(b * 16000)
    idioma = transcricao.idioma_solicitado or transcricao.idioma_detectado or "pt"
    idioma = {"portuguese": "pt", "pt-BR": "pt"}.get(idioma, idioma)
    alinhado = alinhar_texto(audio[ia:ib], s.texto, modelo, idioma, cancelada_ref)
    alinhado = recompor_chunk([alinhado], ia / 16000, transcricao.duracao,
                             relativos=True, duracao_chunk=(ib - ia) / 16000)[0]
    copia = deepcopy(transcricao.segmentos)
    copia[indice] = alinhado
    if any(p["critico"] for p in validar_timestamps(copia, transcricao.duracao) if p["segmento"] in (indice, indice + 1)):
        raise ValueError("Alinhamento conflita com os trechos vizinhos.")
    return alinhado


def refinar_suspeitos(transcricao, modelo, configuracao, cancelada_ref=None, *, vad_intervalos=None):
    from faster_whisper.audio import decode_audio
    from modules.transcricao import _cancelar_se_necessario
    inicio = perf_counter()
    scores = [confianca_temporal(s, transcricao.duracao, transcricao.segmentos[i-1] if i else None, vad_intervalos)
              for i, s in enumerate(transcricao.segmentos)]
    suspeitos = [i for i, score in enumerate(scores) if score["timestamp_confidence"] < configuracao.refino_score
                 and not transcricao.segmentos[i].word_timestamps_invalidated]
    selecionados, total = [], 0
    for i in sorted(suspeitos, key=lambda i: scores[i]["timestamp_confidence"]):
        s = transcricao.segmentos[i]
        custo = s.fim - s.inicio + .7
        if 0 < custo <= 30 and total + custo <= transcricao.duracao * configuracao.refino_fracao_maxima:
            selecionados.append(i)
            total += custo
    relatorio = {"suspeitos": suspeitos, "selecionados": selecionados, "realinhados": [], "falhas": [],
                 "fracao_audio": total / transcricao.duracao, "retranscricoes": 0}
    if selecionados:
        _cancelar_se_necessario(cancelada_ref)
        audio = decode_audio(transcricao.video_path, sampling_rate=16000)
        for i in sorted(selecionados):
            _cancelar_se_necessario(cancelada_ref)
            try:
                s = _alinhar_indice(transcricao, i, audio, modelo, cancelada_ref)
                novo = confianca_temporal(s, transcricao.duracao, vad_intervalos=vad_intervalos)
                if novo["timestamp_confidence"] <= scores[i]["timestamp_confidence"]:
                    raise ValueError("Refino nao melhorou os sinais locais.")
                transcricao.segmentos[i] = s
                relatorio["realinhados"].append(i)
            except ValueError as erro:
                relatorio["falhas"].append({"segmento": i, "motivo": str(erro)})
    _cancelar_se_necessario(cancelada_ref)
    relatorio["tempo_s"] = perf_counter() - inicio
    return relatorio


def realinhar_trecho_editado(transcricao, indice, *, modelo=None, cancelada_ref=None):
    """Explicit opt-in operation on one edited segment; no episode transcription."""
    from faster_whisper.audio import decode_audio
    from modules.transcricao import _obter_modelo, _cancelar_se_necessario, salvar_transcricao
    inicio = perf_counter()
    _cancelar_se_necessario(cancelada_ref)
    if not 0 <= indice < len(transcricao.segmentos):
        raise ValueError("Indice de trecho invalido.")
    if not transcricao.segmentos[indice].word_timestamps_invalidated:
        raise ValueError("Este trecho nao tem timestamps de palavras invalidados.")
    modelo = modelo if modelo is not None else _obter_modelo()
    audio = decode_audio(transcricao.video_path, sampling_rate=16000)
    s = _alinhar_indice(transcricao, indice, audio, modelo, cancelada_ref)
    _cancelar_se_necessario(cancelada_ref)
    score = confianca_temporal(s, transcricao.duracao)
    s.timestamp_confidence, s.timestamp_sinais = score["timestamp_confidence"], score["sinais"]
    transcricao.segmentos[indice] = s
    transcricao.diagnostico_temporal = validar_timestamps(transcricao.segmentos, transcricao.duracao)
    transcricao.tempo_ultima_operacao_s = perf_counter()-inicio
    transcricao.alinhamento_info = {"trecho_editado": indice, "retranscricoes": 0,
                                  "tempo_s": transcricao.tempo_ultima_operacao_s}
    salvar_transcricao(transcricao)
    return s


def realinhar_trecho_solicitado(transcricao, indice, cancelada_ref=None, progresso_cb=None):
    """Background UI operation on a snapshot, publishing only a completed result."""
    copia = deepcopy(transcricao)
    if progresso_cb:
        progresso_cb(5, "Realinhando o trecho editado com audio local.")
    realinhar_trecho_editado(copia, indice, cancelada_ref=cancelada_ref)
    if progresso_cb:
        progresso_cb(100, "Trecho realinhado; demais falas preservadas.")
    return copia
