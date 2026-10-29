"""Orquestación de un estudio: una carpeta por estudio con entrada, caché y resultados."""
from __future__ import annotations

import json
import logging
import re
import shutil
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path

import geopandas as gpd
import numpy as np
from rasterio.features import shapes
from shapely.geometry import LineString, mapping, shape
from shapely.ops import unary_union

from . import afecciones as afe
from . import render, settings, terreno, territorio, tramitacion, vial, volumenes
from .ambito import AmbitoError, leer_ambito

log = logging.getLogger(__name__)

NOMBRES_MODO = {"explanacion": "Explanación", "relleno": "Relleno / depósito de tierras",
                "extraccion": "Extracción (cantera / gravera)", "vial": "Vial / camino"}
MARGEN_MODO = {"explanacion": 250.0, "relleno": 250.0, "extraccion": 30.0}
ID_RE = re.compile(r"^[0-9a-f]{32}$")


class EstudioError(RuntimeError):
    pass


# ------------------------------------------------------------------ utilidades de carpeta
def carpeta(id_: str) -> Path:
    if not ID_RE.match(id_ or ""):
        raise EstudioError("Identificador de estudio no válido.")
    return settings.ESTUDIOS_DIR / id_


def _escribir_json(ruta: Path, datos) -> None:
    tmp = ruta.with_suffix(ruta.suffix + ".tmp")
    tmp.write_text(json.dumps(datos, ensure_ascii=False, default=_json_default), encoding="utf-8")
    tmp.replace(ruta)


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    return str(o)


def leer_json(ruta: Path, defecto=None):
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return defecto


def estado(id_: str) -> dict:
    return leer_json(carpeta(id_) / "estado.json", {"estado": "desconocido"})


def _poner_estado(d: Path, **kw) -> None:
    e = leer_json(d / "estado.json", {}) or {}
    e.update(kw)
    e["actualizado"] = time.time()
    _escribir_json(d / "estado.json", e)


def crear(contenido: bytes, nombre_fichero: str, modo: str, nombre: str, params: dict) -> str:
    if modo not in volumenes.MODOS:
        raise EstudioError(f"Modo no válido: {modo}")
    volumenes.normalizar_parametros(modo, params)  # valida antes de crear nada
    id_ = uuid.uuid4().hex
    d = carpeta(id_)
    (d / "entrada").mkdir(parents=True)
    seguro = re.sub(r"[^\w.\-]+", "_", Path(nombre_fichero).name)[:120] or "ambito"
    (d / "entrada" / seguro).write_bytes(contenido)
    _escribir_json(d / "parametros.json", {"modo": modo, "nombre": nombre.strip()[:120] or "Estudio sin nombre",
                                           "params": params, "fichero": seguro,
                                           "creado": datetime.now().isoformat(timespec="seconds")})
    _poner_estado(d, estado="en_cola", progreso=0.0, mensaje="En cola", version=0)
    return id_


# ------------------------------------------------------------------ pipeline
def _ocupacion_poligono(t: terreno.Terreno, dif: np.ndarray):
    ocup = np.isfinite(dif) & ((np.abs(dif) > 0.01) | t.mascara)
    if not ocup.any():
        return None
    geoms = [shape(g) for g, v in shapes(ocup.astype("uint8"), mask=ocup, transform=t.transform) if v == 1]
    return unary_union(geoms).buffer(0) if geoms else None


def _guardar_geojson(ruta: Path, geom_utm, crs: str, props: dict | None = None) -> None:
    g = gpd.GeoSeries([geom_utm], crs=crs).to_crs("EPSG:4326").iloc[0]
    _escribir_json(ruta, {"type": "FeatureCollection",
                          "features": [{"type": "Feature", "properties": props or {}, "geometry": mapping(g)}]})


