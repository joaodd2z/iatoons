import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _agencia_dados import falas_remix, gerar_fonte, plano_remix, transcricao  # noqa: E402
from modules import preset as P  # noqa: E402
from modules.agencia import pipeline  # noqa: E402
from modules.agencia.av import AjustesAgencia, filtro_pos_escala, filtro_pre_escala  # noqa: E402
from modules.agencia.qualidade import analisar_zona_video, classificar_zona, validar_exportacao  # noqa: E402
from modules.agencia.renderer import _cobrir, fundo_com_furo  # noqa: E402
from modules.perfis_visuais import obter_perfil  # noqa: E402


def fundo(perfil):
    return _cobrir(Image.open(perfil.fundo_path).convert("RGB"), P.SAIDA_LARGURA, P.SAIDA_ALTURA)


class TesteAnaliseDeFrame(unittest.TestCase):
    def setUp(self):
        self.perfil = obter_perfil("toonmix")
        self.fundo = fundo(self.perfil)
        self.zona = self.perfil.zonas.video

    def test_somente_fundo_e_detectado(self):
        m = analisar_zona_video(self.fundo, self.fundo, self.zona)
        self.assertEqual("somente fundo na zona de vídeo", classificar_zona(m))

    def test_frame_preto_e_detectado(self):
        imagem = self.fundo.copy()
        imagem.paste((0, 0, 0), (self.zona.x, self.zona.y, self.zona.x2, self.zona.y2))
        self.assertEqual("frame preto", classificar_zona(analisar_zona_video(imagem, self.fundo, self.zona)))

    def test_conteudo_real_e_aprovado(self):
        imagem = self.fundo.copy()
        desenho = ImageDraw.Draw(imagem)
        for i in range(0, self.zona.w, 40):
            desenho.rectangle((self.zona.x + i, self.zona.y, self.zona.x + i + 20, self.zona.y2),
                              fill=(255, (i * 3) % 255, 40))
        self.assertIsNone(classificar_zona(analisar_zona_video(imagem, self.fundo, self.zona)))

    def test_fundo_com_furo_transparente_exatamente_na_zona(self):
        moldura = Image.open(fundo_com_furo(self.perfil))
        self.assertEqual("RGBA", moldura.mode)
        self.assertEqual(0, moldura.getpixel((self.zona.x + 5, self.zona.y + 5))[3])
        self.assertEqual(0, moldura.getpixel((self.zona.x2 - 1, self.zona.y2 - 1))[3])
        self.assertEqual(255, moldura.getpixel((self.zona.x + 5, self.zona.y - 2))[3])
        self.assertEqual(255, moldura.getpixel((540, 1800))[3])


class TesteExportacao(unittest.TestCase):
    def relatorio(self, **mudancas):
        base = {"resolucao": "1080x1920", "largura": 1080, "altura": 1920, "fps": 60.0,
                "codec_video": "h264", "pix_fmt": "yuv420p", "codec_audio": "aac", "faststart": True,
                "inicio_video": 0.0, "inicio_audio": 0.0, "duracao_video": 10.0, "duracao_audio": 10.0}
        base.update(mudancas)
        return base

    def test_especificacao_valida(self):
        self.assertTrue(all(i.ok for i in validar_exportacao(self.relatorio(), 10.0)))

    def test_fps_resolucao_e_atraso_de_audio_sao_reprovados(self):
        itens = {i.nome: i for i in validar_exportacao(
            self.relatorio(fps=30.0, largura=720, resolucao="720x1280", inicio_audio=0.2), 10.0)}
        self.assertFalse(itens["EXPORTACAO"].ok)
        self.assertFalse(itens["SINCRONIZACAO_AV"].ok)


class TestePresetAgencia(unittest.TestCase):
    def test_preset_moderado_e_separado_do_ia_toons(self):
        cfg = AjustesAgencia()
        self.assertEqual("AGENCY_AV_V1", cfg.nome)
        self.assertLessEqual(cfg.nitidez, 0.6)
        self.assertLessEqual(abs(cfg.contraste - 1), 0.05)
        self.assertEqual(0.0, cfg.brilho)
        self.assertEqual((17, "slow", 192), (cfg.crf, cfg.preset_x264, cfg.audio_kbps))
        self.assertIn("cas=strength", filtro_pos_escala(cfg))
        self.assertNotIn("unsharp", filtro_pos_escala(cfg))

    def test_tratamento_nao_reaplicado(self):
        cfg = AjustesAgencia()
        self.assertEqual("", filtro_pre_escala(cfg, aplicar=False))
        self.assertEqual("", filtro_pos_escala(cfg, aplicar=False))

    def test_valores_fora_da_faixa_sao_recusados(self):
        with self.assertRaises(ValueError):
            AjustesAgencia(nitidez=1.5).validar()
        with self.assertRaises(ValueError):
            AjustesAgencia(crf=30).validar()


class TestePrimeiroFrameInvalido(unittest.TestCase):
    def test_render_so_com_fundo_e_refeito_e_vai_para_revisao_sem_exportar(self):
        pasta = ROOT / "temp" / "teste_agencia_gate"
        fonte = gerar_fonte(pasta / "fonte.mp4", 640, 360, 31.0)
        t = transcricao(falas_remix(), fonte, "gate-fundo", 31.0)
        p = plano_remix("gate-fundo")
        perfil = obter_perfil("toonmix")
        chamadas = []

        def comando_defeituoso(video, saida, timeline, *args, **kwargs):
            chamadas.append(kwargs.get("modo_seguro", args[-1] if args else None))
            # Simula o defeito proibido: só o fundo aparece na zona de vídeo.
            return ["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-framerate", "60",
                    "-i", str(perfil.fundo_path), "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                    "-t", f"{timeline.duracao:.3f}", "-vf", "scale=1080:1920,format=yuv420p",
                    "-c:v", "libx264", "-preset", "ultrafast", "-r", "60", "-c:a", "aac",
                    "-movflags", "+faststart", saida]

        saida = pasta / "final"
        with patch.object(pipeline, "construir_comando", side_effect=comando_defeituoso):
            r = pipeline.exportar_corte_agencia(str(fonte), p, t, saida, perfil=perfil,
                                                ajustes=AjustesAgencia(preset_x264="ultrafast"),
                                                gerar_capa=False)
        self.assertFalse(r["sucesso"])
        self.assertTrue(r["revisao"])
        self.assertIn("FIRST_FRAME", r["erro"])
        self.assertEqual(2, len(chamadas))                  # refeito uma vez em modo seguro
        self.assertEqual("revisão", p.status)
        self.assertFalse(list(saida.glob("*.mp4")) if saida.exists() else [])
        self.assertTrue(list(pipeline.REVISAO_DIR.glob(f"{p.id}_REVISAR*.mp4")))


if __name__ == "__main__":
    unittest.main()
