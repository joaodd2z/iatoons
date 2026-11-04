# Correcao De Recuperacao E Aprovacao De Titulos V2

## Causa Verificada

A lista local mais recente (dados/agencia/listas/
bcafe82e932ecbde0492755d4f4f011d40493ca56bf1c5ea14155cc259def41e.json)
continha dois planos com titulo_escolhido="SEM TITULO" (acentuado no arquivo),
top vazio e status generico de revisao. As dez propostas finais de cada plano
estavam rejeitadas; os diagnosticos citam falta de evidencias, termos/fatos nao
sustentados e, em um candidato, referencia inexistente. Scores: 0, salvo um 19.
Logo esses registros NAO tinham um ou dois titulos aprovados descartados.

O caminho era: lista aprovada vazia -> PlanoAgencia.titulo retornava o placeholder
SEM TITULO -> UI mostrava esse valor -> sincronizacao podia salva-lo como titulo
escolhido. O aviso "Menos de 3 propostas" era emitido tanto para zero como para
um ou dois, confundindo indisponibilidade com poucas alternativas. Alem disso,
zero nao desmarcava o plano, e o teto de transporte da correcao impedia fallback
de provider quando a unica tentativa falhava. Erros RuntimeError de provider
podiam escapar sem finalizar o estado de revisao de titulo.

Nenhum arquivo de listas/caches do usuario foi reescrito para fabricar aprovacao.
Os motivos editoriais/score minimo 75 permanecem iguais.

## Fluxo Corrigido

- Ideal: 3; minimo de titulo principal: 1 aprovado.
- Ranking por score decrescente, texto distinto, ate tres; tipos repetidos nao
  descartam titulos distintos. O maior score vira principal.
- Um ou dois aprovados nao disparam correcao e nao recebem aviso "menos de 3".
- Somente top vazio aciona UMA rodada contextual de correcao; inclusive cache
  vazio/rejeitado. Pede oito novos titulos, sem repetir analise de episodio.
- Prompt inclui apenas os campos da historia final e rejeitados com score/motivos;
  IDs e falas de evidencia ficam na timeline daquela historia, nao na transcricao
  inteira. Contexto/timeline compilados uma vez, nao recompostos na correcao.
- Duas rodadas editoriais (geracao/correcao) sao diferentes de tentativas de
  transporte. Fallback existente pode repetir/trocar provider em erros
  recuperaveis (429, timeout, 5xx, modelo indisponivel), dentro do orcamento global.
  Score baixo NAO troca provider: e correcao editorial no mesmo fluxo.
- Zero apos recuperacao: status REVISAO DE TITULO, motivo "Nenhum titulo atingiu
  o minimo editorial." (mensagens reais localizadas com acentos), selecionado=false.
  Metadados/validacao/roteiro do remix continuam intactos.
- UI mostra REVISAO NECESSARIA no lugar de SEM TITULO, e 1/2/3 alternativas
  aprovadas no menu. Placeholders nao sao titulos utilizaveis ou exportaveis.
- Agencia bloqueia exportacao e preview antes do render; lote automatico tambem
  bloqueia placeholder antes do renderer. Titulo V2 e revalidado para exportacao.
- Restauracao local limpa aviso antigo e aplica o mesmo estado; nao dispara API
  ao abrir listas. Nao promove scores antigos sem revalidacao.

Arquivos alterados: modules/titulos_v2.py, modules/agencia/pipeline.py (somente
guardas de titulo), modules/lote.py (somente guarda de titulo), modules/cortes.py
(adaptacao de titulo/selecao), ui/tela_agencia.py, ui/tela_lote.py,
tests/test_titulos_v2.py (expectativa do novo status), docs/titulos_v2.md.
Novos: tests/test_titulos_recuperacao.py e este documento.
Nao alterados: Remix Validator, transcricao, renderer, FFmpeg, presets,
regras da Fase 1, formula de score e SCORE_MINIMO=75.

## Testes

19 testes novos (incluem subcasos de 3/2/1 aprovados e 429/408/503/404).
Cobrem principal por score, um valido+sete invalidos sem chamada adicional,
correcao unica, sucesso com um, zero persistente, campos restritos ao remix,
analise unica, timeline unica, fallback com/sem saldo, erro seguro finalizado,
ausencia de fallback por score, restauracao de SEM TITULO, tipos repetidos,
exportacao/preview bloqueados e remix validado pelo validador real antes/depois.
Testes usam providers simulados e sockets bloqueados; nenhuma API real.
Regressao: 460 testes passaram em 48,070 s. Verificacao final focada:
97 testes de Titulos V2, recuperacao e Remix Universal passaram em 1,156 s.
Suite historica de oito renders completos nao reexecutada, pois o renderer nao
mudou e os novos testes verificam bloqueio ANTES de executar FFmpeg.
