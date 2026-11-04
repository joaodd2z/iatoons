"""Texto portatil dos remixes da Agencia para consulta externa pelo usuario."""
from __future__ import annotations

import re
from pathlib import Path

from modules.agencia.timeline import OpcoesEdicao, construir_timeline


INSTRUCOES = """Analise cada corte individualmente e crie títulos fortes para vídeos curtos de entretenimento.

Quero títulos específicos, engraçados, provocativos, extravagantes e com potencial de despertar curiosidade. Quando combinar com a cena, pode usar humor absurdo e palavrões moderados.

Não use títulos genéricos como “Por que Peter brigou?”, “O que aconteceu?” ou perguntas automáticas que não expressem o verdadeiro acontecimento.

Identifique o conflito central, a situação mais absurda, a consequência inesperada ou a contradição engraçada da cena. O título deve vender o acontecimento real do corte, não apenas repetir uma fala aleatória.

Não invente acontecimentos nem use informações que não sejam sustentadas pelo resumo ou pelas falas. Não confunda personagens, não entregue spoilers desnecessários e não produza títulos quase idênticos entre cortes.

Para cada corte, entregue:
1. O melhor título recomendado.
2. Duas alternativas realmente diferentes.
3. Um texto curto para a capa, quando apropriado.
4. Uma justificativa de uma frase explicando por que o título representa a cena.

Diferencie claramente o campo Título do campo Capa. Priorize especificidade, humor, impacto e curiosidade, sem sacrificar a fidelidade à história.

Organize a resposta pelo número de cada corte para que eu possa localizar facilmente os campos correspondentes no IA TOONS CUT."""

PAPEIS = {
    "gancho": "GANCHO",
    "contexto": "CONTEXTO",
    "desenvolvimento": "DESENVOLVIMENTO",
    "escalada": "ESCALADA",
    "conclusao": "CONSEQUÊNCIA / PAYOFF",
    "payoff": "CONSEQUÊNCIA / PAYOFF",
    "historia": "HISTÓRIA",
}


def _segundos(valor: float) -> str:
    return f"{float(valor):.2f}".rstrip("0").rstrip(".")


def _episodio(transcricao) -> str:
    nome = Path(transcricao.video_path).stem
    match = re.search(r"S\d{1,2}E\d{1,2}", nome, re.IGNORECASE)
    return match.group(0).upper() if match else nome or transcricao.video_id or "não disponível"


def _fala_intervalo(transcricao, inicio: float, fim: float) -> str:
    # Read only the loaded transcript. Word-timestamp transcripts are clipped
    # exactly as the render timeline; segment-only caches use their existing text.
    itens = []
    for segmento in transcricao.segmentos:
        if segmento.fim <= inicio or segmento.inicio >= fim:
            continue
        if segmento.palavras:
            itens.extend(((p.inicio + p.fim) / 2, p.texto.strip()) for p in segmento.palavras
                         if inicio <= (p.inicio + p.fim) / 2 < fim and p.texto.strip())
        elif segmento.texto.strip():
            itens.append(((max(inicio, segmento.inicio) + min(fim, segmento.fim)) / 2,
                          segmento.texto.strip()))
    return " ".join(texto for _, texto in sorted(itens)) or "não disponível"


def _estrutura(plano, timeline):
    estrutura = plano.estrutura or {}
    campos = (
        ("gancho", "Gancho"), ("contexto", "Contexto"),
        ("desenvolvimento", "Desenvolvimento"), ("escalada", "Escalada"),
        ("conclusao", "Consequência/payoff"), ("payoff", "Consequência/payoff"),
    )
    saida = []
    vistos = set()
    for chave, rotulo in campos:
        valor = estrutura.get(chave)
        if not valor or rotulo in vistos:
            continue
        vistos.add(rotulo)
        if isinstance(valor, dict):
            texto = valor.get("descricao") or valor.get("resumo") or valor.get("texto")
            intervalo = ""
            if valor.get("inicio") is not None and valor.get("fim") is not None:
                intervalo = f" ({_segundos(valor['inicio'])}–{_segundos(valor['fim'])}s)"
            saida.append(f"{rotulo}: {texto or 'não disponível'}{intervalo}")
        else:
            saida.append(f"{rotulo}: {valor}")
    if not saida:
        papeis = []
        for trecho in timeline.trechos:
            rotulo = PAPEIS.get(trecho.funcao, trecho.funcao.upper() if trecho.funcao else "HISTÓRIA")
            if rotulo not in papeis:
                papeis.append(rotulo)
        saida.append("Estrutura narrativa: " + " → ".join(papeis))
    return saida


def construir_pacote(planos, transcricao, *, selecionados=None, intro=None,
                     opcoes: OpcoesEdicao | None = None, campos_ui=None) -> tuple[str, int]:
    """Return clipboard text and count without mutating plans or transcription."""
    planos = list(planos)
    if selecionados:
        ids = set(selecionados)
        planos = [p for p in planos if p.id in ids]
    opcoes = (opcoes or OpcoesEdicao()).validar()
    blocos = [
        "INSTRUÇÕES PARA GERAR TÍTULOS\n\n" + INSTRUCOES,
        f"EPISÓDIO: {_episodio(transcricao)}",
    ]
    for numero, plano in enumerate(planos, 1):
        timeline = construir_timeline(plano, transcricao, intro, opcoes)
        titulo, capa = (campos_ui or {}).get(plano.id, (plano.titulo, plano.titulo_capa))
        linhas = [
            f"CORTE {numero} · ID: {plano.id or 'não disponível'}",
            f"Resumo: {plano.descricao or plano.motivo_escolha or 'não disponível'}",
            f"Duração final: {timeline.duracao:.2f}s",
            f"Título atual: {titulo or 'não disponível'}",
            f"Texto da capa: {capa or 'não disponível'}",
            "ORDEM FINAL DA MONTAGEM:",
        ]
        for indice, trecho in enumerate(timeline.trechos, 1):
            papel = PAPEIS.get(trecho.funcao, trecho.funcao.upper() if trecho.funcao else "HISTÓRIA")
            falas = _fala_intervalo(transcricao, trecho.inicio, trecho.fim)
            linhas.append(
                f"{indice}. Segmento original {_segundos(trecho.inicio)}–{_segundos(trecho.fim)}s — {papel}\n"
                f"   Falas: {falas}"
            )
        linhas.extend(_estrutura(plano, timeline))
        avisos = list(plano.avisos or [])
        if plano.erro:
            avisos.append(plano.erro)
        avisos.extend(g.get("detalhe", "") for g in plano.gate or [] if not g.get("ok") and g.get("detalhe"))
        revisao = plano.revisao_humana or {}
        avisos.extend(revisao.get("motivos", []))
        avisos.extend(plano.titulos_v2.get("alertas", []))
        linhas.append("Avisos/motivos: " + ("; ".join(dict.fromkeys(avisos)) if avisos else "não disponível"))
        rejeitados = [a for a in plano.titulos_v2.get("avaliados", [])
                      if not a.get("aprovado") and a.get("texto")]
        if rejeitados:
            linhas.append("Motivos editoriais de títulos rejeitados:")
            for item in rejeitados[:8]:
                motivos = "; ".join(item.get("motivos", [])) or "não disponível"
                linhas.append(f"- {item['texto']}: {motivos}")
        blocos.append("\n".join(linhas))
    return "\n\n".join(blocos), len(planos)
