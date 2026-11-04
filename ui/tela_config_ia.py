"""Configuração segura de três providers; a rede só é usada por clique explícito."""

import queue
import logging
import threading
from time import perf_counter

import customtkinter as ctk

from modules.ai.factory import obter_provider
from modules.ai.diagnostico_gemini import categoria_erro
from modules.ai.fallback import ultima_analise
from modules.config_ia import carregar_config_ia, salvar_config_ia
from modules.credenciais import obter_chave, salvar_chave, diagnosticar_chave_groq
from ui.app import (COR_BRANCO, COR_CINZA_CLARO, COR_ROXO_CLARO, COR_VERDE,
                    COR_VERMELHO)


PROVIDERS = {"gemini": "Gemini", "mistral": "Mistral", "groq": "Groq"}
logger = logging.getLogger(__name__)


def mensagem_erro(erro: Exception) -> str:
    """Não exibe o texto bruto do SDK, que pode conter uma chave ou requisição."""
    codigo = getattr(erro, "codigo", None) or getattr(erro, "status_code", None) or getattr(erro, "code", None)
    if codigo in (401, 403):
        return "Chave inválida ou sem permissão para este modelo (401/403)."
    if codigo == 429:
        return "Limite de chamadas atingido (429). Aguarde antes de tentar novamente."
    if codigo == 404:
        return "Modelo indisponível para esta conta. Escolha outro modelo."
    if codigo == 408 or isinstance(erro, TimeoutError) or any("timeout" in c.__name__.lower() for c in type(erro).__mro__):
        return "O tempo limite da conexão foi excedido. Tente novamente mais tarde."
    if codigo in (400, 422):
        return "O serviço recusou o teste. Verifique a chave e a compatibilidade do modelo."
    return "Não foi possível concluir a conexão. Verifique a rede, a credencial e o modelo."


