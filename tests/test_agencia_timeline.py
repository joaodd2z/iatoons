import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _agencia_dados import FALAS_HISTORIA, plano, transcricao  # noqa: E402
from modules.agencia.layout import ajustar_texto  # noqa: E402
from modules.agencia.qualidade import aprovado, gate_pre  # noqa: E402
from modules.agencia.timeline import (  # noqa: E402
    OpcoesEdicao, _dentro_de_palavra, construir_timeline, gerar_legendas_timeline, palavras_entre,
)
from modules.intro import IntervaloIntro  # noqa: E402
from modules.perfis_visuais import obter_perfil  # noqa: E402


def gate(p, tl, t, intro=None, outros=()):
    perfil = obter_perfil("toonmix")
    titulo = ajustar_texto(p.titulo, perfil.estilo_titulo_agencia, perfil.zonas.titulo)
    blocos = gerar_legendas_timeline(t, tl, 4)
    legendas = [(b, ajustar_texto(b.texto, perfil.estilo_legenda_agencia, perfil.zonas.legenda,
                                  alinhamento_vertical="topo")) for b in blocos]
    return {i.nome: i for i in gate_pre(p, tl, titulo, legendas, t, perfil, intro, list(outros))}


class TesteCorteEditorial(unittest.TestCase):
    def setUp(self):
        self.t = transcricao(FALAS_HISTORIA)

    def test_corte_cronologico_remove_silencio_inicial_sem_cortar_fala(self):
        tl = construir_timeline(plano(inicio=0.0, fim=23.0), self.t, None, OpcoesEdicao())
        self.assertEqual("normal", tl.tipo)
        self.assertAlmostEqual(2.0 - 0.12, tl.trechos[0].inicio, places=3)
        self.assertTrue(any(r["motivo"] == "silencio_inicial" for r in tl.removidos))
        for trecho in tl.trechos:
            palavras = palavras_entre(self.t, trecho.inicio - 1, trecho.fim + 1)
            self.assertIsNone(_dentro_de_palavra(trecho.inicio, palavras))
            self.assertIsNone(_dentro_de_palavra(trecho.fim, palavras))

    def test_pausa_morta_curta_sai_mas_acao_visual_fica(self):
        falas = [(2.0, 4.0, "Primeira fala aqui."), (7.5, 9.5, "Depois da espera."),
                 (20.0, 22.0, "Depois da ação longa.")]
        t = transcricao(falas)
        p = plano(inicio=1.9, fim=22.2)
        sem_video = construir_timeline(p, t, None, OpcoesEdicao())
        motivos = [r["motivo"] for r in sem_video.removidos]
        self.assertEqual(1, motivos.count("pausa_morta"))   # 3,5 s sai; 10,5 s fica (sem medir vídeo)
        # Com medição: a espera parada sai; a pausa longa com movimento (ação) permanece.
        com_video = construir_timeline(p, t, None, OpcoesEdicao(),
                                       movimento=lambda a, b: 0.3 if b < 10 else 12.0)
        removidos = [(round(r["inicio"], 2), round(r["fim"], 2)) for r in com_video.removidos
                     if r["motivo"] == "pausa_morta"]
        self.assertEqual([(4.38, 7.25)], removidos)

    def test_momento_forte_nunca_e_compactado(self):
        falas = [(2.0, 4.0, "Começo da cena."), (7.0, 9.0, "Reação muito forte!")]
        t = transcricao(falas)
        p = plano(inicio=1.9, fim=9.2, estrutura={"momento_forte": {"inicio": 3.5, "fim": 9.0}})
        tl = construir_timeline(p, t, None, OpcoesEdicao(), movimento=lambda a, b: 0.0)
        self.assertEqual(1, len(tl.trechos))

    def test_ausencia_de_intro(self):
        tl = construir_timeline(plano(), self.t, None, OpcoesEdicao())
        itens = gate(plano(), tl, self.t)
        self.assertTrue(itens["INTRO"].ok)

    def test_intro_detectada_e_removida_da_timeline(self):
        intro = IntervaloIntro(10.8, 13.8, True, True, 0.9, "teste")
        t = transcricao(FALAS_HISTORIA[:3] + FALAS_HISTORIA[4:])  # sem fala dentro da intro
        p = plano()
        tl = construir_timeline(p, t, intro, OpcoesEdicao())
        for trecho in tl.trechos:
            self.assertFalse(min(trecho.fim, intro.fim) - max(trecho.inicio, intro.inicio) > 0.05)
        self.assertTrue(any(r["motivo"] == "intro_removida" for r in tl.removidos))
        self.assertTrue(gate(p, tl, t, intro)["INTRO"].ok)
        # Sem remover (timeline construída sem a intro), o gate reprova.
        tl_errada = construir_timeline(p, t, None, OpcoesEdicao())
        self.assertFalse(gate(p, tl_errada, t, intro)["INTRO"].ok)

    def test_final_nao_termina_antes_da_consequencia(self):
        p = plano(inicio=1.5, fim=17.0, estrutura={"conclusao": {"inicio": 20.0, "fim": 22.5}})
        tl = construir_timeline(p, self.t, None, OpcoesEdicao())
        self.assertFalse(gate(p, tl, self.t)["FINAL"].ok)
        p_ok = plano(estrutura={"conclusao": {"inicio": 20.0, "fim": 22.5}})
        tl_ok = construir_timeline(p_ok, self.t, None, OpcoesEdicao())
        self.assertTrue(gate(p_ok, tl_ok, self.t)["FINAL"].ok)

    def test_duplicacao_detectada_no_gate(self):
        a, b = plano(), plano()
        tl = construir_timeline(a, self.t, None, OpcoesEdicao())
        self.assertFalse(gate(a, tl, self.t, outros=[a, b])["DUPLICACAO"].ok)


