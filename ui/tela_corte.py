"""
tela_corte.py — Tela de definição do trecho, título manual e visualização do frame
"""

import queue
import threading
import uuid
from pathlib import Path
import customtkinter as ctk
from PIL import Image

from ui.app import COR_BRANCO, COR_CINZA_CLARO, COR_CINZA_ESCURO, COR_ROXO_CLARO, COR_VERDE
from modules.importacao import hhmmss_para_segundos, normalizar_tempos, segundos_para_hhmmss
from modules.renderer import extrair_thumbnail_composto
from modules.legendas import gerar_blocos_legenda
from modules.temporarios import PREFIXO_PREVIEW, limpar_previews


class TelaCorte(ctk.CTkFrame):
    """Tela para selecionar o tempo do corte e configurar o título."""

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        
        self.video_info = self.app.estado.get("video_info")
        
        # Cria a pasta temporária se não existir
        self.temp_dir = Path(__file__).parent.parent / "temp"
        self.temp_dir.mkdir(exist_ok=True)
        # Remove apenas previews antigos do aplicativo, sem interferir em outra instância ativa.
        limpar_previews(self.temp_dir, idade_minima_s=24 * 60 * 60)
        self._preview_resultados = queue.Queue()
        self._preview_geracao = 0
        self._preview_ativa = threading.Event()
        self._preview_ativa.set()

        self._construir_layout()
        self._carregar_estado()
        self.after(100, self._processar_resultados_preview)

    def _construir_layout(self):
        self.grid_columnconfigure(0, weight=6)  # Área de edição esquerda
        self.grid_columnconfigure(1, weight=4)  # Área de preview direita
        self.grid_rowconfigure(0, weight=1)

        if not self.video_info or not self.video_info.valido:
            # Caso caia aqui sem vídeo carregado
            lbl = ctk.CTkLabel(
                self, text="⚠️ Nenhum vídeo importado.\n\nVolte à aba 'Importar' e selecione um arquivo de vídeo\npara poder configurar o corte.",
                font=ctk.CTkFont(size=18),
                text_color=COR_BRANCO
            )
            lbl.place(relx=0.5, rely=0.5, anchor="center")
            return

        # ─── LADO ESQUERDO: CONTROLES ───────────────────────────────────────
        frame_esq = ctk.CTkFrame(self, fg_color="transparent")
        frame_esq.grid(row=0, column=0, sticky="nsew", padx=(40, 20), pady=40)

        # Cabeçalho
        ctk.CTkLabel(
            frame_esq,
            text="Passo 2: Configurar Corte",
            font=ctk.CTkFont(size=24, weight="bold"),
            text_color=COR_BRANCO,
        ).pack(anchor="w", pady=(0, 24))

        # Card de Tempos
        card_tempo = ctk.CTkFrame(frame_esq, fg_color="#211536", corner_radius=12)
        card_tempo.pack(fill="x", pady=(0, 24), ipadx=24, ipady=24)

        ctk.CTkLabel(
            card_tempo,
            text="Definir Trecho",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=COR_BRANCO,
        ).pack(anchor="w", pady=(0, 16))

        # Layout Início / Fim lado a lado
        frame_grid_tempo = ctk.CTkFrame(card_tempo, fg_color="transparent")
        frame_grid_tempo.pack(fill="x")
        frame_grid_tempo.grid_columnconfigure(0, weight=1)
        frame_grid_tempo.grid_columnconfigure(1, weight=1)

        # Início
        ctk.CTkLabel(frame_grid_tempo, text="Início (HH:MM:SS)", font=ctk.CTkFont(size=13)).grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.entry_inicio = ctk.CTkEntry(
            frame_grid_tempo, font=ctk.CTkFont(size=14), height=38,
            fg_color="#150C24", border_color="#3E2661"
        )
        self.entry_inicio.grid(row=1, column=0, sticky="ew", padx=(0, 12))

        # Fim
        ctk.CTkLabel(frame_grid_tempo, text="Fim (HH:MM:SS)", font=ctk.CTkFont(size=13)).grid(row=0, column=1, sticky="w", pady=(0, 4))
        self.entry_fim = ctk.CTkEntry(
            frame_grid_tempo, font=ctk.CTkFont(size=14), height=38,
            fg_color="#150C24", border_color="#3E2661"
        )
        self.entry_fim.grid(row=1, column=1, sticky="ew")

        # Duração total exibida
        self.lbl_duracao = ctk.CTkLabel(
            card_tempo, text="Duração do corte: 00:00:00",
            font=ctk.CTkFont(size=13), text_color=COR_CINZA_CLARO
        )
        self.lbl_duracao.pack(anchor="w", pady=(16, 0))

        # Card de Título
        card_titulo = ctk.CTkFrame(frame_esq, fg_color="#211536", corner_radius=12)
        card_titulo.pack(fill="x", pady=(0, 24), ipadx=24, ipady=24)

        ctk.CTkLabel(
            card_titulo,
            text="Título no Topo",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=COR_BRANCO,
        ).pack(anchor="w", pady=(0, 16))

        self.entry_titulo = ctk.CTkEntry(
            card_titulo, font=ctk.CTkFont(size=14), height=42,
            placeholder_text="EX: PRÉ NATAL DO STWIE",
            fg_color="#150C24", border_color="#3E2661"
        )
        self.entry_titulo.pack(fill="x")
        
        ctk.CTkLabel(
            card_titulo,
            text="Na Fase 3 isso será gerado por Inteligência Artificial.",
            font=ctk.CTkFont(size=11), text_color=COR_CINZA_ESCURO
        ).pack(anchor="w", pady=(8, 0))

        # Botão atualizar Preview
        ctk.CTkButton(
            frame_esq,
            text="Atualizar Visualização 🔄",
            font=ctk.CTkFont(size=14),
            height=40,
            fg_color="#3E2661",
            hover_color=COR_ROXO_CLARO,
            command=self._atualizar_preview,
        ).pack(anchor="w", pady=(0, 24))

        # Botão Avançar
        self.btn_avancar = ctk.CTkButton(
            frame_esq,
            text="Salvar e Exportar ➔",
            font=ctk.CTkFont(size=15, weight="bold"),
            height=50,
            width=220,
            fg_color=COR_ROXO_CLARO,
            hover_color="#7B3FC4",
            command=self._salvar_e_avancar,
        )
        self.btn_avancar.pack(anchor="w", side="bottom")

        # ─── LADO DIREITO: PREVIEW ──────────────────────────────────────────
        frame_dir = ctk.CTkFrame(self, fg_color="#150C24", corner_radius=0)
        frame_dir.grid(row=0, column=1, sticky="nsew")
        frame_dir.grid_columnconfigure(0, weight=1)
        frame_dir.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            frame_dir,
            text="Prévia do Frame Inicial",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=COR_CINZA_CLARO,
        ).grid(row=0, column=0, pady=(40, 16))

        self.lbl_preview = ctk.CTkLabel(
            frame_dir,
            text="Carregando prévia...",
            fg_color="#0D0717",
            corner_radius=8,
        )
        self.lbl_preview.grid(row=1, column=0, padx=40, pady=(0, 40), sticky="nsew")

    def _carregar_estado(self):
        if not self.video_info:
            return

        inicio = self.app.estado.get("inicio", 0.0)
        fim = self.app.estado.get("fim", self.video_info.duracao)
        titulo = self.app.estado.get("titulo", "")

        self.entry_inicio.insert(0, segundos_para_hhmmss(inicio))
        self.entry_fim.insert(0, segundos_para_hhmmss(fim))
        if titulo:
            self.entry_titulo.insert(0, titulo)

        self._calcular_duracao()
        
        # Força binds para atualizar duração ao digitar
        self.entry_inicio.bind("<KeyRelease>", lambda e: self._calcular_duracao())
        self.entry_fim.bind("<KeyRelease>", lambda e: self._calcular_duracao())

        # Dispara atualização de preview inicial
        self.after(200, self._atualizar_preview)

    def _calcular_duracao(self) -> float:
        try:
            inicio = hhmmss_para_segundos(self.entry_inicio.get())
            fim = hhmmss_para_segundos(self.entry_fim.get())
            inicio, fim = normalizar_tempos(inicio, fim, self.video_info.duracao)
            duracao = fim - inicio
            self.lbl_duracao.configure(
                text=f"Duração do corte: {segundos_para_hhmmss(duracao)}",
                text_color=COR_VERDE
            )
            return duracao
        except ValueError:
            self.lbl_duracao.configure(text="Erro: Formato de tempo inválido", text_color="red")
            return -1.0

    def _atualizar_preview(self):
        try:
            inicio = hhmmss_para_segundos(self.entry_inicio.get())
            inicio = max(0.0, min(inicio, self.video_info.duracao))
        except ValueError:
            self.lbl_preview.configure(text="Tempo inicial inválido.", image="")
            return

        titulo = self.entry_titulo.get().strip().upper()
        video_path = self.app.estado["video_path"]
        legenda_texto = ""
        transcricao = self.app.estado.get("transcricao")
        if transcricao:
            bloco = next(
                (b for b in gerar_blocos_legenda(transcricao, 5) if b.inicio <= inicio < b.fim),
                None,
            )
            legenda_texto = bloco.texto if bloco else ""
        
        self.lbl_preview.configure(text="Gerando prévia com FFmpeg...", image="")
        self._preview_geracao += 1
        geracao = self._preview_geracao
        thumb_temp = self.temp_dir / f"{PREFIXO_PREVIEW}{uuid.uuid4().hex}.jpg"

        threading.Thread(
            target=self._gerar_preview_worker,
            args=(geracao, video_path, inicio, titulo, legenda_texto, thumb_temp,
                  self.app.estado.get("perfil_visual_id")),
            daemon=True,
        ).start()

    def _gerar_preview_worker(self, geracao, video_path, inicio, titulo, legenda_texto,
                              thumb_temp, perfil_visual):
        """Executa apenas trabalho de FFmpeg; nunca acessa widgets do Tk."""
        sucesso = extrair_thumbnail_composto(
            caminho_video=video_path,
            tempo_segundos=inicio,
            titulo=titulo,
            caminho_saida=str(thumb_temp),
            legenda_texto=legenda_texto,
            ajustes_av=self.app.estado.get("ajustes_av"),
            perfil_visual=perfil_visual,
        )
        if self._preview_ativa.is_set():
            self._preview_resultados.put((geracao, sucesso, thumb_temp))
        else:
            try:
                thumb_temp.unlink(missing_ok=True)
            except OSError:
                pass

    def _processar_resultados_preview(self):
        """Aplica resultados na thread principal e ignora gerações obsoletas."""
        try:
            while True:
                geracao, sucesso, thumb_temp = self._preview_resultados.get_nowait()
                try:
                    if geracao != self._preview_geracao:
                        continue
                    if sucesso and thumb_temp.exists():
                        with Image.open(thumb_temp) as img:
                            imagem = img.copy()
                        self.img_ctk = ctk.CTkImage(
                            light_image=imagem, dark_image=imagem, size=(315, 560)
                        )
                        self.lbl_preview.configure(text="", image=self.img_ctk)
                    else:
                        log_path = self.temp_dir / "ffmpeg_erro.log"
                        dica = "Veja temp/ffmpeg_erro.log para detalhes." if log_path.exists() else ""
                        self.lbl_preview.configure(text=f"Falha ao gerar prévia.\n{dica}", image="")
                except Exception as e:
                    if geracao == self._preview_geracao:
                        self.lbl_preview.configure(text=f"Erro ao carregar imagem.\n{e}", image="")
                finally:
                    try:
                        thumb_temp.unlink(missing_ok=True)
                    except OSError:
                        pass
        except queue.Empty:
            pass

        if self.winfo_exists():
            self.after(100, self._processar_resultados_preview)

    def destroy(self):
        self._preview_ativa.clear()
        super().destroy()

    def _salvar_e_avancar(self):
        try:
            inicio = hhmmss_para_segundos(self.entry_inicio.get())
            fim = hhmmss_para_segundos(self.entry_fim.get())
            inicio, fim = normalizar_tempos(inicio, fim, self.video_info.duracao)
        except ValueError as erro:
            self.app.mostrar_aviso("Atenção", str(erro))
            return

        # Reflete na interface os valores efetivamente validados e preserva ms.
        for entry, valor in ((self.entry_inicio, inicio), (self.entry_fim, fim)):
            entry.delete(0, "end")
            entry.insert(0, segundos_para_hhmmss(valor))

        titulo = self.entry_titulo.get().strip().upper()

        self.app.atualizar_estado("inicio", inicio)
        self.app.atualizar_estado("fim", fim)
        self.app.atualizar_estado("titulo", titulo)
        
        self.app.navegar("exportar")
