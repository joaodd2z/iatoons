"""Fábrica multi-conta: produção em massa sem interface, para várias contas e nichos ao mesmo tempo.

Cada conta (contas.json) tem a própria pasta de entrada, perfil visual, quantidade de cortes por episódio, posts por dia,
horários e hashtags. A fábrica:

1. ``produzir``: joga todo episódio novo de ENTRADA/<conta>/ na EsteiraProducao (transcrição → análise → títulos →
   preparação → render), com o perfil e o preset da conta, e espera terminar. Episódio já feito não é refeito.
2. ``distribuir``: pega os cortes prontos de PRODUZIDOS/<conta>/ e monta PARA_POSTAR/<conta>/<data>/ com os vídeos
   numerados na ordem de postagem, a legenda de cada um (título + hashtags) e AGENDA.csv com o horário. Corte nunca
   é distribuído duas vezes, e episódios são intercalados (o público não vê 5 cortes seguidos do mesmo episódio).

Os vídeos que a esteira manda para revisão (título ou corte sem confiança) NÃO entram na agenda: aparecem no status
para revisar na interface. A fábrica não publica nada sozinha.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import csv
import json
from pathlib import Path
import re
import shutil
import time
import unicodedata

VIDEOS = {".mp4", ".mkv", ".mov", ".avi", ".webm"}
REGISTRO_NOME = "fabrica_registro.json"


# ─── Configuração ─────────────────────────────────────────────────────────────

@dataclass
class Conta:
    id: str
    nome: str = ""
    perfil: str = "toonmix"
    ativa: bool = True
    cortes_por_episodio: int = 10
    posts_por_dia: int = 30
    horarios: list[str] = field(default_factory=list)
    janela: tuple[str, str] = ("07:00", "23:30")
    hashtags: str = ""
    idioma: str = "pt"
    qualidade: str = "rapida"           # rapida (veryfast, CRF 20) | maxima (slow, CRF 17, padrão do app)

    @classmethod
    def de_dict(cls, d: dict) -> "Conta":
        cid = slug(d.get("id") or d.get("nome") or "")
        if not cid:
            raise ValueError("Conta sem id/nome em contas.json.")
        j = d.get("janela") or ["07:00", "23:30"]
        return cls(
            id=cid,
            nome=d.get("nome") or cid,
            perfil=str(d.get("perfil") or "toonmix").lower(),
            ativa=bool(d.get("ativa", True)),
            cortes_por_episodio=max(1, int(d.get("cortes_por_episodio", 10))),
            posts_por_dia=max(1, int(d.get("posts_por_dia", 30))),
            horarios=[h for h in (d.get("horarios") or []) if _hora_valida(h)],
            janela=(j[0], j[1]),
            hashtags=str(d.get("hashtags") or "").strip(),
            idioma=d.get("idioma") or "pt",
            qualidade=str(d.get("qualidade") or "rapida").lower(),
        )

    def grade_horarios(self) -> list[str]:
        """Horários do dia: os informados ou, se faltar, espalhados por igual na janela."""
        if len(self.horarios) >= self.posts_por_dia:
            return sorted(self.horarios)[: self.posts_por_dia]
        ini, fim = _minutos(self.janela[0]), _minutos(self.janela[1])
        if fim <= ini:
            fim = ini + 16 * 60
        n = self.posts_por_dia
        passo = (fim - ini) / max(1, n - 1) if n > 1 else 0
        return [f"{int(ini + i * passo) // 60:02d}:{int(ini + i * passo) % 60:02d}" for i in range(n)]


@dataclass
class ConfigFabrica:
    pasta_base: Path
    contas: list[Conta]

    @property
    def entrada(self) -> Path:
        return self.pasta_base / "ENTRADA"

    @property
    def produzidos(self) -> Path:
        return self.pasta_base / "PRODUZIDOS"

    @property
    def para_postar(self) -> Path:
        return self.pasta_base / "PARA_POSTAR"

    @property
    def registro(self) -> Path:
        return self.pasta_base / REGISTRO_NOME

    def conta(self, cid: str) -> Conta:
        for c in self.contas:
            if c.id == slug(cid):
                return c
        raise KeyError(f"Conta '{cid}' não existe em contas.json.")


def carregar_config(caminho: str | Path) -> ConfigFabrica:
    d = json.loads(Path(caminho).read_text(encoding="utf-8"))
    contas = [Conta.de_dict(c) for c in d.get("contas", [])]
    ids = [c.id for c in contas]
    if len(ids) != len(set(ids)):
        raise ValueError("Há contas com o mesmo id em contas.json.")
    return ConfigFabrica(pasta_base=Path(d.get("pasta_base") or "C:/IA_TOONS_FABRICA"), contas=contas)


def preparar_pastas(cfg: ConfigFabrica) -> list[Path]:
    criadas = []
    for c in cfg.contas:
        for p in (cfg.entrada / c.id, cfg.produzidos / c.id, cfg.para_postar / c.id):
            if not p.exists():
                p.mkdir(parents=True, exist_ok=True)
                criadas.append(p)
    return criadas


# ─── Utilidades ───────────────────────────────────────────────────────────────

def slug(texto: str, limite: int = 60) -> str:
    t = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode()
    t = re.sub(r"[^A-Za-z0-9]+", "_", t).strip("_").lower()
    return t[:limite].strip("_")


def _hora_valida(h) -> bool:
    return bool(re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(h)))


def _minutos(h: str) -> int:
    a, b = str(h).split(":")
    return int(a) * 60 + int(b)


def _ler_json(p: Path, padrao):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return padrao


def _gravar_json(p: Path, dados) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


# ─── Inventário ───────────────────────────────────────────────────────────────

def episodios_na_entrada(cfg: ConfigFabrica, conta: Conta) -> list[Path]:
    pasta = cfg.entrada / conta.id
    if not pasta.is_dir():
        return []
    return sorted(p for p in pasta.rglob("*") if p.is_file() and p.suffix.lower() in VIDEOS)


@dataclass
class CortePronto:
    arquivo: Path
    episodio: str
    titulo: str
    duracao: float
    capa: str
    chave: str


def cortes_prontos(cfg: ConfigFabrica, conta: Conta) -> list[CortePronto]:
    """Todo MP4 exportado (com .metricas.json válido) em PRODUZIDOS/<conta>/<episodio>/."""
    pasta = cfg.produzidos / conta.id
    out: list[CortePronto] = []
    if not pasta.is_dir():
        return out
    for mp4 in sorted(pasta.rglob("*.mp4")):
        met = mp4.with_suffix(".metricas.json")
        if not met.is_file():
            continue                      # sem sidecar = render incompleto ou revisão: não vai para postagem
        m = _ler_json(met, {})
        titulo = (m.get("titulo_escolhido") or mp4.stem).strip()
        capa = m.get("capa") or ""
        out.append(CortePronto(
            arquivo=mp4,
            episodio=mp4.parent.name,
            titulo=titulo,
            duracao=float(m.get("duracao") or 0.0),
            capa=capa if capa and Path(capa).is_file() else "",
            chave=f"{conta.id}/{mp4.relative_to(pasta).as_posix()}",
        ))
    return out


# ─── Distribuição / agenda ────────────────────────────────────────────────────

def intercalar_por_episodio(cortes: list[CortePronto]) -> list[CortePronto]:
    """Round-robin entre episódios: A1 B1 C1 A2 B2 C2… (mantém a ordem interna de cada episódio)."""
    grupos: dict[str, list[CortePronto]] = {}
    for c in cortes:
        grupos.setdefault(c.episodio, []).append(c)
    filas = list(grupos.values())
    out: list[CortePronto] = []
    while any(filas):
        for f in filas:
            if f:
                out.append(f.pop(0))
    return out


def legenda_post(conta: Conta, titulo: str) -> str:
    return f"{titulo.strip()}\n\n{conta.hashtags}".strip() + "\n"


def _capa_do_modelo(conta: Conta, mp4: Path, titulo: str) -> bool:
    """Conta com modelo salvo no Estúdio de Capa: a capa sai no padrão dela (regra da agência). False = sem modelo/erro."""
    from modules import capa_estudio as CE
    if not CE.tem_modelo(conta.id) or not mp4.is_file():
        return False
    try:
        CE.gerar_capa(mp4, conta.perfil, CE.carregar_modelo(conta.id), titulo, mp4.with_name(mp4.stem + "_capa.jpg"))
        return True
    except (RuntimeError, OSError, ValueError):
        return False


def distribuir(cfg: ConfigFabrica, conta: Conta, *, inicio: date | None = None, dias: int = 1,
               copiar: bool = True) -> dict:
    """Monta PARA_POSTAR/<conta>/<data>/ com até posts_por_dia vídeos por dia, sem repetir corte já distribuído."""
    registro = _ler_json(cfg.registro, {})
    usados: dict = registro.setdefault("distribuidos", {})
    disponiveis = [c for c in cortes_prontos(cfg, conta) if c.chave not in usados]
    fila = intercalar_por_episodio(disponiveis)
    grade = conta.grade_horarios()
    inicio = inicio or date.today()
    agora = datetime.now()
    resumo = {"conta": conta.id, "dias": [], "sem_estoque": False, "restante_em_estoque": 0}

    for d in range(max(1, dias)):
        dia = inicio + timedelta(days=d)
        pasta_dia = cfg.para_postar / conta.id / dia.isoformat()
        ja_no_dia = _ler_agenda(pasta_dia / "AGENDA.csv")
        ocupados = {r["horario"] for r in ja_no_dia}
        livres = [h for h in grade if h not in ocupados]
        if dia == agora.date():                               # hoje: só horários que ainda não passaram
            livres = [h for h in livres if _minutos(h) > agora.hour * 60 + agora.minute + 5]
        linhas = list(ja_no_dia)
        feitos = 0
        for h in livres:
            if not fila:
                resumo["sem_estoque"] = True
                break
            c = fila.pop(0)
            n = len(linhas) + 1
            nome = f"{n:03d}_{h.replace(':', 'h')}_{slug(c.titulo, 50) or 'corte'}"
            pasta_dia.mkdir(parents=True, exist_ok=True)
            destino = pasta_dia / f"{nome}.mp4"
            if copiar:
                shutil.copy2(c.arquivo, destino)
            else:
                shutil.move(str(c.arquivo), destino)
            (pasta_dia / f"{nome}.txt").write_text(legenda_post(conta, c.titulo), encoding="utf-8")
            if not _capa_do_modelo(conta, destino, c.titulo) and c.capa:
                try:
                    shutil.copy2(c.capa, pasta_dia / f"{nome}_capa.jpg")
                except OSError:
                    pass
            linhas.append({"ordem": n, "horario": h, "arquivo": destino.name, "titulo": c.titulo,
                           "duracao_s": round(c.duracao, 1), "episodio": c.episodio,
                           "legenda": legenda_post(conta, c.titulo).strip().replace("\n", " ")})
            usados[c.chave] = {"dia": dia.isoformat(), "horario": h, "arquivo": str(destino)}
            feitos += 1
        if feitos:
            linhas.sort(key=lambda r: r["horario"])
            _gravar_agenda(pasta_dia / "AGENDA.csv", linhas)
        resumo["dias"].append({"dia": dia.isoformat(), "novos": feitos, "total_no_dia": len(linhas),
                               "meta": conta.posts_por_dia, "pasta": str(pasta_dia)})
        _gravar_json(cfg.registro, registro)   # grava a cada dia: nada é distribuído duas vezes mesmo se cair no meio
    resumo["restante_em_estoque"] = len(fila)
    return resumo


CAMPOS_AGENDA = ["ordem", "horario", "arquivo", "titulo", "duracao_s", "episodio", "legenda"]


def _ler_agenda(p: Path) -> list[dict]:
    if not p.is_file():
        return []
    with open(p, encoding="utf-8-sig", newline="") as f:
        return [dict(r) for r in csv.DictReader(f, delimiter=";")]


def _gravar_agenda(p: Path, linhas: list[dict]) -> None:
    with open(p, "w", encoding="utf-8-sig", newline="") as f:     # utf-8-sig + ";" abre certo no Excel BR
        w = csv.DictWriter(f, fieldnames=CAMPOS_AGENDA, delimiter=";", extrasaction="ignore")
        w.writeheader()
        for i, r in enumerate(linhas, 1):
            w.writerow({**r, "ordem": i})


# ─── Produção (EsteiraProducao) ───────────────────────────────────────────────

def ajustes_da_conta(conta: Conta):
    """Preset AV do app; em 'rapida' só o encoder muda (veryfast + CRF 20): ~3-4x mais rápido, visual quase igual no celular."""
    from modules.agencia.av import carregar_ajustes_agencia
    import copy
    aj = copy.deepcopy(carregar_ajustes_agencia())
    if conta.qualidade == "rapida":
        aj.preset_x264 = "veryfast"
        aj.crf = 20
    return aj


def produzir(cfg: ConfigFabrica, contas: list[Conta] | None = None, *, esteira=None, log=print,
             intervalo_status: float = 30.0, timeout: float | None = None) -> dict:
    """Coloca todos os episódios novos das contas na esteira e espera terminar. Devolve o resumo por conta."""
    contas = [c for c in (contas or cfg.contas) if c.ativa]
    if esteira is None:
        from modules.esteira import EsteiraProducao
        from modules.preset import DADOS_DIR
        esteira = EsteiraProducao(caminho_persistencia=DADOS_DIR / "fabrica" / "estado_fabrica.json",
                                  carregar_estado_inicial=True)
    total_novos = 0
    for c in contas:
        videos = episodios_na_entrada(cfg, c)
        if not videos:
            log(f"[{c.id}] nenhum episódio em {cfg.entrada / c.id}")
            continue
        add, ign = esteira.adicionar_episodios(
            videos, pasta_saida=cfg.produzidos / c.id, perfil_visual_id=c.perfil, idioma=c.idioma,
            max_cortes=c.cortes_por_episodio, ajustes_av=ajustes_da_conta(c),
        )
        total_novos += len(add)
        log(f"[{c.id}] {len(add)} episódio(s) na fila, {len(ign)} já feitos/ignorados")
    if not total_novos:
        return status(cfg, esteira)
    esteira.iniciar()
    inicio = time.time()
    while not esteira.aguardar_conclusao(timeout=intervalo_status):
        st = esteira.status_concorrencia()
        eps = esteira.listar_episodios()
        ativos = [e for e in eps if e.estado == "em_execucao"]
        log(f"… {len([e for e in eps if e.estado in ('concluido', 'revisao')])}/{len(eps)} episódios prontos | "
            + " | ".join(f"{e.nome[:28]}: {e.rotulo_etapa_atual} {e.progresso_pct:.0f}%" for e in ativos[:3])
            + f" | filas T{st['fila_transcricao']} A{st['fila_agencia']} R{st['fila_renderizacao']}")
        if timeout and time.time() - inicio > timeout:
            log("tempo limite atingido: a esteira continua do ponto em que parou na próxima execução.")
            break
    return status(cfg, esteira)


def status(cfg: ConfigFabrica, esteira=None) -> dict:
    registro = _ler_json(cfg.registro, {})
    usados = registro.get("distribuidos", {})
    out = {}
    for c in cfg.contas:
        prontos = cortes_prontos(cfg, c)
        estoque = [p for p in prontos if p.chave not in usados]
        info = {"ativa": c.ativa, "perfil": c.perfil, "episodios_entrada": len(episodios_na_entrada(cfg, c)),
                "cortes_prontos_total": len(prontos), "cortes_em_estoque": len(estoque),
                "posts_por_dia": c.posts_por_dia,
                "dias_de_estoque": round(len(estoque) / c.posts_por_dia, 1)}
        if esteira is not None:
            eps = [e for e in esteira.listar_episodios()
                   if Path(e.pasta_saida_base).resolve() == (cfg.produzidos / c.id).resolve()]
            info["episodios_em_revisao"] = [e.nome for e in eps if e.estado == "revisao"]
            info["episodios_com_erro"] = [f"{e.nome}: {e.erro[:120]}" for e in eps if e.estado == "erro"]
        out[c.id] = info
    return out
