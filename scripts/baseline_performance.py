"""Phase 3A only: current presets, real FFmpeg, offline Whisper, no APIs."""
import os
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
import copy
from dataclasses import replace
import json
from pathlib import Path
import socket
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from modules.performance import PerformanceProfiler


def main():
    folder = ROOT / "temp" / "performance_3a"
    folder.mkdir(parents=True, exist_ok=True)
    profiler = PerformanceProfiler("baseline_3a", ROOT / "dados" / "performance")
    with patch.object(socket.socket, "connect", side_effect=AssertionError("Network prohibited")), profiler:
        with profiler.medir("importacao_modulos"):
            from _agencia_dados import gerar_fonte, transcricao, falas_remix, plano_remix
            from modules.agencia.pipeline import preparar_corte, renderizar_previa_agencia
            from modules.agencia.renderer import construir_comando, executar_ffmpeg
            from modules.agencia.qualidade import validar_frames, relatorio_exportacao, validar_exportacao, aprovado
            from modules.agencia.av import AjustesAgencia
            from modules.agencia.editorial import prompt_agencia
            from modules.perfis_visuais import obter_perfil
            from modules import transcricao as tr
            from modules.titulos_v2 import montar_contexto, selecionar_titulos
        with profiler.medir("fonte_sintetica", entrada_segundos=31):
            source = gerar_fonte(folder / "source.mp4", 640, 360, 31)
        transcript = transcricao(falas_remix(), source, "baseline", 31)
        plan = plano_remix(transcript.video_id)
        profile = obter_perfil(None)
        results = {"amostra": "31s testsrc2/sine; render e preview de 4s", "apis_reais": 0}
        cache = next((ROOT / "dados" / "transcricoes").glob("*.json"), None)
        if cache:
            with profiler.medir("cache_existente", arquivos_reutilizados=[str(cache)], cache="hit"):
                existing = tr.carregar_transcricao(cache)
            prompt_agencia(existing.segmentos, 5)
            results["cache_segmentos"] = len(existing.segmentos)
        with profiler.medir("titulos_validacao_local_sem_ia"):
            context = montar_contexto(plan, transcript)
            selecionar_titulos([{"texto": title.texto, "tipo": title.tipo} for title in plan.titulos], context)
        for label in ("preview_1", "preview_2"):
            with profiler.medir(label):
                result = renderizar_previa_agencia(str(source), copy.deepcopy(plan), transcript, profile,
                                                   folder / f"{label}.mp4", segundos=4, ajustes=AjustesAgencia())
            results[label] = {k: v for k, v in result.items() if k != "gate"}
            if not result.get("sucesso") or not result.get("valida"):
                raise RuntimeError(f"Preview failed: {result}")
        prep = preparar_corte(str(source), copy.deepcopy(plan), transcript, profile, ajustes=AjustesAgencia())
        results["gate_editorial"] = aprovado(prep.gate)
        # Benchmark excerpt only; the complete editorial timeline remains validated above.
        sections, remaining = [], 4.0
        for section in prep.timeline.trechos:
            if remaining <= 0:
                break
            duration = min(section.duracao, remaining)
            sections.append(replace(section, fim=section.inicio + duration, duracao_transicao=0))
            remaining -= duration
        timeline = replace(prep.timeline, trechos=sections)
        output = folder / "final_excerpt.mp4"
        cmd = construir_comando(str(source), str(output), timeline, prep.info, prep.enquadramento,
                                 profile, AjustesAgencia(), prep.titulo,
                                 [(b, a) for b, a in prep.legendas if b.inicio < timeline.duracao],
                                 folder / "final_graph.txt", prep.aplicar_tratamento)
        result = executar_ffmpeg(cmd, timeline.duracao)
        if not result["sucesso"]:
            raise RuntimeError(str(result))
        gate, _ = validar_frames(output, profile, video_fonte=source, timeline=timeline,
                                  pasta_frames=folder / "frames")
        report = relatorio_exportacao(output)
        results["gate_final"] = aprovado(gate + validar_exportacao(report, timeline.duracao))
        results["final_bytes"] = output.stat().st_size
        # A sine-only source measures setup/VAD, NOT representative speech inference.
        snapshots = list((tr.MODELOS_DIR / "models--Systran--faster-whisper-small" / "snapshots").glob("*"))
        if snapshots:
            with patch.object(tr, "TRANSCRICOES_DIR", folder / "transcricoes"):
                local = tr.transcrever_video(str(source), modelo=str(snapshots[0]), usar_cache=False)
                tr.transcrever_video(str(source), modelo=str(snapshots[0]), usar_cache=True)
            results["whisper_segmentos_sinteticos"] = len(local.segmentos)
            results["whisper_limitacao"] = "Sem fala real: tempo nao representa transcricao de episodio."
        else:
            results["whisper_limitacao"] = "Modelo local indisponivel."
    data = {**profiler.relatorio(), "resultados": results}
    (folder / "baseline.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    for record in profiler.registros:
        if record["pai"] is None:
            print(f'{record["operacao"]}: {record["duracao_s"]:.3f}s', flush=True)
    print(json.dumps(results, indent=2), flush=True)
    print(f"TOTAL: {profiler.duracao_s:.3f}s", flush=True)


if __name__ == "__main__":
    main()
