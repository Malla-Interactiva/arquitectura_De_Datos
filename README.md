# Aplicación ETL — COMUNAS_NORM + FAMOSOS + LUGARES
**Arquitectura y Almacenamiento de Datos — Evaluación 2 · 2026**
Integrantes: Jostin Sáez · Gerardo Millán · Eduardo Díaz

---

## ¿Qué hace esta aplicación?

Esta aplicación web construida con Flask y SQLite está diseñada para normalizar, procesar y almacenar datos provenientes de archivos de texto o CSV con formatos irregulares. Cuenta con **3 módulos principales**:

### 1. Módulo COMUNAS_NORM
Normaliza nombres de comunas de un archivo `.txt`, convirtiéndolos a Título, Mayúsculas o Minúsculas, removiendo tildes, reemplazando la "Ñ" y eliminando duplicados.

### 2. Módulo FAMOSOS
Procesa un archivo `.txt` de personajes famosos y sus fechas de nacimiento.
- Limpia los números de ítem al inicio (ej: `1. `).
- Parsea fechas en múltiples formatos (`YYYY/MM/DD`, `DD-MM-YYYY`, etc) y las normaliza a `DD-MM-YYYY`.
- Detecta **fechas históricas** (a.C., "alrededor de").
- Calcula la **edad actual** (si aplica) y determina de manera dinámica si **hoy es su cumpleaños**.
- Elimina datos duplicados verificando la base de datos.

### 3. Módulo LUGARES
Procesa un archivo `.csv` (separado por `;`) con lugares y coordenadas que presenta problemas estructurales.
- Recupera caracteres con **problemas de encoding**.
- Extrae la latitud y longitud a partir de coordenadas irregulares en un solo campo de texto.
- Parsea direcciones libres extrayendo inteligentemente: `Calle`, `Número`, `Ciudad/Estado` y `País`.
- Divide la información relacional en tres tablas (Lugar → Georeferencia y Dirección).
- Elimina registros duplicados exactos y parciales (misma coordenada, distinta dirección).

---

## Estructura del proyecto

```text
comunas-norm/
├── app.py               # Backend Flask con toda la lógica ETL y API
├── templates/
│   ├── index.html       # Módulo 1 (Comunas)
│   ├── bd.html          # Explorador global de la base de datos
│   ├── famosos.html     # Módulo 2 (Famosos)
│   └── lugares.html     # Módulo 3 (Lugares)
├── requirements.txt     # Dependencias Python (Flask, Gunicorn)
├── Procfile             # Comando de inicio para Railway
├── railway.json         # Configuración de despliegue Railway
└── README.md            # Este archivo
```

---

## Ejecutar localmente

```bash
# 1. Clonar o descargar el proyecto
cd comunas-norm

# 2. Crear entorno virtual (recomendado)
python -m venv venv
source venv/bin/activate        # Linux/Mac
venv\Scripts\activate           # Windows

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Iniciar el servidor
python app.py

# 5. Abrir en el navegador (se abrirá automáticamente)
# http://localhost:5000
```

---

## Despliegue en Railway (Producción)

### Paso 1: Subir a GitHub
Sube este código a un repositorio de GitHub (puedes omitir las carpetas `venv/`, `__pycache__/` y la base de datos `comunas_norm.db`).

### Paso 2: Conectar con Railway
1. Ve a [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub Repo**.
2. Selecciona tu repositorio. Railway detectará la app gracias al archivo `Procfile` y comenzará el despliegue automático.

### Paso 3: Configurar Persistencia de Datos (¡Obligatorio!)
Railway usa un sistema de archivos efímero por defecto. Si no realizas este paso, la base de datos SQLite se borrará cada vez que reinicies o actualices el servidor.
1. Haz clic sobre tu servicio en Railway, ve a la pestaña **Settings** y busca la sección **Volumes**.
2. Haz clic en **Add Volume** y asígnale el *Mount Path*: `/data`
3. Ve a la pestaña **Variables** y crea la siguiente variable de entorno:
   - `DATABASE_PATH` = `/data/comunas_norm.db`
4. El servidor se reiniciará automáticamente y tu base de datos ahora será persistente en la nube.

---

## Endpoints Principales de la API

La aplicación opera a través de 3 juegos de APIs (todas devuelven JSON):

| Módulo | Endpoint de Procesamiento | Endpoint de Lista | Endpoint de Stats |
|---|---|---|---|
| **Comunas** | `POST /api/normalizar` | `GET /api/comunas` | `GET /api/stats` |
| **Famosos** | `POST /api/famosos/procesar` | `GET /api/famosos/lista` | `GET /api/famosos/stats` |
| **Lugares** | `POST /api/lugares/procesar` | `GET /api/lugares/lista` | `GET /api/lugares/stats` |

*También cuenta con endpoints `/api/../descargar/<sesion>/csv` y similares para descargar los datos procesados.*

---

## Diseño de Base de Datos (Esquema SQLite)

La aplicación genera 6 tablas y 2 dependencias relacionadas de forma automática:
- **COMUNAS_NORM** y **PROCESO_LOG**
- **FAMOSOS** y **FAMOSOS_LOG**
- **LUGARES**, **GEOREFERENCIAS**, **DIRECCIONES** y **LUGARES_LOG**

*Para ver el esquema SQL completo con sus columnas, puedes generar un volcado descargando el SQL desde la interfaz de la aplicación web.*

