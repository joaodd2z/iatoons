"""Local content recovery and explicit editorial review, independent of providers."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from hashlib import sha256
import json
import math
import re
import unicodedata
from queue import Queue, Empty
from threading import Thread

REVISAO = "REVISAO_HUMANA"
VERSAO = "content-recovery-v1"
STOP = set("a o os as de da do das dos um uma uns umas e ou que quem como para por com em no na nos nas ao aos se eu tu ele ela eles elas voce voces meu minha seu sua isso isto aqui ali la sim nao mas bem muito mais menos so ja vai vou foi ser ter tem esta estou pode porque quando tudo agora entao coisa coisas fazer fez gente ainda todos mesmo todos nos me te lhe ne ta bom sabe sera seria pelo pela voce tambem sobre dessa desse essas esses esse essa ate sem vamos somos mim dele dela nos nosso nossa onde assim preciso precisa quer quero queria qual dai disse dizer sinto tenho tinha temos cada entre dai nada tanto quanto sim claro".split())
SINAIS = {
    "conflito": r"\b(nao|errado|contra|recus\w*|proibid\w*|problema|menti\w*|odi\w*|destruir|expuls\w*)\b",
    "setup": r"\b(quero|vamos|precis\w*|trabalho|plano|trouxe|peg\w*)\b",
    "escalada": r"\b(mas|agora|ainda|todos|ninguem|mais|pior)\b",
    "consequencia": r"\b(porque|desde|entao|por isso|verdade|acontec\w*|expuls\w*|marginaliz\w*)\b",
    "payoff": r"\b(verdade|consegui\w*|resolv\w*|descobri\w*|menti\w*)\b",
    "momento_forte": r"[!?]|\b(queima|destruir|perigo|odi\w*|chocado|terrorista)\b",
    "mudanca": r"\b(agora|desde|antes|depois|virou|tornou|na verdade)\b",
}
STOP.update('acho nunca sempre pessoas anos dias dizer disse olhos olhar sabe falar fazendo ficar fosse seria dessa jeito apenas outro outra nossos suas seus grande'.split())
STOP.update('pegar colocar passar levar dar pode vamos estou estava estiver tinha disseram falei sabe fazendo sendo'.split())


def normalizar(texto):
    return ''.join(c for c in unicodedata.normalize('NFD', texto.casefold())
                   if unicodedata.category(c) != 'Mn')


def termos(texto):
    return {w for w in re.findall(r"[a-z]{4,}", normalizar(texto)) if w not in STOP}


def falas(plano, transcricao):
    return [s for s in transcricao.segmentos if s.texto.strip() and any(
        float(i['inicio']) - .025 <= s.inicio and s.fim <= float(i['fim']) + .025
        for i in plano.segmentos)]


def evidenciar(segmentos):
    """Conservative lexical evidence, not an assertion of semantic approval."""
    if len(segmentos) < 5:
        return None
    texto = ' '.join(s.texto.strip() for s in segmentos)
    if len(texto.split()) < 35:
        return None
    # A recurring concrete subject must span the window, not just its first line.
    meio = len(segmentos) // 2
    recorrentes = termos(' '.join(s.texto for s in segmentos[:meio])) & termos(
        ' '.join(s.texto for s in segmentos[meio:]))
    contagem = Counter(w for s in segmentos for w in termos(s.texto))
    assuntos = sorted((w for w in recorrentes if contagem[w] >= 3),
                      key=lambda w: (-contagem[w], w))
    sinais = {nome: [dict(inicio=s.inicio, fim=s.fim, texto=s.texto.strip())
                     for s in segmentos if re.search(padrao, normalizar(s.texto))]
              for nome, padrao in SINAIS.items()}
    if not assuntos or len(sinais['conflito']) < 2 or not (
            sinais['setup'] or sinais['consequencia']):
        return None
    relacionados = [s for s in segmentos if termos(s.texto) & set(assuntos)]
    if any(b.inicio-a.fim > 35 for a,b in zip(relacionados,relacionados[1:])):
        return None
    if not any(termos(s.texto) & set(assuntos) for s in segmentos[-3:]):
        return None
    # A scene with no concrete subject connection in a whole temporal third is unsafe.
    presentes = set(assuntos)
    for parte in (segmentos[:len(segmentos)//3], segmentos[len(segmentos)//3:2*len(segmentos)//3],
                  segmentos[2*len(segmentos)//3:]):
        presentes &= set().union(*(termos(s.texto) for s in parte))
    if not presentes:
        return None
    assuntos = [a for a in assuntos if a in presentes]
    score = min(100, 15 * sum(bool(v) for v in sinais.values()))
    return dict(assuntos=assuntos, sinais=sinais, texto_fonte=texto, score_oportunidade=score)


def problemas_tecnicos(plano, transcricao, *, montagem=False, intro=None, timeline=None):
    problemas = []
    if plano.video_id and plano.video_id != transcricao.video_id:
        problemas.append('Plano pertence a outro episodio.')
    try:
        limites = [(float(s['inicio']), float(s['fim'])) for s in plano.segmentos]
        if not limites or any(not math.isfinite(i+f) or i < 0 or f <= i or
                              f > transcricao.duracao + .025 for i, f in limites):
            problemas.append('Intervalos invalidos ou fora do episodio.')
        if not math.isfinite(plano.inicio + plano.fim) or not 20 <= plano.fim-plano.inicio <= 180:
            problemas.append('Duracao da historia fora de 20-180s.')
        if any(i < plano.inicio-.025 or f > plano.fim+.025 for i, f in limites):
            problemas.append('Segmento fora da historia.')
        ordenados = sorted(limites)
        if any(b[0] < a[1]-.025 or b[0]-a[1] > 45 for a,b in zip(ordenados, ordenados[1:])):
            problemas.append('Sobreposicao ou salto grave entre segmentos.')
        if intro and intro.ativo and any(min(f,intro.fim)-max(i,intro.inicio) > .025 for i,f in limites):
            problemas.append('Intervalos atravessam a intro.')
        if not falas(plano, transcricao):
            problemas.append('Nenhum dialogo ancorado nos intervalos.')
        for item in [*plano.remover,*plano.estrutura.values(),*([plano.gancho] if plano.gancho else [])]:
            i,f = float(item['inicio']),float(item['fim'])
            if not math.isfinite(i+f) or i < plano.inicio-.025 or f > plano.fim+.025 or f <= i:
                problemas.append('Gancho/estrutura/remocao com limites invalidos.')
        if montagem:
            from modules.agencia.timeline import construir_timeline, OpcoesEdicao
            tl = timeline or construir_timeline(plano, transcricao, intro, OpcoesEdicao())
            if tl.tipo != 'remix' or not tl.gancho or len(tl.trechos) < 2:
                problemas.append('Escolha um gancho posterior ao contexto para montar um remix real.')
            origem = sorted(tl.trechos, key=lambda s: s.inicio)
            if any(a.fim > b.inicio+.025 for a,b in zip(origem,origem[1:])):
                problemas.append('Timeline repete intervalos de origem.')
            if any(b.inicio-a.fim > 45 or b.inicio < a.fim-.025 for a,b in zip(tl.trechos[1:],tl.trechos[2:])):
                problemas.append('Timeline com salto grave ou ordem causal invertida.')
            if not math.isfinite(tl.duracao) or tl.duracao <= 0:
                problemas.append('Duracao final invalida.')
            for trecho in tl.trechos:
                if not any(i-.025 <= trecho.inicio and trecho.fim <= f+.025 for i,f in limites):
                    problemas.append('Timeline fora dos segmentos confirmados.')
                for s in transcricao.segmentos:
                    for p in s.palavras:
                        if any(p.inicio+.025 < limite < p.fim-.025
                               for limite in (trecho.inicio, trecho.fim)):
                            problemas.append('Limite corta palavra ao meio.')
                            break
    except (TypeError, ValueError, KeyError, AttributeError):
        problemas.append('Estrutura temporal invalida.')
    return list(dict.fromkeys(problemas))


def revisavel(plano, transcricao):
    return not problemas_tecnicos(plano, transcricao) and evidenciar(falas(plano, transcricao)) is not None


def assinatura(plano):
    dados = [plano.video_id, plano.inicio, plano.fim, plano.segmentos, plano.gancho,
             plano.remover, plano.usar_remix, plano.estrutura, plano.titulo_escolhido,
             plano.gancho_visual, plano.repetir_gancho]
    dados.append(plano.falas_protegidas)
    return sha256(json.dumps(dados, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def manual_confirmado(plano):
    return plano.corte_aprovado_manualmente and plano.corte_confirmado_hash == assinatura(plano)


def exigir_corte(plano, transcricao, intro=None):
    if plano.estado_editorial == 'revisao' or plano.corte_aprovado_manualmente:
        if not manual_confirmado(plano):
            plano.corte_aprovado_manualmente = False
            plano.status = REVISAO
            raise ValueError('Confirme a edicao em APROVAR MANUALMENTE.')
        erros = problemas_tecnicos(plano, transcricao, montagem=True, intro=intro)
        if erros:
            raise ValueError('; '.join(erros))


def aprovar_corte(plano, transcricao, texto, intro=None):
    from modules.agencia.titulos_manual import aprovar_titulo
    erros = problemas_tecnicos(plano, transcricao, montagem=True, intro=intro)
    if erros:
        raise ValueError('; '.join(erros))
    aprovar_titulo(plano, texto)
    plano.corte_aprovado_manualmente = True
    plano.corte_confirmado_hash = assinatura(plano)
    plano.status, plano.erro, plano.selecionado = 'pendente', '', True


def marcar_revisao(plano, evidencia, motivos):
    plano.estado_editorial = 'revisao'
    plano.status, plano.selecionado = REVISAO, False
    plano.erro = '; '.join(motivos)
    plano.revisao_humana = {**evidencia, 'motivos': motivos,
        'sugestao': 'Confira a continuidade, ajuste os segmentos e escolha o gancho; confirme titulo e montagem.'}


@dataclass
class DiagnosticoProducao:
    modo: str
    automaticos: int = 0
    revisaveis: int = 0
    candidatos_recebidos: int = 0
    candidatos_rejeitados: int = 0
    candidatos_adiados: int = 0
    partes_analisadas: list = field(default_factory=list)
    motivos_rejeicao: list = field(default_factory=list)
    falhas: list = field(default_factory=list)
    chamadas_feitas: int = 0
    chamadas_historicas: int = 0
    zero_suspeito: bool = False
    versao: str = VERSAO

    def resumo(self):
        total = self.automaticos + self.revisaveis
        return (f'{total} oportunidades sustentadas apos cobertura local. '
                f'PRONTOS (remix): {self.automaticos}; REVISAO: {self.revisaveis}; '
                f'REJEITADOS: {self.candidatos_rejeitados}. '
                + ('Nenhuma oportunidade sustentada foi encontrada neste episodio. ' if not total else '')
                + f'{self.modo}; {len(self.partes_analisadas)} regioes avaliadas; '
                f'{self.chamadas_feitas} chamadas editoriais. '
                + (f'Cache original: {self.chamadas_historicas} chamadas historicas. ' if self.chamadas_historicas else '')
                + '; '.join(self.falhas))


def janelas_locais(transcricao, alvos=(30,45,60,90,120)):
    segmentos = [s for s in transcricao.segmentos if s.texto.strip() and
                 math.isfinite(s.inicio+s.fim) and 0 <= s.inicio < s.fim <= transcricao.duracao]
    vistos = set()
    for ancora in range(0, math.ceil(transcricao.duracao), 15):
        inicio = next((n for n,s in enumerate(segmentos) if s.inicio >= ancora), None)
        if inicio is None:
            continue
        for alvo in alvos:
            possiveis = [(n,s) for n,s in enumerate(segmentos[inicio:], inicio)
                         if 20 <= s.fim-segmentos[inicio].inicio <= min(150,alvo+8)]
            if not possiveis:
                continue
            def custo(item):
                n,s = item
                pausa = segmentos[n+1].inicio-s.fim if n+1 < len(segmentos) else 2
                return abs(s.fim-segmentos[inicio].inicio-alvo) - min(2,pausa)
            fim,_ = min(possiveis, key=custo)
            # Large pauses are scene boundaries, not license to attach the next scene.
            quebra = next((n for n in range(inicio,fim)
                           if segmentos[n+1].inicio-segmentos[n].fim >= 8),None)
            if quebra is not None:
                fim = quebra
            if segmentos[fim].fim-segmentos[inicio].inicio < 20:
                continue
            chave = (inicio,fim)
            if chave in vistos:
                continue
            vistos.add(chave)
            yield segmentos[inicio:fim+1]


def julgar_opcional(juiz, candidatos, timeout=1.0):
    """Optional injected local judge. Late results cannot mutate recovery state."""
    if juiz is None:
        return None, ''
    resultado = Queue(maxsize=1)
    def executar():
        try:
            resultado.put((juiz(candidatos),''))
        except Exception as erro:
            resultado.put((None,f'Juiz auxiliar indisponivel ({type(erro).__name__}); fallback local.'))
    # Daemon prevents an unavailable optional transport from holding app shutdown.
    Thread(target=executar,daemon=True,name='agency-optional-judge').start()
    try:
        return resultado.get(timeout=max(.01,min(timeout,2)))
    except Empty:
        return None, 'Juiz auxiliar indisponivel (TimeoutError); fallback local.'


def recuperar_producao(planos, transcricao, *, sementes=(), rejeicoes=(), juiz=None,
                       timeout_juiz=1, cancelada_ref=None, chamadas_historicas=0):
    from modules.agencia.editorial import ListaPlanos, PlanoAgencia, sobreposicao_relativa
    from modules.agencia.remix import verificar_plano_remix
    from modules.intro import carregar_intro
    intro = carregar_intro(transcricao)
    automaticos = [p for p in planos if p.estado_editorial != 'revisao']
    if len(automaticos) >= 5:
        return planos
    diag = DiagnosticoProducao('DEEP RECOVERY' if not automaticos else 'RECOVERY',
                              automaticos=len(automaticos), motivos_rejeicao=list(rejeicoes),
                              chamadas_feitas=getattr(getattr(planos,'diagnostico',None),'chamadas_feitas',0))
    diag.zero_suspeito = getattr(getattr(planos,'diagnostico',None),'zero_suspeito',False)
    diag.chamadas_historicas = chamadas_historicas
    encontrados = list(planos)
    candidatos = []
    for item in sementes:
        diag.candidatos_recebidos += 1
        try:
            segmentos = item.get('segmentos') or item.get('segmentos_narrativos') or []
            dados = dict(item)
            dados.setdefault('inicio',min(float(s['inicio']) for s in segmentos))
            dados.setdefault('fim',max(float(s['fim']) for s in segmentos))
            dados['segmentos'] = segmentos
            dados.setdefault('video_id',transcricao.video_id)
            p = PlanoAgencia.de_dict(dados)
            if p.fim-p.inicio > 180:
                diag.motivos_rejeicao.append('Historia gigante subdividida em micro-historias locais.')
                diag.candidatos_rejeitados += 1
                continue
            evidencia = evidenciar(falas(p,transcricao))
            if evidencia and not problemas_tecnicos(p,transcricao,intro=intro):
                motivos = verificar_plano_remix(p,transcricao,intro)
                # Show only source quotes, never unverified provider assertions.
                p.descricao = evidencia['sinais']['conflito'][0]['texto']
                marcar_revisao(p,evidencia,motivos or ['Titulo ou decisao editorial pendente.'])
                candidatos.append(p)
            else:
                diag.candidatos_rejeitados += 1
        except (ValueError,TypeError,KeyError):
            diag.candidatos_rejeitados += 1
            continue
    # Coverage is computed for every region before selecting top opportunities.
    regioes = {n: {'inicio':n*180, 'fim':min(transcricao.duracao,(n+1)*180),
                   'etapa':'micro_historias', 'avaliados':0} for n in range(math.ceil(transcricao.duracao/180))}
    for janela in janelas_locais(transcricao):
        if cancelada_ref and cancelada_ref.cancelada:
            raise RuntimeError('Recuperacao cancelada.')
        diag.candidatos_recebidos += 1
        regioes[int(janela[0].inicio//180)]['avaliados'] += 1
        duracao = janela[-1].fim-janela[0].inicio
        densidade = sum(s.fim-s.inicio for s in janela)/duracao
        if densidade < .25 or (intro and intro.ativo and
                min(janela[-1].fim,intro.fim)-max(janela[0].inicio,intro.inicio) > .025):
            diag.candidatos_rejeitados += 1
            continue
        evidencia = evidenciar(janela)
        if evidencia is None:
            diag.candidatos_rejeitados += 1
            continue
        p = PlanoAgencia(inicio=janela[0].inicio, fim=janela[-1].fim,
                         video_id=transcricao.video_id, origem='gemini_agencia',
                         descricao=' / '.join(evidencia['assuntos'][:3]) + ': ' +
                         evidencia['sinais']['conflito'][0]['texto'],
                         segmentos=[dict(inicio=janela[0].inicio,fim=janela[-1].fim,funcao='historia')],
                         confianca=0, motivo_escolha='Oportunidade local ancorada em dialogo; requer decisao humana.')
        hooks = [s for s in janela if 1.5 <= s.fim-s.inicio <= 6.5 and
                 s.inicio-p.inicio >= 6 and s.fim < p.fim-2 and
                 re.search(SINAIS['conflito'],normalizar(s.texto))]
        if hooks:
            h = max(hooks,key=lambda s: len(termos(s.texto)&set(evidencia['assuntos'])))
            p.gancho = dict(inicio=h.inicio,fim=h.fim,motivo=h.texto)
            p.usar_remix = True
        motivos = verificar_plano_remix(p,transcricao,intro)
        # Local lexical evidence is never a replacement for narrative approval.
        marcar_revisao(p,evidencia,motivos or ['Continuidade e gancho propostos localmente: confirmar editorialmente.'])
        candidatos.append(p)
    diag.partes_analisadas = list(regioes.values())
    for p in sorted(candidatos,key=lambda p:(-(p.revisao_humana['score_oportunidade']-
                                             .4*(p.fim-p.inicio)),p.inicio)):
        if all(sobreposicao_relativa(p,outro) <= .1 for outro in encontrados):
            if len(encontrados) < 5:
                encontrados.append(p)
            else:
                diag.candidatos_adiados += 1
        else:
            diag.candidatos_rejeitados += 1
    avaliacao,falha = julgar_opcional(juiz,[p.como_dict() for p in encontrados
                                           if p.estado_editorial == 'revisao'],timeout_juiz)
    if falha:
        diag.falhas.append(falha)
    elif juiz is not None:
        # Auxiliary advice is informational and must cite source, never new timestamps.
        pareceres = avaliacao.get('avaliacoes',[]) if isinstance(avaliacao,dict) else []
        for p in encontrados:
            for parecer in pareceres:
                if isinstance(parecer,dict) and parecer.get('id') == p.id:
                    cita = str(parecer.get('citacao','')).strip()
                    if len(cita) >= 12 and cita in p.revisao_humana.get('texto_fonte',''):
                        p.revisao_humana['parecer_auxiliar'] = str(parecer.get('parecer',''))[:500]
        if not any(p.revisao_humana.get('parecer_auxiliar') for p in encontrados):
            diag.falhas.append('Juiz sem evidencia utilizavel; fallback local.')
    diag.revisaveis = sum(p.estado_editorial == 'revisao' for p in encontrados)
    resultado = ListaPlanos(sorted(encontrados,key=lambda p:p.inicio),
                           rejeicoes,diag.candidatos_recebidos,diag)
    resultado.brutos = list(sementes)
    resultado.brutos_aprovados = [p.como_dict() for p in automaticos]
    resultado.respostas_provider = getattr(planos,'respostas_provider',[])
    return resultado
