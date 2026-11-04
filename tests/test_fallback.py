"""Todas as chamadas simuladas; sockets bloqueados durante cada teste."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_analise_ia import transcricao_teste
from test_agencia_editorial import episodio_longo, remix_em, normal_em
from modules import analise_ia, config_ia
from modules.agencia import editorial
from modules.agencia import rollback_2809
from modules.ai.errors import ErroProvider, erro_seguro
from modules.ai.fallback import FalhaAnalise, LimiteChamadas, OrcamentoChamadas, ProviderFallback
from modules.ai.factory import obter_provider
from modules.config_ia import ConfigIA


class TestFallback(unittest.TestCase):
    def setUp(self):
        for alvo in ('socket.socket.connect', 'socket.socket.connect_ex'):
            p = patch(alvo, side_effect=AssertionError('Nenhuma rede permitida'))
            p.start()
            self.addCleanup(p.stop)
        self.cfg = ConfigIA(fallback_automatico=True, max_chamadas_episodio=5)
        self.providers = {p: Mock() for p in self.cfg.ordem_fallback}
        for p, mock in self.providers.items():
            mock.analisar_uma_vez.return_value = {'candidatos': [], 'model': getattr(self.cfg, f'modelo_{p}')}
        self.factory = Mock(side_effect=lambda nome, **kwargs: self.providers[nome])
        self.sleep = Mock()

    def cadeia(self, **kwargs):
        return ProviderFallback(self.cfg, factory=self.factory, dormir=self.sleep, **kwargs)

    def falhar(self, provider, codigo, espera=None):
        self.providers[provider].analisar_uma_vez.side_effect = ErroProvider(
            'segredo-que-nao-pode-aparecer', codigo=codigo, aguardar_segundos=espera)

    def test_mistral_ok_sem_outras_chamadas(self):
        r = self.cadeia().analisar('ignorado', 'prompt original')
        self.assertEqual('mistral', r['provider_utilizado'])
        self.assertEqual(1, r['numero_de_tentativas'])
        self.assertFalse(r['fallback_ocorreu'])
        self.factory.assert_called_once_with('mistral', modo='analise', schema=None)
        self.providers['mistral'].listar_modelos.assert_not_called()

    def test_429_timeout_500_modelo_rede_usam_groq(self):
        for erro in (ErroProvider('429', codigo=429), TimeoutError('segredo'),
                     ErroProvider('500', codigo=500), ErroProvider('404', codigo=404),
                     ConnectionError('segredo'), ErroProvider('503', codigo=503)):
            with self.subTest(erro=type(erro).__name__, codigo=getattr(erro, 'codigo', None)):
                self.providers['mistral'].analisar_uma_vez.side_effect = erro
                r = self.cadeia().analisar('modelo', 'prompt original')
                self.assertEqual('groq', r['provider_utilizado'])
                self.assertEqual(2, r['numero_de_tentativas'])
                self.assertTrue(r['fallback_ocorreu'])
                self.assertEqual(self.providers['mistral'].analisar_uma_vez.call_args.args[1],
                                 self.providers['groq'].analisar_uma_vez.call_args.args[1])
        self.providers['gemini'].analisar_uma_vez.assert_not_called()

    def test_401_403_interrompem_por_padrao(self):
        for codigo in (401, 403):
            self.falhar('mistral', codigo)
            with self.assertRaises(FalhaAnalise) as erro:
                self.cadeia().analisar('', 'prompt')
            self.assertEqual(codigo, erro.exception.codigo)
            self.assertNotIn('segredo', str(erro.exception))
        self.providers['groq'].analisar_uma_vez.assert_not_called()

    def test_auth_fallback_opcional(self):
        self.cfg.fallback_autenticacao = True
        self.falhar('mistral', 401)
        self.assertEqual('groq', self.cadeia().analisar('', '')['provider_utilizado'])

    def test_dois_429_gemini_ok(self):
        self.falhar('mistral', 429)
        self.falhar('groq', 429)
        r = self.cadeia().analisar('', '')
        self.assertEqual('gemini', r['provider_utilizado'])
        self.assertEqual(3, r['numero_de_tentativas'])
        self.assertEqual('mistral_429;groq_429', r['motivo_fallback'])

    def test_todos_falham_erro_claro_sem_loop(self):
        for p in self.providers:
            self.falhar(p, 500)
        with self.assertRaisesRegex(FalhaAnalise, 'Todos os providers'):
            self.cadeia().analisar('', '')
        self.assertEqual(3, self.factory.call_count)
        for mock in self.providers.values():
            mock.analisar_uma_vez.assert_called_once()

    def test_limite_4_de_5_impede_fallback(self):
        self.falhar('mistral', 429, 1)
        budget = OrcamentoChamadas(5, usadas=4)
        with self.assertRaises(LimiteChamadas):
            self.cadeia(orcamento=budget).analisar('', '')
        self.assertEqual(5, budget.usadas)
        self.providers['mistral'].analisar_uma_vez.assert_called_once()
        self.providers['groq'].analisar_uma_vez.assert_not_called()
        self.sleep.assert_not_called()

    def test_retry_after_respeitado_uma_vez(self):
        self.falhar('mistral', 429, 2)
        r = self.cadeia().analisar('', '')
        self.sleep.assert_called_once_with(2)
        self.assertEqual(2, self.providers['mistral'].analisar_uma_vez.call_count)
        self.assertEqual(3, r['numero_de_tentativas'])
        self.assertEqual('groq', r['provider_utilizado'])

    def test_retry_curto_pode_resolver_no_mesmo_provider(self):
        self.providers['mistral'].analisar_uma_vez.side_effect = [
            ErroProvider('429', codigo=429, aguardar_segundos=1), {'candidatos': []}]
        r = self.cadeia().analisar('', '')
        self.assertEqual('mistral', r['provider_utilizado'])
        self.assertEqual(2, r['numero_de_tentativas'])
        self.providers['groq'].analisar_uma_vez.assert_not_called()

    def test_retry_after_longo_sem_repetir_antes_do_prazo(self):
        self.falhar('mistral', 429, 60)
        self.cadeia().analisar('', '')
        self.sleep.assert_not_called()
        self.providers['mistral'].analisar_uma_vez.assert_called_once()

    def test_retries_todos_limitados_no_orcamento(self):
        self.cfg.max_chamadas_episodio = 6
        for p in self.providers:
            self.falhar(p, 429, 0)
        cadeia = self.cadeia()
        with self.assertRaises(FalhaAnalise):
            cadeia.analisar('', '')
        self.assertEqual(6, cadeia.orcamento.usadas)
        for mock in self.providers.values():
            self.assertEqual(2, mock.analisar_uma_vez.call_count)

    def test_orcamento_compartilhado_janelas_e_nao_repete_provider_falho(self):
        self.falhar('mistral', 500)
        cadeia = self.cadeia(orcamento=OrcamentoChamadas(3))
        cadeia.analisar('', 'janela 1')
        r = cadeia.analisar('', 'janela 2')
        self.assertEqual(1, r['numero_de_tentativas'])
        self.assertEqual('mistral_500', r['motivo_fallback'])
        self.assertEqual(3, cadeia.orcamento.usadas)
        with self.assertRaises(LimiteChamadas):
            cadeia.analisar('', 'janela 3')
        self.providers['mistral'].analisar_uma_vez.assert_called_once()

    def test_preferido_primeiro(self):
        self.cfg.provider_preferido = 'gemini'
        r = self.cadeia().analisar('', '')
        self.assertEqual('gemini', r['provider_solicitado'])
        self.assertEqual('gemini', r['provider_utilizado'])
        self.providers['mistral'].analisar_uma_vez.assert_not_called()

    def test_json_valido_fraco_nao_gera_fallback(self):
        self.providers['mistral'].analisar_uma_vez.return_value = {'candidatos': []}
        self.cadeia().analisar('', '')
        self.providers['groq'].analisar_uma_vez.assert_not_called()

    def test_json_invalido_nao_gera_fallback(self):
        self.providers['mistral'].analisar_uma_vez.side_effect = ErroProvider('JSON invalido')
        with self.assertRaises(FalhaAnalise):
            self.cadeia().analisar('', '')
        self.providers['groq'].analisar_uma_vez.assert_not_called()

    def test_logs_nao_expoem_chave(self):
        self.falhar('mistral', 429, 0)
        with self.assertLogs('ia.fallback', level='INFO') as logs:
            self.cadeia().analisar('', 'segredo-no-prompt')
        texto = '\n'.join(logs.output)
        self.assertNotIn('segredo', texto)
        self.assertIn('[Mistral] retry -> 429', texto)
        self.assertIn('Fallback concluído: Groq', texto)

    def test_retry_after_header(self):
        erro = SimpleNamespace(status_code=429, response=SimpleNamespace(headers={'Retry-After': '2'}))
        self.assertEqual(2, erro_seguro(erro, 'mistral').aguardar_segundos)

    def test_factory_fallback_lazy_sem_chave_ou_rede(self):
        with patch('modules.ai.factory.obter_chave') as chave, patch('modules.ai.factory.obter_chave_gemini') as gemini:
            self.assertIsInstance(obter_provider(self.cfg), ProviderFallback)
            chave.assert_not_called()
            gemini.assert_not_called()

    def test_config_legada_e_persistencia(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(config_ia, 'CONFIG_IA_PATH', Path(tmp)/'config.json'):
            config_ia.CONFIG_IA_PATH.write_text('{"modelo": "gemini-antigo"}')
            antiga = config_ia.carregar_config_ia()
            self.assertEqual('gemini', antiga.provider_ativo)
            self.assertFalse(antiga.fallback_automatico)
            self.cfg.provider_preferido = 'groq'
            config_ia.salvar_config_ia(self.cfg)
            nova = config_ia.carregar_config_ia()
            self.assertTrue(nova.fallback_automatico)
            self.assertEqual('groq', nova.provider_preferido)
            self.assertEqual(['mistral', 'groq', 'gemini'], nova.ordem_fallback)

    def test_cache_normal_associado_a_groq_e_reutilizado_sem_api(self):
        self.falhar('mistral', 429)
        t = episodio_longo('comum-fallback-universal')
        self.cfg.max_chamadas_episodio = 2
        self.providers['groq'].analisar_uma_vez.return_value = {'historias': [remix_em(1)]}
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(analise_ia, 'ANALISES_DIR', Path(tmp)/'cache'), \
                patch.object(analise_ia, 'DIAGNOSTICOS_DIR', Path(tmp)/'diag'), \
                patch.object(editorial, 'ANALISES_AGENCIA_DIR', Path(tmp)/'agencia'), \
                patch.object(rollback_2809,'DADOS_DIR',Path(tmp)/'dados'), \
                patch('modules.intro.INTROS_DIR', Path(tmp)/'intro'):
            analise_ia.analisar_transcricao(t, self.cfg, self.cadeia(modo='agencia', schema=editorial._RespostaAgencia), usar_cache=False)
            real = deepcopy(self.cfg)
            real.provider_ativo = 'groq'
            dados = json.loads(rollback_2809.chave(t, real).read_text(encoding='utf-8'))
            self.assertEqual('groq', dados['provider'])
            self.assertEqual('groq', dados['metadados_ia'][0]['provider_utilizado'])
            self.assertFalse(editorial.caminho_cache_agencia(t, self.cfg).exists())
            self.factory.reset_mock()
            analise_ia.analisar_transcricao(t, self.cfg, self.cadeia(modo='agencia', schema=editorial._RespostaAgencia), usar_cache=True)
            self.factory.assert_not_called()
            self.assertFalse(editorial.caminho_cache_agencia(t, self.cfg).exists())

    def test_agencia_prompt_identico_remix_e_cache_real(self):
        self.cfg.max_chamadas_episodio = 2
        self.falhar('mistral', 429)
        self.providers['groq'].analisar_uma_vez.return_value = {
            'historias': [remix_em(1), normal_em(125)], 'model': self.cfg.modelo_groq}
        t = episodio_longo('fallback-agencia')
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(editorial, 'ANALISES_AGENCIA_DIR', Path(tmp)/'cache'), \
                patch.object(rollback_2809,'DADOS_DIR',Path(tmp)/'dados'), \
                patch('modules.intro.INTROS_DIR', Path(tmp)/'intro'), \
                patch.object(editorial, 'completar_com_remixes_salvos', side_effect=lambda planos, *args: (planos, [])):
            resultado = editorial.analisar_agencia(t, self.cfg,
                self.cadeia(modo='agencia', schema=editorial._RespostaAgencia), usar_cache=False)
            p1 = self.providers['mistral'].analisar_uma_vez.call_args.args[1]
            p2 = self.providers['groq'].analisar_uma_vez.call_args.args[1]
            self.assertEqual(p1, p2)
            self.assertIn('REMIXES NARRATIVOS', p2)
            self.assertIn('Quantidade e limite',p2)
            for call in self.factory.call_args_list:
                self.assertIs(editorial._RespostaAgencia, call.kwargs['schema'])
            self.assertEqual(1, len(resultado))
            self.assertTrue(resultado[0].usar_remix)
            real = deepcopy(self.cfg)
            real.provider_ativo = 'groq'
            dados = json.loads(rollback_2809.chave(t, real).read_text(encoding='utf-8'))
            self.assertEqual('groq', dados['provider'])
            self.assertFalse(editorial.caminho_cache_agencia(t, self.cfg).exists())
            self.factory.reset_mock()
            self.assertEqual(1, len(editorial.analisar_agencia(t, self.cfg, self.cadeia(), usar_cache=True)))
            self.factory.assert_not_called()


if __name__ == '__main__':
    unittest.main()
