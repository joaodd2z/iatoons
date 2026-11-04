"""Compare cached observed production output with offline rollback selection."""
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from modules.agencia.editorial import analisar_agencia,_SomenteCache
from modules.agencia.story_identity import selecionar
from modules.agencia.timeline import construir_timeline
from modules.agencia.remix import verificar_plano_remix
from modules.transcricao import carregar_transcricao
from modules.config_ia import carregar_config_ia
from modules.titulos_v2 import montar_contexto,gerar_titulos_v2
from modules.ai.fallback import OrcamentoChamadas


def executar():
    t = carregar_transcricao(ROOT/'dados/transcricoes/cf72a56c1d3b56d562dd3ef52d68fa38371cceaa189c98a39275217016b761ad.json')
    config = carregar_config_ia()
    config.max_cortes = 10
    atual = json.loads((ROOT/'dados/agencia/auto_recovery/S07E12.json').read_text(encoding='utf-8'))
    parents = {}
    for reparo in atual['reparos']:
        if not reparo.get('filhos'):
            continue
        d = reparo['problemas'][0]['dados']
        family = hashlib.sha256(json.dumps([t.video_id,d['inicio'],d['fim']]).encode()).hexdigest()[:20]
        for filho in reparo['filhos']:
            parents[filho] = family
    itens,contextos = [],{}
    for c in atual['cortes']:
        p = SimpleNamespace(id=c['id'],video_id=t.video_id,inicio=c['inicio'],fim=c['fim'],
                            confianca=1,validacao_remix=c['remix'],story_parent_id=parents.get(c['id'],''))
        itens.append(p)
        contextos[p.id] = c['titulos_v2']['contexto']
    unicos,duplicados = selecionar(itens,t,10,contextos)
    lista = analisar_agencia(t,config,provedor=_SomenteCache())
    assinatura = [(p.segmentos,p.gancho,p.estrutura,p.story_identity) for p in lista]
    antes = deepcopy(assinatura)
    gerar_titulos_v2(lista,t,config,orcamento=OrcamentoChamadas(0))
    assert antes==[(p.segmentos,p.gancho,p.estrutura,p.story_identity) for p in lista]
    cortes = []
    for p in lista:
        c = montar_contexto(p,t)
        erros = verificar_plano_remix(p,t)
        assert not erros,erros
        cortes.append(dict(story_identity=p.story_identity,historia=p.descricao,conflito=c.conflito,
                           gancho=c.gancho,payoff=c.payoff,inicio=p.inicio,fim=p.fim,
                           remix_score=p.validacao_remix['score_remix'],titulo=p.titulo,
                           titulo_aprovado=p.titulo_auto_aprovado,timeline=asdict(construir_timeline(p,t)),
                           plano=p.como_dict()))
    resultado = dict(episodio='Family.Guy.S07E12',
        atual=dict(cortes=len(itens),story_identities=len(unicos),duplicatas_narrativas=len(duplicados),
                   historias_repetidas_com_hooks_diferentes=len(duplicados),
                   diversidade=len(unicos)/len(itens),duplicatas=duplicados),
        rollback=dict(cortes=len(lista),story_identities=len({p.story_identity for p in lista}),
                      duplicatas_narrativas=0,historias_repetidas_com_hooks_diferentes=0,
                      candidatos_repetidos_descartados=lista.diagnostico.duplicatas_narrativas,
                      diversidade=1,minimum_target=lista.diagnostico.minimum_target,
                      chamadas_ia=0,cortes_detalhados=cortes,diagnostico=asdict(lista.diagnostico)),
        nota='Baseline atual observado em cache, nao uma nova chamada de IA. Identidade avaliada pelo mesmo criterio nos dois conjuntos.',
        limitacao='O ZIP nao contem Modo Agencia. Descoberta/janelas/prompt do Remix antigo foram adaptados ao contrato atual; gates temporais e renderer foram preservados.')
    destino = ROOT/'dados/agencia/rollback_2809/comparativo_ep12.json'
    destino.parent.mkdir(parents=True,exist_ok=True)
    destino.write_text(json.dumps(resultado,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:{a:b for a,b in v.items() if a not in ('cortes_detalhados','diagnostico','duplicatas')}
                     for k,v in resultado.items() if k in ('atual','rollback')},ensure_ascii=True))
    return lista,t,resultado


if __name__=='__main__':
    executar()
