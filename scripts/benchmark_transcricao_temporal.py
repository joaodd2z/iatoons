"""Offline local matrix; cloud requires its own explicit command and consent flag."""
import argparse
from contextlib import ExitStack
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
from time import perf_counter
from unittest.mock import patch
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from modules import transcricao as T
from modules.transcricao_benchmark import comparar, LIMITES, VERSAO_BENCHMARK
from modules.transcricao_v2 import ConfigTranscricao, elegivel_auto, identidade_maquina, salvar_auto


def gravar_json(path, dados):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


class Memoria:
    def __enter__(self):
        try:
            import psutil
            self.processo = psutil.Process()
            self.ler = lambda: self.processo.memory_info().rss
        except ImportError:
            if os.name == "nt":
                import ctypes
                from ctypes import wintypes
                class Contadores(ctypes.Structure):
                    _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
                        (nome, ctypes.c_size_t) for nome in ("peak", "working", "quota_peak_paged", "quota_paged",
                            "quota_peak_nonpaged", "quota_nonpaged", "pagefile", "peak_pagefile")]
                def ler():
                    valores = Contadores()
                    valores.cb = ctypes.sizeof(valores)
                    if not ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.c_void_p(-1), ctypes.byref(valores), valores.cb):
                        raise OSError("Nao foi possivel medir working set.")
                    return valores.working
                self.ler = ler
            else:
                import resource
                self.ler = lambda: resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
        self.rss = self.ler()
        self.inicial = self.rss
        self.parar = threading.Event()
        def monitorar():
            while not self.parar.wait(.05):
                self.rss = max(self.rss, self.ler())
        self.worker = threading.Thread(target=monitorar, daemon=True)
        self.worker.start()
        return self

    def __exit__(self, *args):
        self.rss = max(self.rss, self.ler())
        self.parar.set()
        self.worker.join()


def memoria_gpu():
    import subprocess
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=3)
        return int(r.stdout.splitlines()[0]) if r.returncode == 0 else None
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return None


def matriz():
    fast = ConfigTranscricao(modo="rapido", engine="batched", beam_size=1,
                            condition_on_previous_text=False)
    configs = [("Preciso", ConfigTranscricao()), ("Batched antigo", replace(fast, batch_size=8, timestamp_tokens=False))]
    for batch in (1, 2, 4, 8):
        configs.append((f"Batched tokens batch {batch}", replace(fast, batch_size=batch)))
    base = replace(fast, batch_size=4, beam_size=5, vad_min_silence_ms=2000, vad_speech_pad_ms=400,
                   vad_threshold=.5, vad_min_speech_ms=0)
    configs.append(("Batched beam 5 VAD Preciso", base))
    for nome, campos in (
        ("VAD silencio 500", {"vad_min_silence_ms": 500}),
        ("VAD padding 200", {"vad_speech_pad_ms": 200}),
        ("VAD threshold 0.35", {"vad_threshold": .35}),
        ("VAD fala minima 100", {"vad_min_speech_ms": 100}),
        ("VAD chunks 10", {"chunk_seconds": 10}),
        ("Sem VAD", {"vad_filter": False}),
        ("Hibrido suspeitos", {"alinhamento": "suspeitos", "chunk_seconds": 10}),
    ):
        configs.append((nome, replace(base, **campos)))
    configs.append(("GPU int8_float32", ConfigTranscricao(backend="cuda", modo="rapido", compute_type="int8_float32")))
    configs.append(("GPU float32", ConfigTranscricao(backend="cuda", modo="rapido", compute_type="float32")))
    return configs


