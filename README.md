# IA TOONS CUT — Gerador de Cortes Verticais

Aplicativo local para Windows que transforma trechos de um episódio em vídeos
verticais no padrão visual do canal IA TOONS.

## Estado atual — Fase 2

Funcionalidades implementadas:

- importação de vídeos MP4, MKV e MOV;
- leitura de metadados com `ffprobe`;
- seleção manual do início e fim com precisão de milissegundos;
- validação do intervalo contra a duração do vídeo;
- título manual com quebra de linha;
- prévia assíncrona no layout 1080×1920;
- animação central em recorte quadrado 1080×1080;
- fundo e fonte do preset IA TOONS;
- exportação H.264/AAC em 30 ou 60 FPS;
- fila sequencial de renderização com progresso sem bloquear a interface;
- transcrição local com `faster-whisper` usando o modelo `small` em CPU/int8;
- idioma português ou detecção automática;
- cache da transcrição por vídeo, idioma e modelo;
- timestamps de segmentos e palavras salvos em JSON;
- edição manual da transcrição com preservação dos tempos.
- legendas sincronizadas em RailroadGothicCC, brancas e com contorno escuro;
- ajuste de tamanho, posição e palavras por bloco;
- timestamps de legenda relativos ao início de cada corte;
- incorporação das legendas no MP4 e exportação SRT opcional;
- sugestão de cortes entre 60 e 120 segundos por limites de frases e pausas;
- edição, exclusão, seleção e inclusão manual de cortes;
- persistência da lista de cortes para continuar depois;
- exportação sequencial em lote a 60 FPS, com progresso individual e geral;
- cancelamento do lote, repetição dos cortes com erro e nomes sem sobrescrita.
- preset audiovisual persistente **IA TOONS 4K**;
- redimensionamento Lanczos e tratamento aplicado somente à animação;
- nitidez, contraste, saturação, brilho e redução de ruído configuráveis;
- intensidade geral de 0 a 100% e controles individuais para cada tratamento;
- rampa de volume independente por corte com limiter anticlipping;
- mesma cadeia visual na prévia, exportação manual e exportação em lote.
- chave Gemini protegida pelo Gerenciador de Credenciais do Windows;
- consulta dos modelos Gemini disponíveis e seleção na interface;
- limite configurável de chamadas por episódio e cache das análises;
- análise contextual opcional de humor, situações absurdas, reviravoltas e diálogos marcantes;
- três títulos contextuais editáveis para cada candidato inteligente;
- validação local dos cortes inteligentes contra timestamps e limites de fala.
- otimização do ponto de entrada para ganchos nos primeiros 1 a 3 segundos;
- indicação do motivo do gancho, preservando o início original para comparação;
- cortes excepcionalmente curtos quando isso preserva uma situação completa.
- teste A/B opcional: sequência cronológica ou teaser de 2–4 segundos seguido da cena;
- legendas e áudio remapeados para a ordem da versão B;
- prévias A/B de até oito segundos e exportação com sufixos `_A` e `_B`.
- detecção conservadora da abertura pela transcrição, sem remover cenas anteriores;
- correção manual e persistência do intervalo da intro;
- exclusão não destrutiva da intro durante prévias e exportações.

A sugestão atual usa somente estrutura temporal, pausas e limites de fala. Ela
continua disponível como modo local/manual. A opção **Analisar episódio com IA**
usa a Gemini para classificação contextual, mas suas sugestões sempre precisam
de revisão humana e não garantem identificar todo momento engraçado.

## Modo Agência / Nova conta

Modo opcional e independente do preset legado, na aba **🏢 Modo Agência**. O
IA TOONS, o ANIMATOONS, o Corte normal e o Remix Narrativo continuam iguais.

- unidade de edição = HISTÓRIA: GANCHO → CONTEXTO → DESENVOLVIMENTO → ESCALADA → PAYOFF;
- **Analisar com IA** usa um prompt próprio (cache em `dados/agencia/analises`, por
  episódio + transcrição + modelo — o perfil visual não participa);
- **Usar análises salvas (sem Gemini)** converte as análises e remixes já existentes
  do mesmo episódio, sem nenhuma chamada;
- remix sem repetição do gancho quando os limites coincidem com frases completas;
- silêncio inicial, espera e frames mortos saem; pausas com movimento (ação visual)
  ficam; momento forte, reação e gancho nunca são compactados;
- HARD CUT por padrão; DISSOLVE (`transition_mode="agency"`,
  `transition_family="dissolve"`, `transition_duration=1.0`) só em salto temporal,
  gancho do remix ou intro removida, e somente sobre trecho sem fala;
