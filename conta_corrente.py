# =============================================================================
# ARQUIVO: conta_corrente.py
# OBJETIVO: Define uma rota API para buscar dados de vendas e comissões de arquitetos
#           dentro de um período especificado, agrupando por clube para cálculo de pontos.
# O QUE MUDOU: Adicionada anotação do projeto ASANA no topo do arquivo.
# DEPENDENCIAS: fastapi, services.query_service (execute_query, process_results), core.logger (log_execution)
# =============================================================================

# ASANA_PROJECT: Template T.I

from fastapi import APIRouter
from services.query_service import execute_query, process_results
from core.logger import log_execution

router = APIRouter()

# Função assíncrona que busca valores de indicador de vendas e comissões
# Parâmetros:
#   idindicador (str): Identificador do indicador a ser consultado
#   dtinicio (str): Data inicial do período de consulta (formato esperado: string)
#   dtfim (str): Data final do período de consulta (formato esperado: string)
# Retorna:
#   Resultado processado da consulta SQL contendo dados de vendas e comissões
# Regras de negócio:
#   - Busca dados de vendas e comissões efetivadas dentro do período informado
#   - Inclui dados do mês anterior com o mesmo período para comparação
#   - Agrupa os dados por clube para cálculo correto de pontos
@router.get("/{idindicador}&{dtinicio}&{dtfim}")
@log_execution
async def busca_valores_indicador(idindicador: str, dtinicio:str, dtfim:str):
    """
    Esta chamada irá trazer os dados de venda e cotagem de pedidos efetivados dentro do periodo pesquisado e do mês anterior com mesmo periodo.
    Os dados estão agrupados para que seja diferenciado o clube onde a venda foi vinculada para calculo correto de pontos.
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

    # Executa a consulta SQL no banco "dw_postgres" substituindo os parâmetros na query
    # cache_ttl define o tempo de cache da consulta para 3 horas (10800 segundos)
    df = await execute_query(
        "dw_postgres", query=sql,replace_dict= {":IDINDICADOR":idindicador,
                                                ":DTINICIO":dtinicio,
                                                ":DTFIM":dtfim},
        cache_ttl=10800 #DEFINE 3H DE ARMAZENAMENTO
    )

    # Processa os resultados da consulta e retorna os dados formatados
    return await process_results(df, return_with_data=True)