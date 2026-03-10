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
        self._vendedores_mapping = None
        self._clientes_cache = {}

    def _log_curl(self, method: str, url: str, params: Optional[Dict] = None, json_data: Optional[Dict] = None):
        """Genera y loguea el equivalente en curl de la petición."""
        full_url = url
        if params:
            import urllib.parse
            query = urllib.parse.urlencode(params)
            full_url += ("&" if "?" in url else "?") + query
        
        curl = f'curl -X {method} "{full_url}"'
        if json_data:
            import json
            curl += f" -H \"Content-Type: application/json\" -d '{json.dumps(json_data)}'"
        
        logger.info(f"DEBUG CURL: {curl}")

    def _get_access_token(self) -> str:
        if self._access_token:
            return self._access_token
        
        params = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret
        }
        self._log_curl("GET", self.token_url, params)
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
            "PARAMFechaDesde": "2023-01-01",
            "PARAMFechaHasta": "2050-12-31"
        }
        self._log_curl("GET", self.report_url, params)
        response = requests.get(self.report_url, params=params)
        response.raise_for_status()
        return response.json()

    def create_document(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Crea el documento de Solicitud NC en Finnegans.
        """
        token = self._get_access_token()
        url = f"{self.create_url}?ACCESS_TOKEN={token}"
        
        self._log_curl("POST", url, json_data=payload)
        response = requests.post(url, json=payload)
        # No usamos raise_for_status porque queremos parsear el body en caso de error
        return {
            "status": response.status_code,
            "body": response.text,
            "json": response.json() if "application/json" in response.headers.get("Content-Type", "") else None
        }

    def _fetch_clientes_cooperativa(self) -> List[Dict[str, Any]]:
        """
        Obtiene la lista de clientes cuya razon social empiece por 'COOPERATIVA OBRERA'.
        """
        token = self._get_access_token()
        url = f"https://api.finneg.com/api/cliente/list?ACCESS_TOKEN={token}"
        self._log_curl("GET", url)
        response = requests.get(url)
        response.raise_for_status()
        
        clients = response.json()
        # Filtramos por nombre o descripcion
        cooperativas = []
        for c in clients:
            nombre = str(c.get("nombre", "")).upper()
            desc = str(c.get("descripcion", "")).upper()
            # Filtro estricto según pedido del usuario
            if nombre.startswith("COOPERATIVA OBRERA LIMITADA") or desc.startswith("COOPERATIVA OBRERA LIMITADA"):
                cooperativas.append(c)
                
        return cooperativas

    def get_vendedores_mapping(self) -> Dict[str, str]:
        """
        Construye un mapeo rápido de códigos de clientes de la Cooperativa.
        Para evitar lentitud, solo devuelve los códigos. El detalle se obtiene on-demand.
        """
        if self._vendedores_mapping is not None:
            return self._vendedores_mapping

        logger.info("Obteniendo lista de códigos de clientes de la Cooperativa...")
        cooperativas = self._fetch_clientes_cooperativa()
        mapping = {c.get("codigo"): "PENDIENTE" for c in cooperativas}
        self._vendedores_mapping = mapping
        return self._vendedores_mapping

    def get_cliente_data(self, cliente_cod: str) -> Dict[str, Any]:
        """
        Consulta la ficha de un cliente específico para obtener su Nombre y VendedorCodigo.
        """
        if cliente_cod in self._clientes_cache:
            return self._clientes_cache[cliente_cod]

        token = self._get_access_token()
        url = f"https://api.finneg.com/api/cliente/{cliente_cod}?ACCESS_TOKEN={token}"
        try:
            self._log_curl("GET", url)
            res = requests.get(url)
            if res.status_code == 200:
                detail = res.json()
                vendedor = detail.get("VendedorCodigo")
                vendedor_final = vendedor or os.getenv("FINNEGANS_VENDEDOR_COD", "MONTELEONE EDUARDO")
                nombre = detail.get("RazonSocial") or detail.get("Nombre") or detail.get("descripcion") or "N/A"
                
                result = {"vendedor_codigo": vendedor_final, "nombre": nombre}
                self._clientes_cache[cliente_cod] = result
                return result
            else:
                logger.warning(f"No se pudo obtener datos para el cliente {cliente_cod}. Status: {res.status_code}")
        except Exception as e:
            logger.error(f"Error consultando cliente {cliente_cod}: {e}")
        return {"vendedor_codigo": None, "nombre": "N/A"}

    def get_vendedor_cliente(self, cliente_cod: str):
        """
        Compatibilidad hacia atrás: obtiene el vendedor de un cliente.
        """
        data = self.get_cliente_data(cliente_cod)
        return data.get("vendedor_codigo")

    def buscar_solicitudes_por_descripcion(self, descripcion_prefijo: str) -> List[Dict[str, Any]]:
        """
        Busca transacciones (Solicitudes) cuyo NumeroContratoIntermediario o Descripcion empiece con el prefijo dado.
        Usa el reporte de transacciones general o uno específico si existe.
        """
        token = self._get_access_token()
        # Nota: Normalmente se usa un reporte de consulta de transacciones.
        # Asumimos que podemos usar el endpoint de consulta de reporte configurado para buscar estas NCs.
        params = {
            "ACCESS_TOKEN": token,
            "PARAMNumeroContratoIntermediario": f"{descripcion_prefijo}*",
            "PARAMDescripcion": f"{descripcion_prefijo}*",
            "PARAMFechaDesde": "2025-01-01",
            "PARAMFechaHasta": "2050-12-31"
        }
        # Intentamos usar el mismo report_url pero cambiando los parámetros para buscar la solicitud
        self._log_curl("GET", self.report_url, params)
        response = requests.get(self.report_url, params=params)
        response.raise_for_status()
        return response.json()

    def update_vendedor_transaccion(self, transaccion_id: int, vendedor_cod: str) -> Dict[str, Any]:
        """
        Actualiza el vendedor de una transacción existente.
        """
        token = self._get_access_token()
        # Endpoint para actualización (según swagger y convenciones suele ser PUT a la entidad)
        # Para PedidoVentaV3/Solicitud, el ID suele requerirse en el body para actualizaciones
        url = f"{self.create_url}?ACCESS_TOKEN={token}"
        
        payload = {
            "id": transaccion_id,
            "cabecera": {
                "VendedorCodigo": vendedor_cod
            }
        }
        
        # Algunas implementaciones requieren PUT, otras POST para update. 
        # Probamos con PUT que es el estándar de BSA.
        self._log_curl("PUT", url, json_data=payload)
        response = requests.put(url, json=payload)
        return {
            "status": response.status_code,
            "body": response.text,
            "json": response.json() if "application/json" in response.headers.get("Content-Type", "") else None
        }
