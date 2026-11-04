"""Final offline investigation: fixed-text attention/DTW alignment, never ASR."""
import argparse
from copy import deepcopy
from dataclasses import asdict
from difflib import SequenceMatcher
import json
import os
from pathlib import Path
import sys
import threading
from time import perf_counter
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from modules.transcricao import SegmentoTranscricao, PalavraTranscrita
from modules.transcricao_benchmark import normalizar, estatisticas, comparar
from modules.transcricao_temporal import validar_timestamps
from scripts.benchmark_gpu_isolado import restaurar
from scripts.benchmark_hibrido_selecionado import salvar

GATE = {"media_s": .20, "p95_s": .50, "pior_s": .80, "perdas": .03, "extras": .03}


def alinhar_fixo(model, audio, texto, idioma="pt"):
    import numpy as np
    from faster_whisper.tokenizer import Tokenizer
    from faster_whisper.transcribe import merge_punctuations
    if not 0 < len(audio) <= 30*16000:
        raise ValueError("Alinhamento limitado a audio local de ate 30 s")
    tokenizer = Tokenizer(model.hf_tokenizer, model.model.is_multilingual, task="transcribe", language=idioma)
    tokens = tokenizer.encode(texto)
    if not tokens or len(tokens) > 440:
        raise ValueError("Texto fixo excede capacidade da janela")
    features = model.feature_extractor(audio)
    frames = min(features.shape[-1]-1, round(len(audio)/160))
    encoded = model.encode(features[:, :3000][np.newaxis, :])
    aligned = model.find_alignment(tokenizer, [tokens], encoded, [frames])[0]
    merge_punctuations(aligned, "\"'([{-", "\"'.。,，!！?？:：)]}、")
    return [PalavraTranscrita(float(w["start"]), float(w["end"]), w["word"], float(w["probability"]))
            for w in aligned if w["word"].strip()]


def reconstruir_segmentos(reference_segments, words, offset, window_duration):
    expected = [token for s in reference_segments for token in normalizar(s.texto).split()]
    actual = [token for p in words for token in normalizar(p.texto).split()]
    if actual != expected:
        raise ValueError("Alinhamento nao preservou os tokens narrativos fixos")
    if any(not 0 <= p.inicio <= p.fim <= window_duration+.025 for p in words):
        raise ValueError("Tempo local fora da janela de alinhamento")
    positions = [i for i,p in enumerate(words) for _ in normalizar(p.texto).split()]
    output, cursor = [], 0
    for s in reference_segments:
        n = len(normalizar(s.texto).split())
        if not n:
            raise ValueError("Segmento esperado sem tokens")
        indices = sorted(set(positions[cursor:cursor+n]))
        ws = [deepcopy(words[i]) for i in indices]
        for p in ws:
            p.inicio += offset
            p.fim += offset
        output.append(SegmentoTranscricao(ws[0].inicio, ws[-1].fim, s.texto, ws))
        cursor += n
    return output


