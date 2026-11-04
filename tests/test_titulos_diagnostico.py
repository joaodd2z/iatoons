"""Objective diagnostics and editorial guidance, with network blocked."""
from copy import deepcopy
from dataclasses import asdict
from unittest.mock import patch
import unittest

import test_titulos_v2 as fixtures
from modules import titulos_v2 as V
from ui.titulos_diagnostico import detalhar_rejeicao


class TestDiagnosticoTitulos(unittest.TestCase):
    def setUp(self):
        fixtures.TestTitulosV2.setUp(self)

    def pacote(self, candidatos):
        return fixtures.TestTitulosV2.pacote(self,candidatos)

    def test_penalty_accounting_matches_score_for_all_candidates(self):
        for candidato in fixtures.propostas():
            with self.subTest(titulo=candidato['texto']):
                a=V.avaliar_titulo(candidato,self.c)
                bruto=a.score_base+sum(p['valor'] for p in a.penalidades)
                self.assertEqual(bruto,a.score_sem_limitacao)
                self.assertEqual(max(0,min(100,bruto)),a.score_titulo)
                self.assertEqual(set(V.PESOS_CRITERIOS),set(a.criterios))
                self.assertEqual(V.PESOS_CRITERIOS,a.criterios_maximos)

    def test_generic_has_objective_penalty_and_remains_rejected(self):
        a=V.avaliar_titulo('ELE NAO ESPERAVA POR ISSO',self.c)
        self.assertFalse(a.aprovado)
        perdas={p['codigo']:p['valor'] for p in a.penalidades}
        self.assertEqual(-40,perdas['genericidade'])
        self.assertEqual(-20,perdas['baixa_especificidade'])
        self.assertLess(a.score_titulo,75)

    def test_spoiler_penalty_is_explicit(self):
        a=V.avaliar_titulo(fixtures.propostas()[8],self.c)
        self.assertFalse(a.aprovado)
        p=next(p for p in a.penalidades if 'payoff_inteiro' in p['codigo'])
        self.assertEqual(-40,p['valor'])
        self.assertTrue(p['bloqueante'])

    def test_specific_faithful_title_still_passes_75(self):
        a=V.avaliar_titulo(fixtures.propostas()[0],self.c)
        self.assertTrue(a.aprovado,a.motivos)
        self.assertGreaterEqual(a.score_titulo,75)
        self.assertEqual(75,V.SCORE_MINIMO)

    def test_correction_receives_criteria_penalties_and_targeted_directions(self):
        provider=fixtures.ProviderFalso([self.pacote([fixtures.propostas()[6]]),self.pacote(fixtures.propostas()[:1])])
        V.gerar_titulos_v2([self.p],self.t,self.config,provedor=provider,usar_cache=False)
        prompt=provider.prompts[1]
        for campo in ('criterios','criterios_maximos','penalidades','evitar_defeitos','genericidade','baixa_especificidade'):
            self.assertIn(campo,prompt)
        self.assertIn('Troque frase vazia',prompt)
        self.assertIn('8-10 novos titulos',prompt)
        self.assertEqual(2,len(provider.prompts))

    def test_generation_requires_all_categories_and_grounded_story_components(self):
        prompt=V.prompt_titulos([self.c])
        for tipo in V.ESTILOS:
            self.assertIn(tipo,prompt)
        for regra in ('conflito central','personagem comprovado','situacao especifica','gancho','consequencia',
                      'nao copie falas','nao pequenas variacoes','vocabulario_comprovado','termos_resumo_sem_suporte'):
            self.assertIn(regra,prompt)

    def test_inconsistent_summary_does_not_authorize_invented_facts(self):
        context=deepcopy(self.c)
        context.resumo='Brian roubou uma nave espacial.'
        prompt=V.prompt_titulos([context])
        self.assertIn('"brian"',prompt)
        self.assertIn('"nave"',prompt)
        self.assertIn('prevalecem sobre resumo',prompt)
        a=V.avaliar_titulo('BRIAN ROUBOU UMA NAVE ESPACIAL',context)
        self.assertFalse(a.aprovado)

    def test_different_labels_do_not_make_same_title_diverse(self):
        candidatos=[{'texto':fixtures.propostas()[0]['texto'],'tipo':t} for t in V.ESTILOS]
        top,avaliados=V.selecionar_titulos(candidatos,self.c)
        diversity=V.diagnosticar_diversidade(avaliados)
        self.assertEqual(1,len(top))
        self.assertEqual([],diversity['categorias_ausentes'])
        self.assertFalse(diversity['diversidade_real'])
        self.assertTrue(diversity['variacoes_proximas'])
        self.assertTrue(any(p['codigo']=='diversidade_textual' for a in avaliados for p in a.penalidades))

    def test_small_variations_are_not_extra_approved_titles(self):
        candidatos=[fixtures.propostas()[0],{'texto':'POR QUE LOIS RECUSA ESSA LOTERIA?','tipo':'tensao'}]
        top,avaliados=V.selecionar_titulos(candidatos,self.c)
        self.assertEqual(1,len(top))
        self.assertFalse(V.diagnosticar_diversidade(avaliados)['diversidade_real'])

    def test_distinct_focuses_and_types_report_diversity(self):
        candidatos=[{'texto':texto,'tipo':tipo} for texto,tipo in zip(
            ['LOIS RECUSA A LOTERIA?','PETER CONTRA O CARRO','A FAMILIA ENFRENTA O DINHEIRO',
             'LOIS VENDEU O CARRO E PETER FOI MORAR NO PORAO',
             'UMA ESCOLA DIFERENTE DO FUTEBOL','QUEM QUEBROU O TELEFONE NA PRAIA?'],V.ESTILOS)]
        avaliados=[V.avaliar_titulo(c,self.c) for c in candidatos]
        diversity=V.diagnosticar_diversidade(avaliados)
        self.assertTrue(diversity['diversidade_real'])
        # Diversity alone must never approve fabricated or spoiler titles.
        self.assertTrue(any(not a.aprovado for a in avaliados))

    def test_initial_rejections_survive_successful_correction_and_cache(self):
        provider=fixtures.ProviderFalso([self.pacote([fixtures.propostas()[6]]),self.pacote(fixtures.propostas()[:1])])
        V.gerar_titulos_v2([self.p],self.t,self.config,provedor=provider)
        rejeitados=V.diagnosticos_rejeitados(self.p.titulos_v2)
        self.assertEqual(1,len(rejeitados))
        self.assertEqual('geracao',rejeitados[0]['etapa'])
        self.assertTrue(rejeitados[0]['penalidades'])
        cached=fixtures.ProviderFalso([])
        V.gerar_titulos_v2([self.p],self.t,self.config,provedor=cached)
        self.assertFalse(cached.prompts)
        self.assertEqual(1,len(V.diagnosticos_rejeitados(self.p.titulos_v2)))

    def test_both_failed_rounds_are_visible_and_review_remains(self):
        provider=fixtures.ProviderFalso([self.pacote([fixtures.propostas()[6]])]*2)
        V.gerar_titulos_v2([self.p],self.t,self.config,provedor=provider)
        self.assertEqual(['geracao','correcao'],[a['etapa'] for a in V.diagnosticos_rejeitados(self.p.titulos_v2)])
        self.assertEqual(V.STATUS_REVISAO_TITULO,self.p.status)
        self.assertFalse(self.p.selecionado)
        self.assertEqual(2,len(provider.prompts))

    def test_ui_detail_contains_score_criteria_and_penalties(self):
        item=asdict(V.avaliar_titulo(fixtures.propostas()[6],self.c))
        detalhe=detalhar_rejeicao(item)
        for texto in ('Score total','Score por criterio','Penalidades','Motivos objetivos','genericidade: -40'):
            self.assertIn(texto,detalhe)

    def test_saved_results_recompute_diagnostics_without_api(self):
        self.p.titulos_v2={'candidatos':[fixtures.propostas()[6]]}
        with patch.object(V,'gerar_titulos_v2',side_effect=AssertionError('No provider')):
            V.reavaliar_existentes([self.p],self.t)
        self.assertTrue(V.diagnosticos_rejeitados(self.p.titulos_v2)[0]['penalidades'])
