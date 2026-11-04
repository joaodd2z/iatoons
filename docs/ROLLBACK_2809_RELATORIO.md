# Rollback editorial 28/09 - copia separada

Fonte exclusiva: `D:\gerador corte\backups\codigo_antes_modo_agencia_20260928_020515.zip`.
Principal preservada: `D:\gerador corte\GeradorDeCortes`.
Snapshot: `D:\gerador corte\BACKUP_ATUAL_ANTES_ROLLBACK`.
Copia candidata: `D:\gerador corte\GeradorDeCortes_ROLLBACK`.

## Escopo e limite historico

O ZIP precede o Modo Agencia e nao contem modules/agencia ou modules/ai.
Nao foi restaurado integralmente. Janelas, prompt narrativo e montagem de
intervalos completos do motor antigo foram adaptados ao contrato da Agencia
atual. Nao existe uma Agencia antiga nesse ZIP para restaurar literalmente.
O backup de 25/09 nao foi utilizado.

O caminho publico de analise agora usa a fachada rollback_2809, sem o
Auto Recovery recente, subdivisao local para preencher quantidade ou Ollama.
Quantidade e teto, nao piso. O ranking escolhe um candidato por familia narrativa.
Identidade considera conflito, personagens, evento, setup, payoff e fonte;
titulo e hook nao tornam uma historia nova. Listas salvas tambem deduplicam
candidatos automaticos, preservando aprovacoes manuais.

## Comparacao offline: Family.Guy.S07E12

| Medida | Atual observado em cache | Rollback |
|---|---:|---:|
| Cortes | 6 | 4 |
| Identidades narrativas | 5 | 4 |
| Duplicatas narrativas | 1 | 0 |
| Mesma historia, hooks diferentes | 1 | 0 |
| Diversidade por identidade | 83,3% | 100% |

O mesmo criterio local foi aplicado aos dois conjuntos. A versao atual e um
resultado historico congelado, nao uma nova execucao de IA. O rollback reutilizou
o cache bruto existente, descartou quatro candidatos repetidos e nao fez chamadas.
Nao se trata de avaliacao humana cega nem garantia para todos os episodios.

Historias selecionadas:

- 260,78-347,21 s: encobrimento do corpo do gato; gancho na proposta de esconder
  o corpo, consequencia na descoberta policial do motorista ensanguentado.
- 361,69-440,92 s: Brian sai da cadeia e a narrativa explica a proibicao da
  maconha; gancho em Hearst, consequencia no interesse comercial da madeira/papel.
- 502,98-604,70 s: fraude no teste de urina de Brian; gancho na reacao ao teste,
  consequencia na descoberta por Lois e resposta sobre legalizacao.
- 1132,86-1259,84 s: Carter condiciona a publicacao do livro ao discurso de Brian
  contra a maconha; gancho na oferta, consequencia no fracasso do livro e proibicao.

Os quatro passaram pelo Remix Validator atual, sem alterar seu contrato:
score_remix 100 em todos. Timeline, identidade e decisao narrativa permaneceram
inalteradas durante a aplicacao local do Titulos V2. Essa verificacao tecnica
nao substitui assistir aos cortes para julgar a montagem editorial.

## Preservacao e verificacao

Infraestrutura modules/ai e transcricao identicas por SHA-256 a principal.
Gemini, Groq, Mistral, factory, fallback, configuracao, credenciais, renderer,
FFmpeg e exportacao preservados. Nenhuma chave foi exibida ou solicitada.
Nao houve teste de conexao real aos providers.

Snapshot conferido: 43.519 arquivos, nenhum ausente ou divergente em tamanho/data;
arquivos protegidos de codigo/cache conferidos por hash. Cinco links do modelo
Whisper foram materializados nas copias e seus alvos conferidos por SHA-256.
Nenhum cache original foi apagado. Temporarios volumosos nao foram duplicados
na candidata, mas permanecem na principal e no snapshot.

110 testes focados passaram, incluindo providers/fallback, titulos manuais e
interface Tk real. Agencia exibiu quatro cards distintos; uma duplicata salva
foi filtrada; os quatro selecionados entraram na fila de geracao simulada.
Sockets bloqueados e renderer simulado: nao foram produzidos videos neste teste.

Detalhes: dados/agencia/rollback_2809/comparativo_ep12.json e
docs/rollback_2809_integridade.json.

A principal nao foi substituida. A candidata aguarda autorizacao explicita.
