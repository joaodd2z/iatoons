import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


PROJETO_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJETO_DIR))

from modules import transcricao as T  # noqa: E402
from modules.cortes import Corte  # noqa: E402
from modules.lote import carregar_lista, salvar_lista  # noqa: E402
from modules.sessao import iniciar_sessao_video, sessao_corresponde  # noqa: E402
from modules.tasks import FilaTarefas, TarefaRenderizacao  # noqa: E402


class TestTrocaEpisodio(unittest.TestCase):
    def setUp(self):
        self.pasta = PROJETO_DIR / "temp" / "test_troca_episodio"
        self.pasta.mkdir(parents=True, exist_ok=True)
        self.video_a = self.pasta / "episodio_a.mp4"
        self.video_b = self.pasta / "episodio_b.mp4"
        self.video_a.write_bytes(b"episodio A")
        self.video_b.write_bytes(b"episodio B diferente")

    def tearDown(self):
        for item in sorted(self.pasta.rglob("*"), reverse=True):
            if item.is_file():
                item.unlink(missing_ok=True)
            elif item.is_dir():
                try:
                    item.rmdir()
                except OSError:
                    pass
        try:
            self.pasta.rmdir()
        except OSError:
            pass

    def test_nova_sessao_limpa_todo_estado_derivado(self):
        estado = {
            "video_session": 1, "video_path": str(self.video_a),
            "video_info": object(), "inicio": 12, "fim": 80,
            "titulo": "Título A", "transcricao": object(),
            "intro_intervalo": (1, 30), "pasta_export": "destino",
        }
        info_b = SimpleNamespace(duracao=321.5)
        iniciar_sessao_video(estado, str(self.video_b), info_b)
        self.assertEqual(estado["video_session"], 2)
        self.assertEqual(estado["video_path"], str(self.video_b.resolve()))
        self.assertEqual((estado["inicio"], estado["fim"]), (0.0, 321.5))
        self.assertEqual(estado["titulo"], "")
        self.assertIsNone(estado["transcricao"])
        self.assertIsNone(estado["intro_intervalo"])
        self.assertEqual(estado["pasta_export"], "destino")

    def test_resultado_atrasado_do_episodio_a_e_rejeitado(self):
        estado = {"video_session": 1, "video_path": str(self.video_a)}
        sessao_a = estado["video_session"]
        iniciar_sessao_video(estado, str(self.video_b), SimpleNamespace(duracao=10))
        self.assertFalse(sessao_corresponde(estado, sessao_a, str(self.video_a)))
        self.assertTrue(sessao_corresponde(estado, 2, str(self.video_b)))

    def test_transcricoes_de_arquivos_diferentes_nao_compartilham_cache(self):
        with patch.object(T, "TRANSCRICOES_DIR", self.pasta / "transcricoes"):
            cache_a = T.caminho_cache_transcricao(str(self.video_a), idioma="pt")
            cache_b = T.caminho_cache_transcricao(str(self.video_b), idioma="pt")
        self.assertNotEqual(cache_a, cache_b)

    def test_listas_de_cortes_de_arquivos_diferentes_sao_isoladas(self):
        with patch("modules.lote.LOTES_DIR", self.pasta / "listas"):
            salvar_lista(str(self.video_a), [Corte(1, 61, "Cena A")])
            salvar_lista(str(self.video_b), [Corte(2, 72, "Cena B")])
            self.assertEqual(carregar_lista(str(self.video_a))[0].titulo, "Cena A")
            self.assertEqual(carregar_lista(str(self.video_b))[0].titulo, "Cena B")

    def test_troca_durante_tarefa_cancela_sem_publicar_resultado(self):
        fila = FilaTarefas()
        iniciou = threading.Event()
        terminou = threading.Event()
        publicados = []

        def trabalho(cancelada_ref=None, **_kwargs):
            iniciou.set()
            while not cancelada_ref.cancelada:
                time.sleep(0.005)
            return {"sucesso": False, "erro": "Tarefa cancelada."}

        tarefa = TarefaRenderizacao(
            "episodio A", trabalho,
            callback_conclusao=lambda resultado: (publicados.append(resultado), terminou.set()),
        )
        fila.adicionar(tarefa)
        self.assertTrue(iniciou.wait(1))
        fila.cancelar_tudo()
        self.assertTrue(terminou.wait(1))
        self.assertFalse(publicados[0]["sucesso"])
        self.assertFalse(fila.ocupada)


if __name__ == "__main__":
    unittest.main()
