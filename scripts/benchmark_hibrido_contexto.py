"""Context/guard-band ablation on saved five-cut decisions, entirely offline."""
import argparse
from copy import deepcopy
from dataclasses import asdict
from difflib import SequenceMatcher
import json
import math
import os
from pathlib import Path
import sys
from time import perf_counter
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import transcricao_hibrida_experimental as H
from scripts.benchmark_hibrido_selecionado import salvar
from scripts.benchmark_gpu_isolado import restaurar
from modules.transcricao_benchmark import estatisticas, comparar, LIMITES, normalizar


def borda_segura(audio, source, nominal, inicio, alcance=2.):
    import numpy as np
    lo, hi = (max(0., nominal-alcance), nominal) if inicio else (nominal, min(source.duracao, nominal+alcance))
    clip = audio[round(lo*H.SAMPLE_RATE):round(hi*H.SAMPLE_RATE)]
    block = 320
    frames = len(clip)//block
    if frames:
        rms = np.sqrt(np.mean(clip[:frames*block].reshape(frames, block)**2, axis=1))
        candidates, run = [], 0
        for i, value in enumerate(rms):
            run = run+1 if value < .01 else 0
            if run >= 10:
                candidates.append(lo+(i-4)*.02)
        if candidates:
            return (candidates[-1] if inicio else candidates[0]), "baixa_energia_200ms"
    bounds = [s.inicio if inicio else s.fim for s in source.segmentos]
    candidates = [t for t in bounds if lo <= t <= hi]
    if candidates:
        return (max(candidates) if inicio else min(candidates)), "borda_segmento"
    return nominal, "margem_temporal"


def planejar(selecao, source, audio, contexto, guard=2.):
    if contexto < guard or guard <= 0:
        raise ValueError("Contexto deve cobrir guard band positiva")
    raw = H.unir_regioes(selecao, source.duracao, margem=contexto)
    expanded, reasons = [], []
    for a, b in raw:
        x, ra = borda_segura(audio, source, a, True)
        y, rb = borda_segura(audio, source, b, False)
        expanded.append({"inicio": x, "fim": y})
        reasons.append({"nominal": [a, b], "segura": [x, y], "inicio": ra, "fim": rb})
    windows = H.unir_regioes(expanded, source.duracao, margem=0.)
    return [{"refine_window": [a, b], "trusted_window": [a+guard, b-guard],
             "guard_band_s": guard, "contexto_s": contexto,
             "bordas": [r for r in reasons if a <= r["segura"][0] and r["segura"][1] <= b]} for a, b in windows]


def recortar(template, a, b):
    result = deepcopy(template)
    result.segmentos = []
    for s in template.segmentos:
        words = [deepcopy(p) for p in s.palavras if a <= (p.inicio+p.fim)/2 < b]
        if words:
            result.segmentos.append(H.SegmentoTranscricao(words[0].inicio, words[-1].fim,
                " ".join(p.texto.strip() for p in words), words))
    return result


def deltas_palavras(reference, candidate):
    rw, cw = H.palavras(reference.segmentos), H.palavras(candidate.segmentos)
    matcher = SequenceMatcher(None, [normalizar(p.texto) for p, _ in rw],
                              [normalizar(p.texto) for p, _ in cw], autojunk=False)
    rows = []
    for a, b, n in matcher.get_matching_blocks():
        for j in range(n):
            r, c = rw[a+j][0], cw[b+j][0]
            rows.append({"referencia": asdict(r), "candidato": asdict(c),
                         "erros": [abs(r.inicio-c.inicio), abs(r.fim-c.fim)]})
    return rows


def diagnostico_window(reference, candidate, a, b, edge=5.):
    r, c = recortar(reference, a, b), recortar(candidate, a, b)
    rows = deltas_palavras(r, c)
    groups = {"bordas": [], "centro": []}
    for row in rows:
        mid = (row["referencia"]["inicio"]+row["referencia"]["fim"])/2
        group = "bordas" if mid < a+edge or mid >= b-edge else "centro"
        groups[group].extend(row["erros"])
    return {"edge_seconds": edge, "gate_janela_inteira": comparar(r, c),
            **{k: estatisticas(v) | {"fronteiras_pareadas": len(v)} for k, v in groups.items()}, "pareamento": rows}


def validar_trusted(trecho, window):
    a, b = window["trusted_window"]
    if not a <= trecho["inicio"] < trecho["fim"] <= b:
        raise ValueError("Trecho toca guard band: nao liberar timestamps das bordas")
    for s in trecho["segmentos"]:
        if any(p["inicio"] < a or p["fim"] > b for p in s["palavras"]):
            raise ValueError("Palavra fora da trusted window")


def referencia_fixa(selecao, gpu, reference):
    canonical = {}
    for t in selecao:
        a, b = max(0., t["inicio"]-35), min(gpu.duracao, t["fim"]+35)
        local = recortar(reference, a, b)
        try:
            mapped = H.mapear_trecho(t, gpu, local.segmentos, (a, b), margem=3.)
            canonical[t["trecho_id"]] = H.transcricao_trecho(reference, mapped)
        except ValueError as exc:
            canonical[t["trecho_id"]] = str(exc)
    return canonical


