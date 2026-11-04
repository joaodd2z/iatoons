"""Local transcription configurations and persisted, machine-specific calibration."""
from dataclasses import asdict, dataclass
from functools import lru_cache
import hashlib
from importlib.metadata import version
import json
import os
import platform
from pathlib import Path
from datetime import datetime, timezone
import math

from modules.preset import DADOS_DIR

AUTO_PATH = DADOS_DIR / "transcricao_v2" / "auto_local.json"
VERSAO = 2


@dataclass(frozen=True)
class ConfigTranscricao:
    backend: str = "cpu"
    modo: str = "preciso"
    engine: str = "standard"
    threads: int = 6
    beam_size: int = 5
    batch_size: int = 1
    condition_on_previous_text: bool = True
    word_timestamps: bool = True
    vad_filter: bool = True
    compute_type: str = "int8"
    timestamp_tokens: bool = True
    vad_min_silence_ms: int | None = None
    vad_speech_pad_ms: int | None = None
    vad_threshold: float | None = None
    vad_min_speech_ms: int | None = None
    chunk_seconds: int = 30
    alinhamento: str = "nenhum"
    refino_score: int = 75
    refino_fracao_maxima: float = .35
    versao_regras: int = 2

    def validar(self):
        if (self.backend not in ("cpu", "cuda", "groq_whisper", "gemini_transcribe")
                or self.engine not in ("standard", "batched", "remote")
                or self.modo not in ("preciso", "rapido") or self.threads not in (4, 6, 8)
                or self.beam_size not in (1, 2, 3, 4, 5) or self.batch_size not in (1, 2, 4, 8)
                or not self.word_timestamps or self.compute_type not in ("int8", "int8_float32", "float32")
                or not 1 <= self.chunk_seconds <= 30 or self.alinhamento not in ("nenhum", "suspeitos")
                or not 0 <= self.refino_score <= 100 or not 0 < self.refino_fracao_maxima <= .5
                or self.versao_regras != VERSAO):
            raise ValueError("Configuracao de transcricao local invalida.")
        if self.backend == "cpu" and self.compute_type != "int8":
            raise ValueError("CPU preserva int8.")
        remoto = self.backend in ("groq_whisper", "gemini_transcribe")
        if remoto != (self.engine == "remote") or (remoto and self.alinhamento != "nenhum"):
            raise ValueError("Backend remoto exige engine remote e nao faz alinhamento local automatico.")
        if any(v is not None and (not isinstance(v, int) or not 0 <= v <= 5000) for v in (
                self.vad_min_silence_ms, self.vad_speech_pad_ms, self.vad_min_speech_ms)):
            raise ValueError("Parametro VAD invalido.")
        if self.vad_threshold is not None and not 0 < self.vad_threshold < 1:
            raise ValueError("Threshold VAD invalido.")
        if self.modo == "preciso" and self != ConfigTranscricao():
            raise ValueError("Preciso preserva a configuracao original.")
        if self.engine != "batched" and self.batch_size != 1:
            raise ValueError("Batch so se aplica ao pipeline batched.")
        if self.engine == "batched" and self.condition_on_previous_text:
            raise ValueError("Batched instalado nao suporta contexto entre chunks; use False explicitamente.")
        return self

    def parametros_vad(self):
        return {nome: valor for nome, valor in (
            ("min_silence_duration_ms", self.vad_min_silence_ms),
            ("speech_pad_ms", self.vad_speech_pad_ms), ("threshold", self.vad_threshold),
            ("min_speech_duration_ms", self.vad_min_speech_ms)) if valor is not None}


@lru_cache(maxsize=1)
def identidade_maquina():
    dados = {"cpu": os.environ.get("PROCESSOR_IDENTIFIER", platform.processor()),
             "threads": os.cpu_count(), "sistema": platform.system(), "arquitetura": platform.machine(),
             "faster_whisper": version("faster-whisper"), "ctranslate2": version("ctranslate2")}
    try:
        import subprocess
        gpu = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total",
                              "--format=csv,noheader"], capture_output=True, text=True, timeout=3)
        dados["gpu"] = gpu.stdout.strip() if gpu.returncode == 0 else "indisponivel"
    except (OSError, subprocess.TimeoutExpired):
        dados["gpu"] = "indisponivel"
    return hashlib.sha256(json.dumps(dados, sort_keys=True).encode()).hexdigest()


