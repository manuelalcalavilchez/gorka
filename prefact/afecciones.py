"""Cruce del ámbito con las capas de condicionantes del catálogo."""
from __future__ import annotations

import logging

import geopandas as gpd
import pandas as pd
import pyogrio
from pyproj import CRS, Transformer
from shapely.geometry import box, mapping
from shapely.ops import unary_union

from .catalogo import ORDEN_GRAVEDAD, Capa, catalogo

log = logging.getLogger(__name__)

RADIO_BUSQUEDA_M = 1500  # proximidad informativa y extensión del visor
MAX_ELEMENTOS_VISOR = 3000


def _peor(semaforos) -> str:
    sems = list(semaforos)
    return max(sems, key=lambda s: ORDEN_GRAVEDAD.get(s, 0)) if sems else "verde"


def _leer_capa(capa: Capa, zona_busqueda_utm, crs_utm: str) -> tuple[gpd.GeoDataFrame, list[str]]:
    avisos, partes = [], []
    for fichero in capa.ficheros_disponibles:
        try:
            info = pyogrio.read_info(fichero)
            crs_capa = info.get("crs")
            if not crs_capa:
                avisos.append(f"{fichero.name}: sin sistema de referencia, se omite.")
                continue
            tr = Transformer.from_crs(CRS(crs_utm), CRS(crs_capa), always_xy=True)
            bbox = tr.transform_bounds(*zona_busqueda_utm.bounds, densify_pts=21)
            g = gpd.read_file(fichero, bbox=bbox, engine="pyogrio")
        except Exception as e:  # noqa: BLE001
            avisos.append(f"{fichero.name}: error de lectura ({e}).")
            continue
        if g.empty:
            continue
        g = g[g.geometry.notna() & ~g.geometry.is_empty].to_crs(crs_utm)
        g["geometry"] = g.geometry.make_valid()
        partes.append(g)
    if not partes:
        return gpd.GeoDataFrame(geometry=[], crs=crs_utm), avisos
    gdf = gpd.GeoDataFrame(pd.concat(partes, ignore_index=True), crs=crs_utm)
    gdf = gdf[gdf.intersects(zona_busqueda_utm)]
    return gdf, avisos


def _etiquetas(capa: Capa, gdf: gpd.GeoDataFrame, maximo: int = 12) -> list[str]:
    campos = [c for c in [capa.campo_etiqueta, *capa.campos_etiqueta_alternativos] if c]
    for campo in campos:
        if campo in gdf.columns:
            vals = [str(v).strip() for v in gdf[campo].dropna().unique() if str(v).strip() not in ("", "nan", "None")]
            if vals:
                return sorted(vals)[:maximo]
    return []


def _geom_zona(zona, gdf: gpd.GeoDataFrame):
    if gdf.empty:
        return None
    if zona.distancia_por_campo:
        cfg = zona.distancia_por_campo
        campo, valores, defecto = cfg["campo"], cfg.get("valores", {}), float(cfg.get("defecto", 0))
        dist = gdf[campo].map(lambda v: float(valores.get(str(v).strip(), defecto))) if campo in gdf.columns \
            else pd.Series(defecto, index=gdf.index)
        geoms = [g.buffer(d) for g, d in zip(gdf.geometry, dist)]
    elif zona.distancia > 0:
        geoms = list(gdf.geometry.buffer(zona.distancia))
    else:
        geoms = [g for g in gdf.geometry if g.geom_type in ("Polygon", "MultiPolygon")]
        if not geoms:  # líneas o puntos con distancia 0: banda mínima de 1 m
            geoms = list(gdf.geometry.buffer(1))
    return unary_union(geoms) if geoms else None


