"""Providers simulados: toda conexão de socket fica bloqueada nesta suíte."""
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import traceback
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import ANY, Mock, patch

from modules import analise_ia, config_ia, credenciais
from modules.agencia import editorial
from modules.ai import AIProvider, GeminiProvider, MistralProvider, GroqProvider, obter_provider
from modules.ai.errors import ErroProvider
from modules.ai.gemini import ErroGemini
from modules.ai.response import normalizar_resposta
from modules.config_ia import ConfigIA


class FalhaSDK(Exception):
    def __init__(self, codigo, segredo="segredo-de-teste"):
        super().__init__(segredo)
        self.status_code = codigo
        self.response = SimpleNamespace(headers={"retry-after": "12.5"})


def resposta(conteudo, finish="stop"):
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish,
        message=SimpleNamespace(content=conteudo, refusal=None))])


class TestMultiProvider(unittest.TestCase):
    def setUp(self):
        from modules.ai import mistral, groq
        self.mistral_loader = mistral._criar_cliente
        self.groq_loader = groq._criar_cliente
        self.addCleanup(patch.stopall)
        patch('socket.socket.connect', side_effect=AssertionError('Rede proibida nos testes')).start()
        patch('socket.socket.connect_ex', side_effect=AssertionError('Rede proibida nos testes')).start()
        patch.dict(os.environ, {nome: "" for nome in credenciais.VARIAVEIS.values()}).start()
        patch('modules.credenciais.keyring.get_password', return_value=None).start()
        self.mistral_factory = patch('modules.ai.mistral._criar_cliente').start()
        self.groq_factory = patch('modules.ai.groq._criar_cliente').start()
        self.gemini_factory = patch('google.genai.Client').start()

    def provider(self, nome, *, modelos=None, conteudo=None, schema=None):
        factory = self.mistral_factory if nome == 'mistral' else self.groq_factory
        client = factory.return_value
        padrao = 'mistral-small-latest' if nome == 'mistral' else GroqProvider.MODELO_PADRAO
        client.models.list.return_value = SimpleNamespace(data=[SimpleNamespace(id=m) for m in (modelos if modelos is not None else [padrao])])
        gerar = client.chat.complete if nome == 'mistral' else client.chat.completions.create
        gerar.return_value = resposta(conteudo if conteudo is not None else '{"candidatos": []}')
        cls = MistralProvider if nome == 'mistral' else GroqProvider
        return cls('chave-ficticia-123456789', schema=schema), client, gerar, padrao

    def test_criacao_mistral_timeout_sem_retry(self):
        p, _, _, _ = self.provider('mistral')
        self.assertIsInstance(p, AIProvider)
        self.mistral_factory.assert_called_once_with(api_key='chave-ficticia-123456789', timeout_ms=360000, retry_config=None)

    def test_criacao_groq_timeout_sem_retry(self):
        p, _, _, _ = self.provider('groq')
        self.assertIsInstance(p, AIProvider)
        self.groq_factory.assert_called_once_with(api_key='chave-ficticia-123456789', timeout=360, max_retries=0)

    def test_factory_nomes_e_config_ativo(self):
        for nome, cls in [('gemini', GeminiProvider), ('mistral', MistralProvider), ('groq', GroqProvider)]:
            with self.subTest(provider=nome):
                self.assertIsInstance(obter_provider(nome, chave='fake'), cls)
                self.assertIsInstance(obter_provider(ConfigIA(provider_ativo=nome), chave='fake'), cls)

    def test_factory_agencia_encaminha_schema(self):
        for nome in ('mistral', 'groq'):
            p = obter_provider(nome, modo='agencia', schema=editorial._RespostaAgencia, chave='fake')
            self.assertIs(p._schema, editorial._RespostaAgencia)
            self.assertEqual('historias', p._chave_resposta)

    def test_factory_sem_chave_nunca_cria_cliente(self):
        for nome in ('gemini', 'mistral', 'groq'):
            with self.subTest(provider=nome), self.assertRaises(ErroProvider):
                obter_provider(nome)
        self.mistral_factory.assert_not_called()
        self.groq_factory.assert_not_called()
        self.gemini_factory.assert_not_called()

    def test_construtores_sem_chave(self):
        for cls in (MistralProvider, GroqProvider):
            with self.assertRaises(ErroProvider):
                cls()
            with self.assertRaises(ErroProvider):
                cls(' ')

    def test_testar_conexao_so_lista_modelos(self):
        for nome in ('mistral', 'groq'):
            p, client, gerar, _ = self.provider(nome)
            self.assertTrue(p.testar_conexao())
            client.models.list.assert_called_once()
            gerar.assert_not_called()

    def test_listagem_ordena_remove_duplicados(self):
        for nome in ('mistral', 'groq'):
            p, _, _, _ = self.provider(nome, modelos=['modelo-b', 'modelo-a', 'modelo-b'])
            self.assertEqual(['modelo-a', 'modelo-b'], p.listar_modelos())

    def test_mistral_filtra_modelos_sem_chat(self):
        p, client, _, _ = self.provider('mistral')
        client.models.list.return_value.data = [
            SimpleNamespace(id='chat', capabilities=SimpleNamespace(completion_chat=True)),
            SimpleNamespace(id='embedding', capabilities=SimpleNamespace(completion_chat=False))]
        self.assertEqual(['chat'], p.listar_modelos())

    def test_autenticacao_invalida_na_listagem_e_geracao(self):
        for nome in ('mistral', 'groq'):
            for codigo in (401, 403):
                p, client, gerar, modelo = self.provider(nome)
                client.models.list.side_effect = FalhaSDK(codigo)
                with self.assertRaises(ErroProvider) as ctx:
                    p.testar_conexao()
                self.assertEqual(codigo, ctx.exception.codigo)
                client.models.list.side_effect = None
                gerar.side_effect = FalhaSDK(codigo)
                with self.assertRaises(ErroProvider) as ctx:
                    p.analisar(modelo, 'p')
                self.assertEqual(codigo, ctx.exception.codigo)
                gerar.side_effect = None

    def test_429_preserva_espera_sem_retry(self):
        for nome in ('mistral', 'groq'):
            p, _, gerar, modelo = self.provider(nome)
            gerar.side_effect = FalhaSDK(429)
            antes = gerar.call_count
            with self.assertRaises(ErroProvider) as ctx:
                p.analisar(modelo, 'p')
            self.assertEqual(429, ctx.exception.codigo)
            self.assertEqual(12.5, ctx.exception.aguardar_segundos)
            self.assertEqual(antes + 1, gerar.call_count)

    def test_timeout_geracao_e_listagem(self):
        for nome in ('mistral', 'groq'):
            p, client, gerar, modelo = self.provider(nome)
            gerar.side_effect = TimeoutError('segredo')
            with self.assertRaises(ErroProvider) as ctx:
                p.analisar(modelo, 'p')
            self.assertEqual(408, ctx.exception.codigo)
            client.models.list.side_effect = TimeoutError('segredo')
            with self.assertRaises(ErroProvider) as ctx:
                p.testar_conexao()
            self.assertEqual(408, ctx.exception.codigo)

    def test_json_invalido_e_formato_invalido(self):
        for nome in ('mistral', 'groq'):
            for conteudo in ('não é JSON', '[]', '{"historias": []}', '{"candidatos": "texto"}',
                             '{"candidatos": [1]}', '{"candidatos": [{"inicio": NaN}]}'):
                p, _, gerar, modelo = self.provider(nome, conteudo=conteudo)
                gerar.side_effect = None
                with self.assertRaises(ErroProvider):
                    p.analisar(modelo, 'p')

    def test_resposta_truncada_ou_recusada(self):
        for nome in ('mistral', 'groq'):
            for finish in ('length', 'content_filter', 'tool_calls', None):
                p, _, gerar, modelo = self.provider(nome)
                gerar.side_effect = None
                gerar.return_value = resposta('{"candidatos": []}', finish)
                with self.assertRaises(ErroProvider):
                    p.analisar(modelo, 'p')

    def test_mistral_modelo_inexistente_nao_gera(self):
        p, _, gerar, _ = self.provider('mistral')
        with self.assertRaises(ErroProvider) as ctx:
            p.analisar('ausente', 'p')
        self.assertEqual(404, ctx.exception.codigo)
        gerar.assert_not_called()

    def test_groq_fallback_somente_modelo_ausente(self):
        p, _, gerar, _ = self.provider('groq', modelos=['openai/gpt-oss-20b'])
        saida = p.analisar('ausente', 'p')
        self.assertEqual('openai/gpt-oss-20b', saida['model'])
        self.assertEqual('openai/gpt-oss-20b', gerar.call_args.kwargs['model'])

    def test_groq_sem_modelo_de_reserva_nao_gera(self):
        p, _, gerar, _ = self.provider('groq', modelos=['whisper-large-v3'])
        with self.assertRaises(ErroProvider) as ctx:
            p.analisar('ausente', 'p')
        self.assertEqual(404, ctx.exception.codigo)
        gerar.assert_not_called()

    def test_groq_respeita_modelo_configurado_existente(self):
        p, _, gerar, _ = self.provider('groq', modelos=[GroqProvider.MODELO_PADRAO, 'llama-3.1-8b-instant'])
        saida = p.analisar('llama-3.1-8b-instant', 'prompt exato')
        self.assertEqual('llama-3.1-8b-instant', saida['model'])
        self.assertEqual({'type': 'json_object'}, gerar.call_args.kwargs['response_format'])
        self.assertEqual('prompt exato', gerar.call_args.kwargs['messages'][-1]['content'])
        self.assertEqual('system', gerar.call_args.kwargs['messages'][0]['role'])

    def test_groq_nao_troca_modelo_apos_erro_api(self):
        for codigo in (429, 503, 404, 401):
            p, _, gerar, modelo = self.provider('groq', modelos=[GroqProvider.MODELO_PADRAO, 'openai/gpt-oss-20b'])
            gerar.reset_mock(side_effect=True)
            gerar.side_effect = FalhaSDK(codigo)
            with self.assertRaises(ErroProvider):
                p.analisar(modelo, 'p')
            gerar.assert_called_once()
            self.assertEqual(modelo, gerar.call_args.kwargs['model'])

    def test_structured_output_e_prompt_preservado(self):
        for nome in ('mistral', 'groq'):
            p, _, gerar, modelo = self.provider(nome)
            gerar.side_effect = None
            p.analisar(modelo, 'prompt editorial original')
            args = gerar.call_args.kwargs
            self.assertEqual([{'role': 'user', 'content': 'prompt editorial original'}], args['messages'])
            self.assertEqual('json_schema', args['response_format']['type'])
            self.assertEqual('candidatos', next(iter(args['response_format']['json_schema']['schema']['properties'])))

    def test_normalizacao_e_metadados_nao_alteram_editorial(self):
        item = {'inicio': 10, 'fim': 80, 'descricao': 'Cena original', 'titulos': ['A', 'B', 'C']}
        for nome in ('mistral', 'groq'):
            p, _, gerar, modelo = self.provider(nome, conteudo=json.dumps({'candidatos': [item]}))
            gerar.side_effect = None
            saida = p.analisar(modelo, 'p')
            self.assertEqual([item], saida['candidatos'])
            self.assertEqual({
                'candidatos', 'provider', 'model', 'latency_ms',
                'provider_solicitado', 'provider_utilizado', 'modelo_utilizado',
                'numero_de_tentativas', 'fallback_ocorreu', 'motivo_fallback',
                'latencia_total_ms',
            }, set(saida))
            self.assertEqual(nome, saida['provider'])
            self.assertEqual(modelo, saida['model'])
            self.assertGreaterEqual(saida['latency_ms'], 0)

    def test_normalizacao_aliases_e_cercas_markdown(self):
        conteudo = '```json\n{"candidates": [{"start": 10, "end": 80, "description": "Cena", "titles": ["A"]}]}\n```'
        self.assertEqual({'candidatos': [{'inicio': 10, 'fim': 80, 'descricao': 'Cena', 'titulos': ['A']}]},
                         normalizar_resposta(conteudo, 'candidatos'))

    def test_normalizacao_blocos_mistral(self):
        self.assertEqual({'candidatos': []}, normalizar_resposta(
            [SimpleNamespace(text='{"candidatos":'), {'text': ' []}'}], 'candidatos'))

    def test_agencia_mesmo_formato_interno_e_schema(self):
        for nome in ('mistral', 'groq'):
            p, _, gerar, modelo = self.provider(nome, conteudo='{"stories": []}', schema=editorial._RespostaAgencia)
            gerar.side_effect = None
            resultado = p.analisar(modelo, 'prompt agência')
            self.assertEqual([], resultado['historias'])
            self.assertEqual(0.5, gerar.call_args.kwargs['temperature'])
            formato = gerar.call_args.kwargs['response_format']
            self.assertIn('historias', formato['json_schema']['schema']['properties'])

    def test_erros_e_logs_nao_revelam_chave(self):
        chave = 'chave-ficticia-123456789'
        logs = io.StringIO()
        handler = logging.StreamHandler(logs)
        logger = logging.getLogger()
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)
        for nome in ('mistral', 'groq'):
            p, _, gerar, modelo = self.provider(nome)
            gerar.side_effect = FalhaSDK(401, chave)
            try:
                p.analisar(modelo, 'p')
            except ErroProvider:
                texto = traceback.format_exc()
                self.assertNotIn(chave, texto)
                logger.error(texto)
        self.assertNotIn(chave, logs.getvalue())

    def test_erros_compativeis_com_handlers_legados(self):
        self.assertIs(ErroProvider, ErroGemini)

    def test_wrappers_nunca_leem_chave_gemini_para_outros_providers(self):
        with patch.object(analise_ia, 'obter_chave_gemini', side_effect=AssertionError('chave errada')), \
             patch.object(editorial, 'obter_chave_gemini', side_effect=AssertionError('chave errada')), \
             patch('modules.ai.factory.obter_chave', return_value='fake'):
            for nome in ('mistral', 'groq'):
                config = ConfigIA(provider_ativo=nome)
                self.assertEqual(nome, analise_ia.criar_provedor_configurado(config).provider)
                self.assertEqual(nome, editorial.criar_provedor_agencia(config).provider)

    def test_loaders_usam_sdk_oficial_sem_rede(self):
        # Exercita o import oficial mesmo quando os SDKs ainda não estão instalados.
        cliente_m = ModuleType('mistralai.client')
        cliente_m.Mistral = Mock()
        cliente_g = ModuleType('groq')
        cliente_g.Groq = Mock()
        with patch.dict(sys.modules, {'mistralai.client': cliente_m, 'groq': cliente_g}):
            self.mistral_loader(api_key='fake', timeout_ms=360000, retry_config=None)
            self.groq_loader(api_key='fake', timeout=360, max_retries=0)
            cliente_m.Mistral.assert_called_once_with(api_key='fake', timeout_ms=360000, retry_config=None, debug_logger=ANY)
            self.assertTrue(cliente_m.Mistral.call_args.kwargs['debug_logger'].disabled)
            cliente_g.Groq.assert_called_once_with(api_key='fake', timeout=360, max_retries=0)

    def test_fluxo_analise_usa_provider_ativo_e_fallback_groq(self):
        from test_agencia_editorial import episodio_longo, remix_em
        item = remix_em(1)
        item['titulos'] = ['Plano infalível', 'Deu tudo errado', 'A pior ideia']
        with tempfile.TemporaryDirectory() as pasta, \
             patch.object(analise_ia, 'ANALISES_DIR', Path(pasta) / 'analises'), \
             patch.object(analise_ia, 'DIAGNOSTICOS_DIR', Path(pasta) / 'diagnosticos'), \
             patch.object(editorial, 'ANALISES_AGENCIA_DIR', Path(pasta) / 'agencia'), \
             patch('modules.intro.INTROS_DIR', Path(pasta) / 'intros'), \
             patch('modules.ai.factory.obter_chave', return_value='fake'):
            for nome in ('mistral', 'groq'):
                modelos = ['mistral-small-latest'] if nome == 'mistral' else ['openai/gpt-oss-20b']
                _, _, gerar, _ = self.provider(nome, modelos=modelos, conteudo=json.dumps({'historias': [item]}))
                config = ConfigIA(provider_ativo=nome, max_chamadas_episodio=1)
                resultados = analise_ia.analisar_transcricao(episodio_longo('normal-universal'), config, usar_cache=False)
                self.assertEqual(1, len(resultados))
                self.assertEqual('PLANO INFALÍVEL', resultados[0].titulos[0])
                self.assertEqual(modelos[0], gerar.call_args.kwargs['model'])

    def test_fluxo_agencia_preserva_remix_com_novos_providers(self):
        from test_agencia_editorial import episodio_longo, remix_em
        with tempfile.TemporaryDirectory() as pasta, \
             patch.object(editorial, 'ANALISES_AGENCIA_DIR', Path(pasta) / 'agencia'), \
             patch('modules.intro.INTROS_DIR', Path(pasta) / 'intros'), \
             patch('modules.remix_narrativo.REMIX_CACHE_DIR', Path(pasta) / 'remixes'), \
             patch('modules.ai.factory.obter_chave', return_value='fake'):
            for nome in ('mistral', 'groq'):
                _, _, gerar, modelo = self.provider(nome, conteudo=json.dumps({'historias': [remix_em(1)]}))
                config = ConfigIA(provider_ativo=nome, max_chamadas_episodio=1)
                resultados = editorial.analisar_agencia(episodio_longo('teste-' + nome), config, usar_cache=False)
                self.assertEqual(1, len(resultados))
                self.assertTrue(resultados[0].usar_remix)
                self.assertEqual(modelo, gerar.call_args.kwargs['model'])


