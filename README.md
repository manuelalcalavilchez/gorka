# Prefactibilidad de movimiento de tierras

**Tecnología Alcalá · [tecnologiaalcala.com](https://tecnologiaalcala.com)**

Aplicación web que, a partir del ámbito de un proyecto (polígono o eje), genera un **estudio de prefactibilidad**:
terreno, volúmenes de movimiento de tierras, condicionantes territoriales y ambientales, orientación de la
tramitación e informe en PDF y Excel. Los resultados se **revisan en un visor** (mapa + indicadores) y se pueden
**ajustar y recalcular** antes de descargarlos.

## Qué hace

| Paso | Detalle |
|---|---|
| Ámbito | KML, KMZ, GeoJSON, GPKG, SHP (ZIP), GML de Catastro o DXF (ETRS89 UTM). Detecta el huso UTM (25829/25830/25831, REGCAN95 en Canarias). |
| Territorio | Comunidad autónoma y municipios a partir de las unidades administrativas del IGN. |
| Terreno | MDT por prioridad (MDT02 › MDT05 › MDT25 › 1 km), pendientes (Horn), clases de pendiente, sombreado. Avisa si la resolución no es fiable para volúmenes. |
| Movimiento de tierras | **Explanación** (cota fija o compensada, plataforma inclinada, taludes), **Relleno** (cota final o volumen objetivo), **Extracción** (cota de fondo o profundidad, talud del hueco) y **Vial** (rasante con pendiente máxima, secciones, tramos y diagrama de masas). |
| Condicionantes | Cruce con las capas de `config/capas.yaml`: DPH, servidumbre y policía de cauces, inundabilidad, Red Natura, ENP, montes públicos, vías pecuarias (anchura legal por tipo), ferrocarril, carreteras, líneas eléctricas… con **semáforo** por zona. Las afecciones se calculan sobre la **ocupación real con taludes**, no solo sobre el ámbito. |
| Cobertura | Distingue «sin afección» de «sin datos» o «cobertura parcial» según la comunidad. |
| Tramitación | Orientación no vinculante (EIA, organismo de cuenca, vías pecuarias, montes, residuos, normativa autonómica…). |
| Visor | Leaflet con ortofoto PNOA / mapa IGN, rásteres de pendientes y desmonte/terraplén, capas de afección, **perfil del terreno dibujado sobre el mapa**, gráficas y recálculo. |
| Entrega | PDF con planos, Excel y ZIP (PDF + Excel + planos + GeoJSON + resultado.json). |

## Puesta en marcha

### Windows (PC de trabajo)

1. Instala Python 3.11 o 3.12.
2. Copia `.env.example` a `.env` y ajusta `GEODATOS_DIR` (p. ej. `I:\geodatos\nacional`).
3. Doble clic en `iniciar.bat`. Crea el entorno, instala dependencias y abre `http://localhost:8000`.

### Docker / Easypanel

```bash
GEODATOS_HOST=/ruta/a/geodatos docker compose up -d --build
```

En Easypanel: servicio *App* desde este repositorio (Dockerfile), puerto 8000, y dos montajes:
`/data/geodatos` (capas, solo lectura) y `/data/estudios` (volumen persistente).

## Capas (no van en el repositorio)

La carpeta `GEODATOS_DIR` sigue esta estructura (la misma que `I:\geodatos\nacional`):

```
limites/unidades_administrativas_IGN.gpkg
mdt/mdt02/**/*.tif     mdt/mdt05/**/*.tif     mdt/mdt25/**/*.tif     mdt/MDT_ESPANA_1000m_EPSG4258.tif
hidrologia/  medio_natural/  infraestructuras/  geologia/  riesgos/
```

- El catálogo completo, con rutas, buffers, semáforo, cobertura y fuente de cada capa, está en
  **`config/capas.yaml`**. Añadir una capa nueva (o un paquete autonómico) es añadir una entrada; no hace falta tocar código.
- `python scripts/preparar_capas.py` muestra qué capas están instaladas.
  `python scripts/preparar_capas.py --convertir` convierte los SHP grandes a GPKG indexado (mucho más rápido);
  el programa usa el `.gpkg` automáticamente.
- Las hojas de MDT05/MDT02 (GeoTIFF/COG del CNIG) se copian tal cual en `mdt/mdt05/` o `mdt/mdt02/`.

## Desarrollo

```bash
pip install -r requirements-dev.txt
python scripts/descargar_vendor.py
python -m pytest              # genera datos sintéticos y prueba los cuatro modos y la API
python scripts/generar_datos_prueba.py geodatos_prueba   # datos de demo
GEODATOS_DIR=geodatos_prueba python -m prefact.web.app
```

Estructura: `prefact/ambito.py` (entrada), `territorio.py`, `terreno.py`, `volumenes.py`, `vial.py`,
`afecciones.py`, `tramitacion.py`, `estudio.py` (orquestación, una carpeta por estudio), `render.py`,
`informes.py` y `web/` (FastAPI + visor).

## Limitaciones

Estudio **orientativo** de prefactibilidad: no sustituye al proyecto técnico, al levantamiento topográfico,
al estudio geotécnico ni a las consultas a los organismos competentes. Las distancias de protección se aproximan
desde el eje de los elementos y varias capas oficiales tienen cobertura parcial.

## Licencias y atribución

Código © Tecnología Alcalá. El visor usa [Leaflet](https://leafletjs.com) (BSD-2) y [Chart.js](https://www.chartjs.org) (MIT), descargados en la instalación por `scripts/descargar_vendor.py` con verificación de hash.
Cartografía: © Instituto Geográfico Nacional / CNIG, MITECO, IGME y demás organismos citados en cada informe.
