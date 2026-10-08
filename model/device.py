from pydantic import BaseModel, Field


class Device(BaseModel):
    """Egy regisztralt halozati eszkoz (pl. ESP32), ami a mixer streamjet hallgathatja."""

    id: str
    name: str
    ip_address: str
    enabled: bool = True
    token_hash: str = Field(description="A token SHA-256 hash-e (hex); a nyers tokent nem taroljuk")
    token_expires_at: int = Field(description="A token lejarata (epoch masodperc)")
    created_at: int
    last_seen: int | None = None
    mac_address: str | None = None
    hostname: str | None = None
    version: str | None = None
