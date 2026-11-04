"""
renderer.py — Renderização do preset IA TOONS com FFmpeg
Compõe o layout vertical 9:16: fundo roxo + animação 1:1 + título + legenda.
"""

import subprocess
from collections import deque
from pathlib import Path
from typing import Optional, Callable

from modules import preset as P
from modules.importacao import segundos_para_hhmmss, analisar_video
from modules.processamento_av import filtro_animacao, filtro_audio
from modules.perfis_visuais import obter_perfil


# ─── Funções auxiliares ───────────────────────────────────────────────────────

def _escapar_texto_ffmpeg(texto: str) -> str:
    """
    Escapa caracteres especiais para uso no filtro drawtext do FFmpeg.
    O drawtext usa sintaxe própria — : e ' precisam de escape.
    """
    texto = texto.replace("\\", "\\\\")
    texto = texto.replace("'", "\\'")
    texto = texto.replace(":", "\\:")
    return texto


def _quebrar_linhas(texto: str, max_chars: int) -> list[str]:
    """
    Divide o texto em linhas com no máximo max_chars caracteres,
    quebrando nas palavras para não cortar no meio.
    """
    palavras = texto.split()
    linhas = []
    linha_atual = ""
    for palavra in palavras:
        if not linha_atual:
            linha_atual = palavra
        elif len(linha_atual) + 1 + len(palavra) <= max_chars:
            linha_atual += " " + palavra
        else:
            linhas.append(linha_atual)
            linha_atual = palavra
    if linha_atual:
        linhas.append(linha_atual)
    return linhas


def _filtro_drawtext(
    texto: str,
    fonte: str,
    tamanho: int,
    y_centro: int,
    espessura_contorno: int,
    largura_tela: int = P.SAIDA_LARGURA,
    margem_horiz: int = P.TITULO_MARGEM_HORIZ,
    max_chars: int = 40,
    multilinhas_y_base: Optional[int] = None,
    cor: str = "white",
    cor_contorno: str = "black",
    caixa_alta: bool = True,
) -> str:
    """
    Gera a string de filtro drawtext do FFmpeg para um bloco de texto.
    Suporta múltiplas linhas com centralização horizontal automática.
    Retorna uma sequência de filtros separados por vírgulas.
    """
    texto_formatado = texto.upper() if caixa_alta else texto
    linhas = _quebrar_linhas(texto_formatado, max_chars)
    fonte_esc = fonte.replace("\\", "/").replace(":", "\\\\:").replace(" ", "\\\\ ")
    filtros = []

    altura_linha_estimada = int(tamanho * 1.15)  # linha + espaçamento
    n = len(linhas)

    for i, linha in enumerate(linhas):
        texto_esc = _escapar_texto_ffmpeg(linha)

        if multilinhas_y_base is not None:
            y_expr = multilinhas_y_base + i * altura_linha_estimada
        else:
            # centraliza verticalmente no y_centro indicado
            offset_total = (n * altura_linha_estimada) / 2
            y_expr = int(y_centro - offset_total + i * altura_linha_estimada)

        # Contorno: desenha o texto 4 vezes deslocado (sombra cross) + texto principal
        # O drawtext tem 'borderw' nativo para contorno sólido
        filtro = (
            f"drawtext="
            f"fontfile={fonte_esc}:"
            f"text='{texto_esc}':"
            f"fontsize={tamanho}:"
            f"fontcolor={cor}:"
            f"borderw={espessura_contorno}:"
            f"bordercolor={cor_contorno}:"
            f"expansion=none:"
            f"x=(w-text_w)/2:"
            f"y={y_expr}"
        )
        filtros.append(filtro)

    return ",".join(filtros)


