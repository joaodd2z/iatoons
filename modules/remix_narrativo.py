"""Análise, validação e timeline do modo independente Remix Narrativo."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
from pathlib import Path
from typing import Callable, Protocol

from pydantic import BaseModel, Field

from modules.analise_ia import ErroGemini, _erro_seguro, _janelas
from modules.config_ia import ConfigIA
from modules.credenciais import obter_chave_gemini
from modules.ganchos_ab import TrechoFonte
from modules.intro import carregar_intro
from modules.preset import DADOS_DIR
from modules.transcricao import SegmentoTranscricao, Transcricao, transcricao_corresponde_origem


REMIX_CACHE_DIR = DADOS_DIR / "remix_narrativo"
REMIX_LISTAS_DIR = DADOS_DIR / "listas_remix"
VERSAO_REGRAS_REMIX = "remix-narrativo-v1"


@dataclass(frozen=True)
class SegmentoNarrativo:
    inicio: float
    fim: float
    funcao: str

    @property
    def duracao(self) -> float:
        return self.fim - self.inicio


@dataclass
class CandidatoRemix:
    titulos: list[str]
    descricao: str
    gancho_inicio: float
    gancho_fim: float
    gancho_motivo: str
    segmentos: list[SegmentoNarrativo]
    justificativa: str
    continuidade: str
    confianca: float
    recomendado: bool = True
    remix_rules_version: str = ""
    validacao_remix: dict = field(default_factory=dict)
    titulos_v2: dict = field(default_factory=dict)
    story_identity: str = ""
    plano_agencia: dict = field(default_factory=dict)

    @property
    def inicio(self) -> float:
        return self.segmentos[0].inicio

    @property
    def fim(self) -> float:
        return self.segmentos[-1].fim

    def como_dict(self) -> dict:
        return asdict(self)


class _SegmentoResposta(BaseModel):
    inicio: float
    fim: float
    funcao: str


class _GanchoResposta(BaseModel):
    inicio: float
    fim: float
    motivo: str = ""


class _RemixResposta(BaseModel):
    titulos: list[str] = Field(default_factory=list)
    titulo: str = ""
    descricao: str = ""
    gancho: _GanchoResposta
    segmentos_narrativos: list[_SegmentoResposta] = Field(default_factory=list)
    justificativa: str = ""
    continuidade: str = ""
    confianca: float = 0.0
    remix_recomendado: bool = True


class _RespostaRemixes(BaseModel):
    remixes: list[_RemixResposta] = Field(default_factory=list)


class ProvedorRemix(Protocol):
    def listar_modelos(self) -> list[str]: ...
    def analisar(self, modelo: str, prompt: str) -> dict: ...


class ProvedorGeminiRemix:
    def __init__(self, chave: str):
        from google import genai
        self._cliente = genai.Client(api_key=chave)

    def listar_modelos(self) -> list[str]:
        try:
            nomes = []
            for item in self._cliente.models.list():
                acoes = list(getattr(item, "supported_actions", None) or [])
                if acoes and "generateContent" not in acoes:
                    continue
                nome = str(getattr(item, "name", "") or "").removeprefix("models/")
                if nome.startswith("gemini-"):
                    nomes.append(nome)
            return sorted(set(nomes))
        except Exception as erro:
            raise _erro_seguro(erro) from None

    def analisar(self, modelo: str, prompt: str) -> dict:
        from google.genai import types
        try:
            resposta = self._cliente.models.generate_content(
                model=modelo,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=_RespostaRemixes,
                    temperature=0.45,
                    max_output_tokens=12288,
                ),
            )
            if resposta.parsed is not None:
                return resposta.parsed.model_dump()
            if resposta.text:
                return json.loads(resposta.text)
            raise ErroGemini("A Gemini devolveu uma resposta de remix vazia.")
        except ErroGemini:
            raise
        except Exception as erro:
            raise _erro_seguro(erro) from None


def criar_provedor_remix() -> ProvedorGeminiRemix:
    chave = obter_chave_gemini()
    if not chave:
        raise ErroGemini("Configure a chave Gemini na tela Configurações de IA.")
    return ProvedorGeminiRemix(chave)


def _hash_transcricao(transcricao: Transcricao) -> str:
    conteudo = "\n".join(
        f"{s.inicio:.3f}|{s.fim:.3f}|{s.texto.strip()}" for s in transcricao.segmentos
    )
    return hashlib.sha256(conteudo.encode("utf-8")).hexdigest()


def caminho_cache_remix(transcricao: Transcricao, config: ConfigIA) -> Path:
    from modules.agencia.remix import REMIX_RULES_VERSION
    base = (
        f"{transcricao.video_id}|{_hash_transcricao(transcricao)}|{config.modelo}|"
        f"{config.max_chamadas_episodio}|{config.max_cortes}|{config.janela_segundos}|"
        f"{config.sobreposicao_segundos}|{VERSAO_REGRAS_REMIX}|{REMIX_RULES_VERSION}"
    )
    return REMIX_CACHE_DIR / f"{hashlib.sha256(base.encode()).hexdigest()}.json"


def _alinhar(valor: float, segmentos: list[SegmentoTranscricao], inicio: bool) -> float:
    candidatos = [s.inicio if inicio else s.fim for s in segmentos]
    return min(candidatos, key=lambda ponto: abs(ponto - valor))


def _intersecta(a_inicio: float, a_fim: float, b_inicio: float, b_fim: float) -> bool:
    return min(a_fim, b_fim) - max(a_inicio, b_inicio) > 0.05


def validar_remixes(
    dados: list[dict], transcricao: Transcricao, max_remixes: int = 5
) -> tuple[list[CandidatoRemix], list[str]]:
    """Valida timestamps e continuidade localmente; candidatos ruins não afetam os demais."""
    segmentos_transcricao = [s for s in transcricao.segmentos if s.fim > s.inicio]
    intro = carregar_intro(transcricao)
    validos: list[CandidatoRemix] = []
    rejeicoes: list[str] = []
    for indice, item in enumerate(dados, 1):
        try:
            if item.get("remix_recomendado", True) is False:
                rejeicoes.append(f"Remix {indice}: a própria análise recomendou preservar o corte normal.")
                continue
            gancho = item["gancho"]
            gi, gf = float(gancho["inicio"]), float(gancho["fim"])
            if not math.isfinite(gi + gf) or not 2.0 <= gf - gi <= 6.0:
                raise ValueError("gancho deve durar entre 2 e 6 segundos")
            if gi < 0 or gf > transcricao.duracao + 0.05:
                raise ValueError("gancho fora do episodio")
            gi, gf = _alinhar(gi, segmentos_transcricao, True), _alinhar(gf, segmentos_transcricao, False)
            if not 1.5 <= gf - gi <= 8.0:
                raise ValueError("gancho não coincide com limites reais de fala")

            brutos = item.get("segmentos_narrativos", [])
            if not 2 <= len(brutos) <= 5:
                raise ValueError("são necessários de 2 a 5 segmentos narrativos")
            narrativos = []
            for bruto in brutos:
                ri, rf = float(bruto["inicio"]), float(bruto["fim"])
                if not math.isfinite(ri + rf) or ri < 0 or rf > transcricao.duracao + 0.05 or rf <= ri:
                    raise ValueError("segmento fora do episodio")
                inicio = _alinhar(ri, segmentos_transcricao, True)
                fim = _alinhar(rf, segmentos_transcricao, False)
                if abs(inicio-ri) > 1.2 or abs(fim-rf) > 1.2:
                    raise ValueError("segmento distante dos limites reais de fala")
                if inicio < 0 or fim > transcricao.duracao + 0.001 or fim <= inicio:
                    raise ValueError("segmento fora do episódio")
                narrativos.append(SegmentoNarrativo(inicio, fim, str(bruto.get("funcao", "desenvolvimento"))))
            if any(atual.inicio < anterior.fim - 0.05 for anterior, atual in zip(narrativos, narrativos[1:])):
                raise ValueError("segmentos sobrepostos ou fora da ordem cronológica")
            if any(atual.inicio - anterior.fim > 45.0 for anterior, atual in zip(narrativos, narrativos[1:])):
                raise ValueError("saltos grandes indicam histórias possivelmente diferentes")
            if not any(_intersecta(gi, gf, s.inicio, s.fim) for s in narrativos):
                raise ValueError("a sequência narrativa não chega naturalmente ao gancho")
            if intro and intro.ativo:
                todos = [(gi, gf)] + [(s.inicio, s.fim) for s in narrativos]
                if any(_intersecta(i, f, intro.inicio, intro.fim) for i, f in todos):
                    raise ValueError("o candidato atravessa a intro detectada")
            continuidade = " ".join(str(item.get("continuidade", "")).split())
            if not continuidade:
                raise ValueError("continuidade causal não justificada")
            confianca = float(item.get("confianca", 0.0))
            if not 0.0 <= confianca <= 1.0 or confianca < 0.35:
                raise ValueError("confiança insuficiente na continuidade")
            titulos = []
            fontes = item.get("titulos") or ([item.get("titulo")] if item.get("titulo") else [])
            for titulo in fontes:
                limpo = " ".join(str(titulo).split()).strip(" .").upper()
                if limpo and limpo not in titulos:
                    titulos.append(limpo)
            if len(titulos) != 3:
                raise ValueError("é obrigatório retornar três títulos distintos")
            candidato = CandidatoRemix(
                titulos=titulos,
                descricao=str(item.get("descricao", "")).strip(),
                gancho_inicio=gi,
                gancho_fim=gf,
                gancho_motivo=str(gancho.get("motivo", "")).strip(),
                segmentos=narrativos,
                justificativa=str(item.get("justificativa", "")).strip(),
                continuidade=continuidade,
                confianca=confianca,
            )
            from modules.agencia.editorial import plano_de_remix
            from modules.agencia.remix import avaliar_remix_real
            from modules.agencia.timeline import TimelineAgencia, TrechoTimeline
            plano = plano_de_remix(candidato, transcricao)
            trechos = timeline_remix(candidato)
            timeline = TimelineAgencia([
                TrechoTimeline(t.inicio, t.fim, "gancho" if n == 0 else "historia")
                for n, t in enumerate(trechos)], "remix", gancho=(gi, gf))
            contrato = avaliar_remix_real(plano, timeline, transcricao, intro=intro)
            if not contrato["remix_valido"]:
                raise ValueError(contrato["motivo_rejeicao"])
            candidato.remix_rules_version = contrato["remix_rules_version"]
            candidato.validacao_remix = contrato
            validos.append(candidato)
        except (KeyError, TypeError, ValueError) as erro:
            rejeicoes.append(f"Remix {indice}: {erro}.")
    return validos[:max_remixes], rejeicoes


def timeline_remix(candidato: CandidatoRemix, repetir_gancho: bool = False) -> list[TrechoFonte]:
    """Monta gancho + história; por padrão pula o gancho quando a cronologia o reencontra."""
    gancho = TrechoFonte(candidato.gancho_inicio, candidato.gancho_fim)
    timeline = [gancho]
    for segmento in candidato.segmentos:
        if not _intersecta(segmento.inicio, segmento.fim, gancho.inicio, gancho.fim):
            timeline.append(TrechoFonte(segmento.inicio, segmento.fim))
            continue
        if segmento.inicio < gancho.inicio - 0.05:
            timeline.append(TrechoFonte(segmento.inicio, gancho.inicio))
        if segmento.fim > gancho.fim + 0.05:
            timeline.append(TrechoFonte(gancho.fim, segmento.fim))
    return [trecho for trecho in timeline if trecho.duracao > 0.05]


def duracao_timeline(candidato: CandidatoRemix, repetir_gancho: bool = False) -> float:
    return sum(t.duracao for t in timeline_remix(candidato, repetir_gancho))


def _prompt_remix(segmentos: list[SegmentoTranscricao], max_remixes: int) -> str:
    linhas = "\n".join(
        f"[{s.inicio:.3f} --> {s.fim:.3f}] {s.texto.strip()}" for s in segmentos
    )
    return f"""Analise a transcrição inteira e proponha no máximo {max_remixes} REMIXES NARRATIVOS.
