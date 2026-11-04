"""
tests/test_titulos_lote.py — Pacote de cortes e importação de títulos em lote na esteira.
Testes unitários (sem UI real, sem clipboard real, sem Whisper/IA/FFmpeg).
"""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from modules.agencia.editorial import PlanoAgencia
from modules.esteira import ContextoEpisodio, EsteiraProducao
from modules.titulos_lote import (
    TituloCorteItem,
    aplicar_titulos_episodio,
    construir_pacote_esteira,
    limpar_texto_titulo,
    parser_titulos_lote,
    sanitizar_texto_lote,
)
from modules.titulos_v2 import STATUS_REVISAO_TITULO
from modules.transcricao import SegmentoTranscricao, Transcricao


class TestTitulosLote(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        # Nunca escrever na lista real da Agência durante os testes
        self.p_salvar = patch("modules.titulos_lote.salvar_lista_agencia")
        self.mock_salvar_agencia = self.p_salvar.start()

    def tearDown(self):
        self.p_salvar.stop()
        self.temp_dir.cleanup()

    def _episodio(self, num_cortes=3, ep_id="ep_teste", esteira=None):
        caminho = f"C:/videos/{ep_id}.mp4"
        transcricao = Transcricao(
            caminho, f"vid_{ep_id}", "small", "pt", "pt", 1.0, 120.0, "2026-10-23",
            [SegmentoTranscricao(0.0, 5.0, "Stewie encontrou a máquina do tempo."),
             SegmentoTranscricao(10.0, 15.0, "Peter caiu da escada e gritou socorro!"),
             SegmentoTranscricao(20.0, 25.0, "Lois não acreditou no que viu.")],
        )
        planos = [PlanoAgencia(inicio=float((i - 1) * 10), fim=float(i * 10), id=f"{ep_id}-c{i}",
                               descricao=f"Resumo {i}", titulo_escolhido=f"TITULO AUTOMATICO {i}",
                               titulo_capa=f"CAPA {i}", video_id=f"vid_{ep_id}", usar_remix=False)
                  for i in range(1, num_cortes + 1)]
        ep = ContextoEpisodio(id=ep_id, video_path=caminho, nome=f"{ep_id}.mp4",
                              pasta_saida_base=str(self.root), pasta_saida_episodio=str(self.root / ep_id),
                              transcricao=transcricao, planos=planos)
        esteira = esteira or EsteiraProducao(caminho_persistencia=self.root / "estado.json")
        with esteira._lock:
            esteira._episodios[ep.id] = ep
            esteira._ordem_ids.append(ep.id)
        return ep, esteira

    # ─── Limpeza / parser ─────────────────────────────────────────────
    def test_limpeza_de_titulos(self):
        self.assertEqual(limpar_texto_titulo('**"STEWIE DESTRÓI O MUNDO"**'), "STEWIE DESTRÓI O MUNDO")
        self.assertEqual(limpar_texto_titulo("CORTE 1: O Plano de Peter"), "O Plano de Peter")
        self.assertEqual(limpar_texto_titulo("1. Título Recomendado: A Grande Confusão"), "A Grande Confusão")
        self.assertEqual(limpar_texto_titulo("- 'Piada Sem Noção'"), "Piada Sem Noção")
        self.assertEqual(limpar_texto_titulo("• “Lois Surta”"), "Lois Surta")

    def test_blocos_completos(self):
        texto = """
        CORTE 1: Stewie Constrói a Máquina
        CAPA: Máquina do Tempo!
        ALT 1: O Retorno ao Passado
        ALT 2: Não Toque Nisso
        JUSTIFICATIVA: Destaque para o gancho.

        CORTE 2: Peter Cai da Escada
        Capa: Que Tombo!
        Alt 1: Socorro Imediato
        """
        res = parser_titulos_lote(texto, total_esperados=2)
        self.assertEqual(res.total_encontrados, 2)
        self.assertEqual(res.itens[0].titulo, "Stewie Constrói a Máquina")
        self.assertEqual(res.itens[0].titulo_capa, "Máquina do Tempo!")
        self.assertEqual(res.itens[0].alternativas, ["O Retorno ao Passado", "Não Toque Nisso"])
        self.assertEqual(res.itens[0].justificativa, "Destaque para o gancho.")
        self.assertEqual(res.itens[1].titulo, "Peter Cai da Escada")
        self.assertEqual(res.avisos, [])

    def test_formato_markdown_gemini_com_titulo_em_linha_propria(self):
        texto = """Aqui estão os títulos:

**Corte 1**
**Título:** "Peter Vende a Casa Por Um Sanduíche"
**Capa:** VENDEU TUDO
**Alternativas:** irrelevante

### CORTE 2
1. **Melhor título:** Stewie Hackeia a NASA
2. Alternativa: Bebê Gênio
"""
        res = parser_titulos_lote(texto, total_esperados=2)
        self.assertEqual([i.titulo for i in res.itens],
                         ["Peter Vende a Casa Por Um Sanduíche", "Stewie Hackeia a NASA"])
        self.assertEqual(res.itens[0].titulo_capa, "VENDEU TUDO")

    def test_linha_unica_corte_n(self):
        res = parser_titulos_lote("CORTE 1: A\u00e7\u00e3o Um\nCORTE 2: Dois Títulos\nCORTE 3: Três", 3)
        self.assertEqual([i.titulo for i in res.itens], ["Ação Um", "Dois Títulos", "Três"])

    def test_mapeia_pelo_numero_mesmo_fora_de_ordem(self):
        res = parser_titulos_lote("CORTE 3: Terceiro\nCORTE 1: Primeiro", 3)
        self.assertEqual([(i.indice, i.titulo) for i in res.itens], [(1, "Primeiro"), (3, "Terceiro")])
        self.assertTrue(any("Corte 2" in a for a in res.avisos))

    def test_lista_numerada_e_bullets(self):
        res = parser_titulos_lote("1. Stewie Constrói\n2) Peter Cai\n3. Lois Fica Chocada", 3)
        self.assertEqual([i.titulo for i in res.itens], ["Stewie Constrói", "Peter Cai", "Lois Fica Chocada"])
        res2 = parser_titulos_lote("Seguem os títulos:\n- Primeiro Título\n- Segundo Título", 2)
        self.assertEqual([i.titulo for i in res2.itens], ["Primeiro Título", "Segundo Título"])

    def test_ignora_pacote_colado_junto(self):
        texto = """
        CORTE 1 · ID: corte-1
        Resumo: algo
        Título atual: VELHO
        1. Segmento original 10–12s — GANCHO
           Falas: Que absurdo!
        Título: A Incrível Aventura de Peter

        CORTE 2 · ID: corte-2
        1. Segmento original 4–6s — GANCHO
        Título: O Desastre Completo
        """
        res = parser_titulos_lote(texto, total_esperados=2)
        self.assertEqual([i.titulo for i in res.itens], ["A Incrível Aventura de Peter", "O Desastre Completo"])

    def test_utf8_acentos(self):
        res = parser_titulos_lote("CORTE 1: Uma Família da Pesada em Ação Não Esperava Por Isso!")
        self.assertEqual(res.itens[0].titulo, "Uma Família da Pesada em Ação Não Esperava Por Isso!")

    def test_divergencia_contagem(self):
        res = parser_titulos_lote("CORTE 1: Título 1\nCORTE 2: Título 2", total_esperados=4)
        self.assertEqual(res.total_encontrados, 2)
        self.assertTrue(any("2 título(s), mas o episódio possui 4" in a for a in res.avisos))
        self.assertTrue(any("Corte 3" in a for a in res.avisos))
        res2 = parser_titulos_lote("CORTE 1: Alfa\nCORTE 2: Beta\nCORTE 5: Gama", total_esperados=2)
        self.assertEqual([i.indice for i in res2.itens], [1, 2])
        self.assertTrue(any("inexistentes" in a for a in res2.avisos))

    def test_texto_vazio(self):
        res = parser_titulos_lote("   ", 3)
        self.assertEqual(res.total_encontrados, 0)
        self.assertTrue(res.avisos)

    # ─── Pacote ───────────────────────────────────────────────────────
    def test_pacote_contem_todos_os_cortes_e_falas(self):
        ep, _ = self._episodio(num_cortes=3)
        texto, total = construir_pacote_esteira(ep)
        self.assertEqual(total, 3)
        for i in (1, 2, 3):
            self.assertIn(f"CORTE {i} · ID: ep_teste-c{i}", texto)
        self.assertIn("Peter caiu da escada", texto)
        self.assertIn("CORTE 1: [Título principal recomendado]", texto)
        # O pacote colado de volta não deve gerar títulos falsos
        self.assertEqual(parser_titulos_lote(texto, 3).total_encontrados, 0)
        # Pacote + resposta colados juntos: só a resposta vale
        res = parser_titulos_lote(texto + "\n\nCORTE 1: Um\nCORTE 2: Dois\nCORTE 3: Três", 3)
        self.assertEqual([i.titulo for i in res.itens], ["Um", "Dois", "Três"])

    def test_pacote_sem_planos(self):
        ep, _ = self._episodio(num_cortes=0)
        self.assertEqual(construir_pacote_esteira(ep), ("", 0))

    # ─── Aplicação ────────────────────────────────────────────────────
    def test_aplicar_marca_manual_persiste_e_emite_evento(self):
        ep, esteira = self._episodio(num_cortes=2)
        eventos = []
        esteira.registrar_callback(eventos.append)
        aplicados, avisos = aplicar_titulos_episodio(esteira, ep.id, [
            TituloCorteItem(1, "Novo Título 1", "Capa 1", ["Alt A"]),
            TituloCorteItem(2, "Novo Título 2"),
        ])
        self.assertEqual((aplicados, avisos), (2, []))
        p1 = ep.planos[0]
        self.assertEqual(p1.titulo, "Novo Título 1")
        self.assertEqual(p1.titulo_manual_confirmado, "Novo Título 1")
        self.assertTrue(p1.titulo_aprovado_manualmente)
        self.assertEqual(p1.titulo_origem, "manual")
        self.assertEqual(p1.titulo_capa, "Capa 1")
        self.assertEqual([o.texto for o in p1.titulos], ["Novo Título 1", "Alt A"])
        self.assertEqual(ep.planos[1].titulo_capa, "CAPA 2")  # capa não informada é preservada
        self.assertEqual(ep.planos_editados_manualmente, {p.id for p in ep.planos})
        self.mock_salvar_agencia.assert_called_once()
        self.assertTrue((self.root / "estado.json").is_file())
        self.assertTrue(any(e.get("tipo") == "titulos_lote_aplicados" for e in eventos))

    def test_persistencia_recarregada_preserva_titulo_manual(self):
        ep, esteira = self._episodio(num_cortes=1)
        aplicar_titulos_episodio(esteira, ep.id, [TituloCorteItem(1, "Título Persistido")])
        dados = esteira._caminho_persistencia.read_text(encoding="utf-8") if hasattr(esteira, "_caminho_persistencia") \
            else (self.root / "estado.json").read_text(encoding="utf-8")
        self.assertIn("Título Persistido", dados)

    def test_reabilita_status_revisao_titulo(self):
        ep, esteira = self._episodio(num_cortes=1)
        ep.planos[0].status = STATUS_REVISAO_TITULO
        ep.planos[0].erro = "Nenhum título atingiu o mínimo editorial."
        aplicar_titulos_episodio(esteira, ep.id, [TituloCorteItem(1, "Título Salvo")])
        self.assertEqual(ep.planos[0].status, "pendente")
        self.assertEqual(ep.planos[0].erro, "")

    def test_mesclagem_disco_e_etapa_titulos_nao_sobrescrevem(self):
        ep, esteira = self._episodio(num_cortes=1)
        aplicar_titulos_episodio(esteira, ep.id, [TituloCorteItem(1, "Título Intocável")])
        # Disco com versão antiga sem confirmação manual não pode sobrescrever
        antigo = PlanoAgencia(inicio=0.0, fim=10.0, id=ep.planos[0].id, titulo_escolhido="ANTIGO")
        with patch("modules.agencia.persistencia.carregar_lista_agencia", return_value=[antigo]), \
             patch("modules.esteira.carregar_lista_agencia", return_value=[antigo], create=True):
            esteira._mesclar_edicoes_manuais_disco(ep)
        self.assertEqual(ep.planos[0].titulo, "Título Intocável")
        elegiveis = [p for p in ep.planos if not p.titulo_aprovado_manualmente
                     and not p.titulo_manual_confirmado and p.id not in ep.planos_editados_manualmente]
        self.assertEqual(elegiveis, [])

    def test_bloqueio_durante_etapa_editorial(self):
        ep, esteira = self._episodio(num_cortes=1)
        ep.estado, ep.etapa_atual = "em_execucao", "titulos"
        aplicados, avisos = aplicar_titulos_episodio(esteira, ep.id, [TituloCorteItem(1, "X")])
        self.assertEqual(aplicados, 0)
        self.assertTrue(any("processamento ativo" in a for a in avisos))
        self.assertEqual(ep.planos[0].titulo, "TITULO AUTOMATICO 1")

    def test_isolamento_por_episodio(self):
        ep1, esteira = self._episodio(num_cortes=2, ep_id="ep1")
        ep2, _ = self._episodio(num_cortes=2, ep_id="ep2", esteira=esteira)
        aplicar_titulos_episodio(esteira, ep1.id, [TituloCorteItem(1, "Exclusivo Ep 1")])
        self.assertEqual(ep1.planos[0].titulo, "Exclusivo Ep 1")
        self.assertEqual(ep2.planos[0].titulo, "TITULO AUTOMATICO 1")
        self.assertNotIn(ep2.planos[0].id, ep2.planos_editados_manualmente)

    # ─── Handlers da UI (com mocks; não é teste de UI real) ───────────
    def test_ui_copiar_todos_cortes_usa_episodio_correto(self):
        from ui.tela_importar import TelaImportar
        ep1, esteira = self._episodio(num_cortes=1, ep_id="ep1")
        self._episodio(num_cortes=2, ep_id="ep2", esteira=esteira)
        ui = MagicMock()
        ui.esteira = esteira
        TelaImportar._copiar_todos_cortes(ui, "ep2")
        texto = ui.clipboard_append.call_args[0][0]
        self.assertIn("ep2-c1", texto)
        self.assertNotIn("ep1-c1", texto)
        ui.app.mostrar_info.assert_called_once()

    def test_ui_copiar_sem_cortes_avisa(self):
        from ui.tela_importar import TelaImportar
        ep, esteira = self._episodio(num_cortes=0)
        ui = MagicMock()
        ui.esteira = esteira
        TelaImportar._copiar_todos_cortes(ui, ep.id)
        ui.clipboard_append.assert_not_called()
        ui.app.mostrar_aviso.assert_called_once()

    def test_concluir_ou_arquivar_episodio_em_revisao_com_zero_cortes(self):
        """Permite arquivar episódio travado em revisão com 0 cortes preparados."""
        ep, esteira = self._episodio(num_cortes=0)
        ep.estado = "revisao"
        ep.planos_prontos = []
        eventos = []
        esteira.registrar_callback(eventos.append)

        sucesso = esteira.concluir_ou_arquivar_episodio(ep.id)
        self.assertTrue(sucesso)
        self.assertIsNone(esteira.obter_episodio(ep.id))
        self.assertNotIn(ep.id, esteira._ordem_ids)
        self.assertTrue(any(e.get("tipo") == "episodio_arquivado" for e in eventos))

    def test_ui_concluir_ou_arquivar_remove_card_instantaneamente(self):
        """Handler da UI chama arquivamento e redesenha a fila imediatamente."""
        from ui.tela_importar import TelaImportar
        ep, esteira = self._episodio(num_cortes=0)
        ep.estado = "revisao"
        ui = MagicMock()
        ui.esteira = esteira

        TelaImportar._concluir_ou_arquivar_episodio(ui, ep.id)
        self.assertIsNone(esteira.obter_episodio(ep.id))
        ui._redesenhar_fila_esteira.assert_called_once()
        ui._atualizar_cabecalho_esteira.assert_called_once()

    def test_aprovar_e_exportar_episodio_em_revisao_sucesso(self):
        """Aprova todos os cortes com título na esteira e enfileira para produção sem ir na agência."""
        ep, esteira = self._episodio(num_cortes=2)
        ep.estado = "revisao"
        ep.planos_prontos = []
        for p in ep.planos:
            p.corte_aprovado_manualmente = False
            p.status = "revisão"
            p.estado_editorial = "revisao"

        eventos = []
        esteira.registrar_callback(eventos.append)

        qtd, avisos = esteira.aprovar_e_exportar_episodio(ep.id)
        self.assertEqual(qtd, 2)
        self.assertEqual(avisos, [])
        for p in ep.planos:
            self.assertTrue(p.corte_aprovado_manualmente)
            self.assertEqual(p.status, "pendente")
            self.assertEqual(p.estado_editorial, "automatico")
        self.assertTrue(ep.id in esteira._fila_agencia or ep.id in esteira._ativos_agencia)
        self.assertEqual(ep.etapa_atual, "preparacao")
        self.assertTrue(any(e.get("tipo") == "episodio_aprovado_lote" for e in eventos))

    def test_aprovar_e_exportar_sem_titulos_rejeita(self):
        """Episódio sem títulos definidos nos cortes é avisado e não avança."""
        ep, esteira = self._episodio(num_cortes=1)
        ep.planos[0].titulo_escolhido = ""
        ep.planos[0].titulos = []

        qtd, avisos = esteira.aprovar_e_exportar_episodio(ep.id)
        self.assertEqual(qtd, 0)
        self.assertTrue(any("Nenhum corte pôde ser aprovado" in a for a in avisos))

    def test_ui_aprovar_todos_e_exportar_chama_esteira_e_atualiza(self):
        """Disparo direto pelo card na interface atualiza o card e notifica o usuário."""
        from ui.tela_importar import TelaImportar
        ep, esteira = self._episodio(num_cortes=2)
        ep.estado = "revisao"
        ui = MagicMock()
        ui.esteira = esteira

        TelaImportar._aprovar_todos_e_exportar(ui, ep.id)
        ui._atualizar_card_episodio.assert_called_with(ep.id)
        ui._atualizar_cabecalho_esteira.assert_called_once()
        ui.app.mostrar_info.assert_called_once()

    # ─── Sanitização, isolamento de chat e descarte de instruções ──────
    def test_descarte_de_instrucoes_puras(self):
        """Se o usuário cola apenas o bloco de instruções ou prompt, nenhum corte recebe título lixo."""
        texto_instrucoes = """
        INSTRUÇÕES PARA GERAR TÍTULOS DE CORTES (IA TOONS CUT)

        Analise cada corte do episódio e crie títulos fortes para vídeos curtos (Reels, TikTok, Shorts).
        Quero títulos específicos, engraçados, provocativos, extravagantes e com potencial de despertar curiosidade.
        Identifique o conflito central, a situação mais absurda ou a contradição engraçada da cena.
        Não invente acontecimentos que não estejam sustentados pelas falas ou resumo.

        FORMATO DE RESPOSTA OBRIGATÓRIO (para importação automática em lote):
        Responda para cada corte, mantendo exatamente o número do corte, no formato:

        CORTE 1: [Título principal recomendado]
        CAPA: [Texto curto para a capa - opcional]
        ALT 1: [Alternativa diferente - opcional]
        ALT 2: [Segunda alternativa - opcional]
        JUSTIFICATIVA: [Uma frase - opcional]

        (Também é aceito apenas uma linha por corte: "CORTE 1: <título>", "CORTE 2: <título>", etc.)
        """
        res = parser_titulos_lote(texto_instrucoes, total_esperados=3)
        self.assertEqual(res.total_encontrados, 0)
        self.assertEqual(len(res.itens), 0)
        self.assertTrue(any("apenas instruções ou metadados" in a for a in res.avisos))

    def test_isolamento_chat_completo_com_prompt_e_resposta(self):
        """Se o usuário cola o chat inteiro (Prompt + Resposta da IA), isola apenas a resposta final."""
        chat_completo = """
        INSTRUÇÕES PARA GERAR TÍTULOS DE CORTES (IA TOONS CUT)
        Analise cada corte do episódio e crie títulos fortes para vídeos curtos.
        FORMATO DE RESPOSTA OBRIGATÓRIO (para importação automática em lote):
        CORTE 1: [Título principal recomendado]
        CAPA: [Texto curto para a capa - opcional]

        EPISÓDIO: Uma Família da Pesada - S04E01
        CORTE 1 · ID: ep1-c1
        ORDEM FINAL DA MONTAGEM:
        1. Segmento original 00:00 -> 00:30
        Falas: Peter: Olá mundo!
        Lembre-se: responda no formato "CORTE N: <título>" para cada corte acima.

        ChatGPT:
        Aqui estão os títulos para os cortes:

        CORTE 1: Peter Descobre o Segredo Real
        CAPA: Ele Não Esperava!
        ALT 1: O Segredo de Peter

        CORTE 2: Lois Perde a Cabeça
        CAPA: Grito Histérico
        """
        res = parser_titulos_lote(chat_completo, total_esperados=2)
        self.assertEqual(res.total_encontrados, 2)
        self.assertEqual(res.itens[0].titulo, "Peter Descobre o Segredo Real")
        self.assertEqual(res.itens[0].titulo_capa, "Ele Não Esperava!")
        self.assertEqual(res.itens[1].titulo, "Lois Perde a Cabeça")
        self.assertEqual(res.itens[1].titulo_capa, "Grito Histérico")

    def test_filtro_linhas_numeradas_com_instrucoes(self):
        """Linhas numeradas contendo orientações do prompt são descartadas pelo parser."""
        texto = """
        1. Analise cada corte individualmente e crie títulos fortes.
        2. Não invente acontecimentos nem use informações falsas.
        3. Para cada corte, entregue o melhor título recomendado.
        """
        res = parser_titulos_lote(texto, total_esperados=3)
        self.assertEqual(res.total_encontrados, 0)

    def test_prompt_mais_resposta_numerada_sem_role(self):
        """Se o usuário cola prompt com blocos e a resposta da IA é numerada (1., 2.), extrai os títulos corretamente."""
        texto = """
        INSTRUÇÕES PARA GERAR TÍTULOS DE CORTES (IA TOONS CUT)
        Analise cada corte...
        FORMATO DE RESPOSTA OBRIGATÓRIO:
        CORTE 1: [Título principal]
        EPISÓDIO: S11E22
        CORTE 1 · ID: c1
        ORDEM FINAL DA MONTAGEM:
        1. Segmento original 10-20s
           Falas: olá amigo
        CORTE 2 · ID: c2
        ORDEM FINAL DA MONTAGEM:
        1. Segmento original 30-40s
           Falas: até logo

        Aqui estão os títulos:
        1. Peter na Fazenda
        2. Stewie e o Robô
        """
        res = parser_titulos_lote(texto, total_esperados=2)
        self.assertEqual(res.total_encontrados, 2)
        self.assertEqual([i.titulo for i in res.itens], ["Peter na Fazenda", "Stewie e o Robô"])

    def test_formatos_hifen_colchete_e_prefixos(self):
        """Formatos como 'Corte 1 - ...', '[Corte 1] ...', 'Vídeo 1: ...' são suportados."""
        t1 = "Corte 1 - O Segredo Revelado\nCorte 2 - A Fuga Impossível"
        r1 = parser_titulos_lote(t1, 2)
        self.assertEqual([i.titulo for i in r1.itens], ["O Segredo Revelado", "A Fuga Impossível"])

        t2 = "[Corte 1] O Segredo Revelado\n[Corte 2] A Fuga Impossível"
        r2 = parser_titulos_lote(t2, 2)
        self.assertEqual([i.titulo for i in r2.itens], ["O Segredo Revelado", "A Fuga Impossível"])

        t3 = "Vídeo 1: O Segredo Revelado\nVídeo 2: A Fuga Impossível"
        r3 = parser_titulos_lote(t3, 2)
        self.assertEqual([i.titulo for i in r3.itens], ["O Segredo Revelado", "A Fuga Impossível"])

    def test_sanitizar_texto_lote_pacote_bruto_retorna_vazio(self):
        """Pacote bruto com instruções e metadados (~148 linhas) retorna '' e 0 títulos com aviso de formato inválido."""
        ep, _ = self._episodio(num_cortes=10)
        pacote_bruto, _ = construir_pacote_esteira(ep)
        self.assertTrue(len(pacote_bruto.splitlines()) > 50)

        limpo = sanitizar_texto_lote(pacote_bruto)
        self.assertEqual(limpo, "")

        res = parser_titulos_lote(pacote_bruto, total_esperados=10)
        self.assertEqual(res.total_encontrados, 0)
        self.assertEqual(len(res.itens), 0)
        self.assertTrue(
            any("Formato inválido: Certifique-se de copiar apenas os títulos (ex: CORTE 1: ...)." in a for a in res.avisos)
        )

    def test_sanitizar_texto_lote_chat_misto_isola_cortes(self):
        """Chat misto (Prompt + Instruções + resposta da IA) isola apenas as linhas de corte válidas."""
        ep, _ = self._episodio(num_cortes=2)
        pacote_bruto, _ = construir_pacote_esteira(ep)
        chat_misto = f"""{pacote_bruto}

ChatGPT disse:
Com certeza! Aqui estão os títulos sugeridos para os cortes:

CORTE 1: Stewie Constrói a Máquina do Futuro
CAPA: O Futuro Chegou!
ALT 1: Máquina Temporal

CORTE 2: Peter Cai da Escada de Novo
CAPA: Que Queda!
"""
        limpo = sanitizar_texto_lote(chat_misto)
        self.assertNotIn("INSTRUÇÕES", limpo)
        self.assertNotIn("EPISÓDIO:", limpo)
        self.assertNotIn("ORDEM FINAL", limpo)
        self.assertNotIn("Segmento original", limpo)
        self.assertNotIn("Falas:", limpo)
        self.assertNotIn("ChatGPT disse:", limpo)
        self.assertIn("CORTE 1: Stewie Constrói a Máquina do Futuro", limpo)
        self.assertIn("CAPA: O Futuro Chegou!", limpo)
        self.assertIn("ALT 1: Máquina Temporal", limpo)
        self.assertIn("CORTE 2: Peter Cai da Escada de Novo", limpo)

        res = parser_titulos_lote(limpo, total_esperados=2)
        self.assertEqual(res.total_encontrados, 2)
        self.assertEqual(res.itens[0].titulo, "Stewie Constrói a Máquina do Futuro")
        self.assertEqual(res.itens[0].titulo_capa, "O Futuro Chegou!")
        self.assertEqual(res.itens[1].titulo, "Peter Cai da Escada de Novo")

    def test_sanitizar_texto_lote_titulos_limpos_preservados(self):
        """Títulos limpos sequenciais, numerados ou com CORTE N são preservados integralmente."""
        t_corte = "CORTE 1: Primeiro Título\nCORTE 2: Segundo Título"
        self.assertEqual(sanitizar_texto_lote(t_corte), t_corte)

        t_num = "1. Primeiro Título\n2. Segundo Título"
        self.assertEqual(sanitizar_texto_lote(t_num), t_num)

        t_seq = "Primeiro Título\nSegundo Título"
        self.assertEqual(sanitizar_texto_lote(t_seq), t_seq)

    def test_ui_modal_colar_clipboard_sanitiza_e_rejeita_pacote_bruto(self):
        """Ao colar pacote bruto na UI, o texto bruto é rejeitado, txt é limpo e aviso é exibido."""
        from ui.app import COR_VERMELHO
        from ui.tela_importar import ModalColarTitulos
        ep, esteira = self._episodio(num_cortes=3)
        pacote_bruto, _ = construir_pacote_esteira(ep)

        tela = MagicMock()
        tela.winfo_toplevel.return_value = MagicMock()

        with patch.object(ModalColarTitulos, "__init__", lambda self, *args, **kwargs: None):
            modal = ModalColarTitulos()
            modal.tela = tela
            modal.ep = ep
            modal.esteira = esteira
            modal.txt = MagicMock()
            modal.frame_previa = MagicMock()
            modal.frame_previa.winfo_children.return_value = []
            modal.lbl_info = MagicMock()
            modal.btn_aplicar = MagicMock()
            modal.btn_aplicar_exportar = MagicMock()
            modal.clipboard_get = MagicMock(return_value=pacote_bruto)

            ModalColarTitulos._colar_clipboard(modal)

            modal.txt.delete.assert_called_with("1.0", "end")
            modal.txt.insert.assert_not_called()
            modal.lbl_info.configure.assert_called_with(
                text="⚠️ Formato inválido: Certifique-se de copiar apenas os títulos (ex: CORTE 1: ...).",
                text_color=COR_VERMELHO,
            )
            tela.app.mostrar_aviso.assert_called_with(
                "Formato inválido",
                "Formato inválido: Certifique-se de copiar apenas os títulos (ex: CORTE 1: ...).",
            )

    def test_falso_positivo_gemini_markdown_e_quebras_regra_de_ouro(self):
        """Reproduz o cenário real das capturas (S12E01): 5 cortes com markdown/lixo ao redor nunca geram falso positivo."""
        bloco_gemini = (
            "\ufeffPeça ao Gemini\r\n\r\n"
            "**CORTE 1:** PETER E LOIS VÃO ATRÁS DO TESOURO SECRETO\r\n\r\n"
            "**CORTE 2: A RECOMPENSA É UM CUPOM DE COMIDA VENCIDO HÁ 7 ANOS**\r\n\r\n"
            "CORTE 3: GARÇOM RECUSA CUPOM DE DURAÇÃO VENCIDA E PETER ENLOUQUECE NO RESTAURANTE\r\n\r\n"
            "*CORTE 4 -* PETER VAI NA TV E CONTA A PISTA DO TESOURO PRA CIDADE TODA\r\n\r\n"
            "**CORTE 5**: MEG DESVENDA O ENIGMA DO TESOURO E A FAMÍLIA INTEIRA FICA TRAÍRA PRA PEGAR A GRANA\r\n"
            "---\r\n"
            "Gemini pode cometer erros.\r\n"
        )
        limpo = sanitizar_texto_lote(bloco_gemini)
        self.assertTrue(bool(limpo.strip()))
        res = parser_titulos_lote(bloco_gemini, total_esperados=5)
        self.assertEqual(res.total_encontrados, 5)
        self.assertEqual(res.avisos, [])
        self.assertEqual(res.itens[0].titulo, "PETER E LOIS VÃO ATRÁS DO TESOURO SECRETO")
        self.assertEqual(res.itens[1].titulo, "A RECOMPENSA É UM CUPOM DE COMIDA VENCIDO HÁ 7 ANOS")
        self.assertEqual(
            res.itens[2].titulo,
            "GARÇOM RECUSA CUPOM DE DURAÇÃO VENCIDA E PETER ENLOUQUECE NO RESTAURANTE",
        )
        self.assertEqual(res.itens[3].titulo, "PETER VAI NA TV E CONTA A PISTA DO TESOURO PRA CIDADE TODA")
        self.assertEqual(
            res.itens[4].titulo,
            "MEG DESVENDA O ENIGMA DO TESOURO E A FAMÍLIA INTEIRA FICA TRAÍRA PRA PEGAR A GRANA",
        )

    def test_ui_modal_colar_clipboard_aceita_cortes_com_lixo_sem_erro(self):
        """Regra de Ouro na UI: se houver ao menos 1 CORTE válido no meio de instruções/lixo, aceita e NÃO exibe alerta de erro."""
        from ui.tela_importar import ModalColarTitulos
        ep, esteira = self._episodio(num_cortes=3)
        texto_com_lixo = (
            "INSTRUÇÕES PARA GERAR TÍTULOS\n"
            "Lixo aleatório antes\n"
            "**CORTE 1:** Primeiro Título Válido\n"
            "Linha inválida no meio que deve ser ignorada\n"
            "CORTE 2 - Segundo Título Válido\n"
            "Peça ao Gemini\n"
        )

        tela = MagicMock()
        tela.winfo_toplevel.return_value = MagicMock()

        with patch.object(ModalColarTitulos, "__init__", lambda self, *args, **kwargs: None):
            modal = ModalColarTitulos()
            modal.tela = tela
            modal.ep = ep
            modal.esteira = esteira
            modal.txt = MagicMock()
            modal.frame_previa = MagicMock()
            modal.frame_previa.winfo_children.return_value = []
            modal.lbl_info = MagicMock()
            modal.btn_aplicar = MagicMock()
            modal.btn_aplicar_exportar = MagicMock()
            modal.clipboard_get = MagicMock(return_value=texto_com_lixo)
            modal._analisar = MagicMock()

            ModalColarTitulos._colar_clipboard(modal)

            tela.app.mostrar_aviso.assert_not_called()
            modal.txt.insert.assert_called_once()
            texto_inserido = modal.txt.insert.call_args[0][1]
            self.assertIn("CORTE 1: Primeiro Título Válido", texto_inserido)
            self.assertIn("CORTE 2: Segundo Título Válido", texto_inserido)
            self.assertNotIn("INSTRUÇÕES", texto_inserido)
            modal._analisar.assert_called_once()


    def test_fluxo_multi_episodio_copia_e_cola_isolados(self):
        """Simula ciclo completo de múltiplos episódios (Ep 1 -> Ep 2) com total isolamento de sinais e clipboard."""
        from ui.tela_importar import ModalColarTitulos, TelaImportar
        ep1, esteira = self._episodio(num_cortes=2, ep_id="ep1")
        ep2, _ = self._episodio(num_cortes=2, ep_id="ep2", esteira=esteira)

        ui = MagicMock()
        ui.esteira = esteira
        ui.app = MagicMock()
        ui._modal_colar_titulos = None

        # 1. Copia cortes do Ep 1
        TelaImportar._copiar_todos_cortes(ui, "ep1")
        texto_copiado_ep1 = ui.clipboard_append.call_args[0][0]
        self.assertIn("ep1-c1", texto_copiado_ep1)

        # 2. Copia cortes do Ep 2
        TelaImportar._copiar_todos_cortes(ui, "ep2")
        texto_copiado_ep2 = ui.clipboard_append.call_args[0][0]
        self.assertIn("ep2-c1", texto_copiado_ep2)
        self.assertNotIn("ep1-c1", texto_copiado_ep2)
        contagem_clipboard_append = ui.clipboard_append.call_count

        # 3. Usuário abre modal para o Ep 2 (NÃO pode tocar no clipboard nem disparar _copiar_todos_cortes)
        with patch("ui.tela_importar.ModalColarTitulos") as mock_modal_cls:
            TelaImportar._abrir_modal_colar_titulos(ui, "ep2")
            mock_modal_cls.assert_called_once_with(ui, ep2, esteira)
        self.assertEqual(ui.clipboard_append.call_count, contagem_clipboard_append)

        # 4. Simula o colar no modal do Ep 2 com resposta da IA para Ep 2
        resposta_ia_ep2 = "CORTE 1: Título Ep 2 Corte 1\nCORTE 2: Título Ep 2 Corte 2"
        with patch.object(ModalColarTitulos, "__init__", lambda self, *args, **kwargs: None):
            modal = ModalColarTitulos()
            modal.tela = ui
            modal.ep = ep2
            modal.esteira = esteira
            modal.txt = MagicMock()
            modal.frame_previa = MagicMock()
            modal.frame_previa.winfo_children.return_value = []
            modal.lbl_info = MagicMock()
            modal.btn_aplicar = MagicMock()
            modal.btn_aplicar_exportar = MagicMock()
            modal.clipboard_get = MagicMock(return_value=resposta_ia_ep2)
            modal._analisar = MagicMock()

            ModalColarTitulos._colar_clipboard(modal)
            modal.txt.insert.assert_called_once()
            self.assertIn("Título Ep 2 Corte 1", modal.txt.insert.call_args[0][1])
            self.assertIn("Título Ep 2 Corte 2", modal.txt.insert.call_args[0][1])
            self.assertNotIn("INSTRUÇÕES", modal.txt.insert.call_args[0][1])

    def test_isolamento_partial_botoes_cards_esteira(self):
        """Verifica que _atualizar_card_episodio reafirma comandos isolados via functools.partial para cada ep_id."""
        from functools import partial
        from ui.tela_importar import TelaImportar

        ep1, esteira = self._episodio(num_cortes=2, ep_id="ep1")
        ep2, _ = self._episodio(num_cortes=2, ep_id="ep2", esteira=esteira)

        ui = MagicMock()
        ui.esteira = esteira
        ui._cor_estado.return_value = "#FFFFFF"

        def _criar_widgets_mock():
            return {
                "card": MagicMock(),
                "lbl_estado": MagicMock(),
                "barra": MagicMock(),
                "lbl_pct": MagicMock(),
                "lbl_msg": MagicMock(),
                "lbl_cortes": MagicMock(),
                "btn_copiar": MagicMock(),
                "btn_copiar_cortes": MagicMock(),
                "btn_colar_titulos": MagicMock(),
                "btn_aprovar_exportar": MagicMock(),
                "btn_concluir_arquivar": MagicMock(),
                "btn_retry": MagicMock(),
                "btn_cancelar": MagicMock(),
                "btn_remover": MagicMock(),
            }

        ui._cards_episodios = {
            "ep1": _criar_widgets_mock(),
            "ep2": _criar_widgets_mock(),
        }

        TelaImportar._atualizar_card_episodio(ui, "ep1")
        TelaImportar._atualizar_card_episodio(ui, "ep2")

        cmd_colar_ep1 = ui._cards_episodios["ep1"]["btn_colar_titulos"].configure.call_args.kwargs["command"]
        cmd_colar_ep2 = ui._cards_episodios["ep2"]["btn_colar_titulos"].configure.call_args.kwargs["command"]
        cmd_copiar_ep2 = ui._cards_episodios["ep2"]["btn_copiar_cortes"].configure.call_args.kwargs["command"]

        self.assertIsInstance(cmd_colar_ep1, partial)
        self.assertIsInstance(cmd_colar_ep2, partial)
        self.assertEqual(cmd_colar_ep1.args, ("ep1",))
        self.assertEqual(cmd_colar_ep2.args, ("ep2",))
        self.assertEqual(cmd_colar_ep2.func, ui._abrir_modal_colar_titulos)
        self.assertEqual(cmd_copiar_ep2.func, ui._copiar_todos_cortes)
        self.assertNotEqual(cmd_colar_ep2.func, cmd_copiar_ep2.func)

    def test_win32_clipboard_roundtrip_sistema(self):
        """Testa que escrever_clipboard_sistema e ler_clipboard_sistema gravam e leem diretamente da Win32 API."""
        import sys
        if sys.platform != "win32":
            return
        from modules.titulos_lote import escrever_clipboard_sistema, ler_clipboard_sistema
        texto_original = ler_clipboard_sistema(None)
        try:
            # 1. Simula "Copiar todos os cortes" gravando pacote no OS clipboard
            pacote_simulado = "INSTRUÇÕES PARA GERAR TÍTULOS\nCORTE 1: [Título principal recomendado]"
            escrever_clipboard_sistema(None, pacote_simulado)
            self.assertEqual(ler_clipboard_sistema(None), pacote_simulado)

            # 2. Simula usuário copiando os títulos do Episódio 2 no navegador (ChatGPT/Gemini)
            titulos_ep2 = "CORTE 1: PETER E LOIS NO EPISÓDIO 2\nCORTE 2: STEWIE APRONTA DE NOVO"
            escrever_clipboard_sistema(None, titulos_ep2)

            # 3. Ao ler no modal do Episódio 2, retorna estritamente os títulos do Ep 2 (nunca o pacote antigo)
            lido = ler_clipboard_sistema(None)
            self.assertEqual(lido, titulos_ep2)
        finally:
            if texto_original is not None:
                escrever_clipboard_sistema(None, texto_original)


    def test_aprovar_todos_e_exportar_fixa_toonmix_em_100_por_cento_dos_cortes(self):
        """Garante que Aprovar Todos e Exportar usa exclusivamente o perfil TOONMIX e seu fundo sem falhas."""
        from modules.agencia.renderer import construir_comando
        from modules.agencia.av import AjustesAgencia
        from modules.agencia.layout import calcular_enquadramento
        from modules.agencia.renderer import InfoFonte
        from modules.agencia.timeline import TimelineAgencia, TrechoTimeline
        from modules.perfis_visuais import FUNDO_TOONMIX_PATH, PERFIL_TOONMIX, obter_perfil

        ep, esteira = self._episodio(num_cortes=2, ep_id="ep_toonmix")
        ep.perfil_visual_id = "ia_toons"  # mesmo que viesse de estado legado, deve ser fixado em toonmix
        ep.estado = "revisao"

        qtd, avisos = esteira.aprovar_e_exportar_episodio(ep.id)
        self.assertEqual(qtd, 2)
        self.assertEqual(avisos, [])
        self.assertEqual(ep.perfil_visual_id, "toonmix")

        perfil_resolvido = obter_perfil(ep.perfil_visual_id)
        self.assertEqual(perfil_resolvido, PERFIL_TOONMIX)
        self.assertEqual(perfil_resolvido.fundo_path, FUNDO_TOONMIX_PATH)
        self.assertTrue(perfil_resolvido.fundo_path.is_file())

        # Verifica que o comando FFmpeg gerado injeta a moldura com furo do TOONMIX
        tl = TimelineAgencia(
            tipo="remix",
            trechos=[TrechoTimeline(0.0, 5.0, "gancho", "inicio", 0.0)],
            fps=60,
        )
        info = InfoFonte(1920, 1080, 30.0, 120.0, True)
        enq = calcular_enquadramento(1920, 1080)
        cmd = construir_comando(
            ep.video_path,
            str(self.root / "saida.mp4"),
            tl,
            info,
            enq,
            perfil_resolvido,
            AjustesAgencia(),
            None,
            [],
            self.root / "grafo.txt",
        )
        cmd_str = " ".join(cmd)
        self.assertIn("toonmix_", cmd_str)
        self.assertIn("perfil=toonmix", cmd_str)


if __name__ == "__main__":
    unittest.main()
