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


def get_file_content(file):
    try:
        with open(file, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""


def generate_markdown(file, diff):
    content = get_file_content(file)

    if not content:
        return f"{file}\n\nArquivo não encontrado ou vazio."

    prompt = f"""
Você é um engenheiro de software sênior fazendo code review completo.
Gere documentação técnica usando as marcações abaixo. NÃO use Markdown.

REGRAS DE ESCRITA:
- Seja direto, sem introduções genéricas ou floreios
- Explique o propósito geral antes dos detalhes
- Considere que o leitor é técnico (dev/infra/QA)
- Não repita o código inteiro
- Use exemplos curtos quando necessário
- Analise o CODIGO COMPLETO, não apenas o diff
- O diff serve apenas para indicar o que foi alterado neste commit

REGRAS DE FORMATAÇÃO (OBRIGATÓRIO):
- NÃO use #, ##, **, *, `, --- ou qualquer símbolo Markdown
- Títulos de seção em MAIÚSCULAS entre linhas de ====================
- Use ">" no início de cada item de lista
- Para exemplos de código ou trechos relevantes use [CODE] e [/CODE]
- Separe seções com uma linha em branco

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
Explicação direta do propósito geral do arquivo/funcionalidade.

====================
ENTRADA ESPERADA
====================
> Parâmetros, tipos, estrutura e exemplos reais do que o código recebe

====================
SAIDA GERADA
====================
> O que o código produz ou retorna, com exemplos se possível

====================
FLUXO DE EXECUCAO
====================
> Passo 1
> Passo 2
> Passo 3

====================
FUNCOES E METODOS PRINCIPAIS
====================
> nome_da_funcao: responsabilidade detalhada

====================
QUERIES E LOGICA DE DADOS
====================
> Descreva as queries SQL ou lógica de dados presente no código
> CTEs utilizadas e seus papéis
> Joins e filtros relevantes
> Pontos de atenção nas queries
Se houver query relevante, use [CODE]...[/CODE] para mostrar trecho

====================
REGRAS DE NEGOCIO
====================
> Regras implícitas identificadas no código completo

====================
DECISOES ARQUITETURAIS
====================
> Decisões relevantes observadas no código

====================
PONTOS CRITICOS E DEPENDENCIAS
====================
> Dependências externas, riscos ou pontos de atenção

====================
SUGESTOES DE MELHORIA
====================
> Melhorias técnicas identificadas na análise do código completo

Arquivo: {file}

CODIGO COMPLETO DO ARQUIVO:
{content}

DIFF DO COMMIT (o que mudou agora):
{diff if diff else "Nenhuma alteração no diff (arquivo novo ou sem diff disponível)"}
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
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def text_to_asana_html(text: str) -> str:
    lines = text.split("\n")
    html_parts = []
    i = 0

    while i < len(lines):
        line = lines[i]

        # Bloco [CODE]...[/CODE] → <pre> (elemento direto do body, sem nesting)
        if line.strip() == "[CODE]":
            code_lines = []
            i += 1
            while i < len(lines) and lines[i].strip() != "[/CODE]":
                code_lines.append(xml_escape(lines[i]))
                i += 1
            html_parts.append("<pre>" + "\n".join(code_lines) + "</pre>")
            i += 1
            continue

        # Título com separador ====================
        if line.strip().startswith("===================="):
            if i + 1 < len(lines):
                title = lines[i + 1].strip()
                if i + 2 < len(lines) and lines[i + 2].strip().startswith("===================="):
                    html_parts.append(f"<h2>{xml_escape(title)}</h2>")
                    i += 3
                    continue

        # Item de lista > item
        if line.startswith("> "):
            content = xml_escape(line[2:].strip())
            html_parts.append(f"<ul><li>{content}</li></ul>")
            i += 1
            continue

        # Linha vazia
        if line.strip() == "":
            i += 1
            continue

        # Parágrafo normal
        html_parts.append(f"<p>{xml_escape(line)}</p>")
        i += 1

    return "\n".join(html_parts)


def create_asana_task(title, text):
    url = "https://app.asana.com/api/1.0/tasks"
    html_notes = text_to_asana_html(text)

    # DEBUG COMPLETO
    print("=== HTML COMPLETO ENVIADO ===")
    print(f"<body>{html_notes}</body>")
    print("=== FIM ===")

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

    if not r.ok:
        print("ERRO ASANA:", r.status_code, r.text)

    r.raise_for_status()


def main():
    files = get_changed_files()

    if not files:
        print("Nenhum arquivo relevante alterado.")
        return

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    for file in files:
        diff = get_diff_for_file(file)
        text = generate_markdown(file, diff)
        title = f"[DOC] {file} – {now}"
        create_asana_task(title, text)
        print(f"Tarefa criada no Asana para {file}")


if __name__ == "__main__":
    main()