def avaliar(reference, candidate, texto_esperado):
    rw = [(t,p) for s in reference.segmentos for p in s.palavras for t in normalizar(p.texto).split()]
    cw = [(t,p) for s in candidate.segmentos for p in s.palavras for t in normalizar(p.texto).split()]
    pairs = [(a+i,b+i) for a,b,n in SequenceMatcher(None, [t for t,p in rw], [t for t,p in cw], autojunk=False).get_matching_blocks()
             for i in range(n)]
    deltas = [abs(getattr(rw[a][1],k)-getattr(cw[b][1],k)) for a,b in pairs for k in ("inicio","fim")]
    lost, extra = len(rw)-len(pairs), len(cw)-len(pairs)
    stats = estatisticas(deltas)
    reasons = []
    for key, limit in (("media",GATE["media_s"]),("p95",GATE["p95_s"]),("pior",GATE["pior_s"])):
        if stats[key] is None or stats[key] > limit+1e-9:
            reasons.append(key)
    if lost/max(1,len(rw)) > GATE["perdas"]:
        reasons.append("palavras_perdidas")
    if extra/max(1,len(rw)) > GATE["extras"]:
        reasons.append("palavras_extras")
    if " ".join(s.texto for s in candidate.segmentos) != texto_esperado:
        reasons.append("texto_narrativo_alterado")
    if normalizar(" ".join(p.texto for s in candidate.segmentos for p in s.palavras)) != normalizar(texto_esperado):
        reasons.append("tokens_divergentes")
    structural = validar_timestamps(candidate.segmentos, candidate.duracao)
    if any(p["critico"] for p in structural) or any(p.fim <= p.inicio for s in candidate.segmentos for p in s.palavras):
        reasons.append("timestamps_invalidos")
    segment_deltas = [abs(getattr(r,k)-getattr(c,k)) for r,c in zip(reference.segmentos,candidate.segmentos) for k in ("inicio","fim")]
    return {**stats, "palavras_perdidas": lost, "palavras_extras": extra,
            "perdas": lost/max(1,len(rw)), "extras": extra/max(1,len(rw)),
            "divergencia_textual": (lost+extra)/max(1,len(rw)), "texto_preservado": "texto_narrativo_alterado" not in reasons,
            "palavras_pareadas": len(pairs), "segmentos": estatisticas(segment_deltas),
            "diagnostico_estrutural": structural, "aprovado": not reasons, "motivos": reasons,
            "timestamp_quality": "aligned_verified" if not reasons else "aligned_unverified"}


