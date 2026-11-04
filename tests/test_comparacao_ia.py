"""Comparação local com providers simulados e sockets bloqueados."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_analise_ia import transcricao_teste
from test_agencia_editorial import episodio_longo, remix_em, normal_em
from modules.ai import comparacao as c
from modules.ai.errors import ErroProvider
from modules.ai.fallback import LimiteChamadas, OrcamentoChamadas
from modules.config_ia import ConfigIA


def candidato(inicio=0, fim=60):
    return {'inicio': inicio, 'fim': fim, 'descricao': 'História com final',
            'titulos': ['Plano absurdo', 'A briga começou', 'Ninguém esperava'],
            'gancho': {'inicio': 20, 'fim': 24, 'motivo': 'Surpresa'},
            'segmentos': [{'inicio': inicio, 'fim': fim, 'funcao': 'contexto'}],
            'confianca': .9, 'motivo': 'Consequência clara'}


class TestComparacaoIA(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pasta = Path(self.tmp.name)
        for alvo in ('socket.socket.connect', 'socket.socket.connect_ex'):
            p = patch(alvo, side_effect=AssertionError('Rede proibida nos testes'))
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(c, 'obter_chave', return_value='chave-ficticia-secreta')
        p.start()
        self.addCleanup(p.stop)
        p = patch('modules.intro.INTROS_DIR', self.pasta/'intro')
        p.start()
        self.addCleanup(p.stop)
        self.cfg = ConfigIA(fallback_automatico=True, max_chamadas_episodio=5)
        from modules.transcricao import SegmentoTranscricao
        self.t = transcricao_teste()
        self.t.segmentos = [SegmentoTranscricao(float(i),float(i+4),f'Fala {i}.') for i in range(0,240,4)]
        self.providers = {p: Mock() for p in c.PROVIDERS}
        for provider in self.providers.values():
            provider.analisar_uma_vez.return_value = {'historias': [candidato()]}
        self.factory = Mock(side_effect=lambda nome, **kwargs: self.providers[nome])

    def preparar(self, providers=('gemini', 'groq'), **kwargs):
        return c.ComparacaoIA(self.t, self.cfg, providers, factory=self.factory,
                             pasta_cache=self.pasta/'cache', **kwargs)

    def test_dois_ok_mesma_entrada_e_sem_fallback(self):
        comparacao = self.preparar()
        self.factory.assert_not_called()
        self.assertEqual(2, comparacao.chamadas_previstas)
        r = comparacao.executar(confirmado=True)
        self.assertEqual({'gemini', 'groq'}, set(r['resultados']))
        self.assertEqual(2, r['chamadas_utilizadas'])
        for p in ('gemini', 'groq'):
            self.providers[p].analisar_uma_vez.assert_called_once_with(
                getattr(self.cfg, f'modelo_{p}'), comparacao.prompt)
            self.providers[p].analisar.assert_not_called()
            self.providers[p].listar_modelos.assert_not_called()
            self.assertIsNone(r['resultados'][p]['erro'])
        self.providers['mistral'].analisar_uma_vez.assert_not_called()
        schemas = [call.kwargs['schema'] for call in self.factory.call_args_list]
        self.assertIs(schemas[0], schemas[1])

    def test_erro_individual_429_continua(self):
        self.providers['groq'].analisar_uma_vez.side_effect = ErroProvider('segredo', codigo=429)
        r = self.preparar(('groq', 'gemini')).executar(confirmado=True)
        self.assertEqual(429, r['resultados']['groq']['erro']['codigo'])
        self.assertIsNone(r['resultados']['gemini']['erro'])
        self.providers['groq'].analisar_uma_vez.assert_called_once()

    def test_tres_providers(self):
        r = self.preparar(c.PROVIDERS).executar(confirmado=True)
        self.assertEqual(3, r['chamadas_utilizadas'])
        self.assertEqual(3, len(r['resultados']))

    def test_mesma_cena_detectada_localmente(self):
        self.assertTrue(c.mesma_cena((120, 165), (123, 168)))
        self.assertFalse(c.mesma_cena((120, 165), (200, 245)))
        r = self.preparar().executar(confirmado=True)
        self.assertEqual('Mesma cena provável', r['cenas_compartilhadas'][0]['mensagem'])

    def test_duplicados_e_score_tecnico(self):
        dados = {'candidatos': [candidato(), candidato()]}
        r = c.avaliar_resposta(dados, self.t, 'normal', 5)
        self.assertEqual(1, r['candidatos_validos'])
        self.assertEqual(1, r['candidatos_rejeitados'])
        self.assertTrue(r['candidatos'][1]['duplicado'])
        self.assertEqual(100, r['candidatos'][0]['score_tecnico'])
        self.assertEqual(50, r['score_titulo_estrutural'])
        self.assertEqual(50, r['score_remix'])  # 100 do remix aprovado, 0 do duplicado.
        self.assertIsNone(r['candidatos'][0]['verificacoes']['presenca_contexto'])
        self.assertLess(r['candidatos'][1]['score_tecnico'], 100)

    def test_score_penaliza_intro_fala_cortada_fora_episodio_e_titulo_vazio(self):
        item = candidato(1, 300)
        item['titulos'] = ['']
        r = c.avaliar_resposta({'candidatos': [item]}, self.t, 'normal', 5)
        checks = r['candidatos'][0]['verificacoes']
        self.assertFalse(checks['fala_inteira'])
        self.assertFalse(checks['dentro_episodio'])
        self.assertFalse(checks['titulos_nao_vazios'])
        self.assertLess(r['score_tecnico'], 100)

    def test_agencia_ignora_normais_preserva_prompt_e_schema(self):
        self.t = episodio_longo('comparacao-agencia')
        historias = [remix_em(1), normal_em(125)]
        for provider in self.providers.values():
            provider.analisar_uma_vez.return_value = {'historias': historias}
        comparacao = self.preparar(modo='agencia')
        r = comparacao.executar(confirmado=True)
        for resultado in r['resultados'].values():
            self.assertEqual(1, resultado['candidatos_validos'])
            self.assertEqual(1, resultado['remixes_validos'])
            self.assertEqual(0, resultado['candidatos'][1]['score_tecnico'])
            self.assertEqual(0, resultado['candidatos'][1]['score_remix'])
            self.assertEqual(0, resultado['candidatos'][1]['score_titulo_estrutural'])
        self.assertIn('SOMENTE REMIXES NARRATIVOS', comparacao.prompt)
        for call in self.factory.call_args_list:
            self.assertEqual('agencia', call.kwargs['modo'])
            self.assertIs(c.editorial._RespostaAgencia, call.kwargs['schema'])

    def test_confirmacao_obrigatoria_e_execucao_unica(self):
        comparacao = self.preparar()
        with self.assertRaises(ValueError):
            comparacao.executar()
        self.factory.assert_not_called()
        comparacao.executar(confirmado=True)
        with self.assertRaises(ValueError):
            comparacao.executar(confirmado=True)

    def test_limite_impede_toda_cadeia_e_conta_falha(self):
        budget = OrcamentoChamadas(5, usadas=4)
        with self.assertRaises(LimiteChamadas):
            self.preparar(orcamento=budget).executar(confirmado=True)
        self.factory.assert_not_called()
        self.providers['groq'].analisar_uma_vez.side_effect = TimeoutError('segredo')
        budget = OrcamentoChamadas(5, usadas=3)
        r = self.preparar(orcamento=budget).executar(confirmado=True)
        self.assertEqual(5, budget.usadas)
        self.assertEqual(2, r['chamadas_utilizadas'])

    def test_cache_separado_provider_modelo_prompt_regras_tipo(self):
        self.preparar().executar(confirmado=True)
        comparacao = self.preparar(reutilizar_cache=True)
        self.assertEqual(['gemini', 'groq'], comparacao.caches_disponiveis)
        self.assertEqual(0, comparacao.chamadas_previstas)
        self.factory.reset_mock()
        comparacao.executar(confirmado=True)
        self.factory.assert_not_called()
        self.cfg.modelo_groq = 'outro-modelo'
        self.assertEqual(['gemini'], self.preparar(reutilizar_cache=True).caches_disponiveis)
        self.assertEqual([], self.preparar(max_candidatos=6).caches_disponiveis)
        self.assertEqual([], self.preparar(modo='agencia').caches_disponiveis)
        with patch.object(c, 'VERSAO_SCORE', 2):
            self.assertEqual([], self.preparar().caches_disponiveis)

    def test_cache_isola_episodio_e_transcricao(self):
        self.preparar().executar(confirmado=True)
        self.t.segmentos[0].texto = 'Outra fala'
        self.assertEqual([], self.preparar(reutilizar_cache=True).caches_disponiveis)

    def test_relatorio_e_cache_sem_chaves(self):
        item = candidato()
        item['descricao'] = 'chave-ficticia-secreta'
        item['api_key'] = 'chave-ficticia-secreta'
        self.providers['gemini'].analisar_uma_vez.return_value = {'candidatos': [item]}
        self.providers['groq'].analisar_uma_vez.side_effect = Exception('chave-ficticia-secreta')
        comp = self.preparar()
        comp.executar(confirmado=True)
        destino = self.pasta/'relatorio.json'
        comp.salvar(destino)
        for arquivo in self.pasta.rglob('*.json'):
            texto = arquivo.read_text(encoding='utf-8')
            self.assertNotIn('chave-ficticia-secreta', texto)
            self.assertNotIn('api_key', texto)
        r = json.loads(destino.read_text(encoding='utf-8'))
        self.assertIn('data_hora', r)
        self.assertIn('episode_identity', r['episodio'])

    def test_usar_resultado_escolhido_sem_chamadas_extras(self):
        r = self.preparar().executar(confirmado=True)
        app = Mock(estado={'transcricao': self.t})
        self.factory.reset_mock()
        c.escolher_resultado(app, r, 'groq')
        app.navegar.assert_called_once_with('lote')
        escolhido = c.consumir_resultado(app.estado, 'normal')
        self.assertEqual('groq', escolhido['provider'])
        self.assertEqual(r['resultados']['groq']['itens_validos'], escolhido['itens'])
        self.assertIsNone(c.consumir_resultado(app.estado, 'normal'))
        self.factory.assert_not_called()

    def test_usar_resultado_apos_trocar_episodio_e_bloqueado(self):
        r = self.preparar().executar(confirmado=True)
        outro = deepcopy(self.t)
        outro.video_id = 'outro'
        app = Mock(estado={'transcricao': outro})
        with self.assertRaises(ValueError):
            c.escolher_resultado(app, r, 'gemini')
        app.navegar.assert_not_called()

    def test_orcamento_da_ui_preservado_entre_fluxos(self):
        estado = {}
        budget = c.orcamento_episodio(estado, self.t, 5)
        budget.reservar()
        self.assertIs(budget, c.orcamento_episodio(estado, self.t, 5))
        provider = c.provider_orcado(estado, self.t, self.cfg)
        self.assertIs(budget, provider.orcamento)
        self.preparar(orcamento=budget).executar(confirmado=True)
        self.assertEqual(3, budget.usadas)

    def test_agencia_cinco_remixes_tres_normais_contam_cinco(self):
        t = episodio_longo('cinco-remixes')
        itens = [remix_em(i) for i in (1, 125, 249, 373, 435)]
        sem_normais = c.avaliar_resposta({'historias': itens}, t, 'agencia', 10)
        resultado = c.avaliar_resposta({'historias': itens + [normal_em(i) for i in (63, 187, 311)]}, t, 'agencia', 10)
        self.assertEqual(5, resultado['candidatos_validos'])
        self.assertEqual(5, resultado['remixes_validos'])
        self.assertEqual(3, resultado['candidatos_rejeitados'])
        for campo in ('score_tecnico', 'score_remix', 'score_titulo_estrutural'):
            self.assertLessEqual(resultado[campo], sem_normais[campo])

    def test_json_invalido_score_zero_sem_impedir_outro_provider(self):
        self.providers['groq'].analisar_uma_vez.return_value = {'candidatos': 'JSON estruturalmente inválido'}
        r = self.preparar().executar(confirmado=True)
        self.assertIs(False, r['resultados']['groq']['json_valido'])
        self.assertEqual(0, r['resultados']['groq']['score_tecnico'])
        self.assertIsNone(r['resultados']['gemini']['erro'])

    def test_intro_tem_penalidade_local(self):
        from modules.intro import IntervaloIntro
        with patch.object(c, 'carregar_intro', return_value=IntervaloIntro(0, 10)):
            r = c.avaliar_resposta({'candidatos': [candidato()]}, self.t, 'normal', 5)
        self.assertFalse(r['candidatos'][0]['verificacoes']['ausencia_intro'])

    def test_orcamento_sem_fallback_usa_uma_geracao_e_metadados(self):
        from modules.ai.response import com_metadados
        from time import perf_counter
        cfg = ConfigIA(provider_ativo='groq', fallback_automatico=False)
        estado = {}
        cliente = Mock()
        cliente.analisar_uma_vez.return_value = com_metadados({'candidatos': []}, 'groq', cfg.modelo, perf_counter())
        with patch.object(c, 'obter_provider', return_value=cliente):
            p = c.provider_orcado(estado, self.t, cfg)
            resposta = p.analisar(cfg.modelo, 'prompt intacto')
        self.assertEqual(1, estado['orcamento_ia']['orcamento'].usadas)
        self.assertEqual('groq', resposta['provider_utilizado'])
        cliente.analisar_uma_vez.assert_called_once_with(cfg.modelo, 'prompt intacto')
        cliente.analisar.assert_not_called()


if __name__ == '__main__':
    unittest.main()
