"""Selección del MDT, malla de trabajo, pendientes y sombreado."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from rasterio.features import geometry_mask
from rasterio.warp import Resampling, reproject, transform_bounds
from scipy import ndimage
from shapely.geometry import box

from . import settings
from .catalogo import FuenteMDT, catalogo

CLASES_PENDIENTE = [(0, 3, "0–3 %"), (3, 10, "3–10 %"), (10, 20, "10–20 %"), (20, 30, "20–30 %"), (30, 1e9, "> 30 %")]


class TerrenoError(RuntimeError):
    pass


@dataclass
class Terreno:
    z: np.ndarray            # cota (m), NaN = sin dato
    transform: Affine
    crs: str
    res: float
    fuente: dict
    fiable: bool
    mascara: np.ndarray      # True = dentro del ámbito
    avisos: list[str] = field(default_factory=list)

    @property
    def area_celda(self) -> float:
        return self.res * self.res

    @property
    def shape(self):
        return self.z.shape

    @property
    def bounds(self):
        h, w = self.z.shape
        x0, y0 = self.transform.c, self.transform.f
        return (x0, y0 - h * self.res, x0 + w * self.res, y0)

    def guardar(self, ruta: Path) -> None:
        np.savez_compressed(ruta, z=self.z, mascara=self.mascara, transform=np.array(self.transform)[:6],
                            crs=self.crs, res=self.res, fiable=self.fiable,
                            fuente=np.array([str(self.fuente)]), avisos=np.array(self.avisos or [""]))

    @classmethod
    def cargar(cls, ruta: Path) -> "Terreno":
        import ast
        d = np.load(ruta, allow_pickle=False)
        t = d["transform"]
        return cls(z=d["z"], transform=Affine(*t), crs=str(d["crs"]), res=float(d["res"]),
                   fuente=ast.literal_eval(str(d["fuente"][0])), fiable=bool(d["fiable"]),
                   mascara=d["mascara"], avisos=[a for a in d["avisos"].tolist() if a])


@lru_cache(maxsize=4096)
def _info_raster(ruta: str, mtime: float):
    with rasterio.open(ruta) as src:
        return src.crs.to_string() if src.crs else None, tuple(src.bounds), src.res


def _cubre(fuente: FuenteMDT, bbox_utm, crs_utm: str) -> tuple[list[Path], float]:
    """Ficheros de la fuente que tocan el bbox y fracción del bbox cubierta."""
    sel, cubierto = [], None
    objetivo = box(*bbox_utm)
    for p in fuente.ficheros():
        try:
            crs_r, b, _ = _info_raster(str(p), p.stat().st_mtime)
        except Exception:  # noqa: BLE001
            continue
        if not crs_r:
            continue
        try:
            bb = transform_bounds(crs_r, crs_utm, *b, densify_pts=21)
        except Exception:  # noqa: BLE001
            continue
        caja = box(*bb)
        if caja.intersects(objetivo):
            sel.append(p)
            cubierto = caja if cubierto is None else cubierto.union(caja)
    frac = (cubierto.intersection(objetivo).area / objetivo.area) if cubierto is not None else 0.0
    return sel, frac


def seleccionar_mdt(bbox_utm, crs_utm: str):
    for fuente in catalogo().mdt:
        ficheros, frac = _cubre(fuente, bbox_utm, crs_utm)
        if ficheros and frac > 0.98:
            return fuente, ficheros
    raise TerrenoError("No hay ningún MDT instalado que cubra el ámbito. Copia hojas MDT05/MDT02 en "
                       f"{settings.GEODATOS_DIR / 'mdt'}.")


def preparar(ambito, margen_m: float = 150.0, geom_analisis=None) -> Terreno:
    """Construye la malla de trabajo en el CRS del ámbito.

    geom_analisis: polígono que define la máscara (por defecto, el ámbito; en viales, el corredor).
    """
    geom = geom_analisis if geom_analisis is not None else ambito.geom
    minx, miny, maxx, maxy = geom.buffer(margen_m).bounds
    area_km2 = (maxx - minx) * (maxy - miny) / 1e6
    if area_km2 > settings.MAX_AREA_TERRENO_KM2:
        raise TerrenoError(f"La zona de análisis ({area_km2:.0f} km²) supera el máximo de "
                           f"{settings.MAX_AREA_TERRENO_KM2:.0f} km².")
    fuente, ficheros = seleccionar_mdt((minx, miny, maxx, maxy), ambito.crs)
    res = fuente.resolucion_m if fuente.resolucion_m <= 25 else 25.0
    celdas = (maxx - minx) * (maxy - miny) / (res * res)
    if celdas > settings.MAX_CELDAS:
        res = math.ceil(math.sqrt((maxx - minx) * (maxy - miny) / settings.MAX_CELDAS))
    minx, miny = math.floor(minx / res) * res, math.floor(miny / res) * res
    maxx, maxy = math.ceil(maxx / res) * res, math.ceil(maxy / res) * res
    w, h = int(round((maxx - minx) / res)), int(round((maxy - miny) / res))
    dst_t = Affine(res, 0, minx, 0, -res, maxy)
    z = np.full((h, w), np.nan, dtype="float32")
    for p in ficheros:
        tmp = np.full((h, w), np.nan, dtype="float32")
        with rasterio.open(p) as src:
            reproject(source=rasterio.band(src, 1), destination=tmp, src_nodata=src.nodata,
                      dst_transform=dst_t, dst_crs=ambito.crs, dst_nodata=np.nan,
                      resampling=Resampling.bilinear)
        ok = np.isfinite(tmp) & np.isnan(z)
        z[ok] = tmp[ok]
    z[(z < -500) | (z > 5000)] = np.nan
    mascara = geometry_mask([geom], out_shape=(h, w), transform=dst_t, invert=True, all_touched=False)
    if mascara.sum() == 0:  # ámbito menor que una celda
        mascara = geometry_mask([geom], out_shape=(h, w), transform=dst_t, invert=True, all_touched=True)
    avisos = []
    fiable = (not fuente.solo_contexto) and fuente.resolucion_m <= catalogo().mdt_resolucion_fiable_m
    if not fiable:
        avisos.append(f"MDT de baja resolución ({fuente.nombre}): pendientes y volúmenes NO son fiables; "
                      "solo sirven como orientación general.")
    vacios = np.isnan(z[mascara]).mean() if mascara.any() else 1
    if vacios > 0.01:
        avisos.append(f"El MDT no tiene dato en el {100 * vacios:.1f} % del ámbito.")
    info = {"id": fuente.id, "nombre": fuente.nombre, "resolucion_m": fuente.resolucion_m,
            "res_trabajo_m": res, "fuente": fuente.fuente,
            "ficheros": [str(p.relative_to(settings.GEODATOS_DIR)) if p.is_relative_to(settings.GEODATOS_DIR)
                         else p.name for p in ficheros]}
    return Terreno(z=z, transform=dst_t, crs=ambito.crs, res=res, fuente=info, fiable=fiable,
                   mascara=mascara, avisos=avisos)


def _rellenar(z: np.ndarray) -> np.ndarray:
    """Rellena NaN con el vecino válido más cercano (para derivadas)."""
    if not np.isnan(z).any():
        return z
    if np.isnan(z).all():
        return np.zeros_like(z)
    idx = ndimage.distance_transform_edt(np.isnan(z), return_distances=False, return_indices=True)
    return z[tuple(idx)]


def pendiente_pct(z: np.ndarray, res: float) -> np.ndarray:
    """Pendiente (%) por el método de Horn."""
    zf = _rellenar(z.astype("float64"))
    kx = np.array([[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]]) / (8 * res)
    ky = np.array([[-1, -2, -1], [0, 0, 0], [1, 2, 1]]) / (8 * res)
    dzdx = ndimage.correlate(zf, kx, mode="nearest")
    dzdy = ndimage.correlate(zf, ky, mode="nearest")
    p = np.hypot(dzdx, dzdy) * 100
    p[np.isnan(z)] = np.nan
    return p.astype("float32")


def sombreado(z: np.ndarray, res: float, azimut: float = 315, altura: float = 45, exagerar: float = 1.0) -> np.ndarray:
    zf = _rellenar(z.astype("float64")) * exagerar
    kx = np.array([[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]]) / (8 * res)
    ky = np.array([[-1, -2, -1], [0, 0, 0], [1, 2, 1]]) / (8 * res)
    dzdx = ndimage.correlate(zf, kx, mode="nearest")
    dzdy = ndimage.correlate(zf, ky, mode="nearest")
    slope = np.arctan(np.hypot(dzdx, dzdy))
    aspect = np.arctan2(dzdy, -dzdx)
    az, alt = math.radians(360 - azimut + 90), math.radians(altura)
    hs = np.sin(alt) * np.cos(slope) + np.cos(alt) * np.sin(slope) * np.cos(az - aspect)
    hs = np.clip(hs, 0, 1)
    hs[np.isnan(z)] = np.nan
    return hs.astype("float32")


def estadisticas(t: Terreno) -> dict:
    m = t.mascara & np.isfinite(t.z)
    if not m.any():
        return {"sin_datos": True}
    zin = t.z[m]
    p = pendiente_pct(t.z, t.res)
    pin = p[m]
    clases = []
    for lo, hi, eti in CLASES_PENDIENTE:
        sel = (pin >= lo) & (pin < hi)
        clases.append({"clase": eti, "area_m2": round(float(sel.sum() * t.area_celda), 1),
                       "pct": round(100 * float(sel.mean()), 1)})
    return {
        "cota_min": round(float(zin.min()), 2), "cota_max": round(float(zin.max()), 2),
        "cota_media": round(float(zin.mean()), 2), "desnivel": round(float(zin.max() - zin.min()), 2),
        "pendiente_media": round(float(np.nanmean(pin)), 1),
        "pendiente_p90": round(float(np.nanpercentile(pin, 90)), 1),
        "pendiente_max": round(float(np.nanmax(pin)), 1),
        "clases_pendiente": clases,
        "area_analizada_m2": round(float(m.sum() * t.area_celda), 1),
    }


def muestrear(t: Terreno, xs: np.ndarray, ys: np.ndarray, rejilla: np.ndarray | None = None) -> np.ndarray:
    """Interpolación bilineal de una rejilla (por defecto la cota) en coordenadas del CRS de trabajo."""
    arr = _rellenar(t.z if rejilla is None else rejilla).astype("float64")
    cols = (np.asarray(xs) - t.transform.c) / t.transform.a
    filas = (np.asarray(ys) - t.transform.f) / t.transform.e
    return ndimage.map_coordinates(arr, [np.asarray(filas) - 0.5, np.asarray(cols) - 0.5], order=1, mode="nearest")