def memoria_rss():
    import ctypes
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_ = [("cb",wintypes.DWORD),("faults",wintypes.DWORD)] + [(n,ctypes.c_size_t) for n in (
            "peak","working","quota_peak_paged","quota_paged","quota_peak_nonpaged","quota_nonpaged",
            "pagefile","peak_pagefile")]
    c = Counters()
    c.cb = ctypes.sizeof(c)
    if not ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.c_void_p(-1),ctypes.byref(c),c.cb):
        raise OSError("Falha ao medir RAM")
    return c.working/1024**2


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base",type=Path,default=ROOT/"temp/benchmark_hibrido_selecionado")
    parser.add_argument("--saida",type=Path,default=ROOT/"temp/alinhamento_forcado_final")
    args = parser.parse_args()
    os.environ.update(HF_HUB_OFFLINE="1",HF_HUB_DISABLE_TELEMETRY="1")
    import ctypes
    setter = ctypes.windll.kernel32.SetPriorityClass
    setter.argtypes = [ctypes.c_void_p,ctypes.c_ulong]
    setter.restype = ctypes.c_int
    if not setter(ctypes.c_void_p(-1),0x4000):
        raise OSError("Nao foi possivel reduzir prioridade do experimento")
    from modules import transcricao as T
    from faster_whisper.audio import decode_audio
    from scripts.benchmark_transcricao_temporal import memoria_gpu
    baseline = json.loads((args.base/"cpu.json").read_text(encoding="utf-8"))
    first = json.loads((args.base/"gpu.json").read_text(encoding="utf-8"))
    source = restaurar(baseline["resultado"])
    report = {"alinhador":"Whisper attention/DTW via CTranslate2 align", "device":"cpu/int8", "gates":GATE,
              "texto_origem":"CPU completo salvo, teste otimista de texto confiavel",
              "transcricoes_novas":0,"apis_reais":0,"instalacoes":0,"casos":[],
              "vram_inicial_mib":memoria_gpu(),"ram_inicial_mib":memoria_rss()}
    peak, stop = [report["ram_inicial_mib"]], threading.Event()
    def monitor():
        while not stop.wait(.05):
            peak[0] = max(peak[0],memoria_rss())
    thread = threading.Thread(target=monitor,daemon=True)
    thread.start()
    try:
        with patch("socket.socket.connect",side_effect=AssertionError("Offline")), \
                patch("socket.socket.connect_ex",side_effect=AssertionError("Offline")):
            t = perf_counter()
            audio = decode_audio(source.video_path,sampling_rate=16000)
            report["decode_s"] = perf_counter()-t
            t = perf_counter()
            model = T._obter_modelo("small",6)
            report["model_load_s"] = perf_counter()-t
            # Any ASR path is prohibited, including accidental generation inside an adapter.
            with patch.object(model,"transcribe",side_effect=AssertionError("ASR proibido")), \
                    patch.object(T,"transcrever_video",side_effect=AssertionError("ASR proibido")):
                for name,lo,hi in (("dialogo_rapido",170.,184.2),("desvio_central_conhecido",324.,335.)):
                    segments = [deepcopy(s) for s in source.segmentos if s.inicio>=lo and s.fim<=hi]
                    text = " ".join(s.texto for s in segments)
                    a, b = max(0.,segments[0].inicio-2.),min(source.duracao,segments[-1].fim+2.)
                    a,b = round(a*16000)/16000,round(b*16000)/16000
                    ref = deepcopy(source)
                    ref.segmentos = segments
                    t = perf_counter()
                    words = alinhar_fixo(model,audio[round(a*16000):round(b*16000)],text)
                    elapsed = perf_counter()-t
                    aligned = deepcopy(source)
                    aligned.segmentos = reconstruir_segmentos(segments,words,a,b-a)
                    quality = avaliar(ref,aligned,text)
                    serialized = asdict(aligned)
                    serialized["timestamp_quality"] = quality["timestamp_quality"]
                    for s in serialized["segmentos"]:
                        s["timestamp_quality"] = quality["timestamp_quality"]
                    result = {"nome":name,"janela":[a,b],"audio_s":b-a,"texto_fixo":text,
                              "tempo_alinhamento_s":elapsed,"qualidade":quality,
                              "timestamp_quality":quality["timestamp_quality"], "resultado":serialized,
                              "referencia":asdict(ref),"ram_apos_mib":memoria_rss()}
                    report["casos"].append(result)
                    salvar(args.saida/"resultado.json",report)
                    print(name,elapsed,quality,flush=True)
                    if not quality["aprovado"]:
                        report["parada"] = "Gate claramente reprovado; nao expandir para cinco cortes"
                        break
    finally:
        stop.set()
        thread.join()
    report["ram_pico_mib"] = peak[0]
    report["vram_final_mib"] = memoria_gpu()
    report["aprovado"] = len(report["casos"]) == 2 and all(c["qualidade"]["aprovado"] for c in report["casos"])
    if not report["aprovado"]:
        report["encerrado"] = True
    alignment_cost = sum(c["tempo_alinhamento_s"] for c in report["casos"])
    tested_audio = sum(c["audio_s"] for c in report["casos"])
    selected = next(c for c in json.loads((args.base/"resumo.json").read_text(encoding="utf-8"))["cenarios"] if c["cortes"]==5)
    extrapolated = alignment_cost/tested_audio * sum(t["fim"]-t["inicio"] for t in selected["selecao"])
    total = first["tempo_s"]+report["decode_s"]+report["model_load_s"]+extrapolated
    report["tempo_alinhamento_total_s"] = alignment_cost
    report["ganho_total_estimado_percentual"] = (1-total/baseline["tempo_s"])*100
    report["total_cinco_cortes_estimado_s"] = total
    report["estimativa_nao_benchmark_cinco_cortes"] = True
    salvar(args.saida/"resultado.json",report)
    print("FINAL",report["aprovado"],report["ganho_total_estimado_percentual"],flush=True)


def registrar_diagnostico_existente(folder):
    import platform
    import sys
    from importlib.metadata import version, PackageNotFoundError
    path = Path(folder)/"resultado.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    packages = {}
    for name in ("faster-whisper","ctranslate2","numpy","torch","torchaudio","transformers","whisperx"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = "nao instalado"
    report["compatibilidade"] = {"python":sys.version, "windows":platform.platform(),
        "pacotes":packages, "ctc_modelo_local":False,
        "whisperx_ctc": "Nao testados: faltam dependencias/modelos locais; sem rede nao se verificou matriz ABI",
        "alinhador_escolhido": "Compatibilidade comprovada pela execucao CPU local; sem nova dependencia"}
    for case in report["casos"]:
        status = case["timestamp_quality"]
        case["resultado"]["timestamp_quality"] = status
        for segment in case["resultado"]["segmentos"]:
            segment["timestamp_quality"] = status
    salvar(path,report)
    return report


if __name__ == "__main__":
    main()
