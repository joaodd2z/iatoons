"""Renderizações REAIS do Modo Agência (FFmpeg), inspecionadas frame a frame.

Nenhum teste é aprovado só porque o FFmpeg retornou 0: a zona de vídeo de cada
frame é comparada com o fundo do perfil, e a sincronia é medida com um flash
branco + tom colocados no mesmo instante da fonte.
"""

import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _agencia_dados import falas_remix, gerar_fonte, plano, plano_remix, transcricao  # noqa: E402
from modules import preset as P  # noqa: E402
from modules.agencia.av import AjustesAgencia, fonte_ja_processada  # noqa: E402
from modules.agencia.pipeline import exportar_corte_agencia, gerar_capas_candidatas  # noqa: E402
from modules.agencia.qualidade import analisar_zona_video, classificar_zona  # noqa: E402
from modules.agencia.renderer import _cobrir  # noqa: E402
from modules.agencia.timeline import OpcoesEdicao  # noqa: E402
from modules.perfis_visuais import obter_perfil  # noqa: E402


PASTA = ROOT / "temp" / "teste_agencia_real"
TEMPOS = (0.0, 0.1, 0.25, 0.5, 1.0, 3.0)
RAPIDO = AjustesAgencia(preset_x264="ultrafast")
FALAS = falas_remix()


def frame(video, tempo, destino) -> Image.Image:
    cmd = ["ffmpeg", "-y", "-loglevel", "error"] + (["-ss", str(tempo)] if tempo else []) + [
        "-i", str(video), "-frames:v", "1", str(destino)]
    subprocess.run(cmd, check=True)
    return Image.open(destino).convert("RGB")


def fundo(perfil):
    return _cobrir(Image.open(perfil.fundo_path).convert("RGB"), P.SAIDA_LARGURA, P.SAIDA_ALTURA)


