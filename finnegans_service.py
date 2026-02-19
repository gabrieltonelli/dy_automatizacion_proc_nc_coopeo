import os
import requests
import logging
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

class FinnegansService:
    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.token_url = "https://api.teamplace.finneg.com/api/oauth/token"
        self.report_url = "https://api.finneg.com/api/reports/APICONSULTAFACTURAVENTADY"
        self.create_url = "https://api.finneg.com/api/DonYeyoPedidoVentaV3"
        self._access_token = None

    def _get_access_token(self) -> str:
        if self._access_token:
            return self._access_token
        
        params = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret
        }
        response = requests.get(self.token_url, params=params)
        response.raise_for_status()
        self._access_token = response.text.strip()
        return self._access_token

    def buscar_factura(self, nro_comprobante: str) -> List[Dict[str, Any]]:
        """
        Busca una factura en Finnegans por numero de comprobante (formato 'A-00006-00123456').
        Devuelve el primer resultado (o la lista completa).
        """
        token = self._get_access_token()
        params = {
            "ACCESS_TOKEN": token,
            "PARAMNumeroComprobante": nro_comprobante,
            "PARAMIntermediarioCod": "",
            "PARAMClienteCod": "",
            "PARAMFechaDesde": "2023-09-18",
            "PARAMFechaHasta": "2050-09-18"
        }
        response = requests.get(self.report_url, params=params)
        response.raise_for_status()
        return response.json()

    def create_document(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Crea el documento de Solicitud NC en Finnegans.
        """
        token = self._get_access_token()
        url = f"{self.create_url}?ACCESS_TOKEN={token}"
        
        response = requests.post(url, json=payload)
        # No usamos raise_for_status porque queremos parsear el body en caso de error
        return {
            "status": response.status_code,
            "body": response.text,
            "json": response.json() if "application/json" in response.headers.get("Content-Type", "") else None
        }
