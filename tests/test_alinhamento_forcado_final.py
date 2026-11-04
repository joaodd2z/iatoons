from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import numpy as np
from modules.transcricao import Transcricao, SegmentoTranscricao as S, PalavraTranscrita as P
from scripts import benchmark_alinhamento_forcado_final as F


def transcript():
    return Transcricao("fake", "id", "small", "pt", "pt", 1., 30., "test",
                      [S(4.,7.,"Ola mundo",[P(4.,5.,"Ola"),P(6.,7.,"mundo")])])


class TestAlinhamentoFinal(unittest.TestCase):
    def setUp(self):
        for method in ("connect", "connect_ex"):
            p = patch("socket.socket."+method,side_effect=AssertionError("API forbidden"))
            p.start()
            self.addCleanup(p.stop)

    def test_no_asr_or_generate_only_fixed_tokens(self):
        model = Mock()
        model.model.is_multilingual = True
        model.model.generate.side_effect = AssertionError("No generation")
        model.transcribe.side_effect = AssertionError("No ASR")
        model.feature_extractor.return_value = np.zeros((80,3000),dtype=np.float32)
        model.find_alignment.return_value = [[dict(start=1.,end=2.,word=" Ola",probability=.9,tokens=[11])]]
        tokenizer = Mock()
        tokenizer.encode.return_value = [11]
        with patch("faster_whisper.tokenizer.Tokenizer",return_value=tokenizer):
            words = F.alinhar_fixo(model,np.zeros(4*16000),"Ola")
        tokenizer.encode.assert_called_once_with("Ola")
        self.assertEqual(model.find_alignment.call_args.args[1],[[11]])
        model.model.generate.assert_not_called()
        model.transcribe.assert_not_called()
        self.assertEqual(words[0].texto.strip(),"Ola")

    def test_fixed_segments_and_absolute_offset(self):
        segments = [S(100.,101.,"Ola",[]),S(102.,103.,"mundo",[])]
        words = [P(1.,2.," Ola"),P(3.,4.," mundo")]
        result = F.reconstruir_segmentos(segments,words,99.,10.)
        self.assertEqual([s.texto for s in result],["Ola","mundo"])
        self.assertEqual(result[0].inicio,100.)
        self.assertEqual(result[-1].palavras[-1].fim,103.)
        self.assertEqual(words[0].inicio,1.)
        with self.assertRaises(ValueError):
            F.reconstruir_segmentos(segments,[p for s in result for p in s.palavras],99.,10.)

    def test_text_mutation_rejected(self):
        with self.assertRaisesRegex(ValueError,"tokens narrativos"):
            F.reconstruir_segmentos([S(1.,2.,"Ola mundo",[])],[P(1.,2.,"historia nova")],0.,10.)

    def test_quality_only_verified_after_gate(self):
        ref = transcript()
        result = F.avaliar(ref,deepcopy(ref),"Ola mundo")
        self.assertTrue(result["aprovado"])
        self.assertEqual(result["timestamp_quality"],"aligned_verified")

    def test_outlier_blocks_despite_low_average_and_no_text_loss(self):
        ref = transcript()
        words = [P(4.+i,4.5+i,f"w{i}") for i in range(20)]
        text = " ".join(p.texto for p in words)
        ref.segmentos = [S(4.,23.5,text,words)]
        cand = deepcopy(ref)
        cand.segmentos[0].inicio = 2.
        cand.segmentos[0].palavras[0].inicio = 2.
        result = F.avaliar(ref,cand,text)
        self.assertFalse(result["aprovado"])
        self.assertLessEqual(result["media"],.20)
        self.assertLessEqual(result["p95"],.50)
        self.assertEqual(result["pior"],2.)
        self.assertEqual(result["palavras_perdidas"],0)
        self.assertEqual(result["timestamp_quality"],"aligned_unverified")

    def test_extra_word_is_never_approved(self):
        ref = transcript()
        cand = deepcopy(ref)
        cand.segmentos[0].palavras.insert(1,P(5.1,5.5,"novo"))
        result = F.avaliar(ref,cand,"Ola mundo")
        self.assertEqual(result["palavras_extras"],1)
        self.assertFalse(result["aprovado"])

    def test_zero_duration_blocked(self):
        ref = transcript()
        cand = deepcopy(ref)
        cand.segmentos[0].palavras[0].fim = 4.
        result = F.avaliar(ref,cand,"Ola mundo")
        self.assertIn("timestamps_invalidos",result["motivos"])


if __name__ == "__main__":
    unittest.main()
