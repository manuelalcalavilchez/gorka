"""Capas que se consultan por API para cada estudio: viales (IGN), edificios y parcelas (Catastro).

Si un servicio falla o no cubre la zona, sus criterios quedan sin comprobar: un fallo nunca
equivale a «no hay nada». Cada respuesta correcta se guarda con el estudio y se reutiliza al recalcular.
"""
from __future__ import annotations

import json
import logging
import math
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import date
from pathlib import Path

import geopandas as gpd
import requests
from pyproj import Transformer
from shapely.geometry import Polygon, box, mapping, shape
from shapely.ops import unary_union

from . import settings
from .catalogo import implantacion

log = logging.getLogger(__name__)

URL_VIALES = "https://api-features.idee.es/collections/roadlink/items"
URL_CATASTRO = "https://ovc.catastro.meh.es/INSPIRE/"
URL_MDT = "https://servicios.idee.es/wcs-inspire/mdt"
FUENTE_VIALES = "IGN — Redes de transporte (API Features)"
FUENTE_CATASTRO = "Dirección General del Catastro — INSPIRE"
TIMEOUT_S = 60
REINTENTOS = 3
LADO_TESELA_M = 1000  # Catastro rechaza consultas de más de 1 km de lado
LADO_MIN_TESELA_M = 250  # en zonas pobladas tarda según lo que devuelve: las teselas lentas se parten
TIMEOUT_TESELA_S = 20
TIMEOUT_TESELA_MIN_S = 90
PRESUPUESTO_CATASTRO_S = 240  # por servicio y estudio: si se pasa, sus criterios quedan sin comprobar
HILOS_CATASTRO = 4
MAX_TESELAS = 150
MAX_PAGINAS = 40
PAGINA = 1000
CRS_CATASTRO = ("EPSG:25829", "EPSG:25830", "EPSG:25831")
CCAA_SIN_CATASTRO = {"15": "Navarra", "16": "País Vasco"}  # catastros forales propios
PCT_MIN_PARCELA = 1.0  # por debajo es un roce del lindero, no una parcela dentro del ámbito


class RemotoError(RuntimeError):
    pass


class SinCobertura(RemotoError):
    pass


def _get(url: str, params: dict | None, timeout: float = TIMEOUT_S, reintentos: int = REINTENTOS) -> requests.Response:
    for intento in range(reintentos):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            if intento == reintentos - 1:
                raise RemotoError(f"El servicio no responde ({e.__class__.__name__}).") from e
            time.sleep(2 * (intento + 1))
    raise RemotoError("El servicio no responde.")


# ------------------------------------------------------------------ disponibilidad de los servicios
CAJA_PRUEBA = (679300, 4165400, 679400, 4165500)  # 100 m de lado: la respuesta es mínima
TIMEOUT_PRUEBA_S = 8
VIGENCIA_PRUEBA_S = 300  # la página es pública: no se golpea a los servicios en cada visita


def _wfs_catastro(tipo: str) -> dict:
    return {"service": "WFS", "version": "2.0.0", "request": "GetFeature", "typenames": tipo, "srsName": "EPSG::25830",
            "bbox": ",".join(map(str, CAJA_PRUEBA))}


def _sin_excepcion(r) -> bool:
    return b"ExceptionReport" not in r.content[:600]


x0, y0, x1, y1 = CAJA_PRUEBA
SERVICIOS = [
    {"id": "viales", "nombre": "Viales", "uso": "Autovías, carreteras y caminos", "fuente": "IGN", "cobertura": "Toda España",
     "url": URL_VIALES, "params": {"bbox": "-0.967,37.618,-0.966,37.619", "limit": 1, "f": "json"},
     "valida": lambda r: isinstance(r.json().get("features"), list)},
    {"id": "edificios", "nombre": "Edificios", "uso": "Construcciones y viviendas", "fuente": "Catastro",
     "cobertura": "Sin Navarra, País Vasco ni Canarias", "url": URL_CATASTRO + "wfsBU.aspx",
     "params": _wfs_catastro("BU.BUILDING"), "valida": _sin_excepcion},
    {"id": "parcelas", "nombre": "Parcelas catastrales", "uso": "Parcelas dentro del ámbito", "fuente": "Catastro",
     "cobertura": "Sin Navarra, País Vasco ni Canarias", "url": URL_CATASTRO + "wfsCP.aspx",
     "params": _wfs_catastro("CP.CADASTRALPARCEL"), "valida": _sin_excepcion},
    {"id": "mdt05_ign", "nombre": "MDT05 (paso 5 m)", "uso": "Pendientes y volúmenes", "fuente": "IGN", "cobertura": "Toda España",
     "url": URL_MDT, "params": [("service", "WCS"), ("version", "2.0.1"), ("request", "GetCoverage"),
                                ("coverageid", "Elevacion25830_5"), ("format", "application/asc"),
                                ("subset", f"x({x0 + 2.5},{x1 + 2.5})"), ("subset", f"y({y0 + 2.5},{y1 + 2.5})")],
     "valida": lambda r: b"ncols" in r.content},
]
_prueba = {"hora": None, "filas": []}
_cerrojo_prueba = threading.Lock()


