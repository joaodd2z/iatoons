"""Timeline editorial do Modo Agência.

GANCHO → CONTEXTO → DESENVOLVIMENTO → RETORNO AO MOMENTO DO GANCHO → CONSEQUÊNCIA

Regras:
- nenhum limite de trecho cai no meio de uma palavra/fala;
- silêncio inicial, pausas mortas e trechos marcados como irrelevantes saem;
- momento forte, reação e gancho nunca são compactados;
- transição é ferramenta editorial: HARD CUT por padrão; DISSOLVE só em mudança
  temporal/narrativa e somente sobre áudio sem fala (não atropela diálogo nem punchline).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Callable

from modules.agencia.editorial import (
    PlanoAgencia, ajustar_fim_sem_cortar_fala, ajustar_inicio_sem_cortar_fala, frases_no_intervalo,
)
from modules.ganchos_ab import TrechoFonte
from modules.intro import IntervaloIntro
from modules.legendas import BlocoLegenda
from modules.transcricao import PalavraTranscrita, Transcricao


@dataclass
class OpcoesEdicao:
    transition_mode: str = "agency"         # "agency" | "hard"
    transition_family: str = "dissolve"
    transition_duration: float = 1.0
    transicao_minima: float = 0.3
    salto_temporal_min: float = 8.0
    compactar_pausas: bool = True
    pausa_max: float = 1.6
    respiro_depois: float = 0.40
    respiro_antes: float = 0.25
    preroll: float = 0.12
    cauda_final: float = 0.8
    silencio_final_max: float = 1.5
    permitir_remix: bool = True
    gancho_sem_repeticao: bool = True
    palavras_por_bloco: int = 4
    limiar_movimento: float = 2.5          # abaixo disso a pausa é espera/frame morto
    pausa_sem_analise_max: float = 4.0     # sem medir movimento, só remove pausas curtas
    fps: int = 60

    def validar(self) -> "OpcoesEdicao":
        if self.transition_mode not in ("agency", "hard"):
            raise ValueError("transition_mode deve ser 'agency' ou 'hard'.")
        if self.transition_family != "dissolve":
            raise ValueError("Somente a família 'dissolve' está disponível.")
        if not 0.2 <= self.transition_duration <= 2.0:
            raise ValueError("A duração da transição deve ficar entre 0,2 e 2 s.")
        if not 0.6 <= self.pausa_max <= 10:
            raise ValueError("A pausa máxima deve ficar entre 0,6 e 10 s.")
        if not 1 <= self.palavras_por_bloco <= 12:
            raise ValueError("Palavras por bloco deve ficar entre 1 e 12.")
        if self.fps not in (30, 60):
            raise ValueError("FPS deve ser 30 ou 60.")
        return self


@dataclass
class TrechoTimeline:
    inicio: float
    fim: float
    funcao: str = "historia"
    transicao: str = "inicio"            # transição de ENTRADA: inicio | corte_seco | dissolve
    duracao_transicao: float = 0.0
    motivo_corte: str = ""               # por que existe um corte antes deste trecho
    justificativa_transicao: str = ""

    @property
    def duracao(self) -> float:
        return self.fim - self.inicio


@dataclass
class TimelineAgencia:
    trechos: list[TrechoTimeline]
    tipo: str = "normal"                 # normal | remix
    fps: int = 60
    gancho: tuple[float, float] | None = None
    gancho_repetido: bool = False
    removidos: list[dict] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    @property
    def duracao(self) -> float:
        return sum(t.duracao for t in self.trechos) - sum(t.duracao_transicao for t in self.trechos)

    @property
    def cortes_internos(self) -> int:
        return max(0, len(self.trechos) - 1)

    def offsets(self) -> list[float]:
        """Instante de saída em que cada trecho começa (dissolves sobrepõem)."""
        resultado, atual = [], 0.0
        for indice, trecho in enumerate(self.trechos):
            if indice:
                atual -= trecho.duracao_transicao
            resultado.append(atual)
            atual += trecho.duracao
        return resultado

    def saida_para_fonte(self, t_saida: float) -> float:
        offsets = self.offsets()
        for indice in range(len(self.trechos) - 1, -1, -1):
            if t_saida >= offsets[indice] - 1e-9:
                trecho = self.trechos[indice]
                return min(trecho.fim, trecho.inicio + (t_saida - offsets[indice]))
        return self.trechos[0].inicio

    def fronteiras_saida(self) -> list[float]:
        return self.offsets()[1:]

    def transicoes(self) -> list[dict]:
        return [{"entrada_trecho": i, "tipo": t.transicao, "duracao": round(t.duracao_transicao, 3),
                 "motivo_corte": t.motivo_corte, "justificativa": t.justificativa_transicao}
                for i, t in enumerate(self.trechos) if i]

    def trechos_fonte(self) -> list[TrechoFonte]:
        return [TrechoFonte(t.inicio, t.fim) for t in self.trechos]

    def como_dict(self) -> dict:
        dados = asdict(self)
        dados["duracao"] = self.duracao
        return dados


# ─── Palavras ─────────────────────────────────────────────────────────────────

def palavras_entre(transcricao: Transcricao, inicio: float, fim: float) -> list[PalavraTranscrita]:
    """Palavras cujo centro está no intervalo; segmentos sem palavras viram uma palavra longa."""
    resultado = []
    for segmento in transcricao.segmentos:
        if segmento.fim <= inicio or segmento.inicio >= fim:
            continue
        if segmento.palavras:
            for palavra in segmento.palavras:
                centro = (palavra.inicio + palavra.fim) / 2
                if inicio <= centro < fim:
                    resultado.append(palavra)
        elif segmento.texto.strip():
            resultado.append(PalavraTranscrita(segmento.inicio, segmento.fim, " " + segmento.texto.strip()))
    return resultado


def _dentro_de_palavra(t: float, palavras: list[PalavraTranscrita]) -> PalavraTranscrita | None:
    return next((p for p in palavras if p.inicio + 0.02 < t < p.fim - 0.02), None)


def _intersecta(a0: float, a1: float, b0: float, b1: float, margem: float = 0.0) -> bool:
    return min(a1, b1) - max(a0, b0) > -margem


# ─── Operações sobre listas de intervalos ────────────────────────────────────

def _subtrair(intervalos: list[list[float]], r0: float, r1: float) -> list[list[float]]:
    resultado = []
    for i0, i1 in intervalos:
        if r1 <= i0 or r0 >= i1:
            resultado.append([i0, i1])
            continue
        if r0 > i0:
            resultado.append([i0, r0])
        if r1 < i1:
            resultado.append([r1, i1])
    return [i for i in resultado if i[1] - i[0] > 0.04]


def _protegidos(plano: PlanoAgencia) -> list[tuple[float, float]]:
    zonas = []
    zonas.extend((float(f["inicio"]),float(f["fim"])) for f in plano.falas_protegidas)
    for chave in ("momento_forte", "reacao"):
        item = plano.estrutura.get(chave)
        if item:
            zonas.append((float(item["inicio"]), float(item["fim"])))
    if plano.gancho:
        zonas.append((float(plano.gancho["inicio"]), float(plano.gancho["fim"])))
    return zonas


def _ajustar_gancho(plano: PlanoAgencia, transcricao: Transcricao) -> tuple[float, float, bool]:
    """Alinha o gancho a limites de frase. Retorna (inicio, fim, limites_de_frase)."""
    gi, gf = float(plano.gancho["inicio"]), float(plano.gancho["fim"])
    frases = frases_no_intervalo(transcricao, gi - 1.0, gf + 1.0)
    inicios = [f.inicio for f in frases if abs(f.inicio - gi) <= 1.0]
    fins = [f.fim for f in frases if abs(f.fim - gf) <= 1.2]
    if inicios and fins:
        ni = min(inicios, key=lambda v: abs(v - gi))
        nf = min(fins, key=lambda v: abs(v - gf))
        if 1.5 <= nf - ni <= 6.5:
            return ni, nf, True
    palavras = palavras_entre(transcricao, gi - 1.0, gf + 1.0)
    dentro_i = _dentro_de_palavra(gi, palavras)
    dentro_f = _dentro_de_palavra(gf, palavras)
    return (dentro_i.inicio if dentro_i else gi), (dentro_f.fim if dentro_f else gf), False


# ─── Construção ──────────────────────────────────────────────────────────────

from modules.performance import medir_operacao


@medir_operacao("timeline")
def construir_timeline(plano: PlanoAgencia, transcricao: Transcricao,
                       intro: IntervaloIntro | None = None,
                       opcoes: OpcoesEdicao | None = None,
                       movimento: Callable[[float, float], float] | None = None) -> TimelineAgencia:
    """``movimento(inicio, fim)`` mede ação visual; sem ele, só pausas curtas saem."""
    opcoes = (opcoes or OpcoesEdicao()).validar()
    removidos: list[dict] = []
    avisos: list[str] = []
    intervalos = [[float(s["inicio"]), float(s["fim"])] for s in plano.segmentos]
    intervalos.sort()
    if not intervalos:
        raise ValueError("Plano sem segmentos.")
    # Segmentos da própria história separados por salto grande.
    saltos_plano = [(a[1], b[0]) for a, b in zip(intervalos, intervalos[1:])
                    if b[0] - a[1] > 0.05]
    for a1, b0 in saltos_plano:
        removidos.append({"inicio": a1, "fim": b0,
                          "motivo": "salto_temporal" if b0 - a1 >= opcoes.salto_temporal_min
                          else "trecho_irrelevante"})

    # 1) Trechos irrelevantes indicados pelo plano — reduzidos até limites sem fala.
    for item in plano.remover:
        r0 = ajustar_fim_sem_cortar_fala(float(item["inicio"]), transcricao)
        r1 = ajustar_inicio_sem_cortar_fala(float(item["fim"]), transcricao)
        if r1 - r0 > 0.2:
            intervalos = _subtrair(intervalos, r0, r1)
            removidos.append({"inicio": r0, "fim": r1, "motivo": "trecho_irrelevante"})

    # 2) Intro nunca aparece.
    if intro and intro.ativo:
        antes = sum(i[1] - i[0] for i in intervalos)
        intervalos = _subtrair(intervalos, intro.inicio, intro.fim)
        if sum(i[1] - i[0] for i in intervalos) < antes - 0.01:
            removidos.append({"inicio": intro.inicio, "fim": intro.fim, "motivo": "intro_removida"})
    if not intervalos:
        raise ValueError("A história está inteiramente dentro da intro.")

    protegidos = _protegidos(plano)

    # 3) Silêncio inicial (exceto gancho visual) e silêncio final excessivo.
    primeira = palavras_entre(transcricao, intervalos[0][0], intervalos[0][1])
    if primeira and not plano.gancho_visual:
        novo_inicio = max(intervalos[0][0], primeira[0].inicio - opcoes.preroll)
        if novo_inicio - intervalos[0][0] > 0.05:
            removidos.append({"inicio": intervalos[0][0], "fim": novo_inicio, "motivo": "silencio_inicial"})
            intervalos[0][0] = novo_inicio
    ultima = palavras_entre(transcricao, intervalos[-1][0], intervalos[-1][1])
    if ultima:
        fim_fala = ultima[-1].fim
        fim_estrutura = max([float(plano.estrutura[c]["fim"]) for c in ("reacao", "conclusao")
                             if c in plano.estrutura] or [0.0])
        limite = max(fim_fala + opcoes.cauda_final, min(fim_estrutura, intervalos[-1][1]))
        if intervalos[-1][1] - fim_fala > opcoes.silencio_final_max and intervalos[-1][1] > limite + 0.05:
            removidos.append({"inicio": limite, "fim": intervalos[-1][1], "motivo": "silencio_final"})
            intervalos[-1][1] = limite

    # 4) Pausas mortas internas (respeitando momento forte, reação e gancho).
    if opcoes.compactar_pausas:
        for i0, i1 in list(intervalos):
            palavras = palavras_entre(transcricao, i0, i1)
            for anterior, seguinte in zip(palavras, palavras[1:]):
                pausa = seguinte.inicio - anterior.fim
                if pausa <= opcoes.pausa_max:
                    continue
                r0 = anterior.fim + opcoes.respiro_depois
                r1 = seguinte.inicio - opcoes.respiro_antes
                if r1 - r0 < 0.5 or any(_intersecta(r0, r1, p0, p1) for p0, p1 in protegidos):
                    continue
                # Silêncio não é sinônimo de frame morto: em animação costuma haver
                # ação/piada visual sem fala. Só remove espera sem movimento.
                if movimento is not None:
                    if movimento(r0, r1) >= opcoes.limiar_movimento:
                        continue
                elif r1 - r0 > opcoes.pausa_sem_analise_max:
                    continue
                intervalos = _subtrair(intervalos, r0, r1)
                removidos.append({"inicio": r0, "fim": r1, "motivo": "pausa_morta"})

    # 5) Remix: gancho primeiro; a história reencontra o gancho e o pula (sem repetição).
    tipo, gancho, repetido = "normal", None, False
    if plano.usar_remix and plano.gancho and opcoes.permitir_remix:
        gi, gf, limites_frase = _ajustar_gancho(plano, transcricao)
        dentro = any(i0 - 0.05 <= gi and gf <= i1 + 0.05 for i0, i1 in intervalos)
        if not dentro:
            avisos.append("Remix recusado: o gancho não está dentro dos trechos mantidos.")
        elif gi - intervalos[0][0] < 3.0:
            avisos.append("Remix recusado: o gancho já está no início cronológico.")
        else:
            tipo, gancho = "remix", (gi, gf)
            if plano.repetir_gancho or not opcoes.gancho_sem_repeticao:
                avisos.append("Repeticao do gancho normalizada pelo contrato universal.")
            intervalos = _subtrair(intervalos, gi, gf)
            removidos.append({"inicio": gi, "fim": gf, "motivo": "retorno_gancho"})
    elif plano.usar_remix and not opcoes.permitir_remix:
        avisos.append("Remix desativado nas opções; ordem cronológica usada.")

    # 6) Trechos, com durações quantizadas em frames.
    trechos: list[TrechoTimeline] = []
    if gancho:
        trechos.append(TrechoTimeline(gancho[0], gancho[1], "gancho"))
    for i0, i1 in intervalos:
        funcao = next((s.get("funcao", "historia") for s in plano.segmentos
                       if float(s["inicio"]) - 0.05 <= i0 < float(s["fim"])), "historia")
        tem_fala = bool(palavras_entre(transcricao, i0, i1))
        if i1 - i0 < 0.35 and not tem_fala:
            continue
        trechos.append(TrechoTimeline(i0, i1, funcao))
    for trecho in trechos:
        frames = max(1, round(trecho.duracao * opcoes.fps))
        trecho.fim = trecho.inicio + frames / opcoes.fps

    # 7) Motivo de cada corte e escolha da transição.
    for indice in range(1, len(trechos)):
        anterior, atual = trechos[indice - 1], trechos[indice]
        if gancho and indice == 1:
            motivo = "remix_gancho"
        elif gancho and not repetido and anterior.fim <= gancho[0] + 0.1 and atual.inicio >= gancho[1] - 0.1                 and anterior.funcao != "gancho" and _intersecta(anterior.fim, atual.inicio, gancho[0], gancho[1]):
            motivo = "retorno_gancho"
        else:
            motivo = next((r["motivo"] for r in removidos
                           if _intersecta(anterior.fim, atual.inicio, r["inicio"], r["fim"], 0.05)
                           and r["motivo"] not in ("silencio_inicial", "silencio_final")),
                          "salto_temporal" if atual.inicio - anterior.fim >= opcoes.salto_temporal_min
                          else "trecho_irrelevante")
        atual.motivo_corte = motivo
        _escolher_transicao(anterior, atual, transcricao, opcoes)
    return TimelineAgencia(trechos, tipo, opcoes.fps, gancho, repetido, removidos, avisos)


_MOTIVOS_DISSOLVE = {"remix_gancho", "salto_temporal", "intro_removida"}


def _escolher_transicao(anterior: TrechoTimeline, atual: TrechoTimeline,
                        transcricao: Transcricao, opcoes: OpcoesEdicao) -> None:
    atual.transicao, atual.duracao_transicao = "corte_seco", 0.0
    if opcoes.transition_mode != "agency" or atual.motivo_corte not in _MOTIVOS_DISSOLVE:
        atual.justificativa_transicao = {
            "retorno_gancho": "continuidade da mesma cena após o gancho",
            "pausa_morta": "remoção de espera mantém o ritmo",
            "trecho_irrelevante": "remoção de trecho sem valor mantém o ritmo",
        }.get(atual.motivo_corte, "hard cut padrão") if opcoes.transition_mode == "agency" else "modo somente hard cut"
        return
    fala_a = palavras_entre(transcricao, anterior.inicio, anterior.fim)
    fala_b = palavras_entre(transcricao, atual.inicio, atual.fim)
    silencio_cauda = anterior.fim - fala_a[-1].fim if fala_a else anterior.duracao
    silencio_cabeca = fala_b[0].inicio - atual.inicio if fala_b else atual.duracao
    duracao = min(opcoes.transition_duration, silencio_cauda, silencio_cabeca,
                  0.4 * anterior.duracao, 0.4 * atual.duracao)
    duracao = int(duracao * opcoes.fps) / opcoes.fps
    if duracao >= opcoes.transicao_minima:
        atual.transicao, atual.duracao_transicao = "dissolve", round(duracao, 4)
        atual.justificativa_transicao = (f"{atual.motivo_corte}: dissolve de {duracao:.2f}s "
                                         "sobre trecho sem fala")
    else:
        atual.justificativa_transicao = (f"{atual.motivo_corte}: fala próxima do corte; "
                                         "hard cut preserva diálogo/punchline")


# ─── Legendas ─────────────────────────────────────────────────────────────────

PAUSA_QUEBRA_LEGENDA = 0.7

@medir_operacao("legendas")
def gerar_legendas_timeline(transcricao: Transcricao, timeline: TimelineAgencia,
                            palavras_por_bloco: int = 4) -> list[BlocoLegenda]:
    """Legendas geradas a partir das palavras de cada trecho, já no tempo de saída.

    Montar os blocos por trecho evita exibir palavras que foram cortadas e mantém a
    sincronização após remix, compactação e dissolves.
    """
    if not 1 <= palavras_por_bloco <= 12:
        raise ValueError("Palavras por bloco deve ficar entre 1 e 12.")
    resultado: list[BlocoLegenda] = []
    for trecho, offset in zip(timeline.trechos, timeline.offsets()):
        palavras = palavras_entre(transcricao, trecho.inicio, trecho.fim)
        grupo: list[PalavraTranscrita] = []

        def fechar():
            if not grupo:
                return
            inicio = max(trecho.inicio, grupo[0].inicio) - trecho.inicio + offset
            fim = min(trecho.fim, grupo[-1].fim) - trecho.inicio + offset
            texto = "".join(p.texto for p in grupo).strip()
            if fim > inicio and texto:
                resultado.append(BlocoLegenda(round(inicio, 3), round(fim, 3), texto))
            grupo.clear()

        for palavra in palavras:
            # Nunca une falas separadas por pausa: o bloco ficaria na tela durante
            # o silêncio (ou durante uma ação visual sem fala).
            if grupo and palavra.inicio - grupo[-1].fim > PAUSA_QUEBRA_LEGENDA:
                fechar()
            grupo.append(palavra)
            if len(grupo) >= palavras_por_bloco or palavra.texto.rstrip().endswith((".", "!", "?", ":", ";", "…")):
                fechar()
        fechar()
    resultado.sort(key=lambda b: (b.inicio, b.fim))
    # Dissolves sobrepõem trechos: nenhuma legenda pode encobrir a seguinte.
    for atual, seguinte in zip(resultado, resultado[1:]):
        if atual.fim > seguinte.inicio:
            atual.fim = seguinte.inicio
    duracao = timeline.duracao
    finais = []
    for bloco in resultado:
        bloco.fim = min(bloco.fim, duracao)
        if bloco.fim - bloco.inicio >= 0.08:
            finais.append(bloco)
    return finais
