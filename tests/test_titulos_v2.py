"""Titulo V2: evidencias da montagem, providers simulados e rede bloqueada."""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

from _agencia_dados import transcricao
from modules import titulos_v2 as v2
from modules.agencia.editorial import PlanoAgencia,TituloOpcao
from modules.agencia.timeline import construir_timeline
from modules.ai.fallback import OrcamentoChamadas
from modules.config_ia import ConfigIA


def historia():
    falas = ["Peter quer comprar um carro.","A loteria parece o caminho para o carro.",
             "Lois quer guardar o dinheiro.","Peter precisa escolher entre o carro e a familia.",
             "A familia enfrenta um conflito pelo dinheiro.","A disputa pela loteria aumenta a tensao.",
             "Esse carro custa o dinheiro da familia!","Lois recusa o plano da loteria.",
             "Peter tenta comprar o carro mesmo assim.","Peter nao ganhou na loteria.",
             "A familia perdeu o dinheiro.","Lois vendeu o carro e Peter foi morar no porao."]
    t = transcricao([(1+i*4.2,5+i*4.2,f) for i,f in enumerate(falas)],
                    video_id='titulos-v2-teste',duracao=60)
    p = PlanoAgencia(1,51.3,titulos=[TituloOpcao('ELE NAO ESPERAVA POR ISSO')],
                     segmentos=[{'inicio':1,'fim':51.3,'funcao':'historia'}],
                     gancho={'inicio':26.2,'fim':30.2,'motivo':'Custo do carro'},usar_remix=True,
                     estrutura={'conflito':{'inicio':13.6,'fim':22},
                                'conclusao':{'inicio':47.2,'fim':51.3}},
                     descricao='Peter quer um carro; Lois disputa o dinheiro da familia.',
                     motivo_escolha='Disputa da familia pelo dinheiro da loteria',
                     remix_motivo='Custo antecipado seguido do contexto e da consequencia',video_id=t.video_id)
    return p,t


def propostas():
    return [{'texto':texto,'tipo':tipo,'titulo_capa':capa} for texto,tipo,capa in [
        ('POR QUE LOIS RECUSA A LOTERIA?','curiosidade','LOIS RECUSA A LOTERIA'),
        ('O CARRO CONTRA O DINHEIRO DA FAMILIA','conflito','CARRO CONTRA DINHEIRO'),
        ('A LOTERIA ENTRE PETER E LOIS','surpresa','LOTERIA ENTRE PETER E LOIS'),
        ('A DISPUTA DE PETER PELO CARRO','absurdo','DISPUTA PELO CARRO'),
        ('O CARRO DE PETER CONTRA A LOTERIA','tensao','CARRO CONTRA LOTERIA'),
        ('O CONFLITO DE LOIS PELO DINHEIRO','consequencia','CONFLITO PELO DINHEIRO'),
        ('ELE NAO ESPERAVA POR ISSO','surpresa','ELE NAO ESPERAVA'),
        ('PETER ROUBOU UM BANCO','conflito','ROUBOU UM BANCO'),
        ('LOIS VENDEU O CARRO E PETER FOI MORAR NO PORAO','consequencia','PETER NO PORAO'),
        ('LOIS RECUSA O CARRO DE PETER','tensao','RECUSA O CARRO')]]


class ProviderFalso:
    def __init__(self,respostas):
        self.respostas = list(respostas)
        self.prompts = []
    def analisar(self,modelo,prompt):
        self.prompts.append(prompt)
        return self.respostas.pop(0)


