"""September narrative discovery with current transport and final safety gates.

The confirmed ZIP predates Agency. Its windowing/prompt and chronological remix
assembly are adapted here, without minimum targets, subdivision or auto-recovery.
"""
from copy import deepcopy
from dataclasses import asdict,dataclass,field
import hashlib
import json
from pathlib import Path

from modules.agencia.editorial import ListaPlanos,frases_no_intervalo,validar_planos
from modules.agencia.recuperacao import DiagnosticoRemix
from modules.agencia.story_identity import selecionar
from modules.analise_ia import _janelas
from modules.remix_narrativo import _prompt_remix
from modules.preset import DADOS_DIR

VERSION = 'rollback-editorial-20260928-v1'


@dataclass
class DiagnosticoRollback(DiagnosticoRemix):
    modo: str = 'ROLLBACK 28/09'
    minimum_target: int = 0
    automaticos: int = 0
    revisaveis: int = 0
    story_identities: int = 0
    duplicatas_narrativas: int = 0
    chamadas_historicas: int = 0

    def resumo(self):
        return (f'{self.automaticos} remixes; {self.story_identities} historias distintas. '
                f'{self.duplicatas_narrativas} duplicatas narrativas excluidas. '
                'Qualidade e diversidade, sem quantidade minima obrigatoria.')


def chave(transcricao,config):
    from modules.agencia.editorial import _hash_transcricao
    dados = [VERSION,transcricao.video_id,_hash_transcricao(transcricao),config.provider_ativo,config.modelo]
    return DADOS_DIR/'agencia/rollback_2809'/f'{hashlib.sha256(json.dumps(dados).encode()).hexdigest()}.json'


def montar(sementes,transcricao,config,diag=None):
    diag = diag or DiagnosticoRollback()
    planos = []
    for indice,original in enumerate(sementes):
        diag.candidatos_recebidos += 1
        if not isinstance(original,dict):
            diag.motivos_rejeicao.append(f'Candidato {indice+1}: estrutura invalida.')
            continue
        item = deepcopy(original)
        item['segmentos'] = item.get('segmentos') or item.get('segmentos_narrativos') or []
        if item.get('remix_recomendado') is False:
            item['usar_remix'] = False
        item.setdefault('usar_remix',True)
        item['justificativa_remix'] = item.get('justificativa_remix') or item.get('continuidade') or item.get('justificativa','')
        if not item.get('gancho'):
            diag.motivos_rejeicao.append(f'Candidato {indice+1}: sem gancho narrativo declarado.')
            continue
        # The original remix schema assembled complete source segments: it did not
        # remove dialogue inside a scene or compact pauses inside complete speech.
        item['remover'] = []
        try:
            item['falas_protegidas'] = [dict(inicio=float(s['inicio']),fim=float(s['fim']))
                                        for s in item['segmentos']]
        except (TypeError,ValueError,KeyError):
            diag.motivos_rejeicao.append(f'Candidato {indice+1}: intervalos invalidos.')
            continue
        validos,rejeicoes = validar_planos([item],transcricao,1,exigir_titulos=False)
        diag.motivos_rejeicao.extend(rejeicoes)
        diag.candidatos_parseados += 1
        for p in validos:
            p.id = hashlib.sha256(json.dumps([transcricao.video_id,p.segmentos,p.gancho],sort_keys=True).encode()).hexdigest()[:16]
            p.story_parent_id = original.get('story_parent_id','')
            p.estado_editorial,p.status,p.selecionado = 'automatico','pendente',True
            planos.append(p)
    selecionados,duplicados = selecionar(planos,transcricao,config.max_cortes)
    diag.duplicatas_narrativas = len(duplicados)
    diag.motivos_rejeicao.extend(f"Duplicata narrativa {d['candidate_id']}: mesma historia de {d['mantido']}" for d in duplicados)
    diag.automaticos = diag.story_identities = len(selecionados)
    diag.finalizar(selecionados)
    lista = ListaPlanos(selecionados,diag.motivos_rejeicao,len(sementes),diag)
    lista.brutos = list(sementes)
    lista.brutos_aprovados = [p.como_dict() for p in selecionados]
    lista.respostas_provider = []
    return lista


