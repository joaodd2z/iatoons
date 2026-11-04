"""Real Agency window using S07E11 cache, not injected backend/list results."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from modules.agencia import persistencia
from modules.agencia.editorial import ListaPlanos
from modules.agencia.recuperacao import DiagnosticoRemix
from modules.agencia.producao import REVISAO
from modules.transcricao import carregar_transcricao
from modules.sessao import caminho_normalizado
from ui.app import AppIAToons
from ui import tela_agencia as A

CACHE = ROOT / 'dados/transcricoes/db972adc2049a2c9dfa68ff69302490cb013ee26e166a14f10082942eff11ea4.json'


class TestAgenciaUIIntegracao(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.t = carregar_transcricao(CACHE)
        self.errors = []
        for alvo,valor in [('modules.agencia.persistencia.LISTAS_AGENCIA_DIR',Path(self.tmp.name)),
                           ('modules.tasks.DADOS_DIR',Path(self.tmp.name))]:
            p = patch(alvo,valor)
            p.start()
            self.addCleanup(p.stop)
        for alvo in ('socket.socket.connect','socket.socket.connect_ex'):
            p = patch(alvo,side_effect=AssertionError('Nenhuma API real'))
            p.start()
            self.addCleanup(p.stop)
        persistencia.salvar_lista_agencia(self.t.video_path,self.t.video_id,[])
        self.app = AppIAToons()
        self.addCleanup(self.fechar)
        self.app.report_callback_exception = lambda *args: self.errors.append(str(args))
        self.app.mostrar_aviso = lambda *args: self.errors.append(str(args))
        self.app.mostrar_erro = lambda *args: self.errors.append(str(args))
        self.app.estado.update(video_path=self.t.video_path,transcricao=self.t,pasta_export=self.tmp.name)
        self.app.navegar('agencia')
        self.app.update()
        self.tela = self.app._frame_tela

    def fechar(self):
        for callback in self.app.tk.splitlist(self.app.tk.call('after','info')):
            self.app.tk.call('after','cancel',callback)
        self.app.destroy()

    def test_empty_saved_list_restores_five_real_cards_without_injected_result(self):
        self.assertEqual(5,len(self.tela.planos))
        self.assertEqual(5,len(self.tela._cards))
        self.assertTrue(all(c.winfo_ismapped() for c in self.tela._cards.values()))
        self.assertEqual('0 prontos · 5 para revisão',self.tela.lbl_status.cget('text'))
        self.assertIn('PRONTOS: 0 · REVISÃO: 5',self.tela.lbl_contagem.cget('text'))
        for p in self.tela.planos:
            self.assertTrue(p.gancho and p.segmentos)
            self.assertTrue(p.revisao_humana.get('motivos') or p.validacao_remix.get('remix_valido'))
            self.assertFalse(self.tela._linhas[p.id][0].get())
            self.assertIn('REVIS',self.tela._linhas[p.id][3].cget('text').upper())
            self.assertTrue(self.tela._estrutura_remix(p))
        canvas = self.tela.lista._parent_canvas
        for card in self.tela._cards.values():
            canvas.yview_moveto(card.winfo_y()/max(1,self.tela.lista.winfo_height()))
            self.app.update()
            topo,fim = canvas.winfo_rooty(),canvas.winfo_rooty()+canvas.winfo_height()
            self.assertLess(card.winfo_rooty(),fim)
            self.assertGreater(card.winfo_rooty()+card.winfo_height(),topo)
        canvas.yview_moveto(0)
        self.assertEqual(5,len(persistencia.carregar_lista_agencia(self.t.video_path,self.t.video_id)))
        self.assertFalse(self.errors,self.errors)

    def test_review_status_alone_is_not_filtered_through_auto_validator(self):
        p = deepcopy(self.tela.planos[0])
        p.estado_editorial = 'automatico'
        p.status = REVISAO
        with patch.object(A,'verificar_plano_remix',side_effect=AssertionError('Nao filtrar revisao por score')):
            self.assertEqual([p],self.tela._somente_remix([p],self.t))

    def test_ep12_real_auto_recovery_cards_ready_first(self):
        from tools.verificar_auto_recovery import executar
        from modules.agencia.titulos_manual import selecionar_prontos
        lista,t,relatorio = executar('S07E12',cache_titulos=True)
        self.app.estado.update(video_path=t.video_path,transcricao=t)
        self.tela._eventos.put(('ia_fim',self.app.estado['video_session'],
                               caminho_normalizado(t.video_path),lista,len(lista)))
        limite = time.time()+5
        while time.time()<limite and len(self.tela.planos)!=len(lista):
            self.app.update()
            time.sleep(.02)
        self.assertEqual(6,len(self.tela._cards))
        self.assertTrue(all(c.winfo_ismapped() for c in self.tela._cards.values()))
        self.assertIn('PRONTOS: 1 · REVISÃO: 5',self.tela.lbl_contagem.cget('text'))
        self.assertTrue(self.tela._pronto_visual(self.tela.planos[0]))
        self.assertTrue(self.tela._linhas[self.tela.planos[0].id][0].get())
        prontos,_ = selecionar_prontos(self.tela.planos,t)
        self.assertEqual(1,len(prontos))
        self.assertFalse(self.errors,self.errors)

    def test_completion_ignores_stale_zero_diagnostic_and_ready_only_filter(self):
        planos = ListaPlanos(deepcopy(self.tela.planos),diagnostico=DiagnosticoRemix())
        self.tela.filtro.set('Prontos')
        self.tela._filtrar()
        self.tela._eventos.put(('ia_fim',self.app.estado['video_session'],
                               caminho_normalizado(self.t.video_path),planos,5))
        self.tela._processar_eventos()
        self.app.update()
        self.assertEqual('Todos',self.tela.filtro.get())
        self.assertEqual('0 prontos · 5 para revisão',self.tela.lbl_status.cget('text'))
        self.assertTrue(all(c.winfo_ismapped() for c in self.tela._cards.values()))

    def test_manual_approval_immediately_updates_card_and_queues_only_confirmed(self):
        p = self.tela.planos[-1]
        entrada = self.tela._linhas[p.id][1]
        entrada.delete(0,'end')
        entrada.insert(0,'Brian recusa a queima de livros proposta por Meg')
        self.tela._aprovar_corte(p.id)
        self.app.update()
        self.assertTrue(p.corte_aprovado_manualmente)
        self.assertEqual('APROVADO_MANUALMENTE',p.status)
        self.assertEqual('✓ APROVADO MANUALMENTE',self.tela._linhas[p.id][3].cget('text'))
        self.assertTrue(self.tela._linhas[p.id][0].get())
        self.tela._linhas[self.tela.planos[0].id][0].set(True)
        with patch.object(A,'registrar_uso'),patch.object(A,'exportar_corte_agencia',return_value={
                'sucesso':True,'caminho_saida':str(Path(self.tmp.name)/'mock.mp4'),'relatorio':{}}) as exportar:
            self.tela._gerar()
            fim = time.monotonic()+6
            while self.tela._tarefa_atual is not None and time.monotonic() < fim:
                self.app.update()
                time.sleep(.02)
            self.assertIsNone(self.tela._tarefa_atual)
            self.assertEqual(1,exportar.call_count)
            self.assertEqual(p.id,exportar.call_args.args[1].id)
        self.assertFalse(self.errors,self.errors)


if __name__ == '__main__':
    if '--show-s07e11' in sys.argv:
        t = carregar_transcricao(CACHE)
        app = AppIAToons()
        app.estado.update(video_path=t.video_path,transcricao=t)
        app.navegar('agencia')
        app.title('IA TOONS CUT - S07E11 - revisao')
        app.mainloop()
    else:
        unittest.main()
