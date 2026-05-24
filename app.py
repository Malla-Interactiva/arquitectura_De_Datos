# =============================================================================
# app.py — Normalizador COMUNAS_NORM (versión local)
# Arquitectura y Almacenamiento de Datos — Evaluación 2 · 2026
# Integrantes: Jostin Sáez, Gerardo Millán, Eduardo Díaz
# =============================================================================
# Cómo ejecutar:
#   1. Instalar dependencias: pip install flask
#   2. Iniciar servidor:      python app.py
#   3. Se abre automáticamente en:  http://localhost:5000
# =============================================================================
# Módulos:
#   - COMUNAS_NORM: Normalización de nombres de comunas chilenas
#   - FAMOSOS:      ETL de personas famosas con fechas de nacimiento
#   - LUGARES:      ETL de lugares georreferenciados con direcciones
# =============================================================================

import os
import re
import sqlite3
import unicodedata
import webbrowser                     # Abre el navegador automáticamente
from datetime import datetime
from io import StringIO, BytesIO
from threading import Timer           # Delay para abrir el navegador

from flask import Flask, render_template, request, jsonify, send_file, g

# ===========================================================================
# CONFIGURACION
# ===========================================================================

app = Flask(__name__)

# La BD se guarda en la ruta indicada por DATABASE_PATH, o en la misma carpeta por defecto
DATABASE = os.environ.get('DATABASE_PATH', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'comunas_norm.db'))
PORT     = int(os.environ.get('PORT', 5000))


# ===========================================================================
# BASE DE DATOS — CONEXION Y ESQUEMA
# ===========================================================================

def get_db():
    """Obtiene la conexion SQLite reutilizable por peticion HTTP."""
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row   # filas como diccionarios
    return g.db

def close_db(e=None):
    """Cierra la conexion al terminar cada peticion."""
    db = g.pop('db', None)
    if db is not None:
        db.close()

app.teardown_appcontext(close_db)


def init_db():
    """
    Crea las tablas si no existen al iniciar la aplicacion.

    COMUNAS_NORM  — nombres de comunas normalizados y unicos
    PROCESO_LOG   — historial de cada transformacion del ETL
    FAMOSOS       — personas famosas con fechas normalizadas
    FAMOSOS_LOG   — log del procesamiento de famosos
    LUGARES       — lugares georreferenciados
    GEOREFERENCIAS — coordenadas de cada lugar
    DIRECCIONES   — direcciones parseadas de cada lugar
    LUGARES_LOG   — log del procesamiento de lugares
    """
    db = sqlite3.connect(DATABASE)

    # --- Tablas del módulo COMUNAS_NORM (existentes) ---
    db.execute('''
        CREATE TABLE IF NOT EXISTS COMUNAS_NORM (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre_comuna   TEXT    NOT NULL UNIQUE,
            fecha_insercion TEXT    NOT NULL
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS PROCESO_LOG (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            sesion_id   TEXT    NOT NULL,
            linea_num   INTEGER,
            valor_orig  TEXT,
            valor_norm  TEXT,
            estado      TEXT,    -- OK | MODIFICADO | DUPLICADO
            fecha       TEXT     NOT NULL
        )
    ''')

    # --- Tablas del módulo FAMOSOS ---
    db.execute('''
        CREATE TABLE IF NOT EXISTS FAMOSOS (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre            TEXT NOT NULL,
            fecha_original    TEXT,
            fecha_normalizada TEXT,
            es_historica      INTEGER DEFAULT 0,
            edad              INTEGER,
            es_cumpleanos     INTEGER DEFAULT 0,
            fecha_insercion   TEXT NOT NULL
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS FAMOSOS_LOG (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            sesion_id   TEXT NOT NULL,
            linea_num   INTEGER,
            nombre_orig TEXT,
            fecha_orig  TEXT,
            fecha_norm  TEXT,
            estado      TEXT,
            fecha       TEXT NOT NULL
        )
    ''')

    # --- Tablas del módulo LUGARES (3 tablas normalizadas) ---
    db.execute('''
        CREATE TABLE IF NOT EXISTS LUGARES (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre_lugar    TEXT NOT NULL,
            fecha_insercion TEXT NOT NULL
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS GEOREFERENCIAS (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            id_lugar    INTEGER NOT NULL REFERENCES LUGARES(id),
            latitud     REAL NOT NULL,
            longitud    REAL NOT NULL
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS DIRECCIONES (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            id_lugar                INTEGER NOT NULL REFERENCES LUGARES(id),
            direccion_completa      TEXT,
            nombre_calle            TEXT,
            numero_calle            TEXT,
            ciudad_estado_provincia TEXT,
            pais                    TEXT
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS LUGARES_LOG (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            sesion_id       TEXT NOT NULL,
            linea_num       INTEGER,
            nombre_orig     TEXT,
            estado          TEXT,
            detalle         TEXT,
            fecha           TEXT NOT NULL
        )
    ''')

    db.commit()
    db.close()
    print(f"[DB] Base de datos lista en: {DATABASE}")

# Inicializar la base de datos automáticamente (necesario para Gunicorn)
init_db()


# ===========================================================================
# FUNCIONES DE NORMALIZACION (módulo COMUNAS_NORM existente)
# ===========================================================================

def quitar_tildes(texto):
    """
    Elimina tildes usando descomposicion Unicode NFD.
    'a' con tilde se descompone en 'a' + marca de acento; la marca se filtra.
    Ejemplo: 'Concepcion' (con tilde en o) -> 'Concepcion'
    """
    nfd = unicodedata.normalize('NFD', texto)
    return ''.join(c for c in nfd if unicodedata.category(c) != 'Mn')


def reemplazar_enie(texto):
    """Reemplaza n con tilde -> n y N con tilde -> N conservando el case."""
    return texto.replace('\u00f1', 'n').replace('\u00d1', 'N')


def a_titulo(texto):
    """
    Formato Titulo respetando preposiciones y articulos del espanol.
    Ejemplo: 'SAN PEDRO DE LA PAZ' -> 'San Pedro de la Paz'
    """
    minusculas = {'de','del','la','las','los','el','y','e','o','a',
                  'en','al','con','por','para','entre','sin','sobre',
                  'bajo','ante','tras','desde','hasta'}
    palabras = texto.lower().split()
    return ' '.join(
        p.capitalize() if i == 0 or p not in minusculas else p
        for i, p in enumerate(palabras)
    )


def aplicar_formato(texto, fmt):
    """Aplica el formato de texto elegido: titulo | mayus | minus."""
    texto = re.sub(r"'\s+", "'", texto.strip())
    if fmt == 'titulo': return a_titulo(texto)
    if fmt == 'mayus':  return texto.upper()
    if fmt == 'minus':  return texto.lower()
    return texto


def normalizar_valor(raw, opts):
    """
    Aplica todas las transformaciones en orden:
      1. Limpiar espacios multiples
      2. Quitar tildes
      3. Reemplazar n-tilde
      4. Aplicar formato de texto
    """
    val = raw.strip()
    if opts.get('spaces'): val = re.sub(r'\s+', ' ', val)
    if opts.get('tildes'): val = quitar_tildes(val)
    if opts.get('enie'):   val = reemplazar_enie(val)
    val = aplicar_formato(val, opts.get('fmt', 'titulo'))
    return val


def clave_dedup(texto):
    """
    Clave de comparacion para detectar duplicados:
    elimina todo lo que no sea letra o numero y convierte a minusculas.
    'San Pedro', 'SAN PEDRO', 'san-pedro' -> 'sanpedro' (mismo -> duplicado)
    """
    s = quitar_tildes(texto).replace('\u00f1','n').replace('\u00d1','N')
    return re.sub(r'[^a-z0-9]', '', s.lower())


# ===========================================================================
# PROCESO ETL PRINCIPAL (módulo COMUNAS_NORM)
# ===========================================================================

