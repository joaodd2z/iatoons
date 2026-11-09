"""
modules/titulos_lote.py — Geração de pacote de cortes para IA e importação/aplicação
de títulos em lote para a Esteira Multi-Episódio do IA TOONS CUT.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field
import re
import sys
import time
from typing import Any, Optional

from modules.agencia.editorial import TituloOpcao
from modules.agencia.pacote_chatgpt import (
    PAPEIS,
    _episodio,
    _estrutura,
    _fala_intervalo,
    _segundos,
)
from modules.agencia.persistencia import salvar_lista_agencia
from modules.agencia.timeline import OpcoesEdicao, construir_timeline
from modules.titulos_v2 import STATUS_REVISAO_TITULO
from pathlib import Path
from modules.transcricao_gemini import formatar_transcricao_para_gemini, obter_transcricao_episodio


ETAPAS_EDITORIAIS_ATIVAS = ("analise_agencia", "momentos_fortes", "hooks", "titulos")

# ─── Helpers de Clipboard Direto do Sistema Operacional (Win32 API 64-bit) ───
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
_ULTIMO_CLOSE_CLIPBOARD_TS = 0.0

if sys.platform == "win32":
    try:
        _user32 = ctypes.windll.user32
        _kernel32 = ctypes.windll.kernel32

        _user32.OpenClipboard.argtypes = [wintypes.HWND]
        _user32.OpenClipboard.restype = wintypes.BOOL

        _user32.CloseClipboard.argtypes = []
        _user32.CloseClipboard.restype = wintypes.BOOL

        _user32.EmptyClipboard.argtypes = []
        _user32.EmptyClipboard.restype = wintypes.BOOL

        _user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
        _user32.SetClipboardData.restype = ctypes.c_void_p

        _user32.GetClipboardData.argtypes = [wintypes.UINT]
        _user32.GetClipboardData.restype = ctypes.c_void_p

        _user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
        _user32.IsClipboardFormatAvailable.restype = wintypes.BOOL

        _kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        _kernel32.GlobalAlloc.restype = ctypes.c_void_p

        _kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        _kernel32.GlobalLock.restype = ctypes.c_void_p

        _kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        _kernel32.GlobalUnlock.restype = wintypes.BOOL

        _kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
        _kernel32.GlobalFree.restype = ctypes.c_void_p
    except Exception:
        pass


def _eh_widget_tk_real(widget) -> bool:
    """Retorna True apenas se widget for uma instância real do Tkinter (com interpretador tkapp ativo), não um MagicMock."""
    if widget is None:
        return False
    tk_app = getattr(widget, "tk", None)
    if tk_app is None or type(tk_app).__name__ != "tkapp":
        return False
    d = getattr(widget, "__dict__", {})
    if "clipboard_get" in d or "clipboard_append" in d:
        return False
    return True


def _aguardar_cooldown_clipboard_win32() -> None:
    """Garante janela mínima de 55ms após CloseClipboard para o serviço de histórico do Windows (cbdhsvc) liberar o lock."""
    global _ULTIMO_CLOSE_CLIPBOARD_TS
    decorrido = time.monotonic() - _ULTIMO_CLOSE_CLIPBOARD_TS
    if 0.0 <= decorrido < 0.055:
        time.sleep(0.055 - decorrido)


def _registrar_close_clipboard_win32() -> None:
    global _ULTIMO_CLOSE_CLIPBOARD_TS
    _ULTIMO_CLOSE_CLIPBOARD_TS = time.monotonic()


def escrever_clipboard_sistema(widget, texto: str) -> None:
    """
    Grava texto no clipboard do sistema operacional e atualiza o widget Tkinter.
    No Windows, grava diretamente via Win32 API com OpenClipboard(None) + EmptyClipboard(),
    forçando o envio de WM_DESTROYCLIPBOARD para a janela do Tkinter e desativando caches
    internos (dispPtr->clipTargetPtr / clipboardActive) antes de abrir caixas de diálogo.
    """
    if widget is not None:
        try:
            widget.clipboard_clear()
            widget.clipboard_append(texto)
            if _eh_widget_tk_real(widget) and hasattr(widget, "update_idletasks"):
                widget.update_idletasks()
        except Exception:
            pass

    # Se widget foi passado mas é um mock de teste (sem tkapp real), não altera o OS Clipboard real
    if widget is not None and not _eh_widget_tk_real(widget):
        return

    if sys.platform == "win32":
        texto_str = str(texto or "")
        texto_bytes = (texto_str + "\0").encode("utf-16le")
        tamanho = len(texto_bytes)

        _aguardar_cooldown_clipboard_win32()
        hmem = _kernel32.GlobalAlloc(GMEM_MOVEABLE, tamanho)
        if not hmem:
            return
        ptr = _kernel32.GlobalLock(hmem)
        if not ptr:
            _kernel32.GlobalFree(hmem)
            return
        ctypes.memmove(ptr, texto_bytes, tamanho)
        _kernel32.GlobalUnlock(hmem)

        aberto = False
        for _ in range(30):
            try:
                if _user32.OpenClipboard(None):
                    aberto = True
                    break
            except Exception:
                break
            time.sleep(0.015)

        if not aberto:
            _kernel32.GlobalFree(hmem)
            return

        try:
            _user32.EmptyClipboard()
            res = _user32.SetClipboardData(CF_UNICODETEXT, hmem)
            if not res:
                _kernel32.GlobalFree(hmem)
        except Exception:
            pass
        finally:
            try:
                _user32.CloseClipboard()
            except Exception:
                pass
            _registrar_close_clipboard_win32()


def ler_clipboard_sistema(widget=None) -> str:
    """
    Lê texto diretamente do OS Clipboard via Win32 API (CF_UNICODETEXT), contornando caches internos do Tcl/Tk.
    Se estiver em ambiente de teste (mock) ou fora do Windows, utiliza widget.clipboard_get() como fallback.
    """
    if widget is not None and not _eh_widget_tk_real(widget):
        try:
            return widget.clipboard_get()
        except Exception:
            if hasattr(widget, "tela") and hasattr(widget.tela, "winfo_toplevel"):
                try:
                    return widget.tela.winfo_toplevel().clipboard_get()
                except Exception:
                    pass
            return ""

    if sys.platform == "win32":
        _aguardar_cooldown_clipboard_win32()
        aberto = False
        for _ in range(30):
            try:
                if _user32.OpenClipboard(None):
                    aberto = True
                    break
            except Exception:
                break
            time.sleep(0.015)

        if aberto:
            try:
                if _user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
                    handle = _user32.GetClipboardData(CF_UNICODETEXT)
                    if handle:
                        ptr = _kernel32.GlobalLock(handle)
                        if ptr:
                            try:
                                return ctypes.wstring_at(ptr)
                            finally:
                                _kernel32.GlobalUnlock(handle)
            except Exception:
                pass
            finally:
                try:
                    _user32.CloseClipboard()
                except Exception:
                    pass
                _registrar_close_clipboard_win32()

    if widget is not None:
        try:
            return widget.clipboard_get()
        except Exception:
            if hasattr(widget, "tela") and hasattr(widget.tela, "winfo_toplevel"):
                try:
                    return widget.tela.winfo_toplevel().clipboard_get()
                except Exception:
                    pass
    return ""


INSTRUCOES_ESTEIRA = """INSTRUÇÕES PARA GERAR TÍTULOS DE CORTES (IA TOONS CUT)

Analise cada corte do episódio e crie títulos fortes para vídeos curtos (Reels, TikTok, Shorts).
Quero títulos específicos, engraçados, provocativos, extravagantes e com potencial de despertar curiosidade.
Identifique o conflito central, a situação mais absurda ou a contradição engraçada da cena.
Não invente acontecimentos que não estejam sustentados pelas falas ou resumo.

FORMATO DE RESPOSTA OBRIGATÓRIO (para importação automática em lote):
Responda para cada corte, mantendo exatamente o número do corte, no formato:

