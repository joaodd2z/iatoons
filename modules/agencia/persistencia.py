"""Listas do Modo Agência, separadas das listas do corte normal e do remix."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from modules.agencia.editorial import AGENCIA_DIR, PlanoAgencia


LISTAS_AGENCIA_DIR = AGENCIA_DIR / "listas"


def _chave(video_path: str) -> str:
    caminho = Path(video_path).resolve()
    stat = caminho.stat()
    return hashlib.sha256(f"{caminho}|{stat.st_size}|{stat.st_mtime_ns}".encode()).hexdigest()


def salvar_lista_agencia(video_path: str, video_id: str, planos: list[PlanoAgencia]) -> Path:
    destino = LISTAS_AGENCIA_DIR / f"{_chave(video_path)}.json"
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({
        "video_path": str(Path(video_path).resolve()), "video_id": video_id,
        "planos": [p.como_dict() for p in planos],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(destino)
    return destino


def carregar_lista_agencia(video_path: str, video_id: str | None = None) -> list[PlanoAgencia]:
    origem = LISTAS_AGENCIA_DIR / f"{_chave(video_path)}.json"
    if not origem.exists():
        return []
    dados = json.loads(origem.read_text(encoding="utf-8"))
    if Path(dados.get("video_path", "")).resolve() != Path(video_path).resolve():
        raise ValueError("A lista do Modo Agência pertence a outro vídeo.")
    if video_id and dados.get("video_id") not in (None, video_id):
        raise ValueError("A lista do Modo Agência pertence a outra transcrição/episódio.")
    planos = [PlanoAgencia.de_dict(item) for item in dados.get("planos", [])]
    for plano in planos:
        plano.remix_rules_version = ""
        plano.validacao_remix = {}
    # Um plano de outro episódio nunca é reaproveitado, mesmo dentro do arquivo.
    return [p for p in planos if not video_id or p.video_id in ("", video_id)]
