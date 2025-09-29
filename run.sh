#!/bin/bash
# Script para ejecutar PyExplorer (versión PyQt) en Linux

# Nombre del script principal
APP="main.py"

echo "🔍 Preparando entorno virtual y dependencias..."

# Instalar dependencias del sistema
echo "📦 Instalando dependencias del sistema..."
DEPS=(
    "python3-send2trash"    # Para enviar archivos a la papelera
    "python3-pyqt5"         # Base de PyQt5
    "python3-pyqt5.qtwebengine"  # Para previsualización web
    "xdg-utils"             # Para abrir archivos con aplicación predeterminada
    "x-terminal-emulator"   # Para abrir terminal
)

for dep in "${DEPS[@]}"; do
    if ! dpkg -l | grep -q "$dep"; then
        echo "Instalando $dep..."
        sudo apt install -y "$dep"
    else
        echo "✅ $dep ya está instalado"
    fi
done

# Crear entorno virtual si no existe
if [ ! -d ".venv" ]; then
    echo "⚙️ Creando entorno virtual Python (.venv)..."
    python3 -m venv .venv
else
    echo "✅ Entorno virtual .venv ya existe."
fi

# Activar entorno virtual
source .venv/bin/activate

# Instalar dependencias necesarias en el entorno virtual
echo "📦 Instalando dependencias: PyQt5, PyQtWebEngine..."
.venv/bin/pip install --upgrade pip
.venv/bin/pip install PyQt5 PyQtWebEngine

# Ejecutar el programa principal
if [ -f "$APP" ]; then
    echo "🚀 Ejecutando $APP ..."
    .venv/bin/python "$APP"
else
    echo "❌ No se encontró el archivo $APP en el directorio actual."
    exit 1
fi