from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    sip_username: str = ""
    sip_password: str = ""
    sip_server: str = "sip.emf.camp"
    udp_bind_address: str = "0.0.0.0"
    public_ipv4: str | None = None


settings = Settings()
