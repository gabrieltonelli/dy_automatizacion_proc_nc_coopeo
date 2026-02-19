import os
import json
import logging
import argparse
import shutil
from datetime import datetime, timedelta
from dotenv import load_dotenv

from coop_service import CoopPortalService, CoopParser
from finnegans_service import FinnegansService
from repository import MappingRepository
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

def main():
    load_dotenv(override=True)
    
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
    setup_logging()

    # --- CONFIGURACION ---
    BASE_DIR = os.getcwd()
    MAPPINGS_DIR = os.path.join(BASE_DIR, "mappings")
    repo = MappingRepository(
        os.path.join(MAPPINGS_DIR, "productos_coop.csv"),
        os.path.join(MAPPINGS_DIR, "sucursales_coop.csv")
    )

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
                    desc = art.get("descripcion", "").strip()
                    gramaje = art.get("gramaje", 0)
                    und = art.get("descripcion_gramaje", "").strip()
                    desc_compuesta = f"{desc} {gramaje:.2f}{und}"
                    all_descs.add(desc_compuesta)
            
            repo.add_missing_products(list(all_descs))
            logger.info("--- SINCRONIZACION FINALIZADA ---")
        except Exception as e:
            logger.error(f"Error sincronizando catalogo: {e}")
        
        return # Finalizar ejecucion si --sync-catalog esta presente

    # --- CONFIGURACION RESTANTE (Solo si no es sync standalone) ---
    JSON_DIR = os.getenv("JSON_DIR", os.path.join(BASE_DIR, "SolicitudNCCoop", "datos_parseados"))
    SUCCESS_DIR = os.path.join(os.path.dirname(JSON_DIR), "Finnegans_OK")
    ERROR_DIR = os.path.join(os.path.dirname(JSON_DIR), "Finnegans_Error")
    
    # Directorios de Archivo (PDF y TXT) - No se limpian automaticamente
    PDF_DIR = os.getenv("PDF_DIR")
    TEXTOS_DIR = os.getenv("TEXTOS_DIR")
    
    MAPPINGS_DIR = os.path.join(BASE_DIR, "mappings")
    
    if args.limpiar:
        logger.info(f"Limpiando directorios: {JSON_DIR}, {SUCCESS_DIR}, {ERROR_DIR}")
        for d in [JSON_DIR, SUCCESS_DIR, ERROR_DIR]:
            if os.path.exists(d):
                shutil.rmtree(d, ignore_errors=True)
    
    os.makedirs(JSON_DIR, exist_ok=True)
    os.makedirs(SUCCESS_DIR, exist_ok=True)
    os.makedirs(ERROR_DIR, exist_ok=True)
    
    if PDF_DIR: os.makedirs(PDF_DIR, exist_ok=True)
    if TEXTOS_DIR: os.makedirs(TEXTOS_DIR, exist_ok=True)

    # Credenciales Coop
    COOP_USER = os.getenv("PORTAL_USER")
    COOP_PASS = os.getenv("PLAIN_PASSWORD")
    COOP_URL = os.getenv("BASE_URL", "https://proveedoresback.cooperativaobrera.coop")
    
    # Credenciales Finnegans
    FINN_ID = os.getenv("FINNEGANS_CLIENT_ID")
    FINN_SECRET = os.getenv("FINNEGANS_CLIENT_SECRET")
    FINN_EMPRESA = os.getenv("FINNEGANS_EMPRESA_COD", "EMPRE01")

    # Parsear doc-filter
    doc_filter_list = []
    if args.doc_filter:
        doc_filter_list = [x.strip() for x in args.doc_filter.split(",") if x.strip()]

    # --- FASE 1: EXTRACCION (Coop -> JSON) ---
    logger.info("--- INICIANDO FASE 1: EXTRACCION COOP ---")
    coop = CoopPortalService(COOP_USER, COOP_PASS, COOP_URL, os.getenv("ORIGIN", ""), os.getenv("REFERER", ""))
    parser_coop = CoopParser()
    
    try:
        proveedores = coop.login()
        if args.prov:
            proveedores = [p for p in proveedores if str(p.get("prov")) == str(args.prov)]
        
        # Fechas
        fecha_hasta = args.hasta or datetime.now().strftime("%Y-%m-%d")
        fecha_desde = args.desde or (datetime.now() - timedelta(days=args.dias)).strftime("%Y-%m-%d")
        
        logger.info(f"Rango de busqueda: {fecha_desde} hasta {fecha_hasta}")

        total_descargados = 0
        for p_item in proveedores:
            prov_id = str(p_item.get("prov"))
            logger.info(f"Procesando Proveedor {prov_id}...")
            coop.seleccionar_proveedor(prov_id)
            ncs = coop.listar_solicitudes(fecha_desde, fecha_hasta)
            
            for nc in ncs:
                nro = str(nc.get("nro_comprobante"))
                
                # Filtro por documento
                if doc_filter_list and nro not in doc_filter_list:
                    continue

                filename = f"NC_{prov_id}_{nro}.json"
                target_path = os.path.join(JSON_DIR, filename)
                
                if os.path.exists(target_path):
                    logger.info(f"NC {nro} ya existe en {JSON_DIR}. Saltando...")
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
                    total_descargados += 1
                except Exception as e:
                    logger.error(f"Error descargando NC {nro}: {e}")

        logger.info(f"Fase 1 completada. {total_descargados} nuevas NCs descargadas.")

    except Exception as e:
        logger.error(f"Error crítico en Fase 1: {e}")

    # --- FASE 2: INTEGRACION (JSON -> Finnegans) ---
    if args.solo_descarga:
        logger.info("Flag --solo-descarga activo. Saltando Fase 2.")
        return

    logger.info("--- INICIANDO FASE 2: INTEGRACION FINNEGANS ---")
    
    finnegans = FinnegansService(FINN_ID, FINN_SECRET)
    translator = CoopTranslator(repo, finnegans)
    
    processor = FinnegansProcessor(
        finnegans=finnegans,
        translator=translator,
        json_dir=JSON_DIR,
        success_dir=SUCCESS_DIR,
        error_dir=ERROR_DIR
    )
    
    # Le pasamos el dry-run al processor (necesitamos actualizar processor.py para que reciba dry-run si no existia)
    # Por ahora el processor.py no tiene dry-run en su constructor, vamos a actualizarlo tambien.
    processor.dry_run = args.dry_run
    processor.run()
    logger.info("--- PIPELINE FINALIZADO ---")

if __name__ == "__main__":
    main()
