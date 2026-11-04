"""Copy existing model-link targets; never modify/remove source links or download."""
import json
from pathlib import Path
import shutil

BASE = Path(__file__).resolve().parents[2]
src = BASE/'GeradorDeCortes'
links = [p for p in (src/'dados/modelos_whisper').rglob('*') if p.is_symlink() and p.is_file()]
registros = {str(p.relative_to(src)):dict(target=str(p.readlink()),resolvido=str(p.resolve()),
                                        size=p.stat().st_size) for p in links}
for nome in ('BACKUP_ATUAL_ANTES_ROLLBACK','GeradorDeCortes_ROLLBACK'):
    root = BASE/nome
    for p in links:
        destino = root/p.relative_to(src)
        if destino.is_symlink():
            raise SystemExit('Link ja existe no destino; nao sobrescrever/follow link.')
        if destino.exists():
            continue
        destino.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(p.resolve(),destino)
    manifest = root/'links_modelos_originais.json'
    if not manifest.exists():
        manifest.write_text(json.dumps(registros,indent=2),encoding='utf-8')
print(json.dumps(dict(links=len(links),bytes_por_copia=sum(p.stat().st_size for p in links))))