def procesar_datos(lineas, opts, sesion_id):
    """
    Ejecuta el ETL sobre la lista de lineas del archivo cargado.
    Retorna rows (detalle por linea), resultado (lista limpia final),
    estadisticas y log de texto.
    """
    log      = []
    rows     = []
    resultado = []
    seen     = {}    # clave_dedup -> numero de linea donde se vio primero
    dups     = 0
    changes  = 0

    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log += [
        '=' * 55, '  LOG COMUNAS_NORM', '=' * 55,
        f'Fecha     : {now}',
        f'Sesion    : {sesion_id}',
        f'Registros : {len(lineas)}',
        f'Formato   : {opts.get("fmt")}',
        f'Tildes    : {"Si" if opts.get("tildes") else "No"}',
        f'N-tilde   : {"Si" if opts.get("enie") else "No"}',
        f'Duplicados: {"Si" if opts.get("dedup") else "No"}',
        f'Espacios  : {"Si" if opts.get("spaces") else "No"}',
        '-' * 55,
    ]

    for idx, original in enumerate(lineas):
        num        = idx + 1
        normalizado = normalizar_valor(original, opts)
        changed    = original.strip() != normalizado
        clave      = clave_dedup(normalizado)

        if opts.get('dedup') and clave in seen:
            estado = 'DUPLICADO'
            dups  += 1
            log.append(f'[{str(num).rjust(5)}] DUPLICADO  | "{original.strip()}" -> "{normalizado}" (igual a linea {seen[clave]})')
        else:
            if opts.get('dedup'):
                seen[clave] = num
            if changed:
                estado   = 'MODIFICADO'
                changes += 1
                log.append(f'[{str(num).rjust(5)}] MODIFICADO | "{original.strip()}" -> "{normalizado}"')
            else:
                estado = 'OK'
            resultado.append(normalizado)

        rows.append({'linea': num, 'original': original.strip(),
                     'normalizado': normalizado, 'estado': estado})

    log += [
        '-' * 55,
        f'Originales  : {len(lineas)}',
        f'Duplicados  : {dups}',
        f'Unicos      : {len(resultado)}',
        f'Modificados : {changes}',
        '=' * 55,
    ]

    return {
        'rows': rows, 'resultado': resultado, 'log_lines': log,
        'stats': {'total': len(lineas), 'unicos': len(resultado),
                  'duplicados': dups, 'modificados': changes}
    }


# ===========================================================================
# GUARDADO EN BASE DE DATOS (módulo COMUNAS_NORM)
# ===========================================================================

def guardar_en_db(resultado, rows, sesion_id):
    """
    Inserta comunas normalizadas en COMUNAS_NORM y el detalle en PROCESO_LOG.
    INSERT OR IGNORE: no falla si el nombre ya existe (campo UNIQUE).
    """
    db  = get_db()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    for nombre in resultado:
        db.execute(
            'INSERT OR IGNORE INTO COMUNAS_NORM (nombre_comuna, fecha_insercion) VALUES (?,?)',
            (nombre, now)
        )
    for r in rows:
        db.execute(
            'INSERT INTO PROCESO_LOG (sesion_id,linea_num,valor_orig,valor_norm,estado,fecha) VALUES (?,?,?,?,?,?)',
            (sesion_id, r['linea'], r['original'], r['normalizado'], r['estado'], now)
        )
    db.commit()


# ===========================================================================
# ===========================================================================
#  MÓDULO I — FAMOSOS (ETL de personas famosas con fechas)
# ===========================================================================
# ===========================================================================

# ---------------------------------------------------------------------------
# Funciones auxiliares para parseo de fechas del módulo FAMOSOS
# ---------------------------------------------------------------------------

def es_fecha_historica(texto_fecha):
    """
    Detecta si una fecha es histórica (a.C., "alrededor de", etc.).
    Retorna True si la fecha no puede parsearse como fecha moderna.
    Ejemplos:
      "alrededor del 69 a.C."  -> True
      "100 a.C./07/12"         -> True
      "alrededor de 1028"      -> True
      "1564/04/23"             -> False
    """
    texto = texto_fecha.lower().strip()
    # Detectar marcadores de fechas históricas
    if 'a.c.' in texto or 'a.c' in texto:
        return True
    if 'alrededor' in texto:
        return True
    return False


def parsear_fecha(texto_fecha):
    """
    Intenta parsear una fecha en múltiples formatos y normalizarla a DD-MM-YYYY.

    Formatos soportados:
      1. YYYY/MM/DD  → ej: 1564/04/23
      2. YYYY-MM-DD  → ej: 1879-03-14
      3. DD-MM-YYYY  → ej: 24-07-1897
      4. DD/MM/YYYY  → ej: 25/10/1881

    Retorna:
      - (fecha_normalizada_str, objeto_date) si se pudo parsear
      - (None, None) si es histórica o no parseable
    """
    texto = texto_fecha.strip()

    # Si es fecha histórica, no intentar parsear
    if es_fecha_historica(texto):
        return None, None

    # Lista de formatos a intentar en orden de prioridad
    # Primero formatos YYYY al inicio (año > 1000), luego DD al inicio
    formatos = []

    # Determinar si empieza con un año (4 dígitos) o un día (1-2 dígitos)
    # Separar por / o -
    partes = re.split(r'[/\-]', texto)

    if len(partes) == 3:
        primera = partes[0].strip()
        tercera = partes[2].strip()

        # Si la primera parte tiene 4 dígitos → probablemente YYYY al inicio
        if len(primera) == 4 and primera.isdigit():
            formatos = [
                ('%Y/%m/%d', '/'),
                ('%Y-%m-%d', '-'),
            ]
        # Si la tercera parte tiene 4 dígitos → probablemente YYYY al final
        elif len(tercera) == 4 and tercera.isdigit():
            formatos = [
                ('%d-%m-%Y', '-'),
                ('%d/%m/%Y', '/'),
            ]
        else:
            # Intentar todos los formatos
            formatos = [
                ('%Y/%m/%d', '/'),
                ('%Y-%m-%d', '-'),
                ('%d-%m-%Y', '-'),
                ('%d/%m/%Y', '/'),
            ]

    # Intentar cada formato
    for fmt, sep in formatos:
        try:
            fecha_obj = datetime.strptime(texto, fmt)
            # Normalizar a DD-MM-YYYY
            normalizada = fecha_obj.strftime('%d-%m-%Y')
            return normalizada, fecha_obj
        except ValueError:
            continue

    # Si ningún formato funcionó, intentar todos los posibles como respaldo
    todos_formatos = [
        '%Y/%m/%d', '%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y',
        '%Y/%m/%d', '%m/%d/%Y', '%m-%d-%Y',
    ]
    for fmt in todos_formatos:
        try:
            fecha_obj = datetime.strptime(texto, fmt)
            normalizada = fecha_obj.strftime('%d-%m-%Y')
            return normalizada, fecha_obj
        except ValueError:
            continue

    return None, None


def calcular_edad(fecha_obj):
    """
    Calcula la edad en años cumplidos a partir de un objeto datetime.
    Retorna la edad como entero o None si no se puede calcular.
    """
    if fecha_obj is None:
        return None
    hoy = datetime.now()
    edad = hoy.year - fecha_obj.year
    # Ajustar si aún no ha cumplido años este año
    if (hoy.month, hoy.day) < (fecha_obj.month, fecha_obj.day):
        edad -= 1
    return edad


def verificar_cumpleanos(fecha_obj):
    """
    Verifica si hoy es el cumpleaños de la persona.
    Compara día y mes de la fecha de nacimiento con la fecha actual.
    Retorna 1 si es cumpleaños, 0 si no lo es.
    """
    if fecha_obj is None:
        return 0
    hoy = datetime.now()
    if hoy.month == fecha_obj.month and hoy.day == fecha_obj.day:
        return 1
    return 0


def clave_dedup_famoso(nombre, fecha_norm):
    """
    Genera una clave de deduplicación para detectar famosos duplicados.
    Combina el nombre normalizado (lowercase, sin espacios extra) con la fecha.
    Así detecta duplicados aunque la fecha original esté en diferente formato.

    Ejemplo:
      ("Vincent van Gogh", "30-03-1853") en líneas 22, 62, 82
      → clave: "vincentvangogh|30-03-1853"
    """
    nombre_norm = re.sub(r'[^a-z0-9]', '', nombre.lower().strip())
    fecha_key = fecha_norm if fecha_norm else 'historica'
    return f"{nombre_norm}|{fecha_key}"


