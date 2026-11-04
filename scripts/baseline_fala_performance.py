"""Controlled Portuguese speech, synthesized locally using Windows SAPI."""
import json
import subprocess
from unittest.mock import patch
from baseline_performance import ROOT, PerformanceProfiler
from modules import transcricao as tr


def main():
    folder = ROOT / "temp" / "performance_3a"
    folder.mkdir(parents=True, exist_ok=True)
    wav = folder / "fala_pt.wav"
    text = ("Hoje vamos arrumar a casa. Quem deixou isso na sala? "
            "Eu nao acredito nisso. Ninguem vai assumir agora. "
            "Vamos ter que limpar tudo. Amanha tem mais trabalho. ") * 3
    # SAPI generates an artifact, not source code; no service or network is used.
    command = ("Add-Type -AssemblyName System.Speech; "
               "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
               "$s.SelectVoice('Microsoft Maria Desktop'); "
               f"$s.SetOutputToWaveFile('{str(wav).replace(chr(39), chr(39)*2)}'); "
               f"$s.Speak('{text}'); $s.Dispose()")
    subprocess.run(["powershell.exe", "-NoProfile", "-Command", command], check=True, timeout=60)
    model = next((tr.MODELOS_DIR / "models--Systran--faster-whisper-small" / "snapshots").glob("*"))
    with PerformanceProfiler("baseline_fala_pt", ROOT / "dados" / "performance") as profiler:
        with patch.object(tr, "TRANSCRICOES_DIR", folder / "transcricoes"):
            transcript = tr.transcrever_video(str(wav), modelo=str(model), usar_cache=False)
            tr.transcrever_video(str(wav), modelo=str(model), usar_cache=True)
    data = {**profiler.relatorio(), "amostra": "SAPI Maria pt-BR; fala sintetizada localmente",
            "duracao_audio_s": transcript.duracao, "segmentos": len(transcript.segmentos),
            "palavras": sum(len(s.palavras) for s in transcript.segmentos), "apis_reais": 0,
            "limite": "Nao representa dialogo real com musica, ruido ou multiplos personagens."}
    (folder / "baseline_fala.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(json.dumps(data, indent=2), flush=True)


if __name__ == "__main__":
    main()
