"""Tela Fábrica: várias contas, produção em massa e agenda de postagem (meta 100 vídeos/dia).
Tudo aqui usa modules/fabrica.py, o mesmo motor do fabrica.py de linha de comando."""
import json
import os
import queue
import threading
from pathlib import Path

import customtkinter as ctk

from modules import fabrica as F
from ui.particulas import FaixaParticulas

RAIZ = Path(__file__).resolve().parent.parent
CONTAS = RAIZ / "contas.json"
EXEMPLO = RAIZ / "contas.exemplo.json"
META = 100

ROXO, CARD, BORDA, CLARO, HOVER = "#0E071A", "#20123A", "#331E58", "#7C3AED", "#8B5CF6"
BRANCO, CINZA, VERDE, AMARELO, VERMELHO, OURO = "#F8FAFC", "#CBD5E1", "#10B981", "#F59E0B", "#EF4444", "#F5C451"


def _abrir(p: Path):
    p.mkdir(parents=True, exist_ok=True)
    try:
        os.startfile(str(p))  # noqa: S606 - abre a pasta no Explorer (Windows)
    except (AttributeError, OSError):
        pass


class TelaFabrica(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self._eventos: queue.Queue = queue.Queue()
        self._ocupada = False
        self._linhas: dict[str, dict] = {}
        if not CONTAS.is_file() and EXEMPLO.is_file():
            CONTAS.write_text(EXEMPLO.read_text(encoding="utf-8"), encoding="utf-8")
        self._dados = json.loads(CONTAS.read_text(encoding="utf-8"))
        self._construir()
        self._atualizar_status()
        self.after(150, self._processar_eventos)

    # ─── layout ──────────────────────────────────────────────────────────────
    def _construir(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        FaixaParticulas(self, altura=96, fundo=ROXO, titulo="FÁBRICA IATOONS",
                        subtitulo="várias contas · produção em massa · agenda pronta para postar",
                        tam_titulo=24, quantidade=55).grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))

        corpo = ctk.CTkScrollableFrame(self, fg_color="transparent")
        corpo.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 8))
        corpo.grid_columnconfigure(0, weight=1)
        self._corpo = corpo

        # meta do dia
        topo = ctk.CTkFrame(corpo, fg_color=CARD, corner_radius=12, border_width=1, border_color=BORDA)
        topo.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        topo.grid_columnconfigure(1, weight=1)
        self.lbl_meta = ctk.CTkLabel(topo, text="", font=ctk.CTkFont(size=15, weight="bold"), text_color=BRANCO)
        self.lbl_meta.grid(row=0, column=0, columnspan=2, sticky="w", padx=16, pady=(12, 4))
        self.barra_meta = ctk.CTkProgressBar(topo, progress_color=OURO, height=12)
        self.barra_meta.grid(row=1, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 6))
        ctk.CTkLabel(topo, text=f"Pasta da fábrica: {self._dados.get('pasta_base')}", text_color=CINZA,
                     font=ctk.CTkFont(size=11)).grid(row=2, column=0, sticky="w", padx=16, pady=(0, 12))
        ctk.CTkButton(topo, text="Abrir pasta", width=110, fg_color=BORDA, hover_color=CLARO,
                      command=lambda: _abrir(Path(self._dados.get("pasta_base")))).grid(row=2, column=1, sticky="e", padx=16, pady=(0, 12))

        # contas
        self._frame_contas = ctk.CTkFrame(corpo, fg_color="transparent")
        self._frame_contas.grid(row=2, column=0, sticky="ew")
        self._frame_contas.grid_columnconfigure(0, weight=1)
        self._montar_contas()

        botoes = ctk.CTkFrame(corpo, fg_color="transparent")
        botoes.grid(row=3, column=0, sticky="ew", pady=(4, 8))
        ctk.CTkButton(botoes, text="+ Nova conta", width=130, fg_color=BORDA, hover_color=CLARO,
                      command=self._nova_conta).pack(side="left", padx=(0, 8))
        ctk.CTkButton(botoes, text="💾 Salvar contas", width=140, fg_color=BORDA, hover_color=CLARO,
                      command=self._salvar).pack(side="left", padx=(0, 8))
        ctk.CTkButton(botoes, text="↻ Atualizar", width=110, fg_color=BORDA, hover_color=CLARO,
                      command=self._atualizar_status).pack(side="left")

        acoes = ctk.CTkFrame(corpo, fg_color=CARD, corner_radius=12, border_width=1, border_color=BORDA)
        acoes.grid(row=1, column=0, sticky="ew", pady=(0, 10))  # logo abaixo da meta: é o botão do dia a dia
        ctk.CTkLabel(acoes, text="Produção", font=ctk.CTkFont(size=15, weight="bold"), text_color=BRANCO
                     ).grid(row=0, column=0, columnspan=4, sticky="w", padx=16, pady=(12, 2))
        ctk.CTkLabel(acoes, text="1) Coloque os episódios em ENTRADA/<conta>.  2) Clique em Produzir tudo (pode deixar rodando à noite).  "
                                 "3) De manhã, poste na ordem da pasta PARA_POSTAR/<conta>/<data> (legenda no .txt de cada vídeo).",
                     text_color=CINZA, wraplength=820, justify="left", font=ctk.CTkFont(size=12)
                     ).grid(row=1, column=0, columnspan=4, sticky="w", padx=16, pady=(0, 10))
        self.btn_tudo = ctk.CTkButton(acoes, text="▶  Produzir tudo e montar agenda", height=42, fg_color=CLARO,
                                      hover_color=HOVER, font=ctk.CTkFont(size=14, weight="bold"), command=self._produzir_tudo)
        self.btn_tudo.grid(row=2, column=0, padx=(16, 8), pady=(0, 14), sticky="w")
        ctk.CTkLabel(acoes, text="Dias de agenda:", text_color=CINZA).grid(row=2, column=1, padx=(8, 4), pady=(0, 14))
        self.dias = ctk.CTkOptionMenu(acoes, values=["1", "2", "3", "5", "7"], width=70, fg_color=BORDA, button_color=CLARO)
        self.dias.set("2")
        self.dias.grid(row=2, column=2, padx=4, pady=(0, 14))
        self.btn_agenda = ctk.CTkButton(acoes, text="Só montar agenda", height=42, fg_color=BORDA, hover_color=CLARO,
                                        command=self._so_agenda)
        self.btn_agenda.grid(row=2, column=3, padx=(8, 16), pady=(0, 14), sticky="w")

        self.log = ctk.CTkTextbox(self, height=110, fg_color="#120A22", text_color=CINZA, font=ctk.CTkFont(family="Consolas", size=12))
        self.log.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 14))
        self._escrever("Pronto. Contas lidas de contas.json.")

    def _montar_contas(self):
        for w in self._frame_contas.winfo_children():
            w.destroy()
        self._linhas.clear()
        from modules.perfis_visuais import listar_perfis
        perfis = [p.id for p in listar_perfis()] or ["toonmix"]
        for i, c in enumerate(self._dados.get("contas", [])):
            card = ctk.CTkFrame(self._frame_contas, fg_color=CARD, corner_radius=12, border_width=1, border_color=BORDA)
            card.grid(row=i, column=0, sticky="ew", pady=4)
            l1 = ctk.CTkFrame(card, fg_color="transparent")
            l1.pack(fill="x", padx=12, pady=(12, 4))
            l2 = ctk.CTkFrame(card, fg_color="transparent")
            l2.pack(fill="x", padx=12, pady=(0, 12))
            entrada = dict(fg_color="#170C2B", border_color=BORDA)
            ativa = ctk.CTkSwitch(l1, text="ativa", width=70, progress_color=VERDE, text_color=CINZA)
            if c.get("ativa", True):
                ativa.select()
            ativa.pack(side="left", padx=(0, 8))
            nome = ctk.CTkEntry(l1, width=240, **entrada)
            nome.insert(0, c.get("nome", c.get("id", "")))
            nome.pack(side="left", padx=4)
            perfil = ctk.CTkOptionMenu(l1, values=perfis, width=120, fg_color=BORDA, button_color=CLARO)
            perfil.set(c.get("perfil", "toonmix") if c.get("perfil", "toonmix") in perfis else perfis[0])
            perfil.pack(side="left", padx=4)
            cortes = ctk.CTkEntry(l1, width=52, justify="center", **entrada)
            cortes.insert(0, str(c.get("cortes_por_episodio", 10)))
            ctk.CTkLabel(l1, text="cortes/ep.", text_color=CINZA).pack(side="right", padx=(4, 0))
            cortes.pack(side="right", padx=4)
            posts = ctk.CTkEntry(l1, width=52, justify="center", **entrada)
            posts.insert(0, str(c.get("posts_por_dia", 30)))
            ctk.CTkLabel(l1, text="posts/dia", text_color=CINZA).pack(side="right", padx=(4, 14))
            posts.pack(side="right", padx=4)
            cid = F.slug(c.get("id") or c.get("nome", ""))
            base = Path(self._dados.get("pasta_base"))
            ctk.CTkButton(l2, text="Para postar", width=100, fg_color=BORDA, hover_color=CLARO,
                          command=lambda p=base / "PARA_POSTAR" / cid: _abrir(p)).pack(side="right", padx=(4, 0))
            ctk.CTkButton(l2, text="Entrada", width=86, fg_color=BORDA, hover_color=CLARO,
                          command=lambda p=base / "ENTRADA" / cid: _abrir(p)).pack(side="right", padx=4)
            stat = ctk.CTkLabel(l2, text="…", text_color=OURO, font=ctk.CTkFont(size=12, weight="bold"))
            stat.pack(side="right", padx=10)
            hashtags = ctk.CTkEntry(l2, placeholder_text="#hashtags da conta", **entrada)
            hashtags.insert(0, c.get("hashtags", ""))
            hashtags.pack(side="left", fill="x", expand=True, padx=(78, 4))
            self._linhas[cid] = dict(dados=c, ativa=ativa, nome=nome, perfil=perfil, posts=posts, cortes=cortes,
                                     hashtags=hashtags, stat=stat)

    # ─── contas ──────────────────────────────────────────────────────────────
    def _coletar(self) -> dict:
        contas = []
        for cid, w in self._linhas.items():
            d = dict(w["dados"])
            d["id"] = cid
            d["nome"] = w["nome"].get().strip() or cid
            d["ativa"] = bool(w["ativa"].get())
            d["perfil"] = w["perfil"].get()
            d["hashtags"] = w["hashtags"].get().strip()
            for chave, campo, minimo in (("posts_por_dia", "posts", 1), ("cortes_por_episodio", "cortes", 1)):
                try:
                    d[chave] = max(minimo, int(w[campo].get()))
                except ValueError:
                    pass
            contas.append(d)
        return {**self._dados, "contas": contas}

    def _salvar(self, silencioso=False):
        self._dados = self._coletar()
        CONTAS.write_text(json.dumps(self._dados, ensure_ascii=False, indent=2), encoding="utf-8")
        F.preparar_pastas(F.carregar_config(CONTAS))
        if not silencioso:
            self._escrever("Contas salvas e pastas criadas.")
        self._atualizar_status()

    def _nova_conta(self):
        dialogo = ctk.CTkInputDialog(text="Nome da nova conta (ex.: Cortes Simpsons):", title="Nova conta")
        nome = (dialogo.get_input() or "").strip()
        if not nome:
            return
        self._dados = self._coletar()
        cid = F.slug(nome)
        if any(F.slug(c.get("id", "")) == cid for c in self._dados["contas"]):
            self.app.mostrar_aviso("Nova conta", "Já existe uma conta com esse nome.")
            return
        self._dados["contas"].append({"id": cid, "nome": nome, "perfil": "toonmix", "ativa": True, "cortes_por_episodio": 12,
                                      "posts_por_dia": 33, "janela": ["07:00", "23:30"], "hashtags": "", "qualidade": "rapida"})
        self._montar_contas()
        self._salvar()

    # ─── status / ações ──────────────────────────────────────────────────────
    def _atualizar_status(self):
        try:
            st = F.status(F.carregar_config(CONTAS))
        except Exception as erro:  # noqa: BLE001 - contas.json inválido aparece na tela, não derruba o app
            self._escrever(f"contas.json com problema: {erro}")
            return
        meta_dia = 0
        for cid, w in self._linhas.items():
            s = st.get(cid)
            if not s:
                continue
            if s["ativa"]:
                meta_dia += s["posts_por_dia"]
            cor = VERDE if s["dias_de_estoque"] >= 1 else (AMARELO if s["cortes_em_estoque"] else VERMELHO)
            w["stat"].configure(text=f"{s['cortes_em_estoque']} prontos · {s['dias_de_estoque']} dia(s) · "
                                     f"{s['episodios_entrada']} ep. na entrada", text_color=cor)
        self.lbl_meta.configure(text=f"Meta diária das contas ativas: {meta_dia} vídeos/dia  (objetivo: {META})")
        self.barra_meta.set(min(1.0, meta_dia / META))

    def _produzir_tudo(self):
        self._rodar(produzir=True)

    def _so_agenda(self):
        self._rodar(produzir=False)

    def _rodar(self, produzir: bool):
        if self._ocupada:
            return
        self._salvar(silencioso=True)
        self._ocupada = True
        for b in (self.btn_tudo, self.btn_agenda):
            b.configure(state="disabled")
        dias = int(self.dias.get())

        def trabalho():
            try:
                cfg = F.carregar_config(CONTAS)
                if produzir:
                    self._eventos.put(("log", "Produzindo… (pode deixar rodando; dá para fechar e continuar depois)"))
                    F.produzir(cfg, log=lambda m: self._eventos.put(("log", str(m))))
                for c in cfg.contas:
                    if not c.ativa:
                        continue
                    r = F.distribuir(cfg, c, dias=dias)
                    for d in r["dias"]:
                        falta = d["meta"] - d["total_no_dia"]
                        extra = f" · faltam {falta}: coloque mais episódios na entrada" if falta > 0 else " ✓"
                        self._eventos.put(("log", f"[{c.nome}] {d['dia']}: {d['total_no_dia']}/{d['meta']} agendados{extra}"))
                self._eventos.put(("log", "Agenda pronta. Abra 'Para postar' em cada conta."))
            except Exception as erro:  # noqa: BLE001
                self._eventos.put(("log", f"ERRO: {erro}"))
            finally:
                self._eventos.put(("fim", None))

        threading.Thread(target=trabalho, daemon=True).start()

    def _processar_eventos(self):
        try:
            while True:
                tipo, valor = self._eventos.get_nowait()
                if tipo == "log":
                    self._escrever(valor)
                elif tipo == "fim":
                    self._ocupada = False
                    for b in (self.btn_tudo, self.btn_agenda):
                        b.configure(state="normal")
                    self._atualizar_status()
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(250, self._processar_eventos)

    def _escrever(self, texto: str):
        self.log.insert("end", texto + "\n")
        self.log.see("end")
