import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from modules.performance import PerformanceProfiler, anotar, medir_operacao


class TestPerformance(unittest.TestCase):
    def test_nested_exclusive_time(self):
        with PerformanceProfiler("test") as p:
            with p.medir("parent"):
                with p.medir("child"):
                    pass
        parent, child = p.registros
        self.assertEqual(child["pai"], parent["id"])
        self.assertAlmostEqual(parent["exclusiva_s"] + child["duracao_s"], parent["duracao_s"])

    def test_inactive_preserves_identity(self):
        obj = object()
        self.assertIs(medir_operacao("test")(lambda: obj)(), obj)

    def test_errors_preserved_and_stack_restored(self):
        with PerformanceProfiler("test") as p:
            with self.assertRaisesRegex(ValueError, "original"):
                with p.medir("failed"):
                    raise ValueError("original")
            with p.medir("next"):
                pass
        self.assertEqual(p.registros[0]["status"], "erro")
        self.assertIsNone(p.registros[1]["pai"])
        self.assertNotIn("original", json.dumps(p.relatorio()))

    def test_cache_hit_and_miss(self):
        @medir_operacao("cache", cache=True)
        def read(value):
            anotar(modelo="small")
            return value
        with PerformanceProfiler("test") as p:
            self.assertEqual(read([]), [])
            self.assertIsNone(read(None))
        self.assertEqual([r["metadados"]["cache"] for r in p.registros], ["hit", "miss"])

    def test_thread_isolation(self):
        @medir_operacao("operation")
        def operation():
            pass
        with PerformanceProfiler("main") as p:
            thread = threading.Thread(target=operation)
            thread.start()
            thread.join()
            operation()
        self.assertEqual(len(p.registros), 1)

    def test_nested_sessions_restore_context(self):
        with PerformanceProfiler("outer") as outer:
            with PerformanceProfiler("inner") as inner:
                medir_operacao("inner")(lambda: None)()
            medir_operacao("outer")(lambda: None)()
        self.assertEqual(outer.registros[0]["operacao"], "outer")
        self.assertEqual(inner.registros[0]["operacao"], "inner")

    def test_local_report(self):
        with tempfile.TemporaryDirectory() as directory:
            with PerformanceProfiler("test", directory) as p:
                with p.medir("operation", backend="ffmpeg"):
                    pass
            data = json.loads(next(Path(directory).glob("*.json")).read_text())
            self.assertGreaterEqual(data["duracao_s"], 0)
            self.assertEqual(data["registros"][0]["metadados"]["backend"], "ffmpeg")

    def test_write_failure_does_not_change_result(self):
        with patch.object(Path, "mkdir", side_effect=OSError("disk")):
            with PerformanceProfiler("test", "unwritable") as p:
                value = 42
        self.assertEqual(value, 42)
        self.assertEqual(p.erro_gravacao, "OSError")

    def test_worker_result_and_report(self):
        from modules.tasks import TarefaRenderizacao
        result = object()
        completed = []
        with tempfile.TemporaryDirectory() as directory, patch("modules.tasks.DADOS_DIR", Path(directory)):
            task = TarefaRenderizacao("test", lambda **kwargs: result, callback_conclusao=completed.append)
            task._executar()
            self.assertIs(completed[0], result)
            self.assertEqual(len(list((Path(directory) / "performance").glob("*.json"))), 1)

    def test_worker_cancellation_preserved(self):
        from modules.tasks import TarefaRenderizacao
        completed = []
        with tempfile.TemporaryDirectory() as directory, patch("modules.tasks.DADOS_DIR", Path(directory)):
            task = TarefaRenderizacao("test", lambda **kwargs: None, callback_conclusao=completed.append)
            task.cancelar()
            task._executar()
        self.assertFalse(completed[0]["sucesso"])

    def test_worker_exception_callback_preserved(self):
        from modules.tasks import TarefaRenderizacao
        errors = []
        def fail(**kwargs):
            raise RuntimeError("unchanged")
        with tempfile.TemporaryDirectory() as directory, patch("modules.tasks.DADOS_DIR", Path(directory)):
            task = TarefaRenderizacao("test", fail, callback_erro=errors.append)
            task._executar()
        self.assertEqual(errors, ["unchanged"])
