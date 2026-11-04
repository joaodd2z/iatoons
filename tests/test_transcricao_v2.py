from dataclasses import asdict, replace
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from modules import transcricao as T
from modules import transcricao_v2 as V
from modules import transcricao_groq as G
from modules.transcricao_benchmark import comparar
OBTER_MODELO_REAL = T._obter_modelo


class ModeloFalso:
    def __init__(self):
        self.opcoes = []

    def transcribe(self, path, **kwargs):
        self.opcoes.append(kwargs)
        word = SimpleNamespace(start=.2, end=1.2, word=" Ola", probability=.99)
        return iter([SimpleNamespace(start=.2, end=1.2, text="Ola", words=[word])]), \
               SimpleNamespace(duration=2.0, language="pt", language_probability=1)


class TestTranscricaoV2(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name)
        self.video = self.folder / "input.mp4"
        self.video.write_bytes(b"fake")
        self.model = ModeloFalso()
        for target, value in (("TRANSCRICOES_DIR", self.folder / "cache"), ("_obter_modelo", self.model)):
            p = patch.object(T, target, return_value=value) if target == "_obter_modelo" else patch.object(T, target, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(V, "AUTO_PATH", self.folder / "auto.json")
        p.start()
        self.addCleanup(p.stop)
        for method in ("connect", "connect_ex"):
            p = patch("socket.socket."+method, side_effect=AssertionError("Real API prohibited"))
            p.start()
            self.addCleanup(p.stop)

    def transcrever(self, **kwargs):
        return T.transcrever_video(str(self.video), **kwargs)

    def fast(self):
        return V.ConfigTranscricao(modo="rapido", beam_size=1)

    def calibrar(self, config=None):
        config = config or self.fast()
        from modules.transcricao_benchmark import LIMITES, CATEGORIAS, VERSAO_BENCHMARK
        qualidade = {**{k: 0 for k in LIMITES}, "versao": VERSAO_BENCHMARK,
                     "limites": LIMITES, "aprovado": True, "motivos": [],
                     "palavras_pareadas": 10, "segmentos_referencia": 2}
        V.salvar_auto(config, "small", {"versao_benchmark": VERSAO_BENCHMARK,
            "melhor": {"aprovado":True, "configuracao":asdict(config), "amostras": [{
                "audio_real": True, "categorias_verificadas": True, "categorias": sorted(CATEGORIAS),
                "tempo_quente_s": 6, "referencia_quente_s": 10, "qualidade": [qualidade, qualidade]}]}})

    def test_preciso_original(self):
        result = self.transcrever(modo="preciso")
        self.assertEqual(self.model.opcoes[0], {"language":"pt", "beam_size":5,
            "word_timestamps":True,"vad_filter":True,"condition_on_previous_text":True})
        self.assertEqual(result.parametros, asdict(V.ConfigTranscricao()))
        self.assertEqual(result.segmentos[0].palavras[0].inicio, .2)

    def test_rapido_validado(self):
        self.calibrar()
        result = self.transcrever(modo="rapido")
        self.assertEqual(self.model.opcoes[0]["beam_size"], 1)
        self.assertTrue(self.model.opcoes[0]["word_timestamps"])
        self.assertEqual(result.modo, "rapido")

    def test_auto_reuses_benchmark_without_benchmarking(self):
        self.calibrar()
        self.assertEqual(V.escolher_configuracao("auto"), self.fast())
        self.transcrever(modo="auto")
        self.assertEqual(len(self.model.opcoes), 1)
        self.assertEqual(self.model.opcoes[0]["beam_size"], 1)

    def test_auto_without_profile_is_preciso(self):
        self.assertEqual(V.escolher_configuracao("auto"), V.ConfigTranscricao())

    def test_profile_different_machine_rejected(self):
        self.calibrar()
        with patch.object(V, "identidade_maquina", return_value="other-machine"):
            self.assertIsNone(V.carregar_auto())

    def test_profile_different_model_rejected(self):
        self.calibrar()
        self.assertIsNone(V.carregar_auto("medium"))

    def test_bad_profile_rejected(self):
        V.AUTO_PATH.write_text('{"versao":0}', encoding="utf-8")
        self.assertIsNone(V.carregar_auto())

    def test_unapproved_profile_not_saved(self):
        with self.assertRaises(ValueError):
            V.salvar_auto(self.fast(), "small", {"melhor":{"aprovado":False}})

    def test_invalid_config_cannot_remove_word_times(self):
        with self.assertRaises(ValueError):
            replace(self.fast(), word_timestamps=False).validar()

    def test_cache_differentiates_configuration(self):
        self.calibrar()
        precise = self.transcrever(modo="preciso")
        fast = self.transcrever(modo="rapido")
        self.assertNotEqual(precise.caminho_cache, fast.caminho_cache)
        self.assertEqual(precise.video_id, fast.video_id)
        self.assertTrue(T.transcricao_corresponde_origem(fast))
        self.transcrever(modo="rapido")
        self.assertEqual(len(self.model.opcoes), 2)

    def test_cache_hit_never_loads_model(self):
        self.transcrever()
        with patch.object(T, "_obter_modelo", side_effect=AssertionError("Must not load")):
            cached = self.transcrever()
        self.assertTrue(cached.cache_reutilizado)

    def test_auto_reuses_existing_preciso_even_when_fast_validated(self):
        self.transcrever()
        self.calibrar()
        result = self.transcrever(modo="auto")
        self.assertEqual(result.modo, "preciso")
        self.assertEqual(len(self.model.opcoes), 1)

    def test_legacy_cache_preserved(self):
        result = self.transcrever()
        path = Path(result.caminho_cache)
        data = json.loads(path.read_text())
        for key in ("backend", "modo", "parametros", "tempo_transcricao_s", "tempo_ultima_operacao_s", "alertas"):
            data.pop(key)
        for s in data["segmentos"]:
            s.pop("word_timestamps_invalidated")
        path.write_text(json.dumps(data), encoding="utf-8")
        self.assertIsNotNone(T.buscar_transcricao_cache(str(self.video), idioma="pt"))
        self.calibrar()
        self.assertIsNone(T.buscar_transcricao_cache(str(self.video), idioma="pt", modo="rapido"))

    def test_cache_rejects_modified_source(self):
        self.transcrever()
        self.video.write_bytes(b"different video")
        self.assertIsNone(T.buscar_transcricao_cache(str(self.video), idioma="pt"))

    def test_cancel_before_start(self):
        with self.assertRaisesRegex(RuntimeError, "cancelada"):
            self.transcrever(cancelada_ref=SimpleNamespace(cancelada=True))
        self.assertEqual(self.model.opcoes, [])

    def test_cancel_during_progress_does_not_save_or_fallback(self):
        self.calibrar()
        cancel = SimpleNamespace(cancelada=False)
        def progress(pct, message):
            if pct > 10:
                cancel.cancelada=True
        with self.assertRaisesRegex(RuntimeError, "cancelada"):
            self.transcrever(modo="auto", cancelada_ref=cancel, progresso_cb=progress)
        self.assertFalse(list((self.folder / "cache").glob("*.json")))
        self.assertEqual(len(self.model.opcoes), 1)

    def test_progress_reaches_100(self):
        progress=[]
        self.transcrever(progresso_cb=lambda pct,msg:progress.append(pct))
        self.assertEqual(progress[-1],100)

    def test_auto_failure_falls_back_locally_only(self):
        self.calibrar()
        original = T._transcrever_configurado
        def execute(*args):
            if args[-1].modo == "rapido":
                raise RuntimeError("fast failed")
            return original(*args)
        with patch.object(T, "_transcrever_configurado", side_effect=execute), \
             patch.object(G, "_cliente_groq", side_effect=AssertionError("No cloud")):
            result=self.transcrever(modo="auto", usar_cache=False)
        self.assertEqual(result.modo,"preciso")
        self.assertTrue(result.alertas)

    def test_batched_official_configuration(self):
        config=replace(self.fast(), engine="batched", batch_size=4, condition_on_previous_text=False)
        with patch("faster_whisper.BatchedInferencePipeline", return_value=self.model) as factory:
            self.transcrever(configuracao=config, modo="rapido")
        factory.assert_called_once_with(model=self.model)
        self.assertEqual(self.model.opcoes[0]["batch_size"],4)

    def test_manual_edit_only_invalidates_changed_segment(self):
        result=self.transcrever()
        result.segmentos.append(T.SegmentoTranscricao(1.3,1.8,"Tchau",[T.PalavraTranscrita(1.3,1.8," Tchau")]))
        edit=result.texto_editavel().replace("Ola", "Novo texto")
        T.aplicar_texto_editado(result,edit)
        restored=T.carregar_transcricao(Path(result.caminho_cache))
        self.assertEqual(restored.segmentos[0].palavras,[])
        self.assertTrue(restored.segmentos[0].word_timestamps_invalidated)
        self.assertFalse(restored.segmentos[1].word_timestamps_invalidated)
        self.assertEqual(len(restored.segmentos[1].palavras),1)

    def test_manual_edit_times_invalidates_words(self):
        result=self.transcrever()
        T.aplicar_texto_editado(result,result.texto_editavel().replace("00:00:00.200","00:00:00.300"))
        self.assertTrue(result.word_timestamps_invalidated)
        self.assertEqual(result.segmentos[0].palavras,[])

    def test_segment_subtitles_used_for_edit(self):
        from modules.legendas import gerar_blocos_legenda
        result=self.transcrever()
        T.aplicar_texto_editado(result,result.texto_editavel().replace("Ola","Novo texto"))
        legendas=gerar_blocos_legenda(result)
        self.assertEqual((legendas[0].inicio,legendas[0].fim),(.2,1.2))
        self.assertEqual(legendas[0].texto,"Novo texto")

    def test_groq_requires_consent_before_client(self):
        with patch.object(G,"_cliente_groq", side_effect=AssertionError("No consent")):
            with self.assertRaises(ValueError):
                self.transcrever(backend="groq_whisper")
            with self.assertRaises(ValueError):
                G.transcrever_groq(str(self.video),"pt",False,None,None)

    def test_auto_cloud_requires_consent_and_stays_local_without_cloud_benchmark(self):
        with self.assertRaises(ValueError):
            V.escolher_configuracao("auto","auto_nuvem")
        with patch.object(G,"_cliente_groq", side_effect=AssertionError("No cloud benchmark")):
            self.transcrever(modo="auto",backend="auto_nuvem",autorizar_nuvem=True)

    def test_groq_failure_fallback_without_secrets_in_alert(self):
        with patch.object(G,"_cliente_groq", side_effect=RuntimeError("secret credential")):
            result=self.transcrever(backend="groq_whisper",autorizar_nuvem=True)
        self.assertEqual(result.backend,"cpu")
        self.assertNotIn("secret",str(result.alertas))

    def test_groq_failure_can_disable_fallback(self):
        with patch.object(G,"_cliente_groq", side_effect=RuntimeError("failed")):
            with self.assertRaises(RuntimeError):
                self.transcrever(backend="groq_whisper",autorizar_nuvem=True,fallback_local=False)

    def test_groq_mocked_success_and_cache(self):
        client=MagicMock()
        client.audio.transcriptions.create.return_value={"duration":2,"language":"pt","text":"Ola",
            "segments":[{"start":.2,"end":1.2,"text":"Ola"}],
            "words":[{"start":.2,"end":1.2,"word":"Ola"}]}
        def prepare(source, destination, cancel):
            destination.write_bytes(b"FLAC")
            return 2
        with patch.object(G,"_cliente_groq",return_value=client) as factory, patch.object(G,"preparar_audio",side_effect=prepare):
            first=self.transcrever(backend="groq_whisper",autorizar_nuvem=True)
            second=self.transcrever(backend="groq_whisper",autorizar_nuvem=True)
        self.assertEqual(factory.call_count,1)
        self.assertEqual(first.backend,"groq_whisper")
        self.assertTrue(second.cache_reutilizado)
        self.assertIsNone(T.buscar_transcricao_cache(str(self.video),idioma="pt"))
        self.assertEqual(client.audio.transcriptions.create.call_args.kwargs["timestamp_granularities"],["word","segment"])
        self.assertTrue(client.audio.transcriptions.create.call_args.kwargs["file"].closed)

    def test_groq_missing_words_rejected(self):
        with self.assertRaises(ValueError):
            G.converter_resposta({"duration":2,"segments":[{"start":0,"end":1,"text":"speech"}]})

    def test_comparison_rejects_large_shift(self):
        result=self.transcrever()
        changed=T.carregar_transcricao(Path(result.caminho_cache))
        changed.segmentos[0].palavras[0].inicio += .5
        comparison=comparar(result,changed)
        self.assertFalse(comparison["aprovado"])

    def test_comparison_detects_lost_and_extra_words(self):
        result=self.transcrever()
        changed=T.carregar_transcricao(Path(result.caminho_cache))
        changed.segmentos[0].palavras[0].texto=" Outra"
        comparison=comparar(result,changed)
        self.assertEqual(comparison["palavras_perdidas"],1)
        self.assertEqual(comparison["palavras_extras"],1)
        self.assertFalse(comparison["aprovado"])

    def test_comparison_identical_passes(self):
        result=self.transcrever()
        self.assertTrue(comparar(result,result)["aprovado"])

    def test_invalid_profile_thread_count_rejected(self):
        self.calibrar()
        dados=json.loads(V.AUTO_PATH.read_text())
        dados["configuracao"]["threads"]=999
        dados["perfis"][0]["configuracao"]["threads"]=999
        V.AUTO_PATH.write_text(json.dumps(dados),encoding="utf-8")
        self.assertIsNone(V.carregar_auto())

    def test_rapido_without_validation_uses_preciso_and_warns(self):
        result=self.transcrever(modo="rapido")
        self.assertEqual(result.modo,"preciso")
        self.assertTrue(result.alertas)

    def test_groq_cancel_after_request_does_not_save_or_fallback(self):
        cancel=SimpleNamespace(cancelada=False)
        client=MagicMock()
        def request(**kwargs):
            cancel.cancelada=True
            return {}
        client.audio.transcriptions.create.side_effect=request
        def prepare(source,dest,ref):
            dest.write_bytes(b"FLAC")
            return 2
        with patch.object(G,"_cliente_groq",return_value=client), patch.object(G,"preparar_audio",side_effect=prepare):
            with self.assertRaisesRegex(RuntimeError,"cancelada"):
                self.transcrever(backend="groq_whisper",autorizar_nuvem=True,cancelada_ref=cancel)
        self.assertFalse(self.model.opcoes)
        self.assertFalse(list((self.folder / "cache").glob("*.json")))
        client.close.assert_called_once()

    def test_groq_large_audio_not_uploaded(self):
        client=MagicMock()
        def prepare(source,dest,ref):
            with dest.open("wb") as stream:
                stream.truncate(25*1024*1024+1)
            return 2
        with patch.object(G,"_cliente_groq",return_value=client), patch.object(G,"preparar_audio",side_effect=prepare):
            with self.assertRaises(ValueError):
                self.transcrever(backend="groq_whisper",autorizar_nuvem=True,fallback_local=False)
        client.audio.transcriptions.create.assert_not_called()

    def test_ui_denied_consent_never_starts_task(self):
        from ui.tela_transcricao import TelaTranscricao
        fake=SimpleNamespace(app=SimpleNamespace(estado={"video_path":str(self.video)}),
            _opcoes_transcricao=lambda:{"modo":"auto","backend":"groq_whisper"})
        with patch("ui.tela_transcricao.fila_transcricao") as fila, \
             patch("tkinter.messagebox.askyesno",return_value=False):
            fila.ocupada=False
            TelaTranscricao._iniciar_transcricao(fake)
        fila.adicionar.assert_not_called()

    def test_ui_callbacks_only_enqueue_events(self):
        import queue
        from ui.tela_transcricao import TelaTranscricao
        fake=SimpleNamespace(app=SimpleNamespace(estado={"video_path":str(self.video)},atualizar_estado=MagicMock()),
            _opcoes_transcricao=lambda:{"modo":"auto","backend":"auto_local"},
            _idioma_codigo=lambda:"pt", _eventos_ui=queue.Queue(), btn_transcrever=MagicMock(),
            btn_cancelar=MagicMock(),combo_idioma=MagicMock(),combo_modo=MagicMock(),
            combo_backend=MagicMock(),barra=MagicMock())
        fake.combo_modo.get.return_value="Auto"
        with patch("ui.tela_transcricao.fila_transcricao") as fila:
            fila.ocupada=False
            TelaTranscricao._iniciar_transcricao(fake)
        fake._tarefa.callback_progresso(50,"progress")
        self.assertEqual(fake._eventos_ui.get_nowait()[0],"progresso")
        self.assertFalse(fake._tarefa.kwargs["autorizar_nuvem"])

    def test_models_different_threads_are_not_silently_reused(self):
        with patch.object(T,"_modelos",{}),patch.object(T,"MODELOS_DIR",self.folder / "modelos"), \
             patch("faster_whisper.WhisperModel",side_effect=[object(),object()]) as constructor:
            six=OBTER_MODELO_REAL("small",threads=6)
            same=OBTER_MODELO_REAL("small",threads=6)
            four=OBTER_MODELO_REAL("small",threads=4)
        self.assertIs(six,same)
        self.assertIsNot(six,four)
        self.assertEqual([c.kwargs["cpu_threads"] for c in constructor.call_args_list],[6,4])

    def test_sdk_error_is_safe_even_without_fallback(self):
        client=MagicMock()
        client.audio.transcriptions.create.side_effect=RuntimeError("private credential and payload")
        def prepare(source,dest,ref):
            dest.write_bytes(b"FLAC")
            return 2
        with patch.object(G,"_cliente_groq",return_value=client),patch.object(G,"preparar_audio",side_effect=prepare):
            with self.assertRaises(RuntimeError) as raised:
                self.transcrever(backend="groq_whisper",autorizar_nuvem=True,fallback_local=False)
        self.assertNotIn("private",str(raised.exception))

    def test_comparison_does_not_invent_precision_for_reference_zero_words(self):
        result=self.transcrever()
        result.segmentos[0].palavras[0].fim=.2
        self.assertTrue(comparar(result,result)["aprovado"])
