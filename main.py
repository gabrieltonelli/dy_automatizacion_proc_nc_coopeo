import os
import json
import logging
import argparse
import shutil
from datetime import datetime, timedelta
from dotenv import load_dotenv

from coop_service import CoopPortalService, CoopParser
from finnegans_service import FinnegansService
from repository import MappingRepository, ProcessingHistory
from coop_translator import CoopTranslator
from processor import FinnegansProcessor

# Setup Logging
def setup_logging(log_file="pipeline_ejecucion.log"):
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file, encoding='utf-8')
        ]
    )

logger = logging.getLogger("NC_Pipeline")

def print_summary(stats, fecha_desde, fecha_hasta, pdf_dir, error_dir, json_dir, text_dir, log_file):
    sep = "=" * 70
    print(f"\n{sep}")
    print(f"{'RESUMEN FINAL DEL PROCESO':^70}")
    print(f"{sep}")
    print(f"Fecha/Hora: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Ventana procesada: {fecha_desde} -> {fecha_hasta}")
    print()
    print("PROVEEDORES:")
    print(f"  - Total: {stats['total_prov']}")
    print(f"  - Procesados exitosamente: {stats['prov_ok']}")
    print(f"  - Con errores: {stats['prov_error']}")
    print()
    print("NOTAS DE CRÉDITO:")
    print(f"  - Total encontradas: {stats['nc_total']}")
    print(f"  - Ignoradas por filtro: {stats['nc_skipped']}")
    print(f"  - Procesadas OK: {stats['nc_ok']}")
    print(f"  - Con errores: {stats['nc_error']}")
    
    intentadas = stats['nc_total'] - stats['nc_skipped']
    if intentadas > 0:
        tasa = (stats['nc_ok'] / intentadas) * 100
        print(f"  • Tasa de éxito (sobre procesables): {tasa:.1f}%")
    print()
    
    if stats['detalles']:
        print("DETALLE POR PROVEEDOR:")
        for prov, d in stats['detalles'].items():
            nombre = d.get('nombre', '')
            header = f"{prov} - {nombre}" if nombre else prov
            print(f"\n  [{header}]")
            print(f"    - NC encontradas: {d['encontradas']}")
            print(f"    - Saltadas: {d['saltadas']}")
            print(f"    - Procesadas OK: {d['ok']}")
            print(f"    - Con error: {d['error']}")
    
    print("\nARCHIVOS GENERADOS:")
    print(f"  - PDFs procesados: {stats['nc_ok']} (en {pdf_dir})")
    print(f"  - PDFs con error: {stats['nc_error']} (en {error_dir})")
    print(f"  - Textos extraídos: {stats['nc_ok']} (en {text_dir})")
    print(f"  - JSONs parseados: {stats['nc_ok']} (en {json_dir})")
    print()
    print(f"LOG: {log_file}")
    print(f"{sep}\n")

