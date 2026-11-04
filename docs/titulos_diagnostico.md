# Titulos V2 - Qualidade e diagnostico

## Escopo

Score minimo mantido em 75, com os mesmos pesos, descontos e restricoes.
Remix Validator, transcricao, renderer e FFmpeg nao foram alterados.

Cada proposta avaliada registra score total, pontos por criterio, pesos maximos,
motivos objetivos e penalidades com valor e origem. O historico preserva os
rejeitados da primeira rodada mesmo quando a correcao aprova um titulo.
Resultados salvos sao reavaliados localmente, sem chamadas de API.

## Geracao contextual

O prompt solicita 8-10 propostas, com angulos distintos de curiosidade, conflito,
tensao, consequencia, absurdo e surpresa. Conflito, personagem, situacao, gancho
e consequencia orientam o conjunto. Nao exige empilhar todos em cada titulo.

As evidencias da timeline final prevalecem sobre o resumo. O prompt fornece
vocabulario comprovado e aponta termos do resumo sem suporte nas falas, evitando
que um resumo inconsistente autorize nomes, acoes ou fatos inventados.

Somente zero aprovados aciona uma correcao contextual. Essa rodada recebe os
criterios, penalidades e motivos anteriores, com instrucoes especificas para
evitar os defeitos detectados. Nao reanalisa o episodio nem reconstrui o remix.
Se ainda houver zero, permanece REVISAO NECESSARIA, sem invalidar o remix.

A diversidade textual identifica categorias ausentes e propostas muito
parecidas; rotulos diferentes nao tornam textos iguais distintos. Essa
heuristica nao comprova diversidade semantica e nao aprova fatos inventados.
Um unico titulo aprovado continua suficiente, sem nova chamada por quantidade.

## Interface

O botao Ver titulos rejeitados, na Agencia e no lote, abre a tabela
Titulo | Score | Motivos. A selecao mostra detalhes por criterio e penalidades.
O diagnostico inclui as duas rodadas quando houver correcao.

## Verificacao

- 14 testes novos de diagnostico, correcao contextual e diversidade.
- 474 testes de regressao aprovados, com conexoes de rede bloqueadas.
- Nenhuma API real; oito testes historicos de render real nao executados.
- Janela de diagnostico construida e geometria verificada com Tk real.
- Dois exemplos escritos localmente com contextos salvos atingiram 98 pontos.
  Isso verifica compatibilidade com as regras, nao mede taxa real de aprovacao
  dos providers. Nenhum titulo salvo do usuario foi substituido nesse teste.