class TesteRenderReal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        shutil.rmtree(PASTA, ignore_errors=True)
        PASTA.mkdir(parents=True, exist_ok=True)

    def exportar(self, fonte, p, t, perfil="toonmix", **extra):
        extra.setdefault("gerar_capa", False)
        return exportar_corte_agencia(str(fonte), p, t, PASTA / "saida", perfil=perfil, ajustes=RAPIDO,
                                      tempos_verificacao=TEMPOS, **extra)

    def conferir_frames(self, saida, perfil_id, nome):
        """Inspeção independente do pipeline: zona de vídeo ≠ fundo em todos os tempos."""
        perfil = obter_perfil(perfil_id)
        ref = fundo(perfil)
        pasta = PASTA / "frames" / nome
        pasta.mkdir(parents=True, exist_ok=True)
        for tempo in TEMPOS:
            imagem = frame(saida, tempo, pasta / f"{int(tempo * 1000):05d}ms.png")
            self.assertEqual((1080, 1920), imagem.size)
            metricas = analisar_zona_video(imagem, ref, perfil.zonas.video)
            self.assertIsNone(classificar_zona(metricas), f"{nome} t={tempo}: {metricas}")
            self.assertGreater(metricas["diferenca_fundo"], 20, f"{nome} t={tempo}")
            # O template fora da zona de vídeo permanece o fundo do perfil (logo intacto).
            logo = (380, 1778, 700, 1855) if perfil_id == "toonmix" else (380, 1760, 700, 1860)
            diff = ImageStat.Stat(ImageChops.difference(imagem.crop(logo), ref.crop(logo))).mean
            self.assertLess(sum(diff) / 3, 12, f"{nome} t={tempo}: logo alterado")

    def _orientacao(self, nome, largura, altura, estrategia):
        fonte = gerar_fonte(PASTA / f"fonte_{nome}.mp4", largura, altura, 31.0)
        t = transcricao(FALAS, fonte, f"real-{nome}", 31.0)
        p = plano_remix(f"real-{nome}")
        r = self.exportar(fonte, p, t)
        self.assertTrue(r["sucesso"], r.get("erro"))
        gate = {g["nome"]: g for g in r["gate"]}
        self.assertTrue(gate["FIRST_FRAME"]["ok"])
        self.assertTrue(gate["IMAGEM"]["ok"])
        self.assertTrue(gate["REMIX"]["ok"], gate["REMIX"]["detalhe"])
        self.assertEqual("remix", r["timeline"].tipo)
        rel = r["relatorio"]
        self.assertEqual(("1080x1920", 60.0, "h264", "aac", "yuv420p"),
                         (rel["resolucao"], rel["fps"], rel["codec_video"], rel["codec_audio"], rel["pix_fmt"]))
        self.assertTrue(rel["faststart"])
        self.assertEqual(0.0, rel["inicio_video"])
        self.assertEqual(0.0, rel["inicio_audio"])
        for chave in ("bitrate_medio_kbps", "tamanho_bytes", "duracao"):
            self.assertGreater(rel[chave], 0)
        metricas = json.loads(Path(r["metricas"]).read_text(encoding="utf-8"))
        self.assertEqual(estrategia, metricas["enquadramento"]["estrategia"])
        self.assertTrue(fonte_ja_processada(r["caminho_saida"]))
        self.conferir_frames(r["caminho_saida"], "toonmix", nome)
        return r

    def test_video_horizontal_visivel_desde_o_primeiro_frame(self):
        self._orientacao("horizontal", 640, 360, "ajustar_desfoque")

    def test_video_vertical_visivel_desde_o_primeiro_frame(self):
        self._orientacao("vertical", 360, 640, "ajustar_desfoque")

    def test_video_quadrado_visivel_desde_o_primeiro_frame(self):
        self._orientacao("quadrado", 480, 480, "cobrir")

    def test_perfil_visual_diferente(self):
        fonte = gerar_fonte(PASTA / "fonte_perfil.mp4", 640, 360, 31.0)
        t = transcricao(FALAS, fonte, "real-perfil", 31.0)
        r = self.exportar(fonte, plano_remix("real-perfil"), t, perfil="ia_toons")
        self.assertTrue(r["sucesso"], r.get("erro"))
        self.assertIn("IA_TOONS", Path(r["caminho_saida"]).name)
        self.conferir_frames(r["caminho_saida"], "ia_toons", "perfil_ia_toons")

    def test_remix_com_dissolve_mantem_audio_e_video_sincronizados(self):
        # Flash branco + tom em 24,0–24,5 s na fonte; nenhuma outra parte tem som.
        fonte = gerar_fonte(PASTA / "fonte_sync.mp4", 640, 360, 38.0, flash=(24.0, 24.5))
        falas = [(2.0, 5.0, "Primeira parte da história."), (21.5, 23.5, "Mais tarde naquele dia."),
                 (25.2, 27.2, "Que susto enorme agora!"), (28.0, 29.5, "E ficou assim."),
                 (30.0, 32.0, "Ninguém entendeu nada."), (32.4, 34.6, "E a noite acabou assim.")]
        t = transcricao(falas, fonte, "real-sync", 38.0)
        p = plano(inicio=1.9, fim=35.0, video_id="real-sync",
                  gancho={"inicio": 25.2, "fim": 27.2, "motivo": "susto"}, remix=True)
        p.segmentos = [{"inicio": 1.9, "fim": 10.0, "funcao": "contexto"},
                       {"inicio": 20.0, "fim": 35.0, "funcao": "consequencia"}]
        r = self.exportar(fonte, p, t, opcoes=OpcoesEdicao(compactar_pausas=False))
        self.assertTrue(r["sucesso"], r.get("erro"))
        tl = r["timeline"]
        self.assertEqual("remix", tl.tipo)
        self.assertEqual(["inicio", "corte_seco", "dissolve"], [x.transicao for x in tl.trechos[:3]])
        self.assertAlmostEqual(tl.duracao, r["relatorio"]["duracao_video"], delta=0.05)
        # Instante esperado do flash na saída.
        indice = next(i for i, x in enumerate(tl.trechos) if x.inicio <= 24.0 < x.fim)
        esperado = tl.offsets()[indice] + (24.0 - tl.trechos[indice].inicio)
        saida = r["caminho_saida"]
        log = subprocess.run(["ffmpeg", "-hide_banner", "-i", saida, "-af",
                              "silencedetect=n=-35dB:d=0.3", "-f", "null", "-"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
        inicio_som = float(re.findall(r"silence_end: ([0-9.]+)", log)[0])
        self.assertAlmostEqual(esperado, inicio_som, delta=0.06)
        zona = obter_perfil("toonmix").zonas.video
        pasta = PASTA / "frames" / "sync"
        pasta.mkdir(parents=True, exist_ok=True)
        claro = frame(saida, esperado + 0.25, pasta / "flash.png").crop((zona.x, zona.y, zona.x2, zona.y2))
        antes = frame(saida, esperado - 0.4, pasta / "antes.png").crop((zona.x, zona.y, zona.x2, zona.y2))
        self.assertGreater(ImageStat.Stat(claro.convert("L")).mean[0], 200)
        self.assertLess(ImageStat.Stat(antes.convert("L")).mean[0], 170)
        # Legendas do SRT seguem a timeline remontada.
        srt = Path(saida).with_suffix(".srt").read_text(encoding="utf-8-sig")
        self.assertIn("00:00:00,000", srt)
        self.assertIn("Que susto", srt.split("\n\n")[0])

    def test_capa_com_tres_candidatos(self):
        fonte = gerar_fonte(PASTA / "fonte_capa.mp4", 640, 360, 31.0)
        t = transcricao(FALAS, fonte, "real-capa", 31.0)
        p = plano_remix("real-capa")
        capas = gerar_capas_candidatas(str(fonte), p, t, "toonmix", PASTA / "capas")
        self.assertEqual(3, len(capas))
        perfil = obter_perfil("toonmix")
        ref = fundo(perfil)
        for capa in capas:
            imagem = Image.open(capa["caminho"]).convert("RGB")
            self.assertEqual((1080, 1920), imagem.size)
            self.assertTrue(capa["legivel_miniatura"])
            self.assertGreaterEqual(capa["tamanho_fonte"], 90)
            self.assertIsNone(classificar_zona(analisar_zona_video(imagem, ref, perfil.zonas.video)))
            z = perfil.zonas.titulo
            area = (z.x, z.y, z.x2, z.y2)
            texto = sum(ImageStat.Stat(ImageChops.difference(imagem.crop(area), ref.crop(area))).mean) / 3
            self.assertGreater(texto, 5, "título da capa ausente")
        self.assertEqual(3, len({round(c["tempo_fonte"], 2) for c in capas}))

    def test_lote_com_multiplos_cortes_e_duplicado_bloqueado(self):
        fonte = gerar_fonte(PASTA / "fonte_lote.mp4", 640, 360, 63.0)
        t = transcricao(falas_remix() + falas_remix(31.0), fonte, "real-lote", 63.0)
        planos = [plano_remix("real-lote"), plano_remix("real-lote", 31.0)]
        planos[1].titulos[0].texto = "A SEGUNDA SITUAÇÃO"
        duplicado = plano_remix("real-lote")
        todos = planos + [duplicado]
        resultados = [self.exportar(fonte, p, t, outros_planos=todos) for p in todos]
        # O primeiro e o duplicado se bloqueiam mutuamente; só o corte distinto sai.
        self.assertTrue(resultados[1]["sucesso"], resultados[1].get("erro"))
        self.assertTrue(resultados[2]["revisao"])
        self.assertIn("DUPLICACAO", resultados[2]["erro"])
        self.assertEqual("revisão", duplicado.status)
        # Sem duplicado na fila, o lote exporta os dois cortes com nomes distintos.
        ok = [self.exportar(fonte, p, t, outros_planos=planos) for p in planos]
        self.assertTrue(all(r["sucesso"] for r in ok), [r.get("erro") for r in ok])
        caminhos = {r["caminho_saida"] for r in ok}
        self.assertEqual(2, len(caminhos))
        for r in ok:
            metricas = json.loads(Path(r["metricas"]).read_text(encoding="utf-8"))
            for chave in ("tipo_edicao", "duracao", "gancho_utilizado", "numero_segmentos",
                          "numero_cortes_internos", "transicoes_utilizadas", "titulo_escolhido",
                          "perfil_visual", "preset_av", "motivo_escolha"):
                self.assertIn(chave, metricas)
            self.assertEqual("AGENCY_AV_V1", metricas["preset_av"])
            self.assertEqual("remix", metricas["tipo_edicao"])
            self.assertIsNotNone(metricas["gancho_utilizado"])

    def test_corte_normal_nao_e_exportado_no_modo_agencia(self):
        fonte = gerar_fonte(PASTA / "fonte_normal.mp4", 640, 360, 31.0)
        t = transcricao(FALAS, fonte, "real-normal", 31.0)
        normal = plano(inicio=0.9, fim=29.1, video_id="real-normal")   # contínuo, sem gancho
        r = self.exportar(fonte, normal, t)
        self.assertFalse(r["sucesso"])
        self.assertTrue(r["revisao"])
        self.assertIn("REMIX", r["erro"])
        self.assertFalse(list((PASTA / "saida").glob("*real-normal*")))


if __name__ == "__main__":
    unittest.main()
