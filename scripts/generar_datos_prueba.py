"""Genera un GEODATOS_DIR sintético (zona ficticia en Tabernas, Almería) para pruebas y demo.

Uso:  python scripts/generar_datos_prueba.py  <carpeta_destino>
Crea las mismas rutas que el catálogo real (config/capas.yaml) y ámbitos de ejemplo en <destino>/_ambitos.
"""
from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import LineString, Polygon, box

X0, Y0, X1, Y1 = 560000, 4096000, 566000, 4102000  # EPSG:25830
RES = 5.0


def terreno() -> np.ndarray:
    w, h = int((X1 - X0) / RES), int((Y1 - Y0) / RES)
    xs = X0 + (np.arange(w) + 0.5) * RES
    ys = Y1 - (np.arange(h) + 0.5) * RES
    X, Y = np.meshgrid(xs, ys)
    z = 380 + 0.02 * (Y - Y0)                                    # sube hacia el norte
    z += 60 * np.exp(-(((X - 561200) / 700) ** 2 + ((Y - 4100500) / 600) ** 2))   # cerro NO
    z += 35 * np.exp(-(((X - 565000) / 500) ** 2 + ((Y - 4097500) / 800) ** 2))   # loma SE
    z -= 18 * np.exp(-(((X - 563000) / 120) ** 2))                 # rambla N-S en x=563000
    z += 1.5 * np.sin(X / 90) * np.cos(Y / 110)                    # rugosidad
    return z.astype("float32")


def main(destino: Path) -> None:
    destino = destino.resolve()
    g = lambda *p: destino.joinpath(*p)  # noqa: E731
    for sub in ("limites", "mdt/mdt05", "hidrologia", "medio_natural/red_natura", "infraestructuras/vias_pecuarias",
                "_ambitos"):
        g(sub).mkdir(parents=True, exist_ok=True)

    # MDT05
    z = terreno()
    with rasterio.open(g("mdt", "mdt05", "MDT05_prueba_tabernas.tif"), "w", driver="GTiff", height=z.shape[0],
                       width=z.shape[1], count=1, dtype="float32", crs="EPSG:25830",
                       transform=from_origin(X0, Y1, RES, RES), nodata=-9999, compress="deflate") as dst:
        dst.write(z, 1)

    crs = "EPSG:25830"
    a4258 = lambda gdf: gdf.to_crs("EPSG:4258")  # noqa: E731

    # Límites: CCAA, provincia y dos municipios
    oeste = box(X0 - 5000, Y0 - 5000, 563500, Y1 + 5000)
    este = box(563500, Y0 - 5000, X1 + 5000, Y1 + 5000)
    lim = gpd.GeoDataFrame({
        "nationalCode": [34010000000, 34010400000, 34010404088, 34010404066],
        "text": ["Andalucía", "Almería", "Tabernas", "Níjar"],
    }, geometry=[box(X0 - 9000, Y0 - 9000, X1 + 9000, Y1 + 9000), box(X0 - 8000, Y0 - 8000, X1 + 8000, Y1 + 8000),
                 oeste, este], crs=crs)
    a4258(lim).to_file(g("limites", "unidades_administrativas_IGN.gpkg"), layer="unidades_administrativas")

    # Rambla (eje) y DPH estimado
    rambla = LineString([(563000, Y0), (563010, 4099000), (562990, Y1)])
    a4258(gpd.GeoDataFrame({"nom_hidro": ["Rambla de Prueba"]}, geometry=[rambla], crs=crs)).to_file(
        g("hidrologia", "red_hidrografica_MITECO_2021.gpkg"), layer="red_hidrografica_2021")
    a4258(gpd.GeoDataFrame({"rio": ["Rambla de Prueba"]}, geometry=[rambla.buffer(15)], crs=crs)).to_file(
        g("hidrologia", "DPH_estimado_MITECO.gpkg"), layer="dph_estimado")

    # Red Natura (esquina NE)
    a4258(gpd.GeoDataFrame({"text": ["ZEC Sierra de Prueba"], "localId": ["ES6110099"]},
                           geometry=[Polygon([(564200, 4100200), (X1 + 500, 4100200), (X1 + 500, Y1 + 500),
                                              (564200, Y1 + 500)])], crs=crs)).to_file(
        g("medio_natural", "red_natura", "PS.Natura2000_p_2025.gpkg"), layer="PS.Natura2000_p_2025")

    # Vía pecuaria E-O (colada)
    a4258(gpd.GeoDataFrame({"nb_via": ["COLADA DEL CAMINO DE PRUEBA"], "cd_tipo_vp": ["CO"]},
                           geometry=[LineString([(X0, 4097200), (X1, 4097350)])], crs=crs)).to_file(
        g("infraestructuras", "vias_pecuarias", "RGVP_BDN_2024.gpkg"), layer="RGVP_BDN_2024")

    # Ámbitos de ejemplo
    parcela = Polygon([(562300, 4098300), (562950, 4098350), (562920, 4098900), (562280, 4098850)])
    gpd.GeoDataFrame({"nombre": ["Parcela explanación"]}, geometry=[parcela], crs=crs).to_crs("EPSG:4326").to_file(
        g("_ambitos", "parcela.geojson"), driver="GeoJSON")
    gpd.GeoDataFrame({"Name": ["Parcela"]}, geometry=[parcela], crs=crs).to_crs("EPSG:4326").to_file(
        g("_ambitos", "parcela.kml"), driver="KML")
    cantera = Polygon([(560800, 4100100), (561600, 4100050), (561650, 4100800), (560850, 4100850)])
    gpd.GeoDataFrame({"nombre": ["Cantera"]}, geometry=[cantera], crs=crs).to_crs("EPSG:4326").to_file(
        g("_ambitos", "cantera.geojson"), driver="GeoJSON")
    eje = LineString([(560600, 4097800), (561500, 4098400), (562600, 4098700), (563600, 4099300), (564800, 4099500)])
    gpd.GeoDataFrame({"nombre": ["Camino"]}, geometry=[eje], crs=crs).to_crs("EPSG:4326").to_file(
        g("_ambitos", "eje_camino.geojson"), driver="GeoJSON")
    print(f"Datos de prueba creados en {destino}")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "geodatos_prueba"))
