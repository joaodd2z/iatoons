"""Offline GPU experiment. Never changes production defaults or global PATH."""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from time import perf_counter
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DLLS = ("cublas64_12.dll", "cublasLt64_12.dll", "cudart64_12.dll")


def ambiente_filho(runtime=None, base=None):
    env = dict(os.environ if base is None else base)
    env.update(HF_HUB_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1")
    if runtime is not None:
        runtime = Path(runtime).resolve()
        if not all((runtime / name).is_file() for name in DLLS):
            raise ValueError("Runtime CUDA 12 incompleto")
        env["PATH"] = str(runtime) + os.pathsep + env.get("PATH", "")
    return env


def gpu_memoria():
    r = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                       capture_output=True, text=True, timeout=5)
    return int(r.stdout.splitlines()[0]) if r.returncode == 0 else None


def worker(args):
    from modules import transcricao as T
    from modules.transcricao_v2 import ConfigTranscricao
    from scripts.benchmark_transcricao_temporal import gravar_json
    config = (ConfigTranscricao() if args.worker == "cpu" else
              ConfigTranscricao(backend="cuda", modo="rapido", compute_type=args.compute))
    casos = json.loads(args.manifesto.read_text(encoding="utf-8"))
    report = {"config": asdict(config), "amostras": [], "apis_reais": 0}
    # All caches and result files belong to this experiment, never production.
    with patch.object(T, "TRANSCRICOES_DIR", args.saida / (args.worker + "_cache")), \
            patch("socket.socket.connect", side_effect=AssertionError("Offline")), \
            patch("socket.socket.connect_ex", side_effect=AssertionError("Offline")):
        t = perf_counter()
        T._obter_modelo("small", 6, device=config.backend, compute_type=config.compute_type)
        report["carregamento_modelo_s"] = perf_counter() - t
        print(args.worker, "model loaded", report["carregamento_modelo_s"], flush=True)
        for i, caso in enumerate(casos):
            runs = []
            for repeticao in range(3):
                t = perf_counter()
                try:
                    result = T.transcrever_video(caso["arquivo"], usar_cache=False, backend="auto_local",
                        modo=config.modo, configuracao=config, fallback_local=False)
                    error = None
                except Exception as exc:
                    result = getattr(exc, "transcricao", None)
                    error = str(exc)
                    if result is None:
                        raise
                elapsed = perf_counter() - t
                runs.append({"tempo_s": elapsed, "erro": error, "resultado": asdict(result)})
                print(args.worker, i, repeticao, round(elapsed, 3), error, flush=True)
            report["amostras"].append({"caso": caso, "execucoes": runs})
            gravar_json(args.saida / (args.worker + ".json"), report)


def executar_filho(args, backend, env):
    stop = threading.Event()
    readings = []
    def monitor():
        while not stop.is_set():
            try:
                value = gpu_memoria()
                if value is not None:
                    readings.append(value)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass
            stop.wait(.25)
    monitor_thread = threading.Thread(target=monitor, daemon=True)
    monitor_thread.start()
    t = perf_counter()
    try:
        r = subprocess.run([sys.executable, "-B", "-u", __file__, "--worker", backend,
                            "--compute", args.compute, "--manifesto", str(args.manifesto),
                            "--saida", str(args.saida)], env=env, timeout=1200)
    finally:
        stop.set()
        monitor_thread.join()
    return {"returncode": r.returncode, "tempo_processo_s": perf_counter() - t,
            "vram_inicial_mib": readings[0] if readings else None,
            "vram_pico_aproximado_mib": max(readings) if readings else None,
            "amostras_vram": len(readings)}


def restaurar(data):
    from modules.transcricao import Transcricao, SegmentoTranscricao, PalavraTranscrita
    data = dict(data)
    data["segmentos"] = [SegmentoTranscricao(**(dict(s) | {
        "palavras": [PalavraTranscrita(**p) for p in s["palavras"]]})) for s in data["segmentos"]]
    return Transcricao(**data)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--manifesto", type=Path, default=ROOT / "temp/benchmark_temporal/amostras.json")
    parser.add_argument("--saida", type=Path, default=ROOT / "temp/benchmark_gpu_isolado")
    parser.add_argument("--compute", default="int8_float32", choices=("int8_float32", "int8", "float32"))
    parser.add_argument("--worker", choices=("cpu", "cuda"))
    args = parser.parse_args()
    if args.worker:
        worker(args)
        return
    if args.runtime is None:
        parser.error("Informe --runtime explicitamente; nenhuma descoberta implicita do Ollama")
    gpu_env = ambiente_filho(args.runtime)
    args.saida.mkdir(parents=True, exist_ok=True)
    from scripts.benchmark_transcricao_temporal import gravar_json
    from modules.transcricao_benchmark import comparar, LIMITES
    report = {"runtime": str(args.runtime.resolve()), "limites": LIMITES, "apis_reais": 0}
    for backend, env in (("cpu", ambiente_filho()), ("cuda", gpu_env)):
        report[backend] = executar_filho(args, backend, env)
        gravar_json(args.saida / "resumo.json", report)
        if report[backend]["returncode"]:
            print("Worker failed; no automatic retries or production changes", flush=True)
            return
    cpu, gpu = [json.loads((args.saida / (b + ".json")).read_text(encoding="utf-8")) for b in ("cpu", "cuda")]
    comparisons = []
    for c, g in zip(cpu["amostras"], gpu["amostras"]):
        qualities = [comparar(restaurar(c["execucoes"][-1]["resultado"]), restaurar(r["resultado"]))
                     for r in g["execucoes"]]
        tc = sum(r["tempo_s"] for r in c["execucoes"][1:]) / 2
        tg = sum(r["tempo_s"] for r in g["execucoes"][1:]) / 2
        comparisons.append({"arquivo": c["caso"]["arquivo"], "cpu_warm_s": tc, "gpu_warm_s": tg,
                            "ganho_percentual": (1 - tg / tc) * 100, "qualidades": qualities})
    report["comparacoes"] = comparisons
    report["qualidade_aprovada"] = all(q["aprovado"] for a in comparisons for q in a["qualidades"])
    report["cpu_warm_total_s"] = sum(a["cpu_warm_s"] for a in comparisons)
    report["gpu_warm_total_s"] = sum(a["gpu_warm_s"] for a in comparisons)
    report["ganho_percentual"] = (1 - report["gpu_warm_total_s"] / report["cpu_warm_total_s"]) * 100
    gravar_json(args.saida / "resumo.json", report)
    print("FINAL", report["ganho_percentual"], "quality", report["qualidade_aprovada"], flush=True)


if __name__ == "__main__":
    main()
