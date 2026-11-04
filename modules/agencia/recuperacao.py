"""Busca editorial em camadas, com contrato unico e orcamento compartilhado."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
import json
import math

from modules.ai.fallback import LimiteChamadas
from modules.performance import medir_operacao, anotar


MENSAGEM_ZERO = "Nenhum remix narrativo válido foi encontrado neste episódio."


@dataclass
class DiagnosticoRemix:
    candidatos_recebidos: int = 0
    candidatos_parseados: int = 0
    candidatos_reparados: int = 0
    candidatos_rejeitados: int = 0
    remixes_validos: int = 0
    zero_suspeito: bool = False
    chamadas_feitas: int = 0
    etapas: list[str] = field(default_factory=list)
    partes_analisadas: list[dict] = field(default_factory=list)
    motivos_rejeicao: list[str] = field(default_factory=list)
    principais_motivos: list[tuple[str, int]] = field(default_factory=list)
    falhas: list[str] = field(default_factory=list)
    recuperacao_pendente: bool = False

    def finalizar(self, planos):
        self.remixes_validos = len(planos)
        self.candidatos_rejeitados = max(0, self.candidatos_recebidos - len(planos))
        motivos = [m.split(": ", 1)[-1] for m in self.motivos_rejeicao]
        self.principais_motivos = Counter(motivos).most_common(5)

    def resumo(self):
        partes = ", ".join(f"{p['inicio']:.1f}-{p['fim']:.1f}s ({p['etapa']})"
                           for p in self.partes_analisadas) or "nenhuma nova chamada"
        motivos = "; ".join(m for m, _ in self.principais_motivos) or "IA nao apresentou candidatos"
        titulo = MENSAGEM_ZERO if not self.remixes_validos else f"{self.remixes_validos} remixes válidos."
        return (f"{titulo} Avaliados: {self.candidatos_recebidos}; rejeitados: "
                f"{self.candidatos_rejeitados}; chamadas: {self.chamadas_feitas}. "
                f"Motivos: {motivos}. Partes analisadas: {partes}."
                + (" Falhas durante a busca: " + "; ".join(self.falhas) if self.falhas else "")
                + (" Recuperacao pendente: orcamento esgotado." if self.recuperacao_pendente else ""))


class ProvedorMedido:
    """Conta inclusive erros/reservas; o provider continua dono do seu transporte."""
    def __init__(self, provedor, limite, diagnostico):
        self.provedor, self.limite, self.diagnostico = provedor, limite, diagnostico
        self.gerencia_orcamento = getattr(provedor, "gerencia_orcamento", False)

    @property
    def restantes(self):
        local = max(0, self.limite - self.diagnostico.chamadas_feitas)
        return min(local, self.provedor.restantes) if self.gerencia_orcamento else local

    @medir_operacao("chamada_ia")
    def analisar(self, modelo, prompt):
        anotar(modelo=modelo, provider=type(self.provedor).__name__, entrada_caracteres=len(prompt))
        if not self.restantes:
            raise LimiteChamadas("Orcamento esgotado; nenhuma chamada adicional iniciada.")
        antes = self.provedor.restantes if self.gerencia_orcamento else None
        budget = getattr(self.provedor, "orcamento", None)
        maximo = budget.maximo if budget else None
        if budget:
            budget.maximo = min(maximo, budget.usadas + self.restantes)
        try:
            gerar = (getattr(self.provedor, "analisar_uma_vez", None)
                     if not self.gerencia_orcamento else None)
            return (gerar or self.provedor.analisar)(modelo, prompt)
        finally:
            usadas = max(1, antes - self.provedor.restantes) if antes is not None else 1
            if budget:
                # Mede pela quantidade usada, sem contar a reducao temporaria do teto.
                usadas = max(1, budget.usadas - (maximo - antes))
                budget.maximo = maximo
            self.diagnostico.chamadas_feitas += usadas


def intervalos_tentados(itens):
    resultado = []
    for item in itens:
        if not isinstance(item, dict):
            continue
        segmentos = item.get("segmentos") or item.get("segmentos_narrativos") or [item]
        try:
            resultado.append((min(float(s["inicio"]) for s in segmentos),
                              max(float(s["fim"]) for s in segmentos)))
        except (KeyError, TypeError, ValueError):
            continue
    return resultado


def repetido(intervalo, anteriores):
    i, f = intervalo
    return any(max(0, min(f, af) - max(i, ai)) / max(0.001, min(f-i, af-ai)) > 0.6
               for ai, af in anteriores)


def blocos_narrativos(transcricao):
    """Limites temporais e pausas de fala, nunca fragmentos de texto/caracteres."""
    blocos, atual = [], []
    for segmento in transcricao.segmentos:
        if segmento.fim <= segmento.inicio or not segmento.texto.strip():
            continue
        if atual and (segmento.inicio - atual[-1].fim >= 2 and atual[-1].fim - atual[0].inicio >= 60
                      or segmento.fim - atual[0].inicio > 180):
            blocos.append(atual)
            atual = []
        atual.append(segmento)
    if atual:
        blocos.append(atual)
    return blocos


def adaptar_item(item):
    """Aliases estruturais explicitos, sem inventar gancho ou historia."""
    if not isinstance(item, dict):
        return None
    novo = dict(item)
    if "segmentos" not in novo and isinstance(novo.get("segmentos_narrativos"), list):
        novo["segmentos"] = novo["segmentos_narrativos"]
    if novo.get("remix_recomendado") is False:
        novo["usar_remix"] = False
    novo.setdefault("justificativa_remix", novo.get("continuidade") or novo.get("justificativa", ""))
    return novo


def parseavel(item):
    try:
        gancho = item["gancho"]
        segmentos = item["segmentos"]
        return (isinstance(gancho, dict) and isinstance(segmentos, list) and bool(segmentos) and
                all(math.isfinite(float(s[k])) for s in segmentos + [gancho] for k in ("inicio", "fim")))
    except (KeyError, TypeError, ValueError):
        return False


def buscar_remixes(transcricao, config, provedor, *, prompt_factory=None,
                  cancelada_ref=None, progresso_cb=None, sementes=(), validacao_extra=None,
                  producao=False):
    from modules.agencia.editorial import (
        ListaPlanos, MAX_HISTORIAS, _analisar_com_reserva, _janelas,
        prompt_agencia, selecionar_modelo_disponivel, validar_planos,
    )
    from modules.analise_ia import ErroGemini

    prompt_factory = prompt_factory or prompt_agencia
    diag = DiagnosticoRemix()
    medido = ProvedorMedido(provedor, config.max_chamadas_episodio, diag)
    brutos, planos = list(sementes), []
    respostas_provider = []

    def validar(itens):
        adaptados = [adaptar_item(i) for i in itens]
        diag.candidatos_recebidos += len(itens)
        diag.candidatos_parseados += sum(parseavel(i) for i in adaptados if i is not None)
        diag.motivos_rejeicao.extend("Parser: candidato sem estrutura JSON." for i in adaptados if i is None)
        validos, rejeicoes = validar_planos([i for i in adaptados if i is not None], transcricao, 50)
        diag.motivos_rejeicao.extend(rejeicoes)
        if validacao_extra:
            aprovados = []
            for plano in validos:
                problemas = validacao_extra(plano)
                if problemas:
                    diag.motivos_rejeicao.extend(problemas)
                else:
                    aprovados.append(plano)
            validos = aprovados
        for plano in validos:
            original = next((i for i in adaptados if i and isinstance(i.get("gancho"), dict)
                             and abs(float(i["gancho"].get("inicio", -999)) - plano.gancho["inicio"]) < 2), None)
            if original and (original.get("segmentos_narrativos") or
                             original.get("gancho") != plano.gancho):
                diag.candidatos_reparados += 1
        return validos

    def cancelar():
        if cancelada_ref and cancelada_ref.cancelada:
            raise RuntimeError("Analise de remix cancelada.")

    disponiveis, modelo = [], config.modelo

    def chamada(segmentos, etapa, extras="", novas=False, unica=False, prompt_pronto=None, regioes=()):
        nonlocal planos, modelo, disponiveis
        cancelar()
        if not medido.restantes:
            diag.recuperacao_pendente = not planos
            return
        if not disponiveis:
            disponiveis = provedor.listar_modelos()
            modelo = selecionar_modelo_disponivel(provedor, modelo, disponiveis)
        diag.etapas.append(etapa)
        anteriores = intervalos_tentados(brutos)
        registro = {"inicio": segmentos[0].inicio, "fim": segmentos[-1].fim,
                    "etapa": etapa, "sucesso": False}
        diag.partes_analisadas.append(registro)
        if progresso_cb:
            progresso_cb(15, f"Busca de remixes: {etapa}; chamadas {diag.chamadas_feitas}/{config.max_chamadas_episodio}")
        prompt = prompt_pronto or prompt_factory(segmentos, MAX_HISTORIAS) + extras
        try:
            # Recuperacao global tem exatamente uma tentativa de geracao. O limite
            # temporario tambem impede retries internos do fallback compartilhado.
            budget = getattr(provedor, "orcamento", None)
            maximo = budget.maximo if budget else None
            if unica and budget:
                budget.maximo = min(maximo, budget.usadas + 1)
            try:
                resposta = (medido.analisar(modelo, prompt) if unica else
                            _analisar_com_reserva(medido, modelo, disponiveis, prompt,
                                                 progresso_cb, 15, etapa))
            finally:
                if budget:
                    budget.maximo = maximo
            itens = next((resposta[k] for k in ("historias", "remixes", "candidatos")
                          if isinstance(resposta, dict) and isinstance(resposta.get(k), list)), None)
            if itens is None:
                diag.falhas.append(f"{etapa}: resposta invalida, sem lista de candidatos")
                return
            if regioes:
                cobertura = []
                for p in sorted(regioes,key=lambda p:p.inicio):
                    if cobertura and p.inicio<=cobertura[-1][1]+8:
                        cobertura[-1][1] = max(cobertura[-1][1],p.fim)
                    else:
                        cobertura.append([p.inicio,p.fim])
                permitidos = []
                for item in itens:
                    ivs = intervalos_tentados([item])
                    if ivs and ivs[0][1]-ivs[0][0]<=150 and any(i-.5<=ivs[0][0] and ivs[0][1]<=f+.5 for i,f in cobertura):
                        permitidos.append(item)
                    else:
                        diag.motivos_rejeicao.append('Candidato fora das micro-historias fornecidas.')
                itens = permitidos
            registro["sucesso"] = True
            recebidos = validar(itens)
            brutos.extend(itens)
            if novas:
                novos = []
                for p in recebidos:
                    if repetido((p.inicio, p.fim), anteriores):
                        diag.motivos_rejeicao.append("Recuperacao: intervalo anterior repetido.")
                    elif etapa == "janela_narrativa" and (p.inicio < segmentos[0].inicio - 0.5 or
                                                          p.fim > segmentos[-1].fim + 0.5):
                        diag.motivos_rejeicao.append("Recuperacao: candidato fora da janela analisada.")
                    else:
                        novos.append(p)
                recebidos = novos
            respostas_provider.append((resposta, itens))
            from modules.agencia.editorial import remover_duplicados
            planos, duplicados = remover_duplicados(planos + recebidos)
            diag.motivos_rejeicao.extend(duplicados)
        except LimiteChamadas:
            diag.recuperacao_pendente = not planos
        except ErroGemini as erro:
            if getattr(erro, "interromper_analise", False):
                raise
            diag.falhas.append(f"{etapa}: {erro}")

    def contexto_recuperacao():
        return ("\nRECUPERACAO: busque OUTRAS historias remixaveis, nunca repita candidatos "
                "ou intervalos ja tentados. Nao aceite cortes lineares nem invente contexto/payoff.\n"
                f"Intervalos ja tentados: {json.dumps(intervalos_tentados(brutos))}\n"
                f"Motivos de rejeicao anteriores: {json.dumps(diag.motivos_rejeicao[-20:], ensure_ascii=False)}\n")

    if sementes:
        diag.etapas.append("revalidacao_local_cache")
        planos = validar(brutos)
    elif producao:
        from modules.agencia.auto_recovery import DiagnosticoAutoRecovery, oportunidades, prompt_reparo
        cobertura_local = DiagnosticoAutoRecovery()
        locais = oportunidades(transcricao,cobertura_local,descoberta=True)
        escolhidos = []
        cursor = transcricao.segmentos[0].inicio
        while len(escolhidos)<10:
            restantes = [p for p,_ in locais if p not in escolhidos and p.fim>cursor+.5]
            if not restantes:
                break
            proximos = [p for p in restantes if p.inicio<=cursor+8]
            p = max(proximos,key=lambda p:p.fim) if proximos else min(restantes,key=lambda p:p.inicio)
            escolhidos.append(p)
            cursor = p.fim
        if escolhidos:
            frases = [s for s in transcricao.segmentos if any(p.inicio<=s.inicio and s.fim<=p.fim for p in escolhidos)]
            chamada(frases,"primeira_analise",regioes=escolhidos,
                    prompt_pronto=prompt_reparo([(p,[dict(motivo='descoberta_editorial')]) for p in escolhidos],transcricao))
    else:
        janelas = _janelas(transcricao, config)
        for janela in janelas[:config.max_chamadas_episodio]:
            chamada(janela, "primeira_analise")
    diag.zero_suspeito = diag.candidatos_recebidos > 0 and not planos
    if not producao and not planos:
        diag.etapas.append("revalidacao_local")
        chamada(transcricao.segmentos, "recuperacao_global", contexto_recuperacao(), novas=True, unica=True)
        if not planos and len(_janelas(transcricao, config)) == 1:
            for bloco in blocos_narrativos(transcricao):
                if planos or medido.restantes <= 1:
                    break
                if repetido((bloco[0].inicio, bloco[-1].fim), intervalos_tentados(brutos)):
                    continue
                chamada(bloco, "janela_narrativa", contexto_recuperacao(), novas=True, unica=True)
        if not planos:
            chamada(transcricao.segmentos, "oportunidades", contexto_recuperacao() +
                    "\nBusque conflito curto, reacao forte, absurdo, dialogo setup/payoff ou mini-arco "
                    "autocontido. Mantenha gancho futuro, contexto anterior e consequencia; "
                    "o mesmo contrato e duracao minima continuam obrigatorios.", novas=True, unica=True)
    elif not producao and len(planos) < 5 and medido.restantes:
        from modules.agencia.editorial import prompt_complemento
        complemento = prompt_complemento(transcricao.segmentos, planos, diag.motivos_rejeicao, 5-len(planos))
        adicional = complemento.removeprefix(prompt_agencia(transcricao.segmentos, MAX_HISTORIAS))
        chamada(transcricao.segmentos, "complemento", adicional + contexto_recuperacao(), novas=True, unica=True)
    cancelar()
    alvo = min(10, max(3, config.max_cortes))
    from modules.agencia.editorial import pontuacao_qualidade
    planos = sorted(sorted(planos, key=lambda p: -pontuacao_qualidade(p))[:alvo], key=lambda p: p.inicio)
    diag.finalizar(planos)
    if not producao and not planos and diag.partes_analisadas and not any(p["sucesso"] for p in diag.partes_analisadas):
        if diag.falhas:
            raise ErroGemini("Nenhuma parte foi analisada: " + "; ".join(diag.falhas))
    lista = ListaPlanos(planos, diag.motivos_rejeicao, len(brutos), diagnostico=diag)
    lista.brutos = brutos
    lista.brutos_aprovados = [p.como_dict() for p in planos]
    lista.respostas_provider = respostas_provider
    if producao:
        from modules.agencia.auto_recovery import auto_recuperar
        lista = auto_recuperar(lista, transcricao, config, sementes=brutos,provedor=medido,
                              cancelada_ref=cancelada_ref,progresso_cb=progresso_cb)
        if lista.diagnostico is not diag:
            lista.diagnostico.falhas.extend(diag.falhas)
    if progresso_cb:
        progresso_cb(100, lista.mensagem)
    return lista
