import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _agencia_dados import FALAS_HISTORIA, plano, transcricao  # noqa: E402
from modules.agencia import editorial  # noqa: E402
from modules.agencia.editorial import (  # noqa: E402
    PlanoAgencia, analisar_agencia, avaliar_titulo, caminho_cache_agencia, classificar_titulo,
    normalizar_titulos, validar_planos,
)
from modules.agencia.remix import validar_remix_real  # noqa: E402
from modules.agencia.timeline import OpcoesEdicao, construir_timeline, gerar_legendas_timeline  # noqa: E402
from modules.ganchos_ab import TrechoFonte, remapear_legendas  # noqa: E402
from modules.legendas import gerar_blocos_legenda  # noqa: E402
from modules.remix_narrativo import CandidatoRemix, SegmentoNarrativo  # noqa: E402
from modules.agencia.persistencia import carregar_lista_agencia, salvar_lista_agencia  # noqa: E402
from modules.config_ia import ConfigIA  # noqa: E402


def historia_valida(**extra):
    dado = {
        "descricao": "A arrumação vira caos.",
        "motivo_escolha": "Conflito claro com consequência.",
        "estrutura": {"preparacao": {"inicio": 2.0, "fim": 7.5}, "conflito": {"inicio": 8.0, "fim": 10.5},
                      "momento_forte": {"inicio": 14.0, "fim": 16.5}, "conclusao": {"inicio": 20.0, "fim": 22.5}},
        "segmentos": [{"inicio": 1.5, "fim": 23.0, "funcao": "historia"}],
        "remover": [],
        "gancho": {"inicio": 14.0, "fim": 16.5, "motivo": "grito inesperado"},
        "usar_remix": True,
        "remix_motivo": "o grito é mais forte que a abertura",
        "titulos": [{"texto": "Quem mexeu na casa?", "tipo": "curiosidade"},
                    {"texto": "A briga pela arrumação", "tipo": "conflito"},
                    {"texto": "Ninguém esperava essa bagunça", "tipo": "surpresa"}],
        "titulo_capa": "QUEM MEXEU?",
        "confianca": 0.8,
    }
    dado.update(extra)
    return dado


class ProvedorFalso:
    def __init__(self, resposta):
        self.resposta = resposta
        self.chamadas = 0

    def listar_modelos(self):
        return ["gemini-teste"]

    def analisar(self, modelo, prompt):
        self.chamadas += 1
        self.prompt = prompt
        return self.resposta


class TesteTitulos(unittest.TestCase):
    def test_classificacao_curiosidade_conflito_surpresa(self):
        self.assertEqual("curiosidade", classificar_titulo("O que ele escondeu?"))
        self.assertEqual("conflito", classificar_titulo("Peter contra o chefe"))
        self.assertEqual("surpresa", classificar_titulo("Ninguém esperava isso!"))

    def test_titulo_que_repete_primeira_fala_e_descartado(self):
        primeira = "Hoje vamos arrumar a casa."
        self.assertTrue(avaliar_titulo("HOJE VAMOS ARRUMAR A CASA", primeira))
        titulos, avisos = normalizar_titulos(
            [{"texto": "Hoje vamos arrumar a casa", "tipo": "curiosidade"},
             {"texto": "Quem mexeu na casa?", "tipo": "curiosidade"},
             {"texto": "A briga pela arrumação", "tipo": "conflito"}], primeira)
        self.assertEqual(2, len(titulos))
        self.assertTrue(any("repete a primeira fala" in a for a in avisos))

    def test_tipos_repetidos_sao_redistribuidos(self):
        titulos, _ = normalizar_titulos([
            {"texto": "Primeira opção curta", "tipo": "curiosidade"},
            {"texto": "Segunda opção curta", "tipo": "curiosidade"},
            {"texto": "Terceira opção curta", "tipo": "curiosidade"}])
        self.assertEqual(["curiosidade", "conflito", "surpresa"], [t.tipo for t in titulos])


