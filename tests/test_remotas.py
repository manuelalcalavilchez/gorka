import pytest
from shapely.geometry import box

from prefact import afecciones, remotas, settings

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


def test_desactivado_deja_todo_sin_comprobar(tmp_path):
    r = remotas.obtener(box(0, 0, 10, 10), "EPSG:25830", ["01"], tmp_path / "x")
    assert not r["comprobados"] and not r["fuentes"]
