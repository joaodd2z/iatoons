"""Faixa com partículas animadas (marca IATOONS): pontinhos violeta e dourados subindo devagar atrás do texto.
Leve: ~40 círculos num Canvas, 25 quadros/s, e pausa sozinha quando o widget some da tela."""
import math
import random
import tkinter as tk

CORES = ("#A78BFA", "#7C3AED", "#C4B5FD", "#F5C451", "#E9D5FF")


class FaixaParticulas(tk.Canvas):
    def __init__(self, master, *, altura=110, fundo="#180D2C", titulo="IATOONS", subtitulo="",
                 tam_titulo=26, quantidade=40, **kw):
        # width=1: o Canvas pede ~378 px por padrão e alargaria a barra lateral; o pack(fill="x") estica
        kw.setdefault("width", 1)
        super().__init__(master, height=altura, bg=fundo, highlightthickness=0, bd=0, **kw)
        self._titulo, self._subtitulo, self._tam = titulo, subtitulo, tam_titulo
        self._n = quantidade
        self._pts = []
        self._t = 0
        self._vivo = True
        self.bind("<Configure>", lambda e: self._montar())
        self.bind("<Destroy>", lambda e: setattr(self, "_vivo", False))
        self.after(60, self._quadro)

    def _montar(self):
        self.delete("all")
        w, h = max(1, self.winfo_width()), max(1, self.winfo_height())
        rnd = random.Random(7)
        self._pts = []
        for _ in range(self._n):
            r = rnd.uniform(1.0, 2.6)
            x, y = rnd.uniform(0, w), rnd.uniform(0, h)
            item = self.create_oval(x - r, y - r, x + r, y + r, fill=rnd.choice(CORES), outline="")
            self._pts.append([item, x, y, r, rnd.uniform(0.15, 0.6), rnd.uniform(0, 6.28)])
        # brilho atrás do título e o texto por cima das partículas
        self.create_text(w / 2 + 2, h * 0.42 + 2, text=self._titulo, fill="#3B1D72",
                         font=("Arial Black", self._tam, "bold"), tags="txt")
        self.create_text(w / 2, h * 0.42, text=self._titulo, fill="#F8FAFC",
                         font=("Arial Black", self._tam, "bold"), tags="txt")
        if self._subtitulo:
            self.create_text(w / 2, h * 0.78, text=self._subtitulo, fill="#F5C451",
                             font=("Arial", 10, "bold"), tags="txt")

    def _quadro(self):
        if not self._vivo:
            return
        try:
            if self.winfo_ismapped() and self._pts:
                w, h = self.winfo_width(), self.winfo_height()
                self._t += 1
                for p in self._pts:
                    item, x, y, r, v, fase = p
                    y -= v
                    x += math.sin(self._t / 40 + fase) * 0.25
                    if y < -4:
                        y, x = h + 4, random.uniform(0, w)
                    p[1], p[2] = x, y
                    self.coords(item, x - r, y - r, x + r, y + r)
                self.tag_raise("txt")
            self.after(40, self._quadro)
        except tk.TclError:
            self._vivo = False
