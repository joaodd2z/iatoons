"""Plano editorial do Modo Agência: a unidade de edição é uma HISTÓRIA/SITUAÇÃO.

Fontes possíveis de um plano:
- ``gemini_agencia``: análise própria do Modo Agência (cache por episódio);
- ``analise_ia``/``remix``: conversão local, sem nova chamada, dos candidatos que o
  aplicativo já possui para o mesmo episódio.

A troca de perfil visual nunca participa das chaves de cache.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Callable
import unicodedata
import uuid

from pydantic import BaseModel, Field

from modules.analise_ia import ErroGemini, _erro_seguro, _janelas
from modules.ai.cache_fallback import carregar_grupos, salvar_grupos
from modules.ai.base import AIProvider
from modules.ai.factory import obter_provider
from modules.ai.chat_json import selecionar_modelo_disponivel
from modules.ai.gemini import GeminiAgencyProvider
from modules.config_ia import ConfigIA, carregar_config_ia
from modules.credenciais import obter_chave_gemini
from modules.ganchos_ab import _pontuar
from modules.intro import carregar_intro
from modules.preset import DADOS_DIR
from modules.remix_narrativo import CandidatoRemix
from modules.transcricao import SegmentoTranscricao, Transcricao, transcricao_corresponde_origem


AGENCIA_DIR = DADOS_DIR / "agencia"
ANALISES_AGENCIA_DIR = AGENCIA_DIR / "analises"
VERSAO_PROMPT_AGENCIA = "agencia-remix-v5"
ESTRUTURA_CHAVES = ("preparacao", "conflito", "escalada", "momento_forte", "reacao", "conclusao")
TIPOS_TITULO = ("curiosidade", "conflito", "surpresa")
DURACAO_HISTORIA_MIN = 20.0   # o vídeo final (validar_remix_real) precisa passar de 20 s
DURACAO_IDEAL = 45.0          # cortes longos e completos têm preferência na seleção
DURACAO_HISTORIA_MAX = 150.0
SALTO_MAXIMO_HISTORIA = 45.0
MIN_HISTORIAS = 5
MAX_HISTORIAS = 10
CONTEXTO_MINIMO_PLANO = 6.0   # o gancho precisa estar ao menos 6 s depois do início da história


# ─── Modelo ───────────────────────────────────────────────────────────────────

@dataclass
class TituloOpcao:
    texto: str
    tipo: str = "curiosidade"     # A. curiosidade · B. conflito · C. surpresa
    score_titulo: int | None = None
    motivos: list[str] = field(default_factory=list)
    alertas: list[str] = field(default_factory=list)
    similaridade_historico: float = 0.0


@dataclass
class PlanoAgencia:
    inicio: float
    fim: float
    titulos: list[TituloOpcao] = field(default_factory=list)
    descricao: str = ""
    motivo_escolha: str = ""
    titulo_escolhido: str = ""
    titulo_capa: str = ""
    estrutura: dict[str, dict] = field(default_factory=dict)
    segmentos: list[dict] = field(default_factory=list)
    remover: list[dict] = field(default_factory=list)
    gancho: dict | None = None
    gancho_visual: bool = False
    usar_remix: bool = False
    remix_motivo: str = ""
    repetir_gancho: bool = False
    confianca: float = 0.0
    origem: str = "gemini_agencia"
    video_id: str = ""
    avisos: list[str] = field(default_factory=list)
    # Estado de edição/exportação
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    selecionado: bool = True
    status: str = "pendente"
    erro: str = ""
    caminho_saida: str = ""
    caminho_capa: str = ""
    capa_tempos: list[float] = field(default_factory=list)
    capa_indice: int = 0
    centro_x: float | None = None
    centro_y: float | None = None
    gate: list[dict] = field(default_factory=list)
    remix_rules_version: str = ""
    validacao_remix: dict = field(default_factory=dict)
    titulos_v2: dict = field(default_factory=dict)
    titulo_origem: str = "automatico"
    titulo_auto_aprovado: bool = False
    titulo_aprovado_manualmente: bool = False
    titulo_manual_confirmado: str = ""
    titulo_confirmacao_pendente: bool = False
    estado_editorial: str = "automatico"
    revisao_humana: dict = field(default_factory=dict)
    corte_aprovado_manualmente: bool = False
    corte_confirmado_hash: str = ""
    falas_protegidas: list[dict] = field(default_factory=list)
    story_identity: str = ""
    story_parent_id: str = ""
    story_signature: dict = field(default_factory=dict)

    @property
    def duracao_historia(self) -> float:
        return sum(float(s["fim"]) - float(s["inicio"]) for s in self.segmentos)

    @property
    def titulo(self) -> str:
        if self.titulo_escolhido.strip():
            return self.titulo_escolhido.strip()
        return self.titulos[0].texto if self.titulos else "SEM TÍTULO"

    def como_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def de_dict(cls, dados: dict) -> "PlanoAgencia":
        permitidos = cls.__dataclass_fields__
        valores = {k: v for k, v in dados.items() if k in permitidos}
        valores["titulos"] = [
            t if isinstance(t, TituloOpcao) else TituloOpcao(**{
                k: v for k, v in dict(t).items() if k in TituloOpcao.__dataclass_fields__})
            for t in valores.get("titulos", [])
        ]
        return cls(**valores)


# ─── Títulos ──────────────────────────────────────────────────────────────────

def _sem_acentos(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")


def _tokens(texto: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", _sem_acentos(texto.casefold())) if len(t) > 2}


def similaridade(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


_MARCAS_TIPO = {
    "curiosidade": r"\?|\b(por que|porque|o que|como|quem|segredo|descobr|nunca|ninguem sabe|misterio|escond)",
    "conflito": r"\b(contra|briga|discut|odeia|brig|vs|x|enfrent|nao aceita|expuls|traiu|raiva|guerra|irrit)",
    "surpresa": r"!|\b(de repente|ninguem esperava|inesperad|surpresa|chocou|virou|acabou|do nada|susto)",
}


def classificar_titulo(texto: str) -> str:
    alvo = _sem_acentos(texto.casefold())
    pontos = {tipo: len(re.findall(padrao, alvo)) for tipo, padrao in _MARCAS_TIPO.items()}
    melhor = max(pontos, key=lambda tipo: (pontos[tipo], -TIPOS_TITULO.index(tipo)))
    return melhor if pontos[melhor] > 0 else "curiosidade"


def _normalizar_tipo(tipo: str) -> str | None:
    t = _sem_acentos(str(tipo or "").casefold()).strip()
    if t in ("a", "curiosidade", "curiosity"):
        return "curiosidade"
    if t in ("b", "conflito", "conflict"):
        return "conflito"
    if t in ("c", "surpresa", "surprise"):
        return "surpresa"
    return None


def avaliar_titulo(titulo: str, primeira_fala: str = "") -> list[str]:
    """Problemas objetivos de um título de hook (lista vazia = aprovado)."""
    problemas = []
    limpo = " ".join(str(titulo).split())
    if not limpo:
        return ["título vazio"]
    if len(limpo) > 64 or len(limpo.split()) > 11:
        problemas.append("título longo demais para ser lido de imediato")
    if primeira_fala and similaridade(limpo, primeira_fala) >= 0.6:
        problemas.append("título apenas repete a primeira fala")
    return problemas


def normalizar_titulos(brutos: list, primeira_fala: str = "") -> tuple[list[TituloOpcao], list[str]]:
    """Limpa, remove duplicados e garante tipos distintos A/B/C quando possível."""
    titulos: list[TituloOpcao] = []
    avisos: list[str] = []
    for item in brutos or []:
        if isinstance(item, TituloOpcao):
            texto, tipo = item.texto, item.tipo
        elif isinstance(item, dict):
            texto, tipo = item.get("texto", ""), item.get("tipo", "")
        else:
            texto, tipo = str(item), ""
        limpo = " ".join(str(texto).split()).strip(" .").upper()
        if not limpo or any(limpo.casefold() == t.texto.casefold() for t in titulos):
            continue
        problemas = avaliar_titulo(limpo, primeira_fala)
        if problemas:
            avisos.append(f"Título descartado ({'; '.join(problemas)}): {limpo}")
            continue
        titulos.append(TituloOpcao(limpo, _normalizar_tipo(tipo) or classificar_titulo(limpo)))
    # Tipos repetidos são redistribuídos para manter A/B/C distintos.
    usados: set[str] = set()
    for titulo in titulos:
        if titulo.tipo in usados:
            livres = [t for t in TIPOS_TITULO if t not in usados]
            if livres:
                titulo.tipo = livres[0]
        usados.add(titulo.tipo)
    titulos.sort(key=lambda t: TIPOS_TITULO.index(t.tipo) if t.tipo in TIPOS_TITULO else 9)
    return titulos[:3], avisos


def titulo_capa_de(titulo: str, max_palavras: int = 5) -> str:
    palavras = " ".join(str(titulo).split()).upper().split()
    if len(palavras) <= max_palavras:
        return " ".join(palavras)
    return " ".join(palavras[:max_palavras]).rstrip(",;:") + "…"


# ─── Falas e limites seguros ─────────────────────────────────────────────────

@dataclass(frozen=True)
class Frase:
    inicio: float
    fim: float
    texto: str


def frases_no_intervalo(transcricao: Transcricao, inicio: float, fim: float) -> list[Frase]:
    """Unidades de fala completas (frases pontuadas) a partir dos timestamps de palavras."""
    frases: list[Frase] = []
    for segmento in transcricao.segmentos:
        if segmento.fim <= inicio or segmento.inicio >= fim:
            continue
        if not segmento.palavras:
            frases.append(Frase(segmento.inicio, segmento.fim, segmento.texto.strip()))
            continue
        grupo = []
        for palavra in segmento.palavras:
            grupo.append(palavra)
            if palavra.texto.rstrip().endswith((".", "!", "?", "…")):
                frases.append(Frase(grupo[0].inicio, grupo[-1].fim,
                                    "".join(p.texto for p in grupo).strip()))
                grupo = []
        if grupo:
            frases.append(Frase(grupo[0].inicio, grupo[-1].fim,
                                "".join(p.texto for p in grupo).strip()))
    return [f for f in frases if f.fim > inicio and f.inicio < fim]


def ajustar_inicio_sem_cortar_fala(t: float, transcricao: Transcricao) -> float:
    for segmento in transcricao.segmentos:
        if segmento.inicio < t < segmento.fim:
            return segmento.inicio
    return t


def ajustar_fim_sem_cortar_fala(t: float, transcricao: Transcricao) -> float:
    for segmento in transcricao.segmentos:
        if segmento.inicio < t < segmento.fim:
            return segmento.fim
    return t


# ─── Remixes salvos (sem Gemini) ─────────────────────────────────────────────
# O Modo Agência aceita SOMENTE remixes. Cortes normais/cronológicos, análises
# contextuais comuns e sugestões estruturais nunca entram, nem para completar cota.

def _primeira_fala(transcricao: Transcricao, inicio: float) -> str:
    frases = frases_no_intervalo(transcricao, inicio, inicio + 6.0)
    return frases[0].texto if frases else ""


def plano_de_remix(candidato: CandidatoRemix, transcricao: Transcricao) -> PlanoAgencia:
    if getattr(candidato,'plano_agencia',None):
        return PlanoAgencia.de_dict(candidato.plano_agencia)
    segmentos = [{"inicio": s.inicio, "fim": s.fim, "funcao": s.funcao} for s in candidato.segmentos]
    titulos, avisos = normalizar_titulos(candidato.titulos, _primeira_fala(transcricao, candidato.inicio))
    plano = PlanoAgencia(
        inicio=candidato.inicio, fim=candidato.fim, titulos=titulos,
        descricao=candidato.descricao, motivo_escolha=candidato.justificativa,
        segmentos=segmentos,
        gancho={"inicio": candidato.gancho_inicio, "fim": candidato.gancho_fim,
                "motivo": candidato.gancho_motivo},
        estrutura={"momento_forte": {"inicio": candidato.gancho_inicio, "fim": candidato.gancho_fim}},
        usar_remix=True, remix_motivo=candidato.continuidade,
        confianca=candidato.confianca, origem="remix", video_id=transcricao.video_id, avisos=avisos,
    )
    plano.titulo_capa = titulo_capa_de(plano.titulo)
    return plano


def planos_de_remixes_salvos(transcricao: Transcricao,
                             remixes=()) -> tuple[list[PlanoAgencia], list[str]]:
    """Converte remixes já validados pelo Remix Narrativo e REVALIDA a timeline real."""
    from modules.agencia.remix import verificar_plano_remix

    intro = carregar_intro(transcricao)
    aceitos, rejeicoes = [], []
    for remix in remixes:
        plano = plano_de_remix(remix, transcricao)
        problemas = verificar_plano_remix(plano, transcricao, intro)
        if problemas:
            rejeicoes.append(f"Remix salvo {plano.inicio:.1f}s: {problemas[0]}.")
        else:
            aceitos.append(plano)
    unicos, duplicados = remover_duplicados(aceitos)
    return unicos, rejeicoes + duplicados


# ─── Validação de respostas ──────────────────────────────────────────────────

def cobertura(plano: PlanoAgencia) -> list[tuple[float, float]]:
    return [(float(s["inicio"]), float(s["fim"])) for s in plano.segmentos]


def sobreposicao_relativa(a: PlanoAgencia, b: PlanoAgencia) -> float:
    comum = 0.0
    for ai, af in cobertura(a):
        for bi, bf in cobertura(b):
            comum += max(0.0, min(af, bf) - max(ai, bi))
    menor = min(a.duracao_historia, b.duracao_historia)
    return comum / menor if menor > 0 else 0.0


def remover_duplicados(planos: list[PlanoAgencia], limite: float = 0.6) -> tuple[list[PlanoAgencia], list[str]]:
    aceitos: list[PlanoAgencia] = []
    rejeicoes = []
    for plano in sorted(planos, key=lambda p: -p.confianca):
        duplicado = next((a for a in aceitos if sobreposicao_relativa(plano, a) > limite), None)
        if duplicado:
            rejeicoes.append(f"História {plano.inicio:.1f}s duplicada de {duplicado.inicio:.1f}s.")
            continue
        aceitos.append(plano)
    return sorted(aceitos, key=lambda p: p.inicio), rejeicoes


def _intervalo(item) -> tuple[float, float] | None:
    if not isinstance(item, dict):
        return None
    try:
        inicio, fim = float(item["inicio"]), float(item["fim"])
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(inicio + fim) or fim <= inicio:
        return None
    return inicio, fim


def _melhor_bloco_continuo(segmentos: list[dict], item: dict) -> list[dict]:
    """Divide em blocos separados por saltos grandes e escolhe o mais relevante."""
    blocos, atual = [], [segmentos[0]]
    for anterior, seguinte in zip(segmentos, segmentos[1:]):
        if seguinte["inicio"] - anterior["fim"] > SALTO_MAXIMO_HISTORIA:
            blocos.append(atual)
            atual = []
        atual.append(seguinte)
    blocos.append(atual)
    destaques = [_intervalo(item.get("gancho"))]
    destaques += [_intervalo((item.get("estrutura") or {}).get(c)) for c in ("momento_forte", "escalada")]
    destaques = [d for d in destaques if d]

    gancho = _intervalo(item.get("gancho"))

    def relevancia(bloco):
        inicio, fim = bloco[0]["inicio"], bloco[-1]["fim"]
        # O bloco precisa conter o gancho e contexto antes dele; senão não há remix.
        # O bloco precisa conter o gancho E contexto antes dele; senão não há remix.
        tem_gancho = bool(gancho and inicio + CONTEXTO_MINIMO_PLANO <= gancho[0] < fim)
        contem = sum(1 for d in destaques if min(fim, d[1]) - max(inicio, d[0]) > 0.5)
        duracao = sum(s["fim"] - s["inicio"] for s in bloco)
        cabe = DURACAO_HISTORIA_MIN <= duracao <= DURACAO_HISTORIA_MAX
        return (tem_gancho, cabe, contem, duracao)

    return max(blocos, key=relevancia)


def _ampliar_gancho_curto(transcricao: Transcricao, inicio: float, fim: float) -> tuple[float, float] | None:
    """Gancho < 1,5 s: inclui frases imediatamente anteriores (mesma fala) até 2–6 s."""
    frases = frases_no_intervalo(transcricao, inicio - 8.0, fim)
    # Primeiro recupera a propria frase completa, antes de anexar fala anterior.
    contendo = next((f for f in frases if f.inicio <= inicio < f.fim), None)
    if contendo and 1.5 <= contendo.fim - contendo.inicio <= 6.5:
        return contendo.inicio, contendo.fim
    anteriores = [f for f in frases if f.fim <= inicio + 0.3]
    novo = inicio
    for frase in reversed(anteriores):
        if novo - frase.fim > 1.0 or fim - frase.inicio > 6.0:
            break
        novo = frase.inicio
        if fim - novo >= 2.0:
            return novo, fim
    return None


def _recortar_gancho(transcricao: Transcricao, inicio: float, fim: float) -> tuple[float, float] | None:
    """Reduz um gancho longo à frase (ou par de frases) de 2–6 s mais intensa."""
    frases = frases_no_intervalo(transcricao, inicio, fim)
    melhor, melhor_pontos = None, -1
    for i, primeira in enumerate(frases):
        texto = ""
        for ultima in frases[i:i + 3]:
            texto = f"{texto} {ultima.texto}".strip()
            duracao = ultima.fim - primeira.inicio
            if duracao > 6.0:
                break
            if duracao >= 2.0 and _pontuar(texto) > melhor_pontos:
                melhor, melhor_pontos = (primeira.inicio, ultima.fim), _pontuar(texto)
    return melhor


from modules.performance import medir_operacao, anotar, medir_chamada


@medir_operacao("validacao_editorial")
def validar_planos(dados: list[dict], transcricao: Transcricao,
                   max_planos: int = 10, *, exigir_titulos: bool = True) -> tuple[list[PlanoAgencia], list[str]]:
    """Validação local: timestamps reais, falas inteiras, continuidade e títulos."""
    intro = carregar_intro(transcricao)
    planos: list[PlanoAgencia] = []
    rejeicoes: list[str] = []
    for indice, item in enumerate(dados or [], 1):
        try:
            brutos = item.get("segmentos") or []
            limites = [_intervalo(s) for s in brutos]
            if limites and all(limites) and max(s[1] for s in limites) - min(s[0] for s in limites) > 180:
                raise ValueError("historia gigante (>180s): requer subdivisao local antes do validator")
            segmentos = []
            for bruto in brutos:
                intervalo = _intervalo(bruto)
                if intervalo is None:
                    raise ValueError("segmento com timestamps inválidos")
                inicio, fim = intervalo
                if inicio < -0.5 or fim > transcricao.duracao + 0.5:
                    raise ValueError("segmento fora do episódio")
                inicio = ajustar_inicio_sem_cortar_fala(max(0.0, inicio), transcricao)
                fim = ajustar_fim_sem_cortar_fala(min(transcricao.duracao, fim), transcricao)
                segmentos.append({"inicio": inicio, "fim": fim,
                                  "funcao": str(bruto.get("funcao", "desenvolvimento")) or "desenvolvimento"})
            if not segmentos:
                raise ValueError("história sem segmentos")
            segmentos.sort(key=lambda s: s["inicio"])
            unidos = [segmentos[0]]
            for atual in segmentos[1:]:
                if atual["inicio"] <= unidos[-1]["fim"] + 0.05:
                    unidos[-1]["fim"] = max(unidos[-1]["fim"], atual["fim"])
                else:
                    unidos.append(atual)
            avisos_estrutura: list[str] = []
            if any(b["inicio"] - a["fim"] > SALTO_MAXIMO_HISTORIA for a, b in zip(unidos, unidos[1:])):
                # Resposta que junta cenas distantes (ex.: resumo do episódio): mantém
                # somente o bloco contínuo mais relevante; nunca une situações distantes.
                unidos = _melhor_bloco_continuo(unidos, item)
                avisos_estrutura.append(
                    f"Resposta unia cenas distantes; mantido só o trecho contínuo "
                    f"{unidos[0]['inicio']:.1f}–{unidos[-1]['fim']:.1f}s.")
            # Sem "completar" história com falas vizinhas: isso gerava remixes sem sentido.
            # A história precisa vir completa da análise; senão é rejeitada.
            duracao = sum(s["fim"] - s["inicio"] for s in unidos)
            if not DURACAO_HISTORIA_MIN <= duracao <= DURACAO_HISTORIA_MAX:
                raise ValueError(f"duração da história {duracao:.1f}s fora de "
                                 f"{DURACAO_HISTORIA_MIN:.0f}–{DURACAO_HISTORIA_MAX:.0f}s")
            inicio, fim = unidos[0]["inicio"], unidos[-1]["fim"]
            if intro and intro.ativo and intro.inicio <= inicio and fim <= intro.fim:
                raise ValueError("história inteiramente dentro da intro")
            confianca = float(item.get("confianca", 0.0) or 0.0)
            if not 0 <= confianca <= 1:
                raise ValueError("confiança inválida")
            if confianca < 0.3:
                raise ValueError("confiança insuficiente")
            estrutura = {}
            for chave in ESTRUTURA_CHAVES:
                intervalo = _intervalo((item.get("estrutura") or {}).get(chave))
                if intervalo and intervalo[1] > inicio and intervalo[0] < fim:
                    estrutura[chave] = {"inicio": max(inicio, intervalo[0]), "fim": min(fim, intervalo[1])}
            remover = []
            for bruto in item.get("remover") or []:
                intervalo = _intervalo(bruto)
                if intervalo and intervalo[1] > inicio and intervalo[0] < fim:
                    remover.append({"inicio": intervalo[0], "fim": intervalo[1],
                                    "motivo": str(bruto.get("motivo", ""))})
            ordem = [str(o).casefold() for o in (item.get("ordem_final") or [])]
            if item.get("usar_remix") is False or (ordem and ordem[0] != "gancho"):
                raise ValueError("a própria IA indicou ordem cronológica: não é remix")
            primeira = _primeira_fala(transcricao, inicio)
            brutos_titulos = list(item.get("titulos") or [])
            if item.get("titulo"):
                brutos_titulos.insert(0, {"texto": item["titulo"], "tipo": ""})
            titulos, avisos = normalizar_titulos(brutos_titulos, primeira)
            avisos = avisos_estrutura + avisos
            if not titulos and exigir_titulos:
                raise ValueError("nenhum título válido")
            if len(titulos) < 3:
                avisos.append(f"Somente {len(titulos)} título(s) válido(s) de 3.")
            plano = PlanoAgencia(
                inicio=inicio, fim=fim, titulos=titulos,
                descricao=str(item.get("descricao", "")).strip(),
                motivo_escolha=str(item.get("motivo_escolha", "")).strip(),
                titulo_capa=" ".join(str(item.get("titulo_capa", "")).split()).upper(),
                estrutura=estrutura, segmentos=unidos, remover=remover,
                gancho_visual=bool(item.get("gancho_visual", False)),
                confianca=confianca, origem="gemini_agencia",
                video_id=transcricao.video_id, avisos=avisos,
                falas_protegidas=list(item.get("falas_protegidas") or []),
            )
            if not plano.titulo_capa or len(plano.titulo_capa.split()) > 6:
                plano.titulo_capa = titulo_capa_de(plano.titulo)
            plano.remix_motivo = " ".join(str(item.get("justificativa_remix") or
                                              item.get("remix_motivo") or "").split())
            # Reparos seguros do gancho; se não houver gancho válido, NÃO é remix.
            gancho = _intervalo(item.get("gancho"))
            if gancho is None:
                raise ValueError("sem gancho: não é remix")
            if gancho[1] - gancho[0] > 6.5:
                recortado = _recortar_gancho(transcricao, *gancho)
                if recortado is None:
                    raise ValueError(f"gancho de {gancho[1] - gancho[0]:.0f}s sem frase de 2–6 s aproveitável")
                plano.avisos.append(f"Gancho de {gancho[1] - gancho[0]:.0f}s reduzido para "
                                    f"{recortado[1] - recortado[0]:.1f}s (frase mais forte).")
                gancho = recortado
            if gancho[1] - gancho[0] < 1.5:
                ampliado = _ampliar_gancho_curto(transcricao, *gancho)
                if ampliado:
                    plano.avisos.append(f"Gancho de {gancho[1] - gancho[0]:.1f}s ampliado para "
                                        f"{ampliado[1] - ampliado[0]:.1f}s com a frase anterior.")
                    gancho = ampliado
            gi, gf = gancho
            if not 1.5 <= gf - gi <= 6.5:
                raise ValueError("gancho deve durar entre 2 e 6 segundos")
            if not any(s["inicio"] - 0.05 <= gi and gf <= s["fim"] + 0.05 for s in unidos):
                raise ValueError("gancho fora dos segmentos da própria história")
            if gi - inicio < CONTEXTO_MINIMO_PLANO:
                raise ValueError("gancho não é posterior ao contexto: não há o que reorganizar")
            plano.gancho = {"inicio": gi, "fim": gf,
                            "motivo": str((item.get("gancho") or {}).get("motivo", "")).strip()}
            plano.usar_remix = True
            plano.estrutura.setdefault("momento_forte", {"inicio": gi, "fim": gf})
            # Prova final: a timeline montada precisa ser um remix real.
            from modules.agencia.remix import verificar_plano_remix
            problemas = verificar_plano_remix(plano, transcricao, intro)
            if problemas:
                raise ValueError(f"remix inválido na timeline real: {problemas[0]}")
            planos.append(plano)
        except (AttributeError, KeyError, TypeError, ValueError) as erro:
            rejeicoes.append(f"História {indice}: {erro}.")
    aceitos, duplicados = remover_duplicados(planos)
    return aceitos[:max_planos], rejeicoes + duplicados


# ─── Gemini ───────────────────────────────────────────────────────────────────

class _Intervalo(BaseModel):
    inicio: float
    fim: float


class _IntervaloMotivo(BaseModel):
    inicio: float
    fim: float
    motivo: str = ""


class _SegmentoAgencia(BaseModel):
    inicio: float
    fim: float
    funcao: str = "desenvolvimento"


class _TituloAgencia(BaseModel):
    texto: str
    tipo: str


class _EstruturaAgencia(BaseModel):
    preparacao: _Intervalo | None = None
    conflito: _Intervalo | None = None
    escalada: _Intervalo | None = None
    momento_forte: _Intervalo | None = None
    reacao: _Intervalo | None = None
    conclusao: _Intervalo | None = None


class _HistoriaAgencia(BaseModel):
    candidate_id: str = ""
    titulo: str = ""
    descricao: str = ""
    motivo_escolha: str = ""
    ordem_final: list[str] = Field(default_factory=list)
    justificativa_remix: str = ""
    estrutura: _EstruturaAgencia = Field(default_factory=_EstruturaAgencia)
    segmentos: list[_SegmentoAgencia] = Field(default_factory=list)
    remover: list[_IntervaloMotivo] = Field(default_factory=list)
    gancho: _IntervaloMotivo | None = None
    gancho_visual: bool = False
    usar_remix: bool = False
    remix_motivo: str = ""
    titulos: list[_TituloAgencia] = Field(default_factory=list)
    titulo_capa: str = ""
    confianca: float = 0.0


class _RespostaAgencia(BaseModel):
    historias: list[_HistoriaAgencia] = Field(default_factory=list)


class ListaPlanos(list):
    """Lista de planos que também carrega os motivos de rejeição."""

    def __init__(self, itens=(), rejeicoes=(), brutas: int = 0, diagnostico=None):
        super().__init__(itens)
        self.rejeicoes = list(rejeicoes)
        self.brutas = brutas
        self.diagnostico = diagnostico

    @property
    def mensagem(self) -> str:
        if self.diagnostico:
            return self.diagnostico.resumo()
        return mensagem_meta(len(self))


ProvedorAgencia = AIProvider


class ProvedorGeminiAgencia(GeminiAgencyProvider):
    """Construtor legado com o schema editorial original."""
    def __init__(self, chave: str):
        super().__init__(chave, _RespostaAgencia)


def criar_provedor_agencia(config: ConfigIA | None = None) -> AIProvider:
    config = config if config is not None else carregar_config_ia()
    if config.fallback_automatico:
        return obter_provider(config, modo="agencia", schema=_RespostaAgencia)
    if config.provider_ativo == "gemini":
        return obter_provider(config, modo="agencia", chave=obter_chave_gemini(), schema=_RespostaAgencia)
    return obter_provider(config, modo="agencia", schema=_RespostaAgencia)


@medir_operacao("preparacao_transcricao_ia")
def prompt_agencia(janela: list[SegmentoTranscricao], max_historias: int) -> str:
    # Tempos com 1 casa decimal: ~15% menos tokens de entrada, mesma precisão útil
    # (a validação local alinha tudo aos limites reais das falas).
    linhas = "\n".join(f"[{s.inicio:.1f}-{s.fim:.1f}] {s.texto.strip()}" for s in janela)
    return f"""Você é editor-chefe de uma agência profissional de cortes verticais de animação.
