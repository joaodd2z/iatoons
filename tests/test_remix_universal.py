"""Contrato e recuperacao reais, providers falsos, sem rede/render de episodios."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from test_agencia_editorial import episodio_longo, remix_em, normal_em, ProvedorSequencial
from modules.agencia import editorial
from modules.agencia.remix import avaliar_remix_real, REMIX_RULES_VERSION
from modules.agencia.timeline import construir_timeline, gerar_legendas_timeline, TimelineAgencia, TrechoTimeline
from modules.agencia.recuperacao import buscar_remixes, blocos_narrativos, MENSAGEM_ZERO
from modules.analise_ia import analisar_transcricao, validar_candidatos_com_diagnostico
from modules.config_ia import ConfigIA
from modules.cortes import Corte, corte_de_plano, sugerir_cortes
from modules.ganchos_ab import TrechoFonte, remapear_legendas
from modules.intro import IntervaloIntro
from modules.legendas import gerar_blocos_legenda
from modules.lote import validar_exportacao_automatica, carregar_lista, salvar_lista
from modules.remix_narrativo import analisar_remixes


class TesteContratoUniversal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pasta = Path(self.tmp.name)
        for alvo, valor in (("modules.agencia.editorial.ANALISES_AGENCIA_DIR", self.pasta / "agencia"),
                            ("modules.agencia.editorial.AGENCIA_DIR", self.pasta / "estado"),
                            ("modules.remix_narrativo.REMIX_CACHE_DIR", self.pasta / "remix"),
                            ("modules.intro.INTROS_DIR", self.pasta / "intro"),
                            ("modules.lote.LOTES_DIR", self.pasta / "lotes")):
            p = patch(alvo, valor)
            p.start()
            self.addCleanup(p.stop)
        for alvo in ("socket.socket.connect", "socket.socket.connect_ex"):
            p = patch(alvo, side_effect=AssertionError("APIs reais proibidas"))
            p.start()
            self.addCleanup(p.stop)
        self.t = episodio_longo("contrato-universal")
        self.config = ConfigIA(modelo="gemini-teste", max_chamadas_episodio=4)
        self.p = editorial.validar_planos([remix_em(1)], self.t)[0][0]
        self.tl = construir_timeline(self.p, self.t)

    def avaliar(self, tl=None, p=None, intro=None):
        return avaliar_remix_real(p or self.p, tl or self.tl, self.t, intro=intro)

    def test_remix_futuro_contexto_anterior_e_score(self):
        contrato = self.avaliar()
        self.assertTrue(contrato["remix_valido"])
        self.assertGreaterEqual(contrato["score_remix"], 75)
        self.assertTrue(contrato["reordenacao_comprovada"])
        self.assertEqual(REMIX_RULES_VERSION, self.p.remix_rules_version)

    def test_continuo_com_label_remix_rejeitado(self):
        tl = TimelineAgencia([TrechoTimeline(1, 58, "gancho")], "remix", gancho=(1, 58))
        self.assertFalse(self.avaliar(tl)["remix_valido"])

    def test_falsa_timeline_de_remix_rejeitada(self):
        tl = TimelineAgencia([TrechoTimeline(1, 25, "gancho"), TrechoTimeline(25, 58)], "remix", gancho=(25,29))
        self.assertFalse(self.avaliar(tl)["remix_valido"])

    def test_dois_trechos_cronologicos_rejeitados(self):
        tl = TimelineAgencia([TrechoTimeline(1, 25, "gancho"), TrechoTimeline(25, 58)], "remix", gancho=(1,5))
        self.assertFalse(self.avaliar(tl)["reordenacao_comprovada"])
        self.assertFalse(self.avaliar(tl)["remix_valido"])

    def test_gancho_repetido_rejeitado_independente_da_flag(self):
        tl = deepcopy(self.tl)
        tl.trechos.append(deepcopy(tl.trechos[0]))
        for flag in (False, True):
            tl.gancho_repetido = flag
            self.assertFalse(self.avaliar(tl)["remix_valido"])

    def test_intro_rejeitada(self):
        self.assertFalse(self.avaliar(intro=IntervaloIntro(25,29,True))["remix_valido"])

    def test_fala_truncada_rejeitada(self):
        tl = deepcopy(self.tl)
        tl.trechos[1].inicio = 2
        self.assertIn("fala truncada", self.avaliar(tl)["motivo_rejeicao"])

    def test_segmentos_desconexos_rejeitados(self):
        tl = deepcopy(self.tl)
        tl.trechos.append(TrechoTimeline(187, 191))
        self.assertFalse(self.avaliar(tl)["remix_valido"])

    def test_payoff_disponivel_nao_pode_sumir(self):
        p = deepcopy(self.p)
        p.estrutura["conclusao"] = {"inicio": 49, "fim": 57.8}
        tl = deepcopy(self.tl)
        tl.trechos[-1].fim = 49
        self.assertIn("payoff", self.avaliar(tl,p)["motivo_rejeicao"])

    def test_legendas_e_saida_para_fonte_preservadas(self):
        legendas = gerar_legendas_timeline(self.t, self.tl)
        self.assertTrue(avaliar_remix_real(self.p,self.tl,self.t,legendas)["remix_valido"])
        self.assertAlmostEqual(self.tl.gancho[0], self.tl.saida_para_fonte(0))
        for b in legendas:
            self.assertLessEqual(b.fim, self.tl.duracao)

    def test_nao_repetir_audio_gancho(self):
        gi,gf = self.tl.gancho
        self.assertFalse(any(min(t.fim,gf)-max(t.inicio,gi) > .05 for t in self.tl.trechos[1:]))

    def test_timestamps_corrompidos_retorna_diagnostico(self):
        tl = deepcopy(self.tl)
        tl.gancho = (None, 29)
        self.assertFalse(self.avaliar(tl)["remix_valido"])

    def test_lote_linear_automatico_bloqueado(self):
        corte = Corte(1,58,"Linear",origem="analise_ia")
        with self.assertRaises(ValueError):
            validar_exportacao_automatica(corte,self.t,[TrechoFonte(1,58)],[])

    def test_structural_local_nao_e_pronto(self):
        cortes = sugerir_cortes(self.t)
        self.assertTrue(cortes)
        self.assertTrue(all(not c.selecionado and c.status == "revisão" for c in cortes))
        with self.assertRaises(ValueError):
            validar_exportacao_automatica(cortes[0],self.t,[TrechoFonte(cortes[0].inicio,cortes[0].fim)],[])

    def test_manual_continua_disponivel(self):
        corte = Corte(1,58,"Manual")
        self.assertTrue(validar_exportacao_automatica(corte,None,[TrechoFonte(1,58)],[])["manual"])

    def test_edicao_nao_perde_proveniencia(self):
        corte = corte_de_plano(self.p)
        corte.modo_edicao = "normal"
        corte.titulo = "EDITADO"
        with self.assertRaises(ValueError):
            validar_exportacao_automatica(corte,self.t,[TrechoFonte(1,58)],[])

    def test_lote_remix_valida_timeline_enviada_ao_render(self):
        corte = corte_de_plano(self.p)
        trechos = self.tl.trechos_fonte()
        legendas = remapear_legendas(gerar_blocos_legenda(self.t),trechos)
        self.assertTrue(validar_exportacao_automatica(corte,self.t,trechos,legendas)["remix_valido"])
        with self.assertRaises(ValueError):
            validar_exportacao_automatica(corte,self.t,[TrechoFonte(1,58)],[])

    def test_cache_antigo_nao_tem_carimbo_novo(self):
        video = self.pasta / "video.mp4"
        video.write_bytes(b"sintetico")
        caminho = salvar_lista(str(video),[corte_de_plano(self.p)])
        dados = json.loads(caminho.read_text())
        dados["cortes"][0].pop("origem")
        caminho.write_text(json.dumps(dados))
        corte = carregar_lista(str(video))[0]
        self.assertEqual("",corte.remix_rules_version)
        self.assertFalse(corte.validacao_remix)

    def test_analise_comum_nao_aceita_fallback_linear(self):
        validos,_ = validar_candidatos_com_diagnostico([normal_em(1)],self.t,10)
        self.assertFalse(validos)

    def test_analise_comum_aplica_mesmo_contrato(self):
        provedor = ProvedorSequencial([{"historias":[remix_em(1)]}])
        resultado = analisar_transcricao(self.t,self.config,provedor,usar_cache=False)
        self.assertEqual(1,len(resultado))
        self.assertEqual(REMIX_RULES_VERSION,resultado[0].remix_rules_version)
        self.assertTrue(resultado[0].plano_remix["validacao_remix"]["remix_valido"])

    def test_comparacao_carrega_remix_no_editor_correto(self):
        from dataclasses import asdict
        from modules.analise_ia import candidato_de_plano
        from modules.ai.comparacao import episode_identity
        from ui.tela_lote import TelaLote
        tela = object.__new__(TelaLote)
        tela.app = Mock(estado={'transcricao':self.t,'resultado_comparacao':{
            'episode_identity':episode_identity(self.t),'modo':'normal','provider':'groq',
            'itens':[asdict(candidato_de_plano(self.p))]}})
        manual = Corte(1,58,'Manual')
        tela._listas_modo = {'normal':[manual],'remix':[]}
        tela._modo_atual = 'normal'
        tela.var_modo_edicao = Mock()
        tela.btn_ia = Mock()
        tela.lbl_status = Mock()
        tela._redesenhar_lista = Mock()
        tela._receber_comparacao()
        self.assertEqual('remix',tela._modo_atual)
        self.assertEqual([manual],tela._listas_modo['normal'])
        self.assertEqual('remix',tela.cortes[0].origem)


class TesteRecuperacao(unittest.TestCase):
    setUp = TesteContratoUniversal.setUp
    def buscar(self,respostas,limite=4):
        provedor = ProvedorSequencial(respostas)
        cfg = ConfigIA(modelo="gemini-teste",max_chamadas_episodio=limite)
        lista = buscar_remixes(self.t,cfg,provedor)
        self.assertLessEqual(provedor.chamadas,limite)
        return lista,provedor

    def test_primeira_zero_aciona_recuperacao(self):
        lista,p = self.buscar([{"historias":[]},{"historias":[remix_em(63)]}],2)
        self.assertEqual(1,len(lista))
        self.assertIn("recuperacao_global",lista.diagnostico.etapas)
        self.assertIn("OUTRAS historias",p.prompts[1])

    def test_todos_rejeitados_marca_zero_suspeito(self):
        lista,_ = self.buscar([{"historias":[normal_em(1)]}],2)
        self.assertTrue(lista.diagnostico.zero_suspeito)
        self.assertEqual(1,lista.diagnostico.candidatos_recebidos)
        self.assertEqual(0,lista.diagnostico.candidatos_parseados)

    def test_recuperacao_recebe_rejeicoes_e_intervalos(self):
        lista,p = self.buscar([{"historias":[normal_em(1)]},{"historias":[remix_em(63)]}],2)
        self.assertEqual(1,len(lista))
        self.assertIn("1.0, 58.0",p.prompts[1])
        self.assertIn("sem gancho",p.prompts[1])

    def test_recuperacao_nao_repete_intervalo_rejeitado(self):
        lista,_ = self.buscar([{"historias":[normal_em(1)]},{"historias":[remix_em(1)]}],2)
        self.assertFalse(lista)
        self.assertTrue(any("intervalo anterior repetido" in m for m in lista.rejeicoes))

    def test_orcamento_um_nao_faz_segunda_chamada(self):
        lista,p = self.buscar([{"historias":[]}],1)
        self.assertEqual(1,p.chamadas)
        self.assertFalse(lista)
        self.assertTrue(lista.diagnostico.recuperacao_pendente)

    def test_recuperacao_linear_sempre_rejeitada(self):
        lista,_ = self.buscar([{"historias":[]},{"historias":[normal_em(63)]}],2)
        self.assertFalse(lista)

    def test_zero_final_diagnostico_claro(self):
        lista,_ = self.buscar([{"historias":[]}]*4)
        self.assertIn(MENSAGEM_ZERO,lista.mensagem)
        self.assertIn("chamadas: 4",lista.mensagem)
        self.assertTrue(lista.diagnostico.partes_analisadas)

    def test_zero_nao_vira_cinco_cortes_ruins(self):
        lista,_ = self.buscar([{"historias":[normal_em(1),normal_em(63),normal_em(125)]}]*4)
        self.assertFalse(lista)
        self.assertEqual(0,lista.diagnostico.remixes_validos)

    def test_janela_narrativa_preserva_timestamps_e_falas(self):
        blocos = blocos_narrativos(self.t)
        self.assertEqual(self.t.segmentos,[s for bloco in blocos for s in bloco])
        lista,_ = self.buscar([{"historias":[]},{"historias":[]},{"historias":[remix_em(1)]}],4)
        self.assertEqual(1,len(lista))
        self.assertIn("janela_narrativa",lista.diagnostico.etapas)

    def test_oportunidades_reserva_orcamento_e_mantem_contrato(self):
        lista,_ = self.buscar([{"historias":[]}]*3+[{"historias":[remix_em(63)]}],4)
        self.assertEqual(1,len(lista))
        self.assertIn("oportunidades",lista.diagnostico.etapas)
        self.assertTrue(lista[0].validacao_remix["remix_valido"])

    def test_revalidacao_local_repara_frase_sem_chamada_extra(self):
        item = remix_em(1)
        item["gancho"]["fim"] = item["gancho"]["inicio"] + 1
        lista,_ = self.buscar([{"historias":[item]}],1)
        self.assertEqual(1,len(lista))
        self.assertGreater(lista.diagnostico.candidatos_reparados,0)

    def test_cache_nao_reaceita_intervalo_excluido_na_recuperacao(self):
        cfg = ConfigIA(modelo="gemini-teste",max_chamadas_episodio=2)
        editorial.analisar_agencia(self.t,cfg,ProvedorSequencial([
            {"historias":[normal_em(1)]},{"historias":[remix_em(1)]}]))
        segunda = editorial.analisar_agencia(self.t,cfg,ProvedorSequencial([]))
        self.assertFalse(segunda)

    def test_remix_separado_tambem_recupera(self):
        respostas = [{"remixes":[]},{"remixes":[{
            "titulos":["Plano absurdo","Conflito na sala","Que surpresa inesperada"],
            "descricao":"Mesma historia", "gancho":{"inicio":25,"fim":29},
            "segmentos_narrativos":[{"inicio":1,"fim":25,"funcao":"contexto"},
                                      {"inicio":25,"fim":58,"funcao":"consequencia"}],
            "continuidade":"Uma mesma situacao com contexto e consequencia", "confianca":.9}]}]
        cfg = ConfigIA(modelo="gemini-teste",max_chamadas_episodio=2)
        lista = analisar_remixes(self.t,cfg,ProvedorSequencial(respostas),usar_cache=False)
        self.assertEqual(1,len(lista))
        self.assertEqual(REMIX_RULES_VERSION,lista[0].remix_rules_version)

    def test_orcamento_compartilhado_conta_fallback_e_erros(self):
        from modules.ai.fallback import ProviderFallback, OrcamentoChamadas
        from modules.ai.errors import ErroProvider
        cfg = ConfigIA(modelo="gemini-teste",fallback_automatico=True,max_chamadas_episodio=2)
        clientes = {p: Mock() for p in cfg.ordem_fallback}
        clientes['mistral'].analisar_uma_vez.side_effect = ErroProvider('limite',codigo=429)
        clientes['groq'].analisar_uma_vez.return_value = {'historias':[]}
        budget = OrcamentoChamadas(5)
        provider = ProviderFallback(cfg,orcamento=budget,
                                    factory=lambda p,**kwargs: clientes[p],dormir=lambda _:None)
        lista = buscar_remixes(self.t,cfg,provider)
        self.assertEqual(2,budget.usadas)
        self.assertEqual(2,lista.diagnostico.chamadas_feitas)
        clientes['gemini'].analisar_uma_vez.assert_not_called()

    def test_recuperacao_global_nao_faz_retry_interno(self):
        from modules.ai.fallback import ProviderFallback, OrcamentoChamadas
        from modules.ai.errors import ErroProvider
        cfg = ConfigIA(fallback_automatico=True,max_chamadas_episodio=3)
        clientes = {p: Mock() for p in cfg.ordem_fallback}
        clientes['mistral'].analisar_uma_vez.side_effect = [
            {'historias':[]}, ErroProvider('temporario',codigo=429)]
        clientes['groq'].analisar_uma_vez.return_value = {'historias':[]}
        budget = OrcamentoChamadas(3)
        provider = ProviderFallback(cfg,orcamento=budget,
                                    factory=lambda p,**kwargs: clientes[p],dormir=lambda _:None)
        lista = buscar_remixes(self.t,cfg,provider)
        self.assertEqual(3,budget.usadas)
        self.assertEqual(3,lista.diagnostico.chamadas_feitas)
        self.assertEqual(['primeira_analise','recuperacao_global','oportunidades'],
                         [p['etapa'] for p in lista.diagnostico.partes_analisadas])

    def test_cancelamento_interrompe_recuperacao(self):
        cancelada = Mock(cancelada=True)
        provider = ProvedorSequencial([{'historias':[]}])
        with self.assertRaisesRegex(RuntimeError,'cancelada'):
            buscar_remixes(self.t,self.config,provider,cancelada_ref=cancelada)
        self.assertEqual(0,provider.chamadas)

    def test_candidatos_malformados_nao_quebram_recuperacao(self):
        lista,_ = self.buscar([{'historias':[None,{}, {'gancho':{'inicio':'erro'}}]},
                              {'historias':[remix_em(63)]}],2)
        self.assertEqual(1,len(lista))
        self.assertEqual(4,lista.diagnostico.candidatos_recebidos)
        self.assertEqual(1,lista.diagnostico.candidatos_parseados)

    def test_cache_aprovado_revalida_sem_api(self):
        cfg = ConfigIA(modelo='gemini-teste',max_chamadas_episodio=1)
        editorial.analisar_agencia(self.t,cfg,ProvedorSequencial([{'historias':[remix_em(1)]}]))
        provider = ProvedorSequencial([])
        lista = editorial.analisar_agencia(self.t,cfg,provider)
        self.assertEqual(1,len(lista))
        self.assertEqual(0,provider.chamadas)
        self.assertTrue(lista[0].validacao_remix['remix_valido'])

    def test_regras_antigas_nao_aprovam_remix_sem_revalidacao(self):
        cfg = ConfigIA(modelo='gemini-teste',max_chamadas_episodio=1)
        caminho = editorial.caminho_cache_agencia(self.t,cfg)
        caminho.parent.mkdir(parents=True,exist_ok=True)
        item = normal_em(1)
        item['remix_rules_version'] = 'antiga'
        item['validacao_remix'] = {'remix_valido':True,'score_remix':100}
        caminho.write_text(json.dumps({'video_id':self.t.video_id,
                          'hash_transcricao':editorial._hash_transcricao(self.t),
                          'historias_brutas':[item]}))
        lista = editorial.analisar_agencia(self.t,cfg,ProvedorSequencial([]))
        self.assertFalse(lista)

    def test_remix_ui_compartilha_cota_e_cria_cliente_apenas_quando_precisa(self):
        from modules.ai.comparacao import orcamento_episodio
        from modules.remix_narrativo import provedor_remix_orcado
        estado = {}
        budget = orcamento_episodio(estado,self.t,2)
        budget.reservar()
        cfg = ConfigIA(modelo='gemini-teste',max_chamadas_episodio=2)
        cliente = ProvedorSequencial([{'remixes':[]}])
        with patch('modules.remix_narrativo.criar_provedor_remix',return_value=cliente) as criar:
            provider = provedor_remix_orcado(estado,self.t,cfg)
            criar.assert_not_called()
            lista = analisar_remixes(self.t,cfg,provider,usar_cache=False)
        self.assertFalse(lista)
        self.assertEqual(2,budget.usadas)
        self.assertEqual(1,cliente.chamadas)


if __name__ == "__main__":
    unittest.main()
