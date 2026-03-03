# =============================================================================
# ARQUIVO: scripts/generate_doc.py
# OBJETIVO: Automatizar a geração de documentação técnica para arquivos alterados no repositório,
#           classificando-os em projetos Asana adequados e criando subtarefas com a documentação gerada.
# O QUE MUDOU: Refatoração para utilizar API do OpenAI para escolher projeto Asana automaticamente,
#              integração com API Asana para criação de tarefas e subtarefas,
#              melhorias no tratamento de arquivos e diffs, e geração de documentação formatada.
# DEPENDENCIAS: requests, subprocess, datetime, OpenAI API, Asana API
# =============================================================================

import os
import re
import json
import subprocess
import requests
from datetime import datetime

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ASANA_TOKEN = os.getenv("ASANA_TOKEN")

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY não definida")
if not ASANA_TOKEN:
    raise RuntimeError("ASANA_TOKEN não definida")


# Carrega o mapeamento de projetos Asana a partir do arquivo JSON local.
# Retorna:
#   dict contendo as configurações do projeto, incluindo default_project_id e workspace_id.
# Lança RuntimeError se o arquivo não existir ou estiver inválido.
def load_project_map() -> dict:
    try:
        with open("asana_map.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        raise RuntimeError("asana_map.json não encontrado ou inválido")


# Busca os projetos disponíveis em um workspace Asana via API.
# Parâmetros:
#   workspace_id (str): ID do workspace Asana para buscar projetos.
# Retorna:
#   lista de projetos (cada projeto é um dict com informações como 'gid' e 'name').
# Lança exceção se a requisição falhar.
def fetch_asana_projects(workspace_id: str) -> list:
    headers = {
        "Authorization": f"Bearer {ASANA_TOKEN}",
        "Content-Type": "application/json",
    }
    url = f"https://app.asana.com/api/1.0/workspaces/{workspace_id}/projects"
    r = requests.get(url, headers=headers, timeout=20)

    if not r.ok:
        print("ERRO ao buscar projetos:", r.status_code, r.text)
        r.raise_for_status()

    projects = r.json().get("data", [])
    print(f"Projetos encontrados: {[p['name'] for p in projects]}")
    return projects


# Utiliza a API do OpenAI para escolher o projeto Asana mais adequado para um arquivo,
# baseado no nome, caminho e conteúdo do arquivo.
# Parâmetros:
#   file (str): nome/caminho do arquivo.
#   content (str): conteúdo completo do arquivo.
#   projects (list): lista de projetos disponíveis para escolha.
# Retorna:
#   str com o ID do projeto escolhido ou None se a escolha for inválida.
def gpt_choose_project(file: str, content: str, projects: list) -> str:
    # Monta lista de projetos para o prompt
    project_list = "\n".join([
        f"- ID: {p['gid']} | Nome: {p['name']}"
        for p in projects
    ])

    # Prompt detalhado para o modelo GPT escolher o projeto correto
    prompt = f"""
Você é um engenheiro de software sênior.
Analise o arquivo abaixo e escolha qual projeto Asana é mais adequado para receber a documentação dele.

PROJETOS DISPONÍVEIS:
{project_list}

REGRAS:
- Escolha apenas UM projeto
- Base sua escolha no nome do arquivo, caminho e conteúdo
- Responda APENAS com o ID do projeto escolhido, sem mais nada
- Exemplo de resposta válida: 1202515061506196

Arquivo: {file}

Conteúdo (primeiras 3000 chars):
{content[:3000]}
"""

    # Chamada à API OpenAI para obter a escolha do projeto
    response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": "gpt-4.1-mini",
            "messages": [
                {"role": "system", "content": "Você escolhe o projeto Asana mais adequado para um arquivo. Responda APENAS com o ID numérico do projeto, sem texto adicional."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
        },
        timeout=30,
    )
    response.raise_for_status()
    chosen_id = response.json()["choices"][0]["message"]["content"].strip()

    # Valida se o ID retornado está na lista de projetos disponíveis
    valid_ids = [p["gid"] for p in projects]
    if chosen_id in valid_ids:
        chosen_name = next(p["name"] for p in projects if p["gid"] == chosen_id)
        print(f"{file} → GPT escolheu [{chosen_name}] projeto {chosen_id}")
        return chosen_id

    # Caso o ID seja inválido, retorna None para usar default
    print(f"GPT retornou ID inválido ({chosen_id}), usando default")
    return None


# Determina o ID do projeto Asana para um arquivo, utilizando a escolha do GPT ou o default.
# Parâmetros:
#   file (str): nome/caminho do arquivo.
#   content (str): conteúdo completo do arquivo.
#   project_map (dict): mapeamento de projetos carregado do JSON.
#   projects (list): lista de projetos disponíveis.
# Retorna:
#   str com o ID do projeto Asana escolhido.
# Lança RuntimeError se não houver projeto válido e default não estiver definido.
def get_project_id_for_file(file: str, content: str, project_map: dict, projects: list) -> str:
    default = project_map.get("default_project_id")

    if projects:
        chosen = gpt_choose_project(file, content, projects)
        if chosen:
            return chosen

    if not default:
        raise RuntimeError(f"Nenhum projeto encontrado para {file} e default_project_id não definido")

    print(f"{file} → [Padrão] projeto {default}")
    return default


# Obtém a lista de arquivos alterados no último commit ou diff.
# Retorna:
#   lista de strings com os caminhos dos arquivos alterados, excluindo arquivos da pasta .github.
def get_changed_files():
    try:
        # Tenta obter arquivos alterados entre os dois últimos commits
        files = subprocess.check_output(
            ["git", "diff", "HEAD~1", "HEAD", "--name-only"], text=True)
    except Exception:
        # Se falhar, obtém arquivos do último commit
        files = subprocess.check_output(
            ["git", "show", "--pretty=", "--name-only", "HEAD"], text=True)
    # Filtra arquivos ignorando os que começam com .github/
    return [f for f in files.splitlines() if f and not f.startswith(".github/")]


# Obtém o diff do último commit para um arquivo específico.
# Parâmetros:
#   file (str): caminho do arquivo.
# Retorna:
#   str com o diff do arquivo ou string vazia se falhar.
def get_diff_for_file(file):
    try:
        return subprocess.check_output(
            ["git", "diff", "HEAD~1", "HEAD", "--", file], text=True)
    except Exception:
        return ""


# Lê o conteúdo completo de um arquivo.
# Parâmetros:
#   file (str): caminho do arquivo.
# Retorna:
#   str com o conteúdo do arquivo ou string vazia se falhar.
def get_file_content(file):
    try:
        with open(file, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""


# Gera a documentação técnica para um arquivo, utilizando a API OpenAI.
# Parâmetros:
#   file (str): nome/caminho do arquivo.
#   diff (str): diff do último commit para o arquivo.
# Retorna:
#   tuple (str, str): documentação gerada e conteúdo completo do arquivo.
def generate_doc(file, diff):
    content = get_file_content(file)
    if not content:
        # Retorna mensagem de erro e conteúdo vazio se arquivo não encontrado ou vazio
        return f"{file}\n\nArquivo não encontrado ou vazio.", ""

    # Prompt detalhado para geração da documentação técnica, com regras de formatação específicas
    prompt = f"""
Você é um engenheiro de software sênior fazendo code review completo.
Gere documentação técnica usando EXATAMENTE as marcações abaixo.

REGRAS DE ESCRITA:
- Seja direto, sem introduções genéricas ou floreios
- Explique o propósito geral antes dos detalhes
- Considere que o leitor é técnico (dev/infra/QA)
- Não repita o código inteiro
- Analise o CODIGO COMPLETO, não apenas o diff
- O diff serve apenas para indicar o que foi alterado neste commit

REGRAS DE FORMATAÇÃO (SIGA EXATAMENTE):
- NÃO use #, ##, **, *, ` ou qualquer símbolo Markdown
- Títulos: MAIÚSCULAS entre duas linhas exatas de ====================
- Listas: cada item começa com "> " (sinal de maior + espaço)
- Blocos de código: OBRIGATÓRIO usar [CODE] na linha antes e [/CODE] na linha depois
- Separe seções com uma linha em branco
- NUNCA escreva entidades HTML como &lt; &gt; &amp; — escreva os caracteres diretamente

QUANDO USAR [CODE]:
- Exemplos de chamada de função
- Trechos de SQL relevantes
- Exemplos de entrada/saída

ESTRUTURA EXATA:

====================
ARQUIVO
====================
nome do arquivo

====================
O QUE MUDOU NESTE COMMIT
====================
> item 1 (baseado no diff)

====================
OBJETIVO DO CODIGO
====================
Explicação direta do propósito geral.

====================
ENTRADA ESPERADA
====================
> Parâmetros, tipos e exemplos reais
[CODE]
exemplo de entrada se aplicável
[/CODE]

====================
SAIDA GERADA
====================
> O que retorna
[CODE]
exemplo de saída se aplicável
[/CODE]

====================
FLUXO DE EXECUCAO
====================
> Passo 1
> Passo 2

====================
FUNCOES E METODOS PRINCIPAIS
====================
> nome_da_funcao: responsabilidade detalhada

====================
QUERIES E LOGICA DE DADOS
====================
> Descreva cada query ou lógica relevante
[CODE]
trecho da query principal aqui
[/CODE]

====================
REGRAS DE NEGOCIO
====================
> Regras implícitas identificadas

====================
DECISOES ARQUITETURAIS
====================
> Decisões relevantes

Arquivo: {file}

CODIGO COMPLETO:
{content}

DIFF DO COMMIT:
{diff if diff else "Sem diff disponível"}
"""

    # Chamada à API OpenAI para gerar a documentação
    response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": "gpt-4.1-mini",
            "messages": [
                {"role": "system", "content": "Você faz code review completo e gera documentação técnica profissional. Use apenas as marcações definidas no prompt, sem Markdown."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        },
        timeout=60,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"], content


# Realiza escape de caracteres especiais para XML/HTML,
# garantindo que o texto seja seguro para inserção em HTML.
# Parâmetros:
#   text (str): texto original.
# Retorna:
#   str com caracteres especiais convertidos para entidades XML.
def xml_escape(text: str) -> str:
    # Primeiro desfaz escapes já existentes para evitar duplo escape
    text = text.replace("&amp;", "&")
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    text = text.replace("&quot;", '"')
    text = text.replace("&#39;", "'")
    text = text.replace("&#96;", "`")
    # Aplica escapes corretos para XML
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    text = text.replace('"', "&quot;")
    return text


# Converte texto formatado com marcações específicas para HTML compatível com Asana.
# Parâmetros:
#   text (str): texto com marcações customizadas ([CODE], listas, títulos).
# Retorna:
#   str com HTML formatado para ser usado no campo html_notes do Asana.
def text_to_asana_html(text: str) -> str:
    lines = text.split("\n")
    html_parts = []
    i = 0

    while i < len(lines):
        line = lines[i]

        # Detecta blocos de código delimitados por [CODE] e [/CODE]
        if line.strip() == "[CODE]":
            code_lines = []
            i += 1
            # Acumula linhas até encontrar [/CODE]
            while i < len(lines) and lines[i].strip() != "[/CODE]":
                # Escapa caracteres especiais para HTML
                code_lines.append(xml_escape(lines[i]))
                i += 1
            # Envolve o código em tag <pre> para preservar formatação
            html_parts.append("<pre>" + "\n".join(code_lines) + "</pre>")
            i += 1
            continue

        # Detecta títulos formatados com linhas de ====================
        if line.strip().startswith("===================="):
            if i + 1 < len(lines):
                title = lines[i + 1].strip()
                if i + 2 < len(lines) and lines[i + 2].strip().startswith("===================="):
                    # Converte título para <h2>
                    html_parts.append(f"<h2>{xml_escape(title)}</h2>")
                    i += 3
                    continue

        # Detecta linhas de lista iniciadas com "> "
        if line.startswith("> "):
            content = xml_escape(line[2:].strip())
            # Envolve cada item em <ul><li> para criar lista simples
            html_parts.append(f"<ul><li>{content}</li></ul>")
            i += 1
            continue

        # Ignora linhas em branco
        if line.strip() == "":
            i += 1
            continue

        # Para linhas normais, também cria lista com um item
        html_parts.append(f"<ul><li>{xml_escape(line)}</li></ul>")
        i += 1

    return "\n".join(html_parts)


# Busca a tarefa pai "DOCUMENTAÇÃO" em um projeto Asana ou cria uma nova se não existir.
# Parâmetros:
#   project_id (str): ID do projeto Asana.
# Retorna:
#   str com o ID da tarefa pai "DOCUMENTAÇÃO".
# Lança exceção se a requisição falhar.
def get_or_create_parent_task(project_id: str) -> str:
    headers = {
        "Authorization": f"Bearer {ASANA_TOKEN}",
        "Content-Type": "application/json",
    }

    url = f"https://app.asana.com/api/1.0/projects/{project_id}/tasks"
    r = requests.get(url, headers=headers, timeout=20)

    if not r.ok:
        print("ERRO ao buscar tarefas:", r.status_code, r.text)
        r.raise_for_status()

    tasks = r.json().get("data", [])

    # Procura tarefa com nome DOCUMENTAÇÃO (case insensitive)
    for task in tasks:
        if task.get("name", "").upper() == "DOCUMENTAÇÃO":
            print(f"Tarefa DOCUMENTAÇÃO encontrada: {task['gid']}")
            return task["gid"]

    # Se não encontrar, cria a tarefa DOCUMENTAÇÃO no projeto
    print("Tarefa DOCUMENTAÇÃO não encontrada. Criando...")
    create_url = "https://app.asana.com/api/1.0/tasks"
    payload = {
        "data": {
            "name": "DOCUMENTAÇÃO",
            "projects": [project_id],
        }
    }
    r = requests.post(create_url, json=payload, headers=headers, timeout=20)

    if not r.ok:
        print("ERRO ao criar tarefa pai:", r.status_code, r.text)
        r.raise_for_status()

    gid = r.json()["data"]["gid"]
    print(f"Tarefa DOCUMENTAÇÃO criada: {gid}")
    return gid


# Cria uma subtarefa no Asana com a documentação gerada, vinculada à tarefa pai DOCUMENTAÇÃO.
# Parâmetros:
#   title (str): título da subtarefa.
#   text (str): texto da documentação formatado.
#   project_id (str): ID do projeto Asana onde a tarefa será criada.
# Lança exceção se a requisição falhar.
def create_asana_subtask(title, text, project_id: str):
    # Converte texto para HTML compatível com Asana
    html_notes = text_to_asana_html(text)
    body = f"<body>{html_notes}</body>"
    # Obtém ou cria a tarefa pai DOCUMENTAÇÃO
    parent_task_id = get_or_create_parent_task(project_id)

    url = f"https://app.asana.com/api/1.0/tasks/{parent_task_id}/subtasks"
    payload = {
        "data": {
            "name": title,
            "html_notes": body,
        }
    }
    headers = {
        "Authorization": f"Bearer {ASANA_TOKEN}",
        "Content-Type": "application/json",
    }
    r = requests.post(url, json=payload, headers=headers, timeout=20)

    if not r.ok:
        print("ERRO ASANA:", r.status_code, r.text)

    r.raise_for_status()


# Função principal que orquestra o processo:
# - Obtém arquivos alterados
# - Carrega mapeamento de projetos
# - Busca projetos Asana disponíveis
# - Gera documentação para cada arquivo alterado
# - Cria subtarefas no Asana com a documentação gerada
def main():
    files = get_changed_files()

    if not files:
        print("Nenhum arquivo relevante alterado.")
        return

    project_map = load_project_map()
    workspace_id = project_map.get("workspace_id")

    projects = []
    if workspace_id:
        projects = fetch_asana_projects(workspace_id)
    else:
        print("AVISO: workspace_id não definido, usando default para todos")

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    for file in files:
        diff = get_diff_for_file(file)
        text, content = generate_doc(file, diff)
        title = f"[DOC] {file} – {now}"
        project_id = get_project_id_for_file(file, content, project_map, projects)
        create_asana_subtask(title, text, project_id)
        print(f"Subtarefa criada no Asana para {file}")


if __name__ == "__main__":
    main()