A agência publica SOMENTE REMIXES NARRATIVOS: histórias completas REMONTADAS para prender a
atenção. Qualidade é mais importante que quantidade, mas procure de 5 a {max_historias} remixes
independentes, espalhados pelo episódio.

O QUE É UMA BOA HISTÓRIA PARA REMIX
- uma situação completa que um espectador entende sozinho: alguém quer algo, surge um problema,
  a situação piora/fica absurda e termina numa consequência ou piada final;
- DURAÇÃO: de 40 a 90 segundos é o ideal; NUNCA menos de 25 segundos; máximo 120;
- prefira poucos remixes longos e completos a muitos curtos; não fatie uma cena em pedaços;
- todos os segmentos dentro de uma janela de no máximo 150 s, sem intervalo maior que 30 s.

O QUE É UM BOM GANCHO (2 a 6 segundos, uma frase completa)
- a fala mais forte da MESMA história, que acontece DEPOIS do começo dela: frase absurda,
  ameaça, revelação, grito, pergunta chocante ou reação exagerada;
- compreensível sem contexto e faz o espectador pensar "como chegaram nisso?";
- não revela o desfecho; nunca é a primeira fala da história;
- começa pelo menos 15 segundos depois do início da história, para haver contexto real.

MONTAGEM (o app executa): gancho primeiro → volta ao contexto → desenvolvimento → a narrativa
chega ao gancho e CONTINUA a partir do fim dele (sem repetir) → consequência.
Exemplo: história 100.0–180.0 com gancho 160.0–164.5 vira 160.0–164.5 → 100.0–160.0 → 164.5–180.0.