def ejecutar(id_: str, params_nuevos: dict | None = None) -> None:
    """Ejecuta (o recalcula) el estudio. Guarda el resultado en resultado.json."""
    d = carpeta(id_)
    meta = leer_json(d / "parametros.json")
    if meta is None:
        raise EstudioError("Estudio no encontrado.")
    version = (leer_json(d / "estado.json", {}) or {}).get("version", 0) + 1
    if params_nuevos is not None:
        meta["params"] = params_nuevos
        _escribir_json(d / "parametros.json", meta)

    def prog(msg, frac):
        _poner_estado(d, estado="procesando", progreso=round(float(frac), 2), mensaje=msg)

    try:
        prog("Leyendo el ámbito", 0.02)
        modo = meta["modo"]
        p = volumenes.normalizar_parametros(modo, meta.get("params"))
        amb = leer_ambito(d / "entrada" / meta["fichero"], modo)
        _escribir_json(d / "ambito.geojson", amb.a_geojson())

        prog("Detectando comunidad y municipios", 0.08)
        terr = leer_json(d / "territorio.json")
        if terr is None:
            terr = territorio.detectar(amb)
            _escribir_json(d / "territorio.json", terr)
        ccaa = territorio.codigos_ccaa(terr)

        prog("Preparando el modelo del terreno", 0.15)
        avisos = [terr["aviso"]] if terr.get("aviso") else []
        npz = d / "terreno.npz"
        t = None
        if modo == "vial":
            geom_mask = amb.geom.buffer(p["ancho"] / 2)
            margen = p["ancho"] / 2 + 150
        else:
            geom_mask = amb.geom
            margen = MARGEN_MODO[modo]
        try:
            if npz.exists() and modo != "vial":
                t = terreno.Terreno.cargar(npz)
            else:
                t = terreno.preparar(amb, margen_m=margen, geom_analisis=geom_mask)
                t.guardar(npz)
        except terreno.TerrenoError as e:
            avisos.append(str(e))
        visor = {"raster": {}, "vector": {}}
        (d / "visor").mkdir(exist_ok=True)
        stats, vol, extra_vial = None, None, None
        geom_afecciones = amb.geom if amb.tipo == "poligono" else amb.geom.buffer(p["ancho"] / 2)
        if t is not None:
            avisos += t.avisos
            prog("Calculando pendientes", 0.25)
            stats = terreno.estadisticas(t)
            hs = terreno.sombreado(t.z, t.res)
            pend = terreno.pendiente_pct(t.z, t.res)
            visor["raster"]["sombreado"] = {"url": "visor/sombreado.png",
                                            "bounds": render.png_sombreado(hs, t.transform, t.crs, d / "visor/sombreado.png")}
            visor["raster"]["pendientes"] = {"url": "visor/pendientes.png",
                                             "bounds": render.png_pendientes(pend, t.transform, t.crs, d / "visor/pendientes.png")}
            prog("Calculando el movimiento de tierras", 0.35)
            if modo == "vial":
                rv = vial.calcular(t, amb.geom, p)
                vol = rv["resumen"]
                extra_vial = {"perfil": rv["perfil"], "tramos": rv["tramos"]}
                avisos += rv["avisos"]
                geom_afecciones = rv["corredor"]
                _guardar_geojson(d / "visor/ocupacion.geojson", rv["corredor"], amb.crs, {"tipo": "corredor"})
                segs = [{"type": "Feature", "properties": {"h": s["h"]}, "geometry": LineString(s["coords"])}
                        for s in rv["segmentos"]]
                gs = gpd.GeoSeries([f["geometry"] for f in segs], crs=amb.crs).to_crs("EPSG:4326")
                _escribir_json(d / "visor/eje.geojson", {"type": "FeatureCollection", "features": [
                    {"type": "Feature", "properties": f["properties"], "geometry": mapping(g)}
                    for f, g in zip(segs, gs)]})
                visor["vector"]["eje"] = "visor/eje.geojson"
            else:
                rm = volumenes.calcular(modo, t, p)
                vol = rm.resumen
                np.savez_compressed(d / "obra.npz", diseno=rm.diseno.astype("float32"), dif=rm.dif.astype("float32"))
                b, lim = render.png_corte_relleno(rm.dif, t.transform, t.crs, d / "visor/corte_relleno.png")
                visor["raster"]["corte_relleno"] = {"url": "visor/corte_relleno.png", "bounds": b, "lim": lim}
                ocup = _ocupacion_poligono(t, rm.dif)
                if ocup is not None:
                    _guardar_geojson(d / "visor/ocupacion.geojson", ocup, amb.crs, {"tipo": "ocupacion"})
                    geom_afecciones = unary_union([amb.geom, ocup])
                    h, w = t.shape
                    borde = np.zeros((h, w), bool)
                    borde[[0, -1], :] = True
                    borde[:, [0, -1]] = True
                    if (np.isfinite(rm.dif) & ~t.mascara & borde).any():
                        avisos.append("Los taludes alcanzan el borde de la zona analizada: el volumen puede estar "
                                      "infravalorado (desnivel muy grande respecto al ámbito).")
            if (d / "visor/ocupacion.geojson").exists():
                visor["vector"]["ocupacion"] = "visor/ocupacion.geojson"
        elif modo == "vial":
            geom_afecciones = amb.geom.buffer(p["ancho"] / 2)

        prog("Cruzando con las capas de condicionantes", 0.55)
        (d / "capas").mkdir(exist_ok=True)
        res_af, geojsons = afe.evaluar(
            geom_afecciones, ccaa, amb.crs, eje=amb.geom if amb.tipo == "linea" else None,
            progreso=lambda m, f: prog(m, 0.55 + 0.35 * f))
        for cid, gj in geojsons.items():
            if gj["features"]:
                _escribir_json(d / "capas" / f"{cid}.geojson", gj)
                visor["vector"][f"capa:{cid}"] = f"capas/{cid}.geojson"

        prog("Valorando la tramitación", 0.93)
        area = geom_afecciones.area
        tram = tramitacion.orientar(modo, area, vol or {}, res_af, ccaa)
        glob = tramitacion.semaforo_global(res_af, stats, bool(t and t.fiable))
        if t is None:
            glob["texto"] += " Sin MDT: no se han calculado pendientes ni volúmenes."

        c = amb.centroide_wgs84
        resultado = {
            "id": id_, "version": version, "nombre": meta["nombre"], "modo": modo,
            "modo_nombre": NOMBRES_MODO[modo], "fecha": datetime.now().isoformat(timespec="seconds"),
            "ambito": {"fichero": amb.nombre_fichero, "tipo": amb.tipo, "crs": amb.crs,
                       "area_m2": round(amb.area_m2, 1), "longitud_m": round(amb.longitud_m, 1),
                       "area_obra_m2": round(geom_afecciones.area, 1),
                       "centroide": [round(c[0], 6), round(c[1], 6)],
                       "bounds_wgs84": list(amb.geom_wgs84.bounds)},
            "territorio": terr,
            "terreno": {"fuente": t.fuente if t else None, "fiable": bool(t and t.fiable),
                        "estadisticas": stats},
            "parametros": p,
            "volumenes": vol,
            "vial": extra_vial,
            "afecciones": res_af,
            "tramitacion": tram,
            "global": glob,
            "visor": visor,
            "avisos": [a for a in dict.fromkeys(avisos) if a],
            "marca": {"nombre": settings.MARCA, "web": settings.MARCA_WEB, "producto": settings.PRODUCTO},
        }
        _escribir_json(d / "resultado.json", resultado)
        shutil.rmtree(d / "informes", ignore_errors=True)
        _poner_estado(d, estado="listo", progreso=1.0, mensaje="Listo", version=version, error=None)
    except (AmbitoError, volumenes.ParametrosError, EstudioError) as e:
        _poner_estado(d, estado="error", mensaje=str(e), error=str(e))
    except Exception as e:  # noqa: BLE001
        log.exception("Fallo en el estudio %s", id_)
        (d / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
        _poner_estado(d, estado="error", mensaje=f"Error interno: {e}", error=str(e))


def resultado(id_: str) -> dict | None:
    return leer_json(carpeta(id_) / "resultado.json")


def perfil(id_: str, coords_wgs84: list[tuple[float, float]]) -> dict:
    """Perfil del terreno (y del diseño, si existe) a lo largo de una polilínea."""
    d = carpeta(id_)
    if not (d / "terreno.npz").exists():
        raise EstudioError("El estudio no tiene modelo del terreno.")
    t = terreno.Terreno.cargar(d / "terreno.npz")
    linea = gpd.GeoSeries([LineString(coords_wgs84)], crs="EPSG:4326").to_crs(t.crs).iloc[0]
    if linea.length < t.res:
        raise EstudioError("La línea del perfil es demasiado corta.")
    n = int(min(max(linea.length / (t.res / 2), 20), 1500))
    s = np.linspace(0, linea.length, n)
    pts = [linea.interpolate(v) for v in s]
    xs, ys = np.array([q.x for q in pts]), np.array([q.y for q in pts])
    zt = terreno.muestrear(t, xs, ys)
    out = {"distancia": np.round(s, 1).tolist(), "terreno": np.round(zt, 2).tolist(), "diseno": None}
    obra = d / "obra.npz"
    if obra.exists():
        dis = np.load(obra)["diseno"]
        cols = (xs - t.transform.c) / t.transform.a
        filas = (ys - t.transform.f) / t.transform.e
        f, c = np.floor(filas).astype(int), np.floor(cols).astype(int)
        ok = (f >= 0) & (f < dis.shape[0]) & (c >= 0) & (c < dis.shape[1])
        vals = np.full(len(s), np.nan)
        vals[ok] = dis[f[ok], c[ok]]
        out["diseno"] = [None if not np.isfinite(v) else round(float(v), 2) for v in vals]
    return out


def limpiar_antiguos() -> int:
    if not settings.ESTUDIOS_DIR.exists():
        return 0
    limite = time.time() - settings.ESTUDIOS_TTL_DIAS * 86400
    n = 0
    for d in settings.ESTUDIOS_DIR.iterdir():
        if d.is_dir() and ID_RE.match(d.name) and d.stat().st_mtime < limite:
            shutil.rmtree(d, ignore_errors=True)
            n += 1
    return n
