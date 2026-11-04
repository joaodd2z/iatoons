"""
tela_importar.py — Tela de seleção do vídeo original, pasta de destino e
Esteira Inteligente de Produção Multi-Episódio.
"""

from functools import partial
from pathlib import Path
import queue
import time
import tkinter as tk
from tkinter import filedialog
import customtkinter as ctk

from modules.esteira import ContextoEpisodio, EsteiraProducao, esteira_global
from modules.importacao import analisar_video
from modules.perfis_visuais import PERFIL_PADRAO_ID, obter_perfil
from modules.sessao import iniciar_sessao_video
from modules.tasks import fila_analise_ia, fila_render, fila_transcricao
from modules.titulos_lote import (
    ETAPAS_EDITORIAIS_ATIVAS,
    ResultadoParseTitulos,
    aplicar_titulos_episodio,
    construir_pacote_esteira,
    escrever_clipboard_sistema,
    ler_clipboard_sistema,
    parser_titulos_lote,
    sanitizar_texto_lote,
)
from ui.app import (
    COR_AMARELO,
    COR_BRANCO,
    COR_CINZA_CLARO,
    COR_CINZA_ESCURO,
    COR_ROXO_CLARO,
    COR_VERDE,
    COR_VERMELHO,
)


class TelaImportar(ctk.CTkFrame):
    """Tela para carregar vídeo(s) original(is) e controlar a Esteira Multi-Episódio."""

    def __init__(self, master, app, esteira: EsteiraProducao | None = None):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.esteira: EsteiraProducao = esteira or self.app.estado.get("esteira") or esteira_global
        self.app.estado["esteira"] = self.esteira
        self._eventos_esteira: queue.Queue = queue.Queue()
        self._cards_episodios: dict[str, dict] = {}
        self._modal_colar_titulos: ModalColarTitulos | None = None

        if not self.esteira.listar_episodios():
            self.esteira.carregar_estado()

        self._construir_layout()
        self._restaurar_estado()
        self._redesenhar_fila_esteira()

        self.esteira.registrar_callback(self._on_evento_esteira_threadsafe)
        self._after_id = self.after(100, self._processar_eventos_esteira)

    def destroy(self):
        try:
            self.esteira.remover_callback(self._on_evento_esteira_threadsafe)
            if getattr(self, "_after_id", None):
                self.after_cancel(self._after_id)
                self._after_id = None
            if hasattr(self, "_cards_episodios") and isinstance(self._cards_episodios, dict):
                self._cards_episodios.clear()
        except Exception:
            pass
        super().destroy()

    def _on_evento_esteira_threadsafe(self, evento: dict):
        """Recebe notificações de qualquer thread da esteira sem tocar nos widgets diretamente."""
        self._eventos_esteira.put(evento)

    def _construir_layout(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Scrollable container para comportar tanto o fluxo individual quanto a esteira
        container = ctk.CTkScrollableFrame(self, fg_color="transparent")
        container.grid(row=0, column=0, padx=28, pady=20, sticky="nsew")
        container.grid_columnconfigure(0, weight=1)

        # Título da tela
        ctk.CTkLabel(
            container,
            text="⚡ Central de Produção — Esteira Inteligente Multi-Episódio",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=COR_BRANCO,
        ).pack(anchor="w", pady=(0, 14))

        # ─── PASTA DE DESTINO ────────────────────────────────────────────────
        card_topo = ctk.CTkFrame(container, fg_color="#180F2B", border_width=1, border_color="#2D1A4A", corner_radius=12)
        card_topo.pack(fill="x", pady=(0, 14), padx=0, ipadx=18, ipady=10)

        frame_path_dest = ctk.CTkFrame(card_topo, fg_color="transparent")
        frame_path_dest.pack(fill="x", padx=18, pady=(4, 6))
        frame_path_dest.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            frame_path_dest,
            text="Pasta de Destino:",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COR_BRANCO,
        ).grid(row=0, column=0, padx=(0, 10))

        self.entry_destino = ctk.CTkEntry(
            frame_path_dest,
            font=ctk.CTkFont(size=13),
            placeholder_text="Escolha onde salvar os cortes gerados...",
            height=34,
            state="disabled",
            text_color=COR_BRANCO,
            fg_color="#120920",
            border_color="#3E2661",
        )
        self.entry_destino.grid(row=0, column=1, sticky="ew", padx=(0, 10))

        ctk.CTkButton(
            frame_path_dest,
            text="Alterar",
            width=90,
            height=34,
            fg_color="#3E2661",
            hover_color=COR_ROXO_CLARO,
            command=self._selecionar_destino,
        ).grid(row=0, column=2)

        # ─── SEÇÃO DE IMPORTAÇÃO INDIVIDUAL / MÚLTIPLA ───────────────────────
        card_video = ctk.CTkFrame(container, fg_color="#180F2B", border_width=1, border_color="#2D1A4A", corner_radius=12)
        card_video.pack(fill="x", pady=(0, 14), padx=0, ipadx=18, ipady=14)

        self.lbl_caminho_video = ctk.CTkLabel(
            card_video,
            text="Nenhum vídeo selecionado",
            font=ctk.CTkFont(size=14),
            text_color=COR_CINZA_ESCURO,
        )
        self.lbl_caminho_video.pack(pady=(4, 10))

        botoes_import = ctk.CTkFrame(card_video, fg_color="transparent")
        botoes_import.pack()

        ctk.CTkButton(
            botoes_import,
            text="Procurar Arquivo de Vídeo (Individual)...",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=40,
            fg_color=COR_ROXO_CLARO,
            hover_color="#7B3FC4",
            command=self._selecionar_video,
        ).pack(side="left", padx=6)

        self.btn_selecionar_multiplos = ctk.CTkButton(
            botoes_import,
            text="➕ Selecionar Vários Episódios para Esteira...",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=40,
            fg_color=COR_VERDE,
            hover_color="#27AE60",
            command=self._selecionar_multiplos_videos,
        )
        self.btn_selecionar_multiplos.pack(side="left", padx=6)

        
        # Detalhes do vídeo individual (oculto inicialmente)
        self.frame_detalhes = ctk.CTkFrame(card_video, fg_color="transparent")
        self.lbl_detalhe_info = ctk.CTkLabel(
            self.frame_detalhes,
            text="",
            font=ctk.CTkFont(size=13),
            text_color=COR_CINZA_CLARO,
            justify="left",
        )
        self.lbl_detalhe_info.pack(pady=8)

        # ─── SEÇÃO DA ESTEIRA INTELIGENTE MULTI-EPISÓDIO ─────────────────────
        card_esteira = ctk.CTkFrame(container, fg_color="#180F2B", border_width=1, border_color="#2D1A4A", corner_radius=12)
        card_esteira.pack(fill="both", expand=True, pady=(0, 12), padx=0, ipadx=16, ipady=14)

        cab_esteira = ctk.CTkFrame(card_esteira, fg_color="transparent")
        cab_esteira.pack(fill="x", padx=14, pady=(8, 6))

        ctk.CTkLabel(
            cab_esteira,
            text="⚡ Esteira Inteligente de Produção Contínua (Multi-Episódio)",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=COR_BRANCO,
        ).pack(side="left")

        self.lbl_concorrencia = ctk.CTkLabel(
            cab_esteira,
            text="Transcrição: 0/1 · Agência: 0/1 · Render: 0/1",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COR_AMARELO,
        )
        self.lbl_concorrencia.pack(side="right")

        controles_esteira = ctk.CTkFrame(card_esteira, fg_color="#120920", corner_radius=8)
        controles_esteira.pack(fill="x", padx=14, pady=(0, 10), ipady=6)

        self.btn_iniciar_esteira = ctk.CTkButton(
            controles_esteira,
            text="▶ Iniciar Esteira",
            width=145,
            fg_color=COR_VERDE,
            hover_color="#27AE60",
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._iniciar_esteira,
        )
        self.btn_iniciar_esteira.pack(side="left", padx=(10, 6))

        ctk.CTkButton(
            controles_esteira,
            text="➕ Adicionar Episódios",
            width=160,
            fg_color=COR_ROXO_CLARO,
            hover_color="#7B3FC4",
            command=self._selecionar_multiplos_videos,
        ).pack(side="left", padx=6)

        self.btn_cancelar_esteira = ctk.CTkButton(
            controles_esteira,
            text="⏹ Cancelar Todos",
            width=135,
            fg_color=COR_VERMELHO,
            command=self._cancelar_todos_esteira,
        )
        self.btn_cancelar_esteira.pack(side="left", padx=6)

        ctk.CTkButton(
            controles_esteira,
            text="🧹 Limpar Concluídos",
            width=145,
            fg_color="#3E2661",
            command=self._limpar_concluidos_esteira,
        ).pack(side="left", padx=6)

        self.lbl_resumo_esteira = ctk.CTkLabel(
            controles_esteira,
            text="Nenhum episódio na fila.",
            font=ctk.CTkFont(size=12),
            text_color=COR_CINZA_CLARO,
        )
        self.lbl_resumo_esteira.pack(side="right", padx=12)

        self.lista_episodios_frame = ctk.CTkFrame(card_esteira, fg_color="#120920", corner_radius=8)
        self.lista_episodios_frame.pack(fill="both", expand=True, padx=14, pady=(0, 8))
        self.lista_episodios_frame.grid_columnconfigure(0, weight=1)

    # ─── Restauração e Estado ────────────────────────────────────────────────

    def _restaurar_estado(self):
        st = self.app.estado
        self.app.atualizar_estado("perfil_visual_id", PERFIL_PADRAO_ID)
        if st["pasta_export"]:
            self._atualizar_entry_destino(st["pasta_export"])
        else:
            padrao = Path(__file__).parent.parent / "exportados"
            padrao.mkdir(exist_ok=True)
            self.app.atualizar_estado("pasta_export", str(padrao))
            self._atualizar_entry_destino(str(padrao))

        if st["video_info"] and st["video_info"].valido:
            self._exibir_detalhes_video(st["video_info"])
            if hasattr(self, "btn_avancar"):
                self.btn_avancar.configure(state="normal")

    def _atualizar_entry_destino(self, caminho: str):
        self.entry_destino.configure(state="normal")
        self.entry_destino.delete(0, "end")
        self.entry_destino.insert(0, caminho)
        self.entry_destino.configure(state="disabled")

    # ─── Importação Individual (Compatível) ──────────────────────────────────

    def _selecionar_video(self):
        caminho = filedialog.askopenfilename(
            title="Selecione o Vídeo Original",
            filetypes=[
                ("Arquivos de Vídeo", "*.mp4 *.mkv *.mov"),
                ("Todos os Arquivos", "*.*"),
            ],
        )
        if not caminho:
            return

        self.lbl_caminho_video.configure(
            text="Analisando vídeo... aguarde",
            text_color=COR_BRANCO,
        )
        self.app.update()

        info = analisar_video(caminho)

        if not info.valido:
            self.app.mostrar_erro("Erro de Importação", info.erro)
            self.lbl_caminho_video.configure(
                text="Nenhum vídeo válido selecionado",
                text_color=COR_CINZA_ESCURO,
            )
            self.frame_detalhes.pack_forget()
            self.btn_avancar.configure(state="disabled")
            self.app.atualizar_estado("video_path", None)
            self.app.atualizar_estado("video_info", None)
            return

        fila_transcricao.cancelar_tudo()
        fila_analise_ia.cancelar_tudo()
        fila_render.cancelar_tudo()
        iniciar_sessao_video(self.app.estado, caminho, info)

        self._exibir_detalhes_video(info)
        self.btn_avancar.configure(state="normal")

    def _exibir_detalhes_video(self, info):
        self.lbl_caminho_video.configure(
            text=f"✅ Arquivo: {info.nome}",
            text_color=COR_BRANCO,
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        texto = (
            f"• Duração: {info.duracao_formatada()}\n"
            f"• Resolução: {info.resolucao_str()}  ({info.fps_str()})\n"
            f"• Codec: {info.codec_video.upper()} / Áudio: {info.codec_audio.upper()}"
        )
        self.lbl_detalhe_info.configure(text=texto)
        self.frame_detalhes.pack(fill="x", pady=(8, 0))

    def _selecionar_destino(self):
        caminho = filedialog.askdirectory(title="Selecione a Pasta de Destino")
        if caminho:
            self.app.atualizar_estado("pasta_export", caminho)
            self._atualizar_entry_destino(caminho)

    # ─── Importação e Controle da Esteira Multi-Episódio ─────────────────────

    def _selecionar_multiplos_videos(self):
        caminhos = filedialog.askopenfilenames(
            title="Selecione os Episódios para a Esteira de Produção",
            filetypes=[
                ("Arquivos de Vídeo", "*.mp4 *.mkv *.mov"),
                ("Todos os Arquivos", "*.*"),
            ],
        )
        if not caminhos:
            return
        self.adicionar_videos_na_esteira(list(caminhos))

    def adicionar_videos_na_esteira(self, caminhos: list[str], *, iniciar_imediatamente: bool = False):
        pasta = self.app.estado.get("pasta_export") or str(Path(__file__).parent.parent / "exportados")
        self.app.atualizar_estado("perfil_visual_id", PERFIL_PADRAO_ID)
        adicionados, ignorados = self.esteira.adicionar_episodios(
            caminhos,
            pasta_saida=pasta,
            perfil_visual_id=PERFIL_PADRAO_ID,
            iniciar_imediatamente=iniciar_imediatamente,
        )
        self._redesenhar_fila_esteira()
        if ignorados and not adicionados:
            self.app.mostrar_aviso("Esteira de Produção", "\n".join(ignorados))
        elif ignorados:
            self.lbl_resumo_esteira.configure(
                text=f"{len(adicionados)} adicionado(s) · {len(ignorados)} duplicado(s) ignorado(s)."
            )

    def _iniciar_esteira(self):
        episodios = self.esteira.listar_episodios()
        if not episodios:
            self.app.mostrar_aviso("Esteira de Produção", "Adicione ao menos um episódio à fila antes de iniciar.")
            return
        self.esteira.iniciar()
        self._atualizar_cabecalho_esteira()

    def _cancelar_todos_esteira(self):
        self.esteira.cancelar_todos()
        self._redesenhar_fila_esteira()

    def _limpar_concluidos_esteira(self):
        self.esteira.limpar_concluidos()
        self._redesenhar_fila_esteira()

    def _tentar_novamente_episodio(self, ep_id: str):
        self.esteira.tentar_novamente(ep_id)
        self._atualizar_card_episodio(ep_id)
        self._atualizar_cabecalho_esteira()

    def _cancelar_episodio(self, ep_id: str):
        self.esteira.cancelar_episodio(ep_id)
        self._atualizar_card_episodio(ep_id)
        self._atualizar_cabecalho_esteira()

    def _remover_episodio(self, ep_id: str):
        if self.esteira.remover_episodio(ep_id):
            self._redesenhar_fila_esteira()
        else:
            self.app.mostrar_aviso("Esteira", "Não é possível remover um episódio com etapa em execução. Cancele-o primeiro.")

    def _copiar_transcricao_gemini(self, ep_id: str):
        """Copia a transcrição completa do episódio para a área de transferência no formato para o Gemini."""
        ep = self.esteira.obter_episodio(ep_id)
        if not ep:
            return

        from modules.transcricao_gemini import obter_transcricao_episodio, formatar_transcricao_para_gemini

        transcricao = obter_transcricao_episodio(ep)
        if not transcricao or not transcricao.segmentos:
            if ep.estado in ("aguardando", "em_execucao") and ep.etapa_atual in ("aguardando", "importacao", "transcricao"):
                msg = (
                    f"A transcrição de '{ep.nome}' ainda está em processamento na esteira.\n\n"
                    "Por favor, aguarde a conclusão da etapa de Transcrição."
                )
            else:
                msg = (
                    f"Nenhuma transcrição disponível para o episódio '{ep.nome}'.\n\n"
                    "Verifique se o episódio já passou pela etapa de Transcrição."
                )
            self.app.mostrar_aviso("Copiar transcrição para Gemini", msg)
            return

        try:
            texto = formatar_transcricao_para_gemini(transcricao, nome_episodio=ep.nome)
            escrever_clipboard_sistema(self, texto)
        except Exception as erro:
            self.app.mostrar_erro("Copiar transcrição para Gemini", f"Não foi possível copiar para a área de transferência.\n{erro}")
            return

        total_segs = len(transcricao.segmentos)
        unidade = "segmento copiado" if total_segs == 1 else "segmentos copiados"
        self.app.mostrar_info(
            "Copiar transcrição para Gemini",
            f"Transcrição completa copiada com sucesso!\n\n"
            f"Episódio: {ep.nome}\n"
            f"Total: {total_segs} {unidade} ({transcricao.duracao:.1f}s)\n\n"
            "O texto está na sua área de transferência, pronto para colar no Gemini."
        )

    def _copiar_todos_cortes(self, ep_id: str):
        """Copia todos os cortes do episódio (falas + timestamps) para colar no Gemini/ChatGPT."""
        ep = self.esteira.obter_episodio(ep_id)
        if not ep:
            return
        if not ep.planos:
            self.app.mostrar_aviso(
                "Copiar todos os cortes",
                f"O episódio '{ep.nome}' ainda não possui cortes.\n\n"
                "Aguarde a conclusão da análise da Agência na esteira.",
            )
            return
        try:
            texto, total = construir_pacote_esteira(ep)
            escrever_clipboard_sistema(self, texto)
        except Exception as erro:
            self.app.mostrar_erro("Copiar todos os cortes", f"Não foi possível copiar os cortes.\n{erro}")
            return
        unidade = "corte copiado" if total == 1 else "cortes copiados"
        self.app.mostrar_info(
            "Copiar todos os cortes",
            f"{total} {unidade} de '{ep.nome}'.\n\n"
            "Cole no Gemini/ChatGPT, copie a resposta inteira e use \"📝 Colar títulos em lote\".",
        )

    def _abrir_modal_colar_titulos(self, ep_id: str):
        """Abre o modal de colagem isolado para o episódio (sem tocar no clipboard na abertura)."""
        ep = self.esteira.obter_episodio(ep_id)
        if not ep:
            return
        if not ep.planos:
            self.app.mostrar_aviso("Colar títulos em lote", f"O episódio '{ep.nome}' ainda não possui cortes.")
            return
        if ep.estado == "em_execucao" and ep.etapa_atual in ETAPAS_EDITORIAIS_ATIVAS:
            self.app.mostrar_aviso(
                "Colar títulos em lote",
                "Este episódio está na etapa de análise/títulos. Aguarde a etapa concluir.",
            )
            return

        modal_anterior = getattr(self, "_modal_colar_titulos", None)
        if modal_anterior is not None:
            try:
                if modal_anterior.winfo_exists():
                    modal_anterior.destroy()
            except Exception:
                pass
            self._modal_colar_titulos = None

        self._modal_colar_titulos = ModalColarTitulos(self, ep, self.esteira)

    def _aprovar_todos_e_exportar(self, ep_id: str):
        """Aprova todos os cortes com título do episódio e dispara a renderização na esteira."""
        ep = self.esteira.obter_episodio(ep_id)
        if not ep:
            return
        if not ep.planos:
            self.app.mostrar_aviso("Aprovar e Exportar", f"O episódio '{ep.nome}' ainda não possui cortes.")
            return

        from modules.titulos_v2 import titulo_pendente
        cortes_com_titulo = [
            p for p in ep.planos
            if (p.titulo_escolhido or (p.titulos[0].texto if p.titulos else "")).strip()
            and not titulo_pendente((p.titulo_escolhido or (p.titulos[0].texto if p.titulos else "")).strip())
        ]
        if not cortes_com_titulo:
            self.app.mostrar_aviso(
                "Aprovar e Exportar",
                f"Nenhum corte de '{ep.nome}' possui título definido.\n\n"
                "Use '📝 Colar títulos em lote' para colar os títulos antes de aprovar e exportar."
            )
            return

        qtd, avisos = self.esteira.aprovar_e_exportar_episodio(ep_id)
        if qtd == 0:
            self.app.mostrar_aviso(
                "Aprovar e Exportar",
                "\n".join(avisos) or "Não foi possível aprovar os cortes deste episódio."
            )
            return

        try:
            self._atualizar_card_episodio(ep_id)
            self._atualizar_cabecalho_esteira()
        except tk.TclError:
            pass

        msg = (
            f"🚀 {qtd} corte(s) de '{ep.nome}' foram aprovados com sucesso!\n\n"
            "A esteira iniciou a produção e renderização automaticamente em segundo plano."
        )
        if avisos:
            msg += "\n\nObservações:\n• " + "\n• ".join(avisos)
        self.app.mostrar_info("Aprovar e Exportar", msg)

    def _concluir_ou_arquivar_episodio(self, ep_id: str):
        """Conclui e arquiva o episódio, retirando-o da esteira imediatamente em 1 clique."""
        ep = self.esteira.obter_episodio(ep_id)
        if not ep:
            return

        if ep.estado == "em_execucao":
            self.esteira.cancelar_episodio(ep_id)

        if self.esteira.concluir_ou_arquivar_episodio(ep_id):
            self._redesenhar_fila_esteira()
            self._atualizar_cabecalho_esteira()

    def _abrir_no_modo_agencia(self, ep_id: str):
        """Carrega os resultados do episódio na sessão da UI e abre a aba Modo Agência."""
        ep = self.esteira.obter_episodio(ep_id)
        if not ep:
            return
        info = ep.video_info or analisar_video(ep.video_path)
        if not info.valido:
            self.app.mostrar_erro("Abrir Episódio", info.erro)
            return
        iniciar_sessao_video(self.app.estado, ep.video_path, info)
        if ep.transcricao is not None:
            self.app.atualizar_estado("transcricao", ep.transcricao)
        if ep.intro is not None:
            self.app.atualizar_estado("intro_intervalo", ep.intro)
        if ep.planos and ep.transcricao is not None:
            try:
                from modules.agencia.persistencia import carregar_lista_agencia, salvar_lista_agencia
                if not carregar_lista_agencia(ep.video_path, ep.transcricao.video_id):
                    salvar_lista_agencia(ep.video_path, ep.transcricao.video_id, ep.planos)
            except Exception:
                pass
        self.app.navegar("agencia")

    # ─── Renderização e Atualização Segura da UI da Esteira ──────────────────

    def _cor_estado(self, estado: str) -> str:
        if estado == "concluido":
            return COR_VERDE
        if estado == "em_execucao":
            return COR_ROXO_CLARO
        if estado in ("erro", "cancelado"):
            return COR_VERMELHO
        if estado in ("revisao", "interrompido"):
            return COR_AMARELO
        return COR_CINZA_CLARO

    def _atualizar_cabecalho_esteira(self):
        conc = self.esteira.status_concorrencia()
        self.lbl_concorrencia.configure(
            text=(
                f"Transcrição: {conc['transcricoes_ativas']}/{conc['limite_transcricao']} "
                f"(fila: {conc['fila_transcricao']}) · "
                f"Agência: {conc['agencia_ativas']}/{conc['limite_agencia']} "
                f"(fila: {conc['fila_agencia']}) · "
                f"Render: {conc['renderizacoes_ativas']}/{conc['limite_renderizacao']} "
                f"(fila: {conc['fila_renderizacao']})"
            )
        )
        episodios = self.esteira.listar_episodios()
        total = len(episodios)
        concluidos = sum(1 for e in episodios if e.estado == "concluido")
        em_exec = sum(1 for e in episodios if e.estado == "em_execucao")
        erros = sum(1 for e in episodios if e.estado in ("erro", "revisao"))
        self.lbl_resumo_esteira.configure(
            text=f"Total: {total} · Em execução: {em_exec} · Concluídos: {concluidos} · Atenção/Erro: {erros}"
            if total
            else "Nenhum episódio na fila."
        )

    def _redesenhar_fila_esteira(self):
        for filho in self.lista_episodios_frame.winfo_children():
            filho.destroy()
        self._cards_episodios.clear()

        episodios = self.esteira.listar_episodios()
        self._atualizar_cabecalho_esteira()

        if not episodios:
            ctk.CTkLabel(
                self.lista_episodios_frame,
                text="A fila está vazia. Clique em '➕ Selecionar Vários Episódios para Esteira...' para adicionar vídeos.",
                text_color=COR_CINZA_ESCURO,
                font=ctk.CTkFont(size=13),
            ).grid(row=0, column=0, padx=16, pady=24)
            return

        for idx, ep in enumerate(episodios):
            card = ctk.CTkFrame(self.lista_episodios_frame, fg_color="#180F2B", border_width=1, border_color="#2D1A4A", corner_radius=8)
            card.grid(row=idx, column=0, sticky="ew", padx=8, pady=5)
            card.grid_columnconfigure(1, weight=1)

            # Linha 0: Nome, Etapa e Estado
            lbl_nome = ctk.CTkLabel(
                card,
                text=f"🎬 {idx + 1}. {ep.nome}",
                font=ctk.CTkFont(size=14, weight="bold"),
                text_color=COR_BRANCO,
                anchor="w",
            )
            lbl_nome.grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(8, 2))

            lbl_estado = ctk.CTkLabel(
                card,
                text="",
                font=ctk.CTkFont(size=12, weight="bold"),
                anchor="e",
            )
            lbl_estado.grid(row=0, column=2, sticky="e", padx=12, pady=(8, 2))

            # Linha 1: Barra de Progresso + Percentual
            barra = ctk.CTkProgressBar(card, progress_color=COR_ROXO_CLARO, height=10)
            barra.grid(row=1, column=0, columnspan=2, sticky="ew", padx=12, pady=4)

            lbl_pct = ctk.CTkLabel(
                card,
                text="0%",
                font=ctk.CTkFont(size=12),
                text_color=COR_CINZA_CLARO,
                width=50,
                anchor="e",
            )
            lbl_pct.grid(row=1, column=2, sticky="e", padx=12, pady=4)

            # Linha 2: Status detalhado e contagem de cortes
            lbl_msg = ctk.CTkLabel(
                card,
                text="",
                font=ctk.CTkFont(size=12),
                text_color=COR_CINZA_CLARO,
                anchor="w",
                justify="left",
                wraplength=620,
            )
            lbl_msg.grid(row=2, column=0, columnspan=2, sticky="w", padx=12, pady=(2, 6))

            lbl_cortes = ctk.CTkLabel(
                card,
                text="",
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color=COR_AMARELO,
                anchor="e",
            )
            lbl_cortes.grid(row=2, column=2, sticky="e", padx=12, pady=(2, 6))

            # Linha 3: Fluxo editorial e produção direta dos cortes
            frame_acoes1 = ctk.CTkFrame(card, fg_color="transparent")
            frame_acoes1.grid(row=3, column=0, columnspan=3, sticky="e", padx=12, pady=(0, 4))

            btn_copiar = ctk.CTkButton(
                frame_acoes1,
                text="📋 Copiar transcrição para Gemini",
                width=220,
                height=28,
                fg_color="#3E2661",
                hover_color="#523280",
                command=partial(self._copiar_transcricao_gemini, ep.id),
            )
            btn_copiar.pack(side="left", padx=4)

            btn_copiar_cortes = ctk.CTkButton(
                frame_acoes1,
                text="✂️ Copiar todos os cortes",
                width=165,
                height=28,
                fg_color="#3E2661",
                hover_color="#523280",
                command=partial(self._copiar_todos_cortes, ep.id),
            )
            btn_copiar_cortes.pack(side="left", padx=4)

            btn_colar_titulos = ctk.CTkButton(
                frame_acoes1,
                text="📝 Colar títulos em lote",
                width=165,
                height=28,
                fg_color="#3E2661",
                hover_color="#523280",
                command=partial(self._abrir_modal_colar_titulos, ep.id),
            )
            btn_colar_titulos.pack(side="left", padx=4)

            btn_aprovar_exportar = ctk.CTkButton(
                frame_acoes1,
                text="🚀 Aprovar Todos e Exportar",
                width=210,
                height=28,
                font=ctk.CTkFont(size=12, weight="bold"),
                fg_color="#00A86B",
                hover_color="#008f5a",
                command=partial(self._aprovar_todos_e_exportar, ep.id),
            )
            btn_aprovar_exportar.pack(side="left", padx=4)

            # Linha 4: Gestão do episódio e controle da esteira
            frame_acoes2 = ctk.CTkFrame(card, fg_color="transparent")
            frame_acoes2.grid(row=4, column=0, columnspan=3, sticky="e", padx=12, pady=(0, 8))

            btn_concluir_arquivar = ctk.CTkButton(
                frame_acoes2,
                text="✅ Concluir / Arquivar",
                width=160,
                height=28,
                fg_color=COR_VERDE,
                hover_color="#2b8a4e",
                command=partial(self._concluir_ou_arquivar_episodio, ep.id),
            )
            btn_concluir_arquivar.pack(side="left", padx=4)

            btn_retry = ctk.CTkButton(
                frame_acoes2,
                text="🔄 Tentar novamente",
                width=140,
                height=28,
                fg_color=COR_VERDE,
                hover_color="#2b8a4e",
                command=partial(self._tentar_novamente_episodio, ep.id),
            )
            btn_retry.pack(side="left", padx=4)

            btn_cancelar = ctk.CTkButton(
                frame_acoes2,
                text="⏹ Cancelar",
                width=95,
                height=28,
                fg_color=COR_VERMELHO,
                command=partial(self._cancelar_episodio, ep.id),
            )
            btn_cancelar.pack(side="left", padx=4)

            btn_remover = ctk.CTkButton(
                frame_acoes2,
                text="🗑 Remover",
                width=95,
                height=28,
                fg_color="#3E2661",
                hover_color="#523280",
                command=partial(self._remover_episodio, ep.id),
            )
            btn_remover.pack(side="left", padx=4)

            self._cards_episodios[ep.id] = {
                "card": card,
                "lbl_estado": lbl_estado,
                "barra": barra,
                "lbl_pct": lbl_pct,
                "lbl_msg": lbl_msg,
                "lbl_cortes": lbl_cortes,
                "btn_copiar": btn_copiar,
                "btn_copiar_cortes": btn_copiar_cortes,
                "btn_colar_titulos": btn_colar_titulos,
                "btn_aprovar_exportar": btn_aprovar_exportar,
                "btn_concluir_arquivar": btn_concluir_arquivar,
                                "btn_retry": btn_retry,
                "btn_cancelar": btn_cancelar,
                "btn_remover": btn_remover,
            }
            self._atualizar_card_episodio(ep.id)

    def _atualizar_card_episodio(self, ep_id: str):
        ep = self.esteira.obter_episodio(ep_id)
        widgets = self._cards_episodios.get(ep_id)
        if not ep or not widgets:
            return

        cor = self._cor_estado(ep.estado)
        widgets["lbl_estado"].configure(
            text=f"{ep.estado.upper()} · {ep.rotulo_etapa_atual}",
            text_color=cor,
        )
        widgets["barra"].set(max(0.0, min(1.0, ep.progresso_pct / 100.0)))
        widgets["lbl_pct"].configure(text=f"{ep.progresso_pct:.0f}%")

        msg = ep.mensagem_status
        if ep.erro and ep.erro not in msg:
            msg = f"{msg} — {ep.erro}"
        widgets["lbl_msg"].configure(
            text=msg,
            text_color=COR_VERMELHO if ep.estado == "erro" else COR_CINZA_CLARO,
        )
        widgets["lbl_cortes"].configure(
            text=f"Cortes: {ep.cortes_preparados_count} preparados · {ep.cortes_exportados_count} exportados"
        )

        em_exec = ep.estado == "em_execucao"
        pode_retry = ep.estado in ("erro", "cancelado", "interrompido", "revisao")
        pode_cancelar = ep.estado in ("aguardando", "em_execucao")
        pode_remover = not em_exec

        widgets["btn_retry"].configure(
            state="normal" if pode_retry else "disabled",
            command=partial(self._tentar_novamente_episodio, ep.id),
        )
        widgets["btn_cancelar"].configure(
            state="normal" if pode_cancelar else "disabled",
            command=partial(self._cancelar_episodio, ep.id),
        )
        widgets["btn_remover"].configure(
            state="normal" if pode_remover else "disabled",
            command=partial(self._remover_episodio, ep.id),
        )
        if "btn_abrir" in widgets:
            widgets["btn_abrir"].configure(state="normal" if ep.transcricao is not None else "disabled")
        if "btn_copiar" in widgets:
            widgets["btn_copiar"].configure(
                state="normal",
                command=partial(self._copiar_transcricao_gemini, ep.id),
            )
        if "btn_concluir_arquivar" in widgets:
            widgets["btn_concluir_arquivar"].configure(
                state="normal",
                command=partial(self._concluir_ou_arquivar_episodio, ep.id),
            )
        tem_planos = bool(ep.planos)
        em_etapa_editorial = em_exec and ep.etapa_atual in ETAPAS_EDITORIAIS_ATIVAS
        renderizando = em_exec and ep.etapa_atual in ("preparacao", "renderizacao", "validacao_saida")
        pode_aprovar_exportar = tem_planos and not renderizando and not em_etapa_editorial
        if "btn_aprovar_exportar" in widgets:
            widgets["btn_aprovar_exportar"].configure(
                state="normal" if pode_aprovar_exportar else "disabled",
                command=partial(self._aprovar_todos_e_exportar, ep.id),
            )
        if "btn_copiar_cortes" in widgets:
            widgets["btn_copiar_cortes"].configure(
                state="normal" if tem_planos else "disabled",
                command=partial(self._copiar_todos_cortes, ep.id),
            )
        if "btn_colar_titulos" in widgets:
            widgets["btn_colar_titulos"].configure(
                state="normal" if (tem_planos and not em_etapa_editorial) else "disabled",
                command=partial(self._abrir_modal_colar_titulos, ep.id),
            )

    def _processar_eventos_esteira(self):
        try:
            atualizar_ids: set[str] = set()
            redesenhar_tudo = False
            while True:
                ev = self._eventos_esteira.get_nowait()
                eid = ev.get("episodio_id", "")
                if ev.get("tipo") == "episodio_adicionado" or (eid and eid not in self._cards_episodios):
                    redesenhar_tudo = True
                elif eid:
                    atualizar_ids.add(eid)
        except queue.Empty:
            pass
        except tk.TclError:
            return

        try:
            if not self.winfo_exists():
                return
            if redesenhar_tudo:
                self._redesenhar_fila_esteira()
            elif atualizar_ids:
                for eid in atualizar_ids:
                    self._atualizar_card_episodio(eid)
                self._atualizar_cabecalho_esteira()
            self._after_id = self.after(100, self._processar_eventos_esteira)
        except tk.TclError:
            return


