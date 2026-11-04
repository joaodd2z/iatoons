"""Estúdio de Capa: capa no padrão da agência, com prévia ao vivo.
Escolhe a conta, abre um corte pronto, ajusta texto/cores/posição/selo e o quadro do vídeo, e então:
salva a capa deste vídeo, salva o MODELO da conta (a fábrica passa a usar sozinha) ou aplica numa pasta inteira.
Também cria o visual de uma conta nova (outro desenho/nicho) a partir de uma imagem de fundo."""
import json
import queue
import threading
from pathlib import Path
from tkinter import colorchooser, filedialog

import customtkinter as ctk
from PIL import Image

from modules import capa_estudio as CE
from modules import fabrica as F
from modules import preset as P
from modules.perfis_visuais import PERFIS_DIR, obter_perfil
from ui.particulas import FaixaParticulas
from ui.tela_fabrica import AMARELO, BORDA, BRANCO, CARD, CINZA, CLARO, CONTAS, EXEMPLO, HOVER, OURO, ROXO, VERDE

PREVIA_W, PREVIA_H = 315, 560


class TelaCapa(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self._eventos: queue.Queue = queue.Queue()
        self._video: Path | None = None
        self._duracao = 0.0
        self._quadro_cache: dict[float, Image.Image] = {}
        self._agendado = None
        if not CONTAS.is_file() and EXEMPLO.is_file():
            CONTAS.write_text(EXEMPLO.read_text(encoding="utf-8"), encoding="utf-8")
        try:
            self._cfg = F.carregar_config(CONTAS)
        except (OSError, ValueError):
            self._cfg = F.ConfigFabrica(pasta_base=Path("C:/IA_TOONS_FABRICA"), contas=[])
        self._contas = {f"{c.nome} ({c.id})": c for c in self._cfg.contas} or {"Padrão (toonmix)": F.Conta(id="toonmix")}
        self._construir()
        self._trocar_conta(next(iter(self._contas)))
        self.after(150, self._processar_eventos)

    # ─── layout ──────────────────────────────────────────────────────────────
    def _construir(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        FaixaParticulas(self, altura=86, fundo=ROXO, titulo="ESTÚDIO DE CAPA",
                        subtitulo="a capa no padrão da agência · um modelo por conta · em lote para o dia inteiro",
                        tam_titulo=22, quantidade=45).grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 4))

        corpo = ctk.CTkFrame(self, fg_color="transparent")
        corpo.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 12))
        corpo.grid_columnconfigure(0, weight=1)
        corpo.grid_rowconfigure(0, weight=1)

        # coluna de controles (rolável) + prévia fixa à direita
        ctrl = ctk.CTkScrollableFrame(corpo, fg_color="transparent")
        ctrl.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        ctrl.grid_columnconfigure(0, weight=1)

        lado = ctk.CTkFrame(corpo, fg_color=CARD, corner_radius=14, border_width=1, border_color=BORDA)
        lado.grid(row=0, column=1, sticky="ns")
        ctk.CTkLabel(lado, text="Prévia da capa", font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=BRANCO).pack(pady=(12, 6))
        self.lbl_previa = ctk.CTkLabel(lado, text="Abra um vídeo\npara ver a capa", width=PREVIA_W, height=PREVIA_H,
                                       fg_color="#120A22", corner_radius=10, text_color=CINZA)
        self.lbl_previa.pack(padx=14)
        self.lbl_info = ctk.CTkLabel(lado, text="", text_color=CINZA, font=ctk.CTkFont(size=11), wraplength=PREVIA_W)
        self.lbl_info.pack(pady=(6, 12))

        # 1) conta e vídeo
        c1 = self._card(ctrl, 0, "1. Conta e vídeo")
        linha = ctk.CTkFrame(c1, fg_color="transparent")
        linha.pack(fill="x", padx=14, pady=(0, 8))
        self.op_conta = ctk.CTkOptionMenu(linha, values=list(self._contas), width=260, fg_color=BORDA,
                                          button_color=CLARO, command=self._trocar_conta)
        self.op_conta.pack(side="left")
        ctk.CTkButton(linha, text="Abrir vídeo…", width=120, fg_color=CLARO, hover_color=HOVER,
                      command=self._abrir_video).pack(side="left", padx=8)
        self.lbl_video = ctk.CTkLabel(c1, text="Nenhum vídeo aberto. Abra qualquer corte pronto (.mp4).",
                                      text_color=CINZA, anchor="w", font=ctk.CTkFont(size=12))
        self.lbl_video.pack(fill="x", padx=14, pady=(0, 6))
        self.momento = ctk.CTkSlider(c1, from_=0, to=1, number_of_steps=200, command=lambda v: self._mudou(quadro=True),
                                     progress_color=OURO, button_color=OURO)
        self.momento.set(0.3)
        self.momento.pack(fill="x", padx=14, pady=(2, 2))
        linha2 = ctk.CTkFrame(c1, fg_color="transparent")
        linha2.pack(fill="x", padx=14, pady=(0, 12))
        self.lbl_momento = ctk.CTkLabel(linha2, text="Quadro do vídeo", text_color=CINZA, font=ctk.CTkFont(size=11))
        self.lbl_momento.pack(side="left")
        ctk.CTkButton(linha2, text="✨ Melhor quadro", width=130, fg_color=BORDA, hover_color=CLARO,
                      command=self._melhor_quadro).pack(side="right")
        self.var_auto = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(linha2, text="Nos outros vídeos, escolher o quadro sozinho", variable=self.var_auto,
                        command=self._mudou, fg_color=CLARO).pack(side="right", padx=10)

        # 2) texto
        c2 = self._card(ctrl, 1, "2. Texto da capa")
        self.ent_texto = ctk.CTkEntry(c2, placeholder_text="Título da capa (vem do título do corte)", height=36)
        self.ent_texto.pack(fill="x", padx=14, pady=(0, 8))
        self.ent_texto.bind("<KeyRelease>", lambda e: self._mudou())
        g = ctk.CTkFrame(c2, fg_color="transparent")
        g.pack(fill="x", padx=14, pady=(0, 12))
        ctk.CTkLabel(g, text="Posição", text_color=CINZA).grid(row=0, column=0, sticky="w")
        self.op_pos = ctk.CTkSegmentedButton(g, values=list(CE.POSICOES.values()), command=lambda v: self._mudou(),
                                             selected_color=CLARO)
        self.op_pos.grid(row=0, column=1, columnspan=3, sticky="w", padx=8, pady=4)
        ctk.CTkLabel(g, text="Tamanho máx.", text_color=CINZA).grid(row=1, column=0, sticky="w")
        self.sl_tam = ctk.CTkSlider(g, from_=70, to=190, number_of_steps=24, command=lambda v: self._mudou(), width=200)
        self.sl_tam.grid(row=1, column=1, sticky="w", padx=8, pady=4)
        ctk.CTkLabel(g, text="Palavras", text_color=CINZA).grid(row=1, column=2, sticky="e")
        self.op_pal = ctk.CTkOptionMenu(g, values=["todas", "4", "5", "6", "8", "10"], width=80, fg_color=BORDA,
                                        button_color=CLARO, command=lambda v: self._mudou())
        self.op_pal.grid(row=1, column=3, sticky="w", padx=8)
        self.var_caixa = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(g, text="LETRAS MAIÚSCULAS", variable=self.var_caixa, command=self._mudou,
                        fg_color=CLARO).grid(row=2, column=1, sticky="w", padx=8, pady=4)

        # 3) cores
        c3 = self._card(ctrl, 2, "3. Cores, tarja e selo")
        g3 = ctk.CTkFrame(c3, fg_color="transparent")
        g3.pack(fill="x", padx=14, pady=(0, 12))
        self._botoes_cor = {}
        for i, (campo, rotulo) in enumerate([("cor", "Cor do texto"), ("cor_contorno", "Contorno"),
                                             ("cor_faixa", "Tarja"), ("cor_selo", "Selo")]):
            b = ctk.CTkButton(g3, text=rotulo, width=118, height=34, command=lambda c=campo: self._escolher_cor(c))
            b.grid(row=0, column=i, padx=(0, 8), pady=4)
            self._botoes_cor[campo] = b
        ctk.CTkLabel(g3, text="Contorno", text_color=CINZA).grid(row=1, column=0, sticky="w")
        self.sl_cont = ctk.CTkSlider(g3, from_=0, to=18, number_of_steps=18, command=lambda v: self._mudou(), width=160)
        self.sl_cont.grid(row=1, column=1, columnspan=2, sticky="w", pady=4)
        self.var_faixa = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(g3, text="Tarja atrás do texto", variable=self.var_faixa, command=self._mudou,
                        fg_color=CLARO).grid(row=1, column=3, sticky="w")
        ctk.CTkLabel(g3, text="Selo (ex.: PARTE 1)", text_color=CINZA).grid(row=2, column=0, columnspan=2, sticky="w")
        self.ent_selo = ctk.CTkEntry(g3, placeholder_text="vazio = sem selo", width=200)
        self.ent_selo.grid(row=2, column=2, columnspan=2, sticky="w", pady=4)
        self.ent_selo.bind("<KeyRelease>", lambda e: self._mudou())

        # 4) salvar
        c4 = self._card(ctrl, 3, "4. Salvar")
        ctk.CTkLabel(c4, text="O modelo vale para a conta inteira: a Fábrica passa a fazer todas as capas desse jeito, "
                              "sozinha. A capa deste vídeo pode ter texto próprio.",
                     text_color=CINZA, wraplength=560, justify="left", font=ctk.CTkFont(size=12)
                     ).pack(fill="x", padx=14, pady=(0, 8))
        b4 = ctk.CTkFrame(c4, fg_color="transparent")
        b4.pack(fill="x", padx=14, pady=(0, 14))
        ctk.CTkButton(b4, text="⭐ Salvar como modelo da conta", height=40, fg_color=CLARO, hover_color=HOVER,
                      font=ctk.CTkFont(size=13, weight="bold"), command=self._salvar_modelo).pack(side="left")
        ctk.CTkButton(b4, text="💾 Salvar capa deste vídeo", height=40, fg_color=BORDA, hover_color=CLARO,
                      command=self._salvar_capa).pack(side="left", padx=8)
        ctk.CTkButton(b4, text="📂 Aplicar numa pasta inteira…", height=40, fg_color=BORDA, hover_color=CLARO,
                      command=self._aplicar_pasta).pack(side="left")

        # 5) visual de outro conteúdo
        c5 = self._card(ctrl, 4, "5. Outro desenho ou nicho (visual novo)")
        ctk.CTkLabel(c5, text="Para uma conta de outro conteúdo, escolha a imagem de fundo dela (1080x1920, com o "
                              "espaço do vídeo no meio, como o fundo TOONMIX). O visual aparece na lista de perfis "
                              "da Fábrica e da Esteira.",
                     text_color=CINZA, wraplength=560, justify="left", font=ctk.CTkFont(size=12)
                     ).pack(fill="x", padx=14, pady=(0, 8))
        b5 = ctk.CTkFrame(c5, fg_color="transparent")
        b5.pack(fill="x", padx=14, pady=(0, 14))
        self.ent_visual = ctk.CTkEntry(b5, placeholder_text="Nome do visual (ex.: SIMPSONS BR)", width=240)
        self.ent_visual.pack(side="left")
        ctk.CTkButton(b5, text="Escolher fundo e criar", fg_color=BORDA, hover_color=CLARO,
                      command=self._criar_visual).pack(side="left", padx=8)

    def _card(self, pai, linha, titulo):
        c = ctk.CTkFrame(pai, fg_color=CARD, corner_radius=12, border_width=1, border_color=BORDA)
        c.grid(row=linha, column=0, sticky="ew", pady=(0, 10))
        ctk.CTkLabel(c, text=titulo, font=ctk.CTkFont(size=14, weight="bold"), text_color=BRANCO,
                     anchor="w").pack(fill="x", padx=14, pady=(10, 6))
        return c

    # ─── modelo <-> controles ────────────────────────────────────────────────
    def _carregar_controles(self, m: CE.ModeloCapa):
        self._cores = {"cor": m.cor, "cor_contorno": m.cor_contorno, "cor_faixa": m.cor_faixa, "cor_selo": m.cor_selo}
        self.op_pos.set(CE.POSICOES.get(m.posicao, CE.POSICOES["topo"]))
        self.sl_tam.set(m.tamanho_max)
        self.op_pal.set("todas" if m.max_palavras <= 0 else str(m.max_palavras))
        self.var_caixa.set(m.caixa_alta)
        self.sl_cont.set(m.contorno)
        self.var_faixa.set(m.faixa)
        self.ent_selo.delete(0, "end")
        self.ent_selo.insert(0, m.selo)
        self.var_auto.set(m.momento < 0)
        if m.momento >= 0:
            self.momento.set(m.momento)
        self._pintar_botoes_cor()

    def _modelo(self, para_conta: bool = False) -> CE.ModeloCapa:
        pos = next((k for k, v in CE.POSICOES.items() if v == self.op_pos.get()), "topo")
        pal = self.op_pal.get()
        return CE.ModeloCapa(
            posicao=pos, max_palavras=0 if pal == "todas" else int(pal), caixa_alta=self.var_caixa.get(),
            cor=self._cores["cor"], cor_contorno=self._cores["cor_contorno"], contorno=int(self.sl_cont.get()),
            tamanho_max=int(self.sl_tam.get()), faixa=self.var_faixa.get(), cor_faixa=self._cores["cor_faixa"],
            selo=self.ent_selo.get().strip(), cor_selo=self._cores["cor_selo"],
            momento=-1.0 if (para_conta and self.var_auto.get()) else float(self.momento.get()),
        )

    def _pintar_botoes_cor(self):
        for campo, b in self._botoes_cor.items():
            cor = self._cores[campo]
            r, g, bb = (int(cor[i:i + 2], 16) for i in (1, 3, 5)) if len(cor) == 7 else (0, 0, 0)
            b.configure(fg_color=cor, hover_color=cor, text_color="#000000" if r * .3 + g * .59 + bb * .11 > 150 else "#FFFFFF")

    def _escolher_cor(self, campo):
        escolhida = colorchooser.askcolor(color=self._cores[campo], title="Escolha a cor")[1]
        if escolhida:
            self._cores[campo] = escolhida.upper()
            self._pintar_botoes_cor()
            self._mudou()

    # ─── ações ───────────────────────────────────────────────────────────────
    def _conta(self) -> F.Conta:
        return self._contas[self.op_conta.get()]

    def _trocar_conta(self, rotulo):
        self.op_conta.set(rotulo)
        self._carregar_controles(CE.carregar_modelo(self._conta().id))
        tem = CE.tem_modelo(self._conta().id)
        self._info("Modelo salvo desta conta carregado." if tem else "Esta conta ainda não tem modelo: mostrando o padrão TOONMIX.")
        self._mudou()

    def _abrir_video(self):
        inicial = self._cfg.para_postar / self._conta().id
        arq = filedialog.askopenfilename(title="Escolha um corte pronto", filetypes=[("Vídeo", "*.mp4 *.mov *.mkv")],
                                         initialdir=str(inicial if inicial.is_dir() else Path.home()))
        if not arq:
            return
        self._video = Path(arq)
        self._quadro_cache.clear()
        self._duracao = CE.duracao(self._video)
        self.lbl_video.configure(text=f"{self._video.name}  ·  {self._duracao:.0f} s", text_color=BRANCO)
        self.ent_texto.delete(0, "end")
        self.ent_texto.insert(0, CE.titulo_do_post(self._video))
        self._mudou(quadro=True)

    def _melhor_quadro(self):
        if not self._video:
            return self._info("Abra um vídeo primeiro.")
        self._info("Procurando o quadro mais forte…")
        video, perfil = self._video, self._conta().perfil

        def tarefa():
            try:
                t = CE.melhor_momento(video, CE.obter_perfil(perfil))
                self._eventos.put(("momento", t / self._duracao if self._duracao else 0.3))
            except Exception as e:  # noqa: BLE001 - mostra qualquer falha na tela
                self._eventos.put(("info", f"Não deu para escolher: {e}"))
        threading.Thread(target=tarefa, daemon=True).start()

    def _mudou(self, quadro: bool = False):
        if self._duracao:
            self.lbl_momento.configure(text=f"Quadro do vídeo: {self.momento.get() * self._duracao:.1f} s")
        if self._agendado:
            self.after_cancel(self._agendado)
        self._agendado = self.after(180 if quadro else 90, self._redesenhar)

    def _redesenhar(self):
        self._agendado = None
        if not self._video:
            return
        t = round(self.momento.get() * self._duracao, 1)
        try:
            if t not in self._quadro_cache:
                self._quadro_cache[t] = CE.quadro(self._video, t)
            capa = CE.compor(self._quadro_cache[t], obter_perfil(self._conta().perfil), self._modelo(), self.ent_texto.get())
        except Exception as e:  # noqa: BLE001
            return self._info(f"Erro na prévia: {e}")
        self._img = ctk.CTkImage(light_image=capa, dark_image=capa, size=(PREVIA_W, PREVIA_H))
        self.lbl_previa.configure(image=self._img, text="")

    def _salvar_modelo(self):
        destino = CE.salvar_modelo(self._conta().id, self._modelo(para_conta=True))
        self._info(f"Modelo salvo para {self._conta().nome}. A Fábrica já usa nas próximas agendas. ({destino.name})", VERDE)

    def _salvar_capa(self):
        if not self._video:
            return self._info("Abra um vídeo primeiro.")
        destino = self._video.with_name(self._video.stem + "_capa.jpg")
        t = self.momento.get() * self._duracao
        CE.gerar_capa(self._video, self._conta().perfil, self._modelo(), self.ent_texto.get(), destino, segundos=t)
        self._info(f"Capa salva: {destino.name}", VERDE)

    def _aplicar_pasta(self):
        inicial = self._cfg.para_postar / self._conta().id
        pasta = filedialog.askdirectory(title="Pasta com os vídeos (ex.: PARA_POSTAR/conta/data)",
                                        initialdir=str(inicial if inicial.is_dir() else Path.home()))
        if not pasta:
            return
        modelo, perfil = self._modelo(para_conta=True), self._conta().perfil
        self._info("Gerando as capas da pasta…")

        def tarefa():
            try:
                feitas = CE.aplicar_em_pasta(pasta, perfil, modelo,
                                             progresso=lambda i, n, nome: self._eventos.put(("info", f"Capa {i}/{n}: {nome}")))
                self._eventos.put(("ok", f"{len(feitas)} capas prontas na pasta, uma ao lado de cada vídeo."))
            except Exception as e:  # noqa: BLE001
                self._eventos.put(("info", f"Parou com erro: {e}"))
        threading.Thread(target=tarefa, daemon=True).start()

    def _criar_visual(self):
        nome = " ".join(self.ent_visual.get().split())
        if not nome:
            return self._info("Dê um nome para o visual primeiro.", AMARELO)
        arq = filedialog.askopenfilename(title="Imagem de fundo 1080x1920", filetypes=[("Imagem", "*.png *.jpg *.jpeg *.webp")])
        if not arq:
            return
        pid = F.slug(nome)
        destino_img = P.ASSETS_DIR / "fundos" / f"{pid}.png"
        destino_img.parent.mkdir(parents=True, exist_ok=True)
        img = Image.open(arq).convert("RGB")
        if img.size != (P.SAIDA_LARGURA, P.SAIDA_ALTURA):
            from modules.agencia.renderer import _cobrir
            img = _cobrir(img, P.SAIDA_LARGURA, P.SAIDA_ALTURA)
        img.save(destino_img)
        PERFIS_DIR.mkdir(parents=True, exist_ok=True)
        (PERFIS_DIR / f"{pid}.json").write_text(json.dumps(
            {"id": pid, "nome": nome.upper(), "nome_canal": nome.upper(), "fundo": f"fundos/{pid}.png"},
            ensure_ascii=False, indent=2), encoding="utf-8")
        self._info(f"Visual {nome.upper()} criado. Na Fábrica, escolha \"{pid}\" no perfil da conta.", VERDE)

    # ─── eventos ─────────────────────────────────────────────────────────────
    def _info(self, texto, cor=CINZA):
        self.lbl_info.configure(text=texto, text_color=cor)

    def _processar_eventos(self):
        try:
            while True:
                tipo, valor = self._eventos.get_nowait()
                if tipo == "momento":
                    self.momento.set(valor)
                    self._info("Quadro mais forte escolhido.", VERDE)
                    self._mudou(quadro=True)
                elif tipo == "ok":
                    self._info(valor, VERDE)
                else:
                    self._info(valor)
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(150, self._processar_eventos)
