"""MODO AGÊNCIA / NOVA CONTA — edição estruturada, independente do preset legado."""

import os
import json
from pathlib import Path
import queue
import tkinter as tk

import customtkinter as ctk
from PIL import Image

from modules.agencia.av import carregar_ajustes_agencia, salvar_ajustes_agencia
from modules.agencia.editorial import (
    MAX_HISTORIAS, MIN_HISTORIAS, ListaPlanos, PlanoAgencia, analisar_agencia,
    completar_com_remixes_salvos, eh_modelo_lite, _RespostaAgencia,
    caminho_cache_agencia, _SomenteCache,
)
from modules.agencia.remix import verificar_plano_remix
from modules.agencia.producao import (
    REVISAO, aprovar_corte, evidenciar, falas, manual_confirmado as corte_manual,
    marcar_revisao, problemas_tecnicos, recuperar_producao, revisavel,
)
from modules.agencia.timeline import construir_timeline
from modules.agencia.persistencia import carregar_lista_agencia, salvar_lista_agencia
from modules.agencia.pipeline import (
    exportar_corte_agencia, gerar_capas_candidatas, renderizar_previa_agencia,
)
from modules.agencia.timeline import OpcoesEdicao
from modules.config_ia import carregar_config_ia
from modules.ai.comparacao import consumir_resultado, provider_orcado, orcamento_episodio
from modules import titulos_v2 as V
from modules.titulos_v2 import (executar_analise_com_titulos, montar_contexto, registrar_uso,
                               titulo_exibicao, marcar_revisao_titulo)
from modules.agencia.titulos_manual import (
    aprovar_titulo, editar_titulo, reavaliar_salvos, selecionar_prontos,
    status_visual, validar_para_exportacao,
)
from modules.intro import carregar_intro
from modules.lote import caminho_sem_sobrescrever
from modules.perfis_visuais import listar_perfis, obter_perfil, perfil_por_nome
from modules.preset import TEMP_DIR
from modules.sessao import caminho_normalizado, sessao_corresponde
from modules.tasks import TarefaRenderizacao, fila_analise_ia, fila_render
from ui.app import (
    COR_AMARELO, COR_BRANCO, COR_CINZA_CLARO, COR_CINZA_ESCURO, COR_ROXO_CLARO, COR_VERDE,
    COR_VERMELHO,
)

ROTULO_TIPO = {"curiosidade": "A · curiosidade", "conflito": "B · conflito", "surpresa": "C · surpresa"}


def _remixes_salvos(transcricao, config, cancelada_ref=None, progresso_cb=None):
    """Somente remixes salvos e revalidados; cortes normais nunca entram no Modo Agência."""
    final, rejeicoes = completar_com_remixes_salvos([], transcricao, config, config.max_cortes)
    lista = ListaPlanos(final, rejeicoes, len(final) + len(rejeicoes))
    from modules.agencia.auto_recovery import auto_recuperar
    lista = auto_recuperar(lista, transcricao, config, sementes=final,
                          cancelada_ref=cancelada_ref,progresso_cb=progresso_cb)
    if progresso_cb:
        progresso_cb(100, f"Sem chamar a Gemini. {lista.mensagem}")
    return lista