def _filtros_legendas_temporizadas(
    legendas,
    tamanho: int = P.LEGENDA_TAMANHO,
    y_topo: int = P.LEGENDA_Y_TOPO,
    max_chars: int = P.LEGENDA_MAX_CHARS_LINHA,
    estilo=None,
) -> str:
    """Cria drawtexts temporizados; recebe objetos ou dicts com início/fim/texto."""
    fonte = str(P.FONTE_PATH).replace("\\", "/").replace(":", "\\\\:").replace(" ", "\\\\ ")
    filtros = []
    for legenda in legendas or []:
        inicio = float(legenda["inicio"] if isinstance(legenda, dict) else legenda.inicio)
        fim = float(legenda["fim"] if isinstance(legenda, dict) else legenda.fim)
        texto = str(legenda["texto"] if isinstance(legenda, dict) else legenda.texto)
        caixa_alta = True if estilo is None else estilo.caixa_alta
        texto_formatado = texto.upper() if caixa_alta else texto
        for indice, linha in enumerate(_quebrar_linhas(texto_formatado, max_chars)):
            texto_esc = _escapar_texto_ffmpeg(linha)
            y = y_topo + indice * int(tamanho * 1.15)
            filtros.append(
                "drawtext="
                f"fontfile={fonte}:text='{texto_esc}':fontsize={tamanho}:"
                f"fontcolor={estilo.cor if estilo else P.LEGENDA_COR}:"
                f"borderw={estilo.espessura_contorno if estilo else P.LEGENDA_ESPESSURA_CONTORNO}:"
                f"bordercolor={estilo.cor_contorno if estilo else P.LEGENDA_COR_CONTORNO}:expansion=none:"
                f"x=(w-text_w)/2:y={y}:enable='between(t,{inicio:.3f},{fim:.3f})'"
            )
    return ",".join(filtros)


# ─── Extração de thumbnail ────────────────────────────────────────────────────

