"""Imágenes para el visor (PNG en Web Mercator) y planos para el PDF."""
from __future__ import annotations

import io
import logging
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import requests  # noqa: E402
from matplotlib.colors import BoundaryNorm, ListedColormap, TwoSlopeNorm  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from PIL import Image  # noqa: E402
from rasterio.warp import Resampling, calculate_default_transform, reproject, transform_bounds  # noqa: E402

from . import settings  # noqa: E402
from .terreno import CLASES_PENDIENTE  # noqa: E402

log = logging.getLogger(__name__)

COLORES_SEMAFORO = {"rojo": "#C62828", "ambar": "#EF8F00", "info": "#1E88E5", "verde": "#2E7D32", "gris": "#9E9E9E"}
COLORES_PENDIENTE = ["#1a9850", "#a6d96a", "#fee08b", "#f46d43", "#a50026"]
LIMITES_PENDIENTE = [0, 3, 10, 20, 30, 1000]


# ------------------------------------------------------------------ visor
def _a_mercator(arr: np.ndarray, transform, crs: str, resampling=Resampling.bilinear):
    h, w = arr.shape
    b = (transform.c, transform.f + h * transform.e, transform.c + w * transform.a, transform.f)
    dt, dw, dh = calculate_default_transform(crs, "EPSG:3857", w, h, *b)
    out = np.full((dh, dw), np.nan, dtype="float32")
    reproject(arr.astype("float32"), out, src_transform=transform, src_crs=crs, dst_transform=dt,
              dst_crs="EPSG:3857", src_nodata=np.nan, dst_nodata=np.nan, resampling=resampling)
    bm = (dt.c, dt.f + dh * dt.e, dt.c + dw * dt.a, dt.f)
    w_, s_, e_, n_ = transform_bounds("EPSG:3857", "EPSG:4326", *bm)
    return out, [[s_, w_], [n_, e_]]


def _guardar_rgba(rgba: np.ndarray, ruta: Path) -> None:
    Image.fromarray((np.clip(rgba, 0, 1) * 255).astype("uint8"), mode="RGBA").save(ruta, optimize=True)


def png_sombreado(hs: np.ndarray, transform, crs: str, ruta: Path) -> list:
    m, bounds = _a_mercator(hs, transform, crs)
    rgba = np.zeros(m.shape + (4,))
    v = np.nan_to_num(m, nan=0)
    rgba[..., 0] = rgba[..., 1] = rgba[..., 2] = v
    rgba[..., 3] = np.where(np.isfinite(m), 0.55, 0)
    _guardar_rgba(rgba, ruta)
    return bounds


def png_pendientes(p: np.ndarray, transform, crs: str, ruta: Path) -> list:
    m, bounds = _a_mercator(p, transform, crs, Resampling.nearest)
    cmap = ListedColormap(COLORES_PENDIENTE)
    norm = BoundaryNorm(LIMITES_PENDIENTE, cmap.N)
    rgba = cmap(norm(np.nan_to_num(m, nan=0)))
    rgba[..., 3] = np.where(np.isfinite(m), 0.7, 0)
    _guardar_rgba(rgba, ruta)
    return bounds


def _norm_dif(dif: np.ndarray):
    lim = float(np.nanmax(np.abs(dif))) if np.isfinite(dif).any() else 1.0
    lim = max(lim, 0.5)
    return TwoSlopeNorm(vcenter=0, vmin=-lim, vmax=lim), lim


def png_corte_relleno(dif: np.ndarray, transform, crs: str, ruta: Path) -> tuple[list, float]:
    m, bounds = _a_mercator(dif, transform, crs)
    norm, lim = _norm_dif(dif)
    cmap = plt.get_cmap("RdBu")  # rojo = desmonte (dif>0 se invierte abajo), azul = terraplén
    rgba = cmap(norm(-np.nan_to_num(m, nan=0)))
    vis = np.isfinite(m) & (np.abs(np.nan_to_num(m)) > 0.05)
    intensidad = np.clip(0.35 + 2.5 * np.abs(np.nan_to_num(m)) / lim, 0.35, 0.95)
    rgba[..., 3] = np.where(vis, intensidad, 0)
    _guardar_rgba(rgba, ruta)
    return bounds, lim


