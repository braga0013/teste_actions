from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from services.query_service import execute_query, process_results
from core.logger import log_execution
from collections import defaultdict
from datetime import datetime
import asyncio
import io
from xhtml2pdf import pisa
import httpx
import base64
import json
from PIL import Image


def format_value_br(valor: float) -> str:
    """Formata valor float para padrão brasileiro (ex: 2.500,15)"""
    try:
        val = float(valor)
        return f"{val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (ValueError, TypeError):
        return "0,00"

router = APIRouter()

# ── Cliente HTTP compartilhado ────────────────────────────────────────────────
_http_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=10.0,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _http_client


# ── Cache em memória para logos (evita download a cada request) ───────────────
# Chave: URL original  →  Valor: string base64 já processada
_logo_cache: dict[str, str] = {}


@router.get("/busca_orcamento_por_ambiente")
@log_execution
async def busca_orcamento_por_ambiente(idorcamento: str, com_totais: bool = True):
    """
    Esta chamada retorna os produtos do orçamento agrupados por ambiente.

    Parâmetros:
    - idorcamento: ID do orçamento
    - com_totais: Se True, retorna totais calculados por ambiente (padrão: True)
    """
    sql = '''WITH PEDIDO AS (
        SELECT IDORCAMENTO,
               (CASE WHEN FLAGPRENOTA = 'F' THEN 'O'
                    WHEN FLAGPRENOTA = 'T' THEN 'P' END) AS TIPOOPERACAO
        FROM DBA.ORCAMENTO
        WHERE IDORCAMENTO IN (:IDORCAMENTO)
    )
    SELECT
        O.IDORCAMENTO,
        CAST(OP.IDPRODUTO AS VARCHAR(20)) AS IDPRODUTO,
        CAST(OP.IDSUBPRODUTO AS VARCHAR(20)) AS IDSUBPRODUTO,
        DESCRAMBIENTE,
        ROW_NUMBER() OVER (PARTITION BY DESCRAMBIENTE ORDER BY OP.NUMSEQUENCIA ASC) AS ProductSequence,
        OP.TIPOENTREGA,
        COALESCE(CAST(OP.NUMSEQUENCIA AS VARCHAR(20)),0) AS NUMSEQUENCIA,
        CAST(OP.IDLOCALRETIRADA AS VARCHAR(20)) AS IDLOCALRETIRADA,
        LR.DESCRLOCALRETIRADA,
        CAST(IDVENDEDOR AS VARCHAR(20)) AS IDVENDEDOR,
        CAST(IDLOTE AS VARCHAR(20)) AS IDLOTE,
        PV.DESCRCOMPRODUTO,
        CAST(PV.VALMULTIVENDAS AS VARCHAR(20)) AS VALMULTIVENDAS,
        PV.EMBALAGEMSAIDA,
        PV.MODELO,
        CAST(LR.IDLOCALESTOQUE AS VARCHAR(20)) AS IDLOCALESTOQUE,
        CAST(VALDESCONTOPRO AS VARCHAR(20)) AS VALDESCONTOPRO,
        CAST(PERDESCONTOPRO AS VARCHAR(20)) AS PERDESCONTOPRO,
        CAST(VALLUCRO AS VARCHAR(20)) AS VALLUCRO,
        CAST(PERMARGEMLUCRO AS VARCHAR(20)) AS PERMARGEMLUCRO,
        CAST((VALUNITBRUTO * MC.PERCMARGEMCONTRIBUICAO / 100) AS VARCHAR(10)) AS MARGEMUNITBRUTO,
        CAST(((VALTOTLIQUIDO-VALFRETE)* MC.PERCMARGEMCONTRIBUICAO / 100) AS VARCHAR(20)) AS MARGEMTOTAL,
        CAST(QTDPRODUTO AS VARCHAR(20)) AS QTDPRODUTO,
        CAST(VALUNITBRUTO AS VARCHAR(20)) AS VALUNITBRUTO,
        CAST((VALTOTLIQUIDO-VALFRETE) AS VARCHAR(20)) AS VALTOTALSEMFRETE,
        CAST((VALTOTLIQUIDO-VALFRETE)/QTDPRODUTO AS VARCHAR(20)) AS VALUNITLIQUIDO,
        CAST(VALTOTLIQUIDO AS VARCHAR(20)) AS VALTOTLIQUIDO,
        CAST(VALFRETE AS VARCHAR (20)) AS VALFRETE,
        CAST(PV.PESOBRUTO*QTDPRODUTO AS VARCHAR(20)) AS PESOTOTALITEM,
        CAST(MC.PERCMARGEMCONTRIBUICAO AS VARCHAR(20)) AS PERCMARGEMCONTRIBUICAO,
        CAST(VALDESCONTOFINANCEIRO AS VARCHAR(20)) AS VALDESCONTOFINANCEIRO,
        CAST(ROUND((VALDESCONTOFINANCEIRO / (VALUNITBRUTO * QTDPRODUTO)*100), 2)AS VARCHAR(20))AS PERCDESCFINANCEIRO,
        PV.FABRICANTE AS FABRICANTE,
        PV.REFERENCIA AS REFERENCIA,
        OP.DTALTERACAO AS DTADEALTERACAO,
        OP.FLAGPRECOPROMOCAO,
        OP.IDLOCALRETIRADAENTREGA,
        OP.DTPREVENTREGA,
        OP.OBSERVACAO
    FROM PEDIDO AS P
        LEFT JOIN DBA.ORCAMENTO AS O ON (P.IDORCAMENTO = O.IDORCAMENTO)
        LEFT JOIN DBA.ORCAMENTO_PROD AS OP ON (OP.IDORCAMENTO = O.IDORCAMENTO)
        LEFT JOIN DBA.LOCAL_RETIRADA AS LR ON (LR.IDLOCALRETIRADA = OP.IDLOCALRETIRADA)
        LEFT JOIN DBA.PRODUTOS_VIEW AS PV ON (OP.IDPRODUTO = PV.IDPRODUTO AND OP.IDSUBPRODUTO = PV.IDSUBPRODUTO)
        LEFT JOIN DBA.MARGEM_CONTRIBUICAO AS MC ON (MC.IDEMPRESA = OP.IDEMPRESA
            AND MC.IDDOCUMENTO = OP.IDORCAMENTO
            AND MC.NUMSEQUENCIA = OP.NUMSEQUENCIA
            AND MC.TIPODOCUMENTO = P.TIPOOPERACAO)
    WHERE O.IDORCAMENTO IN (:IDORCAMENTO)
    ORDER BY DESCRAMBIENTE ASC, ProductSequence ASC'''

    df = await execute_query(
        "ciss_db2",
        query=sql,
        replace_dict={":IDORCAMENTO": idorcamento}
    )

    result = await process_results(df, return_with_data=True)

    if not result.get("data") or len(result.get("data", [])) == 0:
        return {
            "status": "error",
            "message": "Nenhum item encontrado para este orçamento",
            "data": [],
            "total_ambientes": 0,
            "total_produtos": 0
        }

    # Agrupar por ambiente
    ambientes_dict = defaultdict(lambda: {
        "itens": [],
        "total_quantidade": 0.0,
        "total_bruto": 0.0,
        "total_liquido": 0.0,
        "total_frete": 0.0,
        "total_desconto": 0.0,
        "total_margem": 0.0,
        "total_peso": 0.0
    })

    for item in result["data"]:
        ambiente = item.get('DESCRAMBIENTE', 'SEM_AMBIENTE')
        ambientes_dict[ambiente]["itens"].append(item)

        if com_totais:
            qtd = float(item.get('QTDPRODUTO') or 0)
            val_bruto = float(item.get('VALTOTALSEMFRETE') or 0)
            val_liquido = float(item.get('VALTOTLIQUIDO') or 0)
            frete = float(item.get('VALFRETE') or 0)
            desconto = float(item.get('VALDESCONTOPRO') or 0)
            margem = float(item.get('MARGEMTOTAL') or 0)
            peso = float(item.get('PESOTOTALITEM') or 0)

            ambientes_dict[ambiente]["total_quantidade"] += qtd
            ambientes_dict[ambiente]["total_bruto"] += val_bruto
            ambientes_dict[ambiente]["total_liquido"] += val_liquido
            ambientes_dict[ambiente]["total_frete"] += frete
            ambientes_dict[ambiente]["total_desconto"] += desconto
            ambientes_dict[ambiente]["total_margem"] += margem
            ambientes_dict[ambiente]["total_peso"] += peso

    ambientes = []
    for nome_ambiente, dados in ambientes_dict.items():
        ambiente_obj = {
            "ambiente": nome_ambiente,
            "itens": dados["itens"],
            "total_itens": len(dados["itens"])
        }

        if com_totais:
            ambiente_obj["totais"] = {
                "quantidade": round(dados["total_quantidade"], 2),
                "valor_bruto": round(dados["total_bruto"], 2),
                "valor_liquido": round(dados["total_liquido"], 2),
                "frete": round(dados["total_frete"], 2),
                "desconto": round(dados["total_desconto"], 2),
                "margem": round(dados["total_margem"], 2),
                "peso": round(dados["total_peso"], 2)
            }

        ambientes.append(ambiente_obj)

    return {
        "status": "success",
        "message": "Dados retornados com sucesso",
        "data": ambientes,
        "total_ambientes": len(ambientes),
        "total_produtos": len(result["data"])
    }