def extrair_thumbnail(
    caminho_video: str,
    tempo_segundos: float,
    caminho_saida: str,
) -> bool:
    """
    Extrai um único frame do vídeo no tempo indicado e salva como JPEG.
    Não modifica o vídeo original.
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-ss", str(tempo_segundos),
        "-i", caminho_video,
        "-frames:v", "1",
        "-q:v", "3",
        caminho_saida,
    ]
    try:
        resultado = subprocess.run(
            cmd, capture_output=True, timeout=30,
            encoding="utf-8", errors="replace",
        )
        return resultado.returncode == 0
    except Exception:
        return False


from modules.performance import medir_operacao


@medir_operacao("preview_legado", backend="ffmpeg")
def extrair_thumbnail_composto(
    caminho_video: str,
    tempo_segundos: float,
    titulo: str,
    caminho_saida: str,
    legenda_texto: str = "",
    legenda_tamanho: int = P.LEGENDA_TAMANHO,
    legenda_y: int = P.LEGENDA_Y_TOPO,
    ajustes_av=None,
    perfil_visual=None,
) -> bool:
    """
    Extrai um frame e aplica o perfil visual (fundo + animação 1:1 + título).
    Salva como JPEG para prévia estática na interface.
    """
    perfil = obter_perfil(perfil_visual)
    fundo = str(perfil.fundo_path).replace("\\", "/")
    titulo_exibido = titulo if titulo else "SEM TÍTULO"
    zona = perfil.zonas.video
    filtro_titulo = _filtro_drawtext(
        texto=titulo_exibido,
        fonte=str(P.FONTE_PATH),
        tamanho=P.TITULO_TAMANHO,
        y_centro=P.TITULO_Y_CENTRO,
        espessura_contorno=perfil.titulo.espessura_contorno,
        max_chars=28,
        cor=perfil.titulo.cor,
        cor_contorno=perfil.titulo.cor_contorno,
        caixa_alta=perfil.titulo.caixa_alta,
    )
    filtro_legenda = ""
    if legenda_texto.strip():
        filtro_legenda = "," + _filtro_drawtext(
            texto=legenda_texto,
            fonte=str(P.FONTE_PATH),
            tamanho=legenda_tamanho,
            y_centro=legenda_y,
            espessura_contorno=perfil.legenda.espessura_contorno,
            max_chars=P.LEGENDA_MAX_CHARS_LINHA,
            multilinhas_y_base=legenda_y,
            cor=perfil.legenda.cor,
            cor_contorno=perfil.legenda.cor_contorno,
            caixa_alta=perfil.legenda.caixa_alta,
        )

    # Filtro de composição para o thumbnail
    info_v = analisar_video(caminho_video)
    w_orig = info_v.largura if info_v.valido else 0
    h_orig = info_v.altura if info_v.valido else 0
    filtro_v = (
        # 1) Fundo: estica ou recorta para 1080x1920 garantindo proporção exata
        f"[1:v]scale={P.SAIDA_LARGURA}:{P.SAIDA_ALTURA}:force_original_aspect_ratio=increase,"
        f"crop={P.SAIDA_LARGURA}:{P.SAIDA_ALTURA},setsar=1[bg];"
        # 2) Animação: escala o vídeo inteiro mantendo proporção e CORTA (crop) o centro em 1080x1080
        f"[0:v]{filtro_animacao(ajustes_av, largura=zona.w, altura=zona.h, largura_orig=w_orig, altura_orig=h_orig)}[anim];"
        # 3) Sobrepõe a animação na zona de vídeo do perfil (legado: y=420)
        f"[bg][anim]overlay={zona.x}:{zona.y}[comp];"
        # 4) Desenha o título com as mesmas regras usadas na exportação
        f"[comp]{filtro_titulo}{filtro_legenda}[out]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(tempo_segundos),
        "-i", caminho_video,
        "-i", str(perfil.fundo_path),
        "-filter_complex", filtro_v,
        "-map", "[out]",
        "-frames:v", "1",
        "-q:v", "3",
        caminho_saida,
    ]
    try:
        resultado = subprocess.run(
            cmd, capture_output=True, timeout=60,
            encoding="utf-8", errors="replace",
        )
        if resultado.returncode != 0:
            err_log = Path(P.TEMP_DIR) / "ffmpeg_erro.log"
            err_log.write_text(f"Comando:\n{' '.join(cmd)}\n\nErro:\n{resultado.stderr}", encoding="utf-8")
            return False
        return True
    except Exception as e:
        err_log = Path(P.TEMP_DIR) / "ffmpeg_erro.log"
        err_log.write_text(f"Exception:\n{str(e)}", encoding="utf-8")
        return False


# ─── Renderização principal ───────────────────────────────────────────────────

@medir_operacao("render_legado", backend="ffmpeg")
def renderizar_corte(
    caminho_video: str,
    inicio: float,
    fim: float,
    titulo: str,
    caminho_saida: str,
    fps: int = P.SAIDA_FPS_PAD,
    legendas=None,
    legenda_tamanho: int = P.LEGENDA_TAMANHO,
    legenda_y: int = P.LEGENDA_Y_TOPO,
    ajustes_av=None,
    segmentos_fonte=None,
    cancelada_ref=None,
    progresso_cb: Optional[Callable[[float, str], None]] = None,
    perfil_visual=None,
) -> dict:
    """
    Renderiza um corte vertical usando o perfil visual selecionado.

    Parâmetros:
        caminho_video  : caminho absoluto do vídeo original
        inicio         : tempo de início em segundos
        fim            : tempo de fim em segundos
        titulo         : título do corte (maiúsculas aplicadas automaticamente)
        caminho_saida  : caminho do arquivo MP4 de saída
        fps            : 60 (padrão) ou 30 (teste)
        cancelada_ref  : objeto TarefaRenderizacao para verificar cancelamento
        progresso_cb   : callback(porcentagem: float, mensagem: str)

    Retorna:
        dict com 'sucesso', 'caminho_saida', 'erro', 'duracao_s'
    """
    trechos = []
    for trecho in segmentos_fonte or []:
        ti = float(trecho["inicio"] if isinstance(trecho, dict) else trecho.inicio)
        tf = float(trecho["fim"] if isinstance(trecho, dict) else trecho.fim)
        if tf <= ti:
            return {"sucesso": False, "erro": "Sequência A/B contém trecho inválido."}
        trechos.append((ti, tf))
    duracao = sum(tf - ti for ti, tf in trechos) if trechos else fim - inicio
    if duracao <= 0:
        return {"sucesso": False, "erro": "O tempo de fim deve ser maior que o início."}

    perfil = obter_perfil(perfil_visual)
    fundo = str(perfil.fundo_path)
    titulo_exibido = titulo if titulo else "SEM TÍTULO"
    zona = perfil.zonas.video

    Path(caminho_saida).parent.mkdir(parents=True, exist_ok=True)

    if progresso_cb:
        progresso_cb(0.0, "Preparando filtros…")

    # ── Montagem do filtro de vídeo ──────────────────────────────────────────
    # Estrutura:
    #   [0:v] = vídeo original (entrada 0)
    #   [1:v] = fundo jpg (entrada 1)
    #
    # Passos:
    #   1. Escala o fundo para 1080×1920
    #   2. Escala a animação para 1080×1080, com padding para não distorcer
    #   3. Interpola para o FPS alvo (minterpolate para animações)
    #   4. Sobrepõe a animação no fundo em y=420
    #   5. Desenha o título com a fonte RailroadGothicCC

    # Filtro do título (pode ter múltiplas linhas)
    filtro_titulo = _filtro_drawtext(
        texto=titulo_exibido,
        fonte=str(P.FONTE_PATH),
        tamanho=P.TITULO_TAMANHO,
        y_centro=P.TITULO_Y_CENTRO,
        espessura_contorno=perfil.titulo.espessura_contorno,
        max_chars=28,
        cor=perfil.titulo.cor,
        cor_contorno=perfil.titulo.cor_contorno,
        caixa_alta=perfil.titulo.caixa_alta,
    )
    filtro_legendas = _filtros_legendas_temporizadas(
        legendas, tamanho=legenda_tamanho, y_topo=legenda_y, estilo=perfil.legenda
    )
    cadeia_textos = filtro_titulo
    if filtro_legendas:
        cadeia_textos += "," + filtro_legendas

    prefixo_sequencia = ""
    entrada_animacao = "[0:v]"
    entrada_audio = None
    min_seek = 0.0
    if trechos:
        min_seek = max(0.0, min(ti for ti, tf in trechos) - 1.0)
        partes_v, partes_a = [], []
        for indice, (ti, tf) in enumerate(trechos):
            rel_ti = ti - min_seek
            rel_tf = tf - min_seek
            prefixo_sequencia += (
                f"[0:v]trim=start={rel_ti:.3f}:end={rel_tf:.3f},setpts=PTS-STARTPTS[v{indice}];"
                f"[0:a]atrim=start={rel_ti:.3f}:end={rel_tf:.3f},asetpts=PTS-STARTPTS[a{indice}];"
            )
            partes_v.append(f"[v{indice}]")
            partes_a.append(f"[a{indice}]")
        prefixo_sequencia += (
            f"{''.join(partes_v)}concat=n={len(trechos)}:v=1:a=0[seqv];"
            f"{''.join(partes_a)}concat=n={len(trechos)}:v=0:a=1[seqa];"
        )
        entrada_animacao = "[seqv]"
        entrada_audio = "[seqa]"
    else:
        prefixo_sequencia = "[0:v]setpts=PTS-STARTPTS[seqv];"
        entrada_animacao = "[seqv]"

    filtro_a = filtro_audio(ajustes_av, duracao)
    sufixo_audio = ""
    if entrada_audio:
        if filtro_a:
            sufixo_audio = f"{entrada_audio}{filtro_a}[audioout];"
        else:
            sufixo_audio = f"{entrada_audio}anull[audioout];"

    info_v = analisar_video(caminho_video)
    w_orig = info_v.largura if info_v.valido else 0
    h_orig = info_v.altura if info_v.valido else 0
    filtro_complex = (
        prefixo_sequencia +
        f"[1:v]scale={P.SAIDA_LARGURA}:{P.SAIDA_ALTURA}:force_original_aspect_ratio=increase,"
        f"crop={P.SAIDA_LARGURA}:{P.SAIDA_ALTURA},setsar=1,setpts=PTS-STARTPTS[bg];"
        f"{entrada_animacao}{filtro_animacao(ajustes_av, largura=zona.w, altura=zona.h, fps=fps, largura_orig=w_orig, altura_orig=h_orig)}[anim];"
        f"[bg][anim]overlay={zona.x}:{zona.y}:shortest=1:eof_action=pass[comp];"
        f"[comp]{cadeia_textos}[out];" + sufixo_audio
    ).rstrip(";")

    # ── Comando FFmpeg ──────────────────────────────────────────────────────
    cmd = ["ffmpeg", "-nostdin", "-y", "-nostats", "-loglevel", "error", "-threads", "2"]
    if trechos:
        max_tf = max(tf for ti, tf in trechos) + 0.5
        cmd += ["-ss", f"{min_seek:.3f}", "-t", f"{max_tf - min_seek:.3f}"]
    else:
        cmd += ["-ss", str(inicio), "-t", str(duracao)]
    cmd += [
        "-i", caminho_video,
        "-loop", "1", "-i", fundo,
        "-filter_complex", filtro_complex,
        "-map", "[out]",
        "-map", "[audioout]" if entrada_audio else "0:a?",
        "-t", f"{duracao:.6f}",
        "-c:v", P.SAIDA_CODEC,
        "-threads", "2",
        "-preset", P.SAIDA_PRESET,
        "-crf", str(P.SAIDA_CRF),
        "-r", str(fps),
        "-c:a", P.SAIDA_AUDIO,
        "-b:a", P.SAIDA_AUDIO_BR,
        "-ar", "44100",
        "-ac", "2",
        "-pix_fmt", P.SAIDA_PIXFMT,
        "-movflags", "+faststart",
        "-progress", "pipe:1",    # saída de progresso em tempo real
        caminho_saida,
    ]

    if not entrada_audio:
        # Zera também o relógio do áudio para ele começar junto do primeiro
        # frame. O tratamento AV, quando ativo, permanece na mesma cadeia.
        cadeia_audio = "asetpts=PTS-STARTPTS"
        if filtro_a:
            cadeia_audio += "," + filtro_a
        indice_codec_audio = cmd.index("-c:a")
        cmd[indice_codec_audio:indice_codec_audio] = ["-af", cadeia_audio]

    if progresso_cb:
        progresso_cb(2.0, "Iniciando FFmpeg…")

    processo = None
    frames_total = duracao * fps
    frames_atual = 0
    saida_recente = deque(maxlen=40)
    try:
        processo = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        # Lê o progresso em tempo real a partir do stdout do FFmpeg
        for linha in processo.stdout:
            if cancelada_ref and cancelada_ref.cancelada:
                try:
                    processo.kill()
                except Exception:
                    pass
                return {"sucesso": False, "erro": "Renderização cancelada pelo usuário."}

            linha = linha.strip()
            saida_recente.append(linha)
            if linha.startswith("frame="):
                try:
                    frames_atual = int(linha.split("=")[1])
                    pct = min(98.0, (frames_atual / frames_total) * 100) if frames_total > 0 else 0
                    if progresso_cb:
                        progresso_cb(pct, f"Renderizando… {frames_atual}/{int(frames_total)} frames")
                except Exception:
                    pass
            elif linha.startswith("out_time="):
                pass  # informação adicional, não usada aqui

    except FileNotFoundError:
        return {"sucesso": False, "erro": "ffmpeg não encontrado. Verifique se o FFmpeg está no PATH."}
    except Exception as e:
        if processo:
            try:
                processo.kill()
            except Exception:
                pass
        return {"sucesso": False, "erro": f"Erro inesperado: {str(e)}"}
    finally:
        if processo:
            try:
                if hasattr(processo.stdout, "close"):
                    processo.stdout.close()
            except Exception:
                pass
            try:
                processo.wait(timeout=10)
            except TypeError:
                processo.wait()
            except Exception:
                try:
                    processo.kill()
                    try:
                        processo.wait(timeout=5)
                    except TypeError:
                        processo.wait()
                except Exception:
                    pass
        import gc
        gc.collect()

    if processo.returncode != 0:
        return {
            "sucesso": False,
            "erro": f"FFmpeg retornou erro {processo.returncode}: {' | '.join(saida_recente)[-1000:]}",
        }

    if progresso_cb:
        progresso_cb(100.0, "Concluído!")

    return {
        "sucesso": True,
        "caminho_saida": caminho_saida,
        "duracao_s": duracao,
        "erro": "",
    }
