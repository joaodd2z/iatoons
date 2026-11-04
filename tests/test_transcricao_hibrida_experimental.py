from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import os

from modules.transcricao import PalavraTranscrita as P, SegmentoTranscricao as S, Transcricao
from scripts import transcricao_hibrida_experimental as H


def segmento(inicio, fim, texto):
    return S(inicio, fim, texto, [P(inicio, fim, texto, .99)])


def transcript(segmentos):
    return Transcricao("fake", "identity", "small", "pt", "pt", 1., 30., "test", segmentos)


class TestHibrido(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        for method in ("connect", "connect_ex"):
            p = patch("socket.socket."+method, side_effect=AssertionError("No real API"))
            p.start()
            self.addCleanup(p.stop)
        self.gpu = transcript([segmento(10.5, 11.5, "conflito"), segmento(12.5, 13.5, "consequencia")])
        self.selection = [{"trecho_id": "hook", "funcao": "gancho", "cut_id": "1", "inicio": 10.5, "fim": 13.5}]
        self.cache = H.CachePreciso(self.folder, "episode", 30.)
        self.cpu = Mock(return_value=[segmento(.5, 1.5, "conflito"), segmento(2.5, 3.5, "consequencia")])

    def refinar(self, **kwargs):
        return H.refinar_selecao(H.Passagem(self.gpu, "provisional"), self.selection,
                                self.cache, self.cpu, **kwargs)

    def test_gpu_always_provisional(self):
        cpu = Mock()
        p = H.primeira_passagem(lambda: self.gpu, cpu)
        self.assertEqual(p.timestamp_quality, "provisional")
        self.assertTrue(all(s["timestamp_quality"] == "provisional" for s in p.serializar()["segmentos"]))
        cpu.assert_not_called()

    def test_gpu_error_falls_back_full_cpu(self):
        cpu = Mock(return_value=self.gpu)
        p = H.primeira_passagem(Mock(side_effect=RuntimeError("CUDA missing")), cpu)
        self.assertEqual(p.timestamp_quality, "precise_unverified")
        self.assertTrue(p.fallback_cpu)
        cpu.assert_called_once_with()

    def test_merge_expanded_overlapping_intervals(self):
        selected = [{"inicio": 100., "fim": 130.}, {"inicio": 128., "fim": 160.}]
        self.assertEqual(H.unir_regioes(selected, 200., margem=2.), [(98., 162.)])

    def test_margin_clamps_episode(self):
        self.assertEqual(H.unir_regioes([{"inicio": 1., "fim": 29.}], 30.), [(0., 30.)])

    def test_invalid_intervals_rejected(self):
        for a, b in ((float("nan"), 20), (10, 9), (-1, 5), (20, 31)):
            with self.assertRaises(ValueError):
                H.unir_regioes([{"inicio": a, "fim": b}], 30.)

    def test_cpu_offsets_replace_gpu_timestamps(self):
        result = self.refinar()
        t = result["timeline"][0]
        self.assertTrue(result["todos_processados_cpu"])
        self.assertFalse(result["todos_precisos"])
        self.assertEqual((t["inicio"], t["fim"]), (8., 11.))
        self.assertEqual((t["inicio_original"], t["fim_original"]), (10.5, 13.5))
        self.assertEqual(t["timestamp_quality"], "precise_unverified")
        self.assertEqual(t["segmentos"][0]["palavras"][0]["inicio"], 8.)
        self.assertEqual(self.gpu.segmentos[0].inicio, 10.5)

    def test_same_region_not_transcribed_twice(self):
        self.refinar()
        result = self.refinar()
        self.cpu.assert_called_once_with(7.5, 16.5)
        self.assertTrue(result["regioes"][0]["cache_hit"])

    def test_disk_cache_survives_new_instance(self):
        self.refinar()
        self.cache = H.CachePreciso(self.folder, "episode", 30.)
        self.refinar()
        self.assertEqual(self.cpu.call_count, 1)

    def test_cache_identity_model_language_version(self):
        key, _ = H.chave_cache("a", 1., 9., "small", "pt")
        for values in (("b", 1., 9., "small", "pt"), ("a", 2., 9., "small", "pt"),
                       ("a", 1., 10., "small", "pt"), ("a", 1., 9., "base", "pt"),
                       ("a", 1., 9., "small", "en")):
            self.assertNotEqual(key, H.chave_cache(*values)[0])
        with patch.object(H, "VERSION", H.VERSION+1):
            self.assertNotEqual(key, H.chave_cache("a", 1., 9., "small", "pt")[0])

    def test_corrupt_cache_never_used(self):
        self.refinar()
        path = next(self.folder.glob("*.json"))
        data = json.loads(path.read_text())
        data["segmentos"][0]["inicio"] = -99
        path.write_text(json.dumps(data))
        self.cache = H.CachePreciso(self.folder, "episode", 30.)
        self.refinar()
        self.assertEqual(self.cpu.call_count, 2)

    def test_invalid_cpu_offsets_never_become_precise(self):
        self.cpu.return_value = [segmento(20., 21., "conflito")]
        result = self.refinar()
        self.assertFalse(result["todos_precisos"])
        self.assertEqual(result["timeline"], [])
        self.assertFalse(list(self.folder.glob("*.json")))

    def test_failed_refinement_only_cpu_retry(self):
        self.cpu.side_effect = RuntimeError("CPU transient")
        backup = Mock(return_value=[segmento(.5, 1.5, "conflito"), segmento(2.5, 3.5, "consequencia")])
        result = self.refinar(fallback_cpu=backup)
        self.assertTrue(result["todos_processados_cpu"])
        self.assertTrue(result["regioes"][0]["retry_cpu"])
        backup.assert_called_once_with(7.5, 16.5)

    def test_failed_retry_blocks_instead_of_using_gpu(self):
        self.cpu.side_effect = RuntimeError("failed")
        result = self.refinar(fallback_cpu=Mock(side_effect=RuntimeError("failed twice")))
        self.assertFalse(result["todos_precisos"])
        self.assertEqual(result["timeline"], [])
        self.assertEqual(result["pendentes"][0]["motivo"], "failed twice")

    def test_ambiguous_story_keeps_decision_pending(self):
        self.cpu.return_value = [segmento(.5, 1.5, "outra historia")]
        result = self.refinar()
        self.assertFalse(result["todos_precisos"])
        self.assertEqual(result["timeline"], [])
        self.assertEqual(result["pendentes"][0]["trecho"], self.selection[0])

    def test_provisional_timing_is_blocked(self):
        with self.assertRaisesRegex(ValueError, "provisorio"):
            H.exigir_precisos([{"timestamp_quality": "provisional"}], 30.)

    def test_final_segment_provisional_is_blocked(self):
        result = self.refinar()["timeline"]
        result[0]["segmentos"][0]["timestamp_quality"] = "provisional"
        with self.assertRaisesRegex(ValueError, "provisorio"):
            H.exigir_precisos(result, 30.)

    def test_narrative_order_roles_unchanged_without_ai(self):
        self.selection = [dict(self.selection[0], trecho_id="payoff", inicio=12.5),
                          dict(self.selection[0], trecho_id="context", fim=11.5)]
        result = self.refinar()
        self.assertEqual([t["trecho_id"] for t in result["timeline"]], ["payoff", "context"])
        self.cpu.assert_called_once()

    def test_cpu_full_fallback_not_retranscribed(self):
        result = H.refinar_selecao(H.Passagem(self.gpu, "precise", True), self.selection, self.cache, self.cpu)
        self.cpu.assert_not_called()
        self.assertTrue(result["todos_processados_cpu"])
        self.assertFalse(result["todos_precisos"])
        self.assertEqual(result["audio_retranscrito_s"], 0.)

    def test_overlapping_sources_block_only_affected_cut(self):
        self.selection += [dict(self.selection[0], trecho_id="duplicate"),
                           dict(self.selection[0], trecho_id="independent", cut_id="2")]
        result = self.refinar()
        self.assertEqual([t["cut_id"] for t in result["timeline"]], ["2"])
        self.assertEqual(len(result["pendentes"]), 2)
        self.assertFalse(result["todos_precisos"])
        self.cpu.assert_called_once()

    def test_cpu_function_has_preciso_exact_parameters(self):
        import numpy as np
        model = Mock()
        model.transcribe.return_value = ([], None)
        H.cpu_array(model, np.zeros(10*H.SAMPLE_RATE), 2., 6.)
        self.assertEqual(len(model.transcribe.call_args.args[0]), 4*H.SAMPLE_RATE)
        self.assertEqual(model.transcribe.call_args.kwargs, dict(language="pt", beam_size=5,
            vad_filter=True, word_timestamps=True, condition_on_previous_text=True))

    def test_benchmark_gpu_failure_uses_cpu_and_restores_test_environment(self):
        from scripts import benchmark_hibrido_selecionado as B
        from scripts.benchmark_gpu_isolado import DLLS
        for name in DLLS:
            (self.folder / name).touch()
        original = dict(os.environ)
        args = ["benchmark", "--etapa", "gpu", "--runtime", str(self.folder),
                "--episodio", "fake.mp4", "--saida", str(self.folder)]
        with patch.dict(os.environ), patch.object(B.sys, "argv", args), patch.object(B, "salvar") as save:
            from modules import transcricao as T
            with patch.object(T, "_obter_modelo", side_effect=[RuntimeError("CUDA unavailable"), Mock()]), \
                    patch.object(T, "transcrever_video", return_value=self.gpu) as transcribe:
                B.main()
        payload = save.call_args.args[1]
        self.assertTrue(payload["fallback_cpu"])
        self.assertEqual(payload["timestamp_quality"], "precise_unverified")
        self.assertEqual(transcribe.call_args.kwargs["configuracao"], H.ConfigTranscricao())
        self.assertEqual(dict(os.environ), original)

    def test_cpu_unverified_is_blocked_until_gate(self):
        t = self.refinar()["timeline"][0]
        with self.assertRaisesRegex(ValueError, "nao validado"):
            H.exigir_precisos([t], 30.)
        ref = H.transcricao_trecho(self.gpu, t)
        approved = H.aprovar_temporal(t, ref, self.gpu)
        self.assertEqual(approved["timestamp_quality"], "precise")
        self.assertTrue(H.exigir_precisos([approved], 30.))
        self.assertEqual(t["timestamp_quality"], "precise_unverified")

    def test_failed_temporal_gate_never_promotes_cpu(self):
        t = self.refinar()["timeline"][0]
        ref = H.transcricao_trecho(self.gpu, t)
        for s in ref.segmentos:
            s.inicio += 1.
            s.fim += 1.
            for p in s.palavras:
                p.inicio += 1.
                p.fim += 1.
        rejected = H.aprovar_temporal(t, ref, self.gpu)
        self.assertFalse(rejected["gate_temporal"]["aprovado"])
        self.assertEqual(rejected["timestamp_quality"], "precise_unverified")
        with self.assertRaises(ValueError):
            H.exigir_precisos([rejected], 30.)


if __name__ == "__main__":
    unittest.main()
