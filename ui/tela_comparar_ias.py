"""Comparação confirmada por clique, sem renderização ou chamadas automáticas."""
import queue
import threading
from tkinter import filedialog, messagebox

import customtkinter as ctk

from modules.ai.comparacao import ComparacaoIA, escolher_resultado, orcamento_episodio
from modules.config_ia import carregar_config_ia
from modules.sessao import caminho_normalizado
from ui.app import COR_ROXO_CLARO, COR_VERDE

NOMES = {'gemini': 'Gemini', 'groq': 'Groq', 'mistral': 'Mistral'}


class TelaCompararIAs(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color='transparent')
        self.app = app
        self._eventos = queue.Queue()
        self.relatorio = None
        self._ocupada = False
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        raiz = ctk.CTkScrollableFrame(self)
        raiz.grid(row=0, column=0, sticky='nsew', padx=20, pady=20)
        raiz.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(raiz, text='COMPARAR IAs', font=ctk.CTkFont(size=24, weight='bold')).grid(row=0, column=0, sticky='w')
        ctk.CTkLabel(raiz, text='Mesma transcrição do episódio inteiro. Scores locais não medem qualidade artística.',
                     wraplength=900).grid(row=1, column=0, sticky='w', pady=8)
        controles = ctk.CTkFrame(raiz)
        controles.grid(row=2, column=0, sticky='ew')
        self.checks = {}
        for coluna, (p, nome) in enumerate(NOMES.items()):
            check = ctk.CTkCheckBox(controles, text=nome)
            check.grid(row=0, column=coluna, padx=12, pady=12)
            if p != 'mistral':
                check.select()
            self.checks[p] = check
        self.combo_modo = ctk.CTkComboBox(controles, values=['Cortes normais', 'Modo Agência / Remix'], state='readonly', width=210)
        self.combo_modo.set('Cortes normais')
        self.combo_modo.grid(row=1, column=0, padx=12, pady=8)
        self.combo_max = ctk.CTkComboBox(controles, values=[str(n) for n in range(5, 11)], state='readonly', width=75)
        self.combo_max.set('5')
        ctk.CTkLabel(controles, text='Máx. candidatos:').grid(row=1, column=1)
        self.combo_max.grid(row=1, column=2, padx=12)
        self.check_cache = ctk.CTkCheckBox(controles, text='Reutilizar caches compatíveis')
        self.check_cache.grid(row=2, column=0, columnspan=3, padx=12, pady=8, sticky='w')
        self.btn_cache = ctk.CTkButton(controles, text='Verificar caches / chamadas', command=self._verificar)
        self.btn_cache.grid(row=3, column=0, padx=12, pady=10)
        self.btn_comparar = ctk.CTkButton(controles, text='Comparar', fg_color=COR_VERDE, command=self._comparar)
        self.btn_comparar.grid(row=3, column=1, padx=12, pady=10)
        self.btn_salvar = ctk.CTkButton(controles, text='Salvar comparação', state='disabled', command=self._salvar)
        self.btn_salvar.grid(row=3, column=2, padx=12, pady=10)
        self.lbl_status = ctk.CTkLabel(raiz, text='Nenhuma chamada será feita antes da confirmação.', wraplength=950, justify='left')
        self.lbl_status.grid(row=3, column=0, sticky='w', pady=10)
        self.cards = ctk.CTkFrame(raiz, fg_color='transparent')
        self.cards.grid(row=4, column=0, sticky='ew')
        ctk.CTkButton(raiz, text='Voltar às Configurações de IA', command=lambda: app.navegar('config_ia')).grid(row=5, column=0, sticky='w', pady=12)
        self.after(100, self._processar)

    def _preparar(self):
        t = self.app.estado.get('transcricao')
        if not t:
            raise ValueError('Importe e transcreva um episódio primeiro.')
        video = self.app.estado.get('video_path')
        if not video or caminho_normalizado(video) != caminho_normalizado(t.video_path):
            raise ValueError('A transcrição não corresponde ao episódio atual.')
        cfg = carregar_config_ia()
        return ComparacaoIA(t, cfg, [p for p, check in self.checks.items() if check.get()],
            modo='agencia' if self.combo_modo.get() == 'Modo Agência / Remix' else 'normal',
            max_candidatos=int(self.combo_max.get()), reutilizar_cache=bool(self.check_cache.get()),
            orcamento=orcamento_episodio(self.app.estado, t, cfg.max_chamadas_episodio))

    def _verificar(self):
        try:
            comparacao = self._preparar()
            caches = ', '.join(NOMES[p] for p in comparacao.caches_disponiveis) or 'nenhum'
            self.lbl_status.configure(text=f'Caches compatíveis: {caches}. Esta comparação fará {comparacao.chamadas_previstas} chamadas. '
                f'Orçamento restante: {comparacao.orcamento.restantes}.')
        except (ValueError, TypeError) as erro:
            self.app.mostrar_erro('Comparar IAs', str(erro))

    def _comparar(self):
        from modules.tasks import fila_analise_ia
        if self._ocupada or self.app.estado.get('comparacao_ocupada') or fila_analise_ia.ocupada:
            self.app.mostrar_aviso('Comparar IAs', 'Já existe uma análise em andamento.')
            return
        try:
            comparacao = self._preparar()
            if comparacao.chamadas_previstas > comparacao.orcamento.restantes:
                raise ValueError('O orçamento restante não permite esta comparação. Reutilize cache ou aguarde um novo episódio.')
        except (ValueError, TypeError) as erro:
            self.app.mostrar_erro('Comparar IAs', str(erro))
            return
        if not messagebox.askyesno('Confirmar comparação',
                f'Esta comparação fará {comparacao.chamadas_previstas} chamadas.\n'
                'Uma chamada por provider sem cache, sem fallback nem retry.\nDeseja continuar?', parent=self):
            return
        self._ocupada = True
        self.app.estado['comparacao_ocupada'] = True
        self.comparacao = comparacao
        self.btn_comparar.configure(state='disabled')
        self.btn_cache.configure(state='disabled')
        self.btn_salvar.configure(state='disabled')
        self.relatorio = None
        self.lbl_status.configure(text='Comparação iniciada…')
        threading.Thread(target=self._worker, args=(comparacao,), daemon=True).start()

    def _worker(self, comparacao):
        try:
            relatorio = comparacao.executar(confirmado=True, progresso_cb=lambda msg: self._eventos.put(('status', msg)))
            self._eventos.put(('fim', relatorio))
        except Exception:
            self._eventos.put(('erro', 'Não foi possível concluir a comparação. Verifique o orçamento e a entrada.'))
        finally:
            self.app.estado['comparacao_ocupada'] = False

    def _processar(self):
        try:
            while True:
                tipo, valor = self._eventos.get_nowait()
                if tipo == 'status':
                    self.lbl_status.configure(text=valor)
                    continue
                self._ocupada = False
                self.btn_comparar.configure(state='normal')
                self.btn_cache.configure(state='normal')
                if tipo == 'fim':
                    self.relatorio = valor
                    self.btn_salvar.configure(state='normal')
                    self._mostrar_resultados()
                else:
                    self.lbl_status.configure(text=valor)
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(100, self._processar)

    def _mostrar_resultados(self):
        for widget in self.cards.winfo_children():
            widget.destroy()
        for coluna, (p, r) in enumerate(self.relatorio['resultados'].items()):
            self.cards.grid_columnconfigure(coluna, weight=1)
            card = ctk.CTkFrame(self.cards)
            card.grid(row=0, column=coluna, padx=5, sticky='nsew')
            card.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(card, text=NOMES[p].upper(), font=ctk.CTkFont(size=18, weight='bold')).grid(row=0, column=0, pady=8)
            linhas = [f"Modelo: {r['modelo']}", f"Tempo: {r['latencia_ms']:.0f} ms" + (' (cache local)' if r['cache_reutilizado'] else '')]
            if r['erro']:
                linhas.append(f"FALHA {r['erro']['codigo'] or ''}: {r['erro']['mensagem']}")
            else:
                linhas += [f"Recebidos: {r['candidatos_recebidos']} | Válidos: {r['candidatos_validos']}",
                           f"Rejeitados: {r['candidatos_rejeitados']} | Remixes válidos: {r['remixes_validos']}",
                           f"Títulos gerados: {r['titulos_gerados']}",
                           f"Score técnico: {r['score_tecnico']}/100", f"Score remix: {r['score_remix']}/100",
                           f"Score título estrutural: {r['score_titulo_estrutural']}/100"]
            ctk.CTkLabel(card, text='\n'.join(linhas), justify='left', wraplength=300).grid(row=1, column=0, padx=10, sticky='w')
            ctk.CTkButton(card, text='Usar este resultado', fg_color=COR_ROXO_CLARO,
                state='normal' if r.get('itens_validos') and not r['erro'] else 'disabled',
                command=lambda provider=p: self._usar(provider)).grid(row=2, column=0, padx=10, pady=10)
            for linha, candidato in enumerate(r.get('candidatos', []), 3):
                cenas = [c['mensagem'] + ' com ' + NOMES[next(q for q in c['providers'] if q != p)]
                         for c in self.relatorio['cenas_compartilhadas'] if p in c['providers'] and
                         c['indices'][c['providers'].index(p)] == candidato['indice']]
                checks = '\n'.join(f"{k}: {'OK' if v else 'FALHA'}" for k, v in candidato['verificacoes'].items() if v is not None)
                texto = (f"{candidato['inicio']}–{candidato['fim']} s\nTítulo: {candidato['titulo_principal']}\n"
                         f"Alternativas: {' / '.join(candidato['alternativas'])}\nDescrição: {candidato['descricao']}\n"
                         f"Gancho: {candidato['gancho']}\nJustificativa: {candidato['justificativa']}\n"
                         f"Confiança do provider: {candidato['confianca_provider']}\n"
                         f"{'Válido' if candidato['valido'] else 'Rejeitado'}\n" + '\n'.join(cenas) + '\n' + checks)
                ctk.CTkLabel(card, text=texto, justify='left', wraplength=300).grid(row=linha, column=0, padx=10, pady=10, sticky='w')
        self.lbl_status.configure(text=f"Comparação concluída: {self.relatorio['chamadas_utilizadas']} chamadas. "
                                  'Os resultados permanecem separados. Nenhum vídeo foi gerado.')

    def _usar(self, provider):
        if not self.relatorio:
            return
        try:
            escolher_resultado(self.app, self.relatorio, provider)
        except ValueError as erro:
            self.app.mostrar_erro('Usar resultado', str(erro))

    def _salvar(self):
        if not self.relatorio:
            return
        caminho = filedialog.asksaveasfilename(parent=self, title='Salvar comparação',
            defaultextension='.json', filetypes=[('JSON', '*.json')], initialfile='comparacao_ias.json')
        if caminho:
            try:
                self.comparacao.salvar(caminho)
                self.app.mostrar_info('Comparar IAs', 'Relatório JSON salvo.')
            except OSError:
                self.app.mostrar_erro('Comparar IAs', 'Não foi possível salvar o relatório.')
