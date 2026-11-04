# Upgrade Temporal Da Transcricao

## Decisao Final

AUTO LOCAL e AUTO HIBRIDO preservam Preciso nesta maquina. AUTO COM NUVEM tambem
fica local sem calibracao remota aprovada. Nenhuma configuracao experimental foi
promovida. Nao se atingiu a meta de 30% de reducao com qualidade aprovada.

Preciso continua small, CPU/int8, seis threads, beam 5, VAD, contexto anterior e
word timestamps. Renderer, FFmpeg, titulos, Remix Validator, Agencia, exportacao,
perfis visuais e providers editoriais nao foram modificados nesta fase.

## Investigacao Do Batched

Inspecao do faster-whisper 1.2.1 instalado e comparacao do mesmo audio:

- Batched usa without_timestamps=True por padrao; Standard usa False.
  A chamada antiga nao explicitava essa diferenca. Isso prejudicava fronteiras
  de segmentos: a amostra antiga tinha dois segmentos em vez de dezoito.
- Batched ignora condition_on_previous_text e registra False internamente.
  A configuracao agora exige False explicito para nao prometer contexto ausente.
- VAD batched usa silencio minimo de 160 ms e limita chunks a 30 s; Standard
  usa 2000 ms. Padding padrao de ambos e 400 ms. Esses parametros agora podem
  ser fixados separadamente, sem alterar os defaults do Preciso.
- A biblioteca soma offset do chunk e recompõe os intervalos removidos pelo VAD.
  O aplicativo apenas converte os valores entregues em float. Nao havia soma
  extra de seek, offsets relativos tratados como absolutos ou arredondamento
  local capaz de explicar os 2,50 s.
- Tokens de timestamp corrigiram varias fronteiras reais, mas NAO eliminaram
  o desvio de palavras da amostra antiga. O erro foi reproduzido com batch
  1/2/4/8 e beam 5. Sem VAD, o pior erro de palavras caiu a 0,48 s, mas houve
  diferencas textuais e fronteiras de segmento ainda inaceitaveis.

Conclusao: ha divergencia de chunking/segmentacao e alinhamento na biblioteca,
nao um offset perdido na conversao do aplicativo. Nao foi isolado um unico
componente interno como causa causal exclusiva do desvio de 2,50 s.

## Contrato E Metricas

Benchmark registra medias de inicio/fim separadas, media combinada, p95, pior
erro, palavras ausentes/extras, WER aproximado, texto normalizado, duracoes,
segmentos deslocados/criticos e problemas estruturais. Segmentos unidos sao
medidos nas suas fronteiras reais; nao se finge que foram separados por palavra.

Limites relativos ao Preciso, definidos para esta fase:

| Medida | Media | P95 | Pior |
| --- | ---: | ---: | ---: |
| Fronteiras de segmentos | 0,20 s | 0,50 s | 0,80 s |
| Fronteiras de palavras | 0,15 s | 0,40 s | 0,80 s |

Mantidos controles textuais de 5% e perdas/extras de 3% cada. Ha verificacao de
regressao, sobreposicao, offset explicito, limites do audio/chunk, cobertura de
palavras e novas palavras sem duracao. O benchmark legado continua disponivel
em comparar_legado; os limites desta fase estao versionados separadamente.

As medias/p95 usam pontos de inicio e fim, nao um suposto alinhamento humano.
Fronteiras humanas podem ser fornecidas no manifesto por segmento de referencia;
o pareamento textual local encontra a fronteira candidata correspondente.
Sem anotacao, ground_truth_disponivel=false. Preciso nao e verdade absoluta.

Cada segmento novo recebe timestamp_confidence de 0-100 e sinais locais:
duracao/compressao das palavras, gaps, overlaps, probabilidade, VAD quando
disponivel e consistencia de limites. Esse score NAO prova precisao absoluta:
um deslocamento sistematico pode parecer internamente consistente.

## Benchmark Executado

Fonte real: Family.Guy.S06E11.mp4, ja disponivel localmente. Seis recortes de
15 s: 65-80, 170-185, 300-315, 500-515, 700-715 e 950-965 s. Tambem foi reutilizada
a amostra SAPI Maria de 50,79625 s da Fase 3A, para reproduzir o erro conhecido.
Matriz sequencial de 16 configuracoes; duas execucoes por amostra quando o
backend conseguiu concluir. Rede bloqueada; modelos ja presentes em disco.

LIMITACAO DO CORPUS: os seis recortes confirmam animacao real, mas nao receberam
rotulacao humana de sobreposicao, musica ou gritos. Nao se declarou cobertura
verificada das sete categorias pedidas. A ferramenta aceita esse corpus por
manifesto, e o AUTO exige cobertura real verificada antes de promover aceleracao.
Tambem nao ha marcacoes humanas de inicio/fim real de fala nestas amostras.

Tabela abaixo: mesmo audio de 50,8 s, modelos quentes, ultima repeticao.
Media/P95/pior sao erros de PALAVRAS contra Preciso. Aprovacao inclui segmentos,
texto e corpus completo, nao so essas tres colunas.