class TesteValidacao(unittest.TestCase):
    def setUp(self):
        self.t = transcricao(FALAS_HISTORIA, video_id="agencia-editorial-1")

    def test_historia_valida_com_remix_e_tres_titulos(self):
        planos, rejeicoes = validar_planos([historia_valida()], self.t)
        self.assertEqual([], rejeicoes)
        p = planos[0]
        self.assertTrue(p.usar_remix)
        self.assertEqual({"curiosidade", "conflito", "surpresa"}, {t.tipo for t in p.titulos})
        self.assertIn("momento_forte", p.estrutura)
        self.assertEqual("agencia-editorial-1", p.video_id)

    def test_limites_sao_expandidos_para_nao_cortar_fala(self):
        self.assertEqual(2.0, editorial.ajustar_inicio_sem_cortar_fala(3.0, self.t))   # fala 2.0–4.5
        self.assertEqual(22.5, editorial.ajustar_fim_sem_cortar_fala(21.0, self.t))     # fala 20.0–22.5
        dado = historia_valida(segmentos=[{"inicio": 3.0, "fim": 23.0, "funcao": "historia"}])
        p = validar_planos([dado], self.t)[0][0]
        self.assertEqual(2.0, p.inicio)

    def test_ia_recusando_remix_rejeita_o_candidato(self):
        planos, rejeicoes = validar_planos([historia_valida(usar_remix=False)], self.t)
        self.assertEqual([], planos)
        self.assertIn("não é remix", rejeicoes[0])
        planos, _ = validar_planos([historia_valida(ordem_final=["contexto", "gancho"])], self.t)
        self.assertEqual([], planos)

    def test_gancho_fora_da_historia_rejeita_o_candidato(self):
        dado = historia_valida(gancho={"inicio": 60.0, "fim": 63.0, "motivo": "outra cena"})
        planos, rejeicoes = validar_planos([dado], self.t)
        self.assertEqual([], planos)
        self.assertIn("gancho", rejeicoes[0])

    def test_historias_distantes_nao_sao_unidas(self):
        dado = historia_valida(segmentos=[{"inicio": 2.0, "fim": 10.5}, {"inicio": 70.0, "fim": 80.0}])
        planos, rejeicoes = validar_planos([dado], self.t)
        for p in planos:   # nunca um plano cobrindo as duas cenas
            self.assertFalse(p.inicio < 10.5 and p.fim > 70.0)

    def test_resumo_do_episodio_e_reduzido_ao_bloco_continuo(self):
        falas = [(float(i), float(i) + 2.5, f"Fala número {i} continua.") for i in range(0, 200, 3)]
        t = transcricao(falas, video_id="agencia-resumo", duracao=220.0)
        dado = historia_valida(
            segmentos=[{"inicio": 0.0, "fim": 20.0}, {"inicio": 120.0, "fim": 150.0},
                       {"inicio": 160.0, "fim": 175.0}],
            gancho={"inicio": 138.0, "fim": 170.0, "motivo": "longo demais"},
            estrutura={"momento_forte": {"inicio": 140.0, "fim": 145.0}})
        planos, rejeicoes = validar_planos([dado], t)
        self.assertEqual([], rejeicoes)
        p = planos[0]
        self.assertGreaterEqual(p.inicio, 119.0)          # bloco contínuo com o momento forte
        self.assertLessEqual(p.fim, 177.5)
        self.assertTrue(p.gancho and 2.0 <= p.gancho["fim"] - p.gancho["inicio"] <= 6.5)
        self.assertTrue(any("cenas distantes" in a for a in p.avisos))
        self.assertTrue(any("reduzido" in a for a in p.avisos))

    def test_duplicados_sao_removidos(self):
        outro = historia_valida(confianca=0.5)
        planos, rejeicoes = validar_planos([historia_valida(), outro], self.t)
        self.assertEqual(1, len(planos))
        self.assertTrue(any("duplicada" in r for r in rejeicoes))


