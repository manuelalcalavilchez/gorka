"""Carga y consulta del catálogo de capas (config/capas.yaml)."""
from __future__ import annotations

import glob
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from . import settings

SEMAFOROS = ("rojo", "ambar", "info", "verde", "gris")
ORDEN_GRAVEDAD = {"rojo": 4, "ambar": 3, "gris": 2, "info": 1, "verde": 0}


@dataclass
class Zona:
    etiqueta: str
    semaforo: str
    distancia: float = 0.0
    distancia_por_campo: dict | None = None

    @property
    def distancia_max(self) -> float:
        if self.distancia_por_campo:
            vals = list(self.distancia_por_campo.get("valores", {}).values())
            vals.append(self.distancia_por_campo.get("defecto", 0))
            return float(max(vals))
        return float(self.distancia)


@dataclass
class Capa:
    id: str
    nombre: str
    tema: str
    ficheros: list[Path]
    zonas: list[Zona]
    cobertura: dict
    parcial: Any = None
    campo_etiqueta: str | None = None
    campos_etiqueta_alternativos: list[str] = field(default_factory=list)
    campos_informe: list[str] = field(default_factory=list)
    fuente: str = ""
    fecha: str = ""
    notas: str = ""

    @property
    def ficheros_disponibles(self) -> list[Path]:
        """Ficheros existentes; si junto a un .shp hay un .gpkg con el mismo nombre, se usa el .gpkg
        (indexado, mucho más rápido). Lo genera scripts/preparar_capas.py."""
        out = []
        for f in self.ficheros:
            g = f.with_suffix(".gpkg")
            if f.suffix.lower() == ".shp" and g.exists():
                out.append(g)
            elif f.exists():
                out.append(f)
        return out

    @property
    def instalada(self) -> bool:
        return bool(self.ficheros_disponibles)

    @property
    def distancia_max(self) -> float:
        return max((z.distancia_max for z in self.zonas), default=0.0)

    def cubre(self, ccaa: list[str]) -> bool:
        """True si la capa tiene cobertura en al menos una de las CCAA del ámbito."""
        cob = self.cobertura or {}
        if cob.get("todas"):
            return True
        if "incluye" in cob:
            return any(c in cob["incluye"] for c in ccaa)
        if "excluye" in cob:
            return any(c not in cob["excluye"] for c in ccaa) if ccaa else True
        return True

    def es_parcial(self, ccaa: list[str]) -> bool:
        if self.parcial == "todas":
            return True
        if isinstance(self.parcial, list):
            return any(c in self.parcial for c in ccaa)
        return False


@dataclass
class FuenteMDT:
    id: str
    nombre: str
    patron: str
    resolucion_m: float
    fuente: str = ""
    solo_contexto: bool = False

    def ficheros(self) -> list[Path]:
        base = settings.GEODATOS_DIR
        return sorted(Path(p) for p in glob.glob(str(base / self.patron), recursive=True))


@dataclass
class Catalogo:
    capas: list[Capa]
    mdt: list[FuenteMDT]
    limites: dict
    mdt_resolucion_fiable_m: float

    def capa(self, id_: str) -> Capa:
        for c in self.capas:
            if c.id == id_:
                return c
        raise KeyError(id_)

    @property
    def fichero_limites(self) -> Path:
        return settings.GEODATOS_DIR / self.limites["fichero"]


def _zona(d: dict) -> Zona:
    sem = d.get("semaforo", "ambar")
    if sem not in SEMAFOROS:
        raise ValueError(f"Semáforo no válido: {sem}")
    return Zona(
        etiqueta=d.get("etiqueta", ""),
        semaforo=sem,
        distancia=float(d.get("distancia", 0) or 0),
        distancia_por_campo=d.get("distancia_por_campo"),
    )


def cargar(ruta: Path | None = None) -> Catalogo:
    ruta = ruta or settings.CONFIG_DIR / "capas.yaml"
    with open(ruta, encoding="utf-8") as f:
        datos = yaml.safe_load(f)
    base = settings.GEODATOS_DIR
    capas = []
    for c in datos.get("capas", []):
        capas.append(
            Capa(
                id=c["id"],
                nombre=c["nombre"],
                tema=c.get("tema", "Otros"),
                ficheros=[base / s["fichero"] for s in c.get("fuentes", [])],
                zonas=[_zona(z) for z in c.get("zonas", [])],
                cobertura=c.get("cobertura", {"todas": True}),
                parcial=c.get("parcial"),
                campo_etiqueta=c.get("campo_etiqueta"),
                campos_etiqueta_alternativos=c.get("campos_etiqueta_alternativos", []),
                campos_informe=c.get("campos_informe", []),
                fuente=c.get("fuente", ""),
                fecha=str(c.get("fecha", "")),
                notas=c.get("notas", ""),
            )
        )
    mdt = [
        FuenteMDT(
            id=m["id"],
            nombre=m.get("nombre", m["id"]),
            patron=m["patron"],
            resolucion_m=float(m["resolucion_m"]),
            fuente=m.get("fuente", ""),
            solo_contexto=bool(m.get("solo_contexto", False)),
        )
        for m in datos.get("mdt", [])
    ]
    return Catalogo(
        capas=capas,
        mdt=mdt,
        limites=datos.get("limites", {}),
        mdt_resolucion_fiable_m=float(datos.get("mdt_resolucion_fiable_m", 25)),
    )


@lru_cache(maxsize=1)
def catalogo() -> Catalogo:
    return cargar()


def estado_catalogo() -> list[dict]:
    """Resumen de qué capas están instaladas (para la página de inicio)."""
    cat = catalogo()
    filas = [
        {"id": c.id, "nombre": c.nombre, "tema": c.tema, "instalada": c.instalada,
         "ficheros": [str(f.relative_to(settings.GEODATOS_DIR)) for f in c.ficheros_disponibles]}
        for c in cat.capas
    ]
    for m in cat.mdt:
        filas.append({"id": m.id, "nombre": m.nombre, "tema": "MDT", "instalada": bool(m.ficheros()),
                      "ficheros": [str(p.relative_to(settings.GEODATOS_DIR)) for p in m.ficheros()[:5]]})
    return filas
