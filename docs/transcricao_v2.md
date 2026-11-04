# Fase 3B - Transcricao V2

Data: 2026-10-08. Escopo limitado a transcricao, cache correspondente,
editor/seletores da aba Transcrever, benchmarks e testes. Profiler 3A reutilizado.
Renderer, FFmpeg/presets, Quality Gate, Remix Validator, titulos e providers
editoriais NAO foram alterados. Fases 3C/3D/3E nao iniciadas.

## Resultado Importante

Nao foi encontrada aceleracao local aprovada nos limites de timestamps.
AUTO LOCAL mantem small/CPU/int8, 6 threads, beam_size=5, VAD,
condition_on_previous_text=True e timestamps de palavras.

RAPIDO usa a configuracao calibrada e aprovada quando disponivel. Nesta maquina,
a calibracao selecionou Preciso: RAPIDO retorna esse fallback e mostra o aviso
correspondente. Configuracoes experimentais mais rapidas ficaram restritas ao
benchmark/override explicito, e NAO viraram padrao da UI ou do AUTO.
Nao se declarou ganho de velocidade sacrificando timestamps.

## Benchmark Real

Mesmo arquivo temp/performance_3a/fala_pt.wav, 50,79625 s, voz SAPI Maria pt-BR.
Modelo small local, faster-whisper 1.2.1. Intel i5-10400F, 6 cores/12 threads,
aproximadamente 16 GiB RAM. Nenhum download ou chamada real de API.
Execucao sequencial de 18 configuracoes, duas transcricoes completas por configuracao.

| Configuracao | Tempo com modelo quente | Qualidade relativa ao Preciso |
| --- | ---: | --- |
| Standard, beam 5, 6 threads (Preciso) | 11,55 s | Aprovada |
| Standard, beam 5, 4 threads | 12,20 s | Aprovada, mas mais lenta |
| Standard, beam 5, 8 threads | 12,38 s | Aprovada, mas mais lenta |
| Standard, beam 1/contexto anterior, 4/6/8 threads | 8,49 / 8,50 / 8,65 s | Rejeitada |
| Standard, beam 1/sem contexto, 4/6/8 threads | 8,17 / 7,98 / 8,39 s | Rejeitada |
| Batched, beam 1/batch 4, 4/6/8 threads | 7,44 / 6,98 / 7,50 s | Rejeitada |
| Batched, beam 1/batch 8, 4/6/8 threads | 7,14 / 6,86 / 7,29 s | Rejeitada |
| Standard, beam 2/3/4, 6 threads/contexto anterior | 9,05 / 9,61 / 10,40 s | Rejeitada |

Mais rapida experimental: batched, CPU/int8, 6 threads, beam 1, batch 8,
contexto anterior desativado. 6,86 s contra 11,55 s quente (~40,6% menos tempo),
MAS p95 de diferenca temporal de palavras de 1,60 s, maximo de 2,50 s,
e 2 segmentos em vez de 18. Nao aprovada para AUTO.
Beam 1 standard preservou texto, mas alterou timestamps em ate 3,18 s.
Beams 2-4 tambem ultrapassaram os limites (maximo de palavras: 2,54 s).

O ranking usa a SEGUNDA execucao de cada configuracao, com modelo carregado:
comparar medias misturando carga inicial e reuso de modelo favoreceria
artificialmente configuracoes executadas depois. A primeira matriz teve essa
limitacao; o ranking final a corrigiu e selecionou 6 threads/beam 5.
As duas primeiras medicoes de Preciso foram 14,154 s e 11,550 s.

### Antes / Depois: Primeira Transcricao Publica AUTO

Validacao adicional em processo novo, cache isolado, mesmo audio; carga do
modelo incluida, arquivos do modelo ja presentes em disco, profiler 3A ativo.

| Medida | Tempo |
| --- | ---: |
| Antes, baseline 3A | 13,430 s |
| Depois, AUTO LOCAL aprovado | 14,306 s |
| Ganho observado | -6,53% (mais lento nessa execucao) |
| Cache hit depois | 0,00270 s |
| Cache hit 3A | 0,00110 s |

Esse resultado NAO demonstra aceleracao da primeira transcricao. A configuracao
efetiva continuou igual; carga inicial/variabilidade e alteracoes de infraestrutura
nao foram isoladas causalmente. Nao atribuir o numero a uma otimizacao inexistente,
nem esconder a piora observada. Cache continua em poucos milissegundos, sem carregar
modelo ou enviar audio. O ensaio de cache da matriz tambem mediu 0,00185 s.

## Limites De Qualidade

Comparacao ordenada de palavras/frases contra Preciso, com normalizacao de
maiusculas/pontuacao, pareamento SequenceMatcher sem autojunk e medidas
de inicio/fim de palavras e segmentos. Limites definidos ANTES da selecao:
- erro relativo de palavras <= 5%; palavras perdidas/extras <= 3% cada;
- diferenca media de palavras <= 0,080 s; p95 <= 0,200 s; maximo <= 0,350 s;
- diferenca media de segmentos <= 0,150 s; maximo <= 0,350 s;
- mesma segmentacao textual e duracao (tolerancia 0,100 s);
- tempos finitos, nao negativos, ordenados e dentro de audio/segmento;
- nao introduzir mais palavras de duracao zero que a referencia.

Preciso/AUTO final contra a transcricao salva na 3A: zero palavras perdidas,
zero extras; texto, 18 segmentos e 81 palavras identicos; diferencas media,
p95 e maxima de palavras/segmentos iguais a 0,000 s; mesma duracao.
Nao foram observadas falas textuais novas ou removidas na configuracao final.

Limite do ensaio: o proprio Preciso tem quatro palavras de duracao zero nessa
amostra. Foram preservadas, nao corrigidas ou inventadas. A comparacao prova
compatibilidade RELATIVA com a referencia, nao precisao absoluta/humana.
Fala sintetizada nao representa dialogo real com ruido, musica e personagens.
O batch nessa amostra curta nao atinge necessariamente 4/8 chunks simultaneos;
nao extrapolar throughput ou uso de memoria para episodio completo.

## Modos, Cache E Edicao

PRECISO preserva comportamento/configuracao anteriores. AUTO nunca benchmarka
automaticamente: le dados/transcricao_v2/auto_local.json, verifica maquina,
versoes faster-whisper/CTranslate2, modelo, configuracao e aprovacao.
Sem calibracao compativel, retorna Preciso local. Falha de AUTO rapido faz
fallback somente para Preciso; nunca troca automaticamente para API.

O loader de modelo separa o cache em memoria por modelo/device/compute/threads.
Pipeline batched oficial e criado por requisicao, sem compartilhar seu estado
mutavel de timestamps entre transcricoes.

Transcricao registra backend, modelo, modo, idioma, parametros e tempos.
Caches antigos Preciso conservam nomes e leitura. Configuracoes incompativeis
possuem chaves diferentes; metadados/origem sao verificados antes de reutilizar.
AUTO pode aproveitar cache Preciso compativel mesmo com configuracao rapida
calibrada. Rapido explicito nao mistura silenciosamente resultados Preciso.
Cache Groq nunca passa por cache CPU. Nada foi apagado ou migrado destrutivamente.

Edicao de texto OU timestamps de segmento invalida apenas as palavras desse
segmento, salva word_timestamps_invalidated=true e remove as palavras antigas.
Segmentos intactos mantem suas palavras/flags. Consumers existentes ja usam
timestamps de segmento quando a lista de palavras esta vazia; teste de legenda
confirma esse caminho. Nenhum timestamp de palavra editada foi inventado,
nenhum realinhamento ou retranscricao integral foi acionado.

## Groq Opcional E UI

Adaptador independente de transcricao usando SDK oficial ja instalado,
whisper-large-v3-turbo, verbose_json, granularidades word+segment.
Solicita autorizacao explicita ANTES de criar cliente/enviar audio. UI confirma
envio/cobranca; Groq aparece somente quando existe credencial configurada.
AUTO LOCAL nunca chama Groq. API aceita auto_nuvem com autorizacao explicita,
mas permanece LOCAL porque nao existe calibracao cloud validada nesta entrega.
Nao existe selecao silenciosa de nuvem por credencial presente.

Prepara somente audio FLAC/16 kHz/mono via PyAV em pasta temporaria; nao envia
video ou outras faixas. Limite conservador de 25 MiB; se excedido, nao envia,
e usa fallback local conforme configuracao. Nao implementou chunking remoto
nem troca automatica para outro modelo/API. Artefatos temporarios sao limpos.
Conversao LOCAL real da amostra: 50,79625 s, FLAC de 642.881 bytes.
Nao mediu velocidade/qualidade/custo da API; nenhuma requisicao real foi feita.

