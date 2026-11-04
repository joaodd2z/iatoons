"""Orquestração do Modo Agência: planejar → validar → renderizar → verificar → exportar.

Um corte só chega à pasta final quando passa no QUALITY GATE completo. Se a
verificação dos frames reais falhar, a renderização é refeita em modo seguro
(decodificação sem seek); persistindo a falha, o arquivo vai para revisão e
não é exportado automaticamente.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field, replace
from pathlib import Path
import shutil
from typing import Callable

from PIL import Image

from modules import preset as P
from modules.agencia.av import AjustesAgencia, ajustes_agencia_de, fonte_ja_processada
from modules.agencia.capa import candidatos_capa, candidatos_como_dicts, compor_capa
from modules.agencia.editorial import PlanoAgencia
from modules.agencia.titulos_manual import exigir_confirmacao, manual_confirmado, validar_para_exportacao
from modules.agencia.layout import (
    Enquadramento, TextoAjustado, ajustar_texto, calcular_enquadramento, dividir_legendas_longas,
    estimar_centro_interesse, medir_movimento,
)
from modules.agencia.metricas import montar_metricas, salvar_metricas
from modules.agencia.qualidade import (
    TEMPOS_PRIMEIRO_FRAME, ItemGate, aprovado, como_dicts, extrair_frame, falhas, gate_pre,
    relatorio_exportacao, validar_exportacao, validar_frames,
)
from modules.agencia.renderer import InfoFonte, construir_comando, executar_ffmpeg, inspecionar_fonte
from modules.agencia.timeline import (
    OpcoesEdicao, TimelineAgencia, construir_timeline, gerar_legendas_timeline, palavras_entre,
)
from modules.intro import IntervaloIntro
from modules.legendas import BlocoLegenda, salvar_srt
from modules.lote import caminho_sem_sobrescrever
from modules.perfis_visuais import PerfilVisual, obter_perfil
from modules.transcricao import Transcricao


RENDER_DIR = P.TEMP_DIR / "agencia_render"
REVISAO_DIR = P.TEMP_DIR / "agencia_revisao"
FRAMES_DIR = P.TEMP_DIR / "agencia_frames"


@dataclass
class PreparacaoCorte:
    plano: PlanoAgencia
    timeline: TimelineAgencia
    info: InfoFonte
    enquadramento: Enquadramento
    titulo: TextoAjustado
    legendas: list[tuple[BlocoLegenda, TextoAjustado]]
    gate: list[ItemGate]
    aplicar_tratamento: bool = True
    correcoes: list[str] = field(default_factory=list)


def _estimar_centro(video: str, timeline: TimelineAgencia, pasta: Path) -> tuple[float, float]:
    imagens = []
    duracao = timeline.duracao
    for indice, fracao in enumerate((0.2, 0.5, 0.8)):
        try:
            caminho = extrair_frame(video, timeline.saida_para_fonte(duracao * fracao),
                                    pasta / f"centro_{indice}.jpg")
            with Image.open(caminho) as imagem:
                imagens.append(imagem.convert("RGB").copy())
            caminho.unlink(missing_ok=True)
        except (RuntimeError, OSError):
            continue
    try:
        if pasta.exists():
            shutil.rmtree(str(pasta), ignore_errors=True)
    except Exception:
        pass
    centro = estimar_centro_interesse(imagens) if imagens else (0.5, 0.5)
    del imagens
    return centro


def _ajustar_titulo_com_correcao(plano: PlanoAgencia, perfil: PerfilVisual,
                                 correcoes: list[str]) -> TextoAjustado:
    estilo, zona = perfil.estilo_titulo_agencia, perfil.zonas.titulo
    titulo = ajustar_texto(plano.titulo, estilo, zona)
    if titulo.cabe:
        return titulo
    # Correção segura 1: aceitar uma terceira linha, se ainda couber na safe zone.
    tres = ajustar_texto(plano.titulo, estilo, zona, max_linhas=3)
    if tres.cabe:
        correcoes.append("Título ajustado em 3 linhas para caber na safe zone.")
        return tres
    # Correção segura 2: usar a próxima alternativa gerada que caiba.
    for alternativa in ([] if manual_confirmado(plano) else plano.titulos):
        candidato = ajustar_texto(alternativa.texto, estilo, zona)
        if candidato.cabe and alternativa.texto != plano.titulo:
            correcoes.append(f"Título trocado pela alternativa ({alternativa.tipo}) que cabe na safe zone.")
            plano.titulo_escolhido = alternativa.texto
            return candidato
    return titulo


from modules.performance import medir_operacao, medir_chamada


def _chave_temp(plano: PlanoAgencia, transcricao: Transcricao | None = None) -> str:
    vid = (getattr(transcricao, "video_id", "") if transcricao else "") or getattr(plano, "video_id", "")
    prefixo = f"{vid[:12]}_" if vid else ""
    return f"{prefixo}{plano.id}"


@medir_operacao("preparacao_corte")
def preparar_corte(video: str, plano: PlanoAgencia, transcricao: Transcricao,
                   perfil: str | PerfilVisual | None = None, opcoes: OpcoesEdicao | None = None,
                   intro: IntervaloIntro | None = None, outros_planos: list[PlanoAgencia] = (),
                   ajustes: AjustesAgencia | None = None, info: InfoFonte | None = None,
                   estimar_centro: bool = True) -> PreparacaoCorte:
    if plano.video_id and plano.video_id != transcricao.video_id:
        raise ValueError("Este plano pertence a outro episódio e não pode ser reutilizado.")
    perfil = obter_perfil(perfil)
    opcoes = (opcoes or OpcoesEdicao()).validar()
    ajustes = ajustes_agencia_de(ajustes)
    info = info or inspecionar_fonte(video)
    correcoes: list[str] = []
    plano_trabalho = copy.deepcopy(plano)
    cache_mov: dict[tuple[float, float], float] = {}

    def movimento(inicio: float, fim: float) -> float:
        chave = (round(inicio, 2), round(fim, 2))
        if chave not in cache_mov:
            cache_mov[chave] = medir_movimento(video, inicio, fim)
        return cache_mov[chave]

    timeline = construir_timeline(plano_trabalho, transcricao, intro, opcoes, movimento)

    # Correção automática segura do HOOK: aproxima a entrada da primeira fala.
    primeiro = timeline.trechos[0]
    palavras = palavras_entre(transcricao, primeiro.inicio, primeiro.fim)
    if (palavras and not plano_trabalho.gancho_visual and timeline.tipo == "normal"
            and palavras[0].inicio - primeiro.inicio > 1.5):
        novo = palavras[0].inicio - opcoes.preroll
        for segmento in plano_trabalho.segmentos:
            if float(segmento["inicio"]) <= primeiro.inicio + 0.01 < float(segmento["fim"]):
                segmento["inicio"] = novo
        timeline = construir_timeline(plano_trabalho, transcricao, intro, opcoes, movimento)
        correcoes.append("Entrada avançada até a primeira fala (HOOK).")

    zona = perfil.zonas.video
    if estimar_centro and plano.centro_x is None:
        previo = calcular_enquadramento(info.largura, info.altura, zona)
        if previo.escala_w > previo.crop_w or previo.escala_h > previo.crop_h:
            plano.centro_x, plano.centro_y = _estimar_centro(video, timeline, FRAMES_DIR / _chave_temp(plano, transcricao))
    enquadramento = calcular_enquadramento(info.largura, info.altura, zona,
                                           centro_x=plano.centro_x if plano.centro_x is not None else 0.5,
                                           centro_y=plano.centro_y if plano.centro_y is not None else 0.5)
    titulo = _ajustar_titulo_com_correcao(plano, perfil, correcoes)
    estilo_legenda, zona_legenda = perfil.estilo_legenda_agencia, perfil.zonas.legenda
    blocos = gerar_legendas_timeline(transcricao, timeline, opcoes.palavras_por_bloco)
    divididos = dividir_legendas_longas(blocos, estilo_legenda, zona_legenda)
    if len(divididos) != len(blocos):
        correcoes.append(f"{len(divididos) - len(blocos)} legenda(s) longa(s) divididas para evitar overflow.")
    legendas = [(b, ajustar_texto(b.texto, estilo_legenda, zona_legenda, alinhamento_vertical="topo"))
                for b in divididos]
    gate = gate_pre(plano, timeline, titulo, legendas, transcricao, perfil, intro, list(outros_planos))
    aplicar = not (ajustes.pular_se_processado and fonte_ja_processada(video))
    if not aplicar:
        correcoes.append("Fonte já processada pelo aplicativo: tratamento AV não reaplicado.")
    return PreparacaoCorte(plano, timeline, info, enquadramento, titulo, legendas, gate, aplicar, correcoes)


def _nome_saida(video: str, plano: PlanoAgencia, perfil: PerfilVisual, timeline: TimelineAgencia) -> str:
    titulo = "".join(c for c in plano.titulo if c.isalnum() or c in " -_")[:48].strip()
    return f"{Path(video).stem}_{titulo}_{perfil.nome_canal.replace(' ', '_')}_AGENCIA_{timeline.tipo.upper()}"


@medir_operacao("exportacao")
def exportar_corte_agencia(
    video: str, plano: PlanoAgencia, transcricao: Transcricao, pasta_saida: str | Path,
    perfil: str | PerfilVisual | None = None, ajustes: AjustesAgencia | None = None,
    opcoes: OpcoesEdicao | None = None, intro: IntervaloIntro | None = None,
    outros_planos: list[PlanoAgencia] = (), exportar_srt: bool = True, gerar_capa: bool = True,
    tempos_verificacao=TEMPOS_PRIMEIRO_FRAME, cancelada_ref=None,
    progresso_cb: Callable[[float, str], None] | None = None,
    preparacao: PreparacaoCorte | None = None,
) -> dict:
    try:
        from modules.agencia.producao import exigir_corte, manual_confirmado
        exigir_corte(plano, transcricao, intro)
        manual = exigir_confirmacao(plano)
        if plano.titulos_v2 and not manual:
            validar_para_exportacao(plano, transcricao)
    except ValueError as erro:
        return {"sucesso": False, "revisao": True, "revisao_titulo": plano.estado_editorial != "revisao",
                "erro": str(erro), "gate": plano.gate}

    def progresso(pct: float, msg: str):
        if progresso_cb:
            progresso_cb(pct, msg)

    perfil = obter_perfil(perfil)
    ajustes = ajustes_agencia_de(ajustes)
    opcoes = (opcoes or OpcoesEdicao()).validar()
    progresso(1, "Montando a edição (Modo Agência)…")
    try:
        prep = (
            preparacao
            if (preparacao is not None and getattr(preparacao, "timeline", None) is not None and getattr(preparacao, "gate", None) is not None)
            else preparar_corte(video, plano, transcricao, perfil, opcoes, intro, outros_planos, ajustes)
        )
    except ValueError as erro:
        plano.status, plano.erro = "revisão", str(erro)
        return {"sucesso": False, "revisao": True, "erro": f"Remix inválido: {erro}", "gate": plano.gate}
    if manual_confirmado(plano):
        from modules.agencia.producao import problemas_tecnicos
        erros = problemas_tecnicos(plano,transcricao,montagem=True,intro=intro,timeline=prep.timeline)
        if erros:
            plano.status,plano.erro = "REVISAO_HUMANA","; ".join(erros)
            return {"sucesso":False,"revisao":True,"erro":plano.erro,"gate":plano.gate}
        # Explicit human editorial override never bypasses technical pre/post gates.
        for item in prep.gate:
            if item.nome in ("REMIX", "HOOK", "DUPLICACAO", "FINAL"):
                item.bloqueante = False
    plano.gate = como_dicts(prep.gate)
    if not aprovado(prep.gate):
        motivos = "; ".join(f"{i.nome}: {i.detalhe}" for i in falhas(prep.gate))
        plano.status, plano.erro = "revisão", motivos
        return {"sucesso": False, "revisao": True, "erro": f"Quality gate reprovou: {motivos}",
                "gate": plano.gate, "correcoes": prep.correcoes}

    RENDER_DIR.mkdir(parents=True, exist_ok=True)
    temporario = RENDER_DIR / f"{_chave_temp(plano, transcricao)}.mp4"
    pasta_frames = FRAMES_DIR / _chave_temp(plano, transcricao)
    tentativas = []
    itens_pos: list[ItemGate] = []
    relatorio: dict = {}
    try:
        for tentativa, modo_seguro in enumerate((False, True), 1):
            if cancelada_ref and cancelada_ref.cancelada:
                return {"sucesso": False, "erro": "Renderização cancelada pelo usuário."}
            base = 5 if tentativa == 1 else 50
            cmd = construir_comando(
                video, str(temporario), prep.timeline, prep.info, prep.enquadramento, perfil, ajustes,
                prep.titulo, prep.legendas, RENDER_DIR / f"{_chave_temp(plano, transcricao)}_grafo.txt",
                prep.aplicar_tratamento, modo_seguro,
            )
            resultado = executar_ffmpeg(
                cmd, prep.timeline.duracao, cancelada_ref,
                lambda pct, msg: progresso(base + pct * 0.4, msg + (" (modo seguro)" if modo_seguro else "")),
            )
            if not resultado["sucesso"]:
                tentativas.append({"tentativa": tentativa, "modo_seguro": modo_seguro, "erro": resultado["erro"]})
                if "cancelada" in resultado["erro"]:
                    return resultado
                continue
            progresso(base + 42, "Verificando primeiro frame e imagem…")
            itens_frames, detalhes = validar_frames(temporario, perfil, tempos_verificacao, video,
                                                    prep.timeline, pasta_frames)
            relatorio = relatorio_exportacao(temporario)
            itens_pos = itens_frames + validar_exportacao(relatorio, prep.timeline.duracao, prep.timeline.fps)
            tentativas.append({"tentativa": tentativa, "modo_seguro": modo_seguro,
                               "frames": detalhes, "gate": como_dicts(itens_pos)})
            if aprovado(itens_pos):
                break
        gate_total = prep.gate + itens_pos
        plano.gate = como_dicts(gate_total)
        if not itens_pos or not aprovado(itens_pos):
            motivo = ("; ".join(f"{i.nome}: {i.detalhe}" for i in falhas(itens_pos))
                      or (tentativas[-1].get("erro", "falha na renderização") if tentativas else "falha"))
            if temporario.exists():
                REVISAO_DIR.mkdir(parents=True, exist_ok=True)
                destino_revisao = caminho_sem_sobrescrever(REVISAO_DIR, f"{plano.id}_REVISAR", ".mp4")
                shutil.move(str(temporario), destino_revisao)
            plano.status, plano.erro = "revisão", motivo
            return {"sucesso": False, "revisao": True, "erro": f"Exportação inválida: {motivo}",
                    "gate": plano.gate, "tentativas": tentativas}

        adicionais = (".srt", ".metricas.json") if exportar_srt else (".metricas.json",)
        destino = caminho_sem_sobrescrever(pasta_saida, _nome_saida(video, plano, perfil, prep.timeline),
                                           ".mp4", adicionais)
        medir_chamada("pos_processamento_mover_mp4", shutil.move, str(temporario), destino)
        if exportar_srt:
            salvar_srt([b for b, _ in prep.legendas], destino.with_suffix(".srt"))
        capa_info = {}
        if gerar_capa:
            progresso(96, "Gerando capa…")
            try:
                capa_info = gerar_capa_corte(video, plano, prep, perfil, destino.with_name(destino.stem + "_capa.jpg"),
                                             ajustes)
            except (RuntimeError, OSError) as erro:
                capa_info = {"erro": str(erro)}
        metricas = montar_metricas(
            plano, prep.timeline, perfil.id, ajustes.nome if prep.aplicar_tratamento else f"{ajustes.nome} (não reaplicado)",
            relatorio, plano.gate,
            {"tipo_fonte": prep.enquadramento.tipo_fonte, "estrategia": prep.enquadramento.estrategia,
             "fracao_visivel": prep.enquadramento.fracao_visivel,
             "centro": [plano.centro_x, plano.centro_y]},
            capa_info.get("caminho", ""),
        )
        metricas["correcoes_automaticas"] = prep.correcoes
        metricas["tentativas_render"] = [{k: v for k, v in t.items() if k != "frames"} for t in tentativas]
        caminho_metricas = salvar_metricas(destino, metricas)
        plano.status, plano.erro, plano.caminho_saida = "concluído", "", str(destino)
        progresso(100, "Concluído e aprovado no quality gate.")
        return {"sucesso": True, "caminho_saida": str(destino), "relatorio": relatorio, "gate": plano.gate,
                "validacao_remix": plano.validacao_remix, "remix_rules_version": plano.remix_rules_version,
                "metricas": str(caminho_metricas), "capa": capa_info, "correcoes": prep.correcoes,
                "tentativas": tentativas, "timeline": prep.timeline, "duracao_s": prep.timeline.duracao,
                "erro": ""}
    finally:
        # Limpeza agressiva de buffers e arquivos intermediários de frames
        try:
            if pasta_frames.exists():
                shutil.rmtree(str(pasta_frames), ignore_errors=True)
            pasta_capa = FRAMES_DIR / _chave_temp(plano)
            if pasta_capa.exists():
                shutil.rmtree(str(pasta_capa), ignore_errors=True)
            grafo_txt = RENDER_DIR / f"{_chave_temp(plano, transcricao)}_grafo.txt"
            if grafo_txt.exists():
                grafo_txt.unlink(missing_ok=True)
            if temporario.exists():
                temporario.unlink(missing_ok=True)
        except Exception:
            pass
        import gc
        gc.collect()


@medir_operacao("pos_processamento_capa")
def gerar_capa_corte(video: str, plano: PlanoAgencia, prep: PreparacaoCorte, perfil: PerfilVisual,
                     destino: str | Path, ajustes: AjustesAgencia | None = None) -> dict:
    ajustes = ajustes_agencia_de(ajustes)
    pasta = FRAMES_DIR / _chave_temp(plano) / "capa"
    candidatos = candidatos_capa(video, prep.timeline, plano, pasta)
    if not candidatos:
        raise RuntimeError("Nenhum frame adequado para a capa.")
    plano.capa_tempos = [c.tempo_fonte for c in candidatos]
    indice = min(max(0, plano.capa_indice), len(candidatos) - 1)
    info = compor_capa(video, candidatos[indice].tempo_fonte, perfil, prep.enquadramento,
                       plano.titulo_capa or plano.titulo, destino, ajustes.fundo_desfoque)
    info["candidatos"] = candidatos_como_dicts(candidatos)
    info["indice"] = indice
    plano.caminho_capa = info["caminho"]
    return info


def gerar_capas_candidatas(video: str, plano: PlanoAgencia, transcricao: Transcricao,
                           perfil: str | PerfilVisual | None, pasta: str | Path,
                           opcoes: OpcoesEdicao | None = None, intro: IntervaloIntro | None = None,
                           ajustes: AjustesAgencia | None = None) -> list[dict]:
    """Compõe as 3 capas candidatas para escolha manual (nenhuma renderização de vídeo)."""
    perfil = obter_perfil(perfil)
    prep = preparar_corte(video, plano, transcricao, perfil, opcoes, intro, (), ajustes)
    pasta = Path(pasta)
    candidatos = candidatos_capa(video, prep.timeline, plano, FRAMES_DIR / _chave_temp(plano, transcricao) / "capa")
    plano.capa_tempos = [c.tempo_fonte for c in candidatos]
    resultado = []
    for indice, candidato in enumerate(candidatos):
        info = compor_capa(video, candidato.tempo_fonte, perfil, prep.enquadramento,
                           plano.titulo_capa or plano.titulo,
                           pasta / f"capa_{plano.id}_{indice + 1}.jpg",
                           ajustes_agencia_de(ajustes).fundo_desfoque)
        info["pontuacao"] = candidato.pontuacao
        resultado.append(info)
    return resultado


@medir_operacao("preview")
def renderizar_previa_agencia(video: str, plano: PlanoAgencia, transcricao: Transcricao,
                              perfil: str | PerfilVisual | None, destino: str | Path,
                              segundos: float = 10.0, opcoes: OpcoesEdicao | None = None,
                              intro: IntervaloIntro | None = None, ajustes: AjustesAgencia | None = None,
                              cancelada_ref=None, progresso_cb=None) -> dict:
    """Prévia rápida (30 FPS, ultrafast) com a mesma composição; também valida o primeiro frame."""
    try:
        exigir_confirmacao(plano)
    except ValueError as erro:
        return {"sucesso": False, "revisao_titulo": True, "erro": str(erro)}
    perfil = obter_perfil(perfil)
    opcoes = replace((opcoes or OpcoesEdicao()), fps=30)
    ajustes = replace(ajustes_agencia_de(ajustes), preset_x264="ultrafast")
    prep = preparar_corte(video, plano, transcricao, perfil, opcoes, intro, (), ajustes)
    trechos, total = [], 0.0
    for trecho in prep.timeline.trechos:
        if total >= segundos:
            break
        copia = replace(trecho)
        restante = segundos - total + copia.duracao_transicao
        if copia.duracao > restante:
            copia.fim = copia.inicio + max(0.5, round(restante * 30) / 30)
        trechos.append(copia)
        total += copia.duracao - copia.duracao_transicao
    timeline = replace(prep.timeline, trechos=trechos)
    legendas = [(b, a) for b, a in prep.legendas if b.inicio < timeline.duracao]
    cmd = construir_comando(video, str(destino), timeline, prep.info, prep.enquadramento, perfil, ajustes,
                            prep.titulo, legendas, RENDER_DIR / f"{_chave_temp(plano, transcricao)}_previa.txt",
                            prep.aplicar_tratamento)
    resultado = executar_ffmpeg(cmd, timeline.duracao, cancelada_ref, progresso_cb)
    if not resultado["sucesso"]:
        return resultado
    itens, _ = validar_frames(destino, perfil, (0.0, 0.1, 0.25, 0.5), None, None,
                              FRAMES_DIR / f"{_chave_temp(plano, transcricao)}_previa")
    resultado.update({"caminho_saida": str(destino), "gate": como_dicts(itens), "valida": aprovado(itens)})
    return resultado
