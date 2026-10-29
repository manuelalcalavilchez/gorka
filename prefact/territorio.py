"""Detección de comunidad autónoma, provincia y municipios del ámbito."""
from __future__ import annotations

import geopandas as gpd

from .catalogo import catalogo

NOMBRES_CCAA = {
    "01": "Andalucía", "02": "Aragón", "03": "Principado de Asturias", "04": "Illes Balears",
    "05": "Canarias", "06": "Cantabria", "07": "Castilla y León", "08": "Castilla-La Mancha",
    "09": "Cataluña", "10": "Comunitat Valenciana", "11": "Extremadura", "12": "Galicia",
    "13": "Comunidad de Madrid", "14": "Región de Murcia", "15": "Comunidad Foral de Navarra",
    "16": "País Vasco", "17": "La Rioja", "18": "Ceuta", "19": "Melilla",
}


def _codigo(valor) -> str | None:
    try:
        return str(int(float(valor))).zfill(11)
    except (TypeError, ValueError):
        return None


def _nivel(codigo: str) -> str:
    """Código IGN de 11 dígitos: 34 | CCAA(2) | PROV(2) | MUN(5)."""
    if codigo[2:] == "000000000":
        return "pais"
    if codigo[4:] == "0000000":
        return "ccaa"
    if codigo[6:] == "00000":
        return "provincia"
    return "municipio"


def detectar(ambito) -> dict:
    cat = catalogo()
    lim = cat.limites
    fichero = cat.fichero_limites
    res = {"ccaa": [], "provincias": [], "municipios": [], "aviso": None}
    if not fichero.exists():
        res["aviso"] = "Capa de límites administrativos no instalada: no se puede detectar la comunidad."
        return res
    g = ambito.geom_wgs84
    try:
        gdf = gpd.read_file(fichero, layer=lim.get("capa"), bbox=tuple(g.bounds), engine="pyogrio")
    except Exception as e:  # noqa: BLE001
        res["aviso"] = f"No se pudo leer la capa de límites: {e}"
        return res
    if gdf.empty:
        res["aviso"] = "El ámbito no cae en ninguna unidad administrativa conocida."
        return res
    gdf = gdf.to_crs("EPSG:4326") if gdf.crs else gdf.set_crs("EPSG:4326")
    gdf = gdf[gdf.intersects(g)]
    cod_campo, nom_campo = lim.get("campo_codigo", "nationalCode"), lim.get("campo_nombre", "text")
    geom_utm = ambito.geom if ambito.tipo == "poligono" else ambito.geom.buffer(1)
    for _, f in gdf.iterrows():
        codigo = _codigo(f[cod_campo])
        if codigo is None:
            continue
        nivel = _nivel(codigo)
        nombre = str(f.get(nom_campo, ""))
        if nivel == "ccaa":
            res["ccaa"].append({"codigo": codigo[2:4], "nombre": NOMBRES_CCAA.get(codigo[2:4], nombre)})
        elif nivel == "provincia":
            res["provincias"].append({"codigo": codigo[4:6], "nombre": nombre})
        elif nivel == "municipio":
            inter = gpd.GeoSeries([f.geometry], crs="EPSG:4326").to_crs(ambito.crs).iloc[0].intersection(geom_utm)
            pct = 100 * inter.area / geom_utm.area if geom_utm.area else 0
            res["municipios"].append({"ine": codigo[6:], "nombre": nombre, "pct": round(pct, 1)})
    # Si no vino el polígono de CCAA, se deduce del código del municipio
    if not res["ccaa"]:
        ccaas = {c[2:4] for c in (_codigo(v) for v in gdf[cod_campo]) if c}
        res["ccaa"] = [{"codigo": c, "nombre": NOMBRES_CCAA.get(c, c)} for c in sorted(ccaas) if c != "00"]
    res["municipios"].sort(key=lambda m: -m["pct"])
    seen = set()
    res["ccaa"] = [c for c in res["ccaa"] if not (c["codigo"] in seen or seen.add(c["codigo"]))]
    return res


def codigos_ccaa(territorio: dict) -> list[str]:
    return [c["codigo"] for c in territorio.get("ccaa", [])]