def _probar(s: dict) -> dict:
    inicio = time.monotonic()
    try:
        ok = bool(s["valida"](_get(s["url"], s["params"], TIMEOUT_PRUEBA_S, 1)))
    except (RemotoError, ValueError):
        ok = False
    return {**{k: s[k] for k in ("id", "nombre", "uso", "fuente", "cobertura")}, "disponible": ok,
            "ms": round((time.monotonic() - inicio) * 1000)}


def estado_servicios() -> list[dict]:
    """Prueba real de cada API, con una consulta mínima. Guarda el resultado unos minutos."""
    if not settings.DATOS_REMOTOS:
        return []
    with _cerrojo_prueba:
        if _prueba["hora"] is None or time.monotonic() - _prueba["hora"] > VIGENCIA_PRUEBA_S:
            with ThreadPoolExecutor(len(SERVICIOS)) as pool:
                _prueba["filas"] = list(pool.map(_probar, SERVICIOS))
            _prueba["hora"] = time.monotonic()
        return _prueba["filas"]


# ------------------------------------------------------------------ viales (IGN)
def clase_vial(p: dict) -> str:
    forma = (p.get("formofway_href") or "").rsplit("/", 1)[-1]
    if forma in ("motorway", "freeway", "dualCarriageway"):
        return "autovia"
    if p.get("surfacecategory") == "unpaved" or forma == "tractorRoad":
        return "camino"
    return "carretera"


def viales(geom, crs: str, margen: float) -> gpd.GeoDataFrame:
    tr = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    bbox = tr.transform_bounds(*geom.buffer(margen).bounds, densify_pts=21)
    url, params, feats = URL_VIALES, {"bbox": ",".join(f"{v:.6f}" for v in bbox), "limit": PAGINA, "f": "json"}, []
    for _ in range(MAX_PAGINAS):
        j = _get(url, params).json()
        feats += j.get("features", [])
        siguiente = [e["href"] for e in j.get("links", []) if e.get("rel") == "next"]
        if not siguiente:
            break
        url, params = siguiente[0], None
    else:
        raise RemotoError("Demasiados viales en la zona para consultarlos por API.")
    filas = [{"clase": clase_vial(f["properties"]), "geometry": shape(f["geometry"])} for f in feats if f.get("geometry")]
    return _gdf(filas, "EPSG:4326").to_crs(crs)


# ------------------------------------------------------------------ Catastro
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _anillo(elem) -> list[tuple[float, float]]:
    for e in elem.iter():
        if _local(e.tag) == "posList" and e.text:
            v = [float(x) for x in e.text.split()]
            return list(zip(v[0::2], v[1::2]))
    return []


def _superficie(elem):
    """Une los PolygonPatch de una geometría GML."""
    partes = []
    for patch in elem.iter():
        if _local(patch.tag) != "PolygonPatch":
            continue
        exterior, interiores = [], []
        for hijo in patch:
            if _local(hijo.tag) == "exterior":
                exterior = _anillo(hijo)
            elif _local(hijo.tag) == "interior":
                interiores.append(_anillo(hijo))
        if len(exterior) >= 4:
            partes.append(Polygon(exterior, [i for i in interiores if len(i) >= 4]).buffer(0))
    return unary_union(partes) if partes else None


def _hijo(elem, nombre: str) -> str | None:
    for e in elem.iter():
        if _local(e.tag) == nombre:
            return e.text
    return None


def leer_edificios(xml: bytes) -> list[dict]:
    filas = []
    for e in ET.fromstring(xml).iter():
        if _local(e.tag) != "Building":
            continue
        g = _superficie(e)
        if g is None or g.is_empty:
            continue
        viviendas = int(_hijo(e, "numberOfDwellings") or 0)
        uso = _hijo(e, "currentUse") or ""
        filas.append({"id": _hijo(e, "localId"), "uso": uso, "vivienda": viviendas > 0 or uso == "1_residential",
                      "geometry": g})
    return filas


def leer_parcelas(xml: bytes) -> list[dict]:
    filas = []
    for e in ET.fromstring(xml).iter():
        if _local(e.tag) != "CadastralParcel":
            continue
        g = _superficie(e)
        if g is None or g.is_empty:
            continue
        ref = _hijo(e, "nationalCadastralReference")
        filas.append({"id": ref, "ref": ref, "area_m2": float(_hijo(e, "areaValue") or 0), "geometry": g})
    return filas


