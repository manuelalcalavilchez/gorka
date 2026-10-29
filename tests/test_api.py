import time

from fastapi.testclient import TestClient

from prefact.web.app import app

cliente = TestClient(app)


def _esperar(id_, limite=120):
    t0 = time.time()
    while time.time() - t0 < limite:
        r = cliente.get(f"/api/estudios/{id_}").json()
        if r["estado"]["estado"] in ("listo", "error"):
            return r
        time.sleep(0.3)
    raise TimeoutError


def test_paginas_y_config():
    assert cliente.get("/").status_code == 200
    assert "Tecnología Alcalá" in cliente.get("/").text or "TECNOLOGÍA ALCALÁ" in cliente.get("/").text
    c = cliente.get("/api/config").json()
    assert set(c["modos"]) == {"explanacion", "relleno", "extraccion", "vial"}
    assert any(x["instalada"] for x in cliente.get("/api/capas").json())


def test_flujo_api(ambitos):
    with open(ambitos / "parcela.geojson", "rb") as f:
        r = cliente.post("/api/estudios", files={"fichero": ("parcela.geojson", f, "application/geo+json")},
                         data={"modo": "explanacion", "nombre": "API", "parametros": "{}"})
    assert r.status_code == 200, r.text
    id_ = r.json()["id"]
    assert cliente.get(r.json()["url"]).status_code == 200
    res = _esperar(id_)
    assert res["estado"]["estado"] == "listo"
    v = res["resultado"]["visor"]
    assert cliente.get(f"/api/estudios/{id_}/fichero/{v['raster']['corte_relleno']['url']}").status_code == 200
    assert cliente.get(f"/api/estudios/{id_}/fichero/../parametros.json").status_code == 404
    r = cliente.post(f"/api/estudios/{id_}/recalcular", json={"parametros": {"cota": 424}})
    assert r.status_code == 200
    res = _esperar(id_)
    assert res["resultado"]["version"] == 2
    pdf = cliente.get(f"/api/estudios/{id_}/descargar/pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    xl = cliente.get(f"/api/estudios/{id_}/descargar/xlsx")
    assert xl.status_code == 200 and xl.content[:2] == b"PK"


def test_rechazos():
    r = cliente.post("/api/estudios", files={"fichero": ("a.exe", b"x")}, data={"modo": "explanacion"})
    assert r.status_code == 400
    r = cliente.post("/api/estudios", files={"fichero": ("a.kml", b"x")}, data={"modo": "otro"})
    assert r.status_code == 400
    assert cliente.get("/api/estudios/nohay").status_code == 404
    assert cliente.get("/estudio/" + "0" * 32).status_code == 404
