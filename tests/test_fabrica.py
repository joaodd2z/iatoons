import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from modules import fabrica as F


def _cfg(base: Path, **conta) -> F.ConfigFabrica:
    c = F.Conta.de_dict({"id": "Conta Teste", "posts_por_dia": 4, "hashtags": "#a #b", **conta})
    return F.ConfigFabrica(pasta_base=base, contas=[c])


def _corte(cfg, conta, episodio, n, titulo=None):
    pasta = cfg.produzidos / conta.id / episodio
    pasta.mkdir(parents=True, exist_ok=True)
    mp4 = pasta / f"corte_{n:02d}.mp4"
    mp4.write_bytes(b"x" * 10)
    (pasta / f"corte_{n:02d}.metricas.json").write_text(
        json.dumps({"titulo_escolhido": titulo or f"{episodio} corte {n}", "duracao": 61.5}), encoding="utf-8")
    return mp4


class TestConta(unittest.TestCase):
    def test_slug_e_padroes(self):
        c = F.Conta.de_dict({"nome": "Conta Ação 2!"})
        self.assertEqual(c.id, "conta_acao_2")
        self.assertEqual(c.perfil, "toonmix")
        self.assertEqual(c.qualidade, "rapida")

    def test_grade_espalha_na_janela(self):
        c = F.Conta.de_dict({"id": "x", "posts_por_dia": 5, "janela": ["08:00", "12:00"]})
        self.assertEqual(c.grade_horarios(), ["08:00", "09:00", "10:00", "11:00", "12:00"])

    def test_grade_usa_horarios_informados(self):
        c = F.Conta.de_dict({"id": "x", "posts_por_dia": 2, "horarios": ["19:00", "12:30", "25:00", "08:00"]})
        self.assertEqual(c.grade_horarios(), ["08:00", "12:30"])

    def test_100_por_dia_cabem_na_janela_sem_repetir_horario(self):
        c = F.Conta.de_dict({"id": "x", "posts_por_dia": 100, "janela": ["06:00", "23:59"]})
        g = c.grade_horarios()
        self.assertEqual(len(g), 100)
        self.assertEqual(len(set(g)), 100)


class TestDistribuir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.cfg = _cfg(self.base, janela=["00:01", "00:04"])
        self.conta = self.cfg.contas[0]
        self.amanha = date.today() + timedelta(days=1)

    def tearDown(self):
        self.tmp.cleanup()

    def test_intercala_episodios_e_nao_repete(self):
        for ep in ("ep_a", "ep_b"):
            for n in range(1, 4):
                _corte(self.cfg, self.conta, ep, n)
        r = F.distribuir(self.cfg, self.conta, inicio=self.amanha, dias=2)
        self.assertEqual([d["novos"] for d in r["dias"]], [4, 2])
        agenda = F._ler_agenda(self.cfg.para_postar / self.conta.id / self.amanha.isoformat() / "AGENDA.csv")
        self.assertEqual([a["episodio"] for a in agenda], ["ep_a", "ep_b", "ep_a", "ep_b"])
        # segunda rodada: nada novo para distribuir
        r2 = F.distribuir(self.cfg, self.conta, inicio=self.amanha, dias=2)
        self.assertEqual(sum(d["novos"] for d in r2["dias"]), 0)
        self.assertEqual(r2["restante_em_estoque"], 0)

    def test_completa_dia_parcial_sem_sobrescrever(self):
        _corte(self.cfg, self.conta, "ep_a", 1)
        F.distribuir(self.cfg, self.conta, inicio=self.amanha)
        for n in range(2, 6):
            _corte(self.cfg, self.conta, "ep_a", n)
        r = F.distribuir(self.cfg, self.conta, inicio=self.amanha)
        self.assertEqual(r["dias"][0]["total_no_dia"], 4)
        pasta = self.cfg.para_postar / self.conta.id / self.amanha.isoformat()
        self.assertEqual(len(list(pasta.glob("*.mp4"))), 4)
        self.assertEqual(len(F._ler_agenda(pasta / "AGENDA.csv")), 4)
        self.assertEqual(r["restante_em_estoque"], 1)

    def test_legenda_e_arquivos(self):
        _corte(self.cfg, self.conta, "ep_a", 1, titulo="Peter perdeu o carro")
        F.distribuir(self.cfg, self.conta, inicio=self.amanha)
        pasta = self.cfg.para_postar / self.conta.id / self.amanha.isoformat()
        txt = next(pasta.glob("*.txt")).read_text(encoding="utf-8")
        self.assertEqual(txt, "Peter perdeu o carro\n\n#a #b\n")
        self.assertTrue(next(pasta.glob("*.mp4")).name.startswith("001_00h01_peter_perdeu_o_carro"))

    def test_ignora_corte_sem_metricas(self):
        pasta = self.cfg.produzidos / self.conta.id / "ep_a"
        pasta.mkdir(parents=True)
        (pasta / "incompleto.mp4").write_bytes(b"x")
        self.assertEqual(F.cortes_prontos(self.cfg, self.conta), [])

    def test_hoje_nao_usa_horario_que_ja_passou(self):
        _corte(self.cfg, self.conta, "ep_a", 1)
        r = F.distribuir(self.cfg, self.conta, inicio=date.today())
        # janela 00:01-00:04 já passou hoje (a não ser que o teste rode exatamente à meia-noite)
        self.assertEqual(r["dias"][0]["novos"], 0)
        self.assertEqual(r["restante_em_estoque"], 1)