CORTE 1: [Título principal recomendado]
CAPA: [Texto curto para a capa - opcional]
ALT 1: [Alternativa diferente - opcional]
ALT 2: [Segunda alternativa - opcional]
JUSTIFICATIVA: [Uma frase - opcional]

(Também é aceito apenas uma linha por corte: "CORTE 1: <título>", "CORTE 2: <título>", etc.)"""

_ASPAS = "\"'“”‘’«»„"
_RE_ASPAS = re.compile(f"^[{_ASPAS}]+|[{_ASPAS}]+$")
_RE_ROTULO_TITULO = re.compile(
    r"^(?:Título recomendado|Titulo recomendado|Título principal|Titulo principal|"
    r"Melhor título|Melhor titulo|Título|Titulo|Principal)\s*[:\-–—]\s*",
    re.IGNORECASE,
)


@dataclass
class TituloCorteItem:
    indice: int
    titulo: str
    titulo_capa: str = ""
    alternativas: list[str] = field(default_factory=list)
    justificativa: str = ""
    id_corte: str = ""


@dataclass
class ResultadoParseTitulos:
    itens: list[TituloCorteItem] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    total_encontrados: int = 0
    total_esperados: int = 0


def limpar_texto_titulo(texto: str) -> str:
    """Remove markdown, bullets, aspas externas, numeração e rótulos de título."""
    if not texto:
        return ""
    t = str(texto).strip()
    for _ in range(3):
        anterior = t
        t = re.sub(r"^\s*[*_#`~>]+\s*", "", t)
        t = re.sub(r"\s*[*_#`~]+\s*$", "", t)
        t = re.sub(r"^[-•–—]+\s*", "", t)
        t = _RE_ASPAS.sub("", t).strip()
        t = re.sub(r"^(?:CORTE\s*)?#?\d+\s*[\.:\-\)–—]\s*", "", t, flags=re.IGNORECASE).strip()
        t = _RE_ROTULO_TITULO.sub("", t).strip()
        if t == anterior:
            break
    return " ".join(t.split())


_RE_CORTE = re.compile(
    r"^(?:[*_#`~>\-•\s\[\(]*)(?:CORTE|CANDIDATO|CLIP|VÍDEO|VIDEO|PARTE)\s*#?\s*(\d+)\b\s*[*_#`~\]\)]*\s*[:\.\-·–—)]*\s*(.*)$",
    re.IGNORECASE,
)
_RE_NUM = re.compile(r"^(?:[*_#`~\-\s\[\(]*)(\d+)\s*[\.\)\-–—\]]\s*(.*)$")
_RE_CAPA = re.compile(r"^(?:[*_#`~\-•\s]*)(?:CAPA|Texto da capa|Miniatura)\s*[*_]*\s*[:\-–—]+\s*(.*)$", re.IGNORECASE)
_RE_ALT = re.compile(r"^(?:[*_#`~\-•\s]*)(?:ALT|Alternativa|Opção|Opcao)\s*\d*\s*[*_]*\s*[:\-–—]+\s*(.*)$", re.IGNORECASE)
_RE_JUST = re.compile(r"^(?:[*_#`~\-•\s]*)(?:JUSTIFICATIVA|Motivo|Por que)\s*[*_]*\s*[:\-–—]+\s*(.*)$", re.IGNORECASE)
_RE_TIT = re.compile(
    r"^(?:[*_#`~\-•\s]*)(?:Título recomendado|Titulo recomendado|Título principal|Titulo principal|"
    r"Melhor título|Melhor titulo|Título|Titulo)\s*[*_]*\s*[:\-–—]+\s*(.*)$",
    re.IGNORECASE,
)

_PREFIXOS_INSTRUCOES = (
    "instruções para gerar", "instrucoes para gerar",
    "analise cada corte", "analise o corte", "analise os cortes",
    "quero títulos específicos", "quero titulos especificos", "quero títulos", "quero titulos",
    "não use títulos", "nao use titulos",
    "identifique o conflito", "identifique a situação", "identifique a situacao",
    "não invente acontecimentos", "nao invente acontecimentos",
    "formato de resposta", "formato obrigatório", "formato obrigatorio",
    "responda para cada corte", "responda no formato", "responda mantendo", "responda apenas",
    "lembre-se: responda", "lembre-se",
    "(também é aceito", "(tambem e aceito", "também é aceito", "tambem e aceito",
    "para cada corte, entregue", "para cada corte entregue",
    "o melhor título recomendado", "o melhor titulo recomendado",
    "duas alternativas realmente diferentes", "duas alternativas",
    "um texto curto para a capa",
    "uma justificativa de uma frase", "uma justificativa",
    "diferencie claramente o campo",
    "priorize especificidade", "priorize humor",
    "organize a resposta pelo número", "organize a resposta pelo numero", "organize a resposta",
    "quando combinar com a cena",
    "episódio:", "episodio:",
    "ordem final da montagem",
    "segmento original", "intervalo original",
    "falas:", "fala:", "diálogo:", "dialogo:",
    "resumo:", "descrição:", "descricao:",
    "duração final:", "duracao final:", "duração:", "duracao:",
    "título atual:", "titulo atual:",
    "avisos/motivos:", "aviso/motivo:", "avisos:", "motivos:",
    "estrutura narrativa:",
    "gancho:", "contexto:", "desenvolvimento:", "escalada:",
    "clímax:", "climax:", "consequência:", "consequencia:", "payoff:",
    "consequência / payoff:", "desfecho:",
    "regras:", "orientações:", "orientacoes:", "instruções:", "instrucoes:",
    "candidato a corte", "plano de corte",
)

_ROLES_CHAT = re.compile(
    r"^(?:user|usuário|usuario|assistant|assistente|chatgpt(?:\s+disse)?|gemini|claude|system|sistema|você(?:\s+disse)?|voce(?:\s+disse)?|you(?:\s+said)?|ia|model|modelo|prompt|humano|human)\s*[:\-–—]?\s*$",
    re.IGNORECASE,
)

_PREAMBULOS_IA = re.compile(
    r"^(?:aqui estão|aqui estao|aqui está|aqui esta|segue|seguem|com certeza|claro|abaixo|espero que|boa sorte|aqui vão|aqui vao|veja abaixo|essas são|essas sao|espero ter ajudado|qualquer dúvida|qualquer duvida|sugestões de títulos|sugestoes de titulos|títulos sugeridos|titulos sugeridos)\b",
    re.IGNORECASE,
)


def _sem_placeholder(texto: str) -> str:
    """Descarta placeholders do modelo de resposta, ex.: '[Título principal]' ou '<título>'."""
    t = (texto or "").strip()
    if not t:
        return ""
    if re.fullmatch(r"\[[^\]]*\]|<[^>]*>", t):
        return ""
    if re.search(r"\[(?:Título principal|Texto curto|Alternativa|Segunda alternativa|Uma frase|título)[^\]]*\]", t, re.IGNORECASE):
        return ""
    if re.search(r"<(?:título|titulo|seu\s+t[íi]tulo\s+aqui)[^>]*>", t, re.IGNORECASE):
        return ""
    return t


def eh_instrucao_ou_metadado(linha: str) -> bool:
    """Detecta se uma linha é instrução de sistema, cabeçalho, regra de prompt ou metadado."""
    if not linha:
        return True
    l_raw = linha.strip()
    if not l_raw:
        return True
    if re.match(r"^```|^[-=*_]{3,}$", l_raw):
        return True
    if _ROLES_CHAT.match(l_raw):
        return True

    # Se for CORTE N, só é instrução se tiver placeholder explícito no resto
    m_corte = _RE_CORTE.match(l_raw)
    if m_corte:
        resto = m_corte.group(2).strip()
        if resto and not _sem_placeholder(resto):
            return True
        return False

    # Campos de rótulo (CAPA, ALT, JUST, TIT) com placeholder
    for rg in (_RE_CAPA, _RE_ALT, _RE_JUST, _RE_TIT):
        m = rg.match(l_raw)
        if m:
            resto_limpo = m.group(1).strip()
            if not resto_limpo or not _sem_placeholder(resto_limpo):
                return True
            # Se tem rótulo com valor real (ex: "Título: O Desastre Completo"), não é instrução
            return False

    l = re.sub(r"^[*_#`~>\-•\s]+", "", l_raw).strip()
    l = re.sub(r"^\d+\s*[\.\)]\s*", "", l).strip().lower()
    if not l:
        return True

    if _PREAMBULOS_IA.match(l):
        return True

    if any(l.startswith(pref) for pref in _PREFIXOS_INSTRUCOES):
        return True

    if re.match(r"^(?:id|uuid)\s*[:\-–—]", l):
        return True

    if re.match(r"^(?:segmento|intervalo)\s+original\b", l):
        return True

    if not _sem_placeholder(l_raw):
        return True

    return False


def _linha_descartavel(linha: str) -> bool:
    return eh_instrucao_ou_metadado(linha)


def isolar_resposta_chat(texto: str) -> str:
    """
    Se o usuário colou o chat inteiro (Prompt + Instruções + Resposta da IA),
    isola apenas o bloco final que contém a resposta da IA.
    """
    if not texto:
        return ""
    t = texto.strip()

    marcas_prompt = (
        "instruções para gerar", "instrucoes para gerar",
        "formato de resposta",
        "ordem final da montagem",
    )
    t_lower = t.lower()
    tem_prompt = any(m in t_lower for m in marcas_prompt)
    if not tem_prompt:
        return t

    # 1. Frase padrão de fechamento do prompt do IA TOONS CUT
    padrao_fechamento = re.compile(
        r"Lembre-se:\s*responda\s*no\s*formato[^\n]*\n?",
        re.IGNORECASE,
    )
    partes = padrao_fechamento.split(t)
    if len(partes) > 1 and partes[-1].strip():
        candidato = partes[-1].strip()
        if re.search(r"\b(?:CORTE|CANDIDATO|CLIP|VÍDEO|VIDEO|PARTE)\s*#?\s*\d+\b|\b\d+\s*[\.\)\-–—\]]", candidato, re.IGNORECASE) or len(candidato.splitlines()) >= 2:
            return candidato

    # 2. Marcadores de papel de IA (Assistant, ChatGPT, Gemini, etc.)
    padrao_roles = re.compile(
        r"\n\s*(?:assistant|chatgpt(?:\s+disse)?|gemini|claude|ia|modelo|resposta(?:s)?|resultado)\s*[:\-–—]?\s*\n",
        re.IGNORECASE,
    )
    partes_role = padrao_roles.split(t)
    if len(partes_role) > 1:
        for parte in reversed(partes_role[1:]):
            if parte.strip():
                return parte.strip()

    # 3. Preâmbulos conversacionais comuns da IA
    padrao_preambulo = re.compile(
        r"\n\s*(?:(?:com certeza|claro|aqui estão|aqui estao|aqui está|aqui esta|segue|seguem|abaixo|veja|essas são|essas sao|sugestões|sugestoes|títulos sugeridos)\b[^\n]*:?\s*\n)",
        re.IGNORECASE,
    )
    partes_pre = padrao_preambulo.split(t)
    if len(partes_pre) > 1:
        for parte in reversed(partes_pre[1:]):
            if parte.strip() and len(parte.splitlines()) >= 1:
                return parte.strip()

    # 4. Divisores de markdown (--- ou ===) separando o prompt da resposta
    divisores = re.compile(r"\n\s*[-=]{3,}\s*\n")
    partes_div = divisores.split(t)
    if len(partes_div) > 1:
        for parte in reversed(partes_div[1:]):
            if parte.strip():
                return parte.strip()

    # 5. Se o prompt tem falas / segmentos, a resposta da IA começa após o último bloco
    ultimo_marcador = None
    for m in re.finditer(r"(?:Falas:\s*[^\n]+|Intervalo original[^\n]+|Estrutura narrativa:[^\n]+)", t, re.IGNORECASE):
        ultimo_marcador = m

    if ultimo_marcador:
        resto = t[ultimo_marcador.end():].strip()
        if len(resto) > 10:
            return resto

    return t


_SUBSTRINGS_DESCARTAR_LOTE = (
    "instruções", "instrucoes",
    "analise cada corte", "analise o corte", "analise os cortes",
    "quero títulos", "quero titulos",
    "não use títulos", "nao use titulos",
    "identifique o conflito",
    "não invente acontecimentos", "nao invente acontecimentos",
    "formato de resposta", "formato obrigatório", "formato obrigatorio",
    "responda para cada corte",
    "episódio:", "episodio:",
    "resumo:",
    "duração", "duracao",
    "título atual:", "titulo atual:",
    "texto da capa:",
    "ordem final",
    "segmento original",
    "intervalo original",
    "falas:", "fala:", "diálogo:", "dialogo:",
    "estrutura narrativa",
    "gancho:",
    "contexto:",
    "desenvolvimento:",
    "escalada:",
    "consequência", "consequencia",
    "payoff",
    "lembre-se:", "lembre-se",
    "(também é aceito", "(tambem e aceito", "também é aceito", "tambem e aceito",
    "avisos/motivos:", "aviso/motivo:", "avisos:", "motivos:",
    "candidato a corte", "plano de corte",
    "para cada corte, entregue", "para cada corte entregue",
    "o melhor título recomendado", "o melhor titulo recomendado",
    "duas alternativas",
    "um texto curto para a capa",
    "uma justificativa",
    "diferencie claramente o campo",
    "priorize especificidade", "priorize humor",
    "organize a resposta",
)

_RE_PLACEHOLDERS_EXEMPLO = re.compile(
    r"\[(?:Título principal(?: recomendado)?|Texto curto(?: para a capa - opcional)?|"
    r"Alternativa(?: diferente - opcional)?|Segunda alternativa(?: - opcional)?|"
    r"Uma frase(?: - opcional)?|título|titulo)[^\]]*\]|<(?:título|titulo|seu\s+t[íi]tulo\s+aqui)[^>]*>|"
    r"\[[^\]]*(?:recomendado|opcional|\.\.\.)[^\]]*\]",
    re.IGNORECASE,
)

_RE_ID_LINHA = re.compile(r"(?:\bID\s*:|·\s*ID\b|\b(?:id|uuid)\s*:)", re.IGNORECASE)


def _linha_contem_metadado_ou_instrucao(ln: str) -> bool:
    """Detecta se uma linha contém metadado de pacote, instrução ou placeholder."""
    if not ln or not ln.strip():
        return True
    l_strip = ln.strip()
    if re.match(r"^```|^[-=*_]{3,}$", l_strip):
        return True
    if _ROLES_CHAT.match(l_strip):
        return True
    if _RE_PLACEHOLDERS_EXEMPLO.search(l_strip):
        return True
    if re.fullmatch(r"^\s*(\[[^\]]*\]|<[^>]*>)\s*$", l_strip):
        return True
    if _RE_ID_LINHA.search(l_strip):
        return True
    l_lower = l_strip.lower()
    if any(sub in l_lower for sub in _SUBSTRINGS_DESCARTAR_LOTE):
        return True
    l_sem_bullets = re.sub(r"^[*_#`~>\-•\s]+", "", l_strip).strip()
    l_sem_nums = re.sub(r"^\d+\s*[\.\)]\s*", "", l_sem_bullets).strip().lower()
    if not l_sem_nums:
        return True
    if _PREAMBULOS_IA.match(l_sem_nums):
        return True
    if any(l_sem_nums.startswith(pref) for pref in _PREFIXOS_INSTRUCOES):
        return True
    return False


def _normalizar_texto_clipboard(texto_bruto: str) -> str:
    """Normaliza quebras de linha, espaços invisíveis do navegador/clipboard e múltiplos cortes na mesma linha."""
    if not texto_bruto:
        return ""
    t = str(texto_bruto)
    # Remove caracteres invisíveis (BOM, zero-width spaces) e normaliza non-breaking space
    for ch in ("\ufeff", "\u200b", "\u200c", "\u200d", "\u2060"):
        t = t.replace(ch, "")
    t = t.replace("\xa0", " ").replace("\u2028", "\n").replace("\u2029", "\n")
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    # Se o clipboard colou múltiplos "CORTE N:" na mesma linha separados por espaço, quebra em linhas
    t = re.sub(
        r"(?<=\S)\s+(?=(?:\*\*?)?(?:CORTE|CANDIDATO|CLIP)\s*#?\s*\d+\s*(?:\*\*?)?\s*[:\-])",
        "\n",
        t,
        flags=re.IGNORECASE,
    )
    return t.strip()


# Regex robusta e case-insensitive seguindo o padrão:
# re.match(r'^(?:(?:\*\*?)?CORTE\s*\d+[:\-]\s*(?:\*\*?)?)(.+)', linha, re.IGNORECASE)
_RE_CORTE_ESTRITO = re.compile(
    r"^(?:(?:\*\*?)?(?:CORTE|CANDIDATO|CLIP|VÍDEO|VIDEO|PARTE)\s*#?\s*(\d+)\b\s*(?:\*\*?)?\s*[:\-–—\.\)]\s*(?:\*\*?)?)\s*(.+)$",
    re.IGNORECASE,
)


def _limpar_prefixo_linha(linha: str) -> str:
    """Remove marcadores de lista/markdown iniciais e espaços antes de aplicar a Regex."""
    if not linha:
        return ""
    s = linha.strip()
    # Remove bullets ou cabeçalhos markdown no início da linha (preservando ** se fizer parte de **CORTE 1:**)
    s = re.sub(r"^(?:[#>`~•\-]+\s*)+", "", s).strip()
    # Se começar com colchete tipo "[Corte 1] Titulo", converte para "Corte 1: Titulo"
    s = re.sub(
        r"^[\[\(]\s*((?:CORTE|CANDIDATO|CLIP|VÍDEO|VIDEO|PARTE)\s*#?\s*\d+)\s*[\]\)]\s*[:\-–—]?\s*",
        r"\1: ",
        s,
        flags=re.IGNORECASE,
    )
    return s


def _eh_titulo_placeholder_ou_id(texto_titulo: str) -> bool:
    """Verifica se o texto extraído após 'CORTE N:' é apenas um placeholder do prompt ou ID técnico."""
    if not texto_titulo:
        return True
    t = texto_titulo.strip().strip("*_#`~ ").strip()
    if not t:
        return True
    if _RE_ID_LINHA.match(t) or re.match(r"^(?:·|-|–|—|\s)*ID\s*:", t, re.IGNORECASE):
        return True
    if _RE_PLACEHOLDERS_EXEMPLO.search(t):
        return True
    if not _sem_placeholder(t):
        return True
    return False


def parser_titulos_lote(texto: str, total_esperados: Optional[int] = None) -> ResultadoParseTitulos:
    """
    Extrai títulos da resposta da IA linha a linha de forma flexível e resiliente:
    - Quebra o texto bruto em linhas (splitlines()) após normalizar quebras e caracteres invisíveis.
    - Itera linha por linha, limpando markdown (**) e espaços (.strip()).
    - Prioridade 1: Linhas que dão match na regex de corte (ex: CORTE 1: Título...) ou blocos CORTE N.
      Ignora silenciosamente linhas de instrução/lixo no meio do texto.
    - Prioridade 2 (Fallback): Listas numeradas (1. Título) ou linhas limpas sequenciais.
    - Regra de Ouro: Só reporta erro de "Formato inválido" se, ao final de toda a varredura,
      a lista de títulos extraídos estiver TOTALMENTE VAZIA (len(itens) == 0).
    """
    resultado = ResultadoParseTitulos(total_esperados=int(total_esperados or 0))
    texto_norm = _normalizar_texto_clipboard(texto)
    if not texto_norm:
        resultado.avisos.append("Formato inválido: Certifique-se de copiar apenas os títulos (ex: CORTE 1: ...).")
        resultado.avisos.append("Nenhum texto informado para processar.")
        return resultado

    linhas_brutas = texto_norm.splitlines()
    itens: dict[int, TituloCorteItem] = {}

    # ─── Passagem 1: Varredura linha por linha procurando CORTE N ──────
    def fechar_bloco(idx: int, bloco: list[str], somente_explicito: bool = False):
        explicito = ""
        implicito = ""
        capa = ""
        alternativas: list[str] = []
        justificativa = ""
        for linha in bloco:
            if (m := _RE_CAPA.match(linha)):
                c_val = _sem_placeholder(limpar_texto_titulo(m.group(1)))
                if c_val and not _eh_titulo_placeholder_ou_id(c_val):
                    capa = c_val
                continue
            if (m := _RE_ALT.match(linha)):
                alt = _sem_placeholder(limpar_texto_titulo(m.group(1)))
                if alt and not _eh_titulo_placeholder_ou_id(alt) and alt not in alternativas:
                    alternativas.append(alt)
                continue
            if (m := _RE_JUST.match(linha)):
                j_val = _sem_placeholder(limpar_texto_titulo(m.group(1)))
                if j_val and not _eh_titulo_placeholder_ou_id(j_val):
                    justificativa = j_val
                continue
            if (m := _RE_TIT.match(linha)):
                t = _sem_placeholder(limpar_texto_titulo(m.group(1)))
                if t and not _eh_titulo_placeholder_ou_id(t) and not explicito:
                    explicito = t
                continue
            if eh_instrucao_ou_metadado(linha):
                continue
            if not implicito and not somente_explicito:
                c = _sem_placeholder(limpar_texto_titulo(linha))
                if len(c) >= 2 and not _eh_titulo_placeholder_ou_id(c) and not eh_instrucao_ou_metadado(c):
                    implicito = c
        titulo = explicito or implicito
        if titulo and idx > 0:
            itens[idx] = TituloCorteItem(idx, titulo, capa, alternativas, justificativa)

    idx_atual: Optional[int] = None
    bloco_atual: list[str] = []
    bloco_pacote = False
    encontrou_marcador_corte = False

    for ln_raw in linhas_brutas:
        ln_limpa = _limpar_prefixo_linha(ln_raw)
        if not ln_limpa:
            continue

        # Tenta primeiro o match direto: CORTE N: <título>
        m_direto = _RE_CORTE_ESTRITO.match(ln_limpa)
        if m_direto:
            resto = m_direto.group(2).strip().strip("*").strip()
            eh_id = bool(_RE_ID_LINHA.match(resto) or re.match(r"^(?:·|-|–|—|\s)*ID\s*:", resto, re.IGNORECASE))
            if _eh_titulo_placeholder_ou_id(resto) and not eh_id:
                continue

            encontrou_marcador_corte = True
            if idx_atual is not None:
                fechar_bloco(idx_atual, bloco_atual, bloco_pacote)
            idx_atual = int(m_direto.group(1))
            bloco_pacote = eh_id
            if not eh_id and not _eh_titulo_placeholder_ou_id(resto):
                bloco_atual = [resto]
            else:
                bloco_atual = []
            continue

        # Tenta também cabeçalho de corte sem dois-pontos (ex: "**Corte 1**" ou "CORTE 1 · ID: c1")
        m_cabecalho = _RE_CORTE.match(ln_limpa)
        if m_cabecalho:
            resto = m_cabecalho.group(2).strip().strip("*").strip()
            eh_id = bool(_RE_ID_LINHA.match(resto) or re.match(r"^(?:·|-|–|—|\s)*ID\s*:", resto, re.IGNORECASE))
            if resto and _eh_titulo_placeholder_ou_id(resto) and not eh_id:
                continue

            encontrou_marcador_corte = True
            if idx_atual is not None:
                fechar_bloco(idx_atual, bloco_atual, bloco_pacote)
            idx_atual = int(m_cabecalho.group(1))
            bloco_pacote = eh_id
            if resto and not eh_id and not _eh_titulo_placeholder_ou_id(resto):
                bloco_atual = [resto]
            else:
                bloco_atual = []
            continue

        if idx_atual is not None:
            bloco_atual.append(ln_raw.strip())

    if idx_atual is not None:
        fechar_bloco(idx_atual, bloco_atual, bloco_pacote)

    # ─── Passagem 2: Fallback (Listas numeradas ou linhas sequenciais limpas) ───
    # Executada se a varredura de CORTE N não encontrou nenhum título (ex: IA respondeu "1. Título")
    if not itens:
        texto_para_fallback = isolar_resposta_chat(texto_norm)
        linhas_fb = [ln.strip() for ln in texto_para_fallback.splitlines() if ln.strip()]
        numeradas = []
        for ln in linhas_fb:
            if _linha_contem_metadado_ou_instrucao(ln):
                continue
            m = _RE_NUM.match(ln)
            if m:
                conteudo = m.group(2).strip().strip("*").strip()
                if conteudo and not _linha_contem_metadado_ou_instrucao(conteudo):
                    numeradas.append((int(m.group(1)), conteudo))
        if numeradas:
            for n, resto in numeradas:
                t = _sem_placeholder(limpar_texto_titulo(resto))
                if len(t) >= 2 and not _linha_contem_metadado_ou_instrucao(t):
                    itens[n] = TituloCorteItem(n, t)
        elif not encontrou_marcador_corte:
            candidatas = []
            for ln in linhas_fb:
                if _linha_contem_metadado_ou_instrucao(ln):
                    continue
                t = _sem_placeholder(limpar_texto_titulo(ln))
                if len(t) >= 2 and not _linha_contem_metadado_ou_instrucao(t):
                    candidatas.append(t)
            for i, t in enumerate(candidatas, 1):
                itens[i] = TituloCorteItem(i, t)

    total = resultado.total_esperados
    if total > 0:
        excedentes = [k for k in itens if k > total]
        for k in excedentes:
            del itens[k]
        if excedentes:
            resultado.avisos.append(
                f"{len(excedentes)} título(s) referem-se a cortes inexistentes (episódio tem {total}) e foram ignorados."
            )

    resultado.itens = [itens[k] for k in sorted(itens)]
    resultado.total_encontrados = len(resultado.itens)

    # Regra de Ouro: O erro de Formato Inválido SÓ existe se len(itens) == 0
    if not resultado.itens:
        resultado.avisos.append("Formato inválido: Certifique-se de copiar apenas os títulos (ex: CORTE 1: ...).")
        tem_prompt_na_entrada = any(
            m in texto_norm.lower()
            for m in ("instruções para gerar", "instrucoes para gerar", "ordem final da montagem", "formato de resposta")
        )
        if tem_prompt_na_entrada:
            resultado.avisos.append(
                "O texto colado parece conter apenas instruções ou metadados de prompt (pacote de cortes para envio à IA)."
            )
    elif total > 0:
        faltantes = [i for i in range(1, total + 1) if i not in itens]
        if faltantes:
            resultado.avisos.append(
                f"A resposta contém {resultado.total_encontrados} título(s), mas o episódio possui {total} corte(s)."
            )
            lista = ", ".join(f"Corte {i}" for i in faltantes[:5])
            if len(faltantes) > 5:
                lista += f" (+{len(faltantes) - 5})"
            resultado.avisos.append(f"{lista} não localizado(s); permanecerá(ão) inalterado(s).")
    return resultado


def sanitizar_texto_lote(texto_bruto: str) -> str:
    """
    Sanitiza o texto bruto colado pelo usuário usando a mesma varredura resiliente:
    - Ignora linhas de instrução/metadados/lixo no meio do texto.
    - Se houver pelo menos 1 título válido extraído, retorna apenas as linhas limpas dos títulos.
    - Só retorna "" se absolutamente nenhum título válido for encontrado (len(itens) == 0).
    """
    if not texto_bruto or not str(texto_bruto).strip():
        return ""
    res = parser_titulos_lote(texto_bruto)
    if not res.itens:
        return ""

    # Se o texto original já era composto apenas por linhas limpas (sem instruções/metadados),
    # preserva a formatação original limpa; caso contrário, reconstrói apenas os cortes válidos.
    texto_norm = _normalizar_texto_clipboard(texto_bruto)
    linhas_originais = [ln.strip() for ln in texto_norm.splitlines() if ln.strip()]
    tem_lixo = any(_linha_contem_metadado_ou_instrucao(ln) for ln in linhas_originais if not _RE_CORTE_ESTRITO.match(_limpar_prefixo_linha(ln)))

    if not tem_lixo:
        return "\n".join(linhas_originais)

    linhas_limpas: list[str] = []
    for it in res.itens:
        linhas_limpas.append(f"CORTE {it.indice}: {it.titulo}")
        if it.titulo_capa:
            linhas_limpas.append(f"CAPA: {it.titulo_capa}")
        for idx_alt, alt in enumerate(it.alternativas, 1):
            linhas_limpas.append(f"ALT {idx_alt}: {alt}")
        if it.justificativa:
            linhas_limpas.append(f"JUSTIFICATIVA: {it.justificativa}")
    return "\n".join(linhas_limpas)


def construir_pacote_esteira(ep: Any, *, intro=None, opcoes: Optional[OpcoesEdicao] = None) -> tuple[str, int]:
    """Texto estruturado com TODOS os cortes do episódio (falas + timestamps) para Gemini/ChatGPT."""
    if not ep or not getattr(ep, "planos", None):
        return "", 0

    transcricao = ep.transcricao or obter_transcricao_episodio(ep)
    intro_ref = intro or ep.intro
    opcoes_ref = (opcoes or ep.opcoes_edicao or OpcoesEdicao()).validar()

    blocos = [INSTRUCOES_ESTEIRA, f"EPISÓDIO: {_episodio(transcricao) if transcricao else ep.nome}"]

    for numero, plano in enumerate(ep.planos, 1):
        timeline = None
        trechos = []
        if transcricao:
            try:
                timeline = construir_timeline(plano, transcricao, intro_ref, opcoes_ref)
                trechos = timeline.trechos
            except Exception:
                timeline = None
        duracao = timeline.duracao if timeline else (plano.fim - plano.inicio)

        linhas = [
            f"CORTE {numero} · ID: {plano.id or 'não disponível'}",
            f"Resumo: {plano.descricao or plano.motivo_escolha or 'não disponível'}",
            f"Duração final: {duracao:.2f}s",
            f"Título atual: {plano.titulo or 'não disponível'}",
            f"Texto da capa: {plano.titulo_capa or 'não disponível'}",
            "ORDEM FINAL DA MONTAGEM:",
        ]
        if transcricao and trechos:
            for i, trecho in enumerate(trechos, 1):
                papel = PAPEIS.get(trecho.funcao, trecho.funcao.upper() if trecho.funcao else "HISTÓRIA")
                falas = _fala_intervalo(transcricao, trecho.inicio, trecho.fim)
                linhas.append(f"{i}. Segmento original {_segundos(trecho.inicio)}–{_segundos(trecho.fim)}s — {papel}\n"
                              f"   Falas: {falas}")
            linhas.extend(_estrutura(plano, timeline))
        else:
            falas = _fala_intervalo(transcricao, plano.inicio, plano.fim) if transcricao else "não disponível"
            linhas.append(f"1. Intervalo original {_segundos(plano.inicio)}–{_segundos(plano.fim)}s\n   Falas: {falas}")
        blocos.append("\n".join(linhas))

    blocos.append("Lembre-se: responda no formato \"CORTE N: <título>\" para cada corte acima.")
    return "\n\n".join(blocos), len(ep.planos)


def aplicar_titulos_episodio(esteira: Any, ep_id: str, itens_titulos: list[TituloCorteItem]) -> tuple[int, list[str]]:
    """Aplica títulos como edição manual confirmada; muta sob lock, persiste e emite evento fora dele."""
    ep = esteira.obter_episodio(ep_id)
    if not ep:
        return 0, ["Episódio não encontrado na esteira."]
    if not ep.planos:
        return 0, ["O episódio não possui cortes para receber títulos."]
    if ep.estado == "em_execucao" and ep.etapa_atual in ETAPAS_EDITORIAIS_ATIVAS:
        return 0, ["O episódio está em processamento ativo na etapa editorial/títulos. Aguarde a etapa concluir."]

    avisos: list[str] = []
    mapa = {it.indice: it for it in itens_titulos if it.titulo and it.titulo.strip()}
    aplicados = 0

    with esteira._lock:
        for idx, plano in enumerate(ep.planos, 1):
            item = mapa.get(idx)
            if not item:
                continue
            novo = item.titulo.strip()
            plano.titulo_escolhido = novo
            plano.titulo_manual_confirmado = novo
            plano.titulo_origem = "manual"
            plano.titulo_aprovado_manualmente = True
            plano.titulo_auto_aprovado = False
            plano.titulo_confirmacao_pendente = False
            if item.titulo_capa:
                plano.titulo_capa = item.titulo_capa.strip()
            if item.alternativas:
                plano.titulos = [TituloOpcao(texto=novo, tipo="manual_lote")] + [
                    TituloOpcao(texto=a.strip(), tipo="alternativa") for a in item.alternativas if a.strip()
                ]
            if plano.status == STATUS_REVISAO_TITULO:
                plano.status = "pendente"
                plano.erro = ""
            plano.avisos = [a for a in (plano.avisos or [])
                            if not a.startswith(("Titulo V2:", "Título V2:"))]
            ep.planos_editados_manualmente.add(plano.id)
            aplicados += 1

    if aplicados and ep.transcricao is not None:
        try:
            salvar_lista_agencia(ep.video_path, ep.transcricao.video_id, ep.planos)
        except Exception as erro:
            avisos.append(f"Aviso ao salvar lista na Agência: {erro}")
    if aplicados:
        try:
            esteira.salvar_estado()
        except Exception as erro:
            avisos.append(f"Aviso ao salvar estado da esteira: {erro}")
        esteira._emitir_evento("titulos_lote_aplicados", ep, "titulos", f"{aplicados} títulos aplicados em lote.")
    return aplicados, avisos


# ─── LOTE GLOBAL MULTI-EPISÓDIO (.TXT E DISTRIBUIÇÃO AUTOMÁTICA) ─────────────

CABECALHO_LOTE_GLOBAL_IA = """[INSTRUÇÕES GERAIS PARA A IA]
Atue como um Especialista em Retenção de Vídeos Curtos (TikTok/Kwai).
Abaixo, você receberá os dados de MÚLTIPLOS EPISÓDIOS. Cada episódio contém sua TRANSCRIÇÃO COMPLETA (para contexto) e seus CORTES (trechos exatos).
Sua tarefa é gerar títulos altamente engajadores e virais para cada corte de cada episódio.