class TesteRemix(unittest.TestCase):
    def setUp(self):
        self.t = transcricao(FALAS_HISTORIA)
        self.p = plano(gancho={"inicio": 14.0, "fim": 16.5, "motivo": "grito"}, remix=True)

    def test_remix_gancho_primeiro_e_sem_repeticao(self):
        tl = construir_timeline(self.p, self.t, None, OpcoesEdicao())
        self.assertEqual("remix", tl.tipo)
        self.assertEqual("gancho", tl.trechos[0].funcao)
        self.assertAlmostEqual(14.0, tl.trechos[0].inicio)
        self.assertTrue(2.0 <= tl.trechos[0].duracao <= 6.0)
        self.assertLess(tl.trechos[1].inicio, 14.0)          # volta ao contexto
        historia = tl.trechos[1:]
        # ... → 14.0 → continua a partir de 16.5 (o gancho não se repete)
        self.assertTrue(any(abs(t.fim - 14.0) < 0.02 for t in historia))
        self.assertTrue(any(abs(t.inicio - 16.5) < 0.02 for t in historia))
        self.assertFalse(any(t.inicio < 15.0 < t.fim for t in historia))
        self.assertIn("retorno_gancho", [t.motivo_corte for t in historia])
        self.assertFalse(tl.gancho_repetido)

    def test_repeticao_solicitada_e_normalizada(self):
        self.p.repetir_gancho = True
        tl = construir_timeline(self.p, self.t, None, OpcoesEdicao())
        self.assertFalse(tl.gancho_repetido)
        self.assertFalse(any(t.inicio <= 14.0 and t.fim >= 16.5 for t in tl.trechos[1:]))

    def test_remix_desativado_mantem_cronologia(self):
        tl = construir_timeline(self.p, self.t, None, OpcoesEdicao(permitir_remix=False))
        self.assertEqual("normal", tl.tipo)

    def test_sincronizacao_das_legendas_apos_remix(self):
        tl = construir_timeline(self.p, self.t, None, OpcoesEdicao())
        legendas = gerar_legendas_timeline(self.t, tl, 4)
        self.assertTrue(legendas[0].texto.startswith("Socorro"))
        self.assertAlmostEqual(0.0, legendas[0].inicio, places=2)
        for bloco in legendas:
            fonte = tl.saida_para_fonte((bloco.inicio + bloco.fim) / 2)
            self.assertTrue(palavras_entre(self.t, fonte - 0.6, fonte + 0.6), bloco)
            self.assertLessEqual(bloco.fim, tl.duracao + 1e-6)
        for a, b in zip(legendas, legendas[1:]):
            self.assertLessEqual(a.fim, b.inicio + 1e-6)
        # A fala "Hoje vamos arrumar a casa." (fonte 2.0) aparece logo após o gancho.
        contexto = next(b for b in legendas if b.texto.startswith("Hoje"))
        offset = tl.offsets()[1]
        self.assertAlmostEqual(offset + (2.0 - tl.trechos[1].inicio), contexto.inicio, places=2)
        self.assertTrue(aprovado(list(gate(self.p, tl, self.t).values())))


