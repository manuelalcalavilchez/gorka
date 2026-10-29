"""Orientación de tramitación y semáforo global.

ORIENTATIVO: no sustituye el análisis jurídico ni la consulta a los órganos competentes.
"""
from __future__ import annotations

from .catalogo import ORDEN_GRAVEDAD

NORMATIVA_AUTONOMICA = {
    "01": "Ley 7/2007 de Gestión Integrada de la Calidad Ambiental de Andalucía (GICA)",
}


def _afecta(afecciones: list[dict], id_: str, semaforos=("rojo", "ambar")) -> bool:
    for a in afecciones:
        if a["id"] == id_ and a.get("estado") == "afecta":
            return any(z.get("afecta") and z.get("semaforo") in semaforos for z in a.get("zonas", []))
    return False


def orientar(modo: str, area_m2: float, volumenes: dict, afecciones: list[dict], ccaa: list[str]) -> list[dict]:
    t = []
    ha = area_m2 / 10000

    if modo == "extraccion":
        if ha > 25:
            t.append({"nivel": "alto", "tramite": "Evaluación de impacto ambiental ordinaria",
                      "motivo": f"Extracción a cielo abierto con {ha:.1f} ha de superficie afectada "
                                "(Ley 21/2013, Anexo I: más de 25 ha)."})
        else:
            t.append({"nivel": "medio", "tramite": "Evaluación de impacto ambiental (como mínimo simplificada)",
                      "motivo": "Extracción a cielo abierto (Ley 21/2013, Anexo II). Comprobar umbrales de "
                                "volumen anual y normativa autonómica."})
        t.append({"nivel": "medio", "tramite": "Autorización minera y plan de restauración",
                  "motivo": "Ley 22/1973 de Minas y Real Decreto 975/2009 de residuos mineros y restauración."})

    if _afecta(afecciones, "red_natura", ("ambar",)):
        t.append({"nivel": "alto", "tramite": "Evaluación de repercusiones sobre Red Natura 2000",
                  "motivo": "El ámbito está dentro de un espacio Red Natura 2000: el proyecto debe someterse, "
                            "como mínimo, a evaluación ambiental simplificada (Ley 21/2013, art. 7.2.b)."})
    if _afecta(afecciones, "enp", ("ambar",)):
        t.append({"nivel": "alto", "tramite": "Consulta al órgano gestor del Espacio Natural Protegido",
                  "motivo": "Comprobar usos permitidos en el PORN/PRUG del espacio."})
    if _afecta(afecciones, "dph_estimado") or _afecta(afecciones, "cauces"):
        t.append({"nivel": "alto", "tramite": "Autorización del organismo de cuenca",
                  "motivo": "Obras en DPH, zona de servidumbre o zona de policía (Reglamento del Dominio "
                            "Público Hidráulico)."})
    if _afecta(afecciones, "inundable_t100"):
        t.append({"nivel": "medio", "tramite": "Análisis de inundabilidad",
                  "motivo": "El ámbito está en zona inundable T100: limitaciones de usos y de rellenos que "
                            "alteren el flujo (RDPH)."})
    if _afecta(afecciones, "vias_pecuarias", ("rojo",)):
        t.append({"nivel": "alto", "tramite": "Autorización de ocupación o modificación de trazado de vía pecuaria",
                  "motivo": "Dominio público pecuario (Ley 3/1995 de Vías Pecuarias y normativa autonómica)."})
    if _afecta(afecciones, "montes_publicos", ("ambar",)):
        t.append({"nivel": "medio", "tramite": "Autorización/concesión en monte público",
                  "motivo": "Ley 43/2003 de Montes."})
    if _afecta(afecciones, "ferrocarril"):
        t.append({"nivel": "medio", "tramite": "Autorización del administrador ferroviario",
                  "motivo": "Zona de dominio público o protección ferroviaria (Ley 38/2015 del Sector Ferroviario)."})
    if _afecta(afecciones, "carreteras"):
        t.append({"nivel": "medio", "tramite": "Autorización del titular de la carretera",
                  "motivo": "Zonas de protección de carreteras (Ley 37/2015 o ley autonómica)."})
    if _afecta(afecciones, "lineas_electricas", ("ambar",)) or _afecta(afecciones, "conducciones", ("ambar",)):
        t.append({"nivel": "medio", "tramite": "Consulta a la compañía titular de la infraestructura",
                  "motivo": "Servidumbres de líneas eléctricas o conducciones."})

    exc = volumenes.get("excedente_esponjado_m3") or 0
    if exc > 0:
        t.append({"nivel": "bajo", "tramite": "Plan de gestión de excedentes de tierras",
                  "motivo": f"Excedente estimado de {exc:,.0f} m³ esponjados: reutilización en obra o gestión "
                            "como residuo (Ley 7/2022 de residuos y Real Decreto 105/2008 de RCD)."
                  .replace(",", ".")})
    for c in ccaa:
        if c in NORMATIVA_AUTONOMICA:
            t.append({"nivel": "bajo", "tramite": "Instrumento de prevención ambiental autonómico",
                      "motivo": f"Comprobar si el proyecto está sometido a {NORMATIVA_AUTONOMICA[c]}."})
    t.append({"nivel": "bajo", "tramite": "Licencia urbanística municipal",
              "motivo": "Comprobar clasificación del suelo y usos permitidos en el planeamiento municipal."})
    return t


def semaforo_global(afecciones: list[dict], terreno_stats: dict | None, terreno_fiable: bool) -> dict:
    sems = [a["semaforo"] for a in afecciones if a.get("estado") == "afecta"]
    peor = max(sems, key=lambda s: ORDEN_GRAVEDAD.get(s, 0)) if sems else "verde"
    if peor == "info":
        peor = "verde"
    motivos = [a["nombre"] for a in afecciones if a.get("estado") == "afecta" and a["semaforo"] == peor]
    if terreno_stats and not terreno_stats.get("sin_datos"):
        if terreno_stats.get("pendiente_media", 0) > 20 and ORDEN_GRAVEDAD[peor] < ORDEN_GRAVEDAD["ambar"]:
            peor = "ambar"
            motivos.append("Pendiente media superior al 20 %")
    sin_datos = [a["nombre"] for a in afecciones if a["semaforo"] == "gris"]
    parciales = [a["nombre"] for a in afecciones if a.get("cobertura_parcial") and a.get("estado") != "afecta"
                 and a["semaforo"] != "gris"]
    textos = {"rojo": "Condicionantes muy restrictivos: la viabilidad depende de autorizaciones o de "
                      "modificar el ámbito.",
              "ambar": "Viable con condicionantes que requieren autorizaciones o estudios específicos.",
              "verde": "Sin condicionantes detectados en las capas disponibles."}
    return {"semaforo": peor, "texto": textos[peor], "motivos": motivos, "capas_sin_datos": sin_datos,
            "capas_cobertura_parcial": parciales,
            "terreno_fiable": terreno_fiable}
