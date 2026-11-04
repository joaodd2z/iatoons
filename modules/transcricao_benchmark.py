"""Conservative comparison against Preciso; no ground-truth accuracy claim."""
from difflib import SequenceMatcher
import math
import re
from statistics import mean

LIMITES_LEGADOS = {"erro_texto": .05, "perdidas": .03, "extras": .03,
           "media_palavras_s": .08, "p95_palavras_s": .20,
           "max_palavras_s": .35, "media_segmentos_s": .15, "max_segmentos_s": .35}


def normalizar(texto):
    return " ".join(re.findall(r"\w+", texto.lower()))


def comparar_legado(referencia, candidato):
    rw = [p for s in referencia.segmentos for p in s.palavras]
    cw = [p for s in candidato.segmentos for p in s.palavras]
    rt, ct = [normalizar(p.texto) for p in rw], [normalizar(p.texto) for p in cw]
    pareados = [(a + i, b + i) for a, b, n in SequenceMatcher(None, rt, ct, autojunk=False).get_matching_blocks()
                for i in range(n)]
    deltas = [abs(getattr(rw[a], atributo) - getattr(cw[b], atributo))
              for a, b in pareados for atributo in ("inicio", "fim")]
    segment_deltas = []
    rs, cs = referencia.segmentos, candidato.segmentos
    segment_pairs = [(a+i,b+i) for a,b,n in SequenceMatcher(None,
        [normalizar(s.texto) for s in rs], [normalizar(s.texto) for s in cs], autojunk=False).get_matching_blocks()
        for i in range(n)]
    for a, b in segment_pairs:
        segment_deltas.extend((abs(rs[a].inicio-cs[b].inicio), abs(rs[a].fim-cs[b].fim)))
    faltam = len(rw)-len(pareados)
    extras = len(cw)-len(pareados)
    validos = bool(rw and cw) and all(
        math.isfinite(p.inicio) and math.isfinite(p.fim) and 0 <= p.inicio <= p.fim <= candidato.duracao+.25
        and s.inicio-.25 <= p.inicio and p.fim <= s.fim+.25
        for s in cs for p in s.palavras)
    validos = validos and all(math.isfinite(s.inicio) and math.isfinite(s.fim)
                             and 0 <= s.inicio < s.fim <= candidato.duracao+.25 for s in cs)
    validos = validos and all(a.inicio <= b.inicio for a,b in zip(cw,cw[1:]))
    medidas = {"erro_texto": (faltam+extras)/max(1,len(rw)), "perdidas": faltam/max(1,len(rw)),
               "extras": extras/max(1,len(rw)), "media_palavras_s": mean(deltas) if deltas else 999,
               "p95_palavras_s": sorted(deltas)[max(0,math.ceil(len(deltas)*.95)-1)] if deltas else 999,
               "max_palavras_s": max(deltas,default=999),
               "media_segmentos_s": mean(segment_deltas) if segment_deltas else 999,
               "max_segmentos_s": max(segment_deltas,default=999)}
    motivos = [nome for nome, limite in LIMITES_LEGADOS.items() if medidas[nome] > limite]
    if not validos:
        motivos.append("timestamps_invalidos")
    # Whisper's reference itself can contain zero-duration words. Preserve the
    # reference, but do not approve a candidate that introduces more of them.
    zeros_ref = sum(p.fim == p.inicio for p in rw)
    zeros_cand = sum(p.fim == p.inicio for p in cw)
    if zeros_cand > zeros_ref:
        motivos.append("novas_palavras_sem_duracao")
    if len(segment_pairs) != len(rs) or len(cs) != len(rs):
        motivos.append("segmentos_alterados")
    if abs(referencia.duracao-candidato.duracao) > .1:
        motivos.append("duracao_alterada")
    return {**medidas, "aprovado": not motivos, "motivos": motivos,
            "palavras_perdidas": faltam, "palavras_extras": extras,
            "segmentos_referencia": len(rs), "segmentos_candidato": len(cs),
            "segmentos_pareados": len(segment_pairs), "duracao_s": candidato.duracao}


