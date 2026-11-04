"""Sugestão, edição e exportação sequencial de múltiplos cortes."""

from pathlib import Path
import queue
import os
import customtkinter as ctk

from modules.cortes import Corte, sugerir_cortes, corte_de_plano
from modules.agencia.editorial import PlanoAgencia, _RespostaAgencia
from modules.analise_ia import analisar_transcricao
from modules.ai.comparacao import consumir_resultado, provider_orcado, orcamento_episodio
from modules.titulos_v2 import (executar_analise_com_titulos, validar_titulo_final, registrar_uso,
                               reavaliar_existentes, titulo_exibicao, STATUS_REVISAO_TITULO, MOTIVO_REVISAO_TITULO)
from modules.config_ia import carregar_config_ia
from modules.importacao import hhmmss_para_segundos, normalizar_tempos, segundos_para_hhmmss
from modules.ganchos_ab import identificar_gancho_b, remapear_legendas, segmentos_da_versao
from modules.intro import (IntervaloIntro, carregar_intro, detectar_intro, remover_intro,
                           salvar_intro)
from modules.legendas import gerar_blocos_legenda, recortar_legendas, salvar_srt
from modules.lote import carregar_lista, caminho_sem_sobrescrever, salvar_lista, validar_exportacao_automatica
from modules.renderer import renderizar_corte
from modules.preset import TEMP_DIR
from modules.perfis_visuais import obter_perfil
from modules.remix_narrativo import (
    CandidatoRemix, SegmentoNarrativo, analisar_remixes, carregar_lista_remix,
    duracao_timeline, salvar_lista_remix, timeline_remix,
    provedor_remix_orcado,
)
from modules.tasks import TarefaRenderizacao, fila_analise_ia, fila_render
from modules.sessao import caminho_normalizado, sessao_corresponde
from ui.app import (
    COR_BRANCO, COR_CINZA_CLARO, COR_CINZA_ESCURO, COR_ROXO_CLARO,
    COR_VERDE, COR_VERMELHO,
)


