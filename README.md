# 🚄 Renfe-bot 🚄
Bot de Telegram para monitorizar billetes de Renfe y avisarte automáticamente cuando un tren que estaba lleno tenga plazas libres.

---
## Puesta en marcha  

1. **Clona este repositorio**  
```bash
git clone https://github.com/tuusuario/renfe-bot.git
cd renfe-bot
```

2. **(Opcional) Crea un entorno virtual**
```bash
python -m venv venv
source venv/bin/activate   # Linux/Mac
venv\Scripts\activate      # Windows
```

3. **Instala las dependencias**
```bash
pip install -r requirements.txt
```
4. **Instala los navegadores de Playwright:**
```bash
playwright install
```

5. **Configura el token de Telegram** [(Como conseguir el token)](https://docs.expertflow.com/cx/4.5/how-to-get-telegram-bot-token#:~:text=Search%20for%20%40BotFather%20and%20start%20a%20new%20conversation.&text=It%20should%20be%20verified%20with%20the%20tick%20icon%20from%20the%20Official%20telegram.&text=Send%20this%20command%20%2Ftoken%20and%20you%20will%20receive%20your%20bot%20token.)

Crea un archivo **.env** en la raíz del proyecto con tu token:  
```
TELEGRAM_TOKEN=tu_token_aqui
```
6. **Ejecuta el bot**
```bash
python telegram_bot.py
```
7. Ahora entra en la conversacion de telegram creada previamente y ya puedes añadir viajes para monitorear:
## Funcionalidades  
- Comandos:  
  - `/m` → Añadir un viaje para monitorizar (¡¡añadir nombre como aparece en la web de renfe!!
  - `/list` → Ver viajes guardados  
  - `/delete` → Eliminar un viaje guardado  
  - `/h` → Ayuda  
  - `/stop` → Cancelar conversación
    
El bot comprobará periódicamente los trenes guardados y te notificará cuando haya plazas libres. 🚄  
