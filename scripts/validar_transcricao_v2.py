"""Fresh process: public AUTO, empty isolated cache, same Phase 3A audio."""
import os
os.environ["HF_HUB_OFFLINE"]="1"
os.environ["HF_HUB_DISABLE_TELEMETRY"]="1"
from dataclasses import asdict
import json
from pathlib import Path
import socket
import sys
from time import perf_counter
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from modules import transcricao as T
from modules.transcricao_benchmark import comparar
from modules.performance import PerformanceProfiler


def main():
    folder=ROOT / "temp" / "performance_3b"
    stage3a=json.loads((ROOT / "temp" / "performance_3a" / "baseline_fala.json").read_text())
    record=next(r for r in stage3a["registros"] if r["operacao"]=="cache_transcricao")
    referencia=T.carregar_transcricao(Path(record["metadados"]["arquivos_reutilizados"][0]))
    with patch.object(socket.socket,"connect",side_effect=AssertionError("No network")), \
         patch.object(T,"TRANSCRICOES_DIR",folder / "validacao_cache"), \
         PerformanceProfiler("validacao_3b",ROOT / "dados" / "performance"):
        inicio=perf_counter()
        result=T.transcrever_video(str(ROOT / "temp" / "performance_3a" / "fala_pt.wav"),modo="auto",usar_cache=False)
        tempo=perf_counter()-inicio
        inicio=perf_counter()
        cached=T.transcrever_video(result.video_path,modo="auto")
        cache_s=perf_counter()-inicio
    dados={"antes_s":13.43,"depois_s":tempo,"ganho_percentual":(13.43-tempo)/13.43*100,
           "configuracao":result.parametros,"cache_s":cache_s,"cache_backend":cached.backend,
           "qualidade_vs_3a":comparar(referencia,result),"apis_reais":0,
           "nota":"Mesmo audio; modelo nao carregado antes do cronometro; arquivos ja presentes no disco."}
    (folder / "validacao_final.json").write_text(json.dumps(dados,indent=2),encoding="utf-8")
    print(json.dumps(dados,indent=2),flush=True)


if __name__=="__main__":
    main()
