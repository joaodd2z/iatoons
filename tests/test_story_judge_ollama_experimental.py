import json
import unittest
from scripts import story_judge_ollama_experimental as S


class TestStoryJudge(unittest.TestCase):
    def setUp(self):
        self.evidence={'conflito':'Lois recusa o plano.','setup':'Peter quer o dinheiro.',
                       'escalada':'A disputa piorou.','payoff':'O carro foi vendido.',
                       'gancho_potencial':'Quem ficou sem carro?','mudanca':'A familia mudou de casa.',
                       'momento_forte':'O carro foi vendido.','motivo':'Conflito com consequencia.'}
        text=' '.join(self.evidence.values())
        self.cache={'duracao':180,'segmentos':[{'inicio':i,'fim':i+4,'texto':text}
                                             for i in range(0,180,5)]}
        self.group=S.candidates(self.cache)[1][:2]

    def response(self):
        return {'avaliacoes':[{'candidate_id':c['candidate_id'],'tem_historia':True,
            'presentes':{k:True for k in S.WEIGHTS},
            **self.evidence} for c in self.group]}

    def test_temporal_bounds(self):
        generated,_,_=S.candidates(self.cache)
        self.assertTrue(generated)
        self.assertTrue(all(20<=c['duracao']<=150 for c in generated))

    def test_model_cannot_modify_timestamps(self):
        r=self.response(); r['avaliacoes'][0]['inicio']=0
        with self.assertRaises(ValueError): S.validate(json.dumps(r),self.group)

    def test_unknown_ids_rejected(self):
        r=self.response(); r['avaliacoes'][0]['candidate_id']='inventado'
        with self.assertRaises(ValueError): S.validate(json.dumps(r),self.group)

    def test_local_score_needs_source_evidence(self):
        r=self.response(); r['avaliacoes'][0]['payoff']='Frase inventada que nao consta no bloco'
        result=S.validate(json.dumps(r),self.group)
        self.assertEqual(80,result[0]['score_historia'])
        self.assertEqual(0,result[0]['score_criterios']['payoff'])

    def test_free_score_rejected(self):
        r=self.response(); r['avaliacoes'][0]['score_historia']=100
        with self.assertRaises(ValueError): S.validate(json.dumps(r),self.group)

    def test_repeated_evidence_does_not_inflate_score(self):
        r=self.response()
        for k in S.WEIGHTS: r['avaliacoes'][0][k]='Peter quer o dinheiro.'
        result=S.validate(json.dumps(r),self.group)
        self.assertEqual(20,result[0]['score_historia'])
        self.assertFalse(result[0]['aprovada_experimento'])

    def test_missing_candidate_rejected(self):
        r=self.response(); r['avaliacoes'].pop()
        with self.assertRaises(ValueError): S.validate(json.dumps(r),self.group)

    def test_invalid_json(self):
        with self.assertRaises(ValueError): S.validate('texto livre',self.group)

    def test_empty_candidates_removed(self):
        for s in self.cache['segmentos']: s['texto']=''
        self.assertFalse(S.candidates(self.cache)[1])

    def test_prefilter_duplicates(self):
        generated,filtered,rejected=S.candidates(self.cache)
        self.assertLess(len(filtered),len(generated))
        self.assertTrue(rejected)

    def test_dedup_prefers_complete_arc(self):
        items=S.validate(json.dumps(self.response()),self.group)
        a=dict(items[0],candidate_id='A',inicio=0,fim=60,duracao=60,arco_completo=False)
        b=dict(items[0],candidate_id='B',inicio=0,fim=90,duracao=90,arco_completo=True)
        kept,_=S.deduplicate([a,b])
        self.assertEqual(['B'],[x['candidate_id'] for x in kept])
