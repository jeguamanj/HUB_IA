# =============================================================================
# Procfile — MCP HUB Server IA
# -----------------------------------------------------------------------------
# Azure Web App (Python runtime) lee este archivo para lanzar la app.
# Comando de arranque: un solo worker (el motor LLM ya gestiona concurrencia
# interna y no debe saturarse con muchos workers hacia OpenRouter).
# =============================================================================
web: uvicorn API.server:app --host 0.0.0.0 --port 8000 --workers 1