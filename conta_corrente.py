# =============================================================================
# ARQUIVO: conta_corrente.py
# OBJETIVO: Define uma rota API para buscar dados de vendas e comissões de arquitetos
#           dentro de um período específico, incluindo informações do clube de venda.
# O QUE MUDOU: Implementação inicial da rota para consulta de valores de indicadores.
# DEPENDENCIAS: FastAPI para criação da rota, serviços customizados para execução e processamento de queries,
#               e um logger para monitoramento da execução.
# =============================================================================

from fastapi import APIRouter
from services.query_service import execute_query, process_results
from core.logger import log_execution

router = APIRouter()

# Decorador para definir rota GET com parâmetros na URL para idindicador, dtinicio e dtfim
@router.get("/{idindicador}&{dtinicio}&{dtfim}")
@log_execution  # Decorador para logar a execução da função para monitoramento e auditoria
async def busca_valores_indicador(idindicador: str, dtinicio:str, dtfim:str):
    """
    Busca dados de vendas e comissões de pedidos efetivados dentro do período informado,
    incluindo também os dados do mês anterior no mesmo período para comparação.
    Os dados são agrupados para diferenciar o clube onde a venda foi vinculada,
    garantindo o cálculo correto dos pontos.

    Parâmetros:
    - idindicador (str): Identificador do indicador para filtro dos dados.
    - dtinicio (str): Data inicial do período de consulta no formato esperado pelo banco.
    - dtfim (str): Data final do período de consulta no formato esperado pelo banco.

    Retorna:
    - Resultado processado da consulta contendo os dados de vendas, comissões e tipo de movimento.
    
    Regras de negócio:
    - Considera vendas e devoluções, diferenciando pelo valor da comissão.
    - Utiliza join para agregar informações do cliente/fornecedor da venda.
    - Cache da consulta é definido para 3 horas para otimizar performance.
    """
    sql = '''
        select distinct
            c."IDINDICADOR",
            c."IDORCAMENTO",
            v."IDCLIFOR" ,
            v."NOME" ,
            c."DTMOVIMENTO",
            c."VALOR_COMISSAO" + c."VALOREXTRA" as "VALOR_COMISSAO",
            (case when c."VALOR_COMISSAO"<0 then 'DEVOLUCAO'
                else 'VENDA' end) as TIPOMOVIMENTO
        from app_clube.conta_corrente_arquitetos c left join public.venda_consolidada v 
            using ("IDORCAMENTO")
        where c."DTMOVIMENTO" between ':DTINICIO' and ':DTFIM'
            and c."IDINDICADOR" = ':IDINDICADOR'
	 '''

    # Executa a query no banco de dados 'dw_postgres' substituindo os parâmetros na query
    # O cache_ttl define o tempo de cache da consulta em segundos (3 horas)
    df = await execute_query(
        "dw_postgres", query=sql,replace_dict= {":IDINDICADOR":idindicador,
                                                ":DTINICIO":dtinicio,
                                                ":DTFIM":dtfim},
        cache_ttl=10800 #DEFINE 3H DE ARMAZENAMENTO
    )

    # Processa os resultados da consulta para formato adequado de retorno da API
    return await process_results(df, return_with_data=True)
