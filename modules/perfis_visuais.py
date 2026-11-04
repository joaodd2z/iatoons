"""Perfis visuais. TOONMIX é o padrão; cada conta nova pode ter o seu em dados/perfis/<id>.json
(fundo 1080x1920 com a mesma moldura, cores do título/legenda e nome do canal), sem mexer em código."""

from dataclasses import dataclass, replace
import json
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
PERFIS_DIR: Path = P.DADOS_DIR / "perfis"


def _estilo(base: EstiloTexto, dados: dict | None) -> EstiloTexto:
    if not dados:
        return base
    campos = {k: v for k, v in dados.items() if k in EstiloTexto.__dataclass_fields__}
    return replace(base, **campos)


def _perfil_de_json(caminho: Path) -> PerfilVisual:
    """dados/perfis/<id>.json -> PerfilVisual. Só "id" e "fundo" são obrigatórios; o resto herda do TOONMIX.
    O fundo precisa ter a mesma moldura do TOONMIX (vídeo em y=482–1425), porque as zonas são as mesmas."""
    d = json.loads(caminho.read_text(encoding="utf-8"))
    fundo = Path(d["fundo"])
    if not fundo.is_absolute():
        fundo = P.ASSETS_DIR / fundo
    base = PERFIL_TOONMIX
    return replace(
        base,
        id=str(d["id"]).lower(),
        nome=d.get("nome", str(d["id"]).upper()),
        fundo_path=fundo,
        nome_canal=d.get("nome_canal", d.get("nome", base.nome_canal)),
        cor_principal=d.get("cor_principal", base.cor_principal),
        cor_secundaria=d.get("cor_secundaria", base.cor_secundaria),
        titulo=_estilo(base.titulo, d.get("titulo")),
        legenda=_estilo(base.legenda, d.get("legenda")),
        titulo_agencia=_estilo(base.estilo_titulo_agencia, d.get("titulo_agencia") or d.get("titulo")),
        legenda_agencia=_estilo(base.estilo_legenda_agencia, d.get("legenda_agencia") or d.get("legenda")),
    )


def _carregar_perfis_extras() -> dict[str, PerfilVisual]:
    extras: dict[str, PerfilVisual] = {}
    if PERFIS_DIR.is_dir():
        for arq in sorted(PERFIS_DIR.glob("*.json")):
            try:
                perfil = _perfil_de_json(arq)
            except (OSError, ValueError, KeyError, TypeError):
                continue
            extras[perfil.id] = perfil
    return extras


def todos_perfis() -> dict[str, PerfilVisual]:
    return {**PERFIS_VISUAIS, **_carregar_perfis_extras()}


def normalizar_perfil_id(perfil_id: str | None) -> str:
    """Id de perfil válido: o informado, se existir (TOONMIX ou dados/perfis/*.json); senão TOONMIX (estado legado)."""
    pid = str(perfil_id or "").strip().lower()
    return pid if pid in todos_perfis() else PERFIL_PADRAO_ID


def obter_perfil(perfil: str | PerfilVisual | None = None) -> PerfilVisual:
    """Resolve o perfil pelo id (TOONMIX quando vazio ou desconhecido) e valida o arquivo de fundo."""
    if isinstance(perfil, PerfilVisual):
        resolvido = perfil
    else:
        resolvido = todos_perfis().get(str(perfil or PERFIL_PADRAO_ID).lower(), PERFIL_TOONMIX)
    if not resolvido.disponivel:
        raise FileNotFoundError(
            f"Fundo do perfil {resolvido.nome} não encontrado: {resolvido.fundo_path}"
        )
    return resolvido


def listar_perfis() -> list[PerfilVisual]:
    return list(todos_perfis().values())


def perfil_por_nome(nome: str = "TOONMIX") -> PerfilVisual:
    alvo = str(nome or "").strip().lower()
    for p in todos_perfis().values():
        if p.id == alvo or p.nome.lower() == alvo:
            return obter_perfil(p)
    return obter_perfil(PERFIL_PADRAO_ID)