class TelaConfigIA(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.config = carregar_config_ia()
        self._eventos = queue.Queue()
        self._ocupada = False
        self._ultimos_testes = {}
        self._mensagens = {}
        self._versoes_chaves = dict.fromkeys(PROVIDERS, 0)
        self._credenciais = {}
        self._modelos_locais = {p: [getattr(self.config, f"modelo_{p}")] for p in PROVIDERS}
        self.entries_chaves = {}
        self.combos_modelos = {}
        self.labels_credenciais = {}
        self.caixas_providers = {}
        # Só consulta o cofre/ambiente. Nenhum cliente de API é criado ao abrir.
        for provider in PROVIDERS:
            self._ler_status_credencial(provider)
        self._construir()
        self._atualizar_status()
        self.after(100, self._processar_eventos)

    def _construir(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        caixa = ctk.CTkScrollableFrame(self, fg_color="#211536", corner_radius=12)
        caixa.grid(row=0, column=0, padx=24, pady=20, sticky="nsew")
        caixa.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(caixa, text="Configurações de IA",
                     font=ctk.CTkFont(size=25, weight="bold"), text_color=COR_BRANCO
                     ).grid(row=0, column=0, padx=18, pady=(14, 12), sticky="w")
        seletor = ctk.CTkFrame(caixa, fg_color="transparent")
        seletor.grid(row=1, column=0, padx=18, pady=6, sticky="ew")
        ctk.CTkLabel(seletor, text="Provedor de IA:").grid(row=0, column=0, padx=(0, 12))
        self.combo_provider = ctk.CTkComboBox(
            seletor, values=list(PROVIDERS.values()), state="readonly", command=self._trocar_provider)
        self.combo_provider.set(PROVIDERS[self.config.provider_ativo])
        self.combo_provider.grid(row=0, column=1)
        for linha, (provider, nome) in enumerate(PROVIDERS.items(), 2):
            card = ctk.CTkFrame(caixa, fg_color="#2D1B4E", corner_radius=8,
                                border_color=COR_ROXO_CLARO)
            card.grid(row=linha, column=0, padx=18, pady=6, sticky="ew")
            card.grid_columnconfigure(1, weight=1)
            self.caixas_providers[provider] = card
            ctk.CTkLabel(card, text=f"{nome} API Key:").grid(row=0, column=0, padx=12, pady=(12, 6), sticky="w")
            entry = ctk.CTkEntry(card, show="•", placeholder_text="Cole uma nova chave para salvar")
            entry.grid(row=0, column=1, padx=8, pady=(12, 6), sticky="ew")
            self.entries_chaves[provider] = entry
            ctk.CTkButton(card, text="Salvar chave", fg_color=COR_ROXO_CLARO,
                          command=lambda p=provider: self._salvar_chave(p)
                          ).grid(row=0, column=2, padx=12, pady=(12, 6))
            ctk.CTkLabel(card, text=f"Modelo {nome}:").grid(row=1, column=0, padx=12, pady=(6, 12), sticky="w")
            combo = ctk.CTkComboBox(card, values=self._modelos_locais[provider],
                                    command=lambda valor, p=provider: self._modelo_alterado(p, valor))
            combo.set(getattr(self.config, f"modelo_{provider}"))
            combo.grid(row=1, column=1, padx=8, pady=(6, 12), sticky="ew")
            combo.bind("<FocusOut>", lambda _event, p=provider: self._modelo_alterado(p))
            combo.bind("<Return>", lambda _event, p=provider: self._modelo_alterado(p))
            self.combos_modelos[provider] = combo
            label = ctk.CTkLabel(card, text="", text_color=COR_CINZA_CLARO)
            label.grid(row=1, column=2, padx=12, pady=(6, 12))
            self.labels_credenciais[provider] = label
        acoes = ctk.CTkFrame(caixa, fg_color="transparent")
        acoes.grid(row=5, column=0, padx=18, pady=8, sticky="ew")
        self.btn_modelos = ctk.CTkButton(acoes, text="Atualizar modelos", command=self._atualizar_modelos)
        self.btn_modelos.grid(row=0, column=0, padx=(0, 10))
        self.btn_testar = ctk.CTkButton(acoes, text="Testar conexão", command=self._testar)
        self.btn_testar.grid(row=0, column=1)
        ctk.CTkButton(acoes, text="Comparar IAs", command=lambda: self.app.navegar("comparar_ias")
                      ).grid(row=0, column=2, padx=10)
        preferencias = ctk.CTkFrame(caixa, fg_color="transparent")
        preferencias.grid(row=6, column=0, padx=18, pady=8, sticky="ew")
        ctk.CTkLabel(preferencias, text="Máx. chamadas/episódio:").grid(row=0, column=0, padx=(0, 12))
        self.entry_limite = ctk.CTkEntry(preferencias, width=90)
        self.entry_limite.insert(0, str(self.config.max_chamadas_episodio))
        self.entry_limite.grid(row=0, column=1, padx=(0, 12))
        ctk.CTkButton(preferencias, text="Salvar preferências", fg_color=COR_VERDE,
                      command=self._salvar_config).grid(row=0, column=2)
        self.check_fallback = ctk.CTkCheckBox(preferencias, text="Ativar fallback automático")
        self.check_fallback.grid(row=1, column=0, columnspan=3, pady=8, sticky="w")
        if self.config.fallback_automatico:
            self.check_fallback.select()
        self.check_auth = ctk.CTkCheckBox(preferencias, text="Fallback também em erro de autenticação")
        self.check_auth.grid(row=2, column=0, columnspan=3, pady=8, sticky="w")
        if self.config.fallback_autenticacao:
            self.check_auth.select()
        ctk.CTkLabel(preferencias, text="Provider preferido:").grid(row=3, column=0, sticky="w")
        self.combo_preferido = ctk.CTkComboBox(
            preferencias, values=["Ordem padrão", "Mistral", "Groq", "Gemini"],
            state="readonly", command=self._trocar_preferido)
        self.combo_preferido.set(PROVIDERS.get(self.config.provider_preferido, "Ordem padrão"))
        self.combo_preferido.grid(row=3, column=1, columnspan=2, pady=8, sticky="w")
        self.lbl_ordem = ctk.CTkLabel(preferencias, text="")
        self.lbl_ordem.grid(row=4, column=0, columnspan=3, sticky="w")
        self._atualizar_ordem()
        self.lbl_analise = ctk.CTkLabel(preferencias, text="", justify="left", wraplength=700)
        self.lbl_analise.grid(row=5, column=0, columnspan=3, pady=8, sticky="w")
        self._atualizar_ultima_analise()
        self.lbl_status = ctk.CTkLabel(caixa, text="", justify="left", text_color=COR_CINZA_CLARO)
        self.lbl_status.grid(row=7, column=0, padx=18, pady=(8, 4), sticky="w")
        self.lbl_mensagem = ctk.CTkLabel(caixa, text="", justify="left", wraplength=700,
                                         text_color=COR_CINZA_CLARO)
        self.lbl_mensagem.grid(row=8, column=0, padx=18, pady=4, sticky="w")
        ctk.CTkLabel(caixa, justify="left", wraplength=700, text_color=COR_CINZA_CLARO,
                     text=("As chaves salvas ficam no Gerenciador de Credenciais do Windows.\n"
                           "Atualizar modelos e Testar conexão usam somente o provedor ativo.\n"
                           "O teste envia apenas ‘Responda apenas OK’ e usa uma pequena parte da cota."
                           " Salve as preferências para aplicar o provedor e os modelos escolhidos.")
                     ).grid(row=9, column=0, padx=18, pady=(8, 16), sticky="w")

    def _ler_status_credencial(self, provider):
        try:
            self._credenciais[provider] = "configurada" if obter_chave(provider) else "não configurada"
        except Exception:
            self._credenciais[provider] = "cofre indisponível"

    def _sincronizar_modelos(self):
        for provider, combo in self.combos_modelos.items():
            setattr(self.config, f"modelo_{provider}", combo.get().strip())

    def _trocar_provider(self, nome):
        self._sincronizar_modelos()
        self.config.provider_ativo = next(p for p, label in PROVIDERS.items() if label == nome)
        self._atualizar_status()

    def _modelo_alterado(self, provider, valor=None):
        setattr(self.config, f"modelo_{provider}", (valor if valor is not None else self.combos_modelos[provider].get()).strip())
        self._atualizar_status()

    def _atualizar_status(self):
        ativo = self.config.provider_ativo
        self.entry_chave = self.entries_chaves[ativo]
        self.combo_modelo = self.combos_modelos[ativo]
        for provider, label in self.labels_credenciais.items():
            label.configure(text=f"Credencial: {self._credenciais[provider]}")
            self.caixas_providers[provider].configure(border_width=2 if provider == ativo else 0)
        teste = self._ultimos_testes.get(ativo)
        cor = COR_CINZA_CLARO
        ultimo = "ainda não realizado para este modelo"
        if teste and teste["modelo"] == self.config.modelo:
            ultimo = f"{'OK' if teste['ok'] else 'FALHA'} — {teste['latency_ms']:.0f} ms"
            cor = COR_VERDE if teste["ok"] else COR_VERMELHO
        diagnostico = ""
        if ativo == "groq":
            try:
                info = diagnosticar_chave_groq()
                diagnostico = (f"\nCredencial encontrada: {'sim' if info['encontrada'] else 'não'}"
                               f" | comprimento: {info['comprimento']}"
                               f"\nPrefixo mascarado: {info['prefixo_mascarado']} | origem: {info['origem']}")
            except Exception:
                diagnostico = "\nDiagnóstico Groq: cofre de credenciais indisponível."
        self.lbl_status.configure(
            text=f"Provider atual: {PROVIDERS[ativo]}\nModelo: {self.config.modelo}\n"
                 f"Credencial: {self._credenciais[ativo]}\nÚltimo teste: {ultimo}{diagnostico}", text_color=cor)
        self.lbl_mensagem.configure(text=self._mensagens.get(ativo, ""))

    def _salvar_chave(self, provider=None):
        provider = provider or self.config.provider_ativo
        entry = self.entries_chaves[provider]
        try:
            salvar_chave(provider, entry.get())
            entry.delete(0, "end")
            self._versoes_chaves[provider] += 1
            self._ultimos_testes.pop(provider, None)
            self._credenciais[provider] = "configurada"
            self._mensagens[provider] = "Chave salva com segurança. Nenhuma conexão foi testada."
            self._atualizar_status()
        except ValueError:
            self.app.mostrar_erro("Salvar chave", "Informe uma chave de API válida.")
        except Exception:
            self.app.mostrar_erro("Salvar chave", "Não foi possível salvar a chave no cofre do Windows.")

    def _ler_config(self):
        self._sincronizar_modelos()
        self.config.fallback_automatico = bool(self.check_fallback.get())
        self.config.fallback_autenticacao = bool(self.check_auth.get())
        self._trocar_preferido(self.combo_preferido.get())
        limite = self.entry_limite.get().strip()
        if not limite.isdecimal():
            raise ValueError("O limite de chamadas deve ser um número inteiro entre 1 e 20.")
        self.config.max_chamadas_episodio = int(limite)
        return self.config.validar()

    def _trocar_preferido(self, nome):
        self.config.provider_preferido = next((p for p, label in PROVIDERS.items() if label == nome), None)
        self._atualizar_ordem()

    def _atualizar_ordem(self):
        ordem = list(self.config.ordem_fallback)
        if self.config.provider_preferido:
            ordem.remove(self.config.provider_preferido)
            ordem.insert(0, self.config.provider_preferido)
        self.lbl_ordem.configure(text="Ordem de fallback: " + " → ".join(PROVIDERS[p] for p in ordem))

    def _atualizar_ultima_analise(self):
        self.lbl_analise.configure(text="Última análise: " + ultima_analise())

    def _salvar_config(self):
        try:
            salvar_config_ia(self._ler_config())
            self._mensagens[self.config.provider_ativo] = "Preferências salvas."
            self._atualizar_status()
        except (ValueError, OSError) as erro:
            # Validação local; não usa chaves nem mensagens de SDK.
            self.app.mostrar_erro("Configurações de IA", str(erro))

    def _atualizar_modelos(self):
        self._iniciar("modelos")

    def _testar(self):
        self._iniciar("teste")

    def _iniciar(self, operacao):
        if self._ocupada:
            return
        self._sincronizar_modelos()
        provider, modelo = self.config.provider_ativo, self.config.modelo
        try:
            if not obter_chave(provider):
                self.app.mostrar_erro(PROVIDERS[provider], "Configure e salve a chave deste provedor antes de continuar.")
                self._ler_status_credencial(provider)
                self._atualizar_status()
                return
        except Exception:
            self.app.mostrar_erro(PROVIDERS[provider], "Não foi possível acessar o cofre de credenciais do Windows.")
            return
        if operacao == "teste" and not modelo:
            self.app.mostrar_erro("Testar conexão", "Selecione um modelo antes de testar.")
            return
        self._ocupada = True
        self.btn_testar.configure(state="disabled")
        self.btn_modelos.configure(state="disabled")
        self._mensagens[provider] = "Testando conexão…" if operacao == "teste" else "Atualizando modelos…"
        self._atualizar_status()
        threading.Thread(target=self._worker,
                         args=(operacao, provider, modelo, self._versoes_chaves[provider]), daemon=True).start()

    def _worker(self, operacao, provider, modelo, versao):
        inicio = perf_counter()
        evento = {"operacao": operacao, "provider": provider, "modelo": modelo, "versao": versao}
        try:
            cliente = obter_provider(provider)
            if operacao == "modelos":
                evento.update(ok=True, modelos=cliente.listar_modelos())
            else:
                resultado = cliente.testar_conexao(modelo=modelo)
                ok = resultado is True or (isinstance(resultado, dict) and resultado.get("ok") is True)
                evento.update(ok=ok, erro="" if ok else "O modelo não confirmou o teste com OK.")
        except Exception as erro:
            evento.update(ok=False, erro=mensagem_erro(erro))
            if provider == "gemini" and operacao == "teste":
                evento["categoria"] = getattr(erro, "categoria", None) or categoria_erro(erro)
        evento["latency_ms"] = max(0.0, (perf_counter() - inicio) * 1000)
        if provider == "gemini" and operacao == "teste":
            categoria = evento.setdefault("categoria", "sucesso" if evento["ok"] else "resposta inválida")
            diagnostico = (f"Provider: Gemini | Modelo: {modelo} | "
                           f"Latência: {evento['latency_ms']:.0f} ms | Categoria: {categoria}")
            if not evento["ok"]:
                evento["erro"] += "\n" + diagnostico
            logger.info("Teste de conexão: %s", diagnostico)
        self._eventos.put(evento)

    def _processar_eventos(self):
        try:
            while True:
                evento = self._eventos.get_nowait()
                self._ocupada = False
                self.btn_testar.configure(state="normal")
                self.btn_modelos.configure(state="normal")
                provider = evento["provider"]
                if evento["versao"] != self._versoes_chaves[provider]:
                    continue
                if evento["operacao"] == "teste":
                    self._ultimos_testes[provider] = evento
                    self._mensagens[provider] = "Conexão confirmada." if evento["ok"] else evento["erro"]
                elif evento["ok"] and evento.get("modelos"):
                    atual = self.combos_modelos[provider].get().strip()
                    valores = list(dict.fromkeys([atual, *evento["modelos"]]))
                    self._modelos_locais[provider] = valores
                    self.combos_modelos[provider].configure(values=valores)
                    self.combos_modelos[provider].set(atual)
                    self._mensagens[provider] = f"{len(evento['modelos'])} modelos disponíveis. A seleção atual foi mantida."
                else:
                    detalhe = evento.get("erro", "Nenhum modelo retornado pelo serviço.")
                    self._mensagens[provider] = f"Lista local/configurada mantida. {detalhe}"
                self._atualizar_status()
        except queue.Empty:
            pass
        self._atualizar_ultima_analise()
        if self.winfo_exists():
            self.after(100, self._processar_eventos)
