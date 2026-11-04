"""
modules/transcricao_gemini.py — Formatação e recuperação de transcrições completas
para uso no Google Gemini a partir da esteira multi-episódio do IA TOONS CUT.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from modules.transcricao import (
    TRANSCRICOES_DIR,
    SegmentoTranscricao,
    Transcricao,
    buscar_transcricao_cache,
    caminho_cache_transcricao,
    carregar_transcricao,
)


def formatar_timestamp_gemini(segundos: float, forcar_horas: bool = False) -> str:
    """Formata segundos em [MM:SS] ou [HH:MM:SS] quando horas estiverem presentes."""
    s_total = max(0.0, float(segundos))
    h = int(s_total // 3600)
    m = int((s_total % 3600) // 60)
    s = int(s_total % 60)
    if forcar_horas or h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def formatar_duracao_gemini(duracao: Optional[float]) -> str:
    """Formata duração em formato legível (ex: '42:45' ou '01:15:30')."""
    if duracao is None or float(duracao) <= 0:
        return "não informada"
    s_total = max(0.0, float(duracao))
    h = int(s_total // 3600)
    m = int((s_total % 3600) // 60)
    s = int(s_total % 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def formatar_idioma_gemini(idioma: Optional[str]) -> str:
    """Formata o idioma para exibição clara em português."""
    if not idioma:
        return "não informado"
    idioma_limpo = str(idioma).strip().lower()
    if idioma_limpo in ("pt", "pt-br", "portuguese", "português", "portugues"):
        return "Português (pt)"
    if idioma_limpo in ("en", "english", "inglês", "ingles"):
        return "Inglês (en)"
    if idioma_limpo in ("es", "spanish", "espanhol"):
        return "Espanhol (es)"
    return str(idioma)


def formatar_transcricao_para_gemini(
    transcricao: Transcricao | dict[str, Any],
    nome_episodio: str = "",
) -> str:
    """
    Monta a transcrição completa no formato padrão para colar diretamente no Gemini:

    TRANSCRIÇÃO COMPLETA — UMA FAMÍLIA DA PESADA S10E02

    Arquivo: Uma Família da Pesada S10E02.mp4
    Duração: 21:46
    Idioma: Português (pt)

    [00:00] Fala transcrita...
    [00:05] Continuação...

    FIM DA TRANSCRIÇÃO

    Preserva a ordem cronológica estrita de todos os segmentos do episódio.
    """
    if isinstance(transcricao, Transcricao):
        video_path = transcricao.video_path
        duracao = transcricao.duracao
        idioma = transcricao.idioma_detectado or transcricao.idioma_solicitado
        segmentos_raw = transcricao.segmentos
    elif isinstance(transcricao, dict):
        video_path = transcricao.get("video_path", "")
        duracao = transcricao.get("duracao")
        idioma = transcricao.get("idioma_detectado") or transcricao.get("idioma_solicitado")
        segmentos_raw = transcricao.get("segmentos", [])
    else:
        raise TypeError(f"Tipo de transcrição inválido: {type(transcricao)}")

    # Extrai o nome de exibição e o título
    nome_arquivo = nome_episodio or (Path(video_path).name if video_path else "") or "Episódio"
    titulo_limpo = Path(nome_arquivo).stem.upper()

    # Normaliza e ordena cronologicamente os segmentos
    itens: list[tuple[float, float, str]] = []
    for s in segmentos_raw:
        if isinstance(s, SegmentoTranscricao):
            inicio, fim, texto = s.inicio, s.fim, s.texto
        elif isinstance(s, dict):
            inicio = float(s.get("inicio", 0.0))
            fim = float(s.get("fim", 0.0))
            texto = str(s.get("texto", ""))
        else:
            inicio = float(getattr(s, "inicio", 0.0))
            fim = float(getattr(s, "fim", 0.0))
            texto = str(getattr(s, "texto", ""))

        texto_limpo = texto.strip()
        if texto_limpo:
            itens.append((inicio, fim, texto_limpo))

    # Ordenação cronológica estrita por tempo de início e fim
    itens.sort(key=lambda x: (x[0], x[1]))

    # Determina se há segmentos com 1 hora ou mais para forçar [HH:MM:SS]
    forcar_horas = (duracao is not None and float(duracao) >= 3600) or any(
        inicio >= 3600 for inicio, _, _ in itens
    )

    linhas: list[str] = [
        f"TRANSCRIÇÃO COMPLETA — {titulo_limpo}",
        "",
        f"Arquivo: {nome_arquivo}",
        f"Duração: {formatar_duracao_gemini(duracao)}",
        f"Idioma: {formatar_idioma_gemini(idioma)}",
        "",
    ]

    for inicio, _, texto in itens:
        ts = formatar_timestamp_gemini(inicio, forcar_horas=forcar_horas)
        linhas.append(f"[{ts}] {texto}")

    linhas.append("")
    linhas.append("FIM DA TRANSCRIÇÃO")

    return "\n".join(linhas)


def obter_transcricao_episodio(ep: Any) -> Optional[Transcricao]:
    """
    Recupera a transcrição completa de um episódio da esteira.
    Prioriza objeto em memória, caminho de cache registrado, busca por parâmetros
    e correspondência no diretório de transcrições.
    NUNCA reexecuta o Whisper.
    """
    if ep is None:
        return None

    # 1. Objeto já presente em memória no contexto
    transcricao = getattr(ep, "transcricao", None)
    if isinstance(transcricao, Transcricao) and transcricao.segmentos:
        return transcricao

    # 2. Caminho de cache explícito registrado no episódio
    caminho_cache = getattr(ep, "caminho_cache_transcricao", "") or ""
    if caminho_cache:
        p = Path(caminho_cache)
        if p.is_file():
            try:
                t = carregar_transcricao(p)
                if t and t.segmentos:
                    if hasattr(ep, "transcricao") and ep.transcricao is None:
                        ep.transcricao = t
                    return t
            except Exception:
                pass

    # 3. Busca no cache via buscar_transcricao_cache
    video_path = getattr(ep, "video_path", "") or ""
    if video_path and Path(video_path).is_file():
        modelo = getattr(ep, "modelo_whisper", "small")
        idioma = getattr(ep, "idioma", "pt")
        modo = getattr(ep, "transcricao_modo", "preciso")
        backend = getattr(ep, "transcricao_backend", "auto_local")
        autorizar_nuvem = getattr(ep, "autorizar_nuvem", False)
        try:
            t = buscar_transcricao_cache(
                str(video_path),
                modelo=modelo,
                idioma=idioma,
                modo=modo,
                backend=backend,
                autorizar_nuvem=autorizar_nuvem,
            )
            if t and t.segmentos:
                if hasattr(ep, "transcricao") and ep.transcricao is None:
                    ep.transcricao = t
                if hasattr(ep, "caminho_cache_transcricao") and not ep.caminho_cache_transcricao:
                    ep.caminho_cache_transcricao = t.caminho_cache
                return t
        except Exception:
            pass

        # 4. Caminho padrão calculado
        try:
            cp = caminho_cache_transcricao(str(video_path), modelo=modelo, idioma=idioma)
            if cp.is_file():
                t = carregar_transcricao(cp)
                if t and t.segmentos:
                    if hasattr(ep, "transcricao") and ep.transcricao is None:
                        ep.transcricao = t
                    if hasattr(ep, "caminho_cache_transcricao") and not ep.caminho_cache_transcricao:
                        ep.caminho_cache_transcricao = str(cp)
                    return t
        except Exception:
            pass

    # 5. Varredura no diretório de cache por nome de arquivo ou video_id
    if video_path:
        nome_alvo = Path(video_path).name.lower()
        vid_alvo = getattr(ep, "video_id", None) or getattr(ep, "id", None)
        try:
            for arq in TRANSCRICOES_DIR.glob("*.json"):
                try:
                    with open(arq, "r", encoding="utf-8") as f:
                        cabecalho = f.read(2048)
                    if nome_alvo in cabecalho.lower() or (vid_alvo and vid_alvo in cabecalho):
                        t = carregar_transcricao(arq)
                        if t and t.segmentos:
                            t_nome = Path(t.video_path).name.lower() if t.video_path else ""
                            if t_nome == nome_alvo or (vid_alvo and t.video_id == vid_alvo):
                                if hasattr(ep, "transcricao") and ep.transcricao is None:
                                    ep.transcricao = t
                                if hasattr(ep, "caminho_cache_transcricao") and not ep.caminho_cache_transcricao:
                                    ep.caminho_cache_transcricao = str(arq)
                                return t
                except Exception:
                    continue
        except Exception:
            pass

    return None
