# =============================================================================
# ARQUIVO: scripts/generate_comments.py
# OBJETIVO: Automatizar a inserção de comentários técnicos em arquivos de código
#           modificados, utilizando a API do OpenAI para gerar os comentários.
# O QUE MUDOU: Adicionada limitação no tamanho do conteúdo enviado para a API para evitar timeout;
#              aumento do timeout da requisição para 120 segundos.
# DEPENDENCIAS: subprocess, requests, OpenAI API (via requests)
# =============================================================================

import os
import subprocess
import requests

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Verifica se a variável de ambiente da chave da API está definida, caso contrário lança erro
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY não definida")

# Extensões de arquivos que serão processados para inserção de comentários
EXTENSIONS_ALLOWED = [".py", ".js", ".ts", ".java", ".go"]


def get_changed_files():
    """
    Obtém a lista de arquivos modificados no último commit Git.

    Retorna:
        list: Lista de caminhos de arquivos que foram modificados e que possuem extensões permitidas.
    
    Regras de negócio:
        - Ignora arquivos dentro do diretório .github/
        - Considera apenas arquivos com extensões definidas em EXTENSIONS_ALLOWED
        - Tenta obter arquivos modificados entre HEAD~1 e HEAD; se falhar, obtém arquivos do último commit
    """
    try:
        # Tenta obter arquivos modificados entre os dois últimos commits
        files = subprocess.check_output(
            ["git", "diff", "HEAD~1", "HEAD", "--name-only"], text=True)
    except Exception:
        # Caso falhe, obtém arquivos do último commit
        files = subprocess.check_output(
            ["git", "show", "--pretty=", "--name-only", "HEAD"], text=True)
    return [
        f for f in files.splitlines()
        if f and not f.startswith(".github/")
        and any(f.endswith(ext) for ext in EXTENSIONS_ALLOWED)
    ]


def get_file_content(file):
    """
    Lê o conteúdo de um arquivo de código.

    Parâmetros:
        file (str): Caminho do arquivo a ser lido.

    Retorna:
        str: Conteúdo do arquivo como string. Retorna string vazia em caso de erro.
    """
    try:
        # Abre o arquivo com encoding utf-8 e ignora erros de leitura
        with open(file, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        # Retorna string vazia se não conseguir ler o arquivo
        return ""


def get_diff_for_file(file):
    """
    Obtém o diff Git do arquivo entre os dois últimos commits.

    Parâmetros:
        file (str): Caminho do arquivo para obter o diff.

    Retorna:
        str: Texto do diff do arquivo. Retorna string vazia em caso de erro.
    """
    try:
        # Executa comando git diff para o arquivo específico
        return subprocess.check_output(
            ["git", "diff", "HEAD~1", "HEAD", "--", file], text=True)
    except Exception:
        # Retorna string vazia se não conseguir obter o diff
        return ""


def generate_commented_code(file, content, diff):
    """
    Gera o código com comentários técnicos adicionados utilizando a API do OpenAI.

    Parâmetros:
        file (str): Nome do arquivo que será comentado.
        content (str): Conteúdo original do arquivo.
        diff (str): Diferenças recentes no arquivo para contexto.

    Retorna:
        str: Código completo com comentários técnicos inseridos.

    Regras de negócio:
        - Limita o conteúdo enviado para a API a 8000 caracteres para evitar timeout.
        - Utiliza prompt específico para instruir o modelo a adicionar comentários técnicos
          seguindo regras de estilo para cada linguagem.
        - Remove eventuais cercas de markdown retornadas pela API.
        - Timeout da requisição aumentado para 120 segundos para arquivos maiores.
    """
    # Limita conteúdo para evitar timeout em arquivos grandes
    content_truncated = content[:8000] if len(content) > 8000 else content

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
{content_truncated}

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
        timeout=120,  # aumentado para 120s para suportar arquivos maiores
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
    """
    Salva o conteúdo fornecido no arquivo especificado.

    Parâmetros:
        file (str): Caminho do arquivo onde o conteúdo será salvo.
        content (str): Conteúdo a ser escrito no arquivo.

    Retorna:
        None

    Regras de negócio:
        - Sobrescreve o arquivo existente com o novo conteúdo.
        - Imprime mensagem de confirmação no console.
    """
    with open(file, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Comentários inseridos em {file}")


def main():
    """
    Função principal que orquestra o processo de:
    - Identificar arquivos modificados relevantes
    - Ler seus conteúdos e diffs
    - Gerar código comentado via API
    - Salvar os arquivos atualizados

    Retorna:
        None

    Regras de negócio:
        - Se nenhum arquivo relevante for alterado, exibe mensagem e encerra.
        - Ignora arquivos vazios ou inacessíveis.
    """
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