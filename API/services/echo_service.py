from fastapi import APIRouter

router = APIRouter()

@router.post("/echo")
async def echo_service(message: str):
    """
    Servicio de ejemplo: retorna el mismo mensaje recibido.
    """
    return {"echo": message}

def get_router():
    return router 