"""VALIDAR_REMIX: no Modo Agência só existe remix REAL, comprovado pela timeline.

Não basta um campo ``usar_remix`` ou um gancho declarado: a timeline montada
precisa começar pelo gancho, voltar a um contexto ANTERIOR a ele e ser formada
por intervalos de origem distintos em ordem reorganizada. Um trecho contínuo
(ex.: 172–206 s) nunca é aceito, mesmo que a IA o tenha chamado de remix.
"""

from __future__ import annotations

import math

from modules.agencia.editorial import SALTO_MAXIMO_HISTORIA, PlanoAgencia
from modules.agencia.timeline import (
    OpcoesEdicao, TimelineAgencia, construir_timeline, palavras_entre,
)
from modules.intro import IntervaloIntro
from modules.legendas import BlocoLegenda
from modules.transcricao import Transcricao


CONTEXTO_MINIMO = 3.0      # segundos de história anteriores ao gancho
DURACAO_MINIMA_FINAL = 21.0  # regra da agência: vídeo final com MAIS de 20 s
# Únicas origens aceitas: análise de remix do Modo Agência e remixes do Remix Narrativo.
ORIGENS_REMIX = ("gemini_agencia", "remix")
GANCHO_MIN, GANCHO_MAX = 1.5, 6.5
REMIX_RULES_VERSION = "remix-universal-v1"
SCORE_REMIX_MINIMO = 75


def _normalizar(palavra: str) -> str:
    return "".join(c for c in palavra.casefold() if c.isalnum())


def _intervalos_distintos(timeline: TimelineAgencia) -> int:
    return len({(round(t.inicio, 2), round(t.fim, 2)) for t in timeline.trechos})


def _problemas_remix(plano: PlanoAgencia, timeline: TimelineAgencia,
                       transcricao: Transcricao | None = None,
                       legendas: list[BlocoLegenda] | None = None) -> list[str]:
    """Lista de problemas; vazia somente quando a montagem é um remix real."""
    problemas: list[str] = []
    if plano.origem not in ORIGENS_REMIX:
        # Cortes normais, análises comuns e sugestões estruturais (inclusive de listas
        # antigas) nunca entram, mesmo que tenham recebido um gancho depois.
        return [f"origem '{plano.origem}' não é remix narrativo"]
    if not plano.gancho or not plano.usar_remix:
        return ["candidato sem gancho de remix"]
    if timeline.tipo != "remix" or not timeline.gancho:
        motivo = "; ".join(timeline.avisos) or "montagem não reorganizada"
        return [f"a montagem virou corte cronológico ({motivo})"]
    trechos = timeline.trechos
    gancho = trechos[0]
    gi, gf = timeline.gancho
    if gancho.funcao != "gancho":
        problemas.append("a timeline não começa pelo gancho")
    if not GANCHO_MIN <= gf - gi <= GANCHO_MAX:
        problemas.append(f"gancho de {gf - gi:.1f}s fora de 2–6 s")
    if timeline.duracao < DURACAO_MINIMA_FINAL:
        problemas.append(f"duração final {timeline.duracao:.1f}s abaixo do mínimo de "
                         f"{DURACAO_MINIMA_FINAL:.0f}s (a agência exige mais de 20 s)")
    if len(trechos) < 2 or _intervalos_distintos(timeline) < 2:
        problemas.append("menos de 2 intervalos de origem distintos")
    historia = trechos[1:]
    # Reorganização temporal: depois do gancho a narrativa volta para ANTES dele.
    if not any(t.inicio < gi - 0.05 for t in historia):
        problemas.append("não há retorno a um contexto anterior ao gancho")
    contexto = sum(min(t.fim, gi) - t.inicio for t in historia if t.inicio < gi)
    if contexto < CONTEXTO_MINIMO:
        problemas.append(f"contexto anterior ao gancho curto demais ({contexto:.1f}s)")
    ordem_origem = [t.inicio for t in trechos]
    if ordem_origem == sorted(ordem_origem):
        problemas.append("a ordem final é igual à cronológica (intervalo contínuo)")
    # Continuidade semântica: uma única história, sem saltos entre situações.
    saltos = [b.inicio - a.fim for a, b in zip(historia, historia[1:])]
    if any(s > SALTO_MAXIMO_HISTORIA for s in saltos):
        problemas.append("a narrativa une trechos distantes (histórias diferentes)")
    if any(t.inicio < plano.inicio - 0.2 or t.fim > plano.fim + 0.2 for t in trechos):
        problemas.append("trecho fora da história do candidato")
    if timeline.gancho_repetido or any(t.inicio < gf - 0.05 and t.fim > gi + 0.05 for t in historia):
        problemas.append("o gancho foi repetido sem necessidade")
    # Legendas recalculadas: o início do vídeo precisa exibir as falas do gancho.
    if legendas is not None and transcricao is not None:
        palavras_gancho = palavras_entre(transcricao, gi, gf)
        if palavras_gancho:
            falado = {_normalizar(p.texto) for p in palavras_gancho}
            iniciais = [b for b in legendas if b.inicio < gancho.duracao - 0.05]
            if not iniciais:
                problemas.append("legendas do gancho ausentes no início do vídeo")
            for bloco in iniciais:
                # O texto exibido durante o gancho precisa ser o que é falado no gancho.
                exibido = {_normalizar(p) for p in bloco.texto.split()} - {""}
                if not exibido or len(exibido & falado) / len(exibido) < 0.6:
                    problemas.append("legendas não foram recalculadas para a ordem remixada")
                    break
        if legendas and legendas[-1].fim > timeline.duracao + 0.05:
            problemas.append("legendas ultrapassam a duração remixada")
    return problemas


