"""Gerenciamento seguro dos arquivos temporários criados pelo aplicativo."""

from pathlib import Path
import time


PREFIXO_PREVIEW = "ia_toons_preview_"


def limpar_previews(temp_dir: Path, preservar: set[Path] | None = None, idade_minima_s: float = 0) -> int:
    """Remove somente JPEGs de preview identificados pelo prefixo do aplicativo."""
    preservar_resolvidos = {p.resolve() for p in (preservar or set())}
    agora = time.time()
    removidos = 0

    if not temp_dir.exists():
        return 0

    for arquivo in temp_dir.glob(f"{PREFIXO_PREVIEW}*.jpg"):
        try:
            if arquivo.resolve() in preservar_resolvidos:
                continue
            if idade_minima_s > 0 and agora - arquivo.stat().st_mtime < idade_minima_s:
                continue
            arquivo.unlink()
            removidos += 1
        except OSError:
            continue
    return removidos
