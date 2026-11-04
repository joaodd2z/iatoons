"""Title approval is independent of alternative count and remix validity."""
from copy import deepcopy
from dataclasses import asdict, replace
import json
from unittest.mock import Mock, patch
import unittest

import test_titulos_v2 as fixtures
from modules import titulos_v2 as V
from modules.ai.fallback import ProviderFallback, OrcamentoChamadas
from modules.ai.errors import ErroProvider
from modules.config_ia import ConfigIA


class TestRecuperacaoTitulos(unittest.TestCase):
    def setUp(self):
        fixtures.TestTitulosV2.setUp(self)

    def pacote(self, candidatos):
        return fixtures.TestTitulosV2.pacote(self,candidatos)

    def gerar(self, propostas, correcao=None):
        respostas=[self.pacote(propostas)]
        if correcao is not None:
            respostas.append(self.pacote(correcao))
        provider=fixtures.ProviderFalso(respostas)
        V.gerar_titulos_v2([self.p],self.t,self.config,provedor=provider,usar_cache=False)
        return provider

    def test_three_two_one_use_highest_score(self):
        bons=fixtures.propostas()[:3]
        real=V.avaliar_titulo
        scores={c['texto']:s for c,s in zip(bons,[76,95,86])}
        for n in (3,2,1):
            with self.subTest(n=n), patch.object(V,'avaliar_titulo',side_effect=lambda c,ctx,h:replace(
                    real(c,ctx,h),score_titulo=scores[c['texto']])):
                provider=self.gerar(bons[:n])
            self.assertEqual(n,len(self.p.titulos))
            self.assertEqual(max(scores[c['texto']] for c in bons[:n]),self.p.titulos[0].score_titulo)
            self.assertEqual(self.p.titulos[0].texto,self.p.titulo_escolhido)
            self.assertEqual(1,len(provider.prompts))
            self.assertNotIn('Menos de 3',' '.join(self.p.avisos))

    def test_one_valid_seven_invalid_does_not_correct(self):
        provider=self.gerar(fixtures.propostas()[:1]+[fixtures.propostas()[6]]*7)
        self.assertEqual(1,len(provider.prompts))
        self.assertEqual(fixtures.propostas()[0]['texto'],self.p.titulo)
        self.assertTrue(self.p.selecionado)

    def test_zero_corrects_once_and_accepts_one(self):
        provider=self.gerar([fixtures.propostas()[6]]*8,fixtures.propostas()[:1])
        self.assertEqual(2,len(provider.prompts))
        self.assertEqual(1,len(self.p.titulos))
        self.assertFalse(V.titulo_pendente(self.p.titulo))

    def test_zero_after_correction_is_title_review_not_remix_failure(self):
        contract={'remix_valido':True,'score_remix':95}
        self.p.validacao_remix=deepcopy(contract)
        before=deepcopy(self.p.segmentos)
        provider=self.gerar([fixtures.propostas()[6]]*8,[fixtures.propostas()[6]]*8)
        self.assertEqual(2,len(provider.prompts))
        self.assertEqual(V.STATUS_REVISAO_TITULO,self.p.status)
        self.assertEqual(V.MOTIVO_REVISAO_TITULO,self.p.erro)
        self.assertFalse(self.p.selecionado)
        self.assertEqual(contract,self.p.validacao_remix)
        self.assertEqual(before,self.p.segmentos)
        self.assertEqual('REVISÃO NECESSÁRIA',V.titulo_exibicao(self.p))

    def test_correction_does_not_rerun_episode_or_rebuild_timeline(self):
        from modules.agencia.timeline import construir_timeline
        with patch('modules.agencia.timeline.construir_timeline',wraps=construir_timeline) as timeline, \
             patch('modules.agencia.editorial.analisar_agencia',side_effect=AssertionError('No episode analysis')):
            provider=self.gerar([fixtures.propostas()[6]],fixtures.propostas()[:1])
        self.assertEqual(1,timeline.call_count)
        self.assertNotIn('video_id',provider.prompts[1])
        self.assertIn('8-10 novos titulos',provider.prompts[1])
        self.assertIn('"score"',provider.prompts[1])
        self.assertIn('"motivos"',provider.prompts[1])

    def test_correction_prompt_contains_only_final_story(self):
        self.t.segmentos.extend(deepcopy(self.t.segmentos[:1]))
        self.t.segmentos[-1].inicio=90
        self.t.segmentos[-1].fim=95
        self.t.segmentos[-1].texto='UNRELATED EPISODE SECRET'
        provider=self.gerar([fixtures.propostas()[6]],fixtures.propostas()[:1])
        self.assertNotIn('UNRELATED EPISODE SECRET',provider.prompts[1])
        for field in ('resumo','conflito','personagem_central','gancho','contexto','desenvolvimento','payoff_consequencia','timeline_final'):
            self.assertIn('"'+field+'"',provider.prompts[1])

    def test_no_title_export_is_blocked_before_render(self):
        from modules.agencia.pipeline import exportar_corte_agencia
        for titulo in ('','SEM TÍTULO',' sem titulo ','REVISÃO NECESSÁRIA'):
            with self.subTest(titulo=titulo):
                self.p.titulos=[]
                self.p.titulo_escolhido=titulo
                with patch('modules.agencia.pipeline.executar_ffmpeg',side_effect=AssertionError('No render')):
                    result=exportar_corte_agencia('inexistente.mp4',self.p,self.t,self.tmp.name)
                self.assertFalse(result['sucesso'])
                self.assertTrue(result['revisao_titulo'])

    def test_lote_automatic_placeholder_blocked(self):
        from modules.cortes import corte_de_plano
        from modules.lote import validar_exportacao_automatica
        self.p.titulos=[]
        self.p.titulo_escolhido='SEM TÍTULO'
        corte=corte_de_plano(self.p)
        with self.assertRaisesRegex(ValueError,'Nenhum título'):
            validar_exportacao_automatica(corte,self.t,[],[])
        self.assertFalse(corte.selecionado)

    def test_revalidate_saved_one_removes_old_quantity_warning(self):
        self.p.titulos_v2={'candidatos':fixtures.propostas()[:1]}
        self.p.avisos=['Titulo V2: Menos de 3 propostas distintas aprovadas']
        V.reavaliar_existentes([self.p],self.t)
        self.assertEqual(1,len(self.p.titulos))
        self.assertNotIn('Menos de 3',' '.join(self.p.avisos))
        self.assertFalse(V.titulo_pendente(self.p.titulo))

    def test_cache_zero_candidates_triggers_correction(self):
        self.gerar([fixtures.propostas()[6]],[fixtures.propostas()[6]])
        for path in V.TITULOS_DIR.joinpath('candidatos').glob('*.json'):
            data=json.loads(path.read_text())
            data['candidatos']=[]
            path.write_text(json.dumps(data))
        provider=fixtures.ProviderFalso([self.pacote(fixtures.propostas()[:1])])
        V.gerar_titulos_v2([self.p],self.t,self.config,provedor=provider)
        self.assertEqual(1,len(provider.prompts))
        self.assertIn('CORRECAO UNICA',provider.prompts[0])
        self.assertEqual(1,len(self.p.titulos))

    def test_fallback_correction_on_transport_failure_respects_global_budget(self):
        for code in (429,408,503,404):
            with self.subTest(code=code):
                config=ConfigIA(fallback_automatico=True,max_chamadas_episodio=4)
                clients={name:Mock() for name in config.ordem_fallback}
                clients['mistral'].analisar_uma_vez.side_effect=[self.pacote([fixtures.propostas()[6]]),ErroProvider('failed',codigo=code)]
                clients['groq'].analisar_uma_vez.return_value=self.pacote(fixtures.propostas()[:1])
                provider=ProviderFallback(config,factory=lambda name,**kw:clients[name])
                budget=OrcamentoChamadas(4)
                V.gerar_titulos_v2([self.p],self.t,config,provedor=provider,orcamento=budget,usar_cache=False)
                self.assertEqual(3,budget.usadas)
                self.assertEqual(4,budget.maximo)
                self.assertEqual(1,len(self.p.titulos))
                clients['gemini'].analisar_uma_vez.assert_not_called()
                self.assertEqual(clients['mistral'].analisar_uma_vez.call_args_list[1].args[1],
                                 clients['groq'].analisar_uma_vez.call_args.args[1])

    def test_no_fallback_for_low_scores_only_correction_same_provider(self):
        config=ConfigIA(fallback_automatico=True,max_chamadas_episodio=4)
        client=Mock()
        client.analisar_uma_vez.side_effect=[self.pacote([fixtures.propostas()[6]]),self.pacote(fixtures.propostas()[:1])]
        factory=Mock(return_value=client)
        provider=ProviderFallback(config,factory=factory)
        V.gerar_titulos_v2([self.p],self.t,config,provedor=provider,usar_cache=False)
        self.assertEqual(['mistral','mistral'],[call.args[0] for call in factory.call_args_list])

    def test_exhausted_budget_prevents_fallback_and_leaves_title_review(self):
        config=ConfigIA(fallback_automatico=True,max_chamadas_episodio=2)
        client=Mock()
        client.analisar_uma_vez.side_effect=[self.pacote([fixtures.propostas()[6]]),ErroProvider('quota',codigo=429)]
        factory=Mock(return_value=client)
        budget=OrcamentoChamadas(2)
        provider=ProviderFallback(config,factory=factory)
        V.gerar_titulos_v2([self.p],self.t,config,provedor=provider,orcamento=budget,usar_cache=False)
        self.assertEqual(2,budget.usadas)
        self.assertEqual(2,factory.call_count)
        self.assertEqual(V.STATUS_REVISAO_TITULO,self.p.status)

    def test_two_distinct_same_type_are_not_discarded(self):
        candidates=deepcopy(fixtures.propostas()[:2])
        for c in candidates:
            c['tipo']='curiosidade'
        self.gerar(candidates)
        self.assertEqual(2,len(self.p.titulos))

    def test_adapter_analysis_called_once_even_on_correction(self):
        analise=Mock(return_value=[self.p])
        provider=fixtures.ProviderFalso([self.pacote([fixtures.propostas()[6]]),self.pacote(fixtures.propostas()[:1])])
        V.executar_analise_com_titulos(self.t,self.config,analise=analise,provedor_titulos=provider)
        analise.assert_called_once()

    def test_correction_provider_failure_is_review_not_uncaught_runtime_error(self):
        provider=Mock()
        provider.gerencia_orcamento=False
        provider.analisar_uma_vez=None
        provider.analisar.side_effect=[self.pacote([fixtures.propostas()[6]]),ErroProvider('quota',codigo=429)]
        V.gerar_titulos_v2([self.p],self.t,self.config,provedor=provider,usar_cache=False)
        self.assertEqual(V.STATUS_REVISAO_TITULO,self.p.status)
        self.assertEqual(2,provider.analisar.call_count)

    def test_real_remix_validator_remains_valid_with_pending_title(self):
        from modules.agencia.remix import avaliar_remix_real
        from modules.agencia.timeline import construir_timeline
        timeline=construir_timeline(self.p,self.t)
        before=avaliar_remix_real(self.p,timeline,self.t)
        self.assertTrue(before['remix_valido'],before)
        self.gerar([fixtures.propostas()[6]],[fixtures.propostas()[6]])
        after=avaliar_remix_real(self.p,timeline,self.t)
        self.assertTrue(after['remix_valido'],after)
        self.assertEqual(before['score_remix'],after['score_remix'])

    def test_pending_title_cannot_render_preview(self):
        from modules.agencia.pipeline import renderizar_previa_agencia
        self.p.titulos=[]
        self.p.titulo_escolhido=''
        with patch('modules.agencia.pipeline.executar_ffmpeg',side_effect=AssertionError('No render')):
            result=renderizar_previa_agencia('missing.mp4',self.p,self.t,None,'missing_output.mp4')
        self.assertFalse(result['sucesso'])

    def test_saved_sem_titulo_is_replaced_by_review_and_deselected(self):
        self.p.titulo_escolhido='SEM TÍTULO'
        self.p.titulos=[]
        self.p.status='revisão'
        self.p.titulos_v2={'candidatos':[fixtures.propostas()[6]]}
        self.p.avisos=['Titulo V2: Menos de 3 propostas distintas aprovadas']
        V.reavaliar_existentes([self.p],self.t)
        self.assertEqual('',self.p.titulo_escolhido)
        self.assertEqual('REVISÃO NECESSÁRIA',V.titulo_exibicao(self.p))
        self.assertEqual(V.STATUS_REVISAO_TITULO,self.p.status)
        self.assertFalse(self.p.selecionado)
