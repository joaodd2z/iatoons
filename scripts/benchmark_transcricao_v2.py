"""Controlled, sequential offline benchmark on Phase 3A's SAME audio."""
import os
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import socket
import sys
from time import perf_counter
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from modules import transcricao as tr
from modules.transcricao_v2 import ConfigTranscricao, salvar_auto
from modules.transcricao_benchmark import comparar, LIMITES


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--persistir-auto", action="store_true")
    parser.add_argument("--continuar", action="store_true")
    parser.add_argument("--beams", type=int, nargs="+", default=[2,3,4])
    args = parser.parse_args()
    folder = ROOT / "temp" / "performance_3b"
    folder.mkdir(parents=True, exist_ok=True)
    audio = ROOT / "temp" / "performance_3a" / "fala_pt.wav"
    configs = [ConfigTranscricao()]
    for threads in (4,6,8):
        configs.append(ConfigTranscricao(modo="rapido", threads=threads, beam_size=1,
                                        condition_on_previous_text=True))
        if threads != 6:
            configs.append(ConfigTranscricao(modo="rapido", threads=threads, beam_size=5,
                                            condition_on_previous_text=True))
        configs.append(ConfigTranscricao(modo="rapido", threads=threads, beam_size=1,
                                        condition_on_previous_text=False))
        for batch in (4,8):
            configs.append(ConfigTranscricao(modo="rapido", engine="batched", threads=threads,
                 beam_size=1, batch_size=batch, condition_on_previous_text=False))
    registros = []
    if args.continuar:
        registros = json.loads((folder / "parcial.json").read_text(encoding="utf-8"))
        referencia = tr.carregar_transcricao(folder / "standard_6_1_5_True.json")
        configs = [ConfigTranscricao(modo="rapido", beam_size=beam) for beam in args.beams]
    with patch.object(socket.socket, "connect", side_effect=AssertionError("Network prohibited")), \
         patch.object(tr, "TRANSCRICOES_DIR", folder / "cache"):
        for config in configs:
            tempos, resultados = [], []
            for repeticao in range(2):
                inicio = perf_counter()
                resultado = tr.transcrever_video(str(audio), usar_cache=False, modo=config.modo, configuracao=config)
                tempos.append(perf_counter()-inicio)
                resultados.append(resultado)
            if config.modo == "preciso":
                referencia = resultados[-1]
            qualidade = [comparar(referencia, r) for r in resultados]
            registro = {"configuracao": asdict(config), "tempos_s": tempos,
                        "mediana_s": sum(tempos)/2, "qualidade": qualidade,
                        "aprovado": all(q["aprovado"] for q in qualidade)}
            registros.append(registro)
            (folder / "parcial.json").write_text(json.dumps(registros, indent=2), encoding="utf-8")
            (folder / (f"{config.engine}_{config.threads}_{config.batch_size}_{config.beam_size}_{config.condition_on_previous_text}.json")).write_text(
                json.dumps(asdict(resultados[-1]), indent=2), encoding="utf-8")
            print(json.dumps(registro), flush=True)
        preciso = registros[0]
        elegiveis = [r for r in registros if r["aprovado"]]
        # Rank the second (warm-model) run. Initial model loads differ across
        # configurations; comparing their mixed means would favor reused models.
        melhor = min(elegiveis, key=lambda r:r["tempos_s"][1])
        inicio = perf_counter()
        tr.transcrever_video(str(audio), modo=melhor["configuracao"]["modo"],
                            configuracao=ConfigTranscricao(**melhor["configuracao"]))
        cache_s = perf_counter()-inicio
        # Experimental second pass: existing decoder supports arrays, no FFmpeg changes.
        from faster_whisper.audio import decode_audio
        inicio = perf_counter()
        amostra = decode_audio(str(audio), sampling_rate=16000)
        decode_s = perf_counter()-inicio
        modelo = tr._obter_modelo("small", threads=6)
        intervalos = [(0,10), (20,30), (40,min(50,len(amostra)/16000))]
        inicio = perf_counter()
        for a,b in intervalos:
            segmentos, _ = modelo.transcribe(amostra[round(a*16000):round(b*16000)], language="pt", beam_size=5,
                                    word_timestamps=True, vad_filter=True, condition_on_previous_text=True)
            list(segmentos)
        segunda_passagem = perf_counter()-inicio+decode_s
        dados = {"audio": str(audio), "duracao_s": referencia.duracao, "baseline_3a_s": 13.43,
                 "registros": registros, "melhor": melhor, "cache_s": cache_s, "limites": LIMITES,
                 "ganho_vs_3a_percentual": (13.43-melhor["tempos_s"][0])/13.43*100,
                 "duas_passagens": {"intervalos":intervalos,"segunda_passagem_s":segunda_passagem,
                  "total_estimado_amostra_s":melhor["tempos_s"][1]+segunda_passagem,
                  "observacao":"Cenario amostra, NAO episodio tipico; nao habilitado."}, "apis_reais":0}
        (folder / "benchmark.json").write_text(json.dumps(dados, indent=2), encoding="utf-8")
        if args.persistir_auto:
            salvar_auto(ConfigTranscricao(**melhor["configuracao"]), "small", dados)
        print("RESULTADO", json.dumps({k:v for k,v in dados.items() if k != "registros"}), flush=True)


if __name__ == "__main__":
    main()
