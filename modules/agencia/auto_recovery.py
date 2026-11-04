"""Reason-directed, bounded Agency repair. The Remix Validator is unchanged."""
from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
from time import perf_counter

from modules.agencia.editorial import (
    ListaPlanos, PlanoAgencia, frases_no_intervalo, sobreposicao_relativa, validar_planos,
)
from modules.agencia.producao import (
    evidenciar, falas, janelas_locais, julgar_opcional, marcar_revisao, normalizar, revisavel,
)
from modules.intro import carregar_intro

AUTO_RECOVERY_VERSION = 'agency-auto-recovery-v2'
ACOES_REPARO = {
    'duracao_excessiva': 'subdividir_micro_historias',
    'fala_truncada': 'alinhar_frases_completas',
    'payoff_removido': 'restaurar_payoff_original',
    'sem_reencontro': 'restaurar_chegada_ao_gancho',
    'gancho_invalido': 'testar_ate_tres_hooks_reais',
    'duplicata': 'buscar_proxima_oportunidade',
}


def identidade(dados):
    return hashlib.sha256(json.dumps(dados,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:16]


def motivo_codigo(texto):
    t = normalizar(texto)
    if 'duplicad' in t:
        return 'duplicata'
    if ('duracao' in t or 'historia ocupa' in t or 'gigante' in t) and ('150' in t or '180' in t):
        return 'duracao_excessiva'
    if 'truncad' in t:
        return 'fala_truncada'
    if 'payoff' in t and 'removid' in t:
        return 'payoff_removido'
    if 'reencontra' in t:
        return 'sem_reencontro'
    if any(s in t for s in ('gancho','cronologic','reorganizar','ordem final')):
        return 'gancho_invalido'
    return 'nao_reparavel'


def motivos_estruturados(plano, rejeicoes):
    return [dict(candidate_id=plano.id,motivo=motivo_codigo(m),detalhe=m,
                 acao=ACOES_REPARO.get(motivo_codigo(m)),dados=dict(
                     inicio=plano.inicio,fim=plano.fim,gancho=deepcopy(plano.gancho),
                     estrutura=deepcopy(plano.estrutura),segmentos=deepcopy(plano.segmentos)))
            for m in rejeicoes]


@dataclass
class DiagnosticoAutoRecovery:
    versao: str = AUTO_RECOVERY_VERSION
    modo: str = 'AUTO RECOVERY V2'
    candidatos_iniciais: int = 0
    rejeitados_inicialmente: int = 0
    candidatos_recebidos: int = 0
    candidatos_rejeitados: int = 0
    reparados: int = 0
    novas_oportunidades: int = 0
    automaticos: int = 0
    revisaveis: int = 0
    chamadas_feitas: int = 0
    chamadas_historicas: int = 0
    tempo_s: float = 0
    partes_analisadas: list = field(default_factory=list)
    motivos_rejeicao: list = field(default_factory=list)
    falhas: list = field(default_factory=list)
    reparos: list = field(default_factory=list)
    oportunidades_restantes: int = 0
    motivos_iniciais: list = field(default_factory=list)
    used_story_regions: list = field(default_factory=list)
    used_conflicts: list = field(default_factory=list)
    used_payoffs: list = field(default_factory=list)

    def resumo(self):
        return (f'{self.automaticos} remixes aprovados; {self.revisaveis} para revisao. '
                f'{self.reparados} reparados; {self.novas_oportunidades} novas oportunidades; '
                f'{self.chamadas_feitas} chamadas. ' +
                ('Nenhuma oportunidade sustentada foi encontrada neste episodio.'
                 if not self.automaticos and not self.revisaveis else ''))


def plano_bruto(item, transcricao):
    dados = deepcopy(item.como_dict() if isinstance(item,PlanoAgencia) else item)
    segmentos = dados.get('segmentos') or dados.get('segmentos_narrativos') or []
    limites = [(float(s['inicio']),float(s['fim'])) for s in segmentos]
    if not limites or any(not math.isfinite(i+f) or i<0 or f<=i or f>transcricao.duracao+.5 for i,f in limites):
        raise ValueError('Intervalos invalidos ou fora do episodio.')
    dados['segmentos'] = segmentos
    dados['inicio'],dados['fim'] = min(i for i,_ in limites),max(f for _,f in limites)
    dados.setdefault('video_id',transcricao.video_id)
    dados.setdefault('origem','gemini_agencia')
    dados['id'] = identidade([transcricao.video_id,segmentos,dados.get('gancho'),dados.get('descricao')])
    p = PlanoAgencia.de_dict(dados)
    p.remix_motivo = dados.get('justificativa_remix') or p.remix_motivo
    p.estado_editorial,p.status = 'automatico','pendente'
    p.revisao_humana = {}
    p.corte_aprovado_manualmente = p.titulo_aprovado_manualmente = False
    return p


def avaliar(plano, transcricao):
    if plano.fim-plano.inicio > 150:
        return None,[f'duracao da historia {plano.fim-plano.inicio:.1f}s acima de 150s']
    # Title quality is deliberately evaluated only after video/remix approval.
    validos,motivos = validar_planos([plano.como_dict()],transcricao,1,exigir_titulos=False)
    if not validos:
        return None,motivos
    pronto = validos[0]
    pronto.id = plano.id
    pronto.titulos_v2 = deepcopy(plano.titulos_v2)
    return pronto,[]


def alinhar(plano, transcricao):
    novo = deepcopy(plano)
    frases = frases_no_intervalo(transcricao,0,transcricao.duracao)
    def expandir(item):
        i,f = float(item['inicio']),float(item['fim'])
        for frase in frases:
            if frase.inicio+.025 < i < frase.fim-.025:
                i = frase.inicio
            if frase.inicio+.025 < f < frase.fim-.025:
                f = frase.fim
        return {**item,'inicio':i,'fim':f}
    novo.segmentos = [expandir(s) for s in novo.segmentos]
    novo.inicio = min(s['inicio'] for s in novo.segmentos)
    novo.fim = max(s['fim'] for s in novo.segmentos)
    # Removal boundaries contract inward, never slice a phrase in the retained audio.
    removidos = []
    for r in novo.remover:
        i,f = float(r['inicio']),float(r['fim'])
        for frase in frases:
            if frase.inicio+.025 < i < frase.fim-.025:
                i = frase.fim
            if frase.inicio+.025 < f < frase.fim-.025:
                f = frase.inicio
        if f-i > .1:
            removidos.append({**r,'inicio':i,'fim':f})
    novo.remover = removidos
    # Pauses inside a complete spoken phrase must not become new truncation points.
    novo.falas_protegidas = [dict(inicio=f.inicio,fim=f.fim) for f in frases
                            if novo.inicio-.025<=f.inicio and f.fim<=novo.fim+.025]
    if novo.gancho:
        novo.gancho = expandir(novo.gancho)
    return novo


def proteger(plano, intervalo):
    i,f = float(intervalo['inicio']),float(intervalo['fim'])
    plano.remover = [r for r in plano.remover if min(float(r['fim']),f)-max(float(r['inicio']),i) <= 0]
    if not any(float(s['inicio'])<=i and f<=float(s['fim']) for s in plano.segmentos):
        plano.segmentos.append(dict(inicio=i,fim=f,funcao='consequencia'))
    plano.segmentos.sort(key=lambda s:float(s['inicio']))
    plano.inicio = min(float(s['inicio']) for s in plano.segmentos)
    plano.fim = max(float(s['fim']) for s in plano.segmentos)


def hooks_reais(plano, transcricao):
    frases = [f for f in frases_no_intervalo(transcricao,plano.inicio,plano.fim)
              if f.inicio >= plano.inicio+6 and f.fim <= plano.fim-1.5 and 1.5<=f.fim-f.inicio<=6.5
              and any(float(s['inicio'])-.025<=f.inicio and f.fim<=float(s['fim'])+.025 for s in plano.segmentos)]
    payoff = plano.estrutura.get('conclusao') or plano.estrutura.get('payoff')
    if payoff:
        frases = [f for f in frases if min(f.fim,float(payoff['fim']))-max(f.inicio,float(payoff['inicio']))<=0]
    def forca(f):
        return sum(p in normalizar(f.texto) for p in ('nao','mas','por que','errado','verdade','!','?'))
    return sorted(frases,key=lambda f:(-forca(f),abs((f.inicio+f.fim)/2-(plano.inicio+plano.fim)/2)))[:3]


def reparar(plano, transcricao, motivos, rodada):
    codigos = {m['motivo'] for m in motivos}
    novo = alinhar(plano,transcricao)
    acoes = ['alinhar_frases_completas']
    payoff = plano.estrutura.get('conclusao') or plano.estrutura.get('payoff')
    if 'payoff_removido' in codigos and payoff:
        proteger(novo,payoff)
        # Existing compactor protects reaction and strong-moment ranges.
        novo.estrutura['reacao'] = deepcopy(payoff)
        acoes.append('restaurar_payoff_original')
    if 'sem_reencontro' in codigos and novo.gancho:
        gi = float(novo.gancho['inicio'])
        anteriores = [f for f in frases_no_intervalo(transcricao,novo.inicio,gi) if f.fim <= gi+.025]
        if anteriores:
            anterior = anteriores[-1]
            chegada = dict(inicio=anterior.inicio,fim=gi)
            proteger(novo,chegada)
            novo.estrutura['momento_forte'] = dict(inicio=anterior.inicio,fim=float(novo.gancho['fim']))
            acoes.append('restaurar_chegada_ao_gancho')
    variantes = [novo]
    if 'gancho_invalido' in codigos or ('sem_reencontro' in codigos and rodada == 2):
        variantes = []
        for f in hooks_reais(novo,transcricao):
            variante = deepcopy(novo)
            variante.gancho = dict(inicio=f.inicio,fim=f.fim,motivo=f.texto)
            variante.usar_remix = True
            variante.estrutura['momento_forte'] = dict(inicio=f.inicio,fim=f.fim)
            variantes.append(variante)
        acoes.append('testar_ate_tres_hooks_reais')
    return variantes,acoes


def construir_local(janela, transcricao, evidencia):
    p = PlanoAgencia(janela[0].inicio,janela[-1].fim,video_id=transcricao.video_id,
                     origem='gemini_agencia',confianca=.65,usar_remix=True,
                     segmentos=[dict(inicio=janela[0].inicio,fim=janela[-1].fim,funcao='historia')],
                     descricao=evidencia['sinais']['conflito'][0]['texto'],
                     motivo_escolha='Conflito e consequencia presentes nas falas reais.',
                     remix_motivo='Antecipar conflito real, retornar ao contexto e preservar a consequencia.')
    p.id = identidade([transcricao.video_id,p.segmentos])
    conflitos = evidencia['sinais']['conflito']
    p.estrutura['conflito'] = dict(inicio=conflitos[0]['inicio'],fim=conflitos[0]['fim'])
    consequencias = evidencia['sinais']['consequencia'] or evidencia['sinais']['payoff']
    if consequencias:
        f = consequencias[-1]
        p.estrutura['conclusao'] = dict(inicio=f['inicio'],fim=f['fim'])
    hooks = hooks_reais(p,transcricao)
    if hooks:
        h = hooks[0]
        p.gancho = dict(inicio=h.inicio,fim=h.fim,motivo=h.texto)
    return p


def oportunidades(transcricao, diagnostico, *, descoberta=False):
    regioes = [dict(inicio=i,fim=min(i+180,transcricao.duracao),etapa='micro_historias',
                   avaliados=0,oportunidades=0) for i in range(0,math.ceil(transcricao.duracao),180)]
    resultado = []
    intro = carregar_intro(transcricao)
    for janela in janelas_locais(transcricao,(30,45,60,90,120,150)):
        r = regioes[int(janela[0].inicio//180)]
        r['avaliados'] += 1
        duracao = janela[-1].fim-janela[0].inicio
        if duracao>150 or sum(s.fim-s.inicio for s in janela)/duracao < .25:
            continue
        if intro and intro.ativo and min(janela[-1].fim,intro.fim)-max(janela[0].inicio,intro.inicio)>.025:
            continue
        evidencia = evidenciar(janela)
        if evidencia:
            r['oportunidades'] += 1
            resultado.append((construir_local(janela,transcricao,evidencia),evidencia))
        elif descoberta and len(janela)>=5:
            # Dialogue is an editorial opportunity, not an automatic approval.
            p = PlanoAgencia(janela[0].inicio,janela[-1].fim,video_id=transcricao.video_id,
                             segmentos=[dict(inicio=janela[0].inicio,fim=janela[-1].fim)],
                             descricao='Regiao de dialogo para descoberta editorial',usar_remix=True)
            p.id = identidade([transcricao.video_id,p.segmentos])
            resultado.append((p,dict(score_oportunidade=20+.6*duracao)))
    diagnostico.partes_analisadas = regioes
    return sorted(resultado,key=lambda x:-(x[1]['score_oportunidade']-.4*(x[0].fim-x[0].inicio)))


def subdividir(plano, locais, transcricao=None):
    candidatos = [(deepcopy(p),e) for p,e in locais if plano.inicio-.1<=p.inicio and p.fim<=plano.fim+.1
                  and 45<=p.fim-p.inicio<=150]
    # Ranked natural windows, not a truncation at t=150 or the giant's old payoff.
    if candidatos or transcricao is None:
        return candidatos[:12]
    # Weakly evidenced windows require editorial judgement, never automatic approval.
    janelas = [j for j in janelas_locais(transcricao,(60,90,120,150))
               if plano.inicio-.1<=j[0].inicio and j[-1].fim<=plano.fim+.1
               and 45<=j[-1].fim-j[0].inicio<=150]
    for j in sorted(janelas,key=lambda j:-(j[-1].fim-j[0].inicio))[:12]:
        p = PlanoAgencia(j[0].inicio,j[-1].fim,video_id=transcricao.video_id,
                         origem='micro_historia_pendente',usar_remix=True,
                         descricao=plano.descricao,
                         segmentos=[dict(inicio=j[0].inicio,fim=j[-1].fim)])
        p.id = identidade([transcricao.video_id,p.segmentos,plano.id])
        candidatos.append((p,None))
    return candidatos


def prompt_reparo(itens, transcricao):
    entrada = []
    for p,motivos in itens:
        entrada.append(dict(candidate_id=p.id,inicio=p.inicio,fim=p.fim,problemas=motivos,
                            plano=p.como_dict(),falas=[asdict(f) for f in frases_no_intervalo(transcricao,p.inicio,p.fim)]))
    return ('SOMENTE REMIXES NARRATIVOS: historias REMONTADAS. Repare ou descubra somente estas micro-historias. Nao reanalise o episodio. Preserve conflito, '
            'contexto e consequencia reais; nao invente fatos. Retorne {"historias": [...]} '
            'no contrato da Agencia, com candidate_id. Os intervalos devem permanecer dentro '
            'da micro-historia correspondente e usar somente limites das falas fornecidas. '
            'Hook futuro de 2-6s, retorno ao contexto anterior, chegada ao hook sem repeticao '
            'e payoff preservado. Repare pelos motivos do validator, nao gere a mesma falha.\n'
            + json.dumps(entrada,ensure_ascii=False))


def auto_recuperar(planos, transcricao, config, *, sementes=(), provedor=None,
                   cancelada_ref=None, progresso_cb=None, chamadas_historicas=0, juiz=None):
    inicio = perf_counter()
    target = min(10,max(5,int(config.max_cortes)))
    automaticos = [p for p in planos if p.estado_editorial != 'revisao']
    if len(automaticos)>=target:
        return planos
    diag = DiagnosticoAutoRecovery(candidatos_iniciais=len(sementes),
                                  chamadas_historicas=chamadas_historicas,
                                  chamadas_feitas=getattr(getattr(planos,'diagnostico',None),'chamadas_feitas',0))
    diag.motivos_iniciais = list(getattr(planos,'rejeicoes',[]))
    locais = oportunidades(transcricao,diag)
    _,falha = julgar_opcional(juiz,[p.como_dict() for p,_ in locais[:8]])
    if falha:
        diag.falhas.append(falha)
    aceitos = list(automaticos)
    revisoes,pendentes_ia = [],[]
    usados = set()
    fila = deque()
    def saldo():
        if provedor is None:
            return 0
        valor = getattr(provedor,'restantes',None)
        return valor if isinstance(valor,int) else max(0,config.max_chamadas_episodio-diag.chamadas_feitas)
    permite_ia = lambda: provedor is not None and not getattr(provedor,'somente_cache',False) and saldo()>2
    for item in sementes:
        try:
            fila.append((plano_bruto(item,transcricao),None,True))
        except (ValueError,TypeError,KeyError,AttributeError) as erro:
            diag.rejeitados_inicialmente += 1
            diag.candidatos_rejeitados += 1
            diag.motivos_rejeicao.append(str(erro))
    # Round-robin by episode region prevents spending retries in only one scene.
    por_regiao = {}
    for p,e in locais:
        por_regiao.setdefault(int(p.inicio//180),deque()).append((p,e,False))
    while any(por_regiao.values()):
        for grupo in por_regiao.values():
            if grupo:
                fila.append(grupo.popleft())

    def notificar(texto):
        if cancelada_ref and cancelada_ref.cancelada:
            raise RuntimeError('Auto Recovery cancelado.')
        if progresso_cb:
            progresso_cb(min(90,40+len(aceitos)*5),texto)

    def distinto(p):
        conflito = p.estrutura.get('conflito')
        payoff = p.estrutura.get('conclusao') or p.estrutura.get('payoff')
        return (all(sobreposicao_relativa(p,o)<=.6 for o in aceitos) and
                (not conflito or identidade(conflito) not in diag.used_conflicts) and
                (not payoff or identidade(payoff) not in diag.used_payoffs))

    def aceitar(p):
        if not distinto(p):
            diag.motivos_rejeicao.append(dict(candidate_id=p.id,motivo='duplicata',acao=ACOES_REPARO['duplicata']))
            return False
        p.estado_editorial,p.status,p.selecionado = 'automatico','pendente',True
        aceitos.append(p)
        diag.used_story_regions.append([p.inicio,p.fim])
        if p.estrutura.get('conflito'):
            diag.used_conflicts.append(identidade(p.estrutura['conflito']))
        payoff = p.estrutura.get('conclusao') or p.estrutura.get('payoff')
        if payoff:
            diag.used_payoffs.append(identidade(payoff))
        notificar(f'{len(aceitos)}/{target} cortes aprovados...')
        return True

    while fila and len(aceitos)<target:
        p,evidencia,original = fila.popleft()
        if p.id in usados:
            continue
        usados.add(p.id)
        diag.candidatos_recebidos += 1
        if not original:
            diag.novas_oportunidades += 1
        notificar('Montando remixes... Validando...')
        aprovado,motivos = avaliar(p,transcricao)
        if aprovado and aceitar(aprovado):
            continue
        if aprovado:
            if original:
                diag.rejeitados_inicialmente += 1
            diag.candidatos_rejeitados += 1
            notificar('Buscando novas oportunidades...')
            continue
        if original:
            diag.rejeitados_inicialmente += 1
        estruturados = motivos_estruturados(p,motivos)
        if any(m['motivo']=='duracao_excessiva' for m in estruturados):
            filhos = subdividir(p,locais,transcricao)
            diag.reparos.append(dict(candidate_id=p.id,rodada=1,problemas=estruturados,
                                     acoes=['subdividir_micro_historias'],subdivisoes=len(filhos),
                                     filhos=[c.id for c,_ in filhos],aprovado=False))
            fila.extendleft(reversed([(c,e,False) for c,e in filhos]))
            if not filhos:
                diag.candidatos_rejeitados += 1
            continue
        if p.origem=='micro_historia_pendente':
            pendentes_ia.append((p,[dict(candidate_id=p.id,motivo='descoberta_editorial',
                                        detalhe='Sub-historia requer conflito e consequencia comprovados.')]))
            diag.candidatos_rejeitados += 1
            continue
        if not p.usar_remix or not p.gancho or p.origem not in ('gemini_agencia','remix'):
            diag.candidatos_rejeitados += 1
            diag.motivos_rejeicao.extend(estruturados)
            continue
        for rodada in (1,2):
            if rodada == 2 and permite_ia() and all(m['motivo']=='nao_reparavel' for m in estruturados):
                pendentes_ia.append((p,estruturados))
                break
            notificar(f'Reparando {diag.reparados+1}/{diag.candidatos_recebidos}...')
            variantes,acoes = reparar(p,transcricao,estruturados,rodada)
            registro = dict(candidate_id=p.id,rodada=rodada,problemas=estruturados,
                            acoes=acoes,hooks_testados=len(variantes),aprovado=False)
            diag.reparos.append(registro)
            for variante in variantes:
                aprovado,motivos = avaliar(variante,transcricao)
                if aprovado:
                    break
            if aprovado:
                registro['aprovado'] = True
                registro['resultado'] = dict(inicio=aprovado.inicio,fim=aprovado.fim,gancho=aprovado.gancho,
                                             score=aprovado.validacao_remix['score_remix'])
                if aceitar(aprovado):
                    diag.reparados += 1
                break
            if variantes:
                p = variantes[0]
            estruturados = motivos_estruturados(p,motivos)
        if not aprovado:
            diag.motivos_rejeicao.extend(estruturados)
            evidencia = evidencia or evidenciar(falas(p,transcricao))
            if evidencia and revisavel(p,transcricao):
                marcar_revisao(p,evidencia,motivos)
                revisoes.append(p)
            else:
                diag.candidatos_rejeitados += 1
        notificar('Buscando novas oportunidades...')

    # Each bounded batch consumes new opportunities and preserves title-call reserve.
    while len(aceitos)<target and pendentes_ia and permite_ia():
        lote = [(p,m) for p,m in pendentes_ia if 20<=p.fim-p.inicio<=150][:4]
        ids_lote = {p.id for p,_ in lote}
        pendentes_ia = [(p,m) for p,m in pendentes_ia if p.id not in ids_lote]
        if not lote:
            break
        if lote:
            budget = getattr(provedor,'orcamento',None) or getattr(getattr(provedor,'provedor',None),'orcamento',None)
            maximo = budget.maximo if budget else None
            try:
                antes = saldo()
                if budget:
                    budget.maximo = min(maximo,budget.usadas+max(0,antes-2))
                registros = {}
                for p,m in lote:
                    registro = dict(candidate_id=p.id,rodada=2,acoes=['reparo_editorial_contextual'],
                                    problemas=m,aprovado=False)
                    diag.reparos.append(registro)
                    registros[p.id] = registro
                resposta = provedor.analisar(config.modelo,prompt_reparo(lote,transcricao))
                retornados = set()
                for item in resposta.get('historias',[]):
                    if len(aceitos)>=target:
                        break
                    original = next((p for p,_ in lote if item.get('candidate_id')==p.id),None)
                    if not original or original.id in retornados:
                        continue
                    retornados.add(original.id)
                    p = plano_bruto(item,transcricao)
                    if p.inicio<original.inicio-.025 or p.fim>original.fim+.025:
                        continue
                    p.id = original.id
                    p = alinhar(p,transcricao)
                    aprovado,_ = avaliar(p,transcricao)
                    registros[original.id]['aprovado'] = bool(aprovado)
                    if aprovado:
                        if aceitar(aprovado):
                            diag.reparados += 1
            except Exception as erro:
                if cancelada_ref and cancelada_ref.cancelada:
                    raise RuntimeError('Auto Recovery cancelado.') from None
                diag.falhas.append(f'Reparo editorial indisponivel: {type(erro).__name__}')
            finally:
                if budget:
                    budget.maximo = maximo
                diag.chamadas_feitas += max(1,antes-saldo()) if hasattr(provedor,'restantes') else 1
    diag.automaticos = len(aceitos)
    for r in diag.reparos:
        if 'filhos' in r:
            r['filhos_aprovados'] = sum(p.id in r['filhos'] for p in aceitos)
            r['aprovado'] = r['filhos_aprovados']>0
    revisoes = [p for p in revisoes if all(sobreposicao_relativa(p,a)<=.6 for a in aceitos)]
    finais = list(aceitos)
    for p in revisoes:
        if len(finais)>=target:
            break
        if all(sobreposicao_relativa(p,a)<=.6 for a in finais):
            finais.append(p)
    diag.revisaveis = len(finais)-len(aceitos)
    diag.candidatos_rejeitados = max(0,diag.candidatos_recebidos-len(finais))
    diag.oportunidades_restantes = len(fila)+len(pendentes_ia)
    if pendentes_ia and len(aceitos)<target:
        diag.falhas.append('Oportunidades editoriais pendentes: provider indisponivel ou saldo reservado para titulos.')
    diag.tempo_s = perf_counter()-inicio
    resultado = ListaPlanos(finais,diag.motivos_rejeicao,diag.candidatos_recebidos,diag)
    resultado.brutos = [i.como_dict() if isinstance(i,PlanoAgencia) else i for i in sementes]
    resultado.brutos_aprovados = [p.como_dict() for p in aceitos]
    resultado.respostas_provider = getattr(planos,'respostas_provider',[])
    return resultado
