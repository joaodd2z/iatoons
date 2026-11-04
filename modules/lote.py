"""Persistência e nomes seguros para listas de cortes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from modules.cortes import Corte
from modules.preset import DADOS_DIR


LOTES_DIR = DADOS_DIR / "lotes"


def caminho_lista(video_path: str) -> Path:
    video = Path(video_path).resolve()
    stat = video.stat()
    chave = hashlib.sha256(f"{video}|{stat.st_size}|{stat.st_mtime_ns}".encode()).hexdigest()
    return LOTES_DIR / f"{chave}.json"


def salvar_lista(video_path: str, cortes: list[Corte]) -> Path:
    destino = caminho_lista(video_path)
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporario = destino.with_suffix(".json.tmp")
    temporario.write_text(
        json.dumps({"video_path": str(Path(video_path).resolve()),
                    "cortes": [c.como_dict() for c in cortes]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporario.replace(destino)
    return destino


from modules.performance import medir_operacao, anotar


@medir_operacao("cache_lote")
def carregar_lista(video_path: str) -> list[Corte]:
    origem = caminho_lista(video_path)
    anotar(cache="hit" if origem.exists() else "miss",
           arquivos_reutilizados=[str(origem)] if origem.exists() else [])
    if not origem.exists():
        return []
    dados = json.loads(origem.read_text(encoding="utf-8"))
    if Path(dados.get("video_path", "")).resolve() != Path(video_path).resolve():
        raise ValueError("A lista armazenada pertence a outro vídeo.")
    return [Corte(**{**item, "origem": item.get("origem", "legado_desconhecido"),
                    "remix_rules_version": "", "validacao_remix": {}})
            for item in dados.get("cortes", [])]


def validar_exportacao_automatica(corte: Corte, transcricao, trechos, legendas, intro=None):
    """Gate do lote, sobre os intervalos EXATOS enviados ao renderer legado."""
    automatico = (corte.origem != "manual" or corte.modo_edicao == "remix" or
                  bool(corte.motivo_ia or corte.opcoes_titulo or corte.gancho_ia or
                       corte.inicio_original_ia is not None or corte.remix_segmentos))
    if not automatico:
        return {"manual": True, "remix_valido": False}
    from modules.titulos_v2 import exigir_titulo_principal
    exigir_titulo_principal(corte)
    from modules.agencia.editorial import PlanoAgencia
    from modules.agencia.remix import avaliar_remix_real
    from modules.agencia.timeline import TimelineAgencia, TrechoTimeline
    if corte.modo_edicao != "remix" or not corte.remix_segmentos:
        raise ValueError("Corte automatico sem roteiro de remix real; exportacao bloqueada.")
    if transcricao is None:
        raise ValueError("Corte automatico precisa da transcricao para validar a timeline.")
    plano = PlanoAgencia(inicio=corte.inicio, fim=corte.fim, segmentos=corte.remix_segmentos,
                        estrutura=corte.estrutura_remix, usar_remix=True, origem="remix",
                        video_id=corte.video_id,
                        gancho={"inicio": corte.remix_gancho_inicio, "fim": corte.remix_gancho_fim})
    timeline = TimelineAgencia([TrechoTimeline(t.inicio, t.fim, "gancho" if n == 0 else "historia")
                               for n, t in enumerate(trechos)], "remix",
                              gancho=(corte.remix_gancho_inicio, corte.remix_gancho_fim))
    contrato = avaliar_remix_real(plano, timeline, transcricao, legendas, intro)
    corte.validacao_remix = contrato
    corte.remix_rules_version = contrato["remix_rules_version"] if contrato["remix_valido"] else ""
    if not contrato["remix_valido"]:
        raise ValueError("Corte automatico reprovado: " + contrato["motivo_rejeicao"])
    return contrato


def caminho_sem_sobrescrever(pasta: str | Path, nome_base: str, extensao: str,
                             extensoes_adicionais: tuple[str, ...] = ()) -> Path:
    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    seguro = "".join(c for c in nome_base if c.isalnum() or c in "-_ ").strip().replace(" ", "_")
    seguro = seguro or "corte"
    candidato = pasta / f"{seguro}{extensao}"
    indice = 2
    def ocupado(path: Path) -> bool:
        return path.exists() or any(path.with_suffix(ext).exists() for ext in extensoes_adicionais)

    while ocupado(candidato):
        candidato = pasta / f"{seguro}_{indice}{extensao}"
        indice += 1
    return candidato
