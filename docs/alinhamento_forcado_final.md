# Ultimo experimento: alinhamento forcado

Data: 2026-10-08. Resultado REPROVADO. Investigacao de transcricao encerrada.
CPU Preciso continua padrao. AUTO e toda a producao de cortes foram preservados.
Nenhuma nova fase, otimizacao, instalacao ou dependencia foi iniciada.

## Compatibilidade antes da execucao

- Windows 10 x64, build 19045; Python 3.14.7 x64.
- CPU Intel Core i5-10400F, 6 cores/12 threads; modelo small em CPU/int8.
- GTX 1050 Ti, 4096 MiB, driver 582.78. Alinhamento GPU nao foi executado.
- faster-whisper 1.2.1, CTranslate2 4.8.2, NumPy 2.5.3 ja instalados.
- Torch, torchaudio, transformers e WhisperX nao instalados. Nao foi encontrado
  modelo CTC de alinhamento no cache Hugging Face local: somente faster-whisper
  small e um modelo de traducao, que nao e alinhador acustico.

WhisperX-style/CTC nao foram considerados comprovadamente incompativeis, mas
nao puderam ser executados com os recursos offline encontrados. A matriz exata
Python 3.14/Windows dessas dependencias nao foi verificada sem distribuicoes
locais. Nao houve consulta externa, download, downgrade ou modificacao da .venv.
Nao foi necessario criar outro ambiente para o alinhador disponivel escolhido.

Escolha: alinhamento nativo por atencao + DTW do Whisper via CTranslate2.align.
Compativel nesta maquina pela execucao real em CPU. Nao e um modelo CTC.
No codigo instalado, find_alignment chama model.align com tokens fornecidos;
nao chama model.generate. Task=transcribe no tokenizer e o prefixo de tarefa,
nao uma chamada de reconhecimento/geracao textual.

## Protocolo sem retranscricao

Referencia operacional: CPU Preciso completo ja salvo, 568,25 s.
Texto escolhido: sete segmentos do dialogo rapido em 170,37-184,09 s, 42 palavras.
Origem do texto: CPU completo salvo, um teste favoravel/oracle de texto confiavel,
nao promessa de desempenho com texto provisorio GPU ou com anotacao humana.

Audio local: 168,37-186,09 s (17,72 s), com padding fixo de 2 s para extracao.
Nao houve busca de mais margens, VAD, retranscricao ou prompt editorial.
Tokens do texto fixo foram fornecidos ao alinhador. Timestamps foram convertidos
para absolutos com uma unica soma de offset. Segmentos mantiveram exatamente
o texto fornecido, com fronteiras derivadas das palavras alinhadas.

ASR transcribe/transcrever_video foram bloqueados no processo experimental;
conexoes socket bloqueadas, modelos em modo offline. Nenhuma API ou Ollama.

## Resultado do primeiro trecho

| Medida | Resultado |
| --- | ---: |
| Tempo de alinhamento | 1,097 s |
| Decode do episodio para extrair audio | 2,315 s |
| Carregamento do modelo CPU | 1,415 s |
| Erro medio de palavras | 0,0414 s |
| P95 de palavras | 0,0400 s |
| Pior erro de palavra | 2,0000 s |
| Palavras pareadas | 42 |
| Perdidas / extras | 0 / 0 (0% / 0%) |
| Divergencia textual | 0% |
| Texto narrativo exato preservado | Sim |
| Segmentos: media / P95 / pior | 0,1914 / 2,00 / 2,00 s |
| RAM pico amostrado do processo | 687,21 MiB |
| VRAM total antes / depois | 932 / 933 MiB |

RAM e working set do processo, incluindo modelo, decode e alinhamento; pico
amostrado a cada 50 ms, nao consumo exclusivo do alinhador. VRAM e total da
placa, incluindo outros aplicativos; o alinhador escolhido executou em CPU.

Falha objetiva: primeira palavra 'Entao' (com acento no texto original).
CPU completo: 170,37-170,81 s. Alinhamento: 168,37-170,85 s.
O inicio foi colocado no contexto anterior a fala, dois segundos cedo.
O offset absoluto foi somado corretamente: inicio local 0 + window_start 168,37.
Nao e evidenciado um offset duplicado constante em todas as palavras; as demais
fronteiras ficaram em sua maioria proximas. Nao se tentou consertar/recortar
essa falha depois, nem mascarar o outlier por media/P95 pequenos.

Gates exigidos: media <=0,20 s, P95 <=0,50 s, pior <=0,80 s,
perdas/extras <=3%, texto preservado e integridade estrutural.
Reprovou pelo pior erro. Estado permanece aligned_unverified no trecho e nos
segmentos; nenhum resultado foi marcado precise ou aligned_verified.

## Parada e estimativa economica

Conforme solicitado, falha clara no primeiro dos dois trechos planejados:
segundo trecho problematico nao executado; nao expandimos para os cinco cortes.

Extrapolacao linear de tempo por segundo de audio para as regioes fonte dos
cinco cortes: total GPU + setup CPU + alinhamento estimado de 153,79 s,
ganho estimado 72,94% contra os 568,25 s CPU salvos. Nao e benchmark de cinco
cortes, nao foi validada sua qualidade e nao representa estrategia aprovada.
Custo DTW tambem depende de tokens/chunks, nao apenas duracao. Baseline CPU foi
obtido antes do travamento anterior, limitando comparabilidade de velocidade.

## Verificacao e encerramento

Sete testes locais/mocks aprovados: texto fixo, sem generate/transcribe,
segmentos preservados, offsets absolutos/dupla soma rejeitada, alinhamento
verificado somente apos gate, outlier bloqueado, palavras extras rejeitadas,
palavras sem duracao rejeitadas. Sem APIs reais.

Ferramenta: scripts/benchmark_alinhamento_forcado_final.py.
Artefato: temp/alinhamento_forcado_final/resultado.json, com texto, palavras,
segmentos, referencia, tempos, memoria e diagnostico.
Este e o encerramento da rodada de investigacao de transcricao.
CPU Preciso permanece disponivel para produzir cortes no fluxo existente.
AUTO, Preciso, Agencia, titulos, remix, renderer e exportacao nao foram alterados.
