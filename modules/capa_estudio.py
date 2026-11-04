"""Estúdio de Capa: capa no padrão da agência a partir de um corte JÁ pronto (o .mp4 final 1080x1920).

Pega um quadro do corte, recorta só a área do vídeo (sem título nem legenda queimados), cola no fundo do
perfil da conta e escreve o texto da capa com o estilo do modelo. O modelo fica salvo por conta em
dados/capas/<conta>.json, então a fábrica gera as capas do dia inteiro sozinha e sempre igual.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import json
from pathlib import Path
import subprocess

from PIL import Image, ImageColor, ImageDraw

from modules import preset as P
from modules.agencia.capa import _pontuar
from modules.agencia.editorial import titulo_capa_de
from modules.agencia.layout import _fonte, ajustar_texto
from modules.perfis_visuais import EstiloTexto, PerfilVisual, Zona, obter_perfil

CAPAS_DIR: Path = P.DADOS_DIR / "capas"
POSICOES = {"topo": "No topo (padrão)", "meio": "No meio do vídeo", "base": "Embaixo do vídeo"}


@dataclass
class ModeloCapa:
    """Regras da capa de uma conta. Tudo editável pela tela; o padrão imita o TOONMIX."""
    posicao: str = "topo"                # topo | meio | base
    max_palavras: int = 0                # 0 = título inteiro (como nas capas do TOONMIX); >0 encurta para a miniatura
    caixa_alta: bool = True
    cor: str = "#FFE600"
    cor_contorno: str = "#000000"
    contorno: int = 9
    tamanho_max: int = 150
    faixa: bool = False                  # tarja atrás do texto (ajuda quando o fundo é claro)
    cor_faixa: str = "#000000"
    selo: str = ""                       # ex.: "PARTE 1", "NOVO", "EP 5"; vazio = sem selo
    cor_selo: str = "#E11D48"
    momento: float = -1.0                # 0..1 = ponto fixo do vídeo; -1 = escolhe o quadro mais forte sozinho

    @classmethod
    def de_dict(cls, d: dict | None) -> "ModeloCapa":
        validos = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (d or {}).items() if k in validos})


def caminho_modelo(conta_id: str) -> Path:
    return CAPAS_DIR / f"{conta_id}.json"


def carregar_modelo(conta_id: str) -> ModeloCapa:
    arq = caminho_modelo(conta_id)
    try:
        return ModeloCapa.de_dict(json.loads(arq.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return ModeloCapa()


def salvar_modelo(conta_id: str, modelo: ModeloCapa) -> Path:
    CAPAS_DIR.mkdir(parents=True, exist_ok=True)
    arq = caminho_modelo(conta_id)
    arq.write_text(json.dumps(asdict(modelo), ensure_ascii=False, indent=2), encoding="utf-8")
    return arq


def tem_modelo(conta_id: str) -> bool:
    return caminho_modelo(conta_id).is_file()


# ─── Quadros do corte ─────────────────────────────────────────────────────────

def duracao(video: str | Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
                       capture_output=True, text=True, timeout=30)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def quadro(video: str | Path, segundos: float) -> Image.Image:
    """Um quadro do corte em 1080x1920 (mesmo se o arquivo vier em outra resolução)."""
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, segundos):.3f}", "-i", str(video),
                        "-frames:v", "1", "-vf", f"scale={P.SAIDA_LARGURA}:{P.SAIDA_ALTURA}",
                        "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True, timeout=60)
    if r.returncode or not r.stdout:
        raise RuntimeError(f"Não consegui ler um quadro de {Path(video).name}.")
    from io import BytesIO
    return Image.open(BytesIO(r.stdout)).convert("RGB")


def melhor_momento(video: str | Path, perfil: PerfilVisual, amostras: int = 8) -> float:
    """Testa alguns pontos do corte (fora do começo e do fim) e devolve o segundo do quadro mais forte."""
    total = duracao(video)
    if total <= 0:
        return 0.0
    z = perfil.zonas.video
    melhor, melhor_t = -1.0, total * 0.3
    for i in range(amostras):
        t = total * (0.12 + 0.76 * i / max(1, amostras - 1))
        try:
            area = quadro(video, t).crop((z.x, z.y, z.x2, z.y2))
        except RuntimeError:
            continue
        s = _pontuar(area)
        nota = 0.45 * s["nitidez"] + 0.25 * s["saturacao"] + 0.2 * s["centro"] - 0.3 * s["exposicao"]
        if nota > melhor:
            melhor, melhor_t = nota, t
    return melhor_t


# ─── Composição ───────────────────────────────────────────────────────────────

def _rgb(cor: str, padrao=(0, 0, 0)):
    try:
        return ImageColor.getrgb(cor)
    except (ValueError, AttributeError):
        return padrao


def _zona_texto(perfil: PerfilVisual, posicao: str) -> Zona:
    z = perfil.zonas
    if posicao == "meio":
        return Zona(z.video.x + 40, z.video.y + z.video.h // 2 - 170, z.video.w - 80, 340)
    if posicao == "base":
        return Zona(z.legenda.x - 40, z.base.y + 24, z.legenda.w + 80, 300)
    return z.titulo


def compor(frame: Image.Image, perfil: PerfilVisual, modelo: ModeloCapa, texto: str) -> Image.Image:
    """Fundo do perfil + área do vídeo tirada do quadro + texto e selo do modelo. Devolve 1080x1920 RGB."""
    from modules.agencia.renderer import _cobrir
    fundo = _cobrir(Image.open(perfil.fundo_path).convert("RGB"), P.SAIDA_LARGURA, P.SAIDA_ALTURA)
    zv = perfil.zonas.video
    fundo.paste(frame.crop((zv.x, zv.y, zv.x2, zv.y2)), (zv.x, zv.y))
    img = fundo.convert("RGBA")
    camada = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(camada)

    texto = " ".join(str(texto or "").split())
    if int(modelo.max_palavras) > 0:
        texto = titulo_capa_de(texto, int(modelo.max_palavras))
    zona = _zona_texto(perfil, modelo.posicao)
    estilo = EstiloTexto(cor=modelo.cor, cor_contorno=modelo.cor_contorno, espessura_contorno=int(modelo.contorno),
                         caixa_alta=bool(modelo.caixa_alta), tamanho=int(modelo.tamanho_max),
                         tamanho_min=50, max_linhas=4, sombra_y=6)
    aj = ajustar_texto(texto, estilo, zona, tamanho_max=int(modelo.tamanho_max), tamanho_min=50, max_linhas=4)
    if not aj.cabe:   # título enorme: encurta só o necessário em vez de sair cortado da tela
        for n in (12, 9, 6, 4):
            aj = ajustar_texto(titulo_capa_de(texto, n), estilo, zona, tamanho_max=int(modelo.tamanho_max),
                               tamanho_min=50, max_linhas=4)
            if aj.cabe:
                break
    if aj.linhas:
        if modelo.faixa:
            c = aj.caixa
            d.rounded_rectangle((c.x - 28, c.y - 18, c.x2 + 28, c.y2 + 14), radius=26,
                                fill=(*_rgb(modelo.cor_faixa), 215))
        fonte = _fonte(str(P.FONTE_PATH), aj.tamanho)
        for i, linha in enumerate(aj.linhas):
            pos = (aj.x_centro, aj.y_linha(i))
            d.text((pos[0], pos[1] + 6), linha, font=fonte, fill=(0, 0, 0, 150), anchor="ma",
                   stroke_width=aj.borda, stroke_fill=(0, 0, 0, 150))
            d.text(pos, linha, font=fonte, fill=_rgb(modelo.cor, (255, 230, 0)), anchor="ma",
                   stroke_width=aj.borda, stroke_fill=_rgb(modelo.cor_contorno))
    if modelo.selo.strip():
        fs = _fonte(str(P.FONTE_PATH), 74)
        txt = modelo.selo.strip().upper()
        w = int(d.textlength(txt, font=fs))
        x0, y0 = zv.x2 - w - 90, zv.y + 34
        d.rounded_rectangle((x0, y0, x0 + w + 56, y0 + 104), radius=22, fill=_rgb(modelo.cor_selo, (225, 29, 72)),
                            outline=(255, 255, 255), width=5)
        d.text((x0 + 28, y0 + 14), txt, font=fs, fill=(255, 255, 255), stroke_width=3, stroke_fill=(0, 0, 0))
    return Image.alpha_composite(img, camada).convert("RGB")


def gerar_capa(video: str | Path, perfil: str | PerfilVisual | None, modelo: ModeloCapa, texto: str,
               destino: str | Path, segundos: float | None = None) -> Path:
    perfil = obter_perfil(perfil)
    if segundos is None:
        segundos = (modelo.momento * duracao(video)) if modelo.momento >= 0 else melhor_momento(video, perfil)
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    compor(quadro(video, segundos), perfil, modelo, texto).save(destino, quality=93)
    return destino


def titulo_do_post(mp4: Path) -> str:
    """Título do corte: 1ª linha do .txt da agenda ou, sem ele, o nome do arquivo sem número e horário."""
    txt = mp4.with_suffix(".txt")
    if txt.is_file():
        primeira = txt.read_text(encoding="utf-8").strip().splitlines()
        if primeira and primeira[0].strip():
            return primeira[0].strip()
    partes = mp4.stem.split("_", 2)
    return (partes[2] if len(partes) == 3 and partes[0].isdigit() else mp4.stem).replace("-", " ")


def aplicar_em_pasta(pasta: str | Path, perfil: str | PerfilVisual | None, modelo: ModeloCapa,
                     refazer: bool = True, progresso=None) -> list[Path]:
    """Gera <video>_capa.jpg para todo .mp4 da pasta (ex.: PARA_POSTAR/<conta>/<data>)."""
    videos = sorted(p for p in Path(pasta).glob("*.mp4"))
    feitas = []
    for i, mp4 in enumerate(videos, 1):
        destino = mp4.with_name(mp4.stem + "_capa.jpg")
        if destino.exists() and not refazer:
            continue
        gerar_capa(mp4, perfil, modelo, titulo_do_post(mp4), destino)
        feitas.append(destino)
        if progresso:
            progresso(i, len(videos), mp4.name)
    return feitas
