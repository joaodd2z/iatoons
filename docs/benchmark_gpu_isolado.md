# GPU: experimento isolado de transcricao

Data: 2026-10-08. GTX 1050 Ti, 4096 MiB, driver 582.78, compute capability
6.1; Python 3.14.7, CTranslate2 4.8.2, faster-whisper 1.2.1.

## Decisao

GPU funcional, mas configuracao REJEITADA para uso com os gates atuais.
Preciso e AUTO continuam inalterados. Nenhuma instalacao, mudanca global de
PATH, alteracao no Registro ou copia/modificacao de DLL foi feita.
Somente ferramenta experimental, testes e este documento foram adicionados.
Resultados ficam em temp/benchmark_gpu_isolado; nenhum perfil foi promovido.

## Isolamento e smoke test

Runtime fornecido explicitamente por --runtime, sem descoberta silenciosa:
C:\Users\Bruna\AppData\Local\Programs\Ollama\lib\ollama\cuda_v12.
As tres DLLs existentes foram apenas referenciadas pelo PATH dos processos
filhos. O ambiente do processo pai e o ambiente persistente nao mudam.

small/cuda/int8_float32 passou no primeiro smoke de cinco segundos de audio
real: carregamento 11,118 s, inferencia 2,973 s. Sem OOM, DLL missing ou crash.
Nao foram tentados int8 e float32, conforme a regra de parar no primeiro modo
estavel. float16 e int8_float16 nao sao suportados pelo stack nessa GPU.

Rede bloqueada por mocks de socket; HF offline; nenhuma API externa chamada.
CPU e GPU executadas sequencialmente em processos separados, sem modelo GPU
pesado adicional carregado pelo experimento. Aplicativos existentes nao foram
encerrados; VRAM nao estava completamente livre.

## Benchmark

Mesmo manifesto da fase anterior: seis trechos reais de 15 s e fala_pt.wav
de 50,79625 s. Tres execucoes por amostra, 21 inferencias por backend.
Sem cache de resultados. small, standard, beam 5, seis threads, idioma pt,
VAD, contexto anterior e word timestamps preservados. Unica diferenca
operacional: CPU/int8 versus CUDA/int8_float32; modo rapido e apenas o rotulo
necessario para fornecer a configuracao experimental, nao beam reduzido.

Warm e a media das duas ultimas execucoes; o conjunto soma essas medias.

| Medida | CPU Preciso | GPU |
| --- | ---: | ---: |
| Modelo descarregado, carregamento | 1,792 s | 1,817 s |
| Cold: carregar + primeira inferencia, trecho 1 | 6,981 s | 3,613 s |
| Warm, trecho 1 | 4,855 s | 1,562 s |
| Warm, amostra de 50,8 s | 12,046 s | 3,612 s |
| Warm, conjunto completo | 43,349 s | 14,329 s |
| Processo completo, todas as repeticoes | 133,909 s | 45,809 s |

Ganho warm agregado: 66,945%; amostra de 50,8 s: 70,016%.
Classificacao de velocidade: excelente (>40%), mas qualidade obrigatoria falhou.
Cold acima nao inclui inicializacao do interpretador/imports nem garante cache
frio do sistema operacional: e um modelo inicialmente descarregado no processo.
O primeiro smoke teve carregamento maior; nao esconder essa variabilidade.

VRAM total da placa no processo GPU: inicial 1710 MiB, pico amostrado 2357 MiB
(138 leituras, intervalo nominal de 250 ms mais custo de nvidia-smi).
Incremento observado 647 MiB; nao e uma medida exclusiva do modelo, pois inclui
outros aplicativos. Nenhuma das 21 inferencias apresentou falha CUDA.

## Qualidade relativa ao Preciso

Gates mantidos: palavras media/p95/pior <= 0,15/0,40/0,80 s;
segmentos <= 0,20/0,50/0,80 s; texto <=5%, perdas/extras <=3% cada.
As tres execucoes GPU de cada amostra repetiram o mesmo resultado do gate.

| Amostra | Palavras media/p95/pior (s) | Resultado |
| --- | --- | --- |
| animacao_1 | 0,0047 / 0,02 / 0,08 | Passou |
| animacao_2 | 0,0102 / 0,04 / 0,18 | Segmentos reprovados |
| animacao_3 | 0,0010 / 0 / 0,02 | Passou |
| animacao_4 | 0,0057 / 0,02 / 0,26 | Texto e fronteiras reprovados |
| animacao_5 | 0,0026 / 0,02 / 0,04 | Texto e fronteiras reprovados |
| animacao_6 | 0,0050 / 0,02 / 0,14 | Texto e fronteiras reprovados |
| fala_pt | 0,1288 / 1,56 / 2,54 | Palavras e segmentos reprovados |

animacao_2: seis segmentos CPU versus cinco GPU; pior desvio de fronteira
de segmento 1,92 s. fala_pt: texto identico, 18 segmentos e 81 palavras em
ambos, mas fronteiras de segmento com media 0,1128 s, p95 1,10 s, pior 2,20 s.
animacao_4/5/6: divergencia textual medida de 10,64%/5,13%/15,56%.
Algumas diferencas incluem grafia e hifenizacao; a comparacao e o gate existente,
nao uma conclusao de que toda diferenca GPU e linguisticamente pior.

Nao ha anotacao humana; os erros sao relativos ao Preciso, nao precisao absoluta.
Nao se comprovou cobertura humana das categorias musica/gritos/sobreposicao.
Sem equivalencia temporal, nao se afirma ausencia de regressao de legenda/remix.
Renderer, titulos, remix e providers editoriais nao foram alterados.

## Reproducao e testes

```powershell
.\.venv\Scripts\python.exe -B -u scripts\benchmark_gpu_isolado.py --runtime "C:\Users\Bruna\AppData\Local\Programs\Ollama\lib\ollama\cuda_v12"
```

A ferramenta exige runtime explicito completo e nao modifica defaults da app.
Se um worker falhar, preserva exit code e interrompe, sem instalar ou promover.
Fallback CPU de producao permanece como antes e foi verificado com mocks.
6 testes novos de isolamento e 80 testes existentes V2/temporais aprovados.
Um comando inicial usando importacao por pacote falhou porque o teste temporal
importa seu fixture pelo nome curto; executado corretamente por unittest
discover, todos os 40 testes temporais passaram. Nenhuma API real nos testes.

Arquivos cpu.json e cuda.json contem todas as transcricoes, palavras,
segmentos e repeticoes. resumo.json contem comparacoes, gates e amostragem VRAM.
Sem suporte GPU ativado na aplicacao: a investigacao termina neste benchmark.
