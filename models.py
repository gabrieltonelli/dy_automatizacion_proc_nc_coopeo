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

@dataclass
class NCCabecera:
    fecha: str
    cliente_cod: str
    condicion_pago: str = "30"
    vendedor_cod: str = "MONTELEONE EDUARDO"
    intermediario_cod: str = "17249"
    empresa_cod: str = "EMPRE01"
    identificacion_externa: Optional[str] = None
    descripcion: str = ""
    tipocomp_coop: str = ""
    factura_referencia_id: Optional[str] = None
    lista_precio_cod: str = "3"

@dataclass
class NCPayload:
    cabecera: NCCabecera
    items: List[NCItem] = field(default_factory=list)
