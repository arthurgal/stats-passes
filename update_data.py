import json
import os
import glob
from datetime import datetime

def get_latest_json_file():
    # Encontra todos os arquivos JSON que começam com 'sofascore_analysis'
    json_files = glob.glob('sofascore_analysis_*.json')
    
    if not json_files:
        print("Nenhum arquivo JSON encontrado.")
        return None
    
    # Ordena os arquivos por data de modificação (mais recente primeiro)
    latest_file = max(json_files, key=os.path.getmtime)
    return latest_file

def update_load_data_js(json_file):
    # Lê o arquivo JSON
    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # Formata os dados para o arquivo JavaScript
    js_content = """// Este arquivo carrega os dados do JSON para a aplicação
// Definindo a variável jsonData que será usada pelo index.html
// Atualizado automaticamente em: {}
jsonData = {};
""".format(datetime.now().strftime("%d/%m/%Y %H:%M:%S"), json.dumps(data, indent=2, ensure_ascii=False))
    
    # Escreve no arquivo load-data.js
    with open('load-data.js', 'w', encoding='utf-8') as f:
        f.write(js_content)
    
    print(f"Arquivo load-data.js atualizado com sucesso usando os dados de {json_file}!")

if __name__ == "__main__":
    latest_json = get_latest_json_file()
    if latest_json:
        update_load_data_js(latest_json)
        print(f"Arquivo mais recente: {latest_json}")
    else:
        print("Não foi possível encontrar um arquivo JSON para atualizar o load-data.js")