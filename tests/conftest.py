"""Entorno de pruebas: datos sintéticos generados antes de importar el paquete."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "scripts"))

_TMP = Path(tempfile.mkdtemp(prefix="prefact_test_"))
GEODATOS = _TMP / "geodatos"
os.environ["GEODATOS_DIR"] = str(GEODATOS)
os.environ["ESTUDIOS_DIR"] = str(_TMP / "estudios")
os.environ["PLANOS_CON_FONDO"] = "0"

import generar_datos_prueba  # noqa: E402

generar_datos_prueba.main(GEODATOS)


@pytest.fixture
def ambitos():
    return GEODATOS / "_ambitos"
