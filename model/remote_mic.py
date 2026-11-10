from pydantic import BaseModel, Field


class RemoteMicSource(BaseModel):
    """Egy hálózati mikrofongép, amelyhez a mixer kliensként kapcsolódik."""

    id: str
    name: str
    host: str
    port: int
    token: str = Field(description="A nyers közös titok: a mixer ezzel bizonyít, ezért nem tárolható hash-ként")
    volume: float = 1.0
    created_at: int
    rotated_at: int = Field(description="Az utolsó token-csere ideje (epoch másodperc)")
    last_seen: int | None = None