Cada remix deve usar somente uma mesma história, situação, arco ou cadeia causal.
Estrutura: gancho forte posterior (2–6 s), retorno ao contexto, desenvolvimento,
chegada natural ao gancho e consequência/payoff. Não entregue cenas aleatórias.
O gancho não pode revelar o desfecho completo nem destruir a curiosidade.
Use 2–5 segmentos narrativos cronológicos, com falas completas e poucos cortes.
Se a cena funciona melhor cronologicamente, retorne remix_recomendado=false.
Retorne exatamente três títulos curtos, curiosos, fiéis e sem spoiler por remix.
Todos os timestamps são segundos ABSOLUTOS do episódio.
Explique continuidade causal e justificativa da montagem. Confiança entre 0 e 1.
Formato: {{"remixes":[{{"titulos":["...","...","..."],"descricao":"...",
"gancho":{{"inicio":0,"fim":0,"motivo":"..."}},"segmentos_narrativos":[
{{"inicio":0,"fim":0,"funcao":"contexto"}},{{"inicio":0,"fim":0,"funcao":"desenvolvimento"}}],
"justificativa":"...","continuidade":"...","confianca":0.0,"remix_recomendado":true}}]}}.

TRANSCRIÇÃO:
{linhas}"""


def _analisar_remixes_legado(
    transcricao: Transcricao, config: ConfigIA, provedor: ProvedorRemix | None = None,
    usar_cache: bool = True, cancelada_ref=None,
    progresso_cb: Callable[[float, str], None] | None = None,
) -> list[CandidatoRemix]:
    config.validar()
    if Path(transcricao.video_path).exists() and not transcricao_corresponde_origem(transcricao):
        raise ValueError("A transcrição não corresponde mais ao vídeo atual.")
    if not transcricao.segmentos:
        raise ValueError("A transcrição não contém falas para montar um remix.")
    cache = caminho_cache_remix(transcricao, config)
    if usar_cache and cache.exists():
        try:
            dados = json.loads(cache.read_text(encoding="utf-8"))
            candidatos, _ = validar_remixes(dados.get("remixes_brutos", []), transcricao, config.max_cortes)
            if progresso_cb:
                progresso_cb(100, f"Cache de remix reutilizado: {len(candidatos)} candidatos.")
            return candidatos
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
    if cancelada_ref and cancelada_ref.cancelada:
        raise RuntimeError("Análise de remix cancelada.")
    provedor = provedor or criar_provedor_remix()
    if progresso_cb:
        progresso_cb(5, "Verificando modelo para Remix Narrativo…")
    if config.modelo not in provedor.listar_modelos():
        raise ErroGemini("O modelo selecionado não está disponível para esta chave/projeto.")
    janelas = _janelas(transcricao, config)[:config.max_chamadas_episodio]
    brutos: list[dict] = []
    falhas = []
    for indice, janela in enumerate(janelas, 1):
        if cancelada_ref and cancelada_ref.cancelada:
            raise RuntimeError("Análise de remix cancelada.")
        if progresso_cb:
            progresso_cb(15 + (indice - 1) / max(1, len(janelas)) * 75,
                          f"Procurando histórias {indice}/{len(janelas)}…")
        try:
            resposta = provedor.analisar(config.modelo, _prompt_remix(janela, config.max_cortes))
            itens = resposta.get("remixes", []) if isinstance(resposta, dict) else []
            if not isinstance(itens, list):
                raise ErroGemini("A resposta não contém uma lista de remixes.")
            brutos.extend(itens)
        except ErroGemini as erro:
            falhas.append(str(erro))
    if falhas and len(falhas) == len(janelas):
        raise ErroGemini(f"Nenhuma janela de remix foi analisada: {falhas[0]}")
    candidatos, rejeicoes = validar_remixes(brutos, transcricao, config.max_cortes)
    REMIX_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({
        "versao": VERSAO_REGRAS_REMIX,
        "video_id": transcricao.video_id,
        "hash_transcricao": _hash_transcricao(transcricao),
        "modelo": config.modelo,
        "remixes_brutos": brutos,
        "rejeicoes": rejeicoes,
        "falhas_janelas": falhas,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(cache)
    if progresso_cb:
        progresso_cb(100, f"Remix concluído: {len(candidatos)} válidos; {len(rejeicoes)} rejeitados.")
    return candidatos


def _chave_lista(video_path: str) -> str:
    caminho = Path(video_path).resolve()
    stat = caminho.stat()
    return hashlib.sha256(f"{caminho}|{stat.st_size}|{stat.st_mtime_ns}".encode()).hexdigest()


def salvar_lista_remix(video_path: str, itens: list[dict]) -> Path:
    destino = REMIX_LISTAS_DIR / f"{_chave_lista(video_path)}.json"
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"video_path": str(Path(video_path).resolve()), "cortes": itens},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(destino)
    return destino


def carregar_lista_remix(video_path: str) -> list[dict]:
    origem = REMIX_LISTAS_DIR / f"{_chave_lista(video_path)}.json"
    if not origem.exists():
        return []
    dados = json.loads(origem.read_text(encoding="utf-8"))
    if Path(dados.get("video_path", "")).resolve() != Path(video_path).resolve():
        raise ValueError("A lista de remix pertence a outro vídeo.")
    return [{**item, "origem": item.get("origem", "remix")}
            for item in dados.get("cortes", [])]


class ListaRemixes(list):
    def __init__(self, itens=(), diagnostico=None):
        super().__init__(itens)
        self.diagnostico = diagnostico


def provedor_remix_orcado(estado, transcricao, config):
    """Mantem o transporte de remix e compartilha a cota da sessao do episodio."""
    from modules.ai.comparacao import orcamento_episodio
    budget = orcamento_episodio(estado, transcricao, config.max_chamadas_episodio)

    class ProvedorOrcado:
        gerencia_orcamento = True
        orcamento = budget
        cliente = None

        @property
        def restantes(self):
            return budget.restantes

        def _obter(self):
            if self.cliente is None:
                from modules.ai.factory import obter_provider
                self.cliente = obter_provider(config,modo='agencia',schema=_RespostaRemixes)
            return self.cliente

        def listar_modelos(self):
            return self._obter().listar_modelos()

        def analisar(self, modelo, prompt):
            cliente = self._obter()
            if getattr(cliente,'gerencia_orcamento',False):
                cliente.orcamento = budget
            else:
                budget.reservar()
            return cliente.analisar(modelo,prompt)

    return ProvedorOrcado()


def _remix_bruto_de_plano(plano):
    segmentos = list(plano.segmentos)
    if len(segmentos) == 1:
        unico = segmentos[0]
        gi = plano.gancho["inicio"]
        segmentos = [{"inicio": unico["inicio"], "fim": gi, "funcao": "contexto"},
                     {"inicio": gi, "fim": unico["fim"], "funcao": "desenvolvimento_e_consequencia"}]
    return {"titulos": [t.texto for t in plano.titulos], "descricao": plano.descricao,
            "gancho": plano.gancho, "segmentos_narrativos": segmentos,
            "justificativa": plano.motivo_escolha, "continuidade": plano.remix_motivo,
            "confianca": plano.confianca}


def analisar_remixes(transcricao: Transcricao, config: ConfigIA, provedor=None,
                     usar_cache=True, cancelada_ref=None, progresso_cb=None):
    from modules.agencia.rollback_2809 import analisar
    if provedor is None:
        # Deferred factory construction keeps cache replay completely offline.
        class Lazy:
            gerencia_orcamento = True
            cliente = None
            def obter(self):
                if self.cliente is None:
                    from modules.ai.factory import obter_provider
                    self.cliente = obter_provider(config,modo='agencia',schema=_RespostaRemixes)
                    from modules.ai.fallback import OrcamentoChamadas
                    self.orcamento = getattr(self.cliente,'orcamento',None) or OrcamentoChamadas(config.max_chamadas_episodio)
                return self.cliente
            @property
            def restantes(self):
                self.obter()
                return self.orcamento.restantes
            def listar_modelos(self):
                return self.obter().listar_modelos()
            def analisar(self,modelo,prompt):
                cliente = self.obter()
                if not getattr(cliente,'gerencia_orcamento',False):
                    self.orcamento.reservar()
                return cliente.analisar(modelo,prompt)
        provedor = Lazy()
    planos = analisar(transcricao,config,provedor,usar_cache,cancelada_ref,progresso_cb,prompt_factory=_prompt_remix)
    candidatos = []
    for p in planos:
        dados = _remix_bruto_de_plano(p)
        candidatos.append(CandidatoRemix([t.texto for t in p.titulos],p.descricao,p.gancho['inicio'],p.gancho['fim'],
            p.gancho.get('motivo',''),[SegmentoNarrativo(float(s['inicio']),float(s['fim']),s.get('funcao','historia'))
                                     for s in dados['segmentos_narrativos']],
            p.motivo_escolha,p.remix_motivo,p.confianca,remix_rules_version=p.remix_rules_version,
            validacao_remix=p.validacao_remix,story_identity=p.story_identity,plano_agencia=p.como_dict()))
    return ListaRemixes(candidatos,planos.diagnostico)


def _analisar_remixes_antes_rollback(transcricao: Transcricao, config: ConfigIA, provedor=None,
                                  usar_cache=True, cancelada_ref=None, progresso_cb=None):
    from modules.agencia.recuperacao import DiagnosticoRemix, buscar_remixes
    from modules.agencia.remix import REMIX_RULES_VERSION
    config.validar()
    if not transcricao.segmentos:
        raise ValueError("A transcricao nao contem falas.")
    if Path(transcricao.video_path).exists() and not transcricao_corresponde_origem(transcricao):
        raise ValueError("A transcricao nao corresponde ao video atual.")
    cache = caminho_cache_remix(transcricao, config)
    dados = None
    if usar_cache and cache.exists():
        try:
            dados = json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    if dados and (dados.get("video_id") != transcricao.video_id or
                  dados.get("hash_transcricao") != _hash_transcricao(transcricao)):
        dados = None
    if dados:
        candidatos, _ = validar_remixes(dados.get("remixes_brutos", []), transcricao, config.max_cortes)
        if candidatos or "diagnostico" in dados:
            diag = DiagnosticoRemix(**{k: v for k, v in dados.get("diagnostico", {}).items()
                                     if k in DiagnosticoRemix.__dataclass_fields__})
            diag.finalizar(candidatos)
            if progresso_cb:
                progresso_cb(100, "Cache revalidado. " + diag.resumo())
            return ListaRemixes(candidatos, diag)
    lista = buscar_remixes(transcricao, config, provedor or criar_provedor_remix(),
                           prompt_factory=_prompt_remix, cancelada_ref=cancelada_ref,
                           progresso_cb=progresso_cb,
                           sementes=dados.get("remixes_brutos", []) if dados else (),
                           validacao_extra=lambda p: validar_remixes(
                               [_remix_bruto_de_plano(p)], transcricao, 1)[1])
    brutos = [_remix_bruto_de_plano(p) for p in lista]
    candidatos, rejeicoes = validar_remixes(brutos, transcricao, config.max_cortes)
    lista.diagnostico.motivos_rejeicao.extend(rejeicoes)
    lista.diagnostico.finalizar(candidatos)
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"versao": VERSAO_REGRAS_REMIX, "remix_rules_version": REMIX_RULES_VERSION,
                              "video_id": transcricao.video_id, "hash_transcricao": _hash_transcricao(transcricao),
                              "modelo": config.modelo, "remixes_brutos": brutos,
                              "candidatos_tentados": lista.brutos, "diagnostico": asdict(lista.diagnostico)},
                             ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(cache)
    return ListaRemixes(candidatos, lista.diagnostico)
