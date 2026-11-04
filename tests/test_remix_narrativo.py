import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.ganchos_ab import remapear_legendas  # noqa: E402
from modules.intro import IntervaloIntro  # noqa: E402
from modules.legendas import BlocoLegenda, salvar_srt  # noqa: E402
from modules.remix_narrativo import (  # noqa: E402
    caminho_cache_remix, duracao_timeline, timeline_remix, validar_remixes,
)
from modules.config_ia import ConfigIA  # noqa: E402
from modules.transcricao import SegmentoTranscricao, Transcricao  # noqa: E402


def transcricao_teste():
    segmentos = [
        SegmentoTranscricao(float(i), float(i + 2), f"fala {i // 2}")
        for i in range(0, 140, 2)
    ]
    return Transcricao("inexistente.mp4", "remix-teste-unico", "small", "pt", "pt", 1.0,
                       140.0, "agora", segmentos)


def resposta_valida():
    return {
        "titulos": ["O plano impossível", "Ninguém esperava isso", "Tudo saiu do controle"],
        "descricao": "Uma decisão absurda cresce até provocar uma reação forte.",
        "gancho": {"inicio": 80.0, "fim": 84.0, "motivo": "Reação forte sem revelar o final."},
        "segmentos_narrativos": [
            {"inicio": 60.0, "fim": 76.0, "funcao": "contexto"},
            {"inicio": 76.0, "fim": 92.0, "funcao": "desenvolvimento_e_consequencia"},
        ],
        "justificativa": "O gancho cria curiosidade e a cronologia explica sua causa.",
        "continuidade": "Os personagens, o conflito e a consequência pertencem à mesma situação.",
        "confianca": 0.9,
        "remix_recomendado": True,
    }


class TesteRemixNarrativo(unittest.TestCase):
    def test_gancho_posterior_retorna_ao_contexto_e_chega_naturalmente(self):
        candidatos, erros = validar_remixes([resposta_valida()], transcricao_teste())
        self.assertFalse(erros)
        timeline = timeline_remix(candidatos[0])
        self.assertEqual((80.0, 84.0), (timeline[0].inicio, timeline[0].fim))
        self.assertEqual((60.0, 76.0), (timeline[1].inicio, timeline[1].fim))
        self.assertTrue(any(t.fim == 80.0 for t in timeline))
        self.assertTrue(any(t.inicio == 84.0 for t in timeline))

    def test_sem_repeticao_e_padrao(self):
        candidato = validar_remixes([resposta_valida()], transcricao_teste())[0][0]
        timeline = timeline_remix(candidato)
        repeticoes = [t for t in timeline if t.inicio <= 80 and t.fim >= 84]
        self.assertEqual(1, len(repeticoes))
        self.assertAlmostEqual(32.0, duracao_timeline(candidato))

    def test_com_repeticao_normaliza_gancho_na_cronologia(self):
        candidato = validar_remixes([resposta_valida()], transcricao_teste())[0][0]
        timeline = timeline_remix(candidato, repetir_gancho=True)
        self.assertEqual([(80, 84), (60, 76), (76, 80), (84, 92)],
                         [(t.inicio, t.fim) for t in timeline])
        self.assertAlmostEqual(32.0, duracao_timeline(candidato, True))

    def test_legendas_e_srt_sao_recalculados(self):
        candidato = validar_remixes([resposta_valida()], transcricao_teste())[0][0]
        blocos = [BlocoLegenda(80, 82, "gancho"), BlocoLegenda(60, 62, "contexto"),
                  BlocoLegenda(84, 86, "consequência")]
        remapeadas = remapear_legendas(blocos, timeline_remix(candidato))
        self.assertEqual([0.0, 4.0, 24.0], [b.inicio for b in remapeadas])
        with tempfile.TemporaryDirectory() as pasta:
            caminho = salvar_srt(remapeadas, Path(pasta) / "remix.srt")
            texto = caminho.read_text(encoding="utf-8-sig")
            self.assertIn("00:00:24,000 --> 00:00:26,000", texto)

    def test_segmentos_sobrepostos_sao_rejeitados(self):
        dado = resposta_valida()
        dado["segmentos_narrativos"][1]["inicio"] = 70
        validos, erros = validar_remixes([dado], transcricao_teste())
        self.assertFalse(validos)
        self.assertIn("sobrepostos", erros[0])

    def test_timestamps_invalidos_sao_rejeitados(self):
        dado = resposta_valida()
        dado["gancho"]["fim"] = 9999
        validos, _ = validar_remixes([dado], transcricao_teste())
        self.assertFalse(validos)

    def test_intro_no_meio_recusa_candidato(self):
        intro = IntervaloIntro(70, 75, True)
        with patch("modules.remix_narrativo.carregar_intro", return_value=intro):
            validos, erros = validar_remixes([resposta_valida()], transcricao_teste())
        self.assertFalse(validos)
        self.assertIn("intro", erros[0])

    def test_historias_distantes_nao_sao_unidas(self):
        dado = resposta_valida()
        dado["segmentos_narrativos"] = [
            {"inicio": 2, "fim": 10, "funcao": "contexto"},
            {"inicio": 76, "fim": 92, "funcao": "desenvolvimento"},
        ]
        validos, erros = validar_remixes([dado], transcricao_teste())
        self.assertFalse(validos)
        self.assertIn("histórias", erros[0])

    def test_remix_nao_recomendado_preserva_fallback_normal(self):
        dado = resposta_valida()
        dado["remix_recomendado"] = False
        validos, erros = validar_remixes([dado], transcricao_teste())
        self.assertFalse(validos)
        self.assertIn("preservar o corte normal", erros[0])

    def test_cache_depende_da_transcricao_e_nao_do_visual(self):
        transcricao = transcricao_teste()
        config = ConfigIA(modelo="gemini-teste")
        original = caminho_cache_remix(transcricao, config)
        transcricao.segmentos[0].texto = "fala corrigida"
        corrigido = caminho_cache_remix(transcricao, config)
        self.assertNotEqual(original, corrigido)
        # Perfis visuais nem sequer participam da assinatura da função/cache.
        self.assertEqual(corrigido, caminho_cache_remix(transcricao, config))


if __name__ == "__main__":
    unittest.main()
