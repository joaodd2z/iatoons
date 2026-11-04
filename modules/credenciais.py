"""Credenciais privadas armazenadas pelo cofre nativo do Windows."""

from __future__ import annotations

import keyring
import os


SERVICO = "IA_TOONS_CUT"
CONTA_GEMINI = "gemini_api_key"
CONTA_MISTRAL = "mistral_api_key"
CONTA_GROQ = "groq_api_key"
CONTAS = {"gemini": CONTA_GEMINI, "mistral": CONTA_MISTRAL, "groq": CONTA_GROQ}
VARIAVEIS = {"gemini": "GEMINI_API_KEY", "mistral": "MISTRAL_API_KEY", "groq": "GROQ_API_KEY"}


class ErroCredencial(RuntimeError):
    pass


def salvar_chave_gemini(chave: str) -> None:
    chave = (chave or "").strip()
    if len(chave) < 20:
        raise ValueError("Informe uma chave Gemini válida.")
    try:
        keyring.set_password(SERVICO, CONTA_GEMINI, chave)
    except Exception:
        raise ErroCredencial("Não foi possível salvar a chave no cofre de credenciais do Windows.") from None


def obter_chave_gemini() -> str | None:
    return obter_chave("gemini")


def _provider(provider: str) -> str:
    nomes = {**{nome: nome for nome in CONTAS}, **{valor: nome for nome, valor in VARIAVEIS.items()}}
    nomes[CONTA_GROQ] = "groq"
    if provider not in nomes:
        raise ValueError("Provider de credencial não suportado.")
    return nomes[provider]


def obter_chave(provider: str) -> str | None:
    provider = _provider(provider)
    if provider == "groq":
        chave, _origem = _selecionar_chave_groq()
        return normalizar_chave_groq(chave) or None
    # Variáveis são lidas sem persistência nem migração automática para o cofre.
    chave = os.environ.get(VARIAVEIS[provider], "").strip()
    if chave:
        return chave
    try:
        return keyring.get_password(SERVICO, CONTAS[provider])
    except Exception:
        raise ErroCredencial("Não foi possível acessar o cofre de credenciais do Windows.") from None


def salvar_chave(provider: str, chave: str) -> None:
    provider = _provider(provider)
    chave = normalizar_chave_groq(chave) if provider == "groq" else (chave or "").strip()
    if len(chave) < 20:
        raise ValueError("Informe uma chave de API válida.")
    try:
        keyring.set_password(SERVICO, CONTAS[provider], chave)
    except Exception:
        raise ErroCredencial("Não foi possível salvar a chave no cofre de credenciais do Windows.") from None


def normalizar_chave_groq(chave: str | None) -> str:
    """Remove apenas espaços externos e aspas de cópia; rejeita caracteres extras."""
    chave = (chave or "").strip()
    if len(chave) >= 2 and chave[0] == chave[-1] and chave[0] in ("'", '"'):
        chave = chave[1:-1].strip()
    if any(c.isspace() or not c.isprintable() or not c.isascii() or c in ("'", '"') for c in chave):
        raise ValueError("A chave Groq contém espaços internos, aspas ou caracteres extras. Copie somente a chave.")
    return chave


def _selecionar_chave_groq() -> tuple[str | None, str]:
    # A chave salva explicitamente na UI prevalece sobre uma variável antiga.
    # Não tenta contas alternativas nem migra credenciais automaticamente.
    try:
        chave = keyring.get_password(SERVICO, CONTA_GROQ)
    except Exception:
        # Uma falha de acesso ao cofre não equivale a uma credencial ausente.
        raise ErroCredencial("Não foi possível acessar o cofre de credenciais do Windows.") from None
    if chave and chave.strip():
        return chave, "Gerenciador de Credenciais"
    chave = os.environ.get("GROQ_API_KEY", "")
    if chave.strip():
        return chave, "env"
    return None, "outro"


def diagnosticar_chave_groq() -> dict:
    """Retorna só presença, tamanho, prefixo mascarado e origem; nunca a chave."""
    chave, origem = _selecionar_chave_groq()
    try:
        chave = normalizar_chave_groq(chave)
    except ValueError:
        # Permite diagnosticar uma credencial malformada sem exibir seu conteúdo.
        chave = (chave or "").strip()
    mascara = "gsk_***" + chave[-4:] if chave.startswith("gsk_") and len(chave) > 12 and chave[-4:].isalnum() else "***"
    return {"encontrada": bool(chave), "comprimento": len(chave),
            "prefixo_mascarado": mascara if chave else "—", "origem": origem}


def excluir_chave(provider: str) -> None:
    provider = _provider(provider)
    try:
        if keyring.get_password(SERVICO, CONTAS[provider]):
            keyring.delete_password(SERVICO, CONTAS[provider])
    except Exception:
        raise ErroCredencial("Não foi possível remover a chave do cofre de credenciais do Windows.") from None


def excluir_chave_gemini() -> None:
    try:
        if keyring.get_password(SERVICO, CONTA_GEMINI):
            keyring.delete_password(SERVICO, CONTA_GEMINI)
    except Exception:
        raise ErroCredencial("Não foi possível remover a chave do cofre de credenciais do Windows.") from None