def procesar_famosos(lineas, sesion_id, db=None):
    """
    Ejecuta el ETL completo sobre el archivo de famosos.

    Pasos:
      1. Parsear número de ítem y eliminarlo
      2. Separar nombre de fecha usando " - " como delimitador
      3. Detectar y parsear formatos de fecha → normalizar a DD-MM-YYYY
      4. Para fechas históricas → guardar como NULL, registrar texto original
      5. Eliminar duplicados (misma persona + misma fecha real)
      6. Calcular edad actual
      7. Calcular campo es_cumpleanos (1 si hoy es su cumpleaños)

    Retorna diccionario con datos procesados, estadísticas y log.
    """
    log = []
    registros = []       # Lista de registros válidos (no duplicados)
    log_entries = []     # Entradas para FAMOSOS_LOG
    seen = {}            # Clave dedup → número de línea original

    if db:
        rows_db = db.execute('SELECT nombre, fecha_normalizada FROM FAMOSOS').fetchall()
        for r in rows_db:
            clave = clave_dedup_famoso(r['nombre'], r['fecha_normalizada'])
            seen[clave] = 'Base de Datos'

    dups = 0
    historicas = 0
    cumpleanos_hoy = 0

    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    log += [
        '=' * 60,
        '  LOG FAMOSOS — Procesamiento ETL',
        '=' * 60,
        f'Fecha     : {now_str}',
        f'Sesión    : {sesion_id}',
        f'Líneas    : {len(lineas)}',
        '-' * 60,
    ]

    for idx, linea in enumerate(lineas):
        num = idx + 1
        linea = linea.strip()
        if not linea:
            continue

        # Paso 1: Eliminar el número de ítem al inicio ("1. ", "56. ", etc.)
        match_num = re.match(r'^\d+\.\s*', linea)
        if match_num:
            linea_sin_num = linea[match_num.end():]
        else:
            linea_sin_num = linea

        # Paso 2: Separar nombre de fecha usando " - " como delimitador
        if ' - ' in linea_sin_num:
            partes = linea_sin_num.split(' - ', 1)
            nombre = partes[0].strip()
            fecha_texto = partes[1].strip()
        else:
            # Si no hay delimitador, todo es nombre sin fecha
            nombre = linea_sin_num.strip()
            fecha_texto = ''

        # Paso 3: Detectar si es fecha histórica
        es_hist = es_fecha_historica(fecha_texto) if fecha_texto else False

        # Paso 4: Parsear la fecha
        if es_hist:
            fecha_normalizada = None
            fecha_obj = None
            historicas += 1
            estado = 'HISTORICA'
            log.append(f'[{str(num).rjust(5)}] HISTORICA  | "{nombre}" — fecha: "{fecha_texto}" (no parseable)')
        elif fecha_texto:
            fecha_normalizada, fecha_obj = parsear_fecha(fecha_texto)
            if fecha_normalizada:
                estado = 'OK'
            else:
                # No se pudo parsear pero no es histórica → marcar como histórica
                fecha_normalizada = None
                fecha_obj = None
                es_hist = True
                historicas += 1
                estado = 'HISTORICA'
                log.append(f'[{str(num).rjust(5)}] HISTORICA  | "{nombre}" — fecha no reconocida: "{fecha_texto}"')
        else:
            fecha_normalizada = None
            fecha_obj = None
            es_hist = True
            historicas += 1
            estado = 'HISTORICA'

        # Paso 5: Deduplicación
        clave = clave_dedup_famoso(nombre, fecha_normalizada)

        if clave in seen:
            dups += 1
            estado = 'DUPLICADO'
            log.append(f'[{str(num).rjust(5)}] DUPLICADO  | "{nombre}" (igual a línea {seen[clave]})')
            # Registrar en log pero no agregar a registros
            log_entries.append({
                'linea_num': num,
                'nombre_orig': nombre,
                'fecha_orig': fecha_texto,
                'fecha_norm': fecha_normalizada,
                'estado': 'DUPLICADO'
            })
            continue

        seen[clave] = num

        # Paso 6: Calcular edad
        edad = calcular_edad(fecha_obj)
        if es_hist:
            edad = None

        # Paso 7: Calcular es_cumpleanos
        es_cumple = verificar_cumpleanos(fecha_obj) if not es_hist else 0
        if es_cumple:
            cumpleanos_hoy += 1

        if estado == 'OK':
            # Verificar si la fecha fue modificada (formato diferente al original)
            if fecha_texto != fecha_normalizada:
                estado = 'MODIFICADO'
                log.append(f'[{str(num).rjust(5)}] MODIFICADO | "{nombre}" — "{fecha_texto}" → "{fecha_normalizada}"')
            else:
                log.append(f'[{str(num).rjust(5)}] OK         | "{nombre}" — "{fecha_normalizada}"')

        registro = {
            'nombre': nombre,
            'fecha_original': fecha_texto,
            'fecha_normalizada': fecha_normalizada,
            'es_historica': 1 if es_hist else 0,
            'edad': edad,
            'es_cumpleanos': es_cumple,
        }
        registros.append(registro)

        log_entries.append({
            'linea_num': num,
            'nombre_orig': nombre,
            'fecha_orig': fecha_texto,
            'fecha_norm': fecha_normalizada,
            'estado': estado
        })

    total_unicos = len(registros)

    log += [
        '-' * 60,
        f'Líneas procesadas : {len(lineas)}',
        f'Registros únicos  : {total_unicos}',
        f'Duplicados elim.  : {dups}',
        f'Fechas históricas : {historicas}',
        f'Cumpleaños hoy    : {cumpleanos_hoy}',
        '=' * 60,
    ]

    return {
        'registros': registros,
        'log_entries': log_entries,
        'log_lines': log,
        'stats': {
            'total': len(lineas),
            'unicos': total_unicos,
            'duplicados': dups,
            'historicas': historicas,
            'cumpleanos_hoy': cumpleanos_hoy,
        }
    }


def guardar_famosos_en_db(registros, log_entries, sesion_id):
    """
    Inserta los registros procesados de famosos en la base de datos.
    Guarda en FAMOSOS (datos limpios) y en FAMOSOS_LOG (historial del proceso).
    """
    db = get_db()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # Insertar registros únicos en FAMOSOS
    for r in registros:
        db.execute('''
            INSERT INTO FAMOSOS (nombre, fecha_original, fecha_normalizada,
                                 es_historica, edad, es_cumpleanos, fecha_insercion)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (r['nombre'], r['fecha_original'], r['fecha_normalizada'],
              r['es_historica'], r['edad'], r['es_cumpleanos'], now))

    # Insertar todas las entradas en FAMOSOS_LOG (incluyendo duplicados)
    for le in log_entries:
        db.execute('''
            INSERT INTO FAMOSOS_LOG (sesion_id, linea_num, nombre_orig,
                                     fecha_orig, fecha_norm, estado, fecha)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (sesion_id, le['linea_num'], le['nombre_orig'],
              le['fecha_orig'], le['fecha_norm'], le['estado'], now))

    db.commit()


# ===========================================================================
# ===========================================================================
#  MÓDULO II — LUGARES (ETL de lugares georreferenciados)
# ===========================================================================
# ===========================================================================

# ---------------------------------------------------------------------------
# Funciones auxiliares para el módulo LUGARES
# ---------------------------------------------------------------------------

def normalizar_nombre_lugar(nombre):
    """
    Normaliza el nombre de un lugar para deduplicación:
      - Quita prefijos "The " y "the "
      - Convierte a minúsculas
      - Elimina espacios extra
    Ejemplo: "The Great Wall of China" → "great wall of china"
    """
    n = nombre.strip()
    # Quitar "The " al inicio (case-insensitive)
    if n.lower().startswith('the '):
        n = n[4:]
    return n.strip().lower()


def parsear_georeferencia(geo_str):
    """
    Parsea un string de georeferencia "lat, lon" a tupla de floats.
    Ejemplo: "37.422, -122.084" → (37.422, -122.084)
    Retorna (None, None) si no se puede parsear.
    """
    try:
        partes = geo_str.split(',')
        if len(partes) == 2:
            lat = float(partes[0].strip())
            lon = float(partes[1].strip())
            return lat, lon
    except (ValueError, AttributeError):
        pass
    return None, None


