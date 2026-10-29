"""Modo vial/camino: perfil longitudinal, rasante, secciones y diagrama de masas."""
from __future__ import annotations

import math

import numpy as np
from shapely.geometry import LineString, Polygon

from .terreno import Terreno, muestrear
from .volumenes import ParametrosError


def _media_movil(v: np.ndarray, n: int) -> np.ndarray:
    if n <= 1:
        return v.copy()
    pad = n // 2
    ext = np.pad(v, pad, mode="edge")
    k = np.ones(n) / n
    return np.convolve(ext, k, mode="valid")[: len(v)]


def rasante(zt: np.ndarray, ds: float, pendiente_max: float, ventana: int) -> np.ndarray:
    """Rasante cercana al terreno que respeta la pendiente máxima."""
    z = _media_movil(zt, ventana)
    z[0], z[-1] = zt[0], zt[-1]
    g = pendiente_max * ds
    for _ in range(3):
        for i in range(1, len(z)):
            z[i] = min(max(z[i], z[i - 1] - g), z[i - 1] + g)
        for i in range(len(z) - 2, -1, -1):
            z[i] = min(max(z[i], z[i + 1] - g), z[i + 1] + g)
    return z


def _seccion(h: np.ndarray, b: float, nd: float, nt: float):
    corte = np.where(h > 0, h * (b + nd * h), 0.0)
    terr = np.where(h < 0, -h * (b + nt * -h), 0.0)
    ancho_ocup = b + 2 * np.where(h > 0, nd * h, nt * -h)
    return corte, terr, ancho_ocup


def corredor(eje: LineString, s: np.ndarray, anchos: np.ndarray) -> Polygon:
    """Polígono de ocupación con ancho variable a lo largo del eje."""
    izq, der = [], []
    for si, w in zip(s, anchos):
        p0 = eje.interpolate(max(si - 0.5, 0))
        p1 = eje.interpolate(min(si + 0.5, eje.length))
        dx, dy = p1.x - p0.x, p1.y - p0.y
        n = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / n, dx / n
        p = eje.interpolate(si)
        izq.append((p.x + nx * w / 2, p.y + ny * w / 2))
        der.append((p.x - nx * w / 2, p.y - ny * w / 2))
    poly = Polygon(izq + der[::-1]).buffer(0)
    return poly.union(eje.buffer(float(anchos.min()) / 2))


def calcular(t: Terreno, eje: LineString, p: dict) -> dict:
    L = eje.length
    paso = p.get("paso") or max(t.res, 5.0)
    paso = max(paso, t.res / 2)
    n = max(int(math.ceil(L / paso)), 2)
    s = np.linspace(0, L, n + 1)
    ds = L / n
    pts = [eje.interpolate(si) for si in s]
    xs, ys = np.array([q.x for q in pts]), np.array([q.y for q in pts])
    zt = muestrear(t, xs, ys)
    if not np.isfinite(zt).all():
        raise ParametrosError("El MDT no tiene datos a lo largo de todo el eje.")
    ventana = max(int(round((p.get("suavizado_m") or 60) / ds)) | 1, 1)
    gmax = p["pendiente_max_pct"] / 100
    zr = rasante(zt, ds, gmax, ventana)
    h = zt - zr
    corte_a, terr_a, anchos = _seccion(h, p["ancho"], p["talud_desmonte"], p["talud_terraplen"])
    vol_corte_seg = (corte_a[:-1] + corte_a[1:]) / 2 * ds
    vol_terr_seg = (terr_a[:-1] + terr_a[1:]) / 2 * ds
    masa = np.concatenate([[0], np.cumsum(vol_corte_seg - vol_terr_seg)])
    pend = np.diff(zr) / ds * 100
    pend_terreno = np.diff(zt) / ds * 100

    # Tramos de 100 m
    tramos = []
    lim = np.arange(0, L + 100, 100)
    for a, b in zip(lim[:-1], lim[1:]):
        sel = (s[:-1] >= a) & (s[:-1] < min(b, L))
        if not sel.any():
            continue
        tramos.append({"pk_ini": round(float(a), 0), "pk_fin": round(float(min(b, L)), 0),
                       "desmonte_m3": round(float(vol_corte_seg[sel].sum()), 1),
                       "terraplen_m3": round(float(vol_terr_seg[sel].sum()), 1),
                       "pendiente_media_pct": round(float(np.mean(np.abs(pend[sel]))), 2),
                       "altura_max_m": round(float(np.max(np.abs(h[:-1][sel]))), 2)})

    poli = corredor(eje, s, anchos)
    corte, terr = float(vol_corte_seg.sum()), float(vol_terr_seg.sum())
    balance = corte - terr
    esp = p.get("esponjamiento") or 1.0
    exc = max(balance, 0) * esp
    cam = p.get("capacidad_camion_m3") or 15.0
    avisos = []
    if np.max(np.abs(pend)) > p["pendiente_max_pct"] + 0.05:
        avisos.append("No se ha podido respetar la pendiente máxima en todo el trazado con los extremos "
                      "anclados al terreno.")
    resumen = {
        "longitud_m": round(L, 1), "ancho_plataforma_m": p["ancho"],
        "volumen_desmonte_m3": round(corte, 1), "volumen_terraplen_m3": round(terr, 1),
        "balance_m3": round(balance, 1), "excedente_esponjado_m3": round(exc, 1),
        "deficit_m3": round(max(-balance, 0), 1),
        "viajes_camion": int(math.ceil(exc / cam)) if exc >= 1 else 0,
        "pendiente_max_rasante_pct": round(float(np.max(np.abs(pend))), 2),
        "pendiente_max_terreno_pct": round(float(np.max(np.abs(pend_terreno))), 2),
        "altura_max_desmonte_m": round(float(max(h.max(), 0)), 2),
        "altura_max_terraplen_m": round(float(max(-h.min(), 0)), 2),
        "superficie_ocupada_m2": round(float(poli.area), 1),
        "ancho_ocupacion_max_m": round(float(anchos.max()), 1),
    }
    # Perfil reducido para el visor (máx. 1500 puntos)
    k = max(1, len(s) // 1500)
    perfil = {"pk": np.round(s[::k], 1).tolist(), "terreno": np.round(zt[::k], 2).tolist(),
              "rasante": np.round(zr[::k], 2).tolist(), "masa": np.round(masa[::k], 1).tolist()}
    # Tramos coloreados del eje (desmonte / terraplén) para el visor
    segmentos = []
    for i in range(0, len(s) - 1, max(1, len(s) // 400)):
        j = min(i + max(1, len(s) // 400), len(s) - 1)
        hm = float(np.mean(h[i:j + 1]))
        segmentos.append({"coords": [(xs[i], ys[i]), (xs[j], ys[j])], "h": round(hm, 2)})
    return {"resumen": resumen, "perfil": perfil, "tramos": tramos, "corredor": poli,
            "segmentos": segmentos, "avisos": avisos}
