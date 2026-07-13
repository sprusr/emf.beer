from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    sip_username: str = ""
    sip_password: str = ""
    sip_server: str = "sip.emf.camp"
    udp_bind_address: str = "0.0.0.0"
    public_ipv4: str | None = None

    bar_ws_url: str = "wss://bar.emf.camp/websocket/"
    bar_stocklines_url: str = "https://bar.emf.camp/api/stocklines.json"
    announce_numbers: str = "5288"  # comma-separated

    @property
    def announce_number_list(self) -> list[int]:
        return [int(n) for n in self.announce_numbers.split(",") if n.strip()]


settings = Settings()
