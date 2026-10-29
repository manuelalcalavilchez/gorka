import json
import zipfile

import numpy as np
import pytest
from affine import Affine

from prefact import ambito, catalogo, estudio, informes, terreno, volumenes
from prefact.terreno import Terreno


# ------------------------------------------------------------------ cálculos sobre terreno analítico
def _plano(z0=100.0, pendiente=0.0, n=200, res=1.0, ambito_lado=100):
    """Terreno plano (o inclinado en X) con un ámbito cuadrado centrado."""
    xs = np.arange(n) * res
    z = np.tile(z0 + pendiente * xs, (n, 1)).astype("float32")
    m = np.zeros((n, n), bool)
    a, b = (n - ambito_lado) // 2, (n + ambito_lado) // 2
    m[a:b, a:b] = True
    return Terreno(z=z, transform=Affine(res, 0, 0, 0, -res, n * res), crs="EPSG:25830", res=res,
                   fuente={"nombre": "sintético"}, fiable=True, mascara=m)


def test_relleno_plano_volumen_exacto():
    t = _plano()
    p = volumenes.normalizar_parametros("relleno", {"cota": 102, "talud_terraplen": 2})
    r = volumenes.relleno(t, p)
    dentro = 100 * 100 * 2  # 20.000 m³ sobre el ámbito
    # Taludes 2H:1V de 2 m de altura: sección triangular 4 m × 2 m / 2 = 4 m² en un perímetro ≈ 400 m
    assert r.resumen["volumen_terraplen_m3"] == pytest.approx(dentro + 400 * 4, rel=0.06)
    assert r.resumen["volumen_desmonte_m3"] == 0


def test_explanacion_compensada_equilibra():
    t = _plano(pendiente=0.05)
    p = volumenes.normalizar_parametros("explanacion", {})
    r = volumenes.explanacion(t, p)
    s = r.resumen
    assert s["cota_compensada"] is True
    assert abs(s["balance_m3"]) < 0.01 * s["volumen_desmonte_m3"]
    assert 100 + 0.05 * 50 < s["cota_plataforma"] < 100 + 0.05 * 150


def test_explanacion_cota_fija_solo_desmonte():
    t = _plano(z0=110)
    p = volumenes.normalizar_parametros("explanacion", {"cota": 105, "talud_desmonte": 1})
    r = volumenes.explanacion(t, p)
    assert r.resumen["volumen_terraplen_m3"] == 0
    assert r.resumen["volumen_desmonte_m3"] > 100 * 100 * 5


def test_extraccion_respeta_fondo_y_taludes():
    t = _plano()
    p = volumenes.normalizar_parametros("extraccion", {"cota_fondo": 90, "talud_interior": 1})
    r = volumenes.extraccion(t, p)
    assert np.nanmin(r.diseno) == pytest.approx(90)
    # Hueco troncopiramidal: menos que el prisma completo de 10 m
    assert 0.6 * 100000 < r.resumen["volumen_extraible_m3"] < 100000


def test_parametros_invalidos():
    with pytest.raises(volumenes.ParametrosError):
        volumenes.normalizar_parametros("explanacion", {"talud_desmonte": 0})
    with pytest.raises(volumenes.ParametrosError):
        volumenes.normalizar_parametros("vial", {"ancho": 1})
    with pytest.raises(volumenes.ParametrosError):
        volumenes.normalizar_parametros("relleno", {"cota": "abc"})


def test_pendiente_horn_plano_inclinado():
    z = np.tile(np.arange(50, dtype="float32") * 0.1, (50, 1))  # 10 %
    p = terreno.pendiente_pct(z, 1.0)
    assert np.nanmedian(p) == pytest.approx(10, abs=0.01)


# ------------------------------------------------------------------ ámbito
def test_leer_ambitos(ambitos):
    a = ambito.leer_ambito(ambitos / "parcela.kml", "explanacion")
    assert a.crs == "EPSG:25830" and a.tipo == "poligono" and 300000 < a.area_m2 < 400000
    e = ambito.leer_ambito(ambitos / "eje_camino.geojson", "vial")
    assert e.tipo == "linea" and e.longitud_m > 4000
    with pytest.raises(ambito.AmbitoError):
        ambito.leer_ambito(ambitos / "eje_camino.geojson", "explanacion")
    with pytest.raises(ambito.AmbitoError):
        ambito.leer_ambito(ambitos / "parcela.geojson", "vial")


