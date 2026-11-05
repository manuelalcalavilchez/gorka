"""MDT de 5 m del IGN por WCS: se descarga solo la zona de cada estudio.

Se pide en ASC porque el GeoTIFF del servicio redondea las cotas a metros enteros y falsea las pendientes.
"""
from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from affine import Affine
from rasterio.warp import transform_bounds

from .remotas import URL_MDT, RemotoError, _get

PASO_M = 5.0
LADO_TESELA_M = 5000
HILOS = 4
MARGEN_M = 15  # sin él, el remuestreo deja el borde de la malla sin dato
ETIQUETAS = ("ncols", "nrows", "xllcorner", "yllcorner", "cellsize", "nodata_value")


def _cobertura(crs: str) -> tuple[str, str]:
    return ("Elevacion4083_5", "EPSG:4083") if crs == "EPSG:4083" else ("Elevacion25830_5", "EPSG:25830")


def leer_asc(texto: str) -> tuple[np.ndarray, float, float]:
    """Devuelve (cotas, x izquierda, y inferior). La respuesta llega envuelta en multipart."""
    inicio = texto.find("ncols")
    if inicio < 0:
        raise RemotoError("Respuesta no válida del servicio de MDT.")
    cuerpo = texto[inicio:].split("--wcs")[0].split()
    cabecera, i = {}, 0
    while i + 1 < len(cuerpo) and cuerpo[i].lower() in ETIQUETAS:
        cabecera[cuerpo[i].lower()] = float(cuerpo[i + 1])
        i += 2
    try:
        filas, columnas = int(cabecera["nrows"]), int(cabecera["ncols"])
        z = np.array(cuerpo[i:], dtype="float32").reshape(filas, columnas)
        if abs(cabecera["cellsize"] - PASO_M) > 1e-6:
            raise ValueError
        x0, y0 = cabecera["xllcorner"], cabecera["yllcorner"]
    except (KeyError, ValueError) as e:
        raise RemotoError("Respuesta no válida del servicio de MDT.") from e
    if "nodata_value" in cabecera:
        z[z == cabecera["nodata_value"]] = np.nan
    return z, x0, y0


def _alinear(v: float, hacia) -> float:
    """Los bordes de celda del servicio caen en múltiplos de 5 m más 2,5: así no remuestrea."""
    return hacia((v - PASO_M / 2) / PASO_M) * PASO_M + PASO_M / 2


def descargar(bbox, crs: str) -> tuple[np.ndarray, Affine, str]:
    """Cotas de la zona en el CRS de la cobertura. Lanza RemotoError si falta algún trozo."""
    cobertura, crs_mdt = _cobertura(crs)
    minx, miny, maxx, maxy = transform_bounds(crs, crs_mdt, *bbox, densify_pts=21)
    minx, miny, maxx, maxy = minx - MARGEN_M, miny - MARGEN_M, maxx + MARGEN_M, maxy + MARGEN_M
    minx, miny = _alinear(minx, math.floor), _alinear(miny, math.floor)
    maxx, maxy = _alinear(maxx, math.ceil), _alinear(maxy, math.ceil)
    columnas, filas = round((maxx - minx) / PASO_M), round((maxy - miny) / PASO_M)
    z = np.full((filas, columnas), np.nan, dtype="float32")

    def pedir(t):
        x0, y0, x1, y1 = t
        r = _get(URL_MDT, [("service", "WCS"), ("version", "2.0.1"), ("request", "GetCoverage"), ("coverageid", cobertura),
                           ("format", "application/asc"), ("subset", f"x({x0},{x1})"), ("subset", f"y({y0},{y1})")])
        return leer_asc(r.text)

    xs = np.append(np.arange(minx, maxx, LADO_TESELA_M), maxx)
    ys = np.append(np.arange(miny, maxy, LADO_TESELA_M), maxy)
    teselas = [(float(a), float(b), float(c), float(d)) for a, c in zip(xs, xs[1:]) for b, d in zip(ys, ys[1:])]
    with ThreadPoolExecutor(HILOS) as pool:
        for trozo, x0, y0 in pool.map(pedir, teselas):
            col = round((x0 - minx) / PASO_M)
            fila = round((maxy - y0) / PASO_M) - trozo.shape[0]
            if fila < 0 or col < 0 or fila + trozo.shape[0] > filas or col + trozo.shape[1] > columnas:
                raise RemotoError("El servicio de MDT ha devuelto otra zona.")
            z[fila:fila + trozo.shape[0], col:col + trozo.shape[1]] = trozo
    if not np.isfinite(z).any():
        raise RemotoError("El servicio de MDT no tiene datos en esta zona.")
    return z, Affine(PASO_M, 0, minx, 0, -PASO_M, maxy), crs_mdt