class TestTitulosV2(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        pasta = Path(self.tmp.name)
        for alvo,valor in (('TITULOS_DIR',pasta),('HISTORICO_PATH',pasta/'historico.json'),
                           ('METRICAS_LEGADAS_PATH',pasta/'legado.jsonl')):
            p = patch.object(v2,alvo,valor)
            p.start()
            self.addCleanup(p.stop)
        for alvo in ('socket.socket.connect','socket.socket.connect_ex'):
            p = patch(alvo,side_effect=AssertionError('API real proibida'))
            p.start()
            self.addCleanup(p.stop)
        self.p,self.t = historia()
        self.c = v2.montar_contexto(self.p,self.t)
        self.config = ConfigIA(modelo='gemini-teste')

    def avaliar(self,texto,tipo='curiosidade',historico=()):
        return v2.avaliar_titulo({'texto':texto,'tipo':tipo},self.c,historico)

    def pacote(self,candidatos):
        return {'historias':[{'historia_id':self.c.historia_id,'titulos':candidatos}]}

    def test_contexto_usa_timeline_final_e_nao_abertura_cronologica(self):
        self.assertIn('custa',self.c.primeira_fala)
        self.assertIn('quer comprar',self.c.situacao_inicial)
        self.assertEqual(26.2,self.c.timeline_final[0]['inicio'])
        self.assertLess(self.c.timeline_final[1]['inicio'],26.2)
        self.assertIn('porao',self.c.payoff)
        self.assertEqual('Peter',self.c.personagem_central)

    def test_sem_relacao_rejeitado(self):
        a = self.avaliar('O FUTEBOL DESTRUIU A ESCOLA')
        self.assertFalse(a.aprovado)
        self.assertLess(a.score_titulo,75)

    def test_acontecimento_inventado_rejeitado(self):
        a = self.avaliar('PETER ROUBOU UM BANCO')
        self.assertFalse(a.aprovado)
        self.assertTrue(any('evidencia' in m for m in a.motivos))

    def test_palavras_reais_nao_autorizam_inventar_acao(self):
        a = self.avaliar('LOIS RECUSA O CARRO DE PETER')
        self.assertFalse(a.aprovado)
        self.assertIn('Acao, personagem',a.motivos[0])

    def test_spoiler_inteiro_penalizado(self):
        a = self.avaliar('LOIS VENDEU O CARRO E PETER FOI MORAR NO PORAO')
        self.assertFalse(a.aprovado)
        self.assertTrue(any('payoff' in m for m in a.motivos))

    def test_generico_vazio_penalizado(self):
        a = self.avaliar('ELE NAO ESPERAVA POR ISSO')
        self.assertFalse(a.aprovado)
        self.assertLess(a.score_titulo,75)

    def test_generico_contextualizado_pode_sobreviver(self):
        self.c.evidencias.append({'id':'extra','texto':'A loteria mudou tudo para Peter e Lois.','papel':'desenvolvimento'})
        a = self.avaliar('ISSO MUDOU TUDO NA LOTERIA DE PETER E LOIS')
        self.assertTrue(a.aprovado,a.motivos)
        self.assertLess(a.score_titulo,100)

    def test_fiel_especifico_e_forte_aprovado(self):
        a = self.avaliar('POR QUE LOIS RECUSA A LOTERIA?')
        self.assertTrue(a.aprovado,a.motivos)
        self.assertGreaterEqual(a.score_titulo,75)

    def test_primeira_fala_copiada_rejeitada(self):
        a = self.avaliar(self.c.primeira_fala)
        self.assertFalse(a.aprovado)
        self.assertIn('Titulo apenas copia uma fala',a.motivos)

    def test_titulos_quase_iguais_deduplicados_sem_redistribuir_tipo(self):
        candidatos = [{'texto':'POR QUE LOIS RECUSA A LOTERIA?','tipo':'curiosidade'},
                      {'texto':'LOIS RECUSA A LOTERIA POR QUE?','tipo':'conflito'}]+propostas()[1:]
        top,avaliados = v2.selecionar_titulos(candidatos,self.c)
        self.assertEqual(1,sum('RECUSA' in a.texto for a in top))
        self.assertEqual('curiosidade',next(a.tipo for a in top if 'RECUSA' in a.texto))
        self.assertTrue(any('Proposta quase' in ' '.join(a.motivos) for a in avaliados))

    def test_historico_semelhante_penalizado(self):
        a = self.avaliar('POR QUE LOIS RECUSA A LOTERIA?',historico=[
            {'texto':'POR QUE LOIS RECUSA A LOTERIA?','historia_id':'outro'}])
        self.assertFalse(a.aprovado)
        self.assertEqual(1,a.similaridade_historico)

    def test_mesma_historia_nao_penaliza_proprio_titulo_exportado(self):
        a = self.avaliar('POR QUE LOIS RECUSA A LOTERIA?',historico=[
            {'texto':'POR QUE LOIS RECUSA A LOTERIA?','historia_id':self.c.historia_id}])
        self.assertTrue(a.aprovado)

    def test_capa_curta_coerente_e_sem_truncamento(self):
        titulo = 'POR QUE LOIS RECUSA A LOTERIA?'
        self.assertEqual('LOIS RECUSA A LOTERIA',v2.capa_coerente(titulo,self.c,'LOIS RECUSA A LOTERIA'))
        self.assertEqual(titulo,v2.capa_coerente(titulo,self.c,'POR QUE LOIS RECUSA A'))
        self.assertEqual(titulo,v2.capa_coerente(titulo,self.c,'LOIS LOTERIA'))

    def test_dez_candidatos_viram_top_tres(self):
        top,avaliados = v2.selecionar_titulos(propostas(),self.c)
        self.assertEqual(10,len(avaliados))
        self.assertEqual(3,len(top))
        self.assertEqual(3,len({a.tipo for a in top}))
        self.assertTrue(all(a.score_titulo >=75 for a in top))

    def test_tres_bons_nao_fazem_correcao(self):
        provedor = ProviderFalso([self.pacote(propostas())])
        v2.gerar_titulos_v2([self.p],self.t,self.config,provedor=provedor)
        self.assertEqual(1,len(provedor.prompts))
        self.assertEqual(3,len(self.p.titulos))
        self.assertFalse(v2.HISTORICO_PATH.exists())

    def test_todos_ruins_uma_correcao(self):
        provedor = ProviderFalso([self.pacote(propostas()[6:]),self.pacote(propostas())])
        v2.gerar_titulos_v2([self.p],self.t,self.config,provedor=provedor)
        self.assertEqual(2,len(provedor.prompts))
        self.assertIn('REJEITADOS E MOTIVOS',provedor.prompts[1])
        self.assertIn('timeline_final',provedor.prompts[1])
        self.assertEqual(3,len(self.p.titulos))

    def test_um_ou_dois_bons_nao_fazem_correcao_nem_preenchem(self):
        provedor = ProviderFalso([self.pacote(propostas()[:1]+propostas()[6:])])
        v2.gerar_titulos_v2([self.p],self.t,self.config,provedor=provedor)
        self.assertEqual(1,len(provedor.prompts))
        self.assertEqual(1,len(self.p.titulos))

    def test_orcamento_esgotado_nao_chama_api(self):
        provedor = ProviderFalso([])
        v2.gerar_titulos_v2([self.p],self.t,self.config,provedor=provedor,orcamento=OrcamentoChamadas(1,1))
        self.assertFalse(provedor.prompts)
        self.assertTrue(self.p.titulos)
        self.assertGreaterEqual(self.p.titulos[0].score_titulo,75)

    def test_uma_chamada_disponivel_impede_correcao(self):
        provedor = ProviderFalso([self.pacote(propostas()[6:])])
        v2.gerar_titulos_v2([self.p],self.t,self.config,provedor=provedor,orcamento=OrcamentoChamadas(1))
        self.assertEqual(1,len(provedor.prompts))

    def test_historico_registra_so_uso_confirmado_e_sem_duplicar(self):
        v2.registrar_uso('POR QUE LOIS RECUSA A LOTERIA?',self.c)
        v2.registrar_uso('POR QUE LOIS RECUSA A LOTERIA?',self.c)
        self.assertEqual(1,len(v2.carregar_historico()))

    def test_cache_revalida_historico_sem_chamada(self):
        provider = ProviderFalso([self.pacote(propostas())])
        v2.gerar_titulos_v2([self.p],self.t,self.config,provedor=provider)
        novo = deepcopy(self.p)
        novo.id = 'id-recriado'
        p2 = ProviderFalso([])
        v2.gerar_titulos_v2([novo],self.t,self.config,provedor=p2)
        self.assertFalse(p2.prompts)
        self.assertEqual(3,len(novo.titulos))

    def test_cache_depende_da_montagem_final(self):
        p = ProviderFalso([self.pacote(propostas())])
        v2.gerar_titulos_v2([self.p],self.t,self.config,provedor=p)
        self.p.remover = [{'inicio':5.2,'fim':9.2}]
        novo_contexto = v2.montar_contexto(self.p,self.t)
        provider = ProviderFalso([{'historias':[{'historia_id':novo_contexto.historia_id,'titulos':propostas()}]}])
        v2.gerar_titulos_v2([self.p],self.t,self.config,provedor=provider)
        self.assertEqual(1,len(provider.prompts))

    def test_contradicao_negacao_rejeitada(self):
        a = self.avaliar('PETER GANHOU NA LOTERIA')
        self.assertFalse(a.aprovado)
        self.assertTrue(any('contradiz' in m for m in a.motivos))

    def test_palavras_aleatorias_e_titulo_longo_rejeitados(self):
        self.assertFalse(self.avaliar('PETER CARRO LOTERIA').aprovado)
        self.assertFalse(self.avaliar('PETER '*20).aprovado)

    def test_evidencia_falsa_rejeitada(self):
        a = v2.avaliar_titulo({'texto':'POR QUE LOIS RECUSA A LOTERIA?',
                              'tipo':'curiosidade','evidencias':['inexistente']},self.c)
        self.assertFalse(a.aprovado)

    def test_exportacao_revalida_edicao_manual(self):
        self.p.titulo_escolhido = 'PETER ROUBOU UM BANCO'
        with self.assertRaisesRegex(ValueError,'Titulo V2'):
            v2.validar_titulo_final(self.p,self.t)

    def test_pipeline_adapter_preserva_remix_e_nao_refaz_analise(self):
        analise = Mock(return_value=[self.p])
        antes = asdict(construir_timeline(self.p,self.t))
        provider = ProviderFalso([self.pacote(propostas())])
        lista = v2.executar_analise_com_titulos(self.t,self.config,analise=analise,provedor_titulos=provider)
        analise.assert_called_once()
        self.assertEqual(antes,asdict(construir_timeline(self.p,self.t)))
        self.assertIs(self.p,lista[0])

    def test_varias_historias_usam_uma_chamada_estruturada(self):
        p2 = deepcopy(self.p)
        p2.id = 'historia-2'
        p2.inicio += 60
        p2.fim += 60
        for segmento in p2.segmentos:
            segmento['inicio'] += 60
            segmento['fim'] += 60
        p2.gancho['inicio'] += 60
        p2.gancho['fim'] += 60
        for zona in p2.estrutura.values():
            zona['inicio'] += 60
            zona['fim'] += 60
        adicionais = deepcopy(self.t.segmentos)
        for segmento in adicionais:
            segmento.inicio += 60
            segmento.fim += 60
            for palavra in segmento.palavras:
                palavra.inicio += 60
                palavra.fim += 60
        self.t.segmentos.extend(adicionais)
        self.t.duracao = 120
        c2 = v2.montar_contexto(p2,self.t)
        resposta = self.pacote(propostas())
        resposta['historias'].append({'historia_id':c2.historia_id,'titulos':propostas()})
        provider = ProviderFalso([resposta])
        v2.gerar_titulos_v2([self.p,p2],self.t,self.config,provedor=provider)
        self.assertEqual(1,len(provider.prompts))
        self.assertEqual(3,len(p2.titulos))

    def test_fallback_existente_conta_no_teto_de_duas_tentativas(self):
        from modules.ai.fallback import ProviderFallback
        from modules.ai.errors import ErroProvider
        config = ConfigIA(fallback_automatico=True,max_chamadas_episodio=4)
        clientes = {p:Mock() for p in config.ordem_fallback}
        clientes['mistral'].analisar_uma_vez.side_effect = ErroProvider('quota',codigo=429)
        clientes['groq'].analisar_uma_vez.return_value = self.pacote(propostas())
        provider = ProviderFallback(config,factory=lambda p,**kw:clientes[p],dormir=lambda _:None)
        budget = OrcamentoChamadas(4)
        v2.gerar_titulos_v2([self.p],self.t,config,provedor=provider,orcamento=budget)
        self.assertEqual(2,budget.usadas)
        self.assertEqual(4,budget.maximo)
        self.assertEqual(3,len(self.p.titulos))
        clientes['gemini'].analisar_uma_vez.assert_not_called()

    def test_v3_tem_so_duas_chamadas_adicionais_e_fallback_local(self):
        provider = ProviderFalso([self.pacote(propostas()[6:])]*2)
        v2.gerar_titulos_v2([self.p],self.t,self.config,provedor=provider)
        self.assertEqual(4,len(provider.prompts))
        self.assertTrue(self.p.titulos)
        self.assertEqual(2,sum('TITLE RECOVERY V3' in p for p in provider.prompts))

    def test_cache_com_recovery_concluido_nao_repete_chamadas(self):
        provider = ProviderFalso([self.pacote(propostas()[6:])]*2)
        v2.gerar_titulos_v2([self.p],self.t,self.config,provedor=provider)
        novo = ProviderFalso([self.pacote(propostas())])
        v2.gerar_titulos_v2([self.p],self.t,self.config,provedor=novo)
        self.assertEqual(0,len(novo.prompts))
        self.assertTrue(self.p.titulos_v2.get('recovery_v3'))

    def test_reavaliar_salvos_e_totalmente_local(self):
        self.p.titulos = [TituloOpcao(c['texto'],c['tipo']) for c in propostas()[:3]]
        with patch.object(v2,'gerar_titulos_v2',side_effect=AssertionError('Nao deve gerar')):
            v2.executar_analise_com_titulos(self.t,self.config,analise=Mock(return_value=[self.p]),gerar=False)
        self.assertTrue(all(t.score_titulo >=75 for t in self.p.titulos))

    def test_persistencia_preserva_scores_e_diagnosticos(self):
        provider = ProviderFalso([self.pacote(propostas())])
        v2.gerar_titulos_v2([self.p],self.t,self.config,provedor=provider)
        reconstruido = PlanoAgencia.de_dict(self.p.como_dict())
        self.assertEqual(self.p.titulos[0].score_titulo,reconstruido.titulos[0].score_titulo)
        self.assertEqual(self.p.titulos_v2,reconstruido.titulos_v2)

    def test_cancelamento_nao_salva_propostas(self):
        provider = ProviderFalso([])
        with self.assertRaisesRegex(RuntimeError,'cancelada'):
            v2.gerar_titulos_v2([self.p],self.t,self.config,provedor=provider,
                               cancelada_ref=Mock(cancelada=True))
        self.assertFalse(list(v2.TITULOS_DIR.rglob('*.json')))

    def test_adapter_comum_e_remix_preservam_scores(self):
        from modules.analise_ia import candidato_de_plano
        from modules.remix_narrativo import CandidatoRemix,SegmentoNarrativo
        comum = candidato_de_plano(self.p)
        remix = CandidatoRemix(['Titulo antigo'],self.p.descricao,self.p.gancho['inicio'],
                               self.p.gancho['fim'],self.p.gancho['motivo'],
                               [SegmentoNarrativo(self.p.inicio,self.p.fim,'historia')],
                               self.p.motivo_escolha,self.p.remix_motivo,.9)
        for candidato in (comum,remix):
            provider = ProviderFalso([self.pacote(propostas())])
            v2.executar_analise_com_titulos(self.t,self.config,analise=Mock(return_value=[candidato]),
                                           provedor_titulos=provider,timeline_modo='lote')
            dados = candidato.plano_remix['titulos_v2'] if hasattr(candidato,'plano_remix') else candidato.titulos_v2
            self.assertTrue(dados['top'])
            self.assertTrue(all(t['score_titulo'] >=75 for t in dados['top']))

    def test_identidade_historia_independe_de_rotulos_e_segmentacao(self):
        p2 = deepcopy(self.p)
        p2.id = 'novo-id'
        p2.gancho['motivo'] = 'justificativa editada'
        p2.segmentos = [{'inicio':1.0,'fim':13.6,'funcao':'contexto'},
                         {'inicio':13.6,'fim':51.3,'funcao':'desenvolvimento'}]
        self.assertEqual(self.c.historia_id,v2.montar_contexto(p2,self.t).historia_id)

    def test_interface_remix_recebe_titulos_v2_sem_perder_transcricao(self):
        import queue
        from ui.tela_lote import TelaLote
        from modules.remix_narrativo import CandidatoRemix,SegmentoNarrativo,ListaRemixes
        candidato = CandidatoRemix(['POR QUE LOIS RECUSA A LOTERIA?'],self.p.descricao,
                                   self.p.gancho['inicio'],self.p.gancho['fim'],self.p.gancho['motivo'],
                                   [SegmentoNarrativo(self.p.inicio,self.p.fim,'historia')],
                                   self.p.motivo_escolha,self.p.remix_motivo,.9,
                                   titulos_v2={'top':[{'texto':'POR QUE LOIS RECUSA A LOTERIA?','score_titulo':98}]})
        tela = object.__new__(TelaLote)
        tela.app = Mock(estado={'transcricao':self.t})
        tela._modo_atual = 'remix'
        tela._listas_modo = {}
        tela._eventos = queue.Queue()
        tela._eventos.put(('remix_fim',1,'video.mp4',ListaRemixes([candidato])))
        tela._redesenhar_lista = Mock()
        tela._salvar_lista = Mock()
        tela.lbl_status = Mock()
        tela.btn_ia = Mock()
        tela.btn_cancelar = Mock()
        tela.barra_individual = Mock()
        tela.winfo_exists = Mock(return_value=False)
        with patch('ui.tela_lote.sessao_corresponde',return_value=True):
            tela._processar_eventos()
        self.assertEqual(self.t.video_id,tela.cortes[0].video_id)
        self.assertEqual(98,tela.cortes[0].titulos_v2['top'][0]['score_titulo'])


if __name__ == '__main__':
    unittest.main()