class TestConfigCredenciais(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        patch('socket.socket.connect', side_effect=AssertionError('Rede proibida nos testes')).start()
        patch('socket.socket.connect_ex', side_effect=AssertionError('Rede proibida nos testes')).start()
        patch.dict(os.environ, {nome: '' for nome in credenciais.VARIAVEIS.values()}).start()

    def test_config_antiga_e_padrao_gemini(self):
        self.assertEqual('gemini', ConfigIA().provider_ativo)
        with tempfile.TemporaryDirectory() as pasta:
            path = Path(pasta) / 'config.json'
            path.write_text(json.dumps({'modelo': 'gemini-legado', 'provider': 'gemini'}), encoding='utf-8')
            with patch.object(config_ia, 'CONFIG_IA_PATH', path):
                config = config_ia.carregar_config_ia()
                self.assertEqual('gemini-legado', config.modelo_gemini)
                self.assertEqual('gemini-legado', config.modelo)

    def test_modelos_independentes_e_aliases_mutaveis(self):
        config = ConfigIA(modelo='gemini-editado')
        config.provider_ativo = 'groq'
        self.assertEqual('openai/gpt-oss-120b', config.modelo)
        config.modelo = 'groq-editado'
        config.provider = 'mistral'
        self.assertEqual('mistral-small-latest', config.modelo)
        config.provider = 'gemini'
        self.assertEqual('gemini-editado', config.modelo)
        self.assertEqual('groq-editado', config.modelo_groq)

    def test_persistencia_tres_modelos_sem_segredos(self):
        with tempfile.TemporaryDirectory() as pasta:
            path = Path(pasta) / 'config.json'
            config = ConfigIA(provider_ativo='mistral', modelo_gemini='g', modelo_mistral='m', modelo_groq='q')
            with patch.object(config_ia, 'CONFIG_IA_PATH', path):
                config_ia.salvar_config_ia(config)
                self.assertEqual(config, config_ia.carregar_config_ia())
                self.assertEqual('m', config_ia.carregar_config_ia().modelo)
            self.assertNotIn('API_KEY', path.read_text(encoding='utf-8'))

    def test_variaveis_ambiente_nao_migradas_para_cofre(self):
        for nome, variavel in credenciais.VARIAVEIS.items():
            with patch.dict(os.environ, {variavel: 'fake-secret'}), \
                 patch('modules.credenciais.keyring.set_password') as salvar, \
                 patch('modules.credenciais.keyring.get_password', return_value=None) as obter:
                self.assertEqual('fake-secret', credenciais.obter_chave(nome))
                self.assertEqual('fake-secret', credenciais.obter_chave(variavel))
                salvar.assert_not_called()
                if nome == 'groq':
                    obter.assert_called_with(credenciais.SERVICO, credenciais.CONTA_GROQ)
                else:
                    obter.assert_not_called()

    def test_cofre_preservado_e_contas_independentes(self):
        for nome, conta in credenciais.CONTAS.items():
            with patch('modules.credenciais.keyring.get_password', return_value='fake') as obter:
                self.assertEqual('fake', credenciais.obter_chave(nome))
                obter.assert_called_once_with(credenciais.SERVICO, conta)
            with patch('modules.credenciais.keyring.set_password') as salvar:
                credenciais.salvar_chave(nome, 'fake-secret-123456789012345')
                salvar.assert_called_once_with(credenciais.SERVICO, conta, 'fake-secret-123456789012345')

    def test_erros_cofre_nao_revelam_segredo(self):
        with patch('modules.credenciais.keyring.get_password', side_effect=Exception('segredo')):
            with self.assertRaises(credenciais.ErroCredencial) as ctx:
                credenciais.obter_chave('mistral')
            self.assertNotIn('segredo', str(ctx.exception))

    def test_cache_gemini_preservado_novos_isolados(self):
        transcricao = SimpleNamespace(video_id='episodio')
        configs = [ConfigIA(modelo='mesmo-modelo', provider_ativo=p) for p in ('gemini', 'mistral', 'groq')]
        esperado = 'episodio|mesmo-modelo|4|360.0|30.0|' + analise_ia.VERSAO_PROMPT
        self.assertEqual(hashlib.sha256(esperado.encode()).hexdigest() + '.json',
                         analise_ia._cache_path(transcricao, configs[0]).name)
        self.assertEqual(3, len({analise_ia._cache_path(transcricao, c) for c in configs}))
        with patch.object(editorial, '_hash_transcricao', return_value='hash'):
            self.assertEqual(3, len({editorial.caminho_cache_agencia(transcricao, c) for c in configs}))
        self.assertEqual([], analise_ia._caches_historicos(transcricao, configs[1]))


if __name__ == '__main__':
    unittest.main()
