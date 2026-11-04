"""Local, opt-in timing; worker sessions never change processing results."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import json
from pathlib import Path
from time import perf_counter
from uuid import uuid4


_ativo = ContextVar("performance_profiler", default=None)


class PerformanceProfiler:
    def __init__(self, nome, destino=None):
        self.nome = nome
        self.destino = Path(destino) if destino else None
        self.registros = []
        self._pilha = []
        self.duracao_s = 0.0
        self.erro_gravacao = None

    def __enter__(self):
        self._token = _ativo.set(self)
        self._inicio = perf_counter()
        return self

    def __exit__(self, tipo, erro, traceback):
        self.duracao_s = perf_counter() - self._inicio
        _ativo.reset(self._token)
        if self.destino:
            try:
                self.destino.mkdir(parents=True, exist_ok=True)
                arquivo = self.destino / f"{uuid4().hex}.json"
                arquivo.write_text(json.dumps(self.relatorio(), indent=2), encoding="utf-8")
            except (OSError, ValueError) as exc:
                # Diagnostics must not turn a successful export into a failure.
                self.erro_gravacao = type(exc).__name__

    @contextmanager
    def medir(self, operacao, **metadados):
        registro = {"id": len(self.registros), "operacao": operacao,
                    "pai": self._pilha[-1][0]["id"] if self._pilha else None,
                    "metadados": metadados, "status": "ok"}
        self.registros.append(registro)
        inicio = perf_counter()
        frame = [registro, 0.0]
        self._pilha.append(frame)
        try:
            yield registro
        except BaseException as exc:
            registro["status"] = "erro"
            registro["tipo_erro"] = type(exc).__name__
            raise
        finally:
            duracao = perf_counter() - inicio
            self._pilha.pop()
            registro.update(duracao_s=duracao, exclusiva_s=max(0.0, duracao - frame[1]))
            if self._pilha:
                self._pilha[-1][1] += duracao

    def relatorio(self):
        return {"versao": 1, "nome": self.nome, "duracao_s": self.duracao_s,
                "registros": self.registros}


def anotar(**metadados):
    profiler = _ativo.get()
    if profiler and profiler._pilha:
        profiler._pilha[-1][0]["metadados"].update(metadados)


def medir_operacao(operacao, *, backend=None, cache=False):
    def decorar(funcao):
        @wraps(funcao)
        def executar(*args, **kwargs):
            profiler = _ativo.get()
            if profiler is None:
                return funcao(*args, **kwargs)
            with profiler.medir(operacao, backend=backend) as registro:
                resultado = funcao(*args, **kwargs)
                if cache:
                    registro["metadados"]["cache"] = "hit" if resultado is not None else "miss"
                    arquivo = getattr(resultado, "caminho_cache", None)
                    if arquivo:
                        registro["metadados"]["arquivos_reutilizados"] = [arquivo]
                return resultado
        return executar
    return decorar


def medir_chamada(operacao, funcao, *args, **kwargs):
    return medir_operacao(operacao)(funcao)(*args, **kwargs)