class TesteCacheIsolamento(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.object(editorial, "ANALISES_AGENCIA_DIR", Path(self.tmp.name))
        self.patch.start()
        # Uma chamada: estes testes tratam só de cache/isolamento (sem complemento).
        self.config = ConfigIA(modelo="gemini-teste", max_chamadas_episodio=1)

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_analise_usa_provedor_falso_e_depois_somente_cache(self):
        t = transcricao(FALAS_HISTORIA, video_id="agencia-cache-a")
        provedor = ProvedorFalso({"historias": [historia_valida()]})
        planos = analisar_agencia(t, self.config, provedor=provedor)
        self.assertEqual(1, provedor.chamadas)
        self.assertIn("SOMENTE REMIXES NARRATIVOS", provedor.prompt)
        self.assertIn("REMONTADAS", provedor.prompt)
        self.assertEqual(1, len(planos))

        class SemRede:
            def listar_modelos(self):
                raise AssertionError("Gemini não pode ser chamada com cache válido")
            analisar = listar_modelos

        planos_cache = analisar_agencia(t, self.config, provedor=SemRede())
        self.assertEqual(planos[0].titulos, planos_cache[0].titulos)

    def test_cache_de_outro_episodio_nunca_e_reutilizado(self):
        a = transcricao(FALAS_HISTORIA, video_id="agencia-cache-a")
        b = transcricao(FALAS_HISTORIA, video_id="agencia-cache-b")
        self.assertNotEqual(caminho_cache_agencia(a, self.config), caminho_cache_agencia(b, self.config))
        analisar_agencia(a, self.config, provedor=ProvedorFalso({"historias": [historia_valida()]}))
        # Mesmo forçando o arquivo do episódio A no caminho do episódio B, o conteúdo é recusado.
        destino_b = caminho_cache_agencia(b, self.config)
        destino_b.write_text(caminho_cache_agencia(a, self.config).read_text(encoding="utf-8"), encoding="utf-8")
        self.assertIsNone(editorial.carregar_cache_agencia(b, self.config))
        provedor_b = ProvedorFalso({"historias": []})
        self.assertEqual([], analisar_agencia(b, self.config, provedor=provedor_b))
        self.assertEqual(1, provedor_b.chamadas)

    def test_perfil_visual_nao_participa_do_cache(self):
        import inspect
        t = transcricao(FALAS_HISTORIA, video_id="agencia-cache-c")
        parametros = inspect.signature(caminho_cache_agencia).parameters
        self.assertNotIn("perfil", " ".join(parametros))
        self.assertEqual(caminho_cache_agencia(t, self.config), caminho_cache_agencia(t, self.config))

    def test_plano_de_outro_episodio_nao_e_carregado_da_lista(self):
        with tempfile.TemporaryDirectory() as pasta:
            video = Path(pasta) / "ep.mp4"
            video.write_bytes(b"0")
            with patch("modules.agencia.persistencia.LISTAS_AGENCIA_DIR", Path(pasta) / "listas"):
                salvar_lista_agencia(str(video), "ep-1", [plano(video_id="ep-1"), plano(video_id="ep-2")])
                carregados = carregar_lista_agencia(str(video), "ep-1")
                self.assertEqual(["ep-1"], [p.video_id for p in carregados])
                with self.assertRaises(ValueError):
                    carregar_lista_agencia(str(video), "ep-3")


# ─── Regressão: Modo Agência = SOMENTE remix narrativo ───────────────────────

def episodio_longo(video_id):
    """~10 min; blocos de 12 falas de 4 s (uma frase cada), separados por pausa."""
    falas = []
    for bloco in range(10):
        base = bloco * 62.0
        for i in range(12):
            inicio = base + 1 + i * 4.8
            falas.append((inicio, inicio + 4.0, f"B{bloco}F{i} alfa{bloco}{i} beta{bloco}{i} gama{bloco}{i}."))
    return transcricao(falas, video_id=video_id, duracao=640.0)


def remix_em(inicio, confianca=0.8, **extra):
    """Remix válido numa história de ``inicio`` a ``inicio+57``: gancho na 7ª fala."""
    gi = inicio + 28.8
    dado = historia_valida(
        segmentos=[{"inicio": inicio, "fim": gi, "funcao": "contexto"},
                   {"inicio": gi, "fim": gi + 4.0, "funcao": "chegada_ao_gancho"},
                   {"inicio": gi + 4.0, "fim": inicio + 57.0, "funcao": "consequencia"}],
        gancho={"inicio": gi, "fim": gi + 4.0, "motivo": "virada"}, estrutura={},
        ordem_final=["gancho", "contexto", "chegada_ao_gancho", "consequencia"],
        justificativa_remix="A virada antecipada prende a atenção.", confianca=confianca,
        titulos=[{"texto": f"O que houve no minuto {int(inicio) // 60}?", "tipo": "curiosidade"},
                 {"texto": f"A briga do bloco {int(inicio)}", "tipo": "conflito"},
                 {"texto": f"Ninguém esperava o fim {int(inicio)}", "tipo": "surpresa"}])
    dado.update(extra)
    return dado


def normal_em(inicio):
    """Corte NORMAL (contínuo, sem gancho) — proibido no Modo Agência."""
    dado = remix_em(inicio)
    dado.pop("gancho")
    dado["ordem_final"] = []
    dado["segmentos"] = [{"inicio": inicio, "fim": inicio + 57.0, "funcao": "historia"}]
    return dado


class ProvedorSequencial(ProvedorFalso):
    def __init__(self, respostas):
        super().__init__(None)
        self.respostas = list(respostas)
        self.prompts = []

    def analisar(self, modelo, prompt):
        self.chamadas += 1
        self.prompts.append(prompt)
        return self.respostas.pop(0) if self.respostas else {"historias": []}


class _BaseCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.object(editorial, "ANALISES_AGENCIA_DIR", Path(self.tmp.name))
        self.patch.start()
        # Nenhum remix salvo por padrão (e nenhum acesso a análises normais).
        self.patch_remix = patch("modules.remix_narrativo.analisar_remixes", return_value=[])
        self.mock_remix = self.patch_remix.start()

    def tearDown(self):
        self.patch_remix.stop()
        self.patch.stop()
        self.tmp.cleanup()


class TesteSomenteRemix(_BaseCache):
    # 1. corte NORMAL nunca aparece no Modo Agência
    def test_corte_normal_nunca_aparece(self):
        t = episodio_longo("rx-1")
        planos, rejeicoes = validar_planos([normal_em(1), normal_em(63)], t)
        self.assertEqual([], planos)
        self.assertTrue(all("não é remix" in r for r in rejeicoes))
        lista = analisar_agencia(t, ConfigIA(modelo="gemini-teste", max_chamadas_episodio=1),
                                 provedor=ProvedorSequencial([{"historias": [normal_em(1)]}]))
        self.assertEqual([], lista)

    # 2. structural_local nunca completa quantidade
    def test_nenhum_complemento_estrutural_ou_normal(self):
        self.assertFalse(hasattr(editorial, "planos_estruturais"))
        self.assertFalse(hasattr(editorial, "plano_de_candidato_ia"))
        self.assertFalse(hasattr(editorial, "completar_minimo"))
        t = episodio_longo("rx-2")
        lista = analisar_agencia(t, ConfigIA(modelo="gemini-teste", max_chamadas_episodio=1),
                                 provedor=ProvedorSequencial([{"historias": [remix_em(1)]}]))
        self.assertEqual(1, len(lista))
        self.assertEqual({"gemini_agencia"}, {p.origem for p in lista})

    # 3. remix que virou intervalo contínuo é rejeitado
    def test_remix_que_vira_intervalo_continuo_e_rejeitado(self):
        t = episodio_longo("rx-3")
        # Gancho logo no começo: não há contexto anterior para reorganizar.
        cedo = remix_em(1)
        cedo["gancho"] = {"inicio": 1.0, "fim": 5.0, "motivo": "abre a cena"}
        planos, rejeicoes = validar_planos([cedo], t)
        self.assertEqual([], planos)
        self.assertIn("não é posterior", rejeicoes[0])
        # Resposta tipo "172–206": resumo cujo bloco contínuo aproveitável não tem o gancho.
        resumo = remix_em(1)
        resumo["segmentos"] = [{"inicio": 1.0, "fim": 20.0}, {"inicio": 300.0, "fim": 340.0}]
        resumo["gancho"] = {"inicio": 125.8, "fim": 129.8, "motivo": "fora"}
        self.assertEqual([], validar_planos([resumo], t)[0])
        # E a timeline cronológica de um plano com gancho nunca passa no VALIDAR_REMIX.
        p = validar_planos([remix_em(1)], t)[0][0]
        continua = construir_timeline(p, t, None, OpcoesEdicao(permitir_remix=False))
        self.assertEqual("normal", continua.tipo)
        self.assertTrue(validar_remix_real(p, continua))

    # 4. remix com gancho posterior + contexto anterior é aceito
    def test_remix_real_e_aceito(self):
        t = episodio_longo("rx-4")
        p = validar_planos([remix_em(63)], t)[0][0]
        tl = construir_timeline(p, t, None, OpcoesEdicao())
        self.assertEqual([], validar_remix_real(p, tl, t, gerar_legendas_timeline(t, tl, 4)))
        self.assertEqual("gancho", tl.trechos[0].funcao)
        self.assertLess(tl.trechos[1].inicio, tl.trechos[0].inicio)
        self.assertGreaterEqual(len({(x.inicio, x.fim) for x in tl.trechos}), 2)

    # 5. gancho sem repetição
    def test_gancho_sem_repeticao(self):
        t = episodio_longo("rx-5")
        p = validar_planos([remix_em(1)], t)[0][0]
        tl = construir_timeline(p, t, None, OpcoesEdicao())
        gi, gf = tl.gancho
        self.assertFalse(tl.gancho_repetido)
        self.assertFalse(any(x.inicio < gf - 0.05 and x.fim > gi + 0.05 for x in tl.trechos[1:]))
        self.assertTrue(any(abs(x.fim - gi) < 0.05 for x in tl.trechos[1:]))
        self.assertTrue(any(abs(x.inicio - gf) < 0.05 for x in tl.trechos[1:]))

    # 6. legendas permanecem sincronizadas (e legendas não recalculadas são detectadas)
    def test_legendas_recalculadas_para_a_ordem_remixada(self):
        t = episodio_longo("rx-6")
        p = validar_planos([remix_em(1)], t)[0][0]
        tl = construir_timeline(p, t, None, OpcoesEdicao())
        certas = gerar_legendas_timeline(t, tl, 4)
        self.assertEqual([], validar_remix_real(p, tl, t, certas))
        gi, gf = tl.gancho
        self.assertTrue(certas[0].texto.startswith("B0F6"))   # a fala do gancho abre o vídeo
        cronologicas = remapear_legendas(gerar_blocos_legenda(t, 4), [TrechoFonte(p.inicio, p.fim)])
        self.assertTrue(any("recalculadas" in x for x in validar_remix_real(p, tl, t, cronologicas)))

    # 8. Gemini retorna 3 remixes e 4 normais → resultado final = 3 (sem inventar)
    def test_tres_remix_e_quatro_normais_resultam_em_tres(self):
        t = episodio_longo("rx-8")
        historias = [remix_em(1), remix_em(125), remix_em(249),
                     normal_em(63), normal_em(187), normal_em(311), normal_em(373)]
        lista = analisar_agencia(t, ConfigIA(modelo="gemini-teste", max_chamadas_episodio=1),
                                 provedor=ProvedorSequencial([{"historias": historias}]))
        self.assertEqual(3, len(lista))
        self.assertTrue(all(p.usar_remix and p.gancho for p in lista))
        self.assertIn("3 remixes aprovados", lista.mensagem)
        self.assertGreaterEqual(lista.diagnostico.candidatos_rejeitados,4)
        self.assertGreaterEqual(len({r['candidate_id'] for r in lista.rejeicoes if isinstance(r,dict)}),4)

    # Recovery local nunca repete a analise global para preencher a meta.
    def test_complemento_local_preserva_tres_sem_inventar_cinco(self):
        t = episodio_longo("rx-9")
        provedor = ProvedorSequencial([
            {"historias": [remix_em(1), remix_em(125), remix_em(249), normal_em(63)]},
            {"historias": [remix_em(373), remix_em(435), remix_em(1)]},   # último duplicado
        ])
        lista = analisar_agencia(t, ConfigIA(modelo="gemini-teste", max_cortes=5), provedor=provedor)
        self.assertEqual(1, provedor.chamadas)
        self.assertEqual("AUTO RECOVERY V2", lista.diagnostico.modo)
        self.assertEqual(3, len(lista))
        self.assertTrue(all(p.usar_remix for p in lista))

    # 10. cache normal nunca é usado como remix
    def test_cache_de_analise_normal_nunca_e_consultado(self):
        t = episodio_longo("rx-10")
        with patch("modules.analise_ia.analisar_transcricao",
                   side_effect=AssertionError("análise normal não pode ser usada")):
            final, _ = editorial.completar_com_remixes_salvos([], t, ConfigIA(modelo="gemini-teste"))
        self.assertEqual([], final)

    # 11. cache remix válido pode ser reutilizado (e inválido é rejeitado)
    def test_remix_salvo_valido_e_reutilizado(self):
        t = episodio_longo("rx-11")
        valido = CandidatoRemix(
            ["O que houve?", "A briga", "Ninguém esperava"], "história", 153.8, 157.8, "virada",
            [SegmentoNarrativo(125.0, 153.8, "contexto"), SegmentoNarrativo(153.8, 182.0, "consequencia")],
            "justifica", "mesma cena", 0.7)
        continuo = CandidatoRemix(
            ["Outro título", "Mais um", "Terceiro"], "história", 373.0, 377.0, "começo",
            [SegmentoNarrativo(373.0, 377.0, "gancho"), SegmentoNarrativo(377.0, 430.0, "resto")],
            "justifica", "mesma cena", 0.7)
        self.mock_remix.return_value = [valido, continuo]
        final, rejeicoes = editorial.completar_com_remixes_salvos([], t, ConfigIA(modelo="gemini-teste"))
        self.assertEqual([125.0], [p.inicio for p in final], rejeicoes)
        self.assertEqual("remix", final[0].origem)
        self.assertEqual(1, len(rejeicoes))

    # Lista antiga salva com corte estrutural/normal que recebeu gancho local: bloqueado.
    def test_origem_estrutural_ou_normal_e_bloqueada_mesmo_com_gancho(self):
        t = episodio_longo("rx-origem")
        p = validar_planos([remix_em(63)], t)[0][0]
        tl = construir_timeline(p, t, None, OpcoesEdicao())
        self.assertEqual([], validar_remix_real(p, tl))
        for origem in ("estrutural_local", "analise_ia", "manual"):
            p.origem = origem
            problemas = validar_remix_real(p, tl)
            self.assertTrue(problemas and "não é remix" in problemas[0], origem)

    def test_gemini_sobrecarregada_503_e_repetida_mas_429_nao(self):
        from modules.analise_ia import ErroGemini
        provedor = object.__new__(editorial.ProvedorGeminiAgencia)
        provedor.ESPERAS_RETENTATIVA = (0, 0, 0)
        respostas = [ErroGemini("indisponível", codigo=503), ErroGemini("indisponível", codigo=503),
                     {"historias": []}]

        def uma_vez(_modelo, _prompt):
            item = respostas.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        provedor._analisar_uma_vez = uma_vez
        self.assertEqual({"historias": []}, provedor.analisar("m", "p"))
        tentativas = []

        def cota(_modelo, _prompt):
            tentativas.append(1)
            raise ErroGemini("cota", codigo=429)

        provedor._analisar_uma_vez = cota
        with self.assertRaises(ErroGemini):
            provedor.analisar("m", "p")
        self.assertEqual(1, len(tentativas))

    def test_modelo_sobrecarregado_passa_para_modelo_de_reserva(self):
        from modules.analise_ia import ErroGemini
        t = episodio_longo("rx-reserva")

        class Sobrecarga(ProvedorSequencial):
            def listar_modelos(self):
                return ["gemini-3.5-flash", "gemini-3.6-flash", "gemini-3.1-flash-lite"]

            def analisar(self, modelo, prompt):
                self.modelos = getattr(self, "modelos", []) + [modelo]
                if modelo == "gemini-3.5-flash":
                    raise ErroGemini("alta demanda", codigo=503)
                return super().analisar(modelo, prompt)

        provedor = Sobrecarga([{"historias": [remix_em(1)]}])
        lista = analisar_agencia(t, ConfigIA(modelo="gemini-3.5-flash", max_chamadas_episodio=2),
                                 provedor=provedor)
        # A reserva automática usa só Lite (500/dia); o 3.6-flash (20/dia) não é gasto.
        # Reserva: primeiro UM outro Flash (qualidade), o Lite só em último caso.
        self.assertEqual(["gemini-3.5-flash", "gemini-3.6-flash"], provedor.modelos)
        self.assertEqual((20,), editorial.ProvedorGeminiAgencia.ESPERAS_RETENTATIVA)
        self.assertEqual(1, len(lista))

    def test_remix_com_15s_ou_menos_e_rejeitado(self):
        # Cena isolada (silêncio longo antes e depois): o reparo não tem o que acrescentar.
        t = transcricao([(40.0, 44.0, "Alfa um dois três."), (44.8, 48.8, "Beta quatro cinco seis."),
                         (49.6, 53.6, "Gama sete oito nove!")], video_id="rx-curto", duracao=120.0)
        curto = remix_em(40.0)
        curto["segmentos"] = [{"inicio": 40.0, "fim": 53.6, "funcao": "contexto"}]
        curto["gancho"] = {"inicio": 49.6, "fim": 53.6, "motivo": "virada"}
        planos, rejeicoes = validar_planos([curto], t)   # ~13,6 s de vídeo final
        self.assertEqual([], planos)
        self.assertIn("duração", rejeicoes[0])

    def test_fragmento_sem_contexto_nao_e_completado_com_falas_vizinhas(self):
        # O reparo que "completava" histórias com falas vizinhas gerava remixes sem sentido.
        t = episodio_longo("rx-sem-reparo")
        cedo = remix_em(1)
        cedo["segmentos"] = [{"inicio": 20.2, "fim": 44.0, "funcao": "contexto"}]
        cedo["gancho"] = {"inicio": 25.0, "fim": 29.0, "motivo": "virada"}
        planos, rejeicoes = validar_planos([cedo], t)
        self.assertEqual([], planos)
        self.assertIn("não é posterior", rejeicoes[0])
        self.assertFalse(hasattr(editorial, "_reparar_contexto"))

    def test_duracao_minima_final_e_mais_de_20s(self):
        from modules.agencia.remix import DURACAO_MINIMA_FINAL
        self.assertGreater(DURACAO_MINIMA_FINAL, 20.0)

    def test_selecao_prefere_historias_longas(self):
        curto, longo = editorial.PlanoAgencia(0, 25), editorial.PlanoAgencia(100, 160)
        curto.segmentos = [{"inicio": 0, "fim": 25}]
        longo.segmentos = [{"inicio": 100, "fim": 160}]
        curto.confianca = longo.confianca = 0.8
        self.assertGreater(editorial.pontuacao_qualidade(longo), editorial.pontuacao_qualidade(curto))

    def test_gancho_curto_e_ampliado_com_a_frase_anterior(self):
        t = episodio_longo("rx-gancho-curto")
        dado = remix_em(1)
        dado["gancho"] = {"inicio": 29.8, "fim": 30.8, "motivo": "curto"}   # 1 s
        planos, rejeicoes = validar_planos([dado], t)
        self.assertEqual([], rejeicoes)
        g = planos[0].gancho
        self.assertTrue(2.0 <= g["fim"] - g["inicio"] <= 6.0)

    # 12. quantidade máxima continua 10
    def test_maximo_dez(self):
        t = episodio_longo("rx-12")
        historias = [remix_em(1 + k * 62, confianca=0.5 + k * 0.04) for k in range(10)]
        historias.append(remix_em(1, confianca=0.99))   # duplicado
        lista = analisar_agencia(t, ConfigIA(modelo="gemini-teste", max_cortes=10),
                                 provedor=ProvedorSequencial([{"historias": historias}]))
        self.assertEqual(10, len(lista))
        self.assertEqual(10, editorial.quantidade_alvo(40))
        self.assertEqual(3, editorial.quantidade_alvo(1))


if __name__ == "__main__":
    unittest.main()