class TelaLote(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.cortes: list[Corte] = []
        self._modo_atual = "normal"
        self._listas_modo: dict[str, list[Corte]] = {"normal": [], "remix": []}
        self._remix_widgets = {}
        self._linhas = {}
        self._eventos = queue.Queue()
        self._pendentes = []
        self._tarefa_atual = None
        self._total_lote = 0
        self._concluidos = 0
        self._cancelado = False
        self._construir_layout()
        self._restaurar_lista()
        self._receber_comparacao()
        self.after(100, self._processar_eventos)

    def _receber_comparacao(self):
        resultado = consumir_resultado(self.app.estado, 'normal')
        if resultado is None:
            return
        self.cortes = [Corte(c['inicio'], c['fim'], c['titulos'][0], descricao=c['descricao'],
                            motivo_ia=c['motivo'], opcoes_titulo=c['titulos'],
                            inicio_original_ia=c['inicio_original'], gancho_ia=c['gancho'], origem="analise_ia")
                       for c in resultado['itens']]
        if all(c.get("plano_remix") for c in resultado['itens']):
            planos = [PlanoAgencia.de_dict(c['plano_remix']) for c in resultado['itens']]
            reavaliar_existentes(planos,self.app.estado['transcricao'])
            self.cortes = [corte_de_plano(p) for p in planos]
            self._modo_atual = "remix"
            self.var_modo_edicao.set("Remix narrativo")
            self.btn_ia.configure(text="Criar remixes com IA")
        self._listas_modo[getattr(self, "_modo_atual", "normal")] = self.cortes
        self._redesenhar_lista()
        self.lbl_status.configure(text=f"Resultado de {resultado['provider'].title()} carregado para revisão.")

    def _construir_layout(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        raiz = ctk.CTkFrame(self, fg_color="transparent")
        raiz.grid(row=0, column=0, padx=28, pady=22, sticky="nsew")
        raiz.grid_columnconfigure(0, weight=1)
        raiz.grid_rowconfigure(4, weight=1)

        ctk.CTkLabel(raiz, text="Cortes em lote", font=ctk.CTkFont(size=24, weight="bold"),
                     text_color=COR_BRANCO).grid(row=0, column=0, sticky="w", pady=(0, 12))

        controles = ctk.CTkFrame(raiz, fg_color="#211536", corner_radius=10)
        controles.grid(row=1, column=0, sticky="ew", pady=(0, 10), ipadx=10, ipady=10)
        ctk.CTkLabel(controles, text="Modo de edição:").pack(side="left", padx=(12, 5))
        self.var_modo_edicao = ctk.StringVar(value="Corte normal")
        self.seletor_modo = ctk.CTkOptionMenu(
            controles, values=["Corte normal", "Remix narrativo"],
            variable=self.var_modo_edicao, width=145, command=self._trocar_modo,
        )
        self.seletor_modo.pack(side="left", padx=(0, 12))
        ctk.CTkLabel(controles, text="Máximo:").pack(side="left", padx=(0, 6))
        self.entry_max = ctk.CTkEntry(controles, width=55)
        self.entry_max.insert(0, "5")
        self.entry_max.pack(side="left", padx=(0, 10))
        ctk.CTkButton(controles, text="Sugerir cortes", fg_color=COR_ROXO_CLARO,
                      command=self._sugerir).pack(side="left", padx=4)
        self.btn_ia = ctk.CTkButton(controles, text="Analisar episódio com IA", fg_color=COR_VERDE,
                                    command=self._analisar_ia)
        self.btn_ia.pack(side="left", padx=4)
        ctk.CTkButton(controles, text="+ Corte manual", fg_color="#3E2661",
                      command=self._adicionar_manual).pack(side="left", padx=4)
        ctk.CTkButton(controles, text="Salvar lista", fg_color="#3E2661",
                      command=self._salvar_lista).pack(side="left", padx=4)

        intro = ctk.CTkFrame(raiz, fg_color="#211536", corner_radius=10)
        intro.grid(row=2, column=0, sticky="ew", pady=(0, 10), ipadx=8, ipady=8)
        self.var_excluir_intro = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(intro, text="Excluir intro", variable=self.var_excluir_intro).pack(
            side="left", padx=(12, 8))
        ctk.CTkLabel(intro, text="início:").pack(side="left", padx=(8, 3))
        self.entry_intro_inicio = ctk.CTkEntry(intro, width=105)
        self.entry_intro_inicio.pack(side="left", padx=3)
        ctk.CTkLabel(intro, text="fim:").pack(side="left", padx=(8, 3))
        self.entry_intro_fim = ctk.CTkEntry(intro, width=105)
        self.entry_intro_fim.pack(side="left", padx=3)
        ctk.CTkButton(intro, text="Detectar intro", width=105, fg_color="#3E2661",
                      command=self._detectar_intro).pack(side="left", padx=5)
        ctk.CTkButton(intro, text="Salvar intervalo", width=110, fg_color="#3E2661",
                      command=self._salvar_intro).pack(side="left", padx=5)
        self.lbl_intro = ctk.CTkLabel(intro, text="Não verificada", text_color=COR_CINZA_ESCURO)
        self.lbl_intro.pack(side="left", padx=8)

        legenda = ctk.CTkFrame(raiz, fg_color="#150C24", corner_radius=10)
        legenda.grid(row=3, column=0, sticky="ew", pady=(0, 10), ipadx=8, ipady=8)
        ctk.CTkLabel(legenda, text="Legenda — palavras/bloco:").pack(side="left", padx=(12, 5))
        self.entry_palavras = ctk.CTkEntry(legenda, width=45)
        self.entry_palavras.insert(0, "5")
        self.entry_palavras.pack(side="left", padx=4)
        ctk.CTkLabel(legenda, text="tamanho:").pack(side="left", padx=(10, 4))
        self.entry_tamanho = ctk.CTkEntry(legenda, width=55)
        self.entry_tamanho.insert(0, "56")
        self.entry_tamanho.pack(side="left", padx=4)
        ctk.CTkLabel(legenda, text="posição Y:").pack(side="left", padx=(10, 4))
        self.entry_y = ctk.CTkEntry(legenda, width=65)
        self.entry_y.insert(0, "1528")
        self.entry_y.pack(side="left", padx=4)
        self.var_srt = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(legenda, text="Exportar SRT", variable=self.var_srt).pack(side="left", padx=14)

        self.lista = ctk.CTkScrollableFrame(raiz, fg_color="#150C24")
        self.lista.grid(row=4, column=0, sticky="nsew", pady=(0, 10))
        self.lista.grid_columnconfigure(0, weight=1)

        rodape = ctk.CTkFrame(raiz, fg_color="transparent")
        rodape.grid(row=5, column=0, sticky="ew")
        rodape.grid_columnconfigure(0, weight=1)
        self.lbl_status = ctk.CTkLabel(rodape, text="Aguardando cortes.", text_color=COR_CINZA_CLARO)
        self.lbl_status.grid(row=0, column=0, sticky="w")
        self.barra_individual = ctk.CTkProgressBar(rodape, width=240, progress_color=COR_ROXO_CLARO)
        self.barra_individual.grid(row=1, column=0, sticky="ew", pady=3)
        self.barra_geral = ctk.CTkProgressBar(rodape, width=240, progress_color=COR_VERDE)
        self.barra_geral.grid(row=2, column=0, sticky="ew", pady=3)
        self.barra_individual.set(0)
        self.barra_geral.set(0)

        botoes = ctk.CTkFrame(rodape, fg_color="transparent")
        botoes.grid(row=0, column=1, rowspan=3, padx=(16, 0))
        self.btn_gerar = ctk.CTkButton(botoes, text="Gerar todos os cortes", fg_color=COR_VERDE,
                                      command=self._gerar_todos)
        self.btn_gerar.pack(side="left", padx=4)
        self.btn_cancelar = ctk.CTkButton(botoes, text="Cancelar", fg_color=COR_VERMELHO,
                                         state="disabled", command=self._cancelar)
        self.btn_cancelar.pack(side="left", padx=4)
        self.btn_repetir = ctk.CTkButton(botoes, text="Repetir erros", fg_color="#3E2661",
                                        command=self._repetir_erros)
        self.btn_repetir.pack(side="left", padx=4)

    def _pre_requisitos(self):
        video = self.app.estado.get("video_path")
        transcricao = self.app.estado.get("transcricao")
        if not video or not transcricao:
            self.app.mostrar_aviso("Cortes em lote", "Importe e transcreva um vídeo primeiro.")
            return None, None
        if caminho_normalizado(transcricao.video_path) != caminho_normalizado(video):
            self.app.atualizar_estado("transcricao", None)
            self.app.mostrar_erro(
                "Cortes em lote",
                "A transcrição carregada pertence a outro episódio. Transcreva o vídeo atual.",
            )
            return None, None
        return video, transcricao

    def _restaurar_lista(self):
        video = self.app.estado.get("video_path")
        if video:
            try:
                self._listas_modo["normal"] = carregar_lista(video)
            except (OSError, ValueError, TypeError):
                self._listas_modo["normal"] = []
            try:
                permitidos = Corte.__dataclass_fields__
                self._listas_modo["remix"] = [
                    Corte(**{k: v for k, v in item.items() if k in permitidos})
                    for item in carregar_lista_remix(video)
                ]
            except (OSError, ValueError, TypeError):
                self._listas_modo["remix"] = []
        self.cortes = self._listas_modo["normal"]
        transcricao = self.app.estado.get("transcricao")
        if transcricao:
            for cortes in self._listas_modo.values():
                for corte in cortes:
                    if corte.modo_edicao != "remix":
                        continue
                    try:
                        plano = self._plano_titulo_corte(corte)
                        reavaliar_existentes([plano],transcricao)
                        corte.titulos_v2 = plano.titulos_v2
                        corte.opcoes_titulo = [t.texto for t in plano.titulos]
                        corte.titulo = titulo_exibicao(plano)
                        corte.status = plano.status
                        corte.selecionado, corte.erro = plano.selecionado, plano.erro
                    except (ValueError,TypeError):
                        corte.status = "revisão"
            intervalo = carregar_intro(transcricao)
            if intervalo is None:
                intervalo = detectar_intro(transcricao)
                if intervalo:
                    salvar_intro(transcricao, intervalo)
            self._aplicar_intro_na_tela(intervalo)
            for corte in self.cortes:
                self._preparar_versao_b(corte, transcricao)
        self._redesenhar_lista()

    def _trocar_modo(self, valor: str):
        novo = "remix" if valor == "Remix narrativo" else "normal"
        if novo == self._modo_atual:
            return
        try:
            if self._linhas:
                self._sincronizar_linhas()
        except (ValueError, TypeError):
            pass
        self._listas_modo[self._modo_atual] = self.cortes
        self._modo_atual = novo
        self.cortes = self._listas_modo[novo]
        self.btn_ia.configure(text=("Criar remixes com IA" if novo == "remix"
                                    else "Analisar episódio com IA"))
        self.lbl_status.configure(text=(
            "Remix Narrativo ativo. O corte normal permanece preservado."
            if novo == "remix" else "Modo cronológico normal ativo."
        ))
        self._redesenhar_lista()

    def _aplicar_intro_na_tela(self, intervalo):
        self.entry_intro_inicio.delete(0, "end")
        self.entry_intro_fim.delete(0, "end")
        if not intervalo:
            self.var_excluir_intro.set(False)
            self.lbl_intro.configure(text="Nenhuma intro detectada", text_color=COR_CINZA_ESCURO)
            self.app.atualizar_estado("intro_intervalo", None)
            return
        self.entry_intro_inicio.insert(0, segundos_para_hhmmss(intervalo.inicio))
        self.entry_intro_fim.insert(0, segundos_para_hhmmss(intervalo.fim))
        self.var_excluir_intro.set(intervalo.ativo)
        origem = "automática" if intervalo.automatico else "manual"
        self.lbl_intro.configure(text=f"{origem} ({intervalo.confianca:.0%})", text_color=COR_VERDE)
        self.app.atualizar_estado("intro_intervalo", intervalo)

    def _detectar_intro(self):
        _video, transcricao = self._pre_requisitos()
        if not transcricao:
            return
        intervalo = detectar_intro(transcricao)
        self._aplicar_intro_na_tela(intervalo)
        if intervalo:
            salvar_intro(transcricao, intervalo)

    def _ler_intro(self):
        transcricao = self.app.estado.get("transcricao")
        if not transcricao or not self.entry_intro_inicio.get().strip() or not self.entry_intro_fim.get().strip():
            return None
        intervalo = IntervaloIntro(
            hhmmss_para_segundos(self.entry_intro_inicio.get()),
            hhmmss_para_segundos(self.entry_intro_fim.get()),
            bool(self.var_excluir_intro.get()), False, 1.0, "Intervalo revisado manualmente",
        ).validar(transcricao.duracao)
        self.app.atualizar_estado("intro_intervalo", intervalo)
        return intervalo

    def _salvar_intro(self):
        transcricao = self.app.estado.get("transcricao")
        if not transcricao:
            self.app.mostrar_aviso("Intro", "Transcreva o vídeo primeiro.")
            return
        try:
            intervalo = self._ler_intro()
            if not intervalo:
                raise ValueError("Informe o início e o fim da intro.")
            salvar_intro(transcricao, intervalo)
            self._aplicar_intro_na_tela(intervalo)
            self.lbl_status.configure(text="Intervalo da intro salvo.")
        except (ValueError, OSError) as erro:
            self.app.mostrar_erro("Intro", str(erro))

    @staticmethod
    def _preparar_versao_b(corte, transcricao):
        if corte.gancho_b_inicio is not None:
            return
        trecho, motivo = identificar_gancho_b(corte, transcricao)
        corte.gancho_b_motivo = motivo
        if trecho:
            corte.gancho_b_inicio, corte.gancho_b_fim = trecho.inicio, trecho.fim

    def _sugerir(self):
        if self._modo_atual == "remix":
            self.app.mostrar_info(
                "Remix Narrativo",
                "Use 'Criar remixes com IA'. As sugestões cronológicas continuam disponíveis em Corte normal.",
            )
            return
        video, transcricao = self._pre_requisitos()
        if not video:
            return
        try:
            maximo = self._ler_maximo_cortes()
            self.cortes = sugerir_cortes(transcricao, max_cortes=maximo)
            self._salvar_lista(silencioso=True)
            self._redesenhar_lista()
            self.lbl_status.configure(
                text=f"{len(self.cortes)} sugestões criadas por limites de fala e pausas. "
                     "Não há análise de humor nesta fase."
            )
        except ValueError as erro:
            self.app.mostrar_erro("Sugestões", str(erro))

    def _adicionar_manual(self):
        if self._modo_atual == "remix":
            self.app.mostrar_info(
                "Remix Narrativo",
                "Remixes são criados pela análise de continuidade; edite os segmentos de um candidato existente.",
            )
            return
        info = self.app.estado.get("video_info")
        if not info:
            self.app.mostrar_aviso("Corte manual", "Importe um vídeo primeiro.")
            return
        inicio = self.cortes[-1].fim if self.cortes else 0.0
        fim = min(info.duracao, inicio + 90.0)
        if fim <= inicio:
            inicio, fim = 0.0, min(info.duracao, 90.0)
        self.cortes.append(Corte(inicio, fim, f"CORTE {len(self.cortes) + 1}"))
        self._redesenhar_lista()

    def _analisar_ia(self):
        video, transcricao = self._pre_requisitos()
        if not transcricao:
            return
        if fila_analise_ia.ocupada or self.app.estado.get('comparacao_ocupada'):
            self.app.mostrar_aviso("Análise inteligente", "Já existe uma análise em andamento.")
            return
        try:
            config = carregar_config_ia()
            config.max_cortes = self._ler_maximo_cortes()
            config.validar()
        except (ValueError, TypeError) as erro:
            self.app.mostrar_erro("Análise inteligente", str(erro))
            return
        self.btn_ia.configure(state="disabled")
        self.btn_cancelar.configure(state="normal")
        self.barra_individual.set(0)
        remix = self._modo_atual == "remix"
        self.lbl_status.configure(text=("Preparando análise de Remix Narrativo…"
                                        if remix else "Preparando análise contextual…"))
        sessao = int(self.app.estado.get("video_session", 0))
        origem = caminho_normalizado(video)
        self._tarefa_atual = TarefaRenderizacao(
            nome="Remix Narrativo" if remix else "Análise inteligente",
            funcao=executar_analise_com_titulos,
            args=(transcricao, config),
            kwargs={'analise': analisar_remixes if remix else analisar_transcricao,
                    'timeline_modo':'lote',
                    'provedor': provedor_remix_orcado(self.app.estado, transcricao, config) if remix else
                               provider_orcado(self.app.estado, transcricao, config,modo='agencia',schema=_RespostaAgencia),
                    'orcamento': orcamento_episodio(self.app.estado, transcricao, config.max_chamadas_episodio)},
            callback_progresso=lambda pct, msg: self._eventos.put(("ia_progresso", sessao, origem, pct, msg)),
            callback_conclusao=lambda candidatos: self._eventos.put(
                ("remix_fim" if remix else "ia_fim", sessao, origem, candidatos)),
            callback_erro=lambda erro: self._eventos.put(("ia_erro", sessao, origem, erro)),
        )
        fila_analise_ia.adicionar(self._tarefa_atual)

    def _ler_maximo_cortes(self) -> int:
        texto = self.entry_max.get().strip()
        if not texto.isdecimal():
            raise ValueError("Máximo de cortes deve ser um número inteiro entre 1 e 10, sem letras ou símbolos.")
        valor = int(texto)
        if not 1 <= valor <= 10:
            raise ValueError("Máximo de cortes deve ficar entre 1 e 10.")
        return valor

    @staticmethod
    def _candidato_remix(corte: Corte) -> CandidatoRemix:
        return CandidatoRemix(
            titulos=list(corte.opcoes_titulo or [corte.titulo]),
            descricao=corte.remix_descricao or corte.descricao,
            gancho_inicio=float(corte.remix_gancho_inicio),
            gancho_fim=float(corte.remix_gancho_fim),
            gancho_motivo=corte.remix_gancho_motivo,
            segmentos=[SegmentoNarrativo(**item) for item in corte.remix_segmentos],
            justificativa=corte.remix_justificativa,
            continuidade=corte.remix_continuidade,
            confianca=float(corte.remix_confianca),
        )

    def _timeline_remix_corte(self, corte: Corte):
        return timeline_remix(self._candidato_remix(corte), corte.remix_repetir_gancho)

    @staticmethod
    def _texto_segmentos(corte: Corte) -> str:
        return "; ".join(
            f"{float(s['inicio']):.3f}-{float(s['fim']):.3f}|{s.get('funcao', 'desenvolvimento')}"
            for s in corte.remix_segmentos
        )

    @staticmethod
    def _parse_intervalo(texto: str) -> tuple[float, float]:
        partes = texto.strip().split("-")
        if len(partes) != 2:
            raise ValueError("Use o formato início-fim em segundos, por exemplo 523.200-527.500.")
        inicio, fim = float(partes[0]), float(partes[1])
        if fim <= inicio:
            raise ValueError("O fim precisa ser maior que o início.")
        return inicio, fim

    @classmethod
    def _parse_segmentos(cls, texto: str) -> list[dict]:
        resultado = []
        for parte in texto.split(";"):
            intervalo, separador, funcao = parte.strip().partition("|")
            inicio, fim = cls._parse_intervalo(intervalo)
            resultado.append({"inicio": inicio, "fim": fim,
                              "funcao": funcao.strip() if separador else "desenvolvimento"})
        if not 2 <= len(resultado) <= 5:
            raise ValueError("O remix deve ter entre 2 e 5 segmentos narrativos.")
        return resultado

    def _redesenhar_lista(self):
        for filho in self.lista.winfo_children():
            filho.destroy()
        self._linhas = {}
        self._remix_widgets = {}
        for indice, corte in enumerate(self.cortes, start=1):
            frame = ctk.CTkFrame(self.lista, fg_color="#211536")
            frame.grid(row=indice - 1, column=0, sticky="ew", padx=5, pady=4)
            frame.grid_columnconfigure(4, weight=1)
            var = ctk.BooleanVar(value=corte.selecionado)
            ctk.CTkCheckBox(frame, text=str(indice), width=45, variable=var).grid(row=0, column=0, padx=6)
            ent_i = ctk.CTkEntry(frame, width=105)
            ent_i.insert(0, segundos_para_hhmmss(corte.inicio))
            ent_i.grid(row=0, column=1, padx=3, pady=7)
            ent_f = ctk.CTkEntry(frame, width=105)
            ent_f.insert(0, segundos_para_hhmmss(corte.fim))
            ent_f.grid(row=0, column=2, padx=3)
            duracao_exibida = corte.duracao
            if self._modo_atual == "remix":
                try:
                    duracao_exibida = duracao_timeline(
                        self._candidato_remix(corte), corte.remix_repetir_gancho)
                except (TypeError, ValueError):
                    pass
            dur = ctk.CTkLabel(frame, text=f"{duracao_exibida:.1f}s", width=60,
                               text_color=COR_CINZA_CLARO)
            dur.grid(row=0, column=3, padx=3)
            titulo = ctk.CTkEntry(frame)
            titulo.insert(0, titulo_exibicao(corte))
            titulo.grid(row=0, column=4, padx=5, sticky="ew")
            status = ctk.CTkLabel(frame, text=corte.status, width=85, text_color=COR_CINZA_ESCURO)
            status.grid(row=0, column=5, padx=4)
            ctk.CTkButton(frame, text="Excluir", width=65, fg_color=COR_VERMELHO,
                          command=lambda cid=corte.id: self._excluir(cid)).grid(row=0, column=6, padx=6)
            if corte.opcoes_titulo:
                scores = {t['texto']:t['score_titulo'] for t in corte.titulos_v2.get('top',[])}
                rotulos = [f"[Título {i} · score {scores.get(t,'pendente')}] {t}"
                           for i,t in enumerate(corte.opcoes_titulo,1)]
                escolhas = ctk.CTkOptionMenu(
                    frame, values=rotulos, width=230,
                    command=lambda valor, entrada=titulo: (entrada.delete(0, "end"), entrada.insert(0, valor.split('] ',1)[1])),
                )
                n = len(corte.opcoes_titulo)
                escolhas.set(f"Alternativas: {n} aprovada{'s' if n != 1 else ''}")
                escolhas.grid(row=1, column=1, columnspan=2, padx=5, pady=(0, 7), sticky="w")
                detalhe = corte.descricao
                if corte.titulos_v2.get('alertas'):
                    detalhe += "\nTítulo V2: " + "; ".join(corte.titulos_v2['alertas'])
                if corte.gancho_ia:
                    detalhe = f"Gancho: {corte.gancho_ia}\n{detalhe}"
                ctk.CTkLabel(frame, text=detalhe, anchor="w", justify="left",
                             text_color=COR_CINZA_CLARO).grid(
                    row=1, column=3, columnspan=4, padx=5, pady=(0, 7), sticky="ew")
            if corte.titulos_v2:
                from ui.titulos_diagnostico import mostrar_titulos_rejeitados
                ctk.CTkButton(frame, text="Ver títulos rejeitados", width=150, fg_color="#3E2661",
                    command=lambda c=corte: mostrar_titulos_rejeitados(self,c.titulos_v2)).grid(
                        row=6, column=1, columnspan=2, padx=5, pady=(0, 7), sticky="w")
            if self._modo_atual == "remix":
                detalhe = (
                    f"História: {corte.remix_descricao or corte.descricao}\n"
                    f"Gancho: {corte.remix_gancho_motivo}\n"
                    f"Montagem: {corte.remix_justificativa}\n"
                    f"Continuidade: {corte.remix_continuidade} · confiança {corte.remix_confianca:.0%}"
                )
                ctk.CTkLabel(frame, text=detalhe, anchor="w", justify="left",
                             text_color=COR_CINZA_CLARO).grid(
                    row=2, column=1, columnspan=6, padx=5, pady=(0, 6), sticky="ew")
                ctk.CTkLabel(frame, text="Gancho (s):").grid(row=3, column=1, padx=4, sticky="e")
                ent_gancho = ctk.CTkEntry(frame, width=175)
                ent_gancho.insert(0, f"{corte.remix_gancho_inicio:.3f}-{corte.remix_gancho_fim:.3f}")
                ent_gancho.grid(row=3, column=2, padx=4, sticky="w")
                ctk.CTkLabel(frame, text="Segmentos (s):").grid(row=4, column=1, padx=4, sticky="e")
                ent_segmentos = ctk.CTkEntry(frame)
                ent_segmentos.insert(0, self._texto_segmentos(corte))
                ent_segmentos.grid(row=4, column=2, columnspan=4, padx=4, pady=5, sticky="ew")
                repetir = ctk.BooleanVar(value=False)
                ctk.CTkCheckBox(frame, text="Repetir gancho ao reencontrá-lo",
                                variable=repetir, state="disabled").grid(row=5, column=2, columnspan=2,
                                                       padx=4, pady=(0, 7), sticky="w")
                ctk.CTkButton(frame, text="Prévia remix", width=105, fg_color="#3E2661",
                              command=lambda cid=corte.id: self._visualizar(cid, "REMIX")).grid(
                    row=5, column=4, padx=5, pady=(0, 7), sticky="w")
                var_remix = ctk.StringVar(value="REMIX")
                self._linhas[corte.id] = (var, ent_i, ent_f, titulo, dur, status, var_remix)
                self._remix_widgets[corte.id] = (ent_gancho, ent_segmentos, repetir)
                continue

            opcoes_ab = ["A — cronológica"]
            if corte.gancho_b_inicio is not None:
                opcoes_ab += ["B — gancho antecipado", "A e B"]
            mapa_modo = {"A": opcoes_ab[0], "B": "B — gancho antecipado", "AMBAS": "A e B"}
            var_ab = ctk.StringVar(value=mapa_modo.get(corte.modo_ab, opcoes_ab[0]))
            ctk.CTkOptionMenu(frame, values=opcoes_ab, variable=var_ab, width=190).grid(
                row=2, column=1, columnspan=2, padx=5, pady=(0, 7), sticky="w")
            texto_b = corte.gancho_b_motivo or "Versão B indisponível para este corte."
            ctk.CTkLabel(frame, text=texto_b, anchor="w", justify="left",
                         text_color=COR_CINZA_ESCURO).grid(
                row=2, column=3, columnspan=4, padx=5, pady=(0, 7), sticky="ew")
            ctk.CTkButton(frame, text="Prévia A", width=75, fg_color="#3E2661",
                          command=lambda cid=corte.id: self._visualizar(cid, "A")).grid(
                row=3, column=1, padx=5, pady=(0, 7), sticky="w")
            if corte.gancho_b_inicio is not None:
                ctk.CTkButton(frame, text="Prévia B", width=75, fg_color="#3E2661",
                              command=lambda cid=corte.id: self._visualizar(cid, "B")).grid(
                    row=3, column=2, padx=5, pady=(0, 7), sticky="w")
            self._linhas[corte.id] = (var, ent_i, ent_f, titulo, dur, status, var_ab)

    def _visualizar(self, corte_id, versao):
        video, transcricao = self._pre_requisitos()
        if not video:
            return
        if fila_render.ocupada:
            self.app.mostrar_aviso("Prévia A/B", "Aguarde a tarefa de vídeo atual terminar.")
            return
        try:
            self._sincronizar_linhas()
            palavras, tamanho, y = self._config_legendas()
            corte = next(c for c in self.cortes if c.id == corte_id)
            intervalo_intro = self._ler_intro()
            if versao == "REMIX":
                trechos_base = self._timeline_remix_corte(corte)
            else:
                trechos_base = segmentos_da_versao(corte, versao)
            trechos_originais = remover_intro(trechos_base, intervalo_intro)
            if not trechos_originais:
                raise ValueError("A versão selecionada está inteiramente dentro da intro excluída.")
            restantes, trechos = 8.0, []
            for trecho in trechos_originais:
                if restantes <= 0:
                    break
                fim = min(trecho.fim, trecho.inicio + restantes)
                trechos.append(type(trecho)(trecho.inicio, fim))
                restantes -= fim - trecho.inicio
            blocos = gerar_blocos_legenda(transcricao, palavras)
            legendas = remapear_legendas(blocos, trechos)
            pasta = Path(TEMP_DIR) / "previews_ab"
            pasta.mkdir(parents=True, exist_ok=True)
            saida = pasta / f"preview_{corte.id}_{versao}.mp4"
        except (ValueError, StopIteration) as erro:
            self.app.mostrar_erro("Prévia A/B", str(erro))
            return
        self.lbl_status.configure(text=f"Gerando prévia {versao} de até 8 segundos…")
        self._tarefa_atual = TarefaRenderizacao(
            nome=f"Prévia {versao}", funcao=renderizar_corte,
            kwargs={"caminho_video": video, "inicio": trechos[-1].inicio, "fim": trechos[-1].fim,
                    "titulo": corte.titulo, "caminho_saida": str(saida), "fps": 30,
                    "legendas": legendas, "legenda_tamanho": tamanho, "legenda_y": y,
                    "ajustes_av": self.app.estado.get("ajustes_av"), "segmentos_fonte": trechos,
                    "perfil_visual": self.app.estado.get("perfil_visual_id")},
            callback_progresso=lambda pct, msg: self._eventos.put(("preview_progresso", pct, msg)),
            callback_conclusao=lambda res, path=str(saida): self._eventos.put(("preview_fim", res, path)),
            callback_erro=lambda erro: self._eventos.put(("preview_erro", erro)),
        )
        fila_render.adicionar(self._tarefa_atual)

    def _sincronizar_linhas(self):
        info = self.app.estado.get("video_info")
        if not info:
            raise ValueError("Vídeo não carregado.")
        for corte in self.cortes:
            var, ent_i, ent_f, titulo, dur, _status, var_ab = self._linhas[corte.id]
            inicio = hhmmss_para_segundos(ent_i.get())
            fim = hhmmss_para_segundos(ent_f.get())
            inicio, fim = normalizar_tempos(inicio, fim, info.duracao)
            corte.inicio, corte.fim = inicio, fim
            corte.titulo = titulo.get().strip().upper() or "CORTE"
            corte.selecionado = bool(var.get())
            if self._modo_atual == "remix":
                ent_gancho, ent_segmentos, repetir = self._remix_widgets[corte.id]
                gi, gf = self._parse_intervalo(ent_gancho.get())
                if gi < 0 or gf > info.duracao:
                    raise ValueError("O gancho do remix está fora da duração do episódio.")
                corte.remix_gancho_inicio, corte.remix_gancho_fim = gi, gf
                corte.remix_segmentos = self._parse_segmentos(ent_segmentos.get())
                if any(s["inicio"] < 0 or s["fim"] > info.duracao
                       for s in corte.remix_segmentos):
                    raise ValueError("Há segmento do remix fora da duração do episódio.")
                if any(atual["inicio"] < anterior["fim"]
                       for anterior, atual in zip(corte.remix_segmentos,
                                                  corte.remix_segmentos[1:])):
                    raise ValueError("Os segmentos do remix estão sobrepostos ou fora de ordem.")
                if not any(min(gf, s["fim"]) - max(gi, s["inicio"]) > 0.05
                           for s in corte.remix_segmentos):
                    raise ValueError("A sequência editada não alcança o gancho.")
                corte.remix_repetir_gancho = bool(repetir.get())
                corte.inicio = min(s["inicio"] for s in corte.remix_segmentos)
                corte.fim = max(s["fim"] for s in corte.remix_segmentos)
                dur.configure(text=f"{sum(t.duracao for t in self._timeline_remix_corte(corte)):.1f}s")
            else:
                corte.modo_ab = {"A — cronológica": "A", "B — gancho antecipado": "B",
                                 "A e B": "AMBAS"}.get(var_ab.get(), "A")
                dur.configure(text=f"{corte.duracao:.1f}s")
        return self.cortes

    def _excluir(self, corte_id):
        self.cortes = [c for c in self.cortes if c.id != corte_id]
        self._redesenhar_lista()

    def _salvar_lista(self, silencioso=False):
        video = self.app.estado.get("video_path")
        if not video:
            if not silencioso:
                self.app.mostrar_aviso("Lista", "Importe um vídeo primeiro.")
            return
        try:
            if self._linhas:
                self._sincronizar_linhas()
            self._listas_modo[self._modo_atual] = self.cortes
            if self._modo_atual == "remix":
                caminho = salvar_lista_remix(video, [c.como_dict() for c in self.cortes])
            else:
                caminho = salvar_lista(video, self.cortes)
            if not silencioso:
                self.lbl_status.configure(text=f"Lista salva em {caminho.name}.")
        except (ValueError, OSError) as erro:
            if not silencioso:
                self.app.mostrar_erro("Lista", str(erro))

    def _config_legendas(self):
        palavras = int(self.entry_palavras.get())
        tamanho = int(self.entry_tamanho.get())
        y = int(self.entry_y.get())
        if not 1 <= palavras <= 12:
            raise ValueError("Palavras por bloco deve estar entre 1 e 12.")
        if not 24 <= tamanho <= 100:
            raise ValueError("O tamanho da legenda deve estar entre 24 e 100.")
        if not 1500 <= y <= 1700:
            raise ValueError("A posição Y deve ficar entre 1500 e 1700, abaixo da animação e acima da logo.")
        return palavras, tamanho, y

    def _gerar_todos(self, apenas_erros=False):
        video, transcricao = self._pre_requisitos()
        if not video:
            return
        if fila_render.ocupada:
            self.app.mostrar_aviso("Exportação", "Já existe uma renderização em andamento.")
            return
        try:
            self._sincronizar_linhas()
            palavras, self._legenda_tamanho, self._legenda_y = self._config_legendas()
            self._intro_exportacao = self._ler_intro()
        except (ValueError, TypeError) as erro:
            self.app.mostrar_erro("Exportação", str(erro))
            return
        selecionados = [c for c in self.cortes if c.selecionado]
        if not selecionados:
            self.app.mostrar_aviso("Exportação", "Nenhum corte selecionado para gerar.")
            return
        self._blocos = gerar_blocos_legenda(transcricao, palavras)
        self._sessao_lote = int(self.app.estado.get("video_session", 0))
        self._video_lote = caminho_normalizado(video)
        self._ajustes_lote = self.app.estado.get("ajustes_av")
        self._pasta_lote = self.app.estado.get("pasta_export") or str(Path(__file__).parents[1] / "exportados")
        self._pendentes = []
        for corte in selecionados:
            if self._modo_atual == "remix" or corte.modo_edicao == "remix":
                versoes = ["REMIX"]
            else:
                versoes = ["A", "B"] if corte.modo_ab == "AMBAS" else [corte.modo_ab]
            for versao in versoes:
                if versao == "B" and corte.gancho_b_inicio is None:
                    continue
                if not apenas_erros or corte.status_versoes.get(versao) == "erro":
                    self._pendentes.append((corte, versao))
        if not self._pendentes:
            self.app.mostrar_aviso("Exportação", "Nenhuma versão selecionada para gerar.")
            return
        self._total_lote = len(self._pendentes)
        self._concluidos = 0
        self._cancelado = False
        self.btn_gerar.configure(state="disabled")
        self.btn_cancelar.configure(state="normal")
        self.barra_geral.set(0)
        self._iniciar_proximo()

    def _iniciar_proximo(self):
        if hasattr(self, "_sessao_lote") and not sessao_corresponde(
            self.app.estado, self._sessao_lote, self._video_lote
        ):
            self._cancelado = True
            self._pendentes.clear()
        if self._cancelado or not self._pendentes:
            self._finalizar_lote()
            return
        corte, versao = self._pendentes.pop(0)
        corte.status, corte.erro = f"renderizando {versao}", ""
        corte.status_versoes[versao] = "renderizando"
        self._atualizar_status_corte(corte)
        pasta = self._pasta_lote
        perfil = obter_perfil(self.app.estado.get("perfil_visual_id"))
        nome = f"{Path(self._video_lote).stem}_{corte.titulo}_{perfil.nome_canal.replace(' ', '_')}_{versao}"
        adicionais = (".srt",) if self.var_srt.get() else ()
        saida = caminho_sem_sobrescrever(pasta, nome, ".mp4", adicionais)
        if versao == "REMIX" or corte.modo_edicao == "remix":
            trechos_base = self._timeline_remix_corte(corte)
        else:
            trechos_base = segmentos_da_versao(corte, versao)
        trechos = remover_intro(trechos_base, self._intro_exportacao)
        if not trechos:
            corte.status = "erro"
            corte.erro = "O corte está inteiramente dentro da intro excluída."
            corte.status_versoes[versao] = "erro"
            self._concluidos += 1
            self._atualizar_status_corte(corte)
            self._iniciar_proximo()
            return
        legendas = remapear_legendas(self._blocos, trechos)
        try:
            validar_exportacao_automatica(corte, self.app.estado.get("transcricao"), trechos,
                                         legendas, self._intro_exportacao)
            if corte.origem != "manual":
                self._validar_titulo_corte(corte,trechos)
        except ValueError as erro:
            if corte.status == STATUS_REVISAO_TITULO or str(erro) == MOTIVO_REVISAO_TITULO:
                corte.status, corte.erro, corte.selecionado = STATUS_REVISAO_TITULO, MOTIVO_REVISAO_TITULO, False
            else:
                corte.status, corte.erro = "erro", str(erro)
            corte.status_versoes[versao] = "erro"
            self._concluidos += 1
            self._atualizar_status_corte(corte)
            self._iniciar_proximo()
            return
        if self.var_srt.get():
            salvar_srt(legendas, saida.with_suffix(".srt"))
        self._tarefa_atual = TarefaRenderizacao(
            nome=f"Corte {corte.id}", funcao=renderizar_corte,
            kwargs={
                "caminho_video": self._video_lote, "inicio": corte.inicio,
                "fim": corte.fim, "titulo": corte.titulo, "caminho_saida": str(saida),
                "fps": 60, "legendas": legendas, "legenda_tamanho": self._legenda_tamanho,
                "legenda_y": self._legenda_y,
                "ajustes_av": self._ajustes_lote,
                "segmentos_fonte": trechos if versao in ("B", "REMIX") or corte.modo_edicao == "remix" else None,
                "perfil_visual": perfil.id,
            },
            callback_progresso=lambda pct, msg, cid=corte.id, v=versao: self._eventos.put(("progresso", cid, pct, msg, v)),
            callback_conclusao=lambda res, cid=corte.id, path=str(saida), v=versao: self._eventos.put(("fim", cid, res, path, v)),
            callback_erro=lambda erro, cid=corte.id, v=versao: self._eventos.put(("erro", cid, erro, v)),
        )
        fila_render.adicionar(self._tarefa_atual)

    def _processar_eventos(self):
        try:
            while True:
                evento = self._eventos.get_nowait()
                if evento[0] == "ia_progresso":
                    if not sessao_corresponde(self.app.estado, evento[1], evento[2]):
                        continue
                    self.barra_individual.set(evento[3] / 100)
                    self.lbl_status.configure(text=evento[4])
                    continue
                if evento[0] == "preview_progresso":
                    self.barra_individual.set(evento[1] / 100)
                    self.lbl_status.configure(text=evento[2])
                    continue
                if evento[0] == "preview_fim":
                    self._tarefa_atual = None
                    if evento[1].get("sucesso"):
                        self.lbl_status.configure(text="Prévia concluída; abrindo no player do Windows.")
                        os.startfile(evento[2])
                    else:
                        self.lbl_status.configure(text=f"Falha na prévia: {evento[1].get('erro', '')}")
                    continue
                if evento[0] == "preview_erro":
                    self._tarefa_atual = None
                    self.lbl_status.configure(text=f"Falha na prévia: {evento[1]}")
                    continue
                if evento[0] == "remix_fim":
                    if not sessao_corresponde(self.app.estado, evento[1], evento[2]):
                        continue
                    candidatos = evento[3]
                    transcricao = self.app.estado.get("transcricao")
                    remixes = []
                    for candidato in candidatos:
                        remixes.append(Corte(
                            candidato.inicio, candidato.fim, candidato.titulos[0],
                            descricao=candidato.descricao,
                            opcoes_titulo=candidato.titulos,
                            modo_edicao="remix",
                            remix_descricao=candidato.descricao,
                            remix_gancho_inicio=candidato.gancho_inicio,
                            remix_gancho_fim=candidato.gancho_fim,
                            remix_gancho_motivo=candidato.gancho_motivo,
                            remix_segmentos=[{
                                "inicio": s.inicio, "fim": s.fim, "funcao": s.funcao
                            } for s in candidato.segmentos],
                            remix_justificativa=candidato.justificativa,
                            remix_continuidade=candidato.continuidade,
                            remix_confianca=candidato.confianca,
                            remix_repetir_gancho=False,
                            origem="remix", video_id=transcricao.video_id,
                            remix_rules_version=candidato.remix_rules_version,
                            validacao_remix=candidato.validacao_remix,
                            titulos_v2=candidato.titulos_v2,
                            status=STATUS_REVISAO_TITULO if candidato.titulos_v2 and not candidato.titulos_v2.get('top') else "pendente",
                            selecionado=not (candidato.titulos_v2 and not candidato.titulos_v2.get('top')),
                        ))
                    self._listas_modo["remix"] = remixes
                    if self._modo_atual == "remix":
                        self.cortes = remixes
                        self._redesenhar_lista()
                        self._salvar_lista(silencioso=True)
                    self.btn_ia.configure(state="normal")
                    self.btn_cancelar.configure(state="disabled")
                    self._tarefa_atual = None
                    self.barra_individual.set(1)
                    if remixes:
                        self.lbl_status.configure(
                            text=f"{len(remixes)} remixes narrativos prontos para revisão."
                        )
                    else:
                        self.lbl_status.configure(
                            text=(getattr(candidatos, "diagnostico", None).resumo()
                                  if getattr(candidatos, "diagnostico", None) else
                                  "Nenhum remix narrativo válido foi encontrado neste episódio.")
                        )
                    continue
                if evento[0] == "ia_fim":
                    if not sessao_corresponde(self.app.estado, evento[1], evento[2]):
                        continue
                    candidatos = evento[3]
                    diagnostico = getattr(candidatos, "diagnostico", None)
                    transcricao = self.app.estado.get("transcricao")
                    self.cortes = [Corte(c.inicio, c.fim, c.titulos[0], descricao=c.descricao,
                                         motivo_ia=c.motivo, opcoes_titulo=c.titulos,
                                         inicio_original_ia=c.inicio_original, gancho_ia=c.gancho, origem="analise_ia")
                                   for c in candidatos]
                    self.cortes = [corte_de_plano(PlanoAgencia.de_dict(c.plano_remix)) for c in candidatos]
                    self._listas_modo["remix"] = self.cortes
                    self._modo_atual = "remix"
                    self.var_modo_edicao.set("Remix narrativo")
                    self.btn_ia.configure(text="Criar remixes com IA")
                    if transcricao:
                        for corte in self.cortes:
                            self._preparar_versao_b(corte, transcricao)
                    self._redesenhar_lista()
                    self._salvar_lista(silencioso=True)
                    self.btn_ia.configure(state="normal")
                    self.btn_cancelar.configure(state="disabled")
                    self._tarefa_atual = None
                    self.barra_individual.set(1)
                    if diagnostico:
                        if getattr(diagnostico, "remix", None):
                            self.lbl_status.configure(text=diagnostico.remix.resumo())
                            continue
                        contexto = ""
                        if diagnostico.janelas_total:
                            contexto = (f" Contexto: {diagnostico.janelas_analisadas}/"
                                        f"{diagnostico.janelas_total} janelas.")
                        explicacao = ""
                        if diagnostico.motivos_rejeicao:
                            explicacao = f" Primeira rejeição: {diagnostico.motivos_rejeicao[0]}"
                        reaproveitados = ""
                        if diagnostico.reutilizada_cache_anterior:
                            reaproveitados = (f" Reaproveitados de análises anteriores: "
                                             f"{diagnostico.reutilizada_cache_anterior}.")
                        self.lbl_status.configure(
                            text=(f"Solicitados até {diagnostico.solicitada}; IA retornou "
                                  f"{diagnostico.retornada_ia}; rejeitados {diagnostico.rejeitada}; "
                                  f"exibidos {diagnostico.final_exibida}.{reaproveitados}"
                                  f"{contexto}{explicacao}")
                        )
                    else:
                        self.lbl_status.configure(text=f"IA selecionou {len(self.cortes)} cenas para revisão.")
                    continue
                if evento[0] == "ia_erro":
                    if not sessao_corresponde(self.app.estado, evento[1], evento[2]):
                        continue
                    self.btn_ia.configure(state="normal")
                    self.btn_cancelar.configure(state="disabled")
                    self._tarefa_atual = None
                    self.lbl_status.configure(text=f"Análise indisponível: {evento[3]} Modo manual preservado.")
                    continue
                corte = next((c for c in self.cortes if c.id == evento[1]), None)
                if not corte:
                    continue
                if evento[0] == "progresso":
                    pct = evento[2]
                    self.barra_individual.set(pct / 100)
                    self.barra_geral.set((self._concluidos + pct / 100) / self._total_lote)
                    self.lbl_status.configure(text=f"{corte.titulo}: {evento[3]}")
                elif evento[0] == "fim":
                    resultado = evento[2]
                    versao = evento[4]
                    corte.status = "concluído" if resultado.get("sucesso") else "erro"
                    corte.status_versoes[versao] = corte.status
                    corte.erro = resultado.get("erro", "")
                    corte.caminho_saida = evento[3] if resultado.get("sucesso") else ""
                    if resultado.get("sucesso") and corte.origem != "manual":
                        contexto,_ = self._validar_titulo_corte(corte)
                        registrar_uso(corte.titulo,contexto)
                    self._concluidos += 1
                    self._atualizar_status_corte(corte)
                    self._iniciar_proximo()
                elif evento[0] == "erro":
                    corte.status, corte.erro = "erro", evento[2]
                    corte.status_versoes[evento[3]] = "erro"
                    self._concluidos += 1
                    self._atualizar_status_corte(corte)
                    self._iniciar_proximo()
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(100, self._processar_eventos)

    def _plano_titulo_corte(self,corte):
        from modules.agencia.editorial import TituloOpcao
        plano = PlanoAgencia(corte.inicio,corte.fim,segmentos=corte.remix_segmentos,
                             gancho={'inicio':corte.remix_gancho_inicio,'fim':corte.remix_gancho_fim},
                             usar_remix=True,estrutura=corte.estrutura_remix,video_id=corte.video_id,
                             titulos=[TituloOpcao(t) for t in corte.opcoes_titulo or [corte.titulo]],
                             titulo_escolhido=corte.titulo,titulos_v2=corte.titulos_v2,
                             status=corte.status,erro=corte.erro,selecionado=corte.selecionado)
        return plano

    def _validar_titulo_corte(self,corte,trechos=None):
        from modules.agencia.timeline import TimelineAgencia,TrechoTimeline
        timeline = (TimelineAgencia([TrechoTimeline(t.inicio,t.fim,'gancho' if n == 0 else 'historia')
                                    for n,t in enumerate(trechos)],'remix',
                                   gancho=(corte.remix_gancho_inicio,corte.remix_gancho_fim)) if trechos else None)
        plano = self._plano_titulo_corte(corte)
        try:
            return validar_titulo_final(plano,self.app.estado.get('transcricao'),timeline)
        except ValueError:
            if plano.status == STATUS_REVISAO_TITULO:
                from modules.titulos_v2 import marcar_revisao_titulo
                marcar_revisao_titulo(corte)
            raise

    def _atualizar_status_corte(self, corte):
        linha = self._linhas.get(corte.id)
        if linha:
            cor = COR_VERDE if corte.status == "concluído" else COR_VERMELHO if corte.status == "erro" else COR_CINZA_CLARO
            linha[5].configure(text=corte.status, text_color=cor)

    def _cancelar(self):
        self._cancelado = True
        if self._tarefa_atual:
            self._tarefa_atual.cancelar()
        self._pendentes.clear()
        self.lbl_status.configure(text="Cancelamento solicitado…")

    def _repetir_erros(self):
        self._gerar_todos(apenas_erros=True)

    def _finalizar_lote(self):
        self.btn_gerar.configure(state="normal")
        self.btn_cancelar.configure(state="disabled")
        self.barra_geral.set(self._concluidos / self._total_lote if self._total_lote else 0)
        texto = "Lote cancelado." if self._cancelado else f"Lote finalizado: {self._concluidos}/{self._total_lote}."
        self.lbl_status.configure(text=texto)
        self._tarefa_atual = None
        self._salvar_lista(silencioso=True)
