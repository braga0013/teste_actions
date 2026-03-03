import os
import subprocess
import requests

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY não definida")

EXTENSIONS_ALLOWED = [".py", ".js", ".ts", ".java", ".go"]


def get_changed_files():
    try:
        files = subprocess.check_output(
            ["git", "diff", "HEAD~1", "HEAD", "--name-only"], text=True)
    except Exception:
        files = subprocess.check_output(
            ["git", "show", "--pretty=", "--name-only", "HEAD"], text=True)
    return [
        f for f in files.splitlines()
        if f and not f.startswith(".github/")
        and any(f.endswith(ext) for ext in EXTENSIONS_ALLOWED)
    ]


def get_file_content(file):
    try:
        with open(file, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""


def get_diff_for_file(file):
    try:
        return subprocess.check_output(
            ["git", "diff", "HEAD~1", "HEAD", "--", file], text=True)
    except Exception:
        return ""


def generate_commented_code(file, content, diff):
    prompt = f"""
Você é um engenheiro de software sênior.
Sua tarefa é adicionar comentários técnicos diretamente no código fornecido.

REGRAS:
- Use o estilo de comentário correto para a linguagem do arquivo
- Python: use # para comentários de linha
- JavaScript/TypeScript/Java/Go: use // para linha e /* */ para blocos
- Adicione um bloco de comentário no TOPO do arquivo com resumo geral
- Adicione comentários ACIMA de cada função/método explicando:
  - O que faz
  - Parâmetros esperados
  - O que retorna
  - Regras de negócio relevantes
- Adicione comentários inline em trechos complexos ou não óbvios
- NÃO remova nenhuma linha de código existente
- NÃO modifique a lógica do código
- Retorne O ARQUIVO COMPLETO com os comentários inseridos

BLOCO DO TOPO (OBRIGATÓRIO):
# =============================================================================
# ARQUIVO: nome do arquivo
# OBJETIVO: o que este arquivo faz em 2-3 linhas
# O QUE MUDOU: resumo do commit
# DEPENDENCIAS: dependências externas relevantes
# =============================================================================

Arquivo: {file}

CÓDIGO COMPLETO:
{content}

DIFF DO COMMIT (o que mudou):
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
                {"role": "system", "content": "Você adiciona comentários técnicos em código fonte. Retorne SEMPRE o arquivo completo com os comentários inseridos, sem Markdown, sem blocos de código cercados por ```."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        },
        timeout=60,
    )
    response.raise_for_status()
    result = response.json()["choices"][0]["message"]["content"]

    # Remove cercas de markdown caso o GPT insista em colocar
    result = result.strip()
    if result.startswith("```"):
        result = "\n".join(result.split("\n")[1:])
    if result.endswith("```"):
        result = "\n".join(result.split("\n")[:-1])

    return result


def save_file(file, content):
    with open(file, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Comentários inseridos em {file}")


def main():
    files = get_changed_files()

    if not files:
        print("Nenhum arquivo relevante alterado.")
        return

    for file in files:
        content = get_file_content(file)
        if not content:
            print(f"Arquivo vazio ou inacessível: {file}")
            continue

        diff = get_diff_for_file(file)
        print(f"Processando {file}...")
        commented = generate_commented_code(file, content, diff)
        save_file(file, commented)


if __name__ == "__main__":
    main()