def main():
    # Cargar .env con manejo robusto de encoding.
    # En servidores Windows el archivo puede estar en ANSI/Latin-1 en lugar de UTF-8.
    try:
        load_dotenv(override=True, encoding='utf-8')
    except UnicodeDecodeError:
        load_dotenv(override=True, encoding='latin-1')
    
    parser = argparse.ArgumentParser(description="Pipeline Unificado de Solicitudes NC (Coop -> Finnegans)")
    
    # Filtros de fecha
    parser.add_argument("--dias", type=int, default=int(os.getenv("DIAS_HACIA_ATRAS", 15)), help="Dias hacia atras para buscar NCs (si no se usan --desde/--hasta)")
    parser.add_argument("--desde", help="Fecha desde (YYYY-MM-DD)")
    parser.add_argument("--hasta", help="Fecha hasta (YYYY-MM-DD)")
    
    # Filtros de proveedor y documento
    parser.add_argument("--prov", help="Filtrar por un solo proveedor")
    parser.add_argument("--doc-filter", help="Lista de comprobantes separados por coma")
    
    # Flags de control
    parser.add_argument("--sync-catalog", action="store_true", help="Sincroniza el catálogo de productos y finaliza")
    parser.add_argument("--limpiar", action="store_true", help="Limpia directorios de salida antes de iniciar")
    parser.add_argument("--dry-run", action="store_true", help="Procesa todo pero NO envia a Finnegans (simulacion)")
    parser.add_argument("--solo-descarga", action="store_true", help="Solo descarga los PDFs y genera JSONs, no procesa con Finnegans")
    
    args = parser.parse_args()
    log_file = "pipeline_ejecucion.log"
    setup_logging(log_file)

    # --- CONFIGURACION ---
    BASE_DIR = os.getcwd()
    MAPPINGS_DIR = os.path.join(BASE_DIR, "mappings")
    repo = MappingRepository(
        os.path.join(MAPPINGS_DIR, "productos_coop.csv"),
        os.path.join(MAPPINGS_DIR, "sucursales_coop.csv")
    )

    # Stats Collector
    stats = {
        "total_prov": 0, "prov_ok": 0, "prov_error": 0,
        "nc_total": 0, "nc_skipped": 0, "nc_ok": 0, "nc_error": 0,
        "detalles": {}
    }

    # Credenciales Coop
    COOP_USER = os.getenv("PORTAL_USER")
    COOP_PASS = os.getenv("PLAIN_PASSWORD")
    COOP_URL = os.getenv("BASE_URL", "https://proveedoresback.cooperativaobrera.coop")
    
    coop = CoopPortalService(COOP_USER, COOP_PASS, COOP_URL, os.getenv("ORIGIN", ""), os.getenv("REFERER", ""))

    # --- FASE 0: SINCRONIZACION DE CATALOGO (Standalone if flag present) ---
    if args.sync_catalog:
        logger.info("--- INICIANDO SINCRONIZACION DE CATALOGO ---")
        try:
            proveedores = coop.login()
            all_descs = set()
            for p_item in proveedores:
                prov_id = str(p_item.get("prov"))
                logger.info(f"Sincronizando productos para Proveedor {prov_id}...")
                coop.seleccionar_proveedor(prov_id)
                articulos = coop.listar_articulos()
                
                for art in articulos:
                    # Formateo: Descripcion + Gramaje (2 decimales) + Unidad
                    # Ejemplo: "TAPA PASC HOJALD COOPERAT 400.00grs"
                    desc_compuesta = f"{art.get('descripcion', '').strip()} {art.get('gramaje', 0):.2f}{art.get('descripcion_gramaje', '').strip()}"
                    all_descs.add(desc_compuesta)
            
            repo.add_missing_products(list(all_descs))
            logger.info("--- SINCRONIZACION FINALIZADA ---")
        except Exception as e:
            logger.error(f"Error sincronizando catalogo: {e}")
        
        return # Finalizar ejecucion si --sync-catalog esta presente

    # --- CONFIGURACION RESTANTE (Solo si no es sync standalone) ---
    BASE_OUTPUT = os.path.join(BASE_DIR, "SolicitudNCCoop")
    
    JSON_DIR = os.getenv("JSON_DIR", os.path.join(BASE_OUTPUT, "datos_parseados"))
    SUCCESS_DIR = os.path.join(BASE_OUTPUT, "Finnegans_OK")
    ERROR_DIR = os.path.join(BASE_OUTPUT, "Finnegans_Error")
    
    # Directorios de Archivo (PDF y TXT) - No se limpian automaticamente
    PDF_DIR = os.getenv("PDF_DIR", os.path.join(BASE_OUTPUT, "PDFs"))
    TEXTOS_DIR = os.getenv("TEXTOS_DIR", os.path.join(BASE_OUTPUT, "textos_extraidos"))
    LOGS_DIR = os.getenv("LOGS_DIR", os.path.join(BASE_OUTPUT, "logs"))
    
    # Historial de Procesados (CSV para idempotencia)
    history = ProcessingHistory(os.path.join(LOGS_DIR, "historial_procesados.csv"))
    
    if args.limpiar:
        logger.info(f"Limpiando directorios temporales: {JSON_DIR}, {SUCCESS_DIR}, {ERROR_DIR}")
        for d in [JSON_DIR, SUCCESS_DIR, ERROR_DIR]:
            if os.path.exists(d):
                shutil.rmtree(d, ignore_errors=True)
    
    for d in [JSON_DIR, SUCCESS_DIR, ERROR_DIR, LOGS_DIR, PDF_DIR, TEXTOS_DIR]:
        if d: os.makedirs(d, exist_ok=True)

    # Credenciales Finnegans
    FINN_ID = os.getenv("FINNEGANS_CLIENT_ID")
    FINN_SECRET = os.getenv("FINNEGANS_CLIENT_SECRET")
    FINN_EMPRESA = os.getenv("FINNEGANS_EMPRESA_COD", "EMPRE01")

    # Parsear doc-filter
    doc_filter_list = [x.strip() for x in args.doc_filter.split(",")] if args.doc_filter else []

    # --- FASE 1: EXTRACCION (Coop -> JSON) ---
    logger.info("--- INICIANDO FASE 1: EXTRACCION COOP ---")
    parser_coop = CoopParser()
    
    # Fechas para el resumen
    fecha_hasta = args.hasta or datetime.now().strftime("%Y-%m-%d")
    fecha_desde = args.desde or (datetime.now() - timedelta(days=args.dias)).strftime("%Y-%m-%d")
    
    try:
        proveedores = coop.login()
        if args.prov:
            proveedores = [p for p in proveedores if str(p.get("prov")) == str(args.prov)]
        
        stats["total_prov"] = len(proveedores)
        
        logger.info(f"Rango de busqueda: {fecha_desde} hasta {fecha_hasta}")

        for p_item in proveedores:
            prov_id = str(p_item.get("prov"))
            prov_nombre = p_item.get("nombre", "")
            stats["detalles"][prov_id] = {"nombre": prov_nombre, "encontradas": 0, "saltadas": 0, "ok": 0, "error": 0}
            try:
                logger.info(f"Procesando Proveedor {prov_id}...")
                coop.seleccionar_proveedor(prov_id)
                ncs = coop.listar_solicitudes(fecha_desde, fecha_hasta)
                
                stats["nc_total"] += len(ncs)
                stats["detalles"][prov_id]["encontradas"] = len(ncs)
                
                for nc in ncs:
                    nro = str(nc.get("nro_comprobante"))
                    
                    # 1. Chequeo de Idempotencia via CSV
                    tipocomp = nc.get("tipocomp", "272")
                    letra = nc.get("letra", "")
                    if history.is_processed(prov_id, nro, tipocomp, letra):
                        logger.info(f"NC {nro} ya procesada según historial CSV. Saltando...")
                        stats["nc_skipped"] += 1
                        stats["detalles"][prov_id]["saltadas"] += 1
                        continue

                    # Filtro por documento
                    if doc_filter_list and nro not in doc_filter_list:
                        stats["nc_skipped"] += 1
                        stats["detalles"][prov_id]["saltadas"] += 1
                        continue

                    filename = f"NC_{prov_id}_{nro}.json"
                    target_path = os.path.join(JSON_DIR, filename)
                    
                    if os.path.exists(target_path):
                        logger.info(f"NC {nro} ya existe en {JSON_DIR}. Saltando...")
                        stats["nc_skipped"] += 1
                        stats["detalles"][prov_id]["saltadas"] += 1
                        continue
                    
                    try:
                        logger.info(f"Descargando NC {nro}...")
                        pdf_bytes = coop.descargar_pdf(nro, nc.get("tipocomp"), nc.get("letra"))
                        text = parser_coop.extract_text_from_pdf(pdf_bytes)
                        parsed_data = parser_coop.parse_text_to_dict(text)
                        
                        final_json = {
                            "proveedor": prov_id,
                            "nro_comprobante": nro,
                            "tipocomp": nc.get("tipocomp"),
                            "letra": nc.get("letra"),
                            "fecha_comprobante": parsed_data["fecha"],
                            "items": parsed_data["items"],
                            "empresa_finnegans": FINN_EMPRESA,
                            "timestamp_extraido": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        }
                        
                        # Guardar PDF si esta configurado
                        if PDF_DIR:
                            pdf_path = os.path.join(PDF_DIR, f"NC_{prov_id}_{nro}.pdf")
                            with open(pdf_path, "wb") as f:
                                f.write(pdf_bytes)
                        
                        # Guardar TXT si esta configurado
                        if TEXTOS_DIR:
                            txt_path = os.path.join(TEXTOS_DIR, f"NC_{prov_id}_{nro}.txt")
                            with open(txt_path, "w", encoding="utf-8") as f:
                                f.write(text)

                        with open(target_path, "w", encoding="utf-8") as f:
                            json.dump(final_json, f, indent=2, ensure_ascii=False)
                        
                        stats["detalles"][prov_id]["ok"] += 1
                    except Exception as e:
                        logger.error(f"Error descargando NC {nro}: {e}")
                        stats["detalles"][prov_id]["error"] += 1
                stats["prov_ok"] += 1
            except Exception as e:
                logger.error(f"Error procesando proveedor {prov_id}: {e}")
                stats["prov_error"] += 1

    except Exception as e:
        logger.error(f"Error crítico en Fase 1: {e}")

    # --- FASE 2: INTEGRACION (JSON -> Finnegans) ---
    if args.solo_descarga:
        logger.info("Flag --solo-descarga activo. Saltando Fase 2.")
    else:
        logger.info("--- INICIANDO FASE 2: INTEGRACION FINNEGANS ---")
        
        # Configuración dinámica para Payloads y Traducciones
        finn_config = {
            # Payloads
            "direccion_entrega": int(os.getenv("FINNEGANS_DIRECCION_ENTREGA", 5)),
            "equipo_solicitante": os.getenv("FINNEGANS_EQUIPO_SOLICITANTE", "SOLICITUDNC"),
            "workflow_codigo": os.getenv("FINNEGANS_WORKFLOW_CODIGO", "VENTAS"),
            "provincia_origen_codigo": os.getenv("FINNEGANS_PROVINCIA_ORIGEN_COD", "BSAS"),
            "tipo_impositivo_id": os.getenv("FINNEGANS_TIPO_IMPOSITIVO_ID", "003"),
            "moneda_codigo": os.getenv("FINNEGANS_MONEDA_CODIGO", "PES"),
            "transaccion_subtipo_codigo": os.getenv("FINNEGANS_SUBTIPO_CODIGO", "SOLICITUDNCAUTO"),
            "transaccion_tipo_codigo": os.getenv("FINNEGANS_TRANSAC_TIPO_CODIGO", "OPER"),
            "deposito_origen_codigo": os.getenv("FINNEGANS_DEPOSITO_ORIGEN_COD", "EXPEDICION ELGUEA ROMAN"),
            "dimension_codigo": os.getenv("FINNEGANS_DIMENSION_CODIGO", "DIMCTC"),
            # Traducciones/Motivos - Tipos operativos (SOLICITUDNCAUTO)
            "motivo_dif_precio": os.getenv("FINNEGANS_MOTIVO_DIF_PRECIO", "12"),
            "motivo_devolucion": os.getenv("FINNEGANS_MOTIVO_DEVOLUCION", "16"),
            "motivo_dif_cantidad": os.getenv("FINNEGANS_MOTIVO_DIF_CANTIDAD", "14"),
            "prod_dif_precio": os.getenv("FINNEGANS_PROD_DIF_PRECIO", "DIFERENCIA DE PRECIO"),
            # Tipos comerciales (0273, 0275) -> SOLICITUDNC
            "transaccion_subtipo_codigo_comercial": os.getenv("FINNEGANS_SUBTIPO_CODIGO_COMERCIAL", "SOLICITUDNC"),
            "motivo_bonificacion": os.getenv("FINNEGANS_MOTIVO_BONIFICACION", "12"),
            "prod_bonificacion": os.getenv("FINNEGANS_PROD_BONIFICACION", "BONIFICACION"),
        }

        finnegans = FinnegansService(FINN_ID, FINN_SECRET)
        translator = CoopTranslator(repo, finnegans, config=finn_config)
        
        # Exclusión de clientes
        excluded_clients_env = os.getenv("EXCLUSION_POR_CLIENTES", "")
        excluded_clients = [x.strip() for x in excluded_clients_env.split(",") if x.strip()]
        if excluded_clients:
            logger.info(f"Clientes excluidos de procesamiento: {excluded_clients}")

        # Reemplazo de clientes (Sobreescritura de código X -> Y)
        client_overwrites = {}
        target_client = os.getenv("CLIENTE_A_REEMPLAZAR")
        replacement_client = os.getenv("CLIENTE_REEMPLAZO")
        
        if target_client and replacement_client:
            client_overwrites[target_client.strip()] = replacement_client.strip()
            logger.info(f"Configurado reemplazo de cliente: {target_client} -> {replacement_client}")
        
        if client_overwrites:
            logger.info(f"Mapeos de reemplazo de clientes activos: {client_overwrites}")

        processor = FinnegansProcessor(
            finnegans=finnegans,
            translator=translator,
            json_dir=JSON_DIR,
            success_dir=SUCCESS_DIR,
            error_dir=ERROR_DIR,
            history=history, # Inyectamos el historial
            excluded_clients=excluded_clients,
            client_overwrites=client_overwrites,
            config=finn_config
        )
        
        processor.dry_run = args.dry_run
        f_stats = processor.run()
        stats["nc_ok"] = f_stats["ok"]
        stats["nc_error"] = f_stats["error"]

    print_summary(stats, fecha_desde, fecha_hasta, PDF_DIR, ERROR_DIR, SUCCESS_DIR, TEXTOS_DIR, log_file)
    logger.info("--- PIPELINE FINALIZADO ---")

if __name__ == "__main__":
    main()
