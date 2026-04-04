"""
SPEL — Setup de Telegram (correr UNA SOLA VEZ)
Obtiene tu chat_id y verifica que el bot funciona.

Uso en Colab:
  TELEGRAM_TOKEN = "tu_token_de_botfather"
  exec(open('spel_telegram_setup.py').read())
  setup(TELEGRAM_TOKEN)
"""

import requests, json

def setup(token: str):
    print("\n══════════════════════════════════")
    print("  SPEL Telegram Setup")
    print("══════════════════════════════════\n")

    base = f"https://api.telegram.org/bot{token}"

    # 1. Verificar que el token es válido
    r = requests.get(f"{base}/getMe")
    if r.status_code != 200:
        print(f"❌ Token inválido: {r.text}")
        return
    bot = r.json()['result']
    print(f"✅ Bot conectado: @{bot['username']} ({bot['first_name']})")

    # 2. Obtener updates (necesitas haberle mandado un mensaje al bot primero)
    print("\n📱 PASO NECESARIO:")
    print("  1. Abre Telegram en tu celular")
    print("  2. Busca tu bot: @" + bot['username'])
    print("  3. Mándale cualquier mensaje (ej: 'hola')")
    print("  4. Vuelve aquí y presiona Enter")
    input("\n  → Cuando hayas mandado el mensaje, presiona Enter aquí: ")

    r = requests.get(f"{base}/getUpdates")
    if r.status_code != 200 or not r.json()['result']:
        print("❌ Sin mensajes recibidos. Asegúrate de mandar un mensaje al bot primero.")
        return

    updates = r.json()['result']
    ultimo  = updates[-1]
    chat    = ultimo['message']['chat']

    chat_id   = chat['id']
    chat_name = chat.get('first_name', '') + ' ' + chat.get('last_name', '')

    print(f"\n✅ ¡Chat ID encontrado!")
    print(f"   Nombre:  {chat_name.strip()}")
    print(f"   Chat ID: {chat_id}")

    # 3. Mandar mensaje de prueba
    r = requests.post(f"{base}/sendMessage", json={
        "chat_id":    chat_id,
        "text":       "✅ *SPEL Bot configurado correctamente*\n\nRecibirás reportes nocturnos aquí.",
        "parse_mode": "Markdown"
    })
    if r.status_code == 200:
        print("\n✅ Mensaje de prueba enviado a tu Telegram")
    else:
        print(f"\n⚠️ Error mandando mensaje: {r.text}")

    # 4. Mostrar config para copiar
    print("\n══════════════════════════════════")
    print("  COPIA ESTOS VALORES A master.json")
    print("══════════════════════════════════")
    print(f'\n  "token":   "{token}"')
    print(f'  "chat_id": "{chat_id}"')

    # 5. Instrucciones para canales separados (opcional)
    print("\n══════════════════════════════════")
    print("  OPCIONAL: Crear canales separados")
    print("══════════════════════════════════")
    print("""
  Si quieres canales separados por tipo de archivo:

  1. En Telegram: crear canal privado (ej: "SPEL Checkpoints")
  2. Añadir tu bot como administrador del canal
  3. Mandar un mensaje en ese canal
  4. Correr: obtener_id_canal(token, "@nombre_del_canal")

  Para un solo canal personal (más simple): usar el mismo chat_id para todo.
  El agente añade etiquetas a cada archivo para distinguirlos.
    """)

    return chat_id


def obtener_id_canal(token: str, username_canal: str):
    """
    Obtiene el ID de un canal privado.
    El bot debe ser admin del canal y el canal debe tener al menos un mensaje.
    """
    base = f"https://api.telegram.org/bot{token}"
    r = requests.get(f"{base}/getUpdates")
    for update in r.json().get('result', []):
        msg = update.get('channel_post', {})
        chat = msg.get('chat', {})
        if chat.get('username') == username_canal.lstrip('@'):
            print(f"✅ Canal encontrado: {chat['title']} → ID: {chat['id']}")
            return chat['id']
    print(f"❌ Canal {username_canal} no encontrado en updates")
    print("  Asegúrate de que el bot es admin y mandaste un mensaje en el canal")
    return None


# Para correr directamente
if __name__ == '__main__':
    token = input("Pega tu token de @BotFather: ").strip()
    setup(token)
