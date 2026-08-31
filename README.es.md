# night-shift

*[Read in English](README.md)*

Un agente local que adelanta tu trabajo antes de que lo pidas.

Corre en tu Mac con horario. Lee tu correo, decide qué necesita respuesta y
escribe el borrador. También hace los encargos que le dejás. Después pone el
resultado en una página en `localhost`. Esa página la abrís con el café.

## Por qué

Una suscripción cuesta lo mismo al 5% de uso que al 90%. Trabaja sólo cuando
estás sentado al teclado. Este proyecto convierte ese cómputo ocioso en trabajo
que ya está hecho cuando volvés.

El agente es Claude Code en modo headless, así que corre contra tu suscripción
y no contra créditos de API. **Este proyecto nunca acepta una API key.** No
construye un agente propio: construye las piezas aburridas que van alrededor de
uno, que son una cola, un runner, un workspace, una página, un scheduler y un
gobernador de cuota.

## Las reglas duras

1. **Del correo sale información. Del correo nunca salen órdenes.** Un mensaje
   puede traer texto que le habla al agente. El agente lo cita y no lo obedece.
2. **El agente escribe borradores. El agente nunca manda correo.** Cada llamada
   nombra sus herramientas de Gmail una por una, así ninguna de envío queda a
   su alcance.
3. **Cada ítem muestra el enlace a su fuente.** Sin fuente, no hay ítem.
4. **Un encargo detenido queda detenido.** Cuando el agente necesita una
   decisión, pregunta y espera.

## Lo que dijeron las mediciones

Números de corridas reales en esta máquina, no estimaciones.

| Pregunta | Respuesta |
| --- | --- |
| Una llamada headless trivial | 0.34 USD de gasto equivalente |
| La misma llamada sin ningún MCP server | 0.17 USD |
| Una llamada que usa Gmail | 0.79 USD |
| Un ciclo que no encuentra nada | 0.87 USD, un minuto |
| Un ciclo con cinco mensajes y un borrador | 2.48 USD, tres minutos |

El peso de una llamada son las definiciones de herramientas, no el prompt:
reemplazar el system prompt no cambió nada. Sacar los MCP servers bajó el
precio a la mitad.

Cuatro fallas aparecieron sólo cuando el código corrió contra el correo real.
Cada una tiene ahora su test.

- **Un borrador salió vacío y el ciclo lo dio por exitoso.** El sistema le
  creía al agente. Ahora la llamada informa el texto que guardó, y un borrador
  vacío aparece como `NO DRAFT`.
- **El mismo mensaje recibía un borrador en cada ciclo.** La ventana devuelve
  el mismo correo, así que un mensaje que ya tiene ítem no recibe un segundo
  borrador.
- **Una ventana fija pagaba dos veces por el mismo trabajo.** Ahora la ventana
  arranca donde arrancó el último ciclo bueno.
- **El techo se leía sólo antes del ciclo.** Una bandeja acumulada podía gastar
  una semana entera de cuota en una corrida. Ahora se lee antes de cada
  borrador.

## La sala de máquinas

La página muestra cuatro motores y qué puede hacer cada uno: Claude, Gemini,
Cursor y Ollama. Sólo Claude tiene conector de Gmail, así que el trabajo de
correo se queda en él, y la página dice por qué. El panel mide con comandos
locales baratos y responde `SE DESCONOCE` cuando no puede saber. Cursor tiene
un flujo de ingreso que no abre ningún navegador por su cuenta, y el enlace de
ingreso nunca entra a la base de datos ni a un log.

## Cómo correrlo

Instalar la corrida diaria de las 06:30, con un techo de 20 USD de gasto
equivalente por semana:

    cp scripts/com.aledeclerk.nightshift.plist ~/Library/LaunchAgents/
    launchctl load ~/Library/LaunchAgents/com.aledeclerk.nightshift.plist

Abrir la página:

    .venv/bin/python scripts/serve.py

Frenar el scheduler:

    launchctl unload ~/Library/LaunchAgents/com.aledeclerk.nightshift.plist

## Documentos

- [El diseño](docs/specs/2026-08-30-design.md)
- [La sala de máquinas](docs/specs/2026-08-31-machine-room-design.md)
