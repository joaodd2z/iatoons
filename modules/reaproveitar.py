"""Troca segura do fundo de vídeos finais, sem reanálise ou dupla filtragem."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
from collections import deque
from typing import Callable

from PIL import Image, ImageChops, ImageStat

from modules import preset as P
from modules.lote import caminho_sem_sobrescrever
from modules.perfis_visuais import PerfilVisual, obter_perfil


@dataclass
class VideoReaproveitavel:
    caminho: str
    largura: int = 0
    altura: int = 0
    fps: float = 0.0
    duracao: float = 0.0
    tem_audio: bool = False
    compativel: bool = False
    erro: str = ""
    status: str = "pendente"
    caminho_saida: str = ""

    @property
    def nome(self) -> str:
        return Path(self.caminho).name


def _racional(valor: str) -> float:
    try:
        numerador, denominador = valor.split("/", 1)
        return float(numerador) / float(denominador) if float(denominador) else 0.0
    except (AttributeError, ValueError, ZeroDivisionError):
        return 0.0


def inspecionar_video(caminho: str | Path) -> VideoReaproveitavel:
    caminho = str(Path(caminho).resolve())
    item = VideoReaproveitavel(caminho)
    cmd = [
        "ffprobe", "-v", "error", "-show_streams", "-show_format",
        "-of", "json", caminho,
    ]
    try:
        processo = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=30)
        if processo.returncode != 0:
            item.erro = "Não foi possível ler o vídeo."
            return item
        dados = json.loads(processo.stdout)
        video = next((s for s in dados.get("streams", []) if s.get("codec_type") == "video"), None)
        if not video:
            item.erro = "Arquivo sem faixa de vídeo."
            return item
        item.largura = int(video.get("width", 0))
        item.altura = int(video.get("height", 0))
        item.fps = _racional(video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1")
        item.duracao = float(video.get("duration") or dados.get("format", {}).get("duration") or 0)
        item.tem_audio = any(s.get("codec_type") == "audio" for s in dados.get("streams", []))
        if (item.largura, item.altura) != (P.SAIDA_LARGURA, P.SAIDA_ALTURA):
            item.erro = f"Resolução incompatível: {item.largura}×{item.altura}; esperado 1080×1920."
            return item
        if item.duracao <= 0 or item.fps <= 0:
            item.erro = "Duração ou FPS inválido."
            return item
        item.compativel = True
        return item
    except (OSError, ValueError, TypeError, json.JSONDecodeError, subprocess.TimeoutExpired) as erro:
        item.erro = f"Falha de validação: {erro}"
        return item


def listar_videos(pasta: str | Path) -> list[VideoReaproveitavel]:
    pasta = Path(pasta)
    if not pasta.is_dir():
        raise ValueError("Selecione uma pasta válida.")
    return [inspecionar_video(caminho) for caminho in sorted(pasta.glob("*.mp4"))]


def _fundos_origem_ia_toons() -> list[Path]:
    candidatos = [
        P.FUNDO_PATH,
        P.ASSETS_DIR / "fundo_toonmix.png",
    ]
    unicos = []
    for caminho in candidatos:
        if caminho.is_file() and caminho.resolve() not in [p.resolve() for p in unicos]:
            unicos.append(caminho)
    return unicos


def extrair_frame(caminho_video: str, caminho_saida: str | Path) -> Path:
    destino = Path(caminho_saida)
    destino.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-ss", "0.25", "-i", caminho_video,
           "-frames:v", "1", "-q:v", "2", str(destino)]
    resultado = subprocess.run(cmd, capture_output=True, timeout=30)
    if resultado.returncode != 0 or not destino.exists():
        raise RuntimeError("Não foi possível extrair um frame para detectar o fundo.")
    return destino


def detectar_fundo_origem(caminho_video: str, pasta_temp: str | Path | None = None) -> Path:
    """Escolhe a referência IA TOONS mais parecida usando somente regiões externas."""
    pasta = Path(pasta_temp or P.TEMP_DIR / "reaproveitar")
    frame_path = extrair_frame(caminho_video, pasta / f"detectar_{Path(caminho_video).stem}.jpg")
    frame = Image.open(frame_path).convert("RGB")
    if frame.size != (P.SAIDA_LARGURA, P.SAIDA_ALTURA):
        raise ValueError("O frame não possui canvas vertical 1080×1920.")
    # Evita a área central e reduz o peso das zonas típicas de título/legenda.
    caixas = [(0, 0, 180, 420), (900, 0, 1080, 420),
              (0, 1500, 240, 1920), (840, 1500, 1080, 1920)]
    melhores = []
    for fundo_path in _fundos_origem_ia_toons():
        fundo = Image.open(fundo_path).convert("RGB").resize(frame.size, Image.Resampling.LANCZOS)
        erro = 0.0
        for caixa in caixas:
            diff = ImageChops.difference(frame.crop(caixa), fundo.crop(caixa))
            erro += sum(ImageStat.Stat(diff).mean) / 3
        melhores.append((erro / len(caixas), fundo_path))
    if not melhores:
        raise FileNotFoundError("Nenhum fundo IA TOONS de referência está disponível.")
    return min(melhores, key=lambda item: item[0])[1]


def construir_comando(
    item: VideoReaproveitavel,
    destino: str | Path,
    perfil_destino: str | PerfilVisual,
    preservar_textos: bool = True,
    fundo_origem: str | Path | None = None,
) -> list[str]:
    if not item.compativel:
        raise ValueError(item.erro or "Vídeo incompatível.")
    perfil = obter_perfil(perfil_destino)
    destino = str(destino)
    cmd = ["ffmpeg", "-y", "-nostats", "-loglevel", "error", "-i", item.caminho]
    if preservar_textos:
        if not fundo_origem:
            raise ValueError("Informe o fundo IA TOONS detectado para preservar os textos.")
        cmd += ["-framerate", f"{item.fps:.6f}", "-loop", "1", "-i", str(fundo_origem),
                "-framerate", f"{item.fps:.6f}", "-loop", "1", "-i", str(perfil.fundo_path)]
        filtro = (
            f"[1:v]scale={P.SAIDA_LARGURA}:{P.SAIDA_ALTURA}:force_original_aspect_ratio=increase,"
            f"crop={P.SAIDA_LARGURA}:{P.SAIDA_ALTURA},setsar=1[origbg];"
            f"[2:v]scale={P.SAIDA_LARGURA}:{P.SAIDA_ALTURA}:force_original_aspect_ratio=increase,"
            f"crop={P.SAIDA_LARGURA}:{P.SAIDA_ALTURA},setsar=1[novobg];"
            "[0:v]split=3[original][comparar][centro_fonte];"
            "[comparar][origbg]blend=all_mode=difference,format=gray,"
            "lut='if(gt(val,24),255,0)',"
            "dilation,dilation,dilation,dilation,dilation,dilation,"
            "lut='if(gt(val,128),255,0)',setrange=full[mascara];"
            "[original]format=rgba[original_rgba];"
            "[original_rgba][mascara]alphamerge[texto_preservado];"
            "[novobg][texto_preservado]overlay[externo];"
            f"[centro_fonte]crop={P.ANIM_W}:{P.ANIM_H}:{P.ANIM_X}:{P.ANIM_Y}[centro];"
            f"[externo][centro]overlay={P.ANIM_X}:{P.ANIM_Y}[out]"
        )
    else:
        cmd += ["-framerate", f"{item.fps:.6f}", "-loop", "1", "-i", str(perfil.fundo_path)]
        filtro = (
            f"[1:v]scale={P.SAIDA_LARGURA}:{P.SAIDA_ALTURA}:force_original_aspect_ratio=increase,"
            f"crop={P.SAIDA_LARGURA}:{P.SAIDA_ALTURA},setsar=1[bg];"
            f"[0:v]crop={P.ANIM_W}:{P.ANIM_H}:{P.ANIM_X}:{P.ANIM_Y}[centro];"
            f"[bg][centro]overlay={P.ANIM_X}:{P.ANIM_Y}[out]"
        )
    cmd += [
        "-filter_complex", filtro, "-map", "[out]", "-map", "0:a?",
        "-t", f"{item.duracao:.6f}",
        "-c:v", P.SAIDA_CODEC, "-preset", P.SAIDA_PRESET, "-crf", str(P.SAIDA_CRF),
        "-pix_fmt", P.SAIDA_PIXFMT, "-c:a", "copy", "-map_metadata", "0",
        "-movflags", "+faststart", "-progress", "pipe:1", destino,
    ]
    return cmd


def reaproveitar_video(
    caminho_video: str,
    pasta_saida: str,
    perfil_destino: str = "animatoons",
    preservar_textos: bool = True,
    item: VideoReaproveitavel | None = None,
    cancelada_ref=None,
    progresso_cb: Callable[[float, str], None] | None = None,
) -> dict:
    item = item or inspecionar_video(caminho_video)
    if not item.compativel:
        return {"sucesso": False, "erro": item.erro or "Arquivo incompatível."}
    perfil = obter_perfil(perfil_destino)
    destino = caminho_sem_sobrescrever(
        pasta_saida, f"{Path(caminho_video).stem}_{perfil.nome_canal.replace(' ', '_')}", ".mp4"
    )
    try:
        fundo_origem = detectar_fundo_origem(caminho_video) if preservar_textos else None
        cmd = construir_comando(item, destino, perfil, preservar_textos, fundo_origem)
        processo = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding="utf-8", errors="replace")
        saida_recente = deque(maxlen=40)
        for linha in processo.stdout:
            linha = linha.strip()
            saida_recente.append(linha)
            if cancelada_ref and cancelada_ref.cancelada:
                processo.kill()
                return {"sucesso": False, "erro": "Processamento cancelado."}
            if linha.startswith("out_time_ms="):
                try:
                    segundos = float(linha.split("=", 1)[1]) / 1_000_000
                    pct = min(98.0, segundos / item.duracao * 100)
                    if progresso_cb:
                        progresso_cb(pct, f"Processando {item.nome}…")
                except (ValueError, ZeroDivisionError):
                    pass
        processo.stdout.close()
        processo.wait()
        if processo.returncode != 0:
            return {"sucesso": False,
                    "erro": f"FFmpeg falhou ({processo.returncode}): {' | '.join(saida_recente)[-900:]}"}
        if progresso_cb:
            progresso_cb(100, f"{item.nome} concluído.")
        return {"sucesso": True, "caminho_saida": str(destino), "erro": ""}
    except Exception as erro:
        return {"sucesso": False, "erro": str(erro)}
