# Changelog

Todas las novedades notables de GamePanel se documentan en este archivo.
Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/).

## [1.2.1] - 2026-08-22

### Fixed

- Restaurar un backup ahora muestra un modal con el estado de la restauracion
  (en curso, completada o con error), en vez de dejar al usuario sin ninguna
  indicacion mientras el panel reemplaza los archivos del servidor.
- Corregido un caso donde restaurar un backup podia fallar con un error 500
  generico y sin detalle (por ejemplo, si el sistema de archivos no permitia
  reemplazar los datos del servidor). Ahora, si la restauracion falla, se
  muestra un mensaje claro junto con la opcion de descargar el reporte del
  error para soporte tecnico.

## [1.2.0] - 2026-08-19

### Added

- Seccion "Novedades" en el panel: un boton en el menu lateral abre un modal con
  las notas de la version actual (con soporte Markdown). El modal se abre
  automaticamente la primera vez que cada usuario carga el panel despues de una
  actualizacion, y no vuelve a aparecer solo hasta la siguiente actualizacion.
- El nombre del mundo de Terraria/tModLoader ahora se puede editar desde la
  pestana Configuracion.
