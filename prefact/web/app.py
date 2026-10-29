"""API y visor web."""
from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import estudio, informes, settings
from ..ambito import EXTENSIONES
from ..catalogo import estado_catalogo
from ..volumenes import DEFECTOS, MODOS, ParametrosError, normalizar_parametros

log = logging.getLogger("prefact")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

ESTATICOS = Path(__file__).parent / "static"


@asynccontextmanager
async def _ciclo(_app):
    settings.ESTUDIOS_DIR.mkdir(parents=True, exist_ok=True)
    if not (ESTATICOS / "vendor" / "leaflet" / "leaflet.js").exists():
        log.warning("Faltan las librerías del visor: ejecuta  python scripts/descargar_vendor.py")
    n = estudio.limpiar_antiguos()
    log.info("GEODATOS_DIR=%s · ESTUDIOS_DIR=%s · %d estudios antiguos eliminados",
             settings.GEODATOS_DIR, settings.ESTUDIOS_DIR, n)
    yield


app = FastAPI(title=f"{settings.PRODUCTO} — {settings.MARCA}", docs_url="/api/docs", redoc_url=None,
              lifespan=_ciclo)
(ESTATICOS / "vendor").mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=ESTATICOS), name="static")
_pool = ThreadPoolExecutor(max_workers=settings.MAX_TRABAJOS, thread_name_prefix="estudio")
_bloqueos: dict[str, threading.Lock] = {}


def _lock(id_: str) -> threading.Lock:
    return _bloqueos.setdefault(id_, threading.Lock())


def _lanzar(id_: str, params: dict | None = None) -> None:
    def tarea():
        with _lock(id_):
            estudio.ejecutar(id_, params)
    _pool.submit(tarea)


def _carpeta(id_: str) -> Path:
    try:
        d = estudio.carpeta(id_)
    except estudio.EstudioError as e:
        raise HTTPException(404, str(e)) from e
    if not d.exists():
        raise HTTPException(404, "Estudio no encontrado.")
    return d


# ------------------------------------------------------------------ páginas
@app.get("/", response_class=HTMLResponse)
def inicio():
    return (ESTATICOS / "index.html").read_text(encoding="utf-8")


FAVICON = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="12" fill="#1F2A44"/>'
           '<path d="M8 46 L24 26 L34 38 L44 22 L56 46 Z" fill="#0F7B6C"/><path d="M8 46 H56" stroke="#9FD8CF" stroke-width="4"/></svg>')


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    from fastapi.responses import Response
    return Response(FAVICON, media_type="image/svg+xml", headers={"Cache-Control": "max-age=86400"})


@app.get("/estudio/{id_}", response_class=HTMLResponse)
def pagina_visor(id_: str):
    _carpeta(id_)
    return (ESTATICOS / "visor.html").read_text(encoding="utf-8")


# ------------------------------------------------------------------ API
@app.get("/api/salud", include_in_schema=False)
def salud():
    return {"ok": True, "geodatos": settings.GEODATOS_DIR.exists()}


@app.get("/api/config")
def config():
    return {"modos": MODOS, "defectos": DEFECTOS, "extensiones": sorted(EXTENSIONES),
            "max_mb": settings.MAX_SUBIDA_MB, "marca": settings.MARCA, "web": settings.MARCA_WEB,
            "producto": settings.PRODUCTO,
            "wms": {"pnoa": settings.WMS_PNOA, "pnoa_capa": settings.WMS_PNOA_CAPA,
                    "ign": settings.WMS_IGN_BASE, "ign_capa": settings.WMS_IGN_BASE_CAPA}}


@app.get("/api/capas")
def capas():
    return estado_catalogo()


