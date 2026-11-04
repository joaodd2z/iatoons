import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from modules import analise_ia, config_ia, credenciais
from modules.analise_ia import (CandidatoIA, ErroGemini, _erro_seguro,
                                _prompt, _analisar_transcricao_legado as analisar_transcricao,
                                otimizar_gancho_local)
from modules.config_ia import ConfigIA
from modules.transcricao import SegmentoTranscricao, Transcricao
from modules.intro import IntervaloIntro, salvar_intro


def transcricao_teste():
    segmentos = [
        SegmentoTranscricao(float(i), float(i + 10), f"Fala contextual número {i // 10}.")
        for i in range(0, 240, 10)
    ]
    return Transcricao("episodio.mp4", "video-abc", "small", "pt", "pt", 1.0,
                       240.0, "2026-01-01T00:00:00Z", segmentos)


class ProvedorSimulado:
    def __init__(self, respostas=None):
        self.respostas = list(respostas or [])
        self.chamadas = 0
        self.prompts = []

    def listar_modelos(self):
        return ["gemini-simulado"]

    def analisar(self, modelo, prompt):
        self.chamadas += 1
        self.ultimo_prompt = prompt
        self.prompts.append(prompt)
        return self.respostas.pop(0) if self.respostas else {"candidatos": []}


