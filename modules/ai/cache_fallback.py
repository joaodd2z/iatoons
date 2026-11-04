"""Índice sem conteúdo editorial apontando para caches dos providers reais."""
from copy import deepcopy
import json


def identidade(config):
    return {"ordem": config.ordem_fallback, "preferido": config.provider_preferido,
            "autenticacao": config.fallback_autenticacao,
            "modelos": {p: getattr(config, f"modelo_{p}") for p in config.ordem_fallback}}


def escrever(caminho, dados):
    caminho.parent.mkdir(parents=True, exist_ok=True)
    tmp = caminho.with_suffix(caminho.suffix + ".tmp")
    tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(caminho)


def salvar_grupos(base, config, respostas, campo, dados, caminho_real, validar=None):
    grupos = {}
    for resposta, itens in respostas:
        chave = (resposta["provider_utilizado"], resposta["modelo_utilizado"])
        grupo = grupos.setdefault(chave, {"itens": [], "metadados": []})
        grupo["itens"].extend(itens)
        grupo["metadados"].append({k: resposta[k] for k in (
            "provider_solicitado", "provider_utilizado", "modelo_utilizado",
            "numero_de_tentativas", "fallback_ocorreu", "motivo_fallback", "latencia_total_ms")})
    referencias = []
    for (provider, modelo), grupo in grupos.items():
        real = deepcopy(config)
        real.provider_ativo, real.modelo = provider, modelo
        destino = caminho_real(real)
        conteudo = {**dados, campo: grupo["itens"], "provider": provider, "modelo": modelo,
                    "metadados_ia": grupo["metadados"]}
        if validar:
            conteudo["candidatos"] = validar(grupo["itens"])
        escrever(destino, conteudo)
        referencias.append({"arquivo": destino.name, "provider": provider, "modelo": modelo})
    escrever(base.with_suffix(".fallback.json"), {"config": identidade(config), "referencias": referencias})


from modules.performance import medir_operacao


@medir_operacao("cache_analise", cache=True)
def carregar_grupos(base, config, campo):
    if not config.fallback_automatico:
        return None
    try:
        indice = json.loads(base.with_suffix(".fallback.json").read_text(encoding="utf-8"))
        if indice["config"] != identidade(config) or not indice["referencias"]:
            return None
        resultado, itens, metadados = None, [], []
        for ref in indice["referencias"]:
            # Nunca permite ao índice ler fora do diretório de caches.
            if not isinstance(ref["arquivo"], str) or ref["arquivo"] != base.parent.joinpath(ref["arquivo"]).name:
                return None
            dados = json.loads((base.parent / ref["arquivo"]).read_text(encoding="utf-8"))
            if dados.get("provider") != ref["provider"] or dados.get("modelo") != ref["modelo"]:
                return None
            if resultado and any(dados.get(k) != resultado.get(k) for k in ("video_id", "hash_transcricao")):
                return None
            resultado = dados if resultado is None else resultado
            itens.extend(dados[campo])
            metadados.extend(dados.get("metadados_ia", []))
        return {**resultado, campo: itens, "metadados_ia": metadados, "indice_fallback": True}
    except (OSError, ValueError, KeyError, TypeError):
        return None
