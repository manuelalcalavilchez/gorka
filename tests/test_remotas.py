from types import SimpleNamespace

import numpy as np
import pytest
from shapely.geometry import box

from prefact import afecciones, catalogo, mdt_remoto, remotas, settings, terreno

GML = "http://www.opengis.net/gml/3.2"


def _edificio(id_, x, uso, viviendas):
    return f"""<bu-ext2d:Building xmlns:bu-ext2d="urn:b" xmlns:base="urn:base" xmlns:gml="{GML}" gml:id="B{id_}">
      <base:localId>{id_}</base:localId><bu-ext2d:currentUse>{uso}</bu-ext2d:currentUse>
      <bu-ext2d:numberOfDwellings>{viviendas}</bu-ext2d:numberOfDwellings>
      <gml:Surface><gml:patches><gml:PolygonPatch><gml:exterior><gml:LinearRing>
      <gml:posList>{x} 0 {x + 10} 0 {x + 10} 10 {x} 10 {x} 0</gml:posList>
      </gml:LinearRing></gml:exterior></gml:PolygonPatch></gml:patches></gml:Surface></bu-ext2d:Building>"""


def _coleccion(*elementos):
    return f"<r>{''.join(elementos)}</r>".encode()


def _parcela(ref, x0, x1):
    return f"""<cp:CadastralParcel xmlns:cp="urn:c" xmlns:gml="{GML}" gml:id="P{ref}">
      <cp:areaValue uom="m2">{(x1 - x0) * 100}</cp:areaValue>
      <gml:MultiSurface><gml:surfaceMember><gml:Surface><gml:patches><gml:PolygonPatch><gml:exterior><gml:LinearRing>
      <gml:posList>{x0} 0 {x1} 0 {x1} 100 {x0} 100 {x0} 0</gml:posList>
      </gml:LinearRing></gml:exterior></gml:PolygonPatch></gml:patches></gml:Surface></gml:surfaceMember></gml:MultiSurface>
      <cp:nationalCadastralReference>{ref}</cp:nationalCadastralReference></cp:CadastralParcel>"""


class Respuesta:
    def __init__(self, contenido=b"", datos=None):
        self.content, self.datos = contenido, datos

    def json(self):
        return self.datos

    @property
    def text(self):
        return self.content.decode()


def _vial(forma, firme, x):
    return {"type": "Feature", "properties": {"formofway_href": f"http://x/{forma}" if forma else None,
                                              "surfacecategory": firme},
            "geometry": {"type": "LineString", "coordinates": [[x, 37.0], [x, 37.01]]}}


@pytest.fixture
def activar(monkeypatch):
    monkeypatch.setattr(settings, "DATOS_REMOTOS", True)
    monkeypatch.setattr(remotas.time, "sleep", lambda s: None)


def test_clase_vial():
    assert remotas.clase_vial({"formofway_href": "http://x/freeway", "surfacecategory": "paved"}) == "autovia"
    assert remotas.clase_vial({"formofway_href": "http://x/dualCarriageway"}) == "autovia"
    assert remotas.clase_vial({"formofway_href": "http://x/singleCarriageway", "surfacecategory": "paved"}) == "carretera"
    assert remotas.clase_vial({"formofway_href": None, "surfacecategory": "unpaved"}) == "camino"
    assert remotas.clase_vial({"formofway_href": "http://x/tractorRoad", "surfacecategory": "paved"}) == "camino"


def test_leer_edificios_distingue_viviendas():
    f = remotas.leer_edificios(_coleccion(_edificio("a", 0, "1_residential", 1), _edificio("b", 50, "3_industrial", 0),
                                          _edificio("c", 90, "2_agriculture", 2)))
    assert [x["vivienda"] for x in f] == [True, False, True]
    assert f[0]["geometry"].area == pytest.approx(100)


def test_teselas_respetan_el_limite_y_saltan_las_vacias():
    geom = box(0, 0, 2500, 100).union(box(0, 0, 100, 2500))
    t = remotas.teselas(geom)
    assert all(a[2] - a[0] <= remotas.LADO_TESELA_M and a[3] - a[1] <= remotas.LADO_TESELA_M for a in t)
    assert len(t) == 5  # sin las 4 teselas interiores que no tocan el ámbito


def test_catastro_foral_queda_sin_comprobar(tmp_path, activar, monkeypatch):
    monkeypatch.setattr(remotas, "viales", lambda *a: remotas._gdf([], "EPSG:25830"))
    monkeypatch.setattr(remotas, "_get", lambda *a: pytest.fail("no debe consultar Catastro"))
    r = remotas.obtener(box(0, 0, 100, 100), "EPSG:25830", ["16"], tmp_path)
    assert "Construcciones" not in r["comprobados"] and "Viviendas aisladas" not in r["comprobados"]
    assert {"Carreteras", "Caminos"} <= r["comprobados"]
    assert r["parcelas"] is None and not r["avisos"]
    pendientes = {c["criterio"] for c in afecciones.criterios_pendientes(["16"], r["comprobados"])}
    assert "Construcciones" in pendientes and "Carreteras" not in pendientes


def test_fallo_de_api_no_equivale_a_vacio(tmp_path, activar, monkeypatch):
    def caida(*a, **k):
        raise remotas.requests.ConnectionError("caído")
    monkeypatch.setattr(remotas.requests, "get", caida)
    r = remotas.obtener(box(0, 0, 100, 100), "EPSG:25830", ["01"], tmp_path)
    assert not r["comprobados"] and not r["zonas"] and len(r["avisos"]) == 3
    assert {c["estado"] for c in r["fuentes"]} == {"error"}
    assert not list(tmp_path.glob("*.geojson"))  # un fallo no se guarda: se reintenta al recalcular


def test_excepcion_de_catastro_no_equivale_a_vacio(tmp_path, activar, monkeypatch):
    monkeypatch.setattr(remotas, "viales", lambda *a: remotas._gdf([], "EPSG:25830"))
    monkeypatch.setattr(remotas, "_get", lambda *a: Respuesta(b"<?xml version='1.0'?><ExceptionReport/>"))
    r = remotas.obtener(box(0, 0, 100, 100), "EPSG:25830", ["01"], tmp_path)
    assert "Construcciones" not in r["comprobados"] and len(r["avisos"]) == 2


def test_resta_cuenta_parcelas_y_reutiliza_cache(tmp_path, activar, monkeypatch):
    llamadas = []

    def falso(url, params, *a):
        llamadas.append(url)
        if url == remotas.URL_VIALES:
            return Respuesta(datos={"features": [_vial("freeway", "paved", -3.0), _vial(None, "unpaved", -3.001)], "links": []})
        if "wfsBU" in url:
            return Respuesta(_coleccion(_edificio("a", 500000, "1_residential", 1)))
        return Respuesta(_coleccion(_parcela("P1", 500000, 500100), _parcela("VECINA", 500099.9, 500600)))

    monkeypatch.setattr(remotas, "_get", falso)
    ambito = box(500000, 0, 500100, 100)
    r = remotas.obtener(ambito, "EPSG:25830", ["01"], tmp_path)
    assert {"Autovías y autopistas", "Carreteras", "Caminos", "Construcciones", "Viviendas aisladas"} == r["comprobados"]
    assert {z["capa"] for z in r["zonas"]} == {"Construcciones", "Viviendas aisladas"}  # los viales caen lejos
    assert [p["ref"] for p in r["parcelas"]] == ["P1"]  # la vecina solo roza el lindero
    n = len(llamadas)
    r2 = remotas.obtener(ambito, "EPSG:25830", ["01"], tmp_path)
    assert len(llamadas) == n and r2["comprobados"] == r["comprobados"]
    assert r2["parcelas"] == r["parcelas"]


def test_tesela_lenta_se_parte_y_no_pierde_datos(tmp_path, activar, monkeypatch):
    monkeypatch.setattr(remotas, "viales", lambda *a: remotas._gdf([], "EPSG:25830"))
    pedidas = []

    def falso(url, params, *a):
        if "wfsCP" in url:
            return Respuesta(_coleccion())
        x0, y0, x1, y1 = (float(v) for v in params["bbox"].split(","))
        pedidas.append(x1 - x0)
        if x1 - x0 > remotas.LADO_MIN_TESELA_M:
            raise remotas.RemotoError("lento")
        return Respuesta(_coleccion(_edificio(f"{x0:.0f}-{y0:.0f}", 0, "1_residential", 1)))

    monkeypatch.setattr(remotas, "_get", falso)
    r = remotas.obtener(box(0, 0, 500, 500), "EPSG:25830", ["01"], tmp_path)
    assert "Construcciones" in r["comprobados"] and max(pedidas) > min(pedidas) == remotas.LADO_MIN_TESELA_M
    assert next(c for c in r["fuentes"] if c["nombre"] == "Edificios")["elementos"] > 1


def test_presupuesto_agotado_queda_sin_comprobar(tmp_path, activar, monkeypatch):
    monkeypatch.setattr(remotas, "viales", lambda *a: remotas._gdf([], "EPSG:25830"))
    monkeypatch.setattr(remotas, "PRESUPUESTO_CATASTRO_S", -1)
    r = remotas.obtener(box(0, 0, 100, 100), "EPSG:25830", ["01"], tmp_path)
    assert "Construcciones" not in r["comprobados"] and len(r["avisos"]) == 2


def _asc(params):
    assert ("format", "application/asc") in params
    x0, x1 = (float(v) for v in params[-2][1][2:-1].split(","))
    y0, y1 = (float(v) for v in params[-1][1][2:-1].split(","))
    n, m = round((x1 - x0) / 5), round((y1 - y0) / 5)
    filas = "\n".join(" ".join(f"{100 + (x0 + 5 * c) / 100000:.3f}" for c in range(n)) for _ in range(m))
    return Respuesta(f"--wcs\nContent-Type: application/asc\n\nncols {n}\nnrows {m}\nxllcorner {x0}\nyllcorner {y0}\n"
                     f"cellsize 5.0\nNODATA_value -9999\n{filas}\n--wcs--".encode())