def parsear_direccion(direccion_completa):
    """
    Parsea una dirección de texto libre en sus componentes.

    Estrategia:
      - El último fragmento separado por coma = PAÍS
      - El penúltimo = CIUDAD/ESTADO/PROVINCIA
      - Si hay número al inicio de la dirección → numero_calle + nombre_calle
      - Si no hay número → nombre_calle = NULL, numero_calle = NULL

    Ejemplos:
      "1600 Amphitheatre Parkway, Mountain View, CA 94043, USA"
        → calle: "Amphitheatre Parkway", num: "1600",
          ciudad: "Mountain View, CA 94043", pais: "USA"

      "Westminster, London SW1A 1AA, UK"
        → calle: NULL, num: NULL,
          ciudad: "Westminster, London SW1A 1AA", pais: "UK"

      "Vatican"
        → todo NULL excepto pais: "Vatican"
    """
    if not direccion_completa or not direccion_completa.strip():
        return None, None, None, None

    dir_text = direccion_completa.strip()

    # Separar por comas
    partes = [p.strip() for p in dir_text.split(',')]

    if len(partes) == 1:
        # Solo una parte → es el país (ej: "Vatican")
        return None, None, None, partes[0]

    # El último fragmento es el país
    pais = partes[-1].strip()

    if len(partes) == 2:
        # Dos partes: primera es ciudad/estado, segunda es país
        # Verificar si la primera parte tiene número al inicio
        primera = partes[0].strip()
        match_num = re.match(r'^(\d+[A-Za-z]?)\s+(.+)$', primera)
        if match_num:
            return match_num.group(2), match_num.group(1), None, pais
        else:
            return None, None, primera, pais

    # Tres o más partes
    # Primer fragmento puede contener calle + número
    primera = partes[0].strip()

    # Ciudad/Estado/Provincia = todo lo del medio junto
    ciudad_estado = ', '.join(partes[1:-1]).strip()

    # Verificar si el primer fragmento tiene un número al inicio
    match_num = re.match(r'^(\d+[A-Za-z]?)\s+(.+)$', primera)

    if match_num:
        numero_calle = match_num.group(1)
        nombre_calle = match_num.group(2)
    else:
        # Verificar si parece una dirección con nombre pero sin número
        # (ej: "Champ de Mars, 5 Avenue Anatole France, ...")
        # En este caso, buscar número en el segundo fragmento
        if len(partes) > 2:
            segunda = partes[1].strip() if len(partes) > 1 else ''
            match_num2 = re.match(r'^(\d+[A-Za-z]?)\s+(.+)$', segunda)
            if match_num2:
                # El nombre incluye la primera parte y la segunda
                nombre_calle = primera + ', ' + match_num2.group(2)
                numero_calle = match_num2.group(1)
                # Reajustar ciudad_estado (quitar la segunda parte que ya usamos)
                ciudad_estado = ', '.join(partes[2:-1]).strip()
            else:
                nombre_calle = None
                numero_calle = None
        else:
            nombre_calle = None
            numero_calle = None

    return nombre_calle, numero_calle, ciudad_estado if ciudad_estado else None, pais


def procesar_lugares(lineas, sesion_id, db=None):
    """
    Ejecuta el ETL completo sobre el archivo de lugares (CSV con ;).

    Pasos:
      1. Parsear cabecera y líneas del CSV
      2. Separar campos por punto y coma
      3. Parsear georeferencia a latitud/longitud
      4. Parsear dirección en componentes
      5. Deduplicación (exacta y parcial por nombre + georef)
      6. Registrar todo en el log

    Retorna diccionario con datos procesados, estadísticas y log.
    """
    log = []
    lugares_unicos = []       # Lista de lugares procesados y únicos
    log_entries = []          # Entradas para LUGARES_LOG
    seen = {}                 # clave normalizada → {linea, nombre, lat, lon}

    if db:
        rows_db = db.execute('''
            SELECT l.nombre_lugar, g.latitud, g.longitud, d.direccion_completa
            FROM LUGARES l
            JOIN GEOREFERENCIAS g ON l.id = g.id_lugar
            LEFT JOIN DIRECCIONES d ON l.id = d.id_lugar
        ''').fetchall()
        for r in rows_db:
            nombre_norm = normalizar_nombre_lugar(r['nombre_lugar'])
            lat_r = round(r['latitud'], 4)
            lon_r = round(r['longitud'], 4)
            clave = f"{nombre_norm}|{lat_r}|{lon_r}"
            seen[clave] = {'linea': 'Base de Datos', 'direccion': r['direccion_completa']}

    dups = 0
    encoding_issues = 0

    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    log += [
        '=' * 65,
        '  LOG LUGARES — Procesamiento ETL',
        '=' * 65,
        f'Fecha     : {now_str}',
        f'Sesión    : {sesion_id}',
        f'Líneas    : {len(lineas)}',
        '-' * 65,
    ]

    # Detectar y saltar la cabecera si existe
    primera_linea = lineas[0].strip() if lineas else ''
    inicio = 0
    if 'nombre' in primera_linea.lower() or 'lugar' in primera_linea.lower() or 'georeferencia' in primera_linea.lower():
        inicio = 1
        log.append(f'[CABECERA] Detectada y omitida: "{primera_linea}"')

    paises_set = set()

    for idx in range(inicio, len(lineas)):
        linea = lineas[idx].strip()
        if not linea:
            continue

        num = idx + 1

        # Separar campos por punto y coma
        campos = linea.split(';')

        if len(campos) < 3:
            log.append(f'[{str(num).rjust(5)}] ERROR      | Línea con formato incorrecto: "{linea}"')
            log_entries.append({
                'linea_num': num, 'nombre_orig': linea,
                'estado': 'ENCODING_ROTO', 'detalle': 'Formato incorrecto, faltan campos'
            })
            continue

        nombre = campos[0].strip()
        direccion_completa = campos[1].strip()
        geo_str = campos[2].strip()

        # Detectar problemas de encoding (caracteres de reemplazo)
        if '\ufffd' in nombre or '\ufffd' in direccion_completa:
            encoding_issues += 1
            log.append(f'[{str(num).rjust(5)}] ENCODING   | Caracteres reemplazados en: "{nombre}"')

        # Parsear georeferencia
        lat, lon = parsear_georeferencia(geo_str)
        if lat is None or lon is None:
            log.append(f'[{str(num).rjust(5)}] ERROR      | Georeferencia no válida: "{geo_str}"')
            log_entries.append({
                'linea_num': num, 'nombre_orig': nombre,
                'estado': 'ENCODING_ROTO', 'detalle': f'Georeferencia inválida: {geo_str}'
            })
            continue

        # Parsear dirección
        nombre_calle, numero_calle, ciudad_estado, pais = parsear_direccion(direccion_completa)

        if pais:
            paises_set.add(pais)

        # Deduplicación: nombre normalizado + coordenadas
        nombre_norm = normalizar_nombre_lugar(nombre)
        # Redondear coordenadas a 4 decimales para comparación
        lat_r = round(lat, 4)
        lon_r = round(lon, 4)
        clave = f"{nombre_norm}|{lat_r}|{lon_r}"

        if clave in seen:
            dups += 1
            orig_linea = seen[clave]['linea']
            orig_dir = seen[clave].get('direccion', '')

            # Determinar si es duplicado exacto o parcial
            if direccion_completa == orig_dir:
                estado = 'DUPLICADO_EXACTO'
                log.append(f'[{str(num).rjust(5)}] DUP_EXACTO | "{nombre}" (igual a línea {orig_linea})')
            else:
                estado = 'DUPLICADO_PARCIAL'
                log.append(f'[{str(num).rjust(5)}] DUP_PARCIA | "{nombre}" — misma georef, dif. dirección (línea {orig_linea})')

            log_entries.append({
                'linea_num': num, 'nombre_orig': nombre,
                'estado': estado, 'detalle': f'Duplicado de línea {orig_linea}'
            })
            continue

        # Registrar como visto
        seen[clave] = {'linea': num, 'direccion': direccion_completa}

        # Crear registro único
        lugar = {
            'nombre_lugar': nombre,
            'latitud': lat,
            'longitud': lon,
            'direccion_completa': direccion_completa,
            'nombre_calle': nombre_calle,
            'numero_calle': numero_calle,
            'ciudad_estado_provincia': ciudad_estado,
            'pais': pais,
        }
        lugares_unicos.append(lugar)

        estado = 'OK'
        log.append(f'[{str(num).rjust(5)}] OK         | "{nombre}" — ({lat}, {lon}) — {pais or "Sin país"}')
        log_entries.append({
            'linea_num': num, 'nombre_orig': nombre,
            'estado': estado, 'detalle': f'Procesado: {pais or "N/A"}'
        })

    log += [
        '-' * 65,
        f'Líneas procesadas     : {len(lineas) - inicio}',
        f'Lugares únicos        : {len(lugares_unicos)}',
        f'Duplicados eliminados : {dups}',
        f'Problemas encoding    : {encoding_issues}',
        f'Países detectados     : {len(paises_set)}',
        '=' * 65,
    ]

    return {
        'lugares': lugares_unicos,
        'log_entries': log_entries,
        'log_lines': log,
        'paises': sorted(paises_set),
        'stats': {
            'total_lugares': len(lugares_unicos),
            'total_georef': len(lugares_unicos),
            'total_direcciones': len(lugares_unicos),
            'duplicados_eliminados': dups,
            'paises_unicos': len(paises_set),
        }
    }


