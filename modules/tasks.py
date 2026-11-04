"""
tasks.py — Gerenciamento de tarefas em segundo plano
Executa renderizações sem travar a interface gráfica.
"""

import threading
import queue
from typing import Callable, Optional
from modules.performance import PerformanceProfiler
from modules.preset import DADOS_DIR


class TarefaRenderizacao:
    """Representa uma tarefa de renderização em andamento."""

    def __init__(
        self,
        nome: str,
        funcao: Callable,
        args: tuple = (),
        kwargs: dict = None,
        callback_progresso: Optional[Callable] = None,
        callback_conclusao: Optional[Callable] = None,
        callback_erro: Optional[Callable] = None,
    ):
        self.nome = nome
        self.funcao = funcao
        self.args = args
        self.kwargs = kwargs or {}
        self.callback_progresso = callback_progresso
        self.callback_conclusao = callback_conclusao
        self.callback_erro = callback_erro
        self.cancelada = False
        self._thread: Optional[threading.Thread] = None

    def iniciar(self):
        """Inicia a tarefa em uma thread separada."""
        self._thread = threading.Thread(target=self._executar, daemon=True)
        self._thread.start()

    def cancelar(self):
        """Sinaliza que a tarefa deve ser cancelada."""
        self.cancelada = True

    def _executar(self):
        """Executa a função e chama os callbacks adequados."""
        try:
            self.performance = PerformanceProfiler("tarefa", DADOS_DIR / "performance")
            with self.performance:
                resultado = self.funcao(
                    *self.args,
                    **self.kwargs,
                    cancelada_ref=self,
                    progresso_cb=self.callback_progresso,
                )
        except Exception as e:
            if self.callback_erro:
                self.callback_erro(str(e))
            return

        if not self.cancelada and self.callback_conclusao:
            self.callback_conclusao(resultado)
        elif self.cancelada and self.callback_conclusao:
            self.callback_conclusao({"sucesso": False, "erro": "Tarefa cancelada."})


class CoordenadorRecursos:
    """
    Coordena globalmente os recursos compartilhados ('transcricao', 'agencia', 'render')
    entre as abas individuais (FilaTarefas) e a Esteira Multi-Episódio.
    Garante no máximo 1 operação ativa por recurso em todo o aplicativo.
    """

    def __init__(self, limites: Optional[dict[str, int]] = None):
        self._lock = threading.RLock()
        self._ocupantes: dict[str, set[object]] = {
            "transcricao": set(),
            "agencia": set(),
            "render": set(),
        }
        self._limites: dict[str, int] = {
            "transcricao": 1,
            "agencia": 1,
            "render": 1,
        }
        if limites:
            self._limites.update(limites)
        self._max_observados: dict[str, int] = {
            "transcricao": 0,
            "agencia": 0,
            "render": 0,
        }
        self._callbacks_liberacao: dict[str, list[Callable]] = {
            "transcricao": [],
            "agencia": [],
            "render": [],
        }
        self._callbacks_globais: list[Callable[[str], None]] = []

    def registrar_callback_liberacao(self, recurso_ou_callback, callback: Optional[Callable] = None):
        with self._lock:
            if callback is None and callable(recurso_ou_callback):
                if recurso_ou_callback not in self._callbacks_globais:
                    self._callbacks_globais.append(recurso_ou_callback)
            else:
                recurso = str(recurso_ou_callback)
                lista = self._callbacks_liberacao.setdefault(recurso, [])
                if callback is not None and callback not in lista:
                    lista.append(callback)

    def remover_callback_liberacao(self, recurso_ou_callback, callback: Optional[Callable] = None):
        with self._lock:
            if callback is None and callable(recurso_ou_callback):
                if recurso_ou_callback in self._callbacks_globais:
                    self._callbacks_globais.remove(recurso_ou_callback)
            else:
                recurso = str(recurso_ou_callback)
                lista = self._callbacks_liberacao.get(recurso, [])
                if callback in lista:
                    lista.remove(callback)

    def tentar_adquirir(self, recurso: str, token_dono: object, limite: int = 1) -> bool:
        with self._lock:
            ativos = self._ocupantes.setdefault(recurso, set())
            if token_dono in ativos:
                return True
            lim = max(1, min(limite, self._limites.get(recurso, 1)))
            if len(ativos) >= lim:
                return False
            ativos.add(token_dono)
            self._max_observados[recurso] = max(self._max_observados.get(recurso, 0), len(ativos))
            return True

    def liberar(self, recurso: str, token_dono: object):
        callbacks_recurso: list[Callable] = []
        callbacks_globais: list[Callable[[str], None]] = []
        with self._lock:
            ativos = self._ocupantes.get(recurso)
            if ativos and token_dono in ativos:
                ativos.discard(token_dono)
                callbacks_recurso = list(self._callbacks_liberacao.get(recurso, []))
                callbacks_globais = list(self._callbacks_globais)
        for cb in callbacks_recurso:
            try:
                cb()
            except Exception:
                pass
        for cb_g in callbacks_globais:
            try:
                cb_g(recurso)
            except Exception:
                pass

    def ocupado(self, recurso: str) -> bool:
        with self._lock:
            return bool(self._ocupantes.get(recurso))

    def quantidade_ativa(self, recurso: str) -> int:
        with self._lock:
            return len(self._ocupantes.get(recurso, set()))

    def ativos(self, recurso: str) -> int:
        return self.quantidade_ativa(recurso)

    def max_simultaneos(self, recurso: str) -> int:
        with self._lock:
            return self._max_observados.get(recurso, 0)