@router.get("/busca_pagamento_pedido")
@log_execution
async def busca_pagamento_pedido(idorcamento: str):
    """
    Busca as formas de pagamento do orçamento
    """
    sql = '''SELECT
        VCTO.IDRECEBIMENTO,
        VCTO.DTVENCIMENTO,
        FORMA.DESCRRECEBIMENTO,
        count(VCTO.DIGITODUPLICATA) AS PARCELAS,
        COALESCE(VCTO.IDCONDICAO,0) AS IDCONDICAO,
        CAST(SUM(VALDUPLICATA) AS VARCHAR(20)) AS VALOR_TOTAL,
        FP.FLAGCREDIARIO
    FROM
        DBA.ORCAMENTO_VCTO AS VCTO
        LEFT JOIN DBA.FORMA_PAGREC AS FORMA ON (VCTO.IDRECEBIMENTO = FORMA.IDRECEBIMENTO)
        LEFT JOIN DBA.CONDICOES_PAGREC AS CONDICAO ON (VCTO.IDCONDICAO = CONDICAO.IDCONDICAO)
        LEFT JOIN DBA.FORMA_PAGREC AS FP ON (VCTO.IDRECEBIMENTO = FP.IDRECEBIMENTO)
    WHERE
        IDORCAMENTO = (:IDORCAMENTO)
    GROUP BY
        VCTO.IDRECEBIMENTO,
        VCTO.DTVENCIMENTO,
        FORMA.DESCRRECEBIMENTO,
        VCTO.IDCONDICAO,
        FP.FLAGCREDIARIO'''

    df = await execute_query(
        "ciss_db2",
        query=sql,
        replace_dict={":IDORCAMENTO": idorcamento}
    )

    return await process_results(df, return_with_data=True)


@router.get("/gerar_pdf_orcamento")
@log_execution
async def gerar_pdf_orcamento(
    idorcamento: str,
    flagprenota: str = "F",
    cod_cliente: str = "",
    data_movimento: str = "",
    cnpj_cpf: str = "",
    fone1: str = "",
    fone2: str = "",
    fone_celular: str = "",
    endereco_completo: str = "",
    nome_vendedor: str = "",
    email_vendedor: str = "",
    telefone_vendedor: str = "",
    perfil_frete: str = "",
    obs: str = "",
    desc: str = "sim",
    img: str = "não",
    ref: str = "não",
    desconto: str = "não",
    iddpto: str = ""
):

    titulo_documento = "PEDIDO" if flagprenota.upper() == "T" else "ORÇAMENTO"

    """
    Gera PDF do orçamento usando xhtml2pdf (100% Python, sem dependências do sistema).
    """

    # Lógica de seleção do Logo conforme IDDPTO
    logo_padrao = "https://2dbe67f5823490f65fa9d6de49bdd23b.cdn.bubble.io/f1769633334651x401252819033534000/logo-elevato.png"
    logo_dpto_38 = "https://2dbe67f5823490f65fa9d6de49bdd23b.cdn.bubble.io/f1767015322693x260475703994185200/Design%20sem%20nome%20%287%29.png"
    logo_dpto_25 = "https://2dbe67f5823490f65fa9d6de49bdd23b.cdn.bubble.io/f1767015445819x944347729248270700/Design%20sem%20nome%20%289%29.png"
    logo_dpto_41_42 = "https://2dbe67f5823490f65fa9d6de49bdd23b.cdn.bubble.io/f1771014045342x360420395848848800/ElevatoCasa_Logo_Vertical_RGB_100.png"

    logo_url = logo_padrao
    if iddpto == "38":
        logo_url = logo_dpto_38
    elif iddpto == "25":
        logo_url = logo_dpto_25
    elif iddpto in ("41", "42"):
        logo_url = logo_dpto_41_42

    sql_pagamento = """SELECT
        VCTO.IDRECEBIMENTO,
        VCTO.DTVENCIMENTO,
        FORMA.DESCRRECEBIMENTO,
        COUNT(VCTO.DIGITODUPLICATA) AS PARCELAS,
        COALESCE(VCTO.IDCONDICAO, 0) AS IDCONDICAO,
        SUM(VALDUPLICATA) AS VALOR_TOTAL,
        FP.FLAGCREDIARIO
    FROM
        DBA.ORCAMENTO_VCTO AS VCTO
        LEFT JOIN DBA.FORMA_PAGREC AS FORMA ON (VCTO.IDRECEBIMENTO = FORMA.IDRECEBIMENTO)
        LEFT JOIN DBA.CONDICOES_PAGREC AS CONDICAO ON (VCTO.IDCONDICAO = CONDICAO.IDCONDICAO)
        LEFT JOIN DBA.FORMA_PAGREC AS FP ON (VCTO.IDRECEBIMENTO = FP.IDRECEBIMENTO)
    WHERE
        VCTO.IDORCAMENTO = :IDORCAMENTO_PAG
    GROUP BY
        VCTO.IDRECEBIMENTO,
        VCTO.DTVENCIMENTO,
        FORMA.DESCRRECEBIMENTO,
        VCTO.IDCONDICAO,
        FP.FLAGCREDIARIO"""

    # Query para buscar OBSERVACAO por NUMSEQUENCIA (sem cache para garantir dados atualizados)
    sql_observacoes = """SELECT COALESCE(CAST(OP.NUMSEQUENCIA AS VARCHAR(20)), '0') AS NUMSEQUENCIA,
        OP.OBSERVACAO
    FROM DBA.ORCAMENTO_PROD AS OP
    WHERE OP.IDORCAMENTO = :IDORCAMENTO_OBS"""

    # Disparar logo, itens, pagamento e observações em paralelo.
    # cache_ttl=120 → Redis guarda o resultado por 2 min (re-aberturas do mesmo orçamento são comuns).
    resultado_itens, logo_processado, df_pagamento, df_observacoes = await asyncio.gather(
        busca_orcamento_por_ambiente(idorcamento, com_totais=True),
        processar_logo_segura(logo_url),
        execute_query(
            "ciss_db2",
            query=sql_pagamento,
            replace_dict={":IDORCAMENTO_PAG": idorcamento},
            redis=True,
            cache_ttl=120,
        ),
        execute_query(
            "ciss_db2",
            query=sql_observacoes,
            replace_dict={":IDORCAMENTO_OBS": idorcamento},
            redis=False,
        ),
    )

    if resultado_itens.get("status") != "success":
        raise HTTPException(status_code=404, detail="Orçamento não encontrado")

    ambientes = resultado_itens.get("data", [])
    if not ambientes:
        raise HTTPException(status_code=404, detail="Nenhum item encontrado no orçamento")

    # Montar mapa NUMSEQUENCIA → OBSERVACAO a partir da query sem cache
    obs_map: dict[str, str] = {}
    try:
        resultado_obs = await process_results(df_observacoes, return_with_data=True)
        for row in resultado_obs.get("data", []):
            numseq = str(row.get("NUMSEQUENCIA", "") or "")
            obs_val = row.get("OBSERVACAO") or ""
            if numseq and obs_val:
                obs_map[numseq] = obs_val
    except Exception:
        pass  # Se falhar, segue sem sobrescrever

    # Injetar OBSERVACAO nos itens a partir do mapa (sobrescreve apenas se tiver valor)
    for ambiente in ambientes:
        for item in ambiente.get("itens", []):
            numseq = str(item.get("NUMSEQUENCIA", "") or "")
            if numseq in obs_map:
                item["OBSERVACAO"] = obs_map[numseq]

    resultado_pagamento = await process_results(df_pagamento, return_with_data=True)
    formas_pagamento = resultado_pagamento.get("data", []) if isinstance(resultado_pagamento, dict) else []

    # Calcular totais em uma única passagem
    total_geral = 0.0
    peso_total = 0.0
    total_descontos = 0.0
    total_frete = 0.0
    for ambiente in ambientes:
        for item in ambiente['itens']:
            total_geral += float(item.get('VALTOTALSEMFRETE') or 0)
            peso_total += float(item.get('PESOTOTALITEM') or 0)
            total_descontos += float(item.get('VALDESCONTOPRO') or 0) + float(item.get('VALDESCONTOFINANCEIRO') or 0)
            total_frete += float(item.get('VALFRETE') or 0)

    # Montar HTML do total geral
    total_com_frete = total_geral + total_frete
    if desconto.lower() == "sim" and total_descontos > 0:
        total_original = total_geral + total_descontos
        perc_desconto = (total_descontos / total_original * 100) if total_original > 0 else 0
        total_geral_html = f'''
        <table style="width: 100%; border-collapse: collapse; margin-top: 14px;">
            <tr>
                <td style="text-align: right; padding: 4px 12px; font-size: 10px; color: #888; border-top: 2px solid #1a3a5c;">
                    <span style="text-decoration: line-through;">Subtotal Bruto: R$ {format_value_br(total_original)}</span>
                </td>
            </tr>
            <tr>
                <td style="text-align: right; padding: 4px 12px; font-size: 10px; color: #5a6a7a;">
                    Desconto: <b>{format_value_br(perc_desconto)}%</b> (&minus; R$ {format_value_br(total_descontos)})
                </td>
            </tr>
            <tr>
                <td style="text-align: right; padding: 4px 8px; font-size: 11px; color: #1a3a5c;">
                    Subtotal: <b>R$ {format_value_br(total_geral)}</b>
                </td>
            </tr>
            <tr>
                <td style="text-align: right; padding: 4px 8px; font-size: 11px; color: #1a3a5c;">
                    Frete: <b>R$ {format_value_br(total_frete)}</b>
                </td>
            </tr>
            <tr>
                <td style="background-color: #f7f9fb; color: #1a3a5c; font-size: 14px; font-weight: bold; padding: 8px 12px; text-align: right;">
                    TOTAL: R$ {format_value_br(total_com_frete)}
                </td>
            </tr>
        </table>
        '''
    else:
        total_geral_html = f'''
        <table style="width: 100%; border-collapse: collapse; margin-top: 14px;">
            <tr>
                <td style="text-align: right; padding: 4px 8px; font-size: 11px; color: #1a3a5c; border-top: 2px solid #1a3a5c;">
                    Subtotal: <b>R$ {format_value_br(total_geral)}</b>
                </td>
            </tr>
            <tr>
                <td style="text-align: right; padding: 4px 8px; font-size: 11px; color: #1a3a5c;">
                    Frete: <b>R$ {format_value_br(total_frete)}</b>
                </td>
            </tr>
            <tr>
                <td style="background-color: #f7f9fb; color: #1a3a5c; font-size: 14px; font-weight: bold; padding: 8px 12px; text-align: right; border-top: 1px solid #d0d8e0;">
                    TOTAL: R$ {format_value_br(total_com_frete)}
                </td>
            </tr>
        </table>
        '''

    # Gerar HTML dos itens (async) e formas de pagamento (síncrono, pode rodar em paralelo via gather)
    itens_html, pagamento_html = await asyncio.gather(
        gerar_html_itens(ambientes, desc, img, ref, desconto),
        _wrap_sync(gerar_html_pagamento, formas_pagamento),
    )

    html_content = f'''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <style>
            @page {{
                size: A4;
                margin: 1cm 1cm 2cm 1cm;
                @frame footer_frame {{
                    -pdf-frame-content: page-footer;
                    bottom: 0cm;
                    margin-left: 1cm;
                    margin-right: 1cm;
                    height: 1.2cm;
                }}
            }}
            body {{
                font-family: Helvetica, sans-serif;
                color: #1a1a1a;
                margin: 0;
                padding: 0;
                font-size: 10px;
                line-height: 1.4;
            }}
            .info-table {{
                width: 100%;
                border-collapse: collapse;
                margin-bottom: 14px;
            }}
            .info-section-label {{
                color: #1a3a5c;
                background-color: #f7f9fb;
                font-size: 9px;
                font-weight: bold;
                text-transform: uppercase;
                padding: 5px 8px;
                letter-spacing: 1px;
                border-bottom: 1px solid #d0d8e0;
            }}
            .info-cell {{
                background-color: #ffffff;
                padding: 5px 8px;
                font-size: 10px;
                vertical-align: top;
                border-bottom: 1px solid #d0d8e0;
            }}
            .info-label {{
                color: #5a6a7a;
                font-size: 9px;
            }}
            .info-value {{
                font-weight: bold;
                color: #1a1a1a;
                font-size: 10px;
            }}
            .section-title-bar {{
                background-color: #ffffff;
                color: #1a3a5c;
                font-size: 11px;
                font-weight: bold;
                padding: 6px 10px;
                text-transform: uppercase;
                letter-spacing: 0.5px;
                margin-top: 10px;
                margin-bottom: 0;
            }}
            .items-table {{
                width: 100%;
                border-collapse: collapse;
                font-size: 10px;
                margin-bottom: 2px;
                table-layout: fixed;
            }}
            .items-header td {{
                background-color: #ffffff;
                color: #1a3a5c;
                font-size: 8px;
                font-weight: bold;
                text-transform: uppercase;
                letter-spacing: 0.3px;
                padding: 4px 6px;
            }}
            .item-cell {{
                padding: 5px 6px;
                border-bottom: 1px solid #e0e5ea;
                vertical-align: top;
                word-wrap: break-word;
                overflow-wrap: break-word;
            }}
            .item-image-cell {{
                width: 70px;
                padding: 4px;
                text-align: center;
                vertical-align: middle;
                border-bottom: 1px solid #e0e5ea;
            }}
            .subtotal-row td {{
                background-color: #e8eef4;
                font-weight: bold;
                font-size: 10px;
                color: #1a3a5c;
                padding: 5px 6px;
                border-top: 1.5px solid #2c5f8a;
            }}
            .payment-table {{
                width: 100%;
                border-collapse: collapse;
                margin-top: 10px;
                border: 1px solid #d0d8e0;
            }}
            .payment-header {{
                background-color: #f7f9fb;
                color: #1a3a5c;
                font-size: 9px;
                font-weight: bold;
                text-transform: uppercase;
                padding: 5px 8px;
            }}
            .payment-cell {{
                padding: 5px 8px;
                font-size: 10px;
                border-bottom: 1px solid #e0e5ea;
            }}
        </style>
    </head>
    <body>
        <!-- Barra de acento azul no topo -->
        <table style="width: 100%; margin-bottom: 0;">
            <tr>
                <td style="background-color: #1a3a5c; height: 4px; font-size: 1px;" colspan="2">&nbsp;</td>
            </tr>
        </table>

        <!-- Header: Titulo + Logo -->
        <table style="width: 100%; margin-top: 6px; margin-bottom: 12px;">
            <tr>
                <td style="vertical-align: middle; width: 65%;">
                    <div style="font-size: 18px; font-weight: bold; color: #1a3a5c;">
                        {titulo_documento} #{idorcamento}
                    </div>
                    <div style="font-size: 10px; color: #5a6a7a;">
                        {cod_cliente}
                    </div>
                </td>
                <td style="text-align: right; width: 35%; vertical-align: middle; padding: 8px 0;">
                    <img src="{logo_processado}" alt="Logo" width="100" height="55">
                </td>
            </tr>
        </table>

        <!-- Divisor -->
        <div style="border-bottom: 2px solid #1a3a5c; margin-bottom: 12px;"></div>

        <!-- Informações do Cliente e Vendedor -->
        <table class="info-table">
            <tr>
                <td class="info-section-label" style="width: 50%;">DADOS DO CLIENTE</td>
                <td class="info-section-label" style="width: 50%;">DADOS DO VENDEDOR</td>
            </tr>
            <tr>
                <td class="info-cell">
                    <span class="info-label">Data:</span>
                    <span class="info-value">{data_movimento or datetime.now().strftime('%d/%m/%Y')}</span>
                </td>
                <td class="info-cell">
                    <span class="info-label">Vendedor:</span>
                    <span class="info-value">{nome_vendedor}</span>
                </td>
            </tr>
            <tr>
                <td class="info-cell">
                    <span class="info-label">CNPJ/CPF:</span>
                    <span class="info-value">{cnpj_cpf}</span>
                </td>
                <td class="info-cell">
                    <span class="info-label">E-mail:</span>
                    <span class="info-value">{email_vendedor}</span>
                </td>
            </tr>
            <tr>
                <td class="info-cell">
                    <span class="info-label">Tel. Residencial:</span>
                    <span class="info-value">{fone1}</span>
                </td>
                <td class="info-cell">
                    <span class="info-label">Telefone:</span>
                    <span class="info-value">{telefone_vendedor}</span>
                </td>
            </tr>
            <tr>
                <td class="info-cell">
                    <span class="info-label">Tel. Comercial:</span>
                    <span class="info-value">{fone2}</span>
                </td>
                <td class="info-cell">
                    <span class="info-label">Peso Total:</span>
                    <span class="info-value">{format_value_br(peso_total)} KG</span>
                </td>
            </tr>
            <tr>
                <td class="info-cell">
                    <span class="info-label">Celular:</span>
                    <span class="info-value">{fone_celular}</span>
                </td>
                <td class="info-cell">
                    <span class="info-label">Perfil de Entrega:</span>
                    <span class="info-value">{perfil_frete}</span>
                </td>
            </tr>
            <tr>
                <td class="info-cell" colspan="2">
                    <span class="info-label">Endereço de Entrega:</span>
                    <span class="info-value">{endereco_completo}</span>
                </td>
            </tr>
        </table>

        <!-- Titulo da seção de itens -->
        <div class="section-title-bar">ITENS DO ORÇAMENTO</div>

        {itens_html}

        <!-- Total Geral -->
        {total_geral_html}

        <!-- Forma de Pagamento -->
        {pagamento_html}

        <!-- Observações -->
        <table style="width: 100%; margin-top: 14px;">
            <tr>
                <td style="padding: 8px 10px; background-color: #f7f9fb; border-left: 3px solid #2c5f8a; font-size: 9px; color: #5a6a7a;">
                    <b style="color: #1a3a5c; font-size: 10px;">Observações</b><br>
                    {obs}
                </td>
            </tr>
        </table>
        <table style="width: 100%; margin-top: 6px;">
            <tr>
                <td style="text-align: center; font-size: 8px; color: #999999; font-style: italic;">
                    Fotos meramente ilustrativas
                </td>
            </tr>
        </table>

        <!-- Rodapé com numeração de páginas -->
        <div id="page-footer">
            <table style="width: 100%;">
                <tr>
                    <td style="text-align: center; font-size: 8px; color: #999999;">
                        {titulo_documento} #{idorcamento} &mdash; Página <pdf:pagenumber> de <pdf:pagecount>
                    </td>
                </tr>
            </table>
        </div>
    </body>
    </html>
    '''

    # Gerar PDF em thread separada — pisa.CreatePDF é síncrono/CPU-bound
    # e bloquearia o event loop do asyncio se chamado diretamente.
    def _criar_pdf(html: str) -> io.BytesIO:
        buf = io.BytesIO()
        status = pisa.CreatePDF(html, dest=buf)
        if status.err:
            raise RuntimeError("xhtml2pdf retornou erro ao gerar PDF")
        buf.seek(0)
        return buf

    loop = asyncio.get_event_loop()
    try:
        pdf_buffer = await loop.run_in_executor(None, _criar_pdf, html_content)
    except RuntimeError:
        raise HTTPException(status_code=500, detail="Erro ao gerar PDF")

    return StreamingResponse(
        pdf_buffer,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=orcamento_{idorcamento}.pdf"
        }
    )


