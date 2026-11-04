"""Estúdio de Capa: modelo por conta, composição 1080x1920 e capas em lote numa pasta."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from modules import capa_estudio as CE
from modules import preset as P


def _video(caminho: Path, cor: str = "red", segundos: int = 2):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={cor}:s=1080x1920:d={segundos}",
                    "-pix_fmt", "yuv420p", str(caminho)], check=True)


@unittest.skipUnless(shutil.which("ffmpeg"), "precisa do ffmpeg")
class TestCapaEstudio(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_modelo_salva_e_volta_igual_e_ignora_campos_estranhos(self):
        with mock.patch.object(CE, "CAPAS_DIR", self.tmp):
            self.assertFalse(CE.tem_modelo("conta"))
            m = CE.ModeloCapa(posicao="meio", cor="#FFFFFF", selo="PARTE 1", momento=0.4)
            CE.salvar_modelo("conta", m)
            self.assertTrue(CE.tem_modelo("conta"))
            self.assertEqual(CE.carregar_modelo("conta"), m)
            self.assertEqual(CE.ModeloCapa.de_dict({"cor": "#111111", "lixo": 1}).cor, "#111111")

    def test_compor_mantem_formato_e_poe_o_quadro_na_area_do_video(self):
        perfil = CE.obter_perfil("toonmix")
        frame = Image.new("RGB", (P.SAIDA_LARGURA, P.SAIDA_ALTURA), (0, 200, 0))
        capa = CE.compor(frame, perfil, CE.ModeloCapa(selo="NOVO", faixa=True), "Um título bem comprido " * 4)
        self.assertEqual(capa.size, (P.SAIDA_LARGURA, P.SAIDA_ALTURA))
        z = perfil.zonas.video
        self.assertEqual(capa.getpixel((z.x + 20, z.y2 - 20)), (0, 200, 0))

    def test_aplicar_em_pasta_gera_uma_capa_por_video_com_titulo_do_txt(self):
        _video(self.tmp / "001_12h00_primeiro.mp4")
        _video(self.tmp / "002_12h30_segundo.mp4", "blue")
        (self.tmp / "001_12h00_primeiro.txt").write_text("Título do post\n\n#tag", encoding="utf-8")
        self.assertEqual(CE.titulo_do_post(self.tmp / "001_12h00_primeiro.mp4"), "Título do post")
        self.assertEqual(CE.titulo_do_post(self.tmp / "002_12h30_segundo.mp4"), "segundo")
        feitas = CE.aplicar_em_pasta(self.tmp, "toonmix", CE.ModeloCapa(momento=0.5))
        self.assertEqual(len(feitas), 2)
        for f in feitas:
            self.assertEqual(Image.open(f).size, (P.SAIDA_LARGURA, P.SAIDA_ALTURA))


if __name__ == "__main__":
    unittest.main()
