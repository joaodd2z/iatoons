import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch


PROJETO_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJETO_DIR))

from modules.importacao import (  # noqa: E402
    hhmmss_para_segundos,
    normalizar_tempos,
    segundos_para_hhmmss,
)
from modules.renderer import extrair_thumbnail_composto, renderizar_corte  # noqa: E402
from modules.tasks import FilaTarefas, TarefaRenderizacao  # noqa: E402
from modules.temporarios import PREFIXO_PREVIEW, limpar_previews  # noqa: E402
from ui.tela_corte import TelaCorte  # noqa: E402


class TestTempos(unittest.TestCase):
    def test_milissegundos_sao_preservados(self):
        valor = 3723.456
        texto = segundos_para_hhmmss(valor)
        self.assertEqual(texto, "01:02:03.456")
        self.assertAlmostEqual(hhmmss_para_segundos(texto), valor, places=3)

    def test_tempos_fora_do_video_sao_normalizados(self):
        self.assertEqual(normalizar_tempos(-2.0, 12.0, 10.0), (0.0, 10.0))

    def test_intervalos_invalidos_sao_rejeitados(self):
        for inicio, fim in ((5, 5), (9, 3), (12, 15)):
            with self.subTest(inicio=inicio, fim=fim):
                with self.assertRaises(ValueError):
                    normalizar_tempos(inicio, fim, 10.0)


class TestFila(unittest.TestCase):
    def test_excecao_libera_fila_e_inicia_proxima_tarefa(self):
        fila = FilaTarefas()
        segunda_executada = threading.Event()
        erro_recebido = threading.Event()

        def falhar(**_kwargs):
            raise RuntimeError("falha controlada")

        def concluir(**_kwargs):
            segunda_executada.set()
            return {"sucesso": True}

        fila.adicionar(TarefaRenderizacao("falha", falhar, callback_erro=lambda _e: erro_recebido.set()))
        fila.adicionar(TarefaRenderizacao("seguinte", concluir))

        self.assertTrue(erro_recebido.wait(2))
        self.assertTrue(segunda_executada.wait(2))
        limite = time.time() + 2
        while fila.ocupada and time.time() < limite:
            time.sleep(0.01)
        self.assertFalse(fila.ocupada)


class TestRenderer(unittest.TestCase):
    def test_preview_e_exportacao_usam_mesma_quebra_de_titulo(self):
        titulo = "UM TITULO GRANDE PARA QUEBRAR EM MAIS DE UMA LINHA"
        comando_preview = []

        class Resultado:
            returncode = 0
            stderr = ""

        with patch("modules.renderer.subprocess.run", side_effect=lambda cmd, **_kw: comando_preview.extend(cmd) or Resultado()):
            self.assertTrue(extrair_thumbnail_composto("entrada.mp4", 1.0, titulo, "saida.jpg"))

        filtro_preview = comando_preview[comando_preview.index("-filter_complex") + 1]
        self.assertGreaterEqual(filtro_preview.count("drawtext="), 2)

    def test_ffmpeg_combina_e_consumo_fluxos_continuamente(self):
        argumentos_popen = {}

        class Processo:
            stdout = iter(["frame=1\n", "erro controlado\n"])
            returncode = 1

            def wait(self):
                return self.returncode

            def kill(self):
                self.returncode = -9

        def criar_processo(_cmd, **kwargs):
            argumentos_popen.update(kwargs)
            return Processo()

        with patch("modules.renderer.subprocess.Popen", side_effect=criar_processo):
            resultado = renderizar_corte("entrada.mp4", 0, 1, "TITULO", "saida.mp4", fps=30)

        self.assertIs(argumentos_popen["stderr"], subprocess.STDOUT)
        self.assertFalse(resultado["sucesso"])
        self.assertIn("erro controlado", resultado["erro"])


class TestPreviewAssincrona(unittest.TestCase):
    def test_disparo_da_previa_nao_espera_ffmpeg(self):
        class Campo:
            def __init__(self, valor):
                self.valor = valor

            def get(self):
                return self.valor

        class Label:
            def configure(self, **_kwargs):
                pass

        class App:
            estado = {"video_path": "entrada.mp4"}

        tela = TelaCorte.__new__(TelaCorte)
        tela.entry_inicio = Campo("00:00:00.250")
        tela.entry_titulo = Campo("TITULO")
        tela.video_info = type("Video", (), {"duracao": 10.0})()
        tela.lbl_preview = Label()
        tela.app = App()
        tela.temp_dir = PROJETO_DIR / "temp"
        tela._preview_geracao = 0
        tela._preview_resultados = __import__("queue").Queue()
        tela._preview_ativa = threading.Event()
        tela._preview_ativa.set()

        terminou = threading.Event()

        def ffmpeg_lento(*_args, **_kwargs):
            time.sleep(0.25)
            terminou.set()
            return False

        inicio = time.perf_counter()
        with patch("ui.tela_corte.extrair_thumbnail_composto", side_effect=ffmpeg_lento):
            tela._atualizar_preview()
            decorrido = time.perf_counter() - inicio
            self.assertLess(decorrido, 0.1)
            self.assertTrue(terminou.wait(1))


class TestTemporarios(unittest.TestCase):
    def test_limpeza_remove_somente_previews_do_aplicativo(self):
        pasta = PROJETO_DIR / "temp" / "test_runtime"
        pasta.mkdir(parents=True, exist_ok=True)
        proprio = pasta / f"{PREFIXO_PREVIEW}teste.jpg"
        alheio = pasta / "arquivo_usuario.jpg"
        proprio.write_bytes(b"teste")
        alheio.write_bytes(b"teste")
        try:
            self.assertEqual(limpar_previews(pasta), 1)
            self.assertFalse(proprio.exists())
            self.assertTrue(alheio.exists())
        finally:
            proprio.unlink(missing_ok=True)
            alheio.unlink(missing_ok=True)
            try:
                pasta.rmdir()
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