# ------------------------------------------------------------------ planos PDF
def fondo_wms(bounds, crs: str, ancho_px: int = 1400) -> np.ndarray | None:
    if not settings.PLANOS_CON_FONDO:
        return None
    minx, miny, maxx, maxy = bounds
    alto_px = int(ancho_px * (maxy - miny) / (maxx - minx))
    params = {"SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetMap", "LAYERS": settings.WMS_PNOA_CAPA,
              "STYLES": "", "CRS": crs, "BBOX": f"{minx},{miny},{maxx},{maxy}", "WIDTH": ancho_px,
              "HEIGHT": max(alto_px, 50), "FORMAT": "image/jpeg"}
    try:
        r = requests.get(settings.WMS_PNOA, params=params, timeout=25)
        r.raise_for_status()
        if "image" not in r.headers.get("Content-Type", ""):
            return None
        return np.asarray(Image.open(io.BytesIO(r.content)).convert("RGB"))
    except Exception as e:  # noqa: BLE001
        log.warning("Sin fondo PNOA: %s", e)
        return None


def _marco(geom, margen_rel=0.25, minimo=150):
    minx, miny, maxx, maxy = geom.bounds
    m = max((maxx - minx), (maxy - miny)) * margen_rel + minimo
    return (minx - m, miny - m, maxx + m, maxy + m)


def _dibujar_geom(ax, geom, **kw):
    import geopandas as gpd
    gpd.GeoSeries([geom]).plot(ax=ax, **kw)


def _nuevo_mapa(bounds, crs, titulo):
    fig, ax = plt.subplots(figsize=(8, 8 * (bounds[3] - bounds[1]) / (bounds[2] - bounds[0])), dpi=150)
    fondo = fondo_wms(bounds, crs)
    if fondo is not None:
        ax.imshow(fondo, extent=(bounds[0], bounds[2], bounds[1], bounds[3]), zorder=0)
    ax.set_xlim(bounds[0], bounds[2])
    ax.set_ylim(bounds[1], bounds[3])
    ax.set_aspect("equal")
    ax.set_title(titulo, fontsize=10, weight="bold", color="#1F2A44")
    ax.tick_params(labelsize=6)
    ax.ticklabel_format(useOffset=False, style="plain")
    return fig, ax, fondo is not None


def _guardar_fig(fig, ruta: Path, con_fondo: bool, crs: str):
    fig.text(0.01, 0.005, f"{settings.MARCA} · {settings.MARCA_WEB} · {crs}"
             + (" · Fondo: PNOA © IGN" if con_fondo else ""), fontsize=6, color="#555")
    fig.tight_layout()
    if ruta.suffix.lower() in (".jpg", ".jpeg"):
        fig.savefig(ruta, dpi=150, pil_kwargs={"quality": 82, "optimize": True})
    else:
        fig.savefig(ruta, dpi=150)
    plt.close(fig)


def plano_afecciones(ambito_geom, zonas_utm: list[tuple], crs: str, ruta: Path) -> None:
    """zonas_utm: lista de (geom, semaforo, etiqueta) ya en CRS de trabajo."""
    b = _marco(ambito_geom)
    fig, ax, fondo = _nuevo_mapa(b, crs, "Condicionantes sobre el ámbito")
    vistos = {}
    for geom, sem, eti in zonas_utm:
        color = COLORES_SEMAFORO.get(sem, "#999")
        _dibujar_geom(ax, geom, facecolor=color, edgecolor=color, alpha=0.30, linewidth=0.6, zorder=2)
        vistos.setdefault(sem, color)
    _dibujar_geom(ax, ambito_geom, facecolor="none", edgecolor="#FFEB3B" if fondo else "#000", linewidth=2.2, zorder=5)
    nombres = {"rojo": "Excluyente / muy restrictivo", "ambar": "Condicionante", "info": "Informativo"}
    handles = [Patch(facecolor="none", edgecolor="#FFEB3B" if fondo else "#000", linewidth=2, label="Ámbito")]
    handles += [Patch(facecolor=c, alpha=0.5, label=nombres.get(s, s)) for s, c in vistos.items()]
    ax.legend(handles=handles, loc="lower left", fontsize=7, framealpha=0.9)
    _guardar_fig(fig, ruta, fondo, crs)


