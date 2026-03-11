import os
import logging
import json
import requests
from dotenv import load_dotenv
from finnegans_service import FinnegansService

# Configuración de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s'
)
logger = logging.getLogger("CorreccionVendedores")

def corregir_vendedores_solicitudes():
    load_dotenv()
    
    # Inicializar servicio
    client_id = os.getenv("FINNEGANS_CLIENT_ID")
    client_secret = os.getenv("FINNEGANS_CLIENT_SECRET")
    
    if not client_id or not client_secret:
        logger.error("Faltan credenciales de Finnegans en el .env")
        return

    fs = FinnegansService(client_id, client_secret)
    
    # Ya no obtenemos el mapeo completo al inicio para ahorrar tiempo
    # mapping_vendedores = fs.get_vendedores_mapping()
    
    vendedor_defecto = os.getenv("FINNEGANS_VENDEDOR_COD", "MONTELEONE EDUARDO")
    
    solicitudes_reales = []
    
    # Estrategia: Obtener todos los clientes Coop y buscar sus transacciones
    logger.info("Obteniendo lista de sucursales de la Cooperativa Obrera...")
    mapping_vendedores = fs.get_vendedores_mapping() # Esto nos da los clientes que son 'COOPERATIVA OBRERA'
    
    logger.info(f"Buscando solicitudes en {len(mapping_vendedores)} sucursales (rango 01/01 -> Hoy)...")
    
    # Para evitar lentitud, primero intentamos traer el reporte general pero SIN filtros de prefijo
    # y luego validamos cada uno.
    try:
        rangos = [("2026-01-01", "2026-01-31"), ("2026-02-01", "2026-02-28"), ("2026-03-01", "2026-03-09")]
        ids_vistos = set()
        
        for desde, hasta in rangos:
            params = {
                "ACCESS_TOKEN": fs._get_access_token(),
                "PARAMFechaDesde": desde,
                "PARAMFechaHasta": hasta
            }
            # Probamos usar el reporte pero sin el filtro de prefijos para traer TODO lo de la Coop
            fs._log_curl("GET", fs.report_url, params)
            res = requests.get(fs.report_url, params=params).json()
            if res and isinstance(res, list):
                for item in res:
                    cliente_cod = str(item.get("CLIENTECOD") or "").strip()
                    if cliente_cod in mapping_vendedores:
                        tid = item.get("TRANSACCIONID")
                        if tid not in ids_vistos:
                            solicitudes_reales.append(item)
                            ids_vistos.add(tid)
        
        # Estrategia 2: Si el reporte general es insuficiente, buscamos por NumeroContratoIntermediario '*'
        # que a veces trae las solicitudes que el reporte de facturacion ignora
        logger.info("Realizando búsqueda extendida de solicitudes...")
        res_ext = fs.buscar_solicitudes_por_descripcion("*")
        if res_ext and isinstance(res_ext, list):
            for item in res_ext:
                cliente_cod = str(item.get("CLIENTECOD") or "").strip()
                if cliente_cod in mapping_vendedores:
                    tid = item.get("TRANSACCIONID")
                    if tid not in ids_vistos:
                        solicitudes_reales.append(item)
                        ids_vistos.add(tid)
                        
    except Exception as e:
        logger.error(f"Error en búsqueda: {e}")

    logger.info(f"Total de documentos potenciales de la Coop: {len(solicitudes_reales)}")
    
    # 3. Identificar cambios necesarios
    cambios_pendientes = []
    
    # Mapeo de códigos a nombres amigables
    TIPOS_USUARIO = {
        "SOLICITUDNC": "SOLICITUD NOTA DE CRÉDITO",
        "SOLICITUDNCAUTO": "SOLICITUD NOTA DE CRÉDITO AUTOMATICA",
        "SOLICITUDND": "SOLICITUD NOTA DE DÉBITO"
    }

    logger.info("Validando Razón Social y clasificando por Subtipo (SOLICITUDNC, SOLICITUDNCAUTO, SOLICITUDND)...")
    
    for sol in solicitudes_reales:
        cliente_cod = str(sol.get("CLIENTECOD") or "").strip()
        
        # Validación de Razón Social (con caché rápido)
        cliente_info = fs.get_cliente_data(cliente_cod)
        cliente_nombre = cliente_info.get("nombre", "").upper()
        
        if not cliente_nombre.startswith("COOPERATIVA OBRERA LIMITADA"):
            continue

        tid = sol.get("TRANSACCIONID")
        nro_comp = sol.get("IDENTIFICACIONEXTERNA") or sol.get("NumeroDocumento") or "N/A"
        fecha = sol.get("FECHA") or sol.get("Fecha") or "N/A"
        vendedor_reporte = str(sol.get("VENDEDORCOD") or sol.get("VendedorCodigo") or "").strip()
        vendedor_correcto = cliente_info.get("vendedor_codigo")

        # Clasificación basada en los códigos del usuario y prefijos conocidos
        # Intentamos mapear al subtipo correcto
        tipo_final = TIPOS_USUARIO["SOLICITUDNC"]
        
        if any(x in str(nro_comp).upper() for x in ["NCVA", "AUTO"]):
            tipo_final = TIPOS_USUARIO["SOLICITUDNCAUTO"]
        elif any(x in str(nro_comp).upper() for x in ["NDV", "ND"]):
            tipo_final = TIPOS_USUARIO["SOLICITUDND"]
        elif nro_comp == "N/A" and "AUTO" in str(sol.get("DESCRIPCION", "")).upper():
            tipo_final = TIPOS_USUARIO["SOLICITUDNCAUTO"]

        cambios_pendientes.append({
            "id": tid,
            "nro": nro_comp,
            "fecha": fecha,
            "tipo": tipo_final,
            "cliente_cod": cliente_cod,
            "cliente_nom": cliente_nombre,
            "de": vendedor_reporte,
            "a": vendedor_correcto
        })

    # 4. Mostrar resumen y solicitar confirmación
    total_cambios = len(cambios_pendientes)
    if total_cambios == 0:
        logger.info("No se encontraron solicitudes que requieran corrección de vendedor.")
        return

    print("\n" + "="*170)
    print(f"{'RESUMEN DE CORRECCIONES PENDIENTES':^170}")
    print(f"({total_cambios} registros encontrados en el rango 2026-01-01 -> Hoy)")
    print("="*170)
    print(f"{'ID':<8} | {'Fecha':<10} | {'Tipo':<35} | {'Nro Comp':<15} | {'Cliente (Cód - Nombre)':<50} | {'Vend Actual':<25} | {'Vend Nuevo'}")
    print("-" * 170)
    for c in cambios_pendientes:
        cliente_str = f"{c['cliente_cod']} - {c['cliente_nom']}"
        print(f"{c['id']:<8} | {c['fecha']:<10} | {c['tipo']:<35} | {c['nro']:<15} | {cliente_str[:50]:<50} | {str(c['de']):<25} | {c['a']}")
    print("=" * 170)

    confirmacion = input(f"\n¿Desea proceder con la actualización de estos {total_cambios} registros? (s/n): ").strip().lower()
    
    if confirmacion != 's':
        logger.info("Operación cancelada por el usuario.")
        return

    # 5. Ejecutar correcciones
    stats = {"procesadas": total_cambios, "corregidas": 0, "errores": 0}
    
    logger.info(f"Iniciando actualización de {total_cambios} registros...")
    
    for c in cambios_pendientes:
        try:
            logger.info(f"Corrigiendo ID {c['id']} (Nro {c['nro']} - Cliente {c['cliente_cod']}): {c['de']} -> {c['a']}")
            resp = fs.update_vendedor_transaccion(c['id'], c['a'])
            
            if resp['status'] == 200:
                logger.info(f"✓ Éxito al corregir ID {c['id']}")
                stats["corregidas"] += 1
            else:
                logger.error(f"✗ Error al corregir ID {c['id']}: Status {resp['status']} - {resp['body']}")
                stats["errores"] += 1
        except Exception as ex:
            logger.error(f"✗ Excepción al corregir ID {c['id']}: {ex}")
            stats["errores"] += 1

    logger.info("Proceso de corrección finalizado.")
    logger.info(f"Estadísticas: {stats}")


if __name__ == "__main__":
    corregir_vendedores_solicitudes()
