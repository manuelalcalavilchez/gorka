"""Lectura y validación del ámbito del proyecto (polígono o eje)."""
from __future__ import annotations

import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import pyogrio
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon, mapping
from shapely.ops import linemerge, unary_union

EXTENSIONES = {".kml", ".kmz", ".geojson", ".json", ".gpkg", ".zip", ".gml", ".xml", ".dxf", ".shp"}


class AmbitoError(ValueError):
    """Error de entrada comprensible para el usuario."""


@dataclass
class Ambito:
    geom: Polygon | MultiPolygon | LineString  # en CRS de trabajo
    crs: str  # EPSG de trabajo (UTM)
    tipo: str  # "poligono" | "linea"
    geom_wgs84: object
    nombre_fichero: str
    n_entidades: int

    @property
    def area_m2(self) -> float:
        return float(self.geom.area) if self.tipo == "poligono" else 0.0

    @property
    def longitud_m(self) -> float:
        return float(self.geom.length) if self.tipo == "linea" else float(self.geom.boundary.length)

    @property
    def centroide_wgs84(self) -> tuple[float, float]:
        c = self.geom_wgs84.centroid
        return (c.x, c.y)

    def a_geojson(self) -> dict:
        return {"type": "FeatureCollection",
                "features": [{"type": "Feature", "properties": {"nombre": "Ámbito"},
                              "geometry": mapping(self.geom_wgs84)}]}


def crs_trabajo(lon: float, lat: float) -> str:
    """UTM ETRS89 del huso natural (REGCAN95 en Canarias)."""
    if lat < 30:  # Canarias
        return "EPSG:4083"  # REGCAN95 / UTM 28N
    if lon < -6:
        return "EPSG:25829"
    if lon >= 0:
        return "EPSG:25831"
    return "EPSG:25830"


def _leer_dxf(ruta: Path) -> gpd.GeoDataFrame:
    import ezdxf

    try:
        doc = ezdxf.readfile(ruta)
    except Exception as e:  # noqa: BLE001
        raise AmbitoError(f"No se pudo leer el DXF: {e}") from e
    geoms = []
    for e in doc.modelspace():
        t = e.dxftype()
        try:
            if t == "LWPOLYLINE":
                pts = [(p[0], p[1]) for p in e.get_points("xy")]
                cerrada = e.closed
            elif t == "POLYLINE":
                pts = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
                cerrada = e.is_closed
            elif t == "LINE":
                pts = [(e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)]
                cerrada = False
            else:
                continue
        except Exception:  # noqa: BLE001
            continue
        if len(pts) >= 3 and (cerrada or pts[0] == pts[-1]):
            geoms.append(Polygon(pts))
        elif len(pts) >= 2:
            geoms.append(LineString(pts))
    if not geoms:
        raise AmbitoError("El DXF no contiene polilíneas ni líneas utilizables.")
    # Los DXF no llevan CRS: se asume ETRS89 UTM 30N salvo que las coordenadas no encajen
    x = geoms[0].centroid.x
    crs = "EPSG:25830"
    if x < 100000 or x > 1000000:
        raise AmbitoError("El DXF no parece estar en coordenadas UTM ETRS89. Expórtalo en ETRS89 UTM o usa KML/SHP.")
    return gpd.GeoDataFrame(geometry=geoms, crs=crs)


