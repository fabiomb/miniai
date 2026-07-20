# miniai

Cliente de chat **ultraligero** para usar **Claude, ChatGPT y Gemini** desde la
terminal, pensado para máquinas viejas (probado como objetivo: **Debian 12 de
32 bits, 1 GB de RAM, 1 GB de disco**).

- **Sin dependencias**: solo la biblioteca estándar de Python 3 (ya viene en
  Debian). No instala paquetes, no compila nada, no abre un navegador.
- **Un solo archivo** (`miniai.py`, ~25 KB). El LLM siempre es externo (la API
  del proveedor); en tu PC solo corre el cliente.
- **Chats independientes**, como el cliente de Claude: creás, listás, cambiás,
  renombrás y borrás conversaciones. Cada chat es un archivo JSON.
- **Estado de consumo y tamaño de contexto**: en cada respuesta ves los tokens
  usados, y con `/status` una barra con el % de la ventana de contexto ocupada,
  para evitar que crezca de más.
- **Respuesta en vivo (streaming)**: el texto se imprime a medida que llega,
  para los tres proveedores. Podés cortar la respuesta en curso con `Ctrl-C`: se
  guarda lo recibido y, si la API no alcanzó a informar el consumo, los tokens se
  estiman (se muestran con un `~` para distinguirlos de los reales).
  Se desactiva con `/stream off` si preferís esperar la respuesta completa.

## Requisitos

- Python 3.9 o superior (Debian 12 trae 3.11). Verificá con `python3 --version`.
- Conexión a internet y una clave API de al menos un proveedor.

Huella de recursos: el proceso usa aproximadamente **15–25 MB de RAM** y el
código ocupa **menos de 30 KB** en disco. Nada que instalar.

## Instalación

Copiá `miniai.py` a la PC vieja y (opcional) dejalo accesible:

```bash
chmod +x miniai.py
# opcional: para llamarlo como "miniai" desde cualquier lado
mkdir -p ~/.local/bin
cp miniai.py ~/.local/bin/miniai
```

## Claves API

Podés usar variables de entorno (recomendado) o guardarlas con `/setkey`.

Variables de entorno reconocidas:

| Proveedor | Variable                          |
|-----------|-----------------------------------|
| Claude    | `ANTHROPIC_API_KEY`               |
| ChatGPT   | `OPENAI_API_KEY`                  |
| Gemini    | `GEMINI_API_KEY` o `GOOGLE_API_KEY` |

Ejemplo (agregalo a `~/.bashrc` para que quede fijo):

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export OPENAI_API_KEY="sk-..."
export GEMINI_API_KEY="AIza..."
```

O bien, dentro del programa:

```
/setkey claude sk-ant-...
```

Las claves guardadas van a `~/.config/miniai/config.json` (con permisos 600).

## Uso

```bash
python3 miniai.py     # o simplemente: miniai
```

Escribís texto normal para hablar con el modelo. Si pegás un texto de varias
líneas, se detecta y se envía como **un solo mensaje** (también podés usar
`/paste` para componerlo a mano). Las líneas que empiezan con `/` son comandos:

```
/new [titulo]        crear un chat nuevo
/chats               listar los chats
/switch <n>          cambiar al chat n (número de la lista)
/rename <titulo>     renombrar el chat actual
/delete <n>          borrar el chat n
/clear               vaciar los mensajes del chat actual
/trim <n>            conservar solo los últimos n mensajes
/system [texto]      ver o fijar el prompt de sistema del chat
/paste               escribir/pegar un mensaje multilínea (termina con . sola)
/provider <nombre>   cambiar proveedor: claude | openai | gemini
/model [nombre]      ver o cambiar el modelo del chat actual
/max <n>             fijar max_tokens de respuesta
/stream [on|off]     respuesta en vivo (streaming); por defecto activada
/status              consumo y tamaño del contexto del chat actual
/keys                estado de las claves API
/setkey <prov> <k>   guardar una clave API
/update              descargar la última versión del script desde GitHub
/help                ayuda
/quit                salir
```

### Controlar la ventana de contexto

En cada respuesta aparece una barra de estado:

```
[Claude/claude-sonnet-4-6] msgs 6 | contexto 3.240 tok (1.6% de 200.000) real | total ^12.400 v2.100
```

- `contexto` es lo que se envía al modelo. Si dice `real`, es el dato exacto que
  devolvió la API en la última respuesta; si dice `~est`, es una estimación.
- Cuando el contexto se acerca al límite, el color pasa a amarillo y luego rojo,
  y el programa te avisa. Para achicarlo: `/trim 10` (deja los últimos 10
  mensajes) o `/new` para empezar limpio.

## Modelos

Por defecto:

- Claude: `claude-sonnet-5`
- ChatGPT: `gpt-5-5`
- Gemini: `gemini-3.5-flash`

Cambialos con `/model <nombre>` (queda guardado por proveedor) o editando
`~/.config/miniai/config.json`. Para gastar menos, elegí variantes económicas
(p. ej. modelos *mini* / *flash*).

## Dónde se guarda todo

- Configuración y claves: `~/.config/miniai/config.json`
- Chats (un JSON por conversación): `~/.local/share/miniai/chats/`
- Historial de entrada: `~/.local/share/miniai/history`

Podés copiar/hacer backup de la carpeta de chats sin problema.