class TelaAgencia(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.planos: list[PlanoAgencia] = []
        self._linhas = {}
        self._eventos = queue.Queue()
        self._pendentes = []
        self._tarefa_atual = None
        self._cancelado = False
        self._total = 0
        self._concluidos = 0
        self._rejeicoes: list[str] = []
        self._rejeitados_total = 0
        self._diagnostico_producao = None
        self._after_id = None
        self._traces_titulo: list[tuple[tk.StringVar, str]] = []
        self._construir()
        self._restaurar()
        self._receber_comparacao()
        self._after_id = self.after(100, self._processar_eventos)

    def _obter_esteira(self):
        from modules.esteira import esteira_global
        return self.app.estado.get("esteira_instancia") or esteira_global

    def _limpar_traces_titulo(self):
        for var_t, trace_id in getattr(self, "_traces_titulo", ()):
            try:
                var_t.trace_remove("write", trace_id)
            except Exception:
                pass
        self._traces_titulo = []

    def destroy(self):
        if getattr(self, "_after_id", None) is not None:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        self._limpar_traces_titulo()
        try:
            super().destroy()
        finally:
            if hasattr(self, "_linhas") and isinstance(self._linhas, dict):
                self._linhas.clear()
            if hasattr(self, "_cards") and isinstance(self._cards, dict):
                self._cards.clear()
            for attr in ("var_perfil", "var_dissolve", "var_pausas", "var_srt", "var_capa"):
                if hasattr(self, attr):
                    setattr(self, attr, None)

    def _receber_comparacao(self):
        resultado = consumir_resultado(self.app.estado, 'agencia')
        if resultado is None:
            return
        self.planos = self._somente_remix([PlanoAgencia.de_dict(p) for p in resultado['itens']],
                                         self.app.estado['transcricao'])
        self._rejeicoes = []
        try:
            reavaliar_salvos(self.planos,self.app.estado['transcricao'])
        except (ValueError,TypeError):
            for plano in self.planos:
                plano.status = "revisão"
        self._redesenhar()
        self.lbl_status.configure(text=f"Resultado de {resultado['provider'].title()} carregado para revisão.")

    # ─── Layout ──────────────────────────────────────────────────────────────

    def _construir(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        raiz = ctk.CTkFrame(self, fg_color="transparent")
        raiz.grid(row=0, column=0, padx=24, pady=18, sticky="nsew")
        raiz.grid_columnconfigure(0, weight=1)
        raiz.grid_rowconfigure(4, weight=1)

        cab = ctk.CTkFrame(raiz, fg_color="transparent")
        cab.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(cab, text="Modo Agência / Nova conta", font=ctk.CTkFont(size=24, weight="bold"),
                     text_color=COR_BRANCO).pack(side="left")
        ctk.CTkLabel(cab, text="GANCHO → CONTEXTO → DESENVOLVIMENTO → ESCALADA → PAYOFF",
                     text_color=COR_AMARELO).pack(side="left", padx=16)

        linha1 = ctk.CTkFrame(raiz, fg_color="#211536", corner_radius=10)
        linha1.grid(row=1, column=0, sticky="ew", pady=(10, 6), ipady=6)
        ctk.CTkLabel(linha1, text="Perfil visual:").pack(side="left", padx=(12, 4))
        self.var_perfil = ctk.StringVar()
        ctk.CTkOptionMenu(linha1, values=[p.nome for p in listar_perfis()], variable=self.var_perfil,
                          width=130, command=self._trocar_perfil).pack(side="left", padx=4)
        ctk.CTkLabel(linha1, text=f"Cortes ({MIN_HISTORIAS}–{MAX_HISTORIAS}):").pack(side="left", padx=(12, 4))
        self.entry_max = ctk.CTkEntry(linha1, width=45)
        self.entry_max.insert(0, "5")
        self.entry_max.pack(side="left")
        self.btn_ia = ctk.CTkButton(linha1, text="Analisar com IA", fg_color=COR_VERDE,
                                    command=lambda: self._analisar(gemini=True))
        self.btn_ia.pack(side="left", padx=(12, 4))
        ctk.CTkButton(linha1, text="Usar remixes salvos (sem Gemini)", fg_color="#3E2661",
                      command=lambda: self._analisar(gemini=False)).pack(side="left", padx=4)
        ctk.CTkButton(linha1, text="Salvar lista", fg_color="#3E2661", width=95,
                      command=self._salvar).pack(side="left", padx=4)

        linha2 = ctk.CTkFrame(raiz, fg_color="#150C24", corner_radius=10)
        linha2.grid(row=2, column=0, sticky="ew", pady=(0, 6), ipady=6)
        ctk.CTkLabel(linha2, text="Palavras/bloco:").pack(side="left", padx=(12, 4))
        self.entry_palavras = ctk.CTkEntry(linha2, width=40)
        self.entry_palavras.insert(0, "4")
        self.entry_palavras.pack(side="left")
        self.var_dissolve = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(linha2, text="Dissolve editorial (s):", variable=self.var_dissolve).pack(side="left", padx=(12, 2))
        self.entry_dissolve = ctk.CTkEntry(linha2, width=45)
        self.entry_dissolve.insert(0, "1.0")
        self.entry_dissolve.pack(side="left")
        self.var_pausas = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(linha2, text="Remover frames mortos", variable=self.var_pausas).pack(side="left", padx=10)
        ctk.CTkLabel(linha2, text="Somente REMIX", text_color=COR_AMARELO).pack(side="left", padx=6)
        self.var_srt = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(linha2, text="SRT", variable=self.var_srt, width=60).pack(side="left", padx=6)
        self.var_capa = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(linha2, text="Capa", variable=self.var_capa, width=60).pack(side="left", padx=6)

        linha3 = ctk.CTkFrame(raiz, fg_color="#150C24", corner_radius=10)
        linha3.grid(row=3, column=0, sticky="ew", pady=(0, 8), ipady=6)
        ajustes = carregar_ajustes_agencia()
        ctk.CTkButton(linha3, text="Salvar AGENCY_AV_V1", width=150, fg_color="#3E2661",
                      command=self._salvar_av).pack(side="left", padx=(12, 6))
        self._campos_av = {}
        for chave, rotulo, largura in (("intensidade", "intens.%", 45), ("nitidez", "nitidez", 45),
                                       ("contraste", "contraste", 50), ("saturacao", "saturação", 50),
                                       ("gamma", "exposição", 50), ("crf", "CRF", 40)):
            ctk.CTkLabel(linha3, text=rotulo + ":").pack(side="left", padx=(6, 2))
            campo = ctk.CTkEntry(linha3, width=largura)
            campo.insert(0, str(getattr(ajustes, chave)))
            campo.pack(side="left")
            self._campos_av[chave] = campo

        self.lista = ctk.CTkScrollableFrame(raiz, fg_color="#150C24")
        self.lista.grid(row=4, column=0, sticky="nsew", pady=(0, 8))
        self.lista.grid_columnconfigure(0, weight=1)
        filtros = ctk.CTkFrame(raiz, fg_color="transparent")
        filtros.grid(row=5, column=0, sticky="ew")
        self.lbl_contagem = ctk.CTkLabel(filtros, text="PRONTOS: 0 · REVISÃO: 0 · REJEITADOS: 0")
        self.lbl_contagem.pack(side="left")
        ctk.CTkButton(filtros, text="📋 Copiar cortes para o ChatGPT", width=225,
                      fg_color="#3E2661", command=self._copiar_cortes_chatgpt).pack(side="left", padx=12)
        ctk.CTkButton(filtros, text="Refazer títulos pendentes", width=180,
                      command=self._refazer_titulos).pack(side="left", padx=12)
        self.filtro = ctk.CTkSegmentedButton(filtros, values=["Todos", "Prontos", "Revisão"],
                                            command=lambda _: self._filtrar())
        self.filtro.set("Todos")
        self.filtro.pack(side="right")

        rodape = ctk.CTkFrame(raiz, fg_color="transparent")
        rodape.grid(row=6, column=0, sticky="ew")
        rodape.grid_columnconfigure(0, weight=1)
        self.lbl_status = ctk.CTkLabel(rodape, text="Importe e transcreva um episódio.", anchor="w",
                                       justify="left", text_color=COR_CINZA_CLARO, wraplength=760)
        self.lbl_status.grid(row=0, column=0, sticky="ew")
        self.barra = ctk.CTkProgressBar(rodape, progress_color=COR_ROXO_CLARO)
        self.barra.grid(row=1, column=0, sticky="ew", pady=3)
        self.barra.set(0)
        self.barra_geral = ctk.CTkProgressBar(rodape, progress_color=COR_VERDE)
        self.barra_geral.grid(row=2, column=0, sticky="ew", pady=3)
        self.barra_geral.set(0)
        botoes = ctk.CTkFrame(rodape, fg_color="transparent")
        botoes.grid(row=0, column=1, rowspan=3, padx=(12, 0))
        ctk.CTkButton(botoes, text="Diagnóstico", fg_color="#3E2661", width=100,
                      command=self._mostrar_diagnostico).pack(side="left", padx=4)
        self.btn_gerar = ctk.CTkButton(botoes, text="Gerar selecionados", fg_color=COR_VERDE,
                                       command=self._gerar)
        self.btn_gerar.pack(side="left", padx=4)
        self.btn_cancelar = ctk.CTkButton(botoes, text="Cancelar", fg_color=COR_VERMELHO,
                                          state="disabled", command=self._cancelar)
        self.btn_cancelar.pack(side="left", padx=4)

    # ─── Estado ──────────────────────────────────────────────────────────────

    def _restaurar(self):
        salvos = []
        try:
            perfil = obter_perfil(self.app.estado.get("perfil_visual_id"))
        except (ValueError, FileNotFoundError):
            perfil = obter_perfil()
        self.var_perfil.set(perfil.nome)
        video, transcricao = self.app.estado.get("video_path"), self.app.estado.get("transcricao")
        if video and transcricao and caminho_normalizado(transcricao.video_path) == caminho_normalizado(video):
            try:
                salvos = carregar_lista_agencia(video, transcricao.video_id)
            except (OSError, ValueError, TypeError):
                salvos = []
            # Se o episódio estiver na esteira e tiver planos em memória mais recentes (ou ainda não salvos), mescla
            try:
                ep_esteira = self._obter_esteira().obter_episodio_por_video(video)
            except Exception:
                ep_esteira = None
            if ep_esteira is not None and ep_esteira.planos:
                if not salvos:
                    from copy import deepcopy
                    salvos = [deepcopy(p) for p in ep_esteira.planos]
                else:
                    por_id_ep = {p.id: p for p in ep_esteira.planos}
                    for p_s in salvos:
                        p_e = por_id_ep.get(p_s.id)
                        if p_e is not None and p_e.status in ("renderizando", "concluído"):
                            p_s.status = p_e.status
                            if p_e.caminho_saida:
                                p_s.caminho_saida = p_e.caminho_saida
            self.planos = self._somente_remix(salvos, transcricao)
            if not salvos:
                # An empty persisted list is not an empty backend result. Restore
                # this episode's existing analysis without allowing any provider call.
                config = carregar_config_ia()
                if caminho_cache_agencia(transcricao, config).is_file():
                    from modules.analise_ia import ErroGemini
                    try:
                        resultado = analisar_agencia(transcricao, config, provedor=_SomenteCache())
                    except (ErroGemini, OSError, ValueError) as erro:
                        self._rejeicoes.append(f"Resultado salvo indisponivel: {erro}")
                    else:
                        self.planos = self._somente_remix(list(resultado), transcricao)
                        self._diagnostico_producao = getattr(resultado, "diagnostico", None)
                        self._rejeicoes.extend(getattr(resultado, "rejeicoes", []))
                        self._rejeitados_total = getattr(self._diagnostico_producao, "candidatos_rejeitados", 0)
            reavaliar_salvos(self.planos,transcricao)
            self.lbl_status.configure(text=self._resumo_conteudo() if salvos or self.planos
                                      else "Analise o episódio para montar os remixes.")
        self._redesenhar()
        if self.planos and not salvos:
            self._salvar(silencioso=True)

    def _resumo_conteudo(self):
        prontos = sum(self._pronto_visual(p) for p in self.planos)
        revisao = len(self.planos) - prontos
        if not prontos and not revisao:
            return "Nenhum remix narrativo válido foi encontrado neste episódio."
        return f"{prontos} prontos · {revisao} para revisão"

    def _status_card(self, plano):
        if corte_manual(plano):
            return "✓ APROVADO MANUALMENTE"
        return "REVISÃO" if plano.estado_editorial == "revisao" or plano.status == REVISAO else status_visual(plano)

    def _somente_remix(self, planos, transcricao):
        """Display approved remixes AND backend review plans, not export eligibility."""
        intro = self._intro(transcricao)
        aceitos = []
        for plano in planos:
            if plano.estado_editorial == "revisao" or plano.status == REVISAO:
                if not plano.video_id or plano.video_id == transcricao.video_id:
                    plano.estado_editorial = "revisao"
                    aceitos.append(plano)
                else:
                    self._rejeicoes.append(f"{plano.inicio:.1f}s: plano de outro episodio.")
                continue
            if plano.fim-plano.inicio > 180:
                self._rejeicoes.append(f"{plano.inicio:.1f}s: historia gigante requer subdivisao.")
                continue
            problemas = verificar_plano_remix(plano, transcricao, intro)
            if problemas:
                if revisavel(plano, transcricao):
                    marcar_revisao(plano,evidenciar(falas(plano,transcricao)),problemas)
                    aceitos.append(plano)
                else:
                    self._rejeicoes.append(f"{plano.titulo} ({plano.inicio:.1f}s): {problemas[0]}")
            else:
                aceitos.append(plano)
        return aceitos

    def _mostrar_diagnostico(self):
        if self._diagnostico_producao:
            d = self._diagnostico_producao
            partes = "\n".join(f"{p['inicio']:.1f}–{p['fim']:.1f}s: {p.get('avaliados',0)} janelas" for p in d.partes_analisadas)
            self.app.mostrar_info("Diagnóstico de produção",d.resumo()+"\n"+partes+"\n"+
                                  "\n".join(str(m) for m in d.motivos_rejeicao[:20]))
            return
        if not self._rejeicoes:
            self.app.mostrar_info("Diagnóstico", "Nenhum candidato rejeitado nesta sessão.")
            return
        self.app.mostrar_info("Diagnóstico — candidatos rejeitados (não exportáveis)",
                              "\n".join(f"• {r}" for r in self._rejeicoes[:25]))

    def _trocar_perfil(self, nome):
        # Troca apenas o visual: nenhuma análise é refeita e nenhum cache é invalidado.
        try:
            perfil = perfil_por_nome(nome)
            obter_perfil(perfil)
        except (ValueError, FileNotFoundError) as erro:
            self.app.mostrar_erro("Perfil visual", str(erro))
            return
        self.app.atualizar_estado("perfil_visual_id", perfil.id)
        self.lbl_status.configure(text=f"Perfil {perfil.nome} selecionado. A análise não será refeita.")

    def _pre_requisitos(self):
        video, transcricao = self.app.estado.get("video_path"), self.app.estado.get("transcricao")
        if not video or not transcricao:
            self.app.mostrar_aviso("Modo Agência", "Importe e transcreva um vídeo primeiro.")
            return None, None
        if caminho_normalizado(transcricao.video_path) != caminho_normalizado(video):
            self.app.mostrar_erro("Modo Agência", "A transcrição carregada pertence a outro episódio.")
            return None, None
        return video, transcricao

    def _opcoes(self) -> OpcoesEdicao:
        return OpcoesEdicao(
            transition_mode="agency" if self.var_dissolve.get() else "hard",
            transition_duration=float(self.entry_dissolve.get().replace(",", ".")),
            compactar_pausas=bool(self.var_pausas.get()),
            permitir_remix=True,   # Modo Agência = somente remix narrativo
            palavras_por_bloco=int(self.entry_palavras.get()),
        ).validar()

    def _ajustes(self):
        ajustes = carregar_ajustes_agencia()
        for chave, campo in self._campos_av.items():
            atual = getattr(ajustes, chave)
            valor = campo.get().strip().replace(",", ".")
            setattr(ajustes, chave, type(atual)(float(valor)) if isinstance(atual, int) else float(valor))
        return ajustes.validar()

    def _salvar_av(self):
        try:
            caminho = salvar_ajustes_agencia(self._ajustes())
            self.lbl_status.configure(text=f"Preset AGENCY_AV_V1 salvo em {caminho.name}.")
        except (ValueError, OSError) as erro:
            self.app.mostrar_erro("AGENCY_AV_V1", str(erro))

    def _intro(self, transcricao):
        intro = self.app.estado.get("intro_intervalo")
        return intro if intro is not None else carregar_intro(transcricao)

    # ─── Análise ─────────────────────────────────────────────────────────────

    def _analisar(self, gemini: bool):
        video, transcricao = self._pre_requisitos()
        if not transcricao:
            return
        if self._obter_esteira().episodio_em_processamento(video):
            self.app.mostrar_aviso(
                "Modo Agência",
                "Este episódio está em processamento ativo na Esteira Multi-Episódio. "
                "Aguarde a conclusão ou cancele-o na esteira antes de refazer a análise.",
            )
            return
        if fila_analise_ia.ocupada or self.app.estado.get('comparacao_ocupada'):
            self.app.mostrar_aviso("Modo Agência", "Já existe uma análise em andamento.")
            return
        try:
            config = carregar_config_ia()
            maximo = int(self.entry_max.get())
            if not MIN_HISTORIAS <= maximo <= MAX_HISTORIAS:
                raise ValueError(f"No Modo Agência a quantidade de cortes deve ficar entre "
                                 f"{MIN_HISTORIAS} e {MAX_HISTORIAS}.")
            config.max_cortes = maximo
            config.validar()
        except (ValueError, TypeError) as erro:
            self.app.mostrar_erro("Modo Agência", str(erro))
            return
        sessao, origem = int(self.app.estado.get("video_session", 0)), caminho_normalizado(video)
        aviso = ""
        if gemini and eh_modelo_lite(config.modelo):
            aviso = (" Atenção: modelos Lite montam remixes mais fracos; para melhor qualidade use "
                     "gemini-3.6-flash ou gemini-3.8-flash (1–2 pedidos por episódio).")
        self.lbl_status.configure(text=("Procurando histórias remontáveis…" if gemini
                                        else "Revalidando remixes salvos…") + aviso)
        self._tarefa_atual = TarefaRenderizacao(
            nome="Modo Agência", funcao=executar_analise_com_titulos,
            args=(transcricao, config),
            kwargs={'analise': analisar_agencia if gemini else _remixes_salvos,
                    'provedor': provider_orcado(self.app.estado, transcricao, config, modo='agencia',
                                               schema=_RespostaAgencia) if gemini else None,
                    'orcamento': orcamento_episodio(self.app.estado, transcricao, config.max_chamadas_episodio),
                    'gerar': gemini},
            callback_progresso=lambda pct, msg: self._eventos.put(("progresso_ia", sessao, origem, pct, msg)),
            callback_conclusao=lambda planos: self._eventos.put(("ia_fim", sessao, origem, planos, maximo)),
            callback_erro=lambda erro: self._eventos.put(("ia_erro", sessao, origem, erro)),
        )
        fila_analise_ia.adicionar(self._tarefa_atual)

    # ─── Lista ───────────────────────────────────────────────────────────────

    def _refazer_titulos(self):
        from copy import deepcopy
        from modules.titulos_viral import gerar, finalizar_salvos
        video, transcricao = self._pre_requisitos()
        if not transcricao or fila_analise_ia.ocupada or self._tarefa_atual:
            return
        if self._obter_esteira().episodio_em_processamento(video):
            self.app.mostrar_aviso(
                "Modo Agência",
                "Este episódio está em processamento ativo na Esteira Multi-Episódio. "
                "Edite os títulos manualmente nos campos ou aguarde a conclusão da etapa atual.",
            )
            return
        self._sincronizar()
        finalizar_salvos(self.planos, transcricao)
        pendentes = [p for p in self.planos if not p.titulo_auto_aprovado
                     and not p.titulo_aprovado_manualmente and not p.titulo_confirmacao_pendente]
        if not pendentes:
            self.lbl_status.configure(text="Nenhum título automático pendente.")
            return
        config = carregar_config_ia()
        antes = {p.id: deepcopy(p.como_dict()) for p in pendentes}
        copias = [PlanoAgencia.de_dict(deepcopy(antes[p.id])) for p in pendentes]
        sessao, origem = int(self.app.estado.get('video_session', 0)), caminho_normalizado(video)
        self.lbl_status.configure(text="Refazendo títulos pendentes…")
        self._tarefa_atual = TarefaRenderizacao(
            nome="Títulos Agência", funcao=gerar, args=(copias, transcricao, config),
            kwargs=dict(usar_cache=False, orcamento=orcamento_episodio(
                self.app.estado, transcricao, config.max_chamadas_episodio)),
            callback_conclusao=lambda _: self._eventos.put(('titulos_fim', sessao, origem, antes, copias)),
            callback_erro=lambda erro: self._eventos.put(('ia_erro', sessao, origem, erro)),
        )
        fila_analise_ia.adicionar(self._tarefa_atual)

    def _redesenhar(self):
        # Payload titles, restored lists and recovery results share one final gate.
        from modules.titulos_viral import finalizar_salvos
        if self.planos and self.app.estado.get('transcricao'):
            finalizar_salvos(self.planos,self.app.estado['transcricao'])
        self.planos.sort(key=lambda p: (not self._pronto_visual(p),p.inicio))
        self._limpar_traces_titulo()
        for filho in self.lista.winfo_children():
            filho.destroy()
        self._linhas.clear()
        if hasattr(self, "_cards"):
            self._cards.clear()
        self._linhas = {}
        self._cards = {}
        for indice, plano in enumerate(self.planos, 1):
            frame = ctk.CTkFrame(self.lista, fg_color="#211536")
            frame.grid(row=indice - 1, column=0, sticky="ew", padx=4, pady=4)
            frame.grid_columnconfigure(3, weight=1)
            self._cards[plano.id] = frame
            var_sel = ctk.BooleanVar(value=plano.selecionado)
            ctk.CTkCheckBox(frame, text=str(indice), width=40, variable=var_sel).grid(row=0, column=0, padx=6)
            rotulo = "REVISÃO" if plano.estado_editorial == "revisao" and not corte_manual(plano) else "REMIX"
            ctk.CTkLabel(frame, text=f"{rotulo} · {plano.inicio:.1f}–{plano.fim:.1f}s · {plano.fim-plano.inicio:.1f}s", width=260,
                         text_color=COR_AMARELO).grid(row=0, column=1)
            ctk.CTkLabel(frame, text="Título:").grid(row=0, column=2, padx=(6, 2))
            var_titulo = tk.StringVar(value=titulo_exibicao(plano))
            titulo = ctk.CTkEntry(frame, textvariable=var_titulo)
            titulo.grid(row=0, column=3, sticky="ew", padx=4, pady=6)
            opcoes = [f"[Título {i} · score {t.score_titulo if t.score_titulo is not None else 'pendente'} · "
                      f"{ROTULO_TIPO.get(t.tipo, t.tipo)}] {t.texto}" for i,t in enumerate(plano.titulos,1)]
            if opcoes:
                menu = ctk.CTkOptionMenu(frame, values=opcoes, width=330,
                                         command=lambda valor, pid=plano.id:
                                             self._selecionar_titulo(pid, valor.split("] ", 1)[1]))
                n = len(plano.titulos)
                menu.set(f"Alternativas: {n} aprovada{'s' if n != 1 else ''}")
                menu.grid(row=1, column=1, padx=6, pady=(0, 4), sticky="w")
            ctk.CTkLabel(frame, text="Capa:").grid(row=1, column=2, sticky="e", padx=(6, 2))
            capa = ctk.CTkEntry(frame, placeholder_text="Título curto da capa")
            capa.insert(0, plano.titulo_capa)
            capa.grid(row=1, column=3, sticky="ew", padx=4, pady=(0, 4))
            acoes = ctk.CTkFrame(frame, fg_color="transparent")
            acoes.grid(row=3, column=1, columnspan=3, sticky="w", padx=6, pady=(0, 6))
            ctk.CTkButton(acoes, text="Prévia", width=70, fg_color="#3E2661",
                          command=lambda pid=plano.id: self._previa(pid)).pack(side="left", padx=(0, 6))
            ctk.CTkButton(acoes, text="Capas", width=70, fg_color="#3E2661",
                          command=lambda pid=plano.id: self._capas(pid)).pack(side="left", padx=6)
            ctk.CTkButton(acoes, text="Excluir", width=70, fg_color=COR_VERMELHO,
                          command=lambda pid=plano.id: self._excluir(pid)).pack(side="left", padx=6)
            ctk.CTkButton(acoes, text="Editar montagem", width=120, fg_color="#3E2661",
                          command=lambda pid=plano.id: self._editar_montagem(pid)).pack(side="left", padx=6)
            if plano.estado_editorial == "revisao":
                ctk.CTkButton(acoes, text="APROVAR MANUALMENTE", width=170, fg_color=COR_VERDE,
                              command=lambda pid=plano.id: self._aprovar_corte(pid)).pack(side="left", padx=6)
            from ui.titulos_diagnostico import mostrar_titulos_rejeitados
            ctk.CTkButton(acoes, text="Ver títulos rejeitados", width=150, fg_color="#3E2661",
                command=lambda p=plano: mostrar_titulos_rejeitados(self,p.titulos_v2)).pack(side="left", padx=6)
            ctk.CTkButton(frame, text="✓ Aprovar título", width=140, fg_color="#3E2661",
                          command=lambda pid=plano.id: self._aprovar_titulo(pid)).grid(
                              row=4, column=3, sticky="e", padx=6, pady=(0, 6))
            status = ctk.CTkLabel(frame, text=self._status_card(plano), text_color=COR_CINZA_ESCURO,
                                 wraplength=380, anchor="w", justify="left")
            status.grid(row=4, column=1, columnspan=2, sticky="w", padx=6, pady=(0, 6))
            detalhe = [plano.descricao or plano.motivo_escolha] + self._estrutura_remix(plano) + [
                       f"Justificativa do remix: {plano.remix_motivo}" if plano.remix_motivo else "",
                       f"Origem: {plano.origem}" + (f" · avisos: {'; '.join(plano.avisos)}" if plano.avisos else "")]
            if plano.revisao_humana:
                r = plano.revisao_humana
                detalhe += [f"Score de oportunidade: {r.get('score_oportunidade',0)} (nao e aprovacao automatica)",
                            "Revisar: " + '; '.join(r.get('motivos',[])), r.get('sugestao',''),
                            "Texto fonte: " + r.get('texto_fonte','')]
            gate = [g for g in plano.gate if not g.get("ok")]
            if gate:
                detalhe.append("Revisar: " + "; ".join(f"{g['nome']}: {g['detalhe']}" for g in gate))
            ctk.CTkLabel(frame, text="\n".join(d for d in detalhe if d), anchor="w", justify="left",
                         wraplength=680, text_color=COR_VERMELHO if gate else COR_CINZA_CLARO).grid(
                row=2, column=1, columnspan=3, sticky="ew", padx=6, pady=(0, 4))
            self._linhas[plano.id] = (var_sel, titulo, capa, status)
            trace_id = var_titulo.trace_add("write", lambda *_args, pid=plano.id: self._titulo_editado(pid))
            self._traces_titulo.append((var_titulo, trace_id))
        self._filtrar()

    def _pronto_visual(self, plano):
        if plano.status in ("erro", "revisão", REVISAO):
            return False
        if plano.estado_editorial == "revisao":
            return corte_manual(plano)
        return (plano.titulo_auto_aprovado or plano.titulo_aprovado_manualmente) and not plano.titulo_confirmacao_pendente

    def _filtrar(self, preservar_edicao=None):
        prontos = sum(self._pronto_visual(p) for p in self.planos)
        rejeitados = (getattr(self,'_rejeitados_total',0) if getattr(self,'_diagnostico_producao',None)
                      else len(self._rejeicoes))
        self.lbl_contagem.configure(text=f"PRONTOS: {prontos} · REVISÃO: {len(self.planos)-prontos} · REJEITADOS: {rejeitados}")
        for p in self.planos:
            frame = self._cards.get(p.id)
            if not frame:
                continue
            mostrar = (p.id == preservar_edicao or self.filtro.get() == "Todos" or
                       (self.filtro.get() == "Prontos") == self._pronto_visual(p))
            if mostrar:
                frame.grid()
            else:
                frame.grid_remove()

    def _aprovar_corte(self, plano_id):
        self._sincronizar()
        plano = next(p for p in self.planos if p.id == plano_id)
        try:
            aprovar_corte(plano,self.app.estado['transcricao'],self._linhas[plano_id][1].get(),
                          self._intro(self.app.estado['transcricao']))
            plano.status = "APROVADO_MANUALMENTE"
        except ValueError as erro:
            self.app.mostrar_aviso("Aprovação manual",str(erro))
        self._redesenhar()
        self.lbl_status.configure(text=self._resumo_conteudo())
        self._salvar(silencioso=True)

    def _bloqueado_pela_esteira(self, acao: str = "Esta operação") -> bool:
        video = self.app.estado.get("video_path")
        if video and self._obter_esteira().episodio_em_processamento(video):
            self.app.mostrar_aviso(
                "Modo Agência",
                f"{acao} está bloqueada enquanto este episódio está em processamento ativo na Esteira Multi-Episódio. "
                "Aguarde a conclusão ou cancele-o na esteira antes de continuar.",
            )
            return True
        return False

    def _editar_montagem(self, plano_id):
        if self._bloqueado_pela_esteira("A edição de montagem"):
            return
        self._sincronizar()
        plano = next(p for p in self.planos if p.id == plano_id)
        janela = ctk.CTkToplevel(self)
        janela.title("Montagem da história")
        janela.geometry("650x500")
        campo = ctk.CTkTextbox(janela)
        campo.pack(fill="both",expand=True,padx=12,pady=12)
        campo.insert("1.0",json.dumps(dict(segmentos=plano.segmentos,gancho=plano.gancho,
                     estrutura=plano.estrutura,remover=plano.remover),ensure_ascii=False,indent=2))
        def salvar():
            from copy import deepcopy
            try:
                dados = json.loads(campo.get("1.0","end"))
                editado = deepcopy(plano)
                editado.segmentos = dados['segmentos']
                editado.gancho = dados.get('gancho')
                editado.usar_remix = bool(editado.gancho)
                editado.estrutura = dados.get('estrutura',{})
                editado.remover = dados.get('remover',[])
                editado.inicio = min(float(s['inicio']) for s in editado.segmentos)
                editado.fim = max(float(s['fim']) for s in editado.segmentos)
                erros = problemas_tecnicos(editado,self.app.estado['transcricao'])
                if erros:
                    raise ValueError('; '.join(erros))
                for nome in ('segmentos','gancho','usar_remix','estrutura','remover','inicio','fim'):
                    setattr(plano,nome,getattr(editado,nome))
                plano.corte_aprovado_manualmente = False
                plano.estado_editorial,plano.status = 'revisao',REVISAO
                plano.selecionado = False
                janela.destroy()
                self._redesenhar()
                self._salvar(silencioso=True)
            except (ValueError,TypeError,KeyError) as erro:
                self.app.mostrar_aviso("Montagem",str(erro))
        ctk.CTkButton(janela,text="Salvar montagem",command=salvar).pack(pady=(0,12))

    def _estrutura_remix(self, plano):
        transcricao = self.app.estado.get("transcricao")
        if not transcricao:
            return []
        try:
            tl = construir_timeline(plano, transcricao, self._intro(transcricao), OpcoesEdicao())
        except ValueError:
            return []
        gi, gf = tl.gancho or (0.0, 0.0)

        def faixa(trechos):
            return ", ".join(f"{t.inicio:.1f}–{t.fim:.1f}s" for t in trechos) or "—"

        historia = tl.trechos[1:]
        antes = [t for t in historia if t.fim <= gi + 0.05]
        depois = [t for t in historia if t.inicio >= gf - 0.05]
        contexto, desenvolvimento = antes[:1], antes[1:]
        return [f"Gancho: {gi:.1f}–{gf:.1f}s · Contexto: {faixa(contexto)} · "
                f"Desenvolvimento: {faixa(desenvolvimento)} · Consequência: {faixa(depois)}",
                f"{len(tl.trechos)} segmentos · duração final {tl.duracao:.1f}s · "
                f"ordem: {' → '.join(f'{t.inicio:.0f}s' for t in tl.trechos)}"]

    def _sincronizar(self):
        for plano in self.planos:
            if plano.id not in self._linhas:
                continue
            var_sel, titulo, capa, _status = self._linhas[plano.id]
            plano.selecionado = bool(var_sel.get())
            texto = titulo.get().strip()
            if texto != titulo_exibicao(plano):
                if texto and not V.titulo_pendente(texto):
                    aprovar_titulo(plano, texto)
                else:
                    editar_titulo(plano, texto)
            plano.titulo_capa = " ".join(capa.get().split()).upper()

    def _selecionar_titulo(self, plano_id, texto):
        plano = next(p for p in self.planos if p.id == plano_id)
        if texto not in [t.texto for t in plano.titulos]:
            return
        aprovar_titulo(plano, texto)
        self._selecionando_titulo = True
        try:
            _, campo, capa, _ = self._linhas[plano_id]
            campo.delete(0, 'end')
            campo.insert(0, titulo_exibicao(plano))
            capa.delete(0, 'end')
            capa.insert(0, plano.titulo_capa)
        finally:
            self._selecionando_titulo = False
        self._atualizar_status(plano)
        self._filtrar()

    def _titulo_editado(self, plano_id):
        if getattr(self, '_selecionando_titulo', False):
            return
        plano = next(p for p in self.planos if p.id == plano_id)
        editar_titulo(plano, self._linhas[plano_id][1].get())
        self._atualizar_status(plano)
        if hasattr(self,"_filtrar"):
            self._filtrar(preservar_edicao=plano_id)

    def _aprovar_titulo(self, plano_id):
        plano = next(p for p in self.planos if p.id == plano_id)
        try:
            aprovar_titulo(plano, self._linhas[plano_id][1].get())
        except ValueError as erro:
            self.app.mostrar_aviso("Título", str(erro))
        self._linhas[plano_id][0].set(plano.selecionado)
        self._atualizar_status(plano)
        self._salvar(silencioso=True)
        if hasattr(self,"_filtrar"):
            self._filtrar()

    def _excluir(self, plano_id):
        if self._bloqueado_pela_esteira("A exclusão de cortes"):
            return
        self._sincronizar()
        self.planos = [p for p in self.planos if p.id != plano_id]
        self._redesenhar()

    def _salvar(self, silencioso=False):
        video, transcricao = self.app.estado.get("video_path"), self.app.estado.get("transcricao")
        if not video or not transcricao:
            return
        try:
            if self._linhas:
                self._sincronizar()
            caminho = salvar_lista_agencia(video, transcricao.video_id, self.planos)
            try:
                self._obter_esteira().sincronizar_planos_externos(video, self.planos)
            except Exception:
                pass
            if not silencioso:
                self.lbl_status.configure(text=f"Lista do Modo Agência salva ({caminho.name}).")
        except (OSError, ValueError) as erro:
            if not silencioso:
                self.app.mostrar_erro("Modo Agência", str(erro))

    def _copiar_cortes_chatgpt(self):
        from modules.agencia.pacote_chatgpt import construir_pacote
        _, transcricao = self._pre_requisitos()
        if not transcricao:
            return
        try:
            selecionados = [p.id for p in self.planos
                            if p.id in self._linhas and bool(self._linhas[p.id][0].get())]
            campos_ui = {p.id: (self._linhas[p.id][1].get(), self._linhas[p.id][2].get())
                         for p in self.planos if p.id in self._linhas}
            texto, quantidade = construir_pacote(
                self.planos, transcricao, selecionados=selecionados,
                intro=self._intro(transcricao), opcoes=self._opcoes(), campos_ui=campos_ui)
            if not quantidade:
                self.app.mostrar_aviso("Copiar cortes", "A lista atual não contém cortes para copiar.")
                return
            from modules.titulos_lote import escrever_clipboard_sistema
            escrever_clipboard_sistema(self, texto)
        except Exception as erro:
            self.app.mostrar_erro("Copiar cortes para o ChatGPT", f"Não foi possível copiar os cortes.\n{erro}")
            return
        unidade = "corte foi copiado" if quantidade == 1 else "cortes foram copiados"
        self.app.mostrar_info("Copiar cortes para o ChatGPT",
                              f"{quantidade} {unidade}. O conteúdo está pronto para colar no ChatGPT.")

    def _plano(self, plano_id):
        return next(p for p in self.planos if p.id == plano_id)

    # ─── Prévia e capas ──────────────────────────────────────────────────────

    def _previa(self, plano_id):
        video, transcricao = self._pre_requisitos()
        if not video:
            return
        if self._bloqueado_pela_esteira("A geração de prévia"):
            return
        if fila_render.ocupada:
            return
        try:
            self._sincronizar()
            opcoes = self._opcoes()
            ajustes = self._ajustes()
        except (ValueError, TypeError) as erro:
            self.app.mostrar_erro("Prévia", str(erro))
            return
        plano = self._plano(plano_id)
        destino = TEMP_DIR / "agencia_previas" / f"previa_{plano.id}.mp4"
        destino.parent.mkdir(parents=True, exist_ok=True)
        self._tarefa_atual = TarefaRenderizacao(
            nome="Prévia Agência", funcao=renderizar_previa_agencia,
            args=(video, plano, transcricao, self.app.estado.get("perfil_visual_id"), str(destino)),
            kwargs={"opcoes": opcoes, "intro": self._intro(transcricao), "ajustes": ajustes},
            callback_progresso=lambda pct, msg: self._eventos.put(("progresso", None, pct, msg)),
            callback_conclusao=lambda r: self._eventos.put(("previa_fim", r)),
            callback_erro=lambda e: self._eventos.put(("aviso", f"Falha na prévia: {e}")),
        )
        fila_render.adicionar(self._tarefa_atual)

    def _capas(self, plano_id):
        video, transcricao = self._pre_requisitos()
        if not video:
            return
        if self._bloqueado_pela_esteira("A geração de capas"):
            return
        if fila_render.ocupada:
            return
        try:
            self._sincronizar()
            opcoes = self._opcoes()
        except (ValueError, TypeError) as erro:
            self.app.mostrar_erro("Capas", str(erro))
            return
        plano = self._plano(plano_id)
        self.lbl_status.configure(text="Selecionando 3 frames candidatos para a capa…")
        pasta = TEMP_DIR / "agencia_capas" / plano.id

        def gerar(cancelada_ref=None, progresso_cb=None):
            return gerar_capas_candidatas(video, plano, transcricao, self.app.estado.get("perfil_visual_id"),
                                          pasta, opcoes, self._intro(transcricao))

        self._tarefa_atual = TarefaRenderizacao(
            nome="Capas", funcao=gerar,
            callback_conclusao=lambda capas: self._eventos.put(("capas_fim", plano_id, capas)),
            callback_erro=lambda e: self._eventos.put(("aviso", f"Falha nas capas: {e}")),
        )
        fila_render.adicionar(self._tarefa_atual)

    def _escolher_capa(self, plano_id, capas):
        plano = self._plano(plano_id)
        janela = ctk.CTkToplevel(self)
        janela.title("Escolha a capa")
        janela.attributes("-topmost", True)
        imagens = []
        for indice, capa in enumerate(capas):
            miniatura = ctk.CTkImage(Image.open(capa["caminho"]), size=(216, 384))
            imagens.append(miniatura)
            coluna = ctk.CTkFrame(janela, fg_color="transparent")
            coluna.pack(side="left", padx=8, pady=8)
            ctk.CTkLabel(coluna, image=miniatura, text="").pack()
            ctk.CTkLabel(coluna, text=f"Frame {capa['tempo_fonte']:.2f}s · "
                                      f"{'legível' if capa['legivel_miniatura'] else 'revisar leitura'}").pack()

            def exportar(i=indice, c=capa):
                plano.capa_indice = i
                pasta = self.app.estado.get("pasta_export") or str(Path(__file__).parents[1] / "exportados")
                destino = caminho_sem_sobrescrever(pasta, f"capa_{plano.titulo}", ".jpg")
                Image.open(c["caminho"]).save(destino, quality=95)
                plano.caminho_capa = str(destino)
                self._salvar(silencioso=True)
                self.lbl_status.configure(text=f"Capa {i + 1} escolhida e exportada: {destino.name}")
                janela.destroy()

            ctk.CTkButton(coluna, text=f"Usar capa {indice + 1}", command=exportar).pack(pady=4)
        janela._imagens = imagens

    # ─── Exportação em lote ──────────────────────────────────────────────────

    def _gerar(self):
        video, transcricao = self._pre_requisitos()
        if not video:
            return
        if self._obter_esteira().episodio_em_processamento(video):
            self._salvar(silencioso=True)
            self.app.mostrar_aviso(
                "Modo Agência",
                "Este episódio já está em processamento/renderização na Esteira Multi-Episódio. "
                "Suas edições de títulos e aprovações foram salvas e sincronizadas com a esteira.",
            )
            return
        if fila_render.ocupada:
            self.app.mostrar_aviso("Modo Agência", "Já existe uma renderização em andamento.")
            return
        try:
            self._sincronizar()
            self._opcoes_lote = self._opcoes()
            self._ajustes_lote = self._ajustes()
        except (ValueError, TypeError) as erro:
            self.app.mostrar_erro("Modo Agência", str(erro))
            return
        self._pendentes, revisao_titulo = selecionar_prontos(self.planos, transcricao)
        if not self._pendentes:
            self.app.mostrar_aviso("Modo Agência", "Nenhuma história selecionada pronta para gerar."
                                   if revisao_titulo else "Nenhuma história selecionada.")
            self._redesenhar()
            return
        self._sessao = int(self.app.estado.get("video_session", 0))
        self._video = caminho_normalizado(video)
        self._perfil = self.app.estado.get("perfil_visual_id")
        self._pasta = self.app.estado.get("pasta_export") or str(Path(__file__).parents[1] / "exportados")
        self._intro_lote = self._intro(transcricao)
        self._total, self._concluidos, self._cancelado = len(self._pendentes), 0, False
        self._revisao = [p.id for p in revisao_titulo]
        self.btn_gerar.configure(state="disabled")
        self.btn_cancelar.configure(state="normal")
        self._proximo()

    def _proximo(self):
        if not sessao_corresponde(self.app.estado, self._sessao, self._video):
            self._cancelado = True
        if self._cancelado or not self._pendentes:
            self._finalizar()
            return
        plano = self._pendentes.pop(0)
        try:
            validar_para_exportacao(plano,self.app.estado.get("transcricao"))
        except ValueError as erro:
            if plano.estado_editorial == 'revisao':
                plano.status,plano.erro = REVISAO,str(erro)
            else:
                marcar_revisao_titulo(plano)
            self._revisao.append(plano.id)
            self._concluidos += 1
            self._atualizar_status(plano)
            self._proximo()
            return
        plano.status = "renderizando"
        self._atualizar_status(plano)
        self._tarefa_atual = TarefaRenderizacao(
            nome=f"Agência {plano.id}", funcao=exportar_corte_agencia,
            args=(self._video, plano, self.app.estado.get("transcricao"), self._pasta),
            kwargs={"perfil": self._perfil, "ajustes": self._ajustes_lote, "opcoes": self._opcoes_lote,
                    "intro": self._intro_lote, "outros_planos": [p for p in self.planos if p.selecionado],
                    "exportar_srt": bool(self.var_srt.get()), "gerar_capa": bool(self.var_capa.get())},
            callback_progresso=lambda pct, msg, pid=plano.id: self._eventos.put(("progresso", pid, pct, msg)),
            callback_conclusao=lambda r, pid=plano.id: self._eventos.put(("fim", pid, r)),
            callback_erro=lambda e, pid=plano.id: self._eventos.put(("fim", pid, {"sucesso": False, "erro": str(e)})),
        )
        fila_render.adicionar(self._tarefa_atual)

    def _finalizar(self):
        self.btn_gerar.configure(state="normal")
        self.btn_cancelar.configure(state="disabled")
        self._tarefa_atual = None
        texto = "Lote cancelado." if self._cancelado else f"Lote finalizado: {self._concluidos}/{self._total}."
        if self._revisao:
            texto += f" {len(self._revisao)} corte(s) marcados para revisão e NÃO exportados."
        self.lbl_status.configure(text=texto)
        self._salvar(silencioso=True)
        self._redesenhar()

    def _cancelar(self):
        self._cancelado = True
        self._pendentes.clear()
        if self._tarefa_atual:
            self._tarefa_atual.cancelar()
        self.lbl_status.configure(text="Cancelamento solicitado…")

    def _atualizar_status(self, plano):
        linha = self._linhas.get(plano.id)
        if linha:
            cor = (COR_VERDE if plano.status == "concluído" else
                   COR_VERMELHO if plano.status in ("erro", "revisão") else COR_CINZA_CLARO)
            linha[3].configure(text=self._status_card(plano), text_color=cor)

    # ─── Eventos ─────────────────────────────────────────────────────────────

    def _processar_eventos(self):
        try:
            while True:
                evento = self._eventos.get_nowait()
                tipo = evento[0]
                if tipo == "progresso_ia":
                    if sessao_corresponde(self.app.estado, evento[1], evento[2]):
                        self.barra.set(evento[3] / 100)
                        self.lbl_status.configure(text=evento[4])
                elif tipo == "ia_fim":
                    if not sessao_corresponde(self.app.estado, evento[1], evento[2]):
                        continue
                    rejeicoes = list(getattr(evento[3], "rejeicoes", []))
                    self._rejeicoes = rejeicoes
                    self._diagnostico_producao = getattr(evento[3],'diagnostico',None)
                    self._rejeitados_total = getattr(getattr(evento[3],'diagnostico',None),'candidatos_rejeitados',len(rejeicoes))
                    transcricao = self.app.estado.get("transcricao")
                    self.planos = self._somente_remix(list(evento[3]), transcricao)[:evento[4]]
                    self.filtro.set("Todos")
                    self._tarefa_atual = None
                    self.barra.set(1)
                    texto = self._resumo_conteudo()
                    if self._rejeicoes:
                        texto += (f" {len(self._rejeicoes)} candidato(s) rejeitado(s) — veja "
                                  f"'Diagnóstico'. Primeiro motivo: {self._rejeicoes[0]}")
                    self.lbl_status.configure(text=texto)
                    self._redesenhar()
                    self._salvar(silencioso=True)
                elif tipo == 'titulos_fim':
                    if not sessao_corresponde(self.app.estado, evento[1], evento[2]):
                        continue
                    antes, novos = evento[3], {p.id: p for p in evento[4]}
                    # An edit made while the title request ran always wins.
                    self.planos = [novos[p.id] if p.id in novos and p.como_dict() == antes[p.id]
                                   else p for p in self.planos]
                    self._tarefa_atual = None
                    self._redesenhar()
                    self._salvar(silencioso=True)
                    self.lbl_status.configure(text="Títulos atualizados. Confira os campos e avisos de revisão.")
                elif tipo == "ia_erro":
                    if sessao_corresponde(self.app.estado, evento[1], evento[2]):
                        self._tarefa_atual = None
                        self.lbl_status.configure(text=f"Análise indisponível: {evento[3]}")
                elif tipo == "progresso":
                    self.barra.set(evento[2] / 100)
                    if self._total and evento[1]:
                        self.barra_geral.set((self._concluidos + evento[2] / 100) / self._total)
                    self.lbl_status.configure(text=evento[3])
                elif tipo == "previa_fim":
                    self._tarefa_atual = None
                    resultado = evento[1]
                    if resultado.get("sucesso"):
                        estado = "primeiro frame OK" if resultado.get("valida") else "PRIMEIRO FRAME INVÁLIDO"
                        self.lbl_status.configure(text=f"Prévia pronta ({estado}).")
                        os.startfile(resultado["caminho_saida"])
                    else:
                        self.lbl_status.configure(text=f"Falha na prévia: {resultado.get('erro')}")
                elif tipo == "capas_fim":
                    self._tarefa_atual = None
                    self.lbl_status.configure(text="Escolha uma das 3 capas.")
                    self._escolher_capa(evento[1], evento[2])
                elif tipo == "aviso":
                    self._tarefa_atual = None
                    self.lbl_status.configure(text=evento[1])
                elif tipo == "fim":
                    plano = next((p for p in self.planos if p.id == evento[1]), None)
                    resultado = evento[2]
                    self._concluidos += 1
                    if plano:
                        if resultado.get("sucesso"):
                            plano.status = "concluído"
                            contexto = montar_contexto(plano,self.app.estado.get("transcricao"))
                            registrar_uso(plano.titulo,contexto)
                            rel = resultado.get("relatorio", {})
                            self.lbl_status.configure(text=(
                                f"{Path(resultado['caminho_saida']).name}: {rel.get('resolucao')} · "
                                f"{rel.get('fps')} FPS · {rel.get('bitrate_medio_kbps')} kbps · "
                                f"{rel.get('codec_video')}/{rel.get('codec_audio')} · {rel.get('duracao')}s · "
                                f"{rel.get('tamanho_mb')} MB"))
                        else:
                            if resultado.get("revisao_titulo"):
                                marcar_revisao_titulo(plano)
                            else:
                                plano.status = "revisão" if resultado.get("revisao") else "erro"
                                plano.erro = resultado.get("erro", "")
                            self._revisao.append(plano.id)
                        self._atualizar_status(plano)
                    self.barra_geral.set(self._concluidos / max(1, self._total))
                    self._proximo()
        except queue.Empty:
            pass
        except tk.TclError:
            return
        if self.winfo_exists():
            self._after_id = self.after(100, self._processar_eventos)
