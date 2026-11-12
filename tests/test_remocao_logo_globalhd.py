"""Testes de validação da remoção adaptativa da marca Global HD / emissora."""
import sys
from pathlib import Path

# Adiciona a raiz do projeto ao path
sys.path.insert(0, str(Path(__file__).parent.parent))

import unittest
from modules.agencia.av import AjustesAgencia, filtro_delogo, filtro_delogo_zona, filtro_pre_escala
from modules.processamento_av import AjustesAV, filtro_animacao


class TestRemocaoLogoGlobalHD(unittest.TestCase):
    def test_delogo_calculo_proporcional_1080p(self):
        # Para 1920x1080 (Full HD):
        filtro = filtro_delogo(1920, 1080, ativo=True)
        self.assertTrue(filtro.startswith("delogo="))
        self.assertIn("x=", filtro)
        self.assertIn("y=", filtro)
        self.assertIn("w=", filtro)
        self.assertIn("h=", filtro)
        
        # Extrai os parâmetros
        partes = dict(p.split("=") for p in filtro.replace("delogo=", "").split(":"))
        x, y, w, h = int(partes["x"]), int(partes["y"]), int(partes["w"]), int(partes["h"])
        
        # Garante que a caixa delimitadora fica dentro dos limites do frame
        self.assertLess(x + w, 1920)
        self.assertLess(y + h, 1080)
        # Garante que a caixa cobre o canto inferior direito
        self.assertGreater(x, 1500)
        self.assertGreater(y, 800)

    def test_delogo_calculo_proporcional_720p(self):
        # Para 1280x720 (HD):
        filtro = filtro_delogo(1280, 720, ativo=True)
        self.assertTrue(filtro.startswith("delogo="))
        partes = dict(p.split("=") for p in filtro.replace("delogo=", "").split(":"))
        x, y, w, h = int(partes["x"]), int(partes["y"]), int(partes["w"]), int(partes["h"])
        self.assertLess(x + w, 1280)
        self.assertLess(y + h, 720)
        self.assertGreater(x, 1000)
        self.assertGreater(y, 500)

    def test_delogo_desativado(self):
        self.assertEqual(filtro_delogo(1920, 1080, ativo=False), "")
        self.assertEqual(filtro_delogo(0, 0, ativo=True), "")

    def test_filtro_pre_escala_agencia(self):
        cfg = AjustesAgencia(remover_logo=True)
        filtro = filtro_pre_escala(cfg, aplicar=True, largura=1920, altura=1080)
        self.assertIn("delogo=", filtro)

        cfg_sem_logo = AjustesAgencia(remover_logo=False)
        filtro_sem = filtro_pre_escala(cfg_sem_logo, aplicar=True, largura=1920, altura=1080)
        self.assertNotIn("delogo=", filtro_sem)

    def test_filtro_animacao_classico(self):
        cfg = AjustesAV(remover_logo=True)
        filtro = filtro_animacao(cfg, largura_orig=1920, altura_orig=1080)
        self.assertIn("delogo=", filtro)

        cfg_sem = AjustesAV(remover_logo=False)
        filtro_sem = filtro_animacao(cfg_sem, largura_orig=1920, altura_orig=1080)
        self.assertNotIn("delogo=", filtro_sem)

    def test_delogo_zona_pos_crop_cobre_quina_inferior_esquerda_toonmix(self):
        # Janela TOONMIX 1068x944 após scale/crop
        filtro = filtro_delogo_zona(1068, 944, ativo=True)
        self.assertTrue(filtro.startswith("delogo="))
        partes = dict(p.split("=") for p in filtro.replace("delogo=", "").split(":"))
        x, y, w, h = int(partes["x"]), int(partes["y"]), int(partes["w"]), int(partes["h"])

        # Exigência do FFmpeg delogo: 1px de margem mínima e área dentro do frame
        self.assertEqual(x, 1)
        self.assertGreaterEqual(y, 1)
        self.assertLess(x + w, 1068)
        self.assertLess(y + h, 944)
        self.assertEqual(y + h, 943)  # cobre até a quina inferior esquerda (1px da borda)
        self.assertGreaterEqual(w, 120)
        self.assertGreaterEqual(h, 90)
        self.assertEqual(filtro_delogo_zona(1068, 944, ativo=False), "")

    def test_construir_grafo_injeta_delogo_pos_crop(self):
        from modules.agencia.layout import calcular_enquadramento
        from modules.agencia.renderer import InfoFonte, construir_grafo
        from modules.agencia.timeline import TimelineAgencia, TrechoTimeline
        from modules.perfis_visuais import PERFIL_TOONMIX

        tl = TimelineAgencia(tipo="remix", trechos=[TrechoTimeline(0.0, 5.0, "gancho", "inicio", 0.0)], fps=60)
        info = InfoFonte(1920, 1080, 30.0, 120.0, True)
        enq = calcular_enquadramento(1920, 1080)
        _, grafo = construir_grafo(tl, info, enq, PERFIL_TOONMIX, AjustesAgencia(remover_logo=True), None, [])
        dl_zona = filtro_delogo_zona(enq.crop_w, enq.crop_h, ativo=True)
        self.assertIn(f"crop={enq.crop_w}:{enq.crop_h}:{enq.crop_x}:{enq.crop_y},{dl_zona}", grafo)


if __name__ == "__main__":
    unittest.main()