def guardar_lugares_en_db(lugares, log_entries, sesion_id):
    """
    Inserta los registros procesados de lugares en las 3 tablas normalizadas:
      - LUGARES: nombre y fecha de inserción
      - GEOREFERENCIAS: latitud y longitud vinculadas al lugar
      - DIRECCIONES: dirección parseada en componentes, vinculada al lugar

    También registra el log en LUGARES_LOG.
    """
    db = get_db()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    for lugar in lugares:
        # Insertar en LUGARES
        cursor = db.execute(
            'INSERT INTO LUGARES (nombre_lugar, fecha_insercion) VALUES (?, ?)',
            (lugar['nombre_lugar'], now)
        )
        id_lugar = cursor.lastrowid

        # Insertar en GEOREFERENCIAS
        db.execute(
            'INSERT INTO GEOREFERENCIAS (id_lugar, latitud, longitud) VALUES (?, ?, ?)',
            (id_lugar, lugar['latitud'], lugar['longitud'])
        )

        # Insertar en DIRECCIONES
        db.execute('''
            INSERT INTO DIRECCIONES (id_lugar, direccion_completa, nombre_calle,
                                     numero_calle, ciudad_estado_provincia, pais)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (id_lugar, lugar['direccion_completa'], lugar['nombre_calle'],
              lugar['numero_calle'], lugar['ciudad_estado_provincia'], lugar['pais']))

    # Insertar entradas de log
    for le in log_entries:
        db.execute('''
            INSERT INTO LUGARES_LOG (sesion_id, linea_num, nombre_orig,
                                     estado, detalle, fecha)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (sesion_id, le['linea_num'], le['nombre_orig'],
              le['estado'], le.get('detalle', ''), now))

    db.commit()


# ===========================================================================
# RUTAS — PAGINAS HTML
# ===========================================================================

@app.route('/')
def index():
    """Pagina principal: carga de archivo y normalizacion."""
    return render_template('index.html')


@app.route('/base-datos')
def vista_bd():
    """Pagina de visor de base de datos SQLite."""
    return render_template('bd.html')


@app.route('/famosos')
def vista_famosos():
    """Página del módulo FAMOSOS: carga y procesamiento de datos de personas famosas."""
    return render_template('famosos.html')


@app.route('/lugares')
def vista_lugares():
    """Página del módulo LUGARES: carga y procesamiento de lugares georreferenciados."""
    return render_template('lugares.html')


# ===========================================================================
# RUTAS — API JSON (módulo COMUNAS_NORM existente)
# ===========================================================================

@app.route('/api/normalizar', methods=['POST'])
def api_normalizar():
    """Recibe archivo y opciones, ejecuta ETL y guarda en la BD."""
    if 'archivo' not in request.files:
        return jsonify({'error': 'No se recibio archivo'}), 400

    archivo   = request.files['archivo']
    contenido = archivo.read().decode('utf-8', errors='replace')
    lineas    = [l.strip() for l in contenido.splitlines() if l.strip()]

    if not lineas:
        return jsonify({'error': 'El archivo esta vacio'}), 400

    opts = {
        'fmt':    request.form.get('fmt', 'titulo'),
        'tildes': request.form.get('tildes', 'true') == 'true',
        'enie':   request.form.get('enie',   'true') == 'true',
        'dedup':  request.form.get('dedup',  'true') == 'true',
        'spaces': request.form.get('spaces', 'true') == 'true',
    }

    sesion_id = datetime.now().strftime('%Y%m%d_%H%M%S')
    etl       = procesar_datos(lineas, opts, sesion_id)
    guardar_en_db(etl['resultado'], etl['rows'], sesion_id)

    return jsonify({
        'sesion_id': sesion_id,
        'stats':     etl['stats'],
        'preview':   etl['rows'][:200],
        'log':       '\n'.join(etl['log_lines'])
    })


@app.route('/api/comunas')
def api_comunas():
    """Lista comunas con busqueda opcional por nombre."""
    db     = get_db()
    q      = request.args.get('q', '').strip()
    limite = int(request.args.get('limite', 500))
    # orden: 'reciente' = por id DESC (más nuevos primero), cualquier otro = alfabético
    orden = request.args.get('orden', 'alfa')
    order_sql = 'id DESC' if orden == 'reciente' else 'nombre_comuna'

    if q:
        filas = db.execute(
            f'SELECT id,nombre_comuna,fecha_insercion FROM COMUNAS_NORM WHERE nombre_comuna LIKE ? ORDER BY {order_sql} LIMIT ?',
            (f'%{q}%', limite)
        ).fetchall()
    else:
        filas = db.execute(
            f'SELECT id,nombre_comuna,fecha_insercion FROM COMUNAS_NORM ORDER BY {order_sql} LIMIT ?',
            (limite,)
        ).fetchall()
    return jsonify([dict(f) for f in filas])


@app.route('/api/logs')
def api_logs():
    """Lista entradas de PROCESO_LOG con filtros opcionales."""
    db     = get_db()
    sesion = request.args.get('sesion', '').strip()
    estado = request.args.get('estado', '').strip()
    q      = request.args.get('q', '').strip()
    limite = int(request.args.get('limite', 500))

    sql    = 'SELECT * FROM PROCESO_LOG WHERE 1=1'
    params = []
    if sesion: sql += ' AND sesion_id=?';                              params.append(sesion)
    if estado: sql += ' AND estado=?';                                 params.append(estado)
    if q:      sql += ' AND (valor_orig LIKE ? OR valor_norm LIKE ?)'; params += [f'%{q}%', f'%{q}%']
    sql += ' ORDER BY id DESC LIMIT ?'; params.append(limite)

    return jsonify([dict(f) for f in db.execute(sql, params).fetchall()])


@app.route('/api/sesiones')
def api_sesiones():
    """Lista todas las sesiones disponibles en PROCESO_LOG."""
    db    = get_db()
    filas = db.execute(
        'SELECT sesion_id, COUNT(*) as total, fecha FROM PROCESO_LOG GROUP BY sesion_id ORDER BY fecha DESC'
    ).fetchall()
    return jsonify([dict(f) for f in filas])


@app.route('/api/stats')
def api_stats():
    """Estadisticas generales de la base de datos."""
    db = get_db()
    return jsonify({
        'total_comunas':  db.execute('SELECT COUNT(*) FROM COMUNAS_NORM').fetchone()[0],
        'total_log':      db.execute('SELECT COUNT(*) FROM PROCESO_LOG').fetchone()[0],
        'total_sesiones': db.execute('SELECT COUNT(DISTINCT sesion_id) FROM PROCESO_LOG').fetchone()[0],
        'duplicados':     db.execute("SELECT COUNT(*) FROM PROCESO_LOG WHERE estado='DUPLICADO'").fetchone()[0],
        'modificados':    db.execute("SELECT COUNT(*) FROM PROCESO_LOG WHERE estado='MODIFICADO'").fetchone()[0],
        'ultima_sesion':  db.execute('SELECT MAX(fecha) FROM PROCESO_LOG').fetchone()[0] or 'Sin datos',
        'archivo_db':     DATABASE,
    })