def test_leer_asc_conserva_decimales_y_nulos():
    z, x0, y0 = mdt_remoto.leer_asc("--wcs\n\nncols 2\nnrows 2\nxllcorner 2.5\nyllcorner 7.5\ncellsize 5\n"
                                    "NODATA_value -9999\n1.25 2.5\n-9999 4\n--wcs--")
    assert (x0, y0) == (2.5, 7.5) and z[0, 0] == 1.25 and np.isnan(z[1, 0])


def test_leer_asc_rechaza_respuestas_raras():
    with pytest.raises(remotas.RemotoError):
        mdt_remoto.leer_asc("<ExceptionReport/>")
    with pytest.raises(remotas.RemotoError):
        mdt_remoto.leer_asc("ncols 1\nnrows 1\nxllcorner 0\nyllcorner 0\ndx 4.9\ndy 5\n1")


def test_descarga_une_teselas_sin_huecos(activar, monkeypatch):
    llamadas = []
    monkeypatch.setattr(mdt_remoto, "_get", lambda url, params: llamadas.append(params) or _asc(params))
    monkeypatch.setattr(mdt_remoto, "LADO_TESELA_M", 1000)
    z, t, crs = mdt_remoto.descargar((500000, 4000000, 502400, 4001200), "EPSG:25830")
    assert crs == "EPSG:25830" and len(llamadas) == 6 and np.isfinite(z).all()
    assert (t.c - 2.5) % 5 == 0 and (t.f - 2.5) % 5 == 0
    esperado = 100 + (t.c + 5 * (z.shape[1] - 1)) / 100000
    assert z[0, -1] == pytest.approx(esperado, abs=2e-3) and z[-1, -1] == z[0, -1]


def _ambito():
    return SimpleNamespace(geom=box(500000, 4000000, 500200, 4000200), crs="EPSG:25830")


def _solo_contexto(monkeypatch):
    f = catalogo.FuenteMDT("mdt1000", "MDT 1 km", "no-existe", 1000, "IGN", True)
    monkeypatch.setattr(terreno, "seleccionar_mdt", lambda *a: (f, []))


def test_preparar_prefiere_el_mdt_del_ign_al_de_contexto(activar, monkeypatch):
    _solo_contexto(monkeypatch)
    monkeypatch.setattr(mdt_remoto, "_get", lambda url, params: _asc(params))
    t = terreno.preparar(_ambito(), margen_m=50)
    assert t.fiable and t.fuente["id"] == "mdt05_ign" and t.res == 5 and not t.avisos
    assert np.isfinite(t.z[t.mascara]).all() and np.nanmin(t.z) > 100


def test_fallo_del_mdt_avisa_y_no_lo_da_por_fiable(activar, monkeypatch):
    _solo_contexto(monkeypatch)

    def caido(*a):
        raise remotas.RemotoError("El servicio no responde.")
    monkeypatch.setattr(mdt_remoto, "_get", caido)
    t = terreno.preparar(_ambito(), margen_m=50)
    assert not t.fiable and t.fuente["id"] == "mdt1000"
    assert any("No se ha podido descargar" in a for a in t.avisos) and any("NO son fiables" in a for a in t.avisos)


def test_mdt_local_bueno_no_consulta_el_servicio(activar, monkeypatch):
    f = catalogo.FuenteMDT("mdt02", "MDT02", "x", 2, "CNIG")
    monkeypatch.setattr(terreno, "seleccionar_mdt", lambda *a: (f, []))
    monkeypatch.setattr(mdt_remoto, "_get", lambda *a: pytest.fail("no debe consultar el IGN"))
    assert terreno._elegir_fuente((0, 0, 10, 10), "EPSG:25830")[0] is f


def test_estado_servicios_detecta_caidas_y_guarda_el_resultado(activar, monkeypatch):
    llamadas = []

    def falso(url, params, *a):
        llamadas.append(url)
        if "wfsBU" in url:
            raise remotas.RemotoError("caído")
        if "wfsCP" in url:
            return Respuesta(b"<?xml version='1.0'?><ExceptionReport/>")
        if url == remotas.URL_MDT:
            return Respuesta(b"--wcs ncols 20")
        return Respuesta(datos={"features": []})

    monkeypatch.setattr(remotas, "_get", falso)
    monkeypatch.setitem(remotas._prueba, "hora", None)
    filas = {s["id"]: s["disponible"] for s in remotas.estado_servicios()}
    assert filas == {"viales": True, "edificios": False, "parcelas": False, "mdt05_ign": True}
    n = len(llamadas)
    remotas.estado_servicios()
    assert len(llamadas) == n


def test_desactivado_deja_todo_sin_comprobar(tmp_path):
    r = remotas.obtener(box(0, 0, 10, 10), "EPSG:25830", ["01"], tmp_path / "x")
    assert not r["comprobados"] and not r["fuentes"]
