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
                 json_dir: str, success_dir: str, error_dir: str, dry_run: bool = False):
        self.finnegans = finnegans
        self.translator = translator
        self.json_dir = json_dir
        self.success_dir = success_dir
        self.error_dir = error_dir
        self.dry_run = dry_run

    def run(self):
        logger.info(f"Starting Finnegans processing from {self.json_dir}")
        os.makedirs(self.success_dir, exist_ok=True)
        os.makedirs(self.error_dir, exist_ok=True)

        files = [f for f in os.listdir(self.json_dir) if f.endswith(".json")]
        logger.info(f"Found {len(files)} JSON files to process.")

        for filename in files:
            path = os.path.join(self.json_dir, filename)
            try:
                self._process_file(path, filename)
            except Exception as e:
                logger.error(f"Error processing {filename}: {e}", exc_info=True)
                shutil.move(path, os.path.join(self.error_dir, filename))

    def _process_file(self, path: str, filename: str):
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        payloads = self.translator.translate(data)
        
        results = []
        for p in payloads:
            final_json = self._build_finnegans_payload_v3(p)
            logger.info(f"Sending NC {p.cabecera.descripcion} for client {p.cabecera.cliente_cod} to Finnegans...")
            
            if self.dry_run:
                logger.info(f"[DRY-RUN] Simulating sending NC {p.cabecera.descripcion}")
                results.append(True)
                continue

            resp = self.finnegans.create_document(final_json)
            if resp['status'] == 200:
                logger.info(f"✓ Success creating NC in Finnegans. Response: {resp['body'][:100]}")
                results.append(True)
            else:
                logger.error(f"✗ Failed to create NC. Status: {resp['status']}. Body: {resp['body']}")
                results.append(False)

        if all(results):
            shutil.move(path, os.path.join(self.success_dir, filename))
        else:
            shutil.move(path, os.path.join(self.error_dir, filename))

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
            "USR_DireccionEntrega": 5,
            "EquipoSolicitante": "SOLICITUDNC",
            "USR_CantidadPallets": 0,
            "ListaPrecioCodigo": cab.lista_precio_cod,
            "USR_BancoIntermediarioID": None,
            "USR_FirmaDistribuidor": True,
            "WorkflowCodigo": "VENTAS",
            "ProvinciaOrigenCodigo": "BSAS",
            "Fecha": cab.fecha,
            "FchDesdePeriodo": None,
            "FchHastaPeriodo": None,
            "TransaccionAsociadaFCEID": cab.factura_referencia_id,
            "NumeroContratoIntermediario": nro_formateado,
            "ComprobanteTipoImpositivoID": "003",
            "CondicionPagoCodigo": cab.condicion_pago,
            "MonedaCodigo": "PES",
            "EmpresaCodigo": cab.empresa_cod,
            "TransaccionSubtipoCodigo": "SOLICITUDNCAUTO",
            "Descripcion": nro_formateado,
            "VendedorCodigo": cab.vendedor_cod,
            "Cliente": cab.cliente_cod,
            "TransaccionTipoCodigo": "OPER",
            "Intermediario": cab.intermediario_cod,
            "ProvinciaDestinoCodigo": "BSAS",
            "Cotizaciones": [{"MonedaID": "PES", "Cotizacion": 1}, {"MonedaID": "DOL", "Cotizacion": 1}],
            "Items": []
        }

        for item in p.items:
            data["Items"].append({
                "Cantidad2": item.cantidad,
                "USR_DescParticular": 0,
                "PrecioBase": item.precio_unitario - item.iva_unitario,
                "UnidadIDPresentacion": None,
                "CantidadPresentacion": item.cantidad_presentacion,
                "DepositoOrigenCodigo": "EXPEDICION ELGUEA ROMAN",
                "ProductoCodigo": str(item.producto_codigo_finnegans),
                "Descuento1": 0,
                "USRMotivoDevolucionID": item.motivo_devolucion_id,
                "Cantidad": item.cantidad_presentacion,
                "Descripcion": item.descripcion,
                "Precio": item.precio_unitario - item.iva_unitario,
                "DimensionDistribucion": [
                    {
                        "tipoCalculo": "0",
                        "dimensionCodigo": "DIMCTC"
                    }
                ]
            })
        return data
