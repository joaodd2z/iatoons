"""MODO AGÊNCIA / NOVA CONTA — extensão modular e independente do preset legado.

Submódulos:
    layout      enquadramento por tipo de fonte e ajuste de título/legenda às safe zones
    av          preset AGENCY_AV_V1 (tratamento moderado, somente no conteúdo principal)
    editorial   plano editorial (Gemini opcional + conversão local de análises existentes)
    timeline    montagem GANCHO → CONTEXTO → … → CONSEQUÊNCIA, cortes internos e transições
    renderer    composição FFmpeg 1080×1920/60 FPS com o vídeo como relógio principal
    qualidade   QUALITY GATE (antes e depois da renderização, incluindo primeiro frame)
    capa        capa integrada com 3 frames candidatos
    metricas    métricas internas por corte
    persistencia listas do Modo Agência, separadas das listas legadas
    pipeline    orquestração: planejar → validar → renderizar → verificar → exportar
"""

MODO_AGENCIA = "agencia"
