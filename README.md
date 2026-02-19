# Pipeline de Notas de Crédito (Coop -> Finnegans)

Este proyecto automatiza la descarga de Solicitudes de Notas de Crédito (NC) desde el portal de proveedores de Cooperativa Obrera y su posterior carga en el ERP Finnegans.

## Arquitectura Modular

El sistema está dividido en dos grandes fases coordinadas por un único punto de entrada (`main.py`):

1.  **Fase 1: Extracción (Coop Service)**
    *   Se conecta al portal de la Coop.
    *   Descarga los PDFs de las NCs de los últimos N días o rango específico.
    *   Extrae el texto y genera archivos JSON en `SolicitudNCCoop/datos_parseados`.
2.  **Fase 2: Integración (Finnegans Processor)**
    *   Lee los JSONs generados.
    *   Traduce los datos al formato de Finnegans usando reglas de negocio (0271, 0272, etc.).
    *   Busca facturas de referencia en Finnegans para aplicar la NC correctamente.
    *   Carga el documento final en Finnegans. (Soporta simulación con `--dry-run`).

## Requisitos

*   Python 3.9+
*   Dependencias: `pip install -r requirements.txt`

## Configuración (.env)

Asegúrate de tener un archivo `.env` con las siguientes claves:

```env
# Credenciales Portal Cooperativa Obrera
PORTAL_USER=usuario@ejemplo.com.ar
PLAIN_PASSWORD=tu_password_aqui

# Credenciales API Finnegans
FINNEGANS_CLIENT_ID=tu_client_id
FINNEGANS_CLIENT_SECRET=tu_client_secret
FINNEGANS_EMPRESA_COD=EMPRE01  # <--- Setea aquí la empresa destino

# Configuración de Red / API
BASE_URL=https://proveedoresback.cooperativaobrera.coop
HTTP_TIMEOUT=30
REINTENTOS=3
BACKOFF=0.5

# Parámetros de Proceso
DIAS_HACIA_ATRAS=15
```

## Mapeos (CSV)

Mantén actualizados los archivos en `mappings/`:
*   `productos_coop.csv`: Relaciona la descripción de la Coop con el código de Finnegans.
*   `sucursales_coop.csv`: Mapea el prefijo de recepción al código de cliente Finnegans.

## Uso y Parametrización

Para ejecutar el flujo completo con diversas opciones:

```bash
# Procesar rango de fechas específico
python main.py --desde 2024-01-01 --hasta 2024-01-31

# Filtrar un solo proveedor
python main.py --prov 12345

# Filtrar documentos específicos (lista separada por comas)
python main.py --doc-filter 27200375198,27200375199

# Limpiar directorios de salida antes de iniciar
python main.py --limpiar

# Simulación (descargar y procesa pero NO envía a Finnegans)
python main.py --dry-run

# Solo descargar los archivos PDF (sin procesar con Finnegans)
python main.py --solo-descarga

# Ejemplo completo: Limpiar, filtrar proveedor y simular
python main.py --limpiar --solo-prov 7150 --dry-run
```

## Pruebas de Conexión (Finnegans)

Para validar únicamente la conectividad con la API de Finnegans sin procesar datos de la Cooperativa, puedes utilizar el script de diagnóstico:

```bash
python test_finnegans.py
```

Este script verificará:
1.  Obtención de un Token de acceso válido.
2.  Acceso al reporte de facturas (`APICONSULTAFACTURAVENTADY`).
3.  Búsqueda de una factura de muestra para confirmar permisos de lectura.

## Logs y Trazabilidad

El sistema genera trazabilidad detallada para auditoría y resolución de errores:

*   **`pipeline_ejecucion.log`**: Contiene el detalle paso a paso de lo que está ocurriendo (Log de Consola + Archivo). Aquí verás si un PDF no pudo parsearse o si la API de Finnegans devolvió un error.
*   **Directorios de Estado**:
    *   `SolicitudNCCoop/datos_parseados`: JSONs recién extraídos pendientes de envío.
    *   `SolicitudNCCoop/Finnegans_OK`: JSONs que fueron creados exitosamente en Finnegans.
    *   `SolicitudNCCoop/Finnegans_Error`: JSONs que fallaron durante la fase de integración.

## Extensibilidad (La Anónima, etc.)

La arquitectura actual está preparada para crecer:
1.  **Nuevos Portales:** Para agregar "La Anónima", basta con crear un `anonima_service.py` que herede/imite a `coop_service.py`.
2.  **Nuevos Traductores:** Si La Anónima tiene un formato distinto, se crea un `anonima_translator.py` para mapear sus campos a los `models.py` universales.
3.  **Mismo Destino:** Ambos usarán el mismo `FinnegansService` y `FinnegansProcessor`.
