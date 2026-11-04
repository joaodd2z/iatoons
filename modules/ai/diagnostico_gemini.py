"""Classificação segura exclusiva do teste de conexão Gemini."""
import socket

from .errors import erro_seguro


def categoria_erro(erro):
    codigo = (getattr(erro, "codigo", None) or getattr(erro, "status_code", None)
              or getattr(erro, "code", None))
    if codigo in (401, 403):
        return "401/403"
    if codigo == 429:
        return "429"
    if codigo == 404:
        return "modelo indisponível"
    if isinstance(codigo, int) and 500 <= codigo <= 599:
        return "erro 5xx"
    if codigo == 408 or getattr(erro, "tipo", None) == "timeout":
        return "timeout"
    if getattr(erro, "tipo", None) == "rede":
        return "DNS/rede"
    vistos = set()
    atual = erro
    while atual is not None and id(atual) not in vistos:
        vistos.add(id(atual))
        seguro = erro_seguro(atual, "gemini")
        if seguro.tipo == "timeout":
            return "timeout"
        if seguro.tipo == "rede" or isinstance(atual, socket.gaierror):
            return "DNS/rede"
        atual = atual.__cause__ or atual.__context__
    return "erro desconhecido"


def erro_conexao_seguro(erro):
    seguro = erro_seguro(erro, "gemini")
    seguro.categoria = categoria_erro(erro)
    return seguro
