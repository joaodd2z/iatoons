"""Preset audiovisual AGENCY_AV_V1, separado do IA TOONS 4K.

As referências (nitidez ≈ +27, exposição +11 / brilho 0 no editor) indicam a
*direção* estética — recuperar definição sem lavar a imagem — e não valores
diretamente convertíveis para FFmpeg. Por isso os padrões são moderados:

- ``cas`` (Contrast Adaptive Sharpening) adapta a força ao contraste local e
  não cria os halos típicos de um ``unsharp`` forte;
- ``gamma`` levemente acima de 1 levanta meios-tons (efeito de "exposição")
  sem mexer nos brancos, e o contraste fica perto de 1 para não estourar highlights;
- o tratamento é aplicado somente ao conteúdo principal, nunca ao fundo/template.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import json
import subprocess
from pathlib import Path

from modules import preset as P


NOME_PRESET = "AGENCY_AV_V1"
CONFIG_AGENCIA_AV_PATH = P.DADOS_DIR / "preset_agency_av_v1.json"
# Marca gravada nos MP4 exportados; impede tratar duas vezes o mesmo material.
TAG_PROCESSADO = "IA_TOONS_CUT"


@dataclass
class AjustesAgencia:
    nome: str = NOME_PRESET
    imagem_ativa: bool = True
    remover_logo: bool = True
    intensidade: int = 100
    reducao_ruido: float = 0.8        # hqdn3d luma_spatial (conservador)
    nitidez: float = 0.45             # cas strength 0–1
    contraste: float = 1.03
    saturacao: float = 1.05
    gamma: float = 1.03               # "exposição" suave em meios-tons
    brilho: float = 0.0
    alta_qualidade: bool = True       # lanczos + arredondamento preciso
    fundo_desfoque: int = 26          # raio do desfoque das sobras da zona
    audio_ativo: bool = True
    volume: float = 1.0
    limiter: float = 0.95
    pular_se_processado: bool = True
    # Exportação: alta qualidade, sem reduzir bitrate agressivamente.
    crf: int = 17
    preset_x264: str = "slow"
    audio_kbps: int = 192

    def validar(self) -> "AjustesAgencia":
        if not 0 <= self.intensidade <= 100:
            raise ValueError("A intensidade deve ficar entre 0 e 100%.")
        if not 0 <= self.reducao_ruido <= 4:
            raise ValueError("A redução de ruído deve ficar entre 0 e 4.")
        if not 0 <= self.nitidez <= 1:
            raise ValueError("A nitidez (CAS) deve ficar entre 0 e 1.")
        if not 0.8 <= self.contraste <= 1.2:
            raise ValueError("O contraste deve ficar entre 0,8 e 1,2.")
        if not 0.5 <= self.saturacao <= 1.5:
            raise ValueError("A saturação deve ficar entre 0,5 e 1,5.")
        if not 0.8 <= self.gamma <= 1.25:
            raise ValueError("O gamma deve ficar entre 0,8 e 1,25.")
        if not -0.15 <= self.brilho <= 0.15:
            raise ValueError("O brilho deve ficar entre -0,15 e 0,15.")
        if not 4 <= self.fundo_desfoque <= 60:
            raise ValueError("O desfoque do preenchimento deve ficar entre 4 e 60.")
        if not 0 <= self.volume <= 3:
            raise ValueError("O volume deve ficar entre 0 e 3.")
        if not 0.1 <= self.limiter <= 1.0:
            raise ValueError("O limiter deve ficar entre 0,1 e 1,0.")
        if not 12 <= self.crf <= 23:
            raise ValueError("O CRF deve ficar entre 12 e 23 para manter qualidade alta.")
        if self.preset_x264 not in ("ultrafast", "superfast", "veryfast", "faster", "fast",
                                    "medium", "slow", "slower", "veryslow"):
            raise ValueError("Preset x264 inválido.")
        if not 128 <= self.audio_kbps <= 320:
            raise ValueError("O bitrate de áudio deve ficar entre 128 e 320 kbps.")
        return self


def ajustes_agencia_de(valor=None) -> AjustesAgencia:
    if isinstance(valor, AjustesAgencia):
        return valor.validar()
    if isinstance(valor, dict):
        permitidos = {campo.name for campo in fields(AjustesAgencia)}
        return AjustesAgencia(**{k: v for k, v in valor.items() if k in permitidos}).validar()
    return AjustesAgencia().validar()


def carregar_ajustes_agencia() -> AjustesAgencia:
    if not CONFIG_AGENCIA_AV_PATH.exists():
        return AjustesAgencia()
    try:
        return ajustes_agencia_de(json.loads(CONFIG_AGENCIA_AV_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return AjustesAgencia()


def salvar_ajustes_agencia(ajustes: AjustesAgencia) -> Path:
    ajustes.validar()
    CONFIG_AGENCIA_AV_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_AGENCIA_AV_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(ajustes), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CONFIG_AGENCIA_AV_PATH)
    return CONFIG_AGENCIA_AV_PATH


def fonte_ja_processada(caminho_video: str | Path) -> bool:
    """True quando o arquivo já foi exportado por este aplicativo (tag no container)."""
    try:
        resultado = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format_tags=comment",
             "-of", "default=nw=1:nk=1", str(caminho_video)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
        )
        return TAG_PROCESSADO in (resultado.stdout or "")
    except (OSError, subprocess.TimeoutExpired):
        return False


def filtro_delogo(largura: int, altura: int, ativo: bool = True, canto: str = "direito") -> str:
    """Remove marca d'água/logo de transmissão via FFmpeg delogo adaptativo:
    - 'direito': Canto inferior direito (Global HD / emissoras)
    - 'esquerdo': Canto inferior esquerdo (Rede Canais / NAIS HD)
    - 'ambos': Ambos os cantos
    """
    if not ativo or largura <= 0 or altura <= 0:
        return ""
    filtros = []

    # 1. Canto inferior esquerdo: 'Rede Canais' / 'SuperCanais' / 'NAIS HD'
    if canto in ("esquerdo", "ambos"):
        w_esq = max(16, int(round(largura * 0.175)))
        h_esq = max(16, int(round(altura * 0.135)))
        x_esq = 1
        y_esq = max(1, altura - h_esq - 1)
        if x_esq + w_esq >= largura:
            w_esq = largura - x_esq - 1
        if y_esq + h_esq >= altura:
            h_esq = altura - y_esq - 1
        if w_esq > 4 and h_esq > 4:
            filtros.append(f"delogo=x={x_esq}:y={y_esq}:w={w_esq}:h={h_esq}")

    # 2. Canto inferior direito: 'Global HD' e emissoras
    if canto in ("direito", "ambos"):
        w_dir = max(16, int(round(largura * 0.155)))
        h_dir = max(16, int(round(altura * 0.145)))
        margem_x_dir = max(8, int(round(largura * 0.035)))
        margem_y_dir = max(8, int(round(altura * 0.035)))
        x_dir = max(0, largura - w_dir - margem_x_dir)
        y_dir = max(0, altura - h_dir - margem_y_dir)
        if x_dir + w_dir >= largura:
            w_dir = largura - x_dir - 1
        if y_dir + h_dir >= altura:
            h_dir = altura - y_dir - 1
        if w_dir > 4 and h_dir > 4:
            filtros.append(f"delogo=x={x_dir}:y={y_dir}:w={w_dir}:h={h_dir}")

    return ",".join(filtros)


def filtro_delogo_zona(largura: int, altura: int, ativo: bool = True) -> str:
    """Limpa qualquer resquício de logo (ex: 'S' do SuperCanais / Rede Canais) no canto inferior esquerdo
    da janela já recortada (pós-scale e pós-crop), independente do aspect ratio da fonte original."""
    if not ativo or largura <= 16 or altura <= 16:
        return ""
    w_esq = max(16, int(round(largura * 0.13)))
    h_esq = max(16, int(round(altura * 0.115)))
    x_esq = 1
    y_esq = max(1, altura - h_esq - 1)
    if x_esq + w_esq >= largura:
        w_esq = largura - x_esq - 1
    if y_esq + h_esq >= altura:
        h_esq = altura - y_esq - 1
    if w_esq <= 4 or h_esq <= 4:
        return ""
    return f"delogo=x={x_esq}:y={y_esq}:w={w_esq}:h={h_esq}"


def filtro_pre_escala(cfg: AjustesAgencia, aplicar: bool = True, largura: int = 0, altura: int = 0) -> str:
    """Filtros aplicados na resolução original (delogo e redução de ruído antes do redimensionamento)."""
    filtros = []
    if cfg.remover_logo and aplicar and largura > 0 and altura > 0:
        dl = filtro_delogo(largura, altura, True, canto="ambos")
        if dl:
            filtros.append(dl)
    intensidade = cfg.intensidade / 100.0 if (cfg.imagem_ativa and aplicar) else 0.0
    if intensidade > 0 and cfg.reducao_ruido > 0:
        luma = cfg.reducao_ruido * intensidade
        filtros.append(f"hqdn3d={luma:.3f}:{luma * 0.75:.3f}:{luma * 1.8:.3f}:{luma * 1.4:.3f}")
    return ",".join(filtros)


def flags_escala(cfg: AjustesAgencia) -> str:
    return ":flags=lanczos+accurate_rnd+full_chroma_int" if cfg.alta_qualidade else ":flags=bicubic"


def filtro_pos_escala(cfg: AjustesAgencia, aplicar: bool = True) -> str:
    """Cor e nitidez aplicadas no tamanho final do conteúdo principal."""
    intensidade = cfg.intensidade / 100.0 if (cfg.imagem_ativa and aplicar) else 0.0
    if intensidade <= 0:
        return ""
    filtros = []
    eq = [
        f"contrast={1 + (cfg.contraste - 1) * intensidade:.4f}",
        f"saturation={1 + (cfg.saturacao - 1) * intensidade:.4f}",
        f"gamma={1 + (cfg.gamma - 1) * intensidade:.4f}",
        f"brightness={cfg.brilho * intensidade:.4f}",
    ]
    filtros.append("eq=" + ":".join(eq))
    if cfg.nitidez > 0:
        filtros.append(f"cas=strength={cfg.nitidez * intensidade:.3f}")
    return ",".join(filtros)


def filtro_audio_agencia(cfg: AjustesAgencia) -> str:
    if not cfg.audio_ativo:
        return ""
    return (f"volume={cfg.volume:.4f},"
            f"alimiter=limit={cfg.limiter:.3f}:attack=5:release=50:level=false")
