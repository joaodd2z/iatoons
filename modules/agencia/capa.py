"""Capa integrada ao corte: 3 frames candidatos + identidade do perfil + título curto."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import subprocess

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageStat

from modules import preset as P
from modules.agencia.editorial import PlanoAgencia, titulo_capa_de
from modules.agencia.layout import Enquadramento, _fonte, ajustar_texto
from modules.agencia.renderer import _cobrir
from modules.agencia.timeline import TimelineAgencia
from modules.perfis_visuais import EstiloTexto, PerfilVisual


TAMANHO_MIN_LEGIVEL = 90   # em 1080 px de largura → ~15 px numa miniatura de 180 px


@dataclass
class FrameCandidato:
    tempo_saida: float
    tempo_fonte: float
    pontuacao: float
    miniatura: str
    nitidez: float = 0.0
    saturacao: float = 0.0
    centro: float = 0.0
    exposicao: float = 0.0


def _cor_pil(cor: str) -> str:
    cor = cor.split("@")[0]
    return "#" + cor[2:] if cor.lower().startswith("0x") else cor


def _pontuar(imagem: Image.Image) -> dict:
    cinza = imagem.convert("L")
    bordas = cinza.filter(ImageFilter.FIND_EDGES)
    nitidez = min(1.0, ImageStat.Stat(bordas).var[0] / 1800.0)
    saturacao = ImageStat.Stat(imagem.convert("HSV")).mean[1] / 255.0
    w, h = bordas.size
    total = sum(ImageStat.Stat(bordas).sum) or 1.0
    miolo = sum(ImageStat.Stat(bordas.crop((int(w * 0.2), int(h * 0.1), int(w * 0.8), int(h * 0.95)))).sum)
    centro = miolo / total
    luma = ImageStat.Stat(cinza).mean[0]
    exposicao = abs(luma - 125) / 125
    return {"nitidez": nitidez, "saturacao": saturacao, "centro": centro, "exposicao": exposicao}


def candidatos_capa(video: str | Path, timeline: TimelineAgencia, plano: PlanoAgencia,
                    pasta: str | Path, quantidade: int = 3, passo: float = 0.5,
                    max_amostras: int = 60) -> list[FrameCandidato]:
    """Amostra a timeline (longe de cortes/transições) e escolhe frames fortes e distintos."""
    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    fronteiras = timeline.fronteiras_saida()
    destaque = [plano.estrutura.get(c) for c in ("momento_forte", "reacao")] + [plano.gancho]
    destaque = [(float(d["inicio"]), float(d["fim"])) for d in destaque if d]
    amostras: list[FrameCandidato] = []
    for indice, (trecho, offset) in enumerate(zip(timeline.trechos, timeline.offsets())):
        if trecho.duracao < 1.0:
            continue
        prefixo = pasta / f"amostra_{indice:02d}_"
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error", "-ss", f"{trecho.inicio + 0.25:.3f}",
            "-t", f"{max(0.3, trecho.duracao - 0.5):.3f}", "-i", str(video),
            "-vf", f"fps={1 / passo:.4f},scale=320:-2", "-q:v", "3", f"{prefixo}%03d.jpg",
        ], capture_output=True, timeout=120)
        for arquivo in sorted(pasta.glob(f"amostra_{indice:02d}_*.jpg")):
            numero = int(arquivo.stem.rsplit("_", 1)[1]) - 1
            tempo_fonte = trecho.inicio + 0.25 + numero * passo
            tempo_saida = offset + (tempo_fonte - trecho.inicio)
            if any(abs(tempo_saida - f) < 0.5 for f in fronteiras):
                continue
            with Image.open(arquivo) as imagem:
                m = _pontuar(imagem.convert("RGB"))
            bonus = 0.25 if any(a <= tempo_fonte <= b for a, b in destaque) else 0.0
            pontuacao = (0.35 * m["nitidez"] + 0.2 * m["saturacao"] + 0.25 * m["centro"]
                         + bonus - 0.3 * m["exposicao"])
            amostras.append(FrameCandidato(round(tempo_saida, 3), round(tempo_fonte, 3),
                                           round(pontuacao, 4), str(arquivo), **{k: round(v, 4) for k, v in m.items()}))
    if len(amostras) > max_amostras:
        passo_sub = len(amostras) / max_amostras
        amostras = [amostras[int(i * passo_sub)] for i in range(max_amostras)]
    escolhidos: list[FrameCandidato] = []
    for candidato in sorted(amostras, key=lambda c: -c.pontuacao):
        if any(abs(candidato.tempo_saida - e.tempo_saida) < 1.5 for e in escolhidos):
            continue
        with Image.open(candidato.miniatura) as a:
            atual = a.convert("L").resize((80, 45))
        parecido = False
        for existente in escolhidos:
            with Image.open(existente.miniatura) as b:
                if ImageStat.Stat(_diff(atual, b.convert("L").resize((80, 45)))).mean[0] < 6:
                    parecido = True
                    break
        if not parecido:
            escolhidos.append(candidato)
        if len(escolhidos) >= quantidade:
            break
    # Cenas muito homogêneas: completa com os melhores restantes, ainda espaçados no tempo.
    for candidato in sorted(amostras, key=lambda c: -c.pontuacao):
        if len(escolhidos) >= quantidade:
            break
        if candidato not in escolhidos and all(abs(candidato.tempo_saida - e.tempo_saida) >= 1.0
                                               for e in escolhidos):
            escolhidos.append(candidato)
    return escolhidos


def _diff(a: Image.Image, b: Image.Image) -> Image.Image:
    from PIL import ImageChops
    return ImageChops.difference(a, b)


def _enquadrar_pil(frame: Image.Image, enquadramento: Enquadramento, desfoque: int) -> Image.Image:
    e = enquadramento
    escalado = frame.resize((e.escala_w, e.escala_h), Image.Resampling.LANCZOS)
    conteudo = escalado.crop((e.crop_x, e.crop_y, e.crop_x + e.crop_w, e.crop_y + e.crop_h))
    # Mesmo tratamento moderado do AGENCY_AV_V1, somente no conteúdo principal.
    conteudo = ImageEnhance.Contrast(conteudo).enhance(1.03)
    conteudo = ImageEnhance.Color(conteudo).enhance(1.05)
    conteudo = ImageEnhance.Sharpness(conteudo).enhance(1.25)
    if not e.usa_desfoque:
        return conteudo
    fundo = _cobrir(frame, e.zona_w, e.zona_h).filter(ImageFilter.GaussianBlur(desfoque))
    fundo = ImageEnhance.Brightness(fundo).enhance(0.9)
    fundo.paste(conteudo, (e.pos_x, e.pos_y))
    return fundo


def desenhar_texto(imagem: Image.Image, ajustado, estilo: EstiloTexto) -> None:
    desenho = ImageDraw.Draw(imagem)
    for i, linha in enumerate(ajustado.linhas):
        fonte = _fonte(str(P.FONTE_PATH), ajustado.tamanho)
        posicao = (ajustado.x_centro, ajustado.y_linha(i))
        if estilo.sombra_x or estilo.sombra_y:
            desenho.text((posicao[0] + estilo.sombra_x, posicao[1] + estilo.sombra_y), linha, font=fonte,
                         fill=(0, 0, 0, 150), anchor="ma", stroke_width=ajustado.borda,
                         stroke_fill=(0, 0, 0, 150))
        desenho.text(posicao, linha, font=fonte, fill=_cor_pil(estilo.cor), anchor="ma",
                     stroke_width=ajustado.borda, stroke_fill=_cor_pil(estilo.cor_contorno))


def compor_capa(video: str | Path, tempo_fonte: float, perfil: PerfilVisual,
                enquadramento: Enquadramento, titulo_capa: str, destino: str | Path,
                desfoque: int = 26) -> dict:
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    frame_path = destino.with_name(destino.stem + "_frame.png")
    resultado = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{tempo_fonte:.3f}",
                                "-i", str(video), "-frames:v", "1", str(frame_path)],
                               capture_output=True, timeout=60)
    if resultado.returncode != 0 or not frame_path.exists():
        raise RuntimeError("Não foi possível extrair o frame da capa.")
    frame = Image.open(frame_path).convert("RGB")
    fundo = _cobrir(Image.open(perfil.fundo_path).convert("RGB"), P.SAIDA_LARGURA, P.SAIDA_ALTURA)
    zona = perfil.zonas.video
    fundo.paste(_enquadrar_pil(frame, enquadramento, desfoque), (zona.x, zona.y))
    estilo = perfil.estilo_titulo_agencia
    texto = titulo_capa_de(titulo_capa or "", 5)
    ajustado = ajustar_texto(texto, estilo, perfil.zonas.titulo, tamanho_max=170,
                             tamanho_min=TAMANHO_MIN_LEGIVEL, max_linhas=2)
    if not ajustado.cabe:
        texto = titulo_capa_de(texto, 3)
        ajustado = ajustar_texto(texto, estilo, perfil.zonas.titulo, tamanho_max=170,
                                 tamanho_min=TAMANHO_MIN_LEGIVEL, max_linhas=2)
    imagem = fundo.convert("RGBA")
    camada = Image.new("RGBA", imagem.size, (0, 0, 0, 0))
    desenhar_texto(camada, ajustado, estilo)
    imagem = Image.alpha_composite(imagem, camada).convert("RGB")
    imagem.save(destino, quality=95)
    frame_path.unlink(missing_ok=True)
    return {"caminho": str(destino), "titulo": texto, "tamanho_fonte": ajustado.tamanho,
            "legivel_miniatura": ajustado.cabe and ajustado.tamanho >= TAMANHO_MIN_LEGIVEL,
            "tempo_fonte": tempo_fonte}


def candidatos_como_dicts(candidatos: list[FrameCandidato]) -> list[dict]:
    return [asdict(c) for c in candidatos]
