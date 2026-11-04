"""Análise contextual de transcrições com Gemini, cache e validação local."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Callable


from modules.ai.cache_fallback import carregar_grupos, salvar_grupos
from modules.ai.base import AIProvider
from modules.ai.factory import obter_provider
from modules.ai.chat_json import selecionar_modelo_disponivel
from modules.ai.gemini import GeminiProvider, ErroGemini, _erro_seguro, _RespostaGemini
from modules.config_ia import ConfigIA, carregar_config_ia
from modules.credenciais import obter_chave_gemini
from modules.intro import carregar_intro
from modules.preset import DADOS_DIR
from modules.transcricao import SegmentoTranscricao, Transcricao, transcricao_corresponde_origem


ANALISES_DIR = DADOS_DIR / "analises_ia"
DIAGNOSTICOS_DIR = DADOS_DIR / "diagnosticos_ia"
VERSAO_PROMPT = "ia-toons-episodio-completo-v3"
MAX_CANDIDATOS_EPISODIO = 10
MAX_CARACTERES_CONTEXTO_UNICO = 120_000
VERSAO_VALIDACAO = 3


@dataclass
class CandidatoIA:
    inicio: float
    fim: float
    descricao: str
    titulos: list[str]
    motivo: str = ""
    inicio_original: float | None = None
    gancho: str = ""
    excepcional_curto: bool = False
    plano_remix: dict | None = None
    remix_rules_version: str = ""

    @property
    def duracao(self) -> float:
        return self.fim - self.inicio


@dataclass
class DiagnosticoAnalise:
    solicitada: int
    retornada_ia: int = 0
    rejeitada: int = 0
    motivos_rejeicao: list[str] = None
    final_exibida: int = 0
    janelas_total: int = 0
    janelas_analisadas: int = 0
    origem_cache: bool = False
    reutilizada_cache_anterior: int = 0
    duracao_episodio: float = 0.0
    segmentos_total: int = 0
    cobertura_inicio: float = 0.0
    cobertura_fim: float = 0.0
    janelas_detalhes: list[dict] = None
    candidatos_detalhes: list[dict] = None
    janelas_falhas: list[dict] = None

    def __post_init__(self):
        if self.motivos_rejeicao is None:
            self.motivos_rejeicao = []
        if self.janelas_detalhes is None:
            self.janelas_detalhes = []
        if self.candidatos_detalhes is None:
            self.candidatos_detalhes = []
        if self.janelas_falhas is None:
            self.janelas_falhas = []


class ListaCandidatos(list):
    def __init__(self, itens=(), diagnostico: DiagnosticoAnalise | None = None):
        super().__init__(itens)
        self.diagnostico = diagnostico


def _salvar_diagnostico(video_id: str, diagnostico: DiagnosticoAnalise) -> None:
    """Registra apenas contagens e motivos técnicos; nunca inclui chave ou transcrição."""
    try:
        DIAGNOSTICOS_DIR.mkdir(parents=True, exist_ok=True)
        destino = DIAGNOSTICOS_DIR / f"{video_id}.json"
        tmp = destino.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(diagnostico), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(destino)
    except OSError:
        pass


# Nomes legados mantidos durante a migra??o.
ProvedorAnalise = AIProvider
ProvedorGemini = GeminiProvider


def criar_provedor_configurado(config: ConfigIA | None = None) -> AIProvider:
    config = config if config is not None else carregar_config_ia()
    if config.fallback_automatico:
        return obter_provider(config)
    if config.provider_ativo == "gemini":
        return obter_provider(config, chave=obter_chave_gemini())
    return obter_provider(config)


def _janelas(transcricao: Transcricao, config: ConfigIA) -> list[list[SegmentoTranscricao]]:
    segmentos = [s for s in transcricao.segmentos if s.fim > s.inicio and s.texto.strip()]
    if not segmentos:
        return []
    # Episódios comuns cabem confortavelmente no contexto do modelo. Enviá-los juntos
    # permite comparar cenas do episódio inteiro usando uma única chamada.
    tamanho_texto = sum(len(s.texto) + 32 for s in segmentos)
    if tamanho_texto <= MAX_CARACTERES_CONTEXTO_UNICO:
        return [segmentos]
    janelas, inicio_idx = [], 0
    while inicio_idx < len(segmentos):
        inicio = segmentos[inicio_idx].inicio
        fim_limite = inicio + config.janela_segundos
        fim_idx = inicio_idx
        while fim_idx + 1 < len(segmentos) and segmentos[fim_idx + 1].fim <= fim_limite:
            fim_idx += 1
        janela = segmentos[inicio_idx:fim_idx + 1]
        janelas.append(janela)
        if fim_idx >= len(segmentos) - 1:
            break
        reinicio = max(inicio, janela[-1].fim - config.sobreposicao_segundos)
        proximo = fim_idx + 1
        for i in range(inicio_idx + 1, fim_idx + 1):
            if segmentos[i].inicio >= reinicio:
                proximo = i
                break
        inicio_idx = max(inicio_idx + 1, proximo)
    return janelas


def _prompt(janela: list[SegmentoTranscricao], max_cortes: int) -> str:
    linhas = "\n".join(f"[{s.inicio:.3f} --> {s.fim:.3f}] {s.texto.strip()}" for s in janela)
    return f"""Você analisa uma transcrição de episódio para sugerir cortes verticais.
