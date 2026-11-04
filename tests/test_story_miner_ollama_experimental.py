import json
import socket
import unittest
import urllib.error
from unittest.mock import patch
from scripts import story_miner_ollama_experimental as S


class TestStoryMiner(unittest.TestCase):
    def setUp(self):
        self.h={'inicio':10,'fim':40,'personagens':['Peter'],'resumo':'Peter disputa o dinheiro com Lois.',
                'conflito':'Disputa pelo dinheiro','gancho_potencial':'O dinheiro sumiu',
                'payoff':'Lois recusa o plano','tipo':'conflito','potencial':80}

    def check(self,h):
        return S.validate(json.dumps({'historias':[h]}),0,90,1300)

    def test_valid_json(self):
        self.assertEqual(1,len(self.check(self.h)[0]))
        self.assertIsNone(S.narrative_sanity(self.h))

    def test_single_line_is_not_a_story(self):
        self.h['fim']=12
        self.assertIsNotNone(S.narrative_sanity(self.h))

    def test_declared_no_conflict(self):
        self.h['conflito']='Nenhum conflito detectado.'
        self.assertIsNotNone(S.narrative_sanity(self.h))

    def test_invalid_json(self):
        with self.assertRaises(ValueError): S.validate('not json',0,90,1300)

    def test_timeout(self):
        with patch.object(S.urllib.request,'urlopen',side_effect=socket.timeout('timeout')):
            with self.assertRaises(socket.timeout): S.generate('test')

    def test_offline(self):
        with patch.object(S.urllib.request,'urlopen',side_effect=urllib.error.URLError('offline')):
            with self.assertRaises(urllib.error.URLError): S.api('ps')

    def test_long_duration(self):
        self.h['fim']=300
        self.assertEqual(1,len(self.check(self.h)[1]))

    def test_invalid_timestamp(self):
        for v in (-5,100,float('nan')):
            self.h['inicio']=v
            self.assertFalse(self.check(self.h)[0])

    def test_deduplication(self):
        weaker=dict(self.h,potencial=60,inicio=12,fim=42)
        self.assertEqual([self.h],S.deduplicate([weaker,self.h]))

    def test_empty(self):
        self.assertEqual(([],[],0),S.validate('{"historias":[]}',0,90,1300))
