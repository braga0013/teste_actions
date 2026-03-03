import os
import re
import json
import unicodedata
import subprocess
import requests
from datetime import datetime

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ASANA_TOKEN = os.getenv("ASANA_TOKEN")

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY não definida")
if not ASANA_TOKEN:
    raise RuntimeError("ASANA_TOKEN não definida")


def normalize(text: str) -> str:
    """Remove acentos, ç e converte para lowercase para comparação."""
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return text.lower().strip()


def load_project_map() -> dict:
    try:
        with open("asana_map.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        raise RuntimeError("asana_map.json não encontrado ou inválido")


def fetch_asana_projects(workspace_id: str) -> list:
    """Busca todos os projetos ativos do workspace."""
    headers = {"Authorization": f"Bearer {ASANA_TOKEN}"}
    url = (
        f"https://app.asana.com/api/1.0/workspaces/{workspace_id}/projects"
        f"?opt_fields=name,notes,archived,current_status_update.status_type"
    )
    r = requests.get(url, headers=headers, timeout=30)
    if not r.ok:
        print("ERRO ao buscar projetos:", r.status_code, r.text)
        r.raise_for_status()

    all_projects = r.json().get("data", [])

    active = []
    for p in all_projects:
        if p.get("archived", False):
            continue
        status = p.get("current_status_update") or {}
        if status.get("status_type") == "complete":
            continue
        active.append(p)

    print(f"Projetos ativos: {[p['name'] for p in active]}")
    return active


def update_asana_map(projects: list):
    """Atualiza asana_map.json com projetos atuais do workspace."""
    try:
        with open("asana_map.json", "r", encoding="utf-8") as f:
            current = json.load(f)
    except Exception:
        current = {}

    current["projects"] = [
        {"gid": p["gid"], "name": p["name"]}
        for p in projects
    ]

    with open("asana_map.json", "w", encoding="utf-8") as f:
        json.dump(current, f, ensure_ascii=False, indent=2)

    print("asana_map.json atualizado com projetos atuais")


def find_project_by_name(name: str, projects: list) -> dict | None:
    """Encontra projeto pelo nome ignorando acentos, ç e capitalização."""
    name_normalized = normalize(name)
    for p in projects:
        if normalize(p["name"]) == name_normalized:
            return p
    return None


def extract_asana_projects(content: str) -> list:
    """Extrai todas as linhas # ASANA_PROJECT: do topo do arquivo."""
    projects = []
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        match = re.match(r"#\s*ASANA_PROJECT\s*:\s*(.+)", line, re.IGNORECASE)
        if match:
            projects.append(match.group(1).strip())
        elif not line.startswith("#"):
            break
    return projects


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


def generate_doc(file, diff, content):
    if not content:
        return f"{file}\n\nArquivo não encontrado ou vazio."

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
- NUNCA escreva entidades HTML como &lt; &gt; &amp;

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
    return response.json()["choices"][0]["message"]["content"]


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
    payload = {"data": {"name": "DOCUMENTAÇÃO", "projects": [project_id]}}
    r = requests.post(
        "https://app.asana.com/api/1.0/tasks",
        json=payload, headers=headers, timeout=20
    )
    if not r.ok:
        print("ERRO ao criar tarefa pai:", r.status_code, r.text)
        r.raise_for_status()

    gid = r.json()["data"]["gid"]
    print(f"Tarefa DOCUMENTAÇÃO criada: {gid}")
    return gid


def create_asana_subtask(title: str, text: str, project_id: str):
    html_notes = text_to_asana_html(text)
    body = f"<body>{html_notes}</body>"
    parent_task_id = get_or_create_parent_task(project_id)

    url = f"https://app.asana.com/api/1.0/tasks/{parent_task_id}/subtasks"
    payload = {"data": {"name": title, "html_notes": body}}
    headers = {
        "Authorization": f"Bearer {ASANA_TOKEN}",
        "Content-Type": "application/json",
    }
    r = requests.post(url, json=payload, headers=headers, timeout=20)
    if not r.ok:
        print("ERRO ASANA:", r.status_code, r.text)
    r.raise_for_status()


def main():
    project_map = load_project_map()
    workspace_id = project_map.get("workspace_id")
    default_project_id = project_map.get("default_project_id")

    # Busca projetos ativos e atualiza asana_map.json
    projects = []
    if workspace_id:
        projects = fetch_asana_projects(workspace_id)
        update_asana_map(projects)
    else:
        print("AVISO: workspace_id não definido no asana_map.json")

    files = get_changed_files()
    # Ignora o próprio asana_map.json
    files = [f for f in files if f != "asana_map.json"]

    if not files:
        print("Nenhum arquivo relevante alterado.")
        return

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    for file in files:
        content = get_file_content(file)
        if not content:
            print(f"Arquivo vazio ou inacessível: {file}")
            continue

        diff = get_diff_for_file(file)
        project_names = extract_asana_projects(content)

        if not project_names:
            print(f"{file} → sem # ASANA_PROJECT, pulando documentação Asana")
            continue

        # Gera documentação uma vez só
        print(f"{file} → gerando documentação...")
        doc_text = generate_doc(file, diff, content)
        title = f"[DOC] {file} – {now}"

        for project_name in project_names:
            project = find_project_by_name(project_name, projects)
            if not project:
                print(f"{file} → projeto '{project_name}' não encontrado, pulando")
                continue
            print(f"{file} → enviando para [{project['name']}]")
            create_asana_subtask(title, doc_text, project["gid"])
            print(f"Subtarefa criada em [{project['name']}] para {file}")


if __name__ == "__main__":
    main()
    