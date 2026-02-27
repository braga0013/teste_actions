import os
import subprocess
import requests
from datetime import datetime

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY não definida no ambiente")


def get_git_diff() -> str:
    """
    Retorna o diff entre o último commit e o anterior.
    Funciona tanto em merge quanto em push direto.
    """
    try:
        diff = subprocess.check_output(
            ["git", "diff", "HEAD~1", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True
        )
    except Exception:
        try:
            diff = subprocess.check_output(
                ["git", "show", "--pretty=", "HEAD"],
                stderr=subprocess.DEVNULL,
                text=True
            )
        except Exception:
            diff = ""

    return diff.strip()


def generate_markdown(diff: str) -> str:
    if not diff:
        return (
            "# Atualização de Código\n\n"
            "Nenhuma alteração relevante detectada.\n"
        )

    prompt = f"""
Você é um engenheiro de software sênior.
Gere uma documentação técnica clara e objetiva em **Markdown**.

Regras:
- Explique o que mudou
- Explique impacto técnico
- Use títulos e listas
- NÃO repita o diff literalmente
- NÃO invente informações

Diff analisado:
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
                {
                    "role": "system",
                    "content": "Você gera documentação técnica profissional em Markdown."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "temperature": 0.2
        },
        timeout=30,
    )

    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def save_doc(markdown: str) -> str:
    os.makedirs("docs", exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y-%m-%d_%H-%M-%S")
    path = f"docs/update-ai-{timestamp}.md"

    with open(path, "w", encoding="utf-8") as f:
        f.write(markdown)

    return path


def main():
    diff = get_git_diff()
    markdown = generate_markdown(diff)
    path = save_doc(markdown)
    print(f"Documentação gerada com sucesso: {path}")


if __name__ == "__main__":
    main()