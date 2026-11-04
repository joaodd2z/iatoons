from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from modules import intro
from modules.ganchos_ab import TrechoFonte
from modules.intro import (IntervaloIntro, carregar_intro, detectar_intro,
                           remover_intro, salvar_intro)
from modules.transcricao import SegmentoTranscricao, Transcricao


def criar_transcricao(segmentos, duracao=180):
    return Transcricao("video.mp4", "video-intro", "small", "pt", "pt", 1,
                       duracao, "agora", segmentos)


class TesteIntro(unittest.TestCase):
    def test_preserva_cena_narrativa_antes_da_abertura(self):
        t = criar_transcricao([
            SegmentoTranscricao(0, 12, "Uma cena narrativa importante."),
            SegmentoTranscricao(12, 20, "A história continua e termina."),
            SegmentoTranscricao(30, 34, "Violência em filmes e sexo na TV."),
            SegmentoTranscricao(34.2, 38, "Onde estão os velhos costumes?"),
            SegmentoTranscricao(38.2, 42, "Uma delícia de família."),
            SegmentoTranscricao(43, 46, "Versão brasileira."),
            SegmentoTranscricao(50, 55, "Primeira cena depois da abertura."),
        ])
        intervalo = detectar_intro(t)
        self.assertIsNotNone(intervalo)
        self.assertEqual(30, intervalo.inicio)
        self.assertEqual(46, intervalo.fim)

    def test_sem_evidencia_forte_nao_exclui_nada(self):
        t = criar_transcricao([SegmentoTranscricao(0, 20, "Conversa normal de uma cena.")])
        self.assertIsNone(detectar_intro(t))

    def test_remove_somente_intervalo_da_intro_e_preserva_lados(self):
        trechos = remover_intro([TrechoFonte(0, 90)], IntervaloIntro(20, 40))
        self.assertEqual([(0, 20), (40, 90)], [(t.inicio, t.fim) for t in trechos])

    def test_correcao_manual_persiste(self):
        t = criar_transcricao([SegmentoTranscricao(0, 100, "texto")])
        with tempfile.TemporaryDirectory() as pasta, patch.object(intro, "INTROS_DIR", Path(pasta)):
            salvar_intro(t, IntervaloIntro(12.345, 33.678, True, False, 1, "manual"))
            carregado = carregar_intro(t)
        self.assertEqual(12.345, carregado.inicio)
        self.assertEqual(33.678, carregado.fim)
        self.assertFalse(carregado.automatico)


if __name__ == "__main__":
    unittest.main()
