"""Extrai metadados de XMLs de NFS-e, NF-e, NFC-e e CT-e."""
from __future__ import annotations

import datetime
import re
import xml.etree.ElementTree as ET
from typing import Optional

from .database import NotaMeta


def _text(el: Optional[ET.Element]) -> str:
    if el is None or el.text is None:
        return ""
    return el.text.strip()


def _find_first(root: ET.Element, tags: list[str]) -> Optional[ET.Element]:
    for tag in tags:
        el = root.find(f".//{{*}}{tag}")
        if el is not None:
            return el
    return None


def _find_text(root: ET.Element, tags: list[str]) -> str:
    el = _find_first(root, tags)
    return _text(el)


def _cnpj_de(node: Optional[ET.Element]) -> str:
    """Extrai CNPJ/CPF de um nó sem usar 'or' em Element (falsy sem filhos)."""
    if node is None:
        return ""
    for tag in ("CNPJ", "Cnpj", "cnpj", "CPF", "Cpf", "cpf"):
        el = node.find(f".//{{*}}{tag}")
        if el is None:
            el = node.find(f"{{*}}{tag}")
        if el is not None and el.text:
            return re.sub(r"\D", "", el.text)
    return ""


def _parse_valor(txt: str) -> float:
    """Parse valores monetários em formato BR (1.234,56 ou 1234.56)."""
    if not txt:
        return 0.0
    txt = txt.strip()
    # Remove espaços e caracteres de moeda
    txt = re.sub(r"[R$\s]", "", txt)
    
    # Detecta separador decimal: 1.234,56 ou 1234.56
    if "," in txt and "." in txt:
        # Formato 1.234,56 (BR)
        txt = txt.replace(".", "").replace(",", ".")
    elif "," in txt:
        # Pode ser 1,234.56 (EN) ou 1234,56 (BR)
        # Se tem mais de 2 casas após vírgula, é separador de milhares
        last_comma = txt.rfind(",")
        after_comma = txt[last_comma+1:] if last_comma >= 0 else ""
        if len(after_comma) <= 2:
            # Formato BR: 1234,56
            txt = txt.replace(",", ".")
        else:
            # Formato EN: 1,234.56
            txt = txt.replace(",", "")
    
    try:
        val = float(re.sub(r"[^\d.]", "", txt) or 0)
        return round(val, 2)
    except Exception:
        return 0.0


def _parse_data(txt: str) -> tuple[str, str]:
    """Retorna (data_iso YYYY-MM-DD, competencia YYYY-MM)."""
    now = datetime.datetime.now()
    if not txt:
        return now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")
    txt = txt.strip().replace("Z", "")
    
    # tenta ISO com horário
    try:
        if "T" in txt:
            dt = datetime.datetime.fromisoformat(txt[:19])
            return dt.strftime("%Y-%m-%d"), dt.strftime("%Y-%m")
    except Exception:
        pass
    
    # tenta sem horário
    for fmt, sl in (("%Y-%m-%d", 10), ("%d/%m/%Y", 10), ("%d-%m-%Y", 10)):
        try:
            dt = datetime.datetime.strptime(txt[:sl], fmt)
            return dt.strftime("%Y-%m-%d"), dt.strftime("%Y-%m")
        except Exception:
            continue
    
    return now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")


def _detectar_familia(root: ET.Element, chave: str) -> str:
    """Detecta tipo: nfse | nfe | nfce | cte"""
    ch = re.sub(r"\D", "", chave or "")
    if len(ch) >= 22:
        mod = ch[20:22]
        if mod == "55":
            return "nfe"
        if mod == "65":
            return "nfce"
        if mod == "57":
            return "cte"
    
    # tags típicas
    tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag
    low = tag.lower()
    
    if "cte" in low or root.find(".//{*}CTe") is not None or root.find(".//{*}infCte") is not None:
        return "cte"
    
    if root.find(".//{*}infNFSe") is not None or root.find(".//{*}infDPS") is not None:
        return "nfse"
    
    el_mod = root.find(".//{*}mod")
    if el_mod is not None and el_mod.text:
        m = el_mod.text.strip()
        if m == "65":
            return "nfce"
        if m == "55":
            return "nfe"
        if m == "57":
            return "cte"
    
    if "nfe" in low or root.find(".//{*}infNFe") is not None or root.find(".//{*}resNFe") is not None:
        return "nfe"
    
    return "nfse"


