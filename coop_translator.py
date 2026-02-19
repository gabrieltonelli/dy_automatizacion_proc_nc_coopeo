import re
import logging
from typing import List, Dict, Any, Optional
from models import NCItem, NCCabecera, NCPayload
from repository import MappingRepository
from finnegans_service import FinnegansService

logger = logging.getLogger(__name__)

class CoopTranslator:
    def __init__(self, repo: MappingRepository, finnegans: FinnegansService):
        self.repo = repo
        self.finnegans = finnegans

    def translate(self, json_data: Dict[str, Any]) -> List[NCPayload]:
        """
        Transforms JSON data from Coop to one or more Finnegans payloads.
        Some Coop documents (like 0272) might generate multiple NCs if they involve multiple branches.
        """
        nro = str(json_data.get("nro_comprobante", ""))
        # Normalizar a 12 digitos para extraer el tipo correctamente (ej: 2710... -> 02710...)
        nro_norm = nro.zfill(12)
        tipo_coop = nro_norm[:4] 
        
        logger.info(f"Translating NC {nro} (Normalized Type: {tipo_coop})")

        if tipo_coop == "0271":
            return [self._translate_0271(json_data)]
        elif tipo_coop in ["0272", "0275"]:
            return self._translate_0272_0275(json_data)
        elif tipo_coop in ["0270", "0274"]:
            return [self._translate_0270_0274(json_data)]
        else:
            logger.warning(f"Tipo Coop {tipo_coop} no reconocido. Usando fallback genérico.")
            return [self._translate_generic(json_data)]

    def _translate_0271(self, data: Dict[str, Any]) -> NCPayload:
        """
        Diferencia de Precio.
        """
        items_coop = data.get("items", [])
        if not items_coop:
            raise ValueError("No items found in JSON")

        item_0 = items_coop[0]
        desc = item_0.get("descripcion", "")
        
        # Extraer factura de referencia
        # Apps Script: comprobanteQOrigina= lineas[8].split('NC solicitada: ')[1];
        # Segun JSON: "Dif de precio A000600384946 suc 390 2026-01-26"
        # Regex para buscar algo como A000600384946 o FC A 00006-00384946
        m_fc = re.search(r'([A-Z]*[A-Z]\s?\d{4,5}-?\d{8,10})', desc)
        fc_ref = m_fc.group(1) if m_fc else None
        
        # Normalizar FC para Finnegans (A-00006-00384946)
        fc_finnegans = self._normalize_fc_for_search(fc_ref) if fc_ref else None
        
        client_cod = "CASA CENTRAL" # Fallback
        factura_id = None
        
        if fc_finnegans:
            result = self.finnegans.buscar_factura(fc_finnegans)
            if result:
                client_cod = result[0].get("CLIENTECOD", client_cod)
                factura_id = result[0].get("IDENTIFICACIONEXTERNA")

        empresa_cod = data.get("empresa_finnegans", "EMPRE01")
        
        cabecera = NCCabecera(
            fecha=self._format_date(data.get("fecha_comprobante")),
            cliente_cod=client_cod,
            descripcion=data.get("nro_comprobante"),
            tipocomp_coop="0271",
            factura_referencia_id=factura_id,
            empresa_cod=empresa_cod
        )

        # En 0271 suele haber un solo item con el total
        items_fin = []
        for it in items_coop:
            items_fin.append(NCItem(
                cantidad=1.0,
                precio_unitario=it.get("neto", 0.0),
                unidad="UN",
                descripcion=it.get("descripcion", ""),
                producto_codigo_finnegans="DIFERENCIA DE PRECIO",
                motivo_devolucion_id="12",
                cantidad_presentacion=1.0,
                neto_linea=it.get("neto", 0.0)
            ))
        
        return NCPayload(cabecera, items_fin)

    def _translate_0272_0275(self, data: Dict[str, Any]) -> List[NCPayload]:
        """
        Devoluciones. Agrupa por sucursal.
        """
        items_coop = data.get("items", [])
        by_client = {} # client_cod -> List[NCItem]

        for it in items_coop:
            ref = it.get("np_recepcion", "")
            # Apps Script logic for branch prefix
            prefix_len = 6 if len(ref) == 12 else 5
            prefix = ref[:prefix_len]
            
            client_cod = self.repo.get_branch_client(prefix) or "17249" # Default Intermediario/CC
            
            # Lookup product
            prod_info = self.repo.get_product(it.get("descripcion"))
            if not prod_info:
                logger.warning(f"Producto no encontrado: {it.get('descripcion')}")
                prod_code = "DESCONOCIDO"
                unit = "UN"
                multiplier = 1.0
            else:
                prod_code = prod_info['code']
                unit = prod_info['unit']
                multiplier = prod_info['multiplier']

            nc_item = NCItem(
                cantidad=it.get("cantidad", 0.0) / multiplier,
                precio_unitario=it.get("neto", 0.0) / it.get("cantidad", 1.0) if it.get("cantidad") else 0.0,
                unidad=unit,
                descripcion=it.get("descripcion", ""),
                producto_codigo_finnegans=prod_code,
                motivo_devolucion_id="16",
                cantidad_presentacion=it.get("cantidad", 0.0),
                neto_linea=it.get("neto", 0.0)
            )
            
            if client_cod not in by_client:
                by_client[client_cod] = []
            by_client[client_cod].append(nc_item)

        empresa_cod = data.get("empresa_finnegans", "EMPRE01")
        payloads = []
        for client, items in by_client.items():
            cab = NCCabecera(
                fecha=self._format_date(data.get("fecha_comprobante")),
                cliente_cod=client,
                descripcion=data.get("nro_comprobante"),
                tipocomp_coop="0272_0275",
                empresa_cod=empresa_cod
            )
            payloads.append(NCPayload(cab, items))
        
        return payloads

    def _translate_0270_0274(self, data: Dict[str, Any]) -> NCPayload:
        """
        Diferencias de Cantidad.
        """
        # Similar a 0271 pero con multiples items
        # Usamos el primer item o la info general para buscar la FC
        items_coop = data.get("items", [])
        
        # En 0270 la FC suele estar en el texto general, pero si no, buscamos en items
        # (Aquí asumimos que el parser previo ya capturó algo o que buscamos en descripcion)
        fc_ref = None
        for it in items_coop:
            desc = it.get("descripcion", "")
            m = re.search(r'([A-Z]*[A-Z]\s?\d{4,5}-?\d{8,10})', desc)
            if m:
                fc_ref = m.group(1)
                break
        
        fc_finnegans = self._normalize_fc_for_search(fc_ref) if fc_ref else None
        client_cod = "17249"
        factura_id = None
        
        if fc_finnegans:
            result = self.finnegans.buscar_factura(fc_finnegans)
            if result:
                client_cod = result[0].get("CLIENTECOD", client_cod)
                factura_id = result[0].get("IDENTIFICACIONEXTERNA")

        empresa_cod = data.get("empresa_finnegans", "EMPRE01")
        cabecera = NCCabecera(
            fecha=self._format_date(data.get("fecha_comprobante")),
            cliente_cod=client_cod,
            descripcion=data.get("nro_comprobante"),
            tipocomp_coop=data.get("nro_comprobante")[:4],
            factura_referencia_id=factura_id,
            empresa_cod=empresa_cod
        )

        items_fin = []
        for it in items_coop:
            prod_info = self.repo.get_product(it.get("descripcion"))
            prod_code = prod_info['code'] if prod_info else "DESCONOCIDO"
            unit = prod_info['unit'] if prod_info else "UN"
            multiplier = prod_info['multiplier'] if prod_info else 1.0

            items_fin.append(NCItem(
                cantidad=it.get("cantidad", 0.0) / multiplier,
                precio_unitario=it.get("neto", 0.0) / it.get("cantidad", 1.0) if it.get("cantidad") else 0.0,
                unidad=unit,
                descripcion=it.get("descripcion", ""),
                producto_codigo_finnegans=prod_code,
                motivo_devolucion_id="14",
                cantidad_presentacion=it.get("cantidad", 0.0),
                neto_linea=it.get("neto", 0.0)
            ))
            
        return NCPayload(cabecera, items_fin)

    def _translate_generic(self, data: Dict[str, Any]) -> NCPayload:
        # Fallback ultra simple
        cab = NCCabecera(
            fecha=self._format_date(data.get("fecha_comprobante")),
            cliente_cod="17249",
            descripcion=data.get("nro_comprobante"),
            tipocomp_coop="GENERIC"
        )
        return NCPayload(cab, [])

    def _normalize_fc_for_search(self, fc_str: str) -> str:
        # Convierte "FC A 00006-00305002" o "A000600305002" -> "A-00006-00305002"
        # Limpiar espacios
        s = fc_str.replace(" ", "").replace("FC", "").replace("FCE", "")
        # Extraer letra y resto
        m = re.match(r'([A-Z])(\d{4,5})(\d{8})', s)
        if m:
            return f"{m.group(1)}-{m.group(2).zfill(5)}-{m.group(3)}"
        return fc_str # Si no matchea, devolver tal cual

    def _format_date(self, date_str: Optional[str]) -> str:
        # Coop: "dd/mm/yyyy" -> Finnegans: "yyyy-mm-dd"
        if not date_str:
            return ""
        try:
            dt = datetime.strptime(date_str, "%d/%m/%Y")
            return dt.strftime("%Y-%m-%d")
        except:
            return date_str