def teselas(geom, lado: float = LADO_TESELA_M) -> list[tuple[float, float, float, float]]:
    x0, y0, x1, y1 = geom.bounds
    out = []
    for i in range(math.ceil((x1 - x0) / lado) or 1):
        for j in range(math.ceil((y1 - y0) / lado) or 1):
            t = (x0 + i * lado, y0 + j * lado, x0 + (i + 1) * lado, y0 + (j + 1) * lado)
            if box(*t).intersects(geom):
                out.append(t)
    return out


def _partir(t, geom) -> list:
    x0, y0, x1, y1 = t
    xm, ym = (x0 + x1) / 2, (y0 + y1) / 2
    cuartos = [(x0, y0, xm, ym), (xm, y0, x1, ym), (x0, ym, xm, y1), (xm, ym, x1, y1)]
    return [c for c in cuartos if box(*c).intersects(geom)]


def _catastro(servicio: str, tipo: str, lector, geom, crs: str, ccaa: list[str], progreso) -> gpd.GeoDataFrame:
    foral = [v for k, v in CCAA_SIN_CATASTRO.items() if k in ccaa]
    if foral:
        raise SinCobertura(f"El Catastro estatal no cubre {', '.join(foral)}.")
    if crs not in CRS_CATASTRO:
        raise SinCobertura(f"Consulta no disponible en el sistema {crs}.")
    lista = teselas(geom)
    if len(lista) > MAX_TESELAS:
        raise RemotoError(f"Ámbito demasiado grande para consultarlo por API ({len(lista)} teselas de 1 km).")
    limite = time.monotonic() + PRESUPUESTO_CATASTRO_S

    def pedir(t) -> tuple[list[dict], list]:
        """Devuelve (elementos, subteselas): una tesela lenta se parte en cuatro."""
        if time.monotonic() > limite:
            raise RemotoError("Catastro tarda demasiado en esta zona.")
        minima = t[2] - t[0] <= LADO_MIN_TESELA_M
        params = {"service": "WFS", "version": "2.0.0", "request": "GetFeature", "typenames": tipo,
                  "srsName": "EPSG::" + crs.split(":")[1], "bbox": ",".join(f"{v:.2f}" for v in t)}
        try:
            r = _get(URL_CATASTRO + servicio, params, TIMEOUT_TESELA_MIN_S if minima else TIMEOUT_TESELA_S,
                     REINTENTOS if minima else 1)
        except RemotoError:
            if minima:
                raise
            return [], _partir(t, geom)
        if b"ExceptionReport" in r.content[:600]:
            raise RemotoError("El servicio ha rechazado la consulta.")
        try:
            return lector(r.content), []
        except (ET.ParseError, ValueError) as e:
            raise RemotoError("Respuesta no válida del servicio.") from e

    filas, hechas, total = {}, 0, len(lista)
    with ThreadPoolExecutor(HILOS_CATASTRO) as pool:
        vivos = {pool.submit(pedir, t) for t in lista}
        try:
            while vivos:
                listos, vivos = wait(vivos, return_when=FIRST_COMPLETED)
                for futuro in listos:
                    elementos, subteselas = futuro.result()
                    for f in elementos:
                        filas[f["id"]] = f  # un mismo elemento puede salir en dos teselas
                    vivos |= {pool.submit(pedir, s) for s in subteselas}
                    hechas += 1
                    total += len(subteselas)
                    progreso(hechas / total)
        except BaseException:
            for futuro in vivos:
                futuro.cancel()
            raise
    return _gdf(list(filas.values()), crs)


# ------------------------------------------------------------------ caché por estudio
def _gdf(filas: list[dict], crs: str) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(filas, geometry="geometry", crs=crs) if filas \
        else gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=crs)


def _guardar(ruta: Path, g: gpd.GeoDataFrame) -> None:
    w = g.to_crs("EPSG:4326")
    props = [c for c in w.columns if c != "geometry"]
    feats = [{"type": "Feature", "geometry": mapping(f.geometry), "properties": {c: f[c] for c in props}}
             for _, f in w.iterrows()]
    ruta.write_text(json.dumps({"type": "FeatureCollection", "fecha": date.today().isoformat(), "features": feats},
                               ensure_ascii=False, default=str), encoding="utf-8")