LIMITES = {"erro_texto": .05, "perdidas": .03, "extras": .03,
           "media_palavras_s": .15, "p95_palavras_s": .40, "max_palavras_s": .80,
           "media_segmentos_s": .20, "p95_segmentos_s": .50, "max_segmentos_s": .80}
CATEGORIAS = {"fala_limpa", "dialogo_rapido", "sobreposicao", "silencio",
              "musica", "gritos", "animacao_real"}
VERSAO_BENCHMARK = 2


def estatisticas(deltas):
    return {"media": mean(deltas) if deltas else None,
            "p95": sorted(deltas)[max(0, math.ceil(len(deltas) * .95) - 1)] if deltas else None,
            "pior": max(deltas) if deltas else None}


def _palavras(t):
    return [(token, p, i) for i, s in enumerate(t.segmentos) for p in s.palavras
            for token in normalizar(p.texto).split()]


def comparar(referencia, candidato, anotacoes=None):
    from modules.transcricao_temporal import validar_timestamps
    rw, cw = _palavras(referencia), _palavras(candidato)
    rt, ct = [p[0] for p in rw], [p[0] for p in cw]
    matcher = SequenceMatcher(None, rt, ct, autojunk=False)
    pares = [(a + i, b + i) for a, b, n in matcher.get_matching_blocks() for i in range(n)]
    inicio = [abs(rw[a][1].inicio - cw[b][1].inicio) for a, b in pares]
    fim = [abs(rw[a][1].fim - cw[b][1].fim) for a, b in pares]
    substituicoes = exclusoes = insercoes = 0
    for tag, a, b, c, d in matcher.get_opcodes():
        if tag == "replace":
            substituicoes += min(b - a, d - c)
            exclusoes += max(0, b - a - (d - c))
            insercoes += max(0, d - c - (b - a))
        elif tag == "delete":
            exclusoes += b - a
        elif tag == "insert":
            insercoes += d - c
    faltam, extras = len(rw) - len(pares), len(cw) - len(pares)
    segmentos_inicio, segmentos_fim, detalhes, nao_pareados = [], [], [], 0
    for i, s in enumerate(referencia.segmentos):
        indices = [a for a, w in enumerate(rw) if w[2] == i]
        relacionados = [(a, b) for a, b in pares if rw[a][2] == i]
        if not indices or not relacionados:
            if s.texto.strip():
                nao_pareados += 1
            continue
        # Measure actual segment boundaries, never pretend a merged segment was split.
        if relacionados[0][0] != indices[0] or relacionados[-1][0] != indices[-1]:
            nao_pareados += 1
            continue
        c0 = candidato.segmentos[cw[relacionados[0][1]][2]]
        c1 = candidato.segmentos[cw[relacionados[-1][1]][2]]
        di, df = abs(s.inicio - c0.inicio), abs(s.fim - c1.fim)
        segmentos_inicio.append(di)
        segmentos_fim.append(df)
        detalhes.append({"segmento": i, "inicio_s": di, "fim_s": df,
                         "candidato_inicio": c0.inicio, "candidato_fim": c1.fim,
                         "deslocado": max(di, df) > .5, "critico": max(di, df) > .8})
    ws, ss = estatisticas(inicio + fim), estatisticas(segmentos_inicio + segmentos_fim)
    problemas = validar_timestamps(candidato.segmentos, candidato.duracao)
    silencio = not referencia.texto_corrido().strip() and not candidato.texto_corrido().strip()
    medidas = {"erro_texto": (faltam + extras) / max(1, len(rw)), "perdidas": faltam / max(1, len(rw)),
               "extras": extras / max(1, len(rw)),
               "media_palavras_s": ws["media"], "p95_palavras_s": ws["p95"], "max_palavras_s": ws["pior"],
               "media_segmentos_s": ss["media"], "p95_segmentos_s": ss["p95"], "max_segmentos_s": ss["pior"]}
    motivos = [nome for nome, limite in LIMITES.items() if medidas[nome] is not None and medidas[nome] > limite + 1e-9]
    if not silencio and (not pares or ss["media"] is None
                         or any(not s.palavras for s in candidato.segmentos if s.texto.strip())):
        motivos.append("medidas_temporais_incompletas")
    if any(p["critico"] for p in problemas):
        motivos.append("timestamps_invalidos")
    if sum(w[1].fim == w[1].inicio for w in cw) > sum(w[1].fim == w[1].inicio for w in rw):
        motivos.append("novas_palavras_sem_duracao")
    if nao_pareados:
        motivos.append("fronteiras_nao_pareadas")
    if abs(referencia.duracao - candidato.duracao) > .1:
        motivos.append("duracao_alterada")
    if normalizar(referencia.texto_corrido()) != " ".join(rt) and not silencio:
        motivos.append("referencia_sem_cobertura_palavras")
    if normalizar(candidato.texto_corrido()) != " ".join(ct) and not silencio:
        motivos.append("candidato_sem_cobertura_palavras")
    humanos = []
    for nota in anotacoes or []:
        i = nota["segmento"]
        par = next((d for d in detalhes if d["segmento"] == i), None)
        if par is None:
            motivos.append("fala_anotada_ausente")
            continue
        di, df = abs(par["candidato_inicio"] - nota["inicio_real"]), abs(par["candidato_fim"] - nota["fim_real"])
        humanos.append({"segmento": i, "erro_inicio_real_s": di, "erro_fim_real_s": df})
        if max(di, df) > .8:
            motivos.append("fronteira_humana_critica")
    return {**medidas, "aprovado": not motivos, "motivos": list(dict.fromkeys(motivos)),
            "versao": VERSAO_BENCHMARK, "limites": LIMITES.copy(),
            "media_inicio_segmentos_s": estatisticas(segmentos_inicio)["media"],
            "media_fim_segmentos_s": estatisticas(segmentos_fim)["media"],
            "media_inicio_palavras_s": estatisticas(inicio)["media"],
            "media_fim_palavras_s": estatisticas(fim)["media"],
            "wer_aproximado": (substituicoes + exclusoes + insercoes) / max(1, len(rt)),
            "texto_referencia": normalizar(referencia.texto_corrido()),
            "texto_candidato": normalizar(candidato.texto_corrido()),
            "palavras_perdidas": faltam, "palavras_extras": extras,
            "palavras_pareadas": len(pares), "segmentos_referencia": len(referencia.segmentos),
            "palavras_erro_aceitavel": sum(max(a,b) <= .4 for a,b in zip(inicio,fim)),
            "palavras_erro_critico": sum(max(a,b) > .8 for a,b in zip(inicio,fim)),
            "segmentos_candidato": len(candidato.segmentos), "segmentos_pareados": len(detalhes),
            "segmentos_deslocados": sum(d["deslocado"] for d in detalhes),
            "segmentos_criticos": sum(d["critico"] for d in detalhes), "detalhes_segmentos": detalhes,
            "validacao_temporal": problemas, "duracao_s": candidato.duracao,
            "duracao_referencia_s": referencia.duracao,
            "diferenca_duracao_s": candidato.duracao-referencia.duracao,
            "inicio_fala_referencia": referencia.segmentos[0].inicio if referencia.segmentos else None,
            "fim_fala_referencia": referencia.segmentos[-1].fim if referencia.segmentos else None,
            "inicio_fala_candidato": candidato.segmentos[0].inicio if candidato.segmentos else None,
            "fim_fala_candidato": candidato.segmentos[-1].fim if candidato.segmentos else None,
            "fronteiras_humanas": humanos, "ground_truth_disponivel": bool(humanos), "silencio": silencio}
