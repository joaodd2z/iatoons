"""Comparação independente de análises: só texto, validação local e arquivos JSON."""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from time import perf_counter

from modules import analise_ia
from modules.agencia import editorial
from modules.intro import carregar_intro
from modules.preset import DADOS_DIR
from modules.credenciais import obter_chave
from .cache_fallback import escrever
from .errors import erro_seguro, ErroProvider
from .factory import obter_provider
from .fallback import OrcamentoChamadas, LimiteChamadas
from .response import normalizar_resposta

COMPARACOES_DIR = DADOS_DIR / "comparacoes_ia"
VERSAO_SCORE = 1
PROVIDERS = ("gemini", "groq", "mistral")


def _hash(dados):
    return hashlib.sha256(json.dumps(dados, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def episode_identity(transcricao):
    return _hash({"video_id": transcricao.video_id, "origem": str(Path(transcricao.video_path).resolve()),
                  "duracao": transcricao.duracao,
                  "segmentos": [asdict(s) for s in transcricao.segmentos]})


def _intervalo(item):
    try:
        inicio, fim = float(item['inicio']), float(item['fim'])
        if math.isfinite(inicio + fim) and 0 <= inicio < fim:
            return inicio, fim
    except (TypeError, ValueError, KeyError):
        pass
    return None


def _intervalos(item, modo):
    brutos = item.get('segmentos', []) if modo == 'agencia' else [item]
    if not isinstance(brutos, list):
        return []
    return [iv for iv in (_intervalo(s) for s in brutos if isinstance(s, dict)) if iv]


def mesma_cena(a, b):
    """Interseção >= 70% do menor intervalo, ou fronteiras a até cinco segundos."""
    ai, af = a
    bi, bf = b
    intersecao = max(0, min(af, bf) - max(ai, bi))
    return intersecao > 0 and (intersecao / max(0.001, min(af-ai, bf-bi)) >= .7 or
                              abs(ai-bi) <= 5 and abs(af-bf) <= 5)


def _titulos(item):
    if not isinstance(item.get('titulos'), list):
        return []
    return [str(t.get('texto', '') if isinstance(t, dict) else t).strip()
            for t in (item.get('titulos') or [])]


def avaliar_resposta(dados, transcricao, modo, max_candidatos):
    """Scores 0–100 de conformidade observável; não avaliam qualidade artística."""
    chave = 'historias' if modo == 'agencia' else 'candidatos'
    itens = dados[chave]
    intro = carregar_intro(transcricao)
    validar = editorial.validar_planos if modo == 'agencia' else analise_ia.validar_candidatos_com_diagnostico
    validos, rejeicoes = validar(itens, transcricao, max_candidatos)
    candidatos, vistos = [], []
    for indice, item in enumerate(itens):
        ivs = _intervalos(item, modo)
        limites = (min(i for i, _ in ivs), max(f for _, f in ivs)) if ivs else None
        individual, _ = validar([item], transcricao, 1)
        duplicado = bool(individual) and any(
            editorial.sobreposicao_relativa(individual[0], anterior) > .6 if modo == 'agencia'
            else analise_ia._sobreposicao_excessiva(individual[0], [anterior]) for anterior in vistos)
        if individual and not duplicado:
            vistos.append(individual[0])
        gancho = item.get('gancho')
        gancho_iv = _intervalo(gancho) if isinstance(gancho, dict) else None
        estrutura = item.get('estrutura') if isinstance(item.get('estrutura'), dict) else {}
        segmentos = item.get('segmentos') if isinstance(item.get('segmentos'), list) else []
        funcoes = {str(s.get('funcao', '')).casefold() for s in segmentos if isinstance(s, dict)}
        duracao = sum(f-i for i, f in ivs)
        sem_corte = bool(ivs) and all(not any(
            s.inicio + .05 < t < s.fim - .05 for s in transcricao.segmentos)
            for iv in ivs for t in iv)
        ordenados = sorted(ivs)
        titulos = _titulos(item)
        checks = {
            'json_valido': True,
            'timestamps_validos': bool(ivs) and len(ivs) == (len(segmentos) if modo == 'agencia' else 1),
            'duracao_valida': (25 <= duracao <= 120) if modo == 'agencia' else (45 <= duracao <= 120),
            'dentro_episodio': bool(ivs) and all(0 <= i < f <= transcricao.duracao for i, f in ivs),
            'ausencia_intro': bool(ivs) and not (intro and intro.ativo and any(i < intro.fim and f > intro.inicio for i, f in ivs)),
            'fala_inteira': sem_corte,
            'presenca_gancho': bool(gancho_iv if modo == 'agencia' else gancho),
            'presenca_contexto': bool('contexto' in funcoes or estrutura.get('preparacao')) if modo == 'agencia' else None,
            'presenca_consequencia': bool('consequencia' in funcoes or estrutura.get('conclusao')) if modo == 'agencia' else None,
            'segmentos_suficientes': len(ivs) >= (2 if modo == 'agencia' else 1),
            'continuidade_temporal': bool(ivs) and all(bi-af <= 30 for (_, af), (bi, _) in zip(ordenados, ordenados[1:])),
            'sem_sobreposicao_interna': all(af <= bi + .05 for (_, af), (bi, _) in zip(ordenados, ordenados[1:])),
            'sem_duplicacao': not duplicado,
            'titulos_nao_vazios': bool(titulos) and all(titulos),
        }
        contrato = (individual[0].validacao_remix if modo == 'agencia' else
                    (individual[0].plano_remix or {}).get('validacao_remix', {})) if individual else {}
        remix_real = bool(contrato.get('remix_valido'))
        aceito = bool(individual) and not duplicado
        checks['remix_real'] = remix_real
        aplicaveis = [v for v in checks.values() if v is not None]
        tecnico = 100 * sum(aplicaveis) / len(aplicaveis)
        titulo_checks = [bool(titulos) and all(titulos), len(titulos) == 3,
                         len(set(t.casefold() for t in titulos)) == 3,
                         bool(titulos) and all(len(t.split()) <= 8 for t in titulos)]
        titulo_score = 25 * sum(titulo_checks)
        remix_score = contrato.get('score_remix', 0)
        if not remix_real or duplicado:
            tecnico = titulo_score = remix_score = 0
        candidatos.append({
            'indice': indice, 'inicio': limites[0] if limites else None,
            'fim': limites[1] if limites else None, 'valido': aceito, 'duplicado': duplicado,
            'remix_real': remix_real, 'titulo_principal': str(item.get('titulo') or next(iter(titulos), '')),
            'alternativas': titulos, 'descricao': str(item.get('descricao', '')),
            'gancho': gancho, 'justificativa': str(item.get('justificativa_remix') or item.get('remix_motivo') or
                                                  item.get('motivo_escolha') or item.get('motivo') or ''),
            'confianca_provider': item.get('confianca'), 'estrutura': estrutura,
            'segmentos': segmentos, 'verificacoes': checks,
            'score_tecnico': round(tecnico, 2), 'score_remix': round(remix_score, 2),
            'score_titulo_estrutural': round(titulo_score, 2),
        })
    return {
        'candidatos_recebidos': len(itens), 'candidatos_validos': len(validos),
        'candidatos_rejeitados': len(itens) - len(validos),
        'remixes_validos': len(validos),
        'titulos_gerados': sum(len(_titulos(i)) for i in itens), 'rejeicoes': rejeicoes,
        'candidatos': candidatos, 'itens_validos': [asdict(v) for v in validos],
        **{campo: round(sum(c[campo] for c in candidatos) / max(1, len(candidatos)), 2)
           for campo in ('score_tecnico', 'score_remix', 'score_titulo_estrutural')},
    }


def _erro_publico(original, provider):
    erro = original if isinstance(original, ErroProvider) else erro_seguro(original, provider)
    codigo = erro.codigo
    mensagens = {401: 'Chave inválida ou sem permissão', 403: 'Chave inválida ou sem permissão',
                 429: 'Limite de chamadas atingido', 404: 'Modelo indisponível', 408: 'Timeout'}
    return {'codigo': codigo if isinstance(codigo, int) else None,
            'mensagem': mensagens.get(codigo, 'Resposta inválida ou falha de comunicação')}


class ComparacaoIA:
    """Preparar é local; executar exige confirmação explícita e orçamento disponível."""
    def __init__(self, transcricao, config, providers, *, modo='normal', max_candidatos=5,
                 reutilizar_cache=False, orcamento=None, factory=None, pasta_cache=None):
        if len(providers) not in (2, 3) or len(set(providers)) != len(providers) or any(p not in PROVIDERS for p in providers):
            raise ValueError('Selecione dois ou três providers diferentes.')
        if modo not in ('normal', 'agencia') or not 5 <= int(max_candidatos) <= 10:
            raise ValueError('Modo inválido ou máximo de candidatos fora de 5–10.')
        if not transcricao.segmentos:
            raise ValueError('Carregue uma transcrição com falas antes de comparar.')
        self.transcricao, self.config = deepcopy(transcricao), deepcopy(config).validar()
        self.providers, self.modo, self.max_candidatos = list(providers), modo, int(max_candidatos)
        self.factory = factory or obter_provider
        self.orcamento = orcamento or OrcamentoChamadas(config.max_chamadas_episodio)
        self.pasta_cache = Path(pasta_cache) if pasta_cache is not None else COMPARACOES_DIR
        self.identidade = episode_identity(transcricao)
        self.schema = editorial._RespostaAgencia
        self.prompt = editorial.prompt_agencia(
            self.transcricao.segmentos, self.max_candidatos)
        self._cache, self._chaves_cache = {}, {}
        for p in self.providers:
            from modules.agencia.remix import REMIX_RULES_VERSION
            identidade = {'episode_identity': self.identidade, 'provider': p,
                          'remix_rules_version': REMIX_RULES_VERSION,
                          'model': getattr(config, f'modelo_{p}'),
                          'prompt_version': editorial.VERSAO_PROMPT_AGENCIA if modo == 'agencia' else analise_ia.VERSAO_PROMPT,
                          'rules_version': _hash({'score': VERSAO_SCORE, 'validacao': analise_ia.VERSAO_VALIDACAO,
                              'agencia': editorial.VERSAO_PROMPT_AGENCIA, 'intro': asdict(carregar_intro(transcricao)) if carregar_intro(transcricao) else None}),
                          'analysis_type': modo, 'prompt_hash': _hash(self.prompt),
                          'schema_hash': _hash(self.schema.model_json_schema()), 'max_candidatos': self.max_candidatos}
            self._chaves_cache[p] = identidade
            try:
                cache = json.loads(self._caminho(p).read_text(encoding='utf-8'))
                if cache['identidade'] == identidade:
                    self._cache[p] = normalizar_resposta(cache['resposta'], self._campo)
            except (OSError, ValueError, KeyError, TypeError, ErroProvider):
                pass
        self.reutilizar = bool(reutilizar_cache)
        self._executada = False
        self._segredos = []

    @property
    def _campo(self):
        return 'historias'

    @property
    def caches_disponiveis(self):
        return list(self._cache)

    @property
    def chamadas_previstas(self):
        return len(self.providers) - (len(self._cache) if self.reutilizar else 0)

    def _caminho(self, provider):
        return self.pasta_cache / (_hash(self._chaves_cache[provider]) + '.json')

    def executar(self, *, confirmado=False, progresso_cb=None):
        if confirmado is not True:
            raise ValueError('A comparação exige confirmação explícita do número de chamadas.')
        if self._executada:
            raise ValueError('Esta comparação já foi executada; prepare uma nova comparação.')
        if self.chamadas_previstas > self.orcamento.restantes:
            raise LimiteChamadas('Orçamento insuficiente para a comparação; nenhuma chamada iniciada.')
        self._executada = True
        usadas_antes = self.orcamento.usadas
        resultados = {}
        for p in self.providers:
            inicio = perf_counter()
            resultado = {'provider': p, 'modelo': getattr(self.config, f'modelo_{p}'),
                         'cache_reutilizado': self.reutilizar and p in self._cache, 'erro': None,
                         'candidatos_recebidos': 0, 'candidatos_validos': 0, 'candidatos_rejeitados': 0,
                         'remixes_validos': 0, 'titulos_gerados': 0, 'score_tecnico': 0,
                         'score_remix': 0, 'score_titulo_estrutural': 0, 'json_valido': None}
            if progresso_cb:
                progresso_cb(f'Analisando {p.title()} ({len(resultados)+1}/{len(self.providers)})…')
            try:
                if resultado['cache_reutilizado']:
                    dados = self._cache[p]
                else:
                    chave = obter_chave(p)
                    if chave:
                        self._segredos.append(chave)
                    cliente = self.factory(p, modo='agencia',
                                           schema=self.schema, chave=chave)
                    self.orcamento.reservar()
                    resposta = cliente.analisar_uma_vez(resultado['modelo'], self.prompt)
                    try:
                        dados = normalizar_resposta(resposta, self._campo)
                    except ErroProvider:
                        resultado['json_valido'] = False
                        raise
                    dados = self._limpar(dados)
                    try:
                        escrever(self._caminho(p), {'identidade': self._chaves_cache[p], 'resposta': dados})
                    except OSError:
                        resultado['aviso_cache'] = 'Não foi possível salvar o cache local.'
                resultado['json_valido'] = True
                if self.modo == 'normal':
                    dados = {'candidatos': dados['historias']}
                resultado.update(avaliar_resposta(dados, self.transcricao, self.modo, self.max_candidatos))
            except Exception as erro:
                resultado['erro'] = _erro_publico(erro, p)
            resultado['latencia_ms'] = round((perf_counter()-inicio)*1000, 3)
            resultados[p] = resultado
        cenas = []
        for i, p in enumerate(self.providers):
            for q in self.providers[i+1:]:
                for a in resultados[p].get('candidatos', []):
                    for b in resultados[q].get('candidatos', []):
                        if a['inicio'] is not None and b['inicio'] is not None and mesma_cena((a['inicio'], a['fim']), (b['inicio'], b['fim'])):
                            cenas.append({'providers': [p, q], 'indices': [a['indice'], b['indice']],
                                          'mensagem': 'Mesma cena provável'})
        self.relatorio = self._limpar({
            'episodio': {'episode_identity': self.identidade, 'video_id': self.transcricao.video_id,
                         'arquivo': Path(self.transcricao.video_path).name, 'duracao': self.transcricao.duracao},
            'data_hora': datetime.now(timezone.utc).isoformat(), 'modo': self.modo,
            'max_candidatos': self.max_candidatos, 'chamadas_previstas': self.chamadas_previstas,
            'chamadas_utilizadas': self.orcamento.usadas - usadas_antes,
            'resultados': resultados, 'cenas_compartilhadas': cenas,
            'nota_scores': 'Conformidade técnica local; não mede qualidade artística. Campos ausentes no schema normal são não aplicáveis.',
        })
        return self.relatorio

    def _limpar(self, valor):
        if isinstance(valor, dict):
            return {k: self._limpar(v) for k, v in valor.items()
                    if not any(s in k.lower() for s in ('api_key', 'authorization', 'credential', 'credencial', 'secret'))}
        if isinstance(valor, list):
            return [self._limpar(v) for v in valor]
        if isinstance(valor, str):
            for segredo in self._segredos:
                valor = valor.replace(segredo, '[REDACTED]')
            return re.sub(r'(?:gsk_[A-Za-z0-9]{12,}|AIza[A-Za-z0-9_-]{20,})', '[REDACTED]', valor)
        if isinstance(valor, float) and not math.isfinite(valor):
            return None
        return valor

    def salvar(self, caminho):
        escrever(Path(caminho), self._limpar(self.relatorio))


def escolher_resultado(app, relatorio, provider):
    transcricao = app.estado.get('transcricao')
    if not transcricao or episode_identity(transcricao) != relatorio['episodio']['episode_identity']:
        raise ValueError('O episódio mudou; compare novamente antes de usar este resultado.')
    resultado = relatorio['resultados'][provider]
    if resultado.get('erro') or not resultado.get('itens_validos'):
        raise ValueError('Este resultado não contém candidatos válidos para carregar.')
    app.estado['resultado_comparacao'] = {
        'episode_identity': relatorio['episodio']['episode_identity'], 'provider': provider,
        'modo': relatorio['modo'], 'itens': deepcopy(resultado['itens_validos']),
    }
    app.navegar('agencia' if relatorio['modo'] == 'agencia' else 'lote')


def consumir_resultado(estado, modo):
    resultado = estado.get('resultado_comparacao')
    if not resultado or resultado['modo'] != modo:
        return None
    estado.pop('resultado_comparacao')
    transcricao = estado.get('transcricao')
    if not transcricao or episode_identity(transcricao) != resultado['episode_identity']:
        return None
    return resultado


def orcamento_episodio(estado, transcricao, limite):
    identidade = episode_identity(transcricao)
    registro = estado.get('orcamento_ia')
    if not registro or registro['episode_identity'] != identidade:
        registro = {'episode_identity': identidade, 'orcamento': OrcamentoChamadas(limite)}
        estado['orcamento_ia'] = registro
    # Mudança de configuração não apaga chamadas já feitas neste episódio.
    registro['orcamento'].maximo = int(limite)
    return registro['orcamento']


def provider_orcado(estado, transcricao, config, *, modo='analise', schema=None):
    """Compartilha com a comparação o orçamento dos dois fluxos de análise da UI."""
    budget = orcamento_episodio(estado, transcricao, config.max_chamadas_episodio)
    if config.fallback_automatico:
        from .fallback import ProviderFallback
        return ProviderFallback(config, modo=modo, schema=schema, orcamento=budget)

    class ProviderIndividual:
        gerencia_orcamento = True

        @property
        def restantes(self):
            return budget.restantes

        def listar_modelos(self):
            return [config.modelo]

        def analisar(self, modelo, prompt):
            cliente = obter_provider(config.provider_ativo, modo=modo, schema=schema)
            budget.reservar()
            return cliente.analisar_uma_vez(modelo, prompt)

    return ProviderIndividual()
