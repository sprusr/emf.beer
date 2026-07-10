from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    sip_username: str = ""
    sip_password: str = ""
    udp_bind_address: str = "0.0.0.0"


settings = Settings()
