#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Pipeline de Solicitudes de NC - v2 (CLI, .env, idempotencia, resiliencia, logs).
- Login SHA-256 (password + password2).
- Reutiliza cookie de sesión (Set-Cookie: proveedores_session).
- Itera proveedores -> fija contexto -> lista NC últimos N días o rango --desde/--hasta.
- Descarga PDF, valida content-type + tamaño, guarda en ./espera.
- Extrae texto (PyPDF2; opcional pdfplumber si existe).
- Normaliza importes AR -> float.
- Arma payload y POST a destino (dry-run opcional).
- Mueve a ./Procesados o ./Procesados con Error.
- Log CSV (separador ;) + logging a archivo/console (+ JSON opcional).
- Idempotencia: SQLite (prov, nro, tipo, letra) + omisión si ya está en Procesados.

Requisitos:
- Python 3.9+
- requests, PyPDF2 (instalado); opcional pdfplumber
- python-dotenv (opcional) si usás .env

Autor: Gabriel + M365 Copilot
"""

import os
import re
import sys
import csv
import json
import time
import uuid
import math
import shutil
import sqlite3
import hashlib
import logging
import traceback
import argparse
from logging.handlers import RotatingFileHandler
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional

import requests
from requests.adapters import HTTPAdapter
try:
    from urllib3.util.retry import Retry
except Exception:
    Retry = None

from PyPDF2 import PdfReader

# ====== Opcional: .env ========================================================
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except ImportError:
    # Fallback: leer .env manualmente si no existe python-dotenv
    try:
        if os.path.exists(".env"):
            with open(".env", "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        k, v = line.split("=", 1)
                        # Sobrescribir siempre para priorizar .env local
                        os.environ[k] = v.strip().strip("'").strip('"')
    except Exception:
        pass
except Exception:
    pass  # Si falla algo más, seguimos

# ====== Constantes / Defaults =================================================
DEFAULT_BASE_URL = "https://proveedoresback.cooperativaobrera.coop"
DEFAULT_FRONT_ORIGIN = "https://proveedores.cooperativaobrera.coop"
DEFAULT_REFERER = "https://proveedores.cooperativaobrera.coop/"

TZ = timezone(timedelta(hours=-3))  # AR (-03:00)
DEFAULT_DIAS_ATRAS = 10
DEFAULT_TIMEOUT = 30
DEFAULT_REINTENTOS = 3
DEFAULT_BACKOFF = 0.5
DEFAULT_MIN_PDF_BYTES = 1024  # evita guardar HTML chico o PDFs vacíos

# ====== Directorios de trabajo ================================================
OUT_DIR = os.getenv("OUT_DIR", "./SolicitudNCCoop")
ESPERA_DIR = os.path.join(OUT_DIR, "espera")
OK_DIR = os.path.join(OUT_DIR, "Procesados")
ERROR_DIR = os.path.join(OUT_DIR, "Procesados con Error")
TEXTOS_DIR = os.path.join(OUT_DIR, "textos_extraidos")  # Nuevos: archivos .txt
JSON_DIR = os.path.join(OUT_DIR, "datos_parseados")     # Nuevos: archivos .json
LOGS_DIR = os.path.join(OUT_DIR, "logs")
LOG_CSV = os.path.join(LOGS_DIR, "NC_Log.csv")
POST_DESTINO_TOKEN = os.getenv("POST_DESTINO_TOKEN")  # Bearer token opcional


# ====== Helpers de fechas =====================================================
def hoy_formato() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d")

def desde_formato(dias_atras: int = DEFAULT_DIAS_ATRAS) -> str:
    return (datetime.now(TZ) - timedelta(days=dias_atras)).strftime("%Y-%m-%d")

# ====== Hash ==================================================================
def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

# ====== Números AR -> float ===================================================
_AR_NUM_RE = re.compile(r"[+-]?\d{1,3}(\.\d{3})*,\d{2}|[+-]?\d+\.\d{2}")

def normalizar_importe_ar(s: Optional[str]) -> Optional[float]:
    """
    Convierte '1.234,56' -> 1234.56 ; '1234.56' -> 1234.56 ; None -> None
    """
    if not s:
        return None
    s = s.strip()
    # Si formato AR con coma decimal:
    if "," in s and "." in s:
        # quitar separadores de miles '.', reemplazar ',' por '.'
        s = s.replace(".", "").replace(",", ".")
    elif "," in s and "." not in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None

# ====== PDF extracción ========================================================
def pdf_extract_text(path_pdf: str) -> str:
    """
    Extrae texto con PyPDF2; si existe pdfplumber, lo usamos (suele ser mejor).
    """
    # Intentar pdfplumber si está disponible
    try:
        import pdfplumber  # type: ignore
        texts = []
        with pdfplumber.open(path_pdf) as pdf:
            for page in pdf.pages:
                texts.append(page.extract_text() or "")
        if any(texts):
            return "\n".join(texts)
    except Exception:
        pass

    # Fallback: PyPDF2
    try:
        reader = PdfReader(path_pdf)
        texts = []
        for page in reader.pages:
            t = page.extract_text() or ""
            texts.append(t)
        return "\n".join(texts)
    except Exception as e:
        return f"[PDF_EXTRACT_ERROR] {e}"

def parse_nc_data(text: str) -> Dict[str, Any]:
    """
    Parsea el texto crudo del PDF para extraer:
    - Fecha (del encabezado)
    - Items de la tabla
    """
    data = {}
    
    # 1. Fecha
    # "Fecha [05/02/2026]"
    m_fecha = re.search(r"Fecha\s*\[(\d{2}/\d{2}/\d{4})\]", text)
    if m_fecha:
        data["fecha"] = m_fecha.group(1)

    # 2. Items
    # Regex para fila de tabla:
    # 16.000 RAV D/YEYO... 96100227536 26.114,40 5.484,02 0,00 31.598,42
    # Grupos: 1=Cant, 2=Desc, 3=NP, 4=Neto, 5=IVA, 6=ImpInt, 7=Total
    row_re = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s+(.+?)\s+(\d+)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)\s*$")
    
    items = []
    lines = text.splitlines()
    in_table = False
    
    for line in lines:
        line = line.strip()
        
        # Detectar inicio
        if "Cantidad" in line and "Descripcion" in line and "NP recepcion" in line:
            in_table = True
            continue
        
        # Detectar fin
        if in_table:
            # Si encontramos Neto/Total al inicio, salimos
            if line.startswith("Neto") or line.startswith("Total"):
                break
            
            # Intentar match
            m = row_re.match(line)
            if m:
                # Normalizar valores numéricos
                try:
                    cant = float(normalizar_importe_ar(m.group(1)))
                    neto = float(normalizar_importe_ar(m.group(4)))
                    iva = float(normalizar_importe_ar(m.group(5)))
                    imp_int = float(normalizar_importe_ar(m.group(6)))
                    total = float(normalizar_importe_ar(m.group(7)))
                except:
                    cant = 0.0
                    neto = 0.0
                    iva = 0.0
                    imp_int = 0.0
                    total = 0.0

                items.append({
                    "cantidad": cant,
                    "descripcion": m.group(2).strip(),
                    "np_recepcion": m.group(3),
                    "neto": neto,
                    "iva": iva,
                    "imp_internos": imp_int,
                    "total": total
                })
    
    data["items"] = items
    return data

def safe_filename(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._\-]+", "_", s)


def imprimir_resumen_final(stats: Dict[str, Any], fecha_desde: str, fecha_hasta: str, log_path: str):
    """
    Imprime un resumen detallado del proceso completo.
    """
    sep = "=" * 70
    print(f"\n{sep}")
    print(f"{'RESUMEN FINAL DEL PROCESO':^70}")
    print(f"{sep}")
    print(f"Fecha/Hora: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Ventana procesada: {fecha_desde} → {fecha_hasta}")
    print()
    print("PROVEEDORES:")
    print(f"  • Total: {stats['total_proveedores']}")
    print(f"  • Procesados exitosamente: {stats['proveedores_procesados']}")
    print(f"  • Con errores: {stats['proveedores_error']}")
    print()
    print("NOTAS DE CRÉDITO:")
    print(f"  • Total encontradas: {stats['total_nc_encontradas']}")
    if stats['nc_saltadas'] > 0:
        print(f"  • Ignoradas por filtro: {stats['nc_saltadas']}")
    print(f"  • Procesadas OK: {stats['nc_procesadas_ok']}")
    print(f"  • Con errores: {stats['nc_con_error']}")
    if stats['total_nc_encontradas'] > 0:
        # Tasa sobre las intentadas (encontradas - saltadas)
        intentadas = stats['total_nc_encontradas'] - stats['nc_saltadas']
        if intentadas > 0:
            tasa = (stats['nc_procesadas_ok'] / intentadas) * 100
            print(f"  • Tasa de éxito (sobre procesables): {tasa:.1f}%")
    print()
    
    if stats['detalles_por_proveedor']:
        print("DETALLE POR PROVEEDOR:")
        for prov, det in stats['detalles_por_proveedor'].items():
            print(f"\n  [{prov}]")
            print(f"    - NC encontradas: {det['encontradas']}")
            if det['saltadas'] > 0:
                print(f"    - Saltadas: {det['saltadas']}")
            print(f"    - Procesadas OK: {det['ok']}")
            print(f"    - Con error: {det['error']}")
    
    print()
    print("ARCHIVOS GENERADOS:")
    print(f"  • PDFs procesados: {stats['nc_procesadas_ok']} (en {OK_DIR})")
    print(f"  • PDFs con error: {stats['nc_con_error']} (en {ERROR_DIR})")
    print(f"  • Textos extraídos: {stats['archivos_generados']} (en {TEXTOS_DIR})")
    print(f"  • JSONs parseados: {stats['archivos_generados']} (en {JSON_DIR})")
    print()
    print(f"LOG: {log_path}")
    print(f"{sep}\n")

# ====== Idempotencia: SQLite ==================================================
class ProcessIndex:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._ensure()

    def _ensure(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with sqlite3.connect(self.db_path) as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS procesados (
                    prov TEXT NOT NULL,
                    nro TEXT NOT NULL,
                    tipocomp TEXT NOT NULL,
                    letra TEXT NOT NULL,
                    ts TEXT NOT NULL,
                    PRIMARY KEY (prov, nro, tipocomp, letra)
                )
            """)
            con.commit()

    def exists(self, prov: str, nro: str, tipocomp: str, letra: str) -> bool:
        with sqlite3.connect(self.db_path) as con:
            cur = con.execute("""
                SELECT 1 FROM procesados
                WHERE prov=? AND nro=? AND tipocomp=? AND letra=?
            """, (prov, nro, tipocomp, letra))
            return cur.fetchone() is not None

    def add(self, prov: str, nro: str, tipocomp: str, letra: str):
        with sqlite3.connect(self.db_path) as con:
            con.execute("""
                INSERT OR IGNORE INTO procesados (prov, nro, tipocomp, letra, ts)
                VALUES (?, ?, ?, ?, ?)
            """, (prov, nro, tipocomp, letra, datetime.now(TZ).isoformat()))
            con.commit()

