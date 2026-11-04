"""
main.py — Ponto de entrada do aplicativo IA TOONS CUT
"""

import sys
from pathlib import Path

# Adiciona o diretório atual ao PYTHONPATH para permitir imports relativos
sys.path.insert(0, str(Path(__file__).parent))

from ui.app import AppIAToons
from modules.preset import caminhos_validos


def main():
    # Verifica assets obrigatórios antes de iniciar
    ok, erros = caminhos_validos()
    if not ok:
        print("Erros de configuração encontrados:")
        for erro in erros:
            print(f" - {erro}")
        print("\nVerifique se o fundo e a fonte existem na pasta 'assets'.")
        # Inicia a UI mesmo assim para o usuário ver, 
        # mas os recursos visuais vão falhar
    
    app = AppIAToons()
    app.mainloop()


if __name__ == "__main__":
    main()
