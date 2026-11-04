import sys
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules import preset as P  # noqa: E402
from modules.agencia.layout import (  # noqa: E402
    ajustar_texto, calcular_enquadramento, classificar_fonte, dividir_legendas_longas,
    estimar_centro_interesse, verificar_overflow,
)
from modules.legendas import BlocoLegenda  # noqa: E402
from modules.perfis_visuais import ZONAS_TOONMIX, Zona, obter_perfil  # noqa: E402


class TestePerfilToonmix(unittest.TestCase):
    def test_perfil_toonmix_eh_unico_e_fixo(self):
        toonmix = obter_perfil("toonmix")
        self.assertTrue(toonmix.fundo_path.is_file())
        self.assertEqual("TOONMIX", toonmix.nome)
        self.assertEqual(ZONAS_TOONMIX, toonmix.zonas)
        self.assertEqual((P.ANIM_X, P.ANIM_Y, P.ANIM_W, P.ANIM_H),
                         (toonmix.zonas.video.x, toonmix.zonas.video.y,
                          toonmix.zonas.video.w, toonmix.zonas.video.h))

    def test_zonas_independentes_dentro_do_canvas_e_sem_sobreposicao(self):
        canvas = Zona(0, 0, P.SAIDA_LARGURA, P.SAIDA_ALTURA)
        for perfil_id in ("toonmix",):
            z = obter_perfil(perfil_id).zonas
            for zona in (z.topo, z.video, z.base, z.titulo, z.legenda):
                self.assertTrue(canvas.contem(zona), (perfil_id, zona))
            self.assertLessEqual(z.topo.y2, z.video.y)
            self.assertLessEqual(z.video.y2, z.base.y)
            self.assertTrue(z.topo.contem(z.titulo))
            self.assertTrue(z.base.contem(z.legenda))
            # Faixa superior das plataformas (status/abas) fica livre de texto.
            self.assertGreaterEqual(z.titulo.y, 140)
            self.assertEqual(0, z.video.w % 2)
            self.assertEqual(0, z.video.h % 2)

    def test_zona_de_video_toonmix_coincide_com_a_moldura_do_fundo(self):
        perfil = obter_perfil("toonmix")
        fundo = Image.open(perfil.fundo_path).convert("RGB")
        self.assertEqual((1080, 1920), fundo.size)
        zona = perfil.zonas.video

        def amarelo(px):
            r, g, b = px
            return r > 180 and g > 120 and b < 110

        # Moldura amarela imediatamente acima e abaixo da zona, e não dentro dela.
        self.assertTrue(any(amarelo(fundo.getpixel((540, y))) for y in range(zona.y - 10, zona.y)))
        self.assertTrue(any(amarelo(fundo.getpixel((540, y))) for y in range(zona.y2, zona.y2 + 10)))
        self.assertFalse(amarelo(fundo.getpixel((540, zona.y + zona.h // 2))))
        # Logo (y≈1778–1855) fora da área de legenda.
        self.assertLess(perfil.zonas.legenda.y2, 1778)


class TesteEnquadramento(unittest.TestCase):
    def setUp(self):
        self.zona = obter_perfil("toonmix").zonas.video

    def test_classificacao(self):
        self.assertEqual("horizontal", classificar_fonte(1920, 1080))
        self.assertEqual("vertical", classificar_fonte(1080, 1920))
        self.assertEqual("quadrada", classificar_fonte(720, 720))

    def test_horizontal_preenche_zona_1x1_sem_barras_nem_desfoque(self):
        e = calcular_enquadramento(1920, 1080, self.zona)
        self.assertEqual("cobrir", e.estrategia)
        self.assertFalse(e.usa_desfoque)
        self.assertEqual(self.zona.w, e.crop_w)
        self.assertEqual(self.zona.h, e.crop_h)
        self.assertEqual(0, e.pos_x)
        self.assertEqual(0, e.pos_y)
        self.assertEqual(1.0, e.area_ocupada)

    def test_vertical_preenche_zona_1x1_sem_barras_nem_desfoque(self):
        e = calcular_enquadramento(1080, 1920, self.zona)
        self.assertEqual("vertical", e.tipo_fonte)
        self.assertEqual("cobrir", e.estrategia)
        self.assertFalse(e.usa_desfoque)
        self.assertEqual(self.zona.w, e.crop_w)
        self.assertEqual(self.zona.h, e.crop_h)
        self.assertEqual(0, e.pos_x)
        self.assertEqual(0, e.pos_y)
        self.assertEqual(1.0, e.area_ocupada)

    def test_quadrada_preenche_a_zona_com_recorte_pequeno(self):
        e = calcular_enquadramento(720, 720, self.zona)
        self.assertEqual("cobrir", e.estrategia)
        self.assertEqual((self.zona.w, self.zona.h), (e.crop_w, e.crop_h))
        self.assertGreater(e.fracao_visivel, 0.85)
        self.assertEqual(1.0, e.area_ocupada)

    def test_zona_toonmix_default(self):
        e = calcular_enquadramento(1200, 1080)
        self.assertEqual("cobrir", e.estrategia)
        self.assertEqual((ZONAS_TOONMIX.video.w, ZONAS_TOONMIX.video.h), (e.crop_w, e.crop_h))
        e = calcular_enquadramento(1440, 1080)
        self.assertEqual("cobrir", e.estrategia)
        self.assertFalse(e.usa_desfoque)

    def test_centro_de_interesse_desloca_o_recorte_para_o_personagem(self):
        e_centro = calcular_enquadramento(1440, 1080, self.zona, centro_x=0.5)
        e_direita = calcular_enquadramento(1440, 1080, self.zona, centro_x=0.68)
        self.assertGreater(e_direita.crop_x, e_centro.crop_x)
        self.assertLessEqual(e_direita.crop_x + e_direita.crop_w, e_direita.escala_w)
        imagem = Image.new("RGB", (320, 180), (200, 200, 200))
        for x in range(230, 300, 6):  # "personagem" detalhado à direita
            for y in range(40, 170, 6):
                imagem.putpixel((x, y), (255, 0, 0))
        cx, _ = estimar_centro_interesse([imagem])
        self.assertGreater(cx, 0.55)


class TesteTitulo(unittest.TestCase):
    def setUp(self):
        self.perfil = obter_perfil("toonmix")
        self.estilo = self.perfil.estilo_titulo_agencia
        self.zona = self.perfil.zonas.titulo

    def test_estilo_amarelo_com_contorno_preto_forte(self):
        self.assertEqual("0xFFE600", self.estilo.cor)
        self.assertEqual("black", self.estilo.cor_contorno)
        self.assertGreaterEqual(self.estilo.espessura_contorno, 8)

    def test_titulo_curto_em_uma_linha_ocupa_boa_largura(self):
        ajuste = ajustar_texto("Brian não esperava isso", self.estilo, self.zona)
        self.assertTrue(ajuste.cabe)
        self.assertEqual(1, len(ajuste.linhas))
        self.assertGreater(ajuste.largura_max, self.zona.w * 0.75)
        self.assertEqual([], verificar_overflow(ajuste))

    def test_titulo_longo_em_duas_linhas_equilibradas(self):
        ajuste = ajustar_texto("O que o Stewie escondeu no berço da loja de bebês?", self.estilo, self.zona)
        self.assertTrue(ajuste.cabe)
        self.assertEqual(2, len(ajuste.linhas))
        self.assertTrue(self.zona.contem(ajuste.caixa))
        self.assertTrue(self.perfil.zonas.topo.contem(ajuste.caixa))

    def test_titulo_impossivel_e_reportado_como_overflow(self):
        ajuste = ajustar_texto("palavra " * 30, self.estilo, self.zona)
        self.assertFalse(ajuste.cabe)
        self.assertTrue(verificar_overflow(ajuste))


class TesteLegendas(unittest.TestCase):
    def test_legenda_longa_e_dividida_sem_perder_tempo_nem_palavras(self):
        perfil = obter_perfil("toonmix")
        estilo, zona = perfil.estilo_legenda_agencia, perfil.zonas.legenda
        texto = ("esta legenda é propositalmente enorme para testar a quebra automática "
                 "sem ultrapassar a área inferior reservada para as legendas do vídeo")
        bloco = BlocoLegenda(10.0, 16.0, texto)
        self.assertFalse(ajustar_texto(texto, estilo, zona).cabe)
        partes = dividir_legendas_longas([bloco], estilo, zona)
        self.assertGreater(len(partes), 1)
        self.assertEqual(texto.split(), " ".join(p.texto for p in partes).split())
        self.assertAlmostEqual(10.0, partes[0].inicio)
        self.assertAlmostEqual(16.0, partes[-1].fim)
        for anterior, atual in zip(partes, partes[1:]):
            self.assertAlmostEqual(anterior.fim, atual.inicio)
        for parte in partes:
            ajuste = ajustar_texto(parte.texto, estilo, zona)
            self.assertTrue(ajuste.cabe)
            self.assertLessEqual(len(ajuste.linhas), 2)
            self.assertTrue(zona.contem(ajuste.caixa))

    def test_titulo_e_legenda_sao_estilos_independentes(self):
        perfil = obter_perfil("toonmix")
        self.assertNotEqual(perfil.estilo_titulo_agencia, perfil.estilo_legenda_agencia)
        self.assertNotEqual(perfil.zonas.titulo, perfil.zonas.legenda)


if __name__ == "__main__":
    unittest.main()
