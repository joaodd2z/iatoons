# Hibrido: context windows e guard bands

Data: 2026-10-08. Experimento offline concluido; nenhuma alteracao na producao.
AUTO, Preciso, Agencia, renderer, titulos, Remix Validator, providers e exportacao
nao foram modificados. Sem instalacao, PATH global ou chamadas editoriais.
Ollama nao foi chamado. GPU nao foi executada novamente: usamos os mesmos
artefatos CPU/GPU do episodio e as mesmas 15 decisoes fonte do cenario de 5 cortes.

## Semantica corrigida

Versao 2 do adaptador experimental:

- GPU: provisional.
- CPU processado, ainda sem aprovacao no gate: precise_unverified.
- precise: CPU processado E aprovado no gate temporal/textual existente.
- Cache CPU bruto fica precise_unverified; nao promove uma janela inteira
  porque um trecho dela passou. Cache V1 nao e aceito como aprovacao V2.
- A guarda exigir_precisos exige aprovacao registrada no gate e integridade
  de trechos/palavras. Somente flags ou processamento CPU nao liberam um corte.
- Fallback CPU completo tambem inicia unverified no experimento, ate validacao.

Os testes e os scripts antigos experimentais foram ajustados a essa semantica.
Os arquivos historicos V1 foram preservados e nao equivalem a aprovacao V2.

## Protocolo

Episodio Family.Guy.S06E11.mp4, 1310,42 s. CPU completo e a referencia operacional
salva: 568,25 s. GPU primeira passagem salva: 134,88 s. Nenhum episodio foi
retranscrito inteiro nesta fase. Nao ha anotacao humana de verdade absoluta.

Para cada contexto +/-5/10/20/30 s:

1. Expandir as regioes fonte escolhidas e unir as janelas sobrepostas/proximas.
2. Preferir baixa energia por pelo menos 200 ms antes/depois da borda nominal,
   ate 2 s adicionais; senao usar borda de segmento ou a margem temporal.
   Baixa energia e uma heuristica acustica, nao anotacao humana de silencio.
3. Quantizar inicio/fim na grade de 16 kHz e transcrever via CPU/int8, small,
   seis threads, beam 5. Opcoes padrao mantidas no perfil principal.
4. Refine window: audio realmente enviado ao modelo. Trusted window: interior
   dessa janela, excluindo guard band de 2 s em cada extremidade.
5. Somar window_start uma unica vez aos tempos locais. Validar offsets,
   duracao, segmentos/palavras e sobreposicoes, antes de mapear a selecao.
6. Usar ancoras textuais da decisao salva para descartar o contexto extra.
   Preservar IDs, funcoes e ordem narrativa. Fronteira remapeada deve ficar na
   trusted window e respeitar o limite de ajuste narrativo experimental de 3 s.
7. Comparar cada trecho mapeavel ao CPU completo com referencia textual fixa,
   localizada em +/-35 s. A referencia nao foi usada como prompt da inferencia.
8. Promover apenas os trechos aprovados; um corte exige todos os seus trechos.

Gates nao relaxados: palavras media/P95/pior <=0,15/0,40/0,80 s;
segmentos <=0,20/0,50/0,80 s; texto <=5%, perdas/extras <=3% cada.
Cache inclui episodio, limites, modelo, idioma, versao e opcoes VAD/contexto.

## Resultados principais

Tempo total = GPU salva + decode CPU (2,21 s) + load CPU (1,42 s) + inferencia
regional + overhead medido. Nao inclui load GPU (10,84 s), como a comparacao
anterior sem load. Valores incluindo load estao no JSON.
Media/P95/pior sao fronteiras de palavras pareadas nos trechos finais avaliaveis.
Palavras nao pareadas contam na divergencia textual; pendentes bloqueiam o gate.

| Contexto | Audio CPU | Total | Ganho | Media | P95 | Pior | Trechos aprovados |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| +/-5 s | 312,65 s | 234,79 s | 58,68% | 0,0337 s | 0,12 s | 2,34 s | 1/15 |
| +/-10 s | 370,25 s | 300,42 s | 47,13% | 0,0386 s | 0,13 s | 2,31 s | 0/15 |
| +/-20 s | 474,27 s | 289,49 s | 49,06% | 0,0353 s | 0,14 s | 2,57 s | 0/15 |
| +/-30 s | 575,03 s | 339,33 s | 40,28% | 0,0426 s | 0,15 s | 2,41 s | 0/15 |

Nenhum dos cinco cortes foi aprovado integralmente em qualquer perfil.
Audio CPU varia mais que o contexto nominal porque ha ajustes de borda e merge.
Janelas +/-20/30 uniram as duas regioes finais que +/-5/10 mantiveram separadas.
Mais contexto nao produziu melhora monotona, nem tempo estritamente monotono.

## Bordas versus centro

Diagnostico independente dos trechos finais, sobre as palavras pareadas de cada
refine window: bordas sao os primeiros/ultimos 5 s; centro e todo o restante.
Agregacao usa os deltas individuais, nao medias dos P95 de janelas.

