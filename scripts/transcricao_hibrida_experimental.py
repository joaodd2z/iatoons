"""Selected-region GPU -> CPU experiment, deliberately outside production imports."""
from copy import deepcopy
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
import hashlib
import json
import math
from pathlib import Path
from time import perf_counter

from modules.transcricao import PalavraTranscrita, SegmentoTranscricao, Transcricao
from modules.transcricao_benchmark import normalizar, comparar, LIMITES
from modules.transcricao_temporal import recompor_chunk, validar_timestamps
from modules.transcricao_v2 import ConfigTranscricao

VERSION = 2
SAMPLE_RATE = 16000


@dataclass
class Passagem:
    transcricao: Transcricao
    timestamp_quality: str
    fallback_cpu: bool = False

    def serializar(self):
        data = asdict(self.transcricao)
        data["timestamp_quality"] = self.timestamp_quality
        for s in data["segmentos"]:
            s["timestamp_quality"] = self.timestamp_quality
        return data


def primeira_passagem(gpu, cpu):
    try:
        return Passagem(gpu(), "provisional")
    except Exception:
        return Passagem(cpu(), "precise_unverified", True)


def identidade_episodio(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def unir_regioes(trechos, duracao, margem=3., gap=0.):
    if not (math.isfinite(duracao) and duracao > 0 and math.isfinite(margem)
            and margem >= 0 and math.isfinite(gap) and gap >= 0):
        raise ValueError("Duracao/margem/gap invalidos")
    regions = []
    for t in trechos:
        a, b = t["inicio"], t["fim"]
        if not (math.isfinite(a) and math.isfinite(b) and 0 <= a < b <= duracao):
            raise ValueError("Intervalo fonte invalido")
        # Cache and slicing share the exact sample grid, including fractional bounds.
        a = math.floor(max(0., a - margem) * SAMPLE_RATE) / SAMPLE_RATE
        b = math.ceil(min(duracao, b + margem) * SAMPLE_RATE) / SAMPLE_RATE
        b = min(b, duracao)
        regions.append([a, b])
    merged = []
    for a, b in sorted(regions):
        if merged and a <= merged[-1][1] + gap:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [tuple(r) for r in merged]


def chave_cache(identity, a, b, modelo, idioma, opcoes=None):
    key = {"episode_identity": identity, "inicio_amostra": round(a * SAMPLE_RATE),
           "fim_amostra": round(b * SAMPLE_RATE), "modelo": modelo, "idioma": idioma,
           "versao": VERSION, "config": asdict(ConfigTranscricao()) | (opcoes or {})}
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest(), key


def desserializar_segmentos(data):
    return [SegmentoTranscricao(**(dict(s) | {"palavras": [PalavraTranscrita(**p) for p in s["palavras"]]}))
            for s in data]


def verificar_regiao(segmentos, a, b, duracao):
    if any(p["critico"] for p in validar_timestamps(segmentos, duracao)):
        raise ValueError("CPU Preciso retornou timestamps estruturalmente invalidos")
    if any(s.inicio < a - .025 or s.fim > b + .025 for s in segmentos):
        raise ValueError("Offset fora da regiao precisa")


class CachePreciso:
    def __init__(self, folder, identity, duracao, modelo="small", idioma="pt", opcoes=None):
        self.folder, self.identity, self.duracao = Path(folder), identity, duracao
        self.modelo, self.idioma = modelo, idioma
        self.memory = {}
        self.opcoes = opcoes

    def obter(self, a, b, cpu, fallback_cpu=None):
        key, meta = chave_cache(self.identity, a, b, self.modelo, self.idioma, self.opcoes)
        path = self.folder / (key + ".json")
        if key in self.memory:
            return deepcopy(self.memory[key]), True
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                payload = json.dumps(data["segmentos"], sort_keys=True, ensure_ascii=False).encode()
                if data["meta"] != meta or data["timestamp_quality"] != "precise_unverified" or \
                        hashlib.sha256(payload).hexdigest() != data["sha256_segmentos"]:
                    raise ValueError("Cache incompativel")
                verificar_regiao(desserializar_segmentos(data["segmentos"]), a, b, self.duracao)
                self.memory[key] = data
                return deepcopy(data), True
            except (ValueError, KeyError, TypeError):
                pass
        t = perf_counter()
        try:
            local = cpu(a, b)
            segmentos = recompor_chunk(local, a, self.duracao, relativos=True, duracao_chunk=b-a)
            verificar_regiao(segmentos, a, b, self.duracao)
            retried = False
        except Exception:
            if fallback_cpu is None:
                raise
            # Failed work is never cached; only one explicit CPU-only retry is allowed.
            local = fallback_cpu(a, b)
            segmentos = recompor_chunk(local, a, self.duracao, relativos=True, duracao_chunk=b-a)
            verificar_regiao(segmentos, a, b, self.duracao)
            retried = True
        data = {"meta": meta, "timestamp_quality": "precise_unverified", "tempo_cpu_s": perf_counter()-t,
                "retry_cpu": retried, "segmentos": [asdict(s) for s in segmentos]}
        data["sha256_segmentos"] = hashlib.sha256(
            json.dumps(data["segmentos"], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        self.folder.mkdir(parents=True, exist_ok=True)
        from scripts.benchmark_hibrido_selecionado import salvar
        salvar(path, data)
        self.memory[key] = data
        return deepcopy(data), False


def palavras(segmentos):
    return [(p, i) for i, s in enumerate(segmentos) for p in s.palavras if normalizar(p.texto)]


def mapear_trecho(trecho, provisoria, precisos, regiao, margem=3.):
    a, b = regiao
    gp = [(p, i) for p, i in palavras(provisoria.segmentos) if a <= (p.inicio+p.fim)/2 <= b]
    cp = palavras(precisos)
    selecionados = [i for i, (p, _) in enumerate(gp) if trecho["inicio"] <= (p.inicio+p.fim)/2 < trecho["fim"]]
    if not selecionados or not cp:
        raise ValueError("Trecho sem ancora textual; revisao necessaria, nao exportar")
    matcher = SequenceMatcher(None, [normalizar(p.texto) for p, _ in gp],
                              [normalizar(p.texto) for p, _ in cp], autojunk=False)
    matches = {i+j: k+j for i, k, n in matcher.get_matching_blocks() for j in range(n)}
    coverage = sum(i in matches for i in selecionados) / len(selecionados)
    if coverage < .8 or selecionados[0] not in matches or selecionados[-1] not in matches:
        raise ValueError("Ancora narrativa ambigua apos CPU; preservar decisao e pedir revisao")
    first, last = matches[selecionados[0]], matches[selecionados[-1]]
    if first > last:
        raise ValueError("Ancora narrativa regressiva")
    inicio, fim = cp[first][0].inicio, cp[last][0].fim
    if max(abs(inicio - trecho["inicio"]), abs(fim - trecho["fim"])) > margem + .1:
        raise ValueError("Fronteira precisa excedeu margem segura; nao exportar")
    segments = []
    for i, s in enumerate(precisos):
        selected = [deepcopy(p) for j, (p, segment_index) in enumerate(cp) if segment_index == i and first <= j <= last]
        if selected:
            segments.append(SegmentoTranscricao(selected[0].inicio, selected[-1].fim,
                " ".join(p.texto.strip() for p in selected), selected))
    verificar_regiao(segments, a, b, provisoria.duracao)
    result = deepcopy(trecho)
    result.update(inicio_original=trecho["inicio"], fim_original=trecho["fim"], inicio=inicio, fim=fim,
                  timestamp_quality="precise_unverified", segmentos=[asdict(s) | {"timestamp_quality": "precise_unverified"} for s in segments],
                  texto=" ".join(s.texto for s in segments), cobertura_ancora=coverage,
                  ajuste_inicio_s=inicio-trecho["inicio"], ajuste_fim_s=fim-trecho["fim"])
    return result


def validar_cpu_trechos(trechos, duracao):
    if not trechos:
        raise ValueError("Timeline vazia")
    for t in trechos:
        if t.get("timestamp_quality") not in ("precise", "precise_unverified") or not t.get("segmentos"):
            raise ValueError("Timing provisorio ou incompleto bloqueado")
        clean = []
        for s in t["segmentos"]:
            s = dict(s)
            if s.pop("timestamp_quality", None) not in ("precise", "precise_unverified"):
                raise ValueError("Segmento provisorio bloqueado")
            clean.append(s)
        segs = desserializar_segmentos(clean)
        verificar_regiao(segs, t["inicio"], t["fim"], duracao)
        if abs(segs[0].inicio-t["inicio"]) > .025 or abs(segs[-1].fim-t["fim"]) > .025:
            raise ValueError("Fronteiras da timeline nao correspondem ao texto preciso")
    cut_ids = {t.get("cut_id", "single") for t in trechos}
    for cut_id in cut_ids:
        chronological = sorted((t for t in trechos if t.get("cut_id", "single") == cut_id), key=lambda t: t["inicio"])
        if any(b["inicio"] < a["fim"]-.08 for a, b in zip(chronological, chronological[1:])):
            raise ValueError("Sobreposicao indevida de fontes no mesmo corte")
    return True


def exigir_precisos(trechos, duracao):
    validar_cpu_trechos(trechos, duracao)
    for t in trechos:
        if t.get("timestamp_quality") != "precise" or not t.get("gate_temporal", {}).get("aprovado"):
            raise ValueError("Timing CPU nao validado pelo gate bloqueado")
        if any(s.get("timestamp_quality") != "precise" for s in t["segmentos"]):
            raise ValueError("Segmento CPU nao validado bloqueado")
    return True


def aprovar_temporal(trecho, referencia, template):
    result = deepcopy(trecho)
    quality = comparar(referencia, transcricao_trecho(template, result))
    result["gate_temporal"] = quality
    if quality["aprovado"]:
        result["timestamp_quality"] = "precise"
        for s in result["segmentos"]:
            s["timestamp_quality"] = "precise"
    else:
        result["timestamp_quality"] = "precise_unverified"
        for s in result["segmentos"]:
            s["timestamp_quality"] = "precise_unverified"
    return result


def refinar_selecao(passagem, selecao, cache, cpu, margem=3., fallback_cpu=None):
    source = passagem.transcricao
    regions = unir_regioes(selecao, source.duracao, margem)
    if passagem.timestamp_quality in ("precise", "precise_unverified"):
        final = [mapear_trecho(t, source, source.segmentos, (0., source.duracao), margem) for t in selecao]
        validar_cpu_trechos(final, source.duracao)
        return {"timeline": final, "pendentes": [], "regioes": [], "todos_precisos": False, "todos_processados_cpu": True,
                "audio_retranscrito_s": 0., "tempo_cpu_regioes_s": 0.}
    if passagem.timestamp_quality != "provisional":
        raise ValueError("Qualidade temporal desconhecida")
    refined = []
    for a, b in regions:
        started = perf_counter()
        print("REGIAO CPU", round(a, 3), round(b, 3), flush=True)
        try:
            data, hit = cache.obter(a, b, cpu, fallback_cpu)
            refined.append({"inicio": a, "fim": b, "cache_hit": hit, **data})
        except Exception as exc:
            refined.append({"inicio": a, "fim": b, "erro": str(exc), "tempo_cpu_s": perf_counter()-started,
                            "timestamp_quality": "pending", "segmentos": []})
    final, pending = [], []
    for selected in selecao:
        region = next(r for r in refined if r["inicio"] <= selected["inicio"] and r["fim"] >= selected["fim"])
        try:
            if region.get("erro"):
                raise ValueError(region["erro"])
            final.append(mapear_trecho(selected, source, desserializar_segmentos(region["segmentos"]),
                                      (region["inicio"], region["fim"]), margem))
        except ValueError as exc:
            pending.append({"trecho": deepcopy(selected), "motivo": str(exc)})
    # Narrative order and identifiers are unchanged; no editorial API exists here.
    for cut_id in {t.get("cut_id", "single") for t in final}:
        parts = [t for t in final if t.get("cut_id", "single") == cut_id]
        try:
            validar_cpu_trechos(parts, source.duracao)
        except ValueError as exc:
            ids = {t["trecho_id"] for t in parts}
            pending.extend({"trecho": deepcopy(t), "motivo": str(exc)} for t in selecao if t["trecho_id"] in ids)
            final = [t for t in final if t["trecho_id"] not in ids]
    return {"timeline": final, "pendentes": pending, "regioes": refined,
            "todos_precisos": False, "todos_processados_cpu": not pending and len(final) == len(selecao),
            "audio_retranscrito_s": sum(b-a for a, b in regions),
            "tempo_cpu_regioes_s": sum(r["tempo_cpu_s"] for r in refined)}


def transcricao_trecho(template, trecho):
    trans = deepcopy(template)
    clean = [dict(s) for s in trecho["segmentos"]]
    for s in clean:
        s.pop("timestamp_quality", None)
    trans.segmentos = desserializar_segmentos(clean)
    return trans


def cpu_array(model, audio, a, b, idioma="pt", *, vad_filter=True, condition_on_previous_text=True):
    clip = audio[round(a*SAMPLE_RATE):round(b*SAMPLE_RATE)]
    segments, _ = model.transcribe(clip, language=idioma, beam_size=5, vad_filter=vad_filter,
                                   word_timestamps=True, condition_on_previous_text=condition_on_previous_text)
    return [SegmentoTranscricao(float(s.start), float(s.end), s.text.strip(),
        [PalavraTranscrita(float(w.start), float(w.end), w.word, float(w.probability)) for w in s.words or []])
        for s in segments]


def simular_selecoes(source):
    """Narrative load simulation, not an assertion that these are good stories."""
    candidates = []
    all_words = [p for p, _ in palavras(source.segmentos) if p.fim > p.inicio]
    for i in range(10):
        target = max(45., (i+.5)*source.duracao/10 - 32.)
        words = []
        for shift in (0., -20., 20., -40., 40.):
            a = max(0., target+shift)
            b = min(source.duracao-10., a+64.)
            words = [p for p in all_words if a <= p.inicio and p.fim <= b]
            if len(words) >= 20:
                break
        if len(words) < 20:
            raise ValueError("Amostra insuficiente para dez simulacoes realistas")
        # Snap to provisional word boundaries. Keep hook first, then context and arc.
        n = len(words)
        sections = ((int(n*.75), n, "gancho"), (0, int(n*.35), "contexto"),
                    (int(n*.35), int(n*.75), "desenvolvimento"))
        candidate = [{"cut_id": str(i+1), "trecho_id": f"{i+1}-{role}", "funcao": role,
                      "inicio": words[x].inicio, "fim": words[y-1].fim} for x, y, role in sections]
        candidates.append(candidate)
    return {3: [candidates[i] for i in (0, 4, 9)], 5: [candidates[i] for i in (0, 2, 4, 6, 9)], 10: candidates}


def executar_benchmark(episodio, folder):
    from scripts.benchmark_gpu_isolado import restaurar
    from scripts.benchmark_hibrido_selecionado import salvar
    from modules import transcricao as T
    from faster_whisper.audio import decode_audio
    baseline = json.loads((folder / "cpu.json").read_text(encoding="utf-8"))
    first = json.loads((folder / "gpu.json").read_text(encoding="utf-8"))
    reference, gpu = restaurar(baseline["resultado"]), restaurar(first["resultado"])
    passagem = Passagem(gpu, first.get("timestamp_quality", "provisional"), first.get("fallback_cpu", False))
    salvar(folder / "gpu_provisional.json", passagem.serializar())
    t = perf_counter()
    audio = decode_audio(str(episodio), sampling_rate=SAMPLE_RATE)
    preparation = perf_counter() - t
    t = perf_counter()
    model = T._obter_modelo("small", 6)
    load = perf_counter()-t
    cache = CachePreciso(folder / "cache_regioes", identidade_episodio(episodio), gpu.duracao)
    cpu = lambda a, b: cpu_array(model, audio, a, b)
    report = {"versao": VERSION, "episodio": str(episodio), "duracao_s": gpu.duracao,
              "limites": LIMITES, "tempo_cpu_completo_s": baseline["tempo_s"],
              "tempo_gpu_primeira_s": first["tempo_s"], "modelo_cpu_load_s": load,
              "audio_decode_s": preparation, "estrategia_b": "provisional, nunca producao",
              "apis_reais": 0, "cenarios": []}
    report["limitacao_baseline"] = "CPU medido antes do travamento da maquina; ganho observado nao e comparacao controlada"
    for count, cuts in simular_selecoes(gpu).items():
        selection = [t for cut in cuts for t in cut]
        t = perf_counter()
        outcome = refinar_selecao(passagem, selection, cache, cpu, fallback_cpu=cpu)
        incremental = perf_counter()-t
        qualities = []
        for final in outcome["timeline"]:
            original = next(s for s in selection if s["trecho_id"] == final["trecho_id"])
            region = next((r for r in outcome["regioes"] if r["inicio"] <= original["inicio"] and r["fim"] >= original["fim"]),
                          {"inicio": 0., "fim": reference.duracao})
            ref_segments = [s for s in reference.segmentos if s.inicio < region["fim"] and s.fim > region["inicio"]]
            try:
                ref = mapear_trecho(original, gpu, ref_segments, (max(0, region["inicio"]-4),
                    min(reference.duracao, region["fim"]+4)), margem=7.)
                quality = comparar(transcricao_trecho(reference, ref), transcricao_trecho(gpu, final))
                quality["desvio_inicio_timeline_s"] = abs(ref["inicio"] - final["inicio"])
                quality["desvio_fim_timeline_s"] = abs(ref["fim"] - final["fim"])
                if max(quality["desvio_inicio_timeline_s"], quality["desvio_fim_timeline_s"]) > .8:
                    quality["motivos"].append("fronteira_timeline")
                    quality["aprovado"] = False
            except ValueError as exc:
                quality = {"aprovado": False, "motivos": [str(exc)]}
            qualities.append({"trecho_id": final["trecho_id"], **quality})
            final["gate_temporal"] = quality
            if quality["aprovado"]:
                final["timestamp_quality"] = "precise"
                for s in final["segmentos"]:
                    s["timestamp_quality"] = "precise"
        outcome["todos_precisos"] = not outcome["pendentes"] and len(outcome["timeline"]) == len(selection) and all(q["aprovado"] for q in qualities)
        cpu_cost = outcome["tempo_cpu_regioes_s"] + preparation + load
        total = first["tempo_s"] + cpu_cost
        scenario = {"cortes": count, "selecao": selection, **outcome, "qualidades": qualities,
                    "tempo_cpu_refinamento_s": cpu_cost, "tempo_hibrido_s": total,
                    "tempo_incremental_com_cache_s": incremental,
                    "ganho_percentual": (1-total/baseline["tempo_s"])*100,
                    "gate_aprovado": outcome["todos_precisos"] and all(q["aprovado"] for q in qualities)}
        # Persist only ready cuts; never call a renderer or export adapter.
        ready = []
        for cut in cuts:
            ids = [s["trecho_id"] for s in cut]
            parts = [s for s in outcome["timeline"] if s["trecho_id"] in ids]
            if [s["trecho_id"] for s in parts] == ids and all(s["timestamp_quality"] == "precise" for s in parts):
                exigir_precisos(parts, gpu.duracao)
                ready.append({"cut_id": cut[0]["cut_id"], "trechos": parts})
        scenario["cortes_prontos_temporalmente"] = len(ready)
        scenario["timeline_simulada"] = ready
        report["cenarios"].append(scenario)
        salvar(folder / "resumo.json", report)
        print("CENARIO", count, "cpu", cpu_cost, "total", total, "ganho", scenario["ganho_percentual"],
              "precisos", outcome["todos_precisos"], "gate", scenario["gate_aprovado"], flush=True)
    selected = sum(c["audio_retranscrito_s"] for c in report["cenarios"])
    inference = sum(c["tempo_cpu_regioes_s"] for c in report["cenarios"])
    rate = inference / selected
    report["cpu_s_por_segundo_audio_regional"] = rate
    fixed = first["tempo_s"] + preparation + load
    report["break_even_audio_min_estimado"] = max(0., (baseline["tempo_s"]-fixed)/rate)/60
    report["limite_30_percent_audio_min_estimado"] = max(0., (.7*baseline["tempo_s"]-fixed)/rate)/60
    report["estimativa_nao_medicao_de_crossover"] = True
    report["recomendada"] = any(c["gate_aprovado"] and c["ganho_percentual"] >= 30 for c in report["cenarios"])
    salvar(folder / "resumo.json", report)
    finalizar_relatorio(folder)
    print("FINAL", "recomendada", report["recomendada"], flush=True)


def finalizar_relatorio(folder):
    from scripts.benchmark_gpu_isolado import restaurar
    from scripts.benchmark_hibrido_selecionado import salvar
    folder = Path(folder)
    baseline = json.loads((folder / "cpu.json").read_text(encoding="utf-8"))
    first = json.loads((folder / "gpu.json").read_text(encoding="utf-8"))
    report = json.loads((folder / "resumo.json").read_text(encoding="utf-8"))
    report["qualidade_gpu_sem_refinamento"] = comparar(restaurar(baseline["resultado"]), restaurar(first["resultado"]))
    report["cpu_completo_com_load_s"] = baseline["tempo_s"] + baseline["modelo_load_s"]
    report["gpu_primeira_com_load_s"] = first["tempo_s"] + first["modelo_load_s"]
    for c in report["cenarios"]:
        c["hibrido_com_load_gpu_s"] = c["tempo_hibrido_s"] + first["modelo_load_s"]
        c["ganho_com_load_percentual"] = (1-c["hibrido_com_load_gpu_s"]/report["cpu_completo_com_load_s"])*100
        c["trechos_selecionados"] = len(c["selecao"])
        verified = [t for t in c["timeline"] if t.get("timestamp_quality") == "precise" and t.get("gate_temporal", {}).get("aprovado")]
        c["trechos_precisos"] = len(verified)
        c["segmentos_precisos"] = sum(len(t["segmentos"]) for t in verified)
        c["cache_hits"] = sum(r.get("cache_hit", False) for r in c["regioes"])
        c["candidata_auto_futura"] = c["gate_aprovado"] and c["ganho_percentual"] >= 30
    # This is a counterfactual cost model, not a measured crossover experiment.
    full_rate = baseline["tempo_s"] / report["duracao_s"]
    fixed = report["tempo_gpu_primeira_s"] + report["audio_decode_s"] + report["modelo_cpu_load_s"]
    report["break_even_usando_taxa_cpu_completa_min_estimado"] = max(0., (baseline["tempo_s"]-fixed)/full_rate)/60
    report["limite_30_usando_taxa_cpu_completa_min_estimado"] = max(0., (.7*baseline["tempo_s"]-fixed)/full_rate)/60
    report["crossover_medido"] = False
    report["precise_significa"] = "CPU processado e aprovado no gate temporal relativo"
    salvar(folder / "resumo.json", report)
    return report
