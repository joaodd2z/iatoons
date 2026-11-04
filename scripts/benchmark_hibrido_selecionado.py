"""Offline experiment only; production AUTO and exports are not connected."""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
from time import perf_counter
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def salvar(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--episodio", type=Path, required=True)
    parser.add_argument("--saida", type=Path, default=ROOT / "temp/benchmark_hibrido_selecionado")
    parser.add_argument("--etapa", choices=("cpu", "gpu", "refinar"), required=True)
    parser.add_argument("--runtime", type=Path)
    args = parser.parse_args()
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    if args.etapa == "gpu":
        from scripts.benchmark_gpu_isolado import ambiente_filho
        if args.runtime is None:
            parser.error("GPU exige runtime explicito")
        os.environ.update(ambiente_filho(args.runtime))
    from modules import transcricao as T
    from modules.transcricao_v2 import ConfigTranscricao
    args.saida.mkdir(parents=True, exist_ok=True)
    with patch.object(T, "TRANSCRICOES_DIR", args.saida / "cache_base"), \
            patch("socket.socket.connect", side_effect=AssertionError("Offline")), \
            patch("socket.socket.connect_ex", side_effect=AssertionError("Offline")):
        if args.etapa in ("cpu", "gpu"):
            config = (ConfigTranscricao() if args.etapa == "cpu" else
                      ConfigTranscricao(backend="cuda", modo="rapido", compute_type="int8_float32"))
            load, elapsed, error = 0., 0., None
            def executar(config):
                nonlocal load, elapsed, error
                t = perf_counter()
                T._obter_modelo("small", 6, device=config.backend, compute_type=config.compute_type)
                load = perf_counter() - t
                print(args.etapa, "modelo", config.backend, load, flush=True)
                t = perf_counter()
                try:
                    result = T.transcrever_video(str(args.episodio), usar_cache=False,
                        configuracao=config, modo=config.modo, fallback_local=False,
                        progresso_cb=lambda p, m: print(args.etapa, round(p, 1), flush=True))
                    error = None
                except Exception as exc:
                    result = getattr(exc, "transcricao", None)
                    if result is None:
                        raise
                    error = str(exc)
                elapsed = perf_counter() - t
                return result
            if args.etapa == "gpu":
                from scripts.transcricao_hibrida_experimental import primeira_passagem
                first = primeira_passagem(lambda: executar(config), lambda: executar(ConfigTranscricao()))
                result, quality, fallback = first.transcricao, first.timestamp_quality, first.fallback_cpu
            else:
                result, quality, fallback = executar(config), "precise_unverified", False
            salvar(args.saida / (args.etapa + ".json"), {"tempo_s": elapsed,
                "modelo_load_s": load, "erro_estrutural": error, "resultado": asdict(result),
                "timestamp_quality": quality, "fallback_cpu": fallback})
            print("FINAL", args.etapa, elapsed, flush=True)
        else:
            refinar_benchmark(args)


def refinar_benchmark(args):
    from scripts.transcricao_hibrida_experimental import executar_benchmark
    executar_benchmark(args.episodio, args.saida)


if __name__ == "__main__":
    main()