class ModalColarTitulos(ctk.CTkToplevel):
    """Modal para colar a resposta da IA, ver a prévia (corte → título) e aplicar em lote."""

    def __init__(self, tela: "TelaImportar", episodio: ContextoEpisodio, esteira: EsteiraProducao):
        super().__init__(tela)
        self.tela = tela
        self.ep = episodio
        self.esteira = esteira
        self.resultado: ResultadoParseTitulos | None = None
        self._after_analise = None

        self.title(f"Colar títulos em lote — {episodio.nome}")
        self.geometry("780x660")
        self.minsize(680, 500)
        self.transient(tela.winfo_toplevel())
        self._construir()
        self.after(50, self._focar)

    def _focar(self):
        try:
            self.grab_set()
            self.txt.focus_set()
        except tk.TclError:
            pass

    def _construir(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        ctk.CTkLabel(
            self, text=f"🎬 {self.ep.nome} — {len(self.ep.planos)} corte(s)",
            font=ctk.CTkFont(size=15, weight="bold"), text_color=COR_BRANCO, anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 2))
        ctk.CTkLabel(
            self, text="Cole abaixo os títulos gerados pelo ChatGPT/Gemini para os cortes (ou a conversa inteira). "
                       "Formatos aceitos: \"CORTE 1: título\", lista numerada (1. Título) ou uma linha por título. "
                       "A prévia é atualizada automaticamente.",
            font=ctk.CTkFont(size=12), text_color=COR_CINZA_CLARO, anchor="w", justify="left", wraplength=740,
        ).grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 6))

        self.txt = ctk.CTkTextbox(self, height=170, font=ctk.CTkFont(size=12), border_width=1,
                                  border_color="#3E2661")
        self.txt.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 6))
        self.txt.bind("<KeyRelease>", lambda _e: self._agendar_analise())
        alvos_paste = [self.txt]
        if hasattr(self.txt, "_textbox") and self.txt._textbox is not self.txt:
            alvos_paste.append(self.txt._textbox)
        for alvo in alvos_paste:
            alvo.bind("<<Paste>>", self._ao_colar_evento)
            alvo.bind("<Control-v>", self._ao_colar_evento)
            alvo.bind("<Control-V>", self._ao_colar_evento)

        self.frame_previa = ctk.CTkScrollableFrame(self, label_text="Prévia: Corte → Título")
        self.frame_previa.grid(row=3, column=0, sticky="nsew", padx=16, pady=(0, 6))

        self.lbl_info = ctk.CTkLabel(self, text="Aguardando texto…", font=ctk.CTkFont(size=12),
                                     text_color=COR_CINZA_CLARO, anchor="w", justify="left", wraplength=740)
        self.lbl_info.grid(row=4, column=0, sticky="ew", padx=16)

        rodape = ctk.CTkFrame(self, fg_color="transparent")
        rodape.grid(row=5, column=0, sticky="ew", padx=16, pady=(6, 14))
        ctk.CTkButton(rodape, text="Cancelar", width=100, fg_color="#3E2661",
                      command=self.destroy).pack(side="right", padx=(6, 0))
        self.btn_aplicar_exportar = ctk.CTkButton(
            rodape,
            text="🚀 Aplicar e Exportar",
            width=180,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#00A86B",
            hover_color="#008f5a",
            state="disabled",
            command=self._aplicar_e_exportar,
        )
        self.btn_aplicar_exportar.pack(side="right", padx=(6, 0))
        self.btn_aplicar = ctk.CTkButton(
            rodape,
            text="💾 Apenas Salvar",
            width=135,
            fg_color=COR_VERDE,
            hover_color="#2b8a4e",
            state="disabled",
            command=self._aplicar,
        )
        self.btn_aplicar.pack(side="right")
        ctk.CTkButton(rodape, text="📋 Colar da área de transferência", width=220, fg_color="#3E2661",
                      hover_color="#523280", command=self._colar_clipboard).pack(side="left")

    def _ao_colar_evento(self, _event=None):
        """Intercepta <<Paste>> / Ctrl+V com 'break' para ler diretamente do OS Clipboard sem usar tk_textPaste."""
        agora = time.time()
        if agora - getattr(self, "_ultimo_paste_ts", 0.0) < 0.05:
            return "break"
        self._ultimo_paste_ts = agora
        self._colar_clipboard()
        return "break"

    def _exibir_erro_formato_invalido(self):
        if getattr(self, "_after_analise", None) is not None:
            try:
                self.after_cancel(self._after_analise)
            except Exception:
                pass
            self._after_analise = None

        self.txt.delete("1.0", "end")
        for filho in self.frame_previa.winfo_children():
            filho.destroy()
        self.resultado = None
        self.btn_aplicar.configure(state="disabled")
        if hasattr(self, "btn_aplicar_exportar"):
            self.btn_aplicar_exportar.configure(state="disabled")
        aviso_msg = "⚠️ Formato inválido: Certifique-se de copiar apenas os títulos (ex: CORTE 1: ...)."
        self.lbl_info.configure(text=aviso_msg, text_color=COR_VERMELHO)
        self.tela.app.mostrar_aviso(
            "Formato inválido",
            "Formato inválido: Certifique-se de copiar apenas os títulos (ex: CORTE 1: ...).",
        )

    def _colar_clipboard(self):
        texto_bruto = ler_clipboard_sistema(self)
        if not texto_bruto or not str(texto_bruto).strip():
            return

        total_esperados = len(self.ep.planos) if self.ep and self.ep.planos else 0
        res = parser_titulos_lote(texto_bruto, total_esperados=total_esperados)

        # Regra de Ouro: só dispara alerta de erro se NENHUM título válido for encontrado (total_encontrados == 0)
        if not res or res.total_encontrados == 0:
            self._exibir_erro_formato_invalido()
            return

        texto_limpo = sanitizar_texto_lote(texto_bruto) or "\n".join(
            f"CORTE {it.indice}: {it.titulo}" for it in res.itens
        )
        self.txt.delete("1.0", "end")
        self.txt.insert("1.0", texto_limpo)
        self._analisar()

    def _agendar_analise(self):
        if self._after_analise is not None:
            try:
                self.after_cancel(self._after_analise)
            except tk.TclError:
                pass
        self._after_analise = self.after(250, self._analisar)

    def _analisar(self):
        self._after_analise = None
        if not self.winfo_exists():
            return
        texto = self.txt.get("1.0", "end").strip()
        for filho in self.frame_previa.winfo_children():
            filho.destroy()
        if not texto:
            self.resultado = None
            self.btn_aplicar.configure(state="disabled")
            if hasattr(self, "btn_aplicar_exportar"):
                self.btn_aplicar_exportar.configure(state="disabled")
            self.lbl_info.configure(text="Aguardando texto…", text_color=COR_CINZA_CLARO)
            return

        total_esperados = len(self.ep.planos) if self.ep and self.ep.planos else 0
        res = parser_titulos_lote(texto, total_esperados=total_esperados)

        # Se encontrou títulos válidos mas havia lixo/instruções no texto, limpa o campo silenciosamente
        if res.total_encontrados > 0:
            t_lower = texto.lower()
            if any(k in t_lower for k in ("instruções", "instrucoes", "formato de resposta", "ordem final", "analise cada corte", "peça ao gemini")):
                texto_limpo = sanitizar_texto_lote(texto)
                if texto_limpo and texto_limpo.strip() != texto:
                    self.txt.delete("1.0", "end")
                    self.txt.insert("1.0", texto_limpo)
                    texto = texto_limpo
                    res = parser_titulos_lote(texto, total_esperados=total_esperados)

        self.resultado = res
        if res.total_encontrados == 0:
            self.btn_aplicar.configure(state="disabled")
            if hasattr(self, "btn_aplicar_exportar"):
                self.btn_aplicar_exportar.configure(state="disabled")
            self.lbl_info.configure(
                text="⚠️ Formato inválido: Certifique-se de copiar apenas os títulos (ex: CORTE 1: ...).",
                text_color=COR_VERMELHO,
            )
            return

        mapa = {it.indice: it for it in res.itens}
        for idx, plano in enumerate(self.ep.planos, 1):
            item = mapa.get(idx)
            linha = ctk.CTkFrame(self.frame_previa, fg_color="#1A102A", corner_radius=6)
            linha.pack(fill="x", padx=4, pady=3)
            ctk.CTkLabel(linha, text=f"Corte {idx}", width=70, anchor="w",
                         font=ctk.CTkFont(size=12, weight="bold"),
                         text_color=COR_VERDE if item else COR_AMARELO).pack(side="left", padx=8, pady=4)
            if item:
                desc = f"🎯 {item.titulo}"
                if item.titulo_capa:
                    desc += f"\n🖼 Capa: {item.titulo_capa}"
                if item.alternativas:
                    desc += f"\n🔀 {' | '.join(item.alternativas)}"
                cor = COR_BRANCO
            else:
                desc = f"(sem título na resposta — mantém: {plano.titulo or 'sem título'})"
                cor = COR_CINZA_CLARO
            ctk.CTkLabel(linha, text=desc, anchor="w", justify="left", wraplength=600, text_color=cor,
                         font=ctk.CTkFont(size=12)).pack(side="left", fill="x", expand=True, padx=6, pady=4)

        msg = f"{res.total_encontrados} de {len(self.ep.planos)} corte(s) receberão título."
        if res.avisos:
            msg += "\n" + "\n".join(f"• {a}" for a in res.avisos)
        self.lbl_info.configure(text=msg, text_color=COR_AMARELO if res.avisos else COR_VERDE)
        pode_aplicar = bool(res.total_encontrados)
        self.btn_aplicar.configure(state="normal" if pode_aplicar else "disabled")
        if hasattr(self, "btn_aplicar_exportar"):
            self.btn_aplicar_exportar.configure(state="normal" if pode_aplicar else "disabled")

    def _aplicar_e_exportar(self):
        if not self.resultado or not self.resultado.itens:
            return
        qtd, avisos = aplicar_titulos_episodio(self.esteira, self.ep.id, self.resultado.itens)
        tela, ep_id = self.tela, self.ep.id
        self.destroy()
        if qtd == 0:
            tela.app.mostrar_aviso("Colar títulos em lote", "\n".join(avisos) or "Nenhum título aplicado.")
            return
        tela._aprovar_todos_e_exportar(ep_id)

    def _aplicar(self):
        if not self.resultado or not self.resultado.itens:
            return
        qtd, avisos = aplicar_titulos_episodio(self.esteira, self.ep.id, self.resultado.itens)
        tela, ep_id, nome = self.tela, self.ep.id, self.ep.nome
        self.destroy()
        if qtd == 0:
            tela.app.mostrar_aviso("Colar títulos em lote", "\n".join(avisos) or "Nenhum título aplicado.")
            return
        try:
            tela._atualizar_card_episodio(ep_id)
        except tk.TclError:
            pass
        msg = f"✅ {qtd} título(s) aplicado(s) em lote em '{nome}'."
        ep = tela.esteira.obter_episodio(ep_id)
        if ep is not None and ep.estado == "revisao":
            msg += "\n\nClique no botão '🚀 Aprovar Todos e Exportar' no card para iniciar a renderização imediatamente."
        if avisos:
            msg += "\n\nObservações:\n• " + "\n• ".join(avisos)
        tela.app.mostrar_info("Colar títulos em lote", msg)

    def destroy(self):
        if self._after_analise is not None:
            try:
                self.after_cancel(self._after_analise)
            except tk.TclError:
                pass
            self._after_analise = None
        try:
            self.grab_release()
        except tk.TclError:
            pass
        super().destroy()