- enquadramento por fonte (horizontal/quadrada/vertical) com recorte máximo de 18%
  e preenchimento desfocado do próprio vídeo; centro de interesse estimado;
- perfis com TOP_SAFE_ZONE / VIDEO_ZONE / BOTTOM_SAFE_ZONE; novo perfil **TOONMIX**
  (`assets/fundo_toonmix.png`);
- título/hook amarelo com contorno preto forte e escala adaptativa; 3 alternativas
  (A curiosidade, B conflito, C surpresa), editáveis; legendas com estilo próprio;
- preset **AGENCY_AV_V1** (`dados/preset_agency_av_v1.json`): CAS moderado, gamma
  suave, contraste ≈1; não é reaplicado em vídeos já exportados pelo aplicativo;
- exportação 1080×1920, 60 FPS, H.264 High (CRF 17, slow), AAC 192k, yuv420p, faststart;
- capa integrada com 3 frames candidatos;
- **QUALITY GATE**: HOOK, INTRO, TÍTULO, LEGENDAS, SINCRONIZAÇÃO, CONTINUIDADE,
  DUPLICAÇÃO, FINAL, FIRST FRAME (frames reais em 0/100/250/500/1000 ms), IMAGEM e
  EXPORTAÇÃO. Falha no primeiro frame → nova renderização em modo seguro; persistindo,
  o arquivo vai para `temp/agencia_revisao` e não é exportado;
- métricas por corte em `<corte>.metricas.json` e `dados/agencia/metricas.jsonl`.

A composição usa o próprio vídeo como relógio: o fundo do perfil, com a zona de
vídeo transparente, é sobreposto por cima. Assim não existe frame só com fundo.

## Fábrica multi-conta (meta: 100 vídeos por dia)

Para produzir em massa, para várias contas e nichos, sem precisar abrir episódio por episódio.

**Pela interface:** aba **🏭 Fábrica 100/dia** (primeira aba).
1. Configure as contas: nome, perfil visual, posts por dia, cortes por episódio e hashtags. Depois clique em **Salvar contas**.
2. Clique em **Entrada** de cada conta e coloque ali os episódios daquela conta.
3. Clique em **▶ Produzir tudo e montar agenda**. Pode deixar rodando à noite.
4. Clique em **Para postar**. Cada dia tem uma pasta com os vídeos numerados na ordem e no horário de postagem, a legenda pronta de cada um (`.txt`), a capa e um `AGENDA.csv`, que abre no Excel.

**Sem interface (deixar rodando):** dois cliques em `FABRICA_RODAR_TUDO.bat`, ou:
```powershell
.venv\Scripts\python.exe fabrica.py preparar       # cria contas.json e as pastas
.venv\Scripts\python.exe fabrica.py tudo --dias 2  # produz e monta 2 dias de agenda
.venv\Scripts\python.exe fabrica.py status         # estoque por conta e quantos dias ele cobre
```

**Como a conta da meta fecha:** 3 contas de ~33 posts/dia = 100 vídeos/dia. Com 12 cortes por episódio, são cerca de **9 episódios por dia** no total.

**Regras da fábrica:**
- **Sem repetição:** cada corte entra na agenda uma vez só (registro em `fabrica_registro.json`).
- **Intercalação:** os episódios se alternam (A1 B1 C1 A2…), para o público não ver vários cortes seguidos do mesmo episódio.
- **Horários:** os posts são espalhados pela janela da conta. Hoje, só entram os horários que ainda não passaram.
- **Revisão:** corte que a esteira mandou para revisão não entra na agenda. Ele aparece no status para revisar na **Esteira de Produção**.
- **Qualidade:** `qualidade: "rapida"` (padrão da fábrica) usa x264 veryfast e CRF 20, cerca de 3 a 4 vezes mais rápido que o `slow` CRF 17, e no celular a diferença quase não aparece. Para o visual máximo, use `"maxima"`.
- **Retomada:** a fábrica não refaz episódio já produzido e continua de onde parou.
- **Publicação:** a fábrica **não publica nada sozinha**. A postagem continua sendo feita pela pessoa, com o arquivo e a legenda prontos.

**Perfil visual por conta (outro nicho):** crie `dados/perfis/<id>.json`:
```json
{"id": "cortes_s", "nome": "CORTES S", "fundo": "fundo_cortes_s.png", "titulo": {"cor": "0x00E5FF"}, "legenda": {"cor": "white"}}
```
- **Fundo:** precisa ter 1080×1920, com a mesma moldura do TOONMIX (área do vídeo em y=482–1425). Coloque o arquivo em `assets/`.
- **Escolha na conta:** o perfil aparece na lista de perfis da conta.
- **Id inválido:** um id que não exista volta para o TOONMIX.

