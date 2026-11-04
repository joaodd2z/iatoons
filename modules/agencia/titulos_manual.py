"""Editorial override exclusive to Agency; automatic title rules stay unchanged."""

from modules import titulos_v2 as V


def manual_confirmado(plano):
    return (plano.titulo_aprovado_manualmente
            and not plano.titulo_confirmacao_pendente
            and plano.titulo_escolhido.strip() == plano.titulo_manual_confirmado
            and not V.titulo_pendente(plano.titulo_escolhido))


def editar_titulo(plano, texto):
    texto = texto.strip()
    if texto == plano.titulo_escolhido:
        return
    plano.titulo_escolhido = texto
    plano.titulo_aprovado_manualmente = False
    plano.titulo_auto_aprovado = False
    plano.titulo_confirmacao_pendente = True
    plano.status = V.STATUS_REVISAO_TITULO
    plano.erro = "Confirme o titulo editado em Aprovar titulo."


def aprovar_titulo(plano, texto):
    texto = texto.strip()
    if V.titulo_pendente(texto):
        editar_titulo(plano, texto)
        plano.titulo_aprovado_manualmente = False
        plano.titulo_auto_aprovado = False
        plano.titulo_confirmacao_pendente = True
        V.marcar_revisao_titulo(plano)
        raise ValueError("Informe um titulo antes de aprovar; placeholders nao podem ser aprovados.")
    plano.titulo_escolhido = texto
    plano.titulo_manual_confirmado = texto
    plano.titulo_origem = "manual"
    plano.titulo_aprovado_manualmente = True
    plano.titulo_auto_aprovado = False
    plano.titulo_confirmacao_pendente = False
    if plano.status == V.STATUS_REVISAO_TITULO:
        plano.status, plano.erro = "pendente", ""
    plano.avisos = [a for a in plano.avisos if not a.startswith(("Titulo V2:", "Título V2:"))]
    plano.selecionado = True


def exigir_confirmacao(plano):
    if manual_confirmado(plano):
        return True
    if plano.titulo_aprovado_manualmente or plano.titulo_confirmacao_pendente or plano.titulo_origem == "manual":
        plano.titulo_aprovado_manualmente = False
        plano.titulo_auto_aprovado = False
        plano.titulo_confirmacao_pendente = True
        V.marcar_revisao_titulo(plano)
        raise ValueError("O titulo editado precisa de nova aprovacao manual.")
    V.exigir_titulo_principal(plano)
    return False


def validar_para_exportacao(plano, transcricao):
    from modules.agencia.producao import exigir_corte
    exigir_corte(plano, transcricao)
    if exigir_confirmacao(plano):
        return None, None
    plano.titulo_auto_aprovado = False
    contexto, avaliacao = V.validar_titulo_final(plano, transcricao)
    plano.titulo_auto_aprovado = True
    return contexto, avaliacao


def selecionar_prontos(planos, transcricao):
    prontos, pendentes = [], []
    for plano in planos:
        if not plano.selecionado:
            continue
        try:
            validar_para_exportacao(plano, transcricao)
        except ValueError:
            plano.titulo_auto_aprovado = False
            if plano.estado_editorial == "revisao":
                from modules.agencia.producao import REVISAO
                plano.status = REVISAO
            else:
                V.marcar_revisao_titulo(plano)
            pendentes.append(plano)
        else:
            prontos.append(plano)
    return prontos, pendentes


def reavaliar_salvos(planos, transcricao):
    automaticos = []
    for plano in planos:
        if plano.estado_editorial == "revisao":
            continue
        if manual_confirmado(plano):
            continue
        if plano.titulo_origem == "manual" or plano.titulo_confirmacao_pendente or plano.titulo_aprovado_manualmente:
            try:
                exigir_confirmacao(plano)
            except ValueError:
                pass
        else:
            automaticos.append(plano)
    if automaticos:
        V.reavaliar_existentes(automaticos, transcricao)


def status_visual(plano):
    if plano.estado_editorial == "revisao":
        from modules.agencia.producao import manual_confirmado as corte_manual
        return "APROVADO MANUALMENTE" if corte_manual(plano) else "REVISAO HUMANA RECOMENDADA"
    if manual_confirmado(plano):
        return "✓ TÍTULO APROVADO MANUALMENTE" + (
            " · " + plano.status if plano.status != "pendente" else "")
    if plano.titulo_auto_aprovado and plano.validacao_remix.get("remix_valido") and plano.status == "pendente":
        return "APROVADO AUTOMATICAMENTE"
    return plano.status