@app.route('/api/descargar/<sesion_id>/<tipo>')
def api_descargar(sesion_id, tipo):
    """Genera y descarga CSV, log .txt o script SQL."""
    db = get_db()

    # send_file requiere BytesIO (modo binario) en Python 3 + Flask moderno.
    # Construimos el contenido como string y lo codificamos a bytes UTF-8
    # con BOM (\xef\xbb\xbf) para que Excel abra el CSV con tildes correctamente.

    if tipo == 'csv':
        filas = db.execute('SELECT id,nombre_comuna FROM COMUNAS_NORM ORDER BY id').fetchall()
        texto = 'id,nombre_comuna\n'
        for f in filas:
            texto += f'{f["id"]},"{f["nombre_comuna"]}"\n'
        # UTF-8 con BOM para compatibilidad con Excel en Windows
        return send_file(BytesIO(texto.encode('utf-8-sig')),
                         mimetype='text/csv; charset=utf-8',
                         as_attachment=True, download_name='COMUNAS_NORM.csv')

    elif tipo == 'log':
        filas = db.execute(
            'SELECT linea_num,valor_orig,valor_norm,estado FROM PROCESO_LOG WHERE sesion_id=? ORDER BY linea_num',
            (sesion_id,)
        ).fetchall()
        texto  = f'=== LOG — Sesion {sesion_id} ===\n\n'
        texto += f'{"#".ljust(6)} {"ESTADO".ljust(12)} {"ORIGINAL".ljust(35)} NORMALIZADO\n'
        texto += '-' * 80 + '\n'
        for f in filas:
            texto += f'{str(f["linea_num"]).rjust(5)}  {f["estado"].ljust(12)} {f["valor_orig"].ljust(35)} -> {f["valor_norm"]}\n'
        return send_file(BytesIO(texto.encode('utf-8')),
                         mimetype='text/plain; charset=utf-8',
                         as_attachment=True, download_name=f'log_{sesion_id}.txt')

    elif tipo == 'sql':
        filas = db.execute('SELECT nombre_comuna FROM COMUNAS_NORM ORDER BY id').fetchall()
        texto  = f'-- COMUNAS_NORM | {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} | {len(filas)} registros\n\n'
        texto += 'CREATE TABLE IF NOT EXISTS COMUNAS_NORM (\n    id INT AUTO_INCREMENT PRIMARY KEY,\n    nombre_comuna VARCHAR(100) NOT NULL UNIQUE\n);\n\n'
        texto += 'INSERT INTO COMUNAS_NORM (nombre_comuna) VALUES\n'
        vals   = [f"    ('{f['nombre_comuna'].replace(chr(39), chr(39)*2)}')" for f in filas]
        texto += ',\n'.join(vals) + ';\n'
        return send_file(BytesIO(texto.encode('utf-8')),
                         mimetype='text/plain; charset=utf-8',
                         as_attachment=True, download_name='insert_comunas_norm.sql')

    return jsonify({'error': 'Tipo invalido'}), 400


@app.route('/api/limpiar', methods=['POST'])
def api_limpiar():
    """
    Vacia ambas tablas y reinicia los contadores de ID desde 1.

    DELETE elimina los datos, pero SQLite guarda el ultimo ID en la tabla
    interna sqlite_sequence. Sin resetearla, los IDs siguen creciendo aunque
    la tabla este vacia (ej: tras limpiar, el siguiente ID seria 1700 en vez
    de 1). Borrando la fila de sqlite_sequence, el conteo vuelve a empezar.
    """
    db = get_db()
    # Vaciar datos de ambas tablas
    db.execute('DELETE FROM COMUNAS_NORM')
    db.execute('DELETE FROM PROCESO_LOG')
    # Resetear contadores AUTOINCREMENT para que los IDs vuelvan a 1
    db.execute("DELETE FROM sqlite_sequence WHERE name='COMUNAS_NORM'")
    db.execute("DELETE FROM sqlite_sequence WHERE name='PROCESO_LOG'")
    db.commit()
    return jsonify({'mensaje': 'Base de datos limpiada y contadores de ID reiniciados (vuelven desde 1)'})


# ===========================================================================
# RUTAS — API JSON (módulo FAMOSOS)
# ===========================================================================

@app.route('/api/famosos/procesar', methods=['POST'])
def api_famosos_procesar():
    """
    Recibe un archivo .txt con datos de famosos, ejecuta el ETL completo
    y guarda los resultados en la base de datos.

    El archivo debe tener el formato: "N. Nombre - fecha" por línea.
    """
    if 'archivo' not in request.files:
        return jsonify({'error': 'No se recibió archivo'}), 400

    archivo = request.files['archivo']
    contenido = archivo.read().decode('utf-8', errors='replace')
    lineas = [l for l in contenido.splitlines() if l.strip()]

    if not lineas:
        return jsonify({'error': 'El archivo está vacío'}), 400

    sesion_id = datetime.now().strftime('%Y%m%d_%H%M%S')
    db = get_db()

    # Ejecutar el proceso ETL
    etl = procesar_famosos(lineas, sesion_id, db)

    # Guardar en la base de datos
    guardar_famosos_en_db(etl['registros'], etl['log_entries'], sesion_id)

    # Preparar preview con IDs asignados
    db = get_db()
    preview = db.execute('''
        SELECT id, nombre, fecha_original, fecha_normalizada,
               es_historica, edad, es_cumpleanos
        FROM FAMOSOS ORDER BY id DESC LIMIT ?
    ''', (len(etl['registros']),)).fetchall()
    preview = [dict(r) for r in reversed(preview)]

    return jsonify({
        'sesion_id': sesion_id,
        'stats': etl['stats'],
        'preview': preview,
        'log': '\n'.join(etl['log_lines'])
    })


@app.route('/api/famosos/lista')
def api_famosos_lista():
    """
    Retorna la lista de famosos almacenados en la BD con filtros opcionales.

    Parámetros de query string:
      - q: búsqueda por nombre (LIKE)
      - cumpleanos: si es "1", solo retorna los que cumplen años hoy
      - historica: si es "0", oculta las fechas históricas
      - limite: número máximo de registros (default 500)
    """
    db = get_db()
    q = request.args.get('q', '').strip()
    cumpleanos = request.args.get('cumpleanos', '').strip()
    historica = request.args.get('historica', '').strip()
    limite = int(request.args.get('limite', 500))

    sql = 'SELECT * FROM FAMOSOS WHERE 1=1'
    params = []

    if q:
        sql += ' AND nombre LIKE ?'
        params.append(f'%{q}%')
    if cumpleanos == '1':
        sql += ' AND es_cumpleanos = 1'
    if historica == '0':
        sql += ' AND es_historica = 0'

    sql += ' ORDER BY id LIMIT ?'
    params.append(limite)

    filas = db.execute(sql, params).fetchall()
    return jsonify([dict(f) for f in filas])


@app.route('/api/famosos/stats')
def api_famosos_stats():
    """
    Retorna estadísticas del módulo FAMOSOS.
    Incluye: total de famosos, cumpleaños hoy, históricos y duplicados eliminados.
    """
    db = get_db()
    total = db.execute('SELECT COUNT(*) FROM FAMOSOS').fetchone()[0]
    cumpleanos = db.execute('SELECT COUNT(*) FROM FAMOSOS WHERE es_cumpleanos = 1').fetchone()[0]
    historicas = db.execute('SELECT COUNT(*) FROM FAMOSOS WHERE es_historica = 1').fetchone()[0]
    duplicados = db.execute("SELECT COUNT(*) FROM FAMOSOS_LOG WHERE estado = 'DUPLICADO'").fetchone()[0]

    return jsonify({
        'total': total,
        'cumpleanos_hoy': cumpleanos,
        'historicas': historicas,
        'duplicados_eliminados': duplicados,
    })