def test_xml_catastro_sin_geometria(tmp_path):
    f = tmp_path / "30030A044000470000WD_datos.xml"
    f.write_text("<?xml version='1.0'?><consulta_dnp><bico/></consulta_dnp>", encoding="utf-8")
    with pytest.raises(ambito.AmbitoError):
        ambito.leer_ambito(f, "explanacion")


def test_crs_trabajo():
    assert ambito.crs_trabajo(-8.5, 42.8) == "EPSG:25829"
    assert ambito.crs_trabajo(-2.4, 37.0) == "EPSG:25830"
    assert ambito.crs_trabajo(2.1, 41.4) == "EPSG:25831"
    assert ambito.crs_trabajo(-15.4, 28.1) == "EPSG:4083"


def test_cobertura_catalogo():
    cat = catalogo.catalogo()
    dph = cat.capa("dph_estimado")
    assert dph.cubre(["01"]) and not dph.cubre(["05"])
    assert cat.capa("vias_pecuarias").es_parcial(["12"])


# ------------------------------------------------------------------ estudio completo
@pytest.mark.parametrize("fichero,modo,params", [
    ("parcela.geojson", "explanacion", {}),
    ("parcela.kml", "relleno", {"volumen_objetivo": 50000}),
    ("cantera.geojson", "extraccion", {"profundidad": 15}),
    ("eje_camino.geojson", "vial", {"ancho": 6, "pendiente_max_pct": 8}),
])
def test_estudio_completo(ambitos, fichero, modo, params):
    id_ = estudio.crear((ambitos / fichero).read_bytes(), fichero, modo, f"Test {modo}", params)
    estudio.ejecutar(id_)
    e = estudio.estado(id_)
    assert e["estado"] == "listo", e
    r = estudio.resultado(id_)
    assert r["territorio"]["ccaa"][0]["codigo"] == "01"
    assert r["terreno"]["fiable"] is True
    assert r["volumenes"]["volumen_desmonte_m3"] >= 0
    ids = {a["id"]: a for a in r["afecciones"]}
    assert ids["carreteras"]["estado"] == "no_instalada"
    if modo == "relleno":
        assert r["volumenes"]["capacidad_m3"] == pytest.approx(50000, rel=0.01)
    if modo in ("explanacion", "vial"):
        # La obra toca la rambla de prueba
        assert ids["cauces"]["estado"] == "afecta"
    rutas = informes.generar(id_)
    assert rutas["pdf"].stat().st_size > 20000
    with zipfile.ZipFile(rutas["zip"]) as z:
        nombres = z.namelist()
        assert any(n.endswith(".pdf") for n in nombres) and "resultado.json" in nombres
        assert json.loads(z.read("resultado.json"))["id"] == id_


def test_recalculo_y_perfil(ambitos):
    id_ = estudio.crear((ambitos / "parcela.geojson").read_bytes(), "parcela.geojson", "explanacion", "Recalc", {})
    estudio.ejecutar(id_)
    v1 = estudio.resultado(id_)["volumenes"]
    estudio.ejecutar(id_, {"cota": 425})
    r2 = estudio.resultado(id_)
    assert r2["version"] == 2 and r2["volumenes"]["cota_plataforma"] == 425
    assert r2["volumenes"]["volumen_desmonte_m3"] != v1["volumen_desmonte_m3"]
    lon0, lat0, lon1, lat1 = r2["ambito"]["bounds_wgs84"]
    pf = estudio.perfil(id_, [(lon0, (lat0 + lat1) / 2), (lon1, (lat0 + lat1) / 2)])
    assert len(pf["terreno"]) == len(pf["distancia"]) and any(v == 425 for v in pf["diseno"] if v is not None)


def test_estudio_con_error_de_entrada(tmp_path):
    id_ = estudio.crear(b"no es un kml", "malo.kml", "explanacion", "Error", {})
    estudio.ejecutar(id_)
    e = estudio.estado(id_)
    assert e["estado"] == "error" and e["mensaje"]


def test_id_no_valido():
    with pytest.raises(estudio.EstudioError):
        estudio.carpeta("../../etc")