async def _wrap_sync(fn, *args):
    """Envolve função síncrona para uso em asyncio.gather."""
    return fn(*args)


def gerar_html_pagamento(formas_pagamento):
    """Gera HTML das formas de pagamento"""

    if not formas_pagamento:
        return (
            '<table class="payment-table">'
            '<tr><td class="payment-header" colspan="4">FORMA DE PAGAMENTO</td></tr>'
            '<tr><td class="payment-cell" colspan="4">A definir</td></tr>'
            '</table>'
        )

    parts = []
    parts.append('<table class="payment-table">')
    parts.append(
        '<tr>'
        '<td class="payment-header" style="width: 40%;">FORMA DE PAGAMENTO</td>'
        '<td class="payment-header" style="width: 20%; text-align: center;">PARCELAS</td>'
        '<td class="payment-header" style="width: 20%; text-align: right;">VALOR</td>'
        '<td class="payment-header" style="width: 20%; text-align: right;">VENCIMENTO</td>'
        '</tr>'
    )

    for forma in formas_pagamento:
        descricao = forma.get('DESCRRECEBIMENTO', 'SEM DESCRIÇÃO')
        parcelas = int(forma.get('PARCELAS', 1))
        valor_total = float(forma.get('VALOR_TOTAL', 0))
        valor_parcela = valor_total / parcelas if parcelas > 0 else 0
        dt_vencimento = forma.get('DTVENCIMENTO', '')

        if dt_vencimento:
            try:
                if isinstance(dt_vencimento, str):
                    dt_obj = datetime.strptime(dt_vencimento.split()[0], '%Y-%m-%d')
                else:
                    dt_obj = dt_vencimento
                dt_formatada = dt_obj.strftime('%d/%m/%y')
            except Exception:
                dt_formatada = str(dt_vencimento)[:10]
        else:
            dt_formatada = ''

        parts.append(
            f'<tr>'
            f'<td class="payment-cell"><b>{descricao}</b></td>'
            f'<td class="payment-cell" style="text-align: center;">{parcelas}x</td>'
            f'<td class="payment-cell" style="text-align: right;">R$ {format_value_br(valor_parcela)}</td>'
            f'<td class="payment-cell" style="text-align: right;">{dt_formatada}</td>'
            f'</tr>'
        )

    parts.append('</table>')
    return "\n".join(parts)