def evaluar_capa(capa: Capa, ambito_poligono, ccaa: list[str], crs_utm: str, eje=None) -> tuple[dict, dict]:
    """Devuelve (resultado, geojson para el visor) de una capa."""
    base = {"id": capa.id, "nombre": capa.nombre, "tema": capa.tema, "fuente": capa.fuente,
            "fecha": capa.fecha, "notas": capa.notas, "zonas": [], "elementos": [], "avisos": [],
            "distancia_min_m": None, "cobertura_parcial": capa.es_parcial(ccaa), "valores": []}
    vacio = {"type": "FeatureCollection", "features": []}
    if not capa.instalada:
        return {**base, "estado": "no_instalada", "semaforo": "gris"}, vacio
    if ccaa and not capa.cubre(ccaa):
        return {**base, "estado": "sin_cobertura", "semaforo": "gris"}, vacio

    margen = capa.distancia_max + RADIO_BUSQUEDA_M
    busqueda = box(*ambito_poligono.buffer(margen).bounds)
    gdf, avisos = _leer_capa(capa, busqueda, crs_utm)
    base["avisos"] = avisos
    if gdf.empty:
        estado = "sin_afeccion"
        return {**base, "estado": estado, "semaforo": "verde"}, vacio

    resultado_zonas, sems = [], []
    geo_feats = []
    area_ambito = ambito_poligono.area or 1.0
    for zona in capa.zonas:
        gz = _geom_zona(zona, gdf)
        if gz is None or gz.is_empty:
            continue
        inter = gz.intersection(ambito_poligono)
        afecta = not inter.is_empty and (inter.area > 0.5 or ambito_poligono.area == 0)
        fila = {"etiqueta": zona.etiqueta, "semaforo": zona.semaforo, "afecta": afecta,
                "area_m2": round(inter.area, 1) if afecta else 0.0,
                "pct": round(100 * inter.area / area_ambito, 2) if afecta else 0.0}
        if eje is not None and afecta:
            fila["longitud_eje_m"] = round(eje.intersection(gz).length, 1)
        if afecta:
            sems.append(zona.semaforo)
            dentro = gdf[gdf.geometry.buffer(zona.distancia_max + 0.01).intersects(ambito_poligono)]
            fila["elementos"] = _etiquetas(capa, dentro)
        resultado_zonas.append(fila)
        # Banda de la zona recortada a la vista, para el visor
        vista = ambito_poligono.buffer(RADIO_BUSQUEDA_M)
        gz_vista = gz.intersection(vista).simplify(1.0)
        if not gz_vista.is_empty:
            geo_feats.append({"type": "Feature", "geometry": gz_vista,
                              "properties": {"capa": capa.id, "tipo": "zona", "etiqueta": zona.etiqueta,
                                             "semaforo": zona.semaforo, "afecta": afecta}})

    # Elementos originales (cauces, vías, espacios...) para el visor
    vista = ambito_poligono.buffer(RADIO_BUSQUEDA_M)
    cerca = gdf[gdf.intersects(vista)].head(MAX_ELEMENTOS_VISOR)
    campos_eti = [c for c in [capa.campo_etiqueta, *capa.campos_etiqueta_alternativos] if c and c in cerca.columns]
    for _, f in cerca.iterrows():
        geom = f.geometry.intersection(vista).simplify(0.5)
        if geom.is_empty:
            continue
        eti = next((str(f[c]) for c in campos_eti if pd.notna(f[c]) and str(f[c]).strip()), "")
        geo_feats.append({"type": "Feature", "geometry": geom,
                          "properties": {"capa": capa.id, "tipo": "elemento", "etiqueta": eti}})

    dmin = float(gdf.distance(ambito_poligono).min())
    base["distancia_min_m"] = round(dmin, 1)
    base["zonas"] = resultado_zonas
    base["elementos"] = _etiquetas(capa, gdf[gdf.distance(ambito_poligono) <= capa.distancia_max + 0.01])
    if capa.campos_informe:
        inter = gdf[gdf.intersects(ambito_poligono)]
        cols = [c for c in capa.campos_informe if c in inter.columns]
        eti_col = next((c for c in campos_eti), None)
        for _, f in inter.head(20).iterrows():
            base["valores"].append({"etiqueta": str(f[eti_col]) if eti_col else "",
                                    **{c: (None if pd.isna(f[c]) else f[c].item() if hasattr(f[c], "item") else f[c])
                                       for c in cols}})
    if sems:
        estado, semaforo = "afecta", _peor(sems)
    else:
        estado, semaforo = "sin_afeccion", "verde"
    geojson = _a_wgs84(geo_feats, crs_utm)
    return {**base, "estado": estado, "semaforo": semaforo}, geojson


def _a_wgs84(feats: list[dict], crs_utm: str) -> dict:
    if not feats:
        return {"type": "FeatureCollection", "features": []}
    gs = gpd.GeoSeries([f["geometry"] for f in feats], crs=crs_utm).to_crs("EPSG:4326")
    out = []
    for f, g in zip(feats, gs):
        out.append({"type": "Feature", "geometry": mapping(g), "properties": f["properties"]})
    return {"type": "FeatureCollection", "features": out}


def evaluar(ambito_poligono, ccaa: list[str], crs_utm: str, eje=None, progreso=None) -> tuple[list[dict], dict]:
    cat = catalogo()
    resultados, geojsons = [], {}
    n = len(cat.capas)
    for i, capa in enumerate(cat.capas):
        if progreso:
            progreso(f"Cruzando {capa.nombre}", i / max(n, 1))
        try:
            r, gj = evaluar_capa(capa, ambito_poligono, ccaa, crs_utm, eje=eje)
        except Exception as e:  # noqa: BLE001
            log.exception("Error en capa %s", capa.id)
            r = {"id": capa.id, "nombre": capa.nombre, "tema": capa.tema, "estado": "error", "semaforo": "gris",
                 "avisos": [f"Error procesando la capa: {e}"], "zonas": [], "elementos": [], "fuente": capa.fuente,
                 "fecha": capa.fecha, "notas": capa.notas, "distancia_min_m": None, "cobertura_parcial": False,
                 "valores": []}
            gj = {"type": "FeatureCollection", "features": []}
        resultados.append(r)
        geojsons[capa.id] = gj
    return resultados, geojsons
