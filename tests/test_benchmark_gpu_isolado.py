import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from scripts import benchmark_gpu_isolado as B
from modules import transcricao as T
from modules.transcricao_v2 import ConfigTranscricao


class TestGpuIsolada(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        for name in B.DLLS:
            (self.folder / name).touch()
        for method in ("connect", "connect_ex"):
            p = patch("socket.socket." + method, side_effect=AssertionError("API forbidden"))
            p.start()
            self.addCleanup(p.stop)

    def test_runtime_explicit_complete_and_temporary(self):
        original = dict(os.environ)
        env = B.ambiente_filho(self.folder)
        self.assertTrue(env["PATH"].startswith(str(self.folder.resolve()) + os.pathsep))
        self.assertEqual(dict(os.environ), original)

    def test_base_environment_not_mutated(self):
        base = {"PATH": "original", "CUSTOM": "kept"}
        env = B.ambiente_filho(self.folder, base)
        self.assertEqual(base, {"PATH": "original", "CUSTOM": "kept"})
        self.assertEqual(env["CUSTOM"], "kept")

    def test_missing_runtime_rejected_no_implicit_ollama(self):
        (self.folder / B.DLLS[0]).unlink()
        with self.assertRaisesRegex(ValueError, "incompleto"):
            B.ambiente_filho(self.folder)

    def test_cpu_environment_does_not_inject_gpu_path(self):
        env = B.ambiente_filho(base={"PATH": "cpu-only"})
        self.assertEqual(env["PATH"], "cpu-only")
        self.assertEqual(env["HF_HUB_OFFLINE"], "1")

    def test_gpu_process_error_is_preserved_without_retry(self):
        args = SimpleNamespace(compute="int8_float32", manifesto=self.folder / "manifest.json",
                               saida=self.folder)
        with patch.object(B, "gpu_memoria", return_value=1700), \
                patch.object(B.subprocess, "run", return_value=SimpleNamespace(returncode=1)) as run:
            result = B.executar_filho(args, "cuda", {"PATH": "isolated"})
        self.assertEqual(result["returncode"], 1)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.kwargs["env"], {"PATH": "isolated"})

    def test_cpu_preciso_defaults_unchanged(self):
        config = ConfigTranscricao().validar()
        self.assertEqual((config.backend, config.compute_type, config.modo, config.beam_size,
                          config.threads, config.word_timestamps, config.vad_filter,
                          config.condition_on_previous_text),
                         ("cpu", "int8", "preciso", 5, 6, True, True, True))
        self.assertEqual((T.DEVICE_PADRAO, T.COMPUTE_TYPE_PADRAO), ("cpu", "int8"))


if __name__ == "__main__":
    unittest.main()
