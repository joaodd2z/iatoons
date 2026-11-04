"""QUALITY GATE do Modo Agência.

Pré-renderização: HOOK, INTRO, TÍTULO, LEGENDAS, SINCRONIZAÇÃO, CONTINUIDADE,
DUPLICAÇÃO e FINAL.
Pós-renderização (frames reais do MP4): FIRST FRAME, IMAGEM e EXPORTAÇÃO.
Um código de saída 0 do FFmpeg nunca é considerado aprovação suficiente.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import subprocess

from PIL import Image, ImageChops, ImageStat

from modules import preset as P
from modules.agencia.editorial import SALTO_MAXIMO_HISTORIA, PlanoAgencia, sobreposicao_relativa
from modules.agencia.layout import TextoAjustado
from modules.agencia.timeline import TimelineAgencia, _dentro_de_palavra, palavras_entre
from modules.intro import IntervaloIntro
from modules.legendas import BlocoLegenda
from modules.perfis_visuais import PerfilVisual, Zona
from modules.transcricao import Transcricao


TEMPOS_PRIMEIRO_FRAME = (0.0, 0.1, 0.25, 0.5, 1.0)


@dataclass
class ItemGate:
    nome: str
    ok: bool
    detalhe: str = ""
    corrigido: bool = False
    bloqueante: bool = True


def aprovado(itens: list[ItemGate]) -> bool:
    return all(item.ok or not item.bloqueante for item in itens)


def falhas(itens: list[ItemGate]) -> list[ItemGate]:
    return [item for item in itens if not item.ok and item.bloqueante]


def como_dicts(itens: list[ItemGate]) -> list[dict]:
    return [asdict(item) for item in itens]


# ─── Pré-renderização ────────────────────────────────────────────────────────

from modules.performance import medir_operacao


@medir_operacao("quality_gate_editorial")
def gate_pre(plano: PlanoAgencia, timeline: TimelineAgencia, titulo: TextoAjustado,
             legendas: list[tuple[BlocoLegenda, TextoAjustado]], transcricao: Transcricao,
             perfil: PerfilVisual, intro: IntervaloIntro | None = None,
             outros_planos: list[PlanoAgencia] = ()) -> list[ItemGate]:
    itens: list[ItemGate] = []
    zonas = perfil.zonas

    # HOOK: informação sonora imediata (ou gancho visual declarado).
    primeira_fala = None
    for trecho, offset in zip(timeline.trechos, timeline.offsets()):
        palavras = palavras_entre(transcricao, trecho.inicio, trecho.fim)
        if palavras:
            primeira_fala = offset + max(0.0, palavras[0].inicio - trecho.inicio)
            break
    if plano.gancho_visual:
        itens.append(ItemGate("HOOK", True, "Gancho visual declarado; vídeo presente desde t=0."))
    elif primeira_fala is None:
        itens.append(ItemGate("HOOK", False, "Nenhuma fala no corte."))
    else:
        itens.append(ItemGate("HOOK", primeira_fala <= 1.5,
                              f"Primeira fala em {primeira_fala:.2f}s (máx. 1,5s)."))

    # INTRO
    if intro and intro.ativo:
        invade = [t for t in timeline.trechos if min(t.fim, intro.fim) - max(t.inicio, intro.inicio) > 0.05]
        itens.append(ItemGate("INTRO", not invade,
                              "Intro ausente do corte." if not invade else
                              f"{len(invade)} trecho(s) atravessam a intro {intro.inicio:.1f}–{intro.fim:.1f}s."))
    else:
        itens.append(ItemGate("INTRO", True, "Nenhuma intro ativa para este episódio."))

    # TÍTULO
    dentro_topo = zonas.topo.contem(titulo.caixa) and zonas.titulo.contem(titulo.caixa)
    itens.append(ItemGate("TITULO", titulo.cabe and dentro_topo,
                          f"{len(titulo.linhas)} linha(s), {titulo.tamanho}px, caixa {titulo.caixa}."
                          if titulo.cabe and dentro_topo else
                          f"Título ultrapassa a safe zone: {'; '.join(titulo.avisos) or titulo.caixa}"))

    # LEGENDAS
    estouradas = [b for b, a in legendas if not (a.cabe and zonas.legenda.contem(a.caixa)
                                                 and zonas.base.contem(a.caixa))]
    itens.append(ItemGate("LEGENDAS", not estouradas,
                          f"{len(legendas)} blocos dentro da área inferior." if not estouradas else
                          f"{len(estouradas)} bloco(s) ultrapassam a área: “{estouradas[0].texto[:40]}”"))

    # SINCRONIZAÇÃO: tempos válidos, ordenados, sem sobreposição e ancorados em fala real.
    problemas = []
    duracao = timeline.duracao
    anteriores = 0.0
    for bloco, _ in legendas:
        if bloco.inicio < -0.001 or bloco.fim > duracao + 0.02 or bloco.fim <= bloco.inicio:
            problemas.append(f"bloco fora da timeline ({bloco.inicio:.2f}–{bloco.fim:.2f}s)")
            break
        if bloco.fim - bloco.inicio > 7.0:
            problemas.append(f"legenda exposta por {bloco.fim - bloco.inicio:.1f}s em {bloco.inicio:.2f}s")
            break
        if bloco.inicio < anteriores - 0.001:
            problemas.append(f"blocos sobrepostos em {bloco.inicio:.2f}s")
            break
        anteriores = bloco.fim
        fonte = timeline.saida_para_fonte((bloco.inicio + bloco.fim) / 2)
        if not palavras_entre(transcricao, fonte - 0.6, fonte + 0.6):
            problemas.append(f"legenda sem fala correspondente em {bloco.inicio:.2f}s")
            break
    if abs(sum(t.duracao for t in timeline.trechos) - sum(t.duracao_transicao for t in timeline.trechos)
           - duracao) > 0.001:
        problemas.append("duração da timeline inconsistente")
    itens.append(ItemGate("SINCRONIZACAO", not problemas,
                          "Áudio, legendas e timeline consistentes." if not problemas else problemas[0]))

    # REMIX: o Modo Agência só exporta remix real, comprovado pela timeline montada.
    from modules.agencia.remix import validar_remix_real
    problemas_remix = validar_remix_real(plano, timeline, transcricao, [b for b, _ in legendas], intro)
    itens.append(ItemGate(
        "REMIX", not problemas_remix,
        (f"Remix real: {len(timeline.trechos)} trechos, gancho {timeline.gancho[0]:.1f}–"
         f"{timeline.gancho[1]:.1f}s antecipado, contexto anterior, legendas recalculadas."
         if not problemas_remix else "Não é remix real: " + "; ".join(problemas_remix)),
    ))

    # CONTINUIDADE: somente uma história; remix não junta situações sem relação.
    historia = [t for t in timeline.trechos if t.funcao != "gancho"]
    saltos = [b.inicio - a.fim for a, b in zip(historia, historia[1:])]
    fora = [t for t in timeline.trechos if t.inicio < plano.inicio - 0.2 or t.fim > plano.fim + 0.2]
    ok = not fora and all(s <= SALTO_MAXIMO_HISTORIA for s in saltos) and all(s > -0.05 for s in saltos)
    if timeline.gancho:
        gi, gf = timeline.gancho
        ok = ok and plano.inicio - 0.2 <= gi and gf <= plano.fim + 0.2
    itens.append(ItemGate("CONTINUIDADE", ok,
                          f"{timeline.tipo}: {len(timeline.trechos)} trecho(s) da mesma história." if ok else
                          "Trechos fora da história ou saltos incompatíveis com uma única situação."))

    # DUPLICAÇÃO
    duplicado = next((o for o in outros_planos if o.id != plano.id and sobreposicao_relativa(plano, o) > 0.6), None)
    itens.append(ItemGate("DUPLICACAO", duplicado is None,
                          "Corte distinto dos demais candidatos." if duplicado is None else
                          f"Praticamente o mesmo corte que “{duplicado.titulo}”."))

    # FINAL: não termina no meio de fala nem antes da consequência.
    ultimo = historia[-1] if historia else timeline.trechos[-1]
    palavras_limite = palavras_entre(transcricao, ultimo.fim - 1.0, ultimo.fim + 1.0)
    no_meio = _dentro_de_palavra(ultimo.fim, palavras_limite)
    fim_consequencia = max([float(plano.estrutura[c]["fim"]) for c in ("reacao", "conclusao")
                            if c in plano.estrutura] or [0.0])
    antes_consequencia = fim_consequencia > ultimo.fim + 0.3
    itens.append(ItemGate("FINAL", not no_meio and not antes_consequencia,
                          "Termina após a fala e a consequência." if not (no_meio or antes_consequencia) else
                          ("Termina no meio de uma palavra." if no_meio else
                           f"Termina antes da consequência ({fim_consequencia:.1f}s).")))
    return itens


# ─── Pós-renderização ────────────────────────────────────────────────────────

@medir_operacao("extracao_frame", backend="ffmpeg")
def extrair_frame(caminho_video: str | Path, tempo: float, destino: str | Path) -> Path:
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-nostdin", "-y", "-loglevel", "error"]
    if tempo > 0:
        cmd += ["-ss", f"{tempo:.3f}"]
    cmd += ["-i", str(caminho_video), "-frames:v", "1", str(destino)]
    resultado = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, timeout=60)
    if resultado.returncode != 0 or not destino.exists():
        raise RuntimeError(f"Não foi possível extrair o frame em {tempo:.3f}s.")
    return destino


def _fundo_referencia(perfil: PerfilVisual) -> Image.Image:
    fundo = Image.open(perfil.fundo_path).convert("RGB")
    if fundo.size != (P.SAIDA_LARGURA, P.SAIDA_ALTURA):
        from modules.agencia.renderer import _cobrir
        fundo = _cobrir(fundo, P.SAIDA_LARGURA, P.SAIDA_ALTURA)
    return fundo


def _recorte(imagem: Image.Image, zona: Zona) -> Image.Image:
    return imagem.crop((zona.x, zona.y, zona.x2, zona.y2))


def _diferenca(a: Image.Image, b: Image.Image) -> float:
    if a.size != b.size:
        b = b.resize(a.size)
    return sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3


def analisar_zona_video(frame: Image.Image, fundo: Image.Image, zona: Zona) -> dict:
    area = _recorte(frame.convert("RGB"), zona).resize((267, 236))
    area_fundo = _recorte(fundo, zona).resize((267, 236))
    luma = ImageStat.Stat(area.convert("L"))
    return {
        "diferenca_fundo": round(_diferenca(area, area_fundo), 3),
        "luma_media": round(luma.mean[0], 3),
        "luma_desvio": round(luma.stddev[0], 3),
    }


def classificar_zona(metricas: dict) -> str | None:
    """None quando a zona tem conteúdo; caso contrário o problema encontrado."""
    if metricas["diferenca_fundo"] < 6.0:
        return "somente fundo na zona de vídeo"
    if metricas["luma_desvio"] < 2.5:
        return "zona de vídeo vazia/uniforme" if metricas["luma_media"] >= 10 else "frame preto"
    return None


@medir_operacao("quality_gate_frames")
def validar_frames(caminho_saida: str | Path, perfil: PerfilVisual,
                   tempos=TEMPOS_PRIMEIRO_FRAME, video_fonte: str | Path | None = None,
                   timeline: TimelineAgencia | None = None,
                   pasta_frames: str | Path | None = None) -> tuple[list[ItemGate], list[dict]]:
    """FIRST FRAME + IMAGEM com frames reais do arquivo exportado."""
    pasta = Path(pasta_frames or (P.TEMP_DIR / "agencia_frames" / Path(caminho_saida).stem))
    fundo = _fundo_referencia(perfil)
    zona = perfil.zonas.video
    detalhes, zonas_img = [], []
    for tempo in tempos:
        caminho = extrair_frame(caminho_saida, tempo, pasta / f"saida_{int(round(tempo * 1000)):05d}ms.png")
        with Image.open(caminho) as raw_frame:
            frame = raw_frame.convert("RGB")
        metricas = analisar_zona_video(frame, fundo, zona)
        metricas.update({"tempo": tempo, "arquivo": str(caminho), "problema": classificar_zona(metricas),
                         "tamanho": list(frame.size)})
        detalhes.append(metricas)
        zonas_img.append(_recorte(frame, zona).resize((267, 236)).convert("L"))
        del frame

    primeiros = [d for d in detalhes if d["tempo"] <= 1.0]
    problema_inicio = next((d for d in primeiros if d["problema"]), None)
    itens = [ItemGate(
        "FIRST_FRAME", problema_inicio is None and bool(primeiros) and primeiros[0]["tempo"] == 0.0,
        "Vídeo principal visível e composto desde t=0." if problema_inicio is None else
        f"{problema_inicio['problema']} em {problema_inicio['tempo'] * 1000:.0f} ms.",
    )]
    problema_geral = next((d for d in detalhes if d["problema"]), None)
    congelado = ""
    if len(zonas_img) >= 3:
        variacao_saida = max(_diferenca(a, b) for a, b in zip(zonas_img, zonas_img[1:]))
        if variacao_saida < 0.4 and video_fonte and timeline:
            fontes = []
            for tempo in tempos:
                origem = timeline.saida_para_fonte(tempo)
                caminho = extrair_frame(video_fonte, origem, pasta / f"fonte_{int(round(tempo * 1000)):05d}ms.png")
                fontes.append(Image.open(caminho).convert("L").resize((267, 150)))
            variacao_fonte = max(_diferenca(a, b) for a, b in zip(fontes, fontes[1:]))
            if variacao_fonte > 1.5:
                congelado = (f"zona congelada (variação {variacao_saida:.2f}) enquanto a fonte "
                             f"muda (variação {variacao_fonte:.2f})")
    itens.append(ItemGate(
        "IMAGEM", problema_geral is None and not congelado,
        "Sem frame preto, região vazia ou congelamento indevido." if problema_geral is None and not congelado
        else (congelado or f"{problema_geral['problema']} em {problema_geral['tempo']:.2f}s"),
    ))
    return itens, detalhes


@medir_operacao("ffprobe_saida", backend="ffprobe")
def relatorio_exportacao(caminho: str | Path) -> dict:
    resultado = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(caminho)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    if resultado.returncode != 0:
        raise RuntimeError("Não foi possível ler o arquivo exportado.")
    dados = json.loads(resultado.stdout)
    video = next(s for s in dados["streams"] if s.get("codec_type") == "video")
    audio = next((s for s in dados["streams"] if s.get("codec_type") == "audio"), {})
    formato = dados.get("format", {})
    num, _, den = str(video.get("avg_frame_rate", "0/1")).partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 0.0
    tamanho = Path(caminho).stat().st_size
    duracao = float(formato.get("duration") or video.get("duration") or 0)
    return {
        "resolucao": f"{video.get('width')}x{video.get('height')}",
        "largura": int(video.get("width", 0)),
        "altura": int(video.get("height", 0)),
        "fps": round(fps, 3),
        "codec_video": video.get("codec_name", ""),
        "perfil_h264": video.get("profile", ""),
        "pix_fmt": video.get("pix_fmt", ""),
        "codec_audio": audio.get("codec_name", ""),
        "taxa_audio": int(audio.get("sample_rate", 0) or 0),
        "duracao": round(duracao, 3),
        "duracao_video": round(float(video.get("duration") or 0), 3),
        "duracao_audio": round(float(audio.get("duration") or 0), 3),
        "inicio_video": float(video.get("start_time") or 0),
        "inicio_audio": float(audio.get("start_time") or 0),
        "bitrate_medio_kbps": round(float(formato.get("bit_rate") or (tamanho * 8 / max(duracao, 0.001))) / 1000, 1),
        "bitrate_video_kbps": round(float(video.get("bit_rate") or 0) / 1000, 1),
        "tamanho_bytes": tamanho,
        "tamanho_mb": round(tamanho / 1_048_576, 2),
        "faststart": _moov_no_inicio(caminho),
    }


def _moov_no_inicio(caminho: str | Path) -> bool:
    try:
        with open(caminho, "rb") as arquivo:
            cabecalho = arquivo.read(64 * 1024)
        moov, mdat = cabecalho.find(b"moov"), cabecalho.find(b"mdat")
        return moov != -1 and (mdat == -1 or moov < mdat)
    except OSError:
        return False


@medir_operacao("quality_gate_exportacao")
def validar_exportacao(relatorio: dict, duracao_esperada: float, fps: int = 60) -> list[ItemGate]:
    problemas = []
    if (relatorio["largura"], relatorio["altura"]) != (P.SAIDA_LARGURA, P.SAIDA_ALTURA):
        problemas.append(f"resolução {relatorio['resolucao']}")
    if abs(relatorio["fps"] - fps) > 0.05:
        problemas.append(f"FPS {relatorio['fps']}")
    if relatorio["codec_video"] != "h264" or relatorio["pix_fmt"] not in ("yuv420p", "yuvj420p"):
        problemas.append(f"vídeo {relatorio['codec_video']}/{relatorio['pix_fmt']}")
    if relatorio["codec_audio"] != "aac":
        problemas.append(f"áudio {relatorio['codec_audio'] or 'ausente'}")
    if not relatorio["faststart"]:
        problemas.append("faststart ausente")
    itens = [ItemGate("EXPORTACAO", not problemas,
                      "1080x1920 · 60 FPS · H.264 · AAC · yuv420p · faststart" if not problemas
                      else "; ".join(problemas))]
    sync = []
    if abs(relatorio["inicio_video"]) > 0.05 or (relatorio["inicio_audio"] and abs(relatorio["inicio_audio"]) > 0.08):
        sync.append(f"início v/a {relatorio['inicio_video']}/{relatorio['inicio_audio']}")
    if abs(relatorio["duracao_video"] - duracao_esperada) > 0.20:
        sync.append(f"duração do vídeo {relatorio['duracao_video']}s ≠ {duracao_esperada:.2f}s")
    if relatorio["duracao_audio"] and abs(relatorio["duracao_audio"] - relatorio["duracao_video"]) > 0.20:
        sync.append(f"áudio {relatorio['duracao_audio']}s × vídeo {relatorio['duracao_video']}s")
    itens.append(ItemGate("SINCRONIZACAO_AV", not sync,
                          "Áudio e vídeo começam em 0 e têm a mesma duração." if not sync else "; ".join(sync)))
    return itens
