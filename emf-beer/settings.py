from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    sip_username: str = ""
    sip_password: str = ""


settings = Settings()