RESPOSTA, para cada remix: gancho (inicio, fim, motivo); segmentos cronológicos com funcao
"contexto", "desenvolvimento", "chegada_ao_gancho" (contém o gancho) e "consequencia";
estrutura (preparacao, conflito, escalada, momento_forte, reacao, conclusao, quando existirem);
remover (silêncios/esperas sem valor); ordem_final começando por "gancho"; justificativa_remix
(por que a reorganização aumenta a retenção); confianca de 0 a 1; titulo; titulos: exatamente 3
alternativas de NO MÁXIMO 8 PALAVRAS, fiéis, sem entregar o desfecho, uma de cada tipo
"curiosidade", "conflito" e "surpresa"; titulo_capa de 2 a 5 palavras.
Use timestamps ABSOLUTOS copiados dos limites das falas abaixo; nunca corte uma fala ao meio.

ANTES DE RESPONDER, descarte o remix que falhar em qualquer item:
(1) duração total ≥ 25 s; (2) gancho entre 2 e 6 s; (3) gancho.inicio ≥ início da história + 15;
(4) gancho dentro de um segmento; (5) história compreensível sozinha e com final;
(6) não é resumo do episódio nem junta cenas distantes.

TRANSCRIÇÃO:
{linhas}"""


def _hash_transcricao(transcricao: Transcricao) -> str:
    conteudo = "\n".join(f"{s.inicio:.3f}|{s.fim:.3f}|{s.texto.strip()}" for s in transcricao.segmentos)
    return hashlib.sha256(conteudo.encode("utf-8")).hexdigest()


def caminho_cache_agencia(transcricao: Transcricao, config: ConfigIA) -> Path:
    """Chave por episódio + transcrição + modelo. Perfil visual NÃO participa."""
    from modules.agencia.remix import REMIX_RULES_VERSION
    base = (f"{transcricao.video_id}|{_hash_transcricao(transcricao)}|{config.modelo}|"
            f"{config.max_chamadas_episodio}|{config.janela_segundos}|"
            f"{config.sobreposicao_segundos}|{VERSAO_PROMPT_AGENCIA}|{REMIX_RULES_VERSION}")
    if config.provider_ativo != "gemini":
        base = f"{config.provider_ativo}|{base}"
    return ANALISES_AGENCIA_DIR / f"{hashlib.sha256(base.encode()).hexdigest()}.json"


def carregar_cache_agencia(transcricao: Transcricao, config: ConfigIA) -> list[PlanoAgencia] | None:
    caminho = caminho_cache_agencia(transcricao, config)
    dados = carregar_grupos(caminho, config, "historias_brutas")
    if dados is None:
        if not caminho.exists():
            return None
        try:
            dados = json.loads(caminho.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return None
    try:
        dados = dict(dados)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    # Isolamento: nunca reutilizar análise de outro episódio/transcrição.
    if dados.get("video_id") != transcricao.video_id:
        return None
    if dados.get("hash_transcricao") != _hash_transcricao(transcricao):
        return None
    brutas = dados.get("historias_aprovadas", dados.get("historias_brutas", []))
    planos, rejeicoes = validar_planos(brutas, transcricao, 50)
    return ListaPlanos(planos, rejeicoes, len(brutas))


def _com_relogio(progresso_cb, pct: float, texto: str, funcao):
    """Executa ``funcao`` mostrando o tempo decorrido a cada 5 s (chamadas longas)."""
    import threading
    import time

    if not progresso_cb:
        return funcao()
    inicio, parar = time.monotonic(), threading.Event()

    def relogio():
        while not parar.wait(5):
            progresso_cb(pct, f"{texto} · {int(time.monotonic() - inicio)} s…")

    progresso_cb(pct, f"{texto}…")
    threading.Thread(target=relogio, daemon=True).start()
    try:
        return funcao()
    finally:
        parar.set()


# Ordem de reserva quando o modelo escolhido está sobrecarregado (503). Só modelos
# de texto "flash"; a lista real vem da conta, então os ausentes são ignorados.
# Reserva quando o modelo escolhido está sobrecarregado (503): no máximo UM outro Flash
# (melhor qualidade) e, em último caso, UM Lite (500/dia, mas remixes mais fracos).
# Cada tentativa recusada também gasta cota gratuita, por isso a lista é curta.
MODELOS_RESERVA_FLASH = ("gemini-3.6-flash", "gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash")
MODELOS_RESERVA_LITE = ("gemini-3.1-flash-lite", "gemini-3.5-flash-lite")
MODELOS_RESERVA = MODELOS_RESERVA_FLASH + MODELOS_RESERVA_LITE


def eh_modelo_lite(modelo: str) -> bool:
    return "lite" in modelo.casefold()


def _analisar_com_reserva(provedor, modelo: str, disponiveis: list[str], prompt: str,
                          progresso_cb, pct: float, parte: str) -> dict:
    """Tenta o modelo escolhido; se continuar em 503, passa para os modelos de reserva."""
    if getattr(provedor, "gerencia_orcamento", False):
        return _com_relogio(progresso_cb, pct, f"Aguardando IA - {parte}",
                           lambda: provedor.analisar(modelo, prompt))
    flash = next((m for m in MODELOS_RESERVA_FLASH
                  if m != modelo and m in disponiveis and not eh_modelo_lite(modelo)), None)
    lite = next((m for m in MODELOS_RESERVA_LITE if m != modelo and m in disponiveis), None)
    candidatos = [modelo] + [m for m in (flash, lite) if m]
    ultimo_erro = None
    for atual in candidatos:
        try:
            return _com_relogio(
                progresso_cb, pct,
                f"Aguardando a Gemini ({atual}) — {parte}. Pode levar de 1 a 5 min",
                lambda: provedor.analisar(atual, prompt))
        except ErroGemini as erro:
            if erro.codigo != 503:
                raise
            ultimo_erro = erro
            if progresso_cb:
                progresso_cb(pct, f"{atual} sobrecarregado; tentando outro modelo…")
    raise ultimo_erro or ErroGemini("Nenhum modelo Gemini respondeu.")


def _analisar_agencia_legado(
    transcricao: Transcricao, config: ConfigIA, provedor: ProvedorAgencia | None = None,
    usar_cache: bool = True, cancelada_ref=None,
    progresso_cb: Callable[[float, str], None] | None = None,
) -> list[PlanoAgencia]:
    config.validar()
    if Path(transcricao.video_path).exists() and not transcricao_corresponde_origem(transcricao):
        raise ValueError("A transcrição não corresponde mais ao vídeo atual. Transcreva novamente.")
    if not transcricao.segmentos:
        raise ValueError("A transcrição não contém falas.")
    alvo = quantidade_alvo(config.max_cortes)
    if usar_cache:
        em_cache = carregar_cache_agencia(transcricao, config)
        if em_cache is not None:
            final, extras_rej = completar_com_remixes_salvos(list(em_cache), transcricao, config, alvo)
            lista = ListaPlanos(final, em_cache.rejeicoes + extras_rej, em_cache.brutas)
            if progresso_cb:
                progresso_cb(100, f"Cache do Modo Agência reutilizado. {lista.mensagem}")
            return lista
    if cancelada_ref and cancelada_ref.cancelada:
        raise RuntimeError("Análise cancelada.")
    provedor = provedor or criar_provedor_agencia(config)
    if progresso_cb:
        progresso_cb(5, "Verificando modelo para o Modo Agência…")
    disponiveis = provedor.listar_modelos()
    modelo = selecionar_modelo_disponivel(provedor, config.modelo, disponiveis)
    janelas = _janelas(transcricao, config)[:config.max_chamadas_episodio]
    brutos: list[dict] = []
    respostas_provider = []
    falhas: list[str] = []
    for indice, janela in enumerate(janelas, 1):
        if getattr(provedor, "gerencia_orcamento", False) and provedor.restantes == 0:
            break
        if cancelada_ref and cancelada_ref.cancelada:
            raise RuntimeError("Análise cancelada.")
        pct = 10 + (indice - 1) / max(1, len(janelas)) * 70
        try:
            resposta = _analisar_com_reserva(
                provedor, modelo, disponiveis, prompt_agencia(janela, MAX_HISTORIAS),
                progresso_cb, pct, f"parte {indice}/{len(janelas)}")
            itens = resposta.get("historias", []) if isinstance(resposta, dict) else None
            if not isinstance(itens, list):
                raise ErroGemini("A resposta não contém uma lista de histórias.")
            brutos.extend(itens)
            if getattr(provedor, "gerencia_orcamento", False):
                respostas_provider.append((resposta, itens))
        except ErroGemini as erro:
            if getattr(erro, "interromper_analise", False):
                raise
            falhas.append(str(erro))
    if falhas and len(falhas) == len(janelas):
        raise ErroGemini(f"Nenhuma janela foi analisada: {falhas[0]}")
    planos, rejeicoes = validar_planos(brutos, transcricao, 50)
    # Complemento: uma chamada extra pedindo APENAS novas histórias remixáveis.
    chamadas_restantes = (provedor.restantes if getattr(provedor, "gerencia_orcamento", False)
                          else config.max_chamadas_episodio - len(janelas))
    if (len(planos) < MIN_HISTORIAS and chamadas_restantes > 0
            and not (cancelada_ref and cancelada_ref.cancelada)):
        if progresso_cb:
            progresso_cb(85, f"Só {len(planos)} remixes válidos; pedindo complemento à IA…")
        segmentos = [s for s in transcricao.segmentos if s.fim > s.inicio and s.texto.strip()]
        try:
            resposta = _analisar_com_reserva(
                provedor, modelo, disponiveis,
                prompt_complemento(segmentos, planos, rejeicoes, MIN_HISTORIAS - len(planos)),
                progresso_cb, 85, "complemento")
            itens = resposta.get("historias", []) if isinstance(resposta, dict) else []
            if isinstance(itens, list):
                brutos.extend(itens)
                if getattr(provedor, "gerencia_orcamento", False):
                    respostas_provider.append((resposta, itens))
                planos, rejeicoes = validar_planos(brutos, transcricao, 50)
        except ErroGemini as erro:
            if getattr(erro, "interromper_analise", False):
                raise
            falhas.append(f"complemento: {erro}")
    destino = caminho_cache_agencia(transcricao, config)
    destino.parent.mkdir(parents=True, exist_ok=True)
    if getattr(provedor, "gerencia_orcamento", False):
        salvar_grupos(destino, config, respostas_provider, "historias_brutas", {
            "versao": VERSAO_PROMPT_AGENCIA, "video_id": transcricao.video_id,
            "hash_transcricao": _hash_transcricao(transcricao),
            "rejeicoes": rejeicoes, "falhas_janelas": falhas,
        }, lambda real: caminho_cache_agencia(transcricao, real))
    else:
        tmp = destino.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({
            "versao": VERSAO_PROMPT_AGENCIA, "video_id": transcricao.video_id,
            "hash_transcricao": _hash_transcricao(transcricao), "modelo": config.modelo,
            "historias_brutas": brutos, "rejeicoes": rejeicoes, "falhas_janelas": falhas,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(destino)
    final, extras_rej = completar_com_remixes_salvos(planos, transcricao, config, alvo)
    lista = ListaPlanos(final, rejeicoes + extras_rej, len(brutos))
    if progresso_cb:
        progresso_cb(100, f"{lista.mensagem} {len(lista.rejeicoes)} candidato(s) rejeitado(s).")
    return lista


# ─── Quantidade: mínimo 5, máximo 10 ─────────────────────────────────────────

class _SomenteCache:
    """Provedor que nunca acessa a rede: força o uso exclusivo de análises já salvas."""
    somente_cache = True

    def listar_modelos(self):
        raise ErroGemini("Sem análise em cache para este episódio.")

    def analisar(self, *_args):
        raise ErroGemini("Sem análise em cache para este episódio.")


def quantidade_alvo(solicitada: int) -> int:
    return min(MAX_HISTORIAS, max(3, int(solicitada)))


def prompt_complemento(segmentos: list[SegmentoTranscricao], planos: list[PlanoAgencia],
                       rejeicoes: list[str], faltam: int) -> str:
    """Pedido de complemento com tudo o que já foi usado ou rejeitado."""
    usados = "; ".join(f"{p.inicio:.1f}–{p.fim:.1f}s" for p in planos) or "nenhum"
    historias = "\n".join(f"- {p.titulo}: {p.descricao}" for p in planos) or "- nenhuma"
    titulos = "; ".join(t.texto for p in planos for t in p.titulos) or "nenhum"
    rejeitados = "\n".join(f"- {r}" for r in rejeicoes[:15]) or "- nenhum"
    return (prompt_agencia(segmentos, MAX_HISTORIAS) + f"""

