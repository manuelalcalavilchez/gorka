"""Descarga Leaflet 1.9.4 y Chart.js 4.4.4 desde el registro de npm y comprueba sus hashes.

Se ejecuta una vez (lo hacen el Dockerfile e iniciar.bat). La aplicación sirve después estos ficheros
desde /static/vendor, sin depender de ningún CDN en tiempo de uso.
"""
from __future__ import annotations

import hashlib
import io
import sys
import tarfile
import urllib.request
from pathlib import Path

DESTINO = Path(__file__).resolve().parent.parent / "prefact" / "web" / "static" / "vendor"

PAQUETES = {
    "https://registry.npmjs.org/leaflet/-/leaflet-1.9.4.tgz": {
        "package/dist/leaflet.js": ("leaflet/leaflet.js", "db49d009c841f5ca34a888c96511ae936fd9f5533e90d8b2c4d57596f4e5641a"),
        "package/dist/leaflet.css": ("leaflet/leaflet.css", "a7837102824184820dfa198d1ebcd109ff6d0ff9a2672a074b9a1b4d147d04c6"),
        "package/dist/images/layers.png": ("leaflet/images/layers.png", "1dbbe9d028e292f36fcba8f8b3a28d5e8932754fc2215b9ac69e4cdecf5107c6"),
        "package/dist/images/layers-2x.png": ("leaflet/images/layers-2x.png", "066daca850d8ffbef007af00b06eac0015728dee279c51f3cb6c716df7c42edf"),
        "package/dist/images/marker-icon.png": ("leaflet/images/marker-icon.png", "574c3a5cca85f4114085b6841596d62f00d7c892c7b03f28cbfa301deb1dc437"),
        "package/dist/images/marker-icon-2x.png": ("leaflet/images/marker-icon-2x.png", "00179c4c1ee830d3a108412ae0d294f55776cfeb085c60129a39aa6fc4ae2528"),
        "package/dist/images/marker-shadow.png": ("leaflet/images/marker-shadow.png", "264f5c640339f042dd729062cfc04c17f8ea0f29882b538e3848ed8f10edb4da"),
        "package/LICENSE": ("leaflet/LICENSE", "53e8dc25862014e4324741ca18fbe3611e11d42ef69f59f86ea8c5389647d4cb"),
    },
    "https://registry.npmjs.org/chart.js/-/chart.js-4.4.4.tgz": {
        "package/dist/chart.umd.js": ("chart.umd.js", "fed6a739f8d0f0687174de6cd14745fc0fc7809144ab113d22908a26bf0d7fea"),
        "package/LICENSE.md": ("CHARTJS_LICENSE.md", "41a84aa2caba645f966a18d9c2056b73e6d3a81d80bc0046bc0011a2634d4cce"),
    },
}


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def instalado() -> bool:
    return all((DESTINO / d).exists() and _sha((DESTINO / d).read_bytes()) == h
               for ficheros in PAQUETES.values() for d, h in ficheros.values())


def main() -> int:
    if instalado():
        print("Librerías del visor ya instaladas.")
        return 0
    for url, ficheros in PAQUETES.items():
        print(f"Descargando {url.rsplit('/', 1)[-1]} ...")
        with urllib.request.urlopen(url, timeout=60) as r:
            datos = r.read()
        with tarfile.open(fileobj=io.BytesIO(datos), mode="r:gz") as tar:
            for origen, (destino, hash_esperado) in ficheros.items():
                contenido = tar.extractfile(origen).read()
                if _sha(contenido) != hash_esperado:
                    print(f"ERROR: hash inesperado en {origen}", file=sys.stderr)
                    return 1
                ruta = DESTINO / destino
                ruta.parent.mkdir(parents=True, exist_ok=True)
                ruta.write_bytes(contenido)
    print(f"Librerías del visor instaladas en {DESTINO}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