def _leer_vectorial(ruta: Path) -> gpd.GeoDataFrame:
    ext = ruta.suffix.lower()
    if ext == ".dxf":
        return _leer_dxf(ruta)
    if ext in (".zip", ".kmz"):
        tmp = Path(tempfile.mkdtemp(prefix="ambito_"))
        with zipfile.ZipFile(ruta) as z:
            for n in z.namelist():
                destino = (tmp / n).resolve()
                if not str(destino).startswith(str(tmp.resolve())):
                    raise AmbitoError("Fichero comprimido no válido.")
            z.extractall(tmp)
        candidatos = [p for p in tmp.rglob("*") if p.suffix.lower() in (".shp", ".kml", ".geojson", ".gpkg", ".gml")]
        if not candidatos:
            raise AmbitoError("El comprimido no contiene SHP, KML, GeoJSON, GPKG ni GML.")
        ruta = candidatos[0]
    try:
        capas = pyogrio.list_layers(ruta)
    except Exception as e:  # noqa: BLE001
        raise AmbitoError(f"No se reconoce el formato del fichero ({e}).") from e
    partes = []
    for nombre, tipo_geom in capas:
        if tipo_geom is None:
            continue
        try:
            g = gpd.read_file(ruta, layer=nombre, engine="pyogrio")
        except Exception:  # noqa: BLE001
            continue
        if not g.empty and g.geometry.notna().any():
            partes.append(gpd.GeoDataFrame(geometry=list(g.geometry.values), crs=g.crs))
    if not partes:
        if ext in (".xml", ".gml"):
            raise AmbitoError("El fichero no contiene geometría. Si es de Catastro, descarga el GML de la parcela "
                              "(no el XML de datos alfanuméricos).")
        raise AmbitoError("El fichero no contiene geometrías.")
    crs = next((p.crs for p in partes if p.crs), None)
    if crs is None:
        x = partes[0].geometry.iloc[0].centroid.x
        crs = "EPSG:4326" if -20 < x < 6 else "EPSG:25830"
    geoms = []
    for p in partes:
        p = p.set_crs(crs) if p.crs is None else p.to_crs(crs)
        geoms.extend(g for g in p.geometry if g is not None and not g.is_empty)
    return gpd.GeoDataFrame(geometry=geoms, crs=crs)


def leer_ambito(ruta: str | Path, modo: str) -> Ambito:
    """Lee el fichero y devuelve el ámbito en CRS de trabajo.

    modo == "vial" exige un eje (línea); el resto, un polígono.
    """
    ruta = Path(ruta)
    if ruta.suffix.lower() not in EXTENSIONES:
        raise AmbitoError(f"Formato no admitido ({ruta.suffix}). Usa KML, KMZ, GeoJSON, GPKG, SHP en ZIP, GML o DXF.")
    gdf = _leer_vectorial(ruta)
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
    if gdf.empty:
        raise AmbitoError("El fichero no contiene geometrías válidas.")
    wgs = gdf.to_crs("EPSG:4326")
    c = unary_union(list(wgs.geometry)).centroid
    if not (-19 < c.x < 5 and 27 < c.y < 44.5):
        raise AmbitoError("El ámbito está fuera de España.")
    crs = crs_trabajo(c.x, c.y)
    g = gdf.to_crs(crs)

    polis = [x for x in g.geometry if x.geom_type in ("Polygon", "MultiPolygon")]
    lineas = [x for x in g.geometry if x.geom_type in ("LineString", "MultiLineString")]
    if modo == "vial":
        if not lineas:
            raise AmbitoError("El modo vial necesita un eje (línea). El fichero no contiene líneas.")
        geom = unary_union(lineas)
        if isinstance(geom, MultiLineString):
            geom = linemerge(geom)
        if isinstance(geom, MultiLineString):
            # Se toma el tramo más largo
            geom = max(geom.geoms, key=lambda l: l.length)
        if geom.length < 20:
            raise AmbitoError("El eje es demasiado corto (< 20 m).")
        tipo = "linea"
    else:
        if not polis:
            if lineas:
                raise AmbitoError("Este modo necesita un polígono cerrado; el fichero solo contiene líneas "
                                  "(¿querías el modo vial?).")
            raise AmbitoError("El fichero no contiene polígonos.")
        geom = unary_union([p.buffer(0) for p in polis])
        if geom.area < 100:
            raise AmbitoError("El ámbito es demasiado pequeño (< 100 m²).")
        tipo = "poligono"
    geom_wgs = gpd.GeoSeries([geom], crs=crs).to_crs("EPSG:4326").iloc[0]
    return Ambito(geom=geom, crs=crs, tipo=tipo, geom_wgs84=geom_wgs,
                  nombre_fichero=ruta.name, n_entidades=len(gdf))
