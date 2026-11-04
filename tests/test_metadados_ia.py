"""Consistência dos metadados comuns, sem acessar SDKs ou redes reais."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from modules.ai.errors import ErroProvider
from modules.ai.factory import obter_provider
from modules.ai.fallback import ProviderFallback
from modules.ai.response import com_metadados
from modules.config_ia import ConfigIA


CAMPOS = {
    'provider_solicitado', 'provider_utilizado', 'modelo_utilizado',
    'numero_de_tentativas', 'fallback_ocorreu', 'motivo_fallback', 'latencia_total_ms',
}


class TestMetadadosIA(unittest.TestCase):
    def setUp(self):
        for alvo in ('socket.socket.connect', 'socket.socket.connect_ex'):
            p = patch(alvo, side_effect=AssertionError('Rede bloqueada nos testes'))
            p.start()
            self.addCleanup(p.stop)

    def test_providers_sem_fallback_registram_mesmos_campos(self):
        for nome, alvo in (
            ('gemini', 'google.genai.Client'),
            ('mistral', 'modules.ai.mistral._criar_cliente'),
            ('groq', 'modules.ai.groq._criar_cliente'),
        ):
            with self.subTest(provider=nome), patch(alvo) as sdk:
                cfg = ConfigIA(provider_ativo=nome, fallback_automatico=False)
                cliente = sdk.return_value
                dados = {'candidatos': [{'inicio': 0, 'fim': 60, 'descricao': 'Original'}]}
                if nome == 'gemini':
                    cliente.models.generate_content.return_value = SimpleNamespace(
                        parsed=None, text=json.dumps(dados), candidates=[])
                else:
                    cliente.models.list.return_value = SimpleNamespace(
                        data=[SimpleNamespace(id=cfg.modelo)])
                    resposta = SimpleNamespace(choices=[SimpleNamespace(
                        finish_reason='stop', message=SimpleNamespace(
                            content=json.dumps(dados), refusal=None))])
                    gerar = cliente.chat.complete if nome == 'mistral' else cliente.chat.completions.create
                    gerar.return_value = resposta
                provider = obter_provider(cfg, chave='chave-ficticia')
                resultado = provider.analisar(cfg.modelo, 'prompt original')
                self.assertTrue(CAMPOS <= resultado.keys())
                self.assertEqual(nome, resultado['provider_solicitado'])
                self.assertEqual(nome, resultado['provider_utilizado'])
                self.assertEqual(cfg.modelo, resultado['modelo_utilizado'])
                self.assertEqual(1, resultado['numero_de_tentativas'])
                self.assertIs(False, resultado['fallback_ocorreu'])
                self.assertIsNone(resultado['motivo_fallback'])
                self.assertEqual(resultado['latency_ms'], resultado['latencia_total_ms'])
                self.assertGreaterEqual(resultado['latencia_total_ms'], 0)
                self.assertEqual(dados['candidatos'], resultado['candidatos'])

    def test_fallback_sobrescreve_metadados_individuais(self):
        cfg = ConfigIA(fallback_automatico=True)
        mistral, groq = Mock(), Mock()
        mistral.analisar_uma_vez.side_effect = ErroProvider('Limite', codigo=429)
        with patch('modules.ai.response.perf_counter', return_value=10):
            groq.analisar_uma_vez.return_value = com_metadados(
                {'candidatos': []}, 'groq', cfg.modelo_groq, 9)
        factory = Mock(side_effect=lambda nome, **kwargs: {'mistral': mistral, 'groq': groq}[nome])
        resultado = ProviderFallback(cfg, factory=factory).analisar(cfg.modelo, 'prompt original')
        self.assertTrue(CAMPOS <= resultado.keys())
        self.assertEqual('mistral', resultado['provider_solicitado'])
        self.assertEqual('groq', resultado['provider_utilizado'])
        self.assertEqual(cfg.modelo_groq, resultado['modelo_utilizado'])
        self.assertEqual(2, resultado['numero_de_tentativas'])
        self.assertIs(True, resultado['fallback_ocorreu'])
        self.assertEqual('mistral_429', resultado['motivo_fallback'])
        self.assertGreaterEqual(resultado['latencia_total_ms'], 0)

    def test_metadados_do_conteudo_nao_sobrescrevem_valores_calculados(self):
        dados = {campo: 'valor-nao-confiavel' for campo in CAMPOS}
        dados['candidatos'] = []
        with patch('modules.ai.response.perf_counter', return_value=10):
            resultado = com_metadados(dados, 'mistral', 'modelo-real', 9)
        self.assertEqual('mistral', resultado['provider_solicitado'])
        self.assertEqual('mistral', resultado['provider_utilizado'])
        self.assertEqual('modelo-real', resultado['modelo_utilizado'])
        self.assertEqual(1, resultado['numero_de_tentativas'])
        self.assertIs(False, resultado['fallback_ocorreu'])
        self.assertIsNone(resultado['motivo_fallback'])
        self.assertEqual(1000, resultado['latencia_total_ms'])
        self.assertEqual('valor-nao-confiavel', dados['provider_solicitado'])


if __name__ == '__main__':
    unittest.main()
