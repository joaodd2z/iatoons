"""Isolamento do estado e das tarefas pertencentes ao vídeo atualmente importado."""

from pathlib import Path


def caminho_normalizado(caminho: str | None) -> str:
    return str(Path(caminho).resolve()) if caminho else ""


def iniciar_sessao_video(estado: dict, caminho: str, info) -> int:
    """Substitui o episódio ativo e elimina todo estado derivado do anterior."""
    sessao = int(estado.get("video_session", 0)) + 1
    estado.update({
        "video_session": sessao,
        "video_path": caminho_normalizado(caminho),
        "video_info": info,
        "inicio": 0.0,
        "fim": float(info.duracao),
        "titulo": "",
        "transcricao": None,
        "intro_intervalo": None,
    })
    return sessao


def sessao_corresponde(estado: dict, sessao: int, caminho: str) -> bool:
    """Impede resultados assíncronos atrasados de contaminarem outro episódio."""
    return (
        int(estado.get("video_session", 0)) == int(sessao)
        and caminho_normalizado(estado.get("video_path")) == caminho_normalizado(caminho)
    )
