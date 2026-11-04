import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch


PROJETO_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJETO_DIR))

from modules.cortes import Corte, sugerir_cortes  # noqa: E402
from modules.legendas import gerar_blocos_legenda, recortar_legendas, salvar_srt  # noqa: E402
from modules.lote import carregar_lista, caminho_sem_sobrescrever, salvar_lista  # noqa: E402
from modules.tasks import FilaTarefas, TarefaRenderizacao  # noqa: E402
from modules.transcricao import (  # noqa: E402
    PalavraTranscrita, SegmentoTranscricao, Transcricao,
)


def transcricao_sintetica(duracao=260.0):
    segmentos = []
    tempo = 0.0
    indice = 1
    while tempo + 8 <= duracao:
        fim = tempo + 8
        texto = f"Esta é a frase número {indice}."
        palavras = [
            PalavraTranscrita(tempo, tempo + 1, " Esta", 1.0),
            PalavraTranscrita(tempo + 1, fim, f" é a frase {indice}.", 1.0),
        ]
        segmentos.append(SegmentoTranscricao(tempo, fim, texto, palavras))
        tempo = fim + 1.0  # pausa clara entre falas
        indice += 1
    return Transcricao("video.mp4", "id", "small", "pt", "pt", 1.0,
                       duracao, "agora", segmentos)


class TestLegendas(unittest.TestCase):
    def test_blocos_e_deslocamento_para_inicio_do_corte(self):
        transcricao = transcricao_sintetica(30)
        blocos = gerar_blocos_legenda(transcricao, max_palavras=3)
        recortados = recortar_legendas(blocos, 9.0, 20.0)
        self.assertTrue(recortados)
        self.assertEqual(recortados[0].inicio, 0.0)
        self.assertTrue(all(0 <= b.inicio < b.fim <= 11.0 for b in recortados))

    def test_srt_usa_milisegundos_e_utf8(self):
        pasta = PROJETO_DIR / "temp" / "test_runtime_lote"
        caminho = pasta / "legenda.srt"
        try:
            salvar_srt(recortar_legendas(
                gerar_blocos_legenda(transcricao_sintetica(20), 5), 0.125, 10.0
            ), caminho)
            texto = caminho.read_text(encoding="utf-8-sig")
            self.assertIn("00:00:00,000", texto)
            self.assertIn("-->", texto)
        finally:
            caminho.unlink(missing_ok=True)
            try:
                pasta.rmdir()
            except OSError:
                pass


class TestSugestoes(unittest.TestCase):
    def test_cortes_ficam_entre_60_e_120_sem_sobreposicao(self):
        cortes = sugerir_cortes(transcricao_sintetica(), max_cortes=3)
        self.assertEqual(len(cortes), 3)
        self.assertTrue(all(60 <= c.duracao <= 120 for c in cortes))
        self.assertTrue(all(a.fim <= b.inicio for a, b in zip(cortes, cortes[1:])))
        limites = {s.inicio for s in transcricao_sintetica().segmentos}
        limites.update(s.fim for s in transcricao_sintetica().segmentos)
        self.assertTrue(all(c.inicio in limites and c.fim in limites for c in cortes))

    def test_respeita_quantidade_maxima(self):
        self.assertLessEqual(len(sugerir_cortes(transcricao_sintetica(), max_cortes=2)), 2)


class TestPersistenciaLote(unittest.TestCase):
    def setUp(self):
        self.pasta = PROJETO_DIR / "temp" / "test_runtime_lote"
        self.pasta.mkdir(parents=True, exist_ok=True)
        self.video = self.pasta / "video.mp4"
        self.video.write_bytes(b"video")

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

    def test_salva_reabre_lista(self):
        with patch("modules.lote.LOTES_DIR", self.pasta / "listas"):
            original = [Corte(1.25, 70.5, "TÍTULO")]
            salvar_lista(str(self.video), original)
            restaurada = carregar_lista(str(self.video))
        self.assertEqual(restaurada[0].id, original[0].id)
        self.assertEqual(restaurada[0].inicio, 1.25)

    def test_nome_nunca_sobrescreve(self):
        primeiro = caminho_sem_sobrescrever(self.pasta, "Meu corte", ".mp4")
        primeiro.write_bytes(b"existente")
        segundo = caminho_sem_sobrescrever(self.pasta, "Meu corte", ".mp4")
        self.assertNotEqual(primeiro, segundo)
        self.assertEqual(segundo.name, "Meu_corte_2.mp4")


class TestFilaSequencial(unittest.TestCase):
    def test_nunca_executa_duas_tarefas_ao_mesmo_tempo(self):
        fila = FilaTarefas()
        lock = threading.Lock()
        simultaneas = 0
        maximo = 0
        concluidas = threading.Event()
        total = 0

        def executar(**_kwargs):
            nonlocal simultaneas, maximo
            with lock:
                simultaneas += 1
                maximo = max(maximo, simultaneas)
            time.sleep(0.04)
            with lock:
                simultaneas -= 1
            return {"sucesso": True}

        def terminou(_resultado):
            nonlocal total
            with lock:
                total += 1
                if total == 3:
                    concluidas.set()

        for indice in range(3):
            fila.adicionar(TarefaRenderizacao(str(indice), executar, callback_conclusao=terminou))
        self.assertTrue(concluidas.wait(2))
        self.assertEqual(maximo, 1)


if __name__ == "__main__":
    unittest.main()
