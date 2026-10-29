"""Cálculo de movimiento de tierras sobre la malla del MDT.

Convenios:
- dif = terreno - diseño  →  dif > 0 desmonte (excavación), dif < 0 terraplén (relleno).
- Taludes en proporción H:V (horizontal por cada metro vertical). 1,5 → 1,5H:1V.
- Volúmenes en banco (sin esponjar), en m³.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy import ndimage

from .terreno import Terreno, _rellenar

MODOS = ("explanacion", "relleno", "extraccion", "vial")

DEFECTOS = {
    "explanacion": {"cota": None, "talud_desmonte": 1.5, "talud_terraplen": 2.0, "pendiente_pct": 0.0,
                    "azimut": 180.0, "esponjamiento": 1.25, "capacidad_camion_m3": 15.0},
    "relleno": {"cota": None, "volumen_objetivo": None, "talud_terraplen": 2.0, "esponjamiento": 1.25,
                "capacidad_camion_m3": 15.0},
    "extraccion": {"cota_fondo": None, "profundidad": 10.0, "talud_interior": 1.0, "esponjamiento": 1.25,
                   "capacidad_camion_m3": 15.0},
    "vial": {"ancho": 6.0, "pendiente_max_pct": 10.0, "talud_desmonte": 1.0, "talud_terraplen": 1.5,
             "paso": None, "suavizado_m": 60.0, "esponjamiento": 1.25, "capacidad_camion_m3": 15.0},
}


class ParametrosError(ValueError):
    pass


def normalizar_parametros(modo: str, params: dict | None) -> dict:
    if modo not in MODOS:
        raise ParametrosError(f"Modo no válido: {modo}")
    p = dict(DEFECTOS[modo])
    for k, v in (params or {}).items():
        if k not in p:
            continue
        if v in ("", None):
            p[k] = None if DEFECTOS[modo][k] is None else DEFECTOS[modo][k]
            continue
        try:
            p[k] = float(v)
        except (TypeError, ValueError) as e:
            raise ParametrosError(f"Valor no numérico en «{k}»: {v}") from e
    for k in ("talud_desmonte", "talud_terraplen", "talud_interior"):
        if k in p and p[k] is not None and not (0.1 <= p[k] <= 10):
            raise ParametrosError(f"«{k}» debe estar entre 0,1 y 10 (H:V).")
    if p.get("esponjamiento") is not None and not (1.0 <= p["esponjamiento"] <= 2.0):
        raise ParametrosError("El esponjamiento debe estar entre 1,0 y 2,0.")
    if modo == "vial":
        if not (2 <= p["ancho"] <= 60):
            raise ParametrosError("El ancho de la plataforma debe estar entre 2 y 60 m.")
        if not (0.5 <= p["pendiente_max_pct"] <= 30):
            raise ParametrosError("La pendiente máxima debe estar entre 0,5 y 30 %.")
    if modo == "extraccion" and p.get("profundidad") is not None and p["profundidad"] <= 0:
        raise ParametrosError("La profundidad debe ser positiva.")
    return p


@dataclass
class ResultadoMalla:
    resumen: dict
    diseno: np.ndarray   # cota de diseño (NaN fuera de la obra)
    dif: np.ndarray      # terreno - diseño (NaN fuera de la obra)


def _geometria(t: Terreno):
    dentro = t.mascara
    d_fuera, idx_fuera = ndimage.distance_transform_edt(~dentro, return_indices=True)
    d_dentro, idx_dentro = ndimage.distance_transform_edt(dentro, return_indices=True)
    return dentro, d_fuera * t.res, idx_fuera, d_dentro * t.res, idx_dentro


def _conexo(sel: np.ndarray, dentro: np.ndarray) -> np.ndarray:
    """Conserva solo las celdas de talud conectadas con la plataforma."""
    lab, _ = ndimage.label(sel | dentro)
    ids = np.unique(lab[dentro])
    ids = ids[ids > 0]
    return np.isin(lab, ids) & sel


def _plataforma(t: Terreno, cota: float, pendiente_pct: float, azimut: float) -> np.ndarray:
    """Superficie de la plataforma: plano por el centroide con la pendiente y el sentido de bajada dados."""
    h, w = t.shape
    filas, cols = np.mgrid[0:h, 0:w]
    x = t.transform.c + (cols + 0.5) * t.res
    y = t.transform.f - (filas + 0.5) * t.res
    if not pendiente_pct:
        return np.full((h, w), cota, dtype="float64")
    yy, xx = np.nonzero(t.mascara)
    xc = t.transform.c + (xx.mean() + 0.5) * t.res
    yc = t.transform.f - (yy.mean() + 0.5) * t.res
    az = math.radians(azimut)  # dirección hacia la que baja la plataforma (0 = norte)
    s = pendiente_pct / 100
    return cota - s * ((x - xc) * math.sin(az) + (y - yc) * math.cos(az))


def _superficie(t: Terreno, geo, plataforma: np.ndarray, talud_d: float | None, talud_t: float | None,
                solo_relleno: bool = False):
    """Diseño completo: plataforma dentro y taludes de desmonte/terraplén fuera."""
    dentro, d_fuera, idx_fuera, _, _ = geo
    z = _rellenar(t.z.astype("float64"))
    base = plataforma[tuple(idx_fuera)]  # cota de plataforma en el borde más cercano
    diseno = np.full(t.shape, np.nan)
    if solo_relleno:
        diseno[dentro] = np.maximum(z[dentro], plataforma[dentro])
    else:
        diseno[dentro] = plataforma[dentro]
    fuera = ~dentro
    if talud_d and not solo_relleno:
        sup = base + d_fuera / talud_d
        sel = fuera & (z > sup)
        sel = _conexo(sel, dentro)
        diseno[sel] = sup[sel]
    if talud_t:
        inf = base - d_fuera / talud_t
        sel = fuera & (z < inf)
        sel = _conexo(sel, dentro)
        diseno[sel] = inf[sel]
    dif = z - diseno
    dif[np.isnan(t.z) & ~dentro] = np.nan
    return diseno, dif


def _resumen(t: Terreno, dif: np.ndarray, p: dict, extra: dict) -> dict:
    a = t.area_celda
    corte = np.nansum(np.where(dif > 0, dif, 0)) * a
    relleno = np.nansum(np.where(dif < 0, -dif, 0)) * a
    ocup = np.isfinite(dif) & ((np.abs(dif) > 0.01) | t.mascara)
    balance = corte - relleno
    esp = p.get("esponjamiento") or 1.0
    excedente = max(balance, 0) * esp
    cam = p.get("capacidad_camion_m3") or 15.0
    return {
        "volumen_desmonte_m3": round(float(corte), 1),
        "volumen_terraplen_m3": round(float(relleno), 1),
        "balance_m3": round(float(balance), 1),
        "excedente_esponjado_m3": round(float(excedente), 1),
        "deficit_m3": round(float(max(-balance, 0)), 1),
        "viajes_camion": int(math.ceil(excedente / cam)) if excedente >= 1 else 0,
        "altura_max_desmonte_m": round(float(np.nanmax(np.where(dif > 0, dif, 0))), 2),
        "altura_max_terraplen_m": round(float(np.nanmax(np.where(dif < 0, -dif, 0))), 2),
        "superficie_ocupada_m2": round(float(ocup.sum() * a), 1),
        "superficie_taludes_m2": round(float((ocup & ~t.mascara).sum() * a), 1),
        **extra,
    }


def _biseccion(f, lo: float, hi: float, iter_: int = 40) -> float:
    flo = f(lo)
    for _ in range(iter_):
        mid = (lo + hi) / 2
        fm = f(mid)
        if (fm > 0) == (flo > 0):
            lo, flo = mid, fm
        else:
            hi = mid
    return (lo + hi) / 2


def explanacion(t: Terreno, p: dict) -> ResultadoMalla:
    geo = _geometria(t)
    zin = t.z[t.mascara & np.isfinite(t.z)]
    if zin.size == 0:
        raise ParametrosError("El MDT no tiene datos dentro del ámbito.")

    def calc(cota):
        plat = _plataforma(t, cota, p["pendiente_pct"], p["azimut"])
        return _superficie(t, geo, plat, p["talud_desmonte"], p["talud_terraplen"])

    compensada = p["cota"] is None
    if compensada:
        def f(c):
            _, d = calc(c)
            return np.nansum(d)  # >0 sobra tierra (subir cota)
        cota = _biseccion(f, float(zin.min()), float(zin.max()))
    else:
        cota = float(p["cota"])
    diseno, dif = calc(cota)
    extra = {"cota_plataforma": round(cota, 2), "cota_compensada": compensada,
             "pendiente_plataforma_pct": p["pendiente_pct"], "azimut_bajada": p["azimut"]}
    return ResultadoMalla(_resumen(t, dif, p, extra), diseno, dif)


def relleno(t: Terreno, p: dict) -> ResultadoMalla:
    geo = _geometria(t)
    zin = t.z[t.mascara & np.isfinite(t.z)]
    if zin.size == 0:
        raise ParametrosError("El MDT no tiene datos dentro del ámbito.")

    def calc(cota):
        plat = np.full(t.shape, cota)
        return _superficie(t, geo, plat, None, p["talud_terraplen"], solo_relleno=True)

    if p.get("volumen_objetivo"):
        objetivo = float(p["volumen_objetivo"])

        def f(c):
            _, d = calc(c)
            return objetivo - np.nansum(np.where(d < 0, -d, 0)) * t.area_celda
        cota = _biseccion(f, float(zin.min()), float(zin.max()) + 200)
    elif p.get("cota") is not None:
        cota = float(p["cota"])
    else:
        cota = float(zin.max())  # por defecto: enrasar con el punto más alto
    diseno, dif = calc(cota)
    capacidad = np.nansum(np.where(dif < 0, -dif, 0)) * t.area_celda
    extra = {"cota_final": round(cota, 2), "capacidad_m3": round(float(capacidad), 1),
             "volumen_objetivo_m3": p.get("volumen_objetivo")}
    r = _resumen(t, dif, p, extra)
    r["excedente_esponjado_m3"] = 0.0
    r["viajes_camion"] = int(math.ceil(capacidad * (p.get("esponjamiento") or 1) / (p.get("capacidad_camion_m3") or 15)))
    r["viajes_camion_aporte"] = r.pop("viajes_camion")
    return ResultadoMalla(r, diseno, dif)


def extraccion(t: Terreno, p: dict) -> ResultadoMalla:
    dentro, _, _, d_dentro, idx_dentro = _geometria(t)
    z = _rellenar(t.z.astype("float64"))
    zin = t.z[dentro & np.isfinite(t.z)]
    if zin.size == 0:
        raise ParametrosError("El MDT no tiene datos dentro del ámbito.")
    if p.get("cota_fondo") is not None:
        fondo = float(p["cota_fondo"])
    else:
        fondo = float(zin.mean()) - float(p["profundidad"])
    z_borde = z[tuple(idx_dentro)]  # cota del terreno en el borde más próximo
    corte_talud = z_borde - d_dentro / p["talud_interior"]
    diseno = np.full(t.shape, np.nan)
    diseno[dentro] = np.minimum(z[dentro], np.maximum(fondo, corte_talud[dentro]))
    dif = z - diseno
    dif[~dentro] = np.nan
    area_fondo = float((dentro & np.isclose(diseno, fondo)).sum() * t.area_celda)
    extra = {"cota_fondo": round(fondo, 2), "profundidad_max_m": round(float(np.nanmax(dif)), 2),
             "superficie_fondo_m2": round(area_fondo, 1),
             "superficie_restauracion_m2": round(float(dentro.sum() * t.area_celda), 1)}
    r = _resumen(t, dif, p, extra)
    r["volumen_extraible_m3"] = r["volumen_desmonte_m3"]
    return ResultadoMalla(r, diseno, dif)


def calcular(modo: str, t: Terreno, p: dict) -> ResultadoMalla:
    if modo == "explanacion":
        return explanacion(t, p)
    if modo == "relleno":
        return relleno(t, p)
    if modo == "extraccion":
        return extraccion(t, p)
    raise ParametrosError(f"Modo sin cálculo de malla: {modo}")