Os timestamps são ABSOLUTOS. Faça uma varredura do episódio inteiro e sugira no máximo {max_cortes} cenas realmente fortes.
Não pare após encontrar apenas os dois ou três melhores momentos: continue
avaliando o restante do episódio e tente entregar entre 6 e {max_cortes} cenas quando existirem
candidatos adequados, sem inventar ou preencher a cota com cenas ruins.
Procure humor, situações absurdas, reviravoltas e diálogos marcantes. Cada corte deve durar de 60 a
120 segundos e ter início, desenvolvimento e conclusão. Use limites de fala; não interrompa frase,
piada ou desfecho. Não invente conteúdo. Para cada cena, gere exatamente três títulos curtos,
engraçados, diferentes e fiéis ao conteúdo, sem simplesmente copiar as primeiras palavras.
Depois de identificar cada candidato, preserve o início inicialmente encontrado em inicio_original e
compare pontos de entrada nos 8 segundos seguintes. Em inicio, escolha o melhor limite de fala para
que os primeiros 1 a 3 segundos tragam uma fala inesperada, pergunta, conflito, absurdo ou reação,
sem cortar palavra/frase nem remover contexto indispensável. Preserve o início original quando ele já
for forte. Explique brevemente em gancho por que a abertura funciona. Os três títulos devem complementar
a abertura, sem repetir a primeira fala nem revelar o desfecho. Confirme que o corte mantém continuidade
e conclusão. Excepcionalmente aceite menos de 60 segundos somente para preservar uma situação completa;
nesse caso marque excepcional_curto como true. Nunca acrescente cena sem relação para completar duração.
Retorne somente a estrutura JSON solicitada.