def plano_raster(ambito_geom, arr, transform, crs, ruta: Path, tipo: str) -> None:
    h, w = arr.shape
    ext = (transform.c, transform.c + w * transform.a, transform.f + h * transform.e, transform.f)
    b = _marco(ambito_geom, 0.1, 50)
    titulo = "Pendientes del terreno" if tipo == "pendientes" else "Desmonte (rojo) y terraplén (azul)"
    fig, ax, fondo = _nuevo_mapa(b, crs, titulo)
    if tipo == "pendientes":
        cmap = ListedColormap(COLORES_PENDIENTE)
        norm = BoundaryNorm(LIMITES_PENDIENTE, cmap.N)
        im = ax.imshow(np.ma.masked_invalid(arr), extent=ext, cmap=cmap, norm=norm, alpha=0.7, zorder=2)
        handles = [Patch(color=c, label=e) for c, (_, _, e) in zip(COLORES_PENDIENTE, CLASES_PENDIENTE)]
        ax.legend(handles=handles, loc="lower left", fontsize=7, framealpha=0.9, title="Pendiente", title_fontsize=7)
    else:
        norm, _ = _norm_dif(arr)
        a = np.ma.masked_where(~np.isfinite(arr) | (np.abs(np.nan_to_num(arr)) < 0.05), -arr)
        im = ax.imshow(a, extent=ext, cmap="RdBu", norm=norm, alpha=0.8, zorder=2)
        from matplotlib.ticker import FuncFormatter
        cb = fig.colorbar(im, ax=ax, fraction=0.035, pad=0.02, format=FuncFormatter(lambda x, _: f"{abs(x):.1f}"))
        cb.set_label("Desmonte (rojo) / terraplén (azul), m", fontsize=7)
        cb.ax.tick_params(labelsize=6)
    _dibujar_geom(ax, ambito_geom, facecolor="none", edgecolor="#111", linewidth=1.6, zorder=5)
    _guardar_fig(fig, ruta, fondo, crs)


def grafico_pendientes(clases: list[dict], ruta: Path) -> None:
    fig, ax = plt.subplots(figsize=(6, 2.6), dpi=150)
    ax.bar([c["clase"] for c in clases], [c["pct"] for c in clases], color=COLORES_PENDIENTE)
    ax.set_ylabel("% del ámbito", fontsize=8)
    ax.tick_params(labelsize=7)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(ruta)
    plt.close(fig)


def grafico_perfil(perfil: dict, ruta: Path) -> None:
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(7.5, 4.2), dpi=150, sharex=True,
                                 gridspec_kw={"height_ratios": [2, 1]})
    pk, zt, zr = perfil["pk"], perfil["terreno"], perfil["rasante"]
    a1.plot(pk, zt, color="#6D4C41", lw=1, label="Terreno")
    a1.plot(pk, zr, color="#1F2A44", lw=1.4, label="Rasante")
    zt_, zr_ = np.array(zt), np.array(zr)
    a1.fill_between(pk, zt_, zr_, where=zt_ > zr_, color="#C62828", alpha=0.3, label="Desmonte")
    a1.fill_between(pk, zt_, zr_, where=zt_ < zr_, color="#1565C0", alpha=0.3, label="Terraplén")
    a1.set_ylabel("Cota (m)", fontsize=8)
    a1.legend(fontsize=7, ncol=4, loc="upper left")
    a2.plot(pk, perfil["masa"], color="#2E7D32", lw=1.2)
    a2.axhline(0, color="#999", lw=0.6)
    a2.set_ylabel("Masa acum. (m³)", fontsize=8)
    a2.set_xlabel("PK (m)", fontsize=8)
    for a in (a1, a2):
        a.tick_params(labelsize=7)
        a.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(ruta)
    plt.close(fig)
