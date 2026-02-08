# Movie Renamer — alternativa a FileBot  
*(English version below)*

> Script en Python para **renombrar y organizar** películas automáticamente empleando metadatos de **TMDB** y análisis técnico con **MediaInfo**. Diseñado como reemplazo potente y flexible a FileBot, **optimizando la estructura de carpetas para servidores Jellyfin**.

> ⚠️ **Estado**: funcional y probado bajo **Jellyfin**.  
> Desde la versión **2.0** la estructura de salida es **parametrizable por plantilla** en `config.ini` con presets listos para Jellyfin/Plex/Emby.  
> La plantilla por defecto mantiene exactamente el esquema histórico compatible con **bibliotecas Jellyfin**.

---

## Índice

- [Movie Renamer — alternativa a FileBot](#movie-renamer--alternativa-a-filebot)
  - [Índice](#índice)
  - [Características](#características)
  - [Compatibilidad con Jellyfin](#compatibilidad-con-jellyfin)
  - [Requisitos](#requisitos)
  - [Instalación](#instalación)
  - [Configuración (TMDB)](#configuración-tmdb)
  - [Uso](#uso)
  - [Esquema de salida](#esquema-de-salida)
  - [Plantillas y tags](#plantillas-y-tags)
  - [Estructura del proyecto](#estructura-del-proyecto)
  - [Tests](#tests)
  - [Limitaciones conocidas](#limitaciones-conocidas)
  - [Roadmap](#roadmap)
  - [Solución de problemas](#solución-de-problemas)
  - [Créditos](#créditos)
- [Movie Renamer — FileBot alternative](#movie-renamer--filebot-alternative)
  - [Table of Contents](#table-of-contents)
  - [Features](#features)
  - [Jellyfin compatibility](#jellyfin-compatibility)
  - [Requirements](#requirements)
  - [Installation](#installation)
  - [Configuration (TMDB)](#configuration-tmdb)
  - [Usage](#usage)
  - [Output scheme](#output-scheme)
  - [Templates and tags](#templates-and-tags)
  - [Project layout](#project-layout)
  - [Tests](#tests-1)
  - [Known limitations](#known-limitations)
  - [Roadmap](#roadmap-1)
  - [Troubleshooting](#troubleshooting)
  - [Credits](#credits)
---

## Características

- Identificación en **TMDB** por título+año y, si el archivo contiene un ID de IMDb (`ttXXXXXXX`), búsqueda directa vía **TMDB Find API**.
- Título final en el idioma pedido usando `translations` y `alternative_titles` (cuando existan) evitando mezclar países.
- Carpetas de **colecciones** con nombre traducido (cuando exista) consultando **TMDB Collection Translations API**.
- Normalización de nombres de colección: elimina sufijos ya incluidos por TMDB (`Collection`, `la colección`, `(Collection)`, etc.) y reaplica el sufijo estándar según idioma.
- Extracción técnica con **pymediainfo**: resolución con tolerancia (1792×1080 ⇒ 1080p), códec vídeo/audio, señal HDR (Dolby Vision/HDR10/HLG cuando aplica), bitrate, etc.
- Detección de **fuente** a partir del nombre y/o heurística por altura/bitrate: `WEB-DL`, `WEBRip`, `BDRip`, `BDRemux`, `UHD BDRemux`, `UHDRip`, `MicroHD`.
- Modos: `test` (simulación), `move` (mover), `copy` (copiar) con copias/moves atómicos (tmp `.renamer-tmp-*`).
- Compatible con la **estructura esperada por Jellyfin**, asegurando detección automática de metadatos, pósters y colecciones sin intervención manual.
- Plantilla de destino configurable desde `config.ini` (`[TEMPLATES].destination_template`) con placeholders validados.
- `--lang` soporta idioma y país (`es`, `es-ES`, `pt`, `pt-PT`, `pt-BR`, etc.). Si no se indica país, se intenta elegir uno por defecto con Babel.
- **Logging** en consola (con color) y logs rotados: `renamer.log` (acciones) y `renamer.detail.log` (diagnóstico).
- Solape `--src`/`--dest`: permite re-ejecutar sobre la librería; evita bucles guardando primero la lista de ficheros a procesar cuando aplica.
- Extensiones soportadas: `.mkv`, `.mp4`, `.avi`.


---

## Compatibilidad con Jellyfin

Este script fue diseñado y **probado específicamente con Jellyfin**, asegurando que los archivos resultantes se integran sin problemas con su motor de detección de metadatos.

✔️ Estructura reconocida automáticamente por Jellyfin:  
- Colecciones agrupadas bajo carpetas con sufijo `- Colección`.  
- Carpetas por título con formato `{Título} ({Año}) [ttXXXXXXX]`.  
- Archivos con información técnica en el nombre (resolución, HDR, códec, fuente, etc.).

**Ejemplo:**
```
/movies/H/Harry Potter - Colección/Harry Potter and the Sorcerer's Stone (2001) [tt0241527]/Harry Potter and the Sorcerer's Stone (2001) [tt0241527] - [2160p (UHD BDRemux), HDR, x265, TrueHD].mkv
```

> 🧩 **Objetivo principal:** mantener una organización limpia, estándar y totalmente compatible con **Jellyfin**, sin necesidad de scrapers adicionales ni ajustes manuales.

---

## Requisitos

- **Python 3.8+**
- Paquetes Python (ver `requirements.txt`):
  - `requests`
  - `colorama`
  - `pymediainfo`
  - `Babel`  ← (para inferir la región por defecto: `es` ⇒ `es-ES`, `pt` ⇒ `pt-BR`, etc.)
- **MediaInfo** instalado en el sistema (necesario para `pymediainfo`):
  - **Debian/Ubuntu**: `sudo apt-get install mediainfo`
  - **Fedora**: `sudo dnf install mediainfo`
  - **Arch**: `sudo pacman -S mediainfo`
  - **macOS (brew)**: `brew install mediainfo`
  - **Windows**: instalar desde <https://mediaarea.net/en/MediaInfo> y añadir al PATH si es necesario.

---

## Instalación

```bash
git clone https://github.com/CheChu10/tmdb-movie-renamer.git
cd tmdb-movie-renamer

# Entorno virtual (opcional pero recomendado)
python -m venv .venv
# Linux/macOS:
source .venv/bin/activate
# Windows (PowerShell):
# .venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

> Asegúrate de tener **MediaInfo** instalado (ver arriba).

---

## Configuración (TMDB)

Copia el ejemplo y coloca tu **TMDB Read Access Token (Bearer)**:

```bash
cp config.example.ini config.ini
```

Edita `config.ini`:

```ini
[TMDB]
api_key = YOUR_TMDB_READ_ACCESS_TOKEN

[TEMPLATES]
destination_template = {COLLECTION_NAME|fallback:${TITLE}|char:0|upper}/{COLLECTION_NAME}/{TITLE} ({YEAR}) {IMDB}/{TITLE} ({YEAR}) {IMDB} - [{VF}{SOURCE|ifexists: (%value%)}{HDR|ifexists:, %value%}{VC|ifexists:, %value%}{AC|ifexists:, %value%}]
```

> El script espera un **Bearer Token** válido (TMDB v3). Si está vacío o es inválido, abortará con un error legible.
> `config.example.ini` mantiene el estilo personalizado actual del proyecto (Jellyfin-compatible).
> También puedes usar presets: `preset:jellyfin`, `preset:plex`, `preset:emby`, `preset:minimal`.
> `destination_template` es obligatorio: si falta en `config.ini`, el script aborta.

---

## Uso

```bash
# Simulación (por defecto) — no cambia archivos
python renamer.py --src "/ruta/descargas" --dest "/ruta/libreria"

# Mover archivos
python renamer.py --src "/ruta/descargas" --dest "/ruta/libreria" --action move

# Copiar archivos
python renamer.py --src "/ruta/descargas" --dest "/ruta/libreria" --action copy

# Idioma de metadatos (admite alias como 'spa', 'eng', 'español', 'english'…)
python renamer.py --src "/ruta/descargas" --dest "/ruta/libreria" --lang es

# Idioma con región explícita (cuando te importa el país, p.ej. títulos alternativos)
python renamer.py --src "/ruta/descargas" --dest "/ruta/libreria" --lang es-ES
python renamer.py --src "/ruta/descargas" --dest "/ruta/libreria" --lang pt-PT
python renamer.py --src "/ruta/descargas" --dest "/ruta/libreria" --lang pt-BR

# Depuración detallada
python renamer.py --src "/ruta/descargas" --dest "/ruta/libreria" --debug

# Procesar solo un subconjunto usando glob (cuando el shell no expande, p.ej. entre comillas)
python renamer.py --src "/path/to/library/movies/1/12*" --dest "/path/to/library/movies" --action test --lang es

# Forzar simulación aunque action sea move/copy
python renamer.py --src "/ruta/descargas" --dest "/ruta/libreria" --action move --dry-run
```

Parámetros:

- `--src` (obligatorio): uno o más paths. Puede ser carpeta, fichero o patrón tipo glob (por ejemplo `"/movies/1/12*"`).
- `--dest` (obligatorio): carpeta destino.
- `--lang`: idioma de TMDB. Admite variantes por país tipo `es-ES`, `es-MX`, `pt-PT`, `pt-BR`. Si no se especifica país, se intenta elegir uno por defecto con Babel.
  - Nota: para `pt` el país por defecto suele ser `BR`; usa `pt-PT` si quieres Portugal explícitamente.
- `--action`: `test` (default) | `move` | `copy`.
- `--dry-run`: fuerza simulación.
- `--debug`: log de depuración adicional.

---

## Esquema de salida

Ejemplo mostrado para la plantilla personalizada de `config.example.ini`.

**Carpeta destino**:

```
{DESTINO}/
  ├─ {Primera letra}/
  │   └─ [{Colección opcional}]/ 
  │       └─ {Título} ({Año}) [ttXXXXXXX]/
  │           └─ {Título} ({Año}) [ttXXXXXXX] - [{VF} ({SOURCE}), {HDR?}, {VC}, {AC}].mkv
```

**Ejemplos**:

```
/movies/I/Inception (2010) [tt1375666]/
  Inception (2010) [tt1375666] - [1080p (BluRay), x264, EAC3].mkv

/movies/H/Harry Potter - Colección/Harry Potter and the Sorcerer's Stone (2001) [tt0241527]/
  Harry Potter and the Sorcerer's Stone (2001) [tt0241527] - [2160p (UHD BDRemux), HDR, x265, TrueHD].mkv
```

> El sufijo de **colección** se traduce según el idioma (`Colección`, `Collection`, `Sammlung`, `Collezione`…).
> Jellyfin detectará automáticamente las películas, colecciones y carátulas sin configuración adicional.

---

## Plantillas y tags

La parametrización sigue un enfoque declarativo inspirado en FileBot, pero sin convertirse en un mini lenguaje difícil: campos + transformaciones sencillas.

- Config: `config.ini` -> `[TEMPLATES].destination_template`.
- Sintaxis base: `{CAMPO|filtro:arg|filtro...}`.
- Atajos soportados: `{title.upper}` y acceso por índice `{title[0]}`.
- Seguridad: campos/filtros desconocidos o segmentos `../`/`./` abortan con error.
- Alcance: la plantilla siempre renderiza una **ruta relativa** dentro de `--dest`.
- También puedes usar presets con `preset:<nombre>`.

Plantilla usada en `config.example.ini` (personalizada del proyecto):

```text
{COLLECTION_NAME|fallback:${TITLE}|char:0|upper}/{COLLECTION_NAME}/{TITLE} ({YEAR}) {IMDB}/{TITLE} ({YEAR}) {IMDB} - [{VF}{SOURCE|ifexists: (%value%)}{HDR|ifexists:, %value%}{VC|ifexists:, %value%}{AC|ifexists:, %value%}]
```

Presets incluidos (alineados con documentación oficial):

| Preset | Uso recomendado | Estructura |
| --- | --- | --- |
| `jellyfin` | Convención oficial de películas Jellyfin. | `Movie (Year) [imdbid-tt...]/Movie (Year) [imdbid-tt...]` |
| `plex` | Estructura base recomendada por Plex (`Movie (Year)`). | `Movie (Year)/Movie (Year)` |
| `emby` | Estructura recomendada por Emby para películas. | `Movie (Year)/Movie (Year)` |
| `minimal` | Naming mínimo para flujos simples. | `Title/Title` |

Referencias oficiales:

- Jellyfin: `https://jellyfin.org/docs/general/server/media/movies/`
- Plex: `https://support.plex.tv/articles/naming-and-organizing-your-movie-media-files/`
- Emby: `https://emby.media/support/articles/Movie-Naming.html`

Template expandido de `preset:jellyfin`:

```text
{TITLE} ({YEAR}){IMDB_ID|ifexists: [imdbid-%value%]}/{TITLE} ({YEAR}){IMDB_ID|ifexists: [imdbid-%value%]}
```

Campos disponibles:

| Campo | Descripción |
| --- | --- |
| `{TITLE}` | Título final elegido desde TMDB (normalizado/saneado para filesystem). |
| `{ORIGINAL_TITLE}` | Título original de TMDB (normalizado/saneado). |
| `{LOCAL_FILENAME}` | Nombre local de entrada con extensión (fichero original, no TMDB). |
| `{YEAR}` / `{RELEASE_DATE}` | Año o fecha completa de estreno. |
| `{TMDB_ID}` / `{COLLECTION_ID}` | IDs de TMDB película/colección. |
| `{IMDB_ID}` / `{IMDB}` | IMDb en bruto (`tt...`) y formato opcional con corchetes (`[tt...]`). |
| `{COLLECTION_NAME}` | Nombre de colección final normalizado. |
| `{VF}` / `{SOURCE}` / `{HDR}` / `{VC}` / `{AC}` | Campos técnicos individuales. |
| `{FPS}` / `{BIT_DEPTH}` | FPS y profundidad de color detectados desde análisis real de media. |
| `{LANG}` / `{REGION}` | Contexto de idioma/región normalizado. |

Filtros disponibles:

| Filtro | Descripción |
| --- | --- |
| `upper`, `lower`, `title`, `capitalize` | Transformaciones de mayúsculas/minúsculas/capitalización. |
| `initials` | Primeras letras de cada palabra. |
| `char:N` | Carácter en posición `N` (acepta negativos). |
| `slice:START:END` | Recorte estilo Python (`START`/`END` opcionales). |
| `stem` | Elimina la última extensión del valor (basename). |
| `fallback:ARG` | Si está vacío, usa literal. Para variable usa `fallback:${CAMPO}`. |
| `replace:OLD:NEW` | Reemplazo literal de substring. |
| `trim` | Recorta espacios. |
| `ifexists:THEN[:ELSE]` | Regla: si hay valor, pinta THEN; si no, ELSE. |
| `ifcontains:NEEDLE:THEN[:ELSE]` | Regla: si contiene texto, pinta THEN. |
| `ifeq:TEXT:THEN[:ELSE]` | Regla: igualdad exacta de texto. |
| `ifgt/ifge/iflt/ifle` | Regla numérica sobre el valor actual. |

En reglas condicionales:

- La variable principal del placeholder ya es implícita en `{CAMPO|...}` (sin `$`).
- `%value%` representa el valor actual del campo.
- `${TITLE}`, `${FPS}`, etc. permite referenciar otros campos dentro de THEN/ELSE.

Ejemplos útiles:

- `title[0].upper` -> primera letra del título en mayúscula.
- `fallback:Sin colección` -> literal `Sin colección`.
- `fallback:${TITLE}` -> usa el valor de `TITLE`.
- `LOCAL_FILENAME|stem` -> nombre local sin la última extensión.
- `title|initials|upper` -> siglas del título.
- `"[{VF}, {VC}, {AC}]"` -> literales y separadores escritos directamente en plantilla.
- `{FPS|ifge:60:%value%FPS}` -> si FPS >= 60, pinta `60FPS`.
- `{TITLE|ifcontains:Extended:[Extended]}` -> regla simple por contenido.
- `{SOURCE|ifexists::NOEXISTE}` -> then vacío / else `NOEXISTE`.
- `{SOURCE|ifexists:SIEXISTE:NOEXISTE}` -> then y else explícitos.

Nota de nomenclatura:

- No existe `NAME`: usa `TITLE`.
- `TITLE`/`ORIGINAL_TITLE` vienen de TMDB.
- `LOCAL_FILENAME` viene del nombre local de entrada (con extensión).

Ejemplo personalizado:

```ini
[TEMPLATES]
destination_template = {TITLE|char:0|upper}/{TITLE} ({YEAR})/{TITLE} ({YEAR}) - [{VF}, {VC}, {AC}]
```

---

## Estructura del proyecto

```
.
├── media_analysis.py
├── renamer.py
├── template_engine.py
├── template_presets.py
├── test_renamer.py
├── requirements.txt
├── config.example.ini
└── renamer.log        # se genera en runtime
```

- **`renamer.py`**: script principal (CLI/orquestación).
- **`template_engine.py`**: motor de templates/filtros/reglas condicionales.
- **`template_presets.py`**: presets listos (`jellyfin`, `plex`, `emby`, `minimal`).
- **`media_analysis.py`**: análisis técnico (HDR/FPS/bit depth/resolución/source).
- **`test_renamer.py`**: pruebas (unittest) que cubren extracción de títulos/años, estrategia de búsqueda TMDB y construcción de rutas.
- **`requirements.txt`**: dependencias Python.
- **`config.example.ini`**: ejemplo de configuración TMDB + plantillas de salida.
- **`renamer.log`**: log rotado con operaciones y errores.
- **`renamer.detail.log`**: log rotado más verboso (decisiones internas / diagnóstico).

---

## Tests

Ejecuta los tests con `unittest`:

```bash
# Desde la raíz del proyecto
python -m unittest -v
```

---

## Limitaciones conocidas

- Solo **películas** (no series).
- Solo `.mkv`, `.mp4`, `.avi`.
- La detección de **fuente** es heurística (puede fallar en encodes atípicos).
- Los logs se rotan automáticamente (`renamer.log` y `renamer.detail.log`).

---

## Roadmap

- [x] **Parametrización** de plantilla de carpetas/archivos vía `config.ini` (`destination_template`).
- [ ] Ampliar detecciones HDR/fuente para más perfiles y casos borde.
- [ ] Estrategia de **colisiones** (sobrescribir/versión/omitir interactivo).
- [ ] Explorar bloque `[RULES]` en config para declarar tags calculados reutilizables (ej. `SOURCE_TAG`, `FPS_TAG`) y simplificar templates.
- [x] **Rotación de logs** (`renamer.log` y `renamer.detail.log`).
- [ ] Niveles de log configurables.
- [x] Más **tests** unitarios (mocks de TMDB / manejo de ficheros).

---

## Solución de problemas

- **“Configuration file 'config.ini' not found”**  
  Copia `config.example.ini` a `config.ini` y añade tu token TMDB.

- **“Please set your TMDB API key”**  
  Revisa que `api_key` tenga tu **TMDB v3 Bearer Token**.

- **“Could not read media info…”**  
  Asegúrate de tener **MediaInfo** instalado y accesible. En Windows, añade la instalación al **PATH**.

- **Encuentra la película equivocada**  
  Renombra el archivo de origen para incluir **año** y/o un título más claro. Usa `--debug` para ver las consultas a TMDB.

---

## Créditos

- [TheMovieDB](https://www.themoviedb.org/) (metadatos).
- [MediaInfo](https://mediaarea.net/) y `pymediainfo`.
- (Opcional futuro) parseo avanzado de release names estilo torrent.

---

---

# Movie Renamer — FileBot alternative  
*(Spanish version above)*

> Python script to **automatically rename and organize** movies using **TMDB** metadata and technical analysis via **MediaInfo**. Designed as a powerful and flexible replacement for FileBot, **optimizing folder structures for Jellyfin servers**.  

> ⚠️ **Status**: fully functional and tested under **Jellyfin**.  
> Since version **2.0**, output naming is **template-driven** from `config.ini` with ready-to-use Jellyfin/Plex/Emby presets.  
> The default template preserves the same legacy Jellyfin-friendly structure.

---

## Table of Contents
- [Movie Renamer — alternativa a FileBot](#movie-renamer--alternativa-a-filebot)
  - [Índice](#índice)
  - [Características](#características)
  - [Compatibilidad con Jellyfin](#compatibilidad-con-jellyfin)
  - [Requisitos](#requisitos)
  - [Instalación](#instalación)
  - [Configuración (TMDB)](#configuración-tmdb)
  - [Uso](#uso)
  - [Esquema de salida](#esquema-de-salida)
  - [Plantillas y tags](#plantillas-y-tags)
  - [Estructura del proyecto](#estructura-del-proyecto)
  - [Tests](#tests)
  - [Limitaciones conocidas](#limitaciones-conocidas)
  - [Roadmap](#roadmap)
  - [Solución de problemas](#solución-de-problemas)
  - [Créditos](#créditos)
- [Movie Renamer — FileBot alternative](#movie-renamer--filebot-alternative)
  - [Table of Contents](#table-of-contents)
  - [Features](#features)
  - [Jellyfin compatibility](#jellyfin-compatibility)
  - [Requirements](#requirements)
  - [Installation](#installation)
  - [Configuration (TMDB)](#configuration-tmdb)
  - [Usage](#usage)
  - [Output scheme](#output-scheme)
  - [Templates and tags](#templates-and-tags)
  - [Project layout](#project-layout)
  - [Tests](#tests-1)
  - [Known limitations](#known-limitations)
  - [Roadmap](#roadmap-1)
  - [Troubleshooting](#troubleshooting)
  - [Credits](#credits)

---

## Features

- TMDB identification via title+year, and if the filename contains an IMDb ID (`ttXXXXXXX`), direct lookup via the **TMDB Find API**.
- Final movie title in the requested language using `translations` and `alternative_titles` (when available), without mixing countries.
- **Collection folders** with translated collection names (when available) via **TMDB Collection Translations API**.
- Collection name normalization: strips suffixes already included by TMDB (`Collection`, `la colección`, `(Collection)`, etc.) and reapplies a consistent suffix.
- Technical extraction via **pymediainfo**: resolution with tolerance (e.g., 1792×1080 ⇒ 1080p), video/audio codec, HDR signaling (Dolby Vision/HDR10/HLG when present), bitrate, etc.
- **Source** detection from filename and/or height/bitrate heuristic: `WEB-DL`, `WEBRip`, `BDRip`, `BDRemux`, `UHD BDRemux`, `UHDRip`, `MicroHD`.
- Actions: `test` (dry-run), `move`, `copy` with atomic copy/move using hidden temp files (`.renamer-tmp-*`).
- Destination output is configurable via `config.ini` (`[TEMPLATES].destination_template`) with validated placeholders.
- `--lang` supports language + country codes like `es-ES`, `pt-PT`, `pt-BR`. If country is omitted, the script tries to pick a sensible default using Babel.
- Logging: `renamer.log` (actions) and `renamer.detail.log` (diagnostics), both rotated.
- Overlapping `--src`/`--dest` is supported; risky cases scan a snapshot of files to avoid infinite loops.
- Supported extensions: `.mkv`, `.mp4`, `.avi`.

---

## Jellyfin compatibility

This tool was **built and tested under Jellyfin** to ensure full metadata and collection recognition.

✔️ Folders and filenames follow Jellyfin’s naming conventions:  
- Collections end with `- Collection`.  
- Movies use `{Title} ({Year}) [ttXXXXXXX]`.  
- Technical tags (HDR, codec, resolution, source) embedded in filenames.

**Example:**
```
/movies/H/Harry Potter - Collection/Harry Potter and the Sorcerer's Stone (2001) [tt0241527]/Harry Potter and the Sorcerer's Stone (2001) [tt0241527] - [2160p (UHD BDRemux), HDR, x265, TrueHD].mkv
```

> Ideal for users who organize their movie libraries in **Jellyfin**, and also **compatible** with Plex/Emby naming rules.

---

## Requirements

- **Python 3.8+**
- Python packages (see `requirements.txt`):
  - `requests`
  - `colorama`
  - `pymediainfo`
  - `Babel`  ← (used to infer default regions like `es` ⇒ `es-ES`, `pt` ⇒ `pt-BR`)
- **MediaInfo** installed on your system (required by `pymediainfo`):
  - **Debian/Ubuntu**: `sudo apt-get install mediainfo`
  - **Fedora**: `sudo dnf install mediainfo`
  - **Arch**: `sudo pacman -S mediainfo`
  - **macOS (brew)**: `brew install mediainfo`
  - **Windows**: install from <https://mediaarea.net/en/MediaInfo> and add to PATH if needed.

---

## Installation

```bash
git clone https://github.com/CheChu10/tmdb-movie-renamer.git
cd tmdb-movie-renamer

# Optional virtualenv
python -m venv .venv
# Linux/macOS:
source .venv/bin/activate
# Windows (PowerShell):
# .venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

> Ensure **MediaInfo** is installed (see above).

---

## Configuration (TMDB)

Copy the example and set your **TMDB Read Access Token (Bearer)**:

```bash
cp config.example.ini config.ini
```

Edit `config.ini`:

```ini
[TMDB]
api_key = YOUR_TMDB_READ_ACCESS_TOKEN

[TEMPLATES]
destination_template = {COLLECTION_NAME|fallback:${TITLE}|char:0|upper}/{COLLECTION_NAME}/{TITLE} ({YEAR}) {IMDB}/{TITLE} ({YEAR}) {IMDB} - [{VF}{SOURCE|ifexists: (%value%)}{HDR|ifexists:, %value%}{VC|ifexists:, %value%}{AC|ifexists:, %value%}]
```

> The script expects a valid **Bearer Token** (TMDB v3). If missing/invalid, it will abort with a clear error.
> `config.example.ini` keeps the current project custom layout (Jellyfin-compatible).
> Available presets: `preset:jellyfin`, `preset:plex`, `preset:emby`, `preset:minimal`.
> `destination_template` is mandatory: if missing in `config.ini`, the script aborts.

---

## Usage

```bash
# Simulation (default) — no file changes
python renamer.py --src "/path/downloads" --dest "/path/library"

# Move files
python renamer.py --src "/path/downloads" --dest "/path/library" --action move

# Copy files
python renamer.py --src "/path/downloads" --dest "/path/library" --action copy

# Metadata language (accepts aliases like 'spa', 'eng', 'español', 'english'…)
python renamer.py --src "/path/downloads" --dest "/path/library" --lang en

# Verbose debug
python renamer.py --src "/path/downloads" --dest "/path/library" --debug

# Process only a subset using glob patterns (useful when quoting prevents shell expansion)
python renamer.py --src "/path/to/library/movies/1/12*" --dest "/path/to/library/movies" --action test --lang es

# Force dry-run even if action is move/copy
python renamer.py --src "/path/downloads" --dest "/path/library" --action move --dry-run
```

Parameters:

- `--src` (required): one or more paths. Can be a folder, a single file, or a glob pattern (e.g. `"/movies/1/12*"`).
- `--dest` (required): destination folder.
- `--lang`: TMDB language / country (examples: `es`, `es-ES`, `es-MX`, `pt`, `pt-PT`, `pt-BR`).
  - Note: when country is omitted, Babel is used to infer a default. For Portuguese, `pt` typically resolves to `pt-BR`; use `pt-PT` explicitly for Portugal.
- `--action`: `test` (default) | `move` | `copy`.
- `--dry-run`: force simulation.
- `--debug`: extra debug logging.

---

## Output scheme

Example shown for the custom template used in `config.example.ini`.

**Destination tree**:

```
{DEST}/
  ├─ {First letter}/
  │   └─ [{Optional collection}]/ 
  │       └─ {Title} ({Year}) [ttXXXXXXX]/
  │           └─ {Title} ({Year}) [ttXXXXXXX] - [{VF} ({SOURCE}), {HDR?}, {VC}, {AC}].mkv
```

**Examples**:

```
/movies/I/Inception (2010) [tt1375666]/
  Inception (2010) [tt1375666] - [1080p (BluRay), x264, EAC3].mkv

/movies/H/Harry Potter - Collection/Harry Potter and the Sorcerer's Stone (2001) [tt0241527]/
  Harry Potter and the Sorcerer's Stone (2001) [tt0241527] - [2160p (UHD BDRemux), HDR, x265, TrueHD].mkv
```

> The **collection** suffix is localized based on the selected language (`Colección`, `Collection`, `Sammlung`, `Collezione`, …).

---

## Templates and tags

The template model is field-based (in the spirit of FileBot), but intentionally simple: reusable fields plus lightweight transformations.

- Config key: `config.ini` -> `[TEMPLATES].destination_template`.
- Base syntax: `{FIELD|filter:arg|filter...}`.
- Shortcuts supported: `{title.upper}` and index access `{title[0]}`.
- Safety: unknown fields/filters and `../`/`./` path segments are rejected.
- Scope: rendered output is always treated as a **relative path** under `--dest`.
- You can also use presets with `preset:<name>`.

Template used in `config.example.ini` (project custom layout):

```text
{COLLECTION_NAME|fallback:${TITLE}|char:0|upper}/{COLLECTION_NAME}/{TITLE} ({YEAR}) {IMDB}/{TITLE} ({YEAR}) {IMDB} - [{VF}{SOURCE|ifexists: (%value%)}{HDR|ifexists:, %value%}{VC|ifexists:, %value%}{AC|ifexists:, %value%}]
```

Built-in presets (aligned with official docs):

| Preset | Recommended use | Structure |
| --- | --- | --- |
| `jellyfin` | Official Jellyfin movie naming convention. | `Movie (Year) [imdbid-tt...]/Movie (Year) [imdbid-tt...]` |
| `plex` | Plex-style base naming (`Movie (Year)`). | `Movie (Year)/Movie (Year)` |
| `emby` | Emby recommended movie structure. | `Movie (Year)/Movie (Year)` |
| `minimal` | Minimal deterministic naming. | `Title/Title` |

Official references:

- Jellyfin: `https://jellyfin.org/docs/general/server/media/movies/`
- Plex: `https://support.plex.tv/articles/naming-and-organizing-your-movie-media-files/`
- Emby: `https://emby.media/support/articles/Movie-Naming.html`

Expanded template used by `preset:jellyfin`:

```text
{TITLE} ({YEAR}){IMDB_ID|ifexists: [imdbid-%value%]}/{TITLE} ({YEAR}){IMDB_ID|ifexists: [imdbid-%value%]}
```

Available fields:

| Field | Meaning |
| --- | --- |
| `{TITLE}` | Final display title selected from TMDB (normalized/filesystem-safe). |
| `{ORIGINAL_TITLE}` | Original title from TMDB (normalized/filesystem-safe). |
| `{LOCAL_FILENAME}` | Local input filename with extension, taken from the original file. |
| `{YEAR}` / `{RELEASE_DATE}` | Release year or full release date. |
| `{TMDB_ID}` / `{COLLECTION_ID}` | TMDB movie/collection ids. |
| `{IMDB_ID}` / `{IMDB}` | Raw IMDb id (`tt...`) and optional bracketed form (`[tt...]`). |
| `{COLLECTION_NAME}` | Final normalized collection name. |
| `{VF}` / `{SOURCE}` / `{HDR}` / `{VC}` / `{AC}` | Individual technical fields. |
| `{FPS}` / `{BIT_DEPTH}` | FPS and bit depth detected from real media analysis. |
| `{LANG}` / `{REGION}` | Normalized language/region context. |

Available filters:

| Filter | Meaning |
| --- | --- |
| `upper`, `lower`, `title`, `capitalize` | Case transformations. |
| `initials` | First character from each word. |
| `char:N` | Character at index `N` (negative indexes supported). |
| `slice:START:END` | Python-like slice (`START`/`END` optional). |
| `stem` | Remove the last extension segment (basename). |
| `fallback:ARG` | If empty, ARG is literal text. Use `fallback:${FIELD}` for variable fallback. |
| `replace:OLD:NEW` | Literal substring replacement. |
| `trim` | Strip leading/trailing spaces. |
| `ifexists:THEN[:ELSE]` | Rule: if value exists, render THEN; else ELSE. |
| `ifcontains:NEEDLE:THEN[:ELSE]` | Rule: if value contains NEEDLE, render THEN. |
| `ifeq:TEXT:THEN[:ELSE]` | Rule: exact text equality. |
| `ifgt/ifge/iflt/ifle` | Numeric rule against current value. |

Inside conditional rules:

- The placeholder primary field is implicit in `{FIELD|...}` (no `$`).
- `%value%` means the current field value.
- `${TITLE}`, `${FPS}`, etc. lets you reference other fields inside THEN/ELSE.

Useful examples:

- `title[0].upper` -> uppercase first letter of title.
- `fallback:No Collection` -> literal `No Collection`.
- `fallback:${TITLE}` -> uses the value of `TITLE`.
- `LOCAL_FILENAME|stem` -> local filename without final extension.
- `title|initials|upper` -> title initials.
- `"[{VF}, {VC}, {AC}]"` -> write literals/separators directly between fields.
- `{FPS|ifge:60:%value%FPS}` -> if FPS >= 60, render `60FPS`.
- `{TITLE|ifcontains:Extended:[Extended]}` -> simple content-based rule.
- `{SOURCE|ifexists::MISSING}` -> empty then / `MISSING` else.
- `{SOURCE|ifexists:EXISTS:MISSING}` -> explicit then/else.

Naming notes:

- There is no `NAME`: use `TITLE`.
- `TITLE`/`ORIGINAL_TITLE` come from TMDB.
- `LOCAL_FILENAME` comes from the local input filename (with extension).

Custom example:

```ini
[TEMPLATES]
destination_template = {TITLE|char:0|upper}/{TITLE} ({YEAR})/{TITLE} ({YEAR}) - [{VF}, {VC}, {AC}]
```

---

## Project layout

```
.
├── media_analysis.py
├── renamer.py
├── template_engine.py
├── template_presets.py
├── test_renamer.py
├── requirements.txt
├── config.example.ini
└── renamer.log        # generated at runtime
```

- **`renamer.py`**: main script (CLI/orchestration).
- **`template_engine.py`**: template/filter/conditional-rule engine.
- **`template_presets.py`**: ready-to-use presets (`jellyfin`, `plex`, `emby`, `minimal`).
- **`media_analysis.py`**: technical media analysis (HDR/FPS/bit depth/resolution/source).
- **`test_renamer.py`**: unit tests covering title/year extraction, TMDB search strategy, and destination path building.
- **`requirements.txt`**: Python dependencies.
- **`config.example.ini`**: TMDB + output template config example.
- **`renamer.log`**: rotated log with operations and errors.
- **`renamer.detail.log`**: rotated verbose log (internal decisions / diagnostics).

---

## Tests

Run with `unittest`:

```bash
python -m unittest -v
```

---

## Known limitations

- **Movies only** (no TV shows).
- Only `.mkv`, `.mp4`, `.avi`.
- **Source** detection is heuristic (can fail on atypical encodes).
- Log files are rotated automatically (`renamer.log` and `renamer.detail.log`).

---

## Roadmap

- [x] Folder/file **templating** via `config.ini` (`destination_template`).
- [ ] Expand HDR/source detection for more profiles and edge cases.
- [ ] **Collision** strategy (overwrite/version/interactive skip).
- [ ] Explore a config `[RULES]` block for reusable computed tags (e.g. `SOURCE_TAG`, `FPS_TAG`) to keep templates cleaner.
- [x] **Log rotation** (`renamer.log` and `renamer.detail.log`).
- [ ] Configurable log levels.
- [x] More **unit tests** (TMDB mocks / file operations).

---

## Troubleshooting

- **“Configuration file 'config.ini' not found”**  
  Copy `config.example.ini` to `config.ini` and set your TMDB token.

- **“Please set your TMDB API key”**  
  Ensure `api_key` contains your **TMDB v3 Bearer Token**.

- **“Could not read media info…”**  
  Make sure **MediaInfo** is installed and accessible. On Windows, add installation to **PATH** if necessary.

- **Wrong movie matched**  
  Rename source files to include the **year** and/or a clearer title. Use `--debug` to inspect TMDB queries.

---

## Credits

- [TheMovieDB](https://www.themoviedb.org/) (metadata).
- [MediaInfo](https://mediaarea.net/) and `pymediainfo`.
- (Future optional) advanced torrent-style release name parsing.
