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
Gere documentação técnica em texto puro (sem Markdown, sem símbolos de formatação).

REGRAS DE ESCRITA:
- Seja direto, sem introduções genéricas ou floreios
- Explique o propósito geral antes dos detalhes
- Considere que o leitor é técnico (dev/infra/QA)
- Não repita o código inteiro
- Use exemplos curtos quando necessário

REGRAS DE FORMATAÇÃO (OBRIGATÓRIO):
- NÃO use #, ##, **, *, `, --- ou qualquer símbolo Markdown
- Títulos de seção em MAIÚSCULAS entre linhas de ====================
- Use ">" no início de cada item de lista
- Separe seções com uma linha em branco

ESTRUTURA EXATA:

====================
ARQUIVO
====================
nome do arquivo

====================
O QUE MUDOU
====================
> item 1
> item 2

====================
OBJETIVO
====================
Explicação direta do propósito geral da alteração.

====================
ENTRADA ESPERADA
====================
> Estrutura, tipos e exemplos do que o código recebe

====================
SAÍDA GERADA
====================
> O que o código produz ou retorna

====================
FLUXO DE EXECUÇÃO
====================
> Passo 1
> Passo 2
> Passo 3

====================
FUNÇÕES PRINCIPAIS
====================
> nome_da_funcao: responsabilidade

====================
REGRAS DE NEGÓCIO
====================
> Regras implícitas identificadas no diff

====================
DECISÕES ARQUITETURAIS
====================
> Decisões relevantes observadas

====================
PONTOS CRÍTICOS E DEPENDÊNCIAS
====================
> Dependências externas, riscos ou pontos de atenção

Arquivo analisado: {file}

Diff:
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


def markdown_to_plain(markdown: str) -> str:
    lines = markdown.split("\n")
    plain_lines = []
    in_code_block = False

    for line in lines:
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            continue

        if in_code_block:
            plain_lines.append(f"  {line}")
            continue

        line = re.sub(r"^#{1,6}\s+", "", line)
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
        line = re.sub(r"\*(.+?)\*", r"\1", line)
        line = re.sub(r"`(.+?)`", r"\1", line)

        plain_lines.append(line)

    return "\n".join(plain_lines)


def create_asana_task(title, markdown):
    url = "https://app.asana.com/api/1.0/tasks"
    notes = markdown_to_plain(markdown)

    payload = {
        "data": {
            "name": title,
            "notes": notes,
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