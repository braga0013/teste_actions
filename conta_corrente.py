from fastapi import APIRouter
from services.query_service import execute_query, process_results
from core.logger import log_execution

router = APIRouter()

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

    df = await execute_query(
        "dw_postgres", query=sql,replace_dict= {":IDINDICADOR":idindicador,
                                                ":DTINICIO":dtinicio,
                                                ":DTFIM":dtfim},
        cache_ttl=10800 #DEFINE 3H DE ARMAZENAMENTO
    )

    return await process_results(df, return_with_data=True)

    
