"""Fábrica IA TOONS: produção em massa para várias contas, sem abrir a interface.

Uso (na pasta do projeto, com o .venv):
  .venv\\Scripts\\python.exe fabrica.py preparar              cria contas.json (se não existir) e as pastas de cada conta
  .venv\\Scripts\\python.exe fabrica.py produzir              produz os cortes de todos os episódios novos das contas ativas
  .venv\\Scripts\\python.exe fabrica.py distribuir [--dias 2] monta PARA_POSTAR/<conta>/<data>/ (vídeos + legendas + AGENDA.csv)
  .venv\\Scripts\\python.exe fabrica.py tudo [--dias 2]       produzir e depois distribuir (o normal para deixar rodando à noite)
  .venv\\Scripts\\python.exe fabrica.py status                quanto tem de estoque por conta e quantos dias isso cobre
Opções: --conta <id> (só uma conta) · --config <arquivo> (padrão: contas.json)
"""
import argparse
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from modules import fabrica as F  # noqa: E402

AQUI = Path(__file__).resolve().parent


def main() -> int:
    ap = argparse.ArgumentParser(description="Fábrica IA TOONS (multi-conta)")
    ap.add_argument("comando", choices=["preparar", "produzir", "distribuir", "tudo", "status"])
    ap.add_argument("--config", default=str(AQUI / "contas.json"))
    ap.add_argument("--conta", default="")
    ap.add_argument("--dias", type=int, default=1)
    args = ap.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.is_file():
        shutil.copyfile(AQUI / "contas.exemplo.json", cfg_path)
        print(f"Criei {cfg_path.name} a partir do exemplo. Ajuste as contas e rode de novo.")
        if args.comando != "preparar":
            return 1
    cfg = F.carregar_config(cfg_path)
    contas = [cfg.conta(args.conta)] if args.conta else cfg.contas

    if args.comando == "preparar":
        criadas = F.preparar_pastas(cfg)
        print(f"Pastas em {cfg.pasta_base}:")
        for c in cfg.contas:
            print(f"  {c.id:<20} coloque os episódios em {cfg.entrada / c.id}")
        print(f"({len(criadas)} pasta(s) criada(s) agora)")
        return 0

    if args.comando in ("produzir", "tudo"):
        F.preparar_pastas(cfg)
        resumo = F.produzir(cfg, contas)
        _imprimir_status(resumo)

    if args.comando in ("distribuir", "tudo"):
        for c in contas:
            if not c.ativa:
                continue
            r = F.distribuir(cfg, c, dias=args.dias)
            for d in r["dias"]:
                falta = d["meta"] - d["total_no_dia"]
                aviso = f"  (faltam {falta} para a meta: coloque mais episódios em ENTRADA/{c.id})" if falta > 0 else ""
                print(f"[{c.id}] {d['dia']}: {d['total_no_dia']}/{d['meta']} vídeos agendados{aviso}")
            print(f"[{c.id}] sobram {r['restante_em_estoque']} cortes prontos em estoque")

    if args.comando == "status":
        _imprimir_status(F.status(cfg))
    return 0


def _imprimir_status(resumo: dict) -> None:
    print("\nSTATUS POR CONTA")
    for cid, s in resumo.items():
        if not s.get("ativa", True):
            continue
        print(f"  {cid:<20} entrada: {s['episodios_entrada']:>3} ep. | prontos: {s['cortes_prontos_total']:>4} | "
              f"estoque: {s['cortes_em_estoque']:>4} (= {s['dias_de_estoque']} dia(s) a {s['posts_por_dia']}/dia)")
        for nome in s.get("episodios_em_revisao", []):
            print(f"      revisar na interface: {nome}")
        for erro in s.get("episodios_com_erro", []):
            print(f"      ERRO: {erro}")


if __name__ == "__main__":
    sys.exit(main())
