# Title Recovery V3 - EP12

Fonte exclusiva: seis contextos finais de Family.Guy.S07E12 ja armazenados em
dados/agencia/auto_recovery/S07E12.json. Nenhum Auto Recovery, nova transcricao,
analise do episodio ou reconstrucao/alteracao de timeline foi executado.

Antes: 1 pronto / 5 em revisao de titulo.
Depois: 6 prontos / 0 em revisao de titulo.

| Titulo principal | Score V2 |
| --- | ---: |
| POR QUE CORTADOR DE GRAMA EMPRESTADO? | 96 |
| POR QUE O CARTAO PARA O GATO? | 94 |
| POR QUE ADESIVO DO CLUBE DO LIVRO? | 98 |
| MOTORISTA EMBRIAGADO E COBERTO DE SANGUE | 96 |
| POR QUE URINA DE UM BEBE? | 94 |
| POR QUE PIADAS DE ESTUPRO NA CADEIA? | 98 |

Duas chamadas adicionais reais para o lote de cinco: regeneracao contextual e
reparo com scores, motivos e penalidades. O provider existente e o orcamento
global foram preservados. A camada V3 limita inclusive tentativas de transporte
a duas chamadas adicionais. Falha editorial nao troca provider.

O fallback local extrai expressoes factuais e acoes documentadas em evidencias
reais, sem inventar nomes ou atribuir acoes. Todas as propostas passam pelo
Title Validator V2 normal, minimo 75. Um titulo aprovado basta. Titulos sobre
piadas laterais sem relacao com objetos/eventos centrais sao excluidos antes da
escolha, sem reduzir score ou modificar o Validator. O titulo anteriormente
aprovado foi preservado. Capas coerentes foram derivadas quando possivel;
o titulo principal completo permanece fallback seguro de capa.

Resultados e diagnosticos persistidos em cache. Restauracao local preserva a
selecao editorial V3 e nao dispara API. Os seis titulos passaram novamente pelo
gate final de titulo e ficaram automaticamente prontos/selecionados, sem
override humano. Nao foi realizada nova renderizacao/exportacao fisica.

63 testes focados passaram, com rede bloqueada. Incluem lote, duas chamadas,
fallback de provider, genericidade, cache, foco editorial, capa, gate final e
preservacao dos contextos/timelines e aprovacao manual anterior.

Os seis remixes validos ficam elegiveis para exportacao sem escrever titulos:
SIM. Criterio de producao de pelo menos cinco dos seis prontos: ATINGIDO.

Resultado detalhado: dados/agencia/auto_recovery/S07E12_titulos_v3.json.
