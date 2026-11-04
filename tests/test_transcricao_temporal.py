"""No real cloud, models or GPU calls in automated temporal upgrade tests."""
from copy import deepcopy
from dataclasses import asdict, replace
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock, patch
import unittest

import numpy as np
import test_transcricao_v2 as fixtures
from modules import transcricao as T
from modules import transcricao_v2 as V
from modules import transcricao_temporal as Q
from modules import transcricao_benchmark as B
from modules import transcricao_alinhamento as A
from modules import transcricao_gemini as G


class TestTemporal(unittest.TestCase):
    def setUp(self):
        fixtures.TestTranscricaoV2.setUp(self)

    def transcrever(self, **kwargs):
        return T.transcrever_video(str(self.video), **kwargs)

    def fast(self):
        return V.ConfigTranscricao(modo="rapido", beam_size=1)

    def test_relative_offsets_are_recomposed_once(self):
        s = T.SegmentoTranscricao(.2, 1.2, "Ola", [T.PalavraTranscrita(.2, 1.2, "Ola")])
        r = Q.recompor_chunk([s], 20, 30, relativos=True, duracao_chunk=2)
        self.assertEqual(20.2, r[0].inicio)
        self.assertEqual(21.2, r[0].palavras[0].fim)
        self.assertEqual(.2, s.inicio)
        self.assertEqual(20.2, Q.recompor_chunk(r, 20, 30)[0].inicio)

    def test_relative_offset_requires_explicit_chunk_duration(self):
        with self.assertRaises(ValueError):
            Q.recompor_chunk([], 20, 30, relativos=True)

    def test_relative_timestamps_outside_chunk_are_rejected(self):
        with self.assertRaises(ValueError):
            Q.recompor_chunk([T.SegmentoTranscricao(0, 4, "fala")], 20, 30,
                             relativos=True, duracao_chunk=2)

    def test_lost_absolute_offset_is_rejected(self):
        s=T.SegmentoTranscricao(.2,1.2,"Ola",[T.PalavraTranscrita(.2,1.2,"Ola")])
        with self.assertRaises(ValueError):
            Q.recompor_chunk([s],20,30,duracao_chunk=2)

    def test_editor_snapshot_does_not_persist_before_confirmation(self):
        r=self.transcrever()
        with patch.object(T,"salvar_transcricao") as salvar:
            T.aplicar_texto_editado(r,r.texto_editavel().replace("Ola","Outra fala"),persistir=False)
        salvar.assert_not_called()
        self.assertTrue(r.word_timestamps_invalidated)

    def test_chunk_boundaries_cover_audio_without_rounding_holes(self):
        clips = Q.chunks_sem_vad(np.zeros(65*16000+137), 30)
        self.assertEqual([0, 30, 60], [c["start"] for c in clips])
        self.assertEqual((65*16000+137)/16000, clips[-1]["end"])
        self.assertEqual(clips[0]["end"], clips[1]["start"])

    def test_batch_sizes_one_two_four_eight(self):
        for batch in (1, 2, 4, 8):
            config = replace(self.fast(), engine="batched", batch_size=batch, condition_on_previous_text=False)
            with self.subTest(batch=batch), patch("faster_whisper.BatchedInferencePipeline", return_value=self.model):
                self.transcrever(configuracao=config, modo="rapido", usar_cache=False)
            self.assertEqual(batch, self.model.opcoes[-1]["batch_size"])
            self.assertFalse(self.model.opcoes[-1]["without_timestamps"])

    def test_vad_parameters_are_forwarded_without_changing_preciso(self):
        config = replace(self.fast(), vad_min_silence_ms=500, vad_speech_pad_ms=200,
                         vad_threshold=.35, vad_min_speech_ms=100)
        self.transcrever(configuracao=config, modo="rapido")
        self.assertEqual({"min_silence_duration_ms": 500, "speech_pad_ms": 200,
                          "threshold": .35, "min_speech_duration_ms": 100}, self.model.opcoes[-1]["vad_parameters"])

    def test_batched_without_vad_uses_explicit_absolute_clips(self):
        config = replace(self.fast(), engine="batched", batch_size=2, vad_filter=False,
                         condition_on_previous_text=False)
        with patch("faster_whisper.BatchedInferencePipeline", return_value=self.model), \
             patch("faster_whisper.audio.decode_audio", return_value=np.zeros(65*16000)):
            self.transcrever(configuracao=config, modo="rapido")
        self.assertFalse(self.model.opcoes[-1]["vad_filter"])
        self.assertEqual(60, self.model.opcoes[-1]["clip_timestamps"][-1]["start"])

    def test_timestamp_regression_overlap_and_audio_bounds(self):
        casos = [T.SegmentoTranscricao(3, 4, "um", [T.PalavraTranscrita(3,4,"um")]),
                 T.SegmentoTranscricao(2, 5, "dois", [T.PalavraTranscrita(2,6,"dois")])]
        codigos = {e["codigo"] for e in Q.validar_timestamps(casos, 5)}
        self.assertTrue({"segmento_regressivo", "sobreposicao_segmentos", "palavra_fora_segmento"} <= codigos)

    def test_confidence_has_local_signals_not_claimed_accuracy(self):
        s = T.SegmentoTranscricao(.2,1.2,"Ola",[T.PalavraTranscrita(.2,1.2,"Ola",.99)])
        self.assertEqual(100, Q.confianca_temporal(s,2)["timestamp_confidence"])
        s.palavras[0].fim = .21
        score = Q.confianca_temporal(s,2, vad_intervalos=[])
        self.assertLess(score["timestamp_confidence"],75)
        self.assertIn("palavras_comprimidas",score["sinais"])
        self.assertIn("conflito_vad",score["sinais"])
        self.assertFalse(score["precisao_absoluta_comprovada"])

    def test_detailed_mean_start_end_p95_and_worst(self):
        r = self.transcrever()
        c = deepcopy(r)
        c.segmentos[0].inicio += .1
        c.segmentos[0].fim += .2
        c.segmentos[0].palavras[0].inicio += .1
        c.segmentos[0].palavras[0].fim += .2
        q = B.comparar(r,c)
        self.assertAlmostEqual(.1,q["media_inicio_segmentos_s"])
        self.assertAlmostEqual(.2,q["media_fim_segmentos_s"])
        self.assertAlmostEqual(.15,q["media_palavras_s"])
        self.assertAlmostEqual(.2,q["p95_palavras_s"])
        self.assertAlmostEqual(.2,q["max_palavras_s"])
        self.assertTrue(q["aprovado"])

    def test_merged_segments_do_not_get_imaginary_boundaries(self):
        r = self.transcrever()
        r.segmentos.append(T.SegmentoTranscricao(1.4,1.8,"Tchau",[T.PalavraTranscrita(1.4,1.8,"Tchau")]))
        c = deepcopy(r)
        c.segmentos = [T.SegmentoTranscricao(.2,1.8,"Ola Tchau", [p for s in r.segmentos for p in s.palavras])]
        q = B.comparar(r,c)
        self.assertEqual(0,q["media_palavras_s"])
        self.assertGreater(q["max_segmentos_s"],.8)
        self.assertFalse(q["aprovado"])

    def test_silence_cannot_accept_hallucinated_text_without_words(self):
        r = self.transcrever()
        r.segmentos=[]
        c = deepcopy(r)
        c.segmentos=[T.SegmentoTranscricao(0,1,"Inventado")]
        self.assertFalse(B.comparar(r,c)["aprovado"])
        self.assertTrue(B.comparar(r,r)["aprovado"])

    def test_wer_lost_and_extra_words(self):
        r = self.transcrever()
        c = deepcopy(r)
        c.segmentos[0].texto="Outra"
        c.segmentos[0].palavras[0].texto="Outra"
        q = B.comparar(r,c)
        self.assertEqual(1,q["wer_aproximado"])
        self.assertEqual(1,q["palavras_perdidas"])
        self.assertEqual(1,q["palavras_extras"])

    def test_human_boundaries_are_optional_and_explicit(self):
        r = self.transcrever()
        self.assertFalse(B.comparar(r,r)["ground_truth_disponivel"])
        q = B.comparar(r,r,[{"segmento":0,"inicio_real":.3,"fim_real":1.1}])
        self.assertTrue(q["ground_truth_disponivel"])
        self.assertAlmostEqual(.1,q["fronteiras_humanas"][0]["erro_inicio_real_s"])

    def test_cache_distinguishes_all_temporal_parameters(self):
        base = self.fast()
        campos = [{"batch_size":2,"engine":"batched","condition_on_previous_text":False},
                  {"vad_speech_pad_ms":200}, {"alinhamento":"suspeitos"},
                  {"vad_filter":False}, {"timestamp_tokens":False}, {"chunk_seconds":10},
                  {"backend":"cuda"}]
        caminhos = {T.caminho_cache_transcricao(str(self.video), idioma="pt", configuracao=asdict(base))}
        for campo in campos:
            caminhos.add(T.caminho_cache_transcricao(str(self.video), idioma="pt",
                           configuracao=asdict(replace(base,**campo))))
        self.assertEqual(8,len(caminhos))

    def test_phase3b_preciso_cache_is_preserved(self):
        r=self.transcrever()
        dados=json.loads(Path(r.caminho_cache).read_text(encoding="utf-8"))
        chaves = ("backend","modo","engine","threads","beam_size","batch_size",
                  "condition_on_previous_text","word_timestamps","vad_filter","compute_type")
        dados["parametros"]={k:v for k,v in dados["parametros"].items() if k in chaves}
        Path(r.caminho_cache).write_text(json.dumps(dados),encoding="utf-8")
        with patch.object(T,"_obter_modelo",side_effect=AssertionError("Nao carregar modelo")):
            self.assertIsNotNone(T.buscar_transcricao_cache(str(self.video),idioma="pt"))

    def test_auto_rejects_old_simple_boolean_approval(self):
        with self.assertRaises(ValueError):
            V.salvar_auto(self.fast(),"small",{"melhor":{"aprovado":True,"configuracao":asdict(self.fast())}})

    def test_auto_requires_real_categories_and_thirty_percent_gain(self):
        fixtures.TestTranscricaoV2.calibrar(self)
        dados=json.loads(V.AUTO_PATH.read_text())
        for tipo in ("categorias", "ganho", "qualidade"):
            alterado=deepcopy(dados)
            a=alterado["perfis"][0]["benchmark"]["melhor"]["amostras"][0]
            if tipo=="categorias": a["categorias_verificadas"]=False
            if tipo=="ganho": a["tempo_quente_s"]=8
            if tipo=="qualidade": a["qualidade"][0]["p95_palavras_s"]=2.5
            V.AUTO_PATH.write_text(json.dumps(alterado),encoding="utf-8")
            self.assertIsNone(V.carregar_auto())

    def test_auto_hybrid_has_no_cloud_without_calibration(self):
        with patch.object(G,"_cliente_gemini",side_effect=AssertionError("Nao chamar nuvem")):
            self.assertEqual(V.ConfigTranscricao(), V.escolher_configuracao("auto","auto_hibrido"))
            self.transcrever(modo="auto",backend="auto_hibrido")

    def test_cloud_override_always_requires_consent(self):
        for backend in ("groq_whisper","gemini_transcribe"):
            config=V.ConfigTranscricao(backend=backend, engine="remote", modo="rapido")
            with self.assertRaises(ValueError):
                self.transcrever(modo="auto",configuracao=config)
            with self.assertRaises(ValueError):
                self.transcrever(modo="auto",backend="auto_local",configuracao=config,autorizar_nuvem=True)

    def test_local_hybrid_and_cloud_profiles_are_independent(self):
        fixtures.TestTranscricaoV2.calibrar(self)
        hibrido=replace(self.fast(),alinhamento="suspeitos")
        fixtures.TestTranscricaoV2.calibrar(self,hibrido)
        remoto=V.ConfigTranscricao(backend="gemini_transcribe",modo="rapido",engine="remote")
        fixtures.TestTranscricaoV2.calibrar(self,remoto)
        self.assertEqual(self.fast(),V.escolher_configuracao("auto","auto_local"))
        self.assertEqual(hibrido,V.escolher_configuracao("auto","auto_hibrido"))
        with self.assertRaises(ValueError): V.escolher_configuracao("auto","auto_nuvem")

    def test_batched_cannot_pretend_to_use_previous_text(self):
        with self.assertRaises(ValueError):
            replace(self.fast(),engine="batched",condition_on_previous_text=True).validar()

    def test_gpu_failure_falls_back_to_preciso_without_cloud(self):
        def model(*args,**kwargs):
            if kwargs.get("device")=="cuda":
                raise RuntimeError("cublas indisponivel")
            return self.model
        with patch.object(T,"_obter_modelo",side_effect=model), \
             patch.object(G,"_cliente_gemini",side_effect=AssertionError("Nuvem proibida")):
            r=self.transcrever(backend="cuda")
        self.assertEqual("cpu",r.backend)
        self.assertEqual("preciso",r.modo)
        self.assertTrue(r.alertas)

    def test_alignment_uses_supplied_tokens_never_transcribe(self):
        model=Mock()
        model.feature_extractor.return_value=np.zeros((80,3001),dtype=np.float32)
        model.hf_tokenizer=Mock()
        model.model.is_multilingual=True
        model.find_alignment.return_value=[[{"word":" Ola","tokens":[1],"start":.2,"end":1.2,"probability":.99}]]
        tokenizer=Mock()
        tokenizer.encode.return_value=[1]
        with patch("faster_whisper.tokenizer.Tokenizer",return_value=tokenizer):
            s=A.alinhar_texto(np.zeros(2*16000,dtype=np.float32),"Ola",model)
        self.assertEqual("Ola",s.texto)
        model.transcribe.assert_not_called()
        model.find_alignment.assert_called_once()

    def test_manual_alignment_only_changes_requested_segment(self):
        r=self.transcrever()
        r.segmentos[0].word_timestamps_invalidated=True
        r.segmentos[0].palavras=[]
        intacto=T.SegmentoTranscricao(1.4,1.8,"Tchau",[T.PalavraTranscrita(1.4,1.8,"Tchau")])
        r.segmentos.append(intacto)
        novo=T.SegmentoTranscricao(.3,1.1,"Ola",[T.PalavraTranscrita(.3,1.1,"Ola",.99)])
        with patch("faster_whisper.audio.decode_audio",return_value=np.zeros(2*16000)), \
             patch.object(A,"_alinhar_indice",return_value=novo) as alinhar:
            A.realinhar_trecho_editado(r,0,modelo=Mock())
        self.assertFalse(r.segmentos[0].word_timestamps_invalidated)
        self.assertIs(intacto,r.segmentos[1])
        self.assertEqual(0,alinhar.call_args.args[1])
        salvo=T.carregar_transcricao(Path(r.caminho_cache))
        self.assertEqual(0,salvo.alinhamento_info["trecho_editado"])
        self.assertEqual(0,salvo.alinhamento_info["retranscricoes"])

    def test_refinement_never_processes_all_audio(self):
        r=self.transcrever()
        r.duracao=10
        r.segmentos=[T.SegmentoTranscricao(i,i+.1,"Ola",[T.PalavraTranscrita(i,i+.01,"Ola",.1)])
                     for i in range(10)]
        with patch("faster_whisper.audio.decode_audio",return_value=np.zeros(10*16000)), \
             patch.object(A,"_alinhar_indice",side_effect=ValueError("sem melhora")):
            info=A.refinar_suspeitos(r,Mock(),replace(self.fast(),alinhamento="suspeitos"))
        self.assertLess(len(info["selecionados"]),len(r.segmentos))
        self.assertLessEqual(info["fracao_audio"],.35)
        self.assertEqual(0,info["retranscricoes"])

    def test_alignment_cancel_before_model_or_decode(self):
        r=self.transcrever()
        with patch("faster_whisper.audio.decode_audio",side_effect=AssertionError("Nao decodificar")), \
             self.assertRaisesRegex(RuntimeError,"cancelada"):
            A.realinhar_trecho_editado(r,0,cancelada_ref=SimpleNamespace(cancelada=True))

    def test_hybrid_never_silently_realigns_manually_edited_words(self):
        r=self.transcrever()
        r.segmentos[0].palavras=[]
        r.segmentos[0].word_timestamps_invalidated=True
        with patch("faster_whisper.audio.decode_audio",side_effect=AssertionError("Edicao exige pedido")):
            info=A.refinar_suspeitos(r,Mock(),replace(self.fast(),refino_score=100,alinhamento="suspeitos"))
        self.assertEqual([],info["selecionados"])

    def gemini_data(self):
        return {"steps":[{"type":"model_output","content":[{"type":"text","text":"Ola.","annotations":[
            {"type":"word_info","text":"Ola.","start_offset":"0.2s","end_offset":"1.2s"}]}]}]}

    def test_gemini_structured_word_offsets_and_derived_segment_bounds(self):
        segmentos=G.converter_resposta(self.gemini_data(),2)
        self.assertEqual((.2,1.2),(segmentos[0].inicio,segmentos[0].fim))
        self.assertEqual("Ola.",segmentos[0].palavras[0].texto)

    def test_gemini_annotations_can_use_utf8_byte_spans(self):
        dados=self.gemini_data()
        conteudo=dados["steps"][0]["content"][0]
        conteudo["text"]="Olá."
        anotacao=conteudo["annotations"][0]
        anotacao.pop("text")
        anotacao.update(start_index=0,end_index=len("Olá.".encode("utf-8")))
        self.assertEqual("Olá.",G.converter_resposta(dados,2)[0].palavras[0].texto)
        anotacao["end_index"]=3
        with self.assertRaises(ValueError): G.converter_resposta(dados,2)

    def test_gemini_rejects_missing_word_times_not_editorial_guess(self):
        dados=self.gemini_data()
        dados["steps"][0]["content"][0]["annotations"][0].pop("start_offset")
        with self.assertRaises(ValueError): G.converter_resposta(dados,2)
        with self.assertRaises(ValueError): G.converter_resposta({"outputs":[{"type":"text","text":"Ola"}]},2)

    def test_gemini_requires_consent_before_client(self):
        with patch.object(G,"_cliente_gemini",side_effect=AssertionError("Nao criar cliente")):
            with self.assertRaises(ValueError): self.transcrever(backend="gemini_transcribe")
            with self.assertRaises(ValueError): G.transcrever_gemini(str(self.video),"pt",False,None,None)

    def test_gemini_mocked_success_cleanup_cache_and_no_editorial_model(self):
        client=MagicMock()
        client.files.upload.return_value=SimpleNamespace(name="files/teste",uri="uri",state="ACTIVE")
        client.interactions.create.return_value=self.gemini_data()
        def preparar(source,dest,cancel):
            dest.write_bytes(b"FLAC")
            return 2
        with patch.object(G,"_cliente_gemini",return_value=client) as factory, \
             patch("modules.transcricao_groq.preparar_audio",side_effect=preparar):
            a=self.transcrever(backend="gemini_transcribe",autorizar_nuvem=True)
            b=self.transcrever(backend="gemini_transcribe",autorizar_nuvem=True)
        self.assertEqual(1,factory.call_count)
        self.assertTrue(b.cache_reutilizado)
        self.assertEqual("gemini_transcribe",a.backend)
        self.assertEqual(G.MODELO_GEMINI,client.interactions.create.call_args.kwargs["model"])
        self.assertEqual("verbatim",client.interactions.create.call_args.kwargs["generation_config"]["transcription_config"]["mode"]["type"])
        client.files.delete.assert_called_once_with(name="files/teste")
        client.close.assert_called_once()

    def test_gemini_cancel_after_upload_no_inference_no_save(self):
        cancel=SimpleNamespace(cancelada=False)
        client=MagicMock()
        def upload(**kwargs):
            cancel.cancelada=True
            return SimpleNamespace(name="files/teste",uri="uri",state="ACTIVE")
        client.files.upload.side_effect=upload
        def preparar(source,dest,ref):
            dest.write_bytes(b"FLAC")
            return 2
        with patch.object(G,"_cliente_gemini",return_value=client), \
             patch("modules.transcricao_groq.preparar_audio",side_effect=preparar), \
             self.assertRaisesRegex(RuntimeError,"cancelada"):
            self.transcrever(backend="gemini_transcribe",autorizar_nuvem=True,cancelada_ref=cancel)
        client.interactions.create.assert_not_called()
        client.files.delete.assert_called_once()
        self.assertFalse(self.model.opcoes)

    def test_cloud_failure_does_not_hide_model_unavailable_by_fake_timestamps(self):
        with patch.object(G,"_cliente_gemini",side_effect=RuntimeError("modelo indisponivel")):
            r=self.transcrever(backend="gemini_transcribe",autorizar_nuvem=True)
        self.assertEqual("cpu",r.backend)
        self.assertEqual("preciso",r.modo)

    def test_ui_gemini_denied_consent_never_queues(self):
        from ui.tela_transcricao import TelaTranscricao
        fake=SimpleNamespace(app=SimpleNamespace(estado={"video_path":str(self.video)}),
                             _opcoes_transcricao=lambda:{"modo":"auto","backend":"gemini_transcribe"})
        with patch("ui.tela_transcricao.fila_transcricao") as fila, \
             patch("tkinter.messagebox.askyesno",return_value=False):
            fila.ocupada=False
            TelaTranscricao._iniciar_transcricao(fake)
        fila.adicionar.assert_not_called()

    def tela_realinhar(self):
        import queue
        r=self.transcrever()
        texto=Mock()
        texto.get.return_value=r.texto_editavel().replace("Ola","Outra fala")
        fake=SimpleNamespace(app=SimpleNamespace(estado={"transcricao":r},mostrar_erro=Mock()),
            texto=texto,btn_salvar=Mock(),btn_realinhar=Mock(),btn_transcrever=Mock(),
            btn_cancelar=Mock(),combo_idioma=Mock(),combo_modo=Mock(),combo_backend=Mock(),
            _eventos_ui=queue.Queue())
        return fake,r

    def test_ui_alignment_cancelled_dialog_never_saves_or_queues(self):
        from ui.tela_transcricao import TelaTranscricao
        fake,r=self.tela_realinhar()
        with patch("ui.tela_transcricao.fila_transcricao") as fila, \
             patch("customtkinter.CTkInputDialog") as dialogo,patch.object(T,"salvar_transcricao") as salvar:
            fila.ocupada=False
            dialogo.return_value.get_input.return_value=None
            TelaTranscricao._realinhar_trecho(fake)
        fila.adicionar.assert_not_called()
        salvar.assert_not_called()
        self.assertFalse(r.word_timestamps_invalidated)

    def test_ui_alignment_queues_snapshot_of_only_requested_edited_segment(self):
        from ui.tela_transcricao import TelaTranscricao
        fake,r=self.tela_realinhar()
        with patch("ui.tela_transcricao.fila_transcricao") as fila, \
             patch("customtkinter.CTkInputDialog") as dialogo,patch.object(T,"salvar_transcricao") as salvar:
            fila.ocupada=False
            dialogo.return_value.get_input.return_value="1"
            TelaTranscricao._realinhar_trecho(fake)
        tarefa=fila.adicionar.call_args.args[0]
        self.assertEqual(0,tarefa.kwargs["indice"])
        self.assertIsNot(r,tarefa.kwargs["transcricao"])
        self.assertTrue(tarefa.kwargs["transcricao"].word_timestamps_invalidated)
        self.assertFalse(r.word_timestamps_invalidated)
        fake.texto.configure.assert_called_with(state="disabled")
        salvar.assert_not_called()


if __name__ == "__main__":
    unittest.main()