| Configuracao | Tempo | Ganho | Media | P95 | Pior | Aprovado? |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Preciso | 11,662 s | 0% | 0 | 0 | 0 | SIM, referencia |
| Batched antigo | 7,223 s | 38,1% | 0,138 s | 1,600 s | 2,500 s | NAO |
| Tokens, batch 1 | 8,180 s | 29,9% | 0,138 s | 1,600 s | 2,500 s | NAO |
| Tokens, batch 2 | 7,465 s | 36,0% | 0,138 s | 1,600 s | 2,500 s | NAO |
| Tokens, batch 4 | 7,457 s | 36,1% | 0,138 s | 1,600 s | 2,500 s | NAO |
| Tokens, batch 8 | 7,627 s | 34,6% | 0,138 s | 1,600 s | 2,500 s | NAO |
| Beam 5, VAD Preciso | 9,848 s | 15,6% | 0,134 s | 1,570 s | 2,510 s | NAO |
| VAD silencio 500 | 9,696 s | 16,9% | 0,138 s | 1,600 s | 2,500 s | NAO |
| VAD padding 200 | 9,991 s | 14,3% | 0,132 s | 1,570 s | 2,510 s | NAO |
| VAD threshold 0,35 | 9,624 s | 17,5% | 0,134 s | 1,570 s | 2,510 s | NAO |
| VAD fala minima 100 | 9,732 s | 16,5% | 0,134 s | 1,570 s | 2,510 s | NAO |
| VAD chunks 10 | 18,757 s | -60,8% | 0,144 s | 1,560 s | 2,500 s | NAO |
| Sem VAD, beam 5 | 9,394 s | 19,4% | 0,007 s | 0,020 s | 0,480 s | NAO |
| Hibrido suspeitos | 19,060 s | -63,4% | 0,144 s | 1,560 s | 2,500 s | NAO |
| GPU int8_float32 | indisponivel | - | - | - | - | NAO |
| GPU float32 | nao executado apos falha CUDA | - | - | - | - | NAO |
| Groq Whisper | nao autorizado/executado | - | - | - | - | NAO, sem calibracao |
| Gemini Transcribe | nao autorizado/executado | - | - | - | - | NAO, sem calibracao |

Em cinco recortes reais, beam 5/VAD Preciso passou na qualidade relativa, mas
os ganhos foram aproximadamente -0,1% a 5,1%. Outro recorte teve timestamps
estruturalmente invalidos. Nenhuma configuracao passou no conjunto inteiro.

RAM: pico de working set do processo, nao consumo exclusivo da inferencia.
Preciso ~605 MiB; batches 2/4/8 ~734-735 MiB; chunks 10/hibrido ~992-994 MiB.
Amostras curtas nao exercitam oito chunks simultaneos; nao extrapolar esses
tempos/memoria para throughput de episodio completo.

## GPU

GTX 1050 Ti, 4096 MiB, driver 582.78; CTranslate2 4.8.2 detectou um device CUDA
e reportou int8, int8_float32 e float32. Na consulta inicial, ~1676 MiB estavam
ocupados por outros aplicativos. O modelo small carregou, mas o encoder falhou
por falta de cublas64_12.dll. Driver presente NAO significa runtime cuBLAS pronto.
Nao ha medidas validas de tempo/VRAM de inferencia ou estabilidade. Nao se
afirma que essa GPU e lenta; ela nao estava utilizavel neste ambiente.
Nenhum pacote, driver ou DLL foi instalado; fallback CPU foi testado.

## Realinhamento

Whisper find_alignment usa somente audio local e tokens do texto fornecido.
Nao chama transcribe nem gera um segundo texto. Refina trechos suspeitos,
limitados a 30 s e a 35% da duracao total, incluindo padding. Sem melhora dos
sinais locais, texto preservado ou compatibilidade com vizinhos, descarta.

No ensaio hibrido, um trecho real foi tentado e rejeitado por conflito com a
fala vizinha. Outros grandes desvios nao foram detectados pelos sinais locais,
confirmando a limitacao do score de confianca.

Experimento adicional ORACLE (referencia conhecida, nao disponivel em producao):
realinhou somente o segmento 8, ~10,9% do audio. Custo adicional 0,383 s;
total estimado 19,140 s. P95 passou de 1,56 para 1,55 s, mas o pior erro aumentou
de 2,50 para 2,53 s. Continua reprovado. Nao ativado no AUTO.

O hibrido foi reexecutado com o codigo final em dois recortes reais e na amostra
antiga: continuou reprovado. Tempos quentes de 7,311 / 7,225 / 19,998 s.

Edicoes manuais continuam invalidando palavras do trecho alterado. O botao
Realinhar trecho pede o indice e usa uma copia em background. Cancelar a escolha
nao salva alteracoes; edicoes invalidadas nunca sao refinadas implicitamente.
Nao se retranscreve o episodio inteiro nem se inventam timestamps de palavras.

## AUTO, Cache E Nuvem

Perfis por maquina/modelo/configuracao, com data, versoes e benchmark. Perfil
rapido exige duas repeticoes, medidas aprovadas, sete categorias reais verificadas
e ganho agregado >=30%. Perfis locais, hibridos e remotos sao filtrados conforme
o modo solicitado. Sem perfil valido, usa Preciso. Nao benchmarka ao transcrever.

Cache diferencia backend/modelo/modo/batch/VAD/alinhamento/idioma/regras e
parametros de chunking. Nomes e metadados legados Preciso continuam reutilizaveis.
Cache batched antigo nao e confundido com a nova configuracao de timestamps.
Nenhum cache ou arquivo original do usuario foi apagado.

Groq continua opcional, whisper-large-v3-turbo. Gemini e um adaptador separado
para gemini-3.5-transcribe via Interactions/verbatim e anotacoes word_info.
Segmentos Gemini sao derivados dos limites reais das palavras e pontuacao/gaps;
nao sao timestamps nativos de segmentos prometidos por essa API. Resposta sem
offsets para todo o texto e recusada, sem solicitar tempos a um modelo editorial.
SDK/modelo indisponivel permite fallback local configuravel, nunca outra nuvem.

Consentimento e exigido por requisicao, antes de upload/criacao do cliente.
Leitura de cache na UI nao envia audio. AUTO LOCAL/HIBRIDO recusam override remoto
mesmo com credencial presente. AUTO COM NUVEM exige consentimento e benchmark.
Gemini remove o arquivo remoto ao final, com tentativa de limpeza best effort.
Cancelamento do transporte sincrono e cooperativo apos retorno/timeout de 90 s.

Benchmark remoto mede preparacao e transporte. Groq nao expoe upload/inferencia
separados no SDK: esses tempos e custo/cota ficam explicitamente desconhecidos,
nao inventados. Gemini separa upload/preparacao remota e inferencia/transporte;
custo/cota tambem exigem verificacao real. Nenhuma chamada remota foi realizada.

## Antes / Depois E Verificacao

Processo novo, audio antigo, modelo inicialmente descarregado:

| Medida | Resultado |
| --- | ---: |
| Baseline historico 3A | 13,430 s |
| AUTO final frio | 14,470 s |
| Ganho observado | -7,75% |
| Cache hit | 0,00203 s |
| Erros de palavras e segmentos contra 3B | media/p95/pior = 0 |

Nao houve aceleracao da primeira transcricao. Variabilidade de carga nao foi
isolada causalmente; nao atribuir essa diferenca a uma otimizacao inexistente.

528 testes de regressao aprovados com conexoes bloqueadas, incluindo 40 novos
testes temporais e os consumidores de gancho, legendas, remix, SRT e timeline.
84 testes de transcricao aprovados. Oito testes historicos de render real nao
executados nesta fase. Interface Transcrever verificada com Tk real: seletores
e botao de realinhamento construidos e texto do botao cabe no controle.

## Ferramentas E Artefatos

- scripts/benchmark_transcricao_temporal.py: matriz, manifestos, RAM, GPU e nuvem.
- scripts/benchmark_realinhamento_temporal.py: experimento de desvios conhecidos.
- scripts/validar_transcricao_temporal.py: AUTO frio e cache isolados.
- temp/benchmark_temporal/benchmark.json: matriz original detalhada.
- temp/benchmark_temporal/validacao_hibrido/benchmark.json: verificacao final.
- temp/benchmark_temporal/realinhamento_oracle.json: custo/resultado do alinhamento.
- temp/benchmark_temporal/validacao_auto/resultado.json: AUTO e cache.

Exemplo local, sem nuvem:

```powershell
.\.venv\Scripts\python.exe -B scripts/benchmark_transcricao_temporal.py --manifesto temp/benchmark_temporal/amostras.json --saida temp/novo_benchmark
```

Nuvem somente sob comando explicito, com consentimento para cada arquivo do
manifesto (NAO executado nesta entrega):

```powershell
.\.venv\Scripts\python.exe -B scripts/benchmark_transcricao_temporal.py --manifesto temp/benchmark_temporal/amostras.json --backend-nuvem groq_whisper --autorizar-envio-audio --saida temp/benchmark_groq
```

Trocar o backend por gemini_transcribe para o ensaio Gemini. Sem a flag de
autorizacao, o comando termina antes de enviar audio. --persistir-auto salva
somente a configuracao elegivel; nao promove um candidato rapido reprovado.

Fontes oficiais consultadas, alem do codigo instalado:
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- [CTranslate2: hardware](https://opennmt.net/CTranslate2/hardware_support.html)
- [Groq Speech To Text](https://console.groq.com/docs/speech-to-text)
- [Gemini Transcribe](https://ai.google.dev/gemini-api/docs/transcribe)

Fase encerrada com padrao seguro preservado; aceleracao com os limites exigidos
e cobertura completa do corpus continuam nao demonstradas.