def _processar_imagem_bytes(raw_bytes: bytes) -> str:
    """
    Processamento CPU-bound (Pillow): remove canal alpha e serializa como JPEG base64.
    Executado em thread pool para não bloquear o event loop.
    """
    img = Image.open(io.BytesIO(raw_bytes)).convert("RGBA")
    bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    bg.paste(img, (0, 0), img)
    final_img = bg.convert("RGB")
    buffer = io.BytesIO()
    final_img.save(buffer, format="JPEG", quality=85)
    img_str = base64.b64encode(buffer.getvalue()).decode('utf-8')
    return f"data:image/jpeg;base64,{img_str}"


async def processar_logo_segura(url: str) -> str:
    """
    Baixa uma imagem, remove transparência e retorna base64 para o HTML.
    - Resultado cacheado em memória por URL (evita re-download entre requests).
    - Processamento Pillow rodado em thread pool (não bloqueia o event loop).
    """
    if not url:
        return ""

    # Cache hit → retorna imediatamente sem I/O
    if url in _logo_cache:
        return _logo_cache[url]

    try:
        client = _get_http_client()
        resp = await client.get(url)
        if resp.status_code != 200:
            return url

        loop = asyncio.get_event_loop()
        data_uri = await loop.run_in_executor(None, _processar_imagem_bytes, resp.content)

        _logo_cache[url] = data_uri
        return data_uri
    except Exception as e:
        print(f"Erro ao processar logo: {e}")
        return url


