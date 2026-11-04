"""Read-only integrity audit of principal, protected snapshot and rollback."""
import hashlib
import json
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
ATUAL = BASE/'GeradorDeCortes'
PROTEGIDA = BASE/'BACKUP_ATUAL_ANTES_ROLLBACK'
ROLLBACK = BASE/'GeradorDeCortes_ROLLBACK'
FONTE = BASE/'analise_temp/ROLLBACK_FONTE_20260928'


def sha(p):
    with p.open('rb') as f:
        return hashlib.file_digest(f,'sha256').hexdigest()


def executar():
    ausentes,diferentes,reparse = [],[],[]
    quantidade,bytes_total = 0,0
    for p in ATUAL.rglob('*'):
        if p.is_symlink() or getattr(p.stat(),'st_file_attributes',0)&0x400:
            reparse.append(str(p.relative_to(ATUAL)))
        if not p.is_file():
            continue
        quantidade += 1
        bytes_total += p.stat().st_size
        protegido = PROTEGIDA/p.relative_to(ATUAL)
        if not protegido.is_file():
            ausentes.append(str(p.relative_to(ATUAL)))
        elif p.stat().st_size!=protegido.stat().st_size or p.stat().st_mtime_ns!=protegido.stat().st_mtime_ns:
            diferentes.append(str(p.relative_to(ATUAL)))
    hashes,diff_code = {},[]
    for pasta in ('modules','ui','dados/transcricoes'):
        for p in (ATUAL/pasta).rglob('*'):
            if not p.is_file() or p.suffix not in ('.py','.json'):
                continue
            rel = str(p.relative_to(ATUAL))
            atual,protegida,rollback = sha(p),sha(PROTEGIDA/rel),sha(ROLLBACK/rel)
            hashes[rel] = dict(atual=atual,protegida=protegida,rollback=rollback)
            if atual!=rollback:
                diff_code.append(rel)
    for nome in ('main.py','modules/config_ia.py','modules/credenciais.py','ui/tela_config_ia.py',
                 'modules/renderer.py','modules/agencia/pipeline.py','modules/agencia/timeline.py',
                 'modules/transcricao.py','modules/transcricao_v2.py','dados/config_ia.json'):
        p = ATUAL/nome
        if p.is_file():
            hashes[nome] = dict(atual=sha(p),protegida=sha(PROTEGIDA/nome),rollback=sha(ROLLBACK/nome))
    comparacao = []
    for p in FONTE.rglob('*'):
        if p.is_file() and p.suffix in ('.py','.json','.md','.txt'):
            rel = str(p.relative_to(FONTE))
            atual = ATUAL/rel
            comparacao.append(dict(arquivo=rel,backup=sha(p),atual=sha(atual) if atual.is_file() else None,
                                   igual=atual.is_file() and sha(p)==sha(atual)))
    manifest = json.loads((PROTEGIDA/'links_modelos_originais.json').read_text(encoding='utf-8'))
    links_ok = set(reparse)==set(manifest)
    for rel in reparse:
        if not (PROTEGIDA/rel).is_file() or not (ROLLBACK/rel).is_file():
            links_ok = False
            continue
        links_ok &= sha(ATUAL/rel)==sha(PROTEGIDA/rel)==sha(ROLLBACK/rel)
    integridade = not ausentes and not diferentes and links_ok and all(h['atual']==h['protegida'] for h in hashes.values())
    grupos = {}
    for nome,padrao in dict(infra_ia='modules\\ai\\',transcricao='dados\\transcricoes\\').items():
        grupo = [h for k,h in hashes.items() if k.startswith(padrao)]
        grupos[nome] = bool(grupo) and all(h['atual']==h['rollback'] for h in grupo)
    resultado = dict(atual_protegida=integridade,arquivos=quantidade,bytes=bytes_total,
                     ausentes=ausentes,diferentes=diferentes,reparse=reparse,links_materializados=links_ok,
                     arquivos_editoriais_modificados=diff_code,
                     preservados=grupos,hashes=hashes,comparacao_zip_arquivo_a_arquivo=comparacao)
    destino = ROLLBACK/'docs/rollback_2809_integridade.json'
    destino.write_text(json.dumps(resultado,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in resultado.items() if k not in ('hashes','comparacao_zip_arquivo_a_arquivo')},ensure_ascii=True))
    if not integridade or not all(grupos.values()):
        raise SystemExit('Integridade incompleta; nao substituir projeto principal.')
    return resultado


if __name__=='__main__':
    executar()