TRANSCRIÇÃO:
{linhas}"""


def _detalhes_janelas(janelas: list[list[SegmentoTranscricao]], contagens=None) -> list[dict]:
    contagens = contagens or []
    return [
        {
            "janela": indice,
            "inicio": janela[0].inicio,
            "fim": janela[-1].fim,
            "duracao": janela[-1].fim - janela[0].inicio,
            "segmentos": len(janela),
            "candidatos_recebidos": contagens[indice - 1] if indice <= len(contagens) else None,
        }
        for indice, janela in enumerate(janelas, 1) if janela
    ]


def _detalhes_candidatos(dados: list[dict], rejeicoes: list[str]) -> list[dict]:
    detalhes = []
    for indice, item in enumerate(dados, 1):
        motivo = next((r for r in rejeicoes if r.startswith(f"Candidato {indice}:")), "aceito")
        try:
            inicio, fim = float(item.get("inicio")), float(item.get("fim"))
            duracao = fim - inicio
        except (TypeError, ValueError):
            inicio = fim = duracao = None
        detalhes.append({
            "candidato": indice, "inicio_recebido": inicio, "fim_recebido": fim,
            "duracao_recebida": duracao,
            "resultado": "rejeitado" if motivo != "aceito" else "aceito",
            "motivo": motivo,
        })
    return detalhes


def _alinhar(valor: float, segmentos: list[SegmentoTranscricao], inicio: bool) -> float:
    limites = [s.inicio if inicio else s.fim for s in segmentos]
    return min(limites, key=lambda x: abs(x - valor))


def _expandir_nucleo_curto(
    inicio: float, fim: float, inicio_original: float,
    segmentos: list[SegmentoTranscricao], duracao_video: float,
) -> tuple[float, float]:
    """Completa o contexto em torno de um núcleo válido sem cortar falas.

    A Gemini às vezes devolve somente o trecho central da piada, apesar de o
    prompt pedir a cena completa. O reparo usa exclusivamente limites reais da
    transcrição e mantém integralmente o núcleo indicado.
    """
    if fim - inicio >= 45:
        return inicio, fim
    alvo = 60.0
    base_inicio = min(inicio, inicio_original)
    faltante = max(0.0, alvo - (fim - base_inicio))
    # Um terço antes fornece contexto; dois terços depois preservam o desfecho.
    desejado_inicio = max(0.0, base_inicio - faltante / 3.0)
    desejado_fim = min(duracao_video, fim + faltante * 2.0 / 3.0)
    if desejado_fim - desejado_inicio < alvo:
        desejado_inicio = max(0.0, desejado_fim - alvo)
    inicios = [s.inicio for s in segmentos if s.inicio <= desejado_inicio + 0.001]
    finais = [s.fim for s in segmentos if s.fim >= desejado_fim - 0.001]
    novo_inicio = max(inicios) if inicios else segmentos[0].inicio
    novo_fim = min(finais) if finais else segmentos[-1].fim
    if novo_fim - novo_inicio < alvo:
        finais_alvo = [s.fim for s in segmentos if s.fim >= novo_inicio + alvo - 0.001]
        if finais_alvo:
            novo_fim = min(finais_alvo)
        else:
            inicios_alvo = [s.inicio for s in segmentos if s.inicio <= novo_fim - alvo + 0.001]
            if inicios_alvo:
                novo_inicio = max(inicios_alvo)
    incluidos = [s for s in segmentos if s.fim > novo_inicio and s.inicio < novo_fim]
    # Uma pausa longa é uma fronteira provável de história/cena. Nesse caso a
    # expansão não é considerada segura e o núcleo original será validado como tal.
    if any(b.inicio - a.fim > 8.0 for a, b in zip(incluidos, incluidos[1:])):
        return inicio, fim
    return novo_inicio, novo_fim


def _excluir_intro_do_intervalo(inicio: float, fim: float, intro) -> tuple[float, float] | None:
    """Remove somente a sobreposição real com a intro, preservando o lado narrativo."""
    if not intro or not intro.ativo or fim <= intro.inicio or inicio >= intro.fim:
        return inicio, fim
    partes = []
    if inicio < intro.inicio:
        partes.append((inicio, intro.inicio))
    if fim > intro.fim:
        partes.append((intro.fim, fim))
    if not partes:
        return None
    return max(partes, key=lambda trecho: trecho[1] - trecho[0])


def _sobreposicao_excessiva(candidato: CandidatoIA, existentes: list[CandidatoIA]) -> bool:
    """Deduplicação é uma etapa independente e nunca altera o candidato cronológico."""
    return any(
        max(0.0, min(candidato.fim, atual.fim) - max(candidato.inicio, atual.inicio)) /
        min(candidato.duracao, atual.duracao) > 0.5
        for atual in existentes
    )


def _selecionar_finais(candidatos: list[CandidatoIA], max_cortes: int) -> list[CandidatoIA]:
    """O máximo é somente limite superior; uma lista menor continua válida."""
    return sorted(candidatos, key=lambda candidato: candidato.inicio)[:max_cortes]


def _normalizar_timestamps_da_janela(dados: list[dict], janela: list[SegmentoTranscricao]) -> list[dict]:
    """Converte respostas relativas somente quando elas não cabem no intervalo absoluto."""
    if not janela:
        return dados
    deslocamento = janela[0].inicio
    duracao = janela[-1].fim - deslocamento
    normalizados = []
    for item in dados:
        copia = dict(item)
        try:
            inicio, fim = float(copia["inicio"]), float(copia["fim"])
            if deslocamento > 0 and fim < deslocamento - 0.001 and 0 <= inicio < fim <= duracao + 1:
                copia["inicio"] = inicio + deslocamento
                copia["fim"] = fim + deslocamento
                if copia.get("inicio_original") is not None:
                    copia["inicio_original"] = float(copia["inicio_original"]) + deslocamento
        except (KeyError, TypeError, ValueError):
            pass
        normalizados.append(copia)
    return normalizados


def otimizar_gancho_local(candidato: CandidatoIA, transcricao: Transcricao) -> CandidatoIA:
    """Enriquece caches antigos sem IA; nunca corta uma fala nem inventa análise semântica."""
    if candidato.inicio_original is None:
        candidato.inicio_original = candidato.inicio
    if candidato.gancho:
        return candidato
    relevantes = [s for s in transcricao.segmentos
                  if s.fim > candidato.inicio and s.inicio < min(candidato.fim, candidato.inicio + 8)]
    if not relevantes:
        candidato.gancho = "Início original preservado; não há outra fala próxima para comparação."
        return candidato
    primeiro = relevantes[0]
    # Se o candidato caiu dentro de uma fala, recua ao começo para preservar a frase.
    if primeiro.inicio < candidato.inicio < primeiro.fim:
        candidato.inicio = primeiro.inicio
        candidato.gancho = "Entrada recuada ao início da fala para não cortar palavra ou contexto."
    else:
        espera = max(0.0, primeiro.inicio - candidato.inicio)
        if espera > 1.0 and primeiro.inicio - candidato.inicio_original <= 3.0:
            candidato.inicio = primeiro.inicio
            candidato.gancho = "Entrada avançada até a primeira fala, removendo silêncio inicial."
        else:
            trecho = " ".join(primeiro.texto.strip().split())
            if len(trecho) > 72:
                trecho = trecho[:69].rstrip() + "…"
            candidato.gancho = f"Início original preservado: abre diretamente com “{trecho}”."
    return candidato


def _validar_contextuais_legado(
    dados: list[dict], transcricao: Transcricao, max_cortes: int
) -> tuple[list[CandidatoIA], list[str]]:
    resultado: list[CandidatoIA] = []
    rejeicoes: list[str] = []
    segmentos = [s for s in transcricao.segmentos if s.fim > s.inicio]
    intro = carregar_intro(transcricao)
    for indice, item in enumerate(dados, 1):
        try:
            inicio_bruto, fim_bruto = float(item["inicio"]), float(item["fim"])
            inicio_original_bruto = float(item.get("inicio_original", inicio_bruto))
            # Alguns modelos ocasionalmente devolvem milissegundos apesar do prompt em segundos.
            # A conversão só ocorre quando a escala original é impossível e /1000 cabe no vídeo.
            if (fim_bruto > transcricao.duracao * 2 and
                    fim_bruto / 1000 <= transcricao.duracao + 0.001):
                inicio_bruto /= 1000
                fim_bruto /= 1000
                inicio_original_bruto /= 1000
            if not math.isfinite(inicio_bruto + fim_bruto):
                rejeicoes.append(f"Candidato {indice}: timestamps não finitos.")
                continue
            if fim_bruto <= 0 or inicio_bruto >= transcricao.duracao:
                rejeicoes.append(f"Candidato {indice}: intervalo inteiramente fora do episódio.")
                continue
            tolerancia = 10.0
            if inicio_bruto < -tolerancia or fim_bruto > transcricao.duracao + tolerancia:
                rejeicoes.append(
                    f"Candidato {indice}: timestamps excedem os limites seguros do episódio."
                )
                continue
            inicio_bruto = max(0.0, inicio_bruto)
            fim_bruto = min(transcricao.duracao, fim_bruto)
            inicio_original_bruto = min(transcricao.duracao, max(0.0, inicio_original_bruto))
            # A otimização do gancho só pode avançar alguns segundos, nunca saltar uma cena.
            if inicio_bruto < inicio_original_bruto - 0.001 or inicio_bruto - inicio_original_bruto > 8.0:
                # Um gancho inválido não invalida a cena inteira: restaura o
                # início cronológico seguro e preserva o candidato.
                inicio_bruto = inicio_original_bruto
            inicio = _alinhar(inicio_bruto, segmentos, True)
            fim = _alinhar(fim_bruto, segmentos, False)
            inicio_original = _alinhar(inicio_original_bruto, segmentos, True)
            duracao_nucleo = fim - inicio
            inicio, fim = _expandir_nucleo_curto(
                inicio, fim, inicio_original, segmentos, transcricao.duracao
            )
            sem_intro = _excluir_intro_do_intervalo(inicio, fim, intro)
            if sem_intro is None:
                rejeicoes.append(f"Candidato {indice}: intervalo contido integralmente na intro.")
                continue
            inicio, fim = sem_intro
            if 120 < fim - inicio <= 180:
                # Mantém o desfecho e desloca o começo para um limite de fala seguro.
                alvo_inicio = fim - 120
                opcoes_inicio = [s.inicio for s in segmentos if alvo_inicio <= s.inicio < fim]
                if opcoes_inicio:
                    inicio = min(opcoes_inicio)
            duracao_original = fim - inicio
            # Se uma fronteira forte impediu a expansão, é mais seguro manter o
            # núcleo completo selecionado pela IA do que anexar outra história.
            nucleo_curto_seguro = 15 <= duracao_nucleo < 45 and duracao_original < 45
            excepcional = (bool(item.get("excepcional_curto", False)) or
                           45 <= duracao_original < 60 or nucleo_curto_seguro)
            # A própria IA já classificou o intervalo como cena completa. Exceções de 45–60 s
            # são preservadas, em vez de serem descartadas apenas por não atingir 60 s.
            duracao_minima = 15 if nucleo_curto_seguro else 45
            if inicio < 0 or fim > transcricao.duracao + 0.001 or not duracao_minima <= fim - inicio <= 120:
                rejeicoes.append(
                    f"Candidato {indice}: duração {fim - inicio:.1f}s fora do intervalo permitido "
                    f"({duracao_minima}–120s)."
                )
                continue
            titulos = []
            for titulo in item.get("titulos", []):
                limpo = " ".join(str(titulo).split()).strip(" .")
                if limpo and limpo.casefold() not in {t.casefold() for t in titulos}:
                    titulos.append(limpo.upper())
            if len(titulos) != 3:
                rejeicoes.append(f"Candidato {indice}: não apresentou três títulos distintos.")
                continue
            candidato = CandidatoIA(
                inicio, fim, str(item.get("descricao", "")).strip(), titulos,
                str(item.get("motivo", "")).strip(),
                inicio_original=inicio_original,
                gancho=str(item.get("gancho", "")).strip(), excepcional_curto=excepcional,
            )
            otimizar_gancho_local(candidato, transcricao)
            if not _sobreposicao_excessiva(candidato, resultado):
                resultado.append(candidato)
            else:
                rejeicoes.append(f"Candidato {indice}: sobreposição superior a 50% com outro candidato.")
        except (KeyError, TypeError, ValueError):
            rejeicoes.append(f"Candidato {indice}: estrutura ou timestamps inválidos.")
            continue
    return _selecionar_finais(resultado, max_cortes), rejeicoes


def validar_candidatos_com_diagnostico(dados, transcricao, max_cortes):
    from modules.agencia.editorial import validar_planos
    from modules.agencia.recuperacao import adaptar_item
    brutos = [adaptar_item(i.get("plano_remix") or i) if isinstance(i, dict) else None for i in dados]
    planos, rejeicoes = validar_planos([i for i in brutos if i is not None], transcricao, max_cortes)
    return [candidato_de_plano(p) for p in planos], rejeicoes


def validar_candidatos(dados: list[dict], transcricao: Transcricao, max_cortes: int) -> list[CandidatoIA]:
    return validar_candidatos_com_diagnostico(dados, transcricao, max_cortes)[0]


def candidato_de_plano(plano) -> CandidatoIA:
    return CandidatoIA(plano.inicio, plano.fim, plano.descricao,
                       [t.texto for t in plano.titulos], plano.motivo_escolha,
                       inicio_original=plano.inicio, gancho=plano.gancho.get("motivo", ""),
                       plano_remix=plano.como_dict(), remix_rules_version=plano.remix_rules_version)


def analisar_transcricao(transcricao: Transcricao, config: ConfigIA, provedor=None,
                        usar_cache=True, cancelada_ref=None, progresso_cb=None):
    """Analise comum automatica usa o mesmo planejamento/contrato da Agencia."""
    from modules.agencia.editorial import analisar_agencia
    planos = analisar_agencia(transcricao, config, provedor, usar_cache, cancelada_ref, progresso_cb)
    d = planos.diagnostico
    diag = DiagnosticoAnalise(solicitada=config.max_cortes, retornada_ia=d.candidatos_recebidos,
                             rejeitada=d.candidatos_rejeitados, motivos_rejeicao=d.motivos_rejeicao,
                             final_exibida=len(planos), janelas_total=len(d.partes_analisadas),
                             janelas_analisadas=len(d.partes_analisadas),
                             duracao_episodio=transcricao.duracao, segmentos_total=len(transcricao.segmentos),
                             janelas_detalhes=d.partes_analisadas)
    diag.remix = d
    return ListaCandidatos([candidato_de_plano(p) for p in planos], diag)


def _cache_path(transcricao: Transcricao, config: ConfigIA) -> Path:
    # max_cortes é apenas limite de exibição: alterá-lo não invalida a análise do episódio.
    base = (f"{transcricao.video_id}|{config.modelo}|{config.max_chamadas_episodio}|"
            f"{config.janela_segundos}|{config.sobreposicao_segundos}|{VERSAO_PROMPT}")
    if config.provider_ativo != "gemini":
        base = f"{config.provider_ativo}|{base}"
    return ANALISES_DIR / f"{hashlib.sha256(base.encode()).hexdigest()}.json"


def _cache_path_legado(transcricao: Transcricao, config: ConfigIA, max_cortes: int) -> Path:
    base = f"{transcricao.video_id}|{config.modelo}|{config.max_chamadas_episodio}|{max_cortes}|{VERSAO_PROMPT}"
    if config.provider_ativo != "gemini":
        base = f"{config.provider_ativo}|{base}"
    return ANALISES_DIR / f"{hashlib.sha256(base.encode()).hexdigest()}.json"


def _caches_historicos(transcricao: Transcricao, config: ConfigIA) -> list[Path]:
    if config.provider_ativo != "gemini":
        return []
    caminhos = []
    for versao in ("ia-toons-contexto-v1", "ia-toons-episodio-completo-v2"):
        for chamadas in range(1, 21):
            base_canonica = (f"{transcricao.video_id}|{config.modelo}|{chamadas}|"
                             f"{config.janela_segundos}|{config.sobreposicao_segundos}|{versao}")
            caminhos.append(ANALISES_DIR / f"{hashlib.sha256(base_canonica.encode()).hexdigest()}.json")
            for quantidade in range(1, 51):
                base_legada = (f"{transcricao.video_id}|{config.modelo}|{chamadas}|"
                               f"{quantidade}|{versao}")
                caminhos.append(ANALISES_DIR / f"{hashlib.sha256(base_legada.encode()).hexdigest()}.json")
    return caminhos


def _acrescentar_historicos(candidatos: list[CandidatoIA], transcricao: Transcricao,
                            config: ConfigIA) -> int:
    adicionados = 0
    for caminho in _caches_historicos(transcricao, config):
        if not caminho.exists():
            continue
        try:
            dados = json.loads(caminho.read_text(encoding="utf-8"))
            if dados.get("video_id") not in (None, transcricao.video_id):
                continue
            antigos = [otimizar_gancho_local(CandidatoIA(**item), transcricao)
                       for item in dados.get("candidatos", [])]
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        for antigo in antigos:
            if antigo.inicio < 0 or antigo.fim > transcricao.duracao or antigo.duracao < 45:
                continue
            duplicado = any(
                max(0.0, min(antigo.fim, atual.fim) - max(antigo.inicio, atual.inicio)) /
                min(antigo.duracao, atual.duracao) > 0.5 for atual in candidatos
            )
            if not duplicado:
                candidatos.append(antigo)
                adicionados += 1
    candidatos.sort(key=lambda item: item.inicio)
    return adicionados


def _carregar_melhor_cache(transcricao: Transcricao, config: ConfigIA) -> tuple[dict, Path] | None:
    canonico = _cache_path(transcricao, config)
    grupos = carregar_grupos(canonico, config, "candidatos_brutos")
    if grupos is not None:
        return grupos, canonico
    caminhos = [canonico]
    # Migração transparente: caches antigos variavam indevidamente com o máximo solicitado.
    caminhos.extend(_cache_path_legado(transcricao, config, n) for n in range(1, 51))
    encontrados = []
    for caminho in caminhos:
        if not caminho.exists():
            continue
        try:
            dados = json.loads(caminho.read_text(encoding="utf-8"))
            if dados.get("video_id") not in (None, transcricao.video_id):
                continue
            if isinstance(dados.get("candidatos"), list):
                encontrados.append((len(dados["candidatos"]), caminho.stat().st_mtime_ns, dados, caminho))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    if not encontrados:
        return None
    _quantidade, _data, dados, caminho = max(encontrados, key=lambda item: (item[0], item[1]))
    return dados, caminho


def _analisar_transcricao_legado(
    transcricao: Transcricao, config: ConfigIA, provedor: ProvedorAnalise | None = None,
    usar_cache: bool = True, cancelada_ref=None,
    progresso_cb: Callable[[float, str], None] | None = None,
) -> list[CandidatoIA]:
    config.validar()
    if Path(transcricao.video_path).exists() and not transcricao_corresponde_origem(transcricao):
        raise ValueError(
            "A transcrição não corresponde mais ao arquivo de origem atual. Transcreva novamente."
        )
    if transcricao.duracao < 60 or not transcricao.segmentos:
        raise ValueError(
            "A transcrição atual tem menos de 60 segundos. Importe/transcreva um trecho de pelo menos "
            "60 segundos para usar a análise inteligente; cortes manuais continuam disponíveis."
        )
    cache = _cache_path(transcricao, config)
    cache_encontrado = _carregar_melhor_cache(transcricao, config) if usar_cache else None
    if cache_encontrado:
        try:
            dados, origem = cache_encontrado
            brutos_cache = dados.get("candidatos_brutos")
            rejeicoes_cache = None
            if isinstance(brutos_cache, list):
                todos, rejeicoes_cache = _validar_contextuais_legado(
                    brutos_cache, transcricao, 50)
            else:
                todos = [otimizar_gancho_local(CandidatoIA(**item), transcricao)
                         for item in dados.get("candidatos", [])]
            reutilizados = _acrescentar_historicos(todos, transcricao, config)
            candidatos = _selecionar_finais(todos, config.max_cortes)
            diag_salvo = dados.get("diagnostico", {})
            total_janelas_cache = len(_janelas(transcricao, config))
            diagnostico = DiagnosticoAnalise(
                solicitada=config.max_cortes,
                retornada_ia=len(brutos_cache) if isinstance(brutos_cache, list)
                else int(diag_salvo.get("retornada_ia", len(todos))),
                rejeitada=len(rejeicoes_cache) if rejeicoes_cache is not None
                else int(diag_salvo.get("rejeitada", 0)),
                motivos_rejeicao=rejeicoes_cache if rejeicoes_cache is not None
                else list(diag_salvo.get("motivos_rejeicao", [])),
                final_exibida=len(candidatos),
                janelas_total=int(diag_salvo.get("janelas_total") or total_janelas_cache),
                janelas_analisadas=int(diag_salvo.get("janelas_analisadas") or
                                       min(config.max_chamadas_episodio, total_janelas_cache)),
                origem_cache=True,
                reutilizada_cache_anterior=reutilizados,
                duracao_episodio=transcricao.duracao,
                segmentos_total=len(transcricao.segmentos),
                cobertura_inicio=transcricao.segmentos[0].inicio,
                cobertura_fim=transcricao.segmentos[-1].fim,
                janelas_detalhes=list(diag_salvo.get("janelas_detalhes") or
                                      _detalhes_janelas(_janelas(transcricao, config),
                                                       [len(brutos_cache)] if isinstance(brutos_cache, list) else [])),
                candidatos_detalhes=_detalhes_candidatos(brutos_cache, rejeicoes_cache)
                if isinstance(brutos_cache, list) else list(diag_salvo.get("candidatos_detalhes", [])),
                janelas_falhas=list(diag_salvo.get("janelas_falhas", [])),
            )
            # Consolida o melhor cache legado, sem nova chamada.
            if not dados.get("indice_fallback") and (origem != cache or dados.get("video_id") != transcricao.video_id or
                    dados.get("versao_validacao") != VERSAO_VALIDACAO or
                    dados.get("candidatos") != [asdict(c) for c in todos]):
                ANALISES_DIR.mkdir(parents=True, exist_ok=True)
                consolidado = dict(dados)
                consolidado["video_id"] = transcricao.video_id
                consolidado["candidatos"] = [asdict(c) for c in todos]
                consolidado["diagnostico"] = asdict(diagnostico)
                consolidado["versao_validacao"] = VERSAO_VALIDACAO
                cache.write_text(json.dumps(consolidado, ensure_ascii=False, indent=2), encoding="utf-8")
            if progresso_cb:
                progresso_cb(100, f"Cache reutilizado: {len(candidatos)} candidatos adequados.")
            _salvar_diagnostico(transcricao.video_id, diagnostico)
            return ListaCandidatos(candidatos, diagnostico)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
    provedor = provedor or criar_provedor_configurado(config)
    if progresso_cb:
        progresso_cb(2, "Verificando modelos disponíveis…")
    modelos = provedor.listar_modelos()
    modelo = selecionar_modelo_disponivel(provedor, config.modelo, modelos)
    todas_janelas = _janelas(transcricao, config)
    janelas = todas_janelas[:config.max_chamadas_episodio]
    brutos: list[dict] = []
    respostas_provider = []
    contagens_janelas: list[int] = []
    falhas_janelas: list[dict] = []
    for indice, janela in enumerate(janelas, 1):
        if getattr(provedor, "gerencia_orcamento", False) and provedor.restantes == 0:
            break
        if cancelada_ref and cancelada_ref.cancelada:
            raise RuntimeError("Análise cancelada.")
        if progresso_cb:
            progresso_cb(5 + (indice - 1) / max(1, len(janelas)) * 85,
                          f"Analisando contexto {indice}/{len(janelas)}…")
        # O máximo solicitado não é meta obrigatória nem muda a análise da janela.
        try:
            resposta = provedor.analisar(modelo, _prompt(janela, MAX_CANDIDATOS_EPISODIO))
        except ErroGemini as erro:
            if getattr(erro, "interromper_analise", False):
                raise
            falhas_janelas.append({"janela": indice, "erro": str(erro)})
            contagens_janelas.append(0)
            continue
        if not isinstance(resposta, dict) or not isinstance(resposta.get("candidatos"), list):
            falhas_janelas.append({
                "janela": indice,
                "erro": "A resposta da análise é inválida; nenhum cache vazio foi criado.",
            })
            contagens_janelas.append(0)
            continue
        candidatos_janela = _normalizar_timestamps_da_janela(
            resposta.get("candidatos", []), janela
        )
        contagens_janelas.append(len(candidatos_janela))
        brutos.extend(candidatos_janela)
        if getattr(provedor, "gerencia_orcamento", False):
            respostas_provider.append((resposta, candidatos_janela))
    if falhas_janelas and len(falhas_janelas) == len(janelas):
        raise ErroGemini(f"Nenhuma janela foi analisada: {falhas_janelas[0]['erro']}")
    todos_validos, rejeicoes = _validar_contextuais_legado(brutos, transcricao, 50)
    candidatos = _selecionar_finais(todos_validos, config.max_cortes)
    diagnostico = DiagnosticoAnalise(
        solicitada=config.max_cortes, retornada_ia=len(brutos), rejeitada=len(rejeicoes),
        motivos_rejeicao=rejeicoes, final_exibida=len(candidatos),
        janelas_total=len(todas_janelas), janelas_analisadas=len(contagens_janelas), origem_cache=False,
        duracao_episodio=transcricao.duracao,
        segmentos_total=len(transcricao.segmentos),
        cobertura_inicio=transcricao.segmentos[0].inicio,
        cobertura_fim=transcricao.segmentos[-1].fim,
        janelas_detalhes=_detalhes_janelas(janelas, contagens_janelas),
        candidatos_detalhes=_detalhes_candidatos(brutos, rejeicoes),
        janelas_falhas=falhas_janelas,
    )
    ANALISES_DIR.mkdir(parents=True, exist_ok=True)
    if getattr(provedor, "gerencia_orcamento", False):
        salvar_grupos(cache, config, respostas_provider, "candidatos_brutos", {
            "versao": VERSAO_PROMPT, "video_id": transcricao.video_id,
            "versao_validacao": VERSAO_VALIDACAO, "diagnostico": asdict(diagnostico),
        }, lambda real: _cache_path(transcricao, real),
            lambda itens: [asdict(c) for c in validar_candidatos_com_diagnostico(itens, transcricao, 50)[0]])
    else:
        temporario = cache.with_suffix(".json.tmp")
        temporario.write_text(json.dumps({"versao": VERSAO_PROMPT, "modelo": config.modelo,
                                          "video_id": transcricao.video_id,
                                          "versao_validacao": VERSAO_VALIDACAO,
                                          "candidatos": [asdict(c) for c in todos_validos],
                                          "candidatos_brutos": brutos,
                                          "diagnostico": asdict(diagnostico)},
                                         ensure_ascii=False, indent=2), encoding="utf-8")
        temporario.replace(cache)
    if progresso_cb:
        progresso_cb(100, f"Análise concluída: {len(candidatos)} cenas selecionadas.")
    _salvar_diagnostico(transcricao.video_id, diagnostico)
    return ListaCandidatos(candidatos, diagnostico)