REGRAS DE RESPOSTA (OBRIGATÓRIO MANTENHA ESTE FORMATO EXATO):
[EPISÓDIO: NOME_DO_ARQUIVO.mp4]
CORTE 1: <Seu Título Aqui>
CORTE 2: <Seu Título Aqui>
...

=========================================
[DADOS DOS EPISÓDIOS]"""

_RE_EPISODIO_GLOBAL = re.compile(r"\[EPIS[ÓO]DIO:\s*(.+?)\]", re.IGNORECASE)
_RE_EPISODIO_GLOBAL_FALLBACK = re.compile(
    r"^(?:[*_#>`~\-•\s]*)\[?EPIS[ÓO]DIO\s*[:\-–—]\s*([^\n\]]+?)\]?\s*[*_#`]*$",
    re.IGNORECASE,
)
_RE_CORTE_GLOBAL = re.compile(
    r"^(?:(?:\*\*?)?(?:CORTE|CANDIDATO|CLIP|VÍDEO|VIDEO|PARTE)\s*#?\s*(\d+)\b\s*(?:\*\*?)?\s*[:\-–—\.\)]\s*(?:\*\*?)?)\s*(.+)$",
    re.IGNORECASE,
)
_RE_CODIGO_EPISODIO = re.compile(r"\bS\d{1,2}E\d{1,3}\b", re.IGNORECASE)


@dataclass
class ResultadoEpisodioGlobal:
    nome_capturado: str
    ep_id: str = ""
    ep_nome: str = ""
    itens: list[TituloCorteItem] = field(default_factory=list)
    total_esperados: int = 0
    avisos: list[str] = field(default_factory=list)


@dataclass
class ResultadoParseLoteGlobal:
    episodios: list[ResultadoEpisodioGlobal] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    total_episodios_encontrados: int = 0
    total_episodios_vinculados: int = 0
    total_titulos_encontrados: int = 0


def formatar_cortes_episodio_lote(
    ep: Any,
    *,
    transcricao: Any = None,
    intro: Any = None,
    opcoes: Optional[OpcoesEdicao] = None,
) -> tuple[str, int]:
    """Formata apenas os blocos de cortes de um episódio (sem o cabeçalho individual INSTRUCOES_ESTEIRA)."""
    if not ep or not getattr(ep, "planos", None):
        return "", 0

    transc_ref = transcricao or getattr(ep, "transcricao", None) or obter_transcricao_episodio(ep)
    intro_ref = intro or getattr(ep, "intro", None)
    opcoes_raw = opcoes or getattr(ep, "opcoes_edicao", None) or OpcoesEdicao()
    opcoes_ref = opcoes_raw.validar() if hasattr(opcoes_raw, "validar") else opcoes_raw

    blocos_cortes: list[str] = []
    for numero, plano in enumerate(ep.planos, 1):
        timeline = None
        trechos = []
        if transc_ref:
            try:
                timeline = construir_timeline(plano, transc_ref, intro_ref, opcoes_ref)
                trechos = timeline.trechos
            except Exception:
                timeline = None
        duracao = timeline.duracao if timeline else (plano.fim - plano.inicio)

        linhas = [
            f"CORTE {numero} · ID: {plano.id or 'não disponível'}",
            f"Resumo: {plano.descricao or plano.motivo_escolha or 'não disponível'}",
            f"Duração final: {duracao:.2f}s",
            f"Título atual: {plano.titulo or 'não disponível'}",
            f"Texto da capa: {plano.titulo_capa or 'não disponível'}",
            "ORDEM FINAL DA MONTAGEM:",
        ]
        if transc_ref and trechos:
            for i, trecho in enumerate(trechos, 1):
                papel = PAPEIS.get(trecho.funcao, trecho.funcao.upper() if trecho.funcao else "HISTÓRIA")
                falas = _fala_intervalo(transc_ref, trecho.inicio, trecho.fim)
                linhas.append(
                    f"{i}. Segmento original {_segundos(trecho.inicio)}–{_segundos(trecho.fim)}s — {papel}\n"
                    f"   Falas: {falas}"
                )
            linhas.extend(_estrutura(plano, timeline))
        else:
            falas = _fala_intervalo(transc_ref, plano.inicio, plano.fim) if transc_ref else "não disponível"
            linhas.append(
                f"1. Intervalo original {_segundos(plano.inicio)}–{_segundos(plano.fim)}s\n"
                f"   Falas: {falas}"
            )
        blocos_cortes.append("\n".join(linhas))

    return "\n\n".join(blocos_cortes), len(ep.planos)


def construir_lote_global_txt(episodios: list[Any]) -> tuple[str, int, int, list[str]]:
    """
    Constrói o conteúdo completo do arquivo .txt de Lote Global (Transcrições + Cortes + Prompt IA)
    para todos os episódios da esteira.
    Retorna: (conteudo_txt, qtd_episodios_incluidos, total_cortes_incluidos, avisos)
    """
    avisos: list[str] = []
    if not episodios:
        return "", 0, 0, ["Nenhum episódio carregado na esteira."]

    partes: list[str] = [CABECALHO_LOTE_GLOBAL_IA]
    qtd_episodios = 0
    total_cortes = 0

    for ep in episodios:
        nome_do_arquivo = (
            getattr(ep, "nome", "")
            or (Path(getattr(ep, "video_path", "")).name if getattr(ep, "video_path", "") else "")
            or f"episodio_{getattr(ep, 'id', qtd_episodios + 1)}.mp4"
        )
        transcricao = getattr(ep, "transcricao", None) or obter_transcricao_episodio(ep)
        if transcricao and getattr(transcricao, "segmentos", None):
            try:
                texto_da_transcricao = formatar_transcricao_para_gemini(
                    transcricao, nome_episodio=nome_do_arquivo
                )
            except Exception as erro:
                texto_da_transcricao = f"(Erro ao formatar transcrição: {erro})"
                avisos.append(f"'{nome_do_arquivo}': falha ao formatar transcrição ({erro}).")
        else:
            texto_da_transcricao = "(Transcrição ainda não disponível para este episódio)"
            avisos.append(f"'{nome_do_arquivo}': transcrição ainda não disponível.")

        texto_dos_cortes, qtd_cortes_ep = formatar_cortes_episodio_lote(ep, transcricao=transcricao)
        if not texto_dos_cortes:
            texto_dos_cortes = "(Nenhum corte gerado para este episódio até o momento)"
            avisos.append(f"'{nome_do_arquivo}': nenhum corte disponível.")

        bloco_ep = (
            f"[EPISÓDIO: {nome_do_arquivo}]\n"
            f"--- TRANSCRIÇÃO ---\n"
            f"{texto_da_transcricao}\n\n"
            f"--- CORTES ---\n"
            f"{texto_dos_cortes}\n\n"
            f"========================================="
        )
        partes.append(bloco_ep)
        qtd_episodios += 1
        total_cortes += qtd_cortes_ep

    return "\n".join(partes) + "\n", qtd_episodios, total_cortes, avisos


def exportar_lote_global_arquivo(esteira: Any, caminho_arquivo: str | Path) -> tuple[int, int, list[str]]:
    """Gera e salva em disco o arquivo .txt do lote global de todos os episódios da esteira."""
    episodios = esteira.listar_episodios() if hasattr(esteira, "listar_episodios") else list(esteira or [])
    conteudo, qtd_ep, total_cortes, avisos = construir_lote_global_txt(episodios)
    if not conteudo or qtd_ep == 0:
        return 0, 0, avisos or ["Nenhum episódio disponível para exportar."]

    destino = Path(caminho_arquivo)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(conteudo, encoding="utf-8")
    return qtd_ep, total_cortes, avisos


def _normalizar_nome_episodio_match(nome: str) -> str:
    """Normaliza o nome do arquivo de episódio para comparação resiliente."""
    if not nome:
        return ""
    s = str(nome).strip().strip("[]()*_\"'` ").strip()
    s = Path(s).name.strip()
    return " ".join(s.lower().split())


def _eh_nome_episodio_placeholder(nome: str) -> bool:
    n = _normalizar_nome_episodio_match(nome)
    if not n:
        return True
    if n in (
        "nome_do_arquivo.mp4",
        "nome_do_arquivo",
        "{nome_do_arquivo}",
        "<nome_do_arquivo>",
        "<nome_do_arquivo.mp4>",
        "exemplo.mp4",
    ):
        return True
    if "nome_do_arquivo" in n or "{nome" in n:
        return True
    return False


def localizar_episodio_por_nome(nome_capturado: str, episodios: list[Any]) -> Optional[Any]:
    """
    Localiza o objeto ContextoEpisodio correspondente na lista da esteira pelo nome do arquivo.
    Tenta em ordem:
    1. Match exato case-insensitive pelo nome completo (com extensão)
    2. Match exato pelo stem (sem extensão .mp4/.mkv/.mov)
    3. Match por inclusão/sufixo ou código SxxExx único na esteira
    """
    if not nome_capturado or not episodios:
        return None
    alvo_norm = _normalizar_nome_episodio_match(nome_capturado)
    alvo_stem = Path(alvo_norm).stem.strip()
    if not alvo_norm:
        return None

    # 1. Match exato pelo nome do arquivo
    for ep in episodios:
        ep_nome = _normalizar_nome_episodio_match(
            getattr(ep, "nome", "") or Path(getattr(ep, "video_path", "")).name
        )
        if ep_nome == alvo_norm:
            return ep

    # 2. Match exato pelo stem (sem extensão)
    for ep in episodios:
        ep_nome = _normalizar_nome_episodio_match(
            getattr(ep, "nome", "") or Path(getattr(ep, "video_path", "")).name
        )
        if Path(ep_nome).stem.strip() == alvo_stem and alvo_stem:
            return ep

    # 3. Match por código SxxExx se for único entre os episódios carregados
    m_cod = _RE_CODIGO_EPISODIO.search(alvo_norm)
    if m_cod:
        codigo = m_cod.group(0).lower()
        candidatos_cod = [
            ep for ep in episodios
            if codigo in _normalizar_nome_episodio_match(
                getattr(ep, "nome", "") or Path(getattr(ep, "video_path", "")).name
            )
        ]
        if len(candidatos_cod) == 1:
            return candidatos_cod[0]

    # 4. Match por substring não-ambíguo
    candidatos_sub = []
    for ep in episodios:
        ep_nome = _normalizar_nome_episodio_match(
            getattr(ep, "nome", "") or Path(getattr(ep, "video_path", "")).name
        )
        ep_stem = Path(ep_nome).stem.strip()
        if (alvo_stem and ep_stem and (alvo_stem in ep_stem or ep_stem in alvo_stem)):
            candidatos_sub.append(ep)
    if len(candidatos_sub) == 1:
        return candidatos_sub[0]

    return None


def parser_titulos_lote_global(
    texto: str,
    episodios_esteira: Optional[list[Any]] = None,
) -> ResultadoParseLoteGlobal:
    """
    Varre o texto global colado buscando:
    1. Padrão de episódio: r"\\[EPISÓDIO:\\s*(.+?)\\]" (ou fallback EPISÓDIO: <nome>)
    2. Linhas subsequentes com padrão de cortes: r"^(?:(?:\\*\\*?)?CORTE\\s*\\d+[:\\-]\\s*(?:\\*\\*?)?)(.+)"
    Associa cada bloco ao respectivo episódio da esteira pelo nome do arquivo.
    """
    resultado = ResultadoParseLoteGlobal()
    texto_norm = _normalizar_texto_clipboard(texto)
    if not texto_norm:
        resultado.avisos.append(
            "Formato inválido: Cole a resposta da IA contendo [EPISÓDIO: nome.mp4] e CORTE 1: ..."
        )
        return resultado

    # Quebra caso o clipboard tenha colado "[EPISÓDIO: ...]" e "CORTE 1:" na mesma linha
    texto_norm = re.sub(r"(\[EPIS[ÓO]DIO:\s*[^\]]+\])\s*(?=\S)", r"\1\n", texto_norm, flags=re.IGNORECASE)

    linhas = texto_norm.splitlines()
    ordem_chaves: list[str] = []
    mapa_blocos: dict[str, dict[int, TituloCorteItem]] = {}
    nomes_originais: dict[str, str] = {}

    chave_ep_atual: Optional[str] = None
    item_corte_atual: Optional[TituloCorteItem] = None

    for ln_raw in linhas:
        ln_strip = ln_raw.strip()
        if not ln_strip:
            continue

        # 1. Verifica marcador de episódio: r"\[EPISÓDIO:\s*(.+?)\]"
        m_ep = _RE_EPISODIO_GLOBAL.search(ln_strip)
        if not m_ep:
            m_ep = _RE_EPISODIO_GLOBAL_FALLBACK.match(ln_strip)

        if m_ep:
            nome_cap = m_ep.group(1).strip().strip("*_#`\"' ").strip()
            item_corte_atual = None
            if _eh_nome_episodio_placeholder(nome_cap):
                chave_ep_atual = None
                continue
            chave = _normalizar_nome_episodio_match(nome_cap)
            chave_ep_atual = chave
            if chave not in mapa_blocos:
                ordem_chaves.append(chave)
                mapa_blocos[chave] = {}
                nomes_originais[chave] = nome_cap
            continue

        if chave_ep_atual is None:
            continue

        # 2. Dentro de um episódio ativo, verifica linha de corte:
        # r"^(?:(?:\*\*?)?CORTE\s*\d+[:\-]\s*(?:\*\*?)?)(.+)"
        ln_limpa = _limpar_prefixo_linha(ln_strip)
        m_corte = _RE_CORTE_GLOBAL.match(ln_limpa)
        if m_corte:
            indice = int(m_corte.group(1))
            resto = m_corte.group(2).strip().strip("*").strip()
            eh_id = bool(_RE_ID_LINHA.match(resto) or re.match(r"^(?:·|-|–|—|\s)*ID\s*:", resto, re.IGNORECASE))
            if eh_id or _eh_titulo_placeholder_ou_id(resto):
                item_corte_atual = None
                continue
            titulo_limpo = _sem_placeholder(limpar_texto_titulo(resto))
            if not titulo_limpo or _eh_titulo_placeholder_ou_id(titulo_limpo):
                item_corte_atual = None
                continue
            novo_item = TituloCorteItem(indice=indice, titulo=titulo_limpo)
            mapa_blocos[chave_ep_atual][indice] = novo_item
            item_corte_atual = novo_item
            continue

        # Suporte opcional para CAPA / ALT / JUSTIFICATIVA abaixo de CORTE N
        if item_corte_atual is not None:
            if (m_c := _RE_CAPA.match(ln_strip)):
                c_val = _sem_placeholder(limpar_texto_titulo(m_c.group(1)))
                if c_val and not _eh_titulo_placeholder_ou_id(c_val):
                    item_corte_atual.titulo_capa = c_val
                continue
            if (m_a := _RE_ALT.match(ln_strip)):
                a_val = _sem_placeholder(limpar_texto_titulo(m_a.group(1)))
                if a_val and not _eh_titulo_placeholder_ou_id(a_val) and a_val not in item_corte_atual.alternativas:
                    item_corte_atual.alternativas.append(a_val)
                continue
            if (m_j := _RE_JUST.match(ln_strip)):
                j_val = _sem_placeholder(limpar_texto_titulo(m_j.group(1)))
                if j_val and not _eh_titulo_placeholder_ou_id(j_val):
                    item_corte_atual.justificativa = j_val
                continue

    lista_ep_esteira = list(episodios_esteira or [])
    for chave in ordem_chaves:
        cortes_dict = mapa_blocos.get(chave, {})
        if not cortes_dict:
            continue
        nome_cap = nomes_originais[chave]
        ep_match = localizar_episodio_por_nome(nome_cap, lista_ep_esteira) if lista_ep_esteira else None
        total_esp = len(ep_match.planos) if (ep_match and getattr(ep_match, "planos", None)) else 0

        avisos_ep: list[str] = []
        if total_esp > 0:
            excedentes = [idx for idx in list(cortes_dict.keys()) if idx > total_esp]
            for idx in excedentes:
                del cortes_dict[idx]
            if excedentes:
                avisos_ep.append(
                    f"{len(excedentes)} título(s) acima do limite de {total_esp} corte(s) foram ignorados."
                )

        itens_ordenados = [cortes_dict[k] for k in sorted(cortes_dict)]
        if not itens_ordenados:
            continue

        if ep_match is None and lista_ep_esteira:
            avisos_ep.append(f"Episódio '{nome_cap}' não encontrado na fila atual da esteira.")

        res_ep = ResultadoEpisodioGlobal(
            nome_capturado=nome_cap,
            ep_id=getattr(ep_match, "id", "") if ep_match else "",
            ep_nome=getattr(ep_match, "nome", "") if ep_match else "",
            itens=itens_ordenados,
            total_esperados=total_esp,
            avisos=avisos_ep,
        )
        resultado.episodios.append(res_ep)

    resultado.total_episodios_encontrados = len(resultado.episodios)
    resultado.total_episodios_vinculados = sum(1 for e in resultado.episodios if e.ep_id)
    resultado.total_titulos_encontrados = sum(len(e.itens) for e in resultado.episodios)

    if resultado.total_titulos_encontrados == 0:
        resultado.avisos.append(
            "Nenhum bloco válido no formato [EPISÓDIO: nome.mp4] + CORTE 1: Título foi encontrado."
        )
    else:
        nao_vinculados = [e.nome_capturado for e in resultado.episodios if not e.ep_id and lista_ep_esteira]
        if nao_vinculados:
            resultado.avisos.append(
                f"{len(nao_vinculados)} episódio(s) do texto não foram localizados na esteira: "
                + ", ".join(nao_vinculados[:5])
            )

    return resultado


def aplicar_titulos_lote_global(
    esteira: Any,
    texto_ou_resultado: str | ResultadoParseLoteGlobal,
) -> tuple[int, int, list[str], list[str]]:
    """
    Distribui os títulos globais diretamente para cada episódio correspondente na esteira.
    Opera sobre a lista central de instâncias de episódios da esteira sem Signal Leak.
    Retorna: (qtd_episodios_atualizados, total_titulos_aplicados, ids_episodios_atualizados, avisos)
    """
    episodios_esteira = esteira.listar_episodios() if hasattr(esteira, "listar_episodios") else []
    if isinstance(texto_ou_resultado, str):
        res_global = parser_titulos_lote_global(texto_ou_resultado, episodios_esteira=episodios_esteira)
    else:
        res_global = texto_ou_resultado

    if not res_global or not res_global.episodios:
        return 0, 0, [], (res_global.avisos if res_global else ["Nenhum título global para aplicar."])

    avisos_gerais: list[str] = list(res_global.avisos)
    qtd_episodios_atualizados = 0
    total_titulos_aplicados = 0
    ids_atualizados: list[str] = []

    for ep_res in res_global.episodios:
        ep_id = ep_res.ep_id
        if not ep_id and episodios_esteira:
            ep_match = localizar_episodio_por_nome(ep_res.nome_capturado, episodios_esteira)
            if ep_match:
                ep_id = ep_match.id
        if not ep_id:
            continue

        aplicados, avisos_ep = aplicar_titulos_episodio(esteira, ep_id, ep_res.itens)
        if aplicados > 0:
            ep_obj = esteira.obter_episodio(ep_id)
            if ep_obj is not None:
                with esteira._lock:
                    ep_obj.mensagem_status = f"{aplicados} cortes titulados — pronto para aprovar e exportar"
            qtd_episodios_atualizados += 1
            total_titulos_aplicados += aplicados
            if ep_id not in ids_atualizados:
                ids_atualizados.append(ep_id)
        for av in avisos_ep:
            avisos_gerais.append(f"[{ep_res.ep_nome or ep_res.nome_capturado}] {av}")

    if qtd_episodios_atualizados > 0:
        try:
            esteira.salvar_estado()
        except Exception as erro:
            avisos_gerais.append(f"Aviso ao salvar estado da esteira: {erro}")

    return qtd_episodios_atualizados, total_titulos_aplicados, ids_atualizados, avisos_gerais
