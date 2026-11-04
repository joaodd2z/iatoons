"""Title-only recovery tests: actual cached EP12 evidence, network forbidden."""
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,Mock

from modules import titulos_v2 as V
from modules import title_recovery_v3 as R
from modules.ai.fallback import OrcamentoChamadas,ProviderFallback
from modules.ai.errors import ErroProvider
from modules.config_ia import ConfigIA

ROOT = Path(__file__).resolve().parents[1]


class Fake:
    def __init__(self,respostas):
        self.respostas = list(respostas)
        self.prompts = []
    def analisar(self,modelo,prompt):
        self.prompts.append(prompt)
        return self.respostas.pop(0) if self.respostas else {'historias':[]}


class TestTitleRecoveryV3(unittest.TestCase):
    def setUp(self):
        for nome in ('socket.socket.connect','socket.socket.connect_ex'):
            p = patch(nome,side_effect=AssertionError('Nenhuma API real'))
            p.start()
            self.addCleanup(p.stop)
        self.dados = json.loads((ROOT/'dados/agencia/auto_recovery/S07E12.json').read_text(encoding='utf-8'))
        self.contextos = [V.ContextoTitulo(**c['titulos_v2']['contexto']) for c in self.dados['cortes']]
        self.resultados = {c.historia_id:deepcopy(d['titulos_v2']) for c,d in zip(self.contextos,self.dados['cortes'])}
        self.cfg = ConfigIA(modelo='fake',max_chamadas_episodio=10)
        p = patch.object(V,'carregar_historico',return_value=[])
        p.start()
        self.addCleanup(p.stop)

    def resposta(self,candidatos):
        return {'historias':[dict(historia_id=self.contextos[0].historia_id,titulos=candidatos)]}

    def recuperar(self,provider=None,budget=None):
        return R.recuperar_contextos(self.contextos,self.resultados,self.cfg,
                                    provedor=provider,orcamento=budget or OrcamentoChamadas(0))

    def test_remix_valido_sem_titulo_entra_em_recovery_lote(self):
        fake = Fake([])
        self.recuperar(fake,OrcamentoChamadas(2))
        self.assertEqual(2,len(fake.prompts))
        self.assertTrue(all(c.historia_id in fake.prompts[0] for c in self.contextos if not self.resultados[c.historia_id]['top']))
        self.assertNotIn(self.contextos[3].historia_id,fake.prompts[0])

    def test_um_aprovado_basta_sem_segunda_chamada(self):
        c = self.contextos[0]
        candidato = R.fallback_factual(c)[0][0]
        fake = Fake([self.resposta([candidato])])
        r = R.recuperar_contextos([c],{c.historia_id:self.resultados[c.historia_id]},self.cfg,
                                 provedor=fake,orcamento=OrcamentoChamadas(2))
        self.assertEqual(1,len(fake.prompts))
        self.assertTrue(r[c.historia_id]['top'])

    def test_primeira_falha_reparo_recebe_motivos_e_penalidades(self):
        ruim = dict(texto='VOCE NAO VAI ACREDITAR',tipo='curiosidade')
        fake = Fake([self.resposta([ruim]),{'historias':[]}])
        self.recuperar(fake,OrcamentoChamadas(2))
        self.assertIn('reparo_por_motivos',fake.prompts[1])
        self.assertIn('VOCE NAO VAI ACREDITAR',fake.prompts[1])
        self.assertIn('penalidades',fake.prompts[1])
        self.assertIn('motivos',fake.prompts[1])

    def test_avalia_doze_nao_apenas_primeiros_dez(self):
        c = self.contextos[0]
        bons = R.fallback_factual(c)[0][:1]
        top,avaliados = R.avaliar_pacote([dict(texto='ISSO MUDOU TUDO')]*11+bons,c,[])
        self.assertEqual(12,len(avaliados))
        self.assertTrue(top)

    def test_fallback_so_fatos_da_evidencia_real(self):
        for c in self.contextos:
            candidatos,fatos = R.fallback_factual(c)
            mapa = {e['id']:V.tokens(e['texto']) for e in c.evidencias}
            self.assertTrue(fatos['objetos_eventos'])
            self.assertTrue(all(V.tokens(t['texto'])<=mapa[t['evidencias'][0]] for t in candidatos))
            top,_ = R.avaliar_pacote(candidatos,c,[])
            self.assertTrue(top)
            self.assertGreaterEqual(top[0]['score_titulo'],75)

    def test_genericos_continuam_rejeitados(self):
        for t in ('ELE NAO ESPERAVA POR ISSO','VOCE NAO VAI ACREDITAR','OLHA O QUE ACONTECEU','ISSO MUDOU TUDO'):
            self.assertFalse(V.avaliar_titulo(t,self.contextos[0],[]).aprovado)

    def test_orcamento_global_preservado_maximo_dois(self):
        fake = Fake([])
        budget = OrcamentoChamadas(10,3)
        self.recuperar(fake,budget)
        self.assertEqual(2,len(fake.prompts))
        self.assertEqual(5,budget.usadas)
        self.assertEqual(10,budget.maximo)

    def test_provider_failure_usa_fallback_existente(self):
        cfg = ConfigIA(fallback_automatico=True,max_chamadas_episodio=10)
        clientes = {p:Mock() for p in cfg.ordem_fallback}
        clientes['mistral'].analisar_uma_vez.side_effect = ErroProvider('quota',codigo=429)
        clientes['groq'].analisar_uma_vez.return_value = self.resposta(R.fallback_factual(self.contextos[0])[0])
        provider = ProviderFallback(cfg,factory=lambda p,**kw:clientes[p],dormir=lambda _:None)
        budget = OrcamentoChamadas(10)
        self.recuperar(provider,budget)
        self.assertEqual(2,budget.usadas)
        clientes['gemini'].analisar_uma_vez.assert_not_called()
        self.assertEqual(1,clientes['groq'].analisar_uma_vez.call_count)

    def test_score_baixo_nao_troca_provider(self):
        cfg = ConfigIA(fallback_automatico=True,max_chamadas_episodio=10)
        clientes = {p:Mock() for p in cfg.ordem_fallback}
        clientes['mistral'].analisar_uma_vez.return_value = {'historias':[]}
        provider = ProviderFallback(cfg,factory=lambda p,**kw:clientes[p],dormir=lambda _:None)
        self.recuperar(provider,OrcamentoChamadas(10))
        self.assertEqual(2,clientes['mistral'].analisar_uma_vez.call_count)
        clientes['groq'].analisar_uma_vez.assert_not_called()

    def test_remix_timeline_episodio_intocados(self):
        antes = deepcopy(self.dados)
        contextos = [asdict(c) for c in self.contextos]
        with patch('modules.agencia.auto_recovery.auto_recuperar',side_effect=AssertionError('Proibido')), \
             patch('modules.agencia.timeline.construir_timeline',side_effect=AssertionError('Proibido')):
            r = self.recuperar()
        self.assertEqual(contextos,[asdict(c) for c in self.contextos])
        self.assertEqual(antes,self.dados)
        self.assertEqual(6,sum(bool(r[c.historia_id]['top']) for c in self.contextos))

    def test_titulo_de_piadas_laterais_nao_substitui_historia_central(self):
        c = self.contextos[1]
        lateral = dict(texto='SUPER-HOMEM 4 EM BUSCA DE PAZ',tipo='curiosidade')
        top,avaliados = R.avaliar_pacote([lateral],c,[])
        self.assertFalse(top)
        self.assertFalse(avaliados[0]['aprovado'])

    def test_ep12_cache_e_gate_final_de_titulo(self):
        from modules.agencia.editorial import PlanoAgencia
        origem = ROOT/'dados/agencia/auto_recovery/S07E12_titulos_v3.json'
        if not origem.is_file():
            self.skipTest('Resultado real de titulo ainda nao produzido')
        d = json.loads(origem.read_text(encoding='utf-8'))
        self.assertEqual(6,d['prontos'])
        for corte in d['cortes']:
            contexto = V.ContextoTitulo(**corte['titulos_v2']['contexto'])
            p = PlanoAgencia(0,1,status=V.STATUS_REVISAO_TITULO,selecionado=False,
                             validacao_remix=deepcopy(corte['remix']))
            remix = deepcopy(p.validacao_remix)
            with patch.object(V,'montar_contexto',return_value=contexto), \
                 patch('modules.agencia.timeline.construir_timeline',side_effect=AssertionError('Nao reconstruir')):
                V.reavaliar_existentes([p],None)
                self.assertFalse(V.titulo_pendente(p.titulo))
                self.assertTrue(p.titulo_auto_aprovado and p.selecionado)
                self.assertEqual('pendente',p.status)
                from ui.tela_agencia import TelaAgencia
                self.assertTrue(TelaAgencia._pronto_visual(None,p))
                _,avaliacao = V.validar_titulo_final(p,None)
                self.assertGreaterEqual(avaliacao.score_titulo,75)
                self.assertTrue(p.titulo_capa)
            self.assertEqual(remix,p.validacao_remix)


if __name__=='__main__':
    unittest.main()
