# Fase 2 - Sistema de Titulos V2

## Antes

Os titulos vinham da mesma resposta da analise do episodio. A aprovacao local
considerava principalmente tamanho, duplicacao e similaridade com a primeira fala
cronologica. Categorias repetidas podiam ser redistribuidas sem modificar o texto.
A capa podia ser formada pelas primeiras cinco palavras.

## Depois

`modules/titulos_v2.py` compila o contexto editorial depois da montagem do remix:
resumo, personagem identificado nas falas, conflito, situacao inicial, gancho,
desenvolvimento, virada, payoff, emocao, interesse, ordem final e evidencias com
IDs e tempos de fonte/saida. Falas removidas nao entram nesse contexto. Nomes e
emocoes nao identificados com seguranca ficam explicitamente desconhecidos.

Os botoes de analise da Agencia, analise comum e Remix usam o adaptador
`executar_analise_com_titulos`. Uma chamada estruturada atende o lote de historias
com 8-10 propostas por historia. O ranking local seleciona ate tres propostas
distintas por score, sem redistribuir rotulos. Uma aprovada basta para o titulo
principal; tres alternativas sao o ideal, nao o minimo.
Resultados salvos e comparacoes sao reavaliados localmente, sem API na abertura.
O corte manual puro permanece independente.

## Score e rejeicoes

Score 0-100; minimo 75, alem das restricoes obrigatorias. Contribuicoes locais:

| Criterio | Peso maximo |
| --- | ---: |
| Coerencia com termos da historia | 15 |
| Fidelidade aos termos/fatos sustentados | 15 |
| Clareza | 10 |
| Curiosidade | 8 |
| Forca do conflito | 8 |
| Naturalidade | 8 |
| Especificidade | 10 |
| Tamanho | 8 |
| Ausencia de spoiler | 8 |
| Relacao com payoff | 5 |
| Originalidade | 5 |

Aplicam-se penalidades adicionais por falhas, genericos, leitura longa e
similaridade historica. As verificacoes obrigatorias reprovam mesmo quando o
numero final ainda chega a 75: fatos/termos sem evidencia; acao, personagem e
objeto nao sustentados juntos; contradicao de negacao; desfecho inteiro revelado;
fala copiada; frase incompleta/aleatoria; tamanho excessivo; referencias falsas.

O score e uma heuristica conservadora de conformidade editorial e evidencia
textual, nao uma prova de compreensao semantica completa nem previsao de cliques.
Parafrases/fatos que o validador nao consegue sustentar ficam para revisao.

## Chamadas e historico

No pior caso: DUAS chamadas adicionais de geracao para o lote inteiro, nao por
titulo ou corte. Normalmente uma; a segunda so corrige historias em que TODOS os
candidatos falharam. Uma ou duas alternativas boas nao disparam correcao nem sao
completadas artificialmente. Tres boas nao disparam chamada adicional.

As DUAS rodadas editoriais compartilham o orcamento do episodio. Tentativas do
fallback existente contam nesse orcamento GLOBAL, nao num teto local de duas
tentativas de transporte. Assim uma correcao pode trocar de provider apos erro
recuperavel, com saldo, sem criar outra rodada editorial por score baixo.
Providers e politica de fallback nao foram alterados. Zero aprovados deixa apenas
o titulo em REVISAO DE TITULO e desmarca o corte, sem invalidar o remix.
Cache de titulos valido pode reduzir as chamadas a zero.

`dados/titulos_v2/historico.json` guarda titulos efetivamente exportados. Apenas
propor/selecionar um titulo nao registra uso. Metricas legadas da Agencia sao
consultadas para reconhecer titulos anteriores. Comparacao usa palavras principais,
bigrams, similaridade textual e estrutura. Historias sao identificadas por fonte,
intervalos e gancho, sem depender de ID aleatorio ou justificativa textual.

O cache novo em `dados/titulos_v2/candidatos` depende do contexto da montagem,
modelo, provider e versao das regras. Sempre reavalia candidatos com o historico
atual; nao altera cache de video, transcricao ou analises da Fase 1.

A UI mostra scores nas alternativas existentes, preserva metadados nas listas e
revalida o titulo antes de exportar. A capa usa uma proposta curta coerente com o
titulo aprovado; se a proposta perde o sentido, preserva o titulo completo.

## Exemplo sintetico verificado

Historia: Peter quer um carro; Lois disputa o dinheiro da familia e recusa o plano
da loteria. O gancho antecipa o custo do carro; depois vem contexto e consequencia.

| Antes/depois | Titulo | Score V2 |
| --- | --- | ---: |
| Antes, generico rejeitado | ELE NAO ESPERAVA POR ISSO | 0 |
| Depois, curiosidade | POR QUE LOIS RECUSA A LOTERIA? | 98 |
| Depois, conflito | O CARRO CONTRA O DINHEIRO DA FAMILIA | 100 |
| Depois, surpresa | A LOTERIA ENTRE PETER E LOIS | 98 |
| Invencao rejeitada | PETER ROUBOU UM BANCO | 0 |
| Payoff/copia rejeitados | LOIS VENDEU O CARRO E PETER FOI MORAR NO PORAO | 23 |
| Combinacao inventada de termos reais | LOIS RECUSA O CARRO DE PETER | 68 |

## Arquivos

- `modules/titulos_v2.py`: contexto, geracao, correcao, score, diversidade, capa,
  historico, cache de titulos e adaptadores dos fluxos da UI.
- `modules/agencia/editorial.py`: campos de score/diagnostico e persistencia.
- `modules/cortes.py`: transporte dos metadados V2 e status para o lote.
- `modules/remix_narrativo.py`: campo de metadados V2 no candidato.
- `ui/tela_agencia.py`: geracao posterior ao remix, scores, revalidacao e historico.
- `ui/tela_lote.py`: mesma integracao para analise comum/remix e timeline do lote.
- `tests/test_titulos_v2.py`: testes sem API real.
- `docs/titulos_v2.md`: entrega e escopo desta fase.

Remix Validator, transcricao, renderer, FFmpeg, presets, providers, fallback e
performance geral nao foram modificados. Nenhuma nova renderizacao de episodio.

## Verificacao

- 388 testes de regressao aprovados em 32,986 s, com sockets bloqueados.
- Os oito testes de render real foram excluidos desta rodada: renderer/FFmpeg e
  presets nao mudaram, e ja foram verificados na Fase 1.
- Suite V2 com 37 testes, incluindo identidade estavel da historia e entrega de
  scores/transcricao ao callback da interface de Remix.
- Apos os ultimos ajustes, 205 testes dos modulos afetados aprovados em 2,169 s.
- Sem APIs reais, sem instalacao de dependencias e sem reanalise da auditoria.
