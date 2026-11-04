import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from modules.ai import AIProvider, GeminiProvider, obter_provider
from modules.ai.gemini import ErroGemini, GeminiAgencyProvider, _RespostaGemini
from modules import analise_ia, config_ia
from modules.agencia import editorial
from modules.config_ia import ConfigIA


class TestAIProvider(unittest.TestCase):
    def setUp(self):
        client = patch('google.genai.Client')
        self.client_class = client.start()
        self.addCleanup(client.stop)
        self.client = self.client_class.return_value

    def resposta(self, dados, *, parsed=False, finish='STOP'):
        self.client.models.generate_content.return_value = SimpleNamespace(
            parsed=Mock(model_dump=Mock(return_value=dados)) if parsed else None,
            text=json.dumps(dados), candidates=[SimpleNamespace(finish_reason=finish)])

    def test_contrato_e_alias_legado(self):
        provider = GeminiProvider('chave-falsa')
        self.assertIsInstance(provider, AIProvider)
        self.assertIs(analise_ia.ProvedorGemini, GeminiProvider)
        self.assertIs(analise_ia.ProvedorAnalise, editorial.ProvedorAgencia)

    def test_listagem_filtra_ordena_e_remove_duplicados(self):
        self.client.models.list.return_value = [
            SimpleNamespace(name='models/gemini-b', supported_actions=[]),
            SimpleNamespace(name='gemini-a', supported_actions=['generateContent']),
            SimpleNamespace(name='gemini-b', supported_actions=[]),
            SimpleNamespace(name='outro', supported_actions=[]),
            SimpleNamespace(name='gemini-sem-geracao', supported_actions=['embedContent'])]
        self.assertEqual(['gemini-a', 'gemini-b'], GeminiProvider('x').listar_modelos())

    def test_analise_preserva_prompt_schema_e_parametros(self):
        for parsed in (False, True):
            with self.subTest(parsed=parsed):
                self.resposta({'candidatos': []}, parsed=parsed)
                resposta = GeminiProvider('x').analisar('modelo', 'prompt original')
                self.assertEqual([], resposta['candidatos'])
                self.assertEqual('gemini', resposta['provider'])
                self.assertEqual('modelo', resposta['model'])
                self.assertGreaterEqual(resposta['latency_ms'], 0)
                args = self.client.models.generate_content.call_args.kwargs
                self.assertEqual('modelo', args['model'])
                self.assertEqual('prompt original', args['contents'])
                self.assertIs(_RespostaGemini, args['config'].response_schema)
                self.assertEqual(0.55, args['config'].temperature)
                self.assertEqual(8192, args['config'].max_output_tokens)

    def test_agencia_preserva_timeout_schema_e_parametros(self):
        self.resposta({'historias': []})
        provider = editorial.ProvedorGeminiAgencia('x')
        self.assertIsInstance(provider, GeminiProvider)
        self.assertEqual(360000, self.client_class.call_args.kwargs['http_options'].timeout)
        resposta = provider.analisar('m', 'p')
        self.assertEqual([], resposta['historias'])
        self.assertEqual('gemini', resposta['provider'])
        self.assertEqual('m', resposta['model'])
        self.assertGreaterEqual(resposta['latency_ms'], 0)
        config = self.client.models.generate_content.call_args.kwargs['config']
        self.assertIs(editorial._RespostaAgencia, config.response_schema)
        self.assertEqual(0.5, config.temperature)
        self.assertEqual(16384, config.max_output_tokens)

    def test_factory_e_wrappers_usam_credencial_simulada(self):
        with patch('modules.ai.factory.obter_chave_gemini', return_value='fake'):
            self.assertIsInstance(obter_provider(ConfigIA()), GeminiProvider)
            self.assertIsInstance(obter_provider(ConfigIA(), modo='agencia', schema=editorial._RespostaAgencia), GeminiAgencyProvider)
        with patch.object(analise_ia, 'obter_chave_gemini', return_value='fake'):
            self.assertIsInstance(analise_ia.criar_provedor_configurado(ConfigIA()), AIProvider)
        with patch.object(editorial, 'obter_chave_gemini', return_value='fake'):
            self.assertIsInstance(editorial.criar_provedor_agencia(ConfigIA()), AIProvider)

    def test_factory_rejeita_provider_desconhecido_sem_cliente(self):
        with self.assertRaises(ValueError):
            obter_provider(ConfigIA(provider='desconhecido'))
        self.client_class.assert_not_called()

    def test_sem_chave_nao_cria_cliente(self):
        with patch('modules.ai.factory.obter_chave_gemini', return_value=None):
            with self.assertRaises(ErroGemini):
                obter_provider(ConfigIA())
        self.client_class.assert_not_called()

    def test_respostas_invalidas_e_truncadas_sao_recusadas(self):
        provider = GeminiProvider('x')
        for dados, finish in [({'candidatos': []}, 'MAX_TOKENS'), ({'historias': []}, 'STOP')]:
            self.resposta(dados, finish=finish)
            with self.assertRaises(ErroGemini):
                provider.analisar('m', 'p')

    def test_agencia_repete_so_503_com_sleep_simulado(self):
        provider = editorial.ProvedorGeminiAgencia('x')
        with patch.object(provider, '_analisar_uma_vez', side_effect=[ErroGemini('ocupado', codigo=503), {'historias': []}]) as call, patch('time.sleep') as sleep:
            self.assertEqual({'historias': []}, provider.analisar('m', 'p'))
            self.assertEqual(2, call.call_count)
            sleep.assert_called_once_with(20)
        with patch.object(provider, '_analisar_uma_vez', side_effect=ErroGemini('cota', codigo=429)) as call, patch('time.sleep') as sleep:
            with self.assertRaises(ErroGemini):
                provider.analisar('m', 'p')
            call.assert_called_once()
            sleep.assert_not_called()

    def test_config_antiga_carrega_gemini_por_padrao(self):
        with tempfile.TemporaryDirectory() as pasta:
            path = Path(pasta) / 'config.json'
            path.write_text(json.dumps({'modelo': 'gemini-antigo'}), encoding='utf-8')
            with patch.object(config_ia, 'CONFIG_IA_PATH', path):
                config = config_ia.carregar_config_ia()
                self.assertEqual('gemini', config.provider)
                self.assertEqual('gemini-antigo', config.modelo)


if __name__ == '__main__':
    unittest.main()