@app.post("/api/estudios")
async def crear(fichero: UploadFile = File(...), modo: str = Form(...), nombre: str = Form(""),
                parametros: str = Form("{}")):
    ext = Path(fichero.filename or "").suffix.lower()
    if ext not in EXTENSIONES:
        raise HTTPException(400, f"Formato no admitido ({ext or 'sin extensión'}).")
    contenido = await fichero.read(settings.MAX_SUBIDA_MB * 1024 * 1024 + 1)
    if len(contenido) > settings.MAX_SUBIDA_MB * 1024 * 1024:
        raise HTTPException(413, f"El fichero supera {settings.MAX_SUBIDA_MB} MB.")
    if not contenido:
        raise HTTPException(400, "El fichero está vacío.")
    try:
        params = json.loads(parametros or "{}")
        id_ = estudio.crear(contenido, fichero.filename, modo, nombre, params)
    except (json.JSONDecodeError, ParametrosError, estudio.EstudioError) as e:
        raise HTTPException(400, str(e)) from e
    _lanzar(id_)
    return {"id": id_, "url": f"/estudio/{id_}"}


@app.get("/api/estudios/{id_}")
def obtener(id_: str):
    d = _carpeta(id_)
    e = estudio.estado(id_)
    meta = estudio.leer_json(d / "parametros.json", {})
    out = {"estado": e, "modo": meta.get("modo"), "nombre": meta.get("nombre"), "parametros": meta.get("params")}
    if e.get("estado") == "listo":
        out["resultado"] = estudio.resultado(id_)
    return out


class Recalculo(BaseModel):
    parametros: dict


@app.post("/api/estudios/{id_}/recalcular")
def recalcular(id_: str, cuerpo: Recalculo):
    d = _carpeta(id_)
    meta = estudio.leer_json(d / "parametros.json", {})
    try:
        normalizar_parametros(meta["modo"], cuerpo.parametros)
    except ParametrosError as e:
        raise HTTPException(400, str(e)) from e
    if estudio.estado(id_).get("estado") in ("procesando", "en_cola"):
        raise HTTPException(409, "El estudio se está calculando; espera a que termine.")
    estudio._poner_estado(d, estado="en_cola", progreso=0.0, mensaje="Recalculando")  # noqa: SLF001
    _lanzar(id_, cuerpo.parametros)
    return {"ok": True}


@app.get("/api/estudios/{id_}/perfil")
def perfil(id_: str, coords: str):
    """coords = 'lon,lat;lon,lat;...'"""
    _carpeta(id_)
    try:
        pts = [tuple(float(v) for v in par.split(",")) for par in coords.split(";") if par]
        if len(pts) < 2:
            raise ValueError
    except ValueError as e:
        raise HTTPException(400, "Coordenadas no válidas.") from e
    try:
        return estudio.perfil(id_, pts)
    except estudio.EstudioError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/estudios/{id_}/fichero/{ruta:path}")
def fichero(id_: str, ruta: str):
    d = _carpeta(id_)
    permitido = ruta.startswith(("visor/", "capas/")) or ruta == "ambito.geojson"
    p = (d / ruta).resolve()
    if not permitido or not str(p).startswith(str(d.resolve())) or not p.is_file():
        raise HTTPException(404, "Fichero no encontrado.")
    return FileResponse(p, headers={"Cache-Control": "no-cache"})


@app.get("/api/estudios/{id_}/descargar/{tipo}")
def descargar(id_: str, tipo: str):
    _carpeta(id_)
    if tipo not in ("pdf", "xlsx", "zip"):
        raise HTTPException(404, "Tipo no válido.")
    if estudio.estado(id_).get("estado") != "listo":
        raise HTTPException(409, "El estudio no está listo.")
    with _lock(id_):
        try:
            rutas = informes.generar(id_)
        except FileNotFoundError as e:
            raise HTTPException(404, str(e)) from e
    p = rutas[tipo]
    tipos = {"pdf": "application/pdf", "zip": "application/zip",
             "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
    return FileResponse(p, media_type=tipos[tipo], filename=p.name)


@app.exception_handler(Exception)
async def _error(request: Request, exc: Exception):
    log.exception("Error no controlado en %s", request.url.path)
    return JSONResponse({"detail": "Error interno del servidor."}, status_code=500)


def main() -> None:
    import os

    import uvicorn
    uvicorn.run("prefact.web.app:app", host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "8000")))


if __name__ == "__main__":
    main()
