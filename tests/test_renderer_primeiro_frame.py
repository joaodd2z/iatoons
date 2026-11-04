import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageChops, ImageStat


PROJETO_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJETO_DIR))

from modules import preset as P  # noqa: E402
from modules.processamento_av import AjustesAV  # noqa: E402
from modules.renderer import renderizar_corte  # noqa: E402


class TestePrimeiroFrameRenderer(unittest.TestCase):
    def test_animacao_existe_desde_frame_zero_e_audio_comeca_sincronizado(self):
        pasta = PROJETO_DIR / "temp" / "teste_renderer_primeiro_frame_auto"
        pasta.mkdir(parents=True, exist_ok=True)
        fonte = pasta / "fonte.mp4"
        saida = pasta / "saida.mp4"
        frames = [pasta / f"frame_{i}.png" for i in range(3)]
        try:
            subprocess.run([
                "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30",
                "-f", "lavfi", "-i", "sine=frequency=700:sample_rate=44100",
                "-t", "5", "-c:v", "libx264", "-g", "90", "-pix_fmt", "yuv420p",
                "-c:a", "aac", str(fonte), "-loglevel", "error",
            ], check=True)
            ajustes = AjustesAV(imagem_ativa=False, alta_qualidade=False, audio_ativo=False)
            with patch.object(P, "SAIDA_PRESET", "ultrafast"):
                resultado = renderizar_corte(
                    str(fonte), 2.2, 3.8, "TESTE", str(saida), fps=30, ajustes_av=ajustes
                )
            self.assertTrue(resultado["sucesso"], resultado.get("erro"))

            for tempo, frame in zip((0.0, 0.5, 1.4), frames):
                subprocess.run([
                    "ffmpeg", "-y", "-ss", str(tempo), "-i", str(saida),
                    "-frames:v", "1", str(frame), "-loglevel", "error",
                ], check=True)

            fundo = Image.open(P.FUNDO_PATH).convert("RGB")
            if fundo.size != (P.SAIDA_LARGURA, P.SAIDA_ALTURA):
                fundo = fundo.resize((P.SAIDA_LARGURA, P.SAIDA_ALTURA))
            area_fundo = fundo.crop((P.ANIM_X, P.ANIM_Y,
                                     P.ANIM_X + P.ANIM_W, P.ANIM_Y + P.ANIM_H))
            for frame in frames:
                imagem = Image.open(frame).convert("RGB")
                area = imagem.crop((P.ANIM_X, P.ANIM_Y,
                                    P.ANIM_X + P.ANIM_W, P.ANIM_Y + P.ANIM_H))
                diferenca = sum(ImageStat.Stat(ImageChops.difference(area, area_fundo)).mean) / 3
                self.assertGreater(diferenca, 20, f"Animação ausente em {frame.name}")

            probe = subprocess.run([
                "ffprobe", "-v", "error", "-show_entries", "stream=codec_type,start_time",
                "-of", "json", str(saida),
            ], check=True, capture_output=True, text=True, encoding="utf-8")
            streams = json.loads(probe.stdout)["streams"]
            inicios = {s["codec_type"]: float(s.get("start_time", 0)) for s in streams}
            self.assertAlmostEqual(0.0, inicios["video"], places=3)
            self.assertAlmostEqual(0.0, inicios["audio"], places=3)
        finally:
            for arquivo in frames + [saida, fonte]:
                arquivo.unlink(missing_ok=True)
            try:
                pasta.rmdir()
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