# ====== Logging / CSV =========================================================
def ensure_dirs():
    for d in [ESPERA_DIR, OK_DIR, ERROR_DIR, TEXTOS_DIR, JSON_DIR, LOGS_DIR]:
        os.makedirs(d, exist_ok=True)

def build_logger(log_path: Optional[str], json_logs: bool, run_id: str) -> logging.Logger:
    logger = logging.getLogger("pipeline")
    logger.setLevel(logging.INFO)

    # Limpia handlers previos (si reinvocan en mismo intérprete)
    logger.handlers = []

    formatter = logging.Formatter('%(asctime)s [%(levelname)s] [run_id=%(run_id)s] %(message)s')
    if json_logs:
        class JsonFormatter(logging.Formatter):
            def format(self, record: logging.LogRecord) -> str:
                payload = {
                    "ts": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
                    "level": record.levelname,
                    "run_id": getattr(record, "run_id", run_id),
                    "msg": record.getMessage(),
                    "logger": record.name,
                }
                return json.dumps(payload, ensure_ascii=False)
        formatter = JsonFormatter()

    # Filtro para inyectar run_id
    class RunIdFilter(logging.Filter):
        def filter(self, record):
            record.run_id = run_id
            return True

    # Consola
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(formatter)
    sh.addFilter(RunIdFilter())
    logger.addHandler(sh)

    # Archivo (rotativo) si corresponde
    if log_path:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        fh = RotatingFileHandler(log_path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
        fh.setFormatter(formatter)
        fh.addFilter(RunIdFilter())
        logger.addHandler(fh)

    return logger

def write_log_row_csv(csv_path: str,
                      status: str,
                      prov: Optional[str],
                      nombre_archivo: Optional[str],
                      nro: Optional[str],
                      tipocomp: Optional[str],
                      letra: Optional[str],
                      message: str):
    is_new = not os.path.exists(csv_path)
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        if is_new:
            w.writerow(["timestamp", "status", "prov", "archivo", "nro_comprobante", "tipocomp", "letra", "mensaje"])
        w.writerow([
            datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
            status, prov or "", nombre_archivo or "",
            nro or "", tipocomp or "", letra or "",
            message
        ])

# ====== HTTP / API ============================================================
def build_http_session(common_headers: Dict[str, str], reintentos: int, backoff: float) -> requests.Session:
    s = requests.Session()
    s.headers.update(common_headers)
    if Retry is not None:
        retries = Retry(
            total=reintentos,
            connect=reintentos,
            read=reintentos,
            backoff_factor=backoff,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET", "POST"]),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retries)
        s.mount("https://", adapter)
        s.mount("http://", adapter)
    return s

class ProveedoresClient:
    def __init__(self, session: requests.Session, base_url: str,
                 origin: str, referer: str,
                 username: str, plain_password: str,
                 timeout: int, logger: logging.Logger):
        self.s = session
        self.base_url = base_url.rstrip("/")
        self.origin = origin
        self.referer = referer
        self.username = username
        self.plain_password = plain_password
        self.timeout = timeout
        self.logger = logger

        self.URL_LOGIN = f"{self.base_url}/usuarios/login"
        self.URL_PROV_BASE = f"{self.base_url}/usuarios/proveedor"
        self.URL_SOLICITUDES = f"{self.base_url}/solicitudes_nc"
        self.URL_PDF = f"{self.base_url}/solicitud_nc"

    def _common_headers(self) -> Dict[str, str]:
        return {
            "Accept": "application/json",
            "Origin": self.origin,
            "Referer": self.referer,
            "User-Agent": "Mozilla/5.0 (Automation Script) Python/Requests",
        }

    def login(self) -> Dict[str, Any]:
        password_hash = sha256_hex(self.plain_password)
        headers = {**self._common_headers(), "Content-Type": "application/json; charset=UTF-8"}
        body = {"username": self.username, "password": password_hash, "password2": password_hash}
        r = self.s.post(self.URL_LOGIN, headers=headers, json=body, timeout=self.timeout)
        try:
            r.raise_for_status()
        except requests.exceptions.HTTPError as e:
            self.logger.error(f"Login Fallido. Status: {r.status_code}. Respuesta: {r.text}")
            raise
        js = r.json()
        if not js.get("success"):
            raise RuntimeError(f"Login fallido: {js.get('message')}")
        return js

    def _request_with_relogin(self, method: str, url: str, **kwargs) -> requests.Response:
        # 1er intento
        r = self.s.request(method, url, timeout=self.timeout, **kwargs)
        if r.status_code == 401:
            # re-login y reintento 1 vez
            self.logger.warning("Sesión expirada (401). Reautenticando…")
            self.login()
            r = self.s.request(method, url, timeout=self.timeout, **kwargs)
        r.raise_for_status()
        return r

    def seleccionar_proveedor(self, prov: str) -> Dict[str, Any]:
        url = f"{self.URL_PROV_BASE}/{prov}"
        r = self._request_with_relogin("GET", url, headers=self._common_headers())
        return r.json()

    def listar_solicitudes(self, fecha_desde: str, fecha_hasta: str) -> Dict[str, Any]:
        params = {"fecha_desde": fecha_desde, "fecha_hasta": fecha_hasta}
        r = self._request_with_relogin("GET", self.URL_SOLICITUDES, headers=self._common_headers(), params=params)
        return r.json()

    def descargar_pdf(self, nro: str, tipocomp: str, letra: str) -> bytes:
        params = {"nro_comprobante": str(nro), "tipocomp": str(tipocomp), "letra": str(letra or "")}
        headers = {**self._common_headers(), "Accept": "application/pdf"}
        r = self._request_with_relogin("GET", self.URL_PDF, headers=headers, params=params)
        if "application/pdf" not in r.headers.get("Content-Type", ""):
            raise RuntimeError(f"Contenido no-PDF (Content-Type={r.headers.get('Content-Type')})")
        return r.content

# ====== POST destino (ERP / API) =============================================
def post_destino(url: str, headers: Dict[str, str], payload: Dict[str, Any],
                 timeout: int, dry_run: bool, logger: logging.Logger) -> None:
    if dry_run:
        logger.info(f"[DRY-RUN] No se envía POST. Payload resumen: prov={payload.get('proveedor')}, "
                    f"nro={payload.get('nro_comprobante')}, tipo={payload.get('tipocomp')}, letra={payload.get('letra')}")
        return

    # Soporte para Bearer opcional desde env
    token = POST_DESTINO_TOKEN
    final_headers = dict(headers or {})
    if token and "Authorization" not in final_headers:
        final_headers["Authorization"] = f"Bearer {token}"

    resp = requests.post(url, headers=final_headers, json=payload, timeout=timeout)
    if resp.status_code >= 400:
        raise RuntimeError(f"POST destino status={resp.status_code} body={resp.text[:1000]}")

# ====== Pipeline ==============================================================
def procesar(args: argparse.Namespace):
    run_id = uuid.uuid4().hex[:8]
    
    ensure_dirs()

    # Logger estático inicial (luego podríamos rotarlo si limpiamos logs)
    # Pero build_logger usa LOGS_DIR. Si limpiamos LOGS_DIR abajo, el file handler podría romperse.
    # Estrategia: limpiar PRIMERO, luego configurar logger.
    
    # Si --limpiar, borrar todo ANTES de configurar logger que escribe en archivo
    if args.limpiar:
        for d in [ESPERA_DIR, OK_DIR, ERROR_DIR, TEXTOS_DIR, JSON_DIR, LOGS_DIR]:
            if os.path.exists(d):
                shutil.rmtree(d, ignore_errors=True)
        ensure_dirs()

    # Logger
    logger = build_logger(args.log_file, args.json_logs, run_id)
    logger.info(f"Iniciando pipeline (run_id={run_id})")

    # CSV log
    log_csv = LOG_CSV

    # Headers comunes
    common_headers = {
        "Accept": "application/json",
        "Origin": args.origin,
        "Referer": args.referer,
        "User-Agent": "Mozilla/5.0 (Automation Script) Python/Requests",
    }

    # HTTP session
    session = build_http_session(common_headers, args.reintentos, args.backoff)
    
    # Logs con timestamp en el nombre
    timestamp_str = datetime.now(TZ).strftime("%Y-%m-%d_%H-%M-%S")
    log_csv = os.path.join(LOGS_DIR, f"NC_Log_{timestamp_str}.csv")
    logger.info(f"Log CSV: {log_csv}")
    
    # Parsear filtro de documentos
    doc_filter_list = []
    if args.doc_filter:
        # "123, 456" -> ["123", "456"]
        doc_filter_list = [x.strip() for x in args.doc_filter.split(",") if x.strip()]
        logger.info(f"Filtrando por documentos: {doc_filter_list}")

    # API client
    api = ProveedoresClient(
        session=session,
        base_url=args.base_url,
        origin=args.origin,
        referer=args.referer,
        username=args.username,
        plain_password=args.password,
        timeout=args.timeout,
        logger=logger
    )

    # Índice de procesados
    proc_index = ProcessIndex(db_path=os.path.join(LOGS_DIR, "procesados.db"))

    # Fechas
    fecha_hasta = args.hasta or hoy_formato()
    fecha_desde = args.desde or desde_formato(args.dias_hacia_atras)
    logger.info(f"Ventana de fechas: {fecha_desde} → {fecha_hasta}")

    # Estadísticas del proceso
    # Estadísticas del proceso
    stats = {
        "total_proveedores": 0,
        "proveedores_procesados": 0,
        "proveedores_error": 0,
        "total_nc_encontradas": 0,
        "nc_procesadas_ok": 0,
        "nc_con_error": 0,
        "nc_saltadas": 0,
        "archivos_generados": 0,
        "detalles_por_proveedor": {}
    }

    # Login
    logger.info("Login…")
    login_js = api.login()
    proveedores: List[Dict[str, Any]] = (login_js.get("data") or {}).get("proveedores", [])
    
    stats["total_proveedores"] = len(proveedores)
    logger.info(f"Proveedores encontrados: {len(proveedores)}")

    # Filtrado opcional de proveedor
    if args.solo_prov:
        proveedores = [p for p in proveedores if str(p.get("prov")) == str(args.solo_prov)]
        logger.info(f"Filtrando por prov={args.solo_prov}. Quedan: {len(proveedores)}")

    if not proveedores:
        logger.warning("No se recibieron proveedores (o filtro vacío).")
        return 0

    # Loop proveedores
    for prov_item in proveedores:
        prov = str(prov_item.get("prov"))
        
        # Inicializar contadores del proveedor
        stats["detalles_por_proveedor"][prov] = {
            "encontradas": 0,
            "ok": 0,
            "error": 0,
            "saltadas": 0
        }

        try:
            logger.info(f"\n{'='*70}")
            logger.info(f"[PROVEEDOR {prov}] Iniciando procesamiento...")
            logger.info(f"{'='*70}")

            logger.info(f"[PROVEEDOR {prov}] Seleccionando proveedor…")
            _ = api.seleccionar_proveedor(prov)

            logger.info(f"[PROVEEDOR {prov}] Listando Solicitudes NC…")
            sol_js = api.listar_solicitudes(fecha_desde, fecha_hasta)
            data = sol_js.get("data") or []
            
            stats["total_nc_encontradas"] += len(data)
            stats["detalles_por_proveedor"][prov]["encontradas"] = len(data)

            logger.info(f"[PROVEEDOR {prov}] Solicitudes encontradas: {len(data)}")
            
            if data:
                logger.info(f"[PROVEEDOR {prov}] Registros a procesar:")
                for idx, it in enumerate(data, 1):
                    logger.info(
                        f"  {idx}. NC: {it.get('nro_comprobante')} | "
                        f"Tipo: {it.get('tipocomp')} | "
                        f"Letra: {it.get('letra')} | "
                        f"Importe: {it.get('importe')}"
                    )

            for it in data:
                nro = str(it.get("nro_comprobante"))
                tipocomp = str(it.get("tipocomp"))
                letra = str(it.get("letra") or "")

                # Filtro por documento
                if doc_filter_list and nro not in doc_filter_list:
                    stats["nc_saltadas"] += 1
                    stats["detalles_por_proveedor"][prov]["saltadas"] += 1
                    continue

                nombre_base = safe_filename(f"NC_{prov}_{nro}_{tipocomp}_{letra}")
                nombre_pdf = f"{nombre_base}.pdf"
                nombre_txt = f"{nombre_base}.txt"
                nombre_json = f"{nombre_base}.json"

                path_espera = os.path.join(ESPERA_DIR, nombre_pdf)
                path_ok = os.path.join(OK_DIR, nombre_pdf)
                path_err = os.path.join(ERROR_DIR, nombre_pdf)
                path_txt = os.path.join(TEXTOS_DIR, nombre_txt)
                path_json = os.path.join(JSON_DIR, nombre_json)

                # Idempotencia: si ya procesado o ya está en OK, saltar
                if os.path.exists(path_ok) or proc_index.exists(prov, nro, tipocomp, letra):
                    logger.info(f"[prov={prov}] Ya procesado previamente: {nombre_pdf}")
                    # Considerar si esto debería incrementar 'ok' o 'encontradas' en stats
                    # Por ahora, no modifica stats para evitar doble conteo si ya estaba en OK
                    continue

                try:
                    # Descargar PDF
                    logger.info(f"[prov={prov}] Descargando PDF {nombre_pdf}…")
                    contenido = api.descargar_pdf(nro, tipocomp, letra)
                    if len(contenido) < DEFAULT_MIN_PDF_BYTES:
                        raise RuntimeError(f"PDF demasiado pequeño ({len(contenido)} bytes)")

                    with open(path_espera, "wb") as f:
                        f.write(contenido)

                    # Extraer texto
                    texto = pdf_extract_text(path_espera)
                    
                    # Guardar texto extraído
                    with open(path_txt, "w", encoding="utf-8") as f:
                        f.write(texto)
                    logger.info(f"[PROVEEDOR {prov}] ✓ Texto guardado: {nombre_txt}")

                    extra = parse_nc_data(texto)
                    fecha_comprobante = extra.get("fecha")
                    items = extra.get("items", [])

                    # Normalización de importes principales
                    importe_val = normalizar_importe_ar(str(it.get("importe"))) if it.get("importe") is not None else None
                    iva_val = normalizar_importe_ar(str(it.get("iva"))) if it.get("iva") is not None else None
                    
                    # Guardar JSON parseado
                    datos_json = {
                        "proveedor": prov,
                        "nro_comprobante": nro,
                        "tipocomp": tipocomp,
                        "letra": letra,
                        
                        # Datos unificados
                        "importe": float(importe_val) if importe_val else 0.0,
                        "iva": float(iva_val) if iva_val else 0.0,
                        
                        "fecha_comprobante": fecha_comprobante, # Renombrado de fecha_pdf/fecha
                        "observacion": it.get("observacion"),
                        
                        "archivo_pdf": nombre_pdf,
                        "items": items,
                        
                        "timestamp_procesado": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
                    }
                    
                    with open(path_json, "w", encoding="utf-8") as f:
                        json.dump(datos_json, f, indent=2, ensure_ascii=False)
                    logger.info(f"[PROVEEDOR {prov}] ✓ JSON guardado: {nombre_json}")
                    
                    stats["archivos_generados"] += 1

                    # Payload (ajustar si el destino espera el formato nuevo)
                    # Por ahora mantengo el payload compatible con lo que se enviaba, 
                    # pero agregando items si es necesario. El usuario no especificó payload, solo archivo JSON.
                    # Asumiré que payload debe reflejar JSON.
                    payload = datos_json.copy()

                    # POST al destino


                    # POST destino
                    post_destino(
                        url=args.post_destino_url,
                        headers={"Content-Type": "application/json", "Accept": "application/json"},
                        payload=payload,
                        timeout=args.timeout,
                        dry_run=args.dry_run,
                        logger=logger
                    )

                    # Mover a Procesados + marcar índice
                    if os.path.exists(path_espera):
                        shutil.move(path_espera, path_ok)
                    proc_index.add(prov, nro, tipocomp, letra)
                    write_log_row_csv(log_csv, "OK", prov, nombre_pdf, nro, tipocomp, letra, "Procesado sin errores")
                    stats["nc_procesadas_ok"] += 1
                    stats["detalles_por_proveedor"][prov]["ok"] += 1
                    logger.info(f"[prov={prov}] Procesado OK: {nombre_pdf}")

                except Exception as e_item:
                    # Mover a ERROR y log
                    try:
                        if os.path.exists(path_espera):
                            shutil.move(path_espera, path_err)
                    except Exception:
                        pass
                    write_log_row_csv(log_csv, "ERROR", prov, nombre_pdf, nro, tipocomp, letra, f"Fallo: {e_item}")
                    stats["nc_con_error"] += 1
                    stats["detalles_por_proveedor"][prov]["error"] += 1
                    logger.error(f"[prov={prov}] Error con {nombre_pdf}: {e_item}")
                    continue
            
            # Resumen del proveedor
            det = stats["detalles_por_proveedor"][prov]
            logger.info(f"[PROVEEDOR {prov}] Finalizado - Procesados: {det['ok']}, Errores: {det['error']}")
            logger.info(f"{'='*70}\n")
            stats["proveedores_procesados"] += 1

        except Exception as e_prov:
            logger.error(f"[PROVEEDOR {prov}] Falló el procesamiento del proveedor: {e_prov}")
            write_log_row_csv(log_csv, "ERROR", prov, None, None, None, None, f"Proveedor falló: {e_prov}")
            stats["proveedores_error"] += 1
            continue

    logger.info("Pipeline finalizado.")
    
    # Imprimir resumen final
    imprimir_resumen_final(stats, fecha_desde, fecha_hasta, log_csv)
    return 0

# ====== CLI ===================================================================
def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Pipeline Solicitudes de NC (v2)")

    # Backend / sesión
    p.add_argument("--base-url", default=os.getenv("BASE_URL", DEFAULT_BASE_URL), help="URL base backend")
    p.add_argument("--origin", default=os.getenv("ORIGIN", DEFAULT_FRONT_ORIGIN), help="Header Origin")
    p.add_argument("--referer", default=os.getenv("REFERER", DEFAULT_REFERER), help="Header Referer")
    p.add_argument("--timeout", type=int, default=int(os.getenv("HTTP_TIMEOUT", DEFAULT_TIMEOUT)), help="Timeout HTTP (s)")
    p.add_argument("--reintentos", type=int, default=int(os.getenv("REINTENTOS", DEFAULT_REINTENTOS)), help="Reintentos")
    p.add_argument("--backoff", type=float, default=float(os.getenv("BACKOFF", DEFAULT_BACKOFF)), help="Backoff entre reintentos")

    # Credenciales
    p.add_argument("--username", default=os.getenv("PORTAL_USER"), required=not bool(os.getenv("PORTAL_USER")), help="Usuario (email)")
    p.add_argument("--password", default=os.getenv("PLAIN_PASSWORD"), required=not bool(os.getenv("PLAIN_PASSWORD")), help="Password en claro (se hashea SHA-256)")

    # Ventana de fechas
    p.add_argument("--desde", help="YYYY-MM-DD (inclusive)")
    p.add_argument("--hasta", help="YYYY-MM-DD (inclusive)")
    p.add_argument("--dias-hacia-atras", type=int, default=int(os.getenv("DIAS_HACIA_ATRAS", DEFAULT_DIAS_ATRAS)),
                   help="Si no se especifican fechas, usa hoy-<dias>..hoy")

    # Filtro proveedor y documento
    p.add_argument("--solo-prov", help="Ej: 7150")
    p.add_argument("--doc-filter", help="Lista de comprobantes separados por coma (ej: 27200375198,27200375199)")
    
    # Limpieza
    p.add_argument("--limpiar", action="store_true", help="Limpia directorios de salida y logs antes de iniciar")

    # Destino ERP/API
    p.add_argument("--post-destino-url", default=os.getenv("POST_DESTINO_URL", "https://api.tu-destino.local/ingreso_nc"),
                   help="Endpoint destino")
    p.add_argument("--dry-run", action="store_true", help="No envía POST; solo simula")

    # Salidas / logs
    p.add_argument("--out-dir", default=os.getenv("OUT_DIR", "./SolicitudNCCoop"), help="Directorio raíz de trabajo")
    p.add_argument("--log-file", default=os.getenv("LOG_FILE", "./SolicitudNCCoop/logs/pipeline.log"), help="Archivo de log")
    p.add_argument("--json-logs", action="store_true", help="Formatea logs en JSON")

    return p

def main():
    parser = build_arg_parser()
    args = parser.parse_args()
    try:
        sys.exit(procesar(args))
    except KeyboardInterrupt:
        print("Interrumpido por el usuario.")
        sys.exit(130)
    except Exception as e:
        print(f"Fallo crítico: {e}")
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()