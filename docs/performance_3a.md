# Fase 3A: profiler e baseline local

Data: 2026-10-08. Somente instrumentacao, sem otimizar processamento.
Fases 3B, 3C, 3D e 3E NAO implementadas.

## Medicoes reais

FFmpeg real; presets atuais preservados. Fonte testsrc2 640x360/30 FPS,
31 segundos com sine/AAC. Plano remix completo de aproximadamente 29 segundos
passou no gate editorial; preview e render tecnicos usam um excerto de 4 segundos.
Saida final 1080x1920/60 FPS, libx264 slow/CRF17; preview atual
1080x1920/30 FPS/ultrafast. Sem modificar filtros, fontes ou tratamento AV.

| Etapa | Baseline |
| --- | ---: |
| Importacao dos modulos usados pelo benchmark | 0,431 s |
| Leitura de cache existente, 460 segmentos | 0,007 s |
| Preparacao do prompt com esse cache | <0,001 s |
| Validacao local de titulos sinteticos, sem geracao IA | 0,002 s |
| Preview 1, incluindo preparacao e gate | 6,204 s |
| Preview 2, nova execucao do mesmo plano | 5,929 s |
| Preparacao do corte para o render final | 0,461 s |
| Construcao do comando/filtergraph final | 0,002 s |
| Render final do excerto de 4 s | 8,056 s |
| Quality Gate de frames do render final | 2,546 s |
| ffprobe da saida | 0,099 s |
| Gate tecnico da exportacao | <0,001 s |
| Campanha sintetica completa, incluindo dois previews e teste sem fala | 25,985 s |

O segundo preview ainda gastou 3,71 s renderizando e 1,78 s verificando frames.
Preparacao repetida levou 0,44 s: ffprobe e tres extracoes de frame continuam
ocorrendo. Isso e evidencia de repeticao, NAO melhoria implementada.
Os dois previews, o gate editorial e o gate final foram aprovados.
Arquivo final do excerto: 2.396.365 bytes. Nenhum arquivo exportado de usuario foi alterado.

## Transcricao com fala

Amostra de 50,796 s sintetizada LOCALMENTE pela voz Windows Maria/pt-BR.
Whisper small local, CPU/int8, 6 threads, beam_size=5, VAD e word_timestamps,
configuracao original. Resultado: 18 segmentos, 81 palavras com timestamps.

| Etapa | Baseline |
| --- | ---: |
| Transcricao completa, incluindo carregar modelo e salvar JSON | 13,430 s |
| Carregamento do modelo/importacao da biblioteca | 1,529 s |
| Decode/VAD/preparacao inicial do Whisper | 0,282 s |
| Restante exclusivo: inferencia, consumo e conversao dos segmentos | 11,604 s |
| Escrita do cache | 0,016 s |
| Reutilizacao do cache, chamada publica completa | 0,0011 s |

A preparacao de audio inclui VAD/preparacao interna, nao e uma medicao isolada
do decoder. A biblioteca devolve um gerador; a inferencia acontece durante seu
consumo, incluido no tempo da transcricao, e nao apenas em whisper.transcribe().
Fala sintetizada nao representa episodio com musica, ruido e multiplos personagens.
Nao extrapolar linearmente esses numeros para episodios inteiros.
O teste sine retorna zero falas e NAO e usado como baseline de inferencia.
Modelo estava ausente da memoria do processo, mas com arquivos locais/OS cacheados.
Variacao de aquecimento/importacao observada; nao foi otimizada nem contada como ganho.

Nao houve chamadas reais de IA ou downloads. Latencia de Story Hunter e de
geracao/correcao de titulos por IA permanece NAO MEDIDA; os caminhos estao
instrumentados para a proxima execucao normal. Preparacao e validacao locais
nao devem ser apresentadas como tempo de geracao IA. Pos-processamento de
exportacao e movimento estao instrumentados, mas nao exercitados integralmente
por este benchmark. O total da campanha NAO e o total de um episodio.

## Prioridades Baseadas No Baseline

