import json
import os
import unittest
from unittest.mock import patch

from modules import credenciais as c
from modules.ai.factory import obter_provider
from modules.ai.groq import GroqProvider


class TestCredenciaisGroq(unittest.TestCase):
    def setUp(self):
        for alvo in ('socket.socket.connect', 'socket.socket.connect_ex'):
            p = patch(alvo, side_effect=AssertionError('Rede bloqueada'))
            p.start()
            self.addCleanup(p.stop)
        p = patch.dict(os.environ, {'GROQ_API_KEY': ''})
        p.start()
        self.addCleanup(p.stop)
        self.chave = 'gsk_chave_ficticia_somente_testes_1234'

    def test_identificadores_salvam_e_leem_mesma_conta(self):
        for nome in ('groq', 'GROQ_API_KEY', 'groq_api_key'):
            with patch.object(c.keyring, 'set_password') as salvar, \
                 patch.object(c.keyring, 'get_password', return_value=self.chave) as ler:
                c.salvar_chave(nome, self.chave)
                salvar.assert_called_once_with('IA_TOONS_CUT', 'groq_api_key', self.chave)
                self.assertEqual(self.chave, c.obter_chave(nome))
                ler.assert_called_once_with('IA_TOONS_CUT', 'groq_api_key')

    def test_chave_nova_salva_prevalece_sobre_env_antiga(self):
        with patch.dict(os.environ, {'GROQ_API_KEY': 'gsk_antiga_invalida_5678'}), \
             patch.object(c.keyring, 'get_password', return_value=self.chave):
            self.assertEqual(self.chave, c.obter_chave('groq'))
            self.assertEqual('Gerenciador de Credenciais', c.diagnosticar_chave_groq()['origem'])

    def test_env_somente_quando_cofre_vazio_sem_migracao(self):
        with patch.dict(os.environ, {'GROQ_API_KEY': self.chave}), \
             patch.object(c.keyring, 'get_password', return_value=None), \
             patch.object(c.keyring, 'set_password') as salvar:
            self.assertEqual(self.chave, c.obter_chave('groq'))
            self.assertEqual('env', c.diagnosticar_chave_groq()['origem'])
            salvar.assert_not_called()

    def test_salvamento_remove_espacos_externos_e_aspas(self):
        for valor in ('  ' + self.chave + '\r\n', '"' + self.chave + '"', "'" + self.chave + "'"):
            with patch.object(c.keyring, 'set_password') as salvar:
                c.salvar_chave('groq', valor)
                self.assertEqual(self.chave, salvar.call_args.args[2])

    def test_leitura_normaliza_credencial_historica_sem_regravar(self):
        with patch.object(c.keyring, 'get_password', return_value=' "' + self.chave + '" '), \
             patch.object(c.keyring, 'set_password') as salvar:
            self.assertEqual(self.chave, c.obter_chave('groq'))
            salvar.assert_not_called()

    def test_caracteres_extras_rejeitados_sem_expor_chave(self):
        for valor in (self.chave + ' extra', self.chave + '\x00', self.chave + '\u200b', '"' + self.chave):
            with self.assertRaises(ValueError) as ctx:
                c.normalizar_chave_groq(valor)
            self.assertNotIn(self.chave, str(ctx.exception))

    def test_factory_sdk_recebe_exatamente_chave_salva(self):
        cofre = {}
        with patch.object(c.keyring, 'set_password', side_effect=lambda servico, conta, chave: cofre.update({(servico, conta): chave})), \
             patch.object(c.keyring, 'get_password', side_effect=lambda servico, conta: cofre.get((servico, conta))), \
             patch('modules.ai.groq._criar_cliente') as sdk, \
             patch.dict(os.environ, {'GROQ_API_KEY': 'gsk_antiga_invalida_5678'}):
            c.salvar_chave('groq', ' "' + self.chave + '" ')
            obter_provider('groq')
            self.assertEqual(cofre[('IA_TOONS_CUT', 'groq_api_key')], sdk.call_args.kwargs['api_key'])
            self.assertEqual(self.chave, sdk.call_args.kwargs['api_key'])
            sdk.return_value.models.list.assert_not_called()
            sdk.return_value.chat.completions.create.assert_not_called()

    def test_provider_chave_explicita_normalizada(self):
        with patch('modules.ai.groq._criar_cliente') as sdk:
            GroqProvider(' "' + self.chave + '" ')
            self.assertEqual(self.chave, sdk.call_args.kwargs['api_key'])

    def test_diagnostico_so_metadados_seguros(self):
        with patch.object(c.keyring, 'get_password', return_value=self.chave):
            info = c.diagnosticar_chave_groq()
        self.assertEqual({'encontrada', 'comprimento', 'prefixo_mascarado', 'origem'}, set(info))
        self.assertTrue(info['encontrada'])
        self.assertEqual(len(self.chave), info['comprimento'])
        self.assertEqual('gsk_***1234', info['prefixo_mascarado'])
        self.assertNotIn(self.chave, json.dumps(info))

    def test_diagnostico_chave_ausente(self):
        with patch.object(c.keyring, 'get_password', return_value=None):
            self.assertEqual({'encontrada': False, 'comprimento': 0,
                'prefixo_mascarado': '—', 'origem': 'outro'}, c.diagnosticar_chave_groq())

    def test_diagnostico_nunca_mostra_chave_curta_completa(self):
        with patch.object(c.keyring, 'get_password', return_value='gsk_1234'):
            self.assertEqual('***', c.diagnosticar_chave_groq()['prefixo_mascarado'])

    def test_nao_le_contas_antigas_alternativas(self):
        with patch.object(c.keyring, 'get_password', return_value=None) as ler:
            self.assertIsNone(c.obter_chave('groq'))
            ler.assert_called_once_with('IA_TOONS_CUT', 'groq_api_key')

    def test_falha_cofre_nao_revela_excecao(self):
        with patch.object(c.keyring, 'get_password', side_effect=RuntimeError(self.chave)):
            with self.assertRaises(c.ErroCredencial) as ctx:
                c.obter_chave('groq')
            self.assertNotIn(self.chave, str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
