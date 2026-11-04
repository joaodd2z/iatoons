"""Tela de configuração persistente do preset audiovisual IA TOONS 4K."""

import customtkinter as ctk

from modules.processamento_av import AjustesAV, salvar_ajustes
from ui.app import COR_BRANCO, COR_CINZA_CLARO, COR_ROXO_CLARO, COR_VERDE


class TelaAjustes(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.cfg = app.estado.get("ajustes_av") or AjustesAV()
        self._vars = {}
        self._entries = {}
        self._construir()

    def _construir(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        area = ctk.CTkScrollableFrame(self, fg_color="transparent")
        area.grid(row=0, column=0, padx=40, pady=25, sticky="nsew")
        area.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(area, text="Ajustes avançados — IA TOONS 4K",
                     font=ctk.CTkFont(size=24, weight="bold"), text_color=COR_BRANCO).grid(
                         row=0, column=0, sticky="w", pady=(0, 8))
        ctk.CTkLabel(
            area,
            text="Preset inspirado no acabamento 4K usado no CapCut; não reproduz algoritmos proprietários nem recupera detalhes inexistentes.",
            text_color=COR_CINZA_CLARO,
        ).grid(row=1, column=0, sticky="w", pady=(0, 16))

        imagem = ctk.CTkFrame(area, fg_color="#211536", corner_radius=12)
        imagem.grid(row=2, column=0, sticky="ew", pady=(0, 14), ipadx=16, ipady=12)
        self._booleano(imagem, "imagem_ativa", "Ativar tratamento de imagem", 0)
        self._numero(imagem, "intensidade", "Intensidade geral (%)", 1)
        self._booleano(imagem, "alta_qualidade", "Redimensionamento Lanczos de alta qualidade", 2)
        self._ajuste_duplo(imagem, "nitidez_ativa", "nitidez", "Nitidez / unsharp (0–2)", 3)
        self._ajuste_duplo(imagem, "contraste_ativo", "contraste", "Contraste (0,5–1,5)", 4)
        self._ajuste_duplo(imagem, "saturacao_ativa", "saturacao", "Saturação (0–2)", 5)
        self._ajuste_duplo(imagem, "brilho_ativo", "brilho", "Brilho (-0,3–0,3)", 6)
        self._ajuste_duplo(imagem, "ruido_ativo", "reducao_ruido", "Redução de ruído (0–6)", 7)

        audio = ctk.CTkFrame(area, fg_color="#211536", corner_radius=12)
        audio.grid(row=3, column=0, sticky="ew", pady=(0, 14), ipadx=16, ipady=12)
        self._booleano(audio, "audio_ativo", "Ativar rampa e proteção de áudio", 0)
        self._numero(audio, "volume_inicial", "Volume inicial (1,0 = original)", 1)
        self._numero(audio, "volume_final", "Volume final", 2)
        self._numero(audio, "duracao_rampa", "Duração da curva (s)", 3)
        self._numero(audio, "limiter", "Limiter / teto anticlipping", 4)

        botoes = ctk.CTkFrame(area, fg_color="transparent")
        botoes.grid(row=4, column=0, sticky="ew")
        ctk.CTkButton(botoes, text="Restaurar IA TOONS 4K", fg_color="#3E2661",
                      command=self._restaurar).pack(side="left")
        ctk.CTkButton(botoes, text="Salvar preset", fg_color=COR_VERDE,
                      command=self._salvar).pack(side="right")
        self.lbl_status = ctk.CTkLabel(area, text="", text_color=COR_CINZA_CLARO)
        self.lbl_status.grid(row=5, column=0, sticky="w", pady=(10, 0))

    def _booleano(self, pai, campo, texto, linha):
        var = ctk.BooleanVar(value=bool(getattr(self.cfg, campo)))
        self._vars[campo] = var
        ctk.CTkCheckBox(pai, text=texto, variable=var).grid(
            row=linha, column=0, columnspan=2, sticky="w", padx=12, pady=6)

    def _numero(self, pai, campo, texto, linha):
        ctk.CTkLabel(pai, text=texto).grid(row=linha, column=0, sticky="w", padx=12, pady=5)
        entrada = ctk.CTkEntry(pai, width=110)
        entrada.insert(0, str(getattr(self.cfg, campo)))
        entrada.grid(row=linha, column=1, sticky="w", padx=8)
        self._entries[campo] = entrada

    def _ajuste_duplo(self, pai, campo_bool, campo_num, texto, linha):
        var = ctk.BooleanVar(value=bool(getattr(self.cfg, campo_bool)))
        self._vars[campo_bool] = var
        ctk.CTkCheckBox(pai, text=texto, variable=var).grid(row=linha, column=0, sticky="w", padx=12, pady=5)
        entrada = ctk.CTkEntry(pai, width=110)
        entrada.insert(0, str(getattr(self.cfg, campo_num)))
        entrada.grid(row=linha, column=1, sticky="w", padx=8)
        self._entries[campo_num] = entrada

    def _ler(self):
        dados = {campo: var.get() for campo, var in self._vars.items()}
        for campo, entrada in self._entries.items():
            dados[campo] = int(entrada.get()) if campo == "intensidade" else float(entrada.get().replace(",", "."))
        return AjustesAV(**dados).validar()

    def _salvar(self):
        try:
            self.cfg = self._ler()
            caminho = salvar_ajustes(self.cfg)
            self.app.atualizar_estado("ajustes_av", self.cfg)
            self.lbl_status.configure(text=f"Preset salvo em {caminho.name}. A próxima prévia e exportação usarão estes valores.", text_color=COR_VERDE)
        except (ValueError, OSError) as erro:
            self.app.mostrar_erro("Ajustes inválidos", str(erro))

    def _restaurar(self):
        self.cfg = AjustesAV()
        for campo, var in self._vars.items():
            var.set(getattr(self.cfg, campo))
        for campo, entrada in self._entries.items():
            entrada.delete(0, "end")
            entrada.insert(0, str(getattr(self.cfg, campo)))
        self.lbl_status.configure(text="Valores padrão restaurados; clique em Salvar preset para aplicar.")
