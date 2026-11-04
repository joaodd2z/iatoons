# GPU -> CPU Preciso: experimento isolado

Nota historica: este relatorio descreve a versao 1. Na versao 2 experimental,
CPU sem gate fica precise_unverified; precise exige aprovacao temporal.
Os JSONs historicos abaixo foram preservados, nao representam aprovacao V2.
O experimento de context windows tem relatorio separado.

Data: 2026-10-08. Nenhuma integracao com AUTO, Agencia, exportacao, renderer,
titulos, Remix Validator ou providers editoriais. Nenhuma instalacao ou mudanca
persistente de PATH. CPU Preciso permanece small/CPU/int8, seis threads,
beam 5, VAD, contexto anterior e word timestamps.

## Contrato implementado

- Envelope experimental marca GPU como timestamp_quality=provisional.
- Se GPU falhar, primeira_passagem usa CPU Preciso completo, marcado precise.
- Selecao explicita por cut_id, trecho_id, funcao e intervalo fonte. Nenhuma
  chamada editorial para ajustar tempos; ordem narrativa e identificadores ficam.
- Margem configuravel, inicialmente 3 s, limitada ao audio e quantizada em
  amostras de 16 kHz. Regioes proximas/sobrepostas sao unidas antes da inferencia.
- CPU Preciso executa apenas os arrays dessas regioes, com os mesmos parametros.
- Cache separado por SHA-256 do episodio, inicio/fim na grade de amostras,
  modelo, idioma, versao do adaptador e configuracao Preciso. Cache validado
  por metadados, checksum dos segmentos, offsets e contrato estrutural.
- Offset somado uma vez a segmentos e palavras. As palavras CPU determinam
  as novas fronteiras. Texto ambiguo ou fronteira fora da margem ficam pendentes.
- Falha de inferencia/refinamento permite uma unica tentativa CPU adicional.
  Tentativas bem-sucedidas nunca sao retranscritas por repeticao da selecao.
- A guarda experimental exige precise em todos os trechos e segmentos,
  valida palavras/duracao e impede sobreposicao indevida dentro do mesmo corte.
  Cortes independentes podem compartilhar fontes; um pendente nao libera timing GPU.
- precise significa origem CPU e integridade estrutural. Nao significa que
  o gate comparativo ou precisao humana tenham sido automaticamente comprovados.

## Corpus e limitacoes

Family.Guy.S06E11.mp4 local, duracao 1310,42 s (21,84 minutos).
Simulacoes de 3, 5 e 10 cortes com aproximadamente 20-64 s de audio fonte,
gancho antes de contexto/desenvolvimento. Trechos distribuidos pelo episodio;
janelas sem dialogo suficiente foram deslocadas para uma janela vizinha.
E simulacao de carga narrativa, NAO afirmacao de que dez boas historias foram
encontradas pelo Story Hunter. Nenhuma IA editorial foi chamada.

Referencia CPU completa foi preservada da execucao anterior ao travamento do
PC: 568,25 s de transcricao, carregamento 9,96 s. A maquina foi retomada sem
processos Python antigos. GPU completa nesta retomada: 134,88 s, load 10,84 s.
Nao atribuir causalmente o travamento ao modelo: causa nao foi isolada.
Os ganhos sao observacionais, nao uma comparacao controlada de carga/temperatura.

## Resultados

CPU completo: 568,25 s sem load / 578,21 s incluindo load.
GPU completo provisoria: 134,88 s sem load / 145,72 s incluindo load.
Refinamento abaixo inclui decode do audio (2,25 s), load CPU (1,36 s) e custos
de inferencia regional. Custo de GPU + refinamento assume caches inicialmente
vazios para cada cenario; nao confunde cache quente com trabalho gratuito.

| Cortes | CPU refinamento | Hibrido | Ganho | Audio CPU | Regioes |
| --- | ---: | ---: | ---: | ---: | ---: |
| 3 | 47,09 s | 181,97 s | 67,98% | 145,04 s | 4 |
| 5 | 87,86 s | 222,73 s | 60,80% | 282,37 s | 6 |
| 10 | 189,68 s | 324,56 s | 42,88% | 627,31 s | 11 |

Incluindo load GPU e comparando com CPU incluindo load:
192,81 / 233,58 / 335,40 s; ganhos 66,65% / 59,60% / 41,99%.
Os cenarios 5 e 10 reutilizaram 4 e 6 regioes, respectivamente, da execucao
anterior; custos individuais originalmente medidos foram usados para comparar
cenarios sem cache. As 11 regioes distintas foram inferidas uma vez cada,
sem falha ou retry CPU. Nao se executaram modelos pesados em paralelo.

