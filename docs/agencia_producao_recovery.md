# Recuperacao de producao da Agencia

O planejamento automatico e o score minimo 75 permanecem vigentes. Com cinco
remixes aprovados, a recuperacao nao executa. Com um a quatro, executa RECOVERY;
com zero, DEEP RECOVERY. O fluxo compartilhado fora da Agencia nao mudou.

A recuperacao reutiliza a transcricao e o cache editorial, sem repetir a analise
global. Janelas de 30/45/60/90/120 segundos, com overlap, usam limites de falas
e pausas. Historias maiores que 180 segundos sao bloqueadas antes do Validator.
Os candidatos locais sao pre-filtrados e deduplicados; a cobertura de todas as
regioes de 180 segundos e registrada, inclusive regioes sem dialogo.

Sinais lexicais e citacoes reais indicam oportunidades, nao provam qualidade
editorial. Por isso uma proposta exclusivamente local permanece REVISAO_HUMANA,
mesmo quando sua montagem passa pelo Validator tecnico. A meta de cinco nunca
produz planos sem evidencia. O construtor de timeline e o Validator existentes
continuam usados, sem diminuir seus requisitos automaticos.

Planos em revisao ficam visiveis, editaveis e persistidos. Aprovar apenas o titulo
nao aprova a historia. APROVAR MANUALMENTE confirma titulo e montagem atuais.
A assinatura inclui segmentos, gancho, estrutura, remocoes, origem temporal e
titulo; alteracoes posteriores exigem nova confirmacao. O override editorial nao
dispensa intervalos validos, ausencia de repeticao/saltos graves, remix montado,
palavras inteiras, sincronizacao, layout e verificacoes reais de imagem/exportacao.

O juiz local e opcional, desativado por padrao (callback ausente). Uma integracao
explicita pode fornecer um juiz qwen2.5:3b; o prazo maximo e dois segundos. Offline,
timeout ou parecer sem citacao do texto nao mudam o resultado local. O parecer
nao altera timestamps nem aprova/rejeita sozinho. Nenhum modelo e carregado na
inicializacao, e nenhuma chamada e necessaria para recuperar o cache S07E11.

Validacao real: somente o cache Family.Guy.S07E11, sem retranscricao ou APIs.
Testes da interface simulam renderizacao e confirmam que a revisao de um plano
nao impede a geracao de outro plano confirmado. Renderer e FFmpeg nao foram
alterados.
