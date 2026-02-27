import os
import subprocess
import requests
from datetime import datetime

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ASANA_TOKEN = os.getenv("ASANA_TOKEN")
ASANA_PROJECT_ID = os.getenv("ASANA_PROJECT_ID")

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY não definida")

if not ASANA_TOKEN or not ASANA_PROJECT_ID:
    raise RuntimeError("ASANA_TOKEN ou ASANA_PROJECT_ID não definidos")


def get_changed_files():
    try:
        files = subprocess.check_output(
            ["git", "diff", "HEAD~1", "HEAD", "--name-only"],
            text=True
        )
    except Exception:
        files = subprocess.check_output(
            ["git", "show", "--pretty=", "--name-only", "HEAD"],
            text=True
        )
    return [f for f in files.splitlines() if f and not f.startswith(".github/")]


def get_diff_for_file(file):
    try:
        return subprocess.check_output(
            ["git", "diff", "HEAD~1", "HEAD", "--", file],
            text=True
        )
    except Exception:
        return ""


def generate_markdown(file, diff):
    if not diff:
        return f"# {file}\n\nNenhuma alteração relevante."

    prompt = f"""
Você vai gerar documentação para ser colada DIRETAMENTE no Asana.

REGRAS DE FORMATAÇÃO (OBRIGATÓRIO):
- NÃO use Markdown avançado
- NÃO use ```diff
- Use apenas:
  - Títulos simples (texto em negrito)
  - Listas com hífen (-)
  - Código em bloco simples (``` sem linguagem)
  - Código inline com `

ESTRUTURA EXATA:
- Título em negrito
- Seção "O que mudou"
- Seção "Impacto técnico"
- Seção "Observações" (se houver)

Arquivo analisado: {file}

Código alterado:
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
                {"role": "system", "content": "Você gera documentação técnica profissional."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def create_asana_task(title, markdown):
    url = "https://app.asana.com/api/1.0/tasks"
    payload = {
        "data": {
            "name": title,
            "notes": markdown,
            "projects": [ASANA_PROJECT_ID],
        }
    }
    headers = {
        "Authorization": f"Bearer {ASANA_TOKEN}",
        "Content-Type": "application/json",
    }
    r = requests.post(url, json=payload, headers=headers, timeout=20)
    r.raise_for_status()


def main():
    files = get_changed_files()

    if not files:
        print("Nenhum arquivo relevante alterado.")
        return

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    for file in files:
        diff = get_diff_for_file(file)
        markdown = generate_markdown(file, diff)
        title = f"[DOC] {file} – {now}"
        create_asana_task(title, markdown)
        print(f"Tarefa criada no Asana para {file}")


if __name__ == "__main__":
    main()

