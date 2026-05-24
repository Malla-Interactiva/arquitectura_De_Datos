#!/bin/bash
echo "============================================"
echo "  COMUNAS_NORM — Normalizador ETL"
echo "============================================"
echo ""

# Instalar dependencias
pip install flask --quiet

echo "Iniciando servidor..."
echo "Abriendo http://localhost:5000"
echo "Presiona Ctrl+C para detener"
echo "============================================"
echo ""

python3 app.py
