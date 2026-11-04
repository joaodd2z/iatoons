"""Production recovery regression. Network is forbidden, including local APIs."""
from copy import deepcopy
from pathlib import Path
import time
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from modules.agencia import producao as P
from modules.agencia.editorial import PlanoAgencia, ListaPlanos, validar_planos
from modules.agencia.titulos_manual import selecionar_prontos, editar_titulo
from modules.transcricao import Transcricao, SegmentoTranscricao, carregar_transcricao


def episodio():
    textos = ['Quero salvar o carro de Pedro antes da corrida.',
              'Pedro, o carro nao pode entrar nessa corrida!',
              'Mas Pedro insiste no carro e desafia a regra.',
              'Agora o carro de Pedro esta com um problema.',
              'Pedro, por isso o carro nao pode competir!',
              'Na verdade o carro de Pedro perdeu o motor.',
              'Entao Pedro resolveu reparar o carro.',
              'Pedro conseguiu salvar o carro antes da corrida.']
    return Transcricao('fake.mp4','teste-producao','small','pt','pt',1,60,'',
                       [SegmentoTranscricao(1+4*i,4+4*i,s) for i,s in enumerate(textos)])


class TestRecoveryProducao(unittest.TestCase):
    def setUp(self):
        self.connect = patch('socket.socket.connect',side_effect=AssertionError('API proibida'))
        self.connect_ex = patch('socket.socket.connect_ex',side_effect=AssertionError('API proibida'))
        self.connect.start(); self.connect_ex.start()
        self.addCleanup(self.connect.stop); self.addCleanup(self.connect_ex.stop)
        self.t = episodio()

    def test_five_approved_skip_recovery(self):
        planos = [PlanoAgencia(1,32) for _ in range(5)]
        with patch.object(P,'janelas_locais',side_effect=AssertionError('Sem recovery')):
            self.assertIs(planos,P.recuperar_producao(planos,self.t))

    def test_three_approved_start_recovery(self):
        planos = [PlanoAgencia(40,59,segmentos=[dict(inicio=40,fim=59)]) for _ in range(3)]
        r = P.recuperar_producao(planos,self.t)
        self.assertEqual('RECOVERY',r.diagnostico.modo)
        self.assertTrue(r.diagnostico.revisaveis)

    def test_zero_starts_deep_recovery(self):
        r = P.recuperar_producao([],self.t)
        self.assertEqual('DEEP RECOVERY',r.diagnostico.modo)
        self.assertTrue(r)
        self.assertTrue(all(p.estado_editorial == 'revisao' for p in r))

    def test_giant_never_reaches_validator(self):
        dado = dict(segmentos=[dict(inicio=0,fim=500)],inicio=0,fim=500)
        with patch('modules.agencia.remix.verificar_plano_remix',side_effect=AssertionError('Gigante')):
            planos,rejeicoes = validar_planos([dado],self.t)
        self.assertFalse(planos)
        self.assertIn('gigante',rejeicoes[0])
        r = P.recuperar_producao([],self.t,sementes=[dado])
        self.assertTrue(all(p.fim-p.inicio <= 180 for p in r))

    def test_review_survives_ui_filter_and_persistence(self):
        from ui.tela_agencia import TelaAgencia
        from types import SimpleNamespace
        r = P.recuperar_producao([],self.t)
        salvo = PlanoAgencia.de_dict(r[0].como_dict())
        tela = SimpleNamespace(_intro=lambda _:None,_rejeicoes=[])
        self.assertEqual([salvo],TelaAgencia._somente_remix(tela,[salvo],self.t))

    def test_bad_content_rejected(self):
        self.t.segmentos = [SegmentoTranscricao(1+4*i,4+4*i,'La la la musica la la') for i in range(8)]
        self.assertFalse(P.recuperar_producao([],self.t))

    def test_manual_approval_unlocks_and_edits_invalidate(self):
        p = P.recuperar_producao([],self.t)[0]
        P.aprovar_corte(p,self.t,'Decisao editorial sobre o carro')
        self.assertTrue(P.manual_confirmado(p))
        prontos,revisao = selecionar_prontos([p],self.t)
        self.assertEqual([p],prontos)
        self.assertFalse(revisao)
        editar_titulo(p,'Outro titulo')
        self.assertFalse(P.manual_confirmado(p))
        p.selecionado = True
        self.assertFalse(selecionar_prontos([p],self.t)[0])

    def test_manual_does_not_override_technical_errors(self):
        p = P.recuperar_producao([],self.t)[0]
        p.segmentos[0]['fim'] = 999
        with self.assertRaises(ValueError):
            P.aprovar_corte(p,self.t,'Titulo humano')

    def test_ollama_offline_does_not_block(self):
        def offline(_):
            raise ConnectionError('offline')
        r = P.recuperar_producao([],self.t,juiz=offline)
        self.assertTrue(r)
        self.assertIn('fallback local',r.diagnostico.falhas[0])

    def test_ollama_timeout_does_not_block(self):
        def lento(_):
            time.sleep(.1)
        inicio = time.perf_counter()
        r = P.recuperar_producao([],self.t,juiz=lento,timeout_juiz=.01)
        self.assertTrue(r)
        self.assertLess(time.perf_counter()-inicio,.09)
        self.assertIn('TimeoutError',r.diagnostico.falhas[0])

    def test_episode_coverage_is_broad(self):
        self.t.duracao = 900
        original = deepcopy(self.t.segmentos)
        self.t.segmentos += [SegmentoTranscricao(s.inicio+700,s.fim+700,s.texto) for s in original]
        r = P.recuperar_producao([],self.t)
        partes = r.diagnostico.partes_analisadas
        self.assertEqual(0,partes[0]['inicio'])
        self.assertEqual(900,partes[-1]['fim'])
        self.assertTrue(partes[-2]['avaliados'])

    def test_no_content_has_clear_diagnostic(self):
        self.t.segmentos = []
        r = P.recuperar_producao([],self.t)
        self.assertFalse(r)
        self.assertIn('Nenhuma oportunidade sustentada',r.mensagem)

    def test_never_fabricates_five(self):
        r = P.recuperar_producao([],self.t)
        self.assertLess(len(r),5)
        for p in r:
            self.assertTrue(P.revisavel(p,self.t))
            self.assertFalse(p.titulo_auto_aprovado)

    def test_s07e11_cached_regression(self):
        caminho = Path('dados/transcricoes/db972adc2049a2c9dfa68ff69302490cb013ee26e166a14f10082942eff11ea4.json')
        if not caminho.is_file():
            self.skipTest('Cache real S07E11 nao disponivel')
        t = carregar_transcricao(caminho)
        before = caminho.read_bytes()
        r = P.recuperar_producao([],t)
        self.assertTrue(r)
        self.assertTrue(all(p.fim-p.inicio <= 150 and P.revisavel(p,t) for p in r))
        self.assertTrue(any(p.inicio <= 1080 <= p.fim for p in r))
        self.assertEqual(before,caminho.read_bytes())

    def test_s07e11_zero_cache_recovers_without_another_api(self):
        from modules.agencia.editorial import analisar_agencia
        from modules.config_ia import carregar_config_ia
        from modules.titulos_v2 import executar_analise_com_titulos
        from unittest.mock import Mock
        caminho = Path('dados/transcricoes/db972adc2049a2c9dfa68ff69302490cb013ee26e166a14f10082942eff11ea4.json')
        if not caminho.is_file():
            self.skipTest('Cache S07E11 indisponivel')
        t = carregar_transcricao(caminho)
        provider = Mock()
        provider.listar_modelos.side_effect = AssertionError('Nao chamar API')
        provider.analisar.side_effect = AssertionError('Nao chamar API')
        r = executar_analise_com_titulos(t,carregar_config_ia(),analise=analisar_agencia,
                                       provedor=provider,provedor_titulos=provider,gerar=False)
        self.assertTrue(r)
        self.assertFalse(provider.mock_calls)
        self.assertEqual(0,r.diagnostico.chamadas_feitas)

    def test_whole_cut_manual_approval_exports_without_old_editorial_gate(self):
        from modules.agencia import pipeline as E
        from modules.agencia.timeline import construir_timeline
        from modules.agencia.qualidade import ItemGate
        from unittest.mock import Mock
        p = P.recuperar_producao([],self.t)[0]
        P.aprovar_corte(p,self.t,'O carro de Pedro na corrida')
        perfil = SimpleNamespace(id='teste',nome_canal='teste')
        prep = SimpleNamespace(timeline=construir_timeline(p,self.t),
            gate=[ItemGate('REMIX',False,'Score editorial antigo'),ItemGate('SINCRONIZACAO',True)],
            correcoes=[],info=Mock(),enquadramento=SimpleNamespace(tipo_fonte='teste',
            estrategia='teste',fracao_visivel=1),titulo=Mock(),legendas=[],aplicar_tratamento=False)
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(E,'RENDER_DIR',Path(tmp)),patch.object(E,'FRAMES_DIR',Path(tmp)), \
             patch.object(E,'obter_perfil',return_value=perfil), \
             patch.object(E,'preparar_corte',return_value=prep), \
             patch.object(E,'construir_comando',return_value=[]), \
             patch.object(E,'executar_ffmpeg',return_value={'sucesso':True}) as render, \
             patch.object(E,'validar_frames',return_value=([ItemGate('FRAME',True)],[])), \
             patch.object(E,'relatorio_exportacao',return_value={}), \
             patch.object(E,'validar_exportacao',return_value=[]), \
             patch.object(E.shutil,'move'),patch.object(E,'montar_metricas',return_value={}), \
             patch.object(E,'salvar_metricas',return_value=Path(tmp)/'metricas.json'):
            resultado = E.exportar_corte_agencia('fake.mp4',p,self.t,tmp,gerar_capa=False,exportar_srt=False)
            self.assertTrue(resultado['sucesso'])
            self.assertEqual(1,render.call_count)
            prep.gate.append(ItemGate('SINCRONIZACAO',False,'Erro tecnico'))
            resultado = E.exportar_corte_agencia('fake.mp4',p,self.t,tmp,gerar_capa=False,exportar_srt=False)
            self.assertFalse(resultado['sucesso'])
            self.assertEqual(1,render.call_count)


if __name__ == '__main__':
    unittest.main()
