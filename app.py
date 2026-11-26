import sqlite3
from contextlib import closing
from datetime import date
import pandas as pd
import streamlit as st


ICONO_BRILLA_ASEO = "🌿"
st.set_page_config(page_title="Brilla Aseo - Inventario", page_icon=ICONO_BRILLA_ASEO, layout="wide")

DB_PATH = "siga_clase.db"


@st.cache_resource
def obtener_conexion():
    """Abre la base SQLite y activa las claves foráneas."""
    conexion = sqlite3.connect(DB_PATH, check_same_thread=False)
    conexion.execute("PRAGMA foreign_keys = ON")
    crear_estructura_y_datos(conexion)
    return conexion


def crear_estructura_y_datos(conexion):
    """Crea las tablas y datos de ejemplo si la base todavía está vacía."""
    cursor = conexion.cursor()
    cursor.executescript(
        """
        CREATE TABLE IF NOT EXISTS productos (
            id_producto INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL UNIQUE,
            categoria TEXT NOT NULL,
            unidad_medida TEXT NOT NULL DEFAULT 'unidad',
            stock_minimo INTEGER NOT NULL DEFAULT 0 CHECK (stock_minimo >= 0),
            activo INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1))
        );

        CREATE TABLE IF NOT EXISTS movimientos (
            id_movimiento INTEGER PRIMARY KEY AUTOINCREMENT,
            id_producto INTEGER NOT NULL,
            tipo TEXT NOT NULL CHECK (
                tipo IN ('entrada', 'salida', 'reposicion', 'devolucion',
                         'ajuste_positivo', 'ajuste_negativo')
            ),
            cantidad INTEGER NOT NULL CHECK (cantidad > 0),
            fecha TEXT NOT NULL,
            observacion TEXT,
            FOREIGN KEY (id_producto) REFERENCES productos (id_producto)
        );

        CREATE VIEW IF NOT EXISTS inventario_actual AS
        SELECT
            p.id_producto,
            p.nombre,
            p.categoria,
            p.unidad_medida,
            p.stock_minimo,
            COALESCE(SUM(
                CASE
                    WHEN m.tipo IN ('entrada', 'reposicion', 'devolucion', 'ajuste_positivo')
                        THEN m.cantidad
                    WHEN m.tipo IN ('salida', 'ajuste_negativo')
                        THEN -m.cantidad
                    ELSE 0
                END
            ), 0) AS stock_actual
        FROM productos AS p
        LEFT JOIN movimientos AS m ON m.id_producto = p.id_producto
        GROUP BY p.id_producto, p.nombre, p.categoria,
                 p.unidad_medida, p.stock_minimo;

        CREATE TABLE IF NOT EXISTS pedidos (
            id_pedido INTEGER PRIMARY KEY AUTOINCREMENT,
            tercero TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS movimientos_pedido (
            id_movimiento INTEGER PRIMARY KEY REFERENCES movimientos(id_movimiento),
            id_pedido INTEGER NOT NULL REFERENCES pedidos(id_pedido)
        );
        """
    )

    cantidad_productos = cursor.execute("SELECT COUNT(*) FROM productos").fetchone()[0]
    if cantidad_productos == 0:
        cursor.executemany(
            """
            INSERT INTO productos (nombre, categoria, unidad_medida, stock_minimo)
            VALUES (?, ?, ?, ?)
            """,
            [
                ("Límpido", "Desinfectantes", "litro", 10),
                ("Limpiavidrios", "Limpieza de vidrios", "litro", 8),
                ("Jabón líquido", "Jabones", "litro", 10),
                ("Bolsas negras", "Desechables", "paquete", 5),
            ],
        )
        cursor.executemany(
            """
            INSERT INTO movimientos (id_producto, tipo, cantidad, fecha, observacion)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (1, "entrada", 50, "2026-09-23", "Reposición inicial de límpido"),
                (2, "entrada", 30, "2026-09-23", "Compra de limpiavidrios"),
                (1, "salida", 10, "2026-09-23", "Entrega a cliente"),
                (3, "reposicion", 20, "2026-09-24", "Compra semanal de jabón"),
                (4, "entrada", 12, "2026-09-24", "Recepción de bolsas"),
                (2, "devolucion", 2, "2026-09-25", "Devolución de producto"),
                (1, "ajuste_negativo", 3, "2026-09-25", "Producto vencido"),
                (3, "salida", 5, "2026-09-26", "Uso en servicio de aseo"),
            ],
        )
        conexion.commit()


conexion = obtener_conexion()


def leer(sql, parametros=()):
    """Ejecuta una consulta y devuelve un DataFrame."""
    return pd.read_sql_query(sql, conexion, params=parametros)


def inventario():
    return leer(
        """
        SELECT *,
               CASE WHEN stock_actual <= stock_minimo
                    THEN 'Reponer' ELSE 'Stock suficiente' END AS estado
        FROM inventario_actual
        ORDER BY nombre
        """
    )


def producto_por_id(id_producto):
    return conexion.execute(
        "SELECT id_producto, nombre FROM productos WHERE id_producto = ? AND activo = 1",
        (id_producto,),
    ).fetchone()


def registrar_entrada(id_producto, cantidad, tipo, fecha_movimiento, observacion):
    """Registra una entrada o ajuste positivo."""
    if not isinstance(cantidad, int) or cantidad <= 0:
        return False, "La cantidad debe ser un entero positivo."
    if tipo not in {"entrada", "reposicion", "devolucion", "ajuste_positivo"}:
        return False, "El tipo no corresponde a una entrada."
    if producto_por_id(id_producto) is None:
        return False, "El producto no existe."
    try:
        conexion.execute(
            """
            INSERT INTO movimientos (id_producto, tipo, cantidad, fecha, observacion)
            VALUES (?, ?, ?, ?, ?)
            """,
            (id_producto, tipo, cantidad, fecha_movimiento, observacion),
        )
        conexion.commit()
        return True, "Entrada registrada correctamente."
    except sqlite3.Error as error:
        conexion.rollback()
        return False, f"No se pudo registrar la entrada: {error}"


def registrar_salida(id_producto, cantidad, tipo, fecha_movimiento, observacion):
    """Registra una salida solo si el stock disponible es suficiente."""
    if not isinstance(cantidad, int) or cantidad <= 0:
        return False, "La cantidad debe ser un entero positivo."
    if tipo not in {"salida", "ajuste_negativo"}:
        return False, "El tipo no corresponde a una salida."
    if producto_por_id(id_producto) is None:
        return False, "El producto no existe."
    stock = conexion.execute(
        "SELECT stock_actual FROM inventario_actual WHERE id_producto = ?",
        (id_producto,),
    ).fetchone()[0]
    if cantidad > stock:
        return False, f"Stock insuficiente: hay {stock} y se solicitaron {cantidad}."
    try:
        conexion.execute(
            """
            INSERT INTO movimientos (id_producto, tipo, cantidad, fecha, observacion)
            VALUES (?, ?, ?, ?, ?)
            """,
            (id_producto, tipo, cantidad, fecha_movimiento, observacion),
        )
        conexion.commit()
        return True, "Salida registrada correctamente."
    except sqlite3.Error as error:
        conexion.rollback()
        return False, f"No se pudo registrar la salida: {error}"


def registrar_pedido(tipo, tercero, items, fecha_movimiento, observacion):
    """Registra un pedido y todos sus movimientos en una sola transacción."""
    tercero = tercero.strip()
    if tipo not in {"entrada", "salida"} or not tercero or not items:
        return False, "Selecciona el tipo, indica el proveedor o cliente y añade productos."
    if any(type(cantidad) is not int or cantidad <= 0 for cantidad in items.values()):
        return False, "Todas las cantidades deben ser enteros positivos."

    try:
        with closing(sqlite3.connect(DB_PATH)) as db:
            db.execute("PRAGMA foreign_keys = ON")
            with db:
                db.execute("BEGIN IMMEDIATE")
                for id_producto, cantidad in items.items():
                    producto = db.execute(
                        """SELECT p.nombre, i.stock_actual FROM productos p
                           JOIN inventario_actual i USING (id_producto)
                           WHERE p.id_producto = ? AND p.activo = 1""",
                        (id_producto,),
                    ).fetchone()
                    if producto is None:
                        raise ValueError("Un producto ya no está activo. Revisa el pedido.")
                    nombre, stock = producto
                    if tipo == "salida" and cantidad > stock:
                        raise ValueError(f"{nombre}: hay {stock} disponibles y pediste {cantidad}.")

                pedido = db.execute("INSERT INTO pedidos (tercero) VALUES (?)", (tercero,)).lastrowid
                for id_producto, cantidad in items.items():
                    movimiento = db.execute(
                        """INSERT INTO movimientos (id_producto, tipo, cantidad, fecha, observacion)
                           VALUES (?, ?, ?, ?, ?)""",
                        (id_producto, tipo, cantidad, fecha_movimiento, observacion),
                    ).lastrowid
                    db.execute(
                        "INSERT INTO movimientos_pedido (id_movimiento, id_pedido) VALUES (?, ?)",
                        (movimiento, pedido),
                    )
        return True, f"Pedido #{pedido} registrado ({len(items)} productos)."
    except ValueError as error:
        return False, f"Pedido sin registrar. {error}"
    except (sqlite3.Error, OverflowError):
        return False, "No se pudo registrar el pedido. Revisa las cantidades o inténtalo de nuevo."


st.title(f"{ICONO_BRILLA_ASEO} Brilla Aseo - Sistema de inventario")
st.caption("Control de productos, movimientos, existencias y alertas")

datos = inventario()
total_productos = len(datos)
total_unidades = int(datos["stock_actual"].sum()) if not datos.empty else 0
total_alertas = int((datos["stock_actual"] <= datos["stock_minimo"]).sum()) if not datos.empty else 0

tab_inicio, tab_registro, tab_inventario, tab_alertas = st.tabs(
    ["Inicio", "Registrar movimiento", "Inventario", "Alertas"]
)

with tab_inicio:
    st.subheader("Resumen general")
    c1, c2, c3 = st.columns(3)
    c1.metric("Productos", total_productos)
    c2.metric("Unidades disponibles", total_unidades)
    c3.metric("Productos por reponer", total_alertas)
    st.write("Use las pestañas para registrar movimientos y consultar el estado actual.")
    st.dataframe(datos[["nombre", "categoria", "stock_actual", "stock_minimo", "estado"]], use_container_width=True, hide_index=True)

with tab_registro:
    st.subheader("Registrar pedido")
    if mensaje_guardado := st.session_state.pop("pedido_guardado", None):
        st.success(mensaje_guardado)
        st.session_state["productos_pedido"] = []
    catalogo = leer("SELECT id_producto, nombre, unidad_medida FROM productos WHERE activo = 1 ORDER BY nombre")
    nombres = {fila.id_producto: f"{fila.nombre} ({fila.unidad_medida})" for fila in catalogo.itertuples()}
    tipo = st.radio("Tipo de pedido", ["entrada", "salida"], format_func={
        "entrada": "Compra recibida (entrada)", "salida": "Despacho a cliente (salida)"
    }.__getitem__, horizontal=True)
    seleccion = st.multiselect("Productos del pedido", list(nombres), format_func=nombres.__getitem__, key="productos_pedido")
    with st.form("formulario_pedido"):
        tercero = st.text_input("Proveedor" if tipo == "entrada" else "Cliente", key=f"tercero_{tipo}")
        fecha_movimiento = st.date_input("Fecha del movimiento", value=date.today())
        items = {
            id_producto: st.number_input(
                f"Cantidad de {nombres[id_producto]}", min_value=1, step=1,
                value=1, key=f"cantidad_pedido_{id_producto}",
            )
            for id_producto in seleccion
        }
        observacion = st.text_input("Observación (opcional)")
        enviar = st.form_submit_button("Confirmar pedido", disabled=not seleccion)
    if enviar:
        ok, mensaje = registrar_pedido(tipo, tercero, items, fecha_movimiento.isoformat(), observacion)
        if ok:
            st.session_state["pedido_guardado"] = mensaje
            st.rerun()
        st.error(mensaje)

    with st.expander("Consultar pedidos registrados"):
        historial = leer("""
            SELECT p.id_pedido AS Pedido, m.fecha AS Fecha,
                   CASE m.tipo WHEN 'entrada' THEN 'Compra recibida' ELSE 'Despacho' END AS Tipo,
                   p.tercero AS 'Proveedor o cliente', pr.nombre AS Producto,
                   m.cantidad AS Cantidad, pr.unidad_medida AS Unidad
            FROM pedidos p
            JOIN movimientos_pedido mp USING (id_pedido)
            JOIN movimientos m USING (id_movimiento)
            JOIN productos pr USING (id_producto)
            ORDER BY p.id_pedido DESC, m.id_movimiento
        """)
        if historial.empty:
            st.info("Aún no hay pedidos registrados.")
        else:
            st.dataframe(historial, hide_index=True)

with tab_inventario:
    st.subheader("Inventario actual")
    categorias = ["Todas"] + sorted(datos["categoria"].dropna().unique().tolist())
    categoria = st.selectbox("Filtrar por categoría", categorias)
    mostrar = datos if categoria == "Todas" else datos[datos["categoria"] == categoria]
    st.dataframe(mostrar, use_container_width=True, hide_index=True)
    st.download_button("Descargar inventario CSV", mostrar.to_csv(index=False).encode("utf-8"), "inventario_brilla_aseo.csv", "text/csv")

with tab_alertas:
    st.subheader("Alertas de stock")
    alertas = datos[datos["stock_actual"] <= datos["stock_minimo"]]
    if alertas.empty:
        st.success("No hay productos por debajo del stock mínimo.")
    else:
        st.warning("Estos productos requieren revisión o reposición.")
        st.dataframe(alertas[["nombre", "categoria", "stock_actual", "stock_minimo", "estado"]], use_container_width=True, hide_index=True)

