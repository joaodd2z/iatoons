"""Read-only local title diagnostics; never invokes an AI provider."""
from modules.titulos_v2 import diagnosticos_rejeitados


def detalhar_rejeicao(item):
    criterios = item.get("criterios", {})
    maximos = item.get("criterios_maximos", {})
    linhas = [item.get("texto", ""), f"Rodada: {item.get('etapa', 'atual')}",
              f"Score total: {item.get('score_titulo', 0)}/100", "", "Score por criterio:"]
    linhas.extend(f"{nome}: {valor}/{maximos.get(nome, '?')}" for nome,valor in criterios.items())
    linhas.extend(["", "Penalidades:"])
    linhas.extend(f"{p['codigo']}: {p['valor']:+d} - {p['motivo']}"
                  + (" [bloqueante]" if p.get("bloqueante") else "") for p in item.get("penalidades", []))
    linhas.extend(["", "Motivos objetivos:"] + item.get("motivos", []))
    return "\n".join(linhas)


def mostrar_titulos_rejeitados(master, resultado):
    import customtkinter as ctk
    from tkinter import ttk
    janela = ctk.CTkToplevel(master)
    janela.title("Títulos rejeitados")
    janela.geometry("940x600")
    janela.minsize(720, 480)
    janela.transient(master.winfo_toplevel())
    janela.grid_columnconfigure(0, weight=1)
    janela.grid_rowconfigure(0, weight=1)
    janela.grid_rowconfigure(1, weight=1)
    estilo = ttk.Style(janela)
    estilo.configure("Titulos.Treeview", background="#211536", foreground="white",
                     fieldbackground="#211536", rowheight=30)
    tabela = ttk.Treeview(janela, columns=("titulo", "score", "motivos"), show="headings",
                          selectmode="browse", style="Titulos.Treeview")
    for nome, rotulo, largura in (("titulo", "Título", 310), ("score", "Score", 65), ("motivos", "Motivos", 450)):
        tabela.heading(nome, text=rotulo)
        tabela.column(nome, width=largura, minwidth=55, stretch=nome != "score")
    tabela.grid(row=0, column=0, sticky="nsew", padx=(12, 0), pady=12)
    barra = ttk.Scrollbar(janela, orient="vertical", command=tabela.yview)
    barra.grid(row=0, column=1, sticky="ns", padx=(0, 12), pady=12)
    tabela.configure(yscrollcommand=barra.set)
    detalhe = ctk.CTkTextbox(janela, wrap="word")
    detalhe.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=12, pady=(0, 12))
    rejeitados = diagnosticos_rejeitados(resultado)
    for i,item in enumerate(rejeitados):
        motivos = list(dict.fromkeys(item.get("motivos", []) + [p["motivo"]
                    for p in item.get("penalidades", []) if p.get("origem") == "regra"]))
        tabela.insert("", "end", iid=str(i), values=(item["texto"], item["score_titulo"], "; ".join(motivos)))
    def mostrar(_evento=None):
        detalhe.configure(state="normal")
        detalhe.delete("1.0", "end")
        selecao = tabela.selection()
        detalhe.insert("1.0", detalhar_rejeicao(rejeitados[int(selecao[0])]) if selecao
                       else "Nenhum título rejeitado registrado.")
        detalhe.configure(state="disabled")
    tabela.bind("<<TreeviewSelect>>", mostrar)
    if rejeitados:
        tabela.selection_set("0")
    mostrar()
    return janela
