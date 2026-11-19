"""Elenca i modelli GRATUITI di OpenRouter che supportano gli strumenti (catalogo pubblico, nessuna chiave).
python scripts/free_models.py   ->   usa l'elenco per aggiornare i pool in models.yaml"""
import json
import urllib.request

with urllib.request.urlopen("https://openrouter.ai/api/v1/models", timeout=30) as r:
    data = json.load(r)["data"]
free = [m for m in data if str(m["pricing"].get("prompt")) in ("0", "0.0") and str(m["pricing"].get("completion")) in ("0", "0.0")]
tools = [m for m in free if "tools" in (m.get("supported_parameters") or [])]
print(f"{len(data)} modelli, {len(free)} gratuiti, {len(tools)} con strumenti")
for m in sorted(tools, key=lambda m: -m.get("context_length", 0)):
    print(f"  {m['id']:60s} contesto {m.get('context_length', 0):>8d}")