class TesteTransicoes(unittest.TestCase):
    def test_hard_cut_quando_ha_fala_perto_do_corte(self):
        t = transcricao(FALAS_HISTORIA)
        p = plano(gancho={"inicio": 14.0, "fim": 16.5, "motivo": "grito"}, remix=True)
        tl = construir_timeline(p, t, None, OpcoesEdicao())
        entrada = tl.trechos[1]
        self.assertEqual("remix_gancho", entrada.motivo_corte)
        self.assertEqual("corte_seco", entrada.transicao)   # contexto começa falando
        self.assertIn("preserva diálogo", entrada.justificativa_transicao)
        self.assertAlmostEqual(sum(t.duracao for t in tl.trechos), tl.duracao)

    def test_dissolve_em_salto_temporal_com_silencio(self):
        falas = [(2.0, 5.0, "Primeira parte da história."), (30.0, 33.0, "Mais tarde naquele dia.")]
        t = transcricao(falas)
        p = plano(inicio=1.9, fim=35.0)
        p.segmentos = [{"inicio": 1.9, "fim": 7.0, "funcao": "contexto"},
                       {"inicio": 28.5, "fim": 35.0, "funcao": "consequencia"}]
        tl = construir_timeline(p, t, None, OpcoesEdicao())
        entrada = tl.trechos[1]
        self.assertEqual("salto_temporal", entrada.motivo_corte)
        self.assertEqual("dissolve", entrada.transicao)
        self.assertAlmostEqual(1.0, entrada.duracao_transicao, places=2)
        self.assertAlmostEqual(sum(x.duracao for x in tl.trechos) - 1.0, tl.duracao, places=3)
        legendas = gerar_legendas_timeline(t, tl, 4)
        segunda = next(b for b in legendas if b.texto.startswith("Mais"))
        self.assertAlmostEqual(tl.offsets()[1] + 1.5, segunda.inicio, places=2)

    def test_modo_hard_nunca_usa_dissolve(self):
        falas = [(2.0, 5.0, "Primeira parte da história."), (30.0, 33.0, "Mais tarde naquele dia.")]
        t = transcricao(falas)
        p = plano(inicio=1.9, fim=35.0)
        p.segmentos = [{"inicio": 1.9, "fim": 7.0}, {"inicio": 28.5, "fim": 35.0}]
        tl = construir_timeline(p, t, None, OpcoesEdicao(transition_mode="hard"))
        self.assertEqual({"corte_seco"}, {x.transicao for x in tl.trechos[1:]})

    def test_preset_de_transicao_padrao(self):
        opcoes = OpcoesEdicao()
        self.assertEqual(("agency", "dissolve", 1.0),
                         (opcoes.transition_mode, opcoes.transition_family, opcoes.transition_duration))


if __name__ == "__main__":
    unittest.main()
