"""Cached episode regression; title API requires the explicit --titulos flag."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from modules.agencia.auto_recovery import auto_recuperar
from modules.agencia.editorial import ListaPlanos
from modules.agencia.titulos_manual import validar_para_exportacao
from modules.config_ia import carregar_config_ia
from modules.transcricao import carregar_transcricao
from modules.titulos_v2 import gerar_titulos_v2, reavaliar_existentes
from modules.ai.fallback import OrcamentoChamadas


def executar(episodio,titulos=False,cache_titulos=False):
    arquivos = {
        'S07E12':('cf72a56c1d3b56d562dd3ef52d68fa38371cceaa189c98a39275217016b761ad',
                  '959279ed9b068390d38d9db3777c821df7b0028948541211d568e92ff3d278b4'),
        'S07E11':('db972adc2049a2c9dfa68ff69302490cb013ee26e166a14f10082942eff11ea4',
                  'fa36b5105bf6132717777106319320ff88cca561cd949f93473b41335940ae80')}
    t_id,a_id = arquivos[episodio]
    t = carregar_transcricao(ROOT/'dados/transcricoes'/f'{t_id}.json')
    dados = json.loads((ROOT/'dados/agencia/analises'/f'{a_id}.json').read_text(encoding='utf-8'))
    cfg = carregar_config_ia()
    cfg.max_cortes = 10
    inicio = perf_counter()
    lista = auto_recuperar(ListaPlanos([]),t,cfg,sementes=dados['historias_brutas'],
                          chamadas_historicas=dados.get('diagnostico',{}).get('chamadas_feitas',0))
    budget = OrcamentoChamadas(2 if titulos else 0)
    automaticos = [p for p in lista if p.estado_editorial!='revisao']
    if titulos or cache_titulos:
        gerar_titulos_v2(automaticos,t,cfg,orcamento=budget)
    else:
        reavaliar_existentes(automaticos,t)
    prontos = []
    for p in automaticos:
        try:
            validar_para_exportacao(p,t)
        except ValueError:
            continue
        prontos.append(p)
    relatorio = dict(episodio=episodio,**asdict(lista.diagnostico),prontos=len(prontos),
                     revisao=len(lista)-len(prontos),tempo_total_s=perf_counter()-inicio,
                     chamadas_titulos=budget.usadas,
                     cortes=[dict(id=p.id,inicio=p.inicio,fim=p.fim,remix=p.validacao_remix,
                                  titulo=p.titulo_escolhido,titulo_aprovado=p.titulo_auto_aprovado,
                                  titulos_v2=p.titulos_v2) for p in lista])
    destino = ROOT/'dados/agencia/auto_recovery'/f'{episodio}.json'
    destino.parent.mkdir(parents=True,exist_ok=True)
    destino.write_text(json.dumps(relatorio,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in relatorio.items() if k not in ('cortes','reparos','motivos_rejeicao','motivos_iniciais')},ensure_ascii=True))
    return lista,t,relatorio


if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--episodio',choices=['S07E11','S07E12'],default='S07E12')
    parser.add_argument('--titulos',action='store_true')
    parser.add_argument('--cache-titulos',action='store_true')
    args = parser.parse_args()
    executar(args.episodio,args.titulos,args.cache_titulos)
