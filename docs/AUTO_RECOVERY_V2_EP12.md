# Auto Recovery V2 - regressao de producao

Fonte: Family.Guy.S07E12 e transcricao/análise editorial existentes. Nao houve
retranscricao, benchmark, download ou alteracao do Remix Validator, renderer ou FFmpeg.

## Resultado observado

| Medida | EP12 |
| --- | ---: |
| Candidatos iniciais | 13 |
| Rejeitados inicialmente, incluindo duplicatas | 12 |
| Reparos diretos entregues | 3 |
| Novas montagens aprovadas por subdivisao | 2 |
| Novas oportunidades avaliadas | 46 |
| Remixes aprovados pelo Validator | 6 |
| PRONTOS, com titulo automatico aprovado | 1 |
| REVISAO, exclusivamente de titulo | 5 |
| Candidatos nao entregues | 53 de 59 avaliados |
| Recovery local | 0,85 s |
| Recovery + revalidacao dos titulos em cache | 7,68 s |

Chamadas novas de IA: zero para recovery local; duas para Titulos V2
(geracao e unica correcao). As tres chamadas historicas da analise foram reutilizadas.
O tempo acima e da repeticao com cache, nao inclui a latencia das duas chamadas reais
de titulo. Essa latencia nao foi medida pelo runner, que teve uma falha no relatorio
depois de persistir os titulos; a repeticao corrigida usou exclusivamente o cache.

## Reparos demonstrados

- Historia do gato, 252,3 s no diagnostico anterior: 10 oportunidades naturais;
  2 montagens independentes aprovadas, 92,06-152,81 e 137,05-260,78.
  A variante antiga de 220,1 s corresponde a mesma regiao; nao conta novamente
  como duas historias novas.
- Fala truncada, 1132,9-1259,8: alinhamento aos limites reais e protecao de frases
  contra compactacao interna. Resultado aprovado: 1132,86-1259,84.
- Payoff removido, 260,8-347,2: reinsercao/protecao da consequencia declarada.
  Resultado aprovado: 260,78-347,21.
- Gancho sem reencontro, 503-604,7: protecao da chegada real ao gancho.
  Resultado aprovado: 502,98-604,70.
- Historias de 190,3 / 172,7 s: respectivamente 3 / 7 oportunidades naturais
  preparadas para julgamento editorial. Nenhuma foi aprovada automaticamente
  sem evidencia suficiente. Outra historia longa produziu 5 oportunidades.
- Duplicatas: nao entregues novamente; a fila continua nas proximas oportunidades.

Restam 15 micro-historias que exigem julgamento editorial. O replay local nao
chamou provider para elas e nao declara busca editorial completa. No fluxo normal,
os lotes regionais utilizam o provider existente, respeitando o orcamento e
reservando duas chamadas para titulos. Cada candidato tem no maximo duas rodadas.

## Titulos e interface

Titulo aprovado: MOTORISTA EMBRIAGADO E COBERTO DE SANGUE.
Os outros cinco pacotes continuaram rejeitados apos a unica correcao contextual,
principalmente por termos sem suporte, referencias de evidencia inexistentes e
evidencias insuficientes. Score minimo 75 preservado. Nenhum deles foi contado
como pronto nem liberado artificialmente para exportacao.

A janela real do app, em teste sem rede, mostrou seis cards: um pronto primeiro,
selecionado, e cinco em revisao. A selecao para geracao reconheceu somente o pronto.
Testes usam socket bloqueado e providers falsos; nenhum teste chama API real.

EP11: cache revalidado sem API, 10 remixes aprovados em alvo de 10; sem gerar
novos titulos, nao representa 10 exportacoes prontas. Regressao de interface e
aprovacao manual preservada.

## Conclusao

O Recovery recupera historias antes abandonadas. Entretanto, a meta de 5-10
cortes completamente automaticos NAO foi demonstrada: EP12 tem somente um pronto.
Resposta ao criterio de entrega automatica completa: NAO.
PRODUCAO PRONTA para esse criterio: NAO.
