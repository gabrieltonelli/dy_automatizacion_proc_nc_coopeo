import os
import json
import shutil
import logging
from typing import Dict, Any
from models import NCPayload
from finnegans_service import FinnegansService
from coop_translator import CoopTranslator
from repository import MappingRepository

logger = logging.getLogger(__name__)

class FinnegansProcessor:
    def __init__(self, finnegans: FinnegansService, translator: CoopTranslator, 
                 json_dir: str, success_dir: str, error_dir: str, history=None, 
                 dry_run: bool = False, excluded_clients: list = None,
                 client_overwrites: dict = None, config: dict = None):
        self.finnegans = finnegans
        self.translator = translator
        self.json_dir = json_dir
        self.success_dir = success_dir
        self.error_dir = error_dir
        self.history = history
        self.dry_run = dry_run
        self.excluded_clients = excluded_clients or []
        self.client_overwrites = client_overwrites or {}
        self.config = config or {}

    def run(self):
        logger.info(f"Starting Finnegans processing from {self.json_dir}")
        os.makedirs(self.success_dir, exist_ok=True)
        os.makedirs(self.error_dir, exist_ok=True)

        files = [f for f in os.listdir(self.json_dir) if f.endswith(".json")]
        logger.info(f"Found {len(files)} JSON files to process.")

        stats = {"ok": 0, "error": 0}
        for filename in files:
            path = os.path.join(self.json_dir, filename)
            try:
                if self._process_file(path, filename):
                    stats["ok"] += 1
                else:
                    stats["error"] += 1
            except Exception as e:
                logger.error(f"Error processing {filename}: {e}", exc_info=True)
                shutil.move(path, os.path.join(self.error_dir, filename))
                stats["error"] += 1
        return stats

    def _process_file(self, path: str, filename: str):
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        payloads = self.translator.translate(data)
        
        results = []
        for p in payloads:
            # Aplicar reemplazo de código de cliente si existe
            original_client = str(p.cabecera.cliente_cod)
            client_a_reemplazar = os.getenv("CLIENTE_A_REEMPLAZAR", "").strip()
            client_reemplazo = os.getenv("CLIENTE_REEMPLAZO", "").strip()

            # Obtener y asignar el VendedorCodigo correspondiente al cliente original (no reemplazado)
            mapping_vendedores = self.finnegans.get_vendedores_mapping()
            vendedor_asignado = mapping_vendedores.get(original_client)
            if vendedor_asignado:
                logger.info(f"Asignando vendedor {vendedor_asignado} al cliente original {original_client}.")
                p.cabecera.vendedor_cod = vendedor_asignado
            else:
                logger.warning(f"No se encontró vendedor en el mapping para el cliente original {original_client}. Se utilizará el por defecto: {p.cabecera.vendedor_cod}")


            if original_client in self.client_overwrites:
                new_client = self.client_overwrites[original_client]
                logger.info(f"Reemplazando cliente {original_client} por {new_client} según configuración.")
                p.cabecera.cliente_cod = new_client
                
                # Requerimiento: agregar texto a descripción si es el reemplazo específico
                if original_client == client_a_reemplazar and new_client == client_reemplazo:
                    p.cabecera.descripcion_extra = " (deriva de 6253 CDR)"
            
            client_cod = str(p.cabecera.cliente_cod)
            if self.excluded_clients and client_cod in self.excluded_clients:
                logger.info(f"Skipping NC {p.cabecera.descripcion} for client {client_cod} (Found in exclusion list)")
                results.append(True) # Consider skipped as "processed" to avoid blocking the file
                continue

            final_json = self._build_finnegans_payload_v3(p)
            
            # DEBUG: Guardar payload enviado para inspeccionar
            debug_path = path + f".{client_cod}.sent"
            with open(debug_path, "w", encoding="utf-8") as f:
                json.dump(final_json, f, indent=2)

            logger.info(f"Sending NC {p.cabecera.descripcion} for client {client_cod} to Finnegans...")
            #logger.info(f"--->final_json: {final_json}")
            if self.dry_run:
                logger.info(f"[DRY-RUN] Simulating sending NC {p.cabecera.descripcion}")
                results.append(True)
                continue

            resp = self.finnegans.create_document(final_json)
            if resp['status'] == 200:
                logger.info(f"✓ Success creating NC in Finnegans. Response: {resp['body'][:100]}")
                results.append(True)
                
                # Registrar en historial CSV si tenemos el objeto
                if self.history:
                    # Intentar extraer el prov_id del nombre del archivo (NC_PROV_NRO.json)
                    try:
                        parts = filename.replace(".json", "").split("_")
                        prov_id = parts[1] if len(parts) >= 2 else "0"
                        # Extraer tipo y letra del payload cargado arriba (data)
                        self.history.add(prov_id, p.cabecera.descripcion, p.cabecera.fecha, data.get("tipocomp", "272"), data.get("letra", ""))
                    except Exception as he:
                        logger.warning(f"No se pudo registrar en historial: {he}")
            else:
                logger.error(f"✗ Failed to create NC. Status: {resp['status']}. Body: {resp['body']}")
                results.append(False)

        if all(results):
            shutil.move(path, os.path.join(self.success_dir, filename))
            return True
        else:
            shutil.move(path, os.path.join(self.error_dir, filename))
            return False

    @staticmethod
    def _format_nro_comprobante(nro: str) -> str:
        """
        Formatea el número de comprobante al estilo 'TIPO-NUMERO'.
        Ejemplo: '27200375198' -> '0272-00375198'
        """
        nro_norm = str(nro).zfill(12)
        tipo = nro_norm[:4]
        numero = nro_norm[4:]
        return f"{tipo}-{numero}"

    # Mapeo de tipos especiales a la clave de config que determina su subtipo de transacción.
    # Los tipos no presentes aquí usan el default operativo (SOLICITUDNCAUTO).
    _SUBTIPO_POR_TIPO = {
        "0273": ("transaccion_subtipo_codigo_comercial", "SOLICITUDNC"),   # Quita por bonificaciones (siempre comercial)
    }

    @staticmethod
    def _subtipo_transaccion(tipocomp_coop: str, config: dict) -> str:
        """
        Retorna el TransaccionSubtipoCodigo según el tipo de comprobante Coop.
        - Si el tipo termina en _AJUSTE (ej: 0275_AJUSTE): usa SOLICITUDND
        - 0273:                   SOLICITUDNC        (quita comercial)
        - Resto (0270, 0271, 0272, 0274, 0275): SOLICITUDNCAUTO  (operativo)
        """
        # Caso especial para tipos que vienen marcados como AJUSTE desde el translator
        if tipocomp_coop.endswith("_AJUSTE"):
            return config.get("transaccion_subtipo_codigo_ajuste_snd", "SOLICITUDND")

        # Mapeo explícito para otros tipos especiales
        entry = FinnegansProcessor._SUBTIPO_POR_TIPO.get(tipocomp_coop)
        if entry:
            config_key, default = entry
            return config.get(config_key, default)
            
        return config.get("transaccion_subtipo_codigo", "SOLICITUDNCAUTO")

    def _build_finnegans_payload_v3(self, p: NCPayload) -> Dict[str, Any]:
        """
        Builds the complex JSON expected by Finnegans API (v3).
        """
        cab = p.cabecera
        nro_formateado = self._format_nro_comprobante(cab.descripcion)
        data = {
            "Transaccionid": 1,
            "Nombre": None,
            "NumeroInterno": None,
            "Identificacion": None,
            "USR_PesoPallet": 0,
            "NumeroComprobante": None,
            "IdentificacionExterna": cab.identificacion_externa,
            "USR_DireccionEntrega": self.config.get("direccion_entrega", 5),
            "EquipoSolicitante": self.config.get("equipo_solicitante", "SOLICITUDNC"),
            "USR_CantidadPallets": 0,
            "ListaPrecioCodigo": cab.lista_precio_cod,
            "USR_BancoIntermediarioID": None,
            "USR_FirmaDistribuidor": True,
            "WorkflowCodigo": self.config.get("workflow_codigo", "VENTAS"),
            "ProvinciaOrigenCodigo": self.config.get("provincia_origen_codigo", "BSAS"),
            "Fecha": cab.fecha,
            "FchDesdePeriodo": None,
            "FchHastaPeriodo": None,
            "TransaccionAsociadaFCEID": cab.factura_referencia_id,
            "NumeroContratoIntermediario": nro_formateado,
            "ComprobanteTipoImpositivoID": self.config.get("tipo_impositivo_id", "003"),
            "CondicionPagoCodigo": cab.condicion_pago,
            "MonedaCodigo": self.config.get("moneda_codigo", "PES"),
            "EmpresaCodigo": cab.empresa_cod,
            "TransaccionSubtipoCodigo": self._subtipo_transaccion(cab.tipocomp_coop, self.config),
            "Descripcion": f"{nro_formateado}{cab.descripcion_extra}",
            "VendedorCodigo": cab.vendedor_cod,
            "Cliente": cab.cliente_cod,
            "TransaccionTipoCodigo": self.config.get("transaccion_tipo_codigo", "OPER"),
            "Intermediario": cab.intermediario_cod,
            "ProvinciaDestinoCodigo": "BSAS",
            "Cotizaciones": [{"MonedaID": "PES", "Cotizacion": 1}, {"MonedaID": "DOL", "Cotizacion": 1}],
            "Items": []
        }

        for item in p.items:
            data["Items"].append({
                "Cantidad2": item.cantidad,
                "USR_DescParticular": 0,
                "PrecioBase": item.precio_unitario,
                "UnidadIDPresentacion": None,
                "CantidadPresentacion": None,
                "DepositoOrigenCodigo": self.config.get("deposito_origen_codigo", "EXPEDICION ELGUEA ROMAN"),
                "ProductoCodigo": str(item.producto_codigo_finnegans),
                "Descuento1": 0,
                "USRMotivoDevolucionID": item.motivo_devolucion_id,
                "Cantidad": item.cantidad,
                "Descripcion": item.descripcion,
                "Precio": item.precio_unitario,
                "DimensionDistribucion": [
                    {
                        "tipoCalculo": "0",
                        "dimensionCodigo": self.config.get("dimension_codigo", "DIMCTC")
                    }
                ]
            })
        return data