from modules.performance import medir_operacao


@medir_operacao("validacao_remix")
def avaliar_remix_real(plano: PlanoAgencia, timeline: TimelineAgencia,
                      transcricao: Transcricao | None = None,
                      legendas: list[BlocoLegenda] | None = None,
                      intro: IntervaloIntro | None = None) -> dict:
    """Contrato tecnico unico. Score nao substitui nenhuma restricao obrigatoria.

    Continuidade usa a historia declarada, cobertura e limites reais de fala;
    nao pretende provar causalidade artistica por similaridade de palavras.
    """
    problemas = []
    trechos = timeline.trechos
    try:
        invalida = (not trechos or any(not math.isfinite(t.inicio + t.fim) or t.inicio < 0 or
                                      t.fim <= t.inicio for t in trechos))
        gancho_invalido = timeline.gancho and (len(timeline.gancho) != 2 or
                           not all(math.isfinite(t) for t in timeline.gancho))
    except (TypeError, ValueError):
        invalida, gancho_invalido = True, True
    if invalida:
        problemas.append("timeline vazia ou com intervalos invalidos")
    elif gancho_invalido:
        problemas.append("gancho com timestamps invalidos")
    else:
        problemas.extend(_problemas_remix(plano, timeline, transcricao, legendas))
        margem = max(0.025, 1 / timeline.fps)
        if timeline.gancho and (abs(trechos[0].inicio - timeline.gancho[0]) > margem or
                               abs(trechos[0].fim - timeline.gancho[1]) > margem):
            problemas.append("primeiro trecho difere do gancho declarado")
        ordenados = sorted(trechos, key=lambda t: t.inicio)
        if any(a.fim > b.inicio + margem for a, b in zip(ordenados, ordenados[1:])):
            problemas.append("sobreposicao ou repeticao de intervalos de origem")
        if any(b.inicio < a.fim - margem for a, b in zip(trechos[1:], trechos[2:])):
            problemas.append("historia posterior ao gancho fora da ordem causal")
        if timeline.duracao > 150:
            problemas.append("duracao final acima de 150s")
        if plano.fim - plano.inicio > 150:
            problemas.append("segmentos desconexos: historia ocupa mais de 150s da fonte")
        if any(not any(float(s["inicio"]) - margem <= t.inicio and
                       t.fim <= float(s["fim"]) + margem for s in plano.segmentos) for t in trechos):
            problemas.append("trecho nao pertence aos segmentos da historia declarada")
        if intro and intro.ativo and any(min(t.fim, intro.fim) - max(t.inicio, intro.inicio) > margem
                                        for t in trechos):
            problemas.append("intro presente na timeline final")
        # A historia precisa chegar ao instante antecipado, sem saltar a preparacao.
        if timeline.gancho:
            gi, gf = timeline.gancho
            chegada = any(t.funcao != "gancho" and abs(t.fim - gi) <= 1.0 for t in trechos)
            if not chegada:
                problemas.append("historia nao reencontra narrativamente o gancho")
            conclusao = plano.estrutura.get("conclusao") or plano.estrutura.get("payoff")
            if conclusao and not any(t.funcao != "gancho" and
                                    t.inicio <= float(conclusao["inicio"]) + margem and
                                    t.fim >= float(conclusao["fim"]) - margem for t in trechos):
                problemas.append("payoff disponivel foi removido")
        if transcricao:
            if plano.video_id and plano.video_id != transcricao.video_id:
                problemas.append("plano pertence a outro episodio")
            if any(t.fim > transcricao.duracao + margem for t in trechos):
                problemas.append("trecho fora da duracao do episodio")
            from modules.agencia.editorial import frases_no_intervalo
            frases = frases_no_intervalo(transcricao, 0, transcricao.duracao)
            for trecho in trechos:
                if any(f.inicio + margem < limite < f.fim - margem
                       for limite in (trecho.inicio, trecho.fim) for f in frases):
                    problemas.append("fala truncada em limite da timeline")
                    break
        if legendas is not None:
            if any(b.inicio < -0.001 or b.fim <= b.inicio or b.fim > timeline.duracao + margem
                   for b in legendas) or any(a.fim > b.inicio + margem
                                             for a, b in zip(legendas, legendas[1:])):
                problemas.append("legendas fora da timeline ou sobrepostas")
    problemas = list(dict.fromkeys(problemas))
    reordenada = bool(trechos) and [t.inicio for t in trechos] != sorted(t.inicio for t in trechos)
    score = max(0, 100 - 25 * len(problemas))
    valido = not problemas and score >= SCORE_REMIX_MINIMO
    resultado = {"remix_valido": valido, "score_remix": score, "problemas": problemas,
                 "motivo_rejeicao": "; ".join(problemas),
                 "segmentos_origem": _intervalos_distintos(timeline),
                 "reordenacao_comprovada": reordenada, "remix_rules_version": REMIX_RULES_VERSION}
    plano.remix_rules_version = REMIX_RULES_VERSION if valido else ""
    plano.validacao_remix = resultado
    return resultado


def validar_remix_real(plano: PlanoAgencia, timeline: TimelineAgencia,
                       transcricao: Transcricao | None = None,
                       legendas: list[BlocoLegenda] | None = None,
                       intro: IntervaloIntro | None = None) -> list[str]:
    return avaliar_remix_real(plano, timeline, transcricao, legendas, intro)["problemas"]


def verificar_plano_remix(plano: PlanoAgencia, transcricao: Transcricao,
                          intro: IntervaloIntro | None = None,
                          opcoes: OpcoesEdicao | None = None) -> list[str]:
    """Monta a timeline real do candidato e valida o remix (sem acessar o vídeo)."""
    try:
        timeline = construir_timeline(plano, transcricao, intro, opcoes or OpcoesEdicao())
    except ValueError as erro:
        return [str(erro)]
    return validar_remix_real(plano, timeline, transcricao, intro=intro)
