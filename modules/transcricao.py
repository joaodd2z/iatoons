"""Transcrição local com faster-whisper e cache persistente por vídeo."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import threading
from time import perf_counter
from typing import Callable, Optional

from modules.importacao import hhmmss_para_segundos, segundos_para_hhmmss
from modules.preset import DADOS_DIR
from modules.performance import medir_operacao, medir_chamada, anotar
from modules.transcricao_v2 import ConfigTranscricao, escolher_configuracao


MODELO_PADRAO = "small"
DEVICE_PADRAO = "cpu"
COMPUTE_TYPE_PADRAO = "int8"
TRANSCRICOES_DIR = DADOS_DIR / "transcricoes"
MODELOS_DIR = DADOS_DIR / "modelos_whisper"


@dataclass
class PalavraTranscrita:
    inicio: float
    fim: float
    texto: str
    probabilidade: float = 0.0


@dataclass
class SegmentoTranscricao:
    inicio: float
    fim: float
    texto: str
    palavras: list[PalavraTranscrita] = field(default_factory=list)
    word_timestamps_invalidated: bool = False
    timestamp_confidence: int | None = None
    timestamp_sinais: list[str] = field(default_factory=list)
    source_seek: int | None = None


@dataclass
class Transcricao:
    video_path: str
    video_id: str
    modelo: str
    idioma_solicitado: Optional[str]
    idioma_detectado: str
    probabilidade_idioma: float
    duracao: float
    criada_em: str
    segmentos: list[SegmentoTranscricao] = field(default_factory=list)
    caminho_cache: str = ""
    backend: str = "cpu"
    modo: str = "preciso"
    parametros: dict = field(default_factory=dict)
    tempo_transcricao_s: float = 0.0
    tempo_ultima_operacao_s: float = 0.0
    alertas: list[str] = field(default_factory=list)
    diagnostico_temporal: list[dict] = field(default_factory=list)
    alinhamento_info: dict = field(default_factory=dict)
    tempos_backend: dict = field(default_factory=dict)

    @property
    def word_timestamps_invalidated(self):
        return any(s.word_timestamps_invalidated for s in self.segmentos)

    def texto_editavel(self) -> str:
        linhas = []
        for segmento in self.segmentos:
            inicio = segundos_para_hhmmss(segmento.inicio)
            fim = segundos_para_hhmmss(segmento.fim)
            linhas.append(f"[{inicio} --> {fim}] {segmento.texto.strip()}")
        return "\n".join(linhas)

    def texto_corrido(self) -> str:
        return " ".join(s.texto.strip() for s in self.segmentos if s.texto.strip())


_modelos: dict[tuple[str, str, str, int], object] = {}
_modelo_lock = threading.Lock()


def _identidade_video(caminho_video: str, modelo: str, idioma: Optional[str]) -> str:
    caminho = Path(caminho_video).resolve()
    stat = caminho.stat()
    origem = f"{caminho}|{stat.st_size}|{stat.st_mtime_ns}|{modelo}|{idioma or 'auto'}"
    return hashlib.sha256(origem.encode("utf-8")).hexdigest()


def caminho_cache_transcricao(caminho_video: str, modelo: str = MODELO_PADRAO,
                              idioma: Optional[str] = None, *, configuracao=None) -> Path:
    chave = _identidade_video(caminho_video, modelo, idioma)
    parametros = configuracao or asdict(ConfigTranscricao())
    if parametros != asdict(ConfigTranscricao()):
        chave = hashlib.sha256((chave + json.dumps(parametros, sort_keys=True)).encode()).hexdigest()
    return TRANSCRICOES_DIR / f"{chave}.json"


def transcricao_corresponde_origem(transcricao: Transcricao) -> bool:
    """Confirma identidade por caminho, tamanho, data, modelo e idioma."""
    caminho = Path(transcricao.video_path)
    if not caminho.is_file():
        return False
    try:
        return transcricao.video_id == _identidade_video(
            str(caminho), transcricao.modelo, transcricao.idioma_solicitado
        )
    except OSError:
        return False


@medir_operacao("carregamento_modelo", backend="cpu/int8")
def _obter_modelo(modelo: str = MODELO_PADRAO, threads: int = 6, *, device="cpu", compute_type="int8"):
    chave = (modelo, device, compute_type, threads)
    with _modelo_lock:
        anotar(modelo=modelo, backend=f"{device}/{compute_type}", cache="hit" if chave in _modelos else "miss")
        if chave not in _modelos:
            from faster_whisper import WhisperModel

            MODELOS_DIR.mkdir(parents=True, exist_ok=True)
            try:
                _modelos[chave] = WhisperModel(
                    modelo,
                    device=device,
                    compute_type=compute_type,
                    cpu_threads=threads,
                    num_workers=1,
                    download_root=str(MODELOS_DIR),
                )
            except Exception as erro:
                detalhe = str(erro)
                if "429" in detalhe or "Too Many Requests" in detalhe:
                    raise RuntimeError(
                        "O servidor do modelo limitou temporariamente o download (HTTP 429). "
                        "Aguarde alguns minutos e tente novamente; nenhum processamento foi perdido."
                    ) from None
                raise RuntimeError(f"Não foi possível carregar o modelo Whisper: {detalhe}") from erro
        return _modelos[chave]


@medir_operacao("cache_transcricao_escrita")
def salvar_transcricao(transcricao: Transcricao, caminho: Optional[Path] = None) -> Path:
    destino = caminho
    if destino is None and transcricao.caminho_cache:
        destino = Path(transcricao.caminho_cache)
    if destino is None:
        destino = caminho_cache_transcricao(
            transcricao.video_path, transcricao.modelo, transcricao.idioma_solicitado,
            configuracao=transcricao.parametros,
        )
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    transcricao.caminho_cache = str(destino)
    temporario = destino.with_suffix(".json.tmp")
    temporario.write_text(
        json.dumps(asdict(transcricao), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporario.replace(destino)
    return destino


@medir_operacao("cache_transcricao_leitura")
def carregar_transcricao(caminho: Path) -> Transcricao:
    dados = json.loads(Path(caminho).read_text(encoding="utf-8"))
    segmentos = []
    for item in dados.pop("segmentos", []):
        palavras = [PalavraTranscrita(**p) for p in item.pop("palavras", [])]
        if item.get("word_timestamps_invalidated"):
            palavras = []
        segmentos.append(SegmentoTranscricao(**item, palavras=palavras))
    transcricao = Transcricao(**dados, segmentos=segmentos)
    transcricao.caminho_cache = str(caminho)
    return transcricao


@medir_operacao("cache_transcricao", cache=True)
def buscar_transcricao_cache(caminho_video: str, modelo: str = MODELO_PADRAO,
                             idioma: Optional[str] = None, *, modo="preciso", backend="auto_local",
                             autorizar_nuvem=False, configuracao=None) -> Optional[Transcricao]:
    if configuracao is None:
        config = escolher_configuracao(modo, backend, modelo, autorizar_nuvem)
        if config is None:
            modelo, configuracao = _parametros_nuvem(backend)
        elif config.backend in ("groq_whisper", "gemini_transcribe"):
            modelo, configuracao = _parametros_nuvem(config.backend)
        else:
            configuracao = asdict(config)
    caminho = caminho_cache_transcricao(caminho_video, modelo, idioma, configuracao=configuracao)
    if not caminho.exists():
        return None
    try:
        transcricao = carregar_transcricao(caminho)
        parametros = transcricao.parametros or asdict(ConfigTranscricao())
        # Only unchanged Preciso defaults can use Phase 3B caches lacking new fields.
        legados = {k: v for k, v in asdict(ConfigTranscricao()).items() if k in (
            "backend", "modo", "engine", "threads", "beam_size", "batch_size",
            "condition_on_previous_text", "word_timestamps", "vad_filter", "compute_type")}
        if parametros == legados:
            parametros = asdict(ConfigTranscricao())
            transcricao.parametros = parametros.copy()
        if (parametros != configuracao or not transcricao_corresponde_origem(transcricao)
                or transcricao.idioma_solicitado != idioma or transcricao.modelo != modelo
                or transcricao.backend != configuracao["backend"] or transcricao.modo != configuracao["modo"]):
            return None
        return transcricao
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _transcrever_configurado(
    caminho_video: str,
    idioma: Optional[str] = "pt",
    modelo: str = MODELO_PADRAO,
    usar_cache: bool = True,
    cancelada_ref=None,
    progresso_cb: Optional[Callable[[float, str], None]] = None,
    configuracao: ConfigTranscricao = ConfigTranscricao(),
) -> Transcricao:
    """Transcreve uma vez, preserva timestamps e reutiliza o JSON em chamadas futuras."""
    inicio_operacao = perf_counter()
    caminho = Path(caminho_video)
    configuracao.validar()
    parametros = asdict(configuracao)
    anotar(modelo=modelo, backend=configuracao.backend, parametros=parametros)
    if not caminho.is_file():
        raise FileNotFoundError(f"Vídeo não encontrado: {caminho}")
    if idioma not in (None, "pt"):
        raise ValueError("Idioma não suportado nesta etapa. Use 'pt' ou detecção automática.")

    if usar_cache:
        existente = buscar_transcricao_cache(str(caminho), modelo, idioma, configuracao=parametros)
        if existente:
            existente.cache_reutilizado = True
            if progresso_cb:
                progresso_cb(100.0, "Transcrição carregada do cache.")
            anotar(cache="hit", entrada_segundos=existente.duracao,
                   arquivos_reutilizados=[existente.caminho_cache])
            return existente

    anotar(cache="miss" if usar_cache else "disabled")

    if progresso_cb:
        progresso_cb(1.0, f"Carregando modelo Whisper {modelo} em {configuracao.backend}/{configuracao.compute_type}…")
    whisper = (_obter_modelo(modelo, threads=configuracao.threads) if configuracao.backend == "cpu" else
               _obter_modelo(modelo, threads=configuracao.threads, device=configuracao.backend,
                             compute_type=configuracao.compute_type))
    modelo_alinhamento = whisper
    if cancelada_ref and cancelada_ref.cancelada:
        raise RuntimeError("Transcrição cancelada.")

    if progresso_cb:
        progresso_cb(3.0, "Analisando o áudio…")
    if configuracao.engine == "batched":
        from faster_whisper import BatchedInferencePipeline
        # The official pipeline holds per-request timestamp state; never share it.
        whisper = BatchedInferencePipeline(model=whisper)
    extras = {"batch_size": configuracao.batch_size} if configuracao.engine == "batched" else {}
    entrada = str(caminho)
    if configuracao.engine == "batched":
        extras.update(without_timestamps=not configuracao.timestamp_tokens, chunk_length=configuracao.chunk_seconds)
        if not configuracao.vad_filter:
            from faster_whisper.audio import decode_audio
            from modules.transcricao_temporal import chunks_sem_vad
            entrada = medir_chamada("audio_decode_sem_vad", decode_audio, str(caminho), sampling_rate=16000)
            _cancelar_se_necessario(cancelada_ref)
            extras["clip_timestamps"] = chunks_sem_vad(entrada, configuracao.chunk_seconds)
    vad = configuracao.parametros_vad()
    if vad:
        extras["vad_parameters"] = vad
    gerador, info = medir_chamada("audio_decode_vad_preparacao", whisper.transcribe,
        entrada,
        language=idioma,
        beam_size=configuracao.beam_size,
        vad_filter=configuracao.vad_filter,
        word_timestamps=True,
        condition_on_previous_text=configuracao.condition_on_previous_text,
        **extras,
    )

    duracao = float(getattr(info, "duration", 0.0) or 0.0)
    anotar(entrada_segundos=duracao)
    segmentos: list[SegmentoTranscricao] = []
    for segmento in gerador:
        if cancelada_ref and cancelada_ref.cancelada:
            raise RuntimeError("Transcrição cancelada.")
        palavras = [
            PalavraTranscrita(
                inicio=float(p.start),
                fim=float(p.end),
                texto=p.word,
                probabilidade=float(p.probability),
            )
            for p in (segmento.words or [])
            if p.start is not None and p.end is not None
        ]
        segmentos.append(
            SegmentoTranscricao(
                inicio=float(segmento.start),
                fim=float(segmento.end),
                texto=segmento.text.strip(),
                palavras=palavras,
                source_seek=getattr(segmento, "seek", None),
            )
        )
        if progresso_cb and duracao > 0:
            pct = min(99.0, max(3.0, float(segmento.end) / duracao * 100.0))
            progresso_cb(pct, f"Transcrevendo… {segundos_para_hhmmss(segmento.end)}")

    video_id = _identidade_video(str(caminho), modelo, idioma)
    transcricao = Transcricao(
        video_path=str(caminho.resolve()),
        video_id=video_id,
        modelo=modelo,
        idioma_solicitado=idioma,
        idioma_detectado=str(getattr(info, "language", "") or ""),
        probabilidade_idioma=float(getattr(info, "language_probability", 0.0) or 0.0),
        duracao=duracao,
        criada_em=datetime.now(timezone.utc).isoformat(),
        segmentos=segmentos,
        backend=configuracao.backend, modo=configuracao.modo, parametros=parametros,
    )
    opcoes_efetivas = getattr(info, "transcription_options", None)
    clips_vad = getattr(opcoes_efetivas, "clip_timestamps", None)
    intervalos_vad = ([(clip["start"]/16000, clip["end"]/16000) for clip in clips_vad]
                     if configuracao.vad_filter and configuracao.engine == "batched"
                     and isinstance(clips_vad, list) else None)
    if configuracao.alinhamento == "suspeitos":
        from modules.transcricao_alinhamento import refinar_suspeitos
        transcricao.alinhamento_info = refinar_suspeitos(
            transcricao, modelo_alinhamento, configuracao, cancelada_ref, vad_intervalos=intervalos_vad)
    from modules.transcricao_temporal import confianca_temporal, validar_timestamps
    transcricao.diagnostico_temporal = validar_timestamps(transcricao.segmentos, duracao)
    for i, s in enumerate(transcricao.segmentos):
        score = confianca_temporal(s, duracao, transcricao.segmentos[i-1] if i else None, intervalos_vad)
        s.timestamp_confidence, s.timestamp_sinais = score["timestamp_confidence"], score["sinais"]
    if configuracao.modo == "rapido" and any(p["critico"] for p in transcricao.diagnostico_temporal):
        erro = ValueError("Configuracao rapida retornou timestamps estruturalmente invalidos.")
        erro.transcricao = transcricao
        raise erro
    _cancelar_se_necessario(cancelada_ref)
    transcricao.tempo_transcricao_s = perf_counter()-inicio_operacao
    salvar_transcricao(transcricao)
    if progresso_cb:
        progresso_cb(100.0, "Transcrição concluída.")
    return transcricao


def _cancelar_se_necessario(cancelada_ref):
    if cancelada_ref and cancelada_ref.cancelada:
        raise RuntimeError("Transcricao cancelada.")


def _parametros_nuvem(backend):
    if backend == "groq_whisper":
        from modules.transcricao_groq import MODELO_GROQ, PARAMETROS_GROQ
        return MODELO_GROQ, PARAMETROS_GROQ
    from modules.transcricao_gemini import MODELO_GEMINI, PARAMETROS_GEMINI
    return MODELO_GEMINI, PARAMETROS_GEMINI


@medir_operacao("transcricao", backend="faster-whisper/cpu/int8")
def transcrever_video(caminho_video, idioma="pt", modelo=MODELO_PADRAO, usar_cache=True,
                     cancelada_ref=None, progresso_cb=None, *, modo="preciso", backend="auto_local",
                     autorizar_nuvem=False, fallback_local=True, configuracao=None):
    inicio = perf_counter()
    _cancelar_se_necessario(cancelada_ref)
    config = escolher_configuracao(modo, backend, modelo, autorizar_nuvem)
    if configuracao is not None:
        if backend in ("groq_whisper", "gemini_transcribe"):
            raise ValueError("Configuracao CPU nao se aplica ao Groq.")
        config = configuracao.validar()
    backend_efetivo = config.backend if config is not None else backend
    if backend_efetivo in ("groq_whisper", "gemini_transcribe") and backend not in (
            "auto_nuvem", "groq_whisper", "gemini_transcribe"):
        raise ValueError("Backend local nao pode ser substituido por nuvem via configuracao.")
    if backend_efetivo in ("groq_whisper", "gemini_transcribe") and not autorizar_nuvem:
        raise ValueError("Envio de audio externo exige autorizacao explicita.")
    alertas = []
    if modo == "rapido" and config == ConfigTranscricao() and configuracao is None:
        alertas.append("Sem configuracao Rapido aprovada nesta maquina; usado Preciso local.")
    try:
        if backend_efetivo == "groq_whisper":
            from modules.transcricao_groq import transcrever_groq
            resultado = transcrever_groq(caminho_video, idioma, usar_cache, cancelada_ref,
                                         progresso_cb, autorizar_nuvem=autorizar_nuvem)
        elif backend_efetivo == "gemini_transcribe":
            from modules.transcricao_gemini import transcrever_gemini
            resultado = transcrever_gemini(caminho_video, idioma, usar_cache, cancelada_ref,
                                           progresso_cb, autorizar_nuvem=autorizar_nuvem)
        else:
            # AUTO may reuse a compatible Preciso cache before doing fresh work.
            resultado = (buscar_transcricao_cache(caminho_video, modelo, idioma)
                         if modo == "auto" and usar_cache else None)
            if resultado is not None:
                resultado.cache_reutilizado = True
            if resultado is None:
                resultado = _transcrever_configurado(caminho_video, idioma, modelo, usar_cache,
                                                     cancelada_ref, progresso_cb, config)
            elif progresso_cb:
                progresso_cb(100, "Transcricao Preciso reutilizada do cache local.")
    except Exception:
        _cancelar_se_necessario(cancelada_ref)
        if not fallback_local or (modo != "auto" and not (modo == "rapido" and configuracao is None)
                and backend_efetivo not in ("groq_whisper", "gemini_transcribe", "cuda")):
            raise
        if config == ConfigTranscricao() and backend_efetivo not in ("groq_whisper", "gemini_transcribe", "cuda"):
            raise
        alertas.append("Backend solicitado falhou; usado Preciso local. Nenhuma API adicional foi chamada.")
        if progresso_cb:
            progresso_cb(1, alertas[-1])
        resultado = _transcrever_configurado(caminho_video, idioma, modelo, usar_cache,
                                             cancelada_ref, progresso_cb, ConfigTranscricao())
    _cancelar_se_necessario(cancelada_ref)
    resultado.tempo_ultima_operacao_s = perf_counter()-inicio
    if resultado.tempo_transcricao_s == 0:
        resultado.tempo_transcricao_s = resultado.tempo_ultima_operacao_s
    resultado.alertas.extend(alertas)
    anotar(backend=resultado.backend, modelo=resultado.modelo, modo=resultado.modo,
           entrada_segundos=resultado.duracao)
    return resultado


_LINHA_EDITAVEL = re.compile(r"^\[(.+?)\s*-->\s*(.+?)\]\s*(.*)$")


def aplicar_texto_editado(transcricao: Transcricao, texto: str, *, persistir=True) -> Transcricao:
    """Aplica edições mantendo os timestamps explicitados em cada linha."""
    novos: list[SegmentoTranscricao] = []
    linhas = [linha for linha in texto.splitlines() if linha.strip()]
    for numero, linha in enumerate(linhas, start=1):
        match = _LINHA_EDITAVEL.match(linha.strip())
        if not match:
            raise ValueError(f"Linha {numero} inválida. Preserve [início --> fim] antes do texto.")
        inicio = hhmmss_para_segundos(match.group(1))
        fim = hhmmss_para_segundos(match.group(2))
        if inicio < 0 or fim <= inicio or fim > transcricao.duracao + 0.001:
            raise ValueError(f"Tempos inválidos na linha {numero}.")
        original = transcricao.segmentos[numero - 1] if numero <= len(transcricao.segmentos) else None
        texto_novo = match.group(3).strip()
        mesma_fala = bool(original and original.texto.strip() == texto_novo
                          and abs(original.inicio-inicio) < .001 and abs(original.fim-fim) < .001)
        palavras = original.palavras if mesma_fala else []
        invalidado = original.word_timestamps_invalidated if mesma_fala else True
        novos.append(SegmentoTranscricao(inicio, fim, texto_novo, palavras, invalidado))

    if not novos:
        raise ValueError("A transcrição não pode ficar vazia.")
    for anterior, atual in zip(novos, novos[1:]):
        if atual.inicio < anterior.inicio:
            raise ValueError("Os segmentos devem permanecer em ordem cronológica.")
    transcricao.segmentos = novos
    from modules.transcricao_temporal import validar_timestamps
    transcricao.diagnostico_temporal = validar_timestamps(novos, transcricao.duracao)
    if persistir:
        salvar_transcricao(transcricao)
    return transcricao
