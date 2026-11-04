"""Fallback de transporte, com orçamento único por episódio e sem sondagens de API."""
from copy import deepcopy
import logging
import math
from threading import Lock
import time

from .errors import ErroProvider, erro_seguro

LOG = logging.getLogger("ia.fallback")
_ultima_analise = "Ainda não realizada"


def ultima_analise():
    return _ultima_analise


class FalhaAnalise(ErroProvider):
    interromper_analise = True


class LimiteChamadas(FalhaAnalise):
    pass


class OrcamentoChamadas:
    def __init__(self, maximo, usadas=0):
        self.maximo, self.usadas = int(maximo), int(usadas)
        if not 0 <= self.usadas <= self.maximo:
            raise ValueError("Orçamento de chamadas inválido.")
        self._lock = Lock()

    @property
    def restantes(self):
        return max(0, self.maximo - self.usadas)

    def reservar(self):
        with self._lock:
            if self.restantes == 0:
                raise LimiteChamadas("Limite de chamadas do episódio atingido; nenhuma tentativa extra foi iniciada.")
            self.usadas += 1


class ProviderFallback:
    gerencia_orcamento = True

    def __init__(self, config, *, modo="analise", schema=None, factory=None,
                 orcamento=None, dormir=None):
        self.config = deepcopy(config).validar()
        self.modo, self.schema = modo, schema
        self.factory = factory
        self.orcamento = orcamento or OrcamentoChamadas(config.max_chamadas_episodio)
        self.dormir = dormir or time.sleep
        self.ordem = list(config.ordem_fallback)
        if config.provider_preferido:
            self.ordem.remove(config.provider_preferido)
            self.ordem.insert(0, config.provider_preferido)
        self.solicitado = self.ordem[0]
        self._bloqueados = {}
        self.metadados = []

    @property
    def restantes(self):
        return self.orcamento.restantes

    def listar_modelos(self):
        # Compatibilidade com a preparação legada: nenhuma chamada de catálogo.
        return [self.config.modelo]

    def testar_conexao(self, modelo=None):
        raise ValueError("Teste a conexão de um provider individual, sem fallback.")

    def _criar(self, nome):
        from .factory import obter_provider
        return (self.factory or obter_provider)(nome, modo=self.modo, schema=self.schema)

    def analisar(self, modelo, prompt):
        """Envia o mesmo prompt/schema, reservando uma chamada antes de cada geração."""
        global _ultima_analise
        inicio = time.perf_counter()
        tentativas = 0
        motivos = []
        trace = []
        ultimo = None
        for nome in self.ordem:
            if nome in self._bloqueados:
                motivos.append(self._bloqueados[nome])
                continue
            if self.restantes == 0:
                _ultima_analise = " -> ".join(trace + ["Limite de chamadas atingido"])
                raise LimiteChamadas("Limite de chamadas do episódio atingido; fallback interrompido.")
            try:
                cliente = self._criar(nome)
            except Exception:
                # Configuração/credencial ausente exige intervenção, sem fallback silencioso.
                _ultima_analise = f"{nome.title()}: credencial/configuração indisponível"
                raise FalhaAnalise(f"Configure a credencial de {nome.title()} antes de analisar.") from None
            modelo_atual = getattr(self.config, f"modelo_{nome}")
            for tentativa in (1, 2):
                self.orcamento.reservar()
                tentativas += 1
                try:
                    resposta = cliente.analisar_uma_vez(modelo_atual, prompt)
                    if not isinstance(resposta, dict):
                        raise ErroProvider("Resposta JSON inválida.")
                except Exception as original:
                    erro = original if isinstance(original, ErroProvider) else erro_seguro(original, nome)
                    codigo = erro.codigo
                    status = str(codigo) if codigo else (erro.tipo or "resposta inválida")
                    LOG.info("[%s] %s -> %s", nome.title(), "retry" if tentativa == 2 else "tentativa 1", status)
                    trace.append(f"{nome.title()} -> {status}")
                    ultimo = erro
                    autenticacao = codigo in (401, 403)
                    recuperavel = (codigo in (404, 408, 429) or
                                   isinstance(codigo, int) and 500 <= codigo <= 599 or
                                   erro.tipo in ("rede", "timeout") or
                                   autenticacao and self.config.fallback_autenticacao)
                    if not recuperavel:
                        _ultima_analise = " -> ".join(trace)
                        mensagem = (f"Chave inválida ou sem permissão em {nome.title()} (HTTP {codigo})."
                                    if autenticacao else f"Resposta/configuração inválida em {nome.title()}; análise interrompida.")
                        raise FalhaAnalise(mensagem, codigo=codigo) from None
                    espera = erro.aguardar_segundos
                    # Só repete quando o servidor fornece uma espera curta e segura.
                    if (codigo == 429 and tentativa == 1 and self.restantes > 0 and
                            espera is not None and math.isfinite(espera) and 0 <= espera <= 5):
                        self.dormir(espera)
                        continue
                    motivo = f"{nome}_{status}"
                    motivos.append(motivo)
                    self._bloqueados[nome] = motivo
                    break
                else:
                    LOG.info("[%s] tentativa %s -> OK", nome.title(), tentativa)
                    trace.append(f"{nome.title()} OK")
                    metadata = {
                        "provider_solicitado": self.solicitado,
                        "provider_utilizado": nome,
                        "modelo_utilizado": resposta.get("model", modelo_atual),
                        "numero_de_tentativas": tentativas,
                        "fallback_ocorreu": nome != self.solicitado,
                        "motivo_fallback": ";".join(motivos) or None,
                        "latencia_total_ms": round((time.perf_counter() - inicio) * 1000, 3),
                    }
                    self.metadados.append(metadata)
                    _ultima_analise = " -> ".join(trace)
                    if nome != self.solicitado:
                        LOG.info("Fallback concluído: %s", nome.title())
                    return {**resposta, **metadata, "provider": nome,
                            "model": metadata["modelo_utilizado"]}
        _ultima_analise = " -> ".join(trace) or "Todos os providers indisponíveis neste episódio"
        raise FalhaAnalise("Todos os providers disponíveis falharam: " + "; ".join(motivos),
                           codigo=ultimo.codigo if ultimo else None) from None
