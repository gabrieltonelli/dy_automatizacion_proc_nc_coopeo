import os
import logging
from dotenv import load_dotenv
from finnegans_service import FinnegansService

# Setup minimal logging to see what's happening
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

def test_connection():
    load_dotenv(override=True)
    
    client_id = os.getenv("FINNEGANS_CLIENT_ID")
    client_secret = os.getenv("FINNEGANS_CLIENT_SECRET")
    
    if not client_id or not client_secret:
        logger.error("Faltan FINNEGANS_CLIENT_ID o FINNEGANS_CLIENT_SECRET en el .env")
        return

    logger.info("Probando conexión con Finnegans...")
    service = FinnegansService(client_id, client_secret)
    
    try:
        # 1. Probar obtención de Token
        token = service._get_access_token()
        logger.info(f"✓ Token obtenido correctamente: {token[:10]}...")
        
        # 2. Probar búsqueda de factura (usamos una de ejemplo de las pruebas anteriores)
        # Esto valida que el reporte 'APICONSULTAFACTURAVENTADY' es accesible
        nro_test = "A-00006-00384946"
        logger.info(f"Buscando factura de prueba: {nro_test}...")
        resultado = service.buscar_factura(nro_test)
        
        if resultado:
            logger.info(f"✓ Factura encontrada! Cliente: {resultado[0].get('CLIENTECOD')}")
        else:
            logger.warning("Conexión exitosa, pero no se encontró la factura de prueba (esto es normal si no existe esa factura específica).")
            
        logger.info("==========================================")
        logger.info("EL SERVICIO DE FINNEGANS ESTA FUNCIONANDO")
        logger.info("==========================================")
        
    except Exception as e:
        logger.error(f"✗ Error al conectar con Finnegans: {e}")
        if hasattr(e, 'response') and e.response is not None:
            logger.error(f"Detalle del error: {e.response.text}")

if __name__ == "__main__":
    test_connection()
