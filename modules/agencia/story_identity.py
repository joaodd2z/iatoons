"""Narrative identity independent of hooks, titles and requested quantity."""
from collections import Counter
from dataclasses import asdict,dataclass
from difflib import SequenceMatcher
import hashlib
import json
import re

from modules.titulos_v2 import normalizar,tokens

STOP = set('amigos historia cena absurdo inesperada inesperado comica comico situacao '
           'forma tenta tentam tentando revelando resultando resultado acaba terminar '
           'agora gente pessoal quero tenho coisa aqui entao disse dizer claro bem bom '
           'voce voces nos eles ela ele tudo nada ah oi olha desculpe '.split())
ALIASES = {'prisao':'cadeia','preso':'cadeia','presos':'cadeia','detido':'cadeia',
           'felino':'gato','xixi':'urina','canabis':'maconha','cannabis':'maconha',
           'quagmaier':'quagmire','quahogli':'quahog'}


def termos(texto):
    return {ALIASES.get(t,t) for t in tokens(texto)-STOP}


def similar(a,b):
    return len(a&b)/max(1,min(len(a),len(b)))


def intervalo(p):
    return float(p.inicio),float(p.fim)


@dataclass
class IdentidadeNarrativa:
    video_id: str
    inicio: float
    fim: float
    personagens: list
    evento: list
    conflito: list
    setup: list
    payoff: list
    parent: str = ''

    def chave(self):
        # Hook timestamps, title wording and candidate UUID never participate.
        dados = asdict(self)
        return hashlib.sha256(json.dumps(dados,sort_keys=True).encode()).hexdigest()[:20]


def contexto_de_plano(p,t):
    from modules.agencia.editorial import frases_no_intervalo
    frases = frases_no_intervalo(t,p.inicio,p.fim)
    def zona(nome):
        iv = p.estrutura.get(nome)
        return ' '.join(f.texto for f in frases if iv and f.inicio<float(iv['fim']) and f.fim>float(iv['inicio']))
    falas = [dict(texto=f.texto) for f in frases]
    texto = ' '.join(f.texto for f in frases)
    nomes = Counter(ALIASES.get(normalizar(n),normalizar(n)) for n in re.findall(r'\b[A-ZÀ-Ý][a-zà-ÿ]{2,}\b',texto)
                    if normalizar(n) not in STOP|set('uma nao sou meu minha seu sua porque quando quem temos nossa obrigado calma vou vamo puxa sou por qual'.split()))
    personagens = [n for n,q in nomes.most_common(4) if q>=2]
    return dict(resumo=p.descricao,conflito=zona('conflito') or p.motivo_escolha or p.descricao,
                situacao_inicial=' '.join(f.texto for f in frases[:3]),
                payoff=zona('conclusao') or zona('payoff') or ' '.join(f.texto for f in frases[-3:]),
                evidencias=falas,personagens=personagens)


def extrair(p,t=None,contexto=None):
    c = contexto if contexto is not None else contexto_de_plano(p,t)
    personagens = c.get('personagens') or [c.get('personagem_central','')]
    nomes = {ALIASES.get(normalizar(n),normalizar(n)) for n in personagens if n and 'identificado' not in normalizar(n)}
    return IdentidadeNarrativa(p.video_id,p.inicio,p.fim,sorted(nomes),
        sorted(termos(c.get('resumo',''))-nomes),sorted(termos(c.get('conflito',''))-nomes),
        sorted(termos(c.get('situacao_inicial',''))-nomes),sorted(termos(c.get('payoff',''))-nomes),
        getattr(p,'story_parent_id',''))


def mesma_historia(a,b):
    if a.video_id!=b.video_id:
        return False
    comum = max(0,min(a.fim,b.fim)-max(a.inicio,b.inicio))
    overlap = comum/max(.001,min(a.fim-a.inicio,b.fim-b.inicio))
    distancia = max(0,max(a.inicio,b.inicio)-min(a.fim,b.fim))
    if a.parent and a.parent==b.parent:
        return True
    if overlap>=.5:
        return True
    personagens = bool(set(a.personagens)&set(b.personagens))
    evento = similar(set(a.evento),set(b.evento))
    conflito = similar(set(a.conflito),set(b.conflito))
    setup = similar(set(a.setup),set(b.setup))
    payoff = similar(set(a.payoff),set(b.payoff))
    if personagens and distancia<=30 and overlap>=.15 and max(evento,conflito,setup)>=.4:
        return True
    # Repeated situation after a temporal gap requires matching narrative anchors.
    return personagens and evento>=.65 and conflito>=.65 and max(setup,payoff)>=.65


def selecionar(planos,transcricao,limite=10,contextos=None):
    itens = list(planos)
    features = [extrair(p,transcricao,(contextos or {}).get(p.id)) for p in itens]
    grupos = list(range(len(itens)))
    def raiz(i):
        while grupos[i]!=i:
            i = grupos[i]
        return i
    for i in range(len(itens)):
        for j in range(i):
            if mesma_historia(features[i],features[j]):
                grupos[raiz(i)] = raiz(j)
    familias = {}
    for i,p in enumerate(itens):
        familias.setdefault(raiz(i),[]).append((p,features[i]))
    aceitos,rejeicoes = [],[]
    for familia in familias.values():
        # A canonical family key does not depend on which hook won the ranking.
        canonica = min((f for _,f in familia),key=lambda f:(f.inicio,f.fim,f.chave()))
        identidade = canonica.chave()
        ranking = sorted(familia,key=lambda pf:(-pf[0].confianca,-pf[0].validacao_remix.get('score_remix',0),pf[0].inicio))
        for p,f in ranking:
            p.story_identity = identidade
            p.story_signature = asdict(f)
        aceitos.append(ranking[0][0])
        rejeicoes.extend(dict(candidate_id=p.id,story_identity=identidade,motivo='duplicata_narrativa',
                              mantido=ranking[0][0].id) for p,_ in ranking[1:])
    aceitos.sort(key=lambda p:(-p.confianca,-p.validacao_remix.get('score_remix',0),p.inicio))
    return sorted(aceitos[:max(0,int(limite))],key=lambda p:p.inicio),rejeicoes