async def buscar_imagem_produto(idproduto: str):
    """Busca a imagem do produto da API do Bubble com constraints"""
    try:
        url = "https://portal.elevato.com.br/api/1.1/obj/PRODUTO_IMAGEM"
        constraints = [{"key": "IDPRODUTO", "constraint_type": "equals", "value": idproduto}]

        client = _get_http_client()
        response = await client.get(url, params={"constraints": json.dumps(constraints)})

        if response.status_code == 200:
            data = response.json()
            results = data.get("response", {}).get("results", [])
            if results:
                imagem_url = results[0].get("FOTO")
                if imagem_url and imagem_url.startswith("//"):
                    imagem_url = "https:" + imagem_url
                return imagem_url

        return None
    except Exception as e:
        print(f"Erro ao buscar imagem do produto {idproduto}: {e}")
        return None


async def gerar_html_itens(ambientes, desc="sim", img="não", ref="não", desconto="não"):
    """Gera HTML dos itens agrupados por ambiente (compatível com xhtml2pdf)"""
    usar_img = img.lower() == "sim"
    usar_desc = desc.lower() == "sim"
    usar_ref = ref.lower() == "sim"

    # Buscar todas as imagens em paralelo caso img=sim.
    # Deduplicamos os IDs antes do gather para não fazer N chamadas ao mesmo produto
    # quando ele aparece em múltiplos ambientes.
    if usar_img:
        todos_produtos = [
            item.get('IDPRODUTO', '')
            for ambiente in ambientes
            for item in ambiente['itens']
        ]
        ids_unicos = list(dict.fromkeys(pid for pid in todos_produtos if pid))  # preserva ordem, sem repetição
        imagens_unicas = await asyncio.gather(*[buscar_imagem_produto(pid) for pid in ids_unicos])
        imagens_map: dict[str, str | None] = dict(zip(ids_unicos, imagens_unicas))
    else:
        imagens_map = {}

    parts = []

    for ambiente in ambientes:
        nome_ambiente = ambiente['ambiente']
        itens = ambiente['itens']
        totais = ambiente.get('totais', {})

        nome_valido = (
            nome_ambiente
            and nome_ambiente.strip()
            and nome_ambiente.upper() not in ('SEM_AMBIENTE', 'NULL', 'NONE')
        )

        # Barra do ambiente
        if nome_valido:
            parts.append(
                f'<table style="width: 100%; margin-top: 10px;">'
                f'<tr><td style="background-color: #e8eef4; border-left: 2px solid #2c5f8a; '
                f'padding: 5px 10px; font-size: 11px; font-weight: bold; color: #1a3a5c; '
                f'text-transform: uppercase;">{nome_ambiente}</td></tr>'
                f'</table>'
            )

        # Tabela de itens do ambiente
        parts.append('<table class="items-table">')

        # Cabeçalho das colunas
        if usar_img:
            parts.append(
                '<tr class="items-header">'
                '<td style="width: 70px;"></td>'
                '<td style="width: 4%;">#</td>'
                '<td style="width: 8%;">CÓDIGO</td>'
                '<td style="width: 35%;">DESCRIÇÃO</td>'
                '<td style="width: 13%; text-align: center;">QTD</td>'
                '<td style="width: 15%; text-align: right;">VL. UNIT.</td>'
                '<td style="width: 15%; text-align: right;">VL. TOTAL</td>'
                '</tr>'
            )
            num_cols = 7
        else:
            parts.append(
                '<tr class="items-header">'
                '<td style="width: 4%;">#</td>'
                '<td style="width: 8%;">CÓDIGO</td>'
                '<td style="width: 35%;">DESCRIÇÃO</td>'
                '<td style="width: 12%;">LOTE</td>'
                '<td style="width: 13%; text-align: center;">QTD</td>'
                '<td style="width: 14%; text-align: right;">VL. UNIT.</td>'
                '<td style="width: 14%; text-align: right;">VL. TOTAL</td>'
                '</tr>'
            )
            num_cols = 7

        # Linhas de itens com cores alternadas
        for idx, item in enumerate(itens):
            produto_id = item.get('IDPRODUTO', '')
            if str(produto_id) == '1177792':
                descricao_texto = item.get('OBSERVACAO', '') or item.get('DESCRCOMPRODUTO', '')
            else:
                descricao_texto = item.get('DESCRCOMPRODUTO', '') if usar_desc else item.get('MODELO', '')
            num_sequencia = item.get('NUMSEQUENCIA', '')
            qtd = float(item.get('QTDPRODUTO') or 0)
            embalagem = item.get('EMBALAGEMSAIDA', 'UN')
            val_unit = float(item.get('VALUNITLIQUIDO') or 0)
            val_total = float(item.get('VALTOTALSEMFRETE') or 0)
            lote = item.get('IDLOTE', '')
            referencia = item.get('REFERENCIA', '')

            row_bg = '#ffffff' if idx % 2 == 0 else '#f7f9fb'

            # Montar conteúdo da descrição com sub-informações
            desc_parts_list = [f'<b>{descricao_texto}</b>']
            if usar_ref and referencia:
                desc_parts_list.append(
                    f'<br><span style="font-size: 8px; color: #5a6a7a;">Ref: {referencia}</span>'
                )

            desc_content = ''.join(desc_parts_list)

            # Célula de imagem (quando habilitada)
            if usar_img:
                img_url = imagens_map.get(produto_id)
                if img_url:
                    img_cell = (
                        f'<td class="item-image-cell" style="background-color: {row_bg};">'
                        f'<img src="{img_url}" width="65" height="65">'
                        f'</td>'
                    )
                else:
                    img_cell = (
                        f'<td class="item-image-cell" style="background-color: {row_bg}; '
                        f'color: #cccccc; font-size: 8px;">SEM<br>IMAGEM</td>'
                    )
            else:
                img_cell = ''

            # Célula de lote (apenas quando sem imagem)
            lote_cell = '' if usar_img else f'<td class="item-cell" style="background-color: {row_bg};">{lote}</td>'

            parts.append(
                f'<tr style="page-break-inside: avoid;">'
                f'{img_cell}'
                f'<td class="item-cell" style="text-align: center; color: #5a6a7a; background-color: {row_bg};">{num_sequencia}</td>'
                f'<td class="item-cell" style="background-color: {row_bg};"><b>{produto_id}</b></td>'
                f'<td class="item-cell" style="background-color: {row_bg};">{desc_content}</td>'
                f'{lote_cell}'
                f'<td class="item-cell" style="text-align: center; background-color: {row_bg};">{format_value_br(qtd)} {embalagem}</td>'
                f'<td class="item-cell" style="text-align: right; background-color: {row_bg};">R$ {format_value_br(val_unit)}</td>'
                f'<td class="item-cell" style="text-align: right; font-weight: bold; background-color: {row_bg};">R$ {format_value_br(val_total)}</td>'
                f'</tr>'
            )

        # Subtotal do ambiente
        if totais and nome_valido:
            parts.append(
                f'<tr class="subtotal-row">'
                f'<td colspan="{num_cols - 1}" style="text-align: right; padding-right: 10px;">Total {nome_ambiente}:</td>'
                f'<td style="text-align: right; padding: 5px 6px; border-top: 1.5px solid #2c5f8a; '
                f'background-color: #e8eef4; font-weight: bold; color: #1a3a5c;">'
                f'R$ {format_value_br(totais.get("valor_bruto", 0))}</td>'
                f'</tr>'
            )

        parts.append('</table>')

    return "\n".join(parts)
