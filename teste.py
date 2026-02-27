from fastapi import APIRouter
from services.query_service import execute_query, process_results
from core.logger import log_execution

router = APIRouter()

@router.get("/{idproduto}")
@log_execution
async def calculo_custo_preco(idproduto:str):
    """
    Esta chamada retorna informações de custo e preço de um produto específico.
    """
    sql = '''WITH MC_DADOS AS (
        SELECT
            MC.IDPRODUTO,
            MC.IDPLANILHA,
            MC.QTDPRODUTO,
            MC.TIPOSITTRIB,
            MC.VALUNITARIOBRUTO,	
            MC.DTMOVIMENTO,
            MC.VALFRETENOTA,
            MC.PERFRETECONHE,
            MC.PERIPI,
            PPP.VALPRECOVAREJO,
            PPP.PERMARGEMVAREJO,
            PG2.PERCOMAVISTA AS PERCOMISSAOAVISTA,
            PG2.PERCOMAPRAZO AS PERCOMISSAOAPRAZO,
            MC.FLAGPRODUTOIMPORTADO,
            MC.PERMARGEMSUBST AS PERMVA
        FROM DBA.MOVIMENTO_CUSTO MC
        LEFT JOIN DBA.PRODUTO_GRADE PG2 
            ON MC.IDPRODUTO = PG2.IDPRODUTO
        LEFT JOIN DBA.POLITICA_PRECO_PRODUTO PPP 
            ON MC.IDPRODUTO = PPP.IDPRODUTO
        LEFT JOIN DBA.PRODUTO_FORNECEDOR PF2 
            ON MC.IDPRODUTO = PF2.IDPRODUTO
            AND MC.IDSUBPRODUTO = PF2.IDSUBPRODUTO
        LEFT JOIN DBA.CENARIO_FISCAL_DADOS_VW CFDV 
            ON MC.IDPRODUTO = CFDV.IDPRODUTO
        WHERE MC.IDPRODUTO = :IDPRODUTO
        AND PF2.FLAGFORNECEDORPADRAO = 'T'
        AND MC.FLAGALTEROUCUSTO = 'T'
        FETCH FIRST 1 ROW ONLY
    ),
    UF_DADOS_RAW AS (
        SELECT
            NES.UF,
            EA.IDPRODUTO,
            EA.IDPLANILHA,
            MC.IDEMPRESA,
            MC.PERICMSENTRADA,
            MC.NUMSEQUENCIA,
            ROW_NUMBER() OVER (
                PARTITION BY MC.IDEMPRESA, EA.IDPRODUTO, EA.IDSUBPRODUTO ORDER BY
                MC.DTMOVIMENTO DESC
            ) AS RN
        FROM DBA.ESTOQUE_ANALITICO EA
        JOIN DBA.PRODUTO_FORNECEDOR PF 
        ON EA.IDPRODUTO = PF.IDPRODUTO
        AND EA.IDSUBPRODUTO = PF.IDSUBPRODUTO
        AND PF.FLAGFORNECEDORPADRAO = 'T'
        JOIN DBA.NOTAS N 
        ON N.IDPLANILHA = EA.IDPLANILHA
        AND N.IDCLIFOR = PF.IDCLIFOR
        AND EA.IDEMPRESA = N.IDEMPRESA
        JOIN DBA.NOTAS_ENTRADA_SAIDA NES
        ON EA.IDPLANILHA = NES.IDPLANILHA
        AND EA.IDEMPRESA = NES.IDEMPRESA
        AND NES.TIPOCONSUMO = 'C'
        JOIN DBA.MOVIMENTO_CUSTO MC
        ON MC.IDPRODUTO = EA.IDPRODUTO
        AND MC.IDEMPRESA IN ('26','31')
        AND MC.flagalteroucusto = 'T'
        WHERE EA.IDPRODUTO = :IDPRODUTO
    ),
    UF_DADOS AS (
        SELECT
            UF,
            IDPRODUTO,
            MAX(CASE WHEN IDEMPRESA = '26' THEN PERICMSENTRADA END) AS PERICMSCOMPRA_26,
            MAX(CASE WHEN IDEMPRESA = '31' THEN PERICMSENTRADA END) AS PERICMSCOMPRA_31
        FROM UF_DADOS_RAW
        WHERE RN = 1
        GROUP BY UF, IDPRODUTO
    )
    SELECT 
    M.*,
    U.UF,
    COALESCE(
        U.PERICMSCOMPRA_26,
        (
            SELECT CFDV.ICMS_ALIQUOTA
            FROM DBA.CENARIO_FISCAL_DADOS_VW CFDV
            WHERE CFDV.IDTIPOOPERACAO = '20'
              AND CFDV.UFDESTINO = 'RS'
              AND CFDV.IDPRODUTO = M.IDPRODUTO
            FETCH FIRST 1 ROW ONLY
        )
    ) AS PERICMSCOMPRA_26,
    COALESCE(
        U.PERICMSCOMPRA_31,
        (
            SELECT CFDV.ICMS_ALIQUOTA
            FROM DBA.CENARIO_FISCAL_DADOS_VW CFDV
            WHERE CFDV.IDTIPOOPERACAO = '20'
              AND CFDV.UFDESTINO = 'SC'
              AND CFDV.IDPRODUTO = M.IDPRODUTO
            FETCH FIRST 1 ROW ONLY
        )
    ) AS PERICMSCOMPRA_31
	FROM MC_DADOS M
	JOIN UF_DADOS U 
    	ON U.IDPRODUTO = M.IDPRODUTO;
    '''
    
    df = await execute_query("ciss_db2",query=sql, replace_dict={":IDPRODUTO":idproduto}, redis= False)

    return await process_results(df, return_with_data=True)