## Precisao final: REPROVADA

Gates originais mantidos: palavras media/p95/pior <=0,15/0,40/0,80 s;
segmentos <=0,20/0,50/0,80 s; divergencia textual <=5%, perdas/extras <=3%.

| Cortes | Trechos CPU precise | Selecionados | Pendentes | Gate integral |
| --- | ---: | ---: | ---: | --- |
| 3 | 7 | 9 | 2 | Reprovado |
| 5 | 11 | 15 | 4 | Reprovado |
| 10 | 22 | 30 | 8 | Reprovado |

Todos os trechos produzidos na timeline experimental sao precise. Nem todos
os selecionados puderam ser convertidos: ancoras narrativas ambiguas ficaram
pendentes. As timelines simuladas de cortes completos possuem 1/1/3 cortes
estruturalmente prontos; isso NAO equivale a aprovacao editorial/temporal final.
Nenhum corte foi renderizado ou exportado.

No pior trecho do cenario 3: palavras media 0,0464 s, p95 0,53 s,
pior 0,64 s; fronteira de segmento ate 3,64 s, texto divergente em 20%.
No pior trecho dos cenarios 5/10: palavras media 0,08 s, p95 0,27 s,
pior 2,41 s. Tambem houve divergencias textuais e de segmentacao.
Esses sao exemplos de trechos criticos, nao p95 agregado do conjunto.

GPU sem refinamento, no episodio completo: palavras media 0,0456 s,
p95 0,16 s, pior 7,88 s; outros gates de texto/segmentos tambem reprovados.
Isso nao substitui o resultado antigo de 2,54 s em outra amostra.

Recortar audio reinicia contexto de decodificacao. CPU Preciso regional nao
pode ser suposto identico ao CPU Preciso completo: as medidas demonstram que
origem CPU sozinha nao resolve a equivalencia. Nao foi isolada a causa exata
de cada diferenca. Sem anotacao humana, erros sao relativos ao CPU completo,
nao acuracia absoluta. Nem toda diferenca textual e necessariamente texto pior.

## Ponto de equilibrio

Nao foi observado crossover nos cenarios medidos, ate 10,46 minutos de audio
CPU expandido. O crossover exato NAO foi medido.

Extrapolacao usando taxa CPU regional sugere ~24,07 min para perder o ganho e
~14,52 min para cair abaixo de 30%. A primeira estimativa excede a duracao deste
episodio e nao pode ser tratada como um limite operacional comprovado.
Usando conservadoramente a taxa CPU do episodio inteiro: ~16,52 min para
equilibrio e ~9,97 min para o limite de 30%. A diferenca entre modelos de custo
e mais uma razao para nao promover o experimento ao AUTO.

## Decisao e verificacao

Nao recomendado para producao nesta forma. Economia >=30% nao compensa
reprovacao temporal/textual ou trechos narrativos pendentes. Candidato futuro
ao AUTO somente apos passar integralmente os gates e manter economia >=30%.
AUTO atual nao foi alterado.

105 testes de transcricao passaram, incluindo 21 testes do experimento:
provisional, fallback CPU, merge/margens, cache persistente/identidade/checksum,
offsets, fontes sobrepostas, isolamento de cortes, bloqueio de timing provisorio,
preservacao narrativa, falhas e parametros exatos do Preciso. Sem APIs reais.

Ferramentas: scripts/transcricao_hibrida_experimental.py e
scripts/benchmark_hibrido_selecionado.py. Artefatos:
temp/benchmark_hibrido_selecionado/{cpu.json,gpu.json,gpu_provisional.json,
resumo.json,cache_regioes/}. JSONs preservam textos, palavras, segmentos,
selecao original e final, diagnosticos por trecho e tempos regionais.

```powershell
.\.venv\Scripts\python.exe -B -u scripts\benchmark_hibrido_selecionado.py --etapa cpu --episodio "CAMINHO_LOCAL"
.\.venv\Scripts\python.exe -B -u scripts\benchmark_hibrido_selecionado.py --etapa gpu --episodio "CAMINHO_LOCAL" --runtime "PASTA_CUDA12_EXISTENTE"
.\.venv\Scripts\python.exe -B -u scripts\benchmark_hibrido_selecionado.py --etapa refinar --episodio "CAMINHO_LOCAL"
```

Executar etapas sequencialmente; nao usar esse exemplo para iniciar tres modelos
ao mesmo tempo. O runtime e explicito e temporario no processo experimental.