class TesteBaselineContextualLegado(unittest.TestCase):
    """Referencia dos helpers legados; o fluxo publico universal tem suite propria."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.patch_cache = patch.object(analise_ia, "ANALISES_DIR", Path(self.tmp.name) / "analises")
        self.patch_cache.start()
        self.addCleanup(self.patch_cache.stop)
        self.patch_intro = patch("modules.intro.INTROS_DIR", Path(self.tmp.name) / "intros")
        self.patch_intro.start()
        self.addCleanup(self.patch_intro.stop)
        self.config = ConfigIA(modelo="gemini-simulado", max_chamadas_episodio=2,
                               max_cortes=3, janela_segundos=130, sobreposicao_segundos=10)

    def test_resposta_simulada_gera_tres_titulos_contextuais(self):
        resposta = {"candidatos": [{
            "inicio": 1.2, "fim": 69.0, "descricao": "A situação sai do controle.",
            "motivo": "Reviravolta", "titulos": ["Plano infalível", "Deu tudo errado", "A pior ideia"],
        }]}
        provedor = ProvedorSimulado([resposta, {"candidatos": []}])
        candidatos = analisar_transcricao(transcricao_teste(), self.config, provedor, usar_cache=False)
        self.assertEqual(1, len(candidatos))
        self.assertEqual(3, len(candidatos[0].titulos))
        self.assertEqual("PLANO INFALÍVEL", candidatos[0].titulos[0])
        self.assertIn("[0.000 --> 10.000]", provedor.prompts[0])

    def test_rejeita_intervalo_curto_e_sobreposicao_excessiva(self):
        resposta = {"candidatos": [
            {"inicio": 0, "fim": 30, "descricao": "curto", "titulos": ["a", "b", "c"]},
            {"inicio": 0, "fim": 80, "descricao": "válido", "titulos": ["d", "e", "f"]},
            {"inicio": 10, "fim": 90, "descricao": "duplicado", "titulos": ["g", "h", "i"]},
        ]}
        candidatos = analisar_transcricao(transcricao_teste(), self.config,
                                           ProvedorSimulado([resposta, {"candidatos": []}]),
                                           usar_cache=False)
        self.assertEqual(1, len(candidatos))
        self.assertGreaterEqual(candidatos[0].duracao, 60)

    def test_limite_de_chamadas_e_cache(self):
        provedor = ProvedorSimulado([{"candidatos": []}, {"candidatos": []}])
        transcricao = transcricao_teste()
        analisar_transcricao(transcricao, self.config, provedor)
        self.assertLessEqual(provedor.chamadas, 2)
        segundo = ProvedorSimulado()
        analisar_transcricao(transcricao, self.config, segundo)
        self.assertEqual(0, segundo.chamadas)

    def test_modelo_indisponivel_nao_faz_geracao(self):
        provedor = ProvedorSimulado()
        self.config.modelo = "outro-modelo"
        with self.assertRaises(ErroGemini):
            analisar_transcricao(transcricao_teste(), self.config, provedor, usar_cache=False)
        self.assertEqual(0, provedor.chamadas)

    def test_429_informa_espera_sem_retry(self):
        class Falha(Exception):
            code = 429
        erro = _erro_seguro(Falha("RESOURCE_EXHAUSTED retryDelay: 17 seconds"))
        self.assertEqual(429, erro.codigo)
        self.assertEqual(17, erro.aguardar_segundos)

    def test_video_curto_nao_consume_chamada(self):
        transcricao = transcricao_teste()
        transcricao.duracao = 51
        transcricao.segmentos = transcricao.segmentos[:5]
        provedor = ProvedorSimulado()
        with self.assertRaisesRegex(ValueError, "menos de 60 segundos"):
            analisar_transcricao(transcricao, self.config, provedor, usar_cache=False)
        self.assertEqual(0, provedor.chamadas)

    def test_otimizacao_local_nao_corta_fala(self):
        transcricao = transcricao_teste()
        candidato = CandidatoIA(5.0, 80.0, "Cena", ["A", "B", "C"])
        otimizar_gancho_local(candidato, transcricao)
        self.assertEqual(0.0, candidato.inicio)
        self.assertEqual(5.0, candidato.inicio_original)
        self.assertIn("não cortar", candidato.gancho)

    def test_remove_apenas_silencio_curto_antes_da_fala(self):
        transcricao = transcricao_teste()
        transcricao.segmentos[0].inicio = 2.0
        candidato = CandidatoIA(0.0, 80.0, "Cena", ["A", "B", "C"])
        otimizar_gancho_local(candidato, transcricao)
        self.assertEqual(2.0, candidato.inicio)
        self.assertIn("silêncio", candidato.gancho)

    def test_corte_curto_excepcional_e_aceito_sem_forcar_60(self):
        resposta = {"candidatos": [{
            "inicio_original": 0, "inicio": 0, "fim": 50,
            "descricao": "Piada completa", "gancho": "Pergunta inesperada.",
            "excepcional_curto": True, "titulos": ["um", "dois", "três"],
        }]}
        candidatos = analisar_transcricao(transcricao_teste(), self.config,
                                           ProvedorSimulado([resposta, {"candidatos": []}]),
                                           usar_cache=False)
        self.assertEqual(1, len(candidatos))
        self.assertEqual(50, candidatos[0].duracao)

    def test_cenas_completas_de_48_e_51_segundos_nao_sao_descartadas(self):
        resposta = {"candidatos": [
            {"inicio": 0, "fim": 48.8, "descricao": "Cena completa 1",
             "titulos": ["Um A", "Um B", "Um C"]},
            {"inicio": 60, "fim": 111.4, "descricao": "Cena completa 2",
             "titulos": ["Dois A", "Dois B", "Dois C"]},
        ]}
        config = ConfigIA(modelo="gemini-simulado", max_cortes=10,
                          max_chamadas_episodio=1, janela_segundos=120,
                          sobreposicao_segundos=10)
        resultado = analisar_transcricao(transcricao_teste(), config,
                                         ProvedorSimulado([resposta]), usar_cache=False)
        self.assertEqual(2, len(resultado))
        self.assertEqual(0, resultado.diagnostico.rejeitada)
        self.assertTrue(all(c.excepcional_curto for c in resultado))

    def test_nucleo_curto_e_expandido_em_limites_de_fala(self):
        resposta = {"candidatos": [{
            "inicio_original": 80, "inicio": 90, "fim": 111,
            "descricao": "Núcleo de uma cena completa", "gancho": "Gancho tardio",
            "titulos": ["Um", "Dois", "Três"],
        }]}
        resultado = analisar_transcricao(transcricao_teste(), self.config,
                                         ProvedorSimulado([resposta, {"candidatos": []}]),
                                         usar_cache=False)
        self.assertEqual(1, len(resultado))
        self.assertGreaterEqual(resultado[0].duracao, 45)
        self.assertLessEqual(resultado[0].duracao, 120)
        self.assertEqual(80, resultado[0].inicio_original)

    def test_gancho_invalido_nao_elimina_cena_cronologica(self):
        resposta = {"candidatos": [{
            "inicio_original": 20, "inicio": 35, "fim": 100,
            "descricao": "Cena válida", "gancho": "deslocado",
            "titulos": ["A", "B", "C"],
        }]}
        resultado = analisar_transcricao(transcricao_teste(), self.config,
                                         ProvedorSimulado([resposta, {"candidatos": []}]),
                                         usar_cache=False)
        self.assertEqual(1, len(resultado))
        self.assertEqual(20, resultado[0].inicio)

    def test_sobreposicao_com_intro_preserva_parte_narrativa(self):
        transcricao = transcricao_teste()
        salvar_intro(transcricao, IntervaloIntro(20, 50, ativo=True))
        resposta = {"candidatos": [{
            "inicio": 10, "fim": 110, "descricao": "Cena depois da abertura",
            "titulos": ["A", "B", "C"],
        }]}
        resultado = analisar_transcricao(transcricao, self.config,
                                         ProvedorSimulado([resposta, {"candidatos": []}]),
                                         usar_cache=False)
        self.assertEqual(1, len(resultado))
        self.assertEqual(50, resultado[0].inicio)

    def test_timestamps_fora_do_video_recuperam_so_com_tolerancia(self):
        resposta = {"candidatos": [
            {"inicio": -2, "fim": 70, "descricao": "recuperável", "titulos": ["A", "B", "C"]},
            {"inicio": 100, "fim": 500, "descricao": "fora", "titulos": ["D", "E", "F"]},
        ]}
        resultado = analisar_transcricao(transcricao_teste(), self.config,
                                         ProvedorSimulado([resposta, {"candidatos": []}]),
                                         usar_cache=False)
        self.assertEqual(1, len(resultado))
        self.assertEqual(1, resultado.diagnostico.rejeitada)

    def test_resposta_parcialmente_invalida_preserva_validos(self):
        resposta = {"candidatos": [
            {"inicio": 0, "fim": 70, "descricao": "válida", "titulos": ["A", "B", "C"]},
            {"inicio": "erro", "descricao": "inválida"},
            {"inicio": 100, "fim": 180, "descricao": "válida 2", "titulos": ["D", "E", "F"]},
        ]}
        resultado = analisar_transcricao(transcricao_teste(), self.config,
                                         ProvedorSimulado([resposta, {"candidatos": []}]),
                                         usar_cache=False)
        self.assertEqual(2, len(resultado))
        self.assertEqual(1, resultado.diagnostico.rejeitada)

    def test_dez_recebidos_com_invalidos_nao_zeram_lista(self):
        brutos = []
        for i in range(10):
            brutos.append({"inicio": i * 20, "fim": i * 20 + (70 if i % 3 else 5),
                           "descricao": f"Cena {i}",
                           "titulos": [f"{i}A", f"{i}B", f"{i}C"]})
        config = ConfigIA(modelo="gemini-simulado", max_chamadas_episodio=1,
                          max_cortes=10, janela_segundos=300, sobreposicao_segundos=0)
        resultado = analisar_transcricao(transcricao_teste(), config,
                                         ProvedorSimulado([{"candidatos": brutos}]), usar_cache=False)
        self.assertGreater(len(resultado), 0)
        self.assertLessEqual(len(resultado), 10)

    def test_resposta_vazia_legitima_permanece_vazia(self):
        resultado = analisar_transcricao(transcricao_teste(), self.config,
                                         ProvedorSimulado([{"candidatos": []}, {"candidatos": []}]),
                                         usar_cache=False)
        self.assertEqual([], resultado)
        self.assertEqual(0, resultado.diagnostico.retornada_ia)

    def test_falha_de_rede_em_todas_as_janelas_exibe_erro_real(self):
        class ProvedorFalho(ProvedorSimulado):
            def analisar(self, modelo, prompt):
                raise ErroGemini("rede indisponível")
        with self.assertRaisesRegex(ErroGemini, "rede indisponível"):
            analisar_transcricao(transcricao_teste(), self.config, ProvedorFalho(), usar_cache=False)

    def test_timestamp_relativo_de_janela_e_convertido_para_absoluto(self):
        segmentos = [SegmentoTranscricao(float(i), float(i + 10), f"Fala {i}")
                     for i in range(0, 800, 10)]
        transcricao = Transcricao("longo.mp4", "janelas-relativas", "small", "pt", "pt", 1,
                                  800, "agora", segmentos)
        config = ConfigIA(modelo="gemini-simulado", max_chamadas_episodio=3,
                          max_cortes=10, janela_segundos=300, sobreposicao_segundos=20)
        respostas = [
            {"candidatos": []},
            {"candidatos": [{"inicio": 10, "fim": 80, "descricao": "Cena janela 2",
                              "titulos": ["A", "B", "C"]}]},
            {"candidatos": []},
        ]
        with patch.object(analise_ia, "MAX_CARACTERES_CONTEXTO_UNICO", 1):
            resultado = analisar_transcricao(transcricao, config, ProvedorSimulado(respostas),
                                             usar_cache=False)
        self.assertEqual(1, len(resultado))
        self.assertGreaterEqual(resultado[0].inicio, 280)
        self.assertEqual(1, resultado.diagnostico.janelas_detalhes[1]["candidatos_recebidos"])

    def test_timestamps_em_milissegundos_sao_normalizados(self):
        resposta = {"candidatos": [{
            "inicio": 24810, "fim": 113600, "inicio_original": 24810,
            "descricao": "Cena em milissegundos",
            "titulos": ["Título A", "Título B", "Título C"],
        }]}
        resultado = analisar_transcricao(transcricao_teste(), self.config,
                                         ProvedorSimulado([resposta]), usar_cache=False)
        self.assertEqual(1, len(resultado))
        self.assertAlmostEqual(20, resultado[0].inicio)
        self.assertAlmostEqual(110, resultado[0].fim)

    def test_cena_longa_preserva_final_e_reduz_para_120_segundos(self):
        resposta = {"candidatos": [{
            "inicio": 0, "fim": 150, "descricao": "Cena longa completa",
            "titulos": ["Título A", "Título B", "Título C"],
        }]}
        resultado = analisar_transcricao(transcricao_teste(), self.config,
                                         ProvedorSimulado([resposta]), usar_cache=False)
        self.assertEqual(1, len(resultado))
        self.assertEqual(150, resultado[0].fim)
        self.assertLessEqual(resultado[0].duracao, 120)

    def test_prompt_preserva_contexto_desfecho_e_titulo_nao_repete_abertura(self):
        texto = _prompt(transcricao_teste().segmentos[:10], 3)
        self.assertIn("contexto indispensável", texto)
        self.assertIn("mantém continuidade", texto)
        self.assertIn("sem repetir a primeira fala", texto)

    def test_maximos_5_e_10_reutilizam_analise_completa_sem_nova_chamada(self):
        segmentos = [SegmentoTranscricao(float(i), float(i + 10), f"Fala {i}.")
                     for i in range(0, 1200, 10)]
        transcricao = Transcricao("episodio.mp4", "video-longo", "small", "pt", "pt", 1,
                                  1200, "agora", segmentos)
        brutos = []
        for i in range(12):
            inicio = i * 90.0
            brutos.append({"inicio": inicio, "fim": inicio + 60,
                           "descricao": f"Cena {i}",
                           "titulos": [f"Título {i} A", f"Título {i} B", f"Título {i} C"]})
        config = ConfigIA(modelo="gemini-simulado", max_chamadas_episodio=1,
                          max_cortes=5, janela_segundos=2000, sobreposicao_segundos=0)
        provedor = ProvedorSimulado([{"candidatos": brutos}])
        r5 = analisar_transcricao(transcricao, config, provedor)
        self.assertEqual(5, len(r5))
        config.max_cortes = 10
        r10 = analisar_transcricao(transcricao, config, ProvedorSimulado())
        self.assertEqual(10, len(r10))
        self.assertEqual(12, r10.diagnostico.retornada_ia)
        self.assertEqual(10, r10.diagnostico.final_exibida)
        self.assertEqual(1, r10.diagnostico.janelas_total)

    def test_limite_superior_e_dez_cortes(self):
        config = ConfigIA(max_cortes=15)
        with self.assertRaisesRegex(ValueError, "entre 1 e 10"):
            config.validar()

    def test_prompt_da_chamada_unica_pede_ate_dez_no_episodio_completo(self):
        transcricao = transcricao_teste()
        provedor = ProvedorSimulado([{"candidatos": []}])
        config = ConfigIA(modelo="gemini-simulado", max_chamadas_episodio=1,
                          max_cortes=10, janela_segundos=120, sobreposicao_segundos=10)
        analisar_transcricao(transcricao, config, provedor, usar_cache=False)
        self.assertEqual(1, provedor.chamadas)
        self.assertIn("no máximo 10 cenas", provedor.prompts[0])
        self.assertIn("Fala contextual número 23", provedor.prompts[0])

    def test_migracao_prefere_cache_legado_com_candidatos_ao_vazio(self):
        transcricao = transcricao_teste()
        config = self.config
        valido = {"inicio": 0, "fim": 60, "descricao": "Cena",
                  "titulos": ["Um", "Dois", "Três"]}
        analise_ia.ANALISES_DIR.mkdir(parents=True, exist_ok=True)
        analise_ia._cache_path_legado(transcricao, config, 10).write_text(
            json.dumps({"candidatos": [valido, dict(valido, inicio=70, fim=130,
                                                     titulos=["Quatro", "Cinco", "Seis"])]}),
            encoding="utf-8")
        analise_ia._cache_path_legado(transcricao, config, 15).write_text(
            json.dumps({"candidatos": []}), encoding="utf-8")
        config.max_cortes = 10
        resultado = analisar_transcricao(transcricao, config, ProvedorSimulado())
        self.assertEqual(2, len(resultado))
        self.assertTrue(resultado.diagnostico.origem_cache)

    def test_resposta_invalida_e_erro_em_vez_de_zero_cenas(self):
        class ProvedorInvalido(ProvedorSimulado):
            def analisar(self, modelo, prompt):
                self.chamadas += 1
                return {}
        with self.assertRaisesRegex(ErroGemini, "inválida"):
            analisar_transcricao(transcricao_teste(), self.config, ProvedorInvalido(), usar_cache=False)

    def test_cache_de_um_episodio_nao_e_usado_em_outro(self):
        episodio_a = transcricao_teste()
        episodio_a.video_id = "episodio-a"
        resposta_a = {"candidatos": [{"inicio": 0, "fim": 60, "descricao": "Só A",
                                       "titulos": ["A1", "A2", "A3"]}]}
        analisar_transcricao(episodio_a, self.config, ProvedorSimulado([resposta_a]))

        episodio_b = transcricao_teste()
        episodio_b.video_id = "episodio-b"
        provedor_b = ProvedorSimulado([{"candidatos": []}])
        resultado_b = analisar_transcricao(episodio_b, self.config, provedor_b)
        self.assertEqual(1, provedor_b.chamadas)
        self.assertEqual(0, len(resultado_b))
        self.assertNotEqual(analise_ia._cache_path(episodio_a, self.config),
                            analise_ia._cache_path(episodio_b, self.config))


class TesteSegurancaCredencial(unittest.TestCase):
    @patch("modules.credenciais.keyring.set_password")
    def test_chave_vai_somente_ao_keyring(self, salvar):
        chave = "AIza-chave-secreta-para-teste-123456"
        credenciais.salvar_chave_gemini(chave)
        salvar.assert_called_once_with(credenciais.SERVICO, credenciais.CONTA_GEMINI, chave)

    @patch("modules.credenciais.keyring.set_password", side_effect=Exception("AIza-segredo"))
    def test_erro_nao_revela_chave(self, _salvar):
        with self.assertRaises(credenciais.ErroCredencial) as contexto:
            credenciais.salvar_chave_gemini("AIza-segredo-super-longo-123456")
        self.assertNotIn("AIza", str(contexto.exception))

    def test_json_de_configuracao_nao_contem_credencial(self):
        with tempfile.TemporaryDirectory() as pasta:
            caminho = Path(pasta) / "config.json"
            with patch.object(config_ia, "CONFIG_IA_PATH", caminho):
                config_ia.salvar_config_ia(ConfigIA(modelo="gemini-simulado"))
                dados = json.loads(caminho.read_text(encoding="utf-8"))
                self.assertNotIn("chave", " ".join(dados.keys()).lower())


if __name__ == "__main__":
    unittest.main()
