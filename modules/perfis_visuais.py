"""Perfil visual único e fixo do projeto Yattuns: TOONMIX."""

from dataclasses import dataclass
from pathlib import Path

from modules import preset as P


@dataclass(frozen=True)
class EstiloTexto:
    """Estilo independente do motor; ``traco_referencia`` preserva o valor da UI de origem."""

    cor: str
    cor_contorno: str
    espessura_contorno: int
    traco_referencia: int | None = None
    caixa_alta: bool = True
    # Campos usados pelo Modo Agência; o renderer legado os ignora.
    tamanho: int = 72
    tamanho_min: int = 44
    max_linhas: int = 2
    sombra_x: int = 0
    sombra_y: int = 0
    cor_sombra: str = "black@0.55"


@dataclass(frozen=True)
class Zona:
    x: int
    y: int
    w: int
    h: int

    @property
    def x2(self) -> int:
        return self.x + self.w

    @property
    def y2(self) -> int:
        return self.y + self.h

    def contem(self, outra: "Zona") -> bool:
        return (outra.x >= self.x and outra.y >= self.y and
                outra.x2 <= self.x2 and outra.y2 <= self.y2)


@dataclass(frozen=True)
class ZonasLayout:
    """TOP_SAFE_ZONE, VIDEO_ZONE e BOTTOM_SAFE_ZONE, mais as caixas de texto internas."""

    topo: Zona
    video: Zona
    base: Zona
    titulo: Zona
    legenda: Zona


# Medido em assets/fundo_toonmix.png (1080×1920): moldura amarela y=474–1434,
# área interna x=6–1073 / y=482–1425 e logo em y≈1778–1855. O topo evita a
# faixa de abas/status das plataformas (~0–140 px); a legenda termina antes da
# logo e fora da área mais usada pela descrição/botões das plataformas.
ZONAS_TOONMIX = ZonasLayout(
    topo=Zona(0, 0, 1080, 474),
    video=Zona(6, 482, 1068, 944),
    base=Zona(0, 1434, 1080, 486),
    titulo=Zona(60, 150, 960, 300),
    legenda=Zona(110, 1456, 860, 196),
)

FUNDO_TOONMIX_PATH: Path = P.ASSETS_DIR / "fundo_toonmix.png"


@dataclass(frozen=True)
class PerfilVisual:
    id: str
    nome: str
    fundo_path: Path
    nome_canal: str
    logo_posicao: str | None = None
    logo_escala: float = 1.0
    titulo: EstiloTexto = EstiloTexto("0xFFE600", "black", 6)
    legenda: EstiloTexto = EstiloTexto("white", "black", 5)
    cor_principal: str = "0xFFB800"
    cor_secundaria: str = "0x1E1E1E"
    zonas: ZonasLayout = ZONAS_TOONMIX
    titulo_agencia: EstiloTexto | None = None
    legenda_agencia: EstiloTexto | None = None
    logo_path: Path | None = None

    @property
    def estilo_titulo_agencia(self) -> EstiloTexto:
        return self.titulo_agencia or ESTILO_TITULO_AGENCIA_PADRAO

    @property
    def estilo_legenda_agencia(self) -> EstiloTexto:
        return self.legenda_agencia or ESTILO_LEGENDA_AGENCIA_PADRAO

    @property
    def disponivel(self) -> bool:
        return self.fundo_path.is_file()


# Referência TOONMIX: texto amarelo com contorno preto forte, escala adaptativa.
ESTILO_TITULO_AGENCIA_PADRAO = EstiloTexto(
    cor="0xFFE600", cor_contorno="black", espessura_contorno=9,
    tamanho=112, tamanho_min=58, max_linhas=2, sombra_x=0, sombra_y=6,
)
ESTILO_LEGENDA_AGENCIA_PADRAO = EstiloTexto(
    cor="white", cor_contorno="black", espessura_contorno=6,
    tamanho=60, tamanho_min=44, max_linhas=2, sombra_x=0, sombra_y=4,
)

PERFIL_TOONMIX = PerfilVisual(
    id="toonmix",
    nome="TOONMIX",
    fundo_path=FUNDO_TOONMIX_PATH,
    nome_canal="TOONMIX",
    logo_posicao="incorporada ao fundo, centro inferior (y≈1778–1855)",
    logo_escala=1.0,
    titulo=EstiloTexto(cor="0xFFE600", cor_contorno="black", espessura_contorno=6),
    legenda=EstiloTexto(cor="white", cor_contorno="black", espessura_contorno=5),
    cor_principal="0xFFB800",
    cor_secundaria="0x1E1E1E",
    zonas=ZONAS_TOONMIX,
    titulo_agencia=ESTILO_TITULO_AGENCIA_PADRAO,
    legenda_agencia=ESTILO_LEGENDA_AGENCIA_PADRAO,
)

PERFIS_VISUAIS: dict[str, PerfilVisual] = {
    "toonmix": PERFIL_TOONMIX,
}

PERFIL_PADRAO_ID = "toonmix"


def obter_perfil(perfil: str | PerfilVisual | None = None) -> PerfilVisual:
    """Retorna sempre o perfil único e fixo TOONMIX, validando seu arquivo de fundo."""
    resolvido = PERFIL_TOONMIX
    if not resolvido.disponivel:
        raise FileNotFoundError(
            f"Fundo do perfil {resolvido.nome} não encontrado: {resolvido.fundo_path}"
        )
    return resolvido


def listar_perfis() -> list[PerfilVisual]:
    """Retorna exclusivamente o perfil fixo TOONMIX."""
    return [PERFIL_TOONMIX]


def perfil_por_nome(nome: str = "TOONMIX") -> PerfilVisual:
    """Resolve qualquer nome para o perfil único TOONMIX."""
    return obter_perfil("toonmix")
