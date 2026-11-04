"""Short real-window production smoke; expensive operations and network isolated."""
import tempfile
import time
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from test_titulos_v2 import historia
from modules.agencia.titulos_manual import aprovar_titulo
from modules.preset import caminhos_validos
from modules import transcricao_v2 as V
from ui.app import AppIAToons
from ui import tela_importar as I, tela_transcricao as T, tela_agencia as A


class TestProducaoSanity(TestCase):
    def test_s07e11_reviews_filters_manual_approval_and_generation(self):
        from modules.transcricao import carregar_transcricao
        from modules.agencia.producao import recuperar_producao
        transcricao = carregar_transcricao(Path('dados/transcricoes/db972adc2049a2c9dfa68ff69302490cb013ee26e166a14f10082942eff11ea4.json'))
        oportunidades = recuperar_producao([],transcricao)
        errors = []
        with tempfile.TemporaryDirectory() as tmp, \
             patch('socket.socket.connect',side_effect=AssertionError('API proibida')), \
             patch('socket.socket.connect_ex',side_effect=AssertionError('API proibida')), \
             patch('modules.tasks.DADOS_DIR',Path(tmp)), \
             patch.object(A,'carregar_lista_agencia',return_value=oportunidades), \
             patch.object(A,'registrar_uso'),patch.object(A.TelaAgencia,'_salvar'), \
             patch.object(A,'exportar_corte_agencia',return_value={'sucesso':True,
                 'caminho_saida':str(Path(tmp)/'mock.mp4'),'relatorio':{}}) as render:
            app = AppIAToons()
            try:
                app.report_callback_exception = lambda *args: errors.append(str(args))
                app.mostrar_erro = lambda *args: errors.append(str(args))
                app.mostrar_aviso = lambda *args: errors.append(str(args))
                app.estado.update(video_path=transcricao.video_path,transcricao=transcricao,pasta_export=tmp)
                app.navegar('agencia')
                app.update()
                screen = app._frame_tela
                self.assertEqual(5,len(screen.planos))
                self.assertIn('REVISÃO: 5',screen.lbl_contagem.cget('text'))
                p = screen.planos[-1]
                entrada = screen._linhas[p.id][1]
                entrada.delete(0,'end')
                entrada.insert(0,'Brian recusa a queima de livros proposta por Meg')
                screen._aprovar_corte(p.id)
                self.assertIn('PRONTOS: 1',screen.lbl_contagem.cget('text'))
                screen.filtro.set('Prontos')
                screen._filtrar()
                app.update()
                self.assertTrue(screen._cards[p.id].winfo_ismapped())
                pendente = next(outro for outro in screen.planos if outro.id!=p.id)
                self.assertFalse(screen._cards[pendente.id].winfo_ismapped())
                screen._linhas[pendente.id][0].set(True)
                screen._gerar()
                self.wait(app,lambda:screen._concluidos == 1 and screen._tarefa_atual is None)
                self.assertEqual(1,render.call_count)
                self.assertEqual(p,render.call_args.args[1])
                self.assertEqual(5,len(screen.planos))
                self.assertFalse(errors,errors)
            finally:
                for callback in app.tk.splitlist(app.tk.call('after','info')):
                    app.tk.call('after','cancel',callback)
                app.destroy()

    def test_interface_production_flow(self):
        video = Path(r"C:\Users\Bruna\Downloads\Telegram Desktop\Family.Guy.S07E11.mp4")
        self.assertTrue(video.is_file())
        self.assertTrue(caminhos_validos()[0])
        plano, transcricao = historia()
        transcricao.video_path = str(video)
        aprovar_titulo(plano, "Peter disputa o dinheiro do carro com Lois")
        plano.selecionado = True
        errors = []
        with tempfile.TemporaryDirectory() as tmp, \
             patch("socket.socket.connect", side_effect=AssertionError("Network prohibited")), \
             patch("socket.socket.connect_ex", side_effect=AssertionError("Network prohibited")), \
             patch("modules.tasks.DADOS_DIR", Path(tmp)), \
             patch.object(T, "buscar_transcricao_cache", return_value=None), \
             patch.object(A, "carregar_lista_agencia", return_value=[]), \
             patch.object(A, "registrar_uso"), \
             patch.object(A.TelaAgencia, "_salvar"), \
             patch.object(A, "provider_orcado", return_value=None), \
             patch.object(A, "executar_analise_com_titulos", return_value=[plano]) as analysis, \
             patch.object(A, "exportar_corte_agencia", return_value={
                 "sucesso": True, "caminho_saida": str(Path(tmp) / "mock.mp4"), "relatorio": {}}) as render:
            app = AppIAToons()
            try:
                app.report_callback_exception = lambda *args: errors.append(str(args))
                app.mostrar_erro = lambda *args: errors.append(str(args))
                app.mostrar_aviso = lambda *args: errors.append(str(args))
                app.update()
                with patch.object(I.filedialog, "askopenfilename", return_value=str(video)):
                    app._frame_tela._selecionar_video()
                self.assertEqual(str(video), app.estado["video_path"])
                app.estado["pasta_export"] = tmp
                app.navegar("transcrever")
                screen = app._frame_tela
                self.assertEqual("Preciso", screen.combo_modo.get())
                self.assertEqual(V.ConfigTranscricao(), V.escolher_configuracao("auto", "auto_local"))
                self.assertEqual(V.ConfigTranscricao(), V.escolher_configuracao("auto", "auto_hibrido"))
                with patch.object(T, "transcrever_video", return_value=transcricao) as transcribe:
                    screen._iniciar_transcricao()
                    self.wait(app, lambda: app.estado.get("transcricao") is transcricao)
                    self.assertEqual("preciso", transcribe.call_args.kwargs["modo"])
                app.navegar("agencia")
                screen = app._frame_tela
                screen._analisar(True)
                self.wait(app, lambda: len(screen.planos) == 1)
                self.assertEqual(1, analysis.call_count)
                screen._gerar()
                self.wait(app, lambda: screen._concluidos == 1 and screen._tarefa_atual is None)
                self.assertEqual(1, render.call_count)
                self.assertEqual("conclu\u00eddo", plano.status)
                app.navegar("exportar")
                app.update()
                self.assertEqual("exportar", app._aba_atual)
                self.assertFalse(errors, errors)
            finally:
                for callback in app.tk.splitlist(app.tk.call('after','info')):
                    app.tk.call('after','cancel',callback)
                app.destroy()

    def wait(self, app, predicate):
        deadline = time.monotonic() + 10
        while not predicate() and time.monotonic() < deadline:
            app.update()
            time.sleep(0.02)
        self.assertTrue(predicate(), "Production UI operation did not complete")