@app.route('/api/famosos/descargar/<sesion_id>/<tipo>')
def api_famosos_descargar(sesion_id, tipo):
    """
    Genera archivos de descarga para el módulo FAMOSOS.

    Tipos soportados:
      - csv: Exporta todos los famosos en formato CSV con BOM para Excel
      - sql: Genera script SQL con INSERTs para recrear los datos
      - log: Exporta el log de procesamiento de la sesión indicada
    """
    db = get_db()

    if tipo == 'csv':
        filas = db.execute('''
            SELECT id, nombre, fecha_original, fecha_normalizada,
                   es_historica, edad, es_cumpleanos
            FROM FAMOSOS ORDER BY id
        ''').fetchall()
        texto = 'id,nombre,fecha_original,fecha_normalizada,es_historica,edad,es_cumpleanos\n'
        for f in filas:
            nombre = f['nombre'].replace('"', '""')
            f_orig = (f['fecha_original'] or '').replace('"', '""')
            f_norm = f['fecha_normalizada'] or ''
            edad = f['edad'] if f['edad'] is not None else ''
            texto += f'{f["id"]},"{nombre}","{f_orig}","{f_norm}",{f["es_historica"]},{edad},{f["es_cumpleanos"]}\n'
        return send_file(BytesIO(texto.encode('utf-8-sig')),
                         mimetype='text/csv; charset=utf-8',
                         as_attachment=True, download_name='FAMOSOS.csv')

    elif tipo == 'sql':
        filas = db.execute('''
            SELECT nombre, fecha_original, fecha_normalizada,
                   es_historica, edad, es_cumpleanos
            FROM FAMOSOS ORDER BY id
        ''').fetchall()
        texto = f'-- FAMOSOS | {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} | {len(filas)} registros\n\n'
        texto += '''CREATE TABLE IF NOT EXISTS FAMOSOS (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre            TEXT NOT NULL,
    fecha_original    TEXT,
    fecha_normalizada TEXT,
    es_historica      INTEGER DEFAULT 0,
    edad              INTEGER,
    es_cumpleanos     INTEGER DEFAULT 0,
    fecha_insercion   TEXT NOT NULL
);\n\n'''
        for f in filas:
            nombre_esc = f['nombre'].replace("'", "''")
            f_orig = (f['fecha_original'] or '').replace("'", "''")
            f_norm = f['fecha_normalizada'] or 'NULL'
            f_norm_sql = f"'{f_norm}'" if f_norm != 'NULL' else 'NULL'
            edad = f['edad'] if f['edad'] is not None else 'NULL'
            texto += f"INSERT INTO FAMOSOS (nombre, fecha_original, fecha_normalizada, es_historica, edad, es_cumpleanos, fecha_insercion) VALUES ('{nombre_esc}', '{f_orig}', {f_norm_sql}, {f['es_historica']}, {edad}, {f['es_cumpleanos']}, datetime('now'));\n"
        return send_file(BytesIO(texto.encode('utf-8')),
                         mimetype='text/plain; charset=utf-8',
                         as_attachment=True, download_name='insert_famosos.sql')

    elif tipo == 'log':
        filas = db.execute('''
            SELECT linea_num, nombre_orig, fecha_orig, fecha_norm, estado
            FROM FAMOSOS_LOG WHERE sesion_id = ? ORDER BY linea_num
        ''', (sesion_id,)).fetchall()
        texto = f'=== LOG FAMOSOS — Sesión {sesion_id} ===\n\n'
        texto += f'{"#".ljust(6)} {"ESTADO".ljust(14)} {"NOMBRE".ljust(30)} {"FECHA ORIG".ljust(25)} FECHA NORM\n'
        texto += '-' * 95 + '\n'
        for f in filas:
            texto += f'{str(f["linea_num"]).rjust(5)}  {(f["estado"] or "").ljust(14)} {(f["nombre_orig"] or "").ljust(30)} {(f["fecha_orig"] or "").ljust(25)} {f["fecha_norm"] or "N/A"}\n'
        return send_file(BytesIO(texto.encode('utf-8')),
                         mimetype='text/plain; charset=utf-8',
                         as_attachment=True, download_name=f'log_famosos_{sesion_id}.txt')

    return jsonify({'error': 'Tipo inválido'}), 400


@app.route('/api/famosos/limpiar', methods=['POST'])
def api_famosos_limpiar():
    """
    Vacía las tablas FAMOSOS y FAMOSOS_LOG y resetea los contadores de ID.
    Mismo patrón que api_limpiar() del módulo COMUNAS_NORM.
    """
    db = get_db()
    db.execute('DELETE FROM FAMOSOS')
    db.execute('DELETE FROM FAMOSOS_LOG')
    db.execute("DELETE FROM sqlite_sequence WHERE name='FAMOSOS'")
    db.execute("DELETE FROM sqlite_sequence WHERE name='FAMOSOS_LOG'")
    db.commit()
    return jsonify({'mensaje': 'Tablas FAMOSOS y FAMOSOS_LOG limpiadas. IDs reiniciados.'})


# ===========================================================================
# RUTAS — API JSON (módulo LUGARES)
# ===========================================================================

@app.route('/api/lugares/procesar', methods=['POST'])
def api_lugares_procesar():
    """
    Recibe un archivo CSV (separado por ;) con datos de lugares,
    ejecuta el ETL completo y guarda en las 3 tablas normalizadas.

    Intenta leer el archivo con encoding latin-1 primero (para caracteres
    como ß, ñ), y si falla usa utf-8 con errors='replace'.
    """
    if 'archivo' not in request.files:
        return jsonify({'error': 'No se recibió archivo'}), 400

    archivo = request.files['archivo']
    raw_bytes = archivo.read()

    # Intentar decodificar con diferentes encodings
    # El archivo puede tener encoding roto (Latin-1/CP1252)
    try:
        contenido = raw_bytes.decode('utf-8')
    except UnicodeDecodeError:
        try:
            contenido = raw_bytes.decode('latin-1')
        except UnicodeDecodeError:
            contenido = raw_bytes.decode('utf-8', errors='replace')

    lineas = [l for l in contenido.splitlines() if l.strip()]

    if not lineas:
        return jsonify({'error': 'El archivo está vacío'}), 400

    sesion_id = datetime.now().strftime('%Y%m%d_%H%M%S')
    db = get_db()

    # Ejecutar el proceso ETL
    etl = procesar_lugares(lineas, sesion_id, db)

    # Guardar en la base de datos
    guardar_lugares_en_db(etl['lugares'], etl['log_entries'], sesion_id)

    # Preparar previews de las 3 tablas
    db = get_db()

    # Preview de LUGARES con datos JOINed
    preview_lugares = db.execute('''
        SELECT L.id, L.nombre_lugar, L.fecha_insercion,
               G.latitud, G.longitud,
               D.pais
        FROM LUGARES L
        LEFT JOIN GEOREFERENCIAS G ON G.id_lugar = L.id
        LEFT JOIN DIRECCIONES D ON D.id_lugar = L.id
        ORDER BY L.id
    ''').fetchall()

    preview_georef = db.execute('''
        SELECT G.id, G.id_lugar, L.nombre_lugar, G.latitud, G.longitud
        FROM GEOREFERENCIAS G
        JOIN LUGARES L ON L.id = G.id_lugar
        ORDER BY G.id
    ''').fetchall()

    preview_direcciones = db.execute('''
        SELECT D.id, D.id_lugar, L.nombre_lugar, D.direccion_completa,
               D.nombre_calle, D.numero_calle, D.ciudad_estado_provincia, D.pais
        FROM DIRECCIONES D
        JOIN LUGARES L ON L.id = D.id_lugar
        ORDER BY D.id
    ''').fetchall()

    return jsonify({
        'sesion_id': sesion_id,
        'stats': etl['stats'],
        'preview_lugares': [dict(r) for r in preview_lugares],
        'preview_georef': [dict(r) for r in preview_georef],
        'preview_direcciones': [dict(r) for r in preview_direcciones],
        'log': '\n'.join(etl['log_lines']),
        'paises': etl['paises'],
    })


@app.route('/api/lugares/lista')
def api_lugares_lista():
    """
    Retorna la lista de lugares con sus georeferencias y direcciones (JOIN).

    Parámetros de query string:
      - q: búsqueda por nombre de lugar (LIKE)
      - pais: filtro por país exacto
      - limite: número máximo de registros (default 500)
    """
    db = get_db()
    q = request.args.get('q', '').strip()
    pais = request.args.get('pais', '').strip()
    limite = int(request.args.get('limite', 500))

    sql = '''
        SELECT L.id, L.nombre_lugar, L.fecha_insercion,
               G.latitud, G.longitud,
               D.direccion_completa, D.nombre_calle, D.numero_calle,
               D.ciudad_estado_provincia, D.pais
        FROM LUGARES L
        LEFT JOIN GEOREFERENCIAS G ON G.id_lugar = L.id
        LEFT JOIN DIRECCIONES D ON D.id_lugar = L.id
        WHERE 1=1
    '''
    params = []

    if q:
        sql += ' AND L.nombre_lugar LIKE ?'
        params.append(f'%{q}%')
    if pais:
        sql += ' AND D.pais = ?'
        params.append(pais)

    sql += ' ORDER BY L.id LIMIT ?'
    params.append(limite)

    filas = db.execute(sql, params).fetchall()
    return jsonify([dict(f) for f in filas])


@app.route('/api/lugares/stats')
def api_lugares_stats():
    """
    Retorna estadísticas del módulo LUGARES.
    Incluye totales de cada tabla, duplicados eliminados y países únicos.
    """
    db = get_db()
    total_lugares = db.execute('SELECT COUNT(*) FROM LUGARES').fetchone()[0]
    total_georef = db.execute('SELECT COUNT(*) FROM GEOREFERENCIAS').fetchone()[0]
    total_dir = db.execute('SELECT COUNT(*) FROM DIRECCIONES').fetchone()[0]
    duplicados = db.execute(
        "SELECT COUNT(*) FROM LUGARES_LOG WHERE estado IN ('DUPLICADO_EXACTO', 'DUPLICADO_PARCIAL')"
    ).fetchone()[0]
    paises = db.execute('SELECT COUNT(DISTINCT pais) FROM DIRECCIONES WHERE pais IS NOT NULL').fetchone()[0]

    return jsonify({
        'total_lugares': total_lugares,
        'total_georef': total_georef,
        'total_direcciones': total_dir,
        'duplicados_eliminados': duplicados,
        'paises_unicos': paises,
    })


