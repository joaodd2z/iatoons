"""Focused rollback tests; no cloud or local model APIs."""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from modules.agencia import rollback_2809 as R
from modules.agencia import story_identity as S
from modules.agencia.editorial import PlanoAgencia,_SomenteCache,analisar_agencia
from modules.config_ia import ConfigIA,carregar_config_ia
from modules.transcricao import carregar_transcricao
from test_agencia_editorial import episodio_longo,remix_em


class TestRollback2809(unittest.TestCase):
    def setUp(self):
        for nome in ('socket.socket.connect','socket.socket.connect_ex'):
            p = patch(nome,side_effect=AssertionError('Nenhuma API real'))
            p.start()
            self.addCleanup(p.stop)

    def feat(self,**extras):
        base = dict(video_id='ep',inicio=10,fim=80,personagens=['brian'],evento=['urina','teste'],
                    conflito=['teste','urina'],setup=['teste','condicional'],payoff=['descoberto','urina'])
        base.update(extras)
        return S.IdentidadeNarrativa(**base)

    def test_hooks_nao_criam_identidade(self):
        t = episodio_longo('rollback-hooks')
        a = PlanoAgencia(1,58,video_id=t.video_id,descricao='Teste de urina de Brian',confianca=.9,
                         segmentos=[dict(inicio=1,fim=58)],gancho=dict(inicio=20,fim=24))
        b = deepcopy(a)
        b.id = 'outro'
        b.gancho = dict(inicio=30,fim=34)
        aceitos,rejeitados = S.selecionar([a,b],t)
        self.assertEqual(1,len(aceitos))
        self.assertEqual(a.story_identity,b.story_identity)
        self.assertEqual(1,len(rejeitados))

    def test_subdivisoes_mesmo_parent_sao_uma_historia(self):
        self.assertTrue(S.mesma_historia(self.feat(parent='evento'),self.feat(inicio=80,fim=140,parent='evento')))

    def test_mesmo_personagem_eventos_diferentes_nao_colapsam(self):
        a = self.feat()
        b = self.feat(inicio=500,fim=580,evento=['livro','suborno'],conflito=['livro','campanha'],
                      setup=['publicar','livro'],payoff=['livro','fracasso'])
        self.assertFalse(S.mesma_historia(a,b))

    def test_conflito_setup_payoff_repetidos_em_outra_regiao(self):
        self.assertTrue(S.mesma_historia(self.feat(),self.feat(inicio=500,fim=580)))

    def test_outro_episodio_nao_mescla(self):
        self.assertFalse(S.mesma_historia(self.feat(),self.feat(video_id='outro')))

    def test_maximo_pedido_nao_vira_minimo(self):
        t = episodio_longo('rollback-max')
        cfg = ConfigIA(modelo='fake',max_cortes=10)
        r = R.montar([remix_em(1),remix_em(125),remix_em(249)],t,cfg)
        self.assertEqual(3,len(r))
        self.assertEqual(0,r.diagnostico.minimum_target)

    def test_cache_ep12_sem_auto_recovery_ollama_ou_api(self):
        t = carregar_transcricao('dados/transcricoes/cf72a56c1d3b56d562dd3ef52d68fa38371cceaa189c98a39275217016b761ad.json')
        c = carregar_config_ia()
        c.max_cortes = 10
        with patch('modules.agencia.auto_recovery.auto_recuperar',side_effect=AssertionError('Auto proibido')), \
             patch('modules.agencia.producao.recuperar_producao',side_effect=AssertionError('Recovery proibido')), \
             patch('modules.agencia.producao.julgar_opcional',side_effect=AssertionError('Ollama proibido')):
            r = analisar_agencia(t,c,provedor=_SomenteCache())
        self.assertEqual(4,len(r))
        self.assertEqual(4,len({p.story_identity for p in r}))
        self.assertTrue(all(p.validacao_remix['remix_valido'] for p in r))
        self.assertEqual(0,r.diagnostico.chamadas_feitas)

    def test_prompt_original_sem_meta_minima(self):
        t = episodio_longo('rollback-prompt')
        prompt = R.prompt_2809(t.segmentos,10)
        self.assertIn('no m',prompt)
        self.assertNotIn('minimum_target=5',prompt)
        self.assertIn('Quantidade e limite',prompt)

    def test_remix_independente_nao_volta_ao_recovery(self):
        from modules.remix_narrativo import analisar_remixes
        t = carregar_transcricao('dados/transcricoes/cf72a56c1d3b56d562dd3ef52d68fa38371cceaa189c98a39275217016b761ad.json')
        with patch('modules.agencia.recuperacao.buscar_remixes',side_effect=AssertionError('Recovery proibido')):
            r = analisar_remixes(t,carregar_config_ia(),provedor=_SomenteCache())
        self.assertEqual(4,len(r))
        self.assertEqual(4,len({p.story_identity for p in r}))

    def test_input_malformado_nao_aborta_outros(self):
        t = episodio_longo('rollback-json')
        r = R.montar([None,dict(gancho={},segmentos=[{}]),remix_em(1)],t,ConfigIA(modelo='fake'))
        self.assertEqual(1,len(r))


if __name__=='__main__':
    unittest.main()
