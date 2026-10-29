"""Comprueba el catálogo de capas y optimiza los SHP grandes.

Uso:
    python scripts/preparar_capas.py              # informe del estado de las capas
    python scripts/preparar_capas.py --convertir  # convierte los SHP del catálogo a GPKG indexado

Usa GEODATOS_DIR (por defecto ./geodatos). Los SHP originales no se modifican: se crea un .gpkg al lado
con el mismo nombre y el programa lo usa automáticamente.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pyogrio  # noqa: E402

from prefact import settings  # noqa: E402
from prefact.catalogo import catalogo  # noqa: E402


def informe() -> None:
    cat = catalogo()
    print(f"GEODATOS_DIR = {settings.GEODATOS_DIR}\n")
    print(f"Límites: {'OK ' if cat.fichero_limites.exists() else 'FALTA'}  {cat.limites['fichero']}")
    print("\nModelos del terreno (por prioridad):")
    for m in cat.mdt:
        fs = m.ficheros()
        print(f"  {'OK ' if fs else '-- '} {m.nombre:<38} {len(fs):>5} fichero(s)  [{m.patron}]")
    print("\nCapas de condicionantes:")
    for c in cat.capas:
        disp = c.ficheros_disponibles
        estado = "OK " if disp else "-- "
        detalle = ", ".join(p.name for p in disp) if disp else "no instalada: " + ", ".join(
            str(f.relative_to(settings.GEODATOS_DIR)) for f in c.ficheros)
        print(f"  {estado} {c.nombre:<45} {detalle}")
        for f in disp:
            try:
                info = pyogrio.read_info(f)
                if not info.get("crs"):
                    print(f"       ¡AVISO! {f.name} no tiene sistema de referencia (.prj)")
            except Exception as e:  # noqa: BLE001
                print(f"       ¡ERROR! {f.name}: {e}")


def _convertir(origen: Path, destino: Path) -> None:
    """Copia por bloques con pyogrio (no requiere GDAL de línea de comandos)."""
    info = pyogrio.read_info(origen)
    total = info["features"]
    bloque = 20000
    capa = origen.stem
    escrito = 0
    for ini in range(0, total, bloque):
        gdf = pyogrio.read_dataframe(origen, skip_features=ini, max_features=bloque)
        gdf["geometry"] = gdf.geometry.make_valid()
        pyogrio.write_dataframe(gdf, destino, layer=capa, driver="GPKG", append=ini > 0,
                                layer_options={"SPATIAL_INDEX": "YES"})
        escrito += len(gdf)
        print(f"\r    {origen.name}: {escrito:,}/{total:,} entidades", end="", flush=True)
    print()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--convertir", action="store_true", help="Convertir SHP del catálogo a GPKG indexado")
    ap.add_argument("--min-mb", type=float, default=50, help="Tamaño mínimo del SHP para convertir (MB)")
    a = ap.parse_args()
    if a.convertir:
        for c in catalogo().capas:
            for f in c.ficheros:
                if f.suffix.lower() != ".shp" or not f.exists():
                    continue
                destino = f.with_suffix(".gpkg")
                mb = f.stat().st_size / 1e6
                if destino.exists():
                    print(f"  ya existe {destino.name}")
                    continue
                if mb < a.min_mb:
                    continue
                t0 = time.time()
                tmp = destino.with_name(destino.stem + ".tmp.gpkg")
                tmp.unlink(missing_ok=True)
                _convertir(f, tmp)
                tmp.rename(destino)
                print(f"    -> {destino.name} en {time.time() - t0:,.0f} s")
        print()
    informe()


if __name__ == "__main__":
    main()