def _cargar(ruta: Path, crs: str) -> tuple[gpd.GeoDataFrame, str] | None:
    try:
        d = json.loads(ruta.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    filas = [{**f["properties"], "geometry": shape(f["geometry"])} for f in d["features"]]
    return _gdf(filas, "EPSG:4326").to_crs(crs), d.get("fecha", "")


def _con_cache(ruta: Path, crs: str, consulta) -> tuple[gpd.GeoDataFrame, str]:
    guardado = _cargar(ruta, crs)
    if guardado is not None:
        return guardado
    g = consulta()
    _guardar(ruta, g)
    return g, date.today().isoformat()


# ------------------------------------------------------------------ resultado para el estudio
def _zona(g: gpd.GeoDataFrame, cfg: dict, ambito) -> dict | None:
    if g.empty:
        return None
    inter = unary_union(list(g.geometry.buffer(float(cfg["distancia"]), quad_segs=4))).intersection(ambito)
    if inter.area <= 0.5:
        return None
    return {"capa": cfg["criterio"], "zona": cfg["etiqueta"], "geom": inter}


def _lista_parcelas(g: gpd.GeoDataFrame, ambito) -> list[dict]:
    out = []
    for _, f in g.iterrows():
        dentro = f.geometry.intersection(ambito).area
        pct = 100 * dentro / (f.geometry.area or 1.0)
        if pct >= PCT_MIN_PARCELA:
            out.append({"ref": f["ref"], "area_m2": round(float(f["area_m2"]), 1), "en_ambito_m2": round(dentro, 1),
                        "pct_parcela": round(pct, 1)})
    return sorted(out, key=lambda x: -x["en_ambito_m2"])


def obtener(ambito, crs: str, ccaa: list[str], carpeta: Path, progreso=None) -> dict:
    """Devuelve zonas que restan, criterios comprobados, parcelas, fuentes, avisos y capas del visor."""
    out = {"zonas": [], "comprobados": set(), "parcelas": None, "fuentes": [], "avisos": [], "visor": {}}
    cfg = implantacion().get("remotas", {})
    if not settings.DATOS_REMOTOS:
        return out
    carpeta.mkdir(exist_ok=True)

    def prog(msg, frac=0.0):
        if progreso:
            progreso(msg, frac)

    def consultar(clave, nombre, fuente, consulta) -> gpd.GeoDataFrame | None:
        try:
            g, fecha = _con_cache(carpeta / f"{clave}.geojson", crs, consulta)
        except RemotoError as e:
            estado = "sin_cobertura" if isinstance(e, SinCobertura) else "error"
            out["fuentes"].append({"nombre": nombre, "fuente": fuente, "estado": estado, "elementos": None,
                                   "fecha": None, "mensaje": str(e)})
            if estado == "error":
                out["avisos"].append(f"{nombre}: no se ha podido consultar. {e} Sus criterios quedan sin comprobar.")
            return None
        except Exception as e:  # noqa: BLE001
            log.exception("Fallo consultando %s", clave)
            out["fuentes"].append({"nombre": nombre, "fuente": fuente, "estado": "error", "elementos": None,
                                   "fecha": None, "mensaje": "Error interno."})
            out["avisos"].append(f"{nombre}: no se ha podido procesar ({e.__class__.__name__}). "
                                 "Sus criterios quedan sin comprobar.")
            return None
        out["fuentes"].append({"nombre": nombre, "fuente": fuente, "estado": "ok", "elementos": len(g), "fecha": fecha,
                               "mensaje": ""})
        if not g.empty:
            out["visor"][clave] = f"remotas/{clave}.geojson"
        return g

    def aplicar(g, clave):
        c = cfg.get(clave)
        if c is None:
            return
        z = _zona(g, c, ambito)
        if z:
            out["zonas"].append(z)
        out["comprobados"].add(c["criterio"])

    margen_vial = max((float(cfg[k]["distancia"]) for k in ("autovia", "carretera", "camino") if k in cfg), default=0.0)
    prog("Consultando viales (IGN)")
    g = consultar("viales", "Viales", FUENTE_VIALES, lambda: viales(ambito, crs, margen_vial + 1))
    if g is not None:
        for clase in ("autovia", "carretera", "camino"):
            aplicar(g[g["clase"] == clase] if not g.empty else g, clase)

    margen_edif = max((float(cfg[k]["distancia"]) for k in ("construcciones", "viviendas") if k in cfg), default=0.0)
    g = consultar("edificios", "Edificios", FUENTE_CATASTRO, lambda: _catastro(
        "wfsBU.aspx", "BU.BUILDING", leer_edificios, ambito.buffer(margen_edif + 1), crs, ccaa,
        lambda f: prog("Consultando edificios (Catastro)", f)))
    if g is not None:
        aplicar(g, "construcciones")
        aplicar(g[g["vivienda"].astype(bool)] if not g.empty else g, "viviendas")

    g = consultar("parcelas", "Parcelas catastrales", FUENTE_CATASTRO, lambda: _catastro(
        "wfsCP.aspx", "CP.CADASTRALPARCEL", leer_parcelas, ambito, crs, ccaa,
        lambda f: prog("Consultando parcelas (Catastro)", f)))
    if g is not None:
        out["parcelas"] = _lista_parcelas(g, ambito) if not g.empty else []
    return out
