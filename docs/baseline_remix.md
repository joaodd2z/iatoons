# Baseline e contrato de remix automatico

Registro anterior as mudancas, em 2026-10-08, sem APIs reais:

- Agencia editorial: 33 testes aprovados em 0,119 s.
- Remix Narrativo, timeline/gate da Agencia, analise comum e lote: 73 testes
  aprovados em 30,036 s (inclui verificacoes sinteticas de imagem).
- Total da selecao: 106 testes. Nao foram renderizados episodios completos.

| Caminho | Antes | Depois |
| --- | --- | --- |
| Agencia | Gate de remix temporal proprio | Contrato central fortalecido e recuperacao |
| Remix Narrativo | Validador proprio; repeticao opcional | Contrato central; sem repeticao |
| Analise comum | Candidatos lineares aprovados | Mesmo planejamento e contrato da Agencia |
| Sugestao local de lote | Linear selecionado/pendente | Rascunho estrutural nao selecionado; exportacao bloqueada |
| Listas/comparacao/reutilizacao | Podiam restaurar automaticos lineares | Proveniencia conservada e gate da timeline enviada ao render |
| Manual puro | Intervalo escolhido pelo usuario | Mantido |

Exemplos sinteticos do baseline existente: Agencia aceita gancho posterior com
retorno ao contexto, rejeita intervalo continuo/falso remix; Remix Narrativo
permitia repetir o gancho explicitamente. Depois, a repeticao e normalizada na
montagem, e timelines externas repetidas sao rejeitadas.

Contrato: modules/agencia/remix.py, avaliar_remix_real. Versao:
remix-universal-v1. Score tecnico de 0 a 100, minimo 75; qualquer falha obrigatoria
reprova independentemente do score. Nao e um score de qualidade artistica.
Validacao de causalidade depende da historia declarada pela IA e revisao humana;
as provas locais sao temporais, de cobertura, continuidade e integridade de fala.

Os testes dos helpers contextuais legados permanecem como referencia historica;
o caminho publico de analise usa o contrato universal e tem testes especificos.
Os helpers legados nao aprovam exportacoes: o gate do lote verifica a proveniencia
e os intervalos efetivamente enviados ao renderer.

Recuperacao: reparos locais conservadores -> uma tentativa global diferente ->
blocos temporais em fronteiras de fala/pausas -> mini-arcos de oportunidade.
O mesmo orcamento limita todas as etapas, erros e modelos de reserva. Uma chamada
fica reservada para oportunidades quando ha saldo suficiente. Nenhuma etapa
preenche quantidade com cortes lineares. Meta operacional 3, ideal 5, teto 10.

Caches e transcricoes antigos nao sao apagados. Novas chaves de analise/remix
incluem a versao; listas salvas perdem o carimbo e exigem nova validacao real.
Listas antigas sem proveniencia ficam conservadoramente sem aprovacao automatica.
Diagnosticos registram contagens, zero suspeito, motivos, chamadas e partes
analisadas. Esgotamento de orcamento e informado como recuperacao pendente.

Titulo, transcricao, providers, presets e renderer nao receberam otimizacoes.
As mudancas de UI se limitam a proveniencia, gate e exibicao do diagnostico.

## Verificacao depois

A mesma selecao inicial, somada aos 41 testes novos, passou com 147 testes em
29,045 s. Antes eram 106 testes em 30,155 s (soma dos dois grupos). Esta comparacao
mede regressao e cobertura; nao representa ganho de velocidade de transcricao/render.

- Suite completa no decorrer da implementacao: 353 testes aprovados em 462,118 s,
  incluindo os oito testes de render real em videos sinteticos curtos.
- Revisao posterior sem repetir esses renders: 351 testes aprovados em 44,065 s.
- Regressao final dos modulos afetados, incluindo os dois testes adicionais de UI
  e cota compartilhada: 221 testes aprovados em 2,231 s.
- A suite nova tem 41 testes: contrato, recuperacao em quatro camadas, gate do lote,
  cache, cancelamento, parser, transporte simulado e orçamento compartilhado.
- Sockets connect/connect_ex bloqueados na regressao final. Nenhuma API real.
- Tempos sao de testes locais, nao estimativas de desempenho por episodio.

## Arquivos alterados

- modules/agencia/remix.py: contrato e score central.
- modules/agencia/recuperacao.py: busca em camadas e diagnosticos.
- modules/agencia/editorial.py: orquestracao, revalidacao e cache versionado.
- modules/agencia/timeline.py: gancho sem repeticao.
- modules/agencia/qualidade.py: gate central sobre timeline e legendas finais.
- modules/agencia/pipeline.py: resultado da exportacao inclui contrato e versao.
- modules/agencia/persistencia.py: carimbos persistidos exigem revalidacao.
- modules/agencia/metricas.py: contrato e versao nas metricas por corte.
- modules/analise_ia.py: fluxo comum automatico usa o planejamento universal.
- modules/remix_narrativo.py: contrato, recuperacao, cache e cota compartilhada.
- modules/cortes.py: proveniencia e rascunhos estruturais sem aprovacao.
- modules/lote.py: gate dos intervalos enviados ao render; carregamento conservador.
- modules/ai/comparacao.py: schema de remix e regras versionadas na comparacao.
- ui/tela_lote.py: proveniencia, modo correto, cota e gate antes de exportar.
- ui/tela_agencia.py: diagnostico completo da busca.
- tests/test_remix_universal.py: novos testes obrigatorios.
- tests/test_agencia_editorial.py, tests/test_agencia_timeline.py,
  tests/test_remix_narrativo.py: expectativas atualizadas para regras novas.
- tests/test_analise_ia.py: helpers legados identificados como baseline historico.
- tests/test_multi_provider.py, tests/test_fallback.py, tests/test_comparacao_ia.py:
  fixtures de remix para os caminhos automaticos atuais.
- docs/baseline_remix.md: registro antes/depois e escopo.

Nenhum corte automatico final pode ser aprovado sem validar como remix narrativo real.
