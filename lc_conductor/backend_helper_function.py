import asyncio
from pydantic.dataclasses import dataclass
from pydantic import Field


@dataclass
class RunSettings:
    prompt_debugging: bool = Field(alias="promptDebugging", default=False)

    # RSA settings
    use_rsa: bool = Field(alias="useRsa", default=False)
    rsa_mode: str = Field(alias="rsaMode", default="standalone")
    rsa_n: int = Field(alias="rsaN", default=8)
    rsa_k: int = Field(alias="rsaK", default=4)
    rsa_t: int = Field(alias="rsaT", default=3)


async def loop_executor(executor, func, *args, **kwargs):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, func, *args, **kwargs)