Erros do SDK usam erro_seguro existente, sem publicar credenciais/payload.
Fallback local e configuravel (fallback_local=False preserva erro seguro).
Cancelamento checado antes/depois de carga/decode/chamada e entre segmentos;
nao grava transcricao parcial e nao faz fallback apos cancelamento.
Chamada cloud sincrona tem timeout de 90 s, sem retries: cancelamento durante
o transporte e cooperativo ao retornar/timeout, nao interrompe upload ja enviado.

Aba Transcrever tem Modo Auto/Rapido/Preciso e Backend Auto local/CPU/Groq
quando configurado. Mostra modelo/backend efetivo, audio, tempo e fator realtime
(nao calcula realtime enganoso para cache). Continua usando TarefaRenderizacao,
fila, queue de eventos e after; nenhum widget atualizado pelo worker.
Seletores ficam desabilitados durante transcricao e retornam ao fim/erro.
Layout dos tres controles verificado em Tk transparente: sem sobreposicao
em janela logica 900x700 (1350x1050 fisicos no DPI desta maquina).

## Duas Passagens

Experimento controlado: 1a passagem candidata + 2a Preciso nos intervalos
0-10, 20-30 e 40-50 s (30 s aprovados hipoteticamente de 50,8 s).
Segunda passagem, incluindo decode: 10,449 s. Total usando primeira passagem
aprovada quente: 21,999 s contra 11,550 s de Preciso quente (~90,5% mais caro).
Mesmo usando primeira passagem batched rejeitada de 6,858 s, total estimado
de 17,307 s ainda supera 11,550 s. NAO ativado como padrao.

Nao havia arquivo de episodio real acessivel nos caminhos dos caches consultados;
portanto NAO se mediu economia em episodio tipico. O ensaio de amostra nao prova
que a tecnica nunca beneficia episodios longos: exige medir episodio real e a
fracao/quantidade de trechos selecionados. Experimento nao alterou o pipeline
editorial nem publica intervalos refinados com offsets inadequados.

## Arquivos E Verificacao

Runtime alterado: modules/transcricao.py e ui/tela_transcricao.py.
Runtime novo: modules/transcricao_v2.py, modules/transcricao_benchmark.py,
modules/transcricao_groq.py.
Suporte novo: tests/test_transcricao_v2.py, scripts/benchmark_transcricao_v2.py,
scripts/validar_transcricao_v2.py e este documento.
Calibracao local gerada: dados/transcricao_v2/auto_local.json.
Resultados/artefatos internos: temp/performance_3b, especialmente benchmark.json,
parcial.json e validacao_final.json. Pasta final nao foi alterada.

Reproduzir de forma sequencial, sem outros renders/benchmarks:

```powershell
.\.venv\Scripts\python.exe -B scripts/benchmark_transcricao_v2.py
.\.venv\Scripts\python.exe -B scripts/benchmark_transcricao_v2.py --continuar --persistir-auto
.\.venv\Scripts\python.exe -B scripts/validar_transcricao_v2.py
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_transcricao_v2.py
```

40 testes novos: configuracoes, calibracao/maquina/modelo, cache/legado/origem,
modelo por threads, cancelamento/progresso, edicao granular/legenda por segmento,
consentimento, nuvem opcional, fallback, cancelamento/payload/resposta Groq,
erros seguros, comparacao de qualidade e callbacks UI enfileirados.
441 testes de regressao passaram em 34,647 s, com sockets de rede bloqueados.
Regressao automatizada sem rede inclui integralmente os testes das Fases 1/2
e profiler 3A. Suite antiga de oito renders completos nao reexecutada porque
renderer/FFmpeg/presets/gates nao mudaram; nao declarar validacao real de Groq.

Referencia oficial consultada e API instalada inspecionada:
- [faster-whisper: batched e timestamps](https://github.com/SYSTRAN/faster-whisper)
- [Groq: Speech To Text](https://console.groq.com/docs/speech-to-text)

Encerrar nesta subfase. A reducao significativa da primeira transcricao NAO foi
alcancada sob os limites exigidos; mantida a configuracao segura e registrado
o impedimento medido, sem promover resultados rapidos mas incompativeis.
