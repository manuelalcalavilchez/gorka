"""Configuración por variables de entorno."""
from __future__ import annotations

import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def _cargar_env(ruta: Path) -> None:
    """Carga variables de un fichero .env sin sobrescribir las ya definidas."""
    if not ruta.exists():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        k, v = linea.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_cargar_env(RAIZ / ".env")


def _ruta(var: str, defecto: Path) -> Path:
    valor = os.environ.get(var)
    return Path(valor).expanduser().resolve() if valor else defecto


GEODATOS_DIR = _ruta("GEODATOS_DIR", RAIZ / "geodatos")
ESTUDIOS_DIR = _ruta("ESTUDIOS_DIR", RAIZ / "estudios")
CONFIG_DIR = _ruta("CONFIG_DIR", RAIZ / "config")

MAX_SUBIDA_MB = int(os.environ.get("MAX_SUBIDA_MB", "50"))
ESTUDIOS_TTL_DIAS = int(os.environ.get("ESTUDIOS_TTL_DIAS", "30"))
MAX_TRABAJOS = int(os.environ.get("MAX_TRABAJOS", "2"))
# Superficie máxima de análisis de terreno (km²) para no agotar memoria
MAX_AREA_TERRENO_KM2 = float(os.environ.get("MAX_AREA_TERRENO_KM2", "60"))
# Celdas máximas del ráster de trabajo (se remuestrea si se supera)
MAX_CELDAS = int(os.environ.get("MAX_CELDAS", "16000000"))

# Servicios WMS oficiales (fondo de mapa en visor y planos)
WMS_PNOA = os.environ.get("WMS_PNOA", "https://www.ign.es/wms-inspire/pnoa-ma")
WMS_PNOA_CAPA = os.environ.get("WMS_PNOA_CAPA", "OI.OrthoimageCoverage")
WMS_IGN_BASE = os.environ.get("WMS_IGN_BASE", "https://www.ign.es/wms-inspire/ign-base")
WMS_IGN_BASE_CAPA = os.environ.get("WMS_IGN_BASE_CAPA", "IGNBaseTodo")
# Descargar fondo PNOA para los planos del PDF (desactivar sin conexión)
PLANOS_CON_FONDO = os.environ.get("PLANOS_CON_FONDO", "1") == "1"

MARCA = "Tecnología Alcalá"
MARCA_WEB = "tecnologiaalcala.com"
PRODUCTO = "Prefactibilidad de movimiento de tierras"
