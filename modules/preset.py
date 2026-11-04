"""
preset.py — Configurações do preset IA TOONS
Todos os parâmetros de composição do layout vertical 9:16
"""

from pathlib import Path

# ─── Caminhos base ──────────────────────────────────────────────────────────
PROJETO_DIR = Path(__file__).parent.parent
ASSETS_DIR  = PROJETO_DIR / "assets"
FONTS_DIR   = ASSETS_DIR  / "fonts"
TEMP_DIR    = PROJETO_DIR / "temp"
EXPORT_DIR  = PROJETO_DIR / "exportados"
DADOS_DIR   = PROJETO_DIR / "dados"

FUNDO_PATH  = ASSETS_DIR  / "fundo_toonmix.png"
FONTE_PATH  = FONTS_DIR   / "RailroadGothicCC.ttf"

# ─── Saída ───────────────────────────────────────────────────────────────────
SAIDA_LARGURA  = 1080
SAIDA_ALTURA   = 1920
SAIDA_FPS_PAD  = 60          # FPS padrão
SAIDA_FPS_TEST = 30          # FPS para testes rápidos
SAIDA_CODEC    = "libx264"
SAIDA_PRESET   = "slow"      # qualidade: ultrafast / fast / slow
SAIDA_CRF      = 18          # 18 = alta qualidade (menor = melhor, maior = menor arquivo)
SAIDA_AUDIO    = "aac"
SAIDA_AUDIO_BR = "192k"
SAIDA_PIXFMT   = "yuv420p"

# ─── Área da animação (1:1 centralizada) ─────────────────────────────────────
# Medição precisa do vídeo de referência:
# Tela: 1080 x 1920
# Margem superior e inferior = (1920 - 1080) / 2 = 420 px
ANIM_X      = 6
ANIM_Y      = 482
ANIM_W      = 1068
ANIM_H      = 944            # área exata da moldura TOONMIX

# ─── Título ──────────────────────────────────────────────────────────────────
# Medição: centro em y≈241, borda superior em y≈218
TITULO_FONTE         = str(FONTE_PATH)
TITULO_TAMANHO       = 72           # pontos — ajustável
TITULO_COR           = "white"
TITULO_COR_CONTORNO  = "black"
TITULO_ESPESSURA_CONTORNO = 4       # pixels de contorno
TITULO_Y_CENTRO      = 241          # posição vertical do centro do texto
TITULO_MARGEM_HORIZ  = 40           # margem lateral em pixels

# ─── Legendas ────────────────────────────────────────────────────────────────
# Medição: borda superior em y≈1528, ≈28 px abaixo do fim da animação
LEGENDA_FONTE              = str(FONTE_PATH)
LEGENDA_TAMANHO            = 56          # pontos — ajustável
LEGENDA_COR                = "white"
LEGENDA_COR_CONTORNO       = "black"
LEGENDA_ESPESSURA_CONTORNO = 4
LEGENDA_Y_TOPO             = 1528        # posição y de início das legendas
LEGENDA_MARGEM_HORIZ       = 40
LEGENDA_MAX_CHARS_LINHA    = 32          # caracteres por linha antes de quebrar

# ─── Logo IA TOONS ────────────────────────────────────────────────────────────
# Localizado em y≈1754–1869, centralizado horizontalmente
# O logo já faz parte do fundo — não é sobreposto separadamente

# ─── Tratamento de imagem (Fase 4, valores padrão definidos aqui) ─────────────
IMAGEM_INTENSIDADE    = 100   # 0–100%
IMAGEM_NITIDEZ        = 0.65  # amount moderado do unsharp para evitar halos
IMAGEM_CONTRASTE      = 1.06  # multiplicador (1.0 = original)
IMAGEM_SATURACAO      = 1.12  # multiplicador (1.0 = original)
IMAGEM_BRILHO         = 0.01  # faixa do filtro eq: -1 a 1
IMAGEM_REDUCAO_RUIDO  = 1.5   # luma_spatial do hqdn3d

# ─── Áudio (Fase 4, valores padrão definidos aqui) ───────────────────────────
AUDIO_VOLUME_INICIAL  = 1.00
AUDIO_VOLUME_FINAL    = 1.25
AUDIO_DURACAO_RAMPA   = 8.0
AUDIO_LIMITER         = 0.95


def caminhos_validos() -> tuple[bool, list[str]]:
    """Verifica se todos os assets necessários existem."""
    erros = []
    if not FUNDO_PATH.exists():
        erros.append(f"Fundo não encontrado: {FUNDO_PATH}")
    if not FONTE_PATH.exists():
        erros.append(f"Fonte não encontrada: {FONTE_PATH}")
    if not TEMP_DIR.exists():
        TEMP_DIR.mkdir(parents=True, exist_ok=True)
    if not EXPORT_DIR.exists():
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    if not DADOS_DIR.exists():
        DADOS_DIR.mkdir(parents=True, exist_ok=True)
    return (len(erros) == 0), erros
