import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.perfis_visuais import (  # noqa: E402
    FUNDO_TOONMIX_PATH,
    PERFIL_PADRAO_ID,
    PERFIL_TOONMIX,
    ZONAS_TOONMIX,
    listar_perfis,
    obter_perfil,
    perfil_por_nome,
)
from modules.renderer import renderizar_corte  # noqa: E402
from modules.sessao import iniciar_sessao_video  # noqa: E402


class _Info:
    duracao = 10.0


class _StdoutVazio:
    def __iter__(self):
        return iter(())

    def close(self):
        pass


class _ProcessoConcluido:
    def __init__(self, comando, **_kwargs):
        self.comando = comando
        self.stdout = _StdoutVazio()
        self.returncode = 0

    def wait(self):
        return self.returncode


class TestePerfisVisuais(unittest.TestCase):
    def test_toonmix_eh_unico_perfil_fixo_e_disponivel(self):
        self.assertEqual("toonmix", PERFIL_PADRAO_ID)
        perfis = listar_perfis()
        self.assertEqual(1, len(perfis))
        self.assertEqual(PERFIL_TOONMIX, perfis[0])
        self.assertTrue(FUNDO_TOONMIX_PATH.is_file())
        self.assertEqual(FUNDO_TOONMIX_PATH, PERFIL_TOONMIX.fundo_path)
        self.assertEqual(ZONAS_TOONMIX, PERFIL_TOONMIX.zonas)

    def test_obter_perfil_sempre_resolve_para_toonmix(self):
        for entrada in (None, "toonmix", "ia_toons", "animatoons", "padrao"):
            perfil = obter_perfil(entrada)
            self.assertEqual("toonmix", perfil.id)
            self.assertEqual("TOONMIX", perfil.nome)
            self.assertEqual(FUNDO_TOONMIX_PATH, perfil.fundo_path)
        self.assertEqual(PERFIL_TOONMIX, perfil_por_nome("TOONMIX"))

    def test_perfil_permanece_toonmix_ao_trocar_episodio(self):
        estado = {"perfil_visual_id": "toonmix", "video_session": 0}
        iniciar_sessao_video(estado, "episodio.mp4", _Info())
        self.assertEqual("toonmix", estado["perfil_visual_id"])

    def test_renderer_injeta_fundo_toonmix(self):
        comandos = []

        def criar_processo(comando, **kwargs):
            comandos.append(comando)
            return _ProcessoConcluido(comando, **kwargs)

        with patch("modules.renderer.subprocess.Popen", side_effect=criar_processo):
            resultado = renderizar_corte(
                "entrada.mp4", 0, 1, "Título", "saida_toonmix.mp4",
                fps=30,
            )
            self.assertTrue(resultado["sucesso"])

        self.assertIn(str(FUNDO_TOONMIX_PATH), comandos[0])


if __name__ == "__main__":
    unittest.main()
