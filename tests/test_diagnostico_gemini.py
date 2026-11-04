"""Diagnóstico Gemini com SDK e rede simulados."""
import queue
import socket
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from modules.ai.gemini import GeminiProvider
from modules.ai.errors import ErroProvider
from ui.tela_config_ia import TelaConfigIA


class TestDiagnosticoGemini(unittest.TestCase):
    def test_categorias_registradas_e_exibidas_sem_segredo(self):
        segredo = "AIza-chave-secreta"
        casos = [(401, "401/403"), (403, "401/403"), (429, "429"),
                 (404, "modelo indisponível"), (500, "erro 5xx"),
                 (502, "erro 5xx"), (503, "erro 5xx"), (504, "erro 5xx"),
                 (TimeoutError(segredo), "timeout"),
                 (socket.gaierror(segredo), "DNS/rede"),
                 (ConnectionError(segredo), "DNS/rede")]
        wrapper = RuntimeError(segredo)
        wrapper.__cause__ = socket.gaierror(segredo)
        casos.append((wrapper, "DNS/rede"))
        for origem, categoria in casos:
            with self.subTest(categoria=categoria, origem=origem), \
                    patch('socket.socket.connect', side_effect=AssertionError('Rede proibida')), \
                    patch('socket.socket.connect_ex', side_effect=AssertionError('Rede proibida')), \
                    patch('google.genai.Client') as sdk:
                erro = origem
                if isinstance(origem, int):
                    erro = RuntimeError(segredo)
                    erro.code = origem
                provider = GeminiProvider(segredo)
                sdk.return_value.models.generate_content.side_effect = erro
                with self.assertRaises(ErroProvider) as ctx:
                    provider.testar_conexao('gemini-teste')
                self.assertEqual(categoria, ctx.exception.categoria)
                self.assertNotIn(segredo, str(ctx.exception))
                tela = SimpleNamespace(_eventos=queue.Queue())
                with patch('ui.tela_config_ia.obter_provider', return_value=provider), \
                        patch('ui.tela_config_ia.perf_counter', side_effect=[10, 10.125]), \
                        self.assertLogs('ui.tela_config_ia', level='INFO') as logs:
                    TelaConfigIA._worker(tela, 'teste', 'gemini', 'gemini-teste', 0)
                evento = tela._eventos.get_nowait()
                self.assertFalse(evento['ok'])
                self.assertEqual(categoria, evento['categoria'])
                self.assertEqual(125, evento['latency_ms'])
                self.assertIn('Provider: Gemini', evento['erro'])
                self.assertIn('Modelo: gemini-teste', evento['erro'])
                self.assertIn('Latência: 125 ms', evento['erro'])
                self.assertIn('Categoria: ' + categoria, evento['erro'])
                self.assertNotIn(segredo, str(evento) + str(logs.output))
                sdk.return_value.models.list.assert_not_called()
                self.assertEqual(2, sdk.return_value.models.generate_content.call_count)

    def test_outros_providers_nao_recebem_diagnostico_gemini(self):
        for nome in ('groq', 'mistral'):
            tela = SimpleNamespace(_eventos=queue.Queue())
            provider = Mock()
            provider.testar_conexao.side_effect = ConnectionError('segredo')
            with patch('ui.tela_config_ia.obter_provider', return_value=provider), \
                    patch('ui.tela_config_ia.logger') as logger:
                TelaConfigIA._worker(tela, 'teste', nome, 'modelo', 0)
            self.assertNotIn('categoria', tela._eventos.get_nowait())
            logger.info.assert_not_called()