coordenador_recursos = CoordenadorRecursos()


class FilaTarefas:
    """
    Fila de tarefas de segundo plano.
    Processa uma tarefa por vez e respeita o coordenador global de recursos quando vinculado.
    """

    def __init__(self, recurso: Optional[str] = None, coordenador: Optional[CoordenadorRecursos] = None):
        self._fila: queue.Queue = queue.Queue()
        self._pendente_cabeca: Optional[TarefaRenderizacao] = None
        self._tarefa_atual: Optional[TarefaRenderizacao] = None
        self._lock = threading.RLock()
        self.recurso = recurso
        self._coordenador = coordenador if coordenador is not None else (coordenador_recursos if recurso else None)
        if self.recurso and self._coordenador:
            self._coordenador.registrar_callback_liberacao(self.recurso, self._processar_proximo)

    def adicionar(self, tarefa: TarefaRenderizacao):
        """Adiciona uma tarefa à fila e inicia o processamento se estiver livre."""
        self._fila.put(tarefa)
        self._processar_proximo()

    def _processar_proximo(self):
        with self._lock:
            if self._tarefa_atual is not None:
                return  # já há uma tarefa rodando nesta fila
            if self._pendente_cabeca is None:
                try:
                    self._pendente_cabeca = self._fila.get_nowait()
                except queue.Empty:
                    return

            if self.recurso and self._coordenador:
                if not self._coordenador.tentar_adquirir(self.recurso, self):
                    return  # recurso global ocupado pela Esteira; aguarda callback de liberação

            tarefa = self._pendente_cabeca
            self._pendente_cabeca = None

            tarefa_original_conclusao = tarefa.callback_conclusao
            tarefa_original_erro = tarefa.callback_erro

            def finalizar_tarefa():
                deve_continuar = False
                with self._lock:
                    if self._tarefa_atual is tarefa:
                        self._tarefa_atual = None
                        deve_continuar = True
                if self.recurso and self._coordenador:
                    self._coordenador.liberar(self.recurso, self)
                if deve_continuar:
                    self._processar_proximo()

            def on_conclusao(resultado):
                try:
                    if tarefa_original_conclusao:
                        tarefa_original_conclusao(resultado)
                finally:
                    finalizar_tarefa()

            def on_erro(erro):
                try:
                    if tarefa_original_erro:
                        tarefa_original_erro(erro)
                finally:
                    finalizar_tarefa()

            tarefa.callback_conclusao = on_conclusao
            tarefa.callback_erro = on_erro
            self._tarefa_atual = tarefa
            try:
                tarefa.iniciar()
            except Exception as exc:
                self._tarefa_atual = None
                tarefa.callback_conclusao = tarefa_original_conclusao
                tarefa.callback_erro = tarefa_original_erro
                if self.recurso and self._coordenador:
                    self._coordenador.liberar(self.recurso, self)
                if tarefa_original_erro:
                    try:
                        tarefa_original_erro(str(exc))
                    except Exception:
                        pass
                if not self._fila.empty():
                    self._processar_proximo()

    def cancelar_tudo(self):
        """Cancela a tarefa atual e limpa a fila."""
        with self._lock:
            if self._tarefa_atual:
                self._tarefa_atual.cancelar()
            if self._pendente_cabeca:
                self._pendente_cabeca.cancelar()
                self._pendente_cabeca = None
            while not self._fila.empty():
                try:
                    self._fila.get_nowait()
                except queue.Empty:
                    break

    @property
    def ocupada(self) -> bool:
        with self._lock:
            local = (
                self._tarefa_atual is not None
                or self._pendente_cabeca is not None
                or not self._fila.empty()
            )
            if local:
                return True
            if self.recurso and self._coordenador:
                return self._coordenador.ocupado(self.recurso)
            return False

    @property
    def tamanho_fila(self) -> int:
        with self._lock:
            extra = 1 if self._pendente_cabeca is not None else 0
            return self._fila.qsize() + extra


# Fila única compartilhada pelas exportações manual e em lote.
fila_render = FilaTarefas(recurso="render")
# Filas compartilhadas para que uma troca de episódio possa cancelar todo o
# trabalho derivado do vídeo anterior, mesmo que a tela já tenha sido fechada.
fila_transcricao = FilaTarefas(recurso="transcricao")
fila_analise_ia = FilaTarefas(recurso="agencia")
