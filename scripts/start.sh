#!/bin/bash
# scripts/start.sh
# Run this locally to start the backend for testing
# Usage: bash scripts/start.sh

set -e

echo ""
echo "  ██████╗██████╗ ██╗   ██╗██████╗ ████████╗ ██████╗  ██████╗ ███████╗"
echo " ██╔════╝██╔══██╗╚██╗ ██╔╝██╔══██╗╚══██╔══╝██╔═══██╗██╔═══██╗██╔════╝"
echo " ██║     ██████╔╝ ╚████╔╝ ██████╔╝   ██║   ██║   ██║██║   ██║███████╗"
echo " ██║     ██╔══██╗  ╚██╔╝  ██╔═══╝    ██║   ██║   ██║██║   ██║╚════██║"
echo " ╚██████╗██║  ██║   ██║   ██║        ██║   ╚██████╔╝╚██████╔╝███████║"
echo "  ╚═════╝╚═╝  ╚═╝   ╚═╝   ╚═╝        ╚═╝    ╚═════╝  ╚═════╝ ╚══════╝"
echo ""
echo "  Personal Crypto Trading Bot — Starting up..."
echo ""

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python3 not found. Install from https://python.org"
    exit 1
fi

# Check .env file
if [ ! -f "backend/.env" ]; then
    echo "⚠️  No .env file found. Copying from .env.example..."
    cp .env.example backend/.env
    echo "📝 Edit backend/.env and add your API keys, then run this script again."
    echo ""
fi

# Install dependencies
echo "📦 Installing Python dependencies..."
cd backend
pip install -r requirements.txt -q

echo ""
echo "✅ Dependencies installed"
echo "🚀 Starting CryptoOS backend on http://localhost:8000"
echo "📊 Dashboard: open frontend/index.html in your browser"
echo "📖 API docs:  http://localhost:8000/docs"
echo ""
echo "Press Ctrl+C to stop"
echo ""

# Load .env if it exists
if [ -f ".env" ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi

uvicorn main:app --host 0.0.0.0 --port 8000 --reload
