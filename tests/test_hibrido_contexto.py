import unittest
from unittest.mock import Mock, patch
import numpy as np
from scripts import benchmark_hibrido_contexto as C
from scripts import transcricao_hibrida_experimental as H
from modules.transcricao import Transcricao, SegmentoTranscricao as S, PalavraTranscrita as P


def source(a=100., b=102.):
    return Transcricao("fake", "identity", "small", "pt", "pt", 1., 220., "test",
                      [S(a, b, "historia", [P(a, b, "historia", .9)])])


class TestContexto(unittest.TestCase):
    def setUp(self):
        for method in ("connect", "connect_ex"):
            p = patch("socket.socket."+method, side_effect=AssertionError("Offline"))
            p.start()
            self.addCleanup(p.stop)

    def test_context_windows_merge_near_regions(self):
        selection = [{"inicio": 100., "fim": 140.}, {"inicio": 145., "fim": 190.}]
        with patch.object(C, "borda_segura", side_effect=lambda a,s,n,i:(n,"margem")):
            windows = C.planejar(selection, source(), np.empty(0), 10.)
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0]["refine_window"], [90., 200.])
        self.assertEqual(windows[0]["trusted_window"], [92., 198.])

    def test_guard_rejects_context_edge_words(self):
        with self.assertRaisesRegex(ValueError, "guard band"):
            C.validar_trusted({"inicio": 100., "fim": 104.}, {"trusted_window": [102., 120.]})

    def test_crop_discards_context_without_changing_absolute_times(self):
        s = source()
        s.segmentos += [S(110., 112., "contexto", [P(110.,112.,"contexto")])]
        cropped = C.recortar(s, 99., 105.)
        self.assertEqual(len(cropped.segmentos), 1)
        self.assertEqual(cropped.segmentos[0].palavras[0].inicio, 100.)
        self.assertEqual(len(s.segmentos), 2)

    def test_absolute_offset_added_exactly_once(self):
        local = [S(1., 2., "historia", [P(1.,2.,"historia")])]
        result = H.recompor_chunk(local, 100., 220., relativos=True, duracao_chunk=20.)
        self.assertEqual((result[0].inicio, result[0].palavras[0].fim), (101., 102.))
        self.assertEqual(local[0].inicio, 1.)
        with self.assertRaises(ValueError):
            H.recompor_chunk(result, 100., 220., relativos=True, duracao_chunk=20.)

    def test_edge_and_center_metrics_are_separate(self):
        ref = source(101.,102.)
        ref.segmentos += [S(110.,111.,"centro",[P(110.,111.,"centro")])]
        cand = source(102.,103.)
        cand.segmentos += [S(110.,111.,"centro",[P(110.,111.,"centro")])]
        result = C.diagnostico_window(ref, cand, 100., 120.)
        self.assertEqual(result["bordas"]["pior"], 1.)
        self.assertEqual(result["centro"]["pior"], 0.)

    def test_vad_and_previous_context_have_distinct_cache_keys(self):
        k = H.chave_cache("a", 10., 20., "small", "pt")[0]
        self.assertNotEqual(k, H.chave_cache("a",10.,20.,"small","pt", {"vad_filter": False})[0])
        self.assertNotEqual(k, H.chave_cache("a",10.,20.,"small","pt", {"condition_on_previous_text": False})[0])

    def test_continuous_audio_control_is_only_experimental(self):
        model = Mock()
        model.transcribe.return_value = ([], None)
        H.cpu_array(model, np.zeros(4*16000), 0., 4., vad_filter=False, condition_on_previous_text=False)
        self.assertFalse(model.transcribe.call_args.kwargs["vad_filter"])
        self.assertFalse(model.transcribe.call_args.kwargs["condition_on_previous_text"])
        self.assertTrue(H.ConfigTranscricao().vad_filter)
        self.assertTrue(H.ConfigTranscricao().condition_on_previous_text)


if __name__ == "__main__":
    unittest.main()
