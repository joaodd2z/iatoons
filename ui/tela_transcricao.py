"""Tela da Etapa 2.1: transcrição local, cache e edição manual."""

import queue
from dataclasses import asdict
from pathlib import Path
import customtkinter as ctk

from modules.tasks import TarefaRenderizacao, fila_transcricao
from modules.sessao import caminho_normalizado, sessao_corresponde
from modules.transcricao import (
    aplicar_texto_editado,
    buscar_transcricao_cache,
    salvar_transcricao,
    transcrever_video,
)
from modules.transcricao_v2 import escolher_configuracao
from ui.app import (
    COR_BRANCO,
    COR_CINZA_CLARO,
    COR_CINZA_ESCURO,
    COR_ROXO_CLARO,
    COR_VERDE,
    COR_VERMELHO,
)



class TelaTranscricao(ctk.CTkFrame):
    """Executa o Whisper fora da thread gráfica e permite corrigir o resultado."""

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self._eventos_ui = queue.Queue()
        self._tarefa = None
        self._construir_layout()
        self._restaurar_estado()
        self.after(100, self._processar_eventos_ui)

    def _construir_layout(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        container = ctk.CTkFrame(self, fg_color="transparent")
        container.grid(row=0, column=0, padx=40, pady=30, sticky="nsew")
        container.grid_columnconfigure(0, weight=1)
        container.grid_rowconfigure(4, weight=1)

        ctk.CTkLabel(
            container,
            text="Etapa 2.1: Transcrição Automática",
            font=ctk.CTkFont(size=24, weight="bold"),
            text_color=COR_BRANCO,
        ).grid(row=0, column=0, sticky="w", pady=(0, 18))

        opcoes = ctk.CTkFrame(container, fg_color="#211536", corner_radius=12)
        opcoes.grid(row=1, column=0, sticky="ew", pady=(0, 14), ipadx=18, ipady=14)

        selecao = ctk.CTkFrame(opcoes, fg_color="transparent")
        selecao.pack(fill="x", padx=12, pady=(0, 12))
        for coluna in range(3):
            selecao.grid_columnconfigure(coluna, weight=1, uniform="transcricao")
        ctk.CTkLabel(selecao, text="Idioma", text_color=COR_BRANCO).grid(row=0, column=0, sticky="w")
        self.combo_idioma = ctk.CTkComboBox(
            selecao,
            values=["Português", "Detecção automática"],
            width=190,
            state="readonly",
            command=lambda _valor: self._carregar_cache_idioma(),
        )
        self.combo_idioma.grid(row=1, column=0, sticky="ew", padx=(0, 12))
        self.combo_idioma.set("Português")

        ctk.CTkLabel(selecao, text="Modo", text_color=COR_BRANCO).grid(row=0, column=1, sticky="w")
        self.combo_modo = ctk.CTkComboBox(selecao, values=["Auto", "Rápido", "Preciso"],
            state="readonly", width=120, command=lambda _valor: self._carregar_cache_idioma())
        self.combo_modo.grid(row=1, column=1, sticky="ew", padx=(0, 12))
        self.combo_modo.set(self.app.estado.get("transcricao_modo", "Preciso"))
        ctk.CTkLabel(selecao, text="Backend", text_color=COR_BRANCO).grid(row=0, column=2, sticky="w")
        from modules.credenciais import obter_chave
        backends = ["Auto local", "Auto híbrido", "CPU"]
        if obter_chave("groq"):
            backends.append("Groq Whisper")
        if obter_chave("gemini"):
            backends.append("Gemini Transcribe")
        if obter_chave("groq") or obter_chave("gemini"):
            backends.append("Auto com nuvem")
        self.combo_backend = ctk.CTkComboBox(selecao, values=backends, state="readonly", width=170,
            command=lambda _valor: self._carregar_cache_idioma())
        self.combo_backend.grid(row=1, column=2, sticky="ew")
        self.combo_backend.set("Auto local")

        self.btn_transcrever = ctk.CTkButton(
            opcoes,
            text="Transcrever episódio",
            fg_color=COR_ROXO_CLARO,
            command=self._iniciar_transcricao,
        )
        self.btn_transcrever.pack(side="left", padx=6)

        self.btn_cancelar = ctk.CTkButton(
            opcoes,
            text="Cancelar",
            fg_color=COR_VERMELHO,
            state="disabled",
            command=self._cancelar,
        )
        self.btn_cancelar.pack(side="left", padx=6)

        self.lbl_status = ctk.CTkLabel(
            container, text="Selecione um vídeo na aba Importar.", text_color=COR_CINZA_CLARO,
            wraplength=650, justify="left", anchor="w",
        )
        self.lbl_status.grid(row=2, column=0, sticky="w", pady=(0, 6))

        self.barra = ctk.CTkProgressBar(container, progress_color=COR_ROXO_CLARO)
        self.barra.grid(row=3, column=0, sticky="ew", pady=(0, 12))
        self.barra.set(0)

        self.texto = ctk.CTkTextbox(
            container,
            font=ctk.CTkFont(family="Consolas", size=13),
            fg_color="#150C24",
            border_width=1,
            border_color="#3E2661",
            wrap="word",
        )
        self.texto.grid(row=4, column=0, sticky="nsew")
        self.texto.configure(state="disabled")

        ctk.CTkLabel(
            container,
            text="Edite somente o texto após cada marca [início --> fim]. Os tempos também podem ser corrigidos, desde que permaneçam válidos.",
            text_color=COR_CINZA_ESCURO,
            font=ctk.CTkFont(size=11),
        ).grid(row=5, column=0, sticky="w", pady=(7, 10))

        botoes = ctk.CTkFrame(container, fg_color="transparent")
        botoes.grid(row=6, column=0, sticky="ew")
        self.btn_salvar = ctk.CTkButton(
            botoes,
            text="Salvar correções",
            fg_color="#3E2661",
            state="disabled",
            command=self._salvar_edicao,
        )
        self.btn_salvar.pack(side="left")
        self.btn_realinhar = ctk.CTkButton(botoes, text="Realinhar trecho", fg_color="#3E2661",
            width=130, command=self._realinhar_trecho)
        self.btn_realinhar.pack(side="left", padx=8)
        ctk.CTkButton(
            botoes,
            text="Continuar para Corte ➔",
            fg_color=COR_VERDE,
            command=lambda: self.app.navegar("corte"),
        ).pack(side="right")

    def _idioma_codigo(self):
        return "pt" if self.combo_idioma.get() == "Português" else None

    def _opcoes_transcricao(self):
        modo = {"Auto": "auto", "Rápido": "rapido", "Preciso": "preciso"}[self.combo_modo.get()]
        backend = {"Auto local": "auto_local", "Auto híbrido": "auto_hibrido", "Auto com nuvem": "auto_nuvem",
                   "CPU": "cpu", "Groq Whisper": "groq_whisper", "Gemini Transcribe": "gemini_transcribe"}[self.combo_backend.get()]
        return {"modo": modo, "backend": backend}

    def _restaurar_estado(self):
        video = self.app.estado.get("video_path")
        if not video:
            self.btn_transcrever.configure(state="disabled")
            return
        self.lbl_status.configure(text="Pronto para transcrever em CPU/int8.")
        self._carregar_cache_idioma()

    def _carregar_cache_idioma(self):
        video = self.app.estado.get("video_path")
        if not video or fila_transcricao.ocupada:
            return
        atual = self.app.estado.get("transcricao")
        idioma = self._idioma_codigo()
        opcoes = self._opcoes_transcricao()
        config = escolher_configuracao(**opcoes, autorizar_nuvem=True)
        remoto = opcoes["backend"] in ("groq_whisper", "gemini_transcribe")
        compativel = bool(atual and ((remoto and atual.backend == opcoes["backend"])
            or (not remoto and config is not None
                and ((atual.parametros == asdict(config) or (not atual.parametros and config.modo == "preciso"))
                     or (opcoes["modo"] == "auto" and atual.modo == "preciso" and atual.backend == "cpu")))))
        if compativel and atual.video_path == str(Path(video).resolve()) and atual.idioma_solicitado == idioma:
            self._exibir_transcricao(atual, "Transcrição restaurada da sessão.")
            return
        cache = buscar_transcricao_cache(video, idioma=idioma, **opcoes, autorizar_nuvem=True)
        if cache is None and opcoes["modo"] == "auto" and not remoto:
            cache = buscar_transcricao_cache(video, idioma=idioma)
        if cache:
            self.app.atualizar_estado("transcricao", cache)
            self._exibir_transcricao(cache, "Transcrição carregada do cache local.")
        else:
            self.texto.configure(state="normal")
            self.texto.delete("1.0", "end")
            self.texto.configure(state="disabled")
            self.btn_salvar.configure(state="disabled")
            self.app.atualizar_estado("transcricao", None)
            self.lbl_status.configure(text="Sem cache compatível para esta configuração.")

    def _iniciar_transcricao(self):
        video = self.app.estado.get("video_path")
        if not video:
            self.app.mostrar_aviso("Transcrição", "Importe um vídeo primeiro.")
            return
        if fila_transcricao.ocupada:
            self.app.mostrar_aviso("Transcrição", "Já existe uma transcrição em andamento.")
            return

        opcoes = self._opcoes_transcricao()
        autorizado = False
        if opcoes["backend"] in ("groq_whisper", "gemini_transcribe", "auto_nuvem"):
            from tkinter import messagebox
            autorizado = messagebox.askyesno("Transcrição na nuvem",
                "Autorizar envio do áudio deste episódio ao backend de nuvem selecionado? Pode haver cobrança.", parent=self)
            if not autorizado:
                return
        self.app.atualizar_estado("transcricao_modo", self.combo_modo.get())

        sessao = int(self.app.estado.get("video_session", 0))
        origem = caminho_normalizado(video)
        self.btn_transcrever.configure(state="disabled")
        self.btn_cancelar.configure(state="normal")
        self.combo_idioma.configure(state="disabled")
        self.combo_modo.configure(state="disabled")
        self.combo_backend.configure(state="disabled")
        self.barra.set(0)
        self._tarefa = TarefaRenderizacao(
            nome="Transcrição Whisper",
            funcao=transcrever_video,
            kwargs={"caminho_video": video, "idioma": self._idioma_codigo(),
                    **opcoes, "autorizar_nuvem": autorizado},
            callback_progresso=lambda pct, msg: self._eventos_ui.put(("progresso", sessao, origem, pct, msg)),
            callback_conclusao=lambda resultado: self._eventos_ui.put(("conclusao", sessao, origem, resultado)),
            callback_erro=lambda erro: self._eventos_ui.put(("erro", sessao, origem, erro)),
        )
        fila_transcricao.adicionar(self._tarefa)

    def _cancelar(self):
        if self._tarefa:
            self._tarefa.cancelar()
            self.lbl_status.configure(text="Cancelamento solicitado…")

    def _realinhar_trecho(self):
        from modules.transcricao_alinhamento import realinhar_trecho_solicitado
        if fila_transcricao.ocupada:
            return
        transcricao = self.app.estado.get("transcricao")
        if not transcricao:
            return
        # Apply the editor snapshot first; only the requested invalidated segment is aligned.
        from copy import deepcopy
        snapshot = deepcopy(transcricao)
        try:
            aplicar_texto_editado(snapshot, self.texto.get("1.0", "end"), persistir=False)
            valor = ctk.CTkInputDialog(text="Número do trecho editado", title="Realinhar trecho").get_input()
            if valor is None:
                return
            indice = int(valor) - 1
            if not 0 <= indice < len(snapshot.segmentos) or not snapshot.segmentos[indice].word_timestamps_invalidated:
                raise ValueError("Selecione um trecho com palavras invalidadas por edição.")
        except (ValueError, OSError) as erro:
            self.app.mostrar_erro("Realinhamento", str(erro))
            return
        sessao = int(self.app.estado.get("video_session", 0))
        origem = caminho_normalizado(snapshot.video_path)
        self.texto.configure(state="disabled")
        self.btn_salvar.configure(state="disabled")
        self.btn_realinhar.configure(state="disabled")
        self.btn_transcrever.configure(state="disabled")
        self.btn_cancelar.configure(state="normal")
        self.combo_idioma.configure(state="disabled")
        self.combo_modo.configure(state="disabled")
        self.combo_backend.configure(state="disabled")
        self._tarefa = TarefaRenderizacao(nome="Realinhamento local", funcao=realinhar_trecho_solicitado,
            kwargs={"transcricao": snapshot, "indice": indice},
            callback_progresso=lambda pct,msg:self._eventos_ui.put(("progresso",sessao,origem,pct,msg)),
            callback_conclusao=lambda resultado:self._eventos_ui.put(("conclusao",sessao,origem,resultado)),
            callback_erro=lambda erro:self._eventos_ui.put(("erro",sessao,origem,erro)))
        fila_transcricao.adicionar(self._tarefa)

    def _processar_eventos_ui(self):
        try:
            while True:
                evento = self._eventos_ui.get_nowait()
                if not sessao_corresponde(self.app.estado, evento[1], evento[2]):
                    continue
                if evento[0] == "progresso":
                    self.barra.set(evento[3] / 100.0)
                    self.lbl_status.configure(text=evento[4])
                elif evento[0] == "conclusao":
                    resultado = evento[3]
                    if hasattr(resultado, "segmentos"):
                        self.app.atualizar_estado("transcricao", resultado)
                        self._exibir_transcricao(resultado, "Transcrição concluída e salva.")
                    else:
                        self.lbl_status.configure(text=resultado.get("erro", "Tarefa cancelada."), text_color=COR_VERMELHO)
                    self._finalizar_tarefa_ui()
                elif evento[0] == "erro":
                    self.lbl_status.configure(text=f"Erro: {evento[3]}", text_color=COR_VERMELHO)
                    self._finalizar_tarefa_ui()
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(100, self._processar_eventos_ui)

    def _finalizar_tarefa_ui(self):
        estado_editor = "normal" if self.app.estado.get("transcricao") else "disabled"
        self.texto.configure(state=estado_editor)
        self.btn_salvar.configure(state=estado_editor)
        self.btn_realinhar.configure(state="normal")
        self.btn_transcrever.configure(state="normal")
        self.btn_cancelar.configure(state="disabled")
        self.combo_idioma.configure(state="readonly")
        self.combo_modo.configure(state="readonly")
        self.combo_backend.configure(state="readonly")
        self._tarefa = None

    def _exibir_transcricao(self, transcricao, status):
        self.texto.configure(state="normal")
        self.texto.delete("1.0", "end")
        self.texto.insert("1.0", transcricao.texto_editavel())
        self.btn_salvar.configure(state="normal")
        idioma = transcricao.idioma_detectado or "desconhecido"
        tempo = transcricao.tempo_ultima_operacao_s or transcricao.tempo_transcricao_s
        velocidade = transcricao.duracao / tempo if tempo > 0 else 0
        resumo = f"{transcricao.modelo} · {transcricao.backend}/{transcricao.modo} · Áudio: {transcricao.duracao:.1f}s"
        if tempo > 0:
            resumo += f" · {'Cache' if getattr(transcricao, 'cache_reutilizado', False) else 'Transcrição'}: {tempo:.3f}s"
            if not getattr(transcricao, "cache_reutilizado", False):
                resumo += f" · {velocidade:.1f}x realtime"
        if transcricao.word_timestamps_invalidated:
            resumo += " · Edição com sincronização por segmento"
        if transcricao.alertas:
            resumo += " · " + transcricao.alertas[-1]
        self.lbl_status.configure(
            text=f"{status} Idioma: {idioma}; {len(transcricao.segmentos)} segmentos.\n{resumo}",
            text_color=COR_VERDE,
        )
        self.barra.set(1)

    def _salvar_edicao(self):
        transcricao = self.app.estado.get("transcricao")
        if not transcricao:
            return
        try:
            aplicar_texto_editado(transcricao, self.texto.get("1.0", "end"))
            salvar_transcricao(transcricao)
            self._exibir_transcricao(transcricao, "Correções salvas no cache local.")
        except (ValueError, OSError) as erro:
            self.app.mostrar_erro("Não foi possível salvar", str(erro))
