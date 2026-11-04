"""Interface para trocar o fundo de cortes finais já exportados."""

from pathlib import Path
import queue
from tkinter import filedialog

import customtkinter as ctk

from modules.perfis_visuais import listar_perfis, perfil_por_nome
from modules.reaproveitar import VideoReaproveitavel, listar_videos, reaproveitar_video
from modules.tasks import TarefaRenderizacao, fila_render
from ui.app import (
    COR_BRANCO, COR_CINZA_CLARO, COR_CINZA_ESCURO, COR_ROXO_CLARO,
    COR_VERDE, COR_VERMELHO,
)


class TelaReaproveitar(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.videos: list[VideoReaproveitavel] = []
        self._linhas = {}
        self._eventos = queue.Queue()
        self._pendentes = []
        self._tarefa_atual = None
        self._cancelado = False
        self._concluidos = 0
        self._total = 0
        self._pasta_origem = ""
        self._pasta_saida = ""
        self._after_id = None
        self._construir_layout()
        self._after_id = self.after(100, self._processar_eventos)

    def destroy(self):
        if getattr(self, "_after_id", None) is not None:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        try:
            super().destroy()
        finally:
            if hasattr(self, "_linhas") and isinstance(self._linhas, dict):
                self._linhas.clear()
            if hasattr(self, "var_preservar"):
                self.var_preservar = None

    def _construir_layout(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        raiz = ctk.CTkFrame(self, fg_color="transparent")
        raiz.grid(row=0, column=0, padx=28, pady=22, sticky="nsew")
        raiz.grid_columnconfigure(0, weight=1)
        raiz.grid_rowconfigure(3, weight=1)

        ctk.CTkLabel(raiz, text="Reaproveitar cortes / Trocar perfil visual",
                     font=ctk.CTkFont(size=24, weight="bold"),
                     text_color=COR_BRANCO).grid(row=0, column=0, sticky="w", pady=(0, 12))

        controles = ctk.CTkFrame(raiz, fg_color="#211536", corner_radius=10)
        controles.grid(row=1, column=0, sticky="ew", pady=(0, 10), ipadx=10, ipady=10)
        ctk.CTkButton(controles, text="Selecionar pasta", fg_color=COR_ROXO_CLARO,
                      command=self._selecionar_pasta).pack(side="left", padx=(12, 8))
        self.lbl_pasta = ctk.CTkLabel(controles, text="Nenhuma pasta selecionada",
                                      text_color=COR_CINZA_ESCURO, width=270, anchor="w")
        self.lbl_pasta.pack(side="left", padx=(0, 12))
        ctk.CTkLabel(controles, text="Perfil de destino:").pack(side="left", padx=(0, 5))
        self.menu_perfil = ctk.CTkOptionMenu(
            controles, values=[p.nome for p in listar_perfis()], width=130,
            command=self._perfil_alterado,
        )
        self.menu_perfil.set("TOONMIX")
        self.menu_perfil.pack(side="left", padx=(0, 12))
        self.var_preservar = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(controles, text="Preservar textos existentes",
                        variable=self.var_preservar).pack(side="left", padx=(0, 12))

        saida = ctk.CTkFrame(raiz, fg_color="#150C24", corner_radius=10)
        saida.grid(row=2, column=0, sticky="ew", pady=(0, 10), ipadx=8, ipady=8)
        saida.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(saida, text="Pasta de saída:").grid(row=0, column=0, padx=(12, 6))
        self.entry_saida = ctk.CTkEntry(saida)
        self.entry_saida.grid(row=0, column=1, padx=5, sticky="ew")
        ctk.CTkButton(saida, text="Alterar", width=85, fg_color="#3E2661",
                      command=self._selecionar_saida).grid(row=0, column=2, padx=(6, 12))

        self.lista = ctk.CTkScrollableFrame(raiz, fg_color="#150C24")
        self.lista.grid(row=3, column=0, sticky="nsew", pady=(0, 10))
        self.lista.grid_columnconfigure(0, weight=1)

        rodape = ctk.CTkFrame(raiz, fg_color="transparent")
        rodape.grid(row=4, column=0, sticky="ew")
        rodape.grid_columnconfigure(0, weight=1)
        self.lbl_status = ctk.CTkLabel(rodape, text="Selecione uma pasta com arquivos MP4.",
                                       text_color=COR_CINZA_CLARO)
        self.lbl_status.grid(row=0, column=0, sticky="w")
        self.barra_arquivo = ctk.CTkProgressBar(rodape, progress_color=COR_ROXO_CLARO)
        self.barra_arquivo.grid(row=1, column=0, sticky="ew", pady=3)
        self.barra_geral = ctk.CTkProgressBar(rodape, progress_color=COR_VERDE)
        self.barra_geral.grid(row=2, column=0, sticky="ew", pady=3)
        self.barra_arquivo.set(0)
        self.barra_geral.set(0)
        botoes = ctk.CTkFrame(rodape, fg_color="transparent")
        botoes.grid(row=0, column=1, rowspan=3, padx=(16, 0))
        self.btn_gerar = ctk.CTkButton(botoes, text="Gerar versões", fg_color=COR_VERDE,
                                      command=self._gerar)
        self.btn_gerar.pack(side="left", padx=4)
        self.btn_cancelar = ctk.CTkButton(botoes, text="Cancelar", fg_color=COR_VERMELHO,
                                         state="disabled", command=self._cancelar)
        self.btn_cancelar.pack(side="left", padx=4)
        ctk.CTkButton(botoes, text="Repetir erros", fg_color="#3E2661",
                      command=lambda: self._gerar(apenas_erros=True)).pack(side="left", padx=4)

    def _selecionar_pasta(self):
        pasta = filedialog.askdirectory(title="Pasta com cortes MP4 já exportados")
        if not pasta:
            return
        self._pasta_origem = pasta
        self.lbl_pasta.configure(text=pasta, text_color=COR_BRANCO)
        self.lbl_status.configure(text="Validando vídeos encontrados…")
        self.app.update_idletasks()
        try:
            self.videos = listar_videos(pasta)
        except ValueError as erro:
            self.app.mostrar_erro("Reaproveitar cortes", str(erro))
            return
        self._definir_saida_padrao()
        self._redesenhar_lista()
        validos = sum(v.compativel for v in self.videos)
        self.lbl_status.configure(
            text=f"{len(self.videos)} MP4 encontrados; {validos} compatíveis com 1080×1920."
        )

    def _perfil_alterado(self, _nome=None):
        if self._pasta_origem:
            self._definir_saida_padrao()

    def _definir_saida_padrao(self):
        perfil = perfil_por_nome(self.menu_perfil.get())
        self._pasta_saida = str(Path(self._pasta_origem) / perfil.nome_canal.replace(" ", "_"))
        self.entry_saida.delete(0, "end")
        self.entry_saida.insert(0, self._pasta_saida)

    def _selecionar_saida(self):
        pasta = filedialog.askdirectory(title="Pasta de saída das novas versões")
        if pasta:
            self._pasta_saida = pasta
            self.entry_saida.delete(0, "end")
            self.entry_saida.insert(0, pasta)

    def _redesenhar_lista(self):
        for filho in self.lista.winfo_children():
            filho.destroy()
        self._linhas = {}
        for indice, video in enumerate(self.videos, 1):
            frame = ctk.CTkFrame(self.lista, fg_color="#211536")
            frame.grid(row=indice - 1, column=0, sticky="ew", padx=5, pady=4)
            frame.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(frame, text=str(indice), width=35).grid(row=0, column=0, padx=5)
            ctk.CTkLabel(frame, text=video.nome, anchor="w").grid(
                row=0, column=1, padx=5, pady=(7, 2), sticky="ew")
            detalhes = (f"{video.largura}×{video.altura} · {video.fps:.2f} FPS · "
                        f"{video.duracao:.1f}s · áudio {'sim' if video.tem_audio else 'não'}")
            if not video.compativel:
                detalhes = video.erro
            ctk.CTkLabel(frame, text=detalhes, text_color=(COR_CINZA_CLARO if video.compativel
                                                           else COR_VERMELHO), anchor="w").grid(
                row=1, column=1, padx=5, pady=(0, 7), sticky="ew")
            status = ctk.CTkLabel(frame, text=video.status, width=100,
                                  text_color=COR_CINZA_ESCURO)
            status.grid(row=0, column=2, padx=6)
            barra = ctk.CTkProgressBar(frame, width=120, progress_color=COR_ROXO_CLARO)
            barra.grid(row=1, column=2, padx=6)
            barra.set(1 if video.status == "concluído" else 0)
            self._linhas[video.caminho] = (status, barra)

    def _gerar(self, apenas_erros=False):
        if fila_render.ocupada:
            self.app.mostrar_aviso("Reaproveitar cortes", "Já existe um vídeo sendo processado.")
            return
        self._pasta_saida = self.entry_saida.get().strip()
        if not self._pasta_saida:
            self.app.mostrar_aviso("Saída", "Escolha uma pasta de saída.")
            return
        candidatos = [v for v in self.videos if v.compativel and
                      (not apenas_erros or v.status == "erro")]
        if not candidatos:
            self.app.mostrar_aviso("Reaproveitar cortes", "Nenhum arquivo compatível para processar.")
            return
        self._pendentes = list(candidatos)
        self._total = len(candidatos)
        self._concluidos = 0
        self._cancelado = False
        self.btn_gerar.configure(state="disabled")
        self.btn_cancelar.configure(state="normal")
        self.barra_geral.set(0)
        self._iniciar_proximo()

    def _iniciar_proximo(self):
        if self._cancelado or not self._pendentes:
            self._finalizar()
            return
        video = self._pendentes.pop(0)
        video.status = "processando"
        status, barra = self._linhas[video.caminho]
        status.configure(text=video.status, text_color=COR_CINZA_CLARO)
        barra.set(0)
        perfil = perfil_por_nome(self.menu_perfil.get())
        self._tarefa_atual = TarefaRenderizacao(
            nome=f"Reaproveitar {video.nome}", funcao=reaproveitar_video,
            kwargs={
                "caminho_video": video.caminho,
                "pasta_saida": self._pasta_saida,
                "perfil_destino": perfil.id,
                "preservar_textos": bool(self.var_preservar.get()),
                "item": video,
            },
            callback_progresso=lambda pct, msg, caminho=video.caminho:
                self._eventos.put(("progresso", caminho, pct, msg)),
            callback_conclusao=lambda resultado, caminho=video.caminho:
                self._eventos.put(("fim", caminho, resultado)),
            callback_erro=lambda erro, caminho=video.caminho:
                self._eventos.put(("erro", caminho, erro)),
        )
        fila_render.adicionar(self._tarefa_atual)

    def _processar_eventos(self):
        try:
            while True:
                evento = self._eventos.get_nowait()
                video = next((v for v in self.videos if v.caminho == evento[1]), None)
                if not video:
                    continue
                status, barra = self._linhas[video.caminho]
                if evento[0] == "progresso":
                    pct = evento[2]
                    barra.set(pct / 100)
                    self.barra_arquivo.set(pct / 100)
                    self.barra_geral.set((self._concluidos + pct / 100) / self._total)
                    self.lbl_status.configure(text=evento[3])
                elif evento[0] == "fim":
                    resultado = evento[2]
                    video.status = "concluído" if resultado.get("sucesso") else "erro"
                    video.erro = resultado.get("erro", "")
                    video.caminho_saida = resultado.get("caminho_saida", "")
                    status.configure(text=video.status, text_color=(COR_VERDE if resultado.get("sucesso")
                                                                    else COR_VERMELHO))
                    barra.set(1 if resultado.get("sucesso") else 0)
                    self._concluidos += 1
                    self._tarefa_atual = None
                    self._iniciar_proximo()
                elif evento[0] == "erro":
                    video.status, video.erro = "erro", evento[2]
                    status.configure(text="erro", text_color=COR_VERMELHO)
                    self._concluidos += 1
                    self._tarefa_atual = None
                    self._iniciar_proximo()
        except queue.Empty:
            pass
        if self.winfo_exists():
            self._after_id = self.after(100, self._processar_eventos)

    def _cancelar(self):
        self._cancelado = True
        self._pendentes.clear()
        if self._tarefa_atual:
            self._tarefa_atual.cancelar()
        self.lbl_status.configure(text="Cancelamento solicitado…")

    def _finalizar(self):
        self.btn_gerar.configure(state="normal")
        self.btn_cancelar.configure(state="disabled")
        self.barra_geral.set(self._concluidos / self._total if self._total else 0)
        self.lbl_status.configure(text=("Lote cancelado." if self._cancelado else
                                        f"Lote concluído: {self._concluidos}/{self._total}."))
        self._tarefa_atual = None
