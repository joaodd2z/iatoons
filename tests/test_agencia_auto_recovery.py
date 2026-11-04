"""Bounded automatic recovery, including real cached episodes. No network."""
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from modules.agencia import auto_recovery as A
from modules.agencia.editorial import ListaPlanos
from modules.config_ia import ConfigIA
from modules.transcricao import carregar_transcricao
from test_agencia_editorial import episodio_longo, remix_em, normal_em

ROOT = Path(__file__).resolve().parents[1]
EP12 = 'cf72a56c1d3b56d562dd3ef52d68fa38371cceaa189c98a39275217016b761ad'
RAW12 = '959279ed9b068390d38d9db3777c821df7b0028948541211d568e92ff3d278b4'


class TestAutoRecovery(unittest.TestCase):
    def setUp(self):
        for nome in ('socket.socket.connect','socket.socket.connect_ex'):
            p = patch(nome,side_effect=AssertionError('API real proibida'))
            p.start()
            self.addCleanup(p.stop)
        self.t = episodio_longo('auto-recovery-test')
        self.cfg = ConfigIA(modelo='fake',max_cortes=5,max_chamadas_episodio=4)

    def executar(self,sementes=(),**kwargs):
        return A.auto_recuperar(ListaPlanos([]),self.t,self.cfg,sementes=sementes,**kwargs)

    def test_motivos_estruturados(self):
        p = A.plano_bruto(remix_em(1),self.t)
        motivos = A.motivos_estruturados(p,['fala truncada','payoff disponivel foi removido',
                                          'historia nao reencontra narrativamente o gancho'])
        self.assertEqual(['fala_truncada','payoff_removido','sem_reencontro'],[m['motivo'] for m in motivos])
        self.assertTrue(all(m['candidate_id']==p.id and m['dados']['segmentos'] and m['acao'] for m in motivos))

    def test_gigante_nao_chega_ao_validator(self):
        p = A.plano_bruto(remix_em(1,segmentos=[dict(inicio=1,fim=255)]),self.t)
        with patch.object(A,'validar_planos',side_effect=AssertionError('Gigante no validator')):
            _,m = A.avaliar(p,self.t)
        self.assertEqual('duracao_excessiva',A.motivo_codigo(m[0]))
        locais = [(A.plano_bruto(remix_em(63),self.t),{})]
        self.assertEqual(1,len(A.subdividir(p,locais)))
        self.assertLess(A.subdividir(p,locais)[0][0].fim,150)

    def test_fala_expande_e_protege_frase(self):
        p = A.plano_bruto(remix_em(1),self.t)
        p.segmentos[0]['inicio'] = 2
        p.segmentos[-1]['fim'] = 56
        novo = A.alinhar(p,self.t)
        self.assertEqual(1,novo.inicio)
        self.assertGreater(novo.fim,56)
        self.assertTrue(novo.falas_protegidas)

    def test_payoff_restaurado(self):
        p = A.plano_bruto(remix_em(1),self.t)
        payoff = dict(inicio=49,fim=53)
        p.estrutura['conclusao'] = payoff
        p.remover = [dict(inicio=48,fim=54)]
        variantes,acoes = A.reparar(p,self.t,A.motivos_estruturados(p,['payoff removido']),1)
        self.assertFalse(variantes[0].remover)
        self.assertEqual(payoff,variantes[0].estrutura['reacao'])
        self.assertIn('restaurar_payoff_original',acoes)

    def test_hook_retorno_e_alternativas_reais(self):
        p = A.plano_bruto(remix_em(1),self.t)
        variantes,acoes = A.reparar(p,self.t,A.motivos_estruturados(p,['nao reencontra gancho']),1)
        self.assertIn('restaurar_chegada_ao_gancho',acoes)
        self.assertLess(variantes[0].estrutura['momento_forte']['inicio'],p.gancho['inicio'])
        hooks = A.hooks_reais(p,self.t)
        self.assertLessEqual(len(hooks),3)
        self.assertTrue(hooks)
        self.assertTrue(all(h.texto and p.inicio<h.inicio<h.fim<p.fim for h in hooks))

    def test_duplicata_busca_proxima_ate_minimo(self):
        r = self.executar([remix_em(1),remix_em(1)]+[remix_em(1+62*i) for i in range(1,6)])
        self.assertEqual(5,len(r))
        self.assertEqual(5,len({p.inicio for p in r}))
        self.assertTrue(all(p.selecionado and p.validacao_remix['remix_valido'] for p in r))

    def test_cobertura_e_ranking_antes_de_ia(self):
        d = A.DiagnosticoAutoRecovery()
        oportunidades = A.oportunidades(self.t,d,descoberta=True)
        self.assertTrue(oportunidades)
        self.assertEqual([0,180,360,540],[r['inicio'] for r in d.partes_analisadas])
        self.assertTrue(all(r['avaliados']>0 for r in d.partes_analisadas))
        self.assertTrue(all(p.fim-p.inicio<=150 for p,_ in oportunidades))

    def test_maximo_dois_reparos_sem_loop(self):
        p = remix_em(1)
        with patch.object(A,'avaliar',return_value=(None,['falha editorial persistente'])):
            r = self.executar([p])
        contagem = Counter(m['candidate_id'] for m in r.diagnostico.reparos)
        self.assertTrue(contagem)
        self.assertLessEqual(max(contagem.values()),2)
        self.assertFalse(r)

    def test_linear_nao_vira_remix_artificial(self):
        r = self.executar([normal_em(1)])
        self.assertFalse(r)
        self.assertFalse(r.diagnostico.reparos)

    def test_ollama_offline_opcional(self):
        def offline(_):
            raise ConnectionError('offline')
        r = self.executar([remix_em(1)],juiz=offline)
        self.assertEqual(1,len(r))
        self.assertTrue(r.diagnostico.falhas)

    def test_ia_respeita_orcamento_e_motivo_regional(self):
        class Fake:
            restantes = 3
            chamadas = 0
            def analisar(s,modelo,prompt):
                s.chamadas += 1
                s.restantes -= 1
                s.prompt = prompt
                return {'historias':[]}
        fake = Fake()
        with patch.object(A,'avaliar',return_value=(None,['falha editorial persistente'])):
            r = self.executar([remix_em(1)],provedor=fake)
        self.assertEqual(1,fake.chamadas)
        self.assertEqual(2,fake.restantes)
        self.assertIn('falha editorial persistente',fake.prompt)
        self.assertNotIn('B9F',fake.prompt)
        self.assertEqual([1,2],[m['rodada'] for m in r.diagnostico.reparos])

    def test_titulos_nao_bloqueiam_descoberta(self):
        p = remix_em(1,titulos=[])
        r = self.executar([p])
        self.assertEqual(1,len(r))
        self.assertTrue(r[0].validacao_remix['remix_valido'])
        self.assertFalse(r[0].titulo_auto_aprovado)

    def test_ep12_real_repara_sem_api(self):
        self.t = carregar_transcricao(ROOT/'dados/transcricoes'/f'{EP12}.json')
        dados = json.loads((ROOT/'dados/agencia/analises'/f'{RAW12}.json').read_text(encoding='utf-8'))
        self.cfg.max_cortes = 10
        r = self.executar(dados['historias_brutas'])
        self.assertGreaterEqual(r.diagnostico.automaticos,5)
        self.assertEqual(0,r.diagnostico.chamadas_feitas)
        acoes = {a for m in r.diagnostico.reparos if m['aprovado'] for a in m['acoes']}
        self.assertTrue({'subdividir_micro_historias','alinhar_frases_completas',
                         'restaurar_payoff_original','restaurar_chegada_ao_gancho'}<=acoes)
        self.assertTrue(all(p.validacao_remix['score_remix']>=75 for p in r if p.estado_editorial!='revisao'))

    def test_ep11_cache_nao_quebra(self):
        self.t = carregar_transcricao(ROOT/'dados/transcricoes/db972adc2049a2c9dfa68ff69302490cb013ee26e166a14f10082942eff11ea4.json')
        r = self.executar()
        self.assertEqual(5,len(r))
        self.assertTrue(all(p.video_id==self.t.video_id and p.segmentos for p in r))


if __name__=='__main__':
    unittest.main()
