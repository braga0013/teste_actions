import os
import re
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
Você é um engenheiro de software sênior.
Gere documentação técnica clara e objetiva em Markdown.

Explique:
- O que mudou
- Impacto técnico

Arquivo: {file}

Diff:
```diff
{diff}
```
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


def markdown_to_asana_html(markdown: str) -> str:
    lines = markdown.split("\n")
    html_lines = []
    in_code_block = False
    code_buffer = []

    for line in lines:
        # Bloco de código → vira parágrafo com texto puro (Asana não aceita <pre>/<code>)
        if line.strip().startswith("```"):
            if not in_code_block:
                in_code_block = True
                code_buffer = []
            else:
                in_code_block = False
                code_content = " | ".join(code_buffer)
                html_lines.append(f"<p>{code_content}</p>")
            continue

        if in_code_block:
            code_buffer.append(line)
            continue

        # Títulos → viram <strong> em parágrafo
        if line.startswith("### "):
            html_lines.append(f"<p><strong>{line[4:]}</strong></p>")
        elif line.startswith("## "):
            html_lines.append(f"<p><strong>{line[3:]}</strong></p>")
        elif line.startswith("# "):
            html_lines.append(f"<p><strong>{line[2:]}</strong></p>")

        # Listas
        elif line.startswith("- ") or line.startswith("* "):
            content = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line[2:])
            content = re.sub(r"`(.+?)`", r"\1", content)  # inline code: remove backticks
            html_lines.append(f"<ul><li>{content}</li></ul>")

        # Linha vazia
        elif line.strip() == "":
            continue

        # Parágrafo normal
        else:
            formatted = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line)
            formatted = re.sub(r"`(.+?)`", r"\1", formatted)  # inline code: remove backticks
            html_lines.append(f"<p>{formatted}</p>")

    return "\n".join(html_lines)


def create_asana_task(title, markdown):
    url = "https://app.asana.com/api/1.0/tasks"
    html_notes = markdown_to_asana_html(markdown)

    # DEBUG: mostra o HTML gerado antes de enviar
    print("=== HTML ENVIADO AO ASANA ===")
    print(html_notes[:2000])
    print("=== FIM HTML ===")

    payload = {
        "data": {
            "name": title,
            "html_notes": f"<body>{html_notes}</body>",
            "projects": [ASANA_PROJECT_ID],
        }
    }
    headers = {
        "Authorization": f"Bearer {ASANA_TOKEN}",
        "Content-Type": "application/json",
    }
    r = requests.post(url, json=payload, headers=headers, timeout=20)

    # DEBUG: mostra resposta completa do Asana
    print("=== RESPOSTA ASANA ===")
    print(r.status_code)
    print(r.text)
    print("=== FIM RESPOSTA ===")

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