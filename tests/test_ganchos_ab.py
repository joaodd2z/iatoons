import unittest
from unittest.mock import patch

from modules.cortes import Corte
from modules.ganchos_ab import (TrechoFonte, identificar_gancho_b,
                                remapear_legendas, segmentos_da_versao)
from modules.legendas import BlocoLegenda
from modules.transcricao import PalavraTranscrita, SegmentoTranscricao, Transcricao
from modules.renderer import renderizar_corte


def transcricao_ab():
    palavras = [
        PalavraTranscrita(8.0, 8.8, " Como"),
        PalavraTranscrita(8.8, 9.5, " isso"),
        PalavraTranscrita(9.5, 10.4, " aconteceu?"),
    ]
    segmentos = [
        SegmentoTranscricao(0, 3, "Contexto necessário."),
        SegmentoTranscricao(8, 10.4, "Como isso aconteceu?", palavras),
        SegmentoTranscricao(10.8, 70, "Desenvolvimento e conclusão."),
    ]
    return Transcricao("video.mp4", "id", "small", "pt", "pt", 1, 70, "agora", segmentos)


class TesteGanchosAB(unittest.TestCase):
    def test_identifica_pergunta_completa_sem_cortar_palavra(self):
        corte = Corte(0, 70, "TÍTULO")
        trecho, motivo = identificar_gancho_b(corte, transcricao_ab())
        self.assertEqual((8.0, 10.4), (trecho.inicio, trecho.fim))
        self.assertIn("Como isso aconteceu?", motivo)

    def test_sem_gancho_mantem_somente_a(self):
        transcricao = transcricao_ab()
        transcricao.segmentos[1].texto = "uma frase comum"
        for palavra in transcricao.segmentos[1].palavras:
            palavra.texto = palavra.texto.replace("?", "")
        corte = Corte(0, 70, "TÍTULO")
        trecho, _ = identificar_gancho_b(corte, transcricao)
        self.assertIsNone(trecho)
        self.assertEqual(1, len(segmentos_da_versao(corte, "B")))

    def test_ordem_b_e_legendas_sao_remapeadas(self):
        corte = Corte(0, 70, "TÍTULO", gancho_b_inicio=8.0, gancho_b_fim=10.4)
        trechos = segmentos_da_versao(corte, "B")
        self.assertEqual([(8.0, 10.4), (0, 70)], [(t.inicio, t.fim) for t in trechos])
        blocos = [BlocoLegenda(8.0, 10.4, "Como isso aconteceu?"),
                  BlocoLegenda(0, 3, "Contexto necessário.")]
        legendas = remapear_legendas(blocos, trechos)
        self.assertAlmostEqual(0, legendas[0].inicio)
        self.assertAlmostEqual(2.4, legendas[0].fim)
        self.assertAlmostEqual(2.4, legendas[1].inicio)
        self.assertAlmostEqual(5.4, legendas[1].fim)
        self.assertAlmostEqual(10.4, legendas[2].inicio)
        self.assertAlmostEqual(12.8, legendas[2].fim)

    def test_renderer_concatena_video_audio_e_aplica_tratamento_depois(self):
        capturado = {}

        class Processo:
            stdout = iter([])
            returncode = 0
            def wait(self): return 0
            def kill(self): self.returncode = -9

        def popen(cmd, **_kwargs):
            capturado["cmd"] = cmd
            return Processo()

        trechos = [TrechoFonte(8, 10.4), TrechoFonte(0, 70)]
        with patch("modules.renderer.subprocess.Popen", side_effect=popen):
            resultado = renderizar_corte("entrada.mp4", 0, 70, "T", "saida.mp4",
                                         fps=30, segmentos_fonte=trechos)
        self.assertTrue(resultado["sucesso"])
        self.assertAlmostEqual(72.4, resultado["duracao_s"])
        cmd = capturado["cmd"]
        self.assertNotIn("-ss", cmd)
        complexo = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("trim=start=8.000:end=10.400", complexo)
        self.assertIn("concat=n=2:v=1:a=0[seqv]", complexo)
        self.assertIn("concat=n=2:v=0:a=1[seqa]", complexo)
        self.assertIn("[seqa]volume=", complexo)
        self.assertEqual("[audioout]", cmd[cmd.index("-map", cmd.index("-map") + 1) + 1])


if __name__ == "__main__":
    unittest.main()
