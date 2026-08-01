# template-fix.md — Bug de interpolación de labels en `compose.traefik.yml`

## Contexto

Detectado analizando logs reales de producción de `docker-host`
(UrraHosting-Dashboard) al desplegar esta plantilla como
`runtime_mode=platform_stack`. El deploy termina bien (`docker compose up`
sale 0, el contenedor `dashboard` queda `healthy`), pero Traefik nunca
puede enrutar tráfico hacia la instancia. Log real (`urra-traefik`):

```
ERR EntryPoint doesn't exist entryPointName=game-525565-tcp routerName=game-${INSTANCE_ID}@docker
ERR No valid entryPoint for this router routerName=game-${INSTANCE_ID}@docker
ERR error="the service \"panel-a5f9af60-341e-4119-aa37-f756a0ad927b@docker\" does not exist"
    entryPointName=websecure routerName=panel-${INSTANCE_ID}@docker
```

Nótese `routerName=panel-${INSTANCE_ID}@docker` — literal, sin interpolar,
en vez del UUID real de la instancia (`a5f9af60-341e-4119-aa37-f756a0ad927b`,
que sí aparece correcto en el nombre del *servicio* que el router busca).

## Causa raíz

`compose.traefik.yml` declaraba `labels:` como un **mapa YAML**, con
`${INSTANCE_ID}` incrustado dentro de las *keys* de los labels:

```yaml
labels:
  traefik.http.routers.panel-${INSTANCE_ID}.rule: "Host(`...`)"
  traefik.http.services.panel-${INSTANCE_ID}.loadbalancer.server.port: "${DASHBOARD_PORT}"
```

**Docker Compose interpola `${VAR}` dentro de valores string, pero no
dentro de las *keys* de un mapa.** El log lo prueba de forma empírica: la
key del router (`panel-${INSTANCE_ID}`) quedó literal, pero el *valor* del
label `...service: panel-${INSTANCE_ID}` sí se interpoló bien (porque ahí
`${INSTANCE_ID}` está del lado del valor) y apuntaba correctamente a
`panel-a5f9af60-...`. El problema es que el **servicio** correspondiente se
registra con `traefik.http.services.panel-${INSTANCE_ID}...` — de nuevo
`${INSTANCE_ID}` en una key — así que el servicio quedó registrado bajo el
nombre literal `panel-${INSTANCE_ID}` en vez de `panel-a5f9af60-...`.
Resultado: el router busca un servicio que nunca existe con ese nombre →
`service ... does not exist` → el panel nunca responde aunque el
contenedor esté sano.

El mismo patrón afecta a los labels TCP/UDP de `game-runtime`
(`game-${INSTANCE_ID}`): con una sola instancia corriendo el efecto es solo
cosmético (el nombre del router en los logs queda literal, pero la *regla*
`HostSNI(`*`)`/el *puerto* del loadbalancer sí resuelven bien porque son
valores) — pero con **dos o más instancias de GamePanel corriendo a la
vez**, ambas presentarían exactamente el mismo label-key literal
(`traefik.tcp.routers.game-${INSTANCE_ID}.rule`, sin diferenciar por
instancia) y Traefik solo se quedaría con un router, pisando al de la otra
instancia — routing cruzado/roto entre instancias concurrentes.

## Fix aplicado

`compose.traefik.yml`: `labels:` pasó de mapa a **lista de strings
`"key=value"`** en ambos servicios (`game-runtime` y `dashboard`). Compose
sí interpola de forma confiable un string completo — al escribir la key y
el valor juntos como un solo string, `${INSTANCE_ID}` se expande en toda la
línea, key incluida:

```yaml
labels:
  - "traefik.http.routers.panel-${INSTANCE_ID}.rule=Host(`${INSTANCE_ID}.${PUBLIC_BASE_DOMAIN}`)"
  - "traefik.http.services.panel-${INSTANCE_ID}.loadbalancer.server.port=${DASHBOARD_PORT}"
```

Verificado con una simulación de la interpolación de Compose (sustitución
de `${VAR}` en cada string) contra el archivo ya corregido: con
`INSTANCE_ID=a5f9af60-341e-4119-aa37-f756a0ad927b`, tanto el router
`panel-a5f9af60-...` como el servicio `panel-a5f9af60-...` quedan con
**exactamente el mismo nombre** — ya no hay mismatch. Mismo resultado para
los routers/servicios `game-a5f9af60-...` (TCP y UDP).

No se tocó nada más: `compose.yml` no usa `${INSTANCE_ID}` (ni ninguna otra
variable por-instancia) dentro de una key de `labels:` — solo como *valor*
(`com.urrahosting.instance: ${INSTANCE_ID}`, key fija), que interpola sin
problema.

## Pendiente del lado del operador (no es un bug de este repo)

El primer error del log (`EntryPoint doesn't exist entryPointName=game-
525565-tcp`) es un problema aparte, ya documentado como nota operativa: los
*entryPoints* de Traefik son estáticos y deben pre-registrarse en la config
del propio Traefik del operador (`docker-host` los aprovisiona vía
`scripts/provision_traefik_entrypoints.py` / Administración > Traefik
Entrypoints, pero el fragmento YAML generado debe fusionarse a mano en el
`traefik.yml` real y Traefik debe reiniciarse una vez). No requiere cambios
en este repo.