def prompt_2809(janela,maximo):
    # Exact narrative instructions from 28/09; only the current Agency wire schema
    # is adapted so existing providers/comparison UI keep their contracts.
    antigo = _prompt_remix(janela,maximo)
    return (antigo.replace('"remixes"','"historias"').replace('"segmentos_narrativos"','"segmentos"')
            .replace('"justificativa"','"justificativa_remix"').replace('"remix_recomendado"','"usar_remix"')
            +'\nDiversidade: no maximo uma proposta por historia/situacao/conflito. '
             'Hooks diferentes da mesma historia nao sao novas historias. Quantidade e limite, '
             'nao meta obrigatoria. Selecione historias realmente diferentes; preserve setup e payoff. '
             'Use o schema da Agencia com titulos [{"texto":"...","tipo":"curiosidade"}], '
             'estrutura de preparacao/conflito/conclusao quando sustentada pelas falas. '
             'Nao invente historias, nao subdivida uma historia para preencher quantidade.')


def analisar(transcricao,config,provedor=None,usar_cache=True,cancelada_ref=None,progresso_cb=None,*,prompt_factory=None):
    from modules.agencia import editorial as E
    from modules.ai.cache_fallback import carregar_grupos,salvar_grupos
    from modules.ai.chat_json import selecionar_modelo_disponivel
    from modules.ai.errors import ErroProvider
    from modules.transcricao import transcricao_corresponde_origem
    config.validar()
    if not transcricao.segmentos:
        raise ValueError('Transcricao sem falas.')
    if Path(transcricao.video_path).exists() and not transcricao_corresponde_origem(transcricao):
        raise ValueError('A transcricao nao corresponde ao video atual.')
    diag = DiagnosticoRollback()
    destino = chave(transcricao,config)
    if usar_cache:
        for caminho in (destino,E.caminho_cache_agencia(transcricao,config)):
            dados = carregar_grupos(caminho,config,'historias_brutas')
            if dados is None and caminho.is_file():
                try:
                    dados = json.loads(caminho.read_text(encoding='utf-8'))
                except (OSError,ValueError):
                    continue
            if not dados or dados.get('video_id')!=transcricao.video_id or dados.get('hash_transcricao')!=E._hash_transcricao(transcricao):
                continue
            diag.chamadas_historicas = dados.get('diagnostico',{}).get('chamadas_feitas',0)
            lista = montar(dados.get('historias_brutas',[]),transcricao,config,diag)
            if progresso_cb:
                progresso_cb(100,'Cache editorial revalidado. '+diag.resumo())
            return lista
    if getattr(provedor,'somente_cache',False):
        return montar([],transcricao,config,diag)
    provedor = provedor or E.criar_provedor_agencia(config)
    modelos = provedor.listar_modelos()
    modelo = selecionar_modelo_disponivel(provedor,config.modelo,modelos)
    janelas = _janelas(transcricao,config)[:config.max_chamadas_episodio]
    brutos,respostas = [],[]
    for i,janela in enumerate(janelas):
        if cancelada_ref and cancelada_ref.cancelada:
            raise RuntimeError('Analise cancelada.')
        if getattr(provedor,'gerencia_orcamento',False) and provedor.restantes==0:
            break
        if progresso_cb:
            progresso_cb(10+70*i/max(1,len(janelas)),'Procurando historias diferentes...')
        antes = provedor.restantes if getattr(provedor,'gerencia_orcamento',False) else None
        try:
            prompt = (prompt_factory or prompt_2809)(janela,config.max_cortes)
            resposta = provedor.analisar(modelo,prompt)
            itens = resposta.get('historias',resposta.get('remixes',[]))
            if not isinstance(itens,list):
                raise ValueError('Resposta sem historias.')
            brutos.extend(itens)
            respostas.append((resposta,itens))
            diag.partes_analisadas.append(dict(inicio=janela[0].inicio,fim=janela[-1].fim,etapa='descoberta_2809',sucesso=True))
        except ErroProvider as erro:
            if getattr(erro,'interromper_analise',False):
                raise
            diag.falhas.append(str(erro))
        finally:
            diag.chamadas_feitas += max(1,antes-provedor.restantes) if antes is not None else 1
    if not respostas and diag.falhas:
        raise ErroProvider('Nenhuma janela foi analisada: '+diag.falhas[0])
    lista = montar(brutos,transcricao,config,diag)
    lista.respostas_provider = respostas
    meta = dict(versao=VERSION,video_id=transcricao.video_id,hash_transcricao=E._hash_transcricao(transcricao),
                modelo=config.modelo,diagnostico=asdict(diag),historias_aprovadas=lista.brutos_aprovados)
    if config.fallback_automatico and respostas and all('provider_utilizado' in r for r,_ in respostas):
        salvar_grupos(destino,config,respostas,'historias_brutas',meta,lambda real:chave(transcricao,real))
    else:
        from modules.titulos_v2 import _salvar
        _salvar(destino,dict(meta,historias_brutas=brutos))
    if progresso_cb:
        progresso_cb(100,diag.resumo())
    return lista