def extrair_metadados(
    xml_bytes: bytes,
    cnpj_empresa: str,
    chave: str = "",
    nsu: int = 0,
    caminho_xml: str = "",
) -> NotaMeta:
    """
    Analisa o XML (NFS-e, NF-e, NFC-e, CT-e) e monta NotaMeta.
    tipo = 'emitida' se o CNPJ da empresa for o emitente,
           'tomada' se for destinatário/tomador.
    """
    cnpj_empresa = re.sub(r"\D", "", cnpj_empresa)
    nota = NotaMeta(
        cnpj_empresa=cnpj_empresa,
        chave=chave,
        nsu=nsu,
        caminho_xml=caminho_xml,
        status="normal",
        tipo="",
        origem="nfse",
    )

    try:
        root = ET.fromstring(xml_bytes)
    except Exception:
        return nota

    # Chave
    if not nota.chave:
        for tag in ("chNFe", "chCTe", "chNFSe", "ChaveAcesso", "Id"):
            el = root.find(f".//{{*}}{tag}")
            if el is not None:
                t = (el.text or el.attrib.get("Id") or "").strip()
                if t.startswith("NFe"):
                    t = t[3:]
                if t.startswith("CTe"):
                    t = t[3:]
                t = re.sub(r"\D", "", t) or t
                if t:
                    nota.chave = t
                    break
        if not nota.chave:
            nota.chave = chave

    familia = _detectar_familia(root, nota.chave)
    nota.origem = familia if familia != "nfse" else "nfse"

    # Número
    nota.numero = _find_text(root, ["nNF", "nCT", "nNFSe", "Numero", "nDPS", "NumeroNfse"])

    # Data / competência
    dh = _find_text(
        root,
        ["dhEmi", "dEmi", "DataEmissao", "dhEvento", "DataCompetencia", "Competencia"],
    )
    nota.data_emissao, nota.competencia = _parse_data(dh)

    # Valor — ordem de prioridade por tipo de documento
    # Para NF-e/NFC-e: buscar vNF primeiro (valor total), depois vProd
    # Para CT-e: buscar vTPrest, vRec, vPrest
    # Para NFS-e: buscar vServ, ValorServicos, vLiq
    
    valor_txt = ""
    
    if familia in ("nfe", "nfce"):
        # NF-e / NFC-e: prioriza vNF (valor total da nota)
        valor_txt = _find_text(root, ["vNF", "vProd"])
    elif familia == "cte":
        # CT-e: valor do frete/prestação
        valor_txt = _find_text(root, ["vTPrest", "vRec", "vPrest", "vNF"])
    elif familia == "nfse":
        # NFS-e: valor de serviços
        valor_txt = _find_text(root, ["vServ", "ValorServicos", "vLiq", "ValorLiquidoNfse", "vNFSe", "ValorTotal"])
    
    # Fallback: tenta em qualquer lugar
    if not valor_txt:
        valor_tags = ["vNF", "vProd", "vTPrest", "vRec", "vPrest", "vServ", "ValorServicos", 
                      "vLiq", "ValorLiquidoNfse", "vNFSe", "ValorTotal", "valor"]
        valor_txt = _find_text(root, valor_tags)
    
    # resNFe: tenta vNF na raiz se ainda não achou
    if not valor_txt:
        el = root.find(".//{*}vNF")
        if el is None:
            el = root.find("{*}vNF")
        valor_txt = _text(el)
    
    nota.valor = _parse_valor(valor_txt)

    # Emitente
    emit = _find_first(root, ["emit", "Emitente", "prest", "Prestador", "PrestadorServico"])
    if emit is not None:
        nota.cnpj_emitente = _cnpj_de(emit)
        nota.nome_emitente = _find_text(emit, ["xNome", "RazaoSocial", "Nome"])
    else:
        # resNFe: CNPJ na raiz = emitente
        local = root.tag.split("}")[-1] if "}" in root.tag else root.tag
        if local in ("resNFe", "resCTe") or root.find(".//{*}resNFe") is not None:
            node = root if local.startswith("res") else root.find(".//{*}resNFe")
            nota.cnpj_emitente = _cnpj_de(node)
            nota.nome_emitente = _find_text(node or root, ["xNome", "RazaoSocial"])
        else:
            nota.cnpj_emitente = re.sub(
                r"\D",
                "",
                _find_text(root, ["CNPJPrestador", "CnpjPrestador", "CNPJEmitente"]),
            )
            nota.nome_emitente = _find_text(root, ["RazaoSocialPrestador", "NomePrestador"])

    # Tomador / destinatário
    dest = _find_first(
        root,
        ["dest", "Destinatario", "toma", "toma4", "Tomador", "TomadorServico", "receb", "rem", "exped"],
    )
    if dest is not None:
        nota.cnpj_tomador = _cnpj_de(dest)
        nota.nome_tomador = _find_text(dest, ["xNome", "RazaoSocial", "Nome"])
    else:
        nota.cnpj_tomador = re.sub(
            r"\D",
            "",
            _find_text(root, ["CNPJTomador", "CnpjTomador", "CPFTomador", "CNPJDest"]),
        )
        nota.nome_tomador = _find_text(root, ["RazaoSocialTomador", "NomeTomador"])

    # Tipo emitida / tomada (prioridade: CNPJ fields > chave)
    if nota.cnpj_emitente and nota.cnpj_emitente == cnpj_empresa:
        nota.tipo = "emitida"
    elif nota.cnpj_tomador and nota.cnpj_tomador == cnpj_empresa:
        nota.tipo = "tomada"
    else:
        # fallback: chave (CNPJ emitente nas posições 6-20)
        ch = re.sub(r"\D", "", nota.chave or "")
        if len(ch) >= 20 and ch[6:20] == cnpj_empresa:
            nota.tipo = "emitida"
        else:
            nota.tipo = "tomada"

    # Cancelamento
    status_txt = (_find_text(root, ["Status", "cStat", "Situacao", "tpEvento", "cSitNFe"]) or "").lower()
    if any(x in status_txt for x in ("cancel", "cancelad", "110111")):
        nota.status = "cancelada"
    
    if root.find(".//{*}infEvento") is not None or root.find(".//{*}Evento") is not None:
        desc = (_find_text(root, ["xEvento", "descEvento", "xMotivo"]) or "").lower()
        if "cancel" in desc:
            nota.status = "cancelada"
        elif "substit" in desc:
            nota.status = "substituida"

    return nota


def extrair_ano_mes(xml_bytes: bytes) -> tuple[str, str]:
    """Extrai ano/mês do XML. Compatível com downloader antigo."""
    meta = extrair_metadados(xml_bytes, "")
    if meta.competencia and len(meta.competencia) >= 7:
        return meta.competencia[:4], meta.competencia[5:7]
    now = datetime.datetime.now()
    return str(now.year), f"{now.month:02d}"
