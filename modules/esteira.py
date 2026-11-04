"""
esteira.py — Esteira Inteligente de Produção Multi-Episódio (Pipeline Assíncrono).

Coordena múltiplos episódios de forma contínua e independente:
- Cada episódio avança automaticamente para a próxima etapa elegível assim que a anterior termina.
- Limites rigorosos de concorrência globais (compartilhados com as abas individuais via CoordenadorRecursos):
  * Transcrições simultâneas: 1
  * Análise Agência / Editorial simultânea: 1 (executa em paralelo com Transcrição e Renderização)
  * Renderizações simultâneas: 1
- Isolamento total de contexto por episódio (nenhuma dependência do episódio ativo da UI).
- Recuperação granular por etapa e por corte, cancelamento individual/global e persistência segura.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import inspect
import json
from pathlib import Path
import threading
import time
from time import perf_counter
from typing import Callable, Optional

from modules.agencia.av import AjustesAgencia, carregar_ajustes_agencia
from modules.agencia.editorial import (
    PlanoAgencia,
    analisar_agencia,
)
from modules.agencia.persistencia import carregar_lista_agencia, salvar_lista_agencia
from modules.agencia.pipeline import PreparacaoCorte, exportar_corte_agencia, preparar_corte
from modules.agencia.qualidade import (
    ItemGate,
    aprovado as gate_aprovado,
    como_dicts as gate_como_dicts,
    falhas as gate_falhas,
)
from modules.agencia.timeline import OpcoesEdicao
from modules.agencia.titulos_manual import selecionar_prontos
from modules.config_ia import ConfigIA, carregar_config_ia
from modules.importacao import InfoVideo, analisar_video
from modules.intro import IntervaloIntro, carregar_intro, detectar_intro, salvar_intro
from modules.perfis_visuais import PERFIL_PADRAO_ID, normalizar_perfil_id, obter_perfil
from modules.preset import DADOS_DIR
from modules.sessao import caminho_normalizado
from modules.tasks import CoordenadorRecursos, coordenador_recursos
from modules.titulos_v2 import (
    gerar_titulos_v2,
    montar_contexto,
    registrar_uso,
)
from modules.titulos_viral import finalizar_salvos
from modules.transcricao import (
    MODELO_PADRAO,
    Transcricao,
    carregar_transcricao,
    transcrever_video,
    transcricao_corresponde_origem,
)


ESTEIRA_DIR = DADOS_DIR / "esteira"
ESTADO_ESTEIRA_PATH = ESTEIRA_DIR / "estado_esteira.json"

ETAPAS_ORDEM = (
    "importacao",
    "transcricao",
    "intro",
    "analise_agencia",
    "momentos_fortes",
    "hooks",
    "titulos",
    "preparacao",
    "renderizacao",
    "validacao_saida",
)

ROTULOS_ETAPA = {
    "aguardando": "Na fila",
    "importacao": "1. Importação e validação",
    "transcricao": "2. Transcrição",
    "intro": "3. Identificação de intro",
    "analise_agencia": "4. Análise Modo Agência",
    "momentos_fortes": "5. Avaliação de momentos fortes",
    "hooks": "6. Seleção e validação de hooks",
    "titulos": "7. Geração automática de títulos",
    "preparacao": "8. Preparação de cortes e legendas",
    "renderizacao": "9. Renderização FFmpeg",
    "validacao_saida": "10. Validação e registro de saída",
    "concluido": "Concluído",
    "revisao": "Requer revisão",
    "erro": "Erro",
    "cancelado": "Cancelado",
    "interrompido": "Interrompido (pronto para retomar)",
}


def _agora_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def gerar_id_episodio(caminho_video: str) -> str:
    norm = caminho_normalizado(caminho_video)
    return "ep_" + hashlib.sha256(norm.encode("utf-8")).hexdigest()[:12]


def _converter_itens_gate(gate_raw: object) -> Optional[list[ItemGate]]:
    """Converte uma coleção de ItemGate ou dicts serializados em list[ItemGate]."""
    if not isinstance(gate_raw, (list, tuple)):
        return None
    itens: list[ItemGate] = []
    for it in gate_raw:
        if isinstance(it, ItemGate):
            itens.append(it)
        elif isinstance(it, dict) and "nome" in it and "ok" in it:
            itens.append(
                ItemGate(
                    nome=str(it.get("nome", "GATE")),
                    ok=bool(it.get("ok", False)),
                    detalhe=str(it.get("detalhe", "")),
                    corrigido=bool(it.get("corrigido", False)),
                    bloqueante=bool(it.get("bloqueante", True)),
                )
            )
        else:
            return None
    return itens


def _avaliar_gate_preparacao(prep: Optional[PreparacaoCorte]) -> tuple[bool, str]:
    """
    Avalia `PreparacaoCorte.gate` utilizando `aprovado()` e `falhas()` de
    `modules/agencia/qualidade.py` para a lista real de `ItemGate`.
    """
    if prep is None:
        return False, "Preparação do corte retornou vazio."
    gate_obj = getattr(prep, "gate", None)
    if gate_obj is None:
        return True, ""
    itens_gate = _converter_itens_gate(gate_obj)
    if itens_gate is not None:
        if not gate_aprovado(itens_gate):
            motivos = "; ".join(f"{i.nome}: {i.detalhe}" for i in gate_falhas(itens_gate))
            return False, motivos or "Quality gate reprovou o corte na preparação."
        return True, ""
    if not bool(getattr(gate_obj, "aprovado", True)):
        return False, str(getattr(gate_obj, "motivo_principal", "")) or "Quality gate reprovou o corte."
    return True, ""


def _validar_relatorio_e_gate_exportacao(
    relatorio: Optional[dict],
    gate_raw: object = None,
    *,
    exigir_campos_reais: bool = False,
) -> bool:
    """
    Valida os campos reais de `relatorio_exportacao()` e a lista de `ItemGate` do quality gate,
    sem depender de uma chave `"aprovado"` inexistente no retorno de `relatorio_exportacao()`.
    """
    if exigir_campos_reais:
        if not isinstance(relatorio, dict) or not relatorio:
            return False
        if float(relatorio.get("duracao") or 0.0) <= 0.0:
            return False

    if isinstance(relatorio, dict) and relatorio:
        if "aprovado" in relatorio and not bool(relatorio.get("aprovado")):
            return False
        if "duracao" in relatorio and float(relatorio.get("duracao") or 0.0) <= 0.0:
            return False
        if "largura" in relatorio and int(relatorio.get("largura") or 0) <= 0:
            return False
        if "altura" in relatorio and int(relatorio.get("altura") or 0) <= 0:
            return False
        if "tamanho_bytes" in relatorio and int(relatorio.get("tamanho_bytes") or 0) <= 0:
            return False
        if "codec_video" in relatorio and not str(relatorio.get("codec_video") or "").strip():
            return False

    if gate_raw is not None:
        itens_gate = _converter_itens_gate(gate_raw)
        if itens_gate is not None:
            if exigir_campos_reais and not itens_gate:
                return False
            if not gate_aprovado(itens_gate):
                return False
        elif exigir_campos_reais:
            return False

    return True


def arquivo_exportado_pertence_ao_episodio(
    caminho_saida: str | Path,
    ep: "ContextoEpisodio",
    plano: Optional[PlanoAgencia] = None,
    *,
    exigir_relatorio_dict: Optional[dict] = None,
    exigir_sidecar: bool = False,
) -> bool:
    """
    Verifica rigorosamente se um arquivo de corte exportado:
    1. Existe no disco e tem tamanho > 0 bytes;
    2. Está localizado dentro da pasta de saída específica deste episódio (`ep.pasta_saida_episodio`);
    3. Possui correspondência de `video_id` com a transcrição do episódio (quando disponível);
    4. Valida o sidecar `<saida>.metricas.json` (obrigatório quando `exigir_sidecar=True`),
       conferindo `video_id`, `corte_id`, `relatorio_exportacao` e `quality_gate`;
    5. Se `exigir_relatorio_dict` for fornecido, valida `sucesso`, `relatorio` e `gate` reais.
    """
    if not caminho_saida or not str(caminho_saida).strip():
        return False
    try:
        p_arq = Path(str(caminho_saida)).resolve()
    except OSError:
        return False

    if not p_arq.is_file():
        return False
    try:
        if p_arq.stat().st_size <= 0:
            return False
    except OSError:
        return False

    # 2. Deve estar dentro da pasta_saida_episodio configurada para o episódio
    if not ep.pasta_saida_episodio or not str(ep.pasta_saida_episodio).strip():
        return False
    try:
        pasta_ep = Path(ep.pasta_saida_episodio).resolve()
        if p_arq.parent != pasta_ep:
            return False
    except OSError:
        return False

    # 3. Correspondência de video_id entre plano e transcrição do episódio
    video_id_ep = ep.transcricao.video_id if ep.transcricao else ""
    if plano is not None and video_id_ep:
        if plano.video_id and plano.video_id != video_id_ep:
            return False

    # 4. Verificação do sidecar .metricas.json
    sidecar = p_arq.with_suffix(".metricas.json")
    if exigir_sidecar and not sidecar.is_file():
        return False
    if sidecar.is_file():
        try:
            dados_m = json.loads(sidecar.read_text(encoding="utf-8"))
            if not isinstance(dados_m, dict):
                return False
            corte_id_sidecar = str(dados_m.get("corte_id") or "")
            video_id_sidecar = str(dados_m.get("video_id") or "")
            video_id_ref = video_id_ep or (plano.video_id if plano is not None else "")
            if exigir_sidecar:
                if plano is not None and corte_id_sidecar != str(plano.id):
                    return False
                if video_id_ref and video_id_sidecar != str(video_id_ref):
                    return False
            else:
                if plano is not None and corte_id_sidecar and corte_id_sidecar != str(plano.id):
                    return False
                if video_id_ref and video_id_sidecar and video_id_sidecar != str(video_id_ref):
                    return False
            rel_sidecar = dados_m.get("relatorio_exportacao")
            gate_sidecar = dados_m.get("quality_gate")
            if not _validar_relatorio_e_gate_exportacao(
                rel_sidecar if isinstance(rel_sidecar, dict) else None,
                gate_sidecar,
                exigir_campos_reais=exigir_sidecar,
            ):
                return False
        except Exception:
            return False

    # 5. Verificação de relatório e gate de exportação retornados pela chamada de renderização
    if isinstance(exigir_relatorio_dict, dict):
        if not exigir_relatorio_dict.get("sucesso", False):
            return False
        rel = exigir_relatorio_dict.get("relatorio")
        gate_res = exigir_relatorio_dict.get("gate")
        if not _validar_relatorio_e_gate_exportacao(
            rel if isinstance(rel, dict) else None,
            gate_res,
            exigir_campos_reais=exigir_sidecar,
        ):
            return False

    return True


def reconciliar_corte_exportado_por_metricas(
    ep: "ContextoEpisodio",
    plano: PlanoAgencia,
) -> Optional[str]:
    """
    Reconcilia com segurança um corte que já havia sido exportado e movido para
    `ep.pasta_saida_episodio` antes de uma interrupção inesperada (entre mover o MP4
    e persistir o estado da esteira).
    Nunca associa arquivos apenas pelo nome: exige `<saida>.metricas.json` válido com
    `video_id`, `corte_id` e qualidade aprovada (`relatorio_exportacao` + `quality_gate`).
    """
    if plano is None or not getattr(plano, "id", ""):
        return None
    if not ep.pasta_saida_episodio or not str(ep.pasta_saida_episodio).strip():
        return None
    try:
        pasta_ep = Path(ep.pasta_saida_episodio).resolve()
    except OSError:
        return None
    if not pasta_ep.is_dir():
        return None

    video_id_esperado = (ep.transcricao.video_id if ep.transcricao else "") or getattr(plano, "video_id", "")
    if not video_id_esperado:
        return None

    sufixo_metricas = ".metricas.json"
    for sidecar in sorted(pasta_ep.glob(f"*{sufixo_metricas}")):
        if not sidecar.name.endswith(sufixo_metricas):
            continue
        try:
            dados_m = json.loads(sidecar.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(dados_m, dict):
            continue
        if str(dados_m.get("corte_id") or "") != str(plano.id):
            continue
        if str(dados_m.get("video_id") or "") != str(video_id_esperado):
            continue
        nome_mp4 = sidecar.name[: -len(sufixo_metricas)] + ".mp4"
        mp4_path = sidecar.with_name(nome_mp4)
        if arquivo_exportado_pertence_ao_episodio(mp4_path, ep, plano, exigir_sidecar=True):
            return str(mp4_path)

    return None


class TokenCancelamentoEpisodio:
    """Token de cancelamento isolado por episódio e tarefa."""

    def __init__(self, episodio: "ContextoEpisodio"):
        self._episodio = episodio
        self._cancelado_local = False

    @property
    def cancelada(self) -> bool:
        return self._cancelado_local or self._episodio.cancelado

    def cancelar(self):
        self._cancelado_local = True


@dataclass
class ContextoEpisodio:
    """Contexto completo e isolado de um episódio na esteira."""

    id: str
    video_path: str
    nome: str
    pasta_saida_base: str
    pasta_saida_episodio: str
    perfil_visual_id: str = PERFIL_PADRAO_ID
    idioma: Optional[str] = "pt"
    modelo_whisper: str = MODELO_PADRAO
    transcricao_modo: str = "preciso"
    transcricao_backend: str = "auto_local"
    autorizar_nuvem: bool = False
    max_cortes: int = 5
    exportar_srt: bool = True
    gerar_capa: bool = True
    opcoes_edicao: OpcoesEdicao = field(default_factory=OpcoesEdicao)
    ajustes_av: Optional[AjustesAgencia] = None
    config_ia: Optional[ConfigIA] = None

    # Estado e resultados isolados
    video_info: Optional[InfoVideo] = None
    transcricao: Optional[Transcricao] = None
    caminho_cache_transcricao: str = ""
    intro: Optional[IntervaloIntro] = None
    planos: list[PlanoAgencia] = field(default_factory=list)
    planos_prontos: list[PlanoAgencia] = field(default_factory=list)
    preparacoes: dict[str, PreparacaoCorte] = field(default_factory=dict)
    arquivos_exportados: list[str] = field(default_factory=list)
    relatorios_exportacao: dict[str, dict] = field(default_factory=dict)
    rejeicoes: list[str] = field(default_factory=list)
    planos_editados_manualmente: set[str] = field(default_factory=set)

    # Controle de execução da esteira
    estado: str = "aguardando"  # aguardando, em_execucao, concluido, revisao, erro, cancelado, interrompido
    etapa_atual: str = "aguardando"
    estado_etapas: dict[str, str] = field(default_factory=lambda: {e: "pendente" for e in ETAPAS_ORDEM})
    progresso_pct: float = 0.0
    mensagem_status: str = "Aguardando início da esteira"
    erro: str = ""
    etapa_falha: str = ""
    tentativas: int = 0
    cancelado: bool = False
    em_fila_etapa: Optional[str] = None  # "transcricao", "agencia", "renderizacao" ou None
    criado_em: str = field(default_factory=_agora_iso)
    atualizado_em: str = field(default_factory=_agora_iso)
    historico_transicoes: list[dict] = field(default_factory=list)

    # Referência interna de runtime (não serializada)
    _token_atual: Optional[TokenCancelamentoEpisodio] = field(default=None, repr=False, compare=False)

    @property
    def cortes_preparados_count(self) -> int:
        return len(self.planos_prontos)

    @property
    def cortes_exportados_count(self) -> int:
        return len(self.arquivos_exportados)

    @property
    def cortes_revisao_count(self) -> int:
        from modules.agencia.producao import manual_confirmado
        return sum(
            1
            for p in self.planos
            if p not in self.planos_prontos
            or p.status in ("revisão", "erro", "REVISAO_HUMANA", "REVISÃO DE TÍTULO")
            or (getattr(p, "estado_editorial", "") == "revisao" and not manual_confirmado(p))
        )

    @property
    def rotulo_etapa_atual(self) -> str:
        return ROTULOS_ETAPA.get(self.etapa_atual, self.etapa_atual)

    def registrar_transicao(self, etapa: str, estado_etapa: str, detalhe: str = ""):
        agora = _agora_iso()
        self.atualizado_em = agora
        if etapa in self.estado_etapas:
            self.estado_etapas[etapa] = estado_etapa
        self.historico_transicoes.append({
            "timestamp": agora,
            "perf_counter": round(perf_counter(), 6),
            "etapa": etapa,
            "estado": estado_etapa,
            "detalhe": detalhe,
        })

    def como_dict_persistente(self) -> dict:
        """Serializa o contexto do episódio para persistência segura em disco."""
        return {
            "id": self.id,
            "video_path": self.video_path,
            "nome": self.nome,
            "pasta_saida_base": self.pasta_saida_base,
            "pasta_saida_episodio": self.pasta_saida_episodio,
            "perfil_visual_id": self.perfil_visual_id,
            "idioma": self.idioma,
            "modelo_whisper": self.modelo_whisper,
            "transcricao_modo": self.transcricao_modo,
            "transcricao_backend": self.transcricao_backend,
            "autorizar_nuvem": self.autorizar_nuvem,
            "max_cortes": self.max_cortes,
            "exportar_srt": self.exportar_srt,
            "gerar_capa": self.gerar_capa,
            "caminho_cache_transcricao": self.caminho_cache_transcricao or (self.transcricao.caminho_cache if self.transcricao else ""),
            "video_id": self.transcricao.video_id if self.transcricao else "",
            "planos": [p.como_dict() for p in self.planos],
            "planos_prontos_ids": [p.id for p in self.planos_prontos],
            "planos_editados_manualmente": sorted(self.planos_editados_manualmente),
            "arquivos_exportados": list(self.arquivos_exportados),
            "rejeicoes": list(self.rejeicoes),
            "estado": self.estado,
            "etapa_atual": self.etapa_atual,
            "estado_etapas": dict(self.estado_etapas),
            "progresso_pct": self.progresso_pct,
            "mensagem_status": self.mensagem_status,
            "erro": self.erro,
            "etapa_falha": self.etapa_falha,
            "tentativas": self.tentativas,
            "cancelado": self.cancelado,
            "criado_em": self.criado_em,
            "atualizado_em": self.atualizado_em,
            "historico_transicoes": list(self.historico_transicoes[-50:]),
        }

    @classmethod
    def de_dict_persistente(cls, dados: dict) -> "ContextoEpisodio":
        ep = cls(
            id=dados["id"],
            video_path=dados["video_path"],
            nome=dados.get("nome") or Path(dados["video_path"]).name,
            pasta_saida_base=dados.get("pasta_saida_base", ""),
            pasta_saida_episodio=dados.get("pasta_saida_episodio", ""),
            perfil_visual_id=normalizar_perfil_id(dados.get("perfil_visual_id")),
            idioma=dados.get("idioma", "pt"),
            modelo_whisper=dados.get("modelo_whisper", MODELO_PADRAO),
            transcricao_modo=dados.get("transcricao_modo", "preciso"),
            transcricao_backend=dados.get("transcricao_backend", "auto_local"),
            autorizar_nuvem=bool(dados.get("autorizar_nuvem", False)),
            max_cortes=int(dados.get("max_cortes", 5)),
            exportar_srt=bool(dados.get("exportar_srt", True)),
            gerar_capa=bool(dados.get("gerar_capa", True)),
            caminho_cache_transcricao=dados.get("caminho_cache_transcricao", ""),
            rejeicoes=list(dados.get("rejeicoes", [])),
            planos_editados_manualmente=set(dados.get("planos_editados_manualmente", [])),
            estado=dados.get("estado", "aguardando"),
            etapa_atual=dados.get("etapa_atual", "aguardando"),
            progresso_pct=float(dados.get("progresso_pct", 0.0)),
            mensagem_status=dados.get("mensagem_status", ""),
            erro=dados.get("erro", ""),
            etapa_falha=dados.get("etapa_falha", ""),
            tentativas=int(dados.get("tentativas", 0)),
            cancelado=bool(dados.get("cancelado", False)),
            criado_em=dados.get("criado_em", _agora_iso()),
            atualizado_em=dados.get("atualizado_em", _agora_iso()),
            historico_transicoes=list(dados.get("historico_transicoes", [])),
        )
        etapas_salvas = dados.get("estado_etapas", {})
        for k in ETAPAS_ORDEM:
            if k in etapas_salvas:
                ep.estado_etapas[k] = etapas_salvas[k]

        # Restaura transcrição se existir e for compatível com o arquivo de vídeo
        if ep.caminho_cache_transcricao and Path(ep.caminho_cache_transcricao).is_file():
            try:
                t = carregar_transcricao(Path(ep.caminho_cache_transcricao))
                if transcricao_corresponde_origem(t):
                    ep.transcricao = t
                    ep.intro = carregar_intro(t)
                else:
                    ep.estado_etapas["transcricao"] = "pendente"
            except Exception:
                ep.transcricao = None

        # Restaura planos e higieniza caminhos de saída que não pertençam à pasta atual do episódio
        planos_raw = dados.get("planos", [])
        ep.planos = [PlanoAgencia.de_dict(p) for p in planos_raw if isinstance(p, dict)]
        for p in ep.planos:
            if p.status in ("concluído", "renderizando"):
                if arquivo_exportado_pertence_ao_episodio(p.caminho_saida, ep, p):
                    p.status = "concluído"
                else:
                    reconciliado = reconciliar_corte_exportado_por_metricas(ep, p)
                    if reconciliado:
                        p.status = "concluído"
                        p.caminho_saida = reconciliado
                        p.erro = ""
                    else:
                        p.status = "pendente"
                        p.caminho_saida = ""
            elif p.status == "pendente" and not p.caminho_saida:
                reconciliado = reconciliar_corte_exportado_por_metricas(ep, p)
                if reconciliado:
                    p.status = "concluído"
                    p.caminho_saida = reconciliado
                    p.erro = ""

        prontos_ids = set(dados.get("planos_prontos_ids", []))
        ep.planos_prontos = [p for p in ep.planos if p.id in prontos_ids]

        # Valida arquivos exportados: apenas arquivos reais na pasta_saida_episodio são mantidos
        exportados_validos: list[str] = []
        for arq in dados.get("arquivos_exportados", []):
            if arquivo_exportado_pertence_ao_episodio(arq, ep):
                caminho_str = str(Path(arq))
                if caminho_str not in exportados_validos:
                    exportados_validos.append(caminho_str)
        for p in ep.planos:
            if p.status == "concluído" and p.caminho_saida and arquivo_exportado_pertence_ao_episodio(p.caminho_saida, ep, p):
                if p.caminho_saida not in exportados_validos:
                    exportados_validos.append(p.caminho_saida)
        ep.arquivos_exportados = exportados_validos

        # Se estava em execução quando o programa fechou, marca como interrompido para recuperação segura
        if ep.estado == "em_execucao":
            ep.estado = "interrompido"
            for k, v in ep.estado_etapas.items():
                if v == "executando":
                    ep.estado_etapas[k] = "pendente"
            ep.mensagem_status = "Processamento interrompido. Clique em Tentar novamente ou Iniciar para retomar."
        return ep


class EsteiraProducao:
    """
    Orquestrador de produção contínua multi-episódio.

    Mantém 3 filas independentes por estágio de recurso e coordena os slots
    globalmente com `CoordenadorRecursos` (compartilhado com as abas individuais):
    - Transcrição (limite: 1 simultânea)
    - Agência / Editorial / Títulos / Preparação (limite: 1 simultânea)
    - Renderização FFmpeg / Validação final (limite: 1 simultânea)

    Cada episódio avança automaticamente para a fila seguinte assim que conclui sua etapa atual.
    """

    def __init__(
        self,
        *,
        limite_transcricao: int = 1,
        limite_agencia: int = 1,
        limite_renderizacao: int = 1,
        caminho_persistencia: Optional[Path] = ESTADO_ESTEIRA_PATH,
        coordenador: Optional[CoordenadorRecursos] = None,
        carregar_estado_inicial: bool = False,
        callback_evento: Optional[Callable[[dict], None]] = None,
        fn_importar: Optional[Callable] = None,
        fn_transcrever: Optional[Callable] = None,
        fn_intro: Optional[Callable] = None,
        fn_analisar: Optional[Callable] = None,
        fn_titulos: Optional[Callable] = None,
        fn_preparar: Optional[Callable] = None,
        fn_renderizar: Optional[Callable] = None,
    ):
        if limite_transcricao < 1 or limite_agencia < 1 or limite_renderizacao < 1:
            raise ValueError("Os limites de concorrência devem ser >= 1.")
        self.limite_transcricao = int(limite_transcricao)
        self.limite_agencia = int(limite_agencia)
        self.limite_renderizacao = int(limite_renderizacao)
        self.caminho_persistencia = Path(caminho_persistencia) if caminho_persistencia else None
        self._coordenador: CoordenadorRecursos = coordenador or coordenador_recursos
        self._callbacks: list[Callable[[dict], None]] = []
        if callback_evento:
            self._callbacks.append(callback_evento)

        # Funções injetáveis (permite testes determinísticos ou uso do pipeline real)
        self._fn_importar = fn_importar or analisar_video
        self._fn_transcrever = fn_transcrever or transcrever_video
        self._fn_intro = fn_intro
        self._fn_analisar = fn_analisar
        self._fn_titulos = fn_titulos
        self._fn_preparar = fn_preparar
        self._fn_renderizar = fn_renderizar or exportar_corte_agencia
        self._usa_renderizador_real_agencia = self._fn_renderizar is exportar_corte_agencia
        self._fn_renderizar_aceita_preparacao = self._verificar_suporte_preparacao(self._fn_renderizar)

        self._lock = threading.RLock()
        self._episodios: dict[str, ContextoEpisodio] = {}
        self._ordem_ids: list[str] = []

        # Filas independentes por estágio
        self._fila_transcricao: list[str] = []
        self._fila_agencia: list[str] = []
        self._fila_renderizacao: list[str] = []

        # Conjuntos de episódios em execução ativa por estágio
        self._ativos_transcricao: set[str] = set()
        self._ativos_agencia: set[str] = set()
        self._ativos_renderizacao: set[str] = set()

        # Métricas observáveis de concorrência para auditoria e testes
        self.max_transcricoes_simultaneas_observadas: int = 0
        self.max_agencia_simultaneas_observadas: int = 0
        self.max_renderizacoes_simultaneas_observadas: int = 0
        self.log_eventos: list[dict] = []

        self._pausada: bool = False
        self._encerrada: bool = False
        self._escalonando: bool = False
        self._threads_ativas: list[threading.Thread] = []
        self._evento_ocioso = threading.Event()
        self._evento_ocioso.set()

        # Escuta liberação de recursos globais (ex.: quando uma aba avulsa libera transcrição/render)
        self._coordenador.registrar_callback_liberacao(self._ao_liberar_recurso_global)

        if carregar_estado_inicial and self.caminho_persistencia:
            if self.caminho_persistencia.is_file():
                self.carregar_estado()
            else:
                self.salvar_estado()

    @staticmethod
    def _verificar_suporte_preparacao(fn: Callable) -> bool:
        try:
            sig = inspect.signature(fn)
            if "preparacao" in sig.parameters:
                return True
            return any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
        except (TypeError, ValueError):
            return False

    def _ao_liberar_recurso_global(self, _recurso: str):
        """Disparado pelo CoordenadorRecursos quando qualquer recurso global é liberado."""
        with self._lock:
            if self._encerrada or self._pausada or self._escalonando:
                return
            tem_fila = bool(self._fila_transcricao or self._fila_agencia or self._fila_renderizacao)
        if tem_fila:
            self._escalonar()

    # ─── Callbacks e Observabilidade ──────────────────────────────────────────

    def registrar_callback(self, cb: Callable[[dict], None]):
        with self._lock:
            if cb not in self._callbacks:
                self._callbacks.append(cb)

    def remover_callback(self, cb: Callable[[dict], None]):
        with self._lock:
            if cb in self._callbacks:
                self._callbacks.remove(cb)

    def _emitir_evento(self, tipo: str, ep: Optional[ContextoEpisodio] = None, etapa: str = "", detalhe: str = ""):
        with self._lock:
            registro = {
                "timestamp": _agora_iso(),
                "perf_counter": round(perf_counter(), 6),
                "tipo": tipo,
                "episodio_id": ep.id if ep else "",
                "nome": ep.nome if ep else "",
                "estado": ep.estado if ep else "",
                "etapa": etapa or (ep.etapa_atual if ep else ""),
                "progresso_pct": ep.progresso_pct if ep else 0.0,
                "detalhe": detalhe or (ep.mensagem_status if ep else ""),
                "ativos_transcricao": len(self._ativos_transcricao),
                "ativos_agencia": len(self._ativos_agencia),
                "ativos_renderizacao": len(self._ativos_renderizacao),
                "lista_ativos_transcricao": list(self._ativos_transcricao),
                "lista_ativos_agencia": list(self._ativos_agencia),
                "lista_ativos_renderizacao": list(self._ativos_renderizacao),
            }
            self.log_eventos.append(registro)
            callbacks = list(self._callbacks)
        for cb in callbacks:
            try:
                cb(registro)
            except Exception:
                pass

    # ─── Gerenciamento de Episódios e Integração com a Aba Agência ────────────

    def listar_episodios(self) -> list[ContextoEpisodio]:
        with self._lock:
            return [self._episodios[eid] for eid in self._ordem_ids if eid in self._episodios]

    def obter_episodio(self, episodio_id: str) -> Optional[ContextoEpisodio]:
        with self._lock:
            return self._episodios.get(episodio_id)

    def obter_transcricao_episodio(self, episodio_id: str) -> Optional[Transcricao]:
        """Recupera a transcrição completa do episódio (em memória ou cache), sem reexecutar Whisper."""
        ep = self.obter_episodio(episodio_id)
        if ep is None:
            return None
        from modules.transcricao_gemini import obter_transcricao_episodio
        return obter_transcricao_episodio(ep)

    def obter_episodio_por_video(self, video_path: str | Path) -> Optional[ContextoEpisodio]:
        """Localiza o episódio na esteira pelo caminho normalizado do vídeo."""
        if not video_path or not str(video_path).strip():
            return None
        norm = caminho_normalizado(str(video_path))
        with self._lock:
            for ep in self._episodios.values():
                if caminho_normalizado(ep.video_path) == norm:
                    return ep
        return None

    def episodio_em_processamento(self, video_path: str | Path) -> bool:
        """Retorna True se o vídeo estiver ativo ou na fila da esteira."""
        ep = self.obter_episodio_por_video(video_path)
        if ep is None:
            return False
        with self._lock:
            return (
                ep.estado == "em_execucao"
                or ep.em_fila_etapa is not None
                or ep.id in self._ativos_transcricao
                or ep.id in self._ativos_agencia
                or ep.id in self._ativos_renderizacao
            )

    def sincronizar_planos_externos(self, video_path: str | Path, planos_ui: list[PlanoAgencia]) -> bool:
        """
        Recebe alterações manuais feitas pelo usuário na aba Agência (títulos editados,
        aprovação manual, opção de título selecionada) e mescla de forma segura no
        contexto do episódio da esteira sem perder o progresso de renderização nem a identidade dos cortes.
        """
        ep = self.obter_episodio_por_video(video_path)
        if ep is None or not planos_ui:
            return False

        with self._lock:
            por_id_ui = {p.id: p for p in planos_ui if getattr(p, "id", "")}
            if not ep.planos:
                ep.planos = [copy.deepcopy(p) for p in planos_ui]
                for p in ep.planos:
                    ep.planos_editados_manualmente.add(p.id)
                self.salvar_estado()
                return True

            for plano_ep in ep.planos:
                p_ui = por_id_ui.get(plano_ep.id)
                if p_ui is None:
                    continue
                mudou = (
                    p_ui.titulo_escolhido != plano_ep.titulo_escolhido
                    or p_ui.selecionado != plano_ep.selecionado
                    or p_ui.estado_editorial != plano_ep.estado_editorial
                    or p_ui.titulo_aprovado_manualmente != plano_ep.titulo_aprovado_manualmente
                    or p_ui.corte_aprovado_manualmente != plano_ep.corte_aprovado_manualmente
                )
                if mudou or p_ui.titulo_aprovado_manualmente:
                    ep.planos_editados_manualmente.add(plano_ep.id)

                plano_ep.titulo_escolhido = p_ui.titulo_escolhido
                plano_ep.titulo_capa = p_ui.titulo_capa
                plano_ep.selecionado = p_ui.selecionado
                plano_ep.estado_editorial = p_ui.estado_editorial
                plano_ep.avisos = list(p_ui.avisos)
                plano_ep.titulos = list(p_ui.titulos)
                plano_ep.titulo_origem = p_ui.titulo_origem
                plano_ep.titulo_auto_aprovado = p_ui.titulo_auto_aprovado
                plano_ep.titulo_aprovado_manualmente = p_ui.titulo_aprovado_manualmente
                plano_ep.titulo_manual_confirmado = p_ui.titulo_manual_confirmado
                plano_ep.titulo_confirmacao_pendente = p_ui.titulo_confirmacao_pendente
                plano_ep.corte_aprovado_manualmente = p_ui.corte_aprovado_manualmente
                plano_ep.corte_confirmado_hash = p_ui.corte_confirmado_hash
                plano_ep.gancho = p_ui.gancho
                # Não sobrescreve status se já estiver renderizando ou concluído na esteira
                if plano_ep.status not in ("renderizando", "concluído"):
                    plano_ep.status = p_ui.status

            # Atualiza também referências em planos_prontos
            for idx, pronto in enumerate(list(ep.planos_prontos)):
                correspondente = next((p for p in ep.planos if p.id == pronto.id), None)
                if correspondente is not None:
                    ep.planos_prontos[idx] = correspondente

            self.salvar_estado()
            return True

    def _mesclar_edicoes_manuais_disco(self, ep: ContextoEpisodio):
        """
        Verifica se há edições manuais salvas no disco pela aba Agência e aplica
        aos planos em memória do episódio antes da preparação/renderização.
        """
        if ep.transcricao is None:
            return
        try:
            salvos_disco = carregar_lista_agencia(ep.video_path, ep.transcricao.video_id)
        except Exception:
            salvos_disco = []
        if not salvos_disco:
            return
        planos_disco = [
            p if isinstance(p, PlanoAgencia) else PlanoAgencia.de_dict(p)
            for p in salvos_disco
            if isinstance(p, (PlanoAgencia, dict))
        ]
        por_id_disco = {p.id: p for p in planos_disco if getattr(p, "id", "")}
        with self._lock:
            for plano_ep in ep.planos:
                p_d = por_id_disco.get(plano_ep.id)
                if p_d is None:
                    continue
                if (
                    p_d.titulo_aprovado_manualmente
                    or bool(p_d.titulo_manual_confirmado)
                    or p_d.corte_aprovado_manualmente
                ):
                    ep.planos_editados_manualmente.add(plano_ep.id)
                    plano_ep.titulo_escolhido = p_d.titulo_escolhido
                    plano_ep.titulo_capa = p_d.titulo_capa
                    plano_ep.selecionado = p_d.selecionado
                    plano_ep.estado_editorial = p_d.estado_editorial
                    plano_ep.avisos = list(p_d.avisos)
                    plano_ep.titulos = list(p_d.titulos)
                    plano_ep.titulo_origem = p_d.titulo_origem
                    plano_ep.titulo_auto_aprovado = p_d.titulo_auto_aprovado
                    plano_ep.titulo_aprovado_manualmente = p_d.titulo_aprovado_manualmente
                    plano_ep.titulo_manual_confirmado = p_d.titulo_manual_confirmado
                    plano_ep.titulo_confirmacao_pendente = p_d.titulo_confirmacao_pendente
                    plano_ep.corte_aprovado_manualmente = p_d.corte_aprovado_manualmente
                    plano_ep.corte_confirmado_hash = p_d.corte_confirmado_hash

    def adicionar_episodios(
        self,
        caminhos_video: list[str | Path],
        *,
        pasta_saida: str | Path,
        perfil_visual_id: str = PERFIL_PADRAO_ID,
        idioma: Optional[str] = "pt",
        modelo_whisper: str = MODELO_PADRAO,
        transcricao_modo: str = "preciso",
        transcricao_backend: str = "auto_local",
        autorizar_nuvem: bool = False,
        max_cortes: int = 5,
        exportar_srt: bool = True,
        gerar_capa: bool = True,
        opcoes_edicao: Optional[OpcoesEdicao] = None,
        ajustes_av: Optional[AjustesAgencia] = None,
        config_ia: Optional[ConfigIA] = None,
        iniciar_imediatamente: bool = False,
    ) -> tuple[list[ContextoEpisodio], list[str]]:
        """
        Adiciona um ou mais vídeos à esteira sem permitir duplicações do mesmo arquivo.
        Retorna (episodios_adicionados, avisos_ignorados).
        """
        adicionados: list[ContextoEpisodio] = []
        ignorados: list[str] = []
        pasta_base = str(Path(pasta_saida).resolve())

        with self._lock:
            existentes_norm = {
                caminho_normalizado(ep.video_path): ep
                for ep in self._episodios.values()
            }
            vistos_nesta_chamada: set[str] = set()

            for bruto in caminhos_video:
                if not bruto or not str(bruto).strip():
                    continue
                norm = caminho_normalizado(str(bruto))
                nome = Path(norm).name
                if norm in vistos_nesta_chamada:
                    ignorados.append(f"Arquivo duplicado na seleção ignorado: {nome}")
                    continue
                vistos_nesta_chamada.add(norm)

                stem_seguro = "".join(c for c in Path(norm).stem if c.isalnum() or c in " -_").strip().replace(" ", "_")

                if norm in existentes_norm:
                    ep_existente = existentes_norm[norm]
                    if ep_existente.estado in ("aguardando", "em_execucao"):
                        ignorados.append(f"Episódio já está na esteira ativa: {nome}")
                        continue
                    elif ep_existente.estado == "concluido":
                        ignorados.append(f"Episódio já foi concluído nesta esteira: {nome}")
                        continue
                    else:
                        # Reativa episódio cancelado/erro/interrompido/revisao mantendo etapas válidas
                        ep_existente.cancelado = False
                        ep_existente.erro = ""
                        ep_existente.pasta_saida_base = pasta_base
                        ep_existente.pasta_saida_episodio = str(Path(pasta_base) / (stem_seguro or ep_existente.id))
                        # Revalida arquivos exportados caso a pasta de saída tenha mudado
                        ep_existente.arquivos_exportados = [
                            arq for arq in ep_existente.arquivos_exportados
                            if arquivo_exportado_pertence_ao_episodio(arq, ep_existente)
                        ]
                        for p in ep_existente.planos:
                            if p.status == "concluído" and not arquivo_exportado_pertence_ao_episodio(p.caminho_saida, ep_existente, p):
                                p.status = "pendente"
                                p.caminho_saida = ""
                        if ep_existente.estado in ("cancelado", "erro", "interrompido"):
                            ep_existente.estado = "aguardando"
                        adicionados.append(ep_existente)
                        continue

                ep_id = gerar_id_episodio(norm)
                while ep_id in self._episodios:
                    ep_id = f"{ep_id}_{len(self._episodios) + 1}"

                stem_seguro = stem_seguro or ep_id
                pasta_ep = str(Path(pasta_base) / stem_seguro)

                ep = ContextoEpisodio(
                    id=ep_id,
                    video_path=norm,
                    nome=nome,
                    pasta_saida_base=pasta_base,
                    pasta_saida_episodio=pasta_ep,
                    perfil_visual_id=normalizar_perfil_id(perfil_visual_id),
                    idioma=idioma,
                    modelo_whisper=modelo_whisper,
                    transcricao_modo=transcricao_modo,
                    transcricao_backend=transcricao_backend,
                    autorizar_nuvem=autorizar_nuvem,
                    max_cortes=max_cortes,
                    exportar_srt=exportar_srt,
                    gerar_capa=gerar_capa,
                    opcoes_edicao=copy.deepcopy(opcoes_edicao) if opcoes_edicao else OpcoesEdicao(),
                    ajustes_av=copy.deepcopy(ajustes_av) if ajustes_av else None,
                    config_ia=copy.deepcopy(config_ia) if config_ia else None,
                )
                self._episodios[ep_id] = ep
                self._ordem_ids.append(ep_id)
                existentes_norm[norm] = ep
                adicionados.append(ep)
                ep.registrar_transicao("aguardando", "pendente", "Adicionado à esteira")
                self._emitir_evento("episodio_adicionado", ep, "aguardando", f"Episódio '{nome}' adicionado à fila.")

            self.salvar_estado()

        if iniciar_imediatamente and adicionados:
            self.iniciar()

        return adicionados, ignorados

    # ─── Controle de Execução, Filas e Agendamento ───────────────────────────

    def iniciar(self):
        """Enfileira todos os episódios elegíveis e dispara o escalonador contínuo."""
        with self._lock:
            self._pausada = False
            for ep_id in self._ordem_ids:
                ep = self._episodios[ep_id]
                if ep.cancelado:
                    continue
                if ep.estado in ("aguardando", "interrompido"):
                    self._enfileirar_na_etapa_elegivel(ep)
            self.salvar_estado()
            self._escalonar()

    def _enfileirar_na_etapa_elegivel(self, ep: ContextoEpisodio):
        """
        Determina a primeira etapa ainda não concluída do episódio e o coloca na fila
        correspondente, evitando refazer trabalho válido já concluído.
        """
        with self._lock:
            if ep.cancelado or ep.em_fila_etapa is not None:
                return
            if ep.id in self._ativos_transcricao or ep.id in self._ativos_agencia or ep.id in self._ativos_renderizacao:
                return

            # Verifica se transcrição já está pronta e válida
            transcricao_valida = (
                ep.estado_etapas.get("importacao") == "concluido"
                and ep.estado_etapas.get("transcricao") == "concluido"
                and ep.transcricao is not None
                and bool(ep.transcricao.segmentos)
            )
            if not transcricao_valida:
                if ep.id not in self._fila_transcricao:
                    self._fila_transcricao.append(ep.id)
                    ep.em_fila_etapa = "transcricao"
                    ep.estado = "aguardando"
                    ep.etapa_atual = "transcricao"
                    ep.mensagem_status = "Na fila de transcrição"
                    self._evento_ocioso.clear()
                return

            # Verifica se preparação da Agência já está pronta
            agencia_valida = (
                ep.estado_etapas.get("analise_agencia") == "concluido"
                and ep.estado_etapas.get("titulos") == "concluido"
                and ep.estado_etapas.get("preparacao") == "concluido"
                and bool(ep.planos_prontos)
            )
            if not agencia_valida:
                if ep.id not in self._fila_agencia:
                    self._fila_agencia.append(ep.id)
                    ep.em_fila_etapa = "agencia"
                    ep.estado = "aguardando"
                    ep.etapa_atual = "analise_agencia"
                    ep.mensagem_status = "Na fila do Modo Agência"
                    self._evento_ocioso.clear()
                return

            # Vai direto para a fila de renderização
            if ep.id not in self._fila_renderizacao:
                self._fila_renderizacao.append(ep.id)
                ep.em_fila_etapa = "renderizacao"
                ep.estado = "aguardando"
                ep.etapa_atual = "renderizacao"
                ep.mensagem_status = "Na fila de renderização"
                self._evento_ocioso.clear()

    def _escalonar(self):
        """
        Verifica as 3 filas independentes e inicia novas tarefas respeitando os limites
        locais e globais (`self._coordenador`):
        - Transcrição: <= self.limite_transcricao (1)
        - Agência: <= self.limite_agencia (1)
        - Renderização: <= self.limite_renderizacao (1)
        """
        with self._lock:
            if self._pausada or self._encerrada or self._escalonando:
                self._atualizar_sinal_ocioso()
                return

            self._escalonando = True
            try:
                # 1. Escalonar Transcrição
                while len(self._ativos_transcricao) < self.limite_transcricao and self._fila_transcricao:
                    ep_id = self._fila_transcricao[0]
                    ep = self._episodios.get(ep_id)
                    if not ep or ep.cancelado:
                        self._fila_transcricao.pop(0)
                        if ep:
                            ep.em_fila_etapa = None
                        continue
                    dono_slot = ("esteira_transcricao", id(self), ep_id)
                    if not self._coordenador.tentar_adquirir("transcricao", dono_slot, limite=self.limite_transcricao):
                        # Recurso global ocupado por aba avulsa; aguarda callback de liberação
                        break
                    self._fila_transcricao.pop(0)
                    ep.em_fila_etapa = None
                    self._ativos_transcricao.add(ep_id)
                    ativos_globais = self._coordenador.ativos("transcricao")
                    self.max_transcricoes_simultaneas_observadas = max(
                        self.max_transcricoes_simultaneas_observadas,
                        len(self._ativos_transcricao),
                        ativos_globais,
                    )
                    self._evento_ocioso.clear()
                    t = threading.Thread(
                        target=self._worker_transcricao,
                        args=(ep,),
                        daemon=True,
                        name=f"esteira-transcricao-{ep.id}",
                    )
                    self._threads_ativas.append(t)
                    try:
                        t.start()
                    except Exception as exc_start:
                        if t in self._threads_ativas:
                            self._threads_ativas.remove(t)
                        self._ativos_transcricao.discard(ep_id)
                        self._coordenador.liberar("transcricao", dono_slot)
                        self._marcar_erro(ep, "transcricao", f"Falha ao iniciar thread de transcrição: {exc_start}")
                        self.salvar_estado()

                # 2. Escalonar Agência
                while len(self._ativos_agencia) < self.limite_agencia and self._fila_agencia:
                    ep_id = self._fila_agencia[0]
                    ep = self._episodios.get(ep_id)
                    if not ep or ep.cancelado:
                        self._fila_agencia.pop(0)
                        if ep:
                            ep.em_fila_etapa = None
                        continue
                    dono_slot = ("esteira_agencia", id(self), ep_id)
                    if not self._coordenador.tentar_adquirir("agencia", dono_slot, limite=self.limite_agencia):
                        break
                    self._fila_agencia.pop(0)
                    ep.em_fila_etapa = None
                    self._ativos_agencia.add(ep_id)
                    ativos_globais = self._coordenador.ativos("agencia")
                    self.max_agencia_simultaneas_observadas = max(
                        self.max_agencia_simultaneas_observadas,
                        len(self._ativos_agencia),
                        ativos_globais,
                    )
                    self._evento_ocioso.clear()
                    t = threading.Thread(
                        target=self._worker_agencia,
                        args=(ep,),
                        daemon=True,
                        name=f"esteira-agencia-{ep.id}",
                    )
                    self._threads_ativas.append(t)
                    try:
                        t.start()
                    except Exception as exc_start:
                        if t in self._threads_ativas:
                            self._threads_ativas.remove(t)
                        self._ativos_agencia.discard(ep_id)
                        self._coordenador.liberar("agencia", dono_slot)
                        self._marcar_erro(ep, "analise_agencia", f"Falha ao iniciar thread da Agência: {exc_start}")
                        self.salvar_estado()

                # 3. Escalonar Renderização
                while len(self._ativos_renderizacao) < self.limite_renderizacao and self._fila_renderizacao:
                    ep_id = self._fila_renderizacao[0]
                    ep = self._episodios.get(ep_id)
                    if not ep or ep.cancelado:
                        self._fila_renderizacao.pop(0)
                        if ep:
                            ep.em_fila_etapa = None
                        continue
                    dono_slot = ("esteira_render", id(self), ep_id)
                    if not self._coordenador.tentar_adquirir("render", dono_slot, limite=self.limite_renderizacao):
                        break
                    self._fila_renderizacao.pop(0)
                    ep.em_fila_etapa = None
                    self._ativos_renderizacao.add(ep_id)
                    ativos_globais = self._coordenador.ativos("render")
                    self.max_renderizacoes_simultaneas_observadas = max(
                        self.max_renderizacoes_simultaneas_observadas,
                        len(self._ativos_renderizacao),
                        ativos_globais,
                    )
                    self._evento_ocioso.clear()
                    t = threading.Thread(
                        target=self._worker_renderizacao,
                        args=(ep,),
                        daemon=True,
                        name=f"esteira-render-{ep.id}",
                    )
                    self._threads_ativas.append(t)
                    try:
                        t.start()
                    except Exception as exc_start:
                        if t in self._threads_ativas:
                            self._threads_ativas.remove(t)
                        self._ativos_renderizacao.discard(ep_id)
                        self._coordenador.liberar("render", dono_slot)
                        self._marcar_erro(ep, "renderizacao", f"Falha ao iniciar thread de renderização: {exc_start}")
                        self.salvar_estado()
            finally:
                self._escalonando = False
                self._atualizar_sinal_ocioso()

    def _atualizar_sinal_ocioso(self):
        with self._lock:
            ocupado = bool(
                self._ativos_transcricao
                or self._ativos_agencia
                or self._ativos_renderizacao
                or (not self._pausada and (self._fila_transcricao or self._fila_agencia or self._fila_renderizacao))
            )
            if not ocupado:
                self._evento_ocioso.set()
            else:
                self._evento_ocioso.clear()

    def aguardar_conclusao(self, timeout: Optional[float] = None) -> bool:
        """Aguarda até que todas as filas e tarefas ativas da esteira terminem."""
        return self._evento_ocioso.wait(timeout=timeout)

    @property
    def ocupada(self) -> bool:
        with self._lock:
            return not self._evento_ocioso.is_set()

    def status_concorrencia(self) -> dict:
        with self._lock:
            return {
                "transcricoes_ativas": len(self._ativos_transcricao),
                "transcricoes_globais_ativas": self._coordenador.ativos("transcricao"),
                "limite_transcricao": self.limite_transcricao,
                "fila_transcricao": len(self._fila_transcricao),
                "agencia_ativas": len(self._ativos_agencia),
                "agencia_globais_ativas": self._coordenador.ativos("agencia"),
                "limite_agencia": self.limite_agencia,
                "fila_agencia": len(self._fila_agencia),
                "renderizacoes_ativas": len(self._ativos_renderizacao),
                "renderizacoes_globais_ativas": self._coordenador.ativos("render"),
                "limite_renderizacao": self.limite_renderizacao,
                "fila_renderizacao": len(self._fila_renderizacao),
            }

    # ─── Worker Estágio 1: Importação + Transcrição ───────────────────────────

    def _worker_transcricao(self, ep: ContextoEpisodio):
        token = TokenCancelamentoEpisodio(ep)
        ep._token_atual = token
        sucesso_estagio = False
        dono_slot = ("esteira_transcricao", id(self), ep.id)

        try:
            with self._lock:
                if token.cancelada:
                    self._marcar_cancelado(ep, "transcricao")
                    return
                ep.estado = "em_execucao"
                ep.erro = ""
                ep.etapa_falha = ""
                ep.etapa_atual = "importacao"
                ep.progresso_pct = 2.0
                ep.mensagem_status = "Validando arquivo de vídeo…"
                ep.registrar_transicao("importacao", "executando", "Iniciando validação do vídeo")
            self._emitir_evento("etapa_inicio", ep, "importacao", ep.mensagem_status)

            # Etapa 1: Importação e Validação
            if ep.estado_etapas.get("importacao") != "concluido" or ep.video_info is None:
                caminho_p = Path(ep.video_path)
                if not caminho_p.is_file():
                    raise FileNotFoundError(f"Arquivo de vídeo inacessível ou inexistente: {ep.video_path}")
                info = self._fn_importar(ep.video_path)
                if not getattr(info, "valido", False):
                    raise ValueError(getattr(info, "erro", "Arquivo de vídeo inválido."))
                if token.cancelada:
                    self._marcar_cancelado(ep, "importacao")
                    return
                with self._lock:
                    ep.video_info = info
                    ep.registrar_transicao("importacao", "concluido", f"Vídeo válido ({getattr(info, 'duracao', 0):.1f}s)")
                self._emitir_evento("etapa_concluida", ep, "importacao", "Vídeo validado com sucesso.")

            # Etapa 2: Transcrição
            with self._lock:
                if token.cancelada:
                    self._marcar_cancelado(ep, "transcricao")
                    return
                ep.etapa_atual = "transcricao"
                ep.progresso_pct = 5.0
                ep.mensagem_status = "Iniciando transcrição…"
                ep.registrar_transicao("transcricao", "executando", "Iniciando transcrição")
            self._emitir_evento("etapa_inicio", ep, "transcricao", ep.mensagem_status)

            def prog_transcricao(pct: float, msg: str):
                if token.cancelada:
                    return
                with self._lock:
                    ep.progresso_pct = max(0.0, min(100.0, float(pct)))
                    ep.mensagem_status = str(msg)
                self._emitir_evento("progresso", ep, "transcricao", str(msg))

            transcricao = self._fn_transcrever(
                ep.video_path,
                idioma=ep.idioma,
                modelo=ep.modelo_whisper,
                usar_cache=True,
                cancelada_ref=token,
                progresso_cb=prog_transcricao,
                modo=ep.transcricao_modo,
                backend=ep.transcricao_backend,
                autorizar_nuvem=ep.autorizar_nuvem,
            )
            if token.cancelada:
                self._marcar_cancelado(ep, "transcricao")
                return
            if transcricao is None or not getattr(transcricao, "segmentos", None):
                raise RuntimeError("A transcrição terminou sem produzir segmentos de fala válidos.")
            if Path(transcricao.video_path).resolve() != Path(ep.video_path).resolve():
                raise ValueError("Isolamento violado: a transcrição retornada pertence a outro arquivo de vídeo.")

            with self._lock:
                ep.transcricao = transcricao
                ep.caminho_cache_transcricao = getattr(transcricao, "caminho_cache", "")
                ep.progresso_pct = 100.0
                ep.mensagem_status = f"Transcrição concluída ({len(transcricao.segmentos)} segmentos)."
                ep.registrar_transicao("transcricao", "concluido", ep.mensagem_status)
                sucesso_estagio = True
            self._emitir_evento("etapa_concluida", ep, "transcricao", ep.mensagem_status)

        except Exception as exc:
            if token.cancelada or "cancelad" in str(exc).lower():
                self._marcar_cancelado(ep, ep.etapa_atual)
            else:
                self._marcar_erro(ep, ep.etapa_atual, str(exc))
        finally:
            with self._lock:
                try:
                    ep._token_atual = None
                    self._ativos_transcricao.discard(ep.id)
                    if sucesso_estagio and not ep.cancelado:
                        # Avança IMEDIATAMENTE para a fila da Agência!
                        self._fila_agencia.append(ep.id)
                        ep.em_fila_etapa = "agencia"
                        ep.etapa_atual = "analise_agencia"
                        ep.progresso_pct = 0.0
                        ep.mensagem_status = "Encaminhado para análise do Modo Agência"
                        self._emitir_evento("encaminhado_agencia", ep, "analise_agencia", ep.mensagem_status)
                    self.salvar_estado()
                finally:
                    self._coordenador.liberar("transcricao", dono_slot)
                    self._escalonar()

    # ─── Worker Estágio 2: Intro + Agência + Momentos + Hooks + Títulos + Preparação ───

    def _worker_agencia(self, ep: ContextoEpisodio):
        token = TokenCancelamentoEpisodio(ep)
        ep._token_atual = token
        sucesso_estagio = False
        dono_slot = ("esteira_agencia", id(self), ep.id)

        try:
            with self._lock:
                if token.cancelada:
                    self._marcar_cancelado(ep, "analise_agencia")
                    return
                if ep.transcricao is None:
                    raise RuntimeError("Transcrição ausente ao iniciar etapa do Modo Agência.")
                ep.estado = "em_execucao"
                ep.erro = ""
                ep.etapa_falha = ""

            transcricao = ep.transcricao
            config = copy.deepcopy(ep.config_ia) if ep.config_ia else carregar_config_ia()
            config.max_cortes = ep.max_cortes

            # Etapa 3: Identificação de introdução e trechos descartáveis
            if ep.estado_etapas.get("intro") != "concluido":
                with self._lock:
                    ep.etapa_atual = "intro"
                    ep.progresso_pct = 5.0
                    ep.mensagem_status = "Identificando abertura (intro) e trechos descartáveis…"
                    ep.registrar_transicao("intro", "executando", ep.mensagem_status)
                self._emitir_evento("etapa_inicio", ep, "intro", ep.mensagem_status)

                if self._fn_intro is not None:
                    intro = self._fn_intro(transcricao)
                else:
                    intro = carregar_intro(transcricao)
                    if intro is None:
                        intro = detectar_intro(transcricao)
                        if intro is not None:
                            try:
                                salvar_intro(transcricao, intro)
                            except OSError:
                                pass
                if token.cancelada:
                    self._marcar_cancelado(ep, "intro")
                    return
                with self._lock:
                    ep.intro = intro
                    det_intro = f"Intro detectada ({intro.inicio:.1f}–{intro.fim:.1f}s)" if intro else "Sem intro detectada"
                    ep.registrar_transicao("intro", "concluido", det_intro)
                self._emitir_evento("etapa_concluida", ep, "intro", det_intro)

            # Etapas 4, 5 e 6: Análise do Modo Agência, Momentos Fortes e Seleção de Hooks
            if (
                ep.estado_etapas.get("analise_agencia") != "concluido"
                or ep.estado_etapas.get("momentos_fortes") != "concluido"
                or ep.estado_etapas.get("hooks") != "concluido"
                or not ep.planos
            ):
                with self._lock:
                    ep.etapa_atual = "analise_agencia"
                    ep.progresso_pct = 15.0
                    ep.mensagem_status = "Analisando episódio no Modo Agência…"
                    ep.registrar_transicao("analise_agencia", "executando", "Iniciando análise editorial")
                    ep.registrar_transicao("momentos_fortes", "executando", "Avaliando momentos fortes")
                    ep.registrar_transicao("hooks", "executando", "Selecionando ganchos de remix")
                self._emitir_evento("etapa_inicio", ep, "analise_agencia", ep.mensagem_status)

                def prog_agencia(pct: float, msg: str):
                    if token.cancelada:
                        return
                    with self._lock:
                        ep.progresso_pct = 15.0 + max(0.0, min(100.0, float(pct))) * 0.45
                        ep.mensagem_status = str(msg)
                    self._emitir_evento("progresso", ep, "analise_agencia", str(msg))

                if self._fn_analisar is not None:
                    resultado_planos = self._fn_analisar(
                        transcricao, config, cancelada_ref=token, progresso_cb=prog_agencia
                    )
                else:
                    # Tenta carregar lista salva compatível com este vídeo e transcrição
                    salvos = []
                    try:
                        salvos = carregar_lista_agencia(ep.video_path, transcricao.video_id)
                    except Exception:
                        salvos = []
                    if salvos:
                        resultado_planos = salvos
                    else:
                        resultado_planos = analisar_agencia(
                            transcricao, config, usar_cache=True, cancelada_ref=token, progresso_cb=prog_agencia
                        )

                if token.cancelada:
                    self._marcar_cancelado(ep, "analise_agencia")
                    return

                planos_lista = [
                    p if isinstance(p, PlanoAgencia) else PlanoAgencia.de_dict(p)
                    for p in (resultado_planos or [])
                ]
                for p in planos_lista:
                    if not p.video_id:
                        p.video_id = transcricao.video_id
                    elif p.video_id != transcricao.video_id:
                        raise ValueError("Isolamento violado: plano retornado pertence a outro episódio.")
                    # Higieniza status/caminho_saida antigo que não pertença à pasta atual do episódio
                    if p.status in ("concluído", "renderizando") and not arquivo_exportado_pertence_ao_episodio(p.caminho_saida, ep, p):
                        p.status = "pendente"
                        p.caminho_saida = ""

                if not planos_lista:
                    raise RuntimeError("Nenhum corte remix válido foi encontrado na análise deste episódio.")

                with self._lock:
                    ep.planos = planos_lista
                    ep.rejeicoes = list(getattr(resultado_planos, "rejeicoes", []))
                    ep.registrar_transicao("analise_agencia", "concluido", f"{len(planos_lista)} planos obtidos")
                    ep.registrar_transicao("momentos_fortes", "concluido", f"{len(planos_lista)} momentos avaliados")
                    ep.registrar_transicao("hooks", "concluido", "Hooks de remix validados")
                    try:
                        salvar_lista_agencia(ep.video_path, transcricao.video_id, ep.planos)
                    except Exception:
                        pass
                    self.salvar_estado()
                self._emitir_evento("etapa_concluida", ep, "analise_agencia", f"{len(planos_lista)} cortes remix identificados.")

            # Etapa 7: Geração Automática de Títulos
            if ep.estado_etapas.get("titulos") != "concluido":
                with self._lock:
                    ep.etapa_atual = "titulos"
                    ep.progresso_pct = 65.0
                    ep.mensagem_status = "Gerando opções automáticas de títulos…"
                    ep.registrar_transicao("titulos", "executando", ep.mensagem_status)
                self._emitir_evento("etapa_inicio", ep, "titulos", ep.mensagem_status)

                self._mesclar_edicoes_manuais_disco(ep)
                with self._lock:
                    # Trabalha sobre cópias para nunca sobrescrever edições manuais concorrentes feitas enquanto a IA roda
                    copias_elegiveis = [
                        copy.deepcopy(p)
                        for p in ep.planos
                        if p.estado_editorial != "revisao"
                        and not p.titulo_aprovado_manualmente
                        and not p.titulo_manual_confirmado
                        and p.id not in ep.planos_editados_manualmente
                    ]

                if self._fn_titulos is not None:
                    if copias_elegiveis:
                        self._fn_titulos(copias_elegiveis, transcricao, config, cancelada_ref=token)
                else:
                    if copias_elegiveis:
                        gerar_titulos_v2(
                            copias_elegiveis,
                            transcricao,
                            config,
                            usar_cache=True,
                            cancelada_ref=token,
                            timeline_modo="agencia",
                        )
                        finalizar_salvos(copias_elegiveis, transcricao)

                if token.cancelada:
                    self._marcar_cancelado(ep, "titulos")
                    return

                # Revalida edições manuais salvas no disco ou sincronizadas enquanto a Etapa 7 executava
                self._mesclar_edicoes_manuais_disco(ep)
                with self._lock:
                    por_id_copia = {c.id: c for c in copias_elegiveis if getattr(c, "id", "")}
                    for plano_ep in ep.planos:
                        copia = por_id_copia.get(plano_ep.id)
                        if copia is None:
                            continue
                        if (
                            plano_ep.titulo_aprovado_manualmente
                            or plano_ep.titulo_manual_confirmado
                            or plano_ep.id in ep.planos_editados_manualmente
                        ):
                            # Preserva o título editado/confirmado manualmente durante a execução da IA!
                            continue
                        plano_ep.titulos = list(copia.titulos)
                        plano_ep.titulos_v2 = copy.deepcopy(copia.titulos_v2)
                        plano_ep.titulo_escolhido = copia.titulo_escolhido
                        plano_ep.titulo_capa = copia.titulo_capa
                        plano_ep.titulo_origem = copia.titulo_origem
                        plano_ep.titulo_auto_aprovado = copia.titulo_auto_aprovado
                        plano_ep.titulo_confirmacao_pendente = copia.titulo_confirmacao_pendente
                        plano_ep.avisos = list(copia.avisos)
                        if plano_ep.status not in ("renderizando", "concluído"):
                            plano_ep.status = copia.status

                    ep.registrar_transicao("titulos", "concluido", "Títulos gerados e validados")
                    try:
                        salvar_lista_agencia(ep.video_path, transcricao.video_id, ep.planos)
                    except Exception:
                        pass
                    self.salvar_estado()
                self._emitir_evento("etapa_concluida", ep, "titulos", "Títulos automáticos prontos.")

            # Etapa 8: Preparação dos Cortes e Legendas (tratamento granular por corte!)
            if ep.estado_etapas.get("preparacao") != "concluido" or not ep.planos_prontos:
                with self._lock:
                    ep.etapa_atual = "preparacao"
                    ep.progresso_pct = 82.0
                    ep.mensagem_status = "Preparando cortes, timelines e legendas…"
                    ep.registrar_transicao("preparacao", "executando", ep.mensagem_status)
                self._emitir_evento("etapa_inicio", ep, "preparacao", ep.mensagem_status)

                self._mesclar_edicoes_manuais_disco(ep)
                with self._lock:
                    prontos, _revisao_tit = selecionar_prontos(ep.planos, transcricao)
                preparacoes: dict[str, PreparacaoCorte] = {}
                prontos_validados: list[PlanoAgencia] = []
                falhas_preparacao: list[str] = []
                ep.perfil_visual_id = normalizar_perfil_id(ep.perfil_visual_id)
                perfil = obter_perfil(ep.perfil_visual_id)
                ajustes = ep.ajustes_av or carregar_ajustes_agencia()

                for idx, plano in enumerate(prontos, 1):
                    if token.cancelada:
                        self._marcar_cancelado(ep, "preparacao")
                        return
                    try:
                        if self._fn_preparar is not None:
                            prep = self._fn_preparar(
                                ep.video_path, plano, transcricao, perfil, ep.opcoes_edicao, ep.intro, prontos, ajustes
                            )
                        else:
                            prep = preparar_corte(
                                ep.video_path,
                                plano,
                                transcricao,
                                perfil=perfil,
                                opcoes=ep.opcoes_edicao,
                                intro=ep.intro,
                                outros_planos=prontos,
                                ajustes=ajustes,
                            )
                    except Exception as exc_prep:
                        with self._lock:
                            plano.status = "revisão"
                            plano.estado_editorial = "revisao"
                            plano.erro = f"Falha na preparação do corte: {exc_prep}"
                            if plano.erro not in plano.avisos:
                                plano.avisos.append(plano.erro)
                            ep.rejeicoes.append(f"{plano.id}: {plano.erro}")
                        falhas_preparacao.append(f"{plano.id}: {exc_prep}")
                        continue

                    itens_gate_prep = _converter_itens_gate(getattr(prep, "gate", None)) if prep is not None else None
                    if itens_gate_prep is not None:
                        with self._lock:
                            plano.gate = gate_como_dicts(itens_gate_prep)

                    gate_ok, motivo_gate = _avaliar_gate_preparacao(prep)
                    if prep is None or not gate_ok:
                        motivo = motivo_gate or plano.erro or "Corte reprovado na preparação da timeline/legendas."
                        with self._lock:
                            plano.status = "revisão"
                            plano.estado_editorial = "revisao"
                            plano.erro = str(motivo)
                            if str(motivo) not in plano.avisos:
                                plano.avisos.append(str(motivo))
                        falhas_preparacao.append(f"{plano.id}: {motivo}")
                        continue

                    preparacoes[plano.id] = prep
                    prontos_validados.append(plano)

                    # Limpeza periódica de buffers durante a preparação
                    import gc
                    gc.collect()

                try:
                    with self._lock:
                        salvar_lista_agencia(ep.video_path, transcricao.video_id, ep.planos)
                except Exception:
                    pass

                if not prontos_validados:
                    with self._lock:
                        ep.planos_prontos = []
                        ep.preparacoes = {}
                        ep.estado = "revisao"
                        ep.etapa_atual = "revisao"
                        ep.progresso_pct = 100.0
                        det = "; ".join(falhas_preparacao) if falhas_preparacao else "Todos os candidatos requerem revisão manual antes da renderização."
                        ep.erro = det if falhas_preparacao else ""
                        ep.mensagem_status = f"Todos os candidatos requerem revisão manual ({len(falhas_preparacao)} falha(s) na preparação)." if falhas_preparacao else det
                        ep.registrar_transicao("preparacao", "revisao", ep.mensagem_status)
                    self._emitir_evento("episodio_revisao", ep, "preparacao", ep.mensagem_status)
                    return

                with self._lock:
                    ep.planos_prontos = prontos_validados
                    ep.preparacoes = preparacoes
                    ep.progresso_pct = 100.0
                    msg_prep = f"{len(prontos_validados)} corte(s) preparado(s) para renderização."
                    if falhas_preparacao:
                        msg_prep += f" ({len(falhas_preparacao)} em revisão)."
                    ep.mensagem_status = msg_prep
                    ep.registrar_transicao("preparacao", "concluido", ep.mensagem_status)
                    sucesso_estagio = True
                self._emitir_evento("etapa_concluida", ep, "preparacao", ep.mensagem_status)
            else:
                sucesso_estagio = True

        except Exception as exc:
            if token.cancelada or "cancelad" in str(exc).lower():
                self._marcar_cancelado(ep, ep.etapa_atual)
            else:
                self._marcar_erro(ep, ep.etapa_atual, str(exc))
        finally:
            with self._lock:
                try:
                    ep._token_atual = None
                    self._ativos_agencia.discard(ep.id)
                    if sucesso_estagio and not ep.cancelado:
                        # Avança IMEDIATAMENTE para a fila de Renderização!
                        self._fila_renderizacao.append(ep.id)
                        ep.em_fila_etapa = "renderizacao"
                        ep.etapa_atual = "renderizacao"
                        ep.progresso_pct = 0.0
                        ep.mensagem_status = "Encaminhado para a fila de renderização"
                        self._emitir_evento("encaminhado_renderizacao", ep, "renderizacao", ep.mensagem_status)
                    self.salvar_estado()
                finally:
                    self._coordenador.liberar("agencia", dono_slot)
                    self._escalonar()

    # ─── Worker Estágio 3: Renderização + Validação e Registro de Saída ───────

    def _worker_renderizacao(self, ep: ContextoEpisodio):
        token = TokenCancelamentoEpisodio(ep)
        ep._token_atual = token
        dono_slot = ("esteira_render", id(self), ep.id)

        try:
            with self._lock:
                if token.cancelada:
                    self._marcar_cancelado(ep, "renderizacao")
                    return
                if not ep.planos_prontos or ep.transcricao is None:
                    raise RuntimeError("Sem cortes preparados ou transcrição para renderizar.")
                ep.estado = "em_execucao"
                ep.erro = ""
                ep.etapa_falha = ""
                ep.etapa_atual = "renderizacao"
                ep.progresso_pct = 0.0
                ep.mensagem_status = f"Renderizando cortes (0/{len(ep.planos_prontos)})…"
                ep.registrar_transicao("renderizacao", "executando", "Iniciando renderização dos cortes")
            self._emitir_evento("etapa_inicio", ep, "renderizacao", ep.mensagem_status)

            Path(ep.pasta_saida_episodio).mkdir(parents=True, exist_ok=True)
            transcricao = ep.transcricao
            ep.perfil_visual_id = normalizar_perfil_id(ep.perfil_visual_id)
            perfil = obter_perfil(ep.perfil_visual_id)
            ajustes = ep.ajustes_av or carregar_ajustes_agencia()
            total = len(ep.planos_prontos)
            erros_render: list[str] = []

            for idx, plano in enumerate(ep.planos_prontos, 1):
                if token.cancelada:
                    self._marcar_cancelado(ep, "renderizacao")
                    return

                self._mesclar_edicoes_manuais_disco(ep)

                # Só reaproveita corte concluído se pertencer comprovadamente à pasta_saida_episodio atual
                if (
                    plano.status == "concluído"
                    and arquivo_exportado_pertence_ao_episodio(
                        plano.caminho_saida, ep, plano, exigir_sidecar=self._usa_renderizador_real_agencia
                    )
                ):
                    with self._lock:
                        if plano.caminho_saida not in ep.arquivos_exportados:
                            ep.arquivos_exportados.append(plano.caminho_saida)
                    continue
                elif plano.status == "concluído":
                    # Tinha caminho de pasta antiga ou inválida: limpa antes de tentar reconciliação ou re-renderizar
                    with self._lock:
                        plano.status = "pendente"
                        plano.caminho_saida = ""

                # Reconciliação segura pós-interrupção (entre mover o MP4 e persistir o estado):
                # Reutiliza somente se <saida>.metricas.json confirmar video_id, corte_id e qualidade aprovada.
                reconciliado = reconciliar_corte_exportado_por_metricas(ep, plano)
                if reconciliado:
                    with self._lock:
                        plano.status = "concluído"
                        plano.erro = ""
                        plano.caminho_saida = reconciliado
                        if reconciliado not in ep.arquivos_exportados:
                            ep.arquivos_exportados.append(reconciliado)
                        try:
                            salvar_lista_agencia(ep.video_path, transcricao.video_id, ep.planos)
                        except Exception:
                            pass
                        self.salvar_estado()
                    continue

                with self._lock:
                    plano.status = "renderizando"

                def prog_corte(pct: float, msg: str, indice_atual=idx):
                    if token.cancelada:
                        return
                    with self._lock:
                        geral = ((indice_atual - 1) + max(0.0, min(100.0, float(pct))) / 100.0) / max(1, total) * 100.0
                        ep.progresso_pct = round(geral, 1)
                        ep.mensagem_status = f"Corte {indice_atual}/{total}: {msg}"
                    self._emitir_evento("progresso", ep, "renderizacao", ep.mensagem_status)

                kwargs_render = dict(
                    perfil=perfil,
                    ajustes=ajustes,
                    opcoes=ep.opcoes_edicao,
                    intro=ep.intro,
                    outros_planos=ep.planos_prontos,
                    exportar_srt=ep.exportar_srt,
                    gerar_capa=ep.gerar_capa,
                    cancelada_ref=token,
                    progresso_cb=prog_corte,
                )
                if self._fn_renderizar_aceita_preparacao:
                    kwargs_render["preparacao"] = ep.preparacoes.get(plano.id)

                try:
                    resultado = self._fn_renderizar(
                        ep.video_path,
                        plano,
                        transcricao,
                        ep.pasta_saida_episodio,
                        **kwargs_render,
                    )
                except Exception as exc_render:
                    if token.cancelada or "cancelad" in str(exc_render).lower():
                        self._marcar_cancelado(ep, "renderizacao")
                        return
                    with self._lock:
                        plano.status = "revisão"
                        plano.erro = f"Exceção na renderização: {exc_render}"
                        plano.caminho_saida = ""
                        erros_render.append(f"{plano.id}: {plano.erro}")
                        self.salvar_estado()
                    continue

                if token.cancelada or (isinstance(resultado, dict) and "cancelad" in str(resultado.get("erro", "")).lower()):
                    self._marcar_cancelado(ep, "renderizacao")
                    return

                if isinstance(resultado, dict) and resultado.get("sucesso"):
                    caminho_out = str(resultado.get("caminho_saida", ""))
                    if not arquivo_exportado_pertence_ao_episodio(
                        caminho_out,
                        ep,
                        plano,
                        exigir_relatorio_dict=resultado,
                        exigir_sidecar=self._usa_renderizador_real_agencia,
                    ):
                        with self._lock:
                            plano.status = "erro"
                            plano.caminho_saida = ""
                            plano.erro = (
                                "Arquivo exportado inválido, sem sidecar .metricas.json válido ou fora da pasta de destino do episódio "
                                f"({ep.pasta_saida_episodio})."
                            )
                        erros_render.append(f"{plano.id}: {plano.erro}")
                    else:
                        with self._lock:
                            plano.status = "concluído"
                            plano.erro = ""
                            plano.caminho_saida = caminho_out
                            if caminho_out not in ep.arquivos_exportados:
                                ep.arquivos_exportados.append(caminho_out)
                            ep.relatorios_exportacao[plano.id] = resultado.get("relatorio", {})
                        try:
                            ctx_t = montar_contexto(plano, transcricao)
                            registrar_uso(plano.titulo, ctx_t)
                        except Exception:
                            pass
                else:
                    detalhe_erro = resultado.get("erro", "Falha desconhecida no FFmpeg") if isinstance(resultado, dict) else "Falha na renderização"
                    with self._lock:
                        if isinstance(resultado, dict) and resultado.get("revisao"):
                            plano.status = "revisão"
                        else:
                            plano.status = "erro"
                        plano.caminho_saida = ""
                        plano.erro = str(detalhe_erro)
                    erros_render.append(f"{plano.id}: {detalhe_erro}")

                # Persiste após cada corte para evitar duplicatas (_2.mp4) caso ocorra retomada
                with self._lock:
                    try:
                        salvar_lista_agencia(ep.video_path, transcricao.video_id, ep.planos)
                    except Exception:
                        pass
                    self.salvar_estado()

                # Liberação agressiva de memória e encerramento de buffers entre cortes
                import gc
                gc.collect()
                time.sleep(0.1)

            # Etapa 10: Validação e Registro dos Arquivos de Saída
            with self._lock:
                ep.etapa_atual = "validacao_saida"
                ep.registrar_transicao("validacao_saida", "executando", "Verificando arquivos de saída")
                # Filtra ep.arquivos_exportados garantindo apenas arquivos que pertencem ao episódio
                ep.arquivos_exportados = [
                    arq for arq in ep.arquivos_exportados
                    if arquivo_exportado_pertence_ao_episodio(
                        arq, ep, exigir_sidecar=self._usa_renderizador_real_agencia
                    )
                ]

            try:
                with self._lock:
                    salvar_lista_agencia(ep.video_path, transcricao.video_id, ep.planos)
            except Exception:
                pass

            if not ep.arquivos_exportados and erros_render:
                raise RuntimeError("Nenhum corte pôde ser renderizado: " + "; ".join(erros_render))

            with self._lock:
                cortes_em_revisao = [
                    p for p in ep.planos
                    if p not in ep.planos_prontos or p.status in ("revisão", "erro")
                ]
                ep.registrar_transicao("renderizacao", "concluido", f"{len(ep.arquivos_exportados)}/{total} renderizados")
                ep.registrar_transicao("validacao_saida", "concluido", f"{len(ep.arquivos_exportados)} arquivos validados")
                ep.progresso_pct = 100.0
                if erros_render or cortes_em_revisao:
                    ep.estado = "revisao"
                    ep.etapa_atual = "revisao"
                    ep.erro = "; ".join(erros_render) if erros_render else ""
                    ep.mensagem_status = (
                        f"Concluído com ressalvas: {len(ep.arquivos_exportados)} exportado(s), "
                        f"{len(cortes_em_revisao)} corte(s) em revisão."
                    )
                else:
                    ep.estado = "concluido"
                    ep.etapa_atual = "concluido"
                    ep.erro = ""
                    ep.mensagem_status = f"Concluído! {len(ep.arquivos_exportados)}/{total} corte(s) exportado(s)."
            self._emitir_evento("episodio_concluido", ep, "validacao_saida", ep.mensagem_status)

        except Exception as exc:
            if token.cancelada or "cancelad" in str(exc).lower():
                self._marcar_cancelado(ep, ep.etapa_atual)
            else:
                self._marcar_erro(ep, ep.etapa_atual, str(exc))
        finally:
            with self._lock:
                try:
                    ep._token_atual = None
                    self._ativos_renderizacao.discard(ep.id)
                    self.salvar_estado()
                finally:
                    self._coordenador.liberar("render", dono_slot)
                    self._escalonar()

    # ─── Tratamento de Erros, Cancelamento e Repetição ────────────────────────

    def _marcar_erro(self, ep: ContextoEpisodio, etapa: str, mensagem_erro: str):
        with self._lock:
            ep.estado = "erro"
            ep.etapa_falha = etapa
            ep.erro = str(mensagem_erro)
            ep.mensagem_status = f"Erro em {ROTULOS_ETAPA.get(etapa, etapa)}: {mensagem_erro}"
            ep.registrar_transicao(etapa, "erro", str(mensagem_erro))
        self._emitir_evento("episodio_erro", ep, etapa, str(mensagem_erro))

    def _marcar_cancelado(self, ep: ContextoEpisodio, etapa: str):
        with self._lock:
            ep.cancelado = True
            ep.estado = "cancelado"
            ep.mensagem_status = f"Cancelado pelo usuário ({ROTULOS_ETAPA.get(etapa, etapa)})."
            ep.registrar_transicao(etapa, "cancelado", "Cancelado pelo usuário")
        self._emitir_evento("episodio_cancelado", ep, etapa, ep.mensagem_status)

    def cancelar_episodio(self, episodio_id: str) -> bool:
        """
        Cancela um episódio específico preservando os resultados válidos já obtidos
        e impedindo que novas etapas sejam iniciadas para ele.
        """
        with self._lock:
            ep = self._episodios.get(episodio_id)
            if not ep:
                return False
            ep.cancelado = True
            if ep._token_atual is not None:
                ep._token_atual.cancelar()
            if ep.id in self._fila_transcricao:
                self._fila_transcricao.remove(ep.id)
            if ep.id in self._fila_agencia:
                self._fila_agencia.remove(ep.id)
            if ep.id in self._fila_renderizacao:
                self._fila_renderizacao.remove(ep.id)
            ep.em_fila_etapa = None
            if ep.id not in self._ativos_transcricao and ep.id not in self._ativos_agencia and ep.id not in self._ativos_renderizacao:
                ep.estado = "cancelado"
                ep.mensagem_status = "Cancelado pelo usuário."
                ep.registrar_transicao(ep.etapa_atual, "cancelado", "Cancelado na fila")
                self._emitir_evento("episodio_cancelado", ep, ep.etapa_atual, ep.mensagem_status)
            self.salvar_estado()
            self._atualizar_sinal_ocioso()
            return True

    def cancelar_todos(self):
        """Cancela todos os episódios em fila ou em execução."""
        with self._lock:
            ids = list(self._ordem_ids)
        for eid in ids:
            ep = self.obter_episodio(eid)
            if ep and ep.estado in ("aguardando", "em_execucao"):
                self.cancelar_episodio(eid)

    def tentar_novamente(self, episodio_id: str) -> bool:
        """
        Repete apenas a etapa com falha (e suas dependências invalidadas),
        reaproveitando integralmente as etapas anteriores já concluídas com sucesso.
        """
        with self._lock:
            ep = self._episodios.get(episodio_id)
            if not ep:
                return False
            if ep.id in self._ativos_transcricao or ep.id in self._ativos_agencia or ep.id in self._ativos_renderizacao:
                return False

            ep.cancelado = False
            ep.erro = ""
            ep.tentativas += 1
            falha = ep.etapa_falha

            if falha in ("importacao", "transcricao"):
                ep.estado_etapas["transcricao"] = "pendente"
                if falha == "importacao":
                    ep.estado_etapas["importacao"] = "pendente"
            elif falha in ("intro", "analise_agencia", "momentos_fortes", "hooks", "titulos", "preparacao"):
                ep.estado_etapas[falha] = "pendente"
                ep.estado_etapas["preparacao"] = "pendente"
            elif falha in ("renderizacao", "validacao_saida"):
                ep.estado_etapas["renderizacao"] = "pendente"
                ep.estado_etapas["validacao_saida"] = "pendente"

            ep.etapa_falha = ""
            ep.estado = "aguardando"
            self._enfileirar_na_etapa_elegivel(ep)
            self.salvar_estado()
            self._emitir_evento("episodio_retentativa", ep, ep.etapa_atual, f"Tentativa #{ep.tentativas} iniciada.")
            self._escalonar()
            return True

    def remover_episodio(self, episodio_id: str) -> bool:
        """Remove da fila um episódio que não esteja em execução ativa."""
        with self._lock:
            ep = self._episodios.get(episodio_id)
            if not ep:
                return False
            if ep.id in self._ativos_transcricao or ep.id in self._ativos_agencia or ep.id in self._ativos_renderizacao:
                return False
            if ep.id in self._fila_transcricao:
                self._fila_transcricao.remove(ep.id)
            if ep.id in self._fila_agencia:
                self._fila_agencia.remove(ep.id)
            if ep.id in self._fila_renderizacao:
                self._fila_renderizacao.remove(ep.id)
            self._episodios.pop(episodio_id, None)
            if episodio_id in self._ordem_ids:
                self._ordem_ids.remove(episodio_id)
            self.salvar_estado()
            self._atualizar_sinal_ocioso()
            return True

    def concluir_ou_arquivar_episodio(self, episodio_id: str) -> bool:
        """
        Marca o episódio como concluído e remove-o imediatamente da esteira ativa,
        liberando o card visual mesmo se estiver em 'revisão' ou com contadores zerados.
        """
        with self._lock:
            ep = self._episodios.get(episodio_id)
            if not ep:
                return False
            self._ativos_transcricao.discard(episodio_id)
            self._ativos_agencia.discard(episodio_id)
            self._ativos_renderizacao.discard(episodio_id)
            if ep.id in self._fila_transcricao:
                self._fila_transcricao.remove(ep.id)
            if ep.id in self._fila_agencia:
                self._fila_agencia.remove(ep.id)
            if ep.id in self._fila_renderizacao:
                self._fila_renderizacao.remove(ep.id)

            ep.estado = "concluido"
            ep.mensagem_status = "Concluído e arquivado pelo usuário"
            ep.registrar_transicao("concluido", "concluido", ep.mensagem_status)

            self._episodios.pop(episodio_id, None)
            if episodio_id in self._ordem_ids:
                self._ordem_ids.remove(episodio_id)
            self.salvar_estado()
            self._atualizar_sinal_ocioso()
            self._emitir_evento("episodio_arquivado", ep, "concluido", "Episódio arquivado pelo usuário.")
            return True

    def aprovar_e_exportar_episodio(self, episodio_id: str) -> tuple[int, list[str]]:
        """
        Atribui aprovação a todos os cortes com título definido do episódio e aciona
        imediatamente a esteira para preparação e renderização/exportação em massa,
        sem necessidade de aprovação manual individual na Agência.
        """
        with self._lock:
            ep = self._episodios.get(episodio_id)
            if not ep:
                return 0, ["Episódio não encontrado na esteira."]
            if ep.id in self._ativos_renderizacao:
                return 0, ["Este episódio já está em renderização ativa."]
            if ep.id in self._ativos_transcricao:
                return 0, ["Aguarde a conclusão da transcrição antes de aprovar os cortes."]
            if not ep.planos:
                return 0, [f"O episódio '{ep.nome}' ainda não possui cortes gerados."]

            transcricao = ep.transcricao
            if transcricao is None:
                transcricao = carregar_transcricao(ep.video_path)
                if transcricao:
                    ep.transcricao = transcricao
            if transcricao is None:
                return 0, ["Transcrição do episódio não encontrada para validação dos cortes."]

            avisos: list[str] = []
            aprovados = 0

            from modules.agencia.producao import aprovar_corte, assinatura
            from modules.agencia.titulos_manual import aprovar_titulo
            from modules.titulos_v2 import titulo_pendente

            for idx, plano in enumerate(ep.planos, 1):
                texto_titulo = (plano.titulo_escolhido or (plano.titulos[0].texto if plano.titulos else "")).strip()
                if not texto_titulo or titulo_pendente(texto_titulo):
                    avisos.append(f"Corte {idx} ignorado: sem título definido ou pendente.")
                    continue

                if not plano.segmentos and plano.fim > plano.inicio:
                    plano.segmentos = [{"inicio": plano.inicio, "fim": plano.fim}]

                try:
                    aprovar_corte(plano, transcricao, texto_titulo, intro=ep.intro)
                except ValueError:
                    if ep.intro:
                        try:
                            aprovar_corte(plano, transcricao, texto_titulo, intro=None)
                        except ValueError:
                            aprovar_titulo(plano, texto_titulo)
                            plano.corte_aprovado_manualmente = True
                            plano.corte_confirmado_hash = assinatura(plano)
                    else:
                        aprovar_titulo(plano, texto_titulo)
                        plano.corte_aprovado_manualmente = True
                        plano.corte_confirmado_hash = assinatura(plano)

                plano.estado_editorial = "automatico"
                plano.status = "pendente"
                plano.erro = ""
                plano.selecionado = True
                plano.corte_aprovado_manualmente = True
                plano.corte_confirmado_hash = assinatura(plano)
                ep.planos_editados_manualmente.add(plano.id)
                aprovados += 1

            if aprovados == 0:
                msg_falha = "Nenhum corte pôde ser aprovado."
                if avisos:
                    msg_falha += " Motivo(s):\n• " + "\n• ".join(avisos)
                return 0, [msg_falha]

            try:
                salvar_lista_agencia(ep.video_path, transcricao.video_id, ep.planos)
            except Exception:
                pass

            ep.perfil_visual_id = normalizar_perfil_id(ep.perfil_visual_id)
            ep.cancelado = False
            ep.erro = ""
            ep.etapa_falha = ""
            ep.estado_etapas["preparacao"] = "pendente"
            ep.planos_prontos = []
            ep.preparacoes = {}
            ep.estado = "aguardando"
            ep.em_fila_etapa = "agencia"
            ep.etapa_atual = "preparacao"
            ep.progresso_pct = 80.0
            ep.mensagem_status = f"{aprovados} corte(s) aprovado(s). Iniciando preparação e renderização…"

            if ep.id not in self._fila_agencia:
                self._fila_agencia.append(ep.id)
            if ep.id in self._fila_renderizacao:
                self._fila_renderizacao.remove(ep.id)

            self.salvar_estado()
            self._pausada = False
            self._evento_ocioso.clear()

        self._escalonar()
        self._emitir_evento("episodio_aprovado_lote", ep, "preparacao", ep.mensagem_status)
        return aprovados, avisos

    def limpar_concluidos(self) -> int:
        """Remove da visualização os episódios já concluídos ou cancelados."""
        removidos = 0
        with self._lock:
            for eid in list(self._ordem_ids):
                ep = self._episodios.get(eid)
                if ep and ep.estado in ("concluido", "cancelado"):
                    if self.remover_episodio(eid):
                        removidos += 1
        return removidos

    # ─── Persistência e Recuperação ───────────────────────────────────────────

    def salvar_estado(self) -> bool:
        if not self.caminho_persistencia:
            return False
        with self._lock:
            try:
                dados = {
                    "versao": 1,
                    "atualizado_em": _agora_iso(),
                    "episodios": [
                        self._episodios[eid].como_dict_persistente()
                        for eid in self._ordem_ids
                        if eid in self._episodios
                    ],
                }
                self.caminho_persistencia.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.caminho_persistencia.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
                tmp.replace(self.caminho_persistencia)
                return True
            except Exception:
                return False

    def carregar_estado(self) -> int:
        """Restaura episódios persistidos de uma sessão anterior."""
        if not self.caminho_persistencia or not self.caminho_persistencia.is_file():
            return 0
        try:
            dados = json.loads(self.caminho_persistencia.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return 0

        carregados = 0
        with self._lock:
            for raw in dados.get("episodios", []):
                if not isinstance(raw, dict) or "id" not in raw or "video_path" not in raw:
                    continue
                try:
                    ep = ContextoEpisodio.de_dict_persistente(raw)
                except Exception:
                    continue
                if ep.id not in self._episodios:
                    self._episodios[ep.id] = ep
                    self._ordem_ids.append(ep.id)
                    carregados += 1
        return carregados


# Instância global compartilhada para a interface gráfica (carrega/inicializa estado_esteira.json)
esteira_global = EsteiraProducao(carregar_estado_inicial=True)
