"""
importacao.py — Importação e análise de vídeos
Usa ffprobe para extrair metadados sem modificar o arquivo original.
"""

import subprocess
import json
import math
from pathlib import Path


EXTENSOES_PERMITIDAS = {".mp4", ".mkv", ".mov"}


class InfoVideo:
    """Armazena os metadados de um vídeo importado."""

    def __init__(self):
        self.caminho: str = ""
        self.nome: str = ""
        self.duracao: float = 0.0          # segundos
        self.largura: int = 0
        self.altura: int = 0
        self.fps: float = 0.0
        self.codec_video: str = ""
        self.codec_audio: str = ""
        self.sample_rate: int = 0
        self.canais_audio: int = 0
        self.bitrate_total: int = 0        # bits/s
        self.tamanho_bytes: int = 0
        self.valido: bool = False
        self.erro: str = ""

    def duracao_formatada(self) -> str:
        """Retorna a duração no formato HH:MM:SS."""
        total = int(self.duracao)
        h = total // 3600
        m = (total % 3600) // 60
        s = total % 60
        return f"{h:02d}:{m:02d}:{s:02d}"

    def tamanho_formatado(self) -> str:
        """Retorna o tamanho do arquivo em MB."""
        mb = self.tamanho_bytes / (1024 * 1024)
        return f"{mb:.1f} MB"

    def resolucao_str(self) -> str:
        return f"{self.largura}×{self.altura}"

    def fps_str(self) -> str:
        return f"{self.fps:.2f}".rstrip("0").rstrip(".") + " FPS"

    def __str__(self) -> str:
        return (
            f"{self.nome} | {self.resolucao_str()} | "
            f"{self.fps_str()} | {self.duracao_formatada()} | "
            f"{self.tamanho_formatado()}"
        )


from modules.performance import medir_operacao


@medir_operacao("importacao_ffprobe", backend="ffprobe")
def analisar_video(caminho: str) -> InfoVideo:
    """
    Usa ffprobe para extrair metadados do vídeo.
    Não modifica o arquivo. Retorna um objeto InfoVideo.
    """
    info = InfoVideo()
    info.caminho = caminho
    info.nome = Path(caminho).name

    # Valida extensão
    ext = Path(caminho).suffix.lower()
    if ext not in EXTENSOES_PERMITIDAS:
        info.erro = f"Formato não suportado: {ext}. Use MP4, MKV ou MOV."
        return info

    if not Path(caminho).exists():
        info.erro = "Arquivo não encontrado."
        return info

    # Chama ffprobe para extrair JSON com metadados
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        caminho,
    ]

    try:
        resultado = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        info.erro = "ffprobe não encontrado. Verifique se o FFmpeg está instalado e no PATH."
        return info
    except subprocess.TimeoutExpired:
        info.erro = "Tempo esgotado ao analisar o vídeo."
        return info

    if resultado.returncode != 0:
        info.erro = f"Erro ao analisar o vídeo: {resultado.stderr[:200]}"
        return info

    try:
        dados = json.loads(resultado.stdout)
    except json.JSONDecodeError:
        info.erro = "Não foi possível interpretar os metadados do vídeo."
        return info

    # Extrai informações do formato (container)
    fmt = dados.get("format", {})
    info.duracao = float(fmt.get("duration", 0))
    info.tamanho_bytes = int(fmt.get("size", 0))
    info.bitrate_total = int(fmt.get("bit_rate", 0))

    # Extrai informações das streams de vídeo e áudio
    for stream in dados.get("streams", []):
        tipo = stream.get("codec_type", "")

        if tipo == "video" and not info.codec_video:
            info.codec_video = stream.get("codec_name", "desconhecido")
            info.largura = int(stream.get("width", 0))
            info.altura = int(stream.get("height", 0))
            # FPS pode vir como fração "30000/1001" ou "24/1"
            fps_str = stream.get("r_frame_rate", "0/1")
            try:
                num, den = fps_str.split("/")
                info.fps = float(num) / float(den) if float(den) != 0 else 0.0
            except Exception:
                info.fps = 0.0

        elif tipo == "audio" and not info.codec_audio:
            info.codec_audio = stream.get("codec_name", "desconhecido")
            info.sample_rate = int(stream.get("sample_rate", 0))
            info.canais_audio = int(stream.get("channels", 0))

    info.valido = (
        info.largura > 0
        and info.altura > 0
        and info.duracao > 0
    )
    if not info.valido and not info.erro:
        info.erro = "Não foi possível identificar as dimensões ou duração do vídeo."

    return info


def segundos_para_hhmmss(segundos: float) -> str:
    """Converte segundos (float) para string HH:MM:SS.mmm"""
    s_total = max(0.0, segundos)
    h = int(s_total // 3600)
    m = int((s_total % 3600) // 60)
    s = s_total % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def hhmmss_para_segundos(texto: str) -> float:
    """
    Converte string de tempo para segundos.
    Aceita: "HH:MM:SS", "MM:SS", "SS.mmm", ou número puro.
    """
    texto = texto.strip()
    try:
        partes = texto.split(":")
        if len(partes) == 3:
            return float(partes[0]) * 3600 + float(partes[1]) * 60 + float(partes[2])
        elif len(partes) == 2:
            return float(partes[0]) * 60 + float(partes[1])
        else:
            return float(partes[0])
    except Exception:
        raise ValueError(f"Formato de tempo inválido: '{texto}'. Use HH:MM:SS ou segundos.")


def normalizar_tempos(inicio: float, fim: float, duracao_video: float) -> tuple[float, float]:
    """Valida e limita um intervalo aos limites reais do vídeo."""
    valores = (inicio, fim, duracao_video)
    if not all(math.isfinite(valor) for valor in valores):
        raise ValueError("Os tempos devem ser números finitos.")
    if duracao_video <= 0:
        raise ValueError("A duração do vídeo é inválida.")

    inicio_normalizado = max(0.0, min(float(inicio), float(duracao_video)))
    fim_normalizado = max(0.0, min(float(fim), float(duracao_video)))

    if fim_normalizado <= inicio_normalizado:
        raise ValueError("O tempo final deve ser maior que o tempo inicial.")
    return inicio_normalizado, fim_normalizado
