import os
import re
import hashlib
import json
import logging
import requests
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta, timezone
from PyPDF2 import PdfReader

logger = logging.getLogger(__name__)

class CoopPortalService:
    def __init__(self, username: str, password: str, base_url: str, origin: str, referer: str, timeout: int = 30):
        self.username = username
        self.password = password
        self.base_url = base_url.rstrip("/")
        self.origin = origin
        self.referer = referer
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Automation Script) Python/Requests",
            "Accept": "application/json",
            "Origin": self.origin,
            "Referer": self.referer,
        })
        
        # Endpoints
        self.URL_LOGIN = f"{self.base_url}/usuarios/login"
        self.URL_PROV_BASE = f"{self.base_url}/usuarios/proveedor"
        self.URL_SOLICITUDES = f"{self.base_url}/solicitudes_nc"
        self.URL_PDF = f"{self.base_url}/solicitud_nc"

    def _sha256_hex(self, s: str) -> str:
        return hashlib.sha256(s.encode("utf-8")).hexdigest()

    def login(self) -> List[Dict[str, Any]]:
        password_hash = self._sha256_hex(self.password)
        body = {"username": self.username, "password": password_hash, "password2": password_hash}
        r = self.session.post(self.URL_LOGIN, json=body, timeout=self.timeout)
        r.raise_for_status()
        js = r.json()
        if not js.get("success"):
            raise RuntimeError(f"Login Coop fallido: {js.get('message')}")
        return (js.get("data") or {}).get("proveedores", [])

    def seleccionar_proveedor(self, prov: str):
        url = f"{self.URL_PROV_BASE}/{prov}"
        r = self.session.get(url, timeout=self.timeout)
        r.raise_for_status()

    def listar_solicitudes(self, fecha_desde: str, fecha_hasta: str) -> List[Dict[str, Any]]:
        params = {"fecha_desde": fecha_desde, "fecha_hasta": fecha_hasta}
        r = self.session.get(self.URL_SOLICITUDES, params=params, timeout=self.timeout)
        if r.status_code == 401:
             # Re-intentar login una vez si expira
             self.login()
             r = self.session.get(self.URL_SOLICITUDES, params=params, timeout=self.timeout)
        r.raise_for_status()
        return r.json().get("data") or []

    def descargar_pdf(self, nro: str, tipocomp: str, letra: str) -> bytes:
        params = {"nro_comprobante": str(nro), "tipocomp": str(tipocomp), "letra": str(letra or "")}
        r = self.session.get(self.URL_PDF, params=params, timeout=self.timeout)
        r.raise_for_status()
        if "application/pdf" not in r.headers.get("Content-Type", ""):
            raise RuntimeError(f"Contenido no-PDF recibido para NC {nro}")
        return r.content

class CoopParser:
    @staticmethod
    def normalizar_importe_ar(s: Optional[str]) -> Optional[float]:
        if not s: return None
        s = s.strip()
        if "," in s and "." in s:
            s = s.replace(".", "").replace(",", ".")
        elif "," in s and "." not in s:
            s = s.replace(",", ".")
        try: return float(s)
        except ValueError: return None

    @staticmethod
    def extract_text_from_pdf(pdf_bytes: bytes) -> str:
        from io import BytesIO
        reader = PdfReader(BytesIO(pdf_bytes))
        texts = []
        for page in reader.pages:
            texts.append(page.extract_text() or "")
        return "\n".join(texts)

    def parse_text_to_dict(self, text: str) -> Dict[str, Any]:
        data = {"fecha": None, "items": []}
        
        m_fecha = re.search(r"Fecha\s*\[(\d{2}/\d{2}/\d{4})\]", text)
        if m_fecha:
            data["fecha"] = m_fecha.group(1)

        # Regex para diferentes formatos de tabla (A, B, C)
        row_re_a = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s+(.+?)\s+(\d+)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)\s*$")
        row_re_b = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s+(.+?)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)\s*$")
        row_re_c = re.compile(r"^\s*(.+?)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)\s*$")

        lines = text.splitlines()
        in_table = False
        table_type = None
        
        for line in lines:
            line = line.strip()
            if "Descripcion" in line and "Neto" in line:
                in_table = True
                if "Cantidad" in line:
                    table_type = "A" if "NP recepcion" in line else "B"
                else:
                    table_type = "C"
                continue
            
            if in_table:
                if line.startswith("Neto") or line.startswith("Total"):
                    break
                
                matched = False
                res = {}
                if table_type == "A":
                    m = row_re_a.match(line)
                    if m:
                        res = {"cantidad": self.normalizar_importe_ar(m.group(1)), "desc": m.group(2).strip(), "np": m.group(3), "neto": self.normalizar_importe_ar(m.group(4))}
                        matched = True
                elif table_type == "B":
                    m = row_re_b.match(line)
                    if m:
                        res = {"cantidad": self.normalizar_importe_ar(m.group(1)), "desc": m.group(2).strip(), "np": "", "neto": self.normalizar_importe_ar(m.group(3))}
                        matched = True
                elif table_type == "C":
                    m = row_re_c.match(line)
                    if m:
                        res = {"cantidad": 1.0, "desc": m.group(1).strip(), "np": "", "neto": self.normalizar_importe_ar(m.group(2))}
                        matched = True

                if matched:
                    data["items"].append({
                        "cantidad": res["cantidad"],
                        "descripcion": res["desc"],
                        "np_recepcion": res["np"],
                        "neto": res["neto"]
                    })
        return data
