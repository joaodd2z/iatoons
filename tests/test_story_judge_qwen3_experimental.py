import ast
import json
from pathlib import Path
import unittest
from scripts import story_judge_ollama_experimental as S
from scripts import story_judge_qwen3_experimental as Q


class TestPhase3Parity(unittest.TestCase):
    def setUp(self):
        self.cache={'duracao':300,'segmentos':[{'inicio':i,'fim':i+4,
            'texto':f'Peter pergunta sobre o dinheiro {i}. Lois recusa o plano.'} for i in range(0,300,5)]}
        self.filtered=S.candidates(self.cache)[1]
        self.group=self.filtered[:5]

    def test_exact_phase2_prompt_schema_options(self):
        tree=ast.parse(Path(S.__file__).read_text(encoding='utf-8'))
        node=next(n for n in ast.walk(tree) if isinstance(n,ast.Assign) and
                  any(isinstance(t,ast.Name) and t.id=='prompt' for t in n.targets))
        original=eval(compile(ast.Expression(node.value),'<phase2-prompt>','eval'),
                      {'json':json,'payload':S.payload,'group':self.group,'cache':self.cache})
        request=Q.request_payload(self.group,self.cache['segmentos'])
        self.assertEqual(original,request['prompt'])
        self.assertEqual({'temperature':0,'num_ctx':8192,'num_predict':2200},request['options'])
        self.assertEqual(S.ITEM['required'],request['format']['properties']['avaliacoes']['items']['required'])
        self.assertEqual('qwen3:4b',request['model'])
        self.assertFalse(request['think'])

    def test_expanded_batches_cover_every_id_once(self):
        groups=S.batches(self.filtered,self.cache['segmentos'],limit=len({c['anchor'] for c in self.filtered}))
        ids=[c['candidate_id'] for group in groups for c in group]
        self.assertEqual(len(ids),len(set(ids)))
        self.assertEqual({c['candidate_id'] for c in self.filtered},set(ids))

    def test_native_schema_id_restriction(self):
        schema=Q.request_payload(self.group,self.cache['segmentos'])['format']
        self.assertEqual(len(self.group),schema['properties']['avaliacoes']['maxItems'])
        self.assertEqual([c['candidate_id'] for c in self.group],
            schema['properties']['avaliacoes']['items']['properties']['candidate_id']['enum'])

    def test_only_supported_thinking_control_is_sent(self):
        mode=Q.thinking_mode({'thinking':{'values':[True],'default':True}})
        self.assertTrue(mode)
        self.assertTrue(Q.request_payload(self.group,self.cache['segmentos'],mode)['think'])
        self.assertFalse(Q.thinking_mode({'thinking':{'values':[True,False]}}))
        with self.assertRaises(ValueError): Q.thinking_mode({})
