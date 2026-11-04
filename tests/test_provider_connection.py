"""Geração mínima de diagnóstico: um pedido, sem catálogo, análise ou reserva."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from modules.ai import GeminiProvider, MistralProvider, GroqProvider
from modules.ai.errors import ErroProvider


class TestProviderConnection(unittest.TestCase):
    def setUp(self):
        self.patches = []
        for alvo in ('socket.socket.connect', 'socket.socket.connect_ex'):
            self.patch(alvo, side_effect=AssertionError('Rede proibida nos testes'))
        self.gemini = self.patch('google.genai.Client')
        self.mistral = self.patch('modules.ai.mistral._criar_cliente')
        self.groq = self.patch('modules.ai.groq._criar_cliente')

    def patch(self, alvo, **kwargs):
        p = patch(alvo, **kwargs)
        self.addCleanup(p.stop)
        return p.start()

    def criar(self, nome):
        if nome == 'gemini':
            p = GeminiProvider('fake')
            client = self.gemini.return_value
            gerar = client.models.generate_content
            gerar.return_value = SimpleNamespace(text='OK')
        else:
            cls, factory = (MistralProvider, self.mistral) if nome == 'mistral' else (GroqProvider, self.groq)
            p = cls('fake')
            client = factory.return_value
            gerar = client.chat.complete if nome == 'mistral' else client.chat.completions.create
            gerar.return_value = SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',
                message=SimpleNamespace(content='OK'))])
        return p, client, gerar

    def test_um_pedido_minimo_sem_listagem_editorial_ou_fallback(self):
        for nome in ('gemini', 'mistral', 'groq'):
            with self.subTest(provider=nome):
                p, client, gerar = self.criar(nome)
                with patch.object(p, 'analisar', side_effect=AssertionError('Análise editorial proibida')):
                    resultado = p.testar_conexao(modelo='modelo-exato')
                self.assertTrue(resultado['ok'])
                self.assertEqual(nome, resultado['provider'])
                self.assertEqual('modelo-exato', resultado['model'])
                self.assertGreaterEqual(resultado['latency_ms'], 0)
                gerar.assert_called_once()
                client.models.list.assert_not_called()
                opcoes = gerar.call_args.kwargs
                self.assertEqual('modelo-exato', opcoes['model'])
                if nome == 'gemini':
                    self.assertEqual('Responda apenas OK', opcoes['contents'])
                    self.assertEqual(128, opcoes['config'].max_output_tokens)
                    self.assertEqual(30000, opcoes['config'].http_options.timeout)
                    self.assertEqual(1, opcoes['config'].http_options.retry_options.attempts)
                    self.assertIsNone(opcoes['config'].response_schema)
                else:
                    self.assertEqual([{'role': 'user', 'content': 'Responda apenas OK'}], opcoes['messages'])
                    self.assertNotIn('response_format', opcoes)
                    if nome == 'mistral':
                        self.assertEqual(32, opcoes['max_tokens'])
                        self.assertEqual(30000, opcoes['timeout_ms'])
                        self.assertIsNone(opcoes['retries'])
                    else:
                        self.assertEqual(256, opcoes['max_completion_tokens'])
                        self.assertEqual(30, opcoes['timeout'])
                        self.assertEqual(0, self.groq.call_args.kwargs['max_retries'])

    def test_groq_preferido_usa_raciocinio_baixo(self):
        p, _, gerar = self.criar('groq')
        p.testar_conexao(modelo=GroqProvider.MODELO_PADRAO)
        self.assertEqual('low', gerar.call_args.kwargs['reasoning_effort'])

    def test_erros_sanitizados_sem_retry_ou_reserva(self):
        for nome in ('gemini', 'mistral', 'groq'):
            for codigo in (401, 403, 429, 404, 503):
                with self.subTest(provider=nome, codigo=codigo):
                    p, client, gerar = self.criar(nome)
                    gerar.reset_mock()
                    erro = RuntimeError('chave-secreta-no-sdk')
                    erro.status_code = codigo
                    gerar.side_effect = erro
                    with self.assertRaises(ErroProvider) as ctx:
                        p.testar_conexao(modelo='ausente')
                    self.assertEqual(codigo, ctx.exception.codigo)
                    self.assertNotIn('chave-secreta', str(ctx.exception))
                    gerar.assert_called_once()
                    client.models.list.assert_not_called()
            gerar.side_effect = None

    def test_timeout_sanitizado(self):
        for nome in ('gemini', 'mistral', 'groq'):
            p, _, gerar = self.criar(nome)
            gerar.side_effect = TimeoutError('chave-secreta')
            with self.assertRaises(ErroProvider) as ctx:
                p.testar_conexao(modelo='m')
            self.assertEqual(408, ctx.exception.codigo)
            self.assertNotIn('chave-secreta', str(ctx.exception))
            gerar.side_effect = None

    def test_resposta_diferente_de_ok_e_recusada(self):
        for nome in ('gemini', 'mistral', 'groq'):
            p, _, gerar = self.criar(nome)
            if nome == 'gemini':
                gerar.return_value.text = 'outro conteúdo'
            else:
                gerar.return_value.choices[0].message.content = 'outro conteúdo'
            with self.assertRaises(ErroProvider):
                p.testar_conexao(modelo='m')


if __name__ == '__main__':
    unittest.main()
