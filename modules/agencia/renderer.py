"""Renderização FFmpeg do Modo Agência (1080×1920, 60 FPS, H.264/AAC).

Arquitetura anti "primeiro frame só com fundo":
o relógio da composição é o PRÓPRIO vídeo. A zona de vídeo recebe ``pad`` até
1080×1920 e o fundo do perfil — com um furo transparente exatamente na
VIDEO_ZONE — é sobreposto por cima. Assim, todo frame de saída nasce de um frame
do conteúdo principal; não existe frame composto apenas por fundo.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Callable

from PIL import Image

from modules import preset as P
from modules.agencia.av import (
    TAG_PROCESSADO, AjustesAgencia, filtro_audio_agencia, filtro_pos_escala, filtro_pre_escala,
    flags_escala,
)
from modules.agencia.layout import Enquadramento, TextoAjustado
from modules.agencia.timeline import TimelineAgencia
from modules.legendas import BlocoLegenda
from modules.perfis_visuais import (
    ESTILO_LEGENDA_AGENCIA_PADRAO,
    ESTILO_TITULO_AGENCIA_PADRAO,
    FUNDO_TOONMIX_PATH,
    PERFIL_TOONMIX,
    ZONAS_TOONMIX,
    EstiloTexto,
    PerfilVisual,
)
from modules.renderer import _escapar_texto_ffmpeg


FUNDOS_CACHE_DIR = P.TEMP_DIR / "agencia_fundos"
MARGEM_SEEK = 2.0


@dataclass
class InfoFonte:
    largura: int
    altura: int
    fps: float
    duracao: float
    tem_audio: bool


from modules.performance import medir_operacao, anotar


@medir_operacao("ffprobe", backend="ffprobe")
def inspecionar_fonte(caminho: str | Path) -> InfoFonte:
    resultado = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(caminho)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    if resultado.returncode != 0:
        raise RuntimeError(f"Não foi possível ler o vídeo: {caminho}")
    dados = json.loads(resultado.stdout)
    video = next((s for s in dados.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video:
        raise RuntimeError("Arquivo sem faixa de vídeo.")
    largura, altura = int(video["width"]), int(video["height"])
    rotacao = 0
    for lado in video.get("side_data_list", []) or []:
        if "rotation" in lado:
            rotacao = abs(int(float(lado["rotation"]))) % 180
    if rotacao == 90:
        largura, altura = altura, largura
    num, _, den = str(video.get("avg_frame_rate") or "0/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 0.0
    duracao = float(video.get("duration") or dados.get("format", {}).get("duration") or 0)
    tem_audio = any(s.get("codec_type") == "audio" for s in dados.get("streams", []))
    return InfoFonte(largura, altura, fps, duracao, tem_audio)


def fundo_com_furo(perfil: PerfilVisual | None = None) -> Path:
    """PNG 1080×1920 do fundo fixo TOONMIX com a VIDEO_ZONE transparente (cache por arquivo/zona)."""
    zona = ZONAS_TOONMIX.video
    caminho_fundo = FUNDO_TOONMIX_PATH
    stat = caminho_fundo.stat()
    chave = hashlib.sha256(
        f"{caminho_fundo.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{zona}".encode()
    ).hexdigest()[:20]
    destino = FUNDOS_CACHE_DIR / f"toonmix_{chave}.png"
    if destino.exists():
        return destino
    FUNDOS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    imagem = Image.open(caminho_fundo).convert("RGBA")
    imagem = _cobrir(imagem, P.SAIDA_LARGURA, P.SAIDA_ALTURA)
    furo = Image.new("RGBA", (zona.w, zona.h), (0, 0, 0, 0))
    imagem.paste(furo, (zona.x, zona.y))
    tmp = destino.with_suffix(".tmp.png")
    imagem.save(tmp)
    tmp.replace(destino)
    return destino


def _cobrir(imagem: Image.Image, largura: int, altura: int) -> Image.Image:
    escala = max(largura / imagem.width, altura / imagem.height)
    nova = imagem.resize((max(largura, round(imagem.width * escala)),
                          max(altura, round(imagem.height * escala))), Image.Resampling.LANCZOS)
    x, y = (nova.width - largura) // 2, (nova.height - altura) // 2
    return nova.crop((x, y, x + largura, y + altura))


def _fonte_ffmpeg() -> str:
    # Mesmo escape do renderer legado (dois níveis: filtergraph e opção do filtro).
    return str(P.FONTE_PATH).replace("\\", "/").replace(":", "\\\\:").replace(" ", "\\\\ ")


def _drawtext(linha: str, tamanho: int, estilo: EstiloTexto, x_centro: int, y: int,
              habilitar: str | None = None) -> str:
    partes = [
        f"fontfile={_fonte_ffmpeg()}",
        f"text='{_escapar_texto_ffmpeg(linha)}'",
        f"fontsize={tamanho}",
        f"fontcolor={estilo.cor}",
        f"borderw={estilo.espessura_contorno}",
        f"bordercolor={estilo.cor_contorno}",
        "expansion=none",
        "y_align=font",
        f"x={x_centro}-text_w/2",
        f"y={y}",
    ]
    if estilo.sombra_x or estilo.sombra_y:
        partes += [f"shadowx={estilo.sombra_x}", f"shadowy={estilo.sombra_y}",
                   f"shadowcolor={estilo.cor_sombra}"]
    if habilitar:
        partes.append(f"enable='{habilitar}'")
    return "drawtext=" + ":".join(partes)


def filtros_titulo(titulo: TextoAjustado, estilo: EstiloTexto) -> list[str]:
    return [_drawtext(linha, titulo.tamanho, estilo, titulo.x_centro, titulo.y_linha(i))
            for i, linha in enumerate(titulo.linhas)]


def filtros_legendas(legendas: list[tuple[BlocoLegenda, TextoAjustado]], estilo: EstiloTexto) -> list[str]:
    filtros = []
    for bloco, ajuste in legendas:
        janela = f"between(t,{bloco.inicio:.3f},{max(bloco.inicio, bloco.fim - 0.001):.3f})"
        for i, linha in enumerate(ajuste.linhas):
            filtros.append(_drawtext(linha, ajuste.tamanho, estilo, ajuste.x_centro,
                                     ajuste.y_linha(i), janela))
    return filtros


def construir_grafo(
    timeline: TimelineAgencia, info: InfoFonte, enquadramento: Enquadramento,
    perfil: PerfilVisual, ajustes: AjustesAgencia, titulo: TextoAjustado | None,
    legendas: list[tuple[BlocoLegenda, TextoAjustado]], aplicar_tratamento: bool = True,
    modo_seguro: bool = False,
) -> tuple[list[str], str]:
    """Retorna (argumentos de entrada, filtergraph)."""
    perfil = PERFIL_TOONMIX
    fps = timeline.fps
    zona = ZONAS_TOONMIX.video
    entradas: list[str] = []
    grafo: list[str] = []
    n = len(timeline.trechos)
    margem = 4.0 if modo_seguro else MARGEM_SEEK
    for i, trecho in enumerate(timeline.trechos):
        inicio_seek = max(0.0, trecho.inicio - margem)
        deslocamento = trecho.inicio - inicio_seek
        entradas += ["-ss", f"{inicio_seek:.3f}", "-t", f"{deslocamento + trecho.duracao + 0.5:.3f}",
                     "-i", "{VIDEO}"]
        origem_v, origem_a = f"[{i}:v]", f"[{i}:a]"
        grafo.append(
            f"{origem_v}trim=start={deslocamento:.4f}:duration={trecho.duracao:.4f},"
            f"setpts=PTS-STARTPTS,fps={fps},settb=1/{fps},scale={info.largura}:{info.altura},setsar=1,"
            f"format=yuv420p[v{i}]"
        )
        if info.tem_audio:
            grafo.append(
                f"{origem_a}atrim=start={deslocamento:.4f}:duration={trecho.duracao:.4f},"
                f"asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo[a{i}]"
            )
        else:
            grafo.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={trecho.duracao:.4f}[a{i}]")

    atual_v, atual_a, acumulado = "[v0]", "[a0]", timeline.trechos[0].duracao
    for i in range(1, n):
        trecho = timeline.trechos[i]
        saida_v, saida_a = f"[vc{i}]", f"[ac{i}]"
        if trecho.transicao == "dissolve" and trecho.duracao_transicao > 0:
            d = trecho.duracao_transicao
            grafo.append(f"{atual_v}[v{i}]xfade=transition=fade:duration={d:.4f}:"
                         f"offset={acumulado - d:.4f}{saida_v}")
            grafo.append(f"{atual_a}[a{i}]acrossfade=d={d:.4f}:c1=tri:c2=tri{saida_a}")
            acumulado += trecho.duracao - d
        else:
            # concat altera a base de tempo; o xfade seguinte exige 1/fps nas duas entradas.
            grafo.append(f"{atual_v}[v{i}]concat=n=2:v=1:a=0,settb=1/{fps}{saida_v}")
            grafo.append(f"{atual_a}[a{i}]concat=n=2:v=0:a=1{saida_a}")
            acumulado += trecho.duracao
        atual_v, atual_a = saida_v, saida_a

    pre = filtro_pre_escala(ajustes, aplicar_tratamento, info.largura, info.altura)
    pos = filtro_pos_escala(ajustes, aplicar_tratamento)
    e = enquadramento
    cadeia_fg = (f"scale={e.escala_w}:{e.escala_h}{flags_escala(ajustes)},"
                 f"crop={e.crop_w}:{e.crop_h}:{e.crop_x}:{e.crop_y}" + (f",{pos}" if pos else "") + ",setsar=1")
    base = atual_v + (f"{pre}," if pre else "")
    grafo.append(f"{base}{cadeia_fg}[zona]")

    grafo.append(
        f"[zona]pad={P.SAIDA_LARGURA}:{P.SAIDA_ALTURA}:{zona.x}:{zona.y}:color=black,"
        f"format=yuv420p[tela]"
    )
    indice_fundo = n
    grafo.append(f"[{indice_fundo}:v]format=rgba[moldura]")
    grafo.append("[tela][moldura]overlay=0:0:shortest=1:format=auto[comp]")
    textos = []
    if titulo:
        textos += filtros_titulo(titulo, ESTILO_TITULO_AGENCIA_PADRAO)
    textos += filtros_legendas(legendas, ESTILO_LEGENDA_AGENCIA_PADRAO)
    textos.append("format=yuv420p")
    grafo.append("[comp]" + ",".join(textos) + "[vout]")
    filtro_a = filtro_audio_agencia(ajustes)
    grafo.append(f"{atual_a}{filtro_a or 'anull'}[aout]")
    return entradas, ";\n".join(grafo)


@medir_operacao("filtergraph")
def construir_comando(
    caminho_video: str, caminho_saida: str, timeline: TimelineAgencia, info: InfoFonte,
    enquadramento: Enquadramento, perfil: PerfilVisual, ajustes: AjustesAgencia,
    titulo: TextoAjustado | None, legendas, caminho_grafo: Path,
    aplicar_tratamento: bool = True, modo_seguro: bool = False,
) -> list[str]:
    perfil = PERFIL_TOONMIX
    entradas, grafo = construir_grafo(timeline, info, enquadramento, perfil, ajustes, titulo,
                                      legendas, aplicar_tratamento, modo_seguro)
    caminho_grafo.parent.mkdir(parents=True, exist_ok=True)
    caminho_grafo.write_text(grafo, encoding="utf-8")
    entradas = [caminho_video if item == "{VIDEO}" else item for item in entradas]
    moldura = fundo_com_furo(PERFIL_TOONMIX)
    cmd = ["ffmpeg", "-nostdin", "-y", "-nostats", "-loglevel", "error", "-threads", "2"] + entradas + [
        "-loop", "1", "-framerate", str(timeline.fps), "-i", str(moldura),
        "-/filter_complex", str(caminho_grafo),
        "-map", "[vout]", "-map", "[aout]",
        "-t", f"{timeline.duracao:.4f}",
        "-c:v", "libx264", "-threads", "2", "-preset", ajustes.preset_x264, "-crf", str(ajustes.crf),
        "-profile:v", "high", "-pix_fmt", "yuv420p", "-r", str(timeline.fps),
        "-fps_mode", "cfr", "-g", str(timeline.fps * 2),
        "-c:a", "aac", "-b:a", f"{ajustes.audio_kbps}k", "-ar", "48000", "-ac", "2",
        "-metadata", f"comment={TAG_PROCESSADO};modo=agencia;av={ajustes.nome};perfil={perfil.id}",
        "-movflags", "+faststart",
        "-progress", "pipe:1",
        caminho_saida,
    ]
    return cmd


@medir_operacao("render", backend="ffmpeg")
def executar_ffmpeg(cmd: list[str], duracao: float, cancelada_ref=None,
                    progresso_cb: Callable[[float, str], None] | None = None) -> dict:
    anotar(entrada_segundos=duracao)
    for flag, nome in (("-c:v", "encoder"), ("-preset", "preset"), ("-crf", "crf"), ("-r", "fps")):
        if flag in cmd and cmd.index(flag) + 1 < len(cmd):
            anotar(**{nome: cmd[cmd.index(flag) + 1]})
    processo = None
    recentes = deque(maxlen=40)
    try:
        processo = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        for linha in processo.stdout:
            if cancelada_ref and cancelada_ref.cancelada:
                try:
                    processo.kill()
                except Exception:
                    pass
                return {"sucesso": False, "erro": "Renderização cancelada pelo usuário."}
            linha = linha.strip()
            recentes.append(linha)
            if linha.startswith("out_time_us=") and progresso_cb and duracao > 0:
                try:
                    segundos = int(linha.split("=", 1)[1]) / 1_000_000
                    progresso_cb(min(97.0, segundos / duracao * 100), f"Renderizando… {segundos:.1f}s/{duracao:.1f}s")
                except ValueError:
                    pass
    except FileNotFoundError:
        return {"sucesso": False, "erro": "ffmpeg não encontrado. Verifique o PATH."}
    except Exception as exc:
        if processo:
            try:
                processo.kill()
            except Exception:
                pass
        return {"sucesso": False, "erro": f"Erro na execução do FFmpeg: {exc}"}
    finally:
        if processo:
            try:
                if processo.stdout:
                    processo.stdout.close()
            except Exception:
                pass
            try:
                processo.wait(timeout=10)
            except TypeError:
                processo.wait()
            except Exception:
                try:
                    processo.kill()
                    try:
                        processo.wait(timeout=5)
                    except TypeError:
                        processo.wait()
                except Exception:
                    pass

    if processo.returncode != 0:
        erros = [l for l in recentes if l and "=" not in l[:24]]
        return {"sucesso": False, "erro": f"FFmpeg retornou {processo.returncode}: "
                                          f"{' | '.join(erros or list(recentes))[-900:]}"}
    return {"sucesso": True, "erro": ""}