> Atenção a direitos autorais: cortes de séries como Family Guy usam conteúdo de terceiros, e plataformas como o Kwai podem desmonetizar ou derrubar contas por isso, principalmente postando em volume. Para contas novas, o mais seguro é usar conteúdo licenciado, de domínio público ou próprio.

## Requisitos

- Windows com Python compatível;
- FFmpeg e ffprobe disponíveis no `PATH`;
- dependências Python de `requirements.txt`.
- uma chave da Gemini API do próprio usuário para a análise inteligente opcional.

Os arquivos `assets/fundo_ia_toons.jpg` e
`assets/fonts/RailroadGothicCC.ttf` precisam estar presentes.

## Instalação

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Execução

Na pasta `GeradorDeCortes`:

```powershell
.venv\Scripts\python.exe main.py
```

O fluxo manual continua disponível nas telas **Importar**, **Corte** e
**Exportar**. A transcrição é opcional e fica na aba **Transcrever**.

Para transcrever, importe o vídeo e abra a aba **Transcrever**. Escolha
**Português** ou **Detecção automática** e clique em **Transcrever episódio**.
Na primeira utilização, o modelo gratuito é baixado para
`dados/modelos_whisper`; nas seguintes, ele é reutilizado localmente. A
transcrição fica em `dados/transcricoes` e pode ser corrigida na própria tela.

## Gerar vários cortes

1. Importe e transcreva o episódio.
2. Corrija a transcrição e salve as alterações.
3. Abra **Cortes em lote**.
4. Defina a quantidade máxima e clique em **Sugerir cortes**.
5. Edite os tempos e títulos, exclua itens ou adicione cortes manuais.
6. Ajuste palavras por bloco, tamanho e posição das legendas.
7. Clique em **Gerar todos os cortes**.

Para sugestões contextuais, abra antes **Configurações de IA**, cole a chave no
campo oculto e clique em **Salvar no Windows**. A chave fica no cofre nativo do
Windows; ela não é gravada nos arquivos do projeto. Use **Testar conexão** para
consultar os modelos permitidos para seu projeto, escolha um deles, ajuste o
limite de chamadas e salve as preferências. Em **Cortes em lote**, clique em
**Analisar episódio com IA**. Revise a descrição, escolha uma das três opções de
título (ou edite-a) e ajuste os tempos antes de exportar.

O aplicativo não ativa faturamento. A análise usa a cota associada à chave e
para imediatamente em erro 429, exibindo o tempo de espera quando fornecido pelo
serviço. Resultados já processados são reutilizados do cache. Se a Gemini estiver
indisponível, a sugestão local, os cortes manuais e a exportação continuam ativos.
Alterar apenas o máximo de cortes não invalida a análise armazenada: o campo é um
limite superior de exibição, não uma quantidade obrigatória. A tela informa quantos
candidatos a IA retornou, quantos foram rejeitados e quantas janelas do episódio
foram efetivamente analisadas conforme o limite de chamadas configurado.

O lote processa um arquivo por vez. **Cancelar** interrompe a tarefa atual e
impede o início das seguintes. **Repetir erros** executa somente itens que
falharam. Arquivos existentes nunca são substituídos; um sufixo numérico é
acrescentado quando necessário.

## Ajustes audiovisuais

Abra **Ajustes AV** para configurar e salvar o preset reutilizado nas próximas
prévias e exportações. Os valores iniciais do **IA TOONS 4K** são:

- intensidade: 100%;
- nitidez: 0,65;
- contraste: 1,06;
- saturação: 1,12;
- brilho: 0,01;
- redução de ruído: 1,5;
- redimensionamento: Lanczos com arredondamento preciso e chroma completo;
- volume inicial: 1,00;
- volume final: 1,25;
- curva: 8 segundos;
- limiter: 0,95.

A cadeia de imagem usa `hqdn3d`, `scale` com Lanczos, `crop`, `eq` e
`unsharp`, nesta ordem. Ela é aplicada antes do `overlay`, exclusivamente ao
vídeo de animação. Fundo, logo, títulos e legendas não recebem esses filtros.
O áudio usa `volume` com expressão temporal e `alimiter` com ganho automático
desativado.

O nome do preset descreve o estilo de acabamento. Redimensionar para alta
qualidade não recupera detalhes reais ausentes, e o preset não reproduz os
algoritmos proprietários do CapCut.

## Testes

Os testes unitários não exigem dependências adicionais:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```