def executar_perfil(contexto, vad, previous, selecao, gpu, reference, canonical, audio, model, cache_dir, identity, setup, folder):
    started = perf_counter()
    windows = planejar(selecao, gpu, audio, contexto)
    options = {"vad_filter": vad, "condition_on_previous_text": previous}
    cache = H.CachePreciso(cache_dir, identity, gpu.duracao, opcoes=options)
    cost, real_inference = 0., 0.
    for i, w in enumerate(windows):
        a, b = w["refine_window"]
        print("WINDOW", contexto, options, i, round(a, 3), round(b, 3), flush=True)
        try:
            cpu = lambda x, y: H.cpu_array(model, audio, x, y, vad_filter=vad, condition_on_previous_text=previous)
            data, hit = cache.obter(a, b, cpu)
            w["cpu"] = data
            w["cache_hit"] = hit
            cost += data["tempo_cpu_s"]
            if not hit:
                real_inference += data["tempo_cpu_s"]
            window_trans = deepcopy(gpu)
            window_trans.segmentos = H.desserializar_segmentos(data["segmentos"])
            w["diagnostico"] = diagnostico_window(reference, window_trans, a, b)
        except Exception as exc:
            w["erro"] = str(exc)
        salvar(folder / "window_checkpoint.json", {"contexto": contexto, "opcoes": options, "windows": windows})
    results, rows = [], []
    for t in selecao:
        w = next(w for w in windows if w["refine_window"][0] <= t["inicio"] and t["fim"] <= w["refine_window"][1])
        try:
            if w.get("erro"):
                raise ValueError(w["erro"])
            ref = canonical[t["trecho_id"]]
            if isinstance(ref, str):
                raise ValueError("Referencia operacional ambigua: " + ref)
            candidate = H.mapear_trecho(t, gpu, H.desserializar_segmentos(w["cpu"]["segmentos"]), w["refine_window"], margem=3.)
            validar_trusted(candidate, w)
            candidate = H.aprovar_temporal(candidate, ref, gpu)
            rows.extend(deltas_palavras(ref, H.transcricao_trecho(gpu, candidate)))
            results.append(candidate)
        except ValueError as exc:
            results.append({**deepcopy(t), "timestamp_quality": "pending", "motivo": str(exc)})
    ready = []
    for cut_id in dict.fromkeys(t["cut_id"] for t in selecao):
        parts = [t for t in results if t["cut_id"] == cut_id]
        try:
            H.exigir_precisos(parts, gpu.duracao)
            ready.append(cut_id)
        except ValueError:
            pass
    approved = all(t["timestamp_quality"] == "precise" for t in results) and len(ready) == 5
    stats = estatisticas([v for r in rows for v in r["erros"]])
    wall = perf_counter()-started
    overhead = max(0., wall-real_inference)
    total = setup["gpu_s"]+setup["load_cpu_s"]+setup["decode_s"]+cost+overhead
    gain = (1-total/setup["cpu_completo_s"])*100
    return {"contexto_s": contexto, "opcoes": options, "windows": windows, "trechos": results,
            "metricas_finais": stats, "palavras_pareadas": len(rows), "fronteiras": rows,
            "trechos_aprovados": sum(t["timestamp_quality"] == "precise" for t in results),
            "trechos_selecionados": len(selecao), "cortes_aprovados": ready, "gate_aprovado": approved,
            "cpu_inferencia_s": cost, "overhead_s": overhead, "tempo_total_s": total,
            "ganho_percentual": gain, "audio_refinado_s": sum(b-a for a, b in (w["refine_window"] for w in windows)),
            "wall_perfil_s": wall, "candidata": approved and gain >= 30}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, default=ROOT / "temp/benchmark_hibrido_selecionado")
    parser.add_argument("--saida", type=Path, default=ROOT / "temp/benchmark_hibrido_contexto")
    args = parser.parse_args()
    os.environ.update(HF_HUB_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1")
    if os.name == "nt":
        import ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel.SetPriorityClass.restype = ctypes.c_int
        if not kernel.SetPriorityClass(ctypes.c_void_p(-1), 0x4000):
            raise ctypes.WinError(ctypes.get_last_error())
    from modules import transcricao as T
    from faster_whisper.audio import decode_audio
    baseline, first, old = [json.loads((args.base / n).read_text(encoding="utf-8")) for n in ("cpu.json", "gpu.json", "resumo.json")]
    reference, gpu = restaurar(baseline["resultado"]), restaurar(first["resultado"])
    selecao = next(c["selecao"] for c in old["cenarios"] if c["cortes"] == 5)
    source = Path(first["resultado"]["video_path"])
    setup = {"cpu_completo_s": baseline["tempo_s"], "gpu_s": first["tempo_s"]}
    report = {"selecao": selecao, "setup": setup, "limites": LIMITES, "episodio": str(source),
              "perfis": [], "apis_reais": 0, "guard_band_s": 2., "prioridade": "BelowNormal somente neste processo",
              "prioridade_aplicada": "BelowNormal" if os.name == "nt" else "padrao do SO",
              "baseline_limitacao": "CPU medido antes do travamento; ganho observacional"}
    args.saida.mkdir(parents=True, exist_ok=True)
    canonical = referencia_fixa(selecao, gpu, reference)
    report["referencias_pendentes"] = {k:v for k,v in canonical.items() if isinstance(v, str)}
    with patch("socket.socket.connect", side_effect=AssertionError("Offline")), \
            patch("socket.socket.connect_ex", side_effect=AssertionError("Offline")):
        t = perf_counter()
        audio = decode_audio(str(source), sampling_rate=H.SAMPLE_RATE)
        setup["decode_s"] = perf_counter()-t
        t = perf_counter()
        model = T._obter_modelo("small", 6)
        setup["load_cpu_s"] = perf_counter()-t
        identity = H.identidade_episodio(source)
        profiles = [(c, True, True) for c in (5., 10., 20., 30.)]
        for c, vad, previous in profiles:
            p = executar_perfil(c, vad, previous, selecao, gpu, reference, canonical, audio, model,
                                args.saida / "cache", identity, setup, args.saida)
            report["perfis"].append(p)
            salvar(args.saida / "resumo.json", report)
            print("PROFILE", c, p["metricas_finais"], p["tempo_total_s"], p["trechos_aprovados"], p["gate_aprovado"], flush=True)
        best = max(report["perfis"], key=lambda p:(p["trechos_aprovados"],
            -(p["metricas_finais"]["pior"] if p["metricas_finais"]["pior"] is not None else math.inf), -p["tempo_total_s"]))
        for vad, previous in ((False, True), (True, False)):
            p = executar_perfil(best["contexto_s"], vad, previous, selecao, gpu, reference, canonical, audio, model,
                                args.saida / "cache", identity, setup, args.saida)
            report["perfis"].append(p)
            salvar(args.saida / "resumo.json", report)
            print("CONTROL", vad, previous, p["metricas_finais"], p["tempo_total_s"], p["trechos_aprovados"], flush=True)
    report["melhor_contexto_principal_s"] = best["contexto_s"]
    report["candidata"] = any(p["candidata"] for p in report["perfis"])
    salvar(args.saida / "resumo.json", report)
    finalizar_contexto(args.saida, args.base)
    print("FINAL", report["candidata"], flush=True)


def finalizar_contexto(folder, base):
    folder, base = Path(folder), Path(base)
    report = json.loads((folder / "resumo.json").read_text(encoding="utf-8"))
    baseline = json.loads((base / "cpu.json").read_text(encoding="utf-8"))
    first = json.loads((base / "gpu.json").read_text(encoding="utf-8"))
    for p in report["perfis"]:
        groups = {"bordas": [], "centro": []}
        critical = {"bordas": 0, "centro": 0}
        for w in p["windows"]:
            a, b = w["refine_window"]
            for row in w.get("diagnostico", {}).get("pareamento", []):
                mid = (row["referencia"]["inicio"]+row["referencia"]["fim"])/2
                group = "bordas" if mid < a+5. or mid >= b-5. else "centro"
                groups[group].extend(row["erros"])
                critical[group] += sum(e > LIMITES["max_palavras_s"] for e in row["erros"])
        p["metricas_bordas_centro"] = {k: estatisticas(v) | {"fronteiras_pareadas": len(v),
            "fronteiras_criticas": critical[k]} for k, v in groups.items()}
        gates = [t["gate_temporal"] for t in p["trechos"] if "gate_temporal" in t]
        words_ref = sum(q["palavras_pareadas"]+q["palavras_perdidas"] for q in gates)
        p["diagnostico_texto_segmentos"] = {
            "escopo": "trechos com referencia e mapeamento disponiveis; pendentes bloqueiam o gate integral",
            "divergencia_textual": sum(q["palavras_perdidas"]+q["palavras_extras"] for q in gates)/max(1, words_ref),
            "segmentos_referencia": sum(q["segmentos_referencia"] for q in gates),
            "segmentos_candidato": sum(q["segmentos_candidato"] for q in gates),
            "segmentos_deslocados": sum(q["segmentos_deslocados"] for q in gates),
            "segmentos_criticos": sum(q["segmentos_criticos"] for q in gates)}
        p["tempo_total_com_load_gpu_s"] = p["tempo_total_s"]+first["modelo_load_s"]
        p["ganho_com_load_percentual"] = (1-p["tempo_total_com_load_gpu_s"]/(baseline["tempo_s"]+baseline["modelo_load_s"]))*100
    report["semantica_precise"] = "CPU processado e aprovado no gate; cache CPU bruto permanece precise_unverified"
    report["diagnostico_bordas"] = "primeiros e ultimos 5 s das refine windows; centro e todo o restante"
    if "prioridade_aplicada" not in report:
        report["prioridade"] = "Normal verificado no processo; chamada BelowNormal inicial falhou, corrigida para futuras execucoes"
    salvar(folder / "resumo.json", report)
    return report


if __name__ == "__main__":
    main()
