"""
app.py — Janela principal do IA TOONS CUT
Interface escura com navegação lateral e área de conteúdo.
"""

import customtkinter as ctk
from pathlib import Path
from modules.processamento_av import carregar_ajustes
from modules.perfis_visuais import PERFIL_PADRAO_ID


# ─── Tema global ─────────────────────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

# Cores do canal IA TOONS
COR_ROXO_ESCURO  = "#0E071A"   # fundo geral profundo
COR_ROXO_MEDIO   = "#180D2C"   # sidebar refinada
COR_ROXO_CARD    = "#20123A"   # fundo dos cards
COR_ROXO_BORDA   = "#331E58"   # bordas suaves dos cards
COR_ROXO_CLARO   = "#7C3AED"   # destaque / botão primário violeta neon
COR_ROXO_HOVER   = "#8B5CF6"   # hover dos botões
COR_BRANCO       = "#F8FAFC"   # texto principal limpo
COR_CINZA_CLARO  = "#CBD5E1"   # texto secundário
COR_CINZA_ESCURO = "#64748B"   # texto terciário / desativado
COR_VERDE        = "#10B981"   # esmeralda de ação / sucesso
COR_VERMELHO     = "#EF4444"   # cancelamento / erro
COR_AMARELO      = "#F59E0B"   # avisos e revisão


