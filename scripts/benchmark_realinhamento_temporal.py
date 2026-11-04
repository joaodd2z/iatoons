"""Offline oracle experiment: refine only high-deviation segments, never retranscribe."""
import argparse
from dataclasses import asdict
from difflib import SequenceMatcher
import os
from pathlib import Path
import sys
from time import perf_counter
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.benchmark_transcricao_temporal import gravar_json
from modules import transcricao as T
from modules.transcricao_alinhamento import _alinhar_indice
from modules.transcricao_benchmark import _palavras, comparar


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--referencia", type=Path, required=True)
    parser.add_argument("--candidato", type=Path, required=True)
    parser.add_argument("--tempo-candidato", type=float, required=True)
    parser.add_argument("--saida", type=Path, required=True)
    args = parser.parse_args()
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    ref, candidato = T.carregar_transcricao(args.referencia), T.carregar_transcricao(args.candidato)
    antes = comparar(ref, candidato)
    rw, cw = _palavras(ref), _palavras(candidato)
    desvios = {}
    for a, b, n in SequenceMatcher(None, [w[0] for w in rw], [w[0] for w in cw], autojunk=False).get_matching_blocks():
        for i in range(n):
            _, r, _ = rw[a+i]
            _, c, indice = cw[b+i]
            desvios[indice] = max(desvios.get(indice, 0), abs(r.inicio-c.inicio), abs(r.fim-c.fim))
    selecionados, total = [], 0
    for indice, desvio in sorted(desvios.items(), key=lambda x: -x[1]):
        s = candidato.segmentos[indice]
        custo = s.fim-s.inicio+.7
        if desvio > .4 and 0 < custo <= 30 and total+custo <= candidato.duracao*.35:
            selecionados.append(indice)
            total += custo
    from faster_whisper.audio import decode_audio
    falhas, realinhados = [], []
    with patch("socket.socket.connect", side_effect=AssertionError("Nuvem proibida")), \
         patch("socket.socket.connect_ex", side_effect=AssertionError("Nuvem proibida")):
        modelo = T._obter_modelo()
        inicio = perf_counter()
        if selecionados:
            audio = decode_audio(candidato.video_path, sampling_rate=16000)
            for indice in selecionados:
                try:
                    candidato.segmentos[indice] = _alinhar_indice(candidato, indice, audio, modelo)
                    realinhados.append(indice)
                except ValueError as erro:
                    falhas.append({"segmento": indice, "motivo": str(erro)})
        custo = perf_counter()-inicio
    dados = {"antes": antes, "depois": comparar(ref, candidato), "selecionados": selecionados,
             "realinhados": realinhados, "falhas": falhas, "refino_s": custo,
             "total_estimado_quente_s": args.tempo_candidato+custo,
             "fracao_audio": total/candidato.duracao, "retranscricoes": 0, "apis_reais": 0,
             "observacao": "Experimento oracle usa referencia conhecida para selecionar desvios. Nao e criterio disponivel em producao."}
    gravar_json(args.saida, dados)
    gravar_json(args.saida.with_name(args.saida.stem + "_transcricao.json"), asdict(candidato))
    print({k:v for k,v in dados.items() if k not in ("antes","depois")}, flush=True)
    print("QUALIDADE", {k:dados["depois"][k] for k in ("aprovado","media_palavras_s","p95_palavras_s","max_palavras_s","motivos")})


if __name__ == "__main__":
    main()
