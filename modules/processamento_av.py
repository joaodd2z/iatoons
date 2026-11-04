"""Configuração persistente e filtros FFmpeg do preset audiovisual."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import json
from pathlib import Path

from modules import preset as P


CONFIG_PATH = P.DADOS_DIR / "preset_ia_toons_4k.json"


@dataclass
class AjustesAV:
    nome: str = "IA TOONS 4K"
    imagem_ativa: bool = True
    remover_logo: bool = True
    intensidade: int = P.IMAGEM_INTENSIDADE
    alta_qualidade: bool = True
    nitidez_ativa: bool = True
    nitidez: float = P.IMAGEM_NITIDEZ
    contraste_ativo: bool = True
    contraste: float = P.IMAGEM_CONTRASTE
    saturacao_ativa: bool = True
    saturacao: float = P.IMAGEM_SATURACAO
    brilho_ativo: bool = True
    brilho: float = P.IMAGEM_BRILHO
    ruido_ativo: bool = True
    reducao_ruido: float = P.IMAGEM_REDUCAO_RUIDO
    audio_ativo: bool = True
    volume_inicial: float = P.AUDIO_VOLUME_INICIAL
    volume_final: float = P.AUDIO_VOLUME_FINAL
    duracao_rampa: float = P.AUDIO_DURACAO_RAMPA
    limiter: float = P.AUDIO_LIMITER

    def validar(self):
        if not 0 <= self.intensidade <= 100:
            raise ValueError("A intensidade deve ficar entre 0 e 100%.")
        if not 0 <= self.nitidez <= 2:
            raise ValueError("A nitidez deve ficar entre 0 e 2.")
        if not 0.5 <= self.contraste <= 1.5:
            raise ValueError("O contraste deve ficar entre 0,5 e 1,5.")
        if not 0 <= self.saturacao <= 2:
            raise ValueError("A saturação deve ficar entre 0 e 2.")
        if not -0.3 <= self.brilho <= 0.3:
            raise ValueError("O brilho deve ficar entre -0,3 e 0,3.")
        if not 0 <= self.reducao_ruido <= 6:
            raise ValueError("A redução de ruído deve ficar entre 0 e 6.")
        if not 0 <= self.volume_inicial <= 3 or not 0 <= self.volume_final <= 3:
            raise ValueError("Os volumes devem ficar entre 0 e 3.")
        if not 0 <= self.duracao_rampa <= 60:
            raise ValueError("A duração da curva deve ficar entre 0 e 60 segundos.")
        if not 0.1 <= self.limiter <= 1.0:
            raise ValueError("O limiter deve ficar entre 0,1 e 1,0.")
        return self


def ajustes_de(valor=None) -> AjustesAV:
    if isinstance(valor, AjustesAV):
        return valor.validar()
    if isinstance(valor, dict):
        permitidos = {campo.name for campo in fields(AjustesAV)}
        return AjustesAV(**{k: v for k, v in valor.items() if k in permitidos}).validar()
    return AjustesAV().validar()


def carregar_ajustes() -> AjustesAV:
    if not CONFIG_PATH.exists():
        return AjustesAV()
    try:
        return ajustes_de(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return AjustesAV()


def salvar_ajustes(ajustes: AjustesAV) -> Path:
    ajustes.validar()
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporario = CONFIG_PATH.with_suffix(".json.tmp")
    temporario.write_text(json.dumps(asdict(ajustes), ensure_ascii=False, indent=2), encoding="utf-8")
    temporario.replace(CONFIG_PATH)
    return CONFIG_PATH


def filtro_animacao(ajustes=None, largura=P.ANIM_W, altura=P.ANIM_H, fps=None, largura_orig: int = 0, altura_orig: int = 0) -> str:
    """Monta filtros somente para a animação, antes da composição."""
    cfg = ajustes_de(ajustes)
    filtros = []
    if cfg.remover_logo and largura_orig > 0 and altura_orig > 0:
        # 1. Canto inferior esquerdo ('Rede Canais' / 'NAIS HD')
        w_esq = max(16, int(round(largura_orig * 0.165)))
        h_esq = max(16, int(round(altura_orig * 0.125)))
        x_esq = max(8, int(round(largura_orig * 0.02)))
        y_esq = max(0, altura_orig - h_esq - max(8, int(round(altura_orig * 0.03))))
        if x_esq + w_esq < largura_orig and y_esq + h_esq < altura_orig and w_esq > 4 and h_esq > 4:
            filtros.append(f"delogo=x={x_esq}:y={y_esq}:w={w_esq}:h={h_esq}")

        # 2. Canto inferior direito ('Global HD' e emissoras)
        w_dir = max(16, int(round(largura_orig * 0.155)))
        h_dir = max(16, int(round(altura_orig * 0.145)))
        x_dir = max(0, largura_orig - w_dir - max(8, int(round(largura_orig * 0.035))))
        y_dir = max(0, altura_orig - h_dir - max(8, int(round(altura_orig * 0.035))))
        if x_dir + w_dir < largura_orig and y_dir + h_dir < altura_orig and w_dir > 4 and h_dir > 4:
            filtros.append(f"delogo=x={x_dir}:y={y_dir}:w={w_dir}:h={h_dir}")
    intensidade = cfg.intensidade / 100.0 if cfg.imagem_ativa else 0.0
    if intensidade > 0 and cfg.ruido_ativo and cfg.reducao_ruido > 0:
        luma = cfg.reducao_ruido * intensidade
        filtros.append(f"hqdn3d={luma:.3f}:{luma * 0.8:.3f}:{luma * 2:.3f}:{luma * 1.6:.3f}")
    flags = ":flags=lanczos+accurate_rnd+full_chroma_int" if cfg.alta_qualidade else ""
    filtros.append(f"scale={largura}:{altura}:force_original_aspect_ratio=increase{flags}")
    filtros.append(f"crop={largura}:{altura}")
    if intensidade > 0:
        eq_partes = []
        if cfg.contraste_ativo:
            eq_partes.append(f"contrast={1 + (cfg.contraste - 1) * intensidade:.4f}")
        if cfg.saturacao_ativa:
            eq_partes.append(f"saturation={1 + (cfg.saturacao - 1) * intensidade:.4f}")
        if cfg.brilho_ativo:
            eq_partes.append(f"brightness={cfg.brilho * intensidade:.4f}")
        if eq_partes:
            filtros.append("eq=" + ":".join(eq_partes))
        if cfg.nitidez_ativa and cfg.nitidez > 0:
            filtros.append(f"unsharp=5:5:{cfg.nitidez * intensidade:.3f}:5:5:0.0")
    if fps:
        filtros.append(f"fps={int(fps)}")
    filtros.append("setsar=1")
    return ",".join(filtros)


def filtro_audio(ajustes=None, duracao_corte: float = 0) -> str:
    cfg = ajustes_de(ajustes)
    if not cfg.audio_ativo:
        return ""
    rampa = min(max(0.0, cfg.duracao_rampa), max(0.0, duracao_corte))
    if rampa > 0:
        expressao = (
            f"if(lt(t,{rampa:.3f}),{cfg.volume_inicial:.4f}+"
            f"({cfg.volume_final:.4f}-{cfg.volume_inicial:.4f})*t/{rampa:.3f},{cfg.volume_final:.4f})"
        )
    else:
        expressao = f"{cfg.volume_final:.4f}"
    return (
        f"volume='{expressao}':eval=frame,"
        f"alimiter=limit={cfg.limiter:.3f}:attack=5:release=50:level=false"
    )
