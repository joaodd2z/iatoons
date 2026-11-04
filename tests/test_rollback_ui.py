"""Real Tk cards from the existing EP12 cache; network and rendering disabled."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ui.app import AppIAToons
from modules.agencia import persistencia
from tools.validar_rollback_2809 import executar


class TestRollbackUI(unittest.TestCase):
    def test_cached_stories_are_visible_unique_and_can_queue_generation(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch('socket.socket.connect', side_effect=AssertionError('No API')), \
             patch('socket.socket.connect_ex', side_effect=AssertionError('No API')), \
             patch('modules.tasks.DADOS_DIR', Path(tmp)), \
             patch('modules.agencia.persistencia.LISTAS_AGENCIA_DIR', Path(tmp)):
            planos, t, _ = executar()
            persistencia.salvar_lista_agencia(t.video_path, t.video_id, planos)
            app = AppIAToons()
            errors = []
            app.report_callback_exception = lambda *a: errors.append(str(a))
            app.mostrar_erro = lambda *a: errors.append(str(a))
            app.mostrar_aviso = lambda *a: errors.append(str(a))
            try:
                app.estado.update(video_path=t.video_path, transcricao=t, pasta_export=tmp)
                app.navegar('agencia')
                app.update()
                tela = app._frame_tela
                self.assertEqual(4, len(tela.planos))
                self.assertEqual(4, len(tela._cards))
                self.assertTrue(all(c.winfo_ismapped() for c in tela._cards.values()))
                self.assertEqual(4, len({p.story_identity for p in tela.planos}))
                duplicate = deepcopy(tela.planos[0])
                duplicate.id += '_second_hook'
                self.assertEqual(4, len(tela._somente_remix(tela.planos+[duplicate], t)))
                for p in tela.planos:
                    tela._linhas[p.id][0].set(True)
                with patch.object(tela, '_proximo') as proximo:
                    tela._gerar()
                    self.assertTrue(proximo.called)
                    self.assertEqual(4, len(tela._pendentes))
                self.assertFalse(errors, errors)
            finally:
                for callback in app.tk.splitlist(app.tk.call('after', 'info')):
                    app.tk.call('after', 'cancel', callback)
                app.destroy()
