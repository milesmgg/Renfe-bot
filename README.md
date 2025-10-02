# 🚄 Renfe-bot 🚄
Bot de Telegram para monitorizar billetes de Renfe y avisarte automáticamente cuando un tren que estaba lleno tenga plazas libres.

---
## 🚀 Puesta en marcha  

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

5. **Configura el token de Telegram**
Crea un archivo **.env** en la raíz del proyecto con tu token:  
```
TELEGRAM_TOKEN=tu_token_aqui
```
6. **Ejecuta el bot**
```bash
python telegram_bot.py
```

## ✨ Funcionalidades  
- 📌 Comandos:  
  - `/m` → Añadir un viaje para monitorizar  
  - `/list` → Ver viajes guardados  
  - `/delete` → Eliminar un viaje guardado  
  - `/h` → Ayuda  
  - `/stop` → Cancelar conversación
El bot comprobará periódicamente los trenes guardados y te notificará cuando haya plazas libres. 🚄  
