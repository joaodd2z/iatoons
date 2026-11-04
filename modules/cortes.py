"""Sugestões de cortes por limites de fala e pausas, sem análise semântica."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import uuid

from modules.transcricao import Transcricao


@dataclass
class Corte:
    inicio: float
    fim: float
    titulo: str
    selecionado: bool = True
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    status: str = "pendente"
    erro: str = ""
    caminho_saida: str = ""
    descricao: str = ""
    motivo_ia: str = ""
    opcoes_titulo: list[str] = field(default_factory=list)
    inicio_original_ia: float | None = None
    gancho_ia: str = ""
    modo_ab: str = "A"
    gancho_b_inicio: float | None = None
    gancho_b_fim: float | None = None
    gancho_b_motivo: str = ""
    status_versoes: dict[str, str] = field(default_factory=dict)
    modo_edicao: str = "normal"
    remix_descricao: str = ""
    remix_gancho_inicio: float | None = None
    remix_gancho_fim: float | None = None
    remix_gancho_motivo: str = ""
    remix_segmentos: list[dict] = field(default_factory=list)
    remix_justificativa: str = ""
    remix_continuidade: str = ""
    remix_confianca: float = 0.0
    remix_repetir_gancho: bool = False
    origem: str = "manual"
    remix_rules_version: str = ""
    validacao_remix: dict = field(default_factory=dict)
    video_id: str = ""
    estrutura_remix: dict = field(default_factory=dict)
    titulos_v2: dict = field(default_factory=dict)

    @property
    def duracao(self) -> float:
        return self.fim - self.inicio

    def como_dict(self) -> dict:
        return asdict(self)


def _titulo_provisorio(texto: str, indice: int) -> str:
    palavras = texto.strip().split()
    if not palavras:
        return f"CORTE {indice}"
    return " ".join(palavras[:7]).upper().rstrip(".,;:!?")


def sugerir_cortes(transcricao: Transcricao, max_cortes: int = 5,
                   duracao_min: float = 60.0, duracao_max: float = 120.0,
                   alvo: float = 90.0) -> list[Corte]:
    """Divide por fronteiras de segmentos, favorecendo pausas e frases completas."""
    if max_cortes < 1 or max_cortes > 50:
        raise ValueError("A quantidade máxima deve estar entre 1 e 50.")
    if not (0 < duracao_min <= alvo <= duracao_max):
        raise ValueError("Durações de corte inválidas.")
    segmentos = [s for s in transcricao.segmentos if s.fim > s.inicio]
    if not segmentos:
        return []

    resultado: list[Corte] = []
    posicao = 0
    while posicao < len(segmentos) and len(resultado) < max_cortes:
        inicio = segmentos[posicao].inicio
        candidatos = []
        for i in range(posicao, len(segmentos)):
            fim = segmentos[i].fim
            duracao = fim - inicio
            if duracao < duracao_min:
                continue
            if duracao > duracao_max:
                break
            pausa = 0.0
            if i + 1 < len(segmentos):
                pausa = max(0.0, segmentos[i + 1].inicio - fim)
            termina_frase = segmentos[i].texto.rstrip().endswith((".", "!", "?"))
            # Menor pontuação é melhor: proximidade do alvo, pausa e pontuação.
            score = abs(duracao - alvo) - min(pausa, 2.0) * 8 - (6 if termina_frase else 0)
            candidatos.append((score, i, fim))

        if not candidatos:
            break
        _, fim_indice, fim = min(candidatos, key=lambda item: item[0])
        texto = " ".join(s.texto.strip() for s in segmentos[posicao:fim_indice + 1])
        resultado.append(Corte(inicio, fim, _titulo_provisorio(texto, len(resultado) + 1),
                               origem="structural_local", status="revisão", selecionado=False))
        posicao = fim_indice + 1

    # Se restou uma cauda curta, incorpora ao último corte somente se não ultrapassar o máximo.
    if resultado and posicao < len(segmentos):
        fim_total = segmentos[-1].fim
        if fim_total - resultado[-1].inicio <= duracao_max:
            resultado[-1].fim = fim_total
    return resultado


def corte_de_plano(plano) -> Corte:
    """Preserva proveniencia e roteiro automatico durante edicao/persistencia."""
    from modules.titulos_v2 import titulo_exibicao
    return Corte(plano.inicio, plano.fim, titulo_exibicao(plano), descricao=plano.descricao, status=plano.status,
                 selecionado=plano.selecionado,
                 opcoes_titulo=[t.texto for t in plano.titulos], modo_edicao="remix",
                 origem="remix", video_id=plano.video_id, estrutura_remix=plano.estrutura,
                 remix_descricao=plano.descricao, remix_gancho_inicio=plano.gancho["inicio"],
                 remix_gancho_fim=plano.gancho["fim"], remix_gancho_motivo=plano.gancho.get("motivo", ""),
                 remix_segmentos=plano.segmentos, remix_justificativa=plano.motivo_escolha,
                 remix_continuidade=plano.remix_motivo, remix_confianca=plano.confianca,
                 remix_rules_version=plano.remix_rules_version, validacao_remix=plano.validacao_remix,
                 titulos_v2=plano.titulos_v2)


def texto_do_corte(transcricao: Transcricao, corte: Corte) -> str:
    return " ".join(
        s.texto.strip() for s in transcricao.segmentos
        if s.fim > corte.inicio and s.inicio < corte.fim
    )
