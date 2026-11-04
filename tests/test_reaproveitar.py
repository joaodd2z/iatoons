import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.reaproveitar import (  # noqa: E402
    VideoReaproveitavel, construir_comando, inspecionar_video, listar_videos,
    reaproveitar_video,
)


def item_valido(fps=60.0, caminho="corte.mp4"):
    return VideoReaproveitavel(caminho, 1080, 1920, fps, 10.0, True, True)


class _Stdout:
    def __iter__(self):
        return iter(["out_time_ms=1000000\n"])

    def close(self):
        pass


class _Processo:
    def __init__(self, *_args, **_kwargs):
        self.stdout = _Stdout()
        self.returncode = 0
        self.morto = False

    def kill(self):
        self.morto = True

    def wait(self):
        return self.returncode


class TesteReaproveitar(unittest.TestCase):
    def test_inspeciona_30_e_60_fps_com_audio(self):
        for taxa in ("30/1", "60/1"):
            resposta = Mock(returncode=0, stdout=json.dumps({
                "streams": [
                    {"codec_type": "video", "width": 1080, "height": 1920,
                     "avg_frame_rate": taxa, "duration": "12.5"},
                    {"codec_type": "audio", "codec_name": "aac"},
                ], "format": {"duration": "12.5"},
            }))
            with patch("modules.reaproveitar.subprocess.run", return_value=resposta):
                info = inspecionar_video("video.mp4")
            self.assertTrue(info.compativel)
            self.assertEqual(float(taxa.split("/")[0]), info.fps)
            self.assertTrue(info.tem_audio)

    def test_arquivo_fora_do_padrao_e_rejeitado_individualmente(self):
        resposta = Mock(returncode=0, stdout=json.dumps({
            "streams": [{"codec_type": "video", "width": 1920, "height": 1080,
                         "avg_frame_rate": "30/1", "duration": "5"}],
            "format": {"duration": "5"},
        }))
        with patch("modules.reaproveitar.subprocess.run", return_value=resposta):
            info = inspecionar_video("horizontal.mp4")
        self.assertFalse(info.compativel)
        self.assertIn("Resolução incompatível", info.erro)

    def test_comando_preserva_audio_fps_e_nao_reaplica_filtros_av(self):
        cmd = construir_comando(item_valido(60), "saida.mp4", "toonmix", True,
                                ROOT / "assets" / "fundo_toonmix.png")
        self.assertIn("copy", cmd[cmd.index("-c:a") + 1:])
        self.assertNotIn("-r", cmd)
        filtro = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("crop=1068:944:6:482", filtro)
        for proibido in ("unsharp", "hqdn3d", "eq=", "alimiter"):
            self.assertNotIn(proibido, filtro)

    def test_sem_preservar_textos_troca_apenas_fundo_e_centro(self):
        cmd = construir_comando(item_valido(30), "saida.mp4", "toonmix", False)
        filtro = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("crop=1068:944:6:482", filtro)
        self.assertNotIn("alphamerge", filtro)

    def test_nomes_duplicados_nunca_sobrescrevem(self):
        with tempfile.TemporaryDirectory() as pasta:
            Path(pasta, "corte_TOONMIX.mp4").touch()
            with patch("modules.reaproveitar.detectar_fundo_origem",
                       return_value=ROOT / "assets" / "fundo_toonmix.png"), \
                 patch("modules.reaproveitar.subprocess.Popen", _Processo):
                resultado = reaproveitar_video("corte.mp4", pasta, item=item_valido())
            self.assertTrue(resultado["sucesso"])
            self.assertTrue(resultado["caminho_saida"].endswith("_2.mp4"))

    def test_cancelamento_interrompe_arquivo_atual(self):
        cancelamento = Mock(cancelada=True)
        with tempfile.TemporaryDirectory() as pasta, \
             patch("modules.reaproveitar.detectar_fundo_origem",
                   return_value=ROOT / "assets" / "fundo_toonmix.png"), \
             patch("modules.reaproveitar.subprocess.Popen", _Processo):
            resultado = reaproveitar_video("corte.mp4", pasta, item=item_valido(),
                                           cancelada_ref=cancelamento)
        self.assertFalse(resultado["sucesso"])
        self.assertIn("cancelado", resultado["erro"])

    def test_varios_mp4_sao_listados_sem_interromper_por_invalido(self):
        with tempfile.TemporaryDirectory() as pasta:
            Path(pasta, "a.mp4").touch()
            Path(pasta, "b.mp4").touch()
            respostas = [item_valido(caminho=str(Path(pasta, "a.mp4"))),
                         VideoReaproveitavel(str(Path(pasta, "b.mp4")), erro="inválido")]
            with patch("modules.reaproveitar.inspecionar_video", side_effect=respostas):
                itens = listar_videos(pasta)
        self.assertEqual(2, len(itens))
        self.assertTrue(itens[0].compativel)
        self.assertFalse(itens[1].compativel)


if __name__ == "__main__":
    unittest.main()
