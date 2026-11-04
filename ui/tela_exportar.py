"""
tela_exportar.py — Renderização final com barra de progresso.
"""

import os
import queue
from pathlib import Path
import customtkinter as ctk

from ui.app import COR_BRANCO, COR_CINZA_CLARO, COR_CINZA_ESCURO, COR_VERDE, COR_ROXO_CLARO
from modules.tasks import TarefaRenderizacao, fila_render
from modules.sessao import caminho_normalizado
from modules.renderer import renderizar_corte
from modules.importacao import segundos_para_hhmmss
from modules.legendas import gerar_blocos_legenda, recortar_legendas, salvar_srt
from modules.lote import caminho_sem_sobrescrever
from modules.perfis_visuais import obter_perfil


class TelaExportar(ctk.CTkFrame):
    """Tela de progresso de exportação."""

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self._eventos_ui = queue.Queue()
        self._construir_layout()
        self._preparar_dados()
        self.after(100, self._processar_eventos_ui)

    def _construir_layout(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        container = ctk.CTkFrame(self, fg_color="transparent")
        container.grid(row=0, column=0, padx=40, pady=40, sticky="nsew")
        container.grid_columnconfigure(0, weight=1)

        # Cabeçalho
        ctk.CTkLabel(
            container,
            text="Passo 3: Exportar Corte Vertical",
            font=ctk.CTkFont(size=24, weight="bold"),
            text_color=COR_BRANCO,
        ).pack(anchor="w", pady=(0, 24))

        # Card de Resumo
        card_resumo = ctk.CTkFrame(container, fg_color="#211536", corner_radius=12)
        card_resumo.pack(fill="x", pady=(0, 24), ipadx=24, ipady=24)

        ctk.CTkLabel(
            card_resumo, text="Resumo do Corte",
            font=ctk.CTkFont(size=16, weight="bold"), text_color=COR_BRANCO,
        ).pack(anchor="w", pady=(0, 16))

        self.lbl_resumo = ctk.CTkLabel(
            card_resumo, text="",
            font=ctk.CTkFont(size=14), text_color=COR_CINZA_CLARO, justify="left"
        )
        self.lbl_resumo.pack(anchor="w")

        # Opções de renderização
        frame_opcoes = ctk.CTkFrame(card_resumo, fg_color="transparent")
        frame_opcoes.pack(fill="x", pady=(24, 0))
        
        ctk.CTkLabel(frame_opcoes, text="Qualidade / FPS:", font=ctk.CTkFont(size=14)).pack(side="left", padx=(0, 16))
        
        self.seg_fps = ctk.CTkSegmentedButton(
            frame_opcoes,
            values=["60 FPS (Padrão IA TOONS)", "30 FPS (Rápido)"],
            font=ctk.CTkFont(size=13),
            selected_color=COR_ROXO_CLARO,
            unselected_color="#150C24",
            selected_hover_color="#7B3FC4",
            command=self._on_fps_change
        )
        self.seg_fps.pack(side="left")
        self.seg_fps.set("60 FPS (Padrão IA TOONS)")

        self.var_legendas = ctk.BooleanVar(value=True)
        self.chk_legendas = ctk.CTkCheckBox(
            frame_opcoes, text="Incorporar legendas + SRT",
            variable=self.var_legendas,
        )
        self.chk_legendas.pack(side="left", padx=(20, 0))

        # Card de Progresso
        self.card_progresso = ctk.CTkFrame(container, fg_color="#150C24", corner_radius=12)
        self.card_progresso.pack(fill="x", pady=(0, 24), ipadx=24, ipady=24)

        self.lbl_status = ctk.CTkLabel(
            self.card_progresso, text="Aguardando início...",
            font=ctk.CTkFont(size=14, weight="bold"), text_color=COR_BRANCO,
        )
        self.lbl_status.pack(anchor="w", pady=(0, 12))

        self.barra_progresso = ctk.CTkProgressBar(
            self.card_progresso, height=16, fg_color="#3E2661", progress_color=COR_ROXO_CLARO
        )
        self.barra_progresso.pack(fill="x", pady=(0, 12))
        self.barra_progresso.set(0)

        self.lbl_porcentagem = ctk.CTkLabel(
            self.card_progresso, text="0%",
            font=ctk.CTkFont(size=14), text_color=COR_CINZA_CLARO,
        )
        self.lbl_porcentagem.pack(anchor="e")

        # Botões
        frame_botoes = ctk.CTkFrame(container, fg_color="transparent")
        frame_botoes.pack(fill="x", side="bottom")

        self.btn_iniciar = ctk.CTkButton(
            frame_botoes,
            text="▶ INICIAR RENDERIZAÇÃO",
            font=ctk.CTkFont(size=15, weight="bold"),
            height=50, width=220,
            fg_color=COR_VERDE, hover_color="#27AE60",
            command=self._iniciar_render,
        )
        self.btn_iniciar.pack(side="right")

        self.btn_abrir_pasta = ctk.CTkButton(
            frame_botoes,
            text="Abrir Pasta de Destino",
            font=ctk.CTkFont(size=14),
            height=50, width=180,
            fg_color="#3E2661", hover_color=COR_ROXO_CLARO,
            command=self._abrir_pasta,
        )
        self.btn_abrir_pasta.pack(side="left")

    def _preparar_dados(self):
        # Fotografa a sessão: nenhuma leitura posterior pode trocar o vídeo,
        # tempos, título ou ajustes no meio da renderização.
        st = dict(self.app.estado)
        if not st.get("video_info") or not st.get("pasta_export"):
            self.lbl_resumo.configure(text="Faltam dados. Volte aos passos anteriores.")
            self.btn_iniciar.configure(state="disabled")
            return

        ini = st["inicio"]
        fim = st["fim"]
        dur = fim - ini
        tit = st["titulo"] or "Sem Título"

        texto = (
            f"• Vídeo Origem: {st['video_info'].nome}\n"
            f"• Trecho: {segundos_para_hhmmss(ini)} até {segundos_para_hhmmss(fim)} "
            f"(Duração: {dur:.3f}s)\n"
            f"• Título gerado: {tit}\n"
            f"• Destino: {st['pasta_export']}"
        )
        self.lbl_resumo.configure(text=texto)

    def _on_fps_change(self, value):
        fps = 60 if "60" in value else 30
        self.app.atualizar_estado("fps", fps)

    def _iniciar_render(self):
        if fila_render.ocupada:
            self.app.mostrar_aviso("Aviso", "Já existe uma renderização em andamento.")
            return

        self.btn_iniciar.configure(state="disabled", fg_color=COR_CINZA_ESCURO)
        self.seg_fps.configure(state="disabled")

        st = self.app.estado
        
        # Gera nome de arquivo seguro
        nome_base = st["video_info"].nome.replace(".mp4", "").replace(".mkv", "").replace(".mov", "")
        titulo_limpo = "".join(c for c in st["titulo"] if c.isalnum() or c in " -_").strip()
        if not titulo_limpo:
            titulo_limpo = "corte"
        perfil = obter_perfil(st.get("perfil_visual_id"))
        sufixo_perfil = perfil.nome_canal.replace(" ", "_")
        nome_arquivo = f"{nome_base}_{titulo_limpo}_{sufixo_perfil}.mp4".replace(" ", "_")
        
        nome_sem_extensao = Path(nome_arquivo).stem
        adicionais = (".srt",) if self.var_legendas.get() else ()
        caminho_saida = str(caminho_sem_sobrescrever(
            st["pasta_export"], nome_sem_extensao, ".mp4", adicionais
        ))

        # Prepara a tarefa
        fps = st.get("fps", 60)
        
        legendas = []
        transcricao = st.get("transcricao")
        if transcricao and caminho_normalizado(transcricao.video_path) != caminho_normalizado(st["video_path"]):
            self.btn_iniciar.configure(state="normal", fg_color=COR_VERDE)
            self.seg_fps.configure(state="normal")
            self.app.mostrar_erro(
                "Exportação",
                "A transcrição pertence a outro episódio. Transcreva o vídeo atual antes de exportar legendas.",
            )
            return
        if self.var_legendas.get() and transcricao:
            legendas = recortar_legendas(
                gerar_blocos_legenda(transcricao, max_palavras=5), st["inicio"], st["fim"]
            )
            salvar_srt(legendas, Path(caminho_saida).with_suffix(".srt"))

        tarefa = TarefaRenderizacao(
            nome=f"Corte {perfil.nome}",
            funcao=renderizar_corte,
            kwargs={
                "caminho_video": st["video_path"],
                "inicio": st["inicio"],
                "fim": st["fim"],
                "titulo": st["titulo"],
                "caminho_saida": caminho_saida,
                "fps": fps,
                "legendas": legendas,
                "ajustes_av": st.get("ajustes_av"),
                "perfil_visual": perfil.id,
            },
            callback_progresso=self._on_progresso,
            callback_conclusao=self._on_conclusao,
            callback_erro=self._on_erro,
        )

        fila_render.adicionar(tarefa)

    def _on_progresso(self, pct: float, msg: str):
        self._eventos_ui.put(("progresso", pct, msg))

    def _atualizar_ui_progresso(self, pct: float, msg: str):
        self.barra_progresso.set(pct / 100.0)
        self.lbl_porcentagem.configure(text=f"{pct:.1f}%")
        self.lbl_status.configure(text=msg)

    def _on_conclusao(self, resultado: dict):
        self._eventos_ui.put(("conclusao", resultado))

    def _atualizar_ui_conclusao(self, resultado: dict):
        self.btn_iniciar.configure(state="normal", fg_color=COR_VERDE)
        self.seg_fps.configure(state="normal")
        
        if resultado.get("sucesso"):
            self.lbl_status.configure(text="✅ Renderização concluída com sucesso!", text_color=COR_VERDE)
            self.barra_progresso.set(1.0)
            self.lbl_porcentagem.configure(text="100%")
        else:
            erro = resultado.get("erro", "Erro desconhecido")
            self.lbl_status.configure(text=f"❌ Falha: {erro}", text_color="red")

    def _on_erro(self, erro_msg: str):
        self._eventos_ui.put(("erro", erro_msg))

    def _processar_eventos_ui(self):
        """Transfere eventos da thread de renderização para a thread do Tk."""
        try:
            while True:
                evento = self._eventos_ui.get_nowait()
                if evento[0] == "progresso":
                    self._atualizar_ui_progresso(evento[1], evento[2])
                elif evento[0] == "conclusao":
                    self._atualizar_ui_conclusao(evento[1])
                elif evento[0] == "erro":
                    self._atualizar_ui_erro(evento[1])
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(100, self._processar_eventos_ui)

    def _atualizar_ui_erro(self, erro_msg: str):
        self.btn_iniciar.configure(state="normal", fg_color=COR_VERDE)
        self.seg_fps.configure(state="normal")
        self.lbl_status.configure(text=f"❌ Erro fatal: {erro_msg}", text_color="red")

    def _abrir_pasta(self):
        pasta = self.app.estado.get("pasta_export")
        if pasta and Path(pasta).exists():
            os.startfile(pasta)
