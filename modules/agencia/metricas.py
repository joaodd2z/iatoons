"""Métricas internas por corte, para comparar quais padrões performam melhor.

Nada disso aparece no vídeo: fica em ``<saida>.metricas.json`` e no histórico
``dados/agencia/metricas.jsonl``.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from modules.agencia.editorial import AGENCIA_DIR, PlanoAgencia
from modules.agencia.timeline import TimelineAgencia


HISTORICO_METRICAS = AGENCIA_DIR / "metricas.jsonl"


def montar_metricas(plano: PlanoAgencia, timeline: TimelineAgencia, perfil_id: str,
                    preset_av: str, relatorio: dict | None = None, gate: list[dict] | None = None,
                    enquadramento: dict | None = None, capa: str = "") -> dict:
    transicoes = timeline.transicoes()
    return {
        "criado_em": datetime.now(timezone.utc).isoformat(),
        "video_id": plano.video_id,
        "corte_id": plano.id,
        "tipo_edicao": timeline.tipo,
        "remix_rules_version": plano.remix_rules_version,
        "validacao_remix": plano.validacao_remix,
        "duracao": round(timeline.duracao, 3),
        "gancho_utilizado": ({"inicio": timeline.gancho[0], "fim": timeline.gancho[1],
                              "repetido": timeline.gancho_repetido,
                              "motivo": (plano.gancho or {}).get("motivo", "")}
                             if timeline.gancho else None),
        "numero_segmentos": len(timeline.trechos),
        "numero_cortes_internos": timeline.cortes_internos,
        "transicoes_utilizadas": {
            "dissolve": sum(1 for t in transicoes if t["tipo"] == "dissolve"),
            "corte_seco": sum(1 for t in transicoes if t["tipo"] == "corte_seco"),
            "detalhes": transicoes,
        },
        "titulo_escolhido": plano.titulo,
        "tipo_titulo": next((t.tipo for t in plano.titulos if t.texto == plano.titulo), "manual"),
        "titulos_alternativos": [{"texto": t.texto, "tipo": t.tipo} for t in plano.titulos],
        "perfil_visual": perfil_id,
        "preset_av": preset_av,
        "motivo_escolha": plano.motivo_escolha or plano.descricao,
        "remix_motivo": plano.remix_motivo,
        "origem_plano": plano.origem,
        "trechos_removidos": [{**r, "duracao": round(r["fim"] - r["inicio"], 3)} for r in timeline.removidos],
        "enquadramento": enquadramento or {},
        "capa": capa,
        "relatorio_exportacao": relatorio or {},
        "quality_gate": gate or [],
    }


from modules.performance import medir_operacao


@medir_operacao("pos_processamento_metricas")
def salvar_metricas(caminho_saida: str | Path, metricas: dict) -> Path:
    destino = Path(caminho_saida).with_suffix(".metricas.json")
    destino.write_text(json.dumps(metricas, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        HISTORICO_METRICAS.parent.mkdir(parents=True, exist_ok=True)
        with open(HISTORICO_METRICAS, "a", encoding="utf-8") as arquivo:
            arquivo.write(json.dumps({**metricas, "arquivo": str(caminho_saida)}, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return destino
