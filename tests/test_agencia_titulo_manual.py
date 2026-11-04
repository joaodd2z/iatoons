"""Agency manual approval with isolated storage, fake renderer and blocked network."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest

import test_titulos_v2 as fixtures
from modules import titulos_v2 as V
from modules.agencia import pipeline as P
from modules.agencia import titulos_manual as M
from modules.agencia.editorial import PlanoAgencia
from modules.agencia.qualidade import ItemGate


class TestTituloManual(unittest.TestCase):
    def setUp(self):
        fixtures.TestTitulosV2.setUp(self)
        self.p.titulos = []
        self.p.titulo_escolhido = ""
        self.p.titulos_v2 = {"status_titulo": V.STATUS_REVISAO_TITULO,
                             "avaliados": [{"texto": "Titulo antigo", "score_titulo": 0}]}
        V.marcar_revisao_titulo(self.p)

    def exportar_fake(self):
        pasta = Path(self.tmp.name)
        perfil = SimpleNamespace(id="teste", nome_canal="teste")
        prep = SimpleNamespace(
            timeline=SimpleNamespace(duracao=30, fps=30, tipo="remix"),
            gate=[ItemGate("TESTE", True, "ok")], correcoes=[], info=Mock(),
            enquadramento=SimpleNamespace(tipo_fonte="teste", estrategia="teste", fracao_visivel=1),
            titulo=Mock(), legendas=[], aplicar_tratamento=False)
        with patch.object(P, "RENDER_DIR", pasta / "render"), \
             patch.object(P, "FRAMES_DIR", pasta / "frames"), \
             patch.object(P, "obter_perfil", return_value=perfil), \
             patch.object(P, "preparar_corte", return_value=prep), \
             patch.object(P, "construir_comando", return_value=[]), \
             patch.object(P, "executar_ffmpeg", return_value={"sucesso": True}), \
             patch.object(P, "validar_frames", return_value=([ItemGate("FRAME", True, "ok")], [])), \
             patch.object(P, "relatorio_exportacao", return_value={}), \
             patch.object(P, "validar_exportacao", return_value=[]), \
             patch.object(P.shutil, "move"), \
             patch.object(P, "montar_metricas", return_value={}), \
             patch.object(P, "salvar_metricas", return_value=pasta / "metricas.json"):
            return P.exportar_corte_agencia("episodio.mp4", self.p, self.t, pasta,
                                            exportar_srt=False, gerar_capa=False)

    def test_review_manual_approval_exports_exact_trimmed_text(self):
        M.editar_titulo(self.p, "  Meu titulo: escolha editorial  ")
        M.aprovar_titulo(self.p, "  Meu titulo: escolha editorial  ")
        with patch.object(V, "validar_titulo_final", side_effect=AssertionError("Nao validar override")):
            resultado = self.exportar_fake()
        self.assertTrue(resultado["sucesso"])
        self.assertEqual("Meu titulo: escolha editorial", self.p.titulo)
        self.assertEqual("manual", self.p.titulo_origem)
        self.assertTrue(self.p.titulo_aprovado_manualmente)

    def test_empty_is_blocked(self):
        self.verificar_invalido("  ")

    def test_sem_titulo_is_blocked(self):
        self.verificar_invalido(" SEM TÍTULO ")

    def test_revisao_necessaria_is_blocked(self):
        self.verificar_invalido(" REVISÃO NECESSÁRIA ")

    def verificar_invalido(self, texto):
        with self.assertRaises(ValueError):
            M.aprovar_titulo(self.p, texto)
        self.assertFalse(self.p.titulo_aprovado_manualmente)
        with patch.object(P, "executar_ffmpeg", side_effect=AssertionError("Render proibido")):
            resultado = P.exportar_corte_agencia("episodio.mp4", self.p, self.t, self.tmp.name)
        self.assertTrue(resultado["revisao_titulo"])

    def test_manual_ignores_old_score_and_keeps_remix(self):
        validacao = deepcopy(self.p.validacao_remix)
        M.aprovar_titulo(self.p, "Minha decisao editorial")
        with patch.object(V, "avaliar_titulo", side_effect=AssertionError("Nao usar score")), \
             patch.object(V, "gerar_titulos_v2", side_effect=AssertionError("Nao chamar IA")):
            M.validar_para_exportacao(self.p, self.t)
        self.assertEqual(0, self.p.titulos_v2["avaliados"][0]["score_titulo"])
        self.assertEqual(validacao, self.p.validacao_remix)
        self.assertNotEqual(V.STATUS_REVISAO_TITULO, self.p.status)
        self.assertIn("APROVADO MANUALMENTE", M.status_visual(self.p))

    def test_edit_after_approval_requires_confirmation(self):
        M.aprovar_titulo(self.p, "Titulo confirmado")
        M.editar_titulo(self.p, "Titulo modificado")
        self.assertFalse(self.p.titulo_aprovado_manualmente)
        with self.assertRaises(ValueError):
            M.validar_para_exportacao(self.p, self.t)
        M.aprovar_titulo(self.p, "Titulo modificado")
        M.validar_para_exportacao(self.p, self.t)

    def test_direct_change_cannot_reuse_confirmation(self):
        M.aprovar_titulo(self.p, "Titulo confirmado")
        self.p.titulo_escolhido = "Outra edicao"
        with self.assertRaises(ValueError):
            M.validar_para_exportacao(self.p, self.t)

    def test_pending_cut_does_not_block_ready_cut(self):
        pendente = deepcopy(self.p)
        pendente.id = "pendente"
        pendente.selecionado = True
        M.aprovar_titulo(self.p, "Titulo pronto")
        with patch.object(V, "validar_titulo_final", side_effect=AssertionError("Nao validar manual")):
            prontos, revisao = M.selecionar_prontos([pendente, self.p], self.t)
        self.assertEqual([self.p], prontos)
        self.assertEqual([pendente], revisao)
        self.assertTrue(self.p.selecionado)

    def test_manual_confirmation_survives_save_and_restore(self):
        M.aprovar_titulo(self.p, "Titulo salvo")
        salvo = PlanoAgencia.de_dict(self.p.como_dict())
        with patch.object(V, "reavaliar_existentes", side_effect=AssertionError("Nao sobrescrever manual")):
            M.reavaliar_salvos([salvo], self.t)
        self.assertTrue(M.manual_confirmado(salvo))
        self.assertEqual("Titulo salvo", salvo.titulo)

    def test_unconfirmed_edit_survives_restore_as_pending(self):
        M.aprovar_titulo(self.p, "Titulo salvo")
        M.editar_titulo(self.p, "Edicao nao confirmada")
        salvo = PlanoAgencia.de_dict(self.p.como_dict())
        M.reavaliar_salvos([salvo], self.t)
        with self.assertRaises(ValueError):
            M.validar_para_exportacao(salvo, self.t)
        self.assertEqual("Edicao nao confirmada", salvo.titulo)

    def test_automatic_titles_still_require_same_validator(self):
        self.p.titulo_escolhido = fixtures.propostas()[0]["texto"]
        M.validar_para_exportacao(self.p, self.t)
        self.assertTrue(self.p.titulo_auto_aprovado)
        self.p.titulo_escolhido = "ELE NAO ESPERAVA POR ISSO"
        with self.assertRaises(ValueError):
            M.validar_para_exportacao(self.p, self.t)

    def test_manual_text_is_not_replaced_by_layout_alternative(self):
        self.p.titulos = fixtures.historia()[0].titulos
        M.aprovar_titulo(self.p, "Texto manual exato")
        perfil = SimpleNamespace(estilo_titulo_agencia=Mock(), zonas=SimpleNamespace(titulo=Mock()))
        with patch.object(P, "ajustar_texto", return_value=SimpleNamespace(cabe=False)) as ajustar:
            P._ajustar_titulo_com_correcao(self.p, perfil, [])
        self.assertEqual(2, ajustar.call_count)
        self.assertEqual("Texto manual exato", self.p.titulo)

    def test_ui_approval_and_sync_preserve_exact_case_and_spaces(self):
        from ui.tela_agencia import TelaAgencia
        entrada = Mock()
        entrada.get.return_value = "  Meu titulo  com espacos  "
        selecao = Mock()
        selecao.get.return_value = True
        capa = Mock()
        capa.get.return_value = "capa"
        tela = SimpleNamespace(planos=[self.p], app=Mock(), _salvar=Mock(),
                               _atualizar_status=Mock(),
                               _linhas={self.p.id: (selecao, entrada, capa, Mock())})
        TelaAgencia._titulo_editado(tela, self.p.id)
        self.assertTrue(self.p.titulo_confirmacao_pendente)
        TelaAgencia._aprovar_titulo(tela, self.p.id)
        TelaAgencia._sincronizar(tela)
        self.assertTrue(M.manual_confirmado(self.p))
        self.assertEqual("Meu titulo  com espacos", self.p.titulo)
        entrada.get.return_value = "Outro titulo"
        TelaAgencia._titulo_editado(tela, self.p.id)
        self.assertFalse(M.manual_confirmado(self.p))

    def test_generate_selected_ui_queues_only_ready_cut(self):
        from ui import tela_agencia as UI
        pendente = deepcopy(self.p)
        pendente.id = "pendente"
        pendente.selecionado = True
        M.aprovar_titulo(self.p, "Pronto para gerar")
        tela = SimpleNamespace(
            planos=[pendente, self.p], _pre_requisitos=Mock(return_value=("episodio.mp4", self.t)),
            _sincronizar=Mock(), _opcoes=Mock(), _ajustes=Mock(), _intro=Mock(),
            app=SimpleNamespace(estado={}, mostrar_aviso=Mock(), mostrar_erro=Mock()),
            btn_gerar=Mock(), btn_cancelar=Mock(), _proximo=Mock(), _redesenhar=Mock())
        with patch.object(UI, "fila_render", SimpleNamespace(ocupada=False)):
            UI.TelaAgencia._gerar(tela)
        self.assertEqual([self.p], tela._pendentes)
        self.assertEqual([pendente.id], tela._revisao)
        tela._proximo.assert_called_once()
        tela.app.mostrar_aviso.assert_not_called()


if __name__ == "__main__":
    unittest.main()
