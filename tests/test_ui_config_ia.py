"""Tela construída com widgets simulados; nenhuma janela ou API real é aberta."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from modules.ai.errors import ErroProvider
from modules.config_ia import ConfigIA
from ui import tela_config_ia as ui


class Widget:
    def __init__(self, *args, **kwargs):
        self.opcoes = dict(kwargs)
        self.valor = ""
        self.bindings = {}

    def configure(self, **kwargs):
        self.opcoes.update(kwargs)

    def grid(self, **kwargs):
        self.grid_opcoes = kwargs

    def grid_columnconfigure(self, *args, **kwargs):
        pass

    def get(self):
        return self.valor

    def set(self, valor):
        self.valor = valor

    def select(self):
        self.valor = 1

    def deselect(self):
        self.valor = 0

    def insert(self, index, valor):
        self.valor = str(valor)

    def delete(self, *args):
        self.valor = ""

    def bind(self, evento, callback):
        self.bindings[evento] = callback


class ThreadSincrona:
    def __init__(self, target, args, daemon):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


class TestTelaConfigIA(unittest.TestCase):
    def setUp(self):
        self.patches = []
        self.patch('socket.socket.connect', side_effect=AssertionError('Rede bloqueada nos testes'))
        self.patch('socket.socket.connect_ex', side_effect=AssertionError('Rede bloqueada nos testes'))
        base = ui.TelaConfigIA.__mro__[1]
        for nome in ('__init__', 'grid_columnconfigure', 'grid_rowconfigure', 'after'):
            self.patch_obj(base, nome, return_value=None)
        self.patch_obj(base, 'winfo_exists', return_value=True)
        for nome in ('CTkFrame', 'CTkScrollableFrame', 'CTkLabel', 'CTkEntry', 'CTkComboBox', 'CTkButton', 'CTkFont', 'CTkCheckBox'):
            self.patch_obj(ui.ctk, nome, new=Widget)
        self.config = ConfigIA()
        self.carregar = self.patch_obj(ui, 'carregar_config_ia', return_value=self.config)
        self.salvar_config = self.patch_obj(ui, 'salvar_config_ia')
        self.chaves = {p: None for p in ui.PROVIDERS}
        self.obter_chave = self.patch_obj(ui, 'obter_chave', side_effect=lambda p: self.chaves[p])
        self.salvar_chave = self.patch_obj(ui, 'salvar_chave')
        self.patch_obj(ui, 'diagnosticar_chave_groq', return_value={
            'encontrada': False, 'comprimento': 0, 'prefixo_mascarado': '—', 'origem': 'outro'})
        self.factory = self.patch_obj(ui, 'obter_provider')
        self.provider = self.factory.return_value
        self.provider.testar_conexao.return_value = {'ok': True, 'latency_ms': 340}
        self.provider.listar_modelos.return_value = ['modelo-conta-a', 'modelo-conta-b']
        self.patch_obj(ui.threading, 'Thread', new=ThreadSincrona)
        self.app = Mock()
        self.tela = ui.TelaConfigIA(None, self.app)

    def patch(self, alvo, **kwargs):
        p = patch(alvo, **kwargs)
        self.patches.append(p)
        self.addCleanup(p.stop)
        return p.start()

    def patch_obj(self, alvo, nome, **kwargs):
        p = patch.object(alvo, nome, **kwargs)
        self.patches.append(p)
        self.addCleanup(p.stop)
        return p.start()

    def processar(self):
        self.tela._processar_eventos()

    def status(self):
        return self.tela.lbl_status.opcoes['text']

    def test_abrir_sem_chave_sem_chamadas_automaticas(self):
        self.assertEqual('gemini', self.tela.config.provider_ativo)
        self.assertEqual('Gemini', self.tela.combo_provider.get())
        self.assertIn('Credencial: não configurada', self.status())
        self.factory.assert_not_called()
        self.provider.testar_conexao.assert_not_called()
        self.provider.listar_modelos.assert_not_called()
        self.assertEqual({'gemini', 'mistral', 'groq'}, set(self.tela.entries_chaves))
        for entry in self.tela.entries_chaves.values():
            self.assertEqual('', entry.get())
            self.assertEqual('•', entry.opcoes['show'])

    def test_fallback_padrao_desativado_e_ordem(self):
        self.assertFalse(self.tela.check_fallback.get())
        self.assertFalse(self.tela.check_auth.get())
        self.assertIn('Mistral → Groq → Gemini', self.tela.lbl_ordem.opcoes['text'])
        self.factory.assert_not_called()

    def test_salvar_fallback_e_preferencia_sem_api(self):
        self.tela.check_fallback.select()
        self.tela.combo_preferido.set('Groq')
        self.tela._salvar_config()
        cfg = self.salvar_config.call_args.args[0]
        self.assertTrue(cfg.fallback_automatico)
        self.assertFalse(cfg.fallback_autenticacao)
        self.assertEqual('groq', cfg.provider_preferido)
        self.assertIn('Groq → Mistral → Gemini', self.tela.lbl_ordem.opcoes['text'])
        self.factory.assert_not_called()

    def test_ultima_analise_sem_segredo(self):
        with patch.object(ui, 'ultima_analise', return_value='Mistral -> 429 -> Groq OK'):
            self.processar()
        self.assertIn('Mistral -> 429 -> Groq OK', self.tela.lbl_analise.opcoes['text'])
        self.factory.assert_not_called()

    def test_cofre_indisponivel_ao_abrir_nao_cria_cliente(self):
        self.obter_chave.side_effect = RuntimeError('segredo-na-excecao')
        tela = ui.TelaConfigIA(None, self.app)
        self.assertIn('cofre indisponível', tela.lbl_status.opcoes['text'])
        self.assertNotIn('segredo', tela.lbl_status.opcoes['text'])
        self.factory.assert_not_called()

    def test_diagnostico_groq_mostra_somente_metadados(self):
        with patch.object(ui, 'diagnosticar_chave_groq', return_value={
                'encontrada': True, 'comprimento': 56, 'prefixo_mascarado': 'gsk_***1234',
                'origem': 'Gerenciador de Credenciais'}):
            self.tela._trocar_provider('Groq')
        self.assertIn('Credencial encontrada: sim | comprimento: 56', self.status())
        self.assertIn('Prefixo mascarado: gsk_***1234 | origem: Gerenciador de Credenciais', self.status())
        self.factory.assert_not_called()

    def test_chave_existente_nunca_e_preenchida(self):
        self.chaves['groq'] = 'segredo-existente'
        tela = ui.TelaConfigIA(None, self.app)
        tela._trocar_provider('Groq')
        self.assertEqual('', tela.entries_chaves['groq'].get())
        self.assertIn('Credencial: configurada', tela.lbl_status.opcoes['text'])
        self.assertNotIn('segredo-existente', tela.lbl_status.opcoes['text'])
        self.factory.assert_not_called()

    def test_salvar_cada_chave_limpa_campo_sem_expor_conteudo(self):
        for nome in ui.PROVIDERS:
            segredo = 'segredo-longo-para-teste-' + nome
            self.tela.entries_chaves[nome].insert(0, segredo)
            self.tela._salvar_chave(nome)
            self.salvar_chave.assert_called_with(nome, segredo)
            self.assertEqual('', self.tela.entries_chaves[nome].get())
            self.assertNotIn(segredo, self.tela.labels_credenciais[nome].opcoes['text'])
            self.assertNotIn(segredo, self.status())
        self.factory.assert_not_called()

    def test_erro_salvar_chave_nao_expoe_excecao(self):
        self.salvar_chave.side_effect = RuntimeError('segredo-super-longo')
        self.tela.entries_chaves['gemini'].insert(0, 'segredo-super-longo')
        self.tela._salvar_chave('gemini')
        self.assertNotIn('segredo-super-longo', str(self.app.mostrar_erro.call_args))
        self.factory.assert_not_called()

    def test_digitar_chave_nao_valida_automaticamente(self):
        self.tela.entries_chaves['mistral'].insert(0, 'x')
        self.assertEqual({}, self.tela.entries_chaves['mistral'].bindings)
        self.salvar_chave.assert_not_called()
        self.factory.assert_not_called()

    def test_trocar_provider_preserva_modelos_e_credenciais(self):
        self.tela.combos_modelos['gemini'].set('gemini-editado')
        self.tela.entries_chaves['gemini'].insert(0, 'chave-pendente')
        self.tela._trocar_provider('Groq')
        self.assertEqual('groq', self.config.provider_ativo)
        self.assertEqual('openai/gpt-oss-120b', self.tela.combo_modelo.get())
        self.assertIs(self.tela.entry_chave, self.tela.entries_chaves['groq'])
        self.tela._trocar_provider('Gemini')
        self.assertEqual('gemini-editado', self.config.modelo)
        self.assertEqual('chave-pendente', self.tela.entries_chaves['gemini'].get())
        self.factory.assert_not_called()

    def test_trocar_modelo_preserva_outros_sem_rede(self):
        self.tela.combos_modelos['mistral'].set('mistral-editado')
        self.tela._modelo_alterado('mistral')
        self.assertEqual('mistral-editado', self.config.modelo_mistral)
        self.assertEqual('gemini-3.5-flash-lite', self.config.modelo_gemini)
        self.factory.assert_not_called()

    def test_salvar_preferencias_preserva_tres_modelos(self):
        for provider in ui.PROVIDERS:
            self.tela.combos_modelos[provider].set(provider + '-escolhido')
        self.tela._trocar_provider('Groq')
        self.tela._salvar_config()
        salvo = self.salvar_config.call_args.args[0]
        self.assertEqual('groq', salvo.provider_ativo)
        self.assertEqual('gemini-escolhido', salvo.modelo_gemini)
        self.assertEqual('mistral-escolhido', salvo.modelo_mistral)
        self.assertEqual('groq-escolhido', salvo.modelo_groq)
        self.factory.assert_not_called()

    def test_testar_sem_chave_mostra_mensagem_sem_criar_cliente(self):
        self.tela._testar()
        self.assertIn('salve a chave', self.app.mostrar_erro.call_args.args[1])
        self.factory.assert_not_called()
        self.assertFalse(self.tela._ocupada)

    def test_teste_sucesso_provider_modelo_e_latencia(self):
        self.chaves['groq'] = 'fake'
        self.tela._trocar_provider('Groq')
        with patch.object(ui, 'perf_counter', side_effect=[10.0, 10.340]):
            self.tela._testar()
        self.factory.assert_called_once_with('groq')
        self.provider.testar_conexao.assert_called_once_with(modelo='openai/gpt-oss-120b')
        self.provider.listar_modelos.assert_not_called()
        self.assertTrue(self.tela._ocupada)
        self.processar()
        self.assertIn('Provider atual: Groq', self.status())
        self.assertIn('Modelo: openai/gpt-oss-120b', self.status())
        self.assertIn('OK — 340 ms', self.status())
        self.assertEqual('normal', self.tela.btn_testar.opcoes['state'])

    def verificar_falha(self, erro, trecho):
        self.chaves['gemini'] = 'fake'
        self.provider.testar_conexao.side_effect = erro
        self.tela._testar()
        self.processar()
        self.assertIn('FALHA', self.status())
        self.assertIn(trecho, self.tela.lbl_mensagem.opcoes['text'])
        self.assertNotIn('segredo', self.tela.lbl_mensagem.opcoes['text'])
        self.provider.testar_conexao.assert_called_once()

    def test_erro_401(self):
        self.verificar_falha(ErroProvider('segredo', codigo=401), 'Chave inválida')

    def test_erro_403(self):
        self.verificar_falha(ErroProvider('segredo', codigo=403), 'sem permissão')

    def test_erro_429(self):
        self.verificar_falha(ErroProvider('segredo', codigo=429), 'Limite de chamadas')

    def test_timeout(self):
        self.verificar_falha(TimeoutError('segredo'), 'tempo limite')

    def test_modelo_indisponivel(self):
        self.verificar_falha(ErroProvider('segredo', codigo=404), 'Modelo indisponível')

    def test_falha_rede_sanitizada(self):
        self.verificar_falha(ConnectionError('segredo'), 'Verifique a rede')

    def test_atualizar_modelos_so_provider_ativo_sem_teste(self):
        self.chaves['mistral'] = 'fake'
        self.tela._trocar_provider('Mistral')
        self.tela._atualizar_modelos()
        self.processar()
        self.factory.assert_called_once_with('mistral')
        self.provider.listar_modelos.assert_called_once()
        self.provider.testar_conexao.assert_not_called()
        self.assertEqual('mistral-small-latest', self.tela.combos_modelos['mistral'].get())
        self.assertIn('modelo-conta-a', self.tela.combos_modelos['mistral'].opcoes['values'])
        self.assertEqual(['gemini-3.5-flash-lite'], self.tela._modelos_locais['gemini'])

    def test_listagem_negada_preserva_lista_local_e_modelo(self):
        self.chaves['gemini'] = 'fake'
        self.provider.listar_modelos.side_effect = ErroProvider('segredo', codigo=403)
        antes = list(self.tela._modelos_locais['gemini'])
        self.tela._atualizar_modelos()
        self.processar()
        self.assertEqual(antes, self.tela._modelos_locais['gemini'])
        self.assertIn('Lista local/configurada mantida', self.tela.lbl_mensagem.opcoes['text'])
        self.assertNotIn('segredo', self.tela.lbl_mensagem.opcoes['text'])

    def test_listagem_vazia_preserva_configuracao(self):
        self.chaves['gemini'] = 'fake'
        self.provider.listar_modelos.return_value = []
        self.tela._atualizar_modelos()
        self.processar()
        self.assertEqual('gemini-3.5-flash-lite', self.config.modelo_gemini)
        self.assertIn('Lista local/configurada mantida', self.tela.lbl_mensagem.opcoes['text'])

    def test_troca_provider_durante_teste_nao_atribui_resultado_errado(self):
        self.chaves['gemini'] = 'fake'
        self.tela._testar()
        self.tela._trocar_provider('Groq')
        self.processar()
        self.assertIn('Provider atual: Groq', self.status())
        self.assertIn('ainda não realizado', self.status())
        self.tela._trocar_provider('Gemini')
        self.assertIn('Último teste: OK', self.status())

    def test_troca_modelo_invalida_status_teste_anterior(self):
        self.chaves['gemini'] = 'fake'
        self.tela._testar()
        self.processar()
        self.tela.combos_modelos['gemini'].set('outro-modelo')
        self.tela._modelo_alterado('gemini')
        self.assertIn('ainda não realizado', self.status())
        self.provider.testar_conexao.assert_called_once()

    def test_salvar_chave_durante_teste_descarta_resultado_antigo(self):
        self.chaves['gemini'] = 'fake'
        self.tela._testar()
        self.tela.entries_chaves['gemini'].insert(0, 'nova-chave-segura')
        self.tela._salvar_chave('gemini')
        self.processar()
        self.assertNotIn('gemini', self.tela._ultimos_testes)
        self.assertIn('ainda não realizado', self.status())

    def test_clique_duplicado_nao_dispara_outra_chamada(self):
        self.chaves['gemini'] = 'fake'
        self.tela._testar()
        self.tela._testar()
        self.provider.testar_conexao.assert_called_once()

    def test_config_antiga_abre_com_gemini(self):
        from modules import config_ia
        with tempfile.TemporaryDirectory() as pasta:
            path = Path(pasta) / 'config.json'
            path.write_text(json.dumps({'modelo': 'gemini-antigo'}), encoding='utf-8')
            with patch.object(config_ia, 'CONFIG_IA_PATH', path):
                self.carregar.return_value = config_ia.carregar_config_ia()
                tela = ui.TelaConfigIA(None, self.app)
        self.assertEqual('Gemini', tela.combo_provider.get())
        self.assertEqual('gemini-antigo', tela.combos_modelos['gemini'].get())
        self.factory.assert_not_called()


if __name__ == '__main__':
    unittest.main()
