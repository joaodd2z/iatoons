import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


PROJETO_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJETO_DIR))

from modules import transcricao as T  # noqa: E402


class ModeloFalso:
    def __init__(self):
        self.chamadas = 0

    def transcribe(self, _caminho, **_opcoes):
        self.chamadas += 1
        palavras = [
            SimpleNamespace(start=0.125, end=0.500, word=" Olá", probability=0.98),
            SimpleNamespace(start=0.500, end=1.250, word=" mundo", probability=0.97),
        ]
        segmentos = iter([
            SimpleNamespace(start=0.125, end=1.250, text=" Olá mundo ", words=palavras),
            SimpleNamespace(start=1.500, end=2.750, text=" Segundo trecho ", words=[]),
        ])
        info = SimpleNamespace(duration=3.0, language="pt", language_probability=0.99)
        return segmentos, info


class TestTranscricao(unittest.TestCase):
    def setUp(self):
        self.pasta = PROJETO_DIR / "temp" / "test_runtime_transcricao"
        self.pasta.mkdir(parents=True, exist_ok=True)
        self.video = self.pasta / "curto.mp4"
        self.video.write_bytes(b"video-falso")
        self.cache = self.pasta / "cache"

    def tearDown(self):
        for arquivo in sorted(self.pasta.rglob("*"), reverse=True):
            if arquivo.is_file():
                arquivo.unlink(missing_ok=True)
            elif arquivo.is_dir():
                try:
                    arquivo.rmdir()
                except OSError:
                    pass
        try:
            self.pasta.rmdir()
        except OSError:
            pass

    def test_transcreve_salva_timestamps_e_reutiliza_cache(self):
        modelo = ModeloFalso()
        with (
            patch.object(T, "TRANSCRICOES_DIR", self.cache),
            patch.object(T, "_obter_modelo", return_value=modelo),
        ):
            primeira = T.transcrever_video(str(self.video), idioma="pt")
            segunda = T.transcrever_video(str(self.video), idioma="pt")

        self.assertEqual(modelo.chamadas, 1)
        self.assertEqual(primeira.segmentos[0].inicio, 0.125)
        self.assertEqual(primeira.segmentos[0].palavras[1].fim, 1.250)
        self.assertEqual(segunda.texto_corrido(), "Olá mundo Segundo trecho")
        self.assertTrue(Path(primeira.caminho_cache).is_file())

    def test_edicao_manual_preserva_tempos_e_persiste(self):
        transcricao = T.Transcricao(
            video_path=str(self.video.resolve()),
            video_id="teste",
            modelo="small",
            idioma_solicitado="pt",
            idioma_detectado="pt",
            probabilidade_idioma=1.0,
            duracao=5.0,
            criada_em="agora",
            segmentos=[T.SegmentoTranscricao(0.125, 1.250, "Texto original")],
            caminho_cache=str(self.cache / "edicao.json"),
        )
        editado = "[00:00:00.125 --> 00:00:01.250] Texto corrigido"
        T.aplicar_texto_editado(transcricao, editado)

        recarregada = T.carregar_transcricao(Path(transcricao.caminho_cache))
        self.assertEqual(recarregada.segmentos[0].texto, "Texto corrigido")
        self.assertEqual(recarregada.segmentos[0].inicio, 0.125)
        self.assertEqual(recarregada.segmentos[0].fim, 1.250)

    def test_edicao_rejeita_linha_sem_marcacao(self):
        transcricao = T.Transcricao(
            str(self.video), "id", "small", "pt", "pt", 1.0, 5.0, "agora", []
        )
        with self.assertRaises(ValueError):
            T.aplicar_texto_editado(transcricao, "texto sem timestamps")

    def test_transcricao_deixa_de_corresponder_se_arquivo_mudar(self):
        video_id = T._identidade_video(str(self.video), "small", "pt")
        transcricao = T.Transcricao(
            str(self.video.resolve()), video_id, "small", "pt", "pt", 1.0, 5.0, "agora", []
        )
        self.assertTrue(T.transcricao_corresponde_origem(transcricao))
        self.video.write_bytes(b"outro episodio no mesmo caminho")
        self.assertFalse(T.transcricao_corresponde_origem(transcricao))


if __name__ == "__main__":
    unittest.main()