@app.route('/api/lugares/descargar/<sesion_id>/<tipo>')
def api_lugares_descargar(sesion_id, tipo):
    """
    Genera archivos de descarga para el módulo LUGARES.

    Tipos soportados:
      - csv: Exporta lugares + georef + direcciones en un solo CSV
      - sql: Genera script SQL con INSERTs para las 3 tablas
      - log: Exporta el log de procesamiento de la sesión
    """
    db = get_db()

    if tipo == 'csv':
        filas = db.execute('''
            SELECT L.id, L.nombre_lugar,
                   G.latitud, G.longitud,
                   D.direccion_completa, D.nombre_calle, D.numero_calle,
                   D.ciudad_estado_provincia, D.pais,
                   L.fecha_insercion
            FROM LUGARES L
            LEFT JOIN GEOREFERENCIAS G ON G.id_lugar = L.id
            LEFT JOIN DIRECCIONES D ON D.id_lugar = L.id
            ORDER BY L.id
        ''').fetchall()
        texto = 'id,nombre_lugar,latitud,longitud,direccion_completa,nombre_calle,numero_calle,ciudad_estado_provincia,pais,fecha_insercion\n'
        for f in filas:
            nombre = (f['nombre_lugar'] or '').replace('"', '""')
            dir_comp = (f['direccion_completa'] or '').replace('"', '""')
            calle = (f['nombre_calle'] or '').replace('"', '""')
            num = f['numero_calle'] or ''
            ciudad = (f['ciudad_estado_provincia'] or '').replace('"', '""')
            pais_val = (f['pais'] or '').replace('"', '""')
            texto += f'{f["id"]},"{nombre}",{f["latitud"]},{f["longitud"]},"{dir_comp}","{calle}","{num}","{ciudad}","{pais_val}","{f["fecha_insercion"]}"\n'
        return send_file(BytesIO(texto.encode('utf-8-sig')),
                         mimetype='text/csv; charset=utf-8',
                         as_attachment=True, download_name='LUGARES.csv')

    elif tipo == 'sql':
        # Generar INSERTs para las 3 tablas
        lugares = db.execute('SELECT * FROM LUGARES ORDER BY id').fetchall()
        georef = db.execute('SELECT * FROM GEOREFERENCIAS ORDER BY id').fetchall()
        dirs = db.execute('SELECT * FROM DIRECCIONES ORDER BY id').fetchall()

        texto = f'-- LUGARES ETL | {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n'
        texto += f'-- {len(lugares)} lugares, {len(georef)} georeferencias, {len(dirs)} direcciones\n\n'

        # Esquema
        texto += '''CREATE TABLE IF NOT EXISTS LUGARES (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre_lugar    TEXT NOT NULL,
    fecha_insercion TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS GEOREFERENCIAS (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    id_lugar    INTEGER NOT NULL REFERENCES LUGARES(id),
    latitud     REAL NOT NULL,
    longitud    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS DIRECCIONES (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    id_lugar                INTEGER NOT NULL REFERENCES LUGARES(id),
    direccion_completa      TEXT,
    nombre_calle            TEXT,
    numero_calle            TEXT,
    ciudad_estado_provincia TEXT,
    pais                    TEXT
);\n\n'''

        # INSERTs para LUGARES
        for l in lugares:
            nombre_esc = l['nombre_lugar'].replace("'", "''")
            texto += f"INSERT INTO LUGARES (nombre_lugar, fecha_insercion) VALUES ('{nombre_esc}', '{l['fecha_insercion']}');\n"

        texto += '\n'

        # INSERTs para GEOREFERENCIAS
        for g in georef:
            texto += f"INSERT INTO GEOREFERENCIAS (id_lugar, latitud, longitud) VALUES ({g['id_lugar']}, {g['latitud']}, {g['longitud']});\n"

        texto += '\n'

        # INSERTs para DIRECCIONES
        for d in dirs:
            dir_esc = (d['direccion_completa'] or '').replace("'", "''")
            calle_esc = (d['nombre_calle'] or 'NULL')
            if calle_esc != 'NULL': calle_esc = f"'{calle_esc.replace(chr(39), chr(39)*2)}'"
            num_esc = (d['numero_calle'] or 'NULL')
            if num_esc != 'NULL': num_esc = f"'{num_esc}'"
            ciudad_esc = (d['ciudad_estado_provincia'] or 'NULL')
            if ciudad_esc != 'NULL': ciudad_esc = f"'{ciudad_esc.replace(chr(39), chr(39)*2)}'"
            pais_esc = (d['pais'] or 'NULL')
            if pais_esc != 'NULL': pais_esc = f"'{pais_esc.replace(chr(39), chr(39)*2)}'"
            texto += f"INSERT INTO DIRECCIONES (id_lugar, direccion_completa, nombre_calle, numero_calle, ciudad_estado_provincia, pais) VALUES ({d['id_lugar']}, '{dir_esc}', {calle_esc}, {num_esc}, {ciudad_esc}, {pais_esc});\n"

        return send_file(BytesIO(texto.encode('utf-8')),
                         mimetype='text/plain; charset=utf-8',
                         as_attachment=True, download_name='insert_lugares.sql')

    elif tipo == 'log':
        filas = db.execute('''
            SELECT linea_num, nombre_orig, estado, detalle
            FROM LUGARES_LOG WHERE sesion_id = ? ORDER BY linea_num
        ''', (sesion_id,)).fetchall()
        texto = f'=== LOG LUGARES — Sesión {sesion_id} ===\n\n'
        texto += f'{"#".ljust(6)} {"ESTADO".ljust(18)} {"NOMBRE".ljust(30)} DETALLE\n'
        texto += '-' * 90 + '\n'
        for f in filas:
            texto += f'{str(f["linea_num"]).rjust(5)}  {(f["estado"] or "").ljust(18)} {(f["nombre_orig"] or "").ljust(30)} {f["detalle"] or ""}\n'
        return send_file(BytesIO(texto.encode('utf-8')),
                         mimetype='text/plain; charset=utf-8',
                         as_attachment=True, download_name=f'log_lugares_{sesion_id}.txt')

    return jsonify({'error': 'Tipo inválido'}), 400


@app.route('/api/lugares/limpiar', methods=['POST'])
def api_lugares_limpiar():
    """
    Vacía las tablas LUGARES, GEOREFERENCIAS, DIRECCIONES y LUGARES_LOG.
    Resetea los contadores AUTOINCREMENT de cada tabla.
    """
    db = get_db()
    db.execute('DELETE FROM DIRECCIONES')
    db.execute('DELETE FROM GEOREFERENCIAS')
    db.execute('DELETE FROM LUGARES')
    db.execute('DELETE FROM LUGARES_LOG')
    db.execute("DELETE FROM sqlite_sequence WHERE name='LUGARES'")
    db.execute("DELETE FROM sqlite_sequence WHERE name='GEOREFERENCIAS'")
    db.execute("DELETE FROM sqlite_sequence WHERE name='DIRECCIONES'")
    db.execute("DELETE FROM sqlite_sequence WHERE name='LUGARES_LOG'")
    db.commit()
    return jsonify({'mensaje': 'Tablas LUGARES, GEOREFERENCIAS, DIRECCIONES y LUGARES_LOG limpiadas. IDs reiniciados.'})


# ===========================================================================
# INICIO LOCAL
# ===========================================================================

if __name__ == '__main__':
    # Abrir el navegador automaticamente despues de 1 segundo
    Timer(1.0, lambda: webbrowser.open(f'http://localhost:{PORT}')).start()

    print(f"\n{'='*50}")
    print(f"  COMUNAS_NORM — Normalizador ETL + Famosos + Lugares")
    print(f"{'='*50}")
    print(f"  Inicio    :  http://localhost:{PORT}")
    print(f"  Base datos:  http://localhost:{PORT}/base-datos")
    print(f"  Famosos   :  http://localhost:{PORT}/famosos")
    print(f"  Lugares   :  http://localhost:{PORT}/lugares")
    print(f"  Archivo BD:  {DATABASE}")
    print(f"  Ctrl+C para detener")
    print(f"{'='*50}\n")

    app.run(host='localhost', port=PORT, debug=True, use_reloader=False)