| Gargalo | Tempo observado | Potencial, ainda nao comprovado | Risco | Dificuldade |
| --- | --- | --- | --- | --- |
| Preview repetido | 5,929 s/4 s | Evitar trabalho identico via cache compativel | Baixo/medio: chave e invalidacao | Media |
| Preparacao/frame probes repetidos | 0,44-0,66 s/preparacao | Reuso seguro de metadados/centro | Baixo/medio | Media |
| Quality Gate de frames | 1,78 s preview; 2,546 s final | Consolidar extracoes, mantendo todas as verificacoes | Medio: seek e primeiro frame | Media |
| Inferencia Whisper | 11,604 s/50,796 s de fala sintetica | Benchmark batched/beam/threading | Alto: limites de fala e sincronismo | Alta |
| Render slow + composicao | 8,056 s/4 s | Comparar presets, filtros e NVENC separadamente | Alto: qualidade/estabilidade | Alta |
| IA/titulos por API | Nao medido | Nao quantificar sem latencias reais | Editorial/orcamento | Media |

Ordem de implementacao continua a solicitada: 3B transcricao, 3C preview/cache,
3D renderer, 3E exportacao limpa. Dentro de cada subfase, priorizar reuso e
mudancas comprovadas pelo MESMO benchmark. Nao eliminar Quality Gate.

## Instrumentacao

PerformanceProfiler usa perf_counter e ContextVar, isolado por worker/thread.
Registra operacao, duracao inclusiva/exclusiva, hierarquia, tipo de erro,
backend, encoder/preset/CRF/FPS, modelo/provider, cache e arquivos reutilizados
quando disponiveis. Nunca registra prompts, transcricoes, chaves ou mensagens
de excecao. Caminhos locais de arquivos fazem parte dos metadados solicitados.
Sem CPU/RAM/GPU polling, telemetria externa ou atualizacao direta de widgets.

Uma sessao por TarefaRenderizacao; JSON local em dados/performance, fora da
pasta final. Escrita ocorre uma vez ao terminar a tarefa, inclusive erro/cancelamento;
falha de disco no profiler nao muda resultado/callback do processamento.
Funcoes chamadas sem sessao nao gravam nada. Para benchmarks diretos, abrir
explicitamente `with PerformanceProfiler(...)`.
Somar tempos exclusivos ou raizes, NUNCA pais e filhos inclusivos juntos.

Microbenchmark de 10.000 chamadas vazias: 0,0038 s sem sessao e 0,1341 s com
sessao (aproximadamente 13 microssegundos/chamada adicional nesta execucao).
Nao e uma garantia de custo em todos os fluxos; escrita JSON nao incluida.

Arquivos de runtime alterados exclusivamente para instrumentacao:
- modules/performance.py (novo), modules/tasks.py, modules/transcricao.py;
- modules/importacao.py, modules/renderer.py, modules/lote.py;
- modules/agencia/renderer.py, pipeline.py, qualidade.py, timeline.py, layout.py;
- modules/agencia/remix.py, editorial.py, recuperacao.py, metricas.py;
- modules/ai/cache_fallback.py, modules/titulos_v2.py.

Suporte novo: tests/test_performance.py, scripts/baseline_performance.py,
scripts/baseline_fala_performance.py e este documento.
Regras remix/titulos, schemas editoriais, cache keys, fallback/orcamento,
presets e arquivos finais preservados. Nenhuma otimizacao aplicada.

## Reproducao E Verificacao

Executar na raiz GeradorDeCortes, sem renders/testes concorrentes:

```powershell
.\.venv\Scripts\python.exe -B scripts/baseline_performance.py
.\.venv\Scripts\python.exe -B scripts/baseline_fala_performance.py
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_performance.py
```

Relatorios detalhados: temp/performance_3a/baseline.json e baseline_fala.json;
artefatos controlados permanecem nessa pasta interna. A fala requer Windows
SAPI/Maria; o Whisper requer o snapshot small local ja existente.
Scripts utilizam offline HF; o benchmark principal bloqueia conexoes socket.

11 testes novos: aninhamento/exclusividade, transparencia sem sessao,
erro/contexto, cache hit/miss, isolamento de threads, sessoes aninhadas,
JSON local, erro de disco, callbacks, cancelamento e erro do worker.
Regressao: 401 testes nao pertencentes a test_agencia_render_real passaram.
Verificacao final focada: 89 testes profiler + Titulos V2 + Remix Universal passaram.
Benchmarks reais adicionais: dois previews e render slow com gates aprovados.
A suite antiga de oito exports completos nao foi reexecutada nesta subfase;
o excerto de 4 s nao substitui uma validacao integral de exportacao de episodio.

Antes/depois de otimizacao: NAO APLICAVEL nesta subfase. Ganho percentual:
NAO MEDIDO; nenhuma aceleracao declarada. Nenhuma regressao observada nos
testes executados. Encerrada somente a Fase 3A; aguardar proxima subfase.