def elegivel_auto(config, relatorio):
    from modules.transcricao_benchmark import LIMITES, CATEGORIAS, VERSAO_BENCHMARK
    melhor = relatorio.get("melhor", {})
    if melhor.get("configuracao") != asdict(config) or not melhor.get("aprovado"):
        return False
    if config == ConfigTranscricao():
        return True
    if relatorio.get("versao_benchmark") != VERSAO_BENCHMARK:
        return False
    amostras = melhor.get("amostras", [])
    categorias = set()
    tempo = referencia = 0
    for amostra in amostras:
        if amostra.get("categorias_verificadas") and amostra.get("audio_real"):
            categorias.update(amostra.get("categorias", []))
        qs = amostra.get("qualidade", [])
        if len(qs) < 2 or not all(q.get("aprovado") and q.get("versao") == VERSAO_BENCHMARK for q in qs):
            return False
        for q in qs:
            if q.get("limites") != LIMITES or q.get("motivos"):
                return False
            if not q.get("silencio") and (q.get("palavras_pareadas", 0) <= 0 or q.get("segmentos_referencia", 0) <= 0):
                return False
            for nome, limite in LIMITES.items():
                valor = q.get(nome)
                if valor is None:
                    if not q.get("silencio"):
                        return False
                elif not isinstance(valor, (float, int)) or not math.isfinite(valor) or valor > limite + 1e-9 or valor < 0:
                    return False
        t, r = amostra.get("tempo_quente_s", 0), amostra.get("referencia_quente_s", 0)
        if not all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0 for v in (t, r)):
            return False
        tempo += t
        referencia += r
    return CATEGORIAS <= categorias and referencia > 0 and tempo <= referencia * .7


def carregar_auto(modelo="small", tipo="local"):
    try:
        dados = json.loads(AUTO_PATH.read_text(encoding="utf-8"))
        if dados["versao"] != VERSAO or dados["maquina"] != identidade_maquina():
            return None
        opcoes = []
        for perfil in dados.get("perfis", [dados]):
            if perfil.get("modelo") != modelo or not perfil.get("qualidade_aprovada"):
                continue
            config = ConfigTranscricao(**perfil["configuracao"]).validar()
            if not elegivel_auto(config, perfil["benchmark"]):
                continue
            remoto = config.backend in ("groq_whisper", "gemini_transcribe")
            if tipo == "local" and (remoto or config.alinhamento != "nenhum"):
                continue
            if tipo == "hibrido" and (remoto or config.alinhamento != "suspeitos"):
                continue
            amostras = perfil["benchmark"]["melhor"].get("amostras", [])
            razao = (sum(a["tempo_quente_s"] for a in amostras) /
                     sum(a["referencia_quente_s"] for a in amostras)) if amostras else 1
            opcoes.append((razao, config))
        return min(opcoes, key=lambda p: p[0])[1] if opcoes else None
    except (OSError, ValueError, TypeError, KeyError):
        return None


def salvar_auto(config, modelo, relatorio):
    config.validar()
    if not elegivel_auto(config, relatorio):
        raise ValueError("AUTO exige benchmark aprovado para a configuracao exata.")
    dados = {"versao": VERSAO, "maquina": identidade_maquina(), "modelo": modelo,
             "configuracao": asdict(config), "qualidade_aprovada": True, "benchmark": relatorio,
             "data": datetime.now(timezone.utc).isoformat()}
    perfis = []
    try:
        anterior = json.loads(AUTO_PATH.read_text(encoding="utf-8"))
        if anterior.get("versao") == VERSAO and anterior.get("maquina") == dados["maquina"]:
            perfis = anterior.get("perfis", [anterior])
    except (OSError, ValueError):
        pass
    dados["perfis"] = [p for p in perfis if p.get("modelo") != modelo
                        or p.get("configuracao") != asdict(config)] + [dados.copy()]
    AUTO_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporario = AUTO_PATH.with_suffix(".tmp")
    temporario.write_text(json.dumps(dados, indent=2), encoding="utf-8")
    temporario.replace(AUTO_PATH)


def escolher_configuracao(modo="preciso", backend="auto_local", modelo="small", autorizar_nuvem=False):
    if modo not in ("auto", "rapido", "preciso"):
        raise ValueError("Modo invalido: auto, rapido ou preciso.")
    if backend not in ("cpu", "cuda", "auto_local", "auto_hibrido", "auto_nuvem", "groq_whisper", "gemini_transcribe"):
        raise ValueError("Backend de transcricao invalido.")
    if backend in ("groq_whisper", "gemini_transcribe", "auto_nuvem") and not autorizar_nuvem:
        raise ValueError("Envio de audio externo exige autorizacao explicita.")
    if backend in ("groq_whisper", "gemini_transcribe"):
        return None
    if backend == "cuda":
        return ConfigTranscricao(backend="cuda", modo="rapido").validar()
    if modo == "preciso":
        return ConfigTranscricao()
    tipo = "hibrido" if backend == "auto_hibrido" else "nuvem" if backend == "auto_nuvem" else "local"
    validada = carregar_auto(modelo, tipo)
    if backend == "cpu" and validada is not None and validada.backend != "cpu":
        validada = None
    if validada is not None:
        return validada
    # Without a validated calibration, neither mode risks subtitle alignment.
    return ConfigTranscricao()
