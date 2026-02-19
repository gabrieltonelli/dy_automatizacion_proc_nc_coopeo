import os
from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime

@dataclass
class NCItem:
    cantidad: float
    precio_unitario: float
    unidad: str
    descripcion: str
    producto_codigo_finnegans: str
    motivo_devolucion_id: str
    cantidad_presentacion: float = 0.0
    neto_linea: float = 0.0
    iva_unitario: float = 0.0

@dataclass
class NCCabecera:
    fecha: str
    cliente_cod: str
    condicion_pago: str = os.getenv("FINNEGANS_CONDICION_PAGO", "30")
    vendedor_cod: str = os.getenv("FINNEGANS_VENDEDOR_COD", "MONTELEONE EDUARDO")
    intermediario_cod: str = os.getenv("FINNEGANS_INTERMEDIARIO_COD", "17249")
    empresa_cod: str = os.getenv("FINNEGANS_EMPRESA_COD", "EMPRE01")
    identificacion_externa: Optional[str] = None
    descripcion: str = ""
    tipocomp_coop: str = ""
    factura_referencia_id: Optional[str] = None
    lista_precio_cod: str = os.getenv("FINNEGANS_LISTA_PRECIO_COD", "3")

@dataclass
class NCPayload:
    cabecera: NCCabecera
    items: List[NCItem] = field(default_factory=list)
