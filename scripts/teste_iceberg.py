"""Teste de fumaça: confirma que o PyIceberg cria, grava e lê uma tabela Iceberg nesta máquina.

O PyIceberg tem um problema conhecido no Windows: caminhos como C:\\Users\\... são lidos
como se "C:" fosse um protocolo (igual a "s3:"). Este script testa duas formas de
configurar o local das tabelas e diz qual funciona aqui.

Rodar a partir da raiz do projeto:
    uv run python scripts/teste_iceberg.py
"""

import os
import traceback
from pathlib import Path

import pyarrow as pa
from pyiceberg.catalog.sql import SqlCatalog

RAIZ = Path(__file__).resolve().parent.parent
WAREHOUSE = RAIZ / "warehouse"


def testar(nome: str, warehouse_prop: str) -> bool:
    print(f"\n== Tentativa '{nome}': warehouse = {warehouse_prop}")
    try:
        catalogo = SqlCatalog(
            f"teste_{nome}",
            uri=f"sqlite:///{(WAREHOUSE / f'catalogo_teste_{nome}.db').as_posix()}",
            warehouse=warehouse_prop,
        )
        namespace = f"smoke_{nome}"
        catalogo.create_namespace_if_not_exists(namespace)
        identificador = f"{namespace}.tabela_teste"
        if catalogo.table_exists(identificador):
            catalogo.drop_table(identificador)

        dados = pa.table({
            "id": pa.array([1, 2, 3], pa.int64()),
            "texto": pa.array(["a", "b", "c"], pa.string()),
        })
        tabela = catalogo.create_table(identificador, schema=dados.schema)
        tabela.append(dados)
        tabela.append(dados)  # segundo snapshot, para conferir o histórico

        lido = catalogo.load_table(identificador).scan().to_arrow()
        snapshots = len(tabela.metadata.snapshots)
        print(f"   OK: {lido.num_rows} linhas lidas, {snapshots} snapshots")
        return lido.num_rows == 6 and snapshots == 2
    except Exception:
        print("   FALHOU:")
        traceback.print_exc()
        return False


def main() -> None:
    WAREHOUSE.mkdir(exist_ok=True)
    os.chdir(RAIZ)  # a tentativa "relativo" depende de rodar a partir da raiz

    resultados = {
        "relativo": testar("relativo", "warehouse"),
        "file_uri": testar("file_uri", WAREHOUSE.as_uri()),
    }

    print("\n== Resumo")
    for nome, ok in resultados.items():
        print(f"   {nome:10s} -> {'funciona' if ok else 'não funciona'}")


if __name__ == "__main__":
    main()