class TestProduzir(unittest.TestCase):
    def test_envia_perfil_cortes_e_preset_da_conta(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            cfg = _cfg(base, perfil="toonmix", cortes_por_episodio=9, qualidade="rapida")
            conta = cfg.contas[0]
            F.preparar_pastas(cfg)
            (cfg.entrada / conta.id / "ep1.mp4").write_bytes(b"x")
            (cfg.entrada / conta.id / "notas.txt").write_text("ignorar")

            class EsteiraFalsa:
                def __init__(self):
                    self.chamadas = []
                    self.iniciou = False

                def adicionar_episodios(self, videos, **kw):
                    self.chamadas.append((videos, kw))
                    return list(videos), []

                def iniciar(self):
                    self.iniciou = True

                def aguardar_conclusao(self, timeout=None):
                    return True

                def listar_episodios(self):
                    return []

                def status_concorrencia(self):
                    return {}

            est = EsteiraFalsa()
            F.produzir(cfg, esteira=est, log=lambda *_: None)
            self.assertTrue(est.iniciou)
            videos, kw = est.chamadas[0]
            self.assertEqual([v.name for v in videos], ["ep1.mp4"])
            self.assertEqual(kw["perfil_visual_id"], "toonmix")
            self.assertEqual(kw["max_cortes"], 9)
            self.assertEqual(kw["ajustes_av"].preset_x264, "veryfast")
            self.assertEqual(Path(kw["pasta_saida"]), cfg.produzidos / conta.id)


class TestPerfisJson(unittest.TestCase):
    def test_perfil_extra_herda_toonmix(self):
        from modules import perfis_visuais as PV
        with tempfile.TemporaryDirectory() as tmp:
            antigo = PV.PERFIS_DIR
            try:
                PV.PERFIS_DIR = Path(tmp)
                fundo = Path(tmp) / "fundo.png"
                fundo.write_bytes(b"png")
                (Path(tmp) / "simpsons.json").write_text(json.dumps(
                    {"id": "Cortes_S", "fundo": str(fundo), "nome_canal": "CORTES S", "titulo": {"cor": "0x00FFFF"}}),
                    encoding="utf-8")
                p = PV.obter_perfil("cortes_s")
                self.assertEqual(p.nome_canal, "CORTES S")
                self.assertEqual(p.estilo_titulo_agencia.cor, "0x00FFFF")
                self.assertEqual(p.zonas, PV.ZONAS_TOONMIX)
                self.assertEqual(PV.obter_perfil("nao_existe").id, "toonmix")
                self.assertIn("cortes_s", [x.id for x in PV.listar_perfis()])
            finally:
                PV.PERFIS_DIR = antigo


if __name__ == "__main__":
    unittest.main()