| Contexto | Bordas: media/P95/pior | Centro: media/P95/pior |
| --- | --- | --- |
| +/-5 | 0,0237 / 0,12 / 0,50 s | 0,0310 / 0,12 / 2,34 s |
| +/-10 | 0,0220 / 0,08 / 0,42 s | 0,0447 / 0,16 / 3,90 s |
| +/-20 | 0,0420 / 0,18 / 0,98 s | 0,0285 / 0,10 / 2,57 s |
| +/-30 | 0,0440 / 0,22 / 0,82 s | 0,0397 / 0,15 / 2,41 s |

Os maiores erros persistem no centro, inclusive em palavras fora da selecao
final; por isso o pior de janela inteira pode superar o pior da tabela final.
Nao concluir concentracao nas bordas pelo numero bruto: centro tem mais pontos.
Em +/-5, bordas tiveram 0/212 fronteiras acima de 0,80 s; centro 4/1038.
Em +/-20, foram 3/152 nas bordas e 3/1634 no centro. A concentracao relativa
varia, mas a hipotese de problema exclusivamente nas extremidades nao se sustenta.

## VAD e contexto anterior

Codigo instalado confirma Preciso com condition_on_previous_text=True.
faster-whisper passa os tokens anteriores dentro de uma chamada, reinicia o
prompt em algumas condicoes/temperaturas, e nao carrega o historico do episodio
para uma nova chamada isolada. True numa janela nao equivale ao mesmo historico.

Controles na margem principal menos ruim (+/-5):

| Controle | Total | Ganho | Media/P95/pior | Aprovados |
| --- | ---: | ---: | --- | ---: |
| VAD=False, previous=True | 239,71 s | 57,82% | 0,0459 / 0,17 / 2,29 s | 1/15 |
| VAD=True, previous=False | 215,76 s | 62,03% | 0,0321 / 0,12 / 2,34 s | 1/15 |

Sem VAD, uma janela foi recusada pelo contrato relativo de timestamps; seus
trechos ficaram pendentes. Nenhum tempo dessa janela foi liberado. Diagnostico
de bordas/centro desse controle nao inclui a janela recusada.
Desligar previous observou menor tempo, mas nao resolveu o pior erro nem o gate.
Nao atribuir causalmente todo ganho temporal ao flag com apenas uma repeticao.

## Texto, segmentos e referencias pendentes

Nos trechos comparaveis, divergencia textual agregada: 12,64%/8,07%/11,60%/11,60%
para contextos 5/10/20/30. Contagens de segmentos referencia/candidato:
87/81, 71/61, 78/71 e 78/77. Houve 18/21/16/22 segmentos com desvio critico.
Esses agregados possuem conjuntos pareados diferentes; nao sao ranking isolado
de qualidade. Os gates continuam individuais e incluem perdas/extras.

Quatro referencias de decisao fonte ficaram ambiguas para o remapeamento fixo:
3-gancho, 3-contexto, 5-gancho, 10-gancho. Uma excede o limite de ajuste de 3 s;
as outras nao satisfazem as ancoras textuais. Nao inventamos equivalencia nem
omitimos essas selecoes do gate integral. Isso limita a conclusao sobre todas
as cinco historias; tambem ha reprovacao temporal/textual nos trechos mapeaveis.
O texto completo, motivos e pareamento palavra a palavra estao nos artefatos.

## Decisao e limitacoes

Melhor margem principal deste ensaio: +/-5, por menor custo e 1 trecho aprovado.
Nao e margem aprovada para producao. Todas as estrategias economizaram >=30%
na comparacao observada, mas nenhuma atingiu simultaneamente os gates.
Hibrido continua REPROVADO, sem candidata a producao/AUTO nesta fase.

CPU de referencia foi medido antes do travamento anterior; as medidas nao sao
comparacao controlada de carga, temperatura ou repeticoes. O modelo foi unico
e sequencial, com aproximadamente 545-655 MiB de working set consultado.
A tentativa inicial de prioridade BelowNormal nao foi aplicada por erro no
handle ctypes; prioridade Normal foi verificada. Chamada corrigida para futuras
execucoes, sem alterar prioridade no meio desta matriz nem defaults do Preciso.
Nao houve novo travamento observado. Nao foi isolada causalmente cada falha.

## Verificacao e artefatos

114 testes passaram: 107 de transcricao e 7 de contexto, com rede bloqueada nos
novos testes. Cobrem gate antes de precise, unverified bloqueado, contexto
descartado, trusted/guard bands, merge, offsets absolutos e dupla soma proibida,
cache diferenciado por VAD/previous, parametros de producao preservados.
Nenhuma API real, exportacao ou render nesta fase.

scripts/benchmark_hibrido_contexto.py; testes em tests/test_hibrido_contexto.py
e tests/test_transcricao_hibrida_experimental.py. Resultados:
temp/benchmark_hibrido_contexto/resumo.json, window_checkpoint.json e cache/.
Nao houve nova analise editorial nem chamada ao Ollama.

```powershell
.\.venv\Scripts\python.exe -B -u scripts\benchmark_hibrido_contexto.py
```

Repeticoes podem reutilizar cache regional; tempo original de inferencia e
separado do wall time com cache, evitando declarar aceleracao artificial.
