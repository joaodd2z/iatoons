import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PROJETO_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJETO_DIR))

from modules.processamento_av import (  # noqa: E402
    AjustesAV, filtro_animacao, filtro_audio, carregar_ajustes, salvar_ajustes,
)
from modules.renderer import renderizar_corte  # noqa: E402


class TestFiltrosImagem(unittest.TestCase):
    def test_preset_100_contem_todos_os_filtros(self):
        filtro = filtro_animacao(AjustesAV(), fps=60)
        for esperado in ("hqdn3d=", "flags=lanczos", "eq=", "unsharp=", "fps=60"):
            self.assertIn(esperado, filtro)

    def test_intensidade_zero_neutraliza_ajustes(self):
        filtro = filtro_animacao(AjustesAV(intensidade=0), fps=30)
        self.assertNotIn("hqdn3d", filtro)
        self.assertNotIn("eq=", filtro)
        self.assertNotIn("unsharp", filtro)
        self.assertIn("flags=lanczos", filtro)  # redimensionamento é controle independente

    def test_intensidade_interpola_contraste(self):
        filtro = filtro_animacao(AjustesAV(intensidade=50))
        self.assertIn("contrast=1.0300", filtro)
        self.assertIn("saturation=1.0600", filtro)
        self.assertIn("brightness=0.0050", filtro)


class TestFiltroAudio(unittest.TestCase):
    def test_rampa_e_limiter_estao_presentes(self):
        filtro = filtro_audio(AjustesAV(), duracao_corte=20)
        self.assertIn("if(lt(t,8.000)", filtro)
        self.assertIn("1.0000", filtro)
        self.assertIn("1.2500", filtro)
        self.assertIn("alimiter=limit=0.950", filtro)
        self.assertIn("level=false", filtro)

    def test_audio_pode_ser_desativado(self):
        self.assertEqual(filtro_audio(AjustesAV(audio_ativo=False), 10), "")


class TestIntegracaoComRenderer(unittest.TestCase):
    def test_filtros_ficam_antes_do_overlay_e_audio_no_comando(self):
        capturado = {}

        class Processo:
            stdout = iter([])
            returncode = 0

            def wait(self):
                return 0

            def kill(self):
                self.returncode = -9

        def popen(cmd, **_kwargs):
            capturado["cmd"] = cmd
            return Processo()

        with patch("modules.renderer.subprocess.Popen", side_effect=popen):
            resultado = renderizar_corte("entrada.mp4", 0, 2, "T", "saida.mp4", fps=30)

        self.assertTrue(resultado["sucesso"])
        cmd = capturado["cmd"]
        complexo = cmd[cmd.index("-filter_complex") + 1]
        cadeia_animacao = complexo.split("[0:v]", 1)[1].split("[anim]", 1)[0]
        self.assertIn("hqdn3d", cadeia_animacao)
        self.assertIn("unsharp", cadeia_animacao)
        self.assertNotIn("hqdn3d", complexo.split("[1:v]", 1)[1].split("[bg]", 1)[0])
        self.assertIn("-af", cmd)
        self.assertIn("alimiter", cmd[cmd.index("-af") + 1])


class TestPersistencia(unittest.TestCase):
    def test_salva_e_reabre_preset(self):
        pasta = PROJETO_DIR / "temp" / "test_runtime_av"
        caminho = pasta / "preset.json"
        pasta.mkdir(parents=True, exist_ok=True)
        try:
            with patch("modules.processamento_av.CONFIG_PATH", caminho):
                salvar_ajustes(AjustesAV(intensidade=73, audio_ativo=False))
                restaurado = carregar_ajustes()
            self.assertEqual(restaurado.intensidade, 73)
            self.assertFalse(restaurado.audio_ativo)
        finally:
            caminho.unlink(missing_ok=True)
            try:
                pasta.rmdir()
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
