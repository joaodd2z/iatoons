# Agencia - Aprovacao manual de titulo

## Causa do bloqueio

A sincronizacao antiga apenas substituia titulo_escolhido (convertendo para
maiusculas). Nao registrava aprovacao humana nem retirava a revisao anterior.
O titulo era submetido novamente ao Title Validator V2 em _proximo e na entrada
de exportar_corte_agencia. Assim, editar o campo nao constituia override editorial.
A restauracao tambem podia substituir o texto pela selecao automatica anterior.

## Correcao

O botao Aprovar titulo confirma exatamente o texto atual, com trim apenas nas
extremidades. Vazio e placeholders nao podem ser aprovados. A decisao grava
titulo_origem=manual, titulo_aprovado_manualmente=true e o texto confirmado.
Limpa o estado de revisao de titulo, seleciona o corte e nao chama IA nem exige
score 75. O score e o diagnostico automaticos anteriores nao sao adulterados.

Editar novamente invalida a confirmacao imediatamente. O gate tambem compara
o texto atual com o confirmado para impedir reutilizacao indevida do override.
Aprovacoes e edicoes pendentes sobrevivem ao salvamento/restauracao da lista.

Gerar selecionados filtra cada corte individualmente. Os prontos seguem para
a fila e os pendentes ficam em revisao, sem bloquear os demais. A entrada de
exportacao e a previa verificam novamente a confirmacao. Um titulo manual nao
e substituido por uma alternativa automatica para resolver falta de espaco:
as restricoes visuais existentes continuam podendo impedir a exportacao.

Remix Validator, Title Validator V2 automatico, transcricao, renderer e FFmpeg
nao foram modificados. O override nao contorna gates de remix, layout ou video.

## Verificacao

14 testes novos: exportacao simulada, placeholders, override de score antigo,
nova confirmacao apos edicao, independencia dos cortes, persistencia, selecao
da fila e preservacao do texto. Conexoes de rede bloqueadas; renderer simulado.
488 testes de regressao aprovados em 36,070 s. Os oito testes historicos de render
real foram excluidos; nenhuma renderizacao real executada nesta alteracao.
Campo, botao e transicoes tambem verificados com Tk real em janela de teste.
