"""Enquadramento do conteúdo principal e ajuste de textos às safe zones do perfil."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

from modules import preset as P
from modules.legendas import BlocoLegenda
from modules.perfis_visuais import (
    ESTILO_LEGENDA_AGENCIA_PADRAO,
    ESTILO_TITULO_AGENCIA_PADRAO,
    ZONAS_TOONMIX,
    EstiloTexto,
    Zona,
)


# ─── Enquadramento ────────────────────────────────────────────────────────────

# Recorte máximo aceito antes de trocar "cobrir" por "ajustar com fundo desfocado".
# Acima disso personagens nas bordas de cenas 16:9 costumam ser cortados.
CORTE_MAXIMO_PADRAO = 0.18


def classificar_fonte(largura: int, altura: int) -> str:
    if largura <= 0 or altura <= 0:
        raise ValueError("Dimensões de vídeo inválidas.")
    proporcao = largura / altura
    if proporcao > 1.15:
        return "horizontal"
    if proporcao < 0.87:
        return "vertical"
    return "quadrada"


def _par(valor: float) -> int:
    return max(2, int(round(valor / 2.0)) * 2)


@dataclass(frozen=True)
class Enquadramento:
    """Geometria do conteúdo dentro da VIDEO_ZONE (coordenadas relativas à zona)."""

    tipo_fonte: str
    estrategia: str               # "cobrir" | "ajustar_desfoque"
    escala_w: int                 # tamanho após scale
    escala_h: int
    crop_w: int                   # recorte aplicado após o scale
    crop_h: int
    crop_x: int
    crop_y: int
    pos_x: int                    # posição do conteúdo recortado dentro da zona
    pos_y: int
    zona_w: int
    zona_h: int
    fracao_visivel: float         # fração da imagem original que permanece visível

    @property
    def usa_desfoque(self) -> bool:
        return self.estrategia == "ajustar_desfoque"

    @property
    def area_ocupada(self) -> float:
        return (self.crop_w * self.crop_h) / float(self.zona_w * self.zona_h)


def calcular_enquadramento(
    largura: int, altura: int, zona: Zona = ZONAS_TOONMIX.video,
    corte_maximo: float = CORTE_MAXIMO_PADRAO,
    centro_x: float = 0.5, centro_y: float = 0.5,
) -> Enquadramento:
    """Preenche estritamente 100% da VIDEO_ZONE sem letterbox, barras ou desfoque.

    Padrão Kwai Cut de alta performance: o vídeo principal cobre totalmente a zona
    (estratégia 'cobrir'). Em vídeos 16:9, as laterais são cortadas mantendo a proporção
    exata e o centro de interesse. Em 9:16, o excesso vertical é cortado para caber em 1:1.
    """
    tipo = classificar_fonte(largura, altura)
    centro_x = min(1.0, max(0.0, float(centro_x)))
    centro_y = min(1.0, max(0.0, float(centro_y)))
    zona = zona or ZONAS_TOONMIX.video
    proporcao_fonte = largura / altura
    proporcao_zona = zona.w / zona.h

    if proporcao_fonte >= proporcao_zona:
        # Fonte horizontal (ex: 16:9 em 1:1)
        # Altura casa com a zona; largura excedente é cortada nas laterais (sem barras)
        escala_h = zona.h
        escala_w = max(zona.w, _par(zona.h * proporcao_fonte))
        crop_w, crop_h = zona.w, zona.h
        estrategia, pos_x, pos_y = "cobrir", 0, 0
        excesso = escala_w - crop_w
        crop_x = int(round(min(excesso, max(0.0, centro_x * escala_w - crop_w / 2))))
        crop_x = _par(crop_x)
        if crop_x > excesso:
            crop_x = excesso
        crop_y = 0
        visivel = proporcao_zona / proporcao_fonte
    else:
        # Fonte vertical (ex: 9:16 em 1:1)
        # Largura casa com a zona; altura excedente é cortada no topo e base
        escala_w = zona.w
        escala_h = max(zona.h, _par(zona.w / proporcao_fonte))
        crop_w, crop_h = zona.w, zona.h
        estrategia, pos_x, pos_y = "cobrir", 0, 0
        excesso = escala_h - crop_h
        crop_y = int(round(min(excesso, max(0.0, centro_y * escala_h - crop_h / 2))))
        crop_y = _par(crop_y)
        if crop_y > excesso:
            crop_y = excesso
        crop_x = 0
        visivel = proporcao_fonte / proporcao_zona

    return Enquadramento(tipo, estrategia, escala_w, escala_h, crop_w, crop_h,
                         crop_x, crop_y, pos_x, pos_y, zona.w, zona.h, round(visivel, 4))


def estimar_centro_interesse(imagens) -> tuple[float, float]:
    """Estima o centro horizontal/vertical da ação por energia de bordas e saturação.

    Personagens de animação concentram contornos e cores saturadas; paredes e céu
    são lisos. O resultado é amortecido em direção ao centro para ser conservador.
    """
    import numpy as np
    from PIL import ImageFilter

    soma_x = soma_y = None
    for imagem in imagens:
        pequena = imagem.convert("RGB").resize((192, 108))
        bordas = np.asarray(pequena.convert("L").filter(ImageFilter.FIND_EDGES), dtype=float)
        hsv = np.asarray(pequena.convert("HSV"), dtype=float)
        energia = bordas * (0.5 + hsv[:, :, 1] / 255.0)
        coluna, linha = energia.sum(axis=0), energia.sum(axis=1)
        soma_x = coluna if soma_x is None else soma_x + coluna
        soma_y = linha if soma_y is None else soma_y + linha
    if soma_x is None or soma_x.sum() <= 0:
        return 0.5, 0.5
    xs = (np.arange(len(soma_x)) + 0.5) / len(soma_x)
    ys = (np.arange(len(soma_y)) + 0.5) / len(soma_y)
    cx = float((xs * soma_x).sum() / soma_x.sum())
    cy = float((ys * soma_y).sum() / soma_y.sum())
    # Amortecimento: metade do deslocamento, limitado a 0,32–0,68.
    cx = min(0.68, max(0.32, 0.5 + (cx - 0.5) * 0.5))
    cy = min(0.62, max(0.38, 0.5 + (cy - 0.5) * 0.5))
    return round(cx, 3), round(cy, 3)


# ─── Texto ────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=128)
def _fonte(caminho: str, tamanho: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(caminho, tamanho)


@lru_cache(maxsize=8192)
def _largura_exata(texto: str, tamanho: int, fonte: str) -> int:
    esquerda, _topo, direita, _base = _fonte(fonte, tamanho).getbbox(texto)
    return int(direita - esquerda)


@lru_cache(maxsize=16384)
def _avanco(texto: str, tamanho: int, fonte: str) -> float:
    return _fonte(fonte, tamanho).getlength(texto)


def largura_texto(texto: str, tamanho: int, borda: int = 0, fonte: str | Path = P.FONTE_PATH) -> int:
    if not texto:
        return 0
    return _largura_exata(texto, int(tamanho), str(fonte)) + 2 * int(borda)


def _largura_estimada(palavras: list[str], tamanho: int, borda: int, fonte: str) -> float:
    """Soma de avanços por palavra (rápida); a quebra escolhida é conferida com getbbox."""
    espaco = _avanco(" ", tamanho, fonte)
    return sum(_avanco(p, tamanho, fonte) for p in palavras) + espaco * (len(palavras) - 1) + 2 * borda


def altura_linha(tamanho: int, fonte: str | Path = P.FONTE_PATH) -> int:
    ascendente, descendente = _fonte(str(fonte), int(tamanho)).getmetrics()
    return int(ascendente + descendente)


@dataclass
class TextoAjustado:
    texto: str
    linhas: list[str]
    tamanho: int
    borda: int
    altura_linha: int
    largura_max: int
    altura_total: int
    x_centro: int
    y_topo: int
    cabe: bool
    zona: Zona
    sombra_y: int = 0
    avisos: list[str] = field(default_factory=list)

    @property
    def caixa(self) -> Zona:
        """Caixa real ocupada, incluindo contorno e sombra."""
        x = int(self.x_centro - self.largura_max / 2)
        return Zona(x, self.y_topo - self.borda, self.largura_max,
                    self.altura_total + 2 * self.borda + max(0, self.sombra_y))

    def y_linha(self, indice: int) -> int:
        return self.y_topo + indice * self.altura_linha


def _quebras(palavras: list[str], n: int):
    """Todas as divisões de ``palavras`` em ``n`` linhas não vazias, em ordem."""
    if n == 1:
        yield [" ".join(palavras)]
        return
    for i in range(1, len(palavras) - n + 2):
        for resto in _quebras(palavras[i:], n - 1):
            yield [" ".join(palavras[:i])] + resto


def quebrar_equilibrado(texto: str, tamanho: int, borda: int, largura_max: int,
                        max_linhas: int, fonte: str | Path = P.FONTE_PATH) -> list[str] | None:
    """Menor número de linhas que cabe; entre as opções, a mais equilibrada."""
    palavras = texto.split()
    if not palavras:
        return []
    fonte = str(fonte)
    # Margem de 3% na estimativa; a opção escolhida é sempre conferida exatamente.
    if _largura_estimada(palavras, tamanho, borda, fonte) > largura_max * max_linhas * 1.03:
        return None
    for n in range(1, min(max_linhas, len(palavras)) + 1):
        opcoes = []
        for linhas in _quebras(palavras, n):
            larguras = [_largura_estimada(l.split(), tamanho, borda, fonte) for l in linhas]
            if max(larguras) <= largura_max * 1.03:
                opcoes.append(((max(larguras), -min(larguras)), linhas))
        for _chave, linhas in sorted(opcoes, key=lambda item: item[0]):
            if max(largura_texto(l, tamanho, borda, fonte) for l in linhas) <= largura_max:
                return linhas
    return None


def ajustar_texto(texto: str, estilo: EstiloTexto = ESTILO_TITULO_AGENCIA_PADRAO, zona: Zona = ZONAS_TOONMIX.titulo, *,
                  tamanho_max: int | None = None, tamanho_min: int | None = None,
                  max_linhas: int | None = None, alinhamento_vertical: str = "centro",
                  fonte: str | Path = P.FONTE_PATH) -> TextoAjustado:
    """Escala adaptativa: maior tamanho que cabe na zona em até ``max_linhas`` linhas."""
    limpo = " ".join(str(texto).split())
    if estilo.caixa_alta:
        limpo = limpo.upper()
    tamanho_max = int(tamanho_max or estilo.tamanho)
    tamanho_min = int(tamanho_min or estilo.tamanho_min)
    max_linhas = int(max_linhas or estilo.max_linhas)
    borda = int(estilo.espessura_contorno)
    sombra = max(0, int(estilo.sombra_y))
    escolhido = None
    melhor_pontuacao = None
    for tamanho in range(tamanho_max, tamanho_min - 1, -2):
        linhas = quebrar_equilibrado(limpo, tamanho, borda, zona.w, max_linhas, fonte)
        if linhas is None:
            continue
        lh = altura_linha(tamanho, fonte)
        altura = lh * len(linhas)
        if altura + 2 * borda + sombra > zona.h:
            continue
        # Cada linha extra custa 8 pontos: uma linha larga vence duas estreitas
        # de tamanho parecido, mas títulos longos continuam em duas linhas.
        pontuacao = tamanho - 8 * (len(linhas) - 1)
        if melhor_pontuacao is None or pontuacao > melhor_pontuacao:
            melhor_pontuacao = pontuacao
            escolhido = (tamanho, linhas, lh, altura, True)
    avisos = []
    if escolhido is None:
        # Não coube: devolve a melhor tentativa no tamanho mínimo e marca overflow.
        tamanho = tamanho_min
        linhas = quebrar_equilibrado(limpo, tamanho, borda, 10 ** 6, max_linhas, fonte) or [limpo]
        palavras, linhas = limpo.split(), []
        atual = ""
        for palavra in palavras:
            teste = f"{atual} {palavra}".strip()
            if atual and largura_texto(teste, tamanho, borda, fonte) > zona.w:
                linhas.append(atual)
                atual = palavra
            else:
                atual = teste
        if atual:
            linhas.append(atual)
        lh = altura_linha(tamanho, fonte)
        escolhido = (tamanho, linhas, lh, lh * len(linhas), False)
        avisos.append("Texto não cabe na zona reservada mesmo no tamanho mínimo.")
    tamanho, linhas, lh, altura, cabe = escolhido
    largura = max((largura_texto(l, tamanho, borda, fonte) for l in linhas), default=0)
    altura_bloco = altura + 2 * borda + sombra
    if alinhamento_vertical == "topo":
        y_topo = zona.y + borda
    elif alinhamento_vertical == "base":
        y_topo = zona.y2 - altura_bloco + borda
    else:
        y_topo = int(zona.y + (zona.h - altura_bloco) / 2 + borda)
    ajustado = TextoAjustado(limpo, linhas, tamanho, borda, lh, largura, altura,
                             int(zona.x + zona.w / 2), y_topo, cabe, zona, sombra, avisos)
    if cabe and not zona.contem(ajustado.caixa):
        ajustado.cabe = False
        ajustado.avisos.append("Caixa do texto ultrapassa a safe zone.")
    return ajustado


def verificar_overflow(ajustado: TextoAjustado, zona: Zona | None = None) -> list[str]:
    zona = zona or ajustado.zona
    problemas = list(ajustado.avisos)
    if not zona.contem(ajustado.caixa):
        problemas.append(
            f"Texto ocupa {ajustado.caixa} e ultrapassa a zona {zona}."
        )
    return sorted(set(problemas))


from modules.performance import medir_operacao


@medir_operacao("legendas_layout")
def dividir_legendas_longas(blocos: list[BlocoLegenda], estilo: EstiloTexto = ESTILO_LEGENDA_AGENCIA_PADRAO,
                            zona: Zona = ZONAS_TOONMIX.legenda, fonte: str | Path = P.FONTE_PATH,
                            profundidade: int = 0) -> list[BlocoLegenda]:
    """Divide blocos que não cabem em ``max_linhas`` na zona, preservando os tempos.

    O tempo de cada metade é proporcional ao número de caracteres, e a divisão
    ocorre sempre entre palavras.
    """
    resultado: list[BlocoLegenda] = []
    for bloco in blocos:
        ajustado = ajustar_texto(bloco.texto, estilo, zona, fonte=fonte)
        palavras = bloco.texto.split()
        if ajustado.cabe or len(palavras) < 2 or profundidade > 6:
            resultado.append(bloco)
            continue
        meio = len(palavras) // 2
        primeira, segunda = " ".join(palavras[:meio]), " ".join(palavras[meio:])
        proporcao = len(primeira) / max(1, len(primeira) + len(segunda))
        corte = bloco.inicio + (bloco.fim - bloco.inicio) * proporcao
        resultado.extend(dividir_legendas_longas(
            [BlocoLegenda(bloco.inicio, corte, primeira), BlocoLegenda(corte, bloco.fim, segunda)],
            estilo, zona, fonte, profundidade + 1,
        ))
    return resultado


# ─── Movimento (frames mortos) ───────────────────────────────────────────────

LIMIAR_FRAMES_MORTOS = 2.5


@medir_operacao("movimento", backend="ffmpeg")
def medir_movimento(video: str | Path, inicio: float, fim: float, amostras_por_s: int = 4) -> float:
    """Diferença média (0–255) entre frames consecutivos, em baixa resolução.

    Valores baixos indicam espera/frames mortos; valores altos indicam ação visual
    que não deve ser removida só porque não há fala.
    """
    import subprocess
    import numpy as np

    duracao = max(0.25, fim - inicio)
    resultado = subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-threads", "2", "-ss", f"{max(0.0, inicio):.3f}", "-t", f"{duracao:.3f}",
         "-i", str(video), "-vf", f"fps={amostras_por_s},scale=64:36,format=gray",
         "-f", "rawvideo", "-"],
        stdin=subprocess.DEVNULL, capture_output=True, timeout=60,
    )
    dados = np.frombuffer(resultado.stdout, dtype=np.uint8)
    quadros = len(dados) // (64 * 36)
    if resultado.returncode != 0 or quadros < 2:
        return 255.0  # sem medição confiável: trata como ação (conservador)
    frames = dados[:quadros * 64 * 36].reshape(quadros, 36, 64).astype(np.int16)
    media = float(np.abs(np.diff(frames, axis=0)).mean())
    del dados, frames
    return media
