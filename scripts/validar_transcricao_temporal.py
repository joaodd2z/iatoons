"""Fresh-process AUTO and cache timing without overwriting the Phase 3B baseline."""
import os
from pathlib import Path
import sys
from time import perf_counter
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from modules import transcricao as T
from modules.transcricao_benchmark import comparar
from scripts.benchmark_transcricao_temporal import gravar_json


def main():
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    folder = ROOT / "temp/benchmark_temporal/validacao_auto"
    audio = ROOT / "temp/performance_3a/fala_pt.wav"
    ref = T.carregar_transcricao(ROOT / "temp/performance_3b/standard_6_1_5_True.json")
    with patch("socket.socket.connect", side_effect=AssertionError("Nuvem proibida")), \
         patch("socket.socket.connect_ex", side_effect=AssertionError("Nuvem proibida")), \
         patch.object(T, "TRANSCRICOES_DIR", folder / "cache"):
        inicio = perf_counter()
        resultado = T.transcrever_video(str(audio), modo="auto", usar_cache=False)
        tempo = perf_counter()-inicio
        inicio = perf_counter()
        cached = T.transcrever_video(str(audio), modo="auto")
        cache = perf_counter()-inicio
    dados = {"baseline_historico_s": 13.43, "depois_frio_s": tempo,
             "ganho_percentual": (1-tempo/13.43)*100, "cache_s": cache,
             "cache_reutilizado": getattr(cached,"cache_reutilizado",False),
             "parametros": resultado.parametros, "qualidade": comparar(ref,resultado), "apis_reais": 0,
             "observacao": "Modelo inicialmente descarregado; variabilidade de carga nao isolada causalmente."}
    gravar_json(folder / "resultado.json", dados)
    print({k:v for k,v in dados.items() if k not in ("qualidade","parametros")},flush=True)
    print("QUALIDADE", {k:dados["qualidade"][k] for k in ("aprovado","media_palavras_s","p95_palavras_s","max_palavras_s")})


if __name__ == "__main__":
    main()
