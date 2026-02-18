# Pipeline de Solicitudes de Nota de Crédito (Coop. Obrera)

Este script automatiza la descarga, procesamiento y envío de Solicitudes de Nota de Crédito (NC) desde el portal de proveedores de la Cooperativa Obrera hacia tu sistema ERP/API.

## 🚀 Funcionalidades

1.  **Login Automático**: Autenticación segura con hash SHA-256.
2.  **Gestión de Sesión**: Reutilización de cookies y manejo de múltiples proveedores.
3.  **Descarga de PDFs**: Obtención automática de comprobantes dentro de un rango de fechas.
4.  **Extracción de Texto**: Conversión de PDF a texto plano (`.txt`) para análisis.
5.  **Parseo Inteligente**: Identificación de metadatos (fechas, importes) y exportación a JSON (`.json`).
6.  **Envío a API**: POST de la información extraída a un endpoint configurable.
7.  **Organización de Archivos**: Clasificación automática en `espera`, `Procesados` y `Procesados con Error`.
8.  **Logging Detallado**: Registro completo de actividad en consola y CSV (`NC_Log.csv`), con resumen final estadístico.

## 📋 Requisitos

-   Python 3.9 o superior
-   Librerías Python:
    ```bash
    pip install requests PyPDF2 python-dotenv
    ```
    *(Nota: `python-dotenv` es opcional; el script incluye un lector manual de `.env` como fallback.)*

## ⚙️ Configuración

Crea un archivo `.env` en el mismo directorio del script con tus credenciales y preferencias:

```env
# Credenciales del Portal de Proveedores
PORTAL_USER=tu_email@ejemplo.com
PLAIN_PASSWORD=tu_password_real

# Configuración del Pipeline
DIAS_HACIA_ATRAS=10       # Ventana de días a procesar por defecto
REINTENTOS=3              # Reintentos HTTP
BACKOFF=0.5               # Espera entre reintentos (segundos)

# Endpoint de Destino (Tu ERP/API)
POST_DESTINO_URL=https://api.tu-sistema.local/ingreso_nc
POST_DESTINO_TOKEN=tu_token_opcional

# Directorio de Salida (Opcional)
OUT_DIR=./SolicitudNCCoop
```

## Procesamiento Incremental

El script mantiene un registro de los comprobantes procesados exitosamente en el archivo:
`SolicitudNCCoop/logs/historial_procesados.csv` (ruta por defecto).

**Funcionamiento:**
1.  **Evita duplicados:** Si un comprobante ya figura en este archivo, el script lo saltará (a menos que uses `--doc-filter`).
2.  **Registro:** Al finalizar correctamente un comprobante, se agrega una nueva línea al archivo.
3.  **Edición Manual:** Si necesitas reprocesar un documento específico, abre este archivo (con Excel o Bloc de Notas) y borra la línea correspondiente.
4.  **Auto-Limpieza:** En cada ejecución, el script elimina automáticamente los registros muy antiguos (basado en `--dias-hacia-atras` * 2) para mantener el archivo ligero.

NO borres este archivo si quieres mantener el historial de lo que ya se hizo. Si lo borras, el script intentará procesar todo de nuevo (respetando la ventana de fechas).

## Uso básico

Ejecutar el script directamente para procesar los últimos `N` días (según `.env`):

```bash
python solicitudes_nc_pipeline.py
```

### Opciones Avanzadas (CLI)

Puedes anular la configuración por defecto usando argumentos:

```bash
# Procesar rango de fechas específico
python solicitudes_nc_pipeline.py --desde 2024-01-01 --hasta 2024-01-31

# Filtrar un solo proveedor
python solicitudes_nc_pipeline.py --solo-prov 12345

# Filtrar documentos específicos (lista separada por comas)
python solicitudes_nc_pipeline.py --doc-filter 27200375198,27200375199

# Limpiar directorios de salida y logs antes de iniciar
python solicitudes_nc_pipeline.py --limpiar

# Simulación (descarga y procesa pero NO envía a la API destino)
python solicitudes_nc_pipeline.py --dry-run

# Solo descargar los archivos PDF (sin procesarlos ni parsearlos)
python solicitudes_nc_pipeline.py --solo-descarga

# Ejemplo completo: Limpiar, filtrar documentos específicos de un proveedor y simular
python solicitudes_nc_pipeline.py --limpiar --doc-filter 27200375198,27200374851 --solo-prov 7150 --dry-run
```

Para ver todas las opciones disponibles:
```bash
python solicitudes_nc_pipeline.py --help
```

## 📂 Estructura de Salida

El script genera automáticamente la siguiente estructura en `OUT_DIR` (por defecto `./SolicitudNCCoop`):

```
SolicitudNCCoop/
├── espera/                 # Archivos temporales durante la descarga
├── Procesados/             # PDFs procesados exitosamente
├── Procesados con Error/   # PDFs que fallaron (descarga, extracción o envío)
├── textos_extraidos/       # Contenido crudo de los PDFs (.txt)
├── datos_parseados/        # Metadatos extraídos en formato manipulable (.json)
└── logs/                   # Historial de ejecuciones (NC_Log.csv) y logs técnicos
```

## 📊 Logs y Monitoreo

-   **Consola**: Muestra el progreso en tiempo real y un **resumen final** con estadísticas de éxito.
-   **NC_Log.csv**: Registro histórico de cada comprobante procesado (timestamp, proveedor, archivo, estado, mensaje).
