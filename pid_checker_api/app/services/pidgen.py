def get_key_info(key: str) -> dict:
    """
    Aquí iría la llamada a pidgenx (via wine o wrapper nativo).
    """
    if "ERROR" in key:
        return {"is_valid": False, "error_code": "INVALID_KEY_CHECKSUM"}
        
    return {
        "is_valid": True,
        "edition": "Windows 10 Pro",
        "type": "Volume:MAK"
    }
