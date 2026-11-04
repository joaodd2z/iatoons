"""Widgets e threads simulados: nenhuma janela, vídeo ou API real."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_ui_config_ia import Widget, ThreadSincrona
from test_analise_ia import transcricao_teste
from modules.ai.comparacao import episode_identity
from ui import tela_comparar_ias as ui


class WidgetComparacao(Widget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.filhos = []
        if args and isinstance(args[0], WidgetComparacao):
            args[0].filhos.append(self)

    def winfo_children(self):
        return self.filhos

    def destroy(self):
        pass


class TestTelaCompararIAs(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        for alvo in ('socket.socket.connect', 'socket.socket.connect_ex'):
            patch(alvo, side_effect=AssertionError('Rede bloqueada')).start()
        base = ui.TelaCompararIAs.__mro__[1]
        for nome in ('__init__', 'grid_columnconfigure', 'grid_rowconfigure', 'after'):
            patch.object(base, nome, return_value=None).start()
        patch.object(base, 'winfo_exists', return_value=True).start()
        for nome in ('CTkScrollableFrame', 'CTkFrame', 'CTkLabel', 'CTkCheckBox', 'CTkComboBox', 'CTkButton', 'CTkFont'):
            patch.object(ui.ctk, nome, new=WidgetComparacao).start()
        self.factory = patch.object(ui, 'ComparacaoIA').start()
        self.comparacao = self.factory.return_value
        self.comparacao.chamadas_previstas = 2
        self.comparacao.orcamento.restantes = 4
        self.comparacao.caches_disponiveis = ['gemini']
        self.comparacao.executar.return_value = {'resultados': {}, 'chamadas_utilizadas': 2, 'cenas_compartilhadas': []}
        self.confirmar = patch.object(ui.messagebox, 'askyesno', return_value=True).start()
        patch.object(ui.threading, 'Thread', new=ThreadSincrona).start()
        patch('modules.tasks.fila_analise_ia', Mock(ocupada=False)).start()
        t = transcricao_teste()
        self.app = Mock(estado={'transcricao': t, 'video_path': t.video_path})
        self.tela = ui.TelaCompararIAs(None, self.app)

    def test_abertura_e_trocas_nao_chamam_api(self):
        self.factory.assert_not_called()
        self.comparacao.executar.assert_not_called()
        self.confirmar.assert_not_called()
        self.tela.combo_modo.set('Modo Agência / Remix')
        self.tela.checks['mistral'].select()
        self.comparacao.executar.assert_not_called()

    def test_verificar_cache_e_local(self):
        self.tela._verificar()
        self.assertIn('2 chamadas', self.tela.lbl_status.opcoes['text'])
        self.assertIn('Gemini', self.tela.lbl_status.opcoes['text'])
        self.comparacao.executar.assert_not_called()

    def test_cancelar_confirmacao_nao_executa(self):
        self.confirmar.return_value = False
        self.tela._comparar()
        self.comparacao.executar.assert_not_called()
        self.assertNotIn('comparacao_ocupada', self.app.estado)

    def test_confirmar_mostra_custo_e_executa_uma_vez(self):
        self.tela._comparar()
        self.assertIn('2 chamadas', self.confirmar.call_args.args[1])
        self.comparacao.executar.assert_called_once()
        self.assertIs(True, self.comparacao.executar.call_args.kwargs['confirmado'])
        self.tela._processar()
        self.assertIsNotNone(self.tela.relatorio)
        self.assertFalse(self.app.estado['comparacao_ocupada'])

    def test_limite_impede_ate_confirmacao(self):
        self.comparacao.orcamento.restantes = 1
        self.tela._comparar()
        self.confirmar.assert_not_called()
        self.comparacao.executar.assert_not_called()
        self.app.mostrar_erro.assert_called_once()

    def test_carregar_resultado_normal_no_lote_sem_renderizar(self):
        from ui.tela_lote import TelaLote
        tela = object.__new__(TelaLote)
        tela.app = self.app
        tela._listas_modo = {}
        tela._redesenhar_lista = Mock()
        tela.lbl_status = Mock()
        self.app.estado['resultado_comparacao'] = {
            'episode_identity': episode_identity(self.app.estado['transcricao']),
            'modo': 'normal', 'provider': 'groq', 'itens': [{
                'inicio': 0, 'fim': 60, 'titulos': ['Um título'], 'descricao': 'Descrição',
                'motivo': 'Motivo', 'inicio_original': 0, 'gancho': 'Gancho'}]}
        tela._receber_comparacao()
        self.assertEqual('Um título', tela.cortes[0].titulo)
        tela._redesenhar_lista.assert_called_once()
        self.assertNotIn('resultado_comparacao', self.app.estado)

    def test_carregar_resultado_agencia_sem_renderizar(self):
        from ui.tela_agencia import TelaAgencia
        from modules.agencia.editorial import PlanoAgencia, TituloOpcao
        tela = object.__new__(TelaAgencia)
        tela.app = self.app
        tela._somente_remix = Mock(side_effect=lambda planos, t: [p for p in planos if p.usar_remix])
        tela._redesenhar = Mock()
        tela.lbl_status = Mock()
        p = PlanoAgencia(inicio=0, fim=60, titulos=[TituloOpcao('Um título', 'curiosidade')], usar_remix=True)
        self.app.estado['resultado_comparacao'] = {
            'episode_identity': episode_identity(self.app.estado['transcricao']),
            'modo': 'agencia', 'provider': 'gemini', 'itens': [p.como_dict()]}
        tela._receber_comparacao()
        self.assertEqual(1, len(tela.planos))
        self.assertTrue(tela.planos[0].usar_remix)
        tela._redesenhar.assert_called_once()


if __name__ == '__main__':
    unittest.main()
