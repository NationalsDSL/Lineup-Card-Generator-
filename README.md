# Lineup Manager

Aplicacion en Streamlit para gestionar rosters, armar lineups y exportar PDFs de dugout, umpire y official card.

## Estructura

- `streamlit_app.py`: entrypoint recomendado para Streamlit Community Cloud.
- `lineup.py`: app principal.
- `baseball_app.db`: base de datos SQLite inicial.
- `assets/`: logos y fuentes usadas por la app.

## Ejecutar localmente

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

## Base compartida para Streamlit Cloud

Por defecto la app usa `baseball_app.db`, que sirve bien para trabajar localmente.
Para que los cambios hechos por cualquier usuario en la app publicada queden guardados
y sean visibles para todo el mundo, configura una base Turso/libSQL y agrega estos
secretos en Streamlit Cloud:

```toml
TURSO_DATABASE_URL = "libsql://tu-base.turso.io"
TURSO_AUTH_TOKEN = "tu-token"
```

Luego migra la base local actual a Turso desde tu maquina:

```powershell
$env:TURSO_DATABASE_URL="libsql://tu-base.turso.io"
$env:TURSO_AUTH_TOKEN="tu-token"
python scripts/migrate_sqlite_to_turso.py
```

Con esos secretos activos, la app publicada ya no escribe en el SQLite temporal de
Streamlit: todos los imports, ediciones de roster, lineups guardados, conteos de uso
y logos subidos desde la pagina se guardan en la base compartida.

## Publicar en GitHub y Streamlit Community Cloud

1. Sube este proyecto completo a un repositorio de GitHub.
2. En Streamlit Community Cloud crea una nueva app desde ese repositorio.
3. Usa `streamlit_app.py` como `Main file path`.
4. Para persistencia compartida, define `TURSO_DATABASE_URL` y `TURSO_AUTH_TOKEN` en Secrets.
5. Si en el futuro quieres mover la base local a otra ruta, define la variable de entorno `LINEUP_DB_PATH`.

## Nota sobre datos

La app usa SQLite local si no encuentra secretos de Turso. En Streamlit Community Cloud
el sistema de archivos no debe considerarse persistente a largo plazo, asi que para
produccion conviene activar Turso/libSQL.
