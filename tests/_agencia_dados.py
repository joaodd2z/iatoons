"""Dados sintéticos compartilhados pelos testes do Modo Agência (sem rede, sem Gemini)."""

import subprocess
from pathlib import Path

from modules.agencia.editorial import PlanoAgencia, TituloOpcao
from modules.transcricao import PalavraTranscrita, SegmentoTranscricao, Transcricao


def fala(inicio: float, fim: float, texto: str) -> SegmentoTranscricao:
    palavras = texto.split()
    passo = (fim - inicio) / len(palavras)
    return SegmentoTranscricao(inicio, fim, texto, [
        PalavraTranscrita(round(inicio + i * passo, 3), round(inicio + (i + 1) * passo - 0.02, 3), " " + p, 0.9)
        for i, p in enumerate(palavras)
    ])


def transcricao(falas, video_path="inexistente.mp4", video_id="agencia-teste", duracao=120.0) -> Transcricao:
    return Transcricao(str(video_path), video_id, "small", "pt", "pt", 1.0, duracao, "agora",
                       [fala(*item) for item in falas])


FALAS_HISTORIA = [
    (2.0, 4.5, "Hoje vamos arrumar a casa."),
    (5.0, 7.5, "Ninguém vai atrapalhar isso."),
    (8.0, 10.5, "Espera, quem mexeu aqui?"),
    (11.0, 13.5, "Eu não acredito nisso!"),
    (14.0, 16.5, "Socorro, é um absurdo total!"),
    (17.0, 19.5, "E agora ficou tudo pior."),
    (20.0, 22.5, "Pelo menos aprendemos algo."),
]


def plano(inicio=1.5, fim=23.0, gancho=None, remix=False, video_id="agencia-teste", **extra) -> PlanoAgencia:
    return PlanoAgencia(
        inicio=inicio, fim=fim,
        titulos=[TituloOpcao("QUEM MEXEU NA CASA?", "curiosidade"),
                 TituloOpcao("A BRIGA PELA ARRUMAÇÃO", "conflito"),
                 TituloOpcao("NINGUÉM ESPERAVA ESSA BAGUNÇA", "surpresa")],
        segmentos=[{"inicio": inicio, "fim": fim, "funcao": "historia"}],
        gancho=gancho, usar_remix=remix, video_id=video_id, titulo_capa="QUEM MEXEU?",
        **extra,
    )


def falas_remix(deslocamento: float = 0.0):
    """Mini-história de ~29 s: contexto (1–12,6 s) → gancho (13,4–16 s) → consequência."""
    base = [(1.0, 3.0, "Olha o que apareceu aqui."), (3.3, 5.8, "Eu não acredito nisso!"),
            (6.2, 8.6, "Quem deixou isso na sala?"), (9.0, 11.4, "Ninguém vai assumir agora."),
            (11.8, 12.6, "Espera."), (13.4, 16.0, "Socorro, que absurdo total!"),
            (16.4, 18.4, "Acabou tudo agora."), (18.8, 20.6, "Vamos ter que limpar."),
            (21.0, 23.4, "Ninguém ajudou em nada."), (23.8, 26.0, "Que dia horrível."),
            (26.4, 28.8, "Amanhã tem mais.")]
    return [(a + deslocamento, b + deslocamento, texto) for a, b, texto in base]


def plano_remix(video_id: str, deslocamento: float = 0.0) -> PlanoAgencia:
    d = deslocamento
    return plano(inicio=0.9 + d, fim=29.1 + d, video_id=video_id, remix=True,
                 gancho={"inicio": 13.4 + d, "fim": 16.0 + d, "motivo": "grito"})


def gerar_fonte(caminho: Path, largura: int, altura: int, duracao: float = 25.0,
                flash: tuple[float, float] | None = None, fps: int = 30) -> Path:
    """Vídeo com movimento contínuo e áudio; ``flash`` = quadro branco + tom no intervalo."""
    caminho.parent.mkdir(parents=True, exist_ok=True)
    filtro_v = "[0:v]null[v]"
    filtro_a = "[1:a]volume=0.4[a]"
    if flash:
        a, b = flash
        filtro_v = (f"[0:v]drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill:"
                    f"enable='between(t,{a},{b})'[v]")
        filtro_a = f"[1:a]volume='if(between(t,{a},{b}),0.8,0)':eval=frame[a]"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"testsrc2=size={largura}x{altura}:rate={fps}",
        "-f", "lavfi", "-i", "sine=frequency=800:sample_rate=48000",
        "-filter_complex", f"{filtro_v};{filtro_a}", "-map", "[v]", "-map", "[a]",
        "-t", str(duracao), "-c:v", "libx264", "-preset", "ultrafast", "-g", str(fps * 4),
        "-pix_fmt", "yuv420p", "-c:a", "aac", str(caminho),
    ], check=True)
    return caminho
