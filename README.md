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

## Publicar en GitHub y Streamlit Community Cloud

1. Sube este proyecto completo a un repositorio de GitHub.
2. En Streamlit Community Cloud crea una nueva app desde ese repositorio.
3. Usa `streamlit_app.py` como `Main file path`.
4. Si en el futuro quieres mover la base de datos a otra ruta, define la variable de entorno `LINEUP_DB_PATH`.

## Nota sobre datos

La app usa SQLite. En Streamlit Community Cloud el sistema de archivos no debe considerarse persistente a largo plazo, asi que los cambios hechos en produccion pueden perderse tras reinicios o redeploys. Si quieres persistencia real, conviene migrar la base a un servicio externo.