class AppIAToons(ctk.CTk):
    """Janela principal do aplicativo IA TOONS CUT."""

    LARGURA_MIN  = 1100
    ALTURA_MIN   = 700
    LARGURA_INIT = 1200
    ALTURA_INIT  = 760

    ABAS = [
        ("🏭  Fábrica 100/dia", "fabrica"),
        ("🖼️  Estúdio de Capa", "capa"),
        ("⚡  Esteira de Produção", "importar"),
        ("🎛️  Ajustes Audiovisuais", "ajustes"),
        ("🤖  Configurações de IA", "config_ia"),
    ]

    def __init__(self):
        super().__init__()

        self.title("IATOONS — Fábrica de Cortes Verticais")
        self.geometry(f"{self.LARGURA_INIT}x{self.ALTURA_INIT}")
        self.minsize(self.LARGURA_MIN, self.ALTURA_MIN)
        self.configure(fg_color=COR_ROXO_ESCURO)

        # Ícone (se existir)
        icon_path = Path(__file__).parent.parent / "assets" / "icon.ico"
        if icon_path.exists():
            try:
                self.iconbitmap(str(icon_path))
            except Exception:
                pass

        # Estado compartilhado entre telas
        self.estado = {
            "video_path": None,       # caminho do vídeo importado
            "video_info": None,       # objeto InfoVideo
            "inicio": 0.0,            # início do trecho (segundos)
            "fim": 0.0,               # fim do trecho (segundos)
            "titulo": "",             # título do corte
            "pasta_export": None,     # pasta de destino
            "fps": 60,                # FPS de exportação
            "transcricao": None,      # Transcricao do episódio atual
            "ajustes_av": carregar_ajustes(),
            "intro_intervalo": None,
            "video_session": 0,      # geração do episódio ativo
            "perfil_visual_id": PERFIL_PADRAO_ID,
        }

        self._construir_layout()
        self._aba_atual = None
        self.navegar("fabrica")

    # ─── Layout ──────────────────────────────────────────────────────────────

    def _construir_layout(self):
        """Monta a sidebar e a área de conteúdo."""
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Sidebar esquerda
        self._sidebar = ctk.CTkFrame(
            self,
            width=200,
            corner_radius=0,
            fg_color=COR_ROXO_MEDIO,
        )
        self._sidebar.grid(row=0, column=0, sticky="nsew")
        self._sidebar.grid_propagate(False)

        # Logo com partículas (marca IATOONS)
        from ui.particulas import FaixaParticulas
        FaixaParticulas(
            self._sidebar, altura=104, fundo=COR_ROXO_MEDIO, titulo="IATOONS",
            subtitulo="Studio de Produção Contínua", tam_titulo=22, quantidade=34,
        ).pack(fill="x", pady=(14, 10))

        ctk.CTkFrame(self._sidebar, height=1, fg_color=COR_ROXO_CLARO).pack(
            fill="x", padx=16, pady=(0, 16)
        )

        # Botões de navegação
        self._botoes_nav: dict[str, ctk.CTkButton] = {}
        for label, chave in self.ABAS:
            btn = ctk.CTkButton(
                self._sidebar,
                text=label,
                anchor="w",
                font=ctk.CTkFont(size=13),
                fg_color="transparent",
                text_color=COR_CINZA_CLARO,
                hover_color=COR_ROXO_CLARO,
                corner_radius=8,
                height=42,
                command=lambda c=chave: self.navegar(c),
            )
            btn.pack(fill="x", padx=12, pady=3)
            self._botoes_nav[chave] = btn

        # Versão no rodapé da sidebar
        ctk.CTkLabel(
            self._sidebar,
            text="IATOONS Studio — v3.0",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color="#7C3AED",
        ).pack(side="bottom", pady=14)

        # Área de conteúdo (direita)
        self._area_conteudo = ctk.CTkFrame(
            self,
            corner_radius=0,
            fg_color=COR_ROXO_ESCURO,
        )
        self._area_conteudo.grid(row=0, column=1, sticky="nsew")
        self._area_conteudo.grid_columnconfigure(0, weight=1)
        self._area_conteudo.grid_rowconfigure(0, weight=1)

        # Container da tela atual
        self._frame_tela: ctk.CTkFrame | None = None

    # ─── Navegação ────────────────────────────────────────────────────────────

    def navegar(self, chave: str):
        """Troca a tela exibida na área de conteúdo."""
        if chave == self._aba_atual:
            return

        # Atualiza estilo dos botões
        for c, btn in self._botoes_nav.items():
            if c == chave:
                btn.configure(
                    fg_color=COR_ROXO_CLARO,
                    text_color=COR_BRANCO,
                    font=ctk.CTkFont(size=13, weight="bold"),
                )
            else:
                btn.configure(
                    fg_color="transparent",
                    text_color=COR_CINZA_CLARO,
                    font=ctk.CTkFont(size=13),
                )

        # Remove a tela anterior
        if self._frame_tela is not None:
            self._frame_tela.destroy()
            self._frame_tela = None
            import gc
            import threading
            if threading.current_thread() is threading.main_thread():
                gc.collect()

        # Cria a nova tela
        tela_cls = self._resolver_tela(chave)
        self._frame_tela = tela_cls(
            master=self._area_conteudo,
            app=self,
        )
        self._frame_tela.grid(row=0, column=0, sticky="nsew", padx=0, pady=0)
        self._aba_atual = chave

    def _resolver_tela(self, chave: str):
        """Retorna a classe da tela correspondente à chave."""
        # Importações locais para evitar importação circular
        if chave == "fabrica":
            from ui.tela_fabrica import TelaFabrica
            return TelaFabrica
        if chave == "capa":
            from ui.tela_capa import TelaCapa
            return TelaCapa
        if chave == "importar":
            from ui.tela_importar import TelaImportar
            return TelaImportar
        elif chave == "corte":
            from ui.tela_corte import TelaCorte
            return TelaCorte
        elif chave == "transcrever":
            from ui.tela_transcricao import TelaTranscricao
            return TelaTranscricao
        elif chave == "exportar":
            from ui.tela_exportar import TelaExportar
            return TelaExportar
        elif chave == "lote":
            from ui.tela_lote import TelaLote
            return TelaLote
        elif chave == "agencia":
            from ui.tela_agencia import TelaAgencia
            return TelaAgencia
        elif chave == "reaproveitar":
            from ui.tela_reaproveitar import TelaReaproveitar
            return TelaReaproveitar
        elif chave == "ajustes":
            from ui.tela_ajustes import TelaAjustes
            return TelaAjustes
        elif chave == "config_ia":
            from ui.tela_config_ia import TelaConfigIA
            return TelaConfigIA
        elif chave == "comparar_ias":
            from ui.tela_comparar_ias import TelaCompararIAs
            return TelaCompararIAs
        raise ValueError(f"Tela desconhecida: {chave}")

    # ─── Utilitários ─────────────────────────────────────────────────────────

    def atualizar_estado(self, chave: str, valor):
        """Atualiza um valor no estado compartilhado."""
        self.estado[chave] = valor

    def mostrar_erro(self, titulo: str, mensagem: str):
        """Exibe uma caixa de diálogo de erro."""
        from tkinter import messagebox
        messagebox.showerror(titulo, mensagem)

    def mostrar_aviso(self, titulo: str, mensagem: str):
        from tkinter import messagebox
        messagebox.showwarning(titulo, mensagem)

    def mostrar_info(self, titulo: str, mensagem: str):
        from tkinter import messagebox
        messagebox.showinfo(titulo, mensagem)

    def destroy(self):
        """Encerra a tela ativa e coleta variáveis Tk na thread principal antes de destruir o interpretador Tcl."""
        import gc
        import threading
        try:
            if getattr(self, "_frame_tela", None) is not None:
                self._frame_tela.destroy()
            self._frame_tela = None
            import gc
            import threading
            if threading.current_thread() is threading.main_thread():
                gc.collect()
                self._frame_tela = None
            if hasattr(self, "_botoes_nav") and isinstance(self._botoes_nav, dict):
                self._botoes_nav.clear()
            for child in list(self.winfo_children()):
                child.destroy()
            if threading.current_thread() is threading.main_thread():
                gc.collect()
        except Exception:
            pass
        super().destroy()
        if threading.current_thread() is threading.main_thread():
            gc.collect()