def preparar_amostras(episodio, folder):
    """Real excerpts, never falsely certify scene categories from RMS or generated text."""
    import numpy as np
    from faster_whisper.audio import decode_audio
    audio = decode_audio(str(episodio), sampling_rate=16000)
    casos = []
    for indice, (a, b) in enumerate(((65, 80), (170, 185), (300, 315), (500, 515), (700, 715), (950, 965))):
        if b * 16000 > len(audio):
            continue
        path = folder / f"animacao_{indice+1}.wav"
        amostra = audio[round(a*16000):round(b*16000)]
        with wave.open(str(path), "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(16000)
            f.writeframes(np.clip(amostra*32767, -32768, 32767).astype("<i2").tobytes())
        casos.append({"arquivo": str(path), "origem": str(episodio), "intervalo": [a, b],
                      "audio_real": True, "categorias": ["animacao_real"],
                      "categorias_verificadas": True, "anotacoes": []})
    casos.append({"arquivo": str(ROOT / "temp/performance_3a/fala_pt.wav"), "audio_real": False,
                  "categorias": ["fala_limpa"], "categorias_verificadas": True, "anotacoes": []})
    gravar_json(folder / "amostras.json", casos)
    return casos


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifesto", type=Path)
    parser.add_argument("--preparar-episodio", type=Path)
    parser.add_argument("--saida", type=Path, default=ROOT / "temp/benchmark_temporal")
    parser.add_argument("--somente", nargs="*")
    parser.add_argument("--amostras", type=int, nargs="+")
    parser.add_argument("--persistir-auto", action="store_true")
    parser.add_argument("--backend-nuvem", choices=["groq_whisper", "gemini_transcribe"])
    parser.add_argument("--autorizar-envio-audio", action="store_true")
    args = parser.parse_args()
    if args.backend_nuvem and not args.autorizar_envio_audio:
        parser.error("Benchmark de nuvem exige --autorizar-envio-audio explicitamente.")
    if not args.manifesto and not args.preparar_episodio:
        parser.error("Informe um manifesto de amostras ou episodio local.")
    args.saida.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    casos = (json.loads(args.manifesto.read_text(encoding="utf-8")) if args.manifesto else
             preparar_amostras(args.preparar_episodio, args.saida))
    if args.amostras is not None:
        if any(i < 0 or i >= len(casos) for i in args.amostras):
            parser.error("Indice de amostra fora do manifesto.")
        casos = [casos[i] for i in args.amostras]
    configs = matriz()
    if args.backend_nuvem:
        configs = [configs[0], (args.backend_nuvem, ConfigTranscricao(
            backend=args.backend_nuvem, engine="remote", modo="rapido"))]
    elif args.somente:
        desconhecidas = set(args.somente) - {nome for nome, _ in configs}
        if desconhecidas:
            parser.error("Configuracao desconhecida: " + ", ".join(sorted(desconhecidas)))
        configs = [c for c in configs if c[0] == "Preciso" or c[0] in args.somente]
    relatorio = {"versao_benchmark": VERSAO_BENCHMARK, "maquina": identidade_maquina(),
                 "modelo": "small", "data": datetime.now(timezone.utc).isoformat(),
                 "limites": LIMITES, "registros": [], "apis_reais": 0, "referencia": "Preciso local, nao ground truth humano"}
    referencias, tempos_ref = {}, {}
    indisponiveis = set()
    with ExitStack() as stack:
        stack.enter_context(patch.object(T, "TRANSCRICOES_DIR", args.saida / "cache"))
        if not args.backend_nuvem:
            for metodo in ("connect", "connect_ex"):
                stack.enter_context(patch("socket.socket." + metodo, side_effect=AssertionError("Nuvem proibida")))
        for nome, config in configs:
            registro = {"nome": nome, "configuracao": asdict(config), "amostras": [], "aprovado": True}
            chave = hashlib.sha256(json.dumps(asdict(config), sort_keys=True).encode()).hexdigest()[:12]
            for indice, caso in enumerate(casos):
                if config.backend in indisponiveis:
                    break
                resultados, tempos, memoria, qualidades = [], [], [], []
                vram_inicial = memoria_gpu() if config.backend == "cuda" else None
                inicio = perf_counter()
                try:
                    for repeticao in range(2):
                        with Memoria() as ram:
                            inicio = perf_counter()
                            resultado = T.transcrever_video(caso["arquivo"], usar_cache=False, modo=config.modo,
                                backend=config.backend if config.engine == "remote" else "auto_local",
                                configuracao=None if config.engine == "remote" else config,
                                autorizar_nuvem=args.autorizar_envio_audio, fallback_local=False)
                            tempos.append(perf_counter() - inicio)
                        memoria.append({"rss_pico_mb": ram.rss/1024**2, "rss_incremento_mb": (ram.rss-ram.inicial)/1024**2})
                        resultados.append(resultado)
                        if config.engine == "remote":
                            relatorio["apis_reais"] += 1
                    if config == ConfigTranscricao():
                        referencias[indice] = resultados[-1]
                        tempos_ref[indice] = tempos[-1]
                    qualidades = [comparar(referencias[indice], r, caso.get("anotacoes")) for r in resultados]
                    amostra = {**caso, "tempos_s": tempos, "tempo_quente_s": tempos[-1],
                        "referencia_quente_s": tempos_ref[indice], "qualidade": qualidades,
                        "ram": memoria, "vram_inicial_mb": vram_inicial,
                        "vram_apos_mb": memoria_gpu() if config.backend == "cuda" else None,
                        "ganho_percentual": (1-tempos[-1]/tempos_ref[indice])*100,
                        "alinhamento": resultado.alinhamento_info, "tempos_backend": resultado.tempos_backend}
                    gravar_json(args.saida / f"{chave}_amostra_{indice}.json", asdict(resultados[-1]))
                    registro["aprovado"] &= all(q["aprovado"] for q in qualidades)
                except Exception as erro:
                    parcial = getattr(erro, "transcricao", None)
                    if parcial is not None and indice in referencias:
                        qualidades = [comparar(referencias[indice], parcial, caso.get("anotacoes"))]
                        gravar_json(args.saida / f"{chave}_amostra_{indice}_rejeitada.json", asdict(parcial))
                    if config.engine == "remote":
                        mensagem = "Backend remoto indisponivel; sem fallback no benchmark."
                    else:
                        mensagem = str(erro)
                    amostra = {**caso, "erro": mensagem, "tempo_ate_erro_s": perf_counter()-inicio,
                               "qualidade": qualidades}
                    registro["aprovado"] = False
                    if config.backend == "cuda":
                        indisponiveis.add("cuda")
                registro["amostras"].append(amostra)
                print(json.dumps({"configuracao": nome, "amostra": indice,
                    "tempo_s": amostra.get("tempo_quente_s"), "erro": amostra.get("erro"),
                    "qualidade": [{k: q[k] for k in ("aprovado", "media_palavras_s", "p95_palavras_s", "max_palavras_s",
                                                    "media_segmentos_s", "p95_segmentos_s", "max_segmentos_s")}
                                  for q in qualidades[-1:]]}), flush=True)
                gravar_json(args.saida / "parcial.json", relatorio | {"registro_atual": registro})
            if not registro["amostras"]:
                registro["aprovado"] = False
                registro["erro"] = "GPU indisponivel apos falha anterior."
            relatorio["registros"].append(registro)
        elegiveis = [r for r in relatorio["registros"] if elegivel_auto(ConfigTranscricao(**r["configuracao"]),
                     relatorio | {"melhor": r})]
        relatorio["melhor"] = (min(elegiveis, key=lambda r: sum(a["tempo_quente_s"] for a in r["amostras"]))
                               if elegiveis else relatorio["registros"][0])
        if not elegiveis:
            relatorio["alerta"] = "Nenhum candidato elegivel; Preciso preservado, sem promover benchmark reprovado."
        gravar_json(args.saida / "benchmark.json", relatorio)
        if args.persistir_auto and elegiveis:
            salvar_auto(ConfigTranscricao(**relatorio["melhor"]["configuracao"]), "small", relatorio)
        print("AUTO", relatorio["melhor"]["nome"], flush=True)


if __name__ == "__main__":
    main()
