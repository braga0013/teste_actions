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


def load_project_map() -> dict:
    try:
        with open("asana_map.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        raise RuntimeError("asana_map.json não encontrado ou inválido")


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


def gpt_choose_project(file: str, content: str, projects: list) -> str:
    project_list = "\n".join([
        f"- ID: {p['gid']} | Nome: {p['name']}"
        for p in projects
    ])

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

    valid_ids = [p["gid"] for p in projects]
    if chosen_id in valid_ids:
        chosen_name = next(p["name"] for p in projects if p["gid"] == chosen_id)
        print(f"{file} → GPT escolheu [{chosen_name}] projeto {chosen_id}")
        return chosen_id

    print(f"GPT retornou ID inválido ({chosen_id}), usando default")
    return None


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


def get_changed_files():
    try:
        files = subprocess.check_output(
            ["git", "diff", "HEAD~1", "HEAD", "--name-only"], text=True)
    except Exception:
        files = subprocess.check_output(
            ["git", "show", "--pretty=", "--name-only", "HEAD"], text=True)
    return [f for f in files.splitlines() if f and not f.startswith(".github/")]


def get_diff_for_file(file):
    try:
        return subprocess.check_output(
            ["git", "diff", "HEAD~1", "HEAD", "--", file], text=True)
    except Exception:
        return ""


def get_file_content(file):
    try:
        with open(file, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""


def generate_doc(file, diff):
    content = get_file_content(file)
    if not content:
        return f"{file}\n\nArquivo não encontrado ou vazio.", ""

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


def xml_escape(text: str) -> str:
    text = text.replace("&amp;", "&")
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    text = text.replace("&quot;", '"')
    text = text.replace("&#39;", "'")
    text = text.replace("&#96;", "`")
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    text = text.replace('"', "&quot;")
    return text


def text_to_asana_html(text: str) -> str:
    lines = text.split("\n")
    html_parts = []
    i = 0

    while i < len(lines):
        line = lines[i]

        if line.strip() == "[CODE]":
            code_lines = []
            i += 1
            while i < len(lines) and lines[i].strip() != "[/CODE]":
                code_lines.append(xml_escape(lines[i]))
                i += 1
            html_parts.append("<pre>" + "\n".join(code_lines) + "</pre>")
            i += 1
            continue

        if line.strip().startswith("===================="):
            if i + 1 < len(lines):
                title = lines[i + 1].strip()
                if i + 2 < len(lines) and lines[i + 2].strip().startswith("===================="):
                    html_parts.append(f"<h2>{xml_escape(title)}</h2>")
                    i += 3
                    continue

        if line.startswith("> "):
            content = xml_escape(line[2:].strip())
            html_parts.append(f"<ul><li>{content}</li></ul>")
            i += 1
            continue

        if line.strip() == "":
            i += 1
            continue

        html_parts.append(f"<ul><li>{xml_escape(line)}</li></ul>")
        i += 1

    return "\n".join(html_parts)


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

    for task in tasks:
        if task.get("name", "").upper() == "DOCUMENTAÇÃO":
            print(f"Tarefa DOCUMENTAÇÃO encontrada: {task['gid']}")
            return task["gid"]

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


def create_asana_subtask(title, text, project_id: str):
    html_notes = text_to_asana_html(text)
    body = f"<body>{html_notes}</body>"
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