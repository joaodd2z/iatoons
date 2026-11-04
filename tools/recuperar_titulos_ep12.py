"""Title-only replay of six existing EP12 contexts; never invokes recovery/video code."""
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from modules import titulos_v2 as V
from modules.title_recovery_v3 import recuperar_contextos
from modules.config_ia import carregar_config_ia
from modules.ai.fallback import OrcamentoChamadas


def executar(api=False,revalidar=False):
    origem = ROOT/'dados/agencia/auto_recovery/S07E12.json'
    dados = json.loads(origem.read_text(encoding='utf-8'))
    if revalidar:
        originais = {c['id']:c for c in dados['cortes']}
        dados = json.loads(origem.with_name('S07E12_titulos_v3.json').read_text(encoding='utf-8'))
        for c in dados['cortes']:
            if originais[c['id']]['titulo_aprovado']:
                c['titulos_v2'] = deepcopy(originais[c['id']]['titulos_v2'])
    antes = deepcopy(dados)
    contextos = [V.ContextoTitulo(**c['titulos_v2']['contexto']) for c in dados['cortes']]
    resultados = {ctx.historia_id:deepcopy(c['titulos_v2']) for ctx,c in zip(contextos,dados['cortes'])}
    cfg = carregar_config_ia()
    budget = OrcamentoChamadas(2 if api else 0)
    inicio = perf_counter()
    novos = recuperar_contextos(contextos,resultados,cfg,orcamento=budget)
    aprovados = 0
    for ctx,c in zip(contextos,dados['cortes']):
        r = novos[ctx.historia_id]
        if r['top']:
            principal = r['top'][0]
            assert V.avaliar_titulo(principal,ctx,V.carregar_historico()).aprovado
            c['titulo'] = principal['texto']
            c['titulo_capa'] = V.capa_coerente(principal['texto'],ctx,principal['titulo_capa'])
            c['titulo_aprovado'] = True
            aprovados += 1
        c['titulos_v2'] = r
        chave = V._hash(dict(contexto=asdict(ctx),modelo=cfg.modelo,provider=cfg.provider_ativo,
                             versao=V.TITLE_RULES_VERSION))
        V._salvar(V.TITULOS_DIR/'candidatos'/f'{chave}.json',dict(versao=V.TITLE_RULES_VERSION,
            candidatos=r['candidatos'],historico_rodadas=r.get('historico_rodadas',[]),recovery_v3=r.get('recovery_v3',{})))
    assert [c['remix'] for c in antes['cortes']]==[c['remix'] for c in dados['cortes']]
    assert [c['titulos_v2']['contexto'] for c in antes['cortes']]==[c['titulos_v2']['contexto'] for c in dados['cortes']]
    dados.update(prontos=aprovados,revisao=6-aprovados,antes=antes.get('antes',dict(prontos=antes['prontos'],revisao=antes['revisao'])),
                 chamadas_title_recovery=budget.usadas+(antes.get('chamadas_title_recovery',0) if revalidar else 0),
                 tempo_title_recovery_s=perf_counter()-inicio+(antes.get('tempo_title_recovery_s',0) if revalidar else 0),
                 timelines_preservadas=True)
    V._salvar(origem.with_name('S07E12_titulos_v3.json'),dados)
    print(json.dumps(dict(antes=dados['antes'],prontos=aprovados,revisao=6-aprovados,
                         chamadas=dados['chamadas_title_recovery'],tempo_s=dados['tempo_title_recovery_s'],
                         titulos=[c['titulo'] for c in dados['cortes']]),ensure_ascii=True))
    return dados


if __name__=='__main__':
    executar('--api' in sys.argv,'--revalidar' in sys.argv)