COMPLEMENTO — proponha pelo menos {faltam} NOVOS REMIXES, com histórias DIFERENTES,
completas e de preferência entre 40 e 90 segundos (nunca menos de 25).
Intervalos já utilizados (não se sobreponha a eles): {usados}
Histórias já escolhidas:
{historias}
Títulos já utilizados (não repita): {titulos}
Candidatos rejeitados e motivos (não repita esses erros):
{rejeitados}""")


def completar_com_remixes_salvos(planos: list[PlanoAgencia], transcricao: Transcricao,
                                 config: ConfigIA, alvo: int | None = None
                                 ) -> tuple[list[PlanoAgencia], list[str]]:
    """Completa (se necessário) SOMENTE com remixes salvos e revalidados; limita a 10.

    Nunca usa cortes normais, análises contextuais comuns ou sugestões estruturais.
    Se não houver remixes suficientes, devolve menos que 5 — sem inventar cortes.
    """
    alvo = quantidade_alvo(alvo or config.max_cortes)
    escolhidos = list(planos)
    rejeicoes: list[str] = []
    if len(escolhidos) < MIN_HISTORIAS:
        from modules.remix_narrativo import analisar_remixes

        try:
            remixes = analisar_remixes(transcricao, config, provedor=_SomenteCache())
        except (ErroGemini, ValueError, RuntimeError):
            remixes = []
        salvos, rejeicoes = planos_de_remixes_salvos(transcricao, remixes)
        escolhidos = _acrescentar_sem_duplicar(escolhidos, salvos, MIN_HISTORIAS)
    somente_remix = [p for p in escolhidos if p.usar_remix and p.gancho]
    melhores = sorted(somente_remix, key=lambda p: -pontuacao_qualidade(p))[:alvo]
    return sorted(melhores, key=lambda p: p.inicio), rejeicoes


def pontuacao_qualidade(plano: PlanoAgencia) -> float:
    """Confiança da análise, favorecendo histórias longas e completas (até ~45 s)."""
    return plano.confianca * (0.5 + 0.5 * min(1.0, plano.duracao_historia / DURACAO_IDEAL))


def mensagem_meta(quantidade: int) -> str:
    if quantidade == 0:
        from modules.agencia.recuperacao import MENSAGEM_ZERO
        return MENSAGEM_ZERO
    if quantidade < MIN_HISTORIAS:
        return (f"Encontrados {quantidade} remixes válidos neste episódio. "
                f"Meta da agência: {MIN_HISTORIAS}.")
    return f"{quantidade} remixes válidos prontos para revisão."


@medir_operacao("analise_ia")
def analisar_agencia(transcricao: Transcricao, config: ConfigIA, provedor=None,
                     usar_cache=True, cancelada_ref=None, progresso_cb=None) -> ListaPlanos:
    """Rollback editorial: descoberta original, sem minimum-target ou Auto Recovery."""
    from modules.agencia.rollback_2809 import analisar
    return analisar(transcricao,config,provedor,usar_cache,cancelada_ref,progresso_cb)


def _analisar_agencia_antes_rollback(transcricao: Transcricao, config: ConfigIA, provedor=None,
                                    usar_cache=True, cancelada_ref=None, progresso_cb=None):
    """Preserved inactive implementation; never dispatched by production."""
    anotar(provider=config.provider_ativo, modelo=config.modelo, entrada_segmentos=len(transcricao.segmentos))
    from modules.agencia.recuperacao import DiagnosticoRemix, buscar_remixes
    from modules.agencia.remix import REMIX_RULES_VERSION

    config.validar()
    if not transcricao.segmentos:
        raise ValueError("A transcricao nao contem falas.")
    if Path(transcricao.video_path).exists() and not transcricao_corresponde_origem(transcricao):
        raise ValueError("A transcricao nao corresponde ao episodio atual.")
    destino = caminho_cache_agencia(transcricao, config)
    dados = carregar_grupos(destino, config, "historias_brutas") if usar_cache else None
    if dados is None and usar_cache and destino.exists():
        try:
            dados = json.loads(medir_chamada("cache_analise_leitura", destino.read_text, encoding="utf-8"))
        except (OSError, ValueError):
            dados = None
    if dados and (dados.get("video_id") != transcricao.video_id or
                  dados.get("hash_transcricao") != _hash_transcricao(transcricao)):
        dados = None
    anotar(cache="hit" if dados else ("miss" if usar_cache else "disabled"),
           arquivos_reutilizados=[str(destino)] if dados else [])
    if dados:
        planos, rejeicoes = validar_planos(dados.get("historias_aprovadas", dados.get("historias_brutas", [])), transcricao, 50)
        if planos or "diagnostico" in dados:
            salvos = dados.get("diagnostico", {})
            diag = DiagnosticoRemix(**{k: v for k, v in salvos.items()
                                     if k in DiagnosticoRemix.__dataclass_fields__})
            diag.motivos_rejeicao = rejeicoes or [m.get('detalhe',m.get('motivo','')) if isinstance(m,dict) else m
                                                 for m in diag.motivos_rejeicao]
            planos = sorted(sorted(planos, key=lambda p: -pontuacao_qualidade(p))
                            [:quantidade_alvo(config.max_cortes)], key=lambda p: p.inicio)
            diag.finalizar(planos)
            chamadas_historicas,diag.chamadas_feitas = diag.chamadas_feitas,0
            lista = ListaPlanos(planos, diag.motivos_rejeicao, diag.candidatos_recebidos, diag)
            from modules.agencia.auto_recovery import auto_recuperar
            lista = auto_recuperar(lista, transcricao, config, sementes=dados.get("historias_brutas", []),
                                  provedor=provedor,cancelada_ref=cancelada_ref,progresso_cb=progresso_cb,
                                  chamadas_historicas=chamadas_historicas)
            if progresso_cb:
                progresso_cb(100, "Cache revalidado. " + lista.mensagem)
            return lista
    provedor = provedor or criar_provedor_agencia(config)
    lista = buscar_remixes(transcricao, config, provedor,
                           cancelada_ref=cancelada_ref, progresso_cb=progresso_cb,
                           sementes=dados.get("historias_brutas", []) if dados else (), producao=True)
    # Cache salvo apenas depois de todas as etapas; nunca carimba resultados antigos.
    metadados = {"versao": VERSAO_PROMPT_AGENCIA, "remix_rules_version": REMIX_RULES_VERSION,
                 "video_id": transcricao.video_id, "hash_transcricao": _hash_transcricao(transcricao),
                 "modelo": config.modelo, "diagnostico": asdict(lista.diagnostico),
                 "rejeicoes": lista.rejeicoes, "historias_aprovadas": lista.brutos_aprovados}
    destino.parent.mkdir(parents=True, exist_ok=True)
    if config.fallback_automatico and lista.respostas_provider and all(
            "provider_utilizado" in r for r, _ in lista.respostas_provider):
        salvar_grupos(destino, config, lista.respostas_provider, "historias_brutas", metadados,
                      lambda real: caminho_cache_agencia(transcricao, real))
    else:
        temporario = destino.with_suffix(".json.tmp")
        temporario.write_text(json.dumps({**metadados, "historias_brutas": lista.brutos},
                                        ensure_ascii=False, indent=2), encoding="utf-8")
        temporario.replace(destino)
    diagnostico_path = ANALISES_AGENCIA_DIR.parent / "diagnosticos" / f"{transcricao.video_id}.json"
    diagnostico_path.parent.mkdir(parents=True, exist_ok=True)
    temporario = diagnostico_path.with_suffix(".json.tmp")
    temporario.write_text(json.dumps(metadados, ensure_ascii=False, indent=2), encoding="utf-8")
    temporario.replace(diagnostico_path)
    return lista


def _acrescentar_sem_duplicar(atuais: list[PlanoAgencia], extras: list[PlanoAgencia],
                              minimo: int) -> list[PlanoAgencia]:
    resultado = list(atuais)
    for extra in sorted(extras, key=lambda p: -p.confianca):
        if len(resultado) >= minimo:
            break
        if all(sobreposicao_relativa(extra, atual) <= 0.6 for atual in resultado):
            resultado.append(extra)
    return